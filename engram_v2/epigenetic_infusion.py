"""
EpigeneticInfusion — The Auto-Embedding Pipeline for tooloo-memory.

When SOTAGate discovers a new validated pattern, this module permanently
memorises it into the Vertex AI Vector Search 2.0 Collection that backs
`tooloo-memory`. The engine never has to re-research the same pattern again.

Architecture:
    CogJsonPayload         — dense .cog.json memory object (PsycheBank format)
    VertexVectorBackend    — abstract interface to Vertex AI Vector Search 2.0
    SynapseCollisionEngine — formats discoveries → CogJsonPayload → pushes to Vertex

Vertex AI Vector Search 2.0 Auto-Embedding flow:
    1. SynapseCollisionEngine formats discovery into CogJsonPayload
    2. Push CogJsonPayload as a DataObject to the Vertex Collection
       (vectors field intentionally left empty)
    3. Vertex AI intercepts → calls text-embedding-004 / gemini-embedding-001
    4. Generates 768-dim semantic vector in background
    5. Updates the nearest-neighbour index automatically
    6. Next JITContextAnchor.lookup() retrieves in milliseconds via ANN search

Evolutionary impact:
    - Lower latency over time: first fetch costs seconds; subsequent hits are ms
    - Smaller LLM footprint: validated SOTA patterns prevent hallucination
    - Self-healing baselines: SOTAGate patches stale memories in background
    - Zero manual overhead: no manual embedding pipeline required

Cross-module wiring:
    ConstitutionalGate.SOTAGate → SynapseCollisionEngine.embed_discovery()
    ShadowWeaver._compress_and_fetch() → SynapseCollisionEngine.embed_discovery()
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .cognitive_graph import CognitiveDualGraph
    from .schema import IntentEngram, LogicEngram

log = logging.getLogger(__name__)

# ── PsycheBank / .cog.json format ────────────────────────────

# Memory tiers — drives retrieval priority in JITContextAnchor
class MemoryTier(StrEnum):
    SOTA_FRESH = "sota_fresh"       # just fetched, confidence=1.0
    SOTA_WARM = "sota_warm"         # fetched < 24h ago
    LEARNED = "learned"             # verified over multiple mandates
    BASELINE = "baseline"           # seeded at repo initialisation
    DEPRECATED = "deprecated"       # superseded by newer SOTA

# Domain taxonomy (mirrors engram_v2/schema.Domain)
class CogDomain(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    AUTH = "auth"
    DATABASE = "database"
    INFRA = "infra"
    API_CONTRACT = "api_contract"
    CONFIG = "config"
    SECURITY = "security"
    GENERAL = "general"


@dataclass
class CogJsonPayload:
    """
    Dense PsycheBank memory object — the .cog.json format for tooloo-memory.

    One CogJsonPayload per discovered pattern / SOTA truth.
    Stored as a DataObject in Vertex AI Vector Search 2.0 Collection.
    The `vectors` field is intentionally absent — Vertex auto-embeds via
    vertex_embedding_config (text-embedding-004 or gemini-embedding-001).
    """

    cog_id: UUID = field(default_factory=uuid4)
    tier: MemoryTier = MemoryTier.SOTA_FRESH
    domain: CogDomain = CogDomain.GENERAL
    title: str = ""                    # concise label: "FastAPI lifespan pattern (2026)"
    frameworks: list[str] = field(default_factory=list)
    pattern_summary: str = ""          # 1-3 sentence dense summary
    code_exemplar: str = ""            # minimal code snippet (≤ 50 lines)
    source_url: str = ""               # canonical reference
    version_locked: str = ""           # e.g. "fastapi>=0.115"
    confidence: float = 1.0
    content_hash: str = ""             # SHA-256 of pattern_summary + code_exemplar
    engram_id: UUID | None = None      # originating engram (if applicable)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not self.content_hash:
            self._recompute_hash()

    def _recompute_hash(self) -> None:
        combined = self.pattern_summary + self.code_exemplar
        self.content_hash = hashlib.sha256(combined.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Serialise to DataObject format for Vertex AI Vector Search 2.0."""
        return {
            "id": str(self.cog_id),   # Vertex Collection DataObject id
            "tier": self.tier.value,
            "domain": self.domain.value,
            "title": self.title,
            "frameworks": self.frameworks,
            "pattern_summary": self.pattern_summary,
            "code_exemplar": self.code_exemplar[:2000],   # token cap
            "source_url": self.source_url,
            "version_locked": self.version_locked,
            "confidence": self.confidence,
            "content_hash": self.content_hash,
            "engram_id": str(self.engram_id) if self.engram_id else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # vectors intentionally absent — Vertex AI auto-embeds via:
            # vertex_embedding_config { model_config { version_column: "pattern_summary" } }
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CogJsonPayload":
        cog = cls(
            cog_id=UUID(data["id"]) if data.get("id") else uuid4(),
            tier=MemoryTier(data.get("tier", MemoryTier.BASELINE)),
            domain=CogDomain(data.get("domain", CogDomain.GENERAL)),
            title=data.get("title", ""),
            frameworks=data.get("frameworks", []),
            pattern_summary=data.get("pattern_summary", ""),
            code_exemplar=data.get("code_exemplar", ""),
            source_url=data.get("source_url", ""),
            version_locked=data.get("version_locked", ""),
            confidence=data.get("confidence", 1.0),
            content_hash=data.get("content_hash", ""),
            engram_id=UUID(data["engram_id"]) if data.get("engram_id") else None,
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
            updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
        )
        if not cog.content_hash:
            cog._recompute_hash()
        return cog

    def to_cog_json(self) -> str:
        """Render as pretty-printed .cog.json string for tooloo-memory repo."""
        return json.dumps(self.to_dict(), indent=2)


# ── Vertex AI Vector Search 2.0 backend (swappable) ──────────


class VertexVectorBackend:
    """
    Abstract Vertex AI Vector Search 2.0 interface.

    Production implementation:
        from google.cloud import aiplatform
        # Use MatchingEngineIndex / FeatureOnlineStore or
        # Vertex AI Vector Search 2.0 DataObject API (auto-embed capable)

    Collection configuration (one-time setup):
        collection = aiplatform.MatchingEngineIndexEndpoint.create(...)
        # or via REST:
        # POST /v1/projects/{project}/locations/{location}/collections
        # {
        #   "vector_schema": {
        #     "vertex_embedding_config": {
        #       "model_config": {
        #         "model_name": "text-embedding-004",
        #         "version_column": "pattern_summary"
        #       }
        #     }
        #   }
        # }

    DataObject push (vectors intentionally absent):
        collection.upsert_datapoints([
            {"id": str(cog.cog_id), "pattern_summary": cog.pattern_summary, ...}
        ])
        # Vertex intercepts, auto-calls text-embedding-004, stores 768-dim vector.

    Nearest-neighbour retrieval:
        results = collection.find_neighbors(
            query=embedding_of_mandate,
            num_neighbors=5,
        )
    """

    def __init__(self) -> None:
        self.embedded: list[CogJsonPayload] = []   # in-memory store for testing
        self._lock = threading.Lock()

    def upsert(self, payload: CogJsonPayload) -> str:
        """
        Push a DataObject to the Collection.

        Returns the DataObject id.
        Production note: pass `vectors={}` or omit the field entirely;
        Vertex AI auto-embeds `pattern_summary` via vertex_embedding_config.
        """
        with self._lock:
            # Deduplicate by content_hash
            existing = next(
                (p for p in self.embedded if p.content_hash == payload.content_hash),
                None,
            )
            if existing:
                log.debug("VertexVectorBackend: duplicate content_hash — skip embed")
                return str(existing.cog_id)
            self.embedded.append(payload)
        log.info(
            "VertexVectorBackend.upsert: id=%s tier=%s frameworks=%s",
            payload.cog_id,
            payload.tier.value,
            payload.frameworks,
        )
        return str(payload.cog_id)

    def find_nearest(self, query_text: str, num_neighbors: int = 5) -> list[CogJsonPayload]:
        """
        Stub ANN search — production calls Vertex AI Vector Search query endpoint.

        In production:
            embedding = embed_model.get_embeddings([query_text])[0].values
            results   = index_endpoint.find_neighbors(
                            deployed_index_id=DEPLOYED_INDEX_ID,
                            queries=[embedding],
                            num_neighbors=num_neighbors,
                        )
        """
        # Naive text-match stub for offline testing
        q = query_text.lower()
        scored = []
        with self._lock:
            for p in self.embedded:
                score = sum(
                    q.count(fw) for fw in p.frameworks
                ) + (1 if q in p.pattern_summary.lower() else 0)
                scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:num_neighbors]]

    def count(self) -> int:
        with self._lock:
            return len(self.embedded)


# ── SynapseCollisionEngine ────────────────────────────────────


class SynapseCollisionEngine:
    """
    Formats SOTA discoveries → CogJsonPayload → pushes to Vertex AI.

    This is the epigenetic infusion point: successful SOTAGate discoveries
    are permanently memorised so future mandates retrieve them in milliseconds.

    Called by:
      - ConstitutionalGate.SOTAGate._dispatch_ghost_fetch() on successful fetch
      - ShadowWeaver._compress_and_fetch() when Track B identifies new patterns
    """

    def __init__(self, backend: VertexVectorBackend | None = None) -> None:
        self._backend = backend or VertexVectorBackend()
        self._infusion_count = 0

    def embed_discovery(
        self,
        engram: "LogicEngram",
        sota_text: str,
        source_url: str = "",
        frameworks: list[str] | None = None,
        cognitive_graph: "CognitiveDualGraph | None" = None,
    ) -> CogJsonPayload:
        """
        Format a SOTA discovery and push it into tooloo-memory (Vertex AI Collection).

        If *cognitive_graph* is provided, also synthesises an IntentEngram from the
        SOTA pattern and registers it in the Cognitive Graph (Layer 2).

        Parameters
        ----------
        engram:          The engram that triggered the SOTA fetch.
        sota_text:       Raw SOTA text returned by GhostFetchDrone.
        source_url:      Canonical source (registry URL, docs page, etc.).
        frameworks:      Detected framework names (auto-extracted if None).
        cognitive_graph: Optional CognitiveDualGraph to register the synthesised intent.

        Returns
        -------
        The CogJsonPayload that was embedded.
        """
        from .constitution import _extract_framework_signatures  # avoid circular at top

        fws = frameworks or _extract_framework_signatures(engram.logic_body)

        cog = CogJsonPayload(
            tier=MemoryTier.SOTA_FRESH,
            domain=_domain_to_cog(engram.domain.value),
            title=_derive_title(engram.intent, fws),
            frameworks=fws,
            pattern_summary=_compress_sota(sota_text),
            code_exemplar=_extract_code_exemplar(sota_text),
            source_url=source_url,
            confidence=1.0,
            engram_id=engram.engram_id,
        )

        self._backend.upsert(cog)
        self._infusion_count += 1
        log.info(
            "SynapseCollisionEngine: infused cog=%s frameworks=%s (total=%d)",
            cog.cog_id,
            fws,
            self._infusion_count,
        )

        if cognitive_graph is not None:
            try:
                _, intent = self.synthesize_intent_from_sota(
                    engram, sota_text, cognitive_graph=cognitive_graph,
                )
                log.debug(
                    "SynapseCollisionEngine: synthesised IntentEngram %s from cog %s",
                    intent.intent_id,
                    cog.cog_id,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "SynapseCollisionEngine: intent synthesis failed for cog %s: %s",
                    cog.cog_id,
                    exc,
                )

        return cog

    def embed_intent(
        self,
        intent: "IntentEngram",
        cognitive_graph: "CognitiveDualGraph | None" = None,
    ) -> CogJsonPayload:
        """
        Push an IntentEngram into tooloo-memory AND register it in the Cognitive Graph.

        This is the primary ingest path for externally-constructed IntentEngrams
        (e.g. from IntelProcessor SOTA sweeps or human curation).

        Parameters
        ----------
        intent:          The IntentEngram to embed and register.
        cognitive_graph: CognitiveDualGraph to register the intent in Layer 2.
                         If None, only the Vertex AI embedding is created.

        Returns
        -------
        The CogJsonPayload that was pushed to Vertex AI.
        """
        domain = _intent_domain_to_cog(intent.domains)
        fws: list[str] = []
        for impl_id in intent.known_implementations[:3]:
            fws.append(str(impl_id)[:8])  # short UUID prefix as fw placeholder

        cog = CogJsonPayload(
            tier=MemoryTier.LEARNED,
            domain=domain,
            title=f"{intent.concept_label} (intent)",
            frameworks=fws,
            pattern_summary=intent.core_meaning[:500],
            code_exemplar="",
            source_url=intent.source_url or "",
            confidence=intent.confidence,
            engram_id=None,
        )

        self._backend.upsert(cog)
        self._infusion_count += 1
        log.info(
            "SynapseCollisionEngine: embedded IntentEngram %s (cog=%s)",
            intent.intent_id,
            cog.cog_id,
        )

        if cognitive_graph is not None:
            try:
                cognitive_graph.cognitive.add_intent(intent)
                log.debug(
                    "SynapseCollisionEngine: registered intent %s in CognitiveGraph",
                    intent.intent_id,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "SynapseCollisionEngine: CognitiveGraph registration failed for %s: %s",
                    intent.intent_id,
                    exc,
                )

        return cog

    def synthesize_intent_from_sota(
        self,
        engram: "LogicEngram",
        sota_text: str,
        cognitive_graph: "CognitiveDualGraph | None" = None,
    ) -> tuple[CogJsonPayload, "IntentEngram"]:
        """
        Auto-synthesise an IntentEngram from unstructured SOTA text.

        This is called automatically by embed_discovery() when a cognitive_graph is
        provided, or manually when processing bulk SOTA sweeps.

        The intent is derived from the engram's intent string and detected frameworks;
        it is pushed to Vertex AI via embed_intent() and, if cognitive_graph is
        provided, registered in Layer 2.

        Parameters
        ----------
        engram:          Source LogicEngram (provides domain + intent text).
        sota_text:       Raw SOTA text from GhostFetchDrone.
        cognitive_graph: Optional CognitiveDualGraph to register the synthesised intent.

        Returns
        -------
        Tuple of (CogJsonPayload, IntentEngram) that were created.
        """
        from .constitution import _extract_framework_signatures
        from .schema import IntentDomain, IntentEngram

        fws = _extract_framework_signatures(engram.logic_body)

        # Map engram domain → nearest IntentDomain
        intent_domain = _engram_domain_to_intent_domain(engram.domain.value)

        intent = IntentEngram(
            concept_label=engram.intent[:80].rstrip(),
            core_meaning=_compress_sota(sota_text),
            domains=[intent_domain],
            known_implementations=[engram.engram_id],
            common_scenarios=[engram.intent],
            security_posture=_extract_security_posture(sota_text),
            source_url=engram.source_url if hasattr(engram, "source_url") else "",
        )

        cog = self.embed_intent(intent, cognitive_graph=cognitive_graph)

        # If we have a cognitive_graph, also wire IMPLEMENTED_BY edge
        if cognitive_graph is not None:
            try:
                cognitive_graph.register_implementation(
                    intent.intent_id, engram.engram_id
                )
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "SynapseCollisionEngine: register_implementation skipped: %s", exc
                )

        return cog, intent

    def batch_infuse(self, discoveries: list[tuple["LogicEngram", str]]) -> list[CogJsonPayload]:
        """
        Batch-infuse multiple (engram, sota_text) discoveries.

        Used by background sweeper daemon to process pending discoveries
        accumulated during a mandate execution session.
        """
        results = []
        for engram, sota_text in discoveries:
            try:
                cog = self.embed_discovery(engram, sota_text)
                results.append(cog)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "SynapseCollisionEngine batch_infuse failed for engram %s: %s",
                    engram.engram_id,
                    exc,
                )
        return results

    def query_memory(self, mandate_text: str, num_neighbors: int = 5) -> list[CogJsonPayload]:
        """
        Query tooloo-memory for the most relevant .cog.json patterns.

        Called by ShadowWeaver._compress_and_fetch() to ground ContextTensor.
        """
        return self._backend.find_nearest(mandate_text, num_neighbors)

    @property
    def infusion_count(self) -> int:
        return self._infusion_count

    @property
    def memory_size(self) -> int:
        return self._backend.count()


# ── Helpers ───────────────────────────────────────────────────


def _domain_to_cog(domain: str) -> CogDomain:
    mapping = {
        "backend": CogDomain.BACKEND,
        "frontend": CogDomain.FRONTEND,
        "auth": CogDomain.AUTH,
        "database": CogDomain.DATABASE,
        "infra": CogDomain.INFRA,
        "api_contract": CogDomain.API_CONTRACT,
        "config": CogDomain.CONFIG,
        "test": CogDomain.GENERAL,
    }
    return mapping.get(domain, CogDomain.GENERAL)


def _derive_title(intent: str, frameworks: list[str]) -> str:
    year = datetime.now(UTC).year
    fws = ", ".join(frameworks[:3]) if frameworks else "general"
    # Truncate intent to ~60 chars for the title
    short_intent = intent[:60].rstrip()
    return f"{short_intent} [{fws}] ({year})"


def _compress_sota(sota_text: str) -> str:
    """Trim SOTA text to a dense ≤ 500 char summary for pattern_summary field."""
    return sota_text[:500].strip()


def _extract_code_exemplar(sota_text: str) -> str:
    """
    Extract a code block from SOTA text if present.

    Looks for fenced code blocks (``` ... ```) first; falls back to
    returning the first 800 chars of the text.
    """
    import re as _re
    fence_match = _re.search(r"```(?:\w+)?\n(.*?)```", sota_text, _re.DOTALL)
    if fence_match:
        return fence_match.group(1)[:1600].strip()
    return sota_text[:800]


def _intent_domain_to_cog(domains: "list") -> CogDomain:
    """Map the first IntentDomain to the nearest CogDomain."""
    from .schema import IntentDomain  # local import to avoid top-level circular

    _mapping = {
        IntentDomain.NETWORKING: CogDomain.INFRA,
        IntentDomain.UI_REACTIVITY: CogDomain.FRONTEND,
        IntentDomain.SECURITY: CogDomain.SECURITY,
        IntentDomain.REAL_TIME_SYNC: CogDomain.BACKEND,
        IntentDomain.CHAT_INTERACTION: CogDomain.FRONTEND,
        IntentDomain.CODE_GENERATION: CogDomain.BACKEND,
        IntentDomain.GRAPH_REASONING: CogDomain.BACKEND,
        IntentDomain.DATA_PERSISTENCE: CogDomain.DATABASE,
        IntentDomain.OBSERVABILITY: CogDomain.INFRA,
        IntentDomain.AUTHENTICATION: CogDomain.AUTH,
        IntentDomain.PERFORMANCE: CogDomain.BACKEND,
        IntentDomain.ORCHESTRATION: CogDomain.BACKEND,
        IntentDomain.GENERAL: CogDomain.GENERAL,
    }
    if not domains:
        return CogDomain.GENERAL
    return _mapping.get(domains[0], CogDomain.GENERAL)


def _engram_domain_to_intent_domain(domain_str: str) -> "IntentDomain":
    """Map a LogicEngram domain string to the nearest IntentDomain."""
    from .schema import IntentDomain

    _mapping = {
        "backend": IntentDomain.CODE_GENERATION,
        "frontend": IntentDomain.UI_REACTIVITY,
        "auth": IntentDomain.AUTHENTICATION,
        "database": IntentDomain.DATA_PERSISTENCE,
        "infra": IntentDomain.OBSERVABILITY,
        "api_contract": IntentDomain.NETWORKING,
        "config": IntentDomain.CODE_GENERATION,
        "security": IntentDomain.SECURITY,
        "test": IntentDomain.CODE_GENERATION,
    }
    return _mapping.get(domain_str, IntentDomain.CODE_GENERATION)


def _extract_security_posture(sota_text: str) -> str:
    """
    Extract a security posture statement from SOTA text.

    Looks for lines mentioning auth, injection, XSS, CSRF, encryption, OWASP.
    Returns a brief summary or empty string if nothing security-specific found.
    """
    import re as _re

    security_pattern = _re.compile(
        r"(?i)(auth\w*|inject|xss|csrf|encrypt\w*|owasp|tls|secret\w*|token\w*"
        r"|csrf|sanitiz\w*|escap\w*|rbac|permission)[^\n.]{0,200}",
        _re.DOTALL,
    )
    hits = security_pattern.findall(sota_text)
    if not hits:
        return ""
    # Deduplicate and join up to 3 unique hits
    seen: set[str] = set()
    parts: list[str] = []
    for h in hits:
        key = h[:20].lower()
        if key not in seen:
            seen.add(key)
            parts.append(h.strip())
        if len(parts) >= 3:
            break
    return "; ".join(parts)[:300]
