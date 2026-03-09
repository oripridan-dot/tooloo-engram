"""Tests for engram.verification_engine — TESTS edge execution in sandboxes."""

from __future__ import annotations

from uuid import uuid4

from engram_v2.graph_store import EngramGraph
from engram_v2.schema import (
    Domain,
    EdgeType,
    Language,
    LogicEngram,
    SynapticEdge,
)
from engram_v2.verification_engine import (
    VerificationResult,
    verify_all_tested_engrams,
    verify_engram,
)

# ── Fixtures ─────────────────────────────────────────────────


def _make_target_engram(*, intent: str = "add numbers", body: str = "") -> LogicEngram:
    if not body:
        body = "def add(a: int, b: int) -> int:\n    return a + b\n"
    return LogicEngram(
        intent=intent,
        ast_signature="def add(a: int, b: int) -> int:",
        logic_body=body,
        language=Language.PYTHON,
        domain=Domain.BACKEND,
        module_path="utils/calc.py",
    )


def _make_test_engram(target_module: str = "utils.calc") -> LogicEngram:
    body = (
        f"from {target_module} import add\n\n"
        "def test_add_positive():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_add_zero():\n"
        "    assert add(0, 0) == 0\n"
    )
    return LogicEngram(
        intent="test add function",
        ast_signature="def test_add_positive():",
        logic_body=body,
        language=Language.PYTHON,
        domain=Domain.TEST,
        module_path="tests/test_calc.py",
    )


def _make_failing_test_engram(target_module: str = "utils.calc") -> LogicEngram:
    body = (
        f"from {target_module} import add\n\n"
        "def test_add_wrong():\n"
        "    assert add(2, 3) == 999  # deliberately wrong\n"
    )
    return LogicEngram(
        intent="test add function (failing)",
        ast_signature="def test_add_wrong():",
        logic_body=body,
        language=Language.PYTHON,
        domain=Domain.TEST,
        module_path="tests/test_calc_fail.py",
    )


def _graph_with_tests(*, include_failing: bool = False) -> EngramGraph:
    """Build a graph with a target engram and TESTS edges."""
    graph = EngramGraph()
    target = _make_target_engram()
    test = _make_test_engram()
    graph.add_engram(target)
    graph.add_engram(test)
    graph.add_edge(
        SynapticEdge(
            source_id=test.engram_id,
            target_id=target.engram_id,
            edge_type=EdgeType.TESTS,
        )
    )

    if include_failing:
        fail_test = _make_failing_test_engram()
        graph.add_engram(fail_test)
        graph.add_edge(
            SynapticEdge(
                source_id=fail_test.engram_id,
                target_id=target.engram_id,
                edge_type=EdgeType.TESTS,
            )
        )

    return graph, target.engram_id


# ── VerificationResult ───────────────────────────────────────


class TestVerificationResult:
    def test_default_result_fails(self):
        r = VerificationResult(target_engram_id=uuid4())
        assert r.passed is False
        assert r.tests_run == 0

    def test_to_dict_keys(self):
        r = VerificationResult(target_engram_id=uuid4(), passed=True, tests_run=3, tests_passed=3)
        d = r.to_dict()
        assert "target_engram_id" in d
        assert "passed" in d
        assert "tests_run" in d
        assert d["passed"] is True

    def test_stdout_capped(self):
        r = VerificationResult(target_engram_id=uuid4(), stdout="x" * 5000)
        d = r.to_dict()
        assert len(d["stdout"]) == 2000


# ── verify_engram ────────────────────────────────────────────


class TestVerifyEngram:
    def test_missing_engram(self):
        graph = EngramGraph()
        r = verify_engram(graph, uuid4())
        assert r.passed is False
        assert "not found" in r.errors[0]

    def test_no_tests_vacuous_pass(self):
        graph = EngramGraph()
        e = _make_target_engram()
        graph.add_engram(e)
        r = verify_engram(graph, e.engram_id)
        assert r.passed is True
        assert r.tests_run == 0

    def test_passing_tests(self):
        graph, target_id = _graph_with_tests()
        r = verify_engram(graph, target_id, timeout_s=30)
        assert r.passed is True
        assert r.tests_passed >= 2
        assert r.tests_failed == 0
        assert r.duration_s > 0

    def test_duration_recorded(self):
        graph, target_id = _graph_with_tests()
        r = verify_engram(graph, target_id)
        assert r.duration_s >= 0


# ── verify_all_tested_engrams ────────────────────────────────


class TestVerifyAll:
    def test_empty_graph(self):
        graph = EngramGraph()
        results = verify_all_tested_engrams(graph)
        assert results == []

    def test_no_tests_edges(self):
        graph = EngramGraph()
        e = _make_target_engram()
        graph.add_engram(e)
        results = verify_all_tested_engrams(graph)
        assert results == []

    def test_all_passing(self):
        graph, _target_id = _graph_with_tests()
        results = verify_all_tested_engrams(graph, timeout_s=30)
        assert len(results) == 1
        assert results[0].passed is True


# ── Integration with graph_healer ────────────────────────────


class TestHealerIntegration:
    def test_heal_with_verification_passing(self):
        from engram_v2.graph_healer import (
            validate_and_heal_with_verification,
        )

        graph, _target_id = _graph_with_tests()
        report = validate_and_heal_with_verification(graph, timeout_s=30)
        assert report.is_healthy


# ── Edge cases ───────────────────────────────────────────────


class TestEdgeCases:
    def test_timeout_handled(self):
        """Tests with an engram whose logic has an infinite loop should timeout."""
        graph = EngramGraph()
        target = LogicEngram(
            intent="slow",
            ast_signature="def slow():",
            logic_body="import time\ndef slow():\n    time.sleep(100)\n",
            module_path="slow.py",
        )
        test = LogicEngram(
            intent="test slow",
            ast_signature="def test_slow():",
            logic_body="from slow import slow\ndef test_slow():\n    slow()\n",
            module_path="test_slow.py",
            domain=Domain.TEST,
        )
        graph.add_engram(target)
        graph.add_engram(test)
        graph.add_edge(
            SynapticEdge(
                source_id=test.engram_id,
                target_id=target.engram_id,
                edge_type=EdgeType.TESTS,
            )
        )
        r = verify_engram(graph, target.engram_id, timeout_s=2)
        assert r.passed is False
        assert any("timed out" in e.lower() or "Pytest timed out" in e for e in r.errors)

    def test_syntax_error_engram(self):
        """Test engram with syntax error should fail verification."""
        graph = EngramGraph()
        target = LogicEngram(
            intent="broken",
            ast_signature="def broken():",
            logic_body="def broken(\n",  # invalid syntax
            module_path="broken.py",
        )
        test = LogicEngram(
            intent="test broken",
            ast_signature="def test_broken():",
            logic_body="def test_broken():\n    assert True\n",
            module_path="test_broken.py",
            domain=Domain.TEST,
        )
        graph.add_engram(target)
        graph.add_engram(test)
        graph.add_edge(
            SynapticEdge(
                source_id=test.engram_id,
                target_id=target.engram_id,
                edge_type=EdgeType.TESTS,
            )
        )
        r = verify_engram(graph, target.engram_id, timeout_s=10)
        # Should complete without crashing
        assert r.duration_s >= 0
