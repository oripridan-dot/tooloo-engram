"""Tests for engram.adversary — Fast-fail Adversary Validator."""

from __future__ import annotations

from engram_v2.adversary import (
    AdversaryResult,
    AdversaryValidator,
    FatalErrorLog,
    _check_jit_context_conflicts,
    _extract_snippet,
    _rule_applies,
)
from engram_v2.schema import (
    ContextAwareEngram,
    Domain,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    Language,
    TribunalVerdict,
)

# ── Helpers ───────────────────────────────────────────────────


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


# ── FatalErrorLog ─────────────────────────────────────────────


class TestFatalErrorLog:
    def test_default_not_detected(self):
        log = FatalErrorLog()
        assert not log.detected
        assert log.rule_id == ""
        assert log.conflict_source is None

    def test_to_dict_serializes_correctly(self):
        log = FatalErrorLog(
            detected=True,
            rule_id="SEC-001",
            conflict_source="jit",
            failing_code_snippet="eval(user_input)",
            severity="critical",
        )
        d = log.to_dict()
        assert d["detected"] is True
        assert d["rule_id"] == "SEC-001"
        assert d["severity"] == "critical"


# ── AdversaryResult ───────────────────────────────────────────


class TestAdversaryResult:
    def test_default_pending_verdict(self):
        e = make_engram()
        result = AdversaryResult(engram_target=e.engram_id)
        assert result.adversary_verdict == TribunalVerdict.PENDING

    def test_to_dict_keys(self):
        e = make_engram()
        result = AdversaryResult(engram_target=e.engram_id)
        d = result.to_dict()
        for key in (
            "engram_target",
            "fast_fail_triggered",
            "cross_validation_matrix",
            "fatal_error_log",
            "adversary_verdict",
            "rules_checked",
            "validation_latency_ms",
        ):
            assert key in d


# ── PASS scenarios ────────────────────────────────────────────


class TestAdversaryValidatorPass:
    def test_clean_code_passes(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body=(
                "from datetime import UTC, datetime\n\n"
                "def process(data):\n"
                "    return {'ts': datetime.now(UTC).isoformat(), 'data': data}"
            )
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.PASS
        assert not result.fast_fail_triggered
        assert not result.cross_validation_matrix.any_failed

    def test_clean_frontend_passes(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body=(
                "const MyComponent = () => {\n"
                "  const [val, setVal] = React.useState(null);\n"
                "  return <div>{val}</div>;\n"
                "};"
            ),
            domain=Domain.FRONTEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.PASS

    def test_latency_recorded(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="pass")
        result = validator.validate(e)
        assert result.validation_latency_ms >= 0.0

    def test_rules_checked_nonzero(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="return 42")
        result = validator.validate(e)
        assert result.rules_checked > 0


# ── FAIL: Security rules ──────────────────────────────────────


class TestAdversarySecurityFails:
    def test_sql_interpolation_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="query = f\"SELECT * FROM users WHERE email = '{email}'\"")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fast_fail_triggered
        assert result.cross_validation_matrix.security_vulnerability
        assert result.fatal_error_log.rule_id == "SEC-001"
        assert result.fatal_error_log.severity == "critical"

    def test_hardcoded_secret_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body='api_key = "sk-1234567890abcdef"')
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fast_fail_triggered
        assert result.fatal_error_log.rule_id == "SEC-002"

    def test_eval_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="result = eval(user_input)")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "SEC-003"

    def test_security_fast_fail_skips_remaining_checks(self):
        """After fast_fail, no additional rules should be checked."""
        validator = AdversaryValidator()
        # Both SQL injection and hardcoded secret — fast-fail should stop at SQL
        e = make_engram(
            logic_body=(
                'q = f"SELECT * FROM t WHERE id = \'{id}\'"\nsecret = "hardcoded_secret_1234"'
            )
        )
        result = validator.validate(e)
        assert result.fast_fail_triggered
        assert result.adversary_verdict == TribunalVerdict.FAIL

    def test_redirect_with_user_input_fails(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body="return redirect(request.args.get('next'))",
            domain=Domain.BACKEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "SEC-004"


# ── FAIL: Deprecation rules ───────────────────────────────────


class TestAdversaryDeprecationFails:
    def test_utcnow_fails(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body="ts = datetime.utcnow()",
            domain=Domain.BACKEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.cross_validation_matrix.deprecation_detected
        assert result.fatal_error_log.rule_id == "DEP-002"

    def test_class_component_fails(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body="class MyComp extends React.Component { render() { return null; } }",
            domain=Domain.FRONTEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "DEP-003"

    def test_event_loop_deprecation_fails(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body="asyncio.get_event_loop().run_until_complete(main())",
            domain=Domain.BACKEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "DEP-004"


# ── FAIL: Performance rules ───────────────────────────────────


class TestAdversaryPerformanceFails:
    def test_polling_loop_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="while True:\n    time.sleep(1)\n    check_updates()")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.cross_validation_matrix.performance_violation
        assert result.fatal_error_log.rule_id == "PERF-001"

    def test_n_plus_1_fails(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body=(
                "for user in users:\n"
                "    profile = db.query(Profile).filter(Profile.user_id == user.id).first()"
            ),
            domain=Domain.BACKEND,
        )
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "PERF-002"


# ── FAIL: Heuristic rules ─────────────────────────────────────


class TestAdversaryHeuristicFails:
    def test_bare_except_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="try:\n    do_thing()\nexcept:\n    pass")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.cross_validation_matrix.heuristic_violation
        assert result.fatal_error_log.rule_id == "HEU-001"

    def test_mutable_default_fails(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="def process(items=[]):\n    items.append(1)\n    return items")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "HEU-002"


# ── Critical-only mode ────────────────────────────────────────


class TestAdversaryCriticalOnlyMode:
    def test_low_severity_skipped_in_critical_mode(self):
        validator = AdversaryValidator(critical_only=True)
        e = make_engram(
            logic_body="try:\n    do_thing()\nexcept:\n    pass"  # HEU-001 is high
        )
        result = validator.validate(e)
        # HEU-001 is "high" severity — should still be caught
        # BUT heuristic rules are skipped in critical_only mode entirely
        # (by design: critical_only skips all _HEURISTIC_RULES)
        # so this should PASS in critical_only mode
        assert result.adversary_verdict == TribunalVerdict.PASS

    def test_critical_security_still_caught_in_critical_mode(self):
        validator = AdversaryValidator(critical_only=True)
        e = make_engram(logic_body='api_key = "hardcoded_secret_xyz"')
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL


# ── JIT context conflict detection ───────────────────────────


class TestJITContextConflicts:
    def test_no_conflict_on_empty_matrix(self):
        matrix = JITContextMatrix()
        conflicts = _check_jit_context_conflicts("return 42", matrix)
        assert conflicts == []

    def test_advisory_conflict_detected(self):
        matrix = JITContextMatrix()
        src = JITSource(
            source_type=JITSourceType.DEPRECATION_NOTICE,
            raw_excerpt="avoid utcnow — use datetime.now(UTC) instead",
        )
        matrix.add_source(src)
        code = "ts = datetime.utcnow()"
        conflicts = _check_jit_context_conflicts(code, matrix)
        assert len(conflicts) > 0
        assert "utcnow" in conflicts[0]

    def test_no_conflict_when_code_is_clean(self):
        matrix = JITContextMatrix()
        src = JITSource(
            source_type=JITSourceType.DEPRECATION_NOTICE,
            raw_excerpt="avoid utcnow — use datetime.now(UTC) instead",
        )
        matrix.add_source(src)
        code = "ts = datetime.now(UTC)"
        conflicts = _check_jit_context_conflicts(code, matrix)
        assert conflicts == []


# ── Validate many ─────────────────────────────────────────────


class TestValidateMany:
    def test_validate_many_returns_all_results(self):
        validator = AdversaryValidator()
        engrams = [
            make_engram(logic_body="return 42"),
            make_engram(logic_body='api_key = "hardcoded_1234"'),
            make_engram(logic_body="pass"),
        ]
        results = validator.validate_many(engrams)
        assert len(results) == 3
        assert results[1].adversary_verdict == TribunalVerdict.FAIL  # the poisoned one
        assert results[0].adversary_verdict == TribunalVerdict.PASS
        assert results[2].adversary_verdict == TribunalVerdict.PASS


# ── Helper functions ──────────────────────────────────────────


class TestHelpers:
    def test_extract_snippet_short_code(self):
        snippet = _extract_snippet("x = 1", 2)
        assert snippet != ""

    def test_extract_snippet_long_code(self):
        code = "a" * 200
        snippet = _extract_snippet(code, 100)
        assert len(snippet) <= 80 + 10 + 10  # window + context

    def test_rule_applies_no_domain_restriction(self):
        import re

        from engram_v2.adversary import HeuristicRule

        rule = HeuristicRule(
            rule_id="TEST-001",
            description="test",
            pattern=re.compile(r"test"),
            failure_type="heuristic_violation",
            domains=(),  # applies to all
        )
        assert _rule_applies(rule, "backend")
        assert _rule_applies(rule, "frontend")

    def test_rule_applies_domain_restricted(self):
        import re

        from engram_v2.adversary import HeuristicRule

        rule = HeuristicRule(
            rule_id="TEST-002",
            description="test",
            pattern=re.compile(r"test"),
            failure_type="heuristic_violation",
            domains=(Domain.BACKEND,),
        )
        assert _rule_applies(rule, "backend")
        assert not _rule_applies(rule, "frontend")


# ── TEST domain exclusion ─────────────────────────────────────


class TestTestDomainExclusion:
    """Security rules must NOT fire on Domain.TEST engrams (tests legitimately
    contain eval, exec, hardcoded test secrets, etc.)."""

    def test_eval_in_test_domain_passes(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="result = eval(user_input)", domain=Domain.TEST)
        result = validator.validate(e)
        # SEC-003 should NOT fire — Domain.TEST is excluded
        assert result.fatal_error_log.rule_id != "SEC-003"

    def test_exec_in_test_domain_passes(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body="exec(code_string)", domain=Domain.TEST)
        result = validator.validate(e)
        assert result.fatal_error_log.rule_id != "SEC-003"

    def test_hardcoded_secret_in_test_domain_passes(self):
        validator = AdversaryValidator()
        e = make_engram(logic_body='api_key = "sk-test-1234567890"', domain=Domain.TEST)
        result = validator.validate(e)
        assert result.fatal_error_log.rule_id != "SEC-002"

    def test_sql_injection_in_test_domain_passes(self):
        validator = AdversaryValidator()
        e = make_engram(
            logic_body="query = f\"SELECT * FROM users WHERE id = '{user_id}'\"",
            domain=Domain.TEST,
        )
        result = validator.validate(e)
        assert result.fatal_error_log.rule_id != "SEC-001"


# ── SEC-003 regex precision ───────────────────────────────────


class TestSEC003RegexPrecision:
    """SEC-003 must match word-boundary eval/exec but NOT .exec() or
    identifiers ending in _exec."""

    def test_dotexec_does_not_trigger(self):
        """JS RegExp .exec() should NOT trigger SEC-003."""
        validator = AdversaryValidator()
        e = make_engram(logic_body="match = pattern.exec(input_string)")
        result = validator.validate(e)
        # .exec() has no word boundary before exec, so should pass
        assert result.fatal_error_log.rule_id != "SEC-003"

    def test_subprocess_exec_does_not_trigger(self):
        """subprocess_exec() should NOT trigger SEC-003."""
        validator = AdversaryValidator()
        e = make_engram(logic_body="result = subprocess_exec(cmd)")
        result = validator.validate(e)
        assert result.fatal_error_log.rule_id != "SEC-003"

    def test_bare_exec_still_triggers(self):
        """Bare exec(user_code) MUST still trigger SEC-003."""
        validator = AdversaryValidator()
        e = make_engram(logic_body="exec(user_code)")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "SEC-003"

    def test_bare_eval_still_triggers(self):
        """Bare eval(expr) MUST still trigger SEC-003."""
        validator = AdversaryValidator()
        e = make_engram(logic_body="val = eval(expr)")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "SEC-003"

    def test_exec_with_space_triggers(self):
        """exec (code) with space before paren MUST trigger SEC-003."""
        validator = AdversaryValidator()
        e = make_engram(logic_body="exec (dynamic_code)")
        result = validator.validate(e)
        assert result.adversary_verdict == TribunalVerdict.FAIL
        assert result.fatal_error_log.rule_id == "SEC-003"
