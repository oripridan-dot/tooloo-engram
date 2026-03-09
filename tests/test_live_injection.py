"""Tests for compiler_drone LIVE_INJECTION mode — writes files to disk."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

import pytest
from engram_v2.ast_decomposer import decompose_module
from engram_v2.compiler_drone import (
    OutputMode,
    compile_graph,
)
from engram_v2.graph_store import EngramGraph
from engram_v2.schema import (
    Domain,
    Language,
    LogicEngram,
)

# ── Fixtures ─────────────────────────────────────────────────


def _build_graph() -> EngramGraph:
    """Build a simple graph with two Python modules."""
    graph = EngramGraph()
    e1 = LogicEngram(
        intent="add numbers",
        ast_signature="def add(a: int, b: int) -> int:",
        logic_body="def add(a: int, b: int) -> int:\n    return a + b\n",
        module_path="utils/calc.py",
    )
    e2 = LogicEngram(
        intent="config constants",
        ast_signature="# module_init: config",
        logic_body="MAX_RETRIES = 3\nDEFAULT_TIMEOUT = 30.0\n",
        module_path="config/settings.py",
    )
    graph.add_engram(e1)
    graph.add_engram(e2)
    return graph


# ── OutputMode enum ──────────────────────────────────────────


class TestOutputMode:
    def test_dict_mode_value(self):
        assert OutputMode.DICT == "dict"

    def test_live_injection_mode_value(self):
        assert OutputMode.LIVE_INJECTION == "live_injection"

    def test_default_is_dict(self):
        graph = _build_graph()
        files = compile_graph(graph)
        assert isinstance(files, dict)
        assert len(files) > 0


# ── DICT mode (default) ─────────────────────────────────────


class TestDictMode:
    def test_returns_dict(self):
        graph = _build_graph()
        files = compile_graph(graph, output_mode=OutputMode.DICT)
        assert isinstance(files, dict)

    def test_contains_expected_files(self):
        graph = _build_graph()
        files = compile_graph(graph, output_mode=OutputMode.DICT)
        assert "utils/calc.py" in files
        assert "config/settings.py" in files


# ── LIVE_INJECTION mode ──────────────────────────────────────


class TestLiveInjection:
    def test_requires_target_dir(self):
        graph = _build_graph()
        with pytest.raises(ValueError, match="target_dir is required"):
            compile_graph(graph, output_mode=OutputMode.LIVE_INJECTION)

    def test_writes_files_to_disk(self):
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            files = compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            # Files dict is still returned
            assert len(files) > 0

            # Files exist on disk
            for filepath in files:
                full = Path(tmpdir) / filepath
                assert full.exists(), f"Missing: {full}"
                assert full.read_text(encoding="utf-8").strip() != ""

    def test_creates_directories(self):
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            assert (Path(tmpdir) / "utils").is_dir()
            assert (Path(tmpdir) / "config").is_dir()

    def test_content_matches_dict(self):
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            files = compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            for filepath, source in files.items():
                disk_content = (Path(tmpdir) / filepath).read_text(encoding="utf-8")
                assert disk_content == source

    def test_atomic_write_no_partial(self):
        """No .tmp files should remain after compilation."""
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            tmp_files = list(Path(tmpdir).rglob("*.tmp"))
            assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    def test_overwrite_existing_file(self):
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            # First write
            compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            # Modify graph
            e3 = LogicEngram(
                intent="multiply",
                ast_signature="def mul(a, b):",
                logic_body="def mul(a, b):\n    return a * b\n",
                module_path="utils/calc.py",
            )
            graph.add_engram(e3)

            # Second write
            compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            content = (Path(tmpdir) / "utils/calc.py").read_text(encoding="utf-8")
            assert "mul" in content

    def test_tsx_file_written(self):
        graph = EngramGraph()
        tsx = LogicEngram(
            intent="Widget component",
            ast_signature="tsx:Widget.tsx",
            logic_body="export const Widget = () => <div>Hello</div>;\n",
            language=Language.TSX,
            domain=Domain.FRONTEND,
            module_path="frontend/Widget.tsx",
        )
        graph.add_engram(tsx)

        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            assert (Path(tmpdir) / "frontend/Widget.tsx").exists()

    def test_path_with_target_dir(self):
        """target_dir can be a Path object."""
        graph = _build_graph()
        with tempfile.TemporaryDirectory(prefix="engram_live_") as tmpdir:
            files = compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=Path(tmpdir),
            )
            assert len(files) > 0


# ── Round-trip: decompose → graph → LIVE_INJECTION ───────────


class TestRoundTripLiveInjection:
    def test_decompose_compile_write(self):
        source = "def greet(name: str) -> str:\n    return f'Hello, {name}!'\n"
        graph = EngramGraph()
        result = decompose_module(source, "greet.py", Domain.BACKEND)
        for e in result.engrams:
            graph.add_engram(e)
        for edge in result.edges:
            with contextlib.suppress(Exception):
                graph.add_edge(edge)

        with tempfile.TemporaryDirectory(prefix="engram_rt_") as tmpdir:
            files = compile_graph(
                graph,
                output_mode=OutputMode.LIVE_INJECTION,
                target_dir=tmpdir,
            )
            assert "greet.py" in files
            assert (Path(tmpdir) / "greet.py").exists()
