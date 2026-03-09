"""Tests for engram.compiler_drone — Graph → executable Python round-trip."""

from __future__ import annotations

import ast
import contextlib

import pytest
from conftest import (
    ASYNC_MODULE,
    CLASS_MODULE,
    MODULE_WITH_CONSTANTS,
    MULTI_CLASS_MODULE,
    SIMPLE_MODULE,
    TSX_SOURCE,
)
from engram_v2.ast_decomposer import decompose_module
from engram_v2.compiler_drone import compile_graph
from engram_v2.graph_store import EngramGraph
from engram_v2.schema import Domain


def _roundtrip(source: str, path: str = "mod.py", domain: Domain = Domain.BACKEND) -> str:
    """Decompose → graph → compile → return compiled source for the file."""
    graph = EngramGraph()
    result = decompose_module(source, path, domain)
    for e in result.engrams:
        graph.add_engram(e)
    for edge in result.edges:
        with contextlib.suppress(Exception):
            graph.add_edge(edge)
    compiled = compile_graph(graph)
    return compiled.get(path, "")


# ── Basic round-trip ─────────────────────────────────────────


class TestBasicRoundTrip:
    def test_simple_module_parses(self):
        out = _roundtrip(SIMPLE_MODULE, "calc.py")
        ast.parse(out)  # No SyntaxError

    def test_simple_module_has_functions(self):
        out = _roundtrip(SIMPLE_MODULE, "calc.py")
        assert "def add" in out
        assert "def subtract" in out

    def test_simple_module_preserves_imports(self):
        out = _roundtrip(SIMPLE_MODULE, "calc.py")
        assert "from __future__ import annotations" in out

    def test_class_module_parses(self):
        out = _roundtrip(CLASS_MODULE, "services/todo_service.py")
        ast.parse(out)  # No SyntaxError

    def test_class_docstring_correct_indent(self):
        out = _roundtrip(CLASS_MODULE, "services/todo_service.py")
        lines = out.splitlines()
        # Find the class line, next non-empty line should be 4-space indented docstring
        for i, line in enumerate(lines):
            if line.strip().startswith("class TodoService"):
                # Find next non-blank line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        indent = len(lines[j]) - len(lines[j].lstrip())
                        assert indent == 4, (
                            f"Class body indent should be 4, got {indent}: {lines[j]!r}"
                        )
                        break
                break

    def test_class_module_preserves_all_imports(self):
        out = _roundtrip(CLASS_MODULE, "services/todo_service.py")
        assert "from __future__ import annotations" in out
        assert "from datetime import datetime" in out
        assert "from .todo import TodoItem" in out

    def test_class_methods_present(self):
        out = _roundtrip(CLASS_MODULE, "services/todo_service.py")
        assert "def __init__" in out
        assert "def create" in out
        assert "def get" in out


# ── Multi-class module ───────────────────────────────────────


class TestMultiClass:
    def test_multi_class_parses(self):
        out = _roundtrip(MULTI_CLASS_MODULE, "models.py")
        ast.parse(out)

    def test_both_classes_present(self):
        out = _roundtrip(MULTI_CLASS_MODULE, "models.py")
        assert "class Base" in out
        assert "class Child" in out


# ── Async module ─────────────────────────────────────────────


class TestAsyncRoundTrip:
    def test_async_parses(self):
        out = _roundtrip(ASYNC_MODULE, "async_svc.py")
        ast.parse(out)

    def test_async_preserved(self):
        out = _roundtrip(ASYNC_MODULE, "async_svc.py")
        assert "async def fetch_data" in out
        assert "async def process" in out


# ── Constants module ─────────────────────────────────────────


class TestConstantsRoundTrip:
    def test_constants_parses(self):
        out = _roundtrip(MODULE_WITH_CONSTANTS, "config/settings.py")
        ast.parse(out)

    def test_constants_preserved(self):
        out = _roundtrip(MODULE_WITH_CONSTANTS, "config/settings.py")
        assert "MAX_RETRIES = 3" in out
        assert "DEFAULT_TIMEOUT = 30.0" in out


# ── TSX passthrough ──────────────────────────────────────────


class TestTSXPassthrough:
    def test_tsx_preserved_verbatim(self):
        out = _roundtrip(TSX_SOURCE, "frontend/Widget.tsx", Domain.FRONTEND)
        assert "React.FC<Props>" in out
        assert "className" in out

    def test_tsx_file_present_in_compiled(self):
        graph = EngramGraph()
        result = decompose_module(TSX_SOURCE, "frontend/Widget.tsx", Domain.FRONTEND)
        for e in result.engrams:
            graph.add_engram(e)
        compiled = compile_graph(graph)
        assert "frontend/Widget.tsx" in compiled


# ── Multi-module compilation ─────────────────────────────────


class TestMultiModule:
    def test_compiles_multiple_files(self):
        graph = EngramGraph()
        sources = {
            "models/user.py": SIMPLE_MODULE,
            "services/todo.py": CLASS_MODULE,
        }
        for path, src in sources.items():
            result = decompose_module(src, path)
            for e in result.engrams:
                graph.add_engram(e)
            for edge in result.edges:
                with contextlib.suppress(Exception):
                    graph.add_edge(edge)
        compiled = compile_graph(graph)
        assert "models/user.py" in compiled
        assert "services/todo.py" in compiled
        # Both should parse
        for path, src in compiled.items():
            if path.endswith(".py"):
                ast.parse(src)


# ── Quality parity with MockLLM templates ────────────────────


class TestQualityParity:
    """Compiler output should match template quality at all levels."""

    @pytest.mark.parametrize("level", ["L1", "L2", "L3"])
    def test_compiled_matches_template_quality(self, level: str):
        from experiments.project_engram.harness.config import L1_SIMPLE, L2_MEDIUM, L3_COMPLEX
        from experiments.project_engram.harness.mock_llm import MockLLM
        from experiments.project_engram.harness.quality_scorer import score_output

        mandates = {"L1": L1_SIMPLE, "L2": L2_MEDIUM, "L3": L3_COMPLEX}
        mandate = mandates[level]
        llm = MockLLM()
        templates = llm.get_templates(level)

        graph = EngramGraph()
        for path, src in templates.items():
            result = decompose_module(src, path)
            for e in result.engrams:
                graph.add_engram(e)
            for edge in result.edges:
                with contextlib.suppress(Exception):
                    graph.add_edge(edge)
        compiled = compile_graph(graph)

        a = score_output(templates, mandate)
        b = score_output(compiled, mandate)
        assert b.total == pytest.approx(a.total, abs=0.1), (
            f"Quality gap at {level}: template={a.total}, compiled={b.total}"
        )

    @pytest.mark.parametrize("level", ["L1", "L2", "L3"])
    def test_all_compiled_files_parse(self, level: str):
        from experiments.project_engram.harness.mock_llm import MockLLM

        llm = MockLLM()
        templates = llm.get_templates(level)

        graph = EngramGraph()
        for path, src in templates.items():
            result = decompose_module(src, path)
            for e in result.engrams:
                graph.add_engram(e)
            for edge in result.edges:
                with contextlib.suppress(Exception):
                    graph.add_edge(edge)
        compiled = compile_graph(graph)

        for path, src in compiled.items():
            if path.endswith(".py"):
                try:
                    ast.parse(src)
                except SyntaxError as e:
                    pytest.fail(f"SyntaxError in compiled {path}: {e}")
