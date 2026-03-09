"""
EngramGraph — NetworkX DAG wrapper.

The "filesystem" of the AI-native repo. Logic Engrams are nodes,
SynapticEdges are directed edges. Enforces DAG constraint (no cycles),
supports subgraph extraction, topological traversal, and fractal rollback.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from uuid import UUID

import networkx as nx

from .schema import Domain, LogicEngram, SynapticEdge


class CycleDetectedError(Exception):
    """Raised when adding an edge would create a cycle in the DAG."""


class EngramGraph:
    """NetworkX-backed Directed Acyclic Graph of Logic Engrams."""

    def __init__(self, *, decay_radius: int = 3) -> None:
        self._g = nx.DiGraph()
        self._engrams: dict[UUID, LogicEngram] = {}
        self._edges: dict[UUID, SynapticEdge] = {}
        self._decay_radius = decay_radius  # max BFS depth for lazy-loading subgraphs

    # ── Node Operations ──────────────────────────────────────────

    def add_engram(self, engram: LogicEngram) -> UUID:
        """Add a Logic Engram as a node in the graph."""
        self._g.add_node(
            str(engram.engram_id),
            intent=engram.intent,
            domain=engram.domain.value,
            module_path=engram.module_path,
            language=engram.language.value,
        )
        self._engrams[engram.engram_id] = engram
        return engram.engram_id

    def get_engram(self, engram_id: UUID) -> LogicEngram | None:
        return self._engrams.get(engram_id)

    def has_engram(self, engram_id: UUID) -> bool:
        return engram_id in self._engrams

    # ── Edge Operations ──────────────────────────────────────────

    def add_edge(self, edge: SynapticEdge) -> UUID:
        """Add a directed edge. Rejects cycles (DAG enforcement)."""
        src = str(edge.source_id)
        tgt = str(edge.target_id)

        if src not in self._g or tgt not in self._g:
            missing = []
            if src not in self._g:
                missing.append(f"source={edge.source_id}")
            if tgt not in self._g:
                missing.append(f"target={edge.target_id}")
            raise ValueError(f"Cannot add edge — missing nodes: {', '.join(missing)}")

        # Tentatively add, check for cycle, rollback if needed
        self._g.add_edge(src, tgt, edge_type=edge.edge_type.value, weight=edge.weight)
        if not nx.is_directed_acyclic_graph(self._g):
            self._g.remove_edge(src, tgt)
            raise CycleDetectedError(
                f"Edge {edge.source_id} → {edge.target_id} would create a cycle"
            )

        edge.verified = True
        self._edges[edge.edge_id] = edge
        return edge.edge_id

    def get_edge(self, edge_id: UUID) -> SynapticEdge | None:
        return self._edges.get(edge_id)

    # ── Query Operations ─────────────────────────────────────────

    def query_by_intent(self, intent: str, top_k: int = 5) -> list[LogicEngram]:
        """TF-IDF-style similarity search on intent strings."""
        if not self._engrams:
            return []

        query_terms = _tokenize(intent)
        if not query_terms:
            return list(self._engrams.values())[:top_k]

        # Build document frequency
        all_docs: list[tuple[UUID, list[str]]] = []
        for eid, eng in self._engrams.items():
            tokens = _tokenize(eng.intent)
            all_docs.append((eid, tokens))

        n_docs = len(all_docs)
        df: Counter[str] = Counter()
        for _, tokens in all_docs:
            df.update(set(tokens))

        # Score each engram
        scores: list[tuple[float, UUID]] = []
        for eid, tokens in all_docs:
            tf: Counter[str] = Counter(tokens)
            score = 0.0
            for term in query_terms:
                if term in tf and term in df:
                    idf = math.log((n_docs + 1) / (df[term] + 1)) + 1
                    score += tf[term] * idf
            scores.append((score, eid))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [self._engrams[eid] for _, eid in scores[:top_k] if _ > 0]

    def query_by_domain(self, domain: Domain) -> list[LogicEngram]:
        return [e for e in self._engrams.values() if e.domain == domain]

    def query_by_module(self, module_path: str) -> list[LogicEngram]:
        return [e for e in self._engrams.values() if e.module_path == module_path]

    # ── Subgraph Extraction ──────────────────────────────────────

    def get_dependency_subgraph(self, engram_id: UUID, depth: int | None = None) -> nx.DiGraph:
        """BFS subgraph extraction — returns local neighbourhood.

        ``depth`` defaults to ``self._decay_radius`` to prevent
        runaway traversal on large graphs.
        """
        if depth is None:
            depth = self._decay_radius
        node = str(engram_id)
        if node not in self._g:
            return nx.DiGraph()

        # Collect nodes within BFS depth (both directions)
        visited: set[str] = set()
        frontier = {node}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                if n not in visited:
                    visited.add(n)
                    next_frontier.update(self._g.successors(n))
                    next_frontier.update(self._g.predecessors(n))
            frontier = next_frontier - visited
        visited.update(frontier)

        return self._g.subgraph(visited).copy()

    def topological_order(self) -> list[UUID]:
        """Return engram IDs in topological order (dependencies first)."""
        ordered = list(nx.topological_sort(self._g))
        return [UUID(nid) for nid in ordered if UUID(nid) in self._engrams]

    # ── Rollback & Healing ───────────────────────────────────────

    def rollback_engram(self, engram_id: UUID) -> bool:
        """Remove a node and all connected edges (fractal rollback)."""
        node = str(engram_id)
        if node not in self._g:
            return False

        # Remove associated edges from our registry
        to_remove = []
        for eid, edge in self._edges.items():
            if edge.source_id == engram_id or edge.target_id == engram_id:
                to_remove.append(eid)
        for eid in to_remove:
            del self._edges[eid]

        self._g.remove_node(node)
        del self._engrams[engram_id]
        return True

    def validate_integrity(self) -> list[str]:
        """Check all edges point to valid nodes, no orphans, no cycles."""
        issues: list[str] = []

        # DAG check
        if not nx.is_directed_acyclic_graph(self._g):
            issues.append("CRITICAL: Graph contains cycles")

        # Edge endpoint validation
        for eid, edge in self._edges.items():
            src = str(edge.source_id)
            tgt = str(edge.target_id)
            if src not in self._g:
                issues.append(f"Edge {eid}: source {edge.source_id} missing from graph")
            if tgt not in self._g:
                issues.append(f"Edge {eid}: target {edge.target_id} missing from graph")

        # Checksum verification
        for engram in self._engrams.values():
            if not engram.logic_body:
                issues.append(f"Engram {engram.engram_id}: empty logic_body")

        # Orphan detection (nodes with no edges at all)
        for node in self._g.nodes():
            if self._g.degree(node) == 0 and len(self._engrams) > 1:
                issues.append(f"Engram {node}: orphan (no edges)")

        return issues

    # ── Serialization ────────────────────────────────────────────

    def serialize(self) -> str:
        """JSON export of full graph state."""
        data = {
            "engrams": [e.to_dict() for e in self._engrams.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
            "decay_radius": self._decay_radius,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def deserialize(cls, data: str) -> EngramGraph:
        """Reconstruct graph from JSON."""
        parsed = json.loads(data)
        graph = cls(decay_radius=parsed.get("decay_radius", 3))
        for ed in parsed.get("engrams", []):
            graph.add_engram(LogicEngram.from_dict(ed))
        for ed in parsed.get("edges", []):
            edge = SynapticEdge.from_dict(ed)
            try:
                graph.add_edge(edge)
            except (ValueError, CycleDetectedError):
                pass  # Skip invalid edges on deserialize
        return graph

    # ── Convenience properties ────────────────────────────────────

    @property
    def node_count(self) -> int:
        """Number of engrams currently in the graph."""
        return len(self._engrams)

    @property
    def edge_count(self) -> int:
        """Number of edges currently in the graph."""
        return len(self._edges)

    # ── Statistics & Summary ─────────────────────────────────────

    def stats(self) -> dict:
        return {
            "engram_count": len(self._engrams),
            "edge_count": len(self._edges),
            "nodes": len(self._engrams),
            "edges": len(self._edges),
            "max_depth": nx.dag_longest_path_length(self._g) if self._engrams else 0,
            "connected_components": nx.number_weakly_connected_components(self._g)
            if self._engrams
            else 0,
            "domains": dict(Counter(e.domain.value for e in self._engrams.values())),
            "modules": dict(
                Counter(e.module_path for e in self._engrams.values() if e.module_path)
            ),
        }

    def to_token_summary(self) -> str:
        """Compressed graph representation for LLM context (intents + edges only).

        This is the key innovation: instead of sending full file contents,
        send only the graph topology as structured text.
        """
        lines: list[str] = ["<graph_topology>"]

        # Group engrams by module
        by_module: dict[str, list[LogicEngram]] = {}
        for eng in self._engrams.values():
            key = eng.module_path or "(unassigned)"
            by_module.setdefault(key, []).append(eng)

        for module, engrams in sorted(by_module.items()):
            lines.append(f'  <module path="{module}">')
            for eng in engrams:
                lines.append(
                    f'    <engram id="{eng.engram_id}" '
                    f'intent="{eng.intent}" '
                    f'sig="{eng.ast_signature}" '
                    f'domain="{eng.domain.value}" />'
                )
            lines.append("  </module>")

        if self._edges:
            lines.append("  <edges>")
            for edge in self._edges.values():
                lines.append(
                    f'    <edge src="{edge.source_id}" '
                    f'tgt="{edge.target_id}" '
                    f'type="{edge.edge_type.value}" />'
                )
            lines.append("  </edges>")

        lines.append("</graph_topology>")
        return "\n".join(lines)


def _tokenize(text: str) -> list[str]:
    """Simple word tokenizer for TF-IDF."""
    return re.findall(r"[a-z]+", text.lower())
