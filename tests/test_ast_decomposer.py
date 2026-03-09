"""Tests for engram.ast_decomposer — Python source → Logic Engrams."""

from __future__ import annotations

from engram_v2.ast_decomposer import (
    decompose_module,
)
from engram_v2.schema import Domain, EdgeType, Language

# ── Simple module ────────────────────────────────────────────


class TestSimpleModule:
    """Decompose a module with plain functions and imports."""

    def test_produces_function_engrams(self, SIMPLE_MODULE=None):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        intents = [
            e.intent for e in result.engrams if not e.ast_signature.startswith("# module_init")
        ]
        assert "add(a, b)" in intents
        assert "subtract(a, b)" in intents

    def test_extracts_imports(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        assert "__future__" in result.module_imports

    def test_module_init_captures_imports(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        init_engrams = [e for e in result.engrams if "module_init" in e.ast_signature]
        assert len(init_engrams) == 1
        body = init_engrams[0].logic_body
        assert "from __future__ import annotations" in body

    def test_function_signatures_extracted(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        sigs = [e.ast_signature for e in result.engrams]
        assert any("def add" in s for s in sigs)
        assert any("def subtract" in s for s in sigs)

    def test_module_path_propagated(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        for e in result.engrams:
            assert e.module_path == "calc.py"

    def test_no_errors(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "calc.py")
        assert result.errors == []


# ── Class module ─────────────────────────────────────────────


class TestClassModule:
    """Decompose a module with class + methods."""

    def test_class_engram_created(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        class_engrams = [e for e in result.engrams if e.ast_signature.startswith("class ")]
        assert len(class_engrams) == 1
        assert class_engrams[0].ast_signature == "class TodoService:"

    def test_methods_have_parent(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        class_engram = next(e for e in result.engrams if e.ast_signature.startswith("class "))
        methods = [e for e in result.engrams if e.parent_engram_id == class_engram.engram_id]
        method_names = [e.intent for e in methods]
        assert any("__init__" in n for n in method_names)
        assert any("create" in n for n in method_names)
        assert any("get" in n for n in method_names)

    def test_method_to_class_edges(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        next(e for e in result.engrams if e.ast_signature.startswith("class "))
        inherits_edges = [e for e in result.edges if e.edge_type == EdgeType.INHERITS]
        # Each method should have an edge to the class
        assert len(inherits_edges) >= 3  # __init__, create, get

    def test_class_body_has_class_level_content(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        class_engram = next(e for e in result.engrams if e.ast_signature.startswith("class "))
        # Class body should have the docstring and/or class vars
        assert class_engram.logic_body.strip() != "pass"

    def test_preserves_all_imports(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        init_engrams = [e for e in result.engrams if "module_init" in e.ast_signature]
        assert len(init_engrams) == 1
        body = init_engrams[0].logic_body
        assert "from __future__ import annotations" in body
        assert "from datetime import datetime" in body
        assert "from .todo import TodoItem" in body

    def test_relative_import_in_module_imports(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "services/todo_service.py")
        # ImportFrom(module='todo', level=1) stores module name without dot
        assert "todo" in result.module_imports


# ── TSX/non-Python fallback ──────────────────────────────────


class TestNonPythonFiles:
    """Non-Python files fall through as raw engrams."""

    def test_tsx_creates_raw_engram(self):
        from conftest import TSX_SOURCE

        result = decompose_module(TSX_SOURCE, "frontend/Widget.tsx", Domain.FRONTEND)
        assert len(result.engrams) == 1
        assert result.engrams[0].language == Language.TSX
        assert result.engrams[0].logic_body == TSX_SOURCE

    def test_tsx_no_errors(self):
        from conftest import TSX_SOURCE

        result = decompose_module(TSX_SOURCE, "frontend/Widget.tsx")
        assert result.errors == []

    def test_tsx_module_path_preserved(self):
        from conftest import TSX_SOURCE

        result = decompose_module(TSX_SOURCE, "frontend/Widget.tsx")
        assert result.engrams[0].module_path == "frontend/Widget.tsx"

    def test_tsx_signature_is_raw(self):
        from conftest import TSX_SOURCE

        result = decompose_module(TSX_SOURCE, "frontend/Widget.tsx")
        assert result.engrams[0].ast_signature == "# raw: frontend/Widget.tsx"

    def test_ts_uses_typescript_language(self):
        result = decompose_module("const x: number = 1;", "utils/helper.ts")
        assert result.engrams[0].language == Language.TYPESCRIPT


# ── Async functions ──────────────────────────────────────────


class TestAsyncModule:
    """Async function decomposition."""

    def test_async_intent_prefix(self):
        from conftest import ASYNC_MODULE

        result = decompose_module(ASYNC_MODULE, "async_svc.py")
        intents = [e.intent for e in result.engrams if "fetch_data" in e.intent]
        assert any(i.startswith("async ") for i in intents)

    def test_cross_function_call_edges(self):
        from conftest import ASYNC_MODULE

        result = decompose_module(ASYNC_MODULE, "async_svc.py")
        call_edges = [e for e in result.edges if e.edge_type == EdgeType.CALLS]
        assert len(call_edges) >= 1  # process calls fetch_data


# ── Module-level constants ───────────────────────────────────


class TestModuleLevelConstants:
    """Module-level assignments become module_init engram."""

    def test_constants_in_module_init(self):
        from conftest import MODULE_WITH_CONSTANTS

        result = decompose_module(MODULE_WITH_CONSTANTS, "config/settings.py")
        init_engrams = [e for e in result.engrams if "module_init" in e.ast_signature]
        assert len(init_engrams) == 1
        body = init_engrams[0].logic_body
        assert "MAX_RETRIES = 3" in body
        assert "DEFAULT_TIMEOUT = 30.0" in body


# ── Edge cases ───────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_source(self):
        result = decompose_module("", "empty.py")
        assert result.errors == []
        # Empty module produces no engrams
        assert len(result.engrams) == 0

    def test_single_comment(self):
        result = decompose_module("# just a comment\n", "comment.py")
        assert result.errors == []

    def test_multi_class_module(self):
        from conftest import MULTI_CLASS_MODULE

        result = decompose_module(MULTI_CLASS_MODULE, "models.py")
        classes = [e for e in result.engrams if e.ast_signature.startswith("class ")]
        assert len(classes) == 2

    def test_class_with_bases(self):
        from conftest import MULTI_CLASS_MODULE

        result = decompose_module(MULTI_CLASS_MODULE, "models.py")
        child = next(e for e in result.engrams if "Child" in e.ast_signature)
        assert "Base" in child.ast_signature

    def test_domain_propagated(self):
        from conftest import SIMPLE_MODULE

        result = decompose_module(SIMPLE_MODULE, "tests/test_calc.py", Domain.TEST)
        for e in result.engrams:
            assert e.domain == Domain.TEST

    def test_unique_engram_ids(self):
        from conftest import CLASS_MODULE

        result = decompose_module(CLASS_MODULE, "svc.py")
        ids = [e.engram_id for e in result.engrams]
        assert len(ids) == len(set(ids)), "Engram IDs must be unique"
