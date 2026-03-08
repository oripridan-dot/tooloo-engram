"""
Camp Report Generator — Produces structured Markdown + JSON reports for every
training camp run.

Per-scenario reports capture the tribunal pipeline trace, adversary hit map,
JIT anchor decisions, heal outcomes, and quality score contribution.

The full camp report aggregates all scenarios into a dashboard with KPI table,
regression gate pass/fail matrix, latency histogram (ASCII art), and
per-level breakdown.

Usage:
    from tooloo_engram.report_generator import CampReportGenerator
    gen = CampReportGenerator()
    gen.write_scenario(scenario, metrics, tribunal_results)
    report_path = gen.write_full_report(summary, all_scenario_metrics)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Workspace root
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from training_camp.metrics import (
    MOCK_CAMP_BASELINE,
    REGRESSION_GATES,
    CampRunSummary,
    ScenarioMetrics,
    compare_to_baseline,
)
from training_camp.scenarios import TrainingScenario

_REPORTS_DIR = Path(__file__).parent / "reports"


# ── Per-scenario step report ───────────────────────────────────

def _scenario_header(scenario: TrainingScenario, metrics: ScenarioMetrics) -> str:
    status = "✅ PASS" if metrics.passed else "❌ FAIL"
    return f"""## {status}  `{scenario.scenario_id}` — {scenario.title}

| Field | Value |
|---|---|
| **Level** | {scenario.level.value} |
| **Domains** | `{", ".join(scenario.domain_mix)}` |
| **Engrams** | {metrics.engram_count} |
| **Edges** | {metrics.edge_count} |
| **Latency** | {metrics.total_latency_ms:.1f} ms |
| **Quality Score** | {metrics.quality_score:.2f} |
| **JIT Sources Anchored** | {metrics.jit_sources_added} |
| **Adversary Rules Checked** | {metrics.adversary_rules_checked} |
| **Heal Cycles** | {metrics.heal_cycles} |
| **Adversary First Pass** | {"✅ Yes" if metrics.adversary_passed_on_first_try else "⚠️ Healed"} |
| **Timestamp** | {metrics.timestamp} |
"""


def _scenario_mandate(scenario: TrainingScenario) -> str:
    return f"""### Mandate

> {scenario.mandate_text}
"""


def _scenario_seeds(scenario: TrainingScenario) -> str:
    if not scenario.adversary_seeds:
        return ""
    lines = ["### Adversary Seeds (Injected Flaws)\n"]
    lines.append("| Rule ID | Description |")
    lines.append("|---|---|")
    for seed in scenario.adversary_seeds:
        lines.append(f"| `{seed.rule_id}` | {seed.description} |")
    lines.append("")
    return "\n".join(lines)


def _scenario_tribunal_results(tribunal_results: list[dict]) -> str:
    if not tribunal_results:
        return ""
    lines = ["### Tribunal Pipeline Results\n"]
    lines.append("| Engram | Passed | Heals | JIT | Rules | Latency (ms) | Stages |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in tribunal_results:
        eid = str(r.get("engram_id", "?"))[:8]
        passed = "✅" if r.get("passed") else "❌"
        stages = " → ".join(r.get("pipeline_stages", []))
        lines.append(
            f"| `{eid}` | {passed} | {r.get('heal_cycles', 0)} "
            f"| {r.get('jit_sources_added', 0)} "
            f"| {r.get('adversary_rules_checked', 0)} "
            f"| {r.get('total_latency_ms', 0):.1f} "
            f"| {stages} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_scenario_report(
    scenario: TrainingScenario,
    metrics: ScenarioMetrics,
    tribunal_results: list[dict] | None = None,
) -> str:
    """Build a Markdown report for a single scenario step."""
    parts: list[str] = [
        _scenario_header(scenario, metrics),
        _scenario_mandate(scenario),
        _scenario_seeds(scenario),
    ]
    if tribunal_results:
        parts.append(_scenario_tribunal_results(tribunal_results))
    return "\n".join(parts)


# ── Full camp report ───────────────────────────────────────────

def _kpi_table(summary: CampRunSummary) -> str:
    return f"""## Camp KPIs

| KPI | Value | Gate | Status |
|---|---|---|---|
| Pass Rate | **{summary.pass_rate:.1%}** | ≥ 90% | {"✅" if summary.pass_rate >= 0.90 else "❌"} |
| Avg Quality | **{summary.avg_quality_score:.1f}** | ≥ {REGRESSION_GATES["avg_quality_min"]} | {"✅" if summary.avg_quality_score >= REGRESSION_GATES["avg_quality_min"] else "❌"} |
| Adversary 1st-Pass | **{summary.adversary_first_pass_rate:.1%}** | ≥ {REGRESSION_GATES["adversary_first_pass_rate_min"]:.0%} | {"✅" if summary.adversary_first_pass_rate >= REGRESSION_GATES["adversary_first_pass_rate_min"] else "❌"} |
| Avg Latency | **{summary.avg_latency_ms:.1f} ms** | — | — |
| P99 Latency | **{summary.p99_latency_ms:.1f} ms** | — | — |
| Total Heal Cycles | **{summary.total_heal_cycles}** | ≤ {REGRESSION_GATES["heal_cycles_per_scenario_max"]:.0f}/scenario | {"✅" if summary.total_scenarios == 0 or summary.total_heal_cycles / max(1, summary.total_scenarios) <= REGRESSION_GATES["heal_cycles_per_scenario_max"] else "❌"} |
"""


def _regression_gate_section(summary: CampRunSummary) -> str:
    if summary.regression_pass:
        status = "### ✅ ALL REGRESSION GATES GREEN\n"
    else:
        flags = "\n".join(f"- ❌ {f}" for f in summary.regression_flags)
        status = f"### ❌ REGRESSION FLAGS RAISED\n\n{flags}\n"
    return status


def _per_scenario_table(all_metrics: list[ScenarioMetrics]) -> str:
    lines = ["## Per-Scenario Results\n"]
    lines.append("| Scenario | Level | Status | Quality | Latency (ms) | JIT | Heals | Adversary 1st |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for m in all_metrics:
        status = "✅" if m.passed else "❌"
        adv = "✅" if m.adversary_passed_on_first_try else "⚠️"
        lines.append(
            f"| `{m.scenario_id}` | {m.level} | {status} "
            f"| {m.quality_score:.1f} "
            f"| {m.total_latency_ms:.1f} "
            f"| {m.jit_sources_added} "
            f"| {m.heal_cycles} "
            f"| {adv} |"
        )
    lines.append("")
    return "\n".join(lines)


def _latency_histogram(all_metrics: list[ScenarioMetrics], width: int = 30) -> str:
    """Simple ASCII bar chart of per-scenario latencies."""
    if not all_metrics:
        return ""
    max_lat = max(m.total_latency_ms for m in all_metrics) or 1.0
    lines = ["## Latency Histogram (ms)\n", "```"]
    for m in all_metrics:
        bar_len = int((m.total_latency_ms / max_lat) * width)
        bar = "█" * bar_len
        lines.append(f"  {m.scenario_id:<8} {bar:<{width}} {m.total_latency_ms:>7.1f}ms")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _level_breakdown(all_metrics: list[ScenarioMetrics]) -> str:
    by_level: dict[str, list[ScenarioMetrics]] = {}
    for m in all_metrics:
        by_level.setdefault(m.level, []).append(m)

    lines = ["## Level Breakdown\n"]
    lines.append("| Level | Scenarios | Passed | Avg Quality | Avg Latency |")
    lines.append("|---|---|---|---|---|")
    for level in sorted(by_level):
        ms = by_level[level]
        passed = sum(1 for m in ms if m.passed)
        avg_q = sum(m.quality_score for m in ms) / len(ms)
        avg_l = sum(m.total_latency_ms for m in ms) / len(ms)
        lines.append(f"| {level} | {len(ms)} | {passed}/{len(ms)} | {avg_q:.1f} | {avg_l:.1f} ms |")
    lines.append("")
    return "\n".join(lines)


def _live_vs_mock_comparison(summary: CampRunSummary) -> str:
    """Render a Live vs Mock latency comparison table for live-mode reports."""
    cmp = compare_to_baseline(summary)

    def _ratio_badge(ratio: float, higher_is_better: bool = False) -> str:
        if higher_is_better:
            return "✅" if ratio >= 0.98 else ("⚠️" if ratio >= 0.90 else "❌")
        # Lower is better (latency)
        return "✅" if ratio <= 1.5 else ("⚠️" if ratio <= 3.0 else "❌")

    lat = cmp["avg_latency_ms"]
    p99 = cmp["p99_latency_ms"]
    q = cmp["avg_quality_score"]
    adv = cmp["adversary_first_pass_rate"]

    lines = [
        "## Live vs Mock Baseline",
        "",
        f"> Baseline: `camp-l2l6-full` (mock, 2026-03-08) | "
        f"Avg {MOCK_CAMP_BASELINE['avg_latency_ms']} ms · "
        f"P99 {MOCK_CAMP_BASELINE['p99_latency_ms']} ms",
        "",
        "| Metric | Mock | Live | Δ | ×Overhead | Status |",
        "|---|---|---|---|---|---|",
        f"| Avg Latency | {lat['mock']} ms | {lat['live']} ms | +{lat['delta_ms']} ms "
        f"| {lat['ratio']}× | {_ratio_badge(lat['ratio'])} |",
        f"| P99 Latency | {p99['mock']} ms | {p99['live']} ms | +{p99['delta_ms']} ms "
        f"| {p99['ratio']}× | {_ratio_badge(p99['ratio'])} |",
        f"| Avg Quality | {q['mock']} | {q['live']} | — "
        f"| {q['ratio']}× | {_ratio_badge(q['ratio'], higher_is_better=True)} |",
        f"| Adversary 1st-Pass | {adv['mock']:.0%} | {adv['live']:.0%} | — "
        f"| {adv['ratio']}× | {_ratio_badge(adv['ratio'], higher_is_better=True)} |",
        "",
        "> **Overhead interpretation:** ×1.0–1.5 = acceptable LLM network overhead · "
        "> ×1.5–3.0 = consider TTL caching · ×3.0+ = tune `ThreadPoolExecutor` fan-out",
        "",
    ]
    return "\n".join(lines)


def build_full_report(
    summary: CampRunSummary,
    all_metrics: list[ScenarioMetrics],
    mode: str = "mock",
    run_id: str = "",
) -> str:
    """Build the full camp Markdown report."""
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    rid = summary.run_id or run_id

    header = f"""# Engram V2 Training Camp — Full Report

| Field | Value |
|---|---|
| **Run ID** | `{rid}` |
| **Mode** | `{mode}` |
| **Started** | {summary.started_at} |
| **Completed** | {summary.completed_at or ts} |
| **Scenarios** | {summary.passed_scenarios}/{summary.total_scenarios} passed |
| **Generated** | {ts} |

---

"""

    return (
        header
        + _kpi_table(summary)
        + "\n"
        + _regression_gate_section(summary)
        + "\n---\n\n"
        + (_live_vs_mock_comparison(summary) + "\n---\n\n" if mode == "live" else "")
        + _per_scenario_table(all_metrics)
        + "\n"
        + _latency_histogram(all_metrics)
        + "\n"
        + _level_breakdown(all_metrics)
    )


# ── CampReportGenerator ────────────────────────────────────────

@dataclass
class CampReportGenerator:
    """Writes per-scenario step reports and the final camp report to disk."""

    reports_dir: Path = field(default_factory=lambda: _REPORTS_DIR)
    run_id: str = field(default_factory=lambda: "")
    mode: str = "mock"

    # Accumulated for the final report
    _scenario_metrics: list[ScenarioMetrics] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        if not self.run_id:
            self.run_id = datetime.now(UTC).strftime("camp_%Y%m%d_%H%M%S")

    # ---- Per-scenario ----

    def write_scenario(
        self,
        scenario: TrainingScenario,
        metrics: ScenarioMetrics,
        tribunal_results: list[dict] | None = None,
    ) -> Path:
        """Write per-scenario step report. Returns the report file path."""
        self._scenario_metrics.append(metrics)
        content = build_scenario_report(scenario, metrics, tribunal_results)
        out_path = self.reports_dir / f"{self.run_id}__{scenario.scenario_id}.md"
        out_path.write_text(content, encoding="utf-8")
        return out_path

    # ---- Full camp ----

    def write_full_report(
        self,
        summary: CampRunSummary,
        all_metrics: list[ScenarioMetrics] | None = None,
    ) -> tuple[Path, Path]:
        """Write the full camp Markdown + JSON reports. Returns (md_path, json_path)."""
        metrics = all_metrics or self._scenario_metrics
        md_content = build_full_report(summary, metrics, mode=self.mode, run_id=self.run_id)
        json_content = json.dumps(
            {
                "run_id": self.run_id,
                "mode": self.mode,
                "summary": summary.to_dict(),
                "scenarios": [m.to_dict() for m in metrics],
            },
            indent=2,
        )
        md_path = self.reports_dir / f"{self.run_id}__full_camp.md"
        json_path = self.reports_dir / f"{self.run_id}__full_camp.json"
        md_path.write_text(md_content, encoding="utf-8")
        json_path.write_text(json_content, encoding="utf-8")
        return md_path, json_path

    def print_summary(self, summary: CampRunSummary) -> None:
        """Print a compact summary to stdout — useful after write_full_report."""
        print(f"\n{'='*64}")
        print(f"  ENGRAM V2 CAMP REPORT  [{self.run_id}]  mode={self.mode.upper()}")
        print(f"{'='*64}")
        print(f"  Scenarios : {summary.passed_scenarios}/{summary.total_scenarios} passed ({summary.pass_rate:.0%})")
        print(f"  Quality   : avg={summary.avg_quality_score:.1f}  min={summary.min_quality_score:.1f}")
        print(f"  Latency   : avg={summary.avg_latency_ms:.1f}ms  p99={summary.p99_latency_ms:.1f}ms")
        print(f"  Heals     : {summary.total_heal_cycles} total  |  1st-pass={summary.adversary_first_pass_rate:.0%}")
        if summary.regression_pass:
            print("  Gates     : ✅ ALL GREEN")
        else:
            print(f"  Gates     : ❌ FLAGS → {summary.regression_flags}")
        md_path = self.reports_dir / f"{self.run_id}__full_camp.md"
        json_path = self.reports_dir / f"{self.run_id}__full_camp.json"
        if md_path.exists():
            print(f"\n  Report MD  : {md_path}")
        if json_path.exists():
            print(f"  Report JSON: {json_path}")
        print(f"{'='*64}\n")
