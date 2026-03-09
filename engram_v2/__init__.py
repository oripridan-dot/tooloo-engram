"""
tooloo-engram V2 Public API.

Re-exports the core V2 components for external consumers.
"""

from .adversary import AdversaryResult, AdversaryValidator
from .arbiter import ArbiterHealer, MitosisResult, MockArbiterLLM
from .delta_sync import DeltaSyncBus, MutationEventType
from .graph_store import EngramGraph
from .jit_context import (
    JITContextAnchor,
    MockContextFetcher,
    SotaContextFetcher,
    sweep_stale_engrams,
    upgrade_to_context_aware,
)
from .schema import (
    ContextAwareEngram,
    Domain,
    EdgeType,
    GraphAwareness,
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
    "AdversaryResult",
    # Adversary
    "AdversaryValidator",
    # Arbiter
    "ArbiterHealer",
    # Schema
    "ContextAwareEngram",
    # Delta Sync
    "DeltaSyncBus",
    "Domain",
    "EdgeType",
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
    "MitosisResult",
    "MockArbiterLLM",
    "MockContextFetcher",
    "MutationEventType",
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
