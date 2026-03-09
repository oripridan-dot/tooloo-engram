"""
Full Training Camp Report Tests — Runs all 11 scenarios, collects per-step
reports, and writes the final camp Markdown + JSON reports.

This is the "full camp" test — it:
  1. Drives every scenario through the V2 tribunal (mock mode by default)
  2. After each scenario: writes a per-step report via CampReportGenerator
  3. After all scenarios: writes the full camp Markdown + JSON report
  4. Prints the path of the report so the operator can open it
  5. Asserts all regression gates pass

This file doubles as the live-camp harness: set LIVE_CAMP=1 in the
environment to switch from mock to real Gemini calls (and load .env).

Usage (offline):
    pytest tooloo-engram/tests/test_camp_report.py -v

Usage (live):
    LIVE_CAMP=1 pytest tooloo-engram/tests/test_camp_report.py -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

# ── Path bootstrap ───────────────────────────────────────────
_workspace = Path(__file__).parent.parent.parent
_tooloo_engram_root = Path(__file__).parent.parent
for _p in [str(_workspace), str(_tooloo_engram_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load .env if available
try:
    from dotenv import load_dotenv

    load_dotenv(_workspace / ".env")
except ImportError:
    pass

_LIVE_CAMP = os.environ.get("LIVE_CAMP", "0").strip() in ("1", "true", "yes")
_HAS_API_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("TOOLOO_GEMINI_API_KEY"))

# ── Imports ───────────────────────────────────────────────────
from engram_v2.adversary import AdversaryValidator
from engram_v2.arbiter import ArbiterHealer, MockArbiterLLM
from engram_v2.delta_sync import DeltaSyncBus
from engram_v2.graph_store import EngramGraph
from engram_v2.jit_context import JITContextAnchor, MockContextFetcher
from engram_v2.tribunal_orchestrator import TribunalOrchestrator
from report_generator import (
    CampReportGenerator,
    build_full_report,
    build_scenario_report,
)
from training_camp.camp_runner import (
    _compute_quality_score,
    _make_engrams_for_scenario,
    _make_poisoned_engrams,
)
from training_camp.metrics import (
    MOCK_CAMP_BASELINE,
    CampRunSummary,
    MetricsCollector,
    ScenarioMetrics,
)
from training_camp.scenarios import ALL_SCENARIOS, get_scenarios


# ─────────────────────────────────────────────────────────────
def _build_tribunal(mode: str) -> TribunalOrchestrator:
    """Build a tribunal using mock or live components."""
    if mode == "live" and _HAS_API_KEY:
        from live_adapters import LiveArbiterLLM, LiveContextFetcher

        from experiments.project_engram.harness.live_llm import LiveLLM

        llm = LiveLLM()
        return TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=LiveContextFetcher(llm=llm)),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=LiveArbiterLLM(llm=llm)),
            bus=DeltaSyncBus(),
            max_heal_cycles=3,
        )
    # Default: mock
    return TribunalOrchestrator(
        anchor=JITContextAnchor(fetcher=MockContextFetcher()),
        validator=AdversaryValidator(),
        healer=ArbiterHealer(llm=MockArbiterLLM()),
        bus=DeltaSyncBus(),
        max_heal_cycles=3,
    )


def _run_scenario_with_results(scenario, tribunal, collector):
    """Run one scenario; return (ScenarioMetrics, tribunal_results_dicts)."""
    t0 = time.monotonic()
    graph = EngramGraph(decay_radius=4)

    engrams = _make_engrams_for_scenario(scenario)
    for e in engrams:
        graph.add_engram(e)

    tribunal_results = tribunal.run_batch(graph, engrams)

    poisoned = _make_poisoned_engrams(scenario)
    for p in poisoned:
        graph.add_engram(p)
    poisoned_results = tribunal.run_batch(graph, poisoned)

    total_latency_ms = (time.monotonic() - t0) * 1000

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

    all_results_dicts = [r.to_dict() for r in tribunal_results + poisoned_results]
    return metrics, all_results_dicts


# ═══════════════════════════════════════════════════════════════
#  SECTION 1 — Report builder unit tests (no LLM, pure functions)
# ═══════════════════════════════════════════════════════════════


class TestReportBuilders:
    def test_build_scenario_report_has_required_sections(self) -> None:
        """build_scenario_report must produce markdown with all required sections."""

        scenario = ALL_SCENARIOS[0]  # L1-01
        metrics = ScenarioMetrics(
            scenario_id=scenario.scenario_id,
            level=scenario.level.value,
            passed=True,
            total_latency_ms=42.5,
            jit_sources_added=4,
            adversary_rules_checked=7,
            heal_cycles=0,
            quality_score=95.0,
            adversary_passed_on_first_try=True,
            engram_count=3,
            edge_count=2,
        )
        report = build_scenario_report(scenario, metrics)

        assert scenario.scenario_id in report
        assert scenario.title in report
        assert "Quality Score" in report
        assert "Mandate" in report

    def test_build_full_report_contains_kpi_table(self) -> None:
        """build_full_report must contain KPI table and scenario table."""
        summary = CampRunSummary(
            run_id="test-run-001",
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:01:00+00:00",
            total_scenarios=5,
            passed_scenarios=5,
            failed_scenarios=0,
            avg_latency_ms=45.0,
            p99_latency_ms=90.0,
            avg_quality_score=95.2,
            min_quality_score=91.0,
            total_heal_cycles=2,
            adversary_first_pass_rate=0.80,
            regression_flags=[],
            regression_pass=True,
        )
        metrics_list = [
            ScenarioMetrics(
                scenario_id=f"L1-0{i}",
                level="L1",
                passed=True,
                total_latency_ms=40.0 + i * 5,
                jit_sources_added=3,
                adversary_rules_checked=5,
                heal_cycles=0,
                quality_score=95.0,
                adversary_passed_on_first_try=True,
                engram_count=2,
                edge_count=1,
            )
            for i in range(1, 6)
        ]

        report = build_full_report(summary, metrics_list, mode="mock")

        assert "Camp KPIs" in report
        assert "Per-Scenario Results" in report
        assert "Latency Histogram" in report
        assert "Level Breakdown" in report
        assert "test-run-001" in report

    def test_build_full_report_regression_fail_includes_flags(self) -> None:
        """When regression gates fail, the report must list the flags."""
        summary = CampRunSummary(
            run_id="test-run-fail",
            started_at="2025-01-01T00:00:00+00:00",
            total_scenarios=3,
            passed_scenarios=2,
            failed_scenarios=1,
            avg_quality_score=75.0,  # below gate
            adversary_first_pass_rate=0.50,  # below gate
            regression_flags=["AVG_QUALITY: 75.0 < 90.0", "ADVERSARY_FIRST_PASS: 50.00% < 60.00%"],
            regression_pass=False,
        )
        report = build_full_report(summary, [], mode="mock")
        assert "REGRESSION FLAGS" in report
        assert "AVG_QUALITY" in report

    def test_build_full_report_live_mode_includes_comparison_table(self) -> None:
        """Live-mode full report must include the Live vs Mock Baseline section."""
        summary = CampRunSummary(
            run_id="test-live-cmp",
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:02:00+00:00",
            total_scenarios=5,
            passed_scenarios=5,
            avg_latency_ms=850.0,  # realistic LLM round-trip
            p99_latency_ms=2100.0,
            avg_quality_score=95.0,
            adversary_first_pass_rate=1.0,
            regression_pass=True,
        )
        report = build_full_report(summary, [], mode="live")
        assert "Live vs Mock Baseline" in report
        assert str(MOCK_CAMP_BASELINE["avg_latency_ms"]) in report

    def test_build_full_report_mock_mode_no_comparison_table(self) -> None:
        """Mock-mode full report must NOT include the Live vs Mock Baseline section."""
        summary = CampRunSummary(
            run_id="test-mock-no-cmp",
            started_at="2025-01-01T00:00:00+00:00",
            total_scenarios=3,
            passed_scenarios=3,
            regression_pass=True,
        )
        report = build_full_report(summary, [], mode="mock")
        assert "Live vs Mock Baseline" not in report

    def test_camp_report_generator_writes_files(self, tmp_path) -> None:
        """CampReportGenerator.write_scenario and write_full_report create real files."""

        gen = CampReportGenerator(reports_dir=tmp_path, run_id="test-gen-001", mode="mock")

        s = ALL_SCENARIOS[0]
        m = ScenarioMetrics(
            scenario_id=s.scenario_id,
            level=s.level.value,
            passed=True,
            total_latency_ms=55.0,
            jit_sources_added=3,
            adversary_rules_checked=6,
            heal_cycles=0,
            quality_score=94.0,
            adversary_passed_on_first_try=True,
            engram_count=2,
            edge_count=1,
        )

        out_path = gen.write_scenario(s, m)
        assert out_path.exists(), f"Scenario report not written to {out_path}"
        content = out_path.read_text()
        assert s.scenario_id in content

        summary = CampRunSummary(
            run_id="test-gen-001",
            started_at="2025-01-01T00:00:00+00:00",
            total_scenarios=1,
            passed_scenarios=1,
            avg_quality_score=94.0,
            min_quality_score=94.0,
            adversary_first_pass_rate=1.0,
            regression_flags=[],
            regression_pass=True,
            avg_latency_ms=55.0,
            p99_latency_ms=55.0,
        )
        md_path, json_path = gen.write_full_report(summary)
        assert md_path.exists(), f"Full camp MD report not written to {md_path}"
        assert json_path.exists(), f"Full camp JSON report not written to {json_path}"

        json_data = json.loads(json_path.read_text())
        assert json_data["run_id"] == "test-gen-001"
        assert len(json_data["scenarios"]) == 1

    def test_json_report_is_valid_json(self, tmp_path) -> None:
        """Full camp JSON report must be valid JSON with expected top-level keys."""

        gen = CampReportGenerator(reports_dir=tmp_path, run_id="test-json-001", mode="mock")
        # Record at least one scenario
        s = ALL_SCENARIOS[0]
        m = ScenarioMetrics(
            scenario_id=s.scenario_id,
            level=s.level.value,
            passed=True,
            total_latency_ms=30.0,
            jit_sources_added=2,
            adversary_rules_checked=4,
            heal_cycles=0,
            quality_score=92.0,
            adversary_passed_on_first_try=True,
            engram_count=1,
            edge_count=0,
        )
        gen.write_scenario(s, m)

        summary = CampRunSummary(
            run_id="test-json-001",
            started_at="2025-01-01T00:00:00+00:00",
            total_scenarios=1,
            passed_scenarios=1,
            avg_quality_score=92.0,
            min_quality_score=92.0,
            avg_latency_ms=30.0,
            p99_latency_ms=30.0,
            adversary_first_pass_rate=1.0,
            regression_pass=True,
        )
        _, json_path = gen.write_full_report(summary)
        data = json.loads(json_path.read_text())
        assert {"run_id", "mode", "summary", "scenarios"} == set(data.keys())


# ═══════════════════════════════════════════════════════════════
#  SECTION 2 — Full camp run (all 11 scenarios, offline/mock)
# ═══════════════════════════════════════════════════════════════


class TestFullCampOffline:
    def test_full_camp_all_scenarios_pass(self, tmp_path) -> None:
        """All 11 training scenarios must pass and regression gates must be green."""
        run_id = f"camp-offline-{uuid.uuid4().hex[:8]}"
        gen = CampReportGenerator(reports_dir=tmp_path, run_id=run_id, mode="mock")
        collector = MetricsCollector(run_id)
        tribunal = _build_tribunal("mock")
        scenarios = get_scenarios()

        print(f"\n{'=' * 64}")
        print(f"  OFFLINE CAMP: {len(scenarios)} scenarios | run_id={run_id}")
        print(f"{'=' * 64}")

        for scenario in scenarios:
            metrics, tri_results = _run_scenario_with_results(scenario, tribunal, collector)
            # Write per-step report
            step_path = gen.write_scenario(scenario, metrics, tri_results)

            status = "✅ PASS" if metrics.passed else "❌ FAIL"
            print(
                f"  {status} {scenario.scenario_id} {scenario.title}"
                f" | Q={metrics.quality_score:.1f}"
                f" | {metrics.total_latency_ms:.0f}ms"
                f" | JIT={metrics.jit_sources_added}"
                f" | heals={metrics.heal_cycles}"
                f" | report={step_path.name}"
            )

        summary = collector.summarize()
        md_path, json_path = gen.write_full_report(summary)
        gen.print_summary(summary)

        print(f"\n  Full report: {md_path}")
        print(f"  JSON report: {json_path}")

        # ── Assertions ─────────────────────────────────────────
        assert summary.total_scenarios == len(scenarios)
        assert summary.passed_scenarios == summary.total_scenarios, (
            f"Not all scenarios passed: {summary.passed_scenarios}/{summary.total_scenarios}"
        )
        assert summary.regression_pass, f"Regression gates failed: {summary.regression_flags}"
        assert summary.avg_quality_score >= 90.0, (
            f"Average quality below gate: {summary.avg_quality_score:.1f}"
        )

        # Report files must exist
        assert md_path.exists(), "Full camp MD report not written"
        assert json_path.exists(), "Full camp JSON report not written"

        md_text = md_path.read_text()
        assert "Camp KPIs" in md_text
        assert "Per-Scenario Results" in md_text

        json_data = json.loads(json_path.read_text())
        assert json_data["summary"]["total_scenarios"] == len(scenarios)
        assert json_data["summary"]["regression_pass"] is True

    def test_per_scenario_report_files_created(self, tmp_path) -> None:
        """A per-scenario .md file must be written for every scenario."""
        run_id = f"camp-perscen-{uuid.uuid4().hex[:8]}"
        gen = CampReportGenerator(reports_dir=tmp_path, run_id=run_id, mode="mock")
        collector = MetricsCollector(run_id)
        tribunal = _build_tribunal("mock")
        scenarios = get_scenarios()

        for scenario in scenarios:
            metrics, tri_results = _run_scenario_with_results(scenario, tribunal, collector)
            path = gen.write_scenario(scenario, metrics, tri_results)
            assert path.exists(), f"Per-scenario report missing: {path}"
            assert scenario.scenario_id in path.read_text(), (
                f"Scenario ID not in report: {scenario.scenario_id}"
            )

    def test_level_breakdown_covers_all_levels(self, tmp_path) -> None:
        """Full camp JSON report must include L1, L2, and L3 scenarios."""
        run_id = f"camp-levels-{uuid.uuid4().hex[:8]}"
        gen = CampReportGenerator(reports_dir=tmp_path, run_id=run_id, mode="mock")
        collector = MetricsCollector(run_id)
        tribunal = _build_tribunal("mock")

        for scenario in get_scenarios():
            metrics, tri_results = _run_scenario_with_results(scenario, tribunal, collector)
            gen.write_scenario(scenario, metrics, tri_results)

        summary = collector.summarize()
        _, json_path = gen.write_full_report(summary)
        data = json.loads(json_path.read_text())

        levels = {s["level"] for s in data["scenarios"]}
        assert "L1" in levels
        assert "L2" in levels
        assert "L3" in levels


# ═══════════════════════════════════════════════════════════════
#  SECTION 3 — Live camp (LIVE_CAMP=1 only)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not (_LIVE_CAMP and _HAS_API_KEY),
    reason="Set LIVE_CAMP=1 and GEMINI_API_KEY to run live camp",
)
class TestFullCampLive:
    def test_live_camp_l1_scenarios(self, tmp_path) -> None:
        """Run all L1 scenarios with live Gemini and produce a live camp report."""
        run_id = f"camp-live-l1-{uuid.uuid4().hex[:8]}"
        gen = CampReportGenerator(reports_dir=tmp_path, run_id=run_id, mode="live")
        collector = MetricsCollector(run_id)
        tribunal = _build_tribunal("live")
        scenarios = get_scenarios(level=None)
        # L1 only to keep cost low: 5 scenarios
        l1_scenarios = [s for s in scenarios if s.level.value == "L1"]

        print(f"\n{'=' * 64}")
        print(f"  LIVE CAMP L1: {len(l1_scenarios)} scenarios | run_id={run_id}")
        print(f"{'=' * 64}")

        for scenario in l1_scenarios:
            metrics, tri_results = _run_scenario_with_results(scenario, tribunal, collector)
            step_path = gen.write_scenario(scenario, metrics, tri_results)

            status = "✅ PASS" if metrics.passed else "❌ FAIL"
            print(
                f"  {status} {scenario.scenario_id} {scenario.title}"
                f" | Q={metrics.quality_score:.1f}"
                f" | {metrics.total_latency_ms:.0f}ms"
                f" | report={step_path.name}"
            )

        summary = collector.summarize()
        md_path, json_path = gen.write_full_report(summary)
        gen.print_summary(summary)

        print(f"\n  Full live report: {md_path}")

        assert summary.total_scenarios == len(l1_scenarios)
        assert summary.regression_pass, f"Live camp regression flags: {summary.regression_flags}"
        assert md_path.exists()
        assert json_path.exists()

    def test_live_camp_full_l1_l6(self, tmp_path) -> None:
        """Run all L1-L6 scenarios with live Gemini — establishes live latency baseline.

        This is the counterpart to the mock `camp-l2l6-full` run. Live latency
        overhead vs mock is captured in the report's Live vs Mock Baseline table.
        """
        run_id = f"camp-live-full-{uuid.uuid4().hex[:8]}"
        gen = CampReportGenerator(reports_dir=tmp_path, run_id=run_id, mode="live")
        collector = MetricsCollector(run_id)
        tribunal = _build_tribunal("live")
        scenarios = get_scenarios(level=None)

        print(f"\n{'=' * 64}")
        print(f"  LIVE CAMP L1-L6: {len(scenarios)} scenarios | run_id={run_id}")
        print(f"{'=' * 64}")

        for scenario in scenarios:
            metrics, tri_results = _run_scenario_with_results(scenario, tribunal, collector)
            gen.write_scenario(scenario, metrics, tri_results)

            status = "✅ PASS" if metrics.passed else "❌ FAIL"
            print(
                f"  {status} {scenario.scenario_id} {scenario.title}"
                f" | Q={metrics.quality_score:.1f}"
                f" | {metrics.total_latency_ms:.0f}ms"
                f" | JIT={metrics.jit_sources_added}"
                f" | heals={metrics.heal_cycles}"
            )

        summary = collector.summarize()
        md_path, json_path = gen.write_full_report(summary)
        gen.print_summary(summary)

        print(f"\n  Full live report : {md_path}")
        print(f"  JSON report      : {json_path}")
        print("\n  Live latency baseline:")
        print(f"    avg={summary.avg_latency_ms:.1f}ms  p99={summary.p99_latency_ms:.1f}ms")
        print("    mock avg=31.7ms  mock p99=64.8ms")

        # ── Core assertions ─────────────────────────────────────
        assert summary.total_scenarios == len(scenarios), (
            f"Expected {len(scenarios)} scenarios, got {summary.total_scenarios}"
        )
        assert summary.passed_scenarios == summary.total_scenarios, (
            f"Not all live scenarios passed: {summary.passed_scenarios}/{summary.total_scenarios}"
        )
        assert summary.regression_pass, f"Live camp regression flags: {summary.regression_flags}"
        assert summary.avg_quality_score >= 90.0, (
            f"Live avg quality below gate: {summary.avg_quality_score:.1f}"
        )

        # ── Live latency gate: live P99 must stay under 10s per scenario ─
        # (LLM network round-trips are expected; 10s is the hard ceiling)
        assert summary.p99_latency_ms <= 10_000, (
            f"Live P99 latency exceeded 10s: {summary.p99_latency_ms:.0f}ms. "
            "Investigate ThreadPoolExecutor fan-out or TTL caching."
        )

        # ── Report files must exist and contain the comparison table ──
        assert md_path.exists()
        assert json_path.exists()
        md_text = md_path.read_text()
        assert "Live vs Mock Baseline" in md_text, (
            "Live report must include Live vs Mock Baseline comparison section"
        )
        assert "Camp KPIs" in md_text
        assert "Per-Scenario Results" in md_text

        json_data = json.loads(json_path.read_text())
        assert json_data["mode"] == "live"
        assert json_data["summary"]["total_scenarios"] == len(scenarios)
