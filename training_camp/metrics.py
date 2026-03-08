"""
Training Camp Metrics — Regression-proof metric collection for V2 benchmarks.

Every run produces a CampMetrics record. The metrics gate ensures no regression
vs the established Phase 1 Track B benchmarks:
  Phase 1 L2: −53% time, −63% cost vs Track A
  Phase 1 L3: −34% time, −39% cost vs Track A

V2 quality floor: adversary_pass_rate ≥ 0.80, tribunal_confidence ≥ 90.0
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


@dataclass
class ScenarioMetrics:
    """Metrics for a single scenario run."""

    scenario_id: str
    level: str
    passed: bool
    total_latency_ms: float
    jit_sources_added: int
    adversary_rules_checked: int
    heal_cycles: int
    quality_score: float = 0.0
    adversary_passed_on_first_try: bool = True
    engram_count: int = 0
    edge_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "level": self.level,
            "passed": self.passed,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "jit_sources_added": self.jit_sources_added,
            "adversary_rules_checked": self.adversary_rules_checked,
            "heal_cycles": self.heal_cycles,
            "quality_score": round(self.quality_score, 2),
            "adversary_passed_on_first_try": self.adversary_passed_on_first_try,
            "engram_count": self.engram_count,
            "edge_count": self.edge_count,
            "timestamp": self.timestamp,
        }


@dataclass
class CampRunSummary:
    """Aggregated summary of a full training camp run."""

    run_id: str
    started_at: str
    completed_at: str = ""
    total_scenarios: int = 0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    # Latency stats
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    # Quality stats
    avg_quality_score: float = 0.0
    min_quality_score: float = 0.0
    # Tribunal stats
    total_heal_cycles: int = 0
    adversary_first_pass_rate: float = 0.0
    # Regression gates
    regression_flags: list[str] = field(default_factory=list)
    regression_pass: bool = True

    @property
    def pass_rate(self) -> float:
        if self.total_scenarios == 0:
            return 0.0
        return self.passed_scenarios / self.total_scenarios

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "pass_rate": round(self.pass_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "avg_quality_score": round(self.avg_quality_score, 2),
            "min_quality_score": round(self.min_quality_score, 2),
            "total_heal_cycles": self.total_heal_cycles,
            "adversary_first_pass_rate": round(self.adversary_first_pass_rate, 3),
            "regression_flags": self.regression_flags,
            "regression_pass": self.regression_pass,
        }


# ── Regression gates (Phase 1 Track B baselines) ─────────────

# These are the established Phase 1 benchmarks we must NOT regress below.
REGRESSION_GATES = {
    # Adversary offline check must be < 10ms per engram
    "adversary_latency_per_engram_ms": 10.0,
    # V2 tribunal overall pass rate (including healed engrams)
    "tribunal_pass_rate_min": 0.90,
    # Average quality floor (CAS-equivalent)
    "avg_quality_min": 90.0,
    # Adversary first-pass rate (before healing) — must be > 60% (flawed code seeds are expected)
    "adversary_first_pass_rate_min": 0.60,
    # Total heal cycles across all scenarios — healing too often indicates poor generation
    "heal_cycles_per_scenario_max": 3.0,
}


class MetricsCollector:
    """Collects, aggregates, and validates training camp metrics."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.started_at = datetime.now(UTC).isoformat()
        self._records: list[ScenarioMetrics] = []

    def record(self, metrics: ScenarioMetrics) -> None:
        self._records.append(metrics)

    def get_records(self) -> list[ScenarioMetrics]:
        return list(self._records)

    def summarize(self) -> CampRunSummary:
        """Compute aggregated summary and run regression gates."""
        summary = CampRunSummary(
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=datetime.now(UTC).isoformat(),
            total_scenarios=len(self._records),
        )
        if not self._records:
            return summary

        latencies = [r.total_latency_ms for r in self._records]
        qualities = [r.quality_score for r in self._records]
        passed = [r for r in self._records if r.passed]
        first_pass = [r for r in self._records if r.adversary_passed_on_first_try]

        summary.passed_scenarios = len(passed)
        summary.failed_scenarios = summary.total_scenarios - len(passed)
        summary.avg_latency_ms = statistics.mean(latencies) if latencies else 0.0
        summary.p99_latency_ms = sorted(latencies)[int(len(latencies) * 0.99)] if latencies else 0.0
        summary.avg_quality_score = statistics.mean(qualities) if qualities else 0.0
        summary.min_quality_score = min(qualities) if qualities else 0.0
        summary.total_heal_cycles = sum(r.heal_cycles for r in self._records)
        summary.adversary_first_pass_rate = len(first_pass) / len(self._records) if self._records else 0.0

        # ── Regression gate checks ────────────────────────────
        flags: list[str] = []

        if summary.pass_rate < REGRESSION_GATES["tribunal_pass_rate_min"]:
            flags.append(
                f"TRIBUNAL_PASS_RATE: {summary.pass_rate:.2%} < "
                f"{REGRESSION_GATES['tribunal_pass_rate_min']:.2%}"
            )

        if summary.avg_quality_score < REGRESSION_GATES["avg_quality_min"]:
            flags.append(
                f"AVG_QUALITY: {summary.avg_quality_score:.1f} < "
                f"{REGRESSION_GATES['avg_quality_min']}"
            )

        if summary.adversary_first_pass_rate < REGRESSION_GATES["adversary_first_pass_rate_min"]:
            flags.append(
                f"ADVERSARY_FIRST_PASS: {summary.adversary_first_pass_rate:.2%} < "
                f"{REGRESSION_GATES['adversary_first_pass_rate_min']:.2%}"
            )

        heal_per_scenario = (
            summary.total_heal_cycles / summary.total_scenarios
            if summary.total_scenarios > 0 else 0.0
        )
        if heal_per_scenario > REGRESSION_GATES["heal_cycles_per_scenario_max"]:
            flags.append(
                f"HEAL_CYCLES_PER_SCENARIO: {heal_per_scenario:.1f} > "
                f"{REGRESSION_GATES['heal_cycles_per_scenario_max']}"
            )

        summary.regression_flags = flags
        summary.regression_pass = len(flags) == 0
        return summary
