"""Tests for V2 schema extensions: JITSource, JITContextMatrix, ValidationTribunal,
CrossCheckResults, GraphAwareness, ContextAwareEngram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from engram_v2.schema import (
    ContextAwareEngram,
    CrossCheckResults,
    Domain,
    GraphAwareness,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    Language,
    LogicEngram,
    TribunalVerdict,
    ValidationTribunal,
)

# ── JITSource ─────────────────────────────────────────────────


class TestJITSource:
    def test_is_not_expired_when_fresh(self):
        src = JITSource(source_type=JITSourceType.API_DOCUMENTATION, ttl_hours=72)
        assert not src.is_expired

    def test_is_expired_when_old(self):
        old_time = datetime.now(UTC) - timedelta(hours=100)
        src = JITSource(
            source_type=JITSourceType.SECURITY_ADVISORY,
            ttl_hours=72,
            fetched_at=old_time,
        )
        assert src.is_expired

    def test_to_dict_contains_type(self):
        src = JITSource(source_type=JITSourceType.BEST_PRACTICE, raw_excerpt="Use PEP-8")
        d = src.to_dict()
        assert d["source_type"] == "best_practice"
        assert d["raw_excerpt"] == "Use PEP-8"

    def test_to_dict_truncates_excerpt_at_500_chars(self):
        long_excerpt = "x" * 600
        src = JITSource(source_type=JITSourceType.BEST_PRACTICE, raw_excerpt=long_excerpt)
        d = src.to_dict()
        assert len(d["raw_excerpt"]) <= 500

    def test_roundtrip_from_dict(self):
        src = JITSource(
            source_type=JITSourceType.LIVE_SCHEMA,
            url="https://api.example.com/schema",
            version_locked="v1.2",
            content_hash="abc123",
            raw_excerpt="table: users",
            ttl_hours=24,
        )
        d = src.to_dict()
        restored = JITSource.from_dict(d)
        assert restored.source_type == JITSourceType.LIVE_SCHEMA
        assert restored.version_locked == "v1.2"
        assert restored.ttl_hours == 24


# ── JITContextMatrix ──────────────────────────────────────────


class TestJITContextMatrix:
    def test_empty_matrix_not_any_expired(self):
        matrix = JITContextMatrix()
        assert not matrix.any_expired

    def test_add_source_increases_count(self):
        matrix = JITContextMatrix()
        src = JITSource(source_type=JITSourceType.API_DOCUMENTATION)
        matrix.add_source(src)
        assert len(matrix.sources) == 1

    def test_add_source_updates_reality_hash(self):
        matrix = JITContextMatrix()
        old_hash = matrix.reality_hash
        src = JITSource(source_type=JITSourceType.API_DOCUMENTATION, content_hash="abc123")
        matrix.add_source(src)
        assert matrix.reality_hash != old_hash

    def test_any_expired_true_when_expired_source(self):
        matrix = JITContextMatrix()
        expired = JITSource(
            source_type=JITSourceType.SECURITY_ADVISORY,
            ttl_hours=1,
            fetched_at=datetime.now(UTC) - timedelta(hours=24),
        )
        matrix.add_source(expired)
        assert matrix.any_expired

    def test_to_dict_has_source_count(self):
        matrix = JITContextMatrix()
        matrix.add_source(JITSource(source_type=JITSourceType.BEST_PRACTICE))
        matrix.add_source(JITSource(source_type=JITSourceType.LIVE_SCHEMA))
        d = matrix.to_dict()
        assert d["source_count"] == 2

    def test_roundtrip_empty(self):
        matrix = JITContextMatrix()
        d = matrix.to_dict()
        restored = JITContextMatrix.from_dict(d)
        assert len(restored.sources) == 0

    def test_roundtrip_with_sources(self):
        matrix = JITContextMatrix()
        matrix.add_source(
            JITSource(
                source_type=JITSourceType.PERFORMANCE_BENCHMARK,
                raw_excerpt="p99 < 100ms",
                content_hash="deadbeef",
            )
        )
        d = matrix.to_dict()
        restored = JITContextMatrix.from_dict(d)
        assert len(restored.sources) == 1
        assert restored.sources[0].source_type == JITSourceType.PERFORMANCE_BENCHMARK


# ── CrossCheckResults ─────────────────────────────────────────


class TestCrossCheckResults:
    def test_all_false_by_default(self):
        r = CrossCheckResults()
        assert not r.any_failed

    def test_security_vulnerability_triggers_any_failed(self):
        r = CrossCheckResults(security_vulnerability=True)
        assert r.any_failed

    def test_deprecation_triggers_any_failed(self):
        r = CrossCheckResults(deprecation_detected=True)
        assert r.any_failed

    def test_heuristic_triggers_any_failed(self):
        r = CrossCheckResults(heuristic_violation=True)
        assert r.any_failed

    def test_to_dict_exposes_any_failed(self):
        r = CrossCheckResults(performance_violation=True)
        d = r.to_dict()
        assert d["any_failed"] is True

    def test_roundtrip(self):
        r = CrossCheckResults(
            context_conflict=True,
            deprecation_detected=False,
            security_vulnerability=True,
        )
        restored = CrossCheckResults.from_dict(r.to_dict())
        assert restored.context_conflict is True
        assert restored.security_vulnerability is True
        assert restored.deprecation_detected is False


# ── ValidationTribunal ────────────────────────────────────────


class TestValidationTribunal:
    def test_default_verdict_pending(self):
        t = ValidationTribunal()
        assert t.verdict == TribunalVerdict.PENDING

    def test_set_verdict_pass(self):
        t = ValidationTribunal(verdict=TribunalVerdict.PASS, confidence_score=95.0)
        assert t.verdict == TribunalVerdict.PASS
        assert t.confidence_score == 95.0

    def test_to_dict_verdict_is_string(self):
        t = ValidationTribunal(verdict=TribunalVerdict.FAIL)
        d = t.to_dict()
        assert d["verdict"] == "FAIL"

    def test_roundtrip(self):
        t = ValidationTribunal(
            scout_model="flash-8b",
            adversary_model="flash",
            arbiter_model="pro",
            confidence_score=87.5,
            verdict=TribunalVerdict.PASS,
            heal_cycles_used=2,
        )
        restored = ValidationTribunal.from_dict(t.to_dict())
        assert restored.scout_model == "flash-8b"
        assert restored.confidence_score == 87.5
        assert restored.verdict == TribunalVerdict.PASS
        assert restored.heal_cycles_used == 2

    def test_roundtrip_with_cross_check(self):
        t = ValidationTribunal(
            cross_check_results=CrossCheckResults(deprecation_detected=True),
            verdict=TribunalVerdict.FAIL,
        )
        restored = ValidationTribunal.from_dict(t.to_dict())
        assert restored.cross_check_results.deprecation_detected is True


# ── GraphAwareness ────────────────────────────────────────────


class TestGraphAwareness:
    def test_default_blast_radius(self):
        g = GraphAwareness()
        assert g.blast_radius == 2

    def test_to_dict_includes_macro_hash(self):
        g = GraphAwareness(macro_state_hash="abc123")
        d = g.to_dict()
        assert d["macro_state_hash"] == "abc123"

    def test_roundtrip_with_edges(self):
        edge_id = uuid4()
        g = GraphAwareness(
            blast_radius=5,
            dependent_edge_ids=[edge_id],
            macro_state_hash="deadbeef",
        )
        restored = GraphAwareness.from_dict(g.to_dict())
        assert restored.blast_radius == 5
        assert len(restored.dependent_edge_ids) == 1
        assert restored.dependent_edge_ids[0] == edge_id

    def test_roundtrip_with_timestamp(self):
        now = datetime.now(UTC)
        g = GraphAwareness(last_blast_check=now)
        restored = GraphAwareness.from_dict(g.to_dict())
        assert restored.last_blast_check is not None
        # Compare at second resolution
        assert restored.last_blast_check.replace(microsecond=0) == now.replace(microsecond=0)

    def test_roundtrip_no_timestamp(self):
        g = GraphAwareness()
        assert g.last_blast_check is None
        restored = GraphAwareness.from_dict(g.to_dict())
        assert restored.last_blast_check is None


# ── ContextAwareEngram ────────────────────────────────────────


class TestContextAwareEngram:
    def _make(self, **kwargs) -> ContextAwareEngram:
        defaults = {
            "intent": "test intent",
            "ast_signature": "def f():",
            "logic_body": "return 1",
            "domain": Domain.BACKEND,
            "language": Language.PYTHON,
            "mandate_level": "L1",
        }
        defaults.update(kwargs)
        return ContextAwareEngram(**defaults)

    def test_is_reality_anchored_false_when_no_sources(self):
        e = self._make()
        assert not e.is_reality_anchored()

    def test_is_reality_anchored_true_after_adding_source(self):
        e = self._make()
        e.jit_context.add_source(
            JITSource(
                source_type=JITSourceType.API_DOCUMENTATION,
                raw_excerpt="REST best practices",
            )
        )
        assert e.is_reality_anchored()

    def test_needs_reanchor_false_when_fresh(self):
        e = self._make()
        assert not e.needs_reanchor()

    def test_needs_reanchor_true_when_stale_flag_set(self):
        e = self._make()
        e.jit_context.is_stale = True
        assert e.needs_reanchor()

    def test_needs_reanchor_true_when_source_expired(self):
        e = self._make()
        e.jit_context.add_source(
            JITSource(
                source_type=JITSourceType.SECURITY_ADVISORY,
                ttl_hours=1,
                fetched_at=datetime.now(UTC) - timedelta(hours=24),
            )
        )
        assert e.needs_reanchor()

    def test_from_logic_engram_preserves_intent(self):
        base = LogicEngram(
            intent="original intent",
            ast_signature="def orig():",
            logic_body="pass",
            domain=Domain.BACKEND,
            language=Language.PYTHON,
        )
        ctx = ContextAwareEngram.from_logic_engram(base, mandate_level="L2")
        assert ctx.intent == "original intent"
        assert ctx.engram_id == base.engram_id
        assert ctx.mandate_level == "L2"

    def test_from_logic_engram_creates_empty_tribunal(self):
        base = LogicEngram(
            intent="base",
            ast_signature="def b():",
            logic_body="pass",
            domain=Domain.BACKEND,
            language=Language.PYTHON,
        )
        ctx = ContextAwareEngram.from_logic_engram(base)
        assert ctx.tribunal.verdict == TribunalVerdict.PENDING
        assert ctx.tribunal.confidence_score == 0.0

    def test_to_dict_includes_jit_context(self):
        e = self._make()
        e.jit_context.add_source(JITSource(source_type=JITSourceType.BEST_PRACTICE))
        d = e.to_dict()
        assert "jit_context" in d
        assert d["jit_context"]["source_count"] == 1

    def test_to_dict_includes_tribunal(self):
        e = self._make()
        e.tribunal.verdict = TribunalVerdict.PASS
        d = e.to_dict()
        assert d["tribunal"]["verdict"] == "PASS"

    def test_to_dict_includes_mandate_level(self):
        e = self._make(mandate_level="L3")
        d = e.to_dict()
        assert d["mandate_level"] == "L3"

    def test_to_dict_includes_is_reality_anchored(self):
        e = self._make()
        d = e.to_dict()
        assert "is_reality_anchored" in d
        assert d["is_reality_anchored"] is False

    def test_roundtrip_minimal(self):
        e = self._make()
        d = e.to_dict()
        restored = ContextAwareEngram.from_dict(d)
        assert restored.intent == e.intent
        assert restored.mandate_level == "L1"
        assert restored.tribunal.verdict == TribunalVerdict.PENDING

    def test_roundtrip_with_jit_and_tribunal(self):
        e = self._make(mandate_level="L2")
        e.jit_context.add_source(
            JITSource(
                source_type=JITSourceType.SECURITY_ADVISORY,
                raw_excerpt="Always use parameterized queries",
                content_hash="sec001hash",
            )
        )
        e.tribunal.verdict = TribunalVerdict.PASS
        e.tribunal.confidence_score = 92.0
        e.tribunal.heal_cycles_used = 1

        d = e.to_dict()
        restored = ContextAwareEngram.from_dict(d)
        assert restored.mandate_level == "L2"
        assert restored.tribunal.verdict == TribunalVerdict.PASS
        assert restored.tribunal.confidence_score == 92.0
        assert len(restored.jit_context.sources) == 1
        assert restored.jit_context.sources[0].source_type == JITSourceType.SECURITY_ADVISORY

    def test_roundtrip_with_graph_awareness(self):
        e = self._make()
        e.graph_awareness = GraphAwareness(
            blast_radius=4,
            macro_state_hash="stateabc",
        )
        d = e.to_dict()
        restored = ContextAwareEngram.from_dict(d)
        assert restored.graph_awareness.blast_radius == 4
        assert restored.graph_awareness.macro_state_hash == "stateabc"

    def test_engram_id_preserved_in_roundtrip(self):
        e = self._make()
        original_id = e.engram_id
        d = e.to_dict()
        restored = ContextAwareEngram.from_dict(d)
        assert restored.engram_id == original_id


# ── JITSourceType enum ────────────────────────────────────────


class TestJITSourceType:
    def test_all_source_types_have_values(self):
        expected = {
            "api_documentation",
            "performance_benchmark",
            "security_advisory",
            "live_schema",
            "best_practice",
            "deprecation_notice",
        }
        actual = {t.value for t in JITSourceType}
        assert expected == actual
