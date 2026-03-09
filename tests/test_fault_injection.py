"""Tests for fault_injection.scenarios — stress/resilience tests."""

from __future__ import annotations

import pytest

from experiments.project_engram.fault_injection.scenarios import (
    BudgetCapMockLLM,
    FaultResult,
    TimeoutMockLLM,
    run_all_faults,
    run_budget_ceiling,
    run_edge_corruption,
    run_llm_timeout,
    run_syntax_corruption,
)
from experiments.project_engram.harness.config import L1_SIMPLE, L2_MEDIUM
from experiments.project_engram.harness.instrumentation import MetricsCollector


@pytest.fixture
def collector() -> MetricsCollector:
    return MetricsCollector()


# ── TimeoutMockLLM ───────────────────────────────────────────


class TestTimeoutMockLLM:
    def test_hangs_on_nth_call(self):
        llm = TimeoutMockLLM(hang_on_call=2, hang_duration_s=0.01)
        r1 = llm.query("sys", "first")
        assert len(r1) > 0
        r2 = llm.query("sys", "second")
        assert r2 == ""  # Timeout → empty
        r3 = llm.query("sys", "third")
        assert len(r3) > 0

    def test_hang_on_first_call(self):
        llm = TimeoutMockLLM(hang_on_call=1, hang_duration_s=0.01)
        r1 = llm.query("sys", "first")
        assert r1 == ""


# ── BudgetCapMockLLM ────────────────────────────────────────


class TestBudgetCapMockLLM:
    def test_stops_at_budget(self):
        llm = BudgetCapMockLLM(max_tokens=100)
        results = []
        for i in range(10):
            r = llm.query("sys", f"Generate file {i}")
            results.append(r)
        # At some point should return empty
        assert any(r == "" for r in results)
        assert llm.budget_exceeded

    def test_first_call_succeeds(self):
        llm = BudgetCapMockLLM(max_tokens=10000)
        r = llm.query("sys", "Generate something")
        assert len(r) > 0


# ── LLM Timeout Scenario ────────────────────────────────────


class TestLLMTimeoutScenario:
    def test_timeout_detected_track_a(self, collector: MetricsCollector):
        result = run_llm_timeout(L1_SIMPLE, "A", collector)
        assert isinstance(result, FaultResult)
        assert result.scenario == "llm_timeout"
        assert result.track == "A"

    def test_timeout_l2_recovers_with_retry(self, collector: MetricsCollector):
        result = run_llm_timeout(L2_MEDIUM, "A", collector)
        # With retry logic, L2 should now recover from the timeout
        assert result.recovered is True
        assert result.extra["retries_per_file"] == 2


# ── Syntax Corruption Scenario ───────────────────────────────


class TestSyntaxCorruptionScenario:
    def test_syntax_error_detected_track_a(self, collector: MetricsCollector):
        result = run_syntax_corruption(L1_SIMPLE, "A", collector)
        assert result.recovered  # Scorer should detect the error
        assert result.scenario == "syntax_corruption"

    def test_syntax_error_detected_track_b(self, collector: MetricsCollector):
        result = run_syntax_corruption(L1_SIMPLE, "B", collector)
        assert result.recovered

    def test_ast_score_reduced(self, collector: MetricsCollector):
        result = run_syntax_corruption(L2_MEDIUM, "A", collector)
        assert result.extra["ast_score"] < 30.0


# ── Edge Corruption Scenario ────────────────────────────────


class TestEdgeCorruptionScenario:
    def test_edge_corruption_runs(self, collector: MetricsCollector):
        result = run_edge_corruption(L1_SIMPLE, collector)
        assert result.scenario == "edge_corruption"
        assert result.track == "B"

    def test_edge_corruption_reports_issues(self, collector: MetricsCollector):
        result = run_edge_corruption(L2_MEDIUM, collector)
        assert len(result.extra["issues_found"]) >= 1


# ── Budget Ceiling Scenario ──────────────────────────────────


class TestBudgetCeilingScenario:
    def test_budget_cap_hit(self, collector: MetricsCollector):
        result = run_budget_ceiling(L2_MEDIUM, "A", collector)
        assert result.scenario == "budget_ceiling"
        assert result.extra["budget_exceeded"]

    def test_partial_completion(self, collector: MetricsCollector):
        result = run_budget_ceiling(L2_MEDIUM, "A", collector)
        # Should produce some but not all files
        assert result.extra["files_completed"] < result.extra["files_expected"]


# ── Full fault suite ─────────────────────────────────────────


class TestRunAllFaults:
    def test_all_scenarios_run(self, collector: MetricsCollector):
        results = run_all_faults(L1_SIMPLE, collector)
        scenarios = {r.scenario for r in results}
        assert "llm_timeout" in scenarios
        assert "syntax_corruption" in scenarios
        assert "budget_ceiling" in scenarios
        assert "edge_corruption" in scenarios

    def test_both_tracks_covered(self, collector: MetricsCollector):
        results = run_all_faults(L1_SIMPLE, collector)
        tracks = {r.track for r in results}
        assert "A" in tracks
        assert "B" in tracks

    def test_result_serialization(self, collector: MetricsCollector):
        results = run_all_faults(L1_SIMPLE, collector)
        for r in results:
            d = r.to_dict()
            assert "scenario" in d
            assert "recovered" in d
            assert isinstance(d["recovery_time_s"], float)
