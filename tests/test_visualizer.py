"""Tests for harness.visualizer — visual report generation."""

from __future__ import annotations

import pytest
from engram_v2.graph_store import EngramGraph
from engram_v2.schema import (
    EdgeType,
    LogicEngram,
    SynapticEdge,
)

from experiments.project_engram.harness.instrumentation import MetricsCollector
from experiments.project_engram.harness.visualizer import (
    generate_visual_report,
    render_code_diff,
    render_mermaid_graph,
    render_mermaid_model_chart,
    render_model_comparison,
    render_quality_radar,
    render_topology_tree,
    render_track_comparison,
)


@pytest.fixture
def sample_graph() -> EngramGraph:
    graph = EngramGraph()
    e1 = LogicEngram(
        intent="module init",
        ast_signature="# module_init: main.py",
        logic_body="import os",
        module_path="main.py",
    )
    e2 = LogicEngram(
        intent="add function",
        ast_signature="def add(a, b):",
        logic_body="return a + b",
        module_path="main.py",
    )
    e3 = LogicEngram(
        intent="multiply function",
        ast_signature="def multiply(a, b):",
        logic_body="return a * b",
        module_path="utils.py",
    )
    graph.add_engram(e1)
    graph.add_engram(e2)
    graph.add_engram(e3)
    graph.add_edge(SynapticEdge(source_id=e1.engram_id, target_id=e2.engram_id))
    graph.add_edge(
        SynapticEdge(source_id=e2.engram_id, target_id=e3.engram_id, edge_type=EdgeType.CALLS)
    )
    return graph


class TestMermaidGraph:
    def test_renders_mermaid_fences(self, sample_graph: EngramGraph):
        result = render_mermaid_graph(sample_graph)
        assert result.startswith("```mermaid")
        assert result.endswith("```")

    def test_contains_subgraphs(self, sample_graph: EngramGraph):
        result = render_mermaid_graph(sample_graph)
        assert "subgraph" in result
        assert "main_py" in result or "utils_py" in result

    def test_contains_edges(self, sample_graph: EngramGraph):
        result = render_mermaid_graph(sample_graph)
        assert "-->" in result

    def test_empty_graph(self):
        graph = EngramGraph()
        result = render_mermaid_graph(graph)
        assert "```mermaid" in result


class TestQualityRadar:
    def test_renders_all_dimensions(self):
        report = {
            "ast_score": 30.0,
            "import_score": 20.0,
            "structural_score": 25.0,
            "type_hint_score": 15.0,
            "test_score": 10.0,
            "total": 100.0,
        }
        result = render_quality_radar(report)
        assert "AST Parse" in result
        assert "Import Resolve" in result
        assert "TOTAL" in result
        assert "100.0/100" in result

    def test_renders_partial_scores(self):
        report = {
            "ast_score": 15.0,
            "import_score": 10.0,
            "structural_score": 12.5,
            "type_hint_score": 7.5,
            "test_score": 5.0,
            "total": 50.0,
        }
        result = render_quality_radar(report)
        assert "50.0/100" in result

    def test_renders_zero_scores(self):
        report = {
            "ast_score": 0,
            "import_score": 0,
            "structural_score": 0,
            "type_hint_score": 0,
            "test_score": 0,
            "total": 0,
        }
        result = render_quality_radar(report)
        assert "0.0/100" in result


class TestCodeDiff:
    def test_renders_matching_files(self):
        orig = {"main.py": "def add(a, b):\n    return a + b"}
        compiled = {"main.py": "def add(a, b):\n    return a + b"}
        result = render_code_diff(orig, compiled)
        assert "main.py" in result
        assert "Original" in result
        assert "Compiled" in result

    def test_renders_differing_files(self):
        orig = {"main.py": "def add(a, b):\n    return a + b"}
        compiled = {"main.py": "def add(x, y):\n    return x + y"}
        result = render_code_diff(orig, compiled)
        assert "*|" in result  # Diff marker

    def test_handles_missing_files(self):
        orig = {"a.py": "pass"}
        compiled = {"b.py": "pass"}
        result = render_code_diff(orig, compiled)
        assert "a.py" in result
        assert "b.py" in result


class TestModelComparison:
    def test_renders_model_table(self):
        results = {
            "GPT-4o-mini": {
                "quality": 85.0,
                "time_s": 0.5,
                "cost": 0.001,
                "tokens": 5000,
                "efficiency": 90.0,
            },
            "Claude-Opus-4": {
                "quality": 95.0,
                "time_s": 1.2,
                "cost": 0.05,
                "tokens": 8000,
                "efficiency": 75.0,
            },
        }
        result = render_model_comparison(results)
        assert "GPT-4o-mini" in result
        assert "Claude-Opus-4" in result
        assert "Model Comparison Matrix" in result

    def test_empty_results(self):
        result = render_model_comparison({})
        assert "Model Comparison Matrix" in result


class TestTopologyTree:
    def test_renders_tree_structure(self, sample_graph: EngramGraph):
        result = render_topology_tree(sample_graph)
        assert "Engram Topology Tree" in result
        assert "main.py" in result
        assert "3 engrams" in result

    def test_empty_graph(self):
        graph = EngramGraph()
        result = render_topology_tree(graph)
        assert "0 engrams" in result


class TestTrackComparison:
    def test_renders_comparison(self):
        collector = MetricsCollector()
        s = collector.begin_run("A", "L1", "offline")
        s.quality_score = 90.0
        s.wall_clock_s = 0.5
        s.total_input_tokens = 1000
        s.total_output_tokens = 500
        collector.end_run()

        s = collector.begin_run("B", "L1", "offline")
        s.quality_score = 90.0
        s.wall_clock_s = 0.3
        s.total_input_tokens = 400
        s.total_output_tokens = 200
        collector.end_run()

        result = render_track_comparison(
            collector.get_snapshots(track="A"),
            collector.get_snapshots(track="B"),
        )
        assert "L1" in result
        assert "Quality A" in result
        assert "Quality B" in result

    def test_empty_snapshots(self):
        result = render_track_comparison([], [])
        assert "Track Comparison" in result


class TestMermaidModelChart:
    def test_renders_chart(self):
        data = {
            "GPT-4o": {"L1": 90.0, "L2": 85.0, "L3": 94.0},
            "Claude": {"L1": 92.0, "L2": 87.0, "L3": 96.0},
        }
        result = render_mermaid_model_chart(data)
        assert "```mermaid" in result
        assert "xychart-beta" in result


class TestGenerateVisualReport:
    def test_generates_full_report(self, sample_graph: EngramGraph):
        collector = MetricsCollector()
        s = collector.begin_run("A", "L1", "offline")
        s.quality_score = 90.0
        collector.end_run()
        s = collector.begin_run("B", "L1", "offline")
        s.quality_score = 90.0
        collector.end_run()

        result = generate_visual_report(
            collector,
            graphs={"L1": sample_graph},
            model_results={
                "GPT-4o": {
                    "quality": 90.0,
                    "time_s": 0.5,
                    "cost": 0.01,
                    "tokens": 5000,
                    "efficiency": 85.0,
                    "per_level": {"L1": 90.0, "L2": 85.0, "L3": 94.0},
                },
            },
        )
        assert "# Visual Analysis Report" in result
        assert "Track Performance Comparison" in result
        assert "Dependency Graphs" in result
        assert "Model Comparison" in result
