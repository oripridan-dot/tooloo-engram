"""
CognitiveDualGraph — The Dual-Graph Architecture.

Layer 1: Execution Graph (The What)
    Standard EngramGraph — DAG of LogicEngrams (functions, API routes, components).
    Represents concrete, deployable reality.

Layer 2: Cognitive Graph (The Why & How)
    CognitiveGraph — network of IntentEngrams.
    Stores semantic definitions, common scenarios, domain knowledge,
    and historical trade-offs.  Contains NO code.

The two layers communicate via semantic SynapticEdges:
    IMPLEMENTED_BY       IntentEngram → LogicEngram   (concept → concrete code)
    FREQUENTLY_USED_WITH IntentEngram ↔ IntentEngram  (concept co-occurrence)
    ALTERNATIVE_TO       IntentEngram ↔ IntentEngram  (competing approaches)
    SPECIALISES          IntentEngram → IntentEngram  (narrow → broad)
    DEPRECATED_BY        IntentEngram → IntentEngram  (superseded → replacement)
    SECURITY_GOVERNS     IntentEngram → IntentEngram  (security posture → domain)

Key performance benefits:
    • Semantic retrieval first: SynapseCollisionEngine queries the Cognitive Graph
      with a few hundred abstract tokens before any code is generated.
    • Zero-shot generalisation: intents learned in one domain port instantly to
      another via the SECURITY_GOVERNS / FREQUENTLY_USED_WITH topology.
    • Perpetual evolution: IMPLEMENTED_BY edges are updated when SOTA recon
      finds a better implementation; the intent never breaks.
    • Radical token reduction: abstract reasoning costs 10-50× fewer tokens
      than code-level reasoning.

Public API:
    CognitiveGraph          — the Layer 2 graph (NetworkX DiGraph of IntentEngrams)
    CognitiveDualGraph      — container that wires Layer 1 (EngramGraph) + Layer 2
    SemanticCollisionResult — result of colliding two IntentEngrams to resolve the
                              optimal LogicEngram path for a mandate
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import networkx as nx

from .schema import (
    EdgeType,
    IntentDomain,
    IntentEngram,
    SynapticEdge,
)

if TYPE_CHECKING:
    from .graph_store import EngramGraph
    from .schema import LogicEngram

log = logging.getLogger(__name__)


# ── CognitiveGraph (Layer 2) ──────────────────────────────────

class CognitiveGraph:
    """
    NetworkX-backed graph of IntentEngrams (Layer 2 — the Why & How).

    Unlike the Execution Graph (which is a strict DAG), the Cognitive
    Graph permits cycles because conceptual relationships are bidirectional
    (e.g., "Real-Time Sync" ↔ "Chat Interaction").

    All edges between IntentEngrams use the semantic EdgeType values:
    IMPLEMENTED_BY, FREQUENTLY_USED_WITH, ALTERNATIVE_TO, SPECIALISES,
    DEPRECATED_BY, SECURITY_GOVERNS.
    """

    _SEMANTIC_EDGE_TYPES: frozenset[EdgeType] = frozenset({
        EdgeType.IMPLEMENTED_BY,
        EdgeType.FREQUENTLY_USED_WITH,
        EdgeType.ALTERNATIVE_TO,
        EdgeType.SPECIALISES,
        EdgeType.DEPRECATED_BY,
        EdgeType.SECURITY_GOVERNS,
    })

    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._intents: dict[UUID, IntentEngram] = {}
        self._edges: dict[UUID, SynapticEdge] = {}

    # ── Node Operations ──────────────────────────────────────

    def add_intent(self, intent: IntentEngram) -> UUID:
        """Add an IntentEngram node to the Cognitive Graph."""
        self._g.add_node(
            str(intent.intent_id),
            concept_label=intent.concept_label,
            domains=[d.value for d in intent.domains],
            confidence=intent.confidence,
        )
        self._intents[intent.intent_id] = intent
        log.debug("CognitiveGraph.add_intent: %s '%s'", intent.intent_id, intent.concept_label)
        return intent.intent_id

    def get_intent(self, intent_id: UUID) -> IntentEngram | None:
        return self._intents.get(intent_id)

    def has_intent(self, intent_id: UUID) -> bool:
        return intent_id in self._intents

    def all_intents(self) -> list[IntentEngram]:
        return list(self._intents.values())

    # ── Edge Operations ───────────────────────────────────────

    def add_semantic_edge(self, edge: SynapticEdge) -> UUID:
        """
        Add a semantic edge between two IntentEngrams.

        Both endpoints must already exist in the Cognitive Graph.
        Only semantic EdgeType values are accepted (not execution-layer types).
        """
        if edge.edge_type not in self._SEMANTIC_EDGE_TYPES:
            raise ValueError(
                f"Edge type '{edge.edge_type}' is not a semantic type. "
                f"Use one of: {[e.value for e in self._SEMANTIC_EDGE_TYPES]}"
            )
        src_str = str(edge.source_id)
        tgt_str = str(edge.target_id)
        if src_str not in self._g or tgt_str not in self._g:
            missing = []
            if src_str not in self._g:
                missing.append(f"source={edge.source_id}")
            if tgt_str not in self._g:
                missing.append(f"target={edge.target_id}")
            raise ValueError(f"Cannot add edge — missing nodes: {', '.join(missing)}")
        edge.verified = True
        self._g.add_edge(src_str, tgt_str, edge_type=edge.edge_type.value, weight=edge.weight)
        self._edges[edge.edge_id] = edge
        return edge.edge_id

    def get_neighbours(
        self,
        intent_id: UUID,
        edge_type: EdgeType | None = None,
    ) -> list[IntentEngram]:
        """Return all IntentEngrams connected to *intent_id*, optionally filtered by edge type."""
        results: list[IntentEngram] = []
        for _, tgt_str, data in self._g.out_edges(str(intent_id), data=True):
            if edge_type and data.get("edge_type") != edge_type.value:
                continue
            tgt = self._intents.get(UUID(tgt_str))
            if tgt:
                results.append(tgt)
        return results

    # ── Semantic Search ───────────────────────────────────────

    def search_by_concept(self, query: str, top_k: int = 5) -> list[IntentEngram]:
        """
        Rank IntentEngrams by relevance to *query* using lightweight token overlap.

        In production: replace with Vertex AI Vector Search ANN query from
        SynapseCollisionEngine.  This implementation is fully offline / zero-cost.
        """
        q_tokens = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[float, IntentEngram]] = []
        for intent in self._intents.values():
            haystack = (
                intent.concept_label + " "
                + intent.core_meaning + " "
                + " ".join(intent.common_scenarios)
                + " ".join(d.value for d in intent.domains)
            ).lower()
            hay_tokens = set(re.findall(r"\w+", haystack))
            overlap = len(q_tokens & hay_tokens) / max(1, len(q_tokens))
            # Boost active (non-deprecated) confident intents
            score = overlap * intent.confidence * (0.5 if intent.is_deprecated else 1.0)
            scored.append((score, intent))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [i for _, i in scored[:top_k] if _ > 0]

    def search_by_domain(self, domain: IntentDomain) -> list[IntentEngram]:
        return [i for i in self._intents.values() if domain in i.domains]

    # ── Topology helpers ──────────────────────────────────────

    def implementations_of(self, intent_id: UUID) -> list[UUID]:
        """Return LogicEngram IDs referenced by IMPLEMENTED_BY edges from *intent_id*."""
        intent = self._intents.get(intent_id)
        return list(intent.known_implementations) if intent else []

    def deprecation_chain(self, intent_id: UUID) -> list[IntentEngram]:
        """Follow DEPRECATED_BY edges and return the full supersession chain."""
        chain: list[IntentEngram] = []
        current = self._intents.get(intent_id)
        visited: set[UUID] = set()
        while current and current.is_deprecated and current.intent_id not in visited:
            visited.add(current.intent_id)
            chain.append(current)
            replacements = self.get_neighbours(current.intent_id, EdgeType.DEPRECATED_BY)
            current = replacements[0] if replacements else None
        return chain

    # ── Stats ─────────────────────────────────────────────────

    @property
    def intent_count(self) -> int:
        return len(self._intents)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def stats(self) -> dict:
        domain_counts: dict[str, int] = {}
        for intent in self._intents.values():
            for d in intent.domains:
                domain_counts[d.value] = domain_counts.get(d.value, 0) + 1
        return {
            "intent_count": self.intent_count,
            "edge_count": self.edge_count,
            "deprecated_count": sum(1 for i in self._intents.values() if i.is_deprecated),
            "domain_distribution": domain_counts,
        }


# ── SemanticCollisionResult ───────────────────────────────────

@dataclass
class SemanticCollisionResult:
    """
    Result of colliding two or more IntentEngrams to resolve the optimal
    LogicEngram path for an operator mandate.

    The SynapseCollisionEngine produces this via a lightweight LLM prompt
    over abstract concepts — not raw code — giving radical token efficiency.
    """

    mandate_summary: str
    resolved_intents: list[IntentEngram]
    recommended_implementations: list[UUID]   # LogicEngram IDs to pull into execution
    collision_rationale: str                  # brief LLM explanation (≤300 tokens)
    token_cost_estimate: int = 0              # tokens used for the collision prompt
    confidence: float = 1.0
    collision_id: UUID = field(default_factory=uuid4)
    resolved_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "collision_id": str(self.collision_id),
            "mandate_summary": self.mandate_summary,
            "resolved_intents": [str(i.intent_id) for i in self.resolved_intents],
            "recommended_implementations": [str(uid) for uid in self.recommended_implementations],
            "collision_rationale": self.collision_rationale,
            "token_cost_estimate": self.token_cost_estimate,
            "confidence": self.confidence,
            "resolved_at": self.resolved_at,
        }


# ── CognitiveDualGraph (public facade) ───────────────────────

class CognitiveDualGraph:
    """
    Container that wires the two graph layers together.

    Layer 1 (execution_graph):  EngramGraph — DAG of LogicEngrams.
    Layer 2 (cognitive_graph):  CognitiveGraph — network of IntentEngrams.

    Cross-layer edges (IMPLEMENTED_BY) are stored in execution_graph
    to preserve its existing edge API, but registered here for fast lookup.

    Typical usage by SynapseCollisionEngine:
        1. dual.resolve_mandate(operator_mandate_text)
            → searches Layer 2 first (cheap: concept tokens only)
            → returns SemanticCollisionResult with recommended LogicEngram IDs
        2. CompilerDrone pulls only those LogicEngrams into context
            → radical token reduction vs. loading all code bodies
    """

    def __init__(
        self,
        execution_graph: "EngramGraph",
        cognitive_graph: CognitiveGraph | None = None,
    ) -> None:
        self.execution = execution_graph
        self.cognitive = cognitive_graph or CognitiveGraph()

    # ── Cross-layer wiring ────────────────────────────────────

    def register_implementation(
        self, intent_id: UUID, logic_engram_id: UUID, weight: float = 1.0
    ) -> None:
        """
        Register that *logic_engram_id* implements *intent_id*.

        Creates an IMPLEMENTED_BY edge from the IntentEngram to the LogicEngram
        in the Cognitive Graph's edge store, and updates the IntentEngram's
        known_implementations list.
        """
        intent = self.cognitive.get_intent(intent_id)
        if intent is None:
            raise ValueError(f"IntentEngram {intent_id} not found in Cognitive Graph")
        if not self.execution.has_engram(logic_engram_id):
            raise ValueError(f"LogicEngram {logic_engram_id} not found in Execution Graph")

        if logic_engram_id not in intent.known_implementations:
            intent.known_implementations.append(logic_engram_id)
            intent.updated_at = datetime.now(UTC)

        edge = SynapticEdge(
            source_id=intent_id,   # type: ignore[arg-type]  # UUID (intent) → UUID (engram)
            target_id=logic_engram_id,
            edge_type=EdgeType.IMPLEMENTED_BY,
            weight=weight,
            verified=True,
        )
        self.cognitive._edges[edge.edge_id] = edge  # noqa: SLF001
        log.debug(
            "CognitiveDualGraph.register_implementation: %s → %s",
            intent_id,
            logic_engram_id,
        )

    def update_implementation(
        self, intent_id: UUID, old_logic_id: UUID, new_logic_id: UUID
    ) -> None:
        """
        Swap an IMPLEMENTED_BY reference when SOTA recon finds a better implementation.

        The intent's semantic meaning is preserved — only the concrete pointer changes.
        """
        intent = self.cognitive.get_intent(intent_id)
        if intent is None:
            return
        if old_logic_id in intent.known_implementations:
            intent.known_implementations.remove(old_logic_id)
        if new_logic_id not in intent.known_implementations:
            intent.known_implementations.append(new_logic_id)
        intent.updated_at = datetime.now(UTC)
        log.info(
            "CognitiveDualGraph.update_implementation: %s swapped %s → %s",
            intent_id,
            old_logic_id,
            new_logic_id,
        )

    # ── Mandate resolution ────────────────────────────────────

    def resolve_mandate(
        self,
        mandate_text: str,
        top_intents: int = 3,
        llm_collider: "_LLMCollider | None" = None,
    ) -> SemanticCollisionResult:
        """
        Resolve an operator mandate by colliding abstract IntentEngrams first.

        Flow:
          1. Semantic search Layer 2 for top-N matching IntentEngrams
          2. Collide them via lightweight LLM call (gemini-3.1-flash-lite)
          3. Return recommended LogicEngram IDs (from IMPLEMENTED_BY edges)

        This costs ~50-200 tokens instead of thousands for code-level reasoning.
        """
        matching_intents = self.cognitive.search_by_concept(mandate_text, top_k=top_intents)
        if not matching_intents:
            return SemanticCollisionResult(
                mandate_summary=mandate_text,
                resolved_intents=[],
                recommended_implementations=[],
                collision_rationale="No matching IntentEngrams found — cold start.",
                confidence=0.4,
            )

        # Gather all IMPLEMENTED_BY references from matching intents
        impl_ids: list[UUID] = []
        for intent in matching_intents:
            impl_ids.extend(intent.known_implementations)
        # Deduplicate preserving order
        seen: set[UUID] = set()
        impl_ids = [uid for uid in impl_ids if not (uid in seen or seen.add(uid))]  # type: ignore[func-returns-value]

        # Lightweight LLM collision (stub — replace with live gemini-3.1-flash-lite call)
        collider = llm_collider or _DefaultLLMCollider()
        rationale, token_cost = collider.collide(mandate_text, matching_intents)

        avg_confidence = sum(i.confidence for i in matching_intents) / len(matching_intents)

        return SemanticCollisionResult(
            mandate_summary=mandate_text,
            resolved_intents=matching_intents,
            recommended_implementations=impl_ids[:10],  # hard cap
            collision_rationale=rationale,
            token_cost_estimate=token_cost,
            confidence=avg_confidence,
        )

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "execution_graph": self.execution.stats(),
            "cognitive_graph": self.cognitive.stats(),
        }


# ── Lightweight LLM collider interface ───────────────────────

class _LLMCollider:
    def collide(
        self, mandate: str, intents: list[IntentEngram]
    ) -> tuple[str, int]:
        raise NotImplementedError


class _DefaultLLMCollider(_LLMCollider):
    """
    Stub collider — replace with gemini-3.1-flash-lite call in production.

    Production implementation:
        prompt = build_collision_prompt(mandate, intents)   # ≤ 500 tokens
        response = genai.GenerativeModel("gemini-3.1-flash-lite").generate_content(
            prompt,
            generation_config=genai.GenerationConfig(thinking_budget=0, max_output_tokens=300)
        )
        return response.text, count_tokens(prompt + response.text)
    """

    def collide(
        self, mandate: str, intents: list[IntentEngram]
    ) -> tuple[str, int]:
        labels = [i.concept_label for i in intents]
        rationale = (
            f"Colliding concepts [{', '.join(labels)}] for mandate: '{mandate[:80]}'. "
            f"Recommended path: use implementations from top-confidence intent."
        )
        token_estimate = len(rationale) // 4 + sum(
            len(i.core_meaning) // 4 for i in intents
        )
        return rationale, token_estimate
