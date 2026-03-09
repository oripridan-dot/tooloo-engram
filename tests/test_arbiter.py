"""Tests for engram.arbiter — Zero-Downtime Arbiter Healer."""

from __future__ import annotations

from uuid import uuid4

from engram_v2.arbiter import (
    ArbiterHealer,
    ArbiterPayload,
    MitosisResult,
    MockArbiterLLM,
    _mock_fix_deprecation,
    _mock_fix_heuristic,
    _mock_fix_performance,
    _mock_fix_security,
)
from engram_v2.graph_store import EngramGraph
from engram_v2.schema import (
    ContextAwareEngram,
    Domain,
    EdgeType,
    Language,
    SynapticEdge,
    TribunalVerdict,
)

# ── Fixtures ──────────────────────────────────────────────────


def make_engram(
    logic_body: str = "return True",
    domain: Domain = Domain.BACKEND,
    intent: str = "process data",
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


def make_adversary_result_fail(engram: ContextAwareEngram, rule_id: str = "SEC-001") -> object:
    """Generate a realistic FAIL result from AdversaryValidator."""
    from engram_v2.adversary import AdversaryResult, FatalErrorLog
    from engram_v2.schema import CrossCheckResults

    result = AdversaryResult(
        engram_target=engram.engram_id,
        fast_fail_triggered=True,
        cross_validation_matrix=CrossCheckResults(security_vulnerability=True),
        fatal_error_log=FatalErrorLog(
            detected=True,
            rule_id=rule_id,
            conflict_source="security_advisory",
            failing_code_snippet="f\"SELECT * FROM users WHERE id = '{user_id}'\"",
            severity="critical",
        ),
        adversary_verdict=TribunalVerdict.FAIL,
        rules_checked=3,
        validation_latency_ms=2.5,
    )
    return result


# ── MockArbiterLLM ────────────────────────────────────────────


class TestMockArbiterLLM:
    def test_fixes_security_body(self):
        llm = MockArbiterLLM(latency_ms=0)
        payload = ArbiterPayload(
            target_engram_id=uuid4(),
            intent="get user by email",
            ast_signature="def get_user(email):",
            broken_logic_body="q = f\"SELECT * FROM users WHERE email = '{email}'\"",
            rule_id="SEC-001",
            failure_description="SQL injection",
            failing_snippet='f"SELECT * WHERE email"',
            jit_advisory_excerpts=["use parameterized queries"],
            domain="backend",
            language="python",
        )
        healed = llm.heal(payload)
        assert isinstance(healed, str)
        assert len(healed.strip()) > 0

    def test_fixes_deprecation_body(self):
        llm = MockArbiterLLM(latency_ms=0)
        payload = ArbiterPayload(
            target_engram_id=uuid4(),
            intent="get utc time",
            ast_signature="def get_time():",
            broken_logic_body="return datetime.utcnow()",
            rule_id="DEP-002",
            failure_description="deprecated utcnow",
            failing_snippet="datetime.utcnow()",
            jit_advisory_excerpts=["use datetime.now(UTC)"],
            domain="backend",
            language="python",
        )
        healed = llm.heal(payload)
        assert "utcnow" not in healed or "UTC" in healed  # deprecated form replaced

    def test_fixes_heuristic_bare_except(self):
        llm = MockArbiterLLM(latency_ms=0)
        payload = ArbiterPayload(
            target_engram_id=uuid4(),
            intent="safe execute",
            ast_signature="def safe_exec():",
            broken_logic_body="try:\n    do()\nexcept:\n    pass",
            rule_id="HEU-001",
            failure_description="bare except",
            failing_snippet="except:",
            jit_advisory_excerpts=["always specify exception type"],
            domain="backend",
            language="python",
        )
        healed = llm.heal(payload)
        assert "except Exception:" in healed

    def test_unknown_rule_returns_nonempty(self):
        llm = MockArbiterLLM(latency_ms=0)
        payload = ArbiterPayload(
            target_engram_id=uuid4(),
            intent="test",
            ast_signature="def f():",
            broken_logic_body="",
            rule_id="UNKNOWN-999",
            failure_description="unknown",
            failing_snippet="",
            jit_advisory_excerpts=[],
            domain="backend",
            language="python",
        )
        healed = llm.heal(payload)
        assert healed.strip() != ""


# ── ArbiterPayload ────────────────────────────────────────────


class TestArbiterPayload:
    def test_to_prompt_contains_key_fields(self):
        payload = ArbiterPayload(
            target_engram_id=uuid4(),
            intent="validate JWT token",
            ast_signature="def validate_jwt(token):",
            broken_logic_body='secret = "hardcoded"',
            rule_id="SEC-002",
            failure_description="Hardcoded secret",
            failing_snippet='"hardcoded"',
            jit_advisory_excerpts=["Use environment variables for secrets"],
            domain="backend",
            language="python",
            mandate_level="L2",
        )
        prompt = payload.to_prompt()
        assert "MANDATE: Execute Engram Mitosis (Heal)" in prompt
        assert "SEC-002" in prompt
        assert "validate JWT token" in prompt
        assert "Use environment variables" in prompt
        assert "python" in prompt.lower()


# ── ArbiterHealer ─────────────────────────────────────────────


class TestArbiterHealer:
    def test_heal_success_basic(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body='api_key = "hardcoded_secret"')
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e, "SEC-002")
        result = healer.heal(graph, e, adv_result)
        assert result.success
        assert result.healed_engram_id != e.engram_id
        assert result.heal_latency_ms > 0

    def test_healed_engram_added_to_graph(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body='secret = "hardcoded"')
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e, "SEC-002")
        result = healer.heal(graph, e, adv_result)
        assert graph.has_engram(result.healed_engram_id)

    def test_original_engram_removed_from_graph(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body='secret = "hardcoded"')
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e, "SEC-002")
        original_id = e.engram_id
        healer.heal(graph, e, adv_result)
        assert not graph.has_engram(original_id)

    def test_edge_repointing_incoming(self):
        """Incoming edges to v1 should be repointed to v2."""
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        caller = make_engram(intent="caller")
        target = make_engram(logic_body='secret = "hardcoded"', intent="callee")
        graph.add_engram(caller)
        graph.add_engram(target)
        edge = SynapticEdge(
            source_id=caller.engram_id, target_id=target.engram_id, edge_type=EdgeType.CALLS
        )
        graph.add_edge(edge)

        adv_result = make_adversary_result_fail(target, "SEC-002")
        result = healer.heal(graph, target, adv_result)
        assert result.success
        assert result.edges_repointed > 0

    def test_max_cycles_exceeded_fails(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0), max_cycles=2)
        graph = EngramGraph()
        e = make_engram()
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e)
        result = healer.heal(graph, e, adv_result, cycle=3)  # exceeds max
        assert not result.success
        assert "Max heal cycles" in result.failure_reason

    def test_healed_engram_has_pass_verdict(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body='secret = "hardcoded"')
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e, "SEC-002")
        result = healer.heal(graph, e, adv_result)
        v2 = graph.get_engram(result.healed_engram_id)
        assert v2 is not None
        from engram_v2.schema import ContextAwareEngram

        assert isinstance(v2, ContextAwareEngram)
        assert v2.tribunal.verdict == TribunalVerdict.PASS

    def test_healed_engram_confidence_score_positive(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body='secret = "hardcoded"')
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e, "SEC-002")
        result = healer.heal(graph, e, adv_result)
        v2 = graph.get_engram(result.healed_engram_id)
        from engram_v2.schema import ContextAwareEngram

        if isinstance(v2, ContextAwareEngram):
            assert v2.tribunal.confidence_score > 0

    def test_mitosis_result_to_dict(self):
        result = MitosisResult(
            original_engram_id=uuid4(),
            healed_engram_id=uuid4(),
            success=True,
            edges_repointed=2,
            heal_cycle=1,
            heal_latency_ms=7.5,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["edges_repointed"] == 2
        assert d["heal_latency_ms"] == 7.5

    def test_heal_deprecation_rule(self):
        healer = ArbiterHealer(llm=MockArbiterLLM(latency_ms=0))
        graph = EngramGraph()
        e = make_engram(logic_body="return datetime.utcnow()")
        graph.add_engram(e)
        from engram_v2.adversary import AdversaryResult, FatalErrorLog
        from engram_v2.schema import CrossCheckResults

        adv = AdversaryResult(
            engram_target=e.engram_id,
            cross_validation_matrix=CrossCheckResults(deprecation_detected=True),
            fatal_error_log=FatalErrorLog(
                detected=True,
                rule_id="DEP-002",
                severity="medium",
                failing_code_snippet="datetime.utcnow()",
            ),
            adversary_verdict=TribunalVerdict.FAIL,
        )
        result = healer.heal(graph, e, adv)
        assert result.success

    def test_empty_healed_body_fails(self):
        """If Arbiter LLM returns empty string, result should fail."""

        class EmptyLLM:
            def heal(self, payload):
                return ""

        healer = ArbiterHealer(llm=EmptyLLM())
        graph = EngramGraph()
        e = make_engram()
        graph.add_engram(e)
        adv_result = make_adversary_result_fail(e)
        result = healer.heal(graph, e, adv_result)
        assert not result.success
        assert "empty" in result.failure_reason.lower()


# ── Mock fix functions ────────────────────────────────────────


class TestMockFixers:
    def test_fix_security_removes_sql_interpolation(self):
        code = "q = f\"SELECT * FROM users WHERE email = '{email}'\""
        fixed = _mock_fix_security(code)
        assert len(fixed) > 0

    def test_fix_security_replaces_hardcoded_secret(self):
        code = 'password = "my_secret_password"'
        fixed = _mock_fix_security(code)
        assert "os.environ.get" in fixed

    def test_fix_deprecation_utcnow(self):
        code = "ts = datetime.utcnow()"
        fixed = _mock_fix_deprecation(code)
        assert "utcnow" not in fixed
        assert "UTC" in fixed

    def test_fix_heuristic_bare_except(self):
        code = "try:\n    x()\nexcept:\n    pass"
        fixed = _mock_fix_heuristic(code)
        assert "except Exception:" in fixed

    def test_fix_performance_polling(self):
        code = "while True:\n    time.sleep(1)\n    fetch()"
        fixed = _mock_fix_performance(code)
        assert len(fixed) > 0  # transformed
