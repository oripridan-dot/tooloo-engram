"""
Delta Sync Bus — Real-time graph mutation event system for Engram V2.

Models the WebSocket delta-sync protocol described in the V2 architecture:
  ENGRAM_MUTATION_PENDING  → fired when Adversary returns FAIL (soft-lock UI)
  ENGRAM_MUTATION_COMMIT   → fired when Arbiter completes heal (surgical UI patch)
  ENGRAM_MUTATION_FAILED   → fired when Arbiter exhausts max_cycles (escalate)
  ENGRAM_GRAPH_SNAPSHOT    → periodic full state snapshot for reconnect hydration

In production this maps directly to /ws/forge WebSocket events.
In the Engram experiment it is an in-process thread-safe event bus that
feeds the benchmark harness and the V2 orchestrator.

Usage:
    bus = DeltaSyncBus()
    token = bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, my_handler)
    bus.emit_pending(engram_id, reason="jit_heuristic_heal")
    bus.emit_commit(mitosis_result)
    bus.unsubscribe(token)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from .arbiter import MitosisResult
    from .schema import ContextAwareEngram

# ── Event types ───────────────────────────────────────────────


class MutationEventType(StrEnum):
    ENGRAM_MUTATION_PENDING = "ENGRAM_MUTATION_PENDING"
    ENGRAM_MUTATION_COMMIT = "ENGRAM_MUTATION_COMMIT"
    ENGRAM_MUTATION_FAILED = "ENGRAM_MUTATION_FAILED"
    ENGRAM_GRAPH_SNAPSHOT = "ENGRAM_GRAPH_SNAPSHOT"


# ── Event payloads ────────────────────────────────────────────


@dataclass
class MutationPendingPayload:
    """Fired immediately when adversary fails — tells UI to soft-lock the component."""

    target_engrams: list[UUID]
    mutation_reason: str  # e.g. "jit_heuristic_heal" / "security_vulnerability"
    ui_directive: str = "soft_lock"  # "soft_lock" | "hide" | "freeze"

    def to_dict(self) -> dict:
        return {
            "target_engrams": [str(eid) for eid in self.target_engrams],
            "mutation_reason": self.mutation_reason,
            "ui_directive": self.ui_directive,
        }


@dataclass
class UpsertedNode:
    """A healed engram to insert into the UI's local graph."""

    engram_id: UUID
    domain: str
    intent: str
    module_path: str
    mandate_level: str
    tribunal_verdict: str
    confidence_score: float

    def to_dict(self) -> dict:
        return {
            "engram_id": str(self.engram_id),
            "domain": self.domain,
            "intent": self.intent,
            "module_path": self.module_path,
            "mandate_level": self.mandate_level,
            "tribunal_verdict": self.tribunal_verdict,
            "confidence_score": self.confidence_score,
        }


@dataclass
class RepointedEdge:
    """An edge that was repointed from v1 → v2 during Mitosis."""

    edge_id: UUID
    from_engram: UUID
    to_engram: UUID  # points to healed v2

    def to_dict(self) -> dict:
        return {
            "edge_id": str(self.edge_id),
            "from": str(self.from_engram),
            "to": str(self.to_engram),
        }


@dataclass
class MutationCommitPayload:
    """Surgical delta: exactly what the UI needs to hot-swap the broken node."""

    dropped_nodes: list[UUID]
    upserted_nodes: list[UpsertedNode]
    repointed_edges: list[RepointedEdge]
    heal_latency_ms: float = 0.0
    heal_cycle: int = 1

    def to_dict(self) -> dict:
        return {
            "dropped_nodes": [str(nid) for nid in self.dropped_nodes],
            "upserted_nodes": [n.to_dict() for n in self.upserted_nodes],
            "repointed_edges": [e.to_dict() for e in self.repointed_edges],
            "heal_latency_ms": round(self.heal_latency_ms, 2),
            "heal_cycle": self.heal_cycle,
        }


@dataclass
class MutationFailedPayload:
    """Fired when all heal cycles are exhausted — instructs UI to escalate."""

    target_engram_id: UUID
    reason: str
    heal_cycles_used: int
    ui_directive: str = "show_error"

    def to_dict(self) -> dict:
        return {
            "target_engram_id": str(self.target_engram_id),
            "reason": self.reason,
            "heal_cycles_used": self.heal_cycles_used,
            "ui_directive": self.ui_directive,
        }


# ── The event envelope ────────────────────────────────────────


@dataclass
class DeltaSyncEvent:
    """Complete WebSocket event envelope — mirrors the NarrativeFrame pattern."""

    event_type: MutationEventType
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_id: UUID = field(default_factory=uuid4)
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "event_id": str(self.event_id),
            "payload": self.payload,
        }

    def to_json_bytes(self) -> bytes:
        import json

        return json.dumps(self.to_dict()).encode()


# ── Subscriber token ──────────────────────────────────────────


@dataclass
class SubscriptionToken:
    token_id: UUID = field(default_factory=uuid4)
    event_type: MutationEventType = MutationEventType.ENGRAM_MUTATION_COMMIT


# ── Delta Sync Bus ────────────────────────────────────────────


class DeltaSyncBus:
    """Thread-safe in-process event bus for graph mutation events.

    Subscribers register handlers per event type. Events are dispatched
    synchronously in the order received (consistent with the benchmark harness).
    In production, replace dispatch with WebSocket fan-out.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[MutationEventType, dict[UUID, Callable[[DeltaSyncEvent], None]]] = {
            event_type: {} for event_type in MutationEventType
        }
        self._event_log: list[DeltaSyncEvent] = []  # last-20 replay buffer
        self._log_max = 20

    # ── Subscription management ───────────────────────────────

    def subscribe(
        self,
        event_type: MutationEventType,
        handler: Callable[[DeltaSyncEvent], None],
    ) -> SubscriptionToken:
        """Register a handler for the given event type. Returns a token for unsubscribe."""
        token = SubscriptionToken(event_type=event_type)
        with self._lock:
            self._handlers[event_type][token.token_id] = handler
        return token

    def unsubscribe(self, token: SubscriptionToken) -> None:
        with self._lock:
            self._handlers[token.event_type].pop(token.token_id, None)

    def get_recent_events(self, limit: int = 20) -> list[DeltaSyncEvent]:
        """Return the last N events (for reconnect hydration)."""
        with self._lock:
            return list(self._event_log[-limit:])

    # ── Emit helpers ──────────────────────────────────────────

    def emit_pending(
        self,
        engram_ids: list[UUID],
        reason: str,
        ui_directive: str = "soft_lock",
    ) -> DeltaSyncEvent:
        """Fire ENGRAM_MUTATION_PENDING — adversary failed, UI should soft-lock."""
        payload = MutationPendingPayload(
            target_engrams=engram_ids,
            mutation_reason=reason,
            ui_directive=ui_directive,
        )
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_MUTATION_PENDING,
            payload=payload.to_dict(),
        )
        self._dispatch(event)
        return event

    def emit_commit(
        self, mitosis_result: MitosisResult, v2_engram: ContextAwareEngram | None = None
    ) -> DeltaSyncEvent:
        """Fire ENGRAM_MUTATION_COMMIT — Arbiter healed successfully, UI should hot-swap."""
        upserted: list[UpsertedNode] = []
        if v2_engram is not None:
            upserted.append(
                UpsertedNode(
                    engram_id=v2_engram.engram_id,
                    domain=v2_engram.domain.value,
                    intent=v2_engram.intent,
                    module_path=v2_engram.module_path,
                    mandate_level=v2_engram.mandate_level,
                    tribunal_verdict=v2_engram.tribunal.verdict.value,
                    confidence_score=v2_engram.tribunal.confidence_score,
                )
            )
        # Reconstruct repointed edges from the mitosis result
        # (arbiter already recorded edges_repointed count — emit compact summary)
        payload = MutationCommitPayload(
            dropped_nodes=[mitosis_result.original_engram_id],
            upserted_nodes=upserted,
            repointed_edges=[],  # full edge list available via graph snapshot
            heal_latency_ms=mitosis_result.heal_latency_ms,
            heal_cycle=mitosis_result.heal_cycle,
        )
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_MUTATION_COMMIT,
            payload=payload.to_dict(),
        )
        self._dispatch(event)
        return event

    def emit_failed(
        self,
        engram_id: UUID,
        reason: str,
        cycles_used: int,
    ) -> DeltaSyncEvent:
        """Fire ENGRAM_MUTATION_FAILED — all heal cycles exhausted."""
        payload = MutationFailedPayload(
            target_engram_id=engram_id,
            reason=reason,
            heal_cycles_used=cycles_used,
        )
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_MUTATION_FAILED,
            payload=payload.to_dict(),
        )
        self._dispatch(event)
        return event

    def emit_snapshot(self, graph_summary: dict) -> DeltaSyncEvent:
        """Fire ENGRAM_GRAPH_SNAPSHOT — full state for reconnect hydration."""
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_GRAPH_SNAPSHOT,
            payload=graph_summary,
        )
        self._dispatch(event)
        return event

    # ── Internal dispatch ─────────────────────────────────────

    def _dispatch(self, event: DeltaSyncEvent) -> None:
        with self._lock:
            handlers = dict(self._handlers[event.event_type])
            self._event_log.append(event)
            if len(self._event_log) > self._log_max:
                self._event_log = self._event_log[-self._log_max :]

        # Dispatch outside the lock to avoid deadlocks
        for handler in handlers.values():
            try:
                handler(event)
            except Exception:
                pass  # Handler errors are non-fatal — bus keeps running
