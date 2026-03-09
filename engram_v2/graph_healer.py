"""
Graph Healer — DAG integrity validator + targeted rollback.

The "Immune Layer" for the AI-native codebase. Validates structural
integrity, detects corrupted edges/orphan nodes, and performs
targeted rollback without affecting healthy parts of the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .graph_store import EngramGraph
    from .schema import LogicEngram


@dataclass
class HealingReport:
    """Result of a graph healing pass."""

    issues_found: list[str] = field(default_factory=list)
    engrams_rolled_back: list[UUID] = field(default_factory=list)
    edges_removed: int = 0
    is_healthy: bool = True

    def to_dict(self) -> dict:
        return {
            "issues_found": self.issues_found,
            "engrams_rolled_back": [str(eid) for eid in self.engrams_rolled_back],
            "edges_removed": self.edges_removed,
            "is_healthy": self.is_healthy,
        }


def validate_and_heal(graph: EngramGraph) -> HealingReport:
    """Run full integrity check and heal any issues found.

    1. Detect unregistered NetworkX edges/nodes (phantom cleanup)
    2. Check DAG constraint (no cycles)
    3. Check all edges resolve to real nodes
    4. Check for empty logic bodies
    5. Remove orphan edges
    6. Report on overall health
    """
    report = HealingReport()

    # Phase 0: Clean phantom nodes/edges injected directly into NetworkX
    #          (bypassing the EngramGraph API)
    report.edges_removed += _purge_phantom_elements(graph)

    # Phase 1: Structural integrity check
    issues = graph.validate_integrity()
    report.issues_found.extend(issues)

    # Filter: orphan warnings are informational, not structural failures
    critical_issues = [i for i in issues if "orphan" not in i]

    if not critical_issues:
        report.is_healthy = True
        return report

    # Phase 2: Targeted healing
    for issue in issues:
        if "empty logic_body" in issue:
            # Extract engram ID from issue string
            eid = _extract_engram_id(issue)
            if eid and graph.has_engram(eid):
                graph.rollback_engram(eid)
                report.engrams_rolled_back.append(eid)
                report.edges_removed += 1

        elif "missing from graph" in issue:
            # Edge references a deleted node — find and remove the edge
            eid = _extract_edge_id(issue)
            if eid:
                edge = graph.get_edge(eid)
                if edge:
                    _remove_dangling_edge(graph, eid)
                    report.edges_removed += 1

        elif "orphan" in issue:
            # Orphan node — keep but flag (user may want to connect it)
            pass  # Don't auto-delete orphans; they may be valid standalone engrams

    # Phase 3: Re-validate after healing
    remaining = graph.validate_integrity()
    # Orphan warnings are informational, not structural failures
    critical_remaining = [r for r in remaining if "orphan" not in r]
    report.is_healthy = len(critical_remaining) == 0
    if remaining:
        report.issues_found.extend([f"POST-HEAL: {r}" for r in remaining])

    return report


def validate_engram_output(engram: LogicEngram) -> list[str]:
    """Validate a single Engram's output after LLM generation."""
    issues: list[str] = []

    if not engram.logic_body.strip():
        issues.append(f"Engram {engram.engram_id}: empty logic_body")

    if engram.language.value == "python":
        import ast as _ast

        try:
            _ast.parse(engram.logic_body)
        except SyntaxError as e:
            issues.append(f"Engram {engram.engram_id}: SyntaxError — {e}")

    if not engram.intent:
        issues.append(f"Engram {engram.engram_id}: missing intent")

    if not engram.ast_signature:
        issues.append(f"Engram {engram.engram_id}: missing ast_signature")

    return issues


def validate_and_heal_with_verification(
    graph: EngramGraph,
    *,
    timeout_s: int = 30,
) -> HealingReport:
    """Extended healing: structural validation + execution verification.

    Phase 0–3: Same as validate_and_heal (structural).
    Phase 4: Run TESTS edges in isolated sandboxes for tested engrams.
    Failed tests mark the target engram for rollback.
    """
    from .verification_engine import verify_all_tested_engrams

    # Structural healing first
    report = validate_and_heal(graph)

    # Phase 4: Execution verification via TESTS edges
    verification_results = verify_all_tested_engrams(graph, timeout_s=timeout_s)
    for vr in verification_results:
        if not vr.passed and vr.tests_run > 0:
            report.issues_found.append(
                f"Engram {vr.target_engram_id}: TESTS execution failed "
                f"({vr.tests_failed}/{vr.tests_run} failed)"
            )
            if graph.has_engram(vr.target_engram_id):
                graph.rollback_engram(vr.target_engram_id)
                report.engrams_rolled_back.append(vr.target_engram_id)
                report.is_healthy = False

    return report


def _extract_engram_id(issue_text: str) -> UUID | None:
    """Extract engram UUID from an issue string."""
    # Pattern: "Engram <uuid>: ..."
    parts = issue_text.split(":")
    if len(parts) >= 2:
        id_part = parts[0].replace("Engram ", "").strip()
        try:
            return UUID(id_part)
        except ValueError:
            return None
    return None


def _extract_edge_id(issue_text: str) -> UUID | None:
    """Extract edge UUID from an issue string."""
    parts = issue_text.split(":")
    if len(parts) >= 2:
        id_part = parts[0].replace("Edge ", "").strip()
        try:
            return UUID(id_part)
        except ValueError:
            return None
    return None


def _remove_dangling_edge(graph: EngramGraph, edge_id: UUID) -> None:
    """Remove an edge with a missing endpoint from the graph."""
    if edge_id in graph._edges:
        edge = graph._edges[edge_id]
        src = str(edge.source_id)
        tgt = str(edge.target_id)
        if graph._g.has_edge(src, tgt):
            graph._g.remove_edge(src, tgt)
        del graph._edges[edge_id]


def _purge_phantom_elements(graph: EngramGraph) -> int:
    """Remove nodes and edges present in NetworkX but not registered in EngramGraph.

    Returns the number of phantom edges removed.
    """
    removed = 0

    # 1. Detect phantom nodes (in NetworkX but not in _engrams)
    registered_nodes = {str(eid) for eid in graph._engrams}
    nx_nodes = set(graph._g.nodes())
    phantom_nodes = nx_nodes - registered_nodes

    # 2. Remove edges involving phantom nodes first
    edges_to_remove: list[tuple[str, str]] = []
    for src, tgt in list(graph._g.edges()):
        if src in phantom_nodes or tgt in phantom_nodes:
            edges_to_remove.append((src, tgt))

    # Also detect edges in NetworkX that aren't in _edges registry
    registered_edge_pairs: set[tuple[str, str]] = set()
    for edge in graph._edges.values():
        registered_edge_pairs.add((str(edge.source_id), str(edge.target_id)))
    for src, tgt in list(graph._g.edges()):
        if (src, tgt) not in registered_edge_pairs and (src, tgt) not in edges_to_remove:
            edges_to_remove.append((src, tgt))

    for src, tgt in edges_to_remove:
        graph._g.remove_edge(src, tgt)
        removed += 1

    # 3. Remove phantom nodes themselves
    for node in phantom_nodes:
        graph._g.remove_node(node)

    return removed
