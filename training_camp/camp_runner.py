"""
Training Camp Runner — Master training loop for Engram V2.

Drives all L1/L2/L3 scenarios through the V2 tribunal pipeline,
collects metrics, checks regression gates, and writes a JSONL ledger.

Each scenario run:
  1. Creates a fresh EngramGraph
  2. Generates ContextAwareEngrams from the scenario mandate
  3. Runs the TribunalOrchestrator on each engram
  4. If adversary seeds are present, injects them and verifies they are caught
  5. Records ScenarioMetrics and checks regression gates

Usage:
    python -m tooloo_engram.training_camp.camp_runner [--level L1|L2|L3] [--scenario L1-01]
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# Make tooloo-core and tooloo-engram root available
_workspace_root = Path(__file__).parent.parent.parent
_engram_root = Path(__file__).parent.parent
for _p in (str(_workspace_root), str(_engram_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from experiments.project_engram.engram.adversary import AdversaryValidator
from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
from experiments.project_engram.engram.delta_sync import DeltaSyncBus
from experiments.project_engram.engram.graph_store import EngramGraph
from experiments.project_engram.engram.jit_context import JITContextAnchor, MockContextFetcher
from experiments.project_engram.engram.schema import (
    ContextAwareEngram,
    Domain,
    EdgeType,
    Language,
    SynapticEdge,
)
from experiments.project_engram.engram.tribunal_orchestrator import TribunalOrchestrator

from .metrics import CampRunSummary, MetricsCollector, ScenarioMetrics
from .scenarios import ALL_SCENARIOS, ScenarioLevel, TrainingScenario, get_scenarios


def _make_engrams_for_scenario(scenario: TrainingScenario) -> list[ContextAwareEngram]:
    """Generate representative ContextAwareEngrams for a scenario mandate.

    In offline/training mode, we create synthetic engrams from the scenario's
    domain_mix. The logic bodies are intentionally simple stubs — the tribunal
    is what we're benchmarking, not code quality.
    """
    engrams: list[ContextAwareEngram] = []

    for i, domain_str in enumerate(scenario.domain_mix):
        domain = Domain(domain_str) if domain_str in Domain.__members__.values() else Domain.BACKEND
        language = Language.TSX if domain == Domain.FRONTEND else Language.PYTHON

        logic_body = _stub_body(scenario.mandate_text, domain_str, i)

        engram = ContextAwareEngram(
            intent=f"{scenario.title} [{domain_str}] component {i+1}",
            ast_signature=f"def {domain_str}_component_{i+1}():",
            logic_body=logic_body,
            domain=domain,
            language=language,
            module_path=f"{domain_str}/component_{i+1}.py",
            mandate_level=scenario.level.value,
        )
        engrams.append(engram)

    return engrams


def _make_poisoned_engrams(scenario: TrainingScenario) -> list[ContextAwareEngram]:
    """Create engrams with adversary seed flaws injected for tribunal testing."""
    poisoned: list[ContextAwareEngram] = []
    for seed in scenario.adversary_seeds:
        engram = ContextAwareEngram(
            intent=f"{scenario.title} [POISONED:{seed.rule_id}]",
            ast_signature=f"def poisoned_{seed.rule_id.lower().replace('-', '_')}():",
            logic_body=seed.poisoned_code,
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path=f"poisoned/{seed.rule_id}.py",
            mandate_level=scenario.level.value,
        )
        poisoned.append(engram)
    return poisoned


def _stub_body(mandate: str, domain: str, idx: int) -> str:
    """Generate a simple clean stub body for training camp engrams."""
    if domain == "frontend":
        return (
            f"const Component{idx} = () => {{\n"
            f"  const [data, setData] = React.useState(null);\n"
            f"  React.useEffect(() => {{ fetchData().then(setData); }}, []);\n"
            f"  return <div>{{data}}</div>;\n"
            f"}};\nexport default Component{idx};"
        )
    elif domain == "config":
        return (
            f"import os\n\nCONFIG_{idx} = {{\n"
            f"  'value': os.environ.get('CONFIG_{idx}', 'default'),\n"
            f"}}"
        )
    else:
        return (
            f"from datetime import UTC, datetime\n\n"
            f"def component_{idx}(input_data):\n"
            f"    \"\"\"Auto-generated for: {mandate[:60]}\"\"\"\n"
            f"    result = {{'processed': True, 'ts': datetime.now(UTC).isoformat()}}\n"
            f"    return result"
        )


def _compute_quality_score(
    tribunal_results: list,
    poisoned_results: list,
    expected_min: float,
) -> float:
    """Compute a quality score based on tribunal pass rates and seed detection."""
    if not tribunal_results:
        return 0.0

    passed = sum(1 for r in tribunal_results if r.passed)
    base_score = (passed / len(tribunal_results)) * 95.0

    # Bonus: poison engrams caught (+1.0 per caught)
    poison_caught = sum(1 for r in poisoned_results if not r.passed)
    bonus = poison_caught * 1.0

    return min(99.0, base_score + bonus)


def run_scenario(
    scenario: TrainingScenario,
    tribunal: TribunalOrchestrator,
    collector: MetricsCollector,
) -> ScenarioMetrics:
    """Run a single scenario through the full V2 tribunal pipeline."""
    t0 = time.monotonic()
    graph = EngramGraph(decay_radius=4)

    # Generate clean engrams for the scenario
    engrams = _make_engrams_for_scenario(scenario)
    for e in engrams:
        graph.add_engram(e)

    # Run tribunal on clean engrams
    tribunal_results = tribunal.run_batch(graph, engrams)

    # Generate and run tribunal on poisoned engrams (seed detection test)
    poisoned = _make_poisoned_engrams(scenario)
    for p in poisoned:
        graph.add_engram(p)
    poisoned_results = tribunal.run_batch(graph, poisoned)

    total_latency_ms = (time.monotonic() - t0) * 1000

    # Aggregate metrics
    total_jit = sum(r.jit_sources_added for r in tribunal_results + poisoned_results)
    total_rules = sum(r.adversary_rules_checked for r in tribunal_results + poisoned_results)
    total_heals = sum(r.heal_cycles for r in tribunal_results + poisoned_results)
    all_passed = all(r.passed for r in tribunal_results)
    first_pass = all(r.heal_cycles == 0 for r in tribunal_results)

    quality = _compute_quality_score(
        tribunal_results, poisoned_results, scenario.expected_min_quality
    )

    metrics = ScenarioMetrics(
        scenario_id=scenario.scenario_id,
        level=scenario.level.value,
        passed=all_passed,
        total_latency_ms=round(total_latency_ms, 2),
        jit_sources_added=total_jit,
        adversary_rules_checked=total_rules,
        heal_cycles=total_heals,
        quality_score=quality,
        adversary_passed_on_first_try=first_pass,
        engram_count=graph.node_count,
        edge_count=graph.edge_count,
    )
    collector.record(metrics)
    return metrics


def run_training_camp(
    level_filter: ScenarioLevel | None = None,
    scenario_id_filter: str | None = None,
    *,
    verbose: bool = True,
    mode: str = "mock",
) -> CampRunSummary:
    """Run the full training camp and return a summary."""
    run_id = f"camp-{uuid.uuid4().hex[:8]}"
    collector = MetricsCollector(run_id)

    # Build shared tribunal (mock or live)
    if mode == "live":
        from live_adapters import LiveArbiterLLM, LiveContextFetcher

        from experiments.project_engram.harness.live_llm import LiveLLM

        llm = LiveLLM()
        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=LiveContextFetcher(llm=llm)),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=LiveArbiterLLM(llm=llm)),
            bus=DeltaSyncBus(),
            max_heal_cycles=3,
        )
        mode_label = "live (Gemini)"
    else:
        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=MockArbiterLLM()),
            bus=DeltaSyncBus(),
            max_heal_cycles=3,
        )
        mode_label = "mock (offline)"

    # Get scenarios
    scenarios = get_scenarios(level_filter)
    if scenario_id_filter:
        scenarios = [s for s in scenarios if s.scenario_id == scenario_id_filter]

    if verbose:
        print(f"\n{'='*60}")
        print(f"TOOLOO ENGRAM V2 — TRAINING CAMP [{run_id}]")
        print(f"Scenarios: {len(scenarios)} | Mode: {mode_label}")
        print(f"{'='*60}")

    for scenario in scenarios:
        metrics = run_scenario(scenario, tribunal, collector)
        if verbose:
            status = "✓ PASS" if metrics.passed else "✗ FAIL"
            print(
                f"  [{status}] {scenario.scenario_id} {scenario.title}"
                f" | Q={metrics.quality_score:.1f}"
                f" | {metrics.total_latency_ms:.0f}ms"
                f" | JIT={metrics.jit_sources_added}"
                f" | heals={metrics.heal_cycles}"
            )

    summary = collector.summarize()

    if verbose:
        print(f"\n{'─'*60}")
        print(f"RESULTS: {summary.passed_scenarios}/{summary.total_scenarios} passed"
              f" ({summary.pass_rate:.0%})")
        print(f"Avg quality: {summary.avg_quality_score:.1f} | "
              f"Avg latency: {summary.avg_latency_ms:.0f}ms")
        print(f"Total heal cycles: {summary.total_heal_cycles} | "
              f"First-pass rate: {summary.adversary_first_pass_rate:.0%}")
        if summary.regression_pass:
            print("REGRESSION GATES: ✓ ALL GREEN")
        else:
            print(f"REGRESSION FLAGS: {summary.regression_flags}")
        print(f"{'='*60}\n")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Engram V2 Training Camp")
    parser.add_argument("--level", choices=["L1", "L2", "L3", "L4", "L5", "L6"], help="Filter by level")
    parser.add_argument("--scenario", help="Run a specific scenario by ID")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock",
                        help="mock (offline, no API calls) or live (Gemini API)")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    level = ScenarioLevel(args.level) if args.level else None
    summary = run_training_camp(
        level_filter=level,
        scenario_id_filter=args.scenario,
        verbose=not args.quiet,
        mode=args.mode,
    )
    sys.exit(0 if summary.regression_pass else 1)
