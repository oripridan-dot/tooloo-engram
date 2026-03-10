"""
Engram Schema — The atomic building blocks of an AI-native codebase.

LogicEngram replaces "files/functions".
SynapticEdge replaces "import statements".
ContextTensor replaces CIPEnvelopes for graph-native context injection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class EdgeType(StrEnum):
    # ── Execution Graph edges (Layer 1 — The What) ────────────
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    TESTS = "tests"
    RESOLVES = "resolves"   # source fixes/resolves a bug in target
    CAUSES = "causes"       # source triggers/causes behaviour in target
    # ── Cognitive Graph edges (Layer 2 — The Why & How) ───────
    IMPLEMENTED_BY = "implemented_by"       # IntentEngram → LogicEngram
    FREQUENTLY_USED_WITH = "frequently_used_with"  # IntentEngram ↔ IntentEngram
    ALTERNATIVE_TO = "alternative_to"       # IntentEngram ↔ IntentEngram
    SPECIALISES = "specialises"             # narrower IntentEngram → broader IntentEngram
    DEPRECATED_BY = "deprecated_by"        # superseded IntentEngram → replacement
    SECURITY_GOVERNS = "security_governs"  # security IntentEngram → domain IntentEngrams


class Domain(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    TEST = "test"
    CONFIG = "config"
    DATABASE = "database"
    AUTH = "auth"
    INFRA = "infra"
    API_CONTRACT = "api_contract"


class Language(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TSX = "tsx"


@dataclass
class LogicEngram:
    """Atomic unit of logic — replaces a file or function."""

    intent: str  # NL description: "Authenticate user via JWT"
    ast_signature: str  # function/class signature: "def authenticate(token: str) -> User"
    logic_body: str  # raw code body (no comments/docstrings)
    language: Language = Language.PYTHON
    domain: Domain = Domain.BACKEND
    engram_id: UUID = field(default_factory=uuid4)
    parent_engram_id: UUID | None = None  # sub-function decomposition
    module_path: str = ""  # target filepath when compiled (e.g. "models/todo.py")
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def token_count(self) -> int:
        """Estimated token count (len/4 approximation)."""
        return len(self.logic_body) // 4

    @property
    def checksum(self) -> str:
        """SHA-256 integrity hash of logic_body."""
        return hashlib.sha256(self.logic_body.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "engram_id": str(self.engram_id),
            "intent": self.intent,
            "ast_signature": self.ast_signature,
            "logic_body": self.logic_body,
            "language": self.language.value,
            "domain": self.domain.value,
            "module_path": self.module_path,
            "parent_engram_id": str(self.parent_engram_id) if self.parent_engram_id else None,
            "token_count": self.token_count,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> LogicEngram:
        return cls(
            engram_id=UUID(data["engram_id"]),
            intent=data["intent"],
            ast_signature=data["ast_signature"],
            logic_body=data["logic_body"],
            language=Language(data["language"]),
            domain=Domain(data["domain"]),
            module_path=data.get("module_path", ""),
            parent_engram_id=UUID(data["parent_engram_id"])
            if data.get("parent_engram_id")
            else None,
        )


@dataclass
class SynapticEdge:
    """Directed dependency link — replaces import statements."""

    source_id: UUID  # the Engram that depends
    target_id: UUID  # the Engram depended upon
    edge_type: EdgeType = EdgeType.IMPORTS
    weight: float = 1.0  # coupling strength 0.0-1.0
    verified: bool = False  # True if both endpoints exist
    edge_id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict:
        return {
            "edge_id": str(self.edge_id),
            "source_id": str(self.source_id),
            "target_id": str(self.target_id),
            "edge_type": self.edge_type.value,
            "weight": self.weight,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SynapticEdge:
        return cls(
            edge_id=UUID(data["edge_id"]),
            source_id=UUID(data["source_id"]),
            target_id=UUID(data["target_id"]),
            edge_type=EdgeType(data["edge_type"]),
            weight=data.get("weight", 1.0),
            verified=data.get("verified", False),
        )


@dataclass
class ContextTensor:
    """Multidimensional context payload assembled from graph subgraph."""

    target_engrams: list[UUID]
    dependency_subgraph_json: str  # serialized subgraph (only relevant nodes)
    intent_chain: list[str]  # NL intent hierarchy
    token_budget: int = 8000
    assembled_prompt: str = ""
    tensor_id: UUID = field(default_factory=uuid4)

    @property
    def token_count(self) -> int:
        return len(self.assembled_prompt) // 4

    def to_dict(self) -> dict:
        return {
            "tensor_id": str(self.tensor_id),
            "target_engrams": [str(eid) for eid in self.target_engrams],
            "dependency_subgraph_json": self.dependency_subgraph_json,
            "intent_chain": self.intent_chain,
            "token_budget": self.token_budget,
            "assembled_prompt": self.assembled_prompt,
            "token_count": self.token_count,
        }


# ── V2: JIT Context Matrix ────────────────────────────────────


class JITSourceType(StrEnum):
    API_DOCUMENTATION = "api_documentation"
    PERFORMANCE_BENCHMARK = "performance_benchmark"
    SECURITY_ADVISORY = "security_advisory"
    LIVE_SCHEMA = "live_schema"
    BEST_PRACTICE = "best_practice"
    DEPRECATION_NOTICE = "deprecation_notice"


@dataclass
class JITSource:
    """A single real-world data source anchored into an engram's context."""

    source_type: JITSourceType
    url: str = ""
    version_locked: str = ""
    content_hash: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_hours: int = 72
    raw_excerpt: str = ""  # key facts extracted from the source (token-budgeted)

    @property
    def is_expired(self) -> bool:
        age_hours = (datetime.now(UTC) - self.fetched_at).total_seconds() / 3600
        return age_hours > self.ttl_hours

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type.value,
            "url": self.url,
            "version_locked": self.version_locked,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at.isoformat(),
            "ttl_hours": self.ttl_hours,
            "is_expired": self.is_expired,
            "raw_excerpt": self.raw_excerpt[:500],  # cap excerpt size
        }

    @classmethod
    def from_dict(cls, data: dict) -> JITSource:
        return cls(
            source_type=JITSourceType(data["source_type"]),
            url=data.get("url", ""),
            version_locked=data.get("version_locked", ""),
            content_hash=data.get("content_hash", ""),
            fetched_at=datetime.fromisoformat(data["fetched_at"])
            if data.get("fetched_at")
            else datetime.now(UTC),
            ttl_hours=data.get("ttl_hours", 72),
            raw_excerpt=data.get("raw_excerpt", ""),
        )


@dataclass
class JITContextMatrix:
    """The reality anchor — real-world facts locked into an engram node."""

    last_anchored: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_hours: int = 72
    sources: list[JITSource] = field(default_factory=list)
    reality_hash: str = ""  # SHA-256 of all source content_hashes combined
    is_stale: bool = False

    def __post_init__(self) -> None:
        if not self.reality_hash and self.sources:
            self._recompute_hash()

    def _recompute_hash(self) -> None:
        combined = "".join(s.content_hash for s in self.sources)
        self.reality_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]

    @property
    def any_expired(self) -> bool:
        return any(s.is_expired for s in self.sources)

    def add_source(self, source: JITSource) -> None:
        self.sources.append(source)
        self.last_anchored = datetime.now(UTC)
        self._recompute_hash()

    def to_dict(self) -> dict:
        return {
            "last_anchored": self.last_anchored.isoformat(),
            "ttl_hours": self.ttl_hours,
            "sources": [s.to_dict() for s in self.sources],
            "reality_hash": self.reality_hash,
            "is_stale": self.is_stale or self.any_expired,
            "source_count": len(self.sources),
        }

    @classmethod
    def from_dict(cls, data: dict) -> JITContextMatrix:
        matrix = cls(
            last_anchored=datetime.fromisoformat(data["last_anchored"])
            if data.get("last_anchored")
            else datetime.now(UTC),
            ttl_hours=data.get("ttl_hours", 72),
            reality_hash=data.get("reality_hash", ""),
            is_stale=data.get("is_stale", False),
        )
        matrix.sources = [JITSource.from_dict(s) for s in data.get("sources", [])]
        return matrix


# ── V2: Validation Tribunal ───────────────────────────────────


class TribunalVerdict(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class CrossCheckResults:
    """Per-dimension pass/fail for adversary validation."""

    context_conflict: bool = False  # generated code conflicts with JIT context
    deprecation_detected: bool = False  # uses deprecated API/pattern
    security_vulnerability: bool = False  # OWASP-class vulnerability
    heuristic_violation: bool = False  # domain-specific heuristic failed
    performance_violation: bool = False  # violates performance constraints

    @property
    def any_failed(self) -> bool:
        return any(
            [
                self.context_conflict,
                self.deprecation_detected,
                self.security_vulnerability,
                self.heuristic_violation,
                self.performance_violation,
            ]
        )

    def to_dict(self) -> dict:
        return {
            "context_conflict": self.context_conflict,
            "deprecation_detected": self.deprecation_detected,
            "security_vulnerability": self.security_vulnerability,
            "heuristic_violation": self.heuristic_violation,
            "performance_violation": self.performance_violation,
            "any_failed": self.any_failed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrossCheckResults:
        return cls(
            context_conflict=data.get("context_conflict", False),
            deprecation_detected=data.get("deprecation_detected", False),
            security_vulnerability=data.get("security_vulnerability", False),
            heuristic_violation=data.get("heuristic_violation", False),
            performance_violation=data.get("performance_violation", False),
        )


@dataclass
class ValidationTribunal:
    """Three-model validation tribunal: Scout → Adversary → Arbiter."""

    scout_model: str = "fast"
    adversary_model: str = "flash"
    arbiter_model: str = "pro"
    cross_check_results: CrossCheckResults = field(default_factory=CrossCheckResults)
    confidence_score: float = 0.0
    verdict: TribunalVerdict = TribunalVerdict.PENDING
    heal_cycles_used: int = 0
    fatal_error_log: str = ""  # populated on FAIL; contains the exact failing snippet

    def to_dict(self) -> dict:
        return {
            "scout_model": self.scout_model,
            "adversary_model": self.adversary_model,
            "arbiter_model": self.arbiter_model,
            "cross_check_results": self.cross_check_results.to_dict(),
            "confidence_score": self.confidence_score,
            "verdict": self.verdict.value,
            "heal_cycles_used": self.heal_cycles_used,
            "fatal_error_log": self.fatal_error_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ValidationTribunal:
        return cls(
            scout_model=data.get("scout_model", "fast"),
            adversary_model=data.get("adversary_model", "flash"),
            arbiter_model=data.get("arbiter_model", "pro"),
            cross_check_results=CrossCheckResults.from_dict(data.get("cross_check_results", {})),
            confidence_score=data.get("confidence_score", 0.0),
            verdict=TribunalVerdict(data.get("verdict", "PENDING")),
            heal_cycles_used=data.get("heal_cycles_used", 0),
            fatal_error_log=data.get("fatal_error_log", ""),
        )


# ── V2: Graph Awareness ───────────────────────────────────────


@dataclass
class GraphAwareness:
    """Macro-graph awareness for blast-radius calculation."""

    blast_radius: int = 2
    dependent_edge_ids: list[UUID] = field(default_factory=list)
    macro_state_hash: str = ""  # fingerprint of the graph at anchor time
    last_blast_check: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "blast_radius": self.blast_radius,
            "dependent_edge_ids": [str(eid) for eid in self.dependent_edge_ids],
            "macro_state_hash": self.macro_state_hash,
            "last_blast_check": self.last_blast_check.isoformat()
            if self.last_blast_check
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GraphAwareness:
        return cls(
            blast_radius=data.get("blast_radius", 2),
            dependent_edge_ids=[UUID(eid) for eid in data.get("dependent_edge_ids", [])],
            macro_state_hash=data.get("macro_state_hash", ""),
            last_blast_check=datetime.fromisoformat(data["last_blast_check"])
            if data.get("last_blast_check")
            else None,
        )


# ── V2: ContextAwareEngram ────────────────────────────────────


@dataclass
class ContextAwareEngram(LogicEngram):
    """V2 Engram — LogicEngram augmented with JIT context, tribunal and graph awareness.

    Replaces bare LogicEngram in the V2 pipeline. Backward-compatible:
    all LogicEngram logic_body / signature / intent operations still work.
    """

    jit_context: JITContextMatrix = field(default_factory=JITContextMatrix)
    tribunal: ValidationTribunal = field(default_factory=ValidationTribunal)
    graph_awareness: GraphAwareness = field(default_factory=GraphAwareness)
    mandate_level: str = "L1"  # L1 / L2 / L3 — scales tribunal strictness

    def is_reality_anchored(self) -> bool:
        """True if this engram has at least one non-expired JIT source."""
        return bool(self.jit_context.sources) and not self.jit_context.any_expired

    def needs_reanchor(self) -> bool:
        """True if the JIT matrix is stale or has expired sources."""
        return self.jit_context.is_stale or self.jit_context.any_expired

    def to_dict(self) -> dict:  # type: ignore[override]
        base = super().to_dict()
        base.update(
            {
                "jit_context": self.jit_context.to_dict(),
                "tribunal": self.tribunal.to_dict(),
                "graph_awareness": self.graph_awareness.to_dict(),
                "mandate_level": self.mandate_level,
                "is_reality_anchored": self.is_reality_anchored(),
            }
        )
        return base

    @classmethod
    def from_dict(cls, data: dict) -> ContextAwareEngram:  # type: ignore[override]
        base_engram = LogicEngram.from_dict(data)
        return cls(
            engram_id=base_engram.engram_id,
            intent=base_engram.intent,
            ast_signature=base_engram.ast_signature,
            logic_body=base_engram.logic_body,
            language=base_engram.language,
            domain=base_engram.domain,
            module_path=base_engram.module_path,
            parent_engram_id=base_engram.parent_engram_id,
            jit_context=JITContextMatrix.from_dict(data.get("jit_context", {})),
            tribunal=ValidationTribunal.from_dict(data.get("tribunal", {})),
            graph_awareness=GraphAwareness.from_dict(data.get("graph_awareness", {})),
            mandate_level=data.get("mandate_level", "L1"),
        )

    @classmethod
    def from_logic_engram(
        cls, engram: LogicEngram, mandate_level: str = "L1"
    ) -> ContextAwareEngram:
        """Upgrade a plain LogicEngram to a ContextAwareEngram."""
        return cls(
            engram_id=engram.engram_id,
            intent=engram.intent,
            ast_signature=engram.ast_signature,
            logic_body=engram.logic_body,
            language=engram.language,
            domain=engram.domain,
            module_path=engram.module_path,
            parent_engram_id=engram.parent_engram_id,
            created_at=engram.created_at,
            mandate_level=mandate_level,
        )


# ════════════════════════════════════════════════════════════════
# Layer 2 — The Cognitive Graph  (The Why & How)
#
# IntentEngram stores the *meaning* behind code, not the code itself.
# It lives in the Cognitive Graph, linked to the Execution Graph via
# semantic edges (IMPLEMENTED_BY, FREQUENTLY_USED_WITH, ALTERNATIVE_TO …).
#
# This transforms the repository from a library of code into a network
# of pure logic and intention — the engine's "mental map".
# ════════════════════════════════════════════════════════════════


class IntentDomain(StrEnum):
    """
    Cross-cutting conceptual domains for the Cognitive Graph.

    Deliberately broader than Domain (execution) — one IntentEngram may
    span multiple execution domains.
    """
    NETWORKING = "networking"
    UI_REACTIVITY = "ui_reactivity"
    SECURITY = "security"
    DATA_PERSISTENCE = "data_persistence"
    AUTHENTICATION = "authentication"
    REAL_TIME_SYNC = "real_time_sync"
    CHAT_INTERACTION = "chat_interaction"
    CODE_GENERATION = "code_generation"
    GRAPH_REASONING = "graph_reasoning"
    PERFORMANCE = "performance"
    OBSERVABILITY = "observability"
    ORCHESTRATION = "orchestration"
    GENERAL = "general"


@dataclass
class IntentEngram:
    """
    Layer-2 abstract node — stores *why* and *how*, never code.

    An IntentEngram captures a conceptual pattern (e.g. "Real-Time
    Bidirectional Sync") and connects to concrete LogicEngrams via
    IMPLEMENTED_BY edges and to sibling concepts via FREQUENTLY_USED_WITH
    or ALTERNATIVE_TO edges.

    This is the node type stored in the Cognitive Graph layer of
    CognitiveDualGraph.  It is also the primary payload in .cog.json
    files written to tooloo-memory.

    Key design invariant: `logic_body` is ALWAYS empty.  Any code
    belongs in a LogicEngram on Layer 1.
    """

    # Semantic identity
    intent_id: UUID = field(default_factory=uuid4)
    concept_label: str = ""                 # "Real-Time Bidirectional Sync"
    core_meaning: str = ""                  # 1-3 sentence dense definition
    domains: list[IntentDomain] = field(default_factory=list)

    # Relational index (populated by CognitiveDualGraph)
    known_implementations: list[UUID] = field(default_factory=list)   # LogicEngram IDs
    common_partners: list[UUID] = field(default_factory=list)          # IntentEngram IDs
    alternatives: list[UUID] = field(default_factory=list)             # IntentEngram IDs

    # Situational context
    common_scenarios: list[str] = field(default_factory=list)
    security_posture: str = ""              # security note (OWASP-aware)
    performance_notes: str = ""            # known bottlenecks / trade-offs
    deprecation_note: str = ""             # empty unless superseded

    # Provenance
    confidence: float = 1.0
    source_url: str = ""
    version_locked: str = ""               # e.g. "react>=18, websockets>=12"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_deprecated(self) -> bool:
        return bool(self.deprecation_note)

    @property
    def checksum(self) -> str:
        combined = self.concept_label + self.core_meaning + self.security_posture
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "intent_id": str(self.intent_id),
            "type": "IntentEngram",
            "concept_label": self.concept_label,
            "core_meaning": self.core_meaning,
            "domains": [d.value for d in self.domains],
            "known_implementations": [str(uid) for uid in self.known_implementations],
            "common_partners": [str(uid) for uid in self.common_partners],
            "alternatives": [str(uid) for uid in self.alternatives],
            "common_scenarios": self.common_scenarios,
            "security_posture": self.security_posture,
            "performance_notes": self.performance_notes,
            "deprecation_note": self.deprecation_note,
            "confidence": self.confidence,
            "source_url": self.source_url,
            "version_locked": self.version_locked,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntentEngram":
        node = cls(
            intent_id=UUID(data["intent_id"]) if data.get("intent_id") else uuid4(),
            concept_label=data.get("concept_label", ""),
            core_meaning=data.get("core_meaning", ""),
            domains=[IntentDomain(d) for d in data.get("domains", [])
                     if d in IntentDomain._value2member_map_],
            known_implementations=[UUID(u) for u in data.get("known_implementations", [])],
            common_partners=[UUID(u) for u in data.get("common_partners", [])],
            alternatives=[UUID(u) for u in data.get("alternatives", [])],
            common_scenarios=data.get("common_scenarios", []),
            security_posture=data.get("security_posture", ""),
            performance_notes=data.get("performance_notes", ""),
            deprecation_note=data.get("deprecation_note", ""),
            confidence=data.get("confidence", 1.0),
            source_url=data.get("source_url", ""),
            version_locked=data.get("version_locked", ""),
        )
        if ts := data.get("created_at"):
            node.created_at = datetime.fromisoformat(ts)
        if ts := data.get("updated_at"):
            node.updated_at = datetime.fromisoformat(ts)
        return node
