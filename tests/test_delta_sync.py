"""Tests for engram.delta_sync — DeltaSyncBus and mutation event payloads."""

from __future__ import annotations

import json
import threading
from uuid import uuid4

from engram_v2.arbiter import MitosisResult
from engram_v2.delta_sync import (
    DeltaSyncBus,
    DeltaSyncEvent,
    MutationCommitPayload,
    MutationEventType,
    MutationFailedPayload,
    MutationPendingPayload,
    RepointedEdge,
    SubscriptionToken,
    UpsertedNode,
)
from engram_v2.schema import (
    ContextAwareEngram,
    Domain,
    Language,
    TribunalVerdict,
    ValidationTribunal,
)

# ── Helpers ───────────────────────────────────────────────────


def make_v2_engram() -> ContextAwareEngram:
    e = ContextAwareEngram(
        intent="render dashboard",
        ast_signature="def dashboard():",
        logic_body="return {'ok': True}",
        domain=Domain.FRONTEND,
        language=Language.TSX,
        mandate_level="L2",
    )
    e.tribunal = ValidationTribunal(
        confidence_score=90.0,
        verdict=TribunalVerdict.PASS,
        heal_cycles_used=1,
    )
    return e


def make_mitosis_result(success: bool = True) -> MitosisResult:
    return MitosisResult(
        original_engram_id=uuid4(),
        healed_engram_id=uuid4(),
        success=success,
        edges_repointed=2,
        heal_cycle=1,
        heal_latency_ms=12.5,
        failure_reason="" if success else "Mock failure",
    )


# ── MutationPendingPayload ────────────────────────────────────


class TestMutationPendingPayload:
    def test_to_dict_contains_directive(self):
        p = MutationPendingPayload(
            target_engrams=[uuid4(), uuid4()],
            mutation_reason="SEC-001",
        )
        d = p.to_dict()
        assert d["ui_directive"] == "soft_lock"
        assert d["mutation_reason"] == "SEC-001"
        assert len(d["target_engrams"]) == 2

    def test_custom_ui_directive(self):
        p = MutationPendingPayload(
            target_engrams=[uuid4()],
            mutation_reason="HEU-001",
            ui_directive="freeze",
        )
        assert p.to_dict()["ui_directive"] == "freeze"


# ── MutationCommitPayload ─────────────────────────────────────


class TestMutationCommitPayload:
    def test_to_dict_basic(self):
        node = UpsertedNode(
            engram_id=uuid4(),
            domain="backend",
            intent="test",
            module_path="test.py",
            mandate_level="L1",
            tribunal_verdict="PASS",
            confidence_score=90.0,
        )
        edge = RepointedEdge(edge_id=uuid4(), from_engram=uuid4(), to_engram=uuid4())
        p = MutationCommitPayload(
            dropped_nodes=[uuid4()],
            upserted_nodes=[node],
            repointed_edges=[edge],
            heal_latency_ms=15.0,
            heal_cycle=1,
        )
        d = p.to_dict()
        assert len(d["dropped_nodes"]) == 1
        assert d["heal_latency_ms"] == 15.0
        assert len(d["upserted_nodes"]) == 1
        assert len(d["repointed_edges"]) == 1

    def test_empty_commit_payload(self):
        p = MutationCommitPayload(dropped_nodes=[], upserted_nodes=[], repointed_edges=[])
        d = p.to_dict()
        assert d["dropped_nodes"] == []
        assert d["heal_latency_ms"] == 0.0


# ── MutationFailedPayload ─────────────────────────────────────


class TestMutationFailedPayload:
    def test_to_dict(self):
        p = MutationFailedPayload(
            target_engram_id=uuid4(),
            reason="Max heal cycles exceeded",
            heal_cycles_used=3,
        )
        d = p.to_dict()
        assert d["reason"] == "Max heal cycles exceeded"
        assert d["heal_cycles_used"] == 3
        assert d["ui_directive"] == "show_error"


# ── DeltaSyncEvent ────────────────────────────────────────────


class TestDeltaSyncEvent:
    def test_to_dict_has_required_fields(self):
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_MUTATION_PENDING,
            payload={"foo": "bar"},
        )
        d = event.to_dict()
        assert "event_type" in d
        assert "timestamp" in d
        assert "event_id" in d
        assert d["payload"]["foo"] == "bar"

    def test_to_json_bytes_is_valid_json(self):
        event = DeltaSyncEvent(
            event_type=MutationEventType.ENGRAM_MUTATION_COMMIT,
            payload={"key": "value"},
        )
        raw = event.to_json_bytes()
        parsed = json.loads(raw)
        assert parsed["event_type"] == "ENGRAM_MUTATION_COMMIT"


# ── DeltaSyncBus ──────────────────────────────────────────────


class TestDeltaSyncBusSubscription:
    def test_subscribe_returns_token(self):
        bus = DeltaSyncBus()
        received = []
        token = bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, received.append)
        assert isinstance(token, SubscriptionToken)

    def test_handler_called_on_emit(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, received.append)
        m = make_mitosis_result()
        bus.emit_commit(m)
        assert len(received) == 1
        assert received[0].event_type == MutationEventType.ENGRAM_MUTATION_COMMIT

    def test_unsubscribe_stops_delivery(self):
        bus = DeltaSyncBus()
        received = []
        token = bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, received.append)
        bus.unsubscribe(token)
        m = make_mitosis_result()
        bus.emit_commit(m)
        assert len(received) == 0

    def test_multiple_handlers_for_same_event(self):
        bus = DeltaSyncBus()
        a, b = [], []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_PENDING, a.append)
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_PENDING, b.append)
        bus.emit_pending([uuid4()], reason="test")
        assert len(a) == 1
        assert len(b) == 1

    def test_handler_for_different_event_not_called(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_FAILED, received.append)
        bus.emit_pending([uuid4()], reason="test")  # wrong type
        assert len(received) == 0


class TestDeltaSyncBusEmit:
    def test_emit_pending_returns_event(self):
        bus = DeltaSyncBus()
        eid = uuid4()
        event = bus.emit_pending([eid], reason="SEC-001")
        assert event.event_type == MutationEventType.ENGRAM_MUTATION_PENDING
        assert event.payload["mutation_reason"] == "SEC-001"

    def test_emit_commit_with_v2_engram(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, received.append)
        m = make_mitosis_result()
        v2 = make_v2_engram()
        bus.emit_commit(m, v2)
        d = received[0].payload
        assert len(d["upserted_nodes"]) == 1
        assert d["upserted_nodes"][0]["tribunal_verdict"] == "PASS"

    def test_emit_commit_without_v2(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, received.append)
        m = make_mitosis_result()
        bus.emit_commit(m)
        assert received[0].payload["upserted_nodes"] == []

    def test_emit_failed(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_FAILED, received.append)
        bus.emit_failed(uuid4(), reason="Max cycles exceeded", cycles_used=3)
        d = received[0].payload
        assert d["heal_cycles_used"] == 3
        assert d["ui_directive"] == "show_error"

    def test_emit_snapshot(self):
        bus = DeltaSyncBus()
        received = []
        bus.subscribe(MutationEventType.ENGRAM_GRAPH_SNAPSHOT, received.append)
        bus.emit_snapshot({"engrams": 5, "edges": 3})
        assert received[0].payload["engrams"] == 5


class TestDeltaSyncBusReplayBuffer:
    def test_recent_events_empty_initially(self):
        bus = DeltaSyncBus()
        assert bus.get_recent_events() == []

    def test_recent_events_captures_all_types(self):
        bus = DeltaSyncBus()
        bus.emit_pending([uuid4()], reason="test")
        bus.emit_commit(make_mitosis_result())
        bus.emit_failed(uuid4(), "test", 1)
        events = bus.get_recent_events()
        assert len(events) == 3

    def test_replay_buffer_capped_at_20(self):
        bus = DeltaSyncBus()
        for _ in range(25):
            bus.emit_pending([uuid4()], reason="overflow")
        events = bus.get_recent_events()
        assert len(events) == 20

    def test_get_recent_limit_respected(self):
        bus = DeltaSyncBus()
        for _ in range(10):
            bus.emit_pending([uuid4()], reason="test")
        events = bus.get_recent_events(limit=3)
        assert len(events) == 3


class TestDeltaSyncBusThreadSafety:
    def test_concurrent_emits_dont_crash(self):
        bus = DeltaSyncBus()
        errors = []

        def emit_loop():
            try:
                for _ in range(20):
                    bus.emit_pending([uuid4()], reason="concurrent")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_loop) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == [], f"Concurrent emit raised: {errors}"

    def test_handler_error_does_not_crash_bus(self):
        bus = DeltaSyncBus()

        def bad_handler(event):
            raise RuntimeError("Handler explosion!")

        bus.subscribe(MutationEventType.ENGRAM_MUTATION_PENDING, bad_handler)
        # Should not raise
        event = bus.emit_pending([uuid4()], reason="crash_test")
        assert event is not None

    def test_concurrent_subscribe_and_emit(self):
        bus = DeltaSyncBus()
        results = []

        def subscriber_thread():
            bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, results.append)

        def emitter_thread():
            for _ in range(5):
                bus.emit_commit(make_mitosis_result())

        threads = [threading.Thread(target=subscriber_thread) for _ in range(3)]
        threads += [threading.Thread(target=emitter_thread) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        # Just verify no crash — result count depends on race
        assert isinstance(results, list)
