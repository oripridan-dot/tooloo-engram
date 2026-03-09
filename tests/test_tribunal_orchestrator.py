"""Tests for engram.tribunal_orchestrator — TribunalOrchestrator V2 pipeline."""

from __future__ import annotations

from uuid import uuid4

from engram_v2.adversary import AdversaryValidator
from engram_v2.arbiter import ArbiterHealer, MockArbiterLLM
from engram_v2.delta_sync import DeltaSyncBus, MutationEventType
from engram_v2.graph_store import EngramGraph
from engram_v2.jit_context import JITContextAnchor, MockContextFetcher
from engram_v2.schema import (
    ContextAwareEngram,
    Domain,
    Language,
    TribunalVerdict,
)
from engram_v2.tribunal_orchestrator import (
    TribunalOrchestrator,
    TribunalRunResult,
)

# ── Fixtures ──────────────────────────────────────────────────


def make_orchestrator(max_heal_cycles: int = 3) -> tuple[TribunalOrchestrator, DeltaSyncBus]:
    bus = DeltaSyncBus()
    orchid = TribunalOrchestrator(
        anchor=JITContextAnchor(fetcher=MockContextFetcher()),
        validator=AdversaryValidator(),
        healer=ArbiterHealer(llm=MockArbiterLLM(latency_ms=0)),
        bus=bus,
        max_heal_cycles=max_heal_cycles,
    )
    return orchid, bus


def make_clean_engram(
    intent: str = "process request", logic_body: str = "return True"
) -> ContextAwareEngram:
    return ContextAwareEngram(
        intent=intent,
        ast_signature="def func():",
        logic_body=logic_body,
        domain=Domain.BACKEND,
        language=Language.PYTHON,
        mandate_level="L1",
    )


def make_poisoned_engram(rule_id: str = "SEC-001") -> ContextAwareEngram:
    bodies = {
        "SEC-001": "q = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
        "SEC-002": 'api_key = "hardcoded_secret_key_1234"',
        "DEP-002": "return datetime.utcnow()",
        "HEU-001": "try:\n    do_work()\nexcept:\n    pass",
        "PERF-001": "while True:\n    time.sleep(5)\n    poll()",
    }
    return ContextAwareEngram(
        intent=f"poisoned engram [{rule_id}]",
        ast_signature="def poisoned():",
        logic_body=bodies.get(rule_id, f'dangerous_code("{rule_id}")'),
        domain=Domain.BACKEND,
        language=Language.PYTHON,
        mandate_level="L1",
    )


# ── TribunalRunResult ─────────────────────────────────────────


class TestTribunalRunResult:
    def test_to_dict_all_fields(self):
        r = TribunalRunResult(
            engram_id=uuid4(),
            final_engram_id=uuid4(),
            passed=True,
            heal_cycles=0,
            jit_sources_added=4,
            adversary_rules_checked=8,
            total_latency_ms=12.5,
            pipeline_stages=["JIT_ANCHOR:sources=4", "ADVERSARY:PASS:rules=8"],
        )
        d = r.to_dict()
        assert d["passed"] is True
        assert d["jit_sources_added"] == 4
        assert d["adversary_rules_checked"] == 8
        assert len(d["pipeline_stages"]) == 2

    def test_pipeline_stages_default_empty(self):
        r = TribunalRunResult(engram_id=uuid4(), final_engram_id=uuid4())
        assert r.pipeline_stages == []


# ── Happy path ────────────────────────────────────────────────


class TestTribunalOrchestratorPassPath:
    def test_clean_engram_passes(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.passed

    def test_pass_path_has_jit_sources(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.jit_sources_added > 0

    def test_pass_path_stages_contain_jit(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert any("JIT_ANCHOR" in s for s in result.pipeline_stages)

    def test_pass_path_no_heal_cycles(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.heal_cycles == 0

    def test_pass_path_final_id_matches_original(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.final_engram_id == e.engram_id

    def test_clean_engram_tribunal_verdict_set(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        orchid.run(graph, e)
        assert e.tribunal.verdict == TribunalVerdict.PASS


# ── Heal / mitosis path ───────────────────────────────────────


class TestTribunalOrchestratorHealPath:
    def test_poisoned_engram_fails_then_heals(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-001")
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.heal_cycles >= 1

    def test_heal_path_final_id_differs(self):
        """After Mitosis, final_engram_id must differ from original."""
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-001")
        graph.add_engram(e)
        result = orchid.run(graph, e)
        if result.passed:
            assert result.final_engram_id != result.engram_id

    def test_heal_path_emits_pending_event(self):
        orchid, bus = make_orchestrator()
        pending_events = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_PENDING, pending_events.append)
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-001")
        graph.add_engram(e)
        orchid.run(graph, e)
        assert len(pending_events) >= 1

    def test_heal_path_emits_commit_event(self):
        orchid, bus = make_orchestrator()
        commit_events = []
        bus.subscribe(MutationEventType.ENGRAM_MUTATION_COMMIT, commit_events.append)
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-002")
        graph.add_engram(e)
        result = orchid.run(graph, e)
        if result.passed:
            assert len(commit_events) >= 1

    def test_heal_path_stages_contain_arbiter(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-001")
        graph.add_engram(e)
        result = orchid.run(graph, e)
        if result.heal_cycles > 0:
            assert any("ARBITER_HEAL" in s for s in result.pipeline_stages)

    def test_heal_path_pipeline_stages_in_order(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_poisoned_engram("SEC-001")
        graph.add_engram(e)
        result = orchid.run(graph, e)
        # JIT must come before ADVERSARY
        if result.pipeline_stages:
            stage_names = [s.split(":")[0] for s in result.pipeline_stages]
            if "JIT_ANCHOR" in stage_names and "ADVERSARY" in stage_names:
                assert stage_names.index("JIT_ANCHOR") < stage_names.index("ADVERSARY")


# ── Latency ────────────────────────────────────────────────────


class TestTribunalOrchestratorLatency:
    def test_total_latency_recorded(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.total_latency_ms >= 0

    def test_latency_reasonable_for_mock(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        # MockContextFetcher + no LLM → should complete in < 500ms
        assert result.total_latency_ms < 500.0


# ── Batch runs ────────────────────────────────────────────────


class TestTribunalOrchestratorBatch:
    def test_run_batch_returns_results_per_engram(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        engrams = [make_clean_engram(f"intent {i}") for i in range(5)]
        for e in engrams:
            graph.add_engram(e)
        results = orchid.run_batch(graph, engrams)
        assert len(results) == 5

    def test_run_batch_all_clean_pass(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        engrams = [
            make_clean_engram(f"safe operation {i}", "return {'ok': True}") for i in range(3)
        ]
        for e in engrams:
            graph.add_engram(e)
        results = orchid.run_batch(graph, engrams)
        assert all(r.passed for r in results)

    def test_run_batch_empty_list(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        results = orchid.run_batch(graph, [])
        assert results == []

    def test_run_batch_mixed_clean_and_poisoned(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        clean = [make_clean_engram("safe op", "return 42")]
        poisoned = [make_poisoned_engram("SEC-001")]
        all_engrams = clean + poisoned
        for e in all_engrams:
            graph.add_engram(e)
        results = orchid.run_batch(graph, all_engrams)
        assert len(results) == 2
        # clean should pass
        assert results[0].passed


# ── Rules checked ─────────────────────────────────────────────


class TestTribunalOrchestratorRulesChecked:
    def test_adversary_rules_checked_positive(self):
        orchid, _ = make_orchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result.adversary_rules_checked > 0

    def test_default_orchestrator_uses_mocks(self):
        """Default construction (no args) should use Mock implementations and succeed."""
        orchid = TribunalOrchestrator()
        graph = EngramGraph()
        e = make_clean_engram()
        graph.add_engram(e)
        result = orchid.run(graph, e)
        assert result is not None
        assert result.total_latency_ms >= 0
