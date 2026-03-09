"""Tests for engram.graph_store — NetworkX DAG wrapper."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from engram_v2.graph_store import (
    CycleDetectedError,
    EngramGraph,
)
from engram_v2.schema import (
    Domain,
    LogicEngram,
    SynapticEdge,
)

# ── Node operations ──────────────────────────────────────────


class TestNodeOperations:
    def test_add_and_retrieve(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="test", ast_signature="def f():", logic_body="pass")
        eid = empty_graph.add_engram(e)
        assert empty_graph.get_engram(eid) is e
        assert empty_graph.has_engram(eid)

    def test_get_missing_returns_none(self, empty_graph: EngramGraph):
        assert empty_graph.get_engram(uuid4()) is None

    def test_has_engram_false_for_missing(self, empty_graph: EngramGraph):
        assert not empty_graph.has_engram(uuid4())


# ── Edge operations / DAG enforcement ────────────────────────


class TestEdgeOperations:
    def test_add_valid_edge(self, two_node_graph: EngramGraph):
        stats = two_node_graph.stats()
        assert stats["edge_count"] == 1

    def test_edge_marked_verified(self, empty_graph: EngramGraph):
        e1 = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        e2 = LogicEngram(intent="b", ast_signature="def b():", logic_body="pass")
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        edge = SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id)
        empty_graph.add_edge(edge)
        assert edge.verified is True

    def test_cycle_rejected(self, empty_graph: EngramGraph):
        e1 = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        e2 = LogicEngram(intent="b", ast_signature="def b():", logic_body="pass")
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        empty_graph.add_edge(SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id))
        with pytest.raises(CycleDetectedError):
            empty_graph.add_edge(SynapticEdge(source_id=e2.engram_id, target_id=e1.engram_id))

    def test_self_loop_rejected(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        empty_graph.add_engram(e)
        with pytest.raises(CycleDetectedError):
            empty_graph.add_edge(SynapticEdge(source_id=e.engram_id, target_id=e.engram_id))

    def test_missing_source_raises(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        empty_graph.add_engram(e)
        with pytest.raises(ValueError, match="missing nodes"):
            empty_graph.add_edge(SynapticEdge(source_id=uuid4(), target_id=e.engram_id))

    def test_missing_target_raises(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        empty_graph.add_engram(e)
        with pytest.raises(ValueError, match="missing nodes"):
            empty_graph.add_edge(SynapticEdge(source_id=e.engram_id, target_id=uuid4()))

    def test_triangle_dag_ok(self, empty_graph: EngramGraph):
        """A→B, A→C, B→C should be fine (no cycle)."""
        a = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        b = LogicEngram(intent="b", ast_signature="def b():", logic_body="pass")
        c = LogicEngram(intent="c", ast_signature="def c():", logic_body="pass")
        empty_graph.add_engram(a)
        empty_graph.add_engram(b)
        empty_graph.add_engram(c)
        empty_graph.add_edge(SynapticEdge(source_id=a.engram_id, target_id=b.engram_id))
        empty_graph.add_edge(SynapticEdge(source_id=a.engram_id, target_id=c.engram_id))
        empty_graph.add_edge(SynapticEdge(source_id=b.engram_id, target_id=c.engram_id))
        assert empty_graph.stats()["edge_count"] == 3

    def test_three_node_cycle_rejected(self, empty_graph: EngramGraph):
        """A→B, B→C, C→A should be rejected."""
        a = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        b = LogicEngram(intent="b", ast_signature="def b():", logic_body="pass")
        c = LogicEngram(intent="c", ast_signature="def c():", logic_body="pass")
        empty_graph.add_engram(a)
        empty_graph.add_engram(b)
        empty_graph.add_engram(c)
        empty_graph.add_edge(SynapticEdge(source_id=a.engram_id, target_id=b.engram_id))
        empty_graph.add_edge(SynapticEdge(source_id=b.engram_id, target_id=c.engram_id))
        with pytest.raises(CycleDetectedError):
            empty_graph.add_edge(SynapticEdge(source_id=c.engram_id, target_id=a.engram_id))
        # The rejected edge should not remain
        assert empty_graph.stats()["edge_count"] == 2


# ── Query operations ─────────────────────────────────────────


class TestQueryOperations:
    def test_query_by_intent(self, empty_graph: EngramGraph):
        e1 = LogicEngram(
            intent="user authentication via JWT", ast_signature="def auth():", logic_body="pass"
        )
        e2 = LogicEngram(
            intent="database connection pool", ast_signature="def pool():", logic_body="pass"
        )
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        results = empty_graph.query_by_intent("JWT authentication")
        assert len(results) >= 1
        assert results[0].engram_id == e1.engram_id

    def test_query_by_intent_empty_graph(self, empty_graph: EngramGraph):
        assert empty_graph.query_by_intent("anything") == []

    def test_query_by_intent_respects_top_k(self, empty_graph: EngramGraph):
        for i in range(10):
            empty_graph.add_engram(
                LogicEngram(intent=f"func_{i}", ast_signature=f"def f{i}():", logic_body="pass")
            )
        results = empty_graph.query_by_intent("func", top_k=3)
        assert len(results) <= 3

    def test_query_by_domain(self, empty_graph: EngramGraph):
        e1 = LogicEngram(
            intent="a", ast_signature="def a():", logic_body="pass", domain=Domain.BACKEND
        )
        e2 = LogicEngram(
            intent="b", ast_signature="def b():", logic_body="pass", domain=Domain.FRONTEND
        )
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        backend = empty_graph.query_by_domain(Domain.BACKEND)
        assert len(backend) == 1
        assert backend[0].engram_id == e1.engram_id

    def test_query_by_module(self, empty_graph: EngramGraph):
        e1 = LogicEngram(
            intent="a", ast_signature="def a():", logic_body="pass", module_path="m1.py"
        )
        e2 = LogicEngram(
            intent="b", ast_signature="def b():", logic_body="pass", module_path="m2.py"
        )
        empty_graph.add_engram(e1)
        empty_graph.add_engram(e2)
        results = empty_graph.query_by_module("m1.py")
        assert len(results) == 1


# ── Topological order ────────────────────────────────────────


class TestTopologicalOrder:
    def test_order_respects_edge_direction(self, empty_graph: EngramGraph):
        """Edge A→B means A comes before B in topo order (A is depended upon by B)."""
        a = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        b = LogicEngram(intent="b", ast_signature="def b():", logic_body="pass")
        c = LogicEngram(intent="c", ast_signature="def c():", logic_body="pass")
        empty_graph.add_engram(a)
        empty_graph.add_engram(b)
        empty_graph.add_engram(c)
        # a → b → c
        empty_graph.add_edge(SynapticEdge(source_id=a.engram_id, target_id=b.engram_id))
        empty_graph.add_edge(SynapticEdge(source_id=b.engram_id, target_id=c.engram_id))
        order = empty_graph.topological_order()
        assert order.index(a.engram_id) < order.index(b.engram_id)
        assert order.index(b.engram_id) < order.index(c.engram_id)

    def test_empty_graph_order(self, empty_graph: EngramGraph):
        assert empty_graph.topological_order() == []


# ── Subgraph extraction ─────────────────────────────────────


class TestSubgraph:
    def test_depth_limited(self, empty_graph: EngramGraph):
        nodes = []
        for i in range(5):
            e = LogicEngram(intent=f"n{i}", ast_signature=f"def n{i}():", logic_body="pass")
            empty_graph.add_engram(e)
            nodes.append(e)
        # Chain: 0→1→2→3→4
        for i in range(4):
            empty_graph.add_edge(
                SynapticEdge(source_id=nodes[i].engram_id, target_id=nodes[i + 1].engram_id)
            )
        sub = empty_graph.get_dependency_subgraph(nodes[0].engram_id, depth=2)
        # Depth=2 from node 0 should reach 0,1,2
        assert len(sub.nodes()) >= 2

    def test_missing_node_returns_empty(self, empty_graph: EngramGraph):
        sub = empty_graph.get_dependency_subgraph(uuid4(), depth=2)
        assert len(sub.nodes()) == 0


# ── Rollback ─────────────────────────────────────────────────


class TestRollback:
    def test_rollback_removes_node_and_edges(self, two_node_graph: EngramGraph):
        stats_before = two_node_graph.stats()
        assert stats_before["engram_count"] == 2
        assert stats_before["edge_count"] == 1

        # Roll back the second node
        eids = list(two_node_graph._engrams.keys())
        two_node_graph.rollback_engram(eids[1])

        stats_after = two_node_graph.stats()
        assert stats_after["engram_count"] == 1
        assert stats_after["edge_count"] == 0

    def test_rollback_missing_returns_false(self, empty_graph: EngramGraph):
        assert not empty_graph.rollback_engram(uuid4())


# ── Integrity validation ────────────────────────────────────


class TestValidateIntegrity:
    def test_healthy_graph(self, two_node_graph: EngramGraph):
        issues = two_node_graph.validate_integrity()
        # Two connected nodes → no orphans
        assert not any("CRITICAL" in i for i in issues)

    def test_empty_body_flagged(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="empty", ast_signature="def f():", logic_body="")
        empty_graph.add_engram(e)
        issues = empty_graph.validate_integrity()
        assert any("empty logic_body" in i for i in issues)


# ── Serialization round-trip ─────────────────────────────────


class TestSerialization:
    def test_serialize_deserialize_roundtrip(self, two_node_graph: EngramGraph):
        json_str = two_node_graph.serialize()
        restored = EngramGraph.deserialize(json_str)
        assert restored.stats()["engram_count"] == 2
        assert restored.stats()["edge_count"] == 1

    def test_serialize_is_valid_json(self, two_node_graph: EngramGraph):
        data = json.loads(two_node_graph.serialize())
        assert "engrams" in data
        assert "edges" in data

    def test_empty_graph_roundtrip(self, empty_graph: EngramGraph):
        restored = EngramGraph.deserialize(empty_graph.serialize())
        assert restored.stats()["engram_count"] == 0


# ── Stats & token summary ───────────────────────────────────


class TestStats:
    def test_stats_keys(self, two_node_graph: EngramGraph):
        s = two_node_graph.stats()
        assert "engram_count" in s
        assert "edge_count" in s
        assert "nodes" in s
        assert "edges" in s
        assert "max_depth" in s
        assert "connected_components" in s
        assert "domains" in s
        assert "modules" in s

    def test_empty_stats(self, empty_graph: EngramGraph):
        s = empty_graph.stats()
        assert s["engram_count"] == 0
        assert s["edge_count"] == 0

    def test_token_summary_contains_topology(self, two_node_graph: EngramGraph):
        summary = two_node_graph.to_token_summary()
        assert "<graph_topology>" in summary
        assert "models" in summary or "service" in summary


# ── Decay radius ─────────────────────────────────────────────


class TestDecayRadius:
    def _build_chain(self, length: int, radius: int) -> tuple[EngramGraph, list]:
        """Build a linear chain of `length` engrams with given decay_radius."""
        g = EngramGraph(decay_radius=radius)
        engrams = []
        for i in range(length):
            e = LogicEngram(
                intent=f"node_{i}",
                ast_signature=f"def f{i}():",
                logic_body="pass",
            )
            g.add_engram(e)
            engrams.append(e)
        for i in range(length - 1):
            g.add_edge(
                SynapticEdge(
                    source_id=engrams[i].engram_id,
                    target_id=engrams[i + 1].engram_id,
                )
            )
        return g, engrams

    def test_default_decay_radius_is_three(self):
        g = EngramGraph()
        assert g._decay_radius == 3

    def test_custom_decay_radius(self):
        g = EngramGraph(decay_radius=5)
        assert g._decay_radius == 5

    def test_subgraph_respects_decay_radius(self):
        g, engrams = self._build_chain(10, radius=2)
        sub = g.get_dependency_subgraph(engrams[0].engram_id)
        # With radius=2 from node 0: reaches node 0, 1, 2
        assert len(sub.nodes()) <= 3

    def test_larger_radius_captures_more(self):
        g, engrams = self._build_chain(10, radius=5)
        sub = g.get_dependency_subgraph(engrams[0].engram_id)
        assert len(sub.nodes()) <= 6

    def test_explicit_depth_overrides_radius(self):
        g, engrams = self._build_chain(10, radius=2)
        sub = g.get_dependency_subgraph(engrams[0].engram_id, depth=8)
        assert len(sub.nodes()) >= 5

    def test_decay_radius_serialization_roundtrip(self):
        g = EngramGraph(decay_radius=7)
        e = LogicEngram(intent="x", ast_signature="def x():", logic_body="pass")
        g.add_engram(e)
        restored = EngramGraph.deserialize(g.serialize())
        assert restored._decay_radius == 7
