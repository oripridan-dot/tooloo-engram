"""
Compiler Drone — Graph → executable Python files.

Traverses the EngramGraph in topological order and reassembles
human-readable Python files. This is the "exhaust" translation layer.

Supports two output modes:
  - DICT (default): returns {filepath: source_code} dict
  - LIVE_INJECTION: writes files directly to a target directory
"""

from __future__ import annotations

import os
from collections import defaultdict
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .schema import EdgeType, Language, LogicEngram

if TYPE_CHECKING:
    from uuid import UUID

    from .graph_store import EngramGraph


class OutputMode(StrEnum):
    DICT = "dict"  # Return filepath→source dict (default)
    LIVE_INJECTION = "live_injection"  # Write to working tree


def compile_graph(
    graph: EngramGraph,
    *,
    output_mode: OutputMode = OutputMode.DICT,
    target_dir: str | Path | None = None,
) -> dict[str, str]:
    """Compile an EngramGraph back into human-readable source files.

    Args:
        graph: The EngramGraph to compile.
        output_mode: DICT (return dict) or LIVE_INJECTION (write to disk).
        target_dir: Required when output_mode=LIVE_INJECTION. Target directory.

    Returns: {filepath: source_code} dict (always returned regardless of mode).
    """
    files: dict[str, str] = {}

    # Group engrams by module_path
    by_module: dict[str, list[tuple[int, LogicEngram]]] = defaultdict(list)
    topo_order = graph.topological_order()
    order_map = {eid: idx for idx, eid in enumerate(topo_order)}

    for eid in topo_order:
        engram = graph.get_engram(eid)
        if engram is None:
            continue
        path = engram.module_path or _infer_module_path(engram)
        by_module[path].append((order_map.get(eid, 0), engram))

    # Compile each module
    for module_path, engrams in sorted(by_module.items()):
        if not module_path:
            continue
        source = _compile_module(engrams, graph)
        files[module_path] = source

    # LIVE_INJECTION: write files to disk
    if output_mode == OutputMode.LIVE_INJECTION:
        if target_dir is None:
            raise ValueError("target_dir is required for LIVE_INJECTION mode")
        _write_to_disk(files, Path(target_dir))

    return files


def _compile_module(
    engrams: list[tuple[int, LogicEngram]],
    graph: EngramGraph,
) -> str:
    """Assemble a single Python module from its constituent Engrams."""
    # Sort by topological order within the module
    engrams.sort(key=lambda x: x[0])

    sections: list[str] = []
    imports_block: list[str] = []
    module_init_block: list[str] = []
    class_blocks: dict[UUID, list[str]] = {}  # class_id → method bodies
    function_blocks: list[str] = []

    for _, engram in engrams:
        if engram.language != Language.PYTHON:
            # For TSX/TS, just emit the body as-is
            sections.append(engram.logic_body)
            continue

        # Module-level init (constants, assignments)
        if engram.ast_signature.startswith("# module_init:"):
            module_init_block.append(engram.logic_body)
            # Extract imports from the init block
            for line in engram.logic_body.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports_block.append(stripped)
            continue

        # Class definition (header only)
        if engram.ast_signature.startswith("class "):
            class_blocks[engram.engram_id] = [engram.ast_signature]
            if engram.logic_body and engram.logic_body.strip() != "pass":
                # Class-level attributes — preserve existing indent
                for line in engram.logic_body.splitlines():
                    if line.strip():
                        if line.startswith("    "):
                            class_blocks[engram.engram_id].append(line)
                        else:
                            class_blocks[engram.engram_id].append(f"    {line}")
                    else:
                        class_blocks[engram.engram_id].append("")
            continue

        # Method (child of a class)
        if engram.parent_engram_id and engram.parent_engram_id in class_blocks:
            method_lines = _indent_method(engram)
            class_blocks[engram.parent_engram_id].extend(method_lines)
            continue

        # Standalone function
        function_blocks.append(engram.logic_body)

    # Assemble imports
    _collect_cross_module_imports(engrams, graph, imports_block)

    # Deduplicate imports
    seen_imports: set[str] = set()
    unique_imports: list[str] = []
    for imp in imports_block:
        if imp not in seen_imports:
            seen_imports.add(imp)
            unique_imports.append(imp)

    # Build final source
    parts: list[str] = []
    if unique_imports:
        parts.append("\n".join(sorted(unique_imports)))
        parts.append("")

    # Module-level init (non-import lines)
    for block in module_init_block:
        non_import_lines = [
            line
            for line in block.splitlines()
            if not line.strip().startswith("import ")
            and not line.strip().startswith("from ")
            and line.strip()
        ]
        if non_import_lines:
            parts.append("\n".join(non_import_lines))
            parts.append("")

    # Classes with their methods
    for _class_id, lines in class_blocks.items():
        if len(lines) == 1:
            # Empty class
            lines.append("    pass")
        parts.append("\n".join(lines))
        parts.append("")

    # Standalone functions
    for func in function_blocks:
        parts.append(func)
        parts.append("")

    # Non-Python sections (TSX, TS, etc.) — emit as-is
    for section in sections:
        parts.append(section)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _indent_method(engram: LogicEngram) -> list[str]:
    """Indent a method body to be inside a class."""
    lines: list[str] = []
    for line in engram.logic_body.splitlines():
        if line.strip():
            # Check if it's already indented (part of the function body)
            if line.startswith("    "):
                lines.append(line)
            elif line.startswith("def ") or line.startswith("async def "):
                lines.append(f"    {line}")
            else:
                lines.append(f"    {line}")
        else:
            lines.append("")
    return ["", *lines]  # blank line before method


def _collect_cross_module_imports(
    engrams: list[tuple[int, LogicEngram]],
    graph: EngramGraph,
    imports_block: list[str],
) -> None:
    """Resolve cross-module IMPORTS edges into import statements.

    Only processes edges with ``edge_type == EdgeType.IMPORTS``.
    CALLS, TESTS, INHERITS, etc. edges are architectural metadata and do NOT
    trigger import generation — they are used by the healer/topology only.
    """
    module_paths_in_this_file = {e.module_path for _, e in engrams}

    for _, engram in engrams:
        # Check outgoing IMPORTS edges for cross-module dependencies
        for edge in graph._edges.values():
            if edge.source_id != engram.engram_id:
                continue
            if edge.edge_type != EdgeType.IMPORTS:
                continue  # only IMPORTS edges generate import statements
            target = graph.get_engram(edge.target_id)
            if (
                target
                and target.module_path
                and target.module_path not in module_paths_in_this_file
            ):
                # Generate import statement
                mod = target.module_path.replace("/", ".").replace(".py", "")
                # Extract the function/class name from intent — strip AST prefixes
                raw_name = target.intent.split("(")[0].split(".")[-1].strip()
                # Remove "class " / "def " / "async def " prefixes injected by AST decomposer
                for prefix in ("async def ", "def ", "class "):
                    if raw_name.startswith(prefix):
                        raw_name = raw_name[len(prefix) :]
                        break
                name = raw_name.strip()
                if name and not name.startswith("#") and not name.startswith("Module"):
                    imp = f"from {mod} import {name}"
                    imports_block.append(imp)


def _infer_module_path(engram: LogicEngram) -> str:
    """Infer a module path from intent/domain when not explicitly set."""
    if engram.domain.value == "test":
        return f"tests/test_{engram.intent.split('(')[0].lower()}.py"
    if engram.domain.value == "frontend":
        name = engram.intent.split("(")[0].split(".")[-1].strip()
        return f"frontend/{name}.tsx"
    return f"{engram.domain.value}/{engram.intent.split('(')[0].lower()}.py"


def _write_to_disk(files: dict[str, str], target_dir: Path) -> None:
    """Write compiled files to a target directory on disk.

    Creates directories as needed. Uses atomic write pattern
    (write to .tmp then rename) to avoid partial writes.
    """
    for filepath, source in files.items():
        full_path = target_dir / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp then rename
        tmp_path = full_path.with_suffix(full_path.suffix + ".tmp")
        tmp_path.write_text(source, encoding="utf-8")
        os.replace(str(tmp_path), str(full_path))
