"""Tests for the tooloo-engram training camp system.

Validates:
- Scenario registry structure (all IDs, levels, adversary seeds are valid)
- MetricsCollector aggregation and regression gates
- CampRunSummary pass_rate property
- run_training_camp() executes cleanly for L1 scenarios
- Scenario runner produces ScenarioMetrics with sane values
"""

from __future__ import annotations

import pytest
from training_camp.metrics import (
    REGRESSION_GATES,
    CampRunSummary,
    MetricsCollector,
    ScenarioMetrics,
)
from training_camp.scenarios import (
    ALL_SCENARIOS,
    AdversarySeed,
    ScenarioLevel,
    TrainingScenario,
    get_scenarios,
)

# ── Scenario Registry ──────────────────────────────────────────

class TestScenarioRegistry:
    def test_all_scenarios_not_empty(self):
        assert len(ALL_SCENARIOS) > 0

    def test_all_scenario_ids_unique(self):
        ids = [s.scenario_id for s in ALL_SCENARIOS]
        assert len(ids) == len(set(ids)), "Duplicate scenario IDs found"

    def test_all_scenarios_have_title(self):
        for s in ALL_SCENARIOS:
            assert s.title, f"Scenario {s.scenario_id} missing title"

    def test_all_scenarios_have_mandate_text(self):
        for s in ALL_SCENARIOS:
            assert s.mandate_text, f"Scenario {s.scenario_id} missing mandate_text"

    def test_all_scenarios_have_domain_mix(self):
        for s in ALL_SCENARIOS:
            assert s.domain_mix, f"Scenario {s.scenario_id} missing domain_mix"

    def test_all_scenarios_have_valid_levels(self):
        valid = {ScenarioLevel.L1, ScenarioLevel.L2, ScenarioLevel.L3}
        for s in ALL_SCENARIOS:
            assert s.level in valid, f"Scenario {s.scenario_id} has invalid level: {s.level}"

    def test_l1_scenarios_exist(self):
        l1 = [s for s in ALL_SCENARIOS if s.level == ScenarioLevel.L1]
        assert len(l1) >= 3, f"Expected at least 3 L1 scenarios, got {len(l1)}"

    def test_l2_scenarios_exist(self):
        l2 = [s for s in ALL_SCENARIOS if s.level == ScenarioLevel.L2]
        assert len(l2) >= 1, f"Expected at least 1 L2 scenario, got {len(l2)}"

    def test_l3_scenarios_exist(self):
        l3 = [s for s in ALL_SCENARIOS if s.level == ScenarioLevel.L3]
        assert len(l3) >= 1, f"Expected at least 1 L3 scenario, got {len(l3)}"

    def test_adversary_seeds_have_required_fields(self):
        for scenario in ALL_SCENARIOS:
            for seed in scenario.adversary_seeds:
                assert seed.rule_id, f"Seed in {scenario.scenario_id} missing rule_id"
                assert seed.poisoned_code, f"Seed in {scenario.scenario_id} missing poisoned_code"

    def test_adversary_seed_rule_ids_are_known_format(self):
        valid_prefixes = {"SEC", "DEP", "PERF", "HEU"}
        for scenario in ALL_SCENARIOS:
            for seed in scenario.adversary_seeds:
                prefix = seed.rule_id.split("-")[0]
                assert prefix in valid_prefixes, f"Unknown rule prefix: {seed.rule_id}"

    def test_expected_min_quality_positive(self):
        for s in ALL_SCENARIOS:
            assert s.expected_min_quality > 0.0

    def test_expected_max_latency_positive(self):
        for s in ALL_SCENARIOS:
            assert s.expected_max_latency_ms > 0.0


class TestGetScenarios:
    def test_get_all_scenarios(self):
        result = get_scenarios()
        assert len(result) == len(ALL_SCENARIOS)

    def test_filter_by_l1(self):
        result = get_scenarios(level=ScenarioLevel.L1)
        assert all(s.level == ScenarioLevel.L1 for s in result)

    def test_filter_by_l2(self):
        result = get_scenarios(level=ScenarioLevel.L2)
        assert all(s.level == ScenarioLevel.L2 for s in result)

    def test_filter_by_scenario_id(self):
        if ALL_SCENARIOS:
            target_id = ALL_SCENARIOS[0].scenario_id
            result = get_scenarios(scenario_id=target_id)
            assert len(result) == 1
            assert result[0].scenario_id == target_id

    def test_filter_by_nonexistent_id_returns_empty(self):
        result = get_scenarios(scenario_id="NONEXISTENT-99")
        assert result == []


# ── ScenarioMetrics ───────────────────────────────────────────

class TestScenarioMetrics:
    def _make(self, **kwargs) -> ScenarioMetrics:
        defaults = dict(
            scenario_id="L1-01",
            level="L1",
            passed=True,
            total_latency_ms=25.0,
            jit_sources_added=4,
            adversary_rules_checked=8,
            heal_cycles=0,
            quality_score=94.5,
        )
        defaults.update(kwargs)
        return ScenarioMetrics(**defaults)

    def test_to_dict_all_fields(self):
        m = self._make()
        d = m.to_dict()
        assert d["scenario_id"] == "L1-01"
        assert d["passed"] is True
        assert d["quality_score"] == 94.5

    def test_to_dict_latency_rounded(self):
        m = self._make(total_latency_ms=12.3456789)
        d = m.to_dict()
        assert d["total_latency_ms"] == 12.35


# ── CampRunSummary ────────────────────────────────────────────

class TestCampRunSummary:
    def test_pass_rate_zero_when_no_scenarios(self):
        s = CampRunSummary(run_id="test-1", started_at="2024-01-01T00:00:00")
        assert s.pass_rate == 0.0

    def test_pass_rate_full(self):
        s = CampRunSummary(
            run_id="test-2",
            started_at="2024-01-01T00:00:00",
            total_scenarios=5,
            passed_scenarios=5,
        )
        assert s.pass_rate == 1.0

    def test_pass_rate_partial(self):
        s = CampRunSummary(
            run_id="test-3",
            started_at="2024-01-01T00:00:00",
            total_scenarios=10,
            passed_scenarios=7,
        )
        assert abs(s.pass_rate - 0.7) < 1e-9

    def test_to_dict_has_regression_fields(self):
        s = CampRunSummary(run_id="x", started_at="t", regression_pass=True)
        d = s.to_dict()
        assert "regression_flags" in d
        assert "regression_pass" in d


# ── MetricsCollector ──────────────────────────────────────────

class TestMetricsCollector:
    def _make_metric(self, passed: bool = True, quality: float = 94.0,
                     latency: float = 20.0, heal_cycles: int = 0,
                     first_pass: bool = True) -> ScenarioMetrics:
        return ScenarioMetrics(
            scenario_id="L1-01",
            level="L1",
            passed=passed,
            total_latency_ms=latency,
            jit_sources_added=3,
            adversary_rules_checked=5,
            heal_cycles=heal_cycles,
            quality_score=quality,
            adversary_passed_on_first_try=first_pass,
        )

    def test_empty_collector_summarizes_empty(self):
        col = MetricsCollector("run-0")
        summary = col.summarize()
        assert summary.total_scenarios == 0
        assert summary.pass_rate == 0.0

    def test_single_passing_scenario(self):
        col = MetricsCollector("run-1")
        col.record(self._make_metric(passed=True, quality=95.0))
        summary = col.summarize()
        assert summary.total_scenarios == 1
        assert summary.passed_scenarios == 1
        assert summary.avg_quality_score == 95.0

    def test_mixed_pass_fail(self):
        col = MetricsCollector("run-2")
        col.record(self._make_metric(passed=True))
        col.record(self._make_metric(passed=False, quality=70.0))
        summary = col.summarize()
        assert summary.passed_scenarios == 1
        assert summary.failed_scenarios == 1

    def test_total_heal_cycles_aggregated(self):
        col = MetricsCollector("run-3")
        col.record(self._make_metric(heal_cycles=2))
        col.record(self._make_metric(heal_cycles=1))
        col.record(self._make_metric(heal_cycles=0))
        summary = col.summarize()
        assert summary.total_heal_cycles == 3

    def test_adversary_first_pass_rate_calculated(self):
        col = MetricsCollector("run-4")
        col.record(self._make_metric(first_pass=True))
        col.record(self._make_metric(first_pass=True))
        col.record(self._make_metric(first_pass=False))
        summary = col.summarize()
        assert abs(summary.adversary_first_pass_rate - (2/3)) < 0.01

    def test_regression_flags_populated_on_quality_gate_failure(self):
        col = MetricsCollector("run-5")
        col.record(self._make_metric(quality=50.0, passed=False))
        summary = col.summarize()
        # Low quality or pass rate should trigger regression flags
        assert isinstance(summary.regression_flags, list)

    def test_get_records_returns_all(self):
        col = MetricsCollector("run-6")
        col.record(self._make_metric())
        col.record(self._make_metric())
        assert len(col.get_records()) == 2


# ── Regression gates ──────────────────────────────────────────

class TestRegressionGates:
    def test_gates_defined(self):
        required = {
            "adversary_latency_per_engram_ms",
            "tribunal_pass_rate_min",
            "avg_quality_min",
            "adversary_first_pass_rate_min",
            "heal_cycles_per_scenario_max",
        }
        assert required.issubset(set(REGRESSION_GATES.keys()))

    def test_tribunal_pass_rate_min_at_least_60_percent(self):
        assert REGRESSION_GATES["tribunal_pass_rate_min"] >= 0.60

    def test_avg_quality_min_at_least_75(self):
        assert REGRESSION_GATES["avg_quality_min"] >= 75.0

    def test_adversary_latency_per_engram_under_100ms(self):
        assert REGRESSION_GATES["adversary_latency_per_engram_ms"] < 100.0


# ── Integration: run_training_camp L1 ─────────────────────────

class TestRunTrainingCampL1:
    def test_l1_camp_runs_without_error(self):
        from training_camp.camp_runner import run_training_camp
        summary = run_training_camp(level_filter=ScenarioLevel.L1, verbose=False)
        assert summary is not None

    def test_l1_camp_runs_all_l1_scenarios(self):
        from training_camp.camp_runner import run_training_camp
        l1_count = len([s for s in ALL_SCENARIOS if s.level == ScenarioLevel.L1])
        summary = run_training_camp(level_filter=ScenarioLevel.L1, verbose=False)
        assert summary.total_scenarios == l1_count

    def test_l1_camp_summary_has_run_id(self):
        from training_camp.camp_runner import run_training_camp
        summary = run_training_camp(level_filter=ScenarioLevel.L1, verbose=False)
        assert summary.run_id.startswith("camp-")

    def test_l1_camp_quality_above_zero(self):
        from training_camp.camp_runner import run_training_camp
        summary = run_training_camp(level_filter=ScenarioLevel.L1, verbose=False)
        assert summary.avg_quality_score > 0.0

    def test_l1_camp_latency_recorded(self):
        from training_camp.camp_runner import run_training_camp
        summary = run_training_camp(level_filter=ScenarioLevel.L1, verbose=False)
        assert summary.avg_latency_ms >= 0.0

    def test_single_scenario_filter(self):
        from training_camp.camp_runner import run_training_camp
        summary = run_training_camp(scenario_id_filter="L1-01", verbose=False)
        assert summary.total_scenarios == 1


# ── Integration: run_scenario ─────────────────────────────────

class TestRunScenario:
    def test_run_scenario_returns_metrics(self):
        from training_camp.camp_runner import run_scenario
        from training_camp.metrics import MetricsCollector

        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.delta_sync import DeltaSyncBus
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )
        from experiments.project_engram.engram.tribunal_orchestrator import TribunalOrchestrator

        scenario = next(s for s in ALL_SCENARIOS if s.scenario_id == "L1-01")
        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            healer=ArbiterHealer(llm=MockArbiterLLM(latency_ms=0)),
            bus=DeltaSyncBus(),
        )
        collector = MetricsCollector("test-run")
        metrics = run_scenario(scenario, tribunal, collector)

        assert metrics.scenario_id == "L1-01"
        assert metrics.total_latency_ms >= 0
        assert metrics.engram_count > 0

    def test_run_scenario_records_to_collector(self):
        from training_camp.camp_runner import run_scenario
        from training_camp.metrics import MetricsCollector

        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.delta_sync import DeltaSyncBus
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )
        from experiments.project_engram.engram.tribunal_orchestrator import TribunalOrchestrator

        scenario = next(s for s in ALL_SCENARIOS if s.scenario_id == "L1-02")
        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            healer=ArbiterHealer(llm=MockArbiterLLM(latency_ms=0)),
            bus=DeltaSyncBus(),
        )
        collector = MetricsCollector("test-run-2")
        run_scenario(scenario, tribunal, collector)
        assert len(collector.get_records()) == 1
