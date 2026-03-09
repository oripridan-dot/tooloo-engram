"""
AST Decomposer — Python source → Logic Engrams.

Takes a Python source string, parses its AST, and decomposes it into
atomic LogicEngram nodes with SynapticEdge dependencies.
"""

from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING

from .schema import Domain, EdgeType, Language, LogicEngram, SynapticEdge

if TYPE_CHECKING:
    from uuid import UUID


class DecompositionResult:
    """Result of decomposing a Python module into Engrams."""

    def __init__(self) -> None:
        self.engrams: list[LogicEngram] = []
        self.edges: list[SynapticEdge] = []
        self.module_imports: list[str] = []
        self.errors: list[str] = []


def decompose_module(
    source: str,
    module_path: str = "",
    domain: Domain = Domain.BACKEND,
) -> DecompositionResult:
    """Decompose a Python source string into Logic Engrams.

    Each top-level function and class becomes an Engram.
    Import relationships become SynapticEdges.
    Module-level code (constants, assignments) becomes a special "module_init" Engram.
    """
    result = DecompositionResult()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Non-Python file (TSX, TS, etc.) — store as a single raw engram
        lang = Language.PYTHON
        if module_path.endswith((".tsx", ".jsx")):
            lang = Language.TSX
        elif module_path.endswith((".ts", ".js")):
            lang = Language.TYPESCRIPT
        raw_engram = LogicEngram(
            intent=f"Raw module {module_path}",
            ast_signature=f"# raw: {module_path}",
            logic_body=source,
            language=lang,
            domain=domain,
            module_path=module_path,
        )
        result.engrams.append(raw_engram)
        return result

    # Track which names map to which Engram IDs (for edge resolution)
    name_to_engram: dict[str, UUID] = {}
    module_level_lines: list[str] = []
    source_lines = source.splitlines()

    # First pass: extract imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.module_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.module_imports.append(node.module)

    # Second pass: extract top-level definitions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            engram = _function_to_engram(node, source_lines, module_path, domain)
            result.engrams.append(engram)
            name_to_engram[node.name] = engram.engram_id

        elif isinstance(node, ast.ClassDef):
            class_engram = _class_to_engram(node, source_lines, module_path, domain)
            result.engrams.append(class_engram)
            name_to_engram[node.name] = class_engram.engram_id

            # Decompose methods as child Engrams
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_engram = _function_to_engram(item, source_lines, module_path, domain)
                    method_engram.parent_engram_id = class_engram.engram_id
                    method_engram.intent = f"{node.name}.{method_engram.intent}"
                    result.engrams.append(method_engram)
                    name_to_engram[f"{node.name}.{item.name}"] = method_engram.engram_id

                    # Method → Class edge
                    result.edges.append(
                        SynapticEdge(
                            source_id=method_engram.engram_id,
                            target_id=class_engram.engram_id,
                            edge_type=EdgeType.INHERITS,
                        )
                    )

        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Capture import source lines so they appear in module_init body
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            module_level_lines.append("\n".join(source_lines[start:end]))

        else:
            # Module-level code (assignments, constants, expressions)
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            chunk = "\n".join(source_lines[start:end])
            module_level_lines.append(chunk)

    # Create module-init Engram for constants/assignments
    if module_level_lines:
        init_body = "\n".join(module_level_lines)
        init_engram = LogicEngram(
            intent=f"Module-level initialization for {module_path or 'module'}",
            ast_signature=f"# module_init: {module_path}",
            logic_body=init_body,
            language=Language.PYTHON,
            domain=domain,
            module_path=module_path,
        )
        result.engrams.append(init_engram)
        name_to_engram["__module_init__"] = init_engram.engram_id

    # Third pass: resolve intra-module call edges
    for engram in result.engrams:
        if not engram.logic_body:
            continue
        called = _extract_called_names(engram.logic_body)
        for name in called:
            if name in name_to_engram and name_to_engram[name] != engram.engram_id:
                result.edges.append(
                    SynapticEdge(
                        source_id=engram.engram_id,
                        target_id=name_to_engram[name],
                        edge_type=EdgeType.CALLS,
                    )
                )

    return result


def _function_to_engram(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    module_path: str,
    domain: Domain,
) -> LogicEngram:
    """Convert an AST function node to a LogicEngram."""
    # Extract signature
    sig = _extract_signature(node, source_lines)

    # Extract body (without docstring)
    body_start = node.lineno - 1
    body_end = getattr(node, "end_lineno", node.lineno)
    "\n".join(source_lines[body_start:body_end])

    # Strip the docstring from the body for token efficiency
    body_lines = source_lines[body_start:body_end]
    clean_body = _strip_docstring(body_lines)

    # Generate intent from function name + args
    async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    intent = f"{async_prefix}{node.name}({_arg_names(node)})"

    return LogicEngram(
        intent=intent,
        ast_signature=sig,
        logic_body=clean_body,
        language=Language.PYTHON,
        domain=domain,
        module_path=module_path,
    )


def _class_to_engram(
    node: ast.ClassDef,
    source_lines: list[str],
    module_path: str,
    domain: Domain,
) -> LogicEngram:
    """Convert an AST class node to a LogicEngram (header only, methods decomposed separately)."""
    # Class signature: class Name(bases):
    bases = ", ".join(_get_base_names(node))
    sig = f"class {node.name}({bases}):" if bases else f"class {node.name}:"

    # Extract class-level code (docstring, class variables) — NOT methods
    class_level: list[str] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = item.lineno - 1
        end = getattr(item, "end_lineno", item.lineno)
        class_level.append("\n".join(source_lines[start:end]))

    body = "\n".join(class_level) if class_level else "pass"

    return LogicEngram(
        intent=f"class {node.name}",
        ast_signature=sig,
        logic_body=body,
        language=Language.PYTHON,
        domain=domain,
        module_path=module_path,
    )


def _extract_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> str:
    """Extract the def line as a string."""
    line = source_lines[node.lineno - 1].strip()
    # Handle multi-line signatures
    if not line.endswith(":"):
        for i in range(node.lineno, min(node.lineno + 5, len(source_lines))):
            line += " " + source_lines[i].strip()
            if line.endswith(":"):
                break
    return line


def _arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract argument names as comma-separated string."""
    args = []
    for arg in node.args.args:
        if arg.arg != "self" and arg.arg != "cls":
            args.append(arg.arg)
    return ", ".join(args)


def _get_base_names(node: ast.ClassDef) -> list[str]:
    """Extract base class names."""
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(ast.unparse(base))
    return names


def _strip_docstring(lines: list[str]) -> str:
    """Remove docstring from function/class body for token efficiency."""
    "\n".join(lines)
    # Remove triple-quoted docstrings
    in_docstring = False
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and len(stripped) > 3:
                continue  # Single-line docstring
            in_docstring = True
            continue
        if in_docstring:
            if '"""' in stripped or "'''" in stripped:
                in_docstring = False
            continue
        clean.append(line)
    return "\n".join(clean)


def _extract_called_names(code: str) -> set[str]:
    """Extract function/class names called within a code block."""
    names: set[str] = set()
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names
