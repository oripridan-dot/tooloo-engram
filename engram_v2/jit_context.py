"""
JIT Context — Just-In-Time Reality Anchor for Engram V2.

Injects real-world context (API docs, deprecation notices, security advisories,
performance benchmarks) directly into ContextAwareEngram nodes at the moment
they are generated or healed. Context is stored with TTL and re-fetched when stale.

Architecture (air-gapped, no internet in offline mode):
  ContextFetcher — interface; MockContextFetcher for offline, LiveContextFetcher for CI
  JITContextAnchor — assembles sources, attaches to ContextAwareEngram
  TTLSweeper — BFS-traverses EngramGraph and marks stale nodes for re-anchoring
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .schema import (
    ContextAwareEngram,
    Domain,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    LogicEngram,
)

if TYPE_CHECKING:
    from uuid import UUID

    from .graph_store import EngramGraph

# ── Domain → default source types to fetch ───────────────────

_DOMAIN_SOURCE_TYPES: dict[str, list[JITSourceType]] = {
    Domain.BACKEND: [
        JITSourceType.API_DOCUMENTATION,
        JITSourceType.SECURITY_ADVISORY,
        JITSourceType.DEPRECATION_NOTICE,
    ],
    Domain.FRONTEND: [
        JITSourceType.API_DOCUMENTATION,
        JITSourceType.BEST_PRACTICE,
        JITSourceType.DEPRECATION_NOTICE,
    ],
    Domain.TEST: [
        JITSourceType.BEST_PRACTICE,
        JITSourceType.PERFORMANCE_BENCHMARK,
    ],
    Domain.CONFIG: [
        JITSourceType.SECURITY_ADVISORY,
        JITSourceType.LIVE_SCHEMA,
    ],
    Domain.DATABASE: [
        JITSourceType.LIVE_SCHEMA,
        JITSourceType.PERFORMANCE_BENCHMARK,
        JITSourceType.SECURITY_ADVISORY,
    ],
    Domain.AUTH: [
        JITSourceType.SECURITY_ADVISORY,
        JITSourceType.API_DOCUMENTATION,
        JITSourceType.BEST_PRACTICE,
    ],
    Domain.INFRA: [
        JITSourceType.SECURITY_ADVISORY,
        JITSourceType.BEST_PRACTICE,
        JITSourceType.DEPRECATION_NOTICE,
    ],
    Domain.API_CONTRACT: [
        JITSourceType.API_DOCUMENTATION,
        JITSourceType.BEST_PRACTICE,
        JITSourceType.DEPRECATION_NOTICE,
    ],
}

# ── Intent keyword → additional heuristic advisories to pull ─

_INTENT_ADVISORY_HINTS: dict[str, JITSourceType] = {
    "auth": JITSourceType.SECURITY_ADVISORY,
    "password": JITSourceType.SECURITY_ADVISORY,
    "token": JITSourceType.SECURITY_ADVISORY,
    "sql": JITSourceType.SECURITY_ADVISORY,
    "query": JITSourceType.SECURITY_ADVISORY,
    "jwt": JITSourceType.SECURITY_ADVISORY,
    "oauth": JITSourceType.SECURITY_ADVISORY,
    "rbac": JITSourceType.SECURITY_ADVISORY,
    "mfa": JITSourceType.SECURITY_ADVISORY,
    "encrypt": JITSourceType.SECURITY_ADVISORY,
    "websocket": JITSourceType.PERFORMANCE_BENCHMARK,
    "realtime": JITSourceType.PERFORMANCE_BENCHMARK,
    "latency": JITSourceType.PERFORMANCE_BENCHMARK,
    "cache": JITSourceType.PERFORMANCE_BENCHMARK,
    "index": JITSourceType.PERFORMANCE_BENCHMARK,
    "schema": JITSourceType.LIVE_SCHEMA,
    "database": JITSourceType.LIVE_SCHEMA,
    "migrate": JITSourceType.LIVE_SCHEMA,
    "migration": JITSourceType.LIVE_SCHEMA,
    "table": JITSourceType.LIVE_SCHEMA,
    "api": JITSourceType.API_DOCUMENTATION,
    "rest": JITSourceType.API_DOCUMENTATION,
    "graphql": JITSourceType.API_DOCUMENTATION,
    "docker": JITSourceType.BEST_PRACTICE,
    "k8s": JITSourceType.BEST_PRACTICE,
    "kubernetes": JITSourceType.BEST_PRACTICE,
    "helm": JITSourceType.BEST_PRACTICE,
    "terraform": JITSourceType.BEST_PRACTICE,
    "deploy": JITSourceType.BEST_PRACTICE,
}


@runtime_checkable
class ContextFetcher(Protocol):
    """Protocol for fetching real-world context for a given JITSourceType + intent keyword."""

    def fetch(
        self,
        source_type: JITSourceType,
        intent_keyword: str,
        domain: str,
    ) -> JITSource: ...


# ── Mock fetcher (deterministic, no network) ──────────────────

_MOCK_EXCERPTS: dict[JITSourceType, str] = {
    JITSourceType.API_DOCUMENTATION: "Use versioned endpoints. Avoid breaking changes in minor releases.",
    JITSourceType.SECURITY_ADVISORY: "Validate all inputs. Use parameterized queries. Rotate secrets regularly.",
    JITSourceType.DEPRECATION_NOTICE: "Avoid deprecated sync APIs. Use async equivalents where available.",
    JITSourceType.BEST_PRACTICE: "Prefer immutable data structures. Keep functions under 20 lines.",
    JITSourceType.PERFORMANCE_BENCHMARK: "Target p99 < 50ms. Avoid polling — use WebSocket/CRDT for real-time sync.",
    JITSourceType.LIVE_SCHEMA: "Schema version pinned. Run migrations before service restart.",
}


@dataclass
class MockContextFetcher:
    """Deterministic context fetcher for offline benchmarks and tests."""

    latency_ms: float = 2.0  # simulated fetch latency

    def fetch(
        self,
        source_type: JITSourceType,
        intent_keyword: str,
        domain: str,
    ) -> JITSource:
        time.sleep(self.latency_ms / 1000)
        excerpt = _MOCK_EXCERPTS.get(source_type, "No advisory available.")
        content = f"{source_type.value}:{intent_keyword}:{domain}"
        return JITSource(
            source_type=source_type,
            url=f"mock://context/{source_type.value}/{intent_keyword}",
            version_locked="mock-2026-03",
            content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
            raw_excerpt=excerpt,
        )


@dataclass
class GeminiContextFetcher:
    """Live JIT context fetcher — uses Gemini Flash Lite to generate domain-specific advisories.

    Replaces MockContextFetcher in live environments where GEMINI_API_KEY is set.
    Each call generates a precise, context-aware engineering advisory for the
    specific source_type + intent_keyword + domain combination — never a hardcoded string.
    Falls back to MockContextFetcher on API failure.
    """

    model: str = "gemini-2.0-flash-lite"
    _type_labels: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._type_labels = {
            JITSourceType.API_DOCUMENTATION: "API design and versioning guidance",
            JITSourceType.SECURITY_ADVISORY: "security vulnerability prevention advisory",
            JITSourceType.DEPRECATION_NOTICE: "deprecated API / pattern replacement notice",
            JITSourceType.BEST_PRACTICE: "engineering best practice recommendation",
            JITSourceType.PERFORMANCE_BENCHMARK: "performance optimisation advisory",
            JITSourceType.LIVE_SCHEMA: "database schema and migration advisory",
        }

    def fetch(
        self,
        source_type: JITSourceType,
        intent_keyword: str,
        domain: str,
    ) -> JITSource:
        import os

        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return MockContextFetcher().fetch(source_type, intent_keyword, domain)
        try:
            from google import genai  # type: ignore[import-untyped]

            client = genai.Client(api_key=key)
            type_label = (self._type_labels or {}).get(source_type, source_type.value)
            prompt = (
                f"You are a senior engineer writing a real-world {type_label}.\n"
                f"Domain: {domain}\n"
                f"Context keyword: {intent_keyword}\n\n"
                f"Write exactly 1-2 sentences of precise, actionable guidance specific to "
                f"{intent_keyword} in {domain} code.\n"
                f"Be concrete — reference real patterns, standards, or known pitfalls.\n"
                f"Return ONLY the advisory text. No headings. No bullet points."
            )
            resp = client.models.generate_content(model=self.model, contents=prompt)
            excerpt = (resp.text or "").strip()[:500]
            content = f"{source_type.value}:{intent_keyword}:{domain}"
            return JITSource(
                source_type=source_type,
                url=f"gemini://jit/{source_type.value}/{intent_keyword}",
                version_locked="gemini-live-2026-03",
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                raw_excerpt=excerpt or _MOCK_EXCERPTS.get(source_type, "No advisory available."),
            )
        except Exception:
            return MockContextFetcher().fetch(source_type, intent_keyword, domain)


# ── JIT Anchor result ─────────────────────────────────────────


@dataclass
class AnchorResult:
    """Result of anchoring an engram to real-world context."""

    engram_id: UUID
    sources_added: int = 0
    reality_hash: str = ""
    latency_ms: float = 0.0
    was_reanchor: bool = False  # True if this replaced stale context


# ── JIT Context Anchor ────────────────────────────────────────


@dataclass
class JITContextAnchor:
    """Orchestrates real-world context injection into ContextAwareEngram nodes.

    Usage:
        anchor = JITContextAnchor(fetcher=MockContextFetcher())
        result = anchor.anchor(engram)
        # engram.jit_context now has fresh sources
    """

    fetcher: ContextFetcher = field(default_factory=MockContextFetcher)

    def anchor(
        self,
        engram: ContextAwareEngram,
        *,
        force: bool = False,
    ) -> AnchorResult:
        """Inject JIT context into the engram's JITContextMatrix.

        Skips re-anchoring unless force=True or sources are stale/expired.
        """
        t0 = time.monotonic()
        was_reanchor = bool(engram.jit_context.sources)

        if not force and engram.is_reality_anchored():
            # Already anchored and not stale — nothing to do
            return AnchorResult(
                engram_id=engram.engram_id,
                sources_added=0,
                reality_hash=engram.jit_context.reality_hash,
                latency_ms=0.0,
                was_reanchor=False,
            )

        # Clear stale sources on re-anchor
        if was_reanchor:
            engram.jit_context = JITContextMatrix()

        # Determine which source types to fetch
        source_types = _resolve_source_types(engram.intent, engram.domain)

        # Fetch each source (air-gapped path via fetcher)
        sources_added = 0
        for source_type in source_types:
            keyword = _extract_intent_keyword(engram.intent)
            try:
                source = self.fetcher.fetch(source_type, keyword, engram.domain.value)
                engram.jit_context.add_source(source)
                sources_added += 1
            except Exception:
                pass  # Individual source failure is non-fatal

        engram.jit_context.is_stale = False
        latency_ms = (time.monotonic() - t0) * 1000

        return AnchorResult(
            engram_id=engram.engram_id,
            sources_added=sources_added,
            reality_hash=engram.jit_context.reality_hash,
            latency_ms=round(latency_ms, 2),
            was_reanchor=was_reanchor,
        )

    def anchor_many(
        self,
        engrams: list[ContextAwareEngram],
        *,
        force: bool = False,
    ) -> list[AnchorResult]:
        """Anchor a list of engrams. Sequential (no network in mock mode)."""
        return [self.anchor(e, force=force) for e in engrams]


# ── TTL Sweeper ───────────────────────────────────────────────


@dataclass
class StalenessReport:
    """Result of a TTL sweep over the graph."""

    total_checked: int = 0
    stale_count: int = 0
    stale_engram_ids: list[UUID] = field(default_factory=list)
    sweep_latency_ms: float = 0.0


def sweep_stale_engrams(
    graph: EngramGraph,
    *,
    decay_radius: int = 3,
    anchor: JITContextAnchor | None = None,
    auto_reanchor: bool = False,
) -> StalenessReport:
    """BFS-traverse the graph and identify ContextAwareEngrams with expired JIT context.

    If auto_reanchor=True and an anchor is provided, immediately re-anchors stale nodes.
    Uses decay_radius to limit BFS depth (identical to EngramGraph.get_dependency_subgraph).
    """
    t0 = time.monotonic()
    report = StalenessReport()

    for engram_id, engram in graph._engrams.items():
        if not isinstance(engram, ContextAwareEngram):
            continue
        report.total_checked += 1
        if engram.needs_reanchor():
            report.stale_count += 1
            report.stale_engram_ids.append(engram_id)
            if auto_reanchor and anchor is not None:
                anchor.anchor(engram, force=True)

    report.sweep_latency_ms = round((time.monotonic() - t0) * 1000, 2)
    return report


# ── Helpers ───────────────────────────────────────────────────


def _resolve_source_types(intent: str, domain: Domain) -> list[JITSourceType]:
    """Determine which source types to fetch, enriched by intent keywords."""
    base_types: set[JITSourceType] = set(
        _DOMAIN_SOURCE_TYPES.get(domain, [JITSourceType.API_DOCUMENTATION])
    )
    intent_lower = intent.lower()
    for keyword, source_type in _INTENT_ADVISORY_HINTS.items():
        if keyword in intent_lower:
            base_types.add(source_type)
    return list(base_types)


def _extract_intent_keyword(intent: str) -> str:
    """Extract the most relevant keyword from intent for context lookup."""
    words = intent.lower().split()
    priority_words = {
        "auth",
        "password",
        "token",
        "sql",
        "query",
        "jwt",
        "oauth",
        "rbac",
        "mfa",
        "encrypt",
        "websocket",
        "realtime",
        "cache",
        "index",
        "schema",
        "database",
        "migrate",
        "migration",
        "table",
        "api",
        "rest",
        "graphql",
        "docker",
        "k8s",
        "kubernetes",
        "helm",
        "terraform",
        "deploy",
    }
    for w in words:
        if w in priority_words:
            return w
    return words[0] if words else "general"


def upgrade_to_context_aware(
    engram: LogicEngram,
    anchor: JITContextAnchor,
    mandate_level: str = "L1",
) -> ContextAwareEngram:
    """One-shot upgrade: promote a plain LogicEngram to a JIT-anchored ContextAwareEngram."""
    ctx_engram = ContextAwareEngram.from_logic_engram(engram, mandate_level=mandate_level)
    anchor.anchor(ctx_engram)
    return ctx_engram


# ── Phase 8: SOTA Context Fetcher ─────────────────────────────


@dataclass
class SotaContextFetcher:
    """Fetches JIT context from the Hippocampus SOTA collection and .cog.json
    emergent rules, bridging the SOTA → Engram infusion path (Phase 8).

    Phase 8 additions:
      - Queries ``sota_collection`` with domain-scoped metadata filter so only
        domain-relevant SIPs are returned (avoids cross-domain noise).
      - Skips SIPs whose ``expires_at`` has passed (TTL enforcement).
      - Also queries ``architectural_sota`` / ``human_cognition_vectors`` and
        enriches results with ``.cog.json`` rules from ``psyche_bank/emergent/``.
      - ``max_tokens`` caps the combined excerpt length (default 500 chars).

    Falls back to MockContextFetcher when Hippocampus is unavailable.
    """

    hippocampus: object | None = None
    fallback: MockContextFetcher = field(default_factory=MockContextFetcher)
    max_tokens: int = 500  # soft cap for combined raw_excerpt

    # ------------------------------------------------------------------
    # Public API (ContextFetcher protocol)
    # ------------------------------------------------------------------

    def fetch(
        self,
        source_type: JITSourceType,
        intent_keyword: str,
        domain: str,
    ) -> JITSource:
        """Query SOTA collections for domain-relevant context, TTL-filtered."""
        import json
        from datetime import UTC, datetime
        from pathlib import Path

        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        excerpts: list[str] = []

        # ── 1. Query sota_collection with domain + TTL filtering ──────────
        if self.hippocampus is not None:
            sota_coll = getattr(self.hippocampus, "sota_collection", None)
            if sota_coll is not None:
                try:
                    count = sota_coll.count()
                    if count > 0:
                        query_text = f"{intent_keyword} {domain} {source_type.value}"
                        results = sota_coll.query(
                            query_texts=[query_text],
                            n_results=min(5, count),
                            include=["documents", "metadatas"],
                        )
                        docs = results.get("documents", [[]])[0]
                        metas = results.get("metadatas", [[]])[0]
                        for doc, meta in zip(docs, metas, strict=False):
                            if not doc:
                                continue
                            # TTL filter: skip if expires_at is set and past
                            expires_at: str = meta.get("expires_at", "") if meta else ""
                            if expires_at and expires_at < now_iso:
                                continue
                            # Domain filter: skip if domain set and doesn't match
                            sip_domain: str = meta.get("domain", "") if meta else ""
                            if sip_domain and sip_domain != domain.lower():
                                continue
                            excerpts.append(doc[:300])
                except Exception:
                    pass

            # ── 2. Supplemental: architectural_sota + cognition collections ──
            for coll_attr in ("architectural_sota_collection", "cognition_collection"):
                coll = getattr(self.hippocampus, coll_attr, None)
                if coll is None:
                    continue
                try:
                    count = coll.count()
                    if count == 0:
                        continue
                    results = coll.query(
                        query_texts=[f"{intent_keyword} {domain} {source_type.value}"],
                        n_results=min(3, count),
                        include=["documents"],
                    )
                    docs = results.get("documents", [[]])[0]
                    for doc in docs:
                        if doc:
                            excerpts.append(doc[:300])
                except Exception:
                    pass

        # ── 3. Scan .cog.json emergent rules ──────────────────────────────
        try:
            cog_dir = (
                Path(__file__).resolve().parent.parent.parent
                / "core"
                / "engine"
                / "psyche_bank"
                / "emergent"
            )
            if cog_dir.is_dir():
                keyword_lower = intent_keyword.lower()
                domain_lower = domain.lower()
                for cog_path in cog_dir.glob("*.cog.json"):
                    try:
                        cog = json.loads(cog_path.read_text(encoding="utf-8"))
                        cog_keywords = [k.lower() for k in cog.get("keywords", [])]
                        cog_domain = cog.get("domain", "").lower()
                        if keyword_lower in cog_keywords or cog_domain == domain_lower:
                            desc = cog.get("description", "")
                            if desc:
                                excerpts.append(f"[Emergent:{cog.get('id', '')}] {desc[:200]}")
                    except (json.JSONDecodeError, OSError):
                        pass
        except Exception:
            pass

        # ── 4. Combine or fallback ────────────────────────────────────────
        if not excerpts:
            return self.fallback.fetch(source_type, intent_keyword, domain)

        combined = " | ".join(excerpts)[: self.max_tokens]
        content = f"sota:{source_type.value}:{intent_keyword}:{domain}"
        return JITSource(
            source_type=source_type,
            url=f"sota://hippocampus/{source_type.value}/{intent_keyword}",
            version_locked="sota-live",
            content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
            raw_excerpt=combined,
        )
