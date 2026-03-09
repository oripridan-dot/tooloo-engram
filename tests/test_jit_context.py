"""Tests for engram.jit_context — JIT Reality Anchor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engram_v2.graph_store import EngramGraph
from engram_v2.jit_context import (
    JITContextAnchor,
    MockContextFetcher,
    _extract_intent_keyword,
    _resolve_source_types,
    sweep_stale_engrams,
    upgrade_to_context_aware,
)
from engram_v2.schema import (
    ContextAwareEngram,
    Domain,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    Language,
    LogicEngram,
)

# ── Fixtures ──────────────────────────────────────────────────


def make_engram(
    intent: str = "process data",
    domain: Domain = Domain.BACKEND,
    logic_body: str = "return True",
    mandate_level: str = "L1",
) -> ContextAwareEngram:
    return ContextAwareEngram(
        intent=intent,
        ast_signature="def func():",
        logic_body=logic_body,
        domain=domain,
        language=Language.PYTHON,
        mandate_level=mandate_level,
    )


# ── MockContextFetcher ────────────────────────────────────────


class TestMockContextFetcher:
    def test_returns_jit_source(self):
        fetcher = MockContextFetcher(latency_ms=0)
        src = fetcher.fetch(JITSourceType.API_DOCUMENTATION, "api", "backend")
        assert isinstance(src, JITSource)
        assert src.source_type == JITSourceType.API_DOCUMENTATION
        assert src.content_hash != ""
        assert src.raw_excerpt != ""

    def test_url_contains_source_type(self):
        fetcher = MockContextFetcher(latency_ms=0)
        src = fetcher.fetch(JITSourceType.SECURITY_ADVISORY, "auth", "backend")
        assert "security_advisory" in src.url

    def test_different_source_types_different_excerpts(self):
        fetcher = MockContextFetcher(latency_ms=0)
        s1 = fetcher.fetch(JITSourceType.API_DOCUMENTATION, "api", "backend")
        s2 = fetcher.fetch(JITSourceType.SECURITY_ADVISORY, "api", "backend")
        assert s1.raw_excerpt != s2.raw_excerpt

    def test_deterministic_content_hash(self):
        fetcher = MockContextFetcher(latency_ms=0)
        s1 = fetcher.fetch(JITSourceType.BEST_PRACTICE, "cache", "backend")
        s2 = fetcher.fetch(JITSourceType.BEST_PRACTICE, "cache", "backend")
        assert s1.content_hash == s2.content_hash

    def test_not_expired_immediately(self):
        fetcher = MockContextFetcher(latency_ms=0)
        src = fetcher.fetch(JITSourceType.DEPRECATION_NOTICE, "api", "backend")
        assert not src.is_expired


# ── JITContextMatrix ──────────────────────────────────────────


class TestJITContextMatrix:
    def test_empty_matrix_no_sources(self):
        m = JITContextMatrix()
        assert m.sources == []
        assert m.reality_hash == ""
        assert not m.any_expired

    def test_add_source_updates_hash(self):
        m = JITContextMatrix()
        fetcher = MockContextFetcher(latency_ms=0)
        src = fetcher.fetch(JITSourceType.API_DOCUMENTATION, "api", "backend")
        m.add_source(src)
        assert len(m.sources) == 1
        assert m.reality_hash != ""

    def test_add_two_sources_different_hash(self):
        m = JITContextMatrix()
        fetcher = MockContextFetcher(latency_ms=0)
        s1 = fetcher.fetch(JITSourceType.API_DOCUMENTATION, "api", "backend")
        s2 = fetcher.fetch(JITSourceType.SECURITY_ADVISORY, "auth", "backend")
        m.add_source(s1)
        hash1 = m.reality_hash
        m.add_source(s2)
        hash2 = m.reality_hash
        assert hash1 != hash2

    def test_expired_source_detected(self):
        m = JITContextMatrix()
        src = JITSource(
            source_type=JITSourceType.API_DOCUMENTATION,
            fetched_at=datetime.now(UTC) - timedelta(hours=100),
            ttl_hours=72,
        )
        m.add_source(src)
        assert m.any_expired

    def test_to_dict_roundtrip(self):
        m = JITContextMatrix()
        fetcher = MockContextFetcher(latency_ms=0)
        m.add_source(fetcher.fetch(JITSourceType.API_DOCUMENTATION, "api", "backend"))
        d = m.to_dict()
        restored = JITContextMatrix.from_dict(d)
        assert len(restored.sources) == len(m.sources)
        assert restored.reality_hash == m.reality_hash


# ── JITContextAnchor ──────────────────────────────────────────


class TestJITContextAnchor:
    def test_anchor_populates_sources(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        result = anchor.anchor(e)
        assert result.sources_added > 0
        assert len(e.jit_context.sources) > 0

    def test_anchor_skipped_if_already_anchored(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        anchor.anchor(e)  # first anchor
        count_after_first = len(e.jit_context.sources)
        result = anchor.anchor(e)  # second anchor — should skip
        assert result.sources_added == 0
        assert len(e.jit_context.sources) == count_after_first

    def test_force_reanchor_replaces_sources(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        anchor.anchor(e)
        result = anchor.anchor(e, force=True)
        assert result.was_reanchor
        assert result.sources_added > 0

    def test_security_intent_fetches_security_advisory(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram(intent="validate auth token and password")
        anchor.anchor(e)
        types = [s.source_type for s in e.jit_context.sources]
        assert JITSourceType.SECURITY_ADVISORY in types

    def test_websocket_intent_fetches_performance_benchmark(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram(intent="websocket realtime sync handler")
        anchor.anchor(e)
        types = [s.source_type for s in e.jit_context.sources]
        assert JITSourceType.PERFORMANCE_BENCHMARK in types

    def test_anchor_many(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        engrams = [make_engram(intent=f"component {i}") for i in range(5)]
        results = anchor.anchor_many(engrams)
        assert len(results) == 5
        assert all(r.sources_added > 0 for r in results)

    def test_anchor_result_has_reality_hash(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        result = anchor.anchor(e)
        assert result.reality_hash != ""
        assert isinstance(result.latency_ms, float)

    def test_frontend_domain_fetches_best_practice(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram(domain=Domain.FRONTEND)
        anchor.anchor(e)
        types = [s.source_type for s in e.jit_context.sources]
        assert JITSourceType.BEST_PRACTICE in types


# ── TTL Sweeper ───────────────────────────────────────────────


class TestSweepStaleEngrams:
    def test_no_stale_engrams_initially(self):
        graph = EngramGraph()
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        anchor.anchor(e)
        graph.add_engram(e)
        report = sweep_stale_engrams(graph)
        assert report.stale_count == 0
        assert report.total_checked == 1

    def test_plain_logic_engram_not_counted(self):
        graph = EngramGraph()
        e = LogicEngram(intent="test", ast_signature="def f():", logic_body="pass")
        graph.add_engram(e)
        report = sweep_stale_engrams(graph)
        assert report.total_checked == 0  # only ContextAwareEngrams are checked

    def test_stale_engram_detected(self):
        graph = EngramGraph()
        e = make_engram()
        # Inject an expired source
        e.jit_context.sources.append(
            JITSource(
                source_type=JITSourceType.API_DOCUMENTATION,
                fetched_at=datetime.now(UTC) - timedelta(hours=200),
                ttl_hours=72,
            )
        )
        graph.add_engram(e)
        report = sweep_stale_engrams(graph)
        assert report.stale_count == 1
        assert e.engram_id in report.stale_engram_ids

    def test_auto_reanchor_on_sweep(self):
        graph = EngramGraph()
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = make_engram()
        e.jit_context.sources.append(
            JITSource(
                source_type=JITSourceType.API_DOCUMENTATION,
                fetched_at=datetime.now(UTC) - timedelta(hours=200),
                ttl_hours=72,
            )
        )
        graph.add_engram(e)
        sweep_stale_engrams(graph, anchor=anchor, auto_reanchor=True)
        # After re-anchor, sources should be fresh
        assert not e.jit_context.any_expired

    def test_sweep_report_has_latency(self):
        graph = EngramGraph()
        report = sweep_stale_engrams(graph)
        assert report.sweep_latency_ms >= 0.0


# ── Helpers ───────────────────────────────────────────────────


class TestHelpers:
    def test_extract_intent_keyword_auth(self):
        assert _extract_intent_keyword("validate auth token") == "auth"

    def test_extract_intent_keyword_sql(self):
        assert _extract_intent_keyword("run sql query safely") == "sql"

    def test_extract_intent_keyword_fallback(self):
        kw = _extract_intent_keyword("create user profile")
        assert kw == "create"  # first word

    def test_resolve_source_types_backend(self):
        types = _resolve_source_types("process data", Domain.BACKEND)
        assert JITSourceType.API_DOCUMENTATION in types
        assert JITSourceType.SECURITY_ADVISORY in types

    def test_resolve_source_types_adds_advisory_on_keyword(self):
        types = _resolve_source_types("validate password auth", Domain.BACKEND)
        assert JITSourceType.SECURITY_ADVISORY in types


# ── upgrade_to_context_aware ──────────────────────────────────


class TestUpgradeToContextAware:
    def test_upgrades_plain_engram(self):
        anchor = JITContextAnchor(fetcher=MockContextFetcher(latency_ms=0))
        e = LogicEngram(intent="create user", ast_signature="def f():", logic_body="pass")
        ctx = upgrade_to_context_aware(e, anchor, mandate_level="L2")
        assert isinstance(ctx, ContextAwareEngram)
        assert ctx.engram_id == e.engram_id
        assert ctx.mandate_level == "L2"
        assert ctx.is_reality_anchored()
