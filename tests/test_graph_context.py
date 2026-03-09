"""Tests for engram.graph_context — ContextTensor assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from engram_v2.graph_context import (
    assemble_full_graph_context,
    assemble_tensor,
)
from engram_v2.schema import (
    LogicEngram,
)

if TYPE_CHECKING:
    from engram_v2.graph_store import EngramGraph

# ── assemble_tensor ──────────────────────────────────────────


class TestAssembleTensor:
    def test_produces_context_tensor(self, two_node_graph: EngramGraph):
        eids = list(two_node_graph._engrams.keys())
        tensor = assemble_tensor(two_node_graph, [eids[0]], "Build user model")
        assert tensor.assembled_prompt
        assert len(tensor.target_engrams) == 1
        assert tensor.token_budget == 8000

    def test_includes_mandate_in_prompt(self, two_node_graph: EngramGraph):
        eids = list(two_node_graph._engrams.keys())
        tensor = assemble_tensor(two_node_graph, [eids[0]], "Build user model")
        assert "Build user model" in tensor.assembled_prompt

    def test_includes_dependency_context(self, two_node_graph: EngramGraph):
        eids = list(two_node_graph._engrams.keys())
        tensor = assemble_tensor(two_node_graph, [eids[0]], "test mandate")
        # Both nodes should appear in the dependency subgraph
        assert tensor.dependency_subgraph_json
        assert "engrams" in tensor.dependency_subgraph_json

    def test_intent_chain_populated(self, two_node_graph: EngramGraph):
        eids = list(two_node_graph._engrams.keys())
        tensor = assemble_tensor(two_node_graph, [eids[0]], "mandate text")
        assert "mandate text" in tensor.intent_chain
        assert len(tensor.intent_chain) >= 2

    def test_token_count_property(self, two_node_graph: EngramGraph):
        eids = list(two_node_graph._engrams.keys())
        tensor = assemble_tensor(two_node_graph, [eids[0]], "test")
        assert tensor.token_count > 0
        assert tensor.token_count == len(tensor.assembled_prompt) // 4

    def test_respects_token_budget(self, empty_graph: EngramGraph):
        # Create a large graph
        for i in range(20):
            empty_graph.add_engram(
                LogicEngram(
                    intent=f"function_{i} with a long description for token padding " * 5,
                    ast_signature=f"def func_{i}():",
                    logic_body="x = 1\n" * 50,
                    module_path="big.py",
                )
            )
        eids = list(empty_graph._engrams.keys())
        tensor = assemble_tensor(empty_graph, eids[:5], "test", token_budget=100)
        assert tensor.token_count <= 100

    def test_missing_engram_id_graceful(self, empty_graph: EngramGraph):
        e = LogicEngram(intent="a", ast_signature="def a():", logic_body="pass")
        empty_graph.add_engram(e)
        # Request both a valid and invalid ID
        tensor = assemble_tensor(empty_graph, [e.engram_id, uuid4()], "test")
        assert tensor.assembled_prompt  # Should not crash


# ── assemble_full_graph_context ──────────────────────────────


class TestAssembleFullGraphContext:
    def test_includes_topology(self, two_node_graph: EngramGraph):
        ctx = assemble_full_graph_context(two_node_graph, "Plan everything")
        assert "<planning_context>" in ctx
        assert "Plan everything" in ctx

    def test_includes_stats(self, two_node_graph: EngramGraph):
        ctx = assemble_full_graph_context(two_node_graph, "test")
        assert 'nodes="2"' in ctx
        assert 'edges="1"' in ctx

    def test_empty_graph_context(self, empty_graph: EngramGraph):
        ctx = assemble_full_graph_context(empty_graph, "empty test")
        assert "<planning_context>" in ctx
        assert "empty test" in ctx

    def test_respects_budget(self, two_node_graph: EngramGraph):
        ctx = assemble_full_graph_context(two_node_graph, "test", token_budget=10)
        assert len(ctx) // 4 <= 10
