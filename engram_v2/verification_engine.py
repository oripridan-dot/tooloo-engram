"""
Verification Engine — Execute TESTS edges to validate engram correctness.

Isolates an engram + its dependencies into a temp sandbox, writes them
as Python files, runs pytest on attached TESTS edges, and feeds binary
pass/fail back to the graph healer.

This closes the gap between "graph validates structure" (graph_healer)
and "graph validates execution" — the key differentiator for Track B's
"heal, don't cascade" advantage.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from .schema import EdgeType, LogicEngram

if TYPE_CHECKING:
    from .graph_store import EngramGraph


@dataclass
class VerificationResult:
    """Result of executing TESTS edges for a target engram."""

    target_engram_id: UUID
    passed: bool = False
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    errors: list[str] = field(default_factory=list)
    stdout: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "target_engram_id": str(self.target_engram_id),
            "passed": self.passed,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "errors": self.errors,
            "stdout": self.stdout[:2000],  # cap output size
            "duration_s": round(self.duration_s, 4),
        }


def verify_engram(
    graph: EngramGraph,
    engram_id: UUID,
    *,
    timeout_s: int = 30,
) -> VerificationResult:
    """Isolate an engram + deps in a temp sandbox, run its TESTS edges.

    1. Find all TESTS edges pointing at this engram (test → target).
    2. Collect the target engram and its dependency subgraph.
    3. Write everything to a temp directory as Python files.
    4. Run pytest on the test files.
    5. Return binary pass/fail.
    """
    import time

    t0 = time.monotonic()
    result = VerificationResult(target_engram_id=engram_id)

    target = graph.get_engram(engram_id)
    if target is None:
        result.errors.append(f"Engram {engram_id} not found in graph")
        result.duration_s = time.monotonic() - t0
        return result

    # Step 1: Find test engrams linked via TESTS edges
    test_engrams = _find_test_engrams(graph, engram_id)
    if not test_engrams:
        # No tests attached — vacuously passes
        result.passed = True
        result.duration_s = time.monotonic() - t0
        return result

    # Step 2: Collect dependency subgraph engrams
    dep_subgraph = graph.get_dependency_subgraph(engram_id)
    dep_engram_ids = {UUID(n) for n in dep_subgraph.nodes()}
    dep_engrams = [
        graph.get_engram(eid) for eid in dep_engram_ids if graph.get_engram(eid) is not None
    ]

    # Step 3: Write to temp sandbox
    with tempfile.TemporaryDirectory(prefix="engram_verify_") as tmpdir:
        sandbox = Path(tmpdir)
        _write_sandbox(sandbox, dep_engrams, test_engrams)

        # Step 4: Run pytest
        test_files = [str(p) for p in sandbox.rglob("test_*.py")]
        if not test_files:
            result.passed = True
            result.duration_s = time.monotonic() - t0
            return result

        result = _run_pytest(sandbox, test_files, engram_id, timeout_s=timeout_s)

    result.duration_s = time.monotonic() - t0
    return result


def verify_all_tested_engrams(
    graph: EngramGraph,
    *,
    timeout_s: int = 30,
) -> list[VerificationResult]:
    """Run verification for every engram that has TESTS edges pointing to it.

    Returns a list of VerificationResults.
    """
    # Find all engrams that are targets of TESTS edges
    tested_ids: set[UUID] = set()
    for edge in graph._edges.values():
        if edge.edge_type == EdgeType.TESTS:
            tested_ids.add(edge.target_id)

    results = []
    for eid in tested_ids:
        results.append(verify_engram(graph, eid, timeout_s=timeout_s))
    return results


def _find_test_engrams(graph: EngramGraph, target_id: UUID) -> list[LogicEngram]:
    """Find all engrams that TEST the given target engram."""
    test_engrams: list[LogicEngram] = []
    for edge in graph._edges.values():
        if edge.edge_type == EdgeType.TESTS and edge.target_id == target_id:
            test_eng = graph.get_engram(edge.source_id)
            if test_eng is not None:
                test_engrams.append(test_eng)
    return test_engrams


def _write_sandbox(
    sandbox: Path,
    dep_engrams: list[LogicEngram],
    test_engrams: list[LogicEngram],
) -> None:
    """Write engrams as Python files into the sandbox directory."""
    # Write dependency engrams grouped by module_path
    _write_engrams_to_dir(sandbox, dep_engrams)

    # Write test engrams
    _write_engrams_to_dir(sandbox, test_engrams)

    # Create __init__.py for all directories
    for dirpath in sandbox.rglob("*"):
        if dirpath.is_dir():
            init = dirpath / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
    # Root init
    root_init = sandbox / "__init__.py"
    if not root_init.exists():
        root_init.write_text("", encoding="utf-8")


def _write_engrams_to_dir(base: Path, engrams: list[LogicEngram]) -> None:
    """Write a list of engrams as files, grouping by module_path."""
    by_module: dict[str, list[LogicEngram]] = {}
    for eng in engrams:
        path = eng.module_path or f"{eng.domain.value}/{eng.intent.split('(')[0].lower()}.py"
        by_module.setdefault(path, []).append(eng)

    for module_path, engs in by_module.items():
        filepath = base / module_path
        filepath.parent.mkdir(parents=True, exist_ok=True)

        parts: list[str] = []
        for eng in engs:
            body = eng.logic_body.strip()
            if body:
                parts.append(body)

        content = "\n\n\n".join(parts) + "\n"
        filepath.write_text(content, encoding="utf-8")


def _run_pytest(
    sandbox: Path,
    test_files: list[str],
    engram_id: UUID,
    *,
    timeout_s: int = 30,
) -> VerificationResult:
    """Execute pytest in the sandbox and parse results."""
    result = VerificationResult(target_engram_id=engram_id)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_files,
        "-v",
        "--tb=short",
        "--no-header",
        "-q",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={
                "PYTHONPATH": str(sandbox),
                "PATH": "",  # minimal env
            },
        )
        result.stdout = proc.stdout + proc.stderr

        # Parse pytest output for pass/fail counts
        _parse_pytest_output(result, proc.stdout + proc.stderr, proc.returncode)

    except subprocess.TimeoutExpired:
        result.errors.append(f"Pytest timed out after {timeout_s}s")
        result.passed = False
    except FileNotFoundError:
        result.errors.append("pytest not found in environment")
        result.passed = False

    return result


def _parse_pytest_output(
    result: VerificationResult,
    output: str,
    returncode: int,
) -> None:
    """Parse pytest -q output to extract pass/fail counts."""
    import re

    # Look for "X passed" or "X failed" in output
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    error_match = re.search(r"(\d+) error", output)

    result.tests_passed = int(passed_match.group(1)) if passed_match else 0
    result.tests_failed = int(failed_match.group(1)) if failed_match else 0
    errors = int(error_match.group(1)) if error_match else 0

    result.tests_run = result.tests_passed + result.tests_failed + errors
    result.passed = returncode == 0 and result.tests_failed == 0 and errors == 0

    if returncode != 0 and not result.tests_run:
        # pytest couldn't even collect tests
        result.errors.append(f"pytest exited with code {returncode}")
        result.passed = False
