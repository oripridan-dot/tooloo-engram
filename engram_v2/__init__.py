"""
tooloo-engram V2 Public API.

Re-exports the core V2 components for external consumers.
"""

from .adversary import AdversaryResult, AdversaryValidator
from .arbiter import ArbiterHealer, MitosisResult, MockArbiterLLM
from .cognitive_graph import CognitiveDualGraph, CognitiveGraph, SemanticCollisionResult
from .constitution import (
    ConstitutionalGate,
    GateOutcome,
    GateVerdict,
    LicenseGate,
    OWASPGate,
    SOTAGate,
)
from .delta_sync import DeltaSyncBus, MutationEventType
from .epigenetic_infusion import (
    CogJsonPayload,
    MemoryTier,
    SynapseCollisionEngine,
    VertexVectorBackend,
)
from .graph_store import EngramGraph
from .jit_context import (
    JITContextAnchor,
    MockContextFetcher,
    SotaContextFetcher,
    sweep_stale_engrams,
    upgrade_to_context_aware,
)
from .mandate_pipeline import (
    ForegroundPipeline,
    FramePhase,
    LLMBackend,
    MandateEnvelope,
    MandatePipeline,
    NarrativeFrame,
    ShadowWeaver,
)
from .persistence import DeltaCheckpoint, GraphPersistence
from .pr_materializer import (
    CompiledArtifact,
    GitHubBackend,
    GraphDiff,
    PRMaterializer,
    PRResult,
    diff_graphs,
)
from .schema import (
    ContextAwareEngram,
    Domain,
    EdgeType,
    GraphAwareness,
    IntentDomain,
    IntentEngram,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    Language,
    LogicEngram,
    SynapticEdge,
    TribunalVerdict,
    ValidationTribunal,
)
from .tribunal_orchestrator import (
    TribunalOrchestrator,
    TribunalRunResult,
)

__all__ = [
    # Adversary
    "AdversaryResult",
    "AdversaryValidator",
    # Arbiter
    "ArbiterHealer",
    "MitosisResult",
    "MockArbiterLLM",
    # Cognitive Graph (Layer 2)
    "CognitiveDualGraph",
    "CognitiveGraph",
    "SemanticCollisionResult",
    # Constitution / Gates
    "ConstitutionalGate",
    "GateOutcome",
    "GateVerdict",
    "LicenseGate",
    "OWASPGate",
    "SOTAGate",
    # Schema
    "ContextAwareEngram",
    # Delta Sync
    "DeltaSyncBus",
    "Domain",
    "EdgeType",
    # Epigenetic Infusion
    "CogJsonPayload",
    "MemoryTier",
    "SynapseCollisionEngine",
    "VertexVectorBackend",
    # Graph
    "EngramGraph",
    "GraphAwareness",
    # JIT
    "JITContextAnchor",
    "JITContextMatrix",
    "JITSource",
    "JITSourceType",
    "Language",
    "LogicEngram",
    # Mandate Pipeline
    "ForegroundPipeline",
    "FramePhase",
    "LLMBackend",
    "MandateEnvelope",
    "MandatePipeline",
    "NarrativeFrame",
    "ShadowWeaver",
    "MitosisResult",
    "MockArbiterLLM",
    "MockContextFetcher",
    "MutationEventType",
    # Persistence
    "DeltaCheckpoint",
    "GraphPersistence",
    # PR Materializer
    "CompiledArtifact",
    "GitHubBackend",
    "GraphDiff",
    "PRMaterializer",
    "PRResult",
    "diff_graphs",
    # Schema (new)
    "IntentDomain",
    "IntentEngram",
    "SotaContextFetcher",
    "SynapticEdge",
    # Tribunal
    "TribunalOrchestrator",
    "TribunalRunResult",
    "TribunalVerdict",
    "ValidationTribunal",
    "sweep_stale_engrams",
    "upgrade_to_context_aware",
]
