"""Tests for engram.graph_healer — DAG integrity + targeted rollback."""

from __future__ import annotations

import contextlib
from uuid import uuid4

from engram_v2.graph_healer import (
    HealingReport,
    validate_and_heal,
    validate_engram_output,
)
from engram_v2.graph_store import EngramGraph
from engram_v2.schema import (
    LogicEngram,
    SynapticEdge,
)

# ── Healthy graph ────────────────────────────────────────────


class TestHealthyGraph:
    def test_connected_graph_healthy(self, two_node_graph: EngramGraph):
        report = validate_and_heal(two_node_graph)
        assert report.is_healthy
        assert len(report.engrams_rolled_back) == 0

    def test_empty_graph_healthy(self, empty_graph: EngramGraph):
        report = validate_and_heal(empty_graph)
        assert report.is_healthy


# ── Empty logic_body healing ─────────────────────────────────


class TestEmptyBodyHealing:
    def test_empty_body_detected_and_rolled_back(self, empty_graph: EngramGraph):
        good = LogicEngram(intent="good", ast_signature="def f():", logic_body="return 1")
        bad = LogicEngram(intent="bad", ast_signature="def g():", logic_body="")
        empty_graph.add_engram(good)
        empty_graph.add_engram(bad)
        empty_graph.add_edge(SynapticEdge(source_id=good.engram_id, target_id=bad.engram_id))

        report = validate_and_heal(empty_graph)
        assert bad.engram_id in report.engrams_rolled_back
        assert not empty_graph.has_engram(bad.engram_id)

    def test_good_engrams_survive_healing(self, empty_graph: EngramGraph):
        good = LogicEngram(intent="good", ast_signature="def f():", logic_body="return 1")
        bad = LogicEngram(intent="bad", ast_signature="def g():", logic_body="")
        empty_graph.add_engram(good)
        empty_graph.add_engram(bad)

        validate_and_heal(empty_graph)
        assert empty_graph.has_engram(good.engram_id)


# ── Dangling edge healing ───────────────────────────────────


class TestDanglingEdgeHealing:
    def test_phantom_node_detected_by_healer(self, empty_graph: EngramGraph):
        """Phantom node in NetworkX graph (not in _engrams) should be flagged."""
        e1 = LogicEngram(intent="real", ast_signature="def f():", logic_body="return 1")
        e2 = LogicEngram(intent="dep", ast_signature="def g():", logic_body="return 2")
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        edge = SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id)
        empty_graph.add_edge(edge)

        # Corrupt: manually inject a dangling edge to phantom in the _edges dict
        phantom_id = uuid4()
        bad_edge = SynapticEdge(source_id=e1.engram_id, target_id=phantom_id)
        empty_graph._edges[bad_edge.edge_id] = bad_edge

        issues = empty_graph.validate_integrity()
        assert any("missing from graph" in i for i in issues)


# ── HealingReport ────────────────────────────────────────────


class TestHealingReport:
    def test_report_serialization(self):
        report = HealingReport(
            issues_found=["empty body"],
            engrams_rolled_back=[uuid4()],
            edges_removed=1,
            is_healthy=False,
        )
        d = report.to_dict()
        assert d["is_healthy"] is False
        assert len(d["engrams_rolled_back"]) == 1
        assert d["edges_removed"] == 1


# ── validate_engram_output ───────────────────────────────────


class TestValidateEngramOutput:
    def test_valid_python_engram(self):
        e = LogicEngram(
            intent="add numbers",
            ast_signature="def add(a, b):",
            logic_body="def add(a, b):\n    return a + b",
        )
        issues = validate_engram_output(e)
        assert issues == []

    def test_syntax_error_detected(self):
        e = LogicEngram(
            intent="broken",
            ast_signature="def f():",
            logic_body="def f(:\n    return",
        )
        issues = validate_engram_output(e)
        assert any("SyntaxError" in i for i in issues)

    def test_empty_body_flagged(self):
        e = LogicEngram(
            intent="empty",
            ast_signature="def f():",
            logic_body="   ",
        )
        issues = validate_engram_output(e)
        assert any("empty logic_body" in i for i in issues)

    def test_missing_intent_flagged(self):
        e = LogicEngram(
            intent="",
            ast_signature="def f():",
            logic_body="pass",
        )
        issues = validate_engram_output(e)
        assert any("missing intent" in i for i in issues)

    def test_missing_signature_flagged(self):
        e = LogicEngram(
            intent="stuff",
            ast_signature="",
            logic_body="pass",
        )
        issues = validate_engram_output(e)
        assert any("missing ast_signature" in i for i in issues)


# ── Phantom element purge (edge corruption fix) ─────────────


class TestPhantomPurge:
    """Tests for the Phase 0 phantom cleanup in validate_and_heal."""

    def test_phantom_node_purged(self, empty_graph: EngramGraph):
        """A node in NetworkX but not in _engrams should be removed."""
        e1 = LogicEngram(intent="real", ast_signature="def f():", logic_body="return 1")
        empty_graph.add_engram(e1)
        # Inject phantom node directly into NetworkX
        empty_graph._g.add_node("phantom-node-id")
        assert "phantom-node-id" in empty_graph._g

        report = validate_and_heal(empty_graph)
        assert "phantom-node-id" not in empty_graph._g
        assert report.is_healthy

    def test_phantom_edge_purged(self, empty_graph: EngramGraph):
        """An edge in NetworkX but not in _edges should be removed."""
        e1 = LogicEngram(intent="real", ast_signature="def f():", logic_body="return 1")
        e2 = LogicEngram(intent="dep", ast_signature="def g():", logic_body="return 2")
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        # Add a valid registered edge
        edge = SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id)
        empty_graph.add_edge(edge)
        # Inject phantom edge directly into NetworkX (bypassing _edges)
        phantom_id = uuid4()
        empty_graph._g.add_node(str(phantom_id))
        empty_graph._g.add_edge(str(e1.engram_id), str(phantom_id))

        report = validate_and_heal(empty_graph)
        assert report.edges_removed >= 1
        assert str(phantom_id) not in empty_graph._g
        assert report.is_healthy

    def test_phantom_purge_preserves_valid_edges(self, empty_graph: EngramGraph):
        """Valid registered edges should not be affected by phantom purge."""
        e1 = LogicEngram(intent="a", ast_signature="def a():", logic_body="return 1")
        e2 = LogicEngram(intent="b", ast_signature="def b():", logic_body="return 2")
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        edge = SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id)
        empty_graph.add_edge(edge)

        # No phantom elements — should not remove anything
        report = validate_and_heal(empty_graph)
        assert report.edges_removed == 0
        assert empty_graph._g.has_edge(str(e1.engram_id), str(e2.engram_id))
        assert report.is_healthy

    def test_edge_corruption_scenario_heals(self):
        """The exact scenario from fault_injection: phantom node + unregistered edge."""
        import uuid

        from engram_v2.ast_decomposer import decompose_module
        from engram_v2.schema import Domain

        from experiments.project_engram.harness.mock_llm import MockLLM

        mock = MockLLM()
        graph = EngramGraph()
        templates = mock.get_templates("L1")

        for filepath, source in templates.items():
            if filepath.endswith(".py"):
                result = decompose_module(source, filepath, Domain.BACKEND)
                for engram in result.engrams:
                    graph.add_engram(engram)
                for edge in result.edges:
                    with contextlib.suppress(Exception):
                        graph.add_edge(edge)

        # Inject corruption: phantom node + unregistered edge
        phantom_id = uuid.uuid4()
        engram_ids = list(graph._engrams.keys())
        graph._g.add_edge(str(engram_ids[0]), str(phantom_id))

        report = validate_and_heal(graph)
        # Phantom edge was purged
        assert report.edges_removed >= 1
        # Phantom node no longer in NetworkX
        assert str(phantom_id) not in graph._g
        # Only remaining issues should be "orphan" (pre-existing, not corruption)
        non_orphan_issues = [i for i in report.issues_found if "orphan" not in i]
        assert len(non_orphan_issues) == 0, f"Non-orphan issues remain: {non_orphan_issues}"
