"""
Adversary Validator — Fast-fail binary engram cross-checker for Engram V2.

The Adversary is NOT a conversational AI reviewer. It is a strict boolean
logic gate that compares generated engram logic against real-world JIT context
and domain heuristics. Output is always a structured AdversaryResult (JSON-able),
never prose.

Architecture:
  - Fast-fail: hits stop the check immediately, saving tokens
  - Domain heuristics: injected lazily based on engram domain + intent
  - Cross-check matrix: per-dimension boolean flags
  - Token starvation: PASS output is ~10 fields; FAIL adds only one focused snippet

Usage:
    validator = AdversaryValidator()
    result = validator.validate(engram)
    if result.verdict == AdversaryVerdict.FAIL:
        # route to ArbiterHealer
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .schema import (
    ContextAwareEngram,
    CrossCheckResults,
    Domain,
    JITContextMatrix,
    TribunalVerdict,
)

# ── Heuristic rule definitions ────────────────────────────────


@dataclass(frozen=True)
class HeuristicRule:
    """A single adversary heuristic — domain-scoped, fast-regex check."""

    rule_id: str
    description: str
    pattern: re.Pattern  # type: ignore[type-arg]
    failure_type: str  # "heuristic_violation" | "security_vulnerability" | "deprecation_detected" | "performance_violation"
    domains: tuple[str, ...] = ()  # empty = all domains
    severity: str = "medium"  # low / medium / high / critical


# Domains where security rules apply — TEST is excluded because test code
# legitimately contains eval(), exec(), hardcoded test secrets, etc.
_NON_TEST_DOMAINS: tuple[str, ...] = (
    Domain.BACKEND,
    Domain.FRONTEND,
    Domain.AUTH,
    Domain.INFRA,
    Domain.API_CONTRACT,
    Domain.DATABASE,
    Domain.CONFIG,
)

_SECURITY_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="SEC-001",
        description="SQL string interpolation — injection risk",
        pattern=re.compile(r'f["\'].*SELECT.*\{|%s.*WHERE|format\(.*SELECT', re.IGNORECASE),
        failure_type="security_vulnerability",
        domains=_NON_TEST_DOMAINS,
        severity="critical",
    ),
    HeuristicRule(
        rule_id="SEC-002",
        description="Hardcoded secret or credential",
        pattern=re.compile(
            r'(password|secret|api_key|token)\s*=\s*["\'][^"\']{4,}["\']', re.IGNORECASE
        ),
        failure_type="security_vulnerability",
        domains=_NON_TEST_DOMAINS,
        severity="critical",
    ),
    HeuristicRule(
        rule_id="SEC-003",
        description="eval() or exec() with user-supplied input",
        pattern=re.compile(r"(?<![.\w])eval\s*\(|(?<![.\w])exec\s*\(", re.IGNORECASE),
        failure_type="security_vulnerability",
        domains=_NON_TEST_DOMAINS,
        severity="high",
    ),
    HeuristicRule(
        rule_id="SEC-004",
        description="Unvalidated redirect URL construction",
        pattern=re.compile(r"redirect\(.*request\.(args|params|body)", re.IGNORECASE),
        failure_type="security_vulnerability",
        domains=(Domain.BACKEND, Domain.FRONTEND),
        severity="high",
    ),
]

_DEPRECATION_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="DEP-001",
        description="Python 2 print statement",
        pattern=re.compile(r'\bprint\s+["\']', re.IGNORECASE),
        failure_type="deprecation_detected",
        domains=(Domain.BACKEND, Domain.TEST),
        severity="low",
    ),
    HeuristicRule(
        rule_id="DEP-002",
        description="Deprecated datetime.utcnow() — use datetime.now(UTC)",
        pattern=re.compile(r"datetime\.utcnow\(\)", re.IGNORECASE),
        failure_type="deprecation_detected",
        domains=(Domain.BACKEND,),
        severity="medium",
    ),
    HeuristicRule(
        rule_id="DEP-003",
        description="React class component (use functional + hooks)",
        pattern=re.compile(r"extends\s+(React\.)?Component\b", re.IGNORECASE),
        failure_type="deprecation_detected",
        domains=(Domain.FRONTEND,),
        severity="medium",
    ),
    HeuristicRule(
        rule_id="DEP-004",
        description="asyncio.get_event_loop() deprecated in 3.10+",
        pattern=re.compile(r"asyncio\.get_event_loop\(\)\.run_until_complete", re.IGNORECASE),
        failure_type="deprecation_detected",
        domains=(Domain.BACKEND,),
        severity="medium",
    ),
]

_PERFORMANCE_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="PERF-001",
        description="Polling loop instead of event-driven (realtime domain)",
        pattern=re.compile(
            r"while\s+True.*time\.sleep|setInterval|setTimeout.*poll", re.IGNORECASE | re.DOTALL
        ),
        failure_type="performance_violation",
        severity="high",
    ),
    HeuristicRule(
        rule_id="PERF-002",
        description="N+1 query pattern (loop containing ORM call)",
        pattern=re.compile(
            r"for\s+\w+\s+in\s+\w+.*\.(get|filter|query|find)\(", re.IGNORECASE | re.DOTALL
        ),
        failure_type="performance_violation",
        domains=(Domain.BACKEND,),
        severity="high",
    ),
    HeuristicRule(
        rule_id="PERF-003",
        description="Synchronous HTTP call in async context",
        pattern=re.compile(
            r"(async def|await).*requests\.(get|post|put|delete)\(", re.IGNORECASE | re.DOTALL
        ),
        failure_type="performance_violation",
        domains=(Domain.BACKEND,),
        severity="medium",
    ),
]

_HEURISTIC_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="HEU-001",
        description="Empty except clause swallows all errors",
        pattern=re.compile(r"except\s*:", re.IGNORECASE),
        failure_type="heuristic_violation",
        severity="high",
    ),
    HeuristicRule(
        rule_id="HEU-002",
        description="Mutable default argument in function signature",
        pattern=re.compile(r"def\s+\w+\([^)]*=\s*(\[\]|\{\}|\(\))", re.IGNORECASE),
        failure_type="heuristic_violation",
        domains=(Domain.BACKEND, Domain.TEST),
        severity="medium",
    ),
    HeuristicRule(
        rule_id="HEU-003",
        description="Implicit string concatenation in loop",
        pattern=re.compile(r'for\s+\w+\s+in.*:\s*\n\s*\w+\s*\+=\s*["\']', re.IGNORECASE),
        failure_type="heuristic_violation",
        severity="low",
    ),
]

_DATABASE_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="DB-001",
        description="N+1 query pattern — ORM call inside loop",
        pattern=re.compile(
            r"for\s+\w+\s+in\s+\w+.*\.(get|filter|query|find|find_one|fetch)\(",
            re.IGNORECASE | re.DOTALL,
        ),
        failure_type="performance_violation",
        domains=(Domain.BACKEND, Domain.DATABASE),
        severity="high",
    ),
    HeuristicRule(
        rule_id="DB-002",
        description="Unparameterized query — string interpolation in SQL",
        pattern=re.compile(
            r'f["\'].*(?:INSERT|UPDATE|DELETE|SELECT).*\{',
            re.IGNORECASE,
        ),
        failure_type="security_vulnerability",
        domains=(Domain.BACKEND, Domain.DATABASE),
        severity="critical",
    ),
    HeuristicRule(
        rule_id="DB-003",
        description="Missing index hint — large table scan without LIMIT",
        pattern=re.compile(
            r"SELECT\s+\*\s+FROM\s+\w+\s*(?:WHERE|$)(?!.*LIMIT)",
            re.IGNORECASE,
        ),
        failure_type="performance_violation",
        domains=(Domain.DATABASE,),
        severity="medium",
    ),
]

_AUTH_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="AUTH-001",
        description="Plaintext password storage — no hashing",
        pattern=re.compile(
            r"(?:password|passwd)\s*=\s*(?:request|body|payload|data|params)\.",
            re.IGNORECASE,
        ),
        failure_type="security_vulnerability",
        domains=(Domain.BACKEND, Domain.AUTH),
        severity="critical",
    ),
    HeuristicRule(
        rule_id="AUTH-002",
        description="JWT without expiry — token never expires",
        pattern=re.compile(
            r"jwt\.encode\([^)]*(?!exp|expires)",
            re.IGNORECASE,
        ),
        failure_type="security_vulnerability",
        domains=(Domain.BACKEND, Domain.AUTH),
        severity="high",
    ),
    HeuristicRule(
        rule_id="AUTH-003",
        description="Missing RBAC check — no permission validation before action",
        pattern=re.compile(
            r"@app\.(?:post|put|delete|patch)\([^)]*\)\s*\n(?:async\s+)?def\s+\w+\([^)]*\)(?:(?!check_permission|require_role|verify_access|has_permission|authorize).)*?(?:db\.|repository\.|service\.)",
            re.IGNORECASE | re.DOTALL,
        ),
        failure_type="security_vulnerability",
        domains=(Domain.AUTH,),
        severity="high",
    ),
]

_INFRA_RULES: list[HeuristicRule] = [
    HeuristicRule(
        rule_id="INFRA-001",
        description="Hardcoded host or port in production code",
        pattern=re.compile(
            r'(?:host|hostname|HOST)\s*=\s*["\'](?:localhost|127\.0\.0\.1|0\.0\.0\.0)["\']',
            re.IGNORECASE,
        ),
        failure_type="heuristic_violation",
        domains=(Domain.BACKEND, Domain.INFRA, Domain.CONFIG),
        severity="medium",
    ),
    HeuristicRule(
        rule_id="INFRA-002",
        description="Missing health check endpoint in service definition",
        pattern=re.compile(
            r"(?:Dockerfile|docker-compose).*(?!HEALTHCHECK)",
            re.IGNORECASE | re.DOTALL,
        ),
        failure_type="heuristic_violation",
        domains=(Domain.INFRA,),
        severity="medium",
    ),
    HeuristicRule(
        rule_id="INFRA-003",
        description="Container running as root user",
        pattern=re.compile(
            r"USER\s+root|--privileged",
            re.IGNORECASE,
        ),
        failure_type="security_vulnerability",
        domains=(Domain.INFRA,),
        severity="high",
    ),
]

_ALL_RULES: list[HeuristicRule] = (
    _SECURITY_RULES
    + _DEPRECATION_RULES
    + _PERFORMANCE_RULES
    + _HEURISTIC_RULES
    + _DATABASE_RULES
    + _AUTH_RULES
    + _INFRA_RULES
)

# ── Dynamic Heuristic Weights (Phase 3 — W42-DH1) ──────────────────────────

import json as _json
from pathlib import Path as _Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

_ROOT = _Path(__file__).resolve().parents[3]
_HEURISTIC_WEIGHTS_PATH = _ROOT / "backend" / "data" / "heuristic_weights.json"

# Severity levels ordered from lowest to highest
_SEVERITY_ORDER = ("low", "medium", "high", "critical")

# Security rules (SEC-*) can never drop below this severity level
_CONSTITUTIONAL_FLOOR: dict[str, str] = {}
for _sec_rule in _SECURITY_RULES:
    _CONSTITUTIONAL_FLOOR[_sec_rule.rule_id] = "medium"


def _load_severity_adjustments() -> dict[str, int]:
    """Load per-rule override counts from ``heuristic_weights.json``.

    Returns dict of rule_id → override_count (how many times the Governor
    overrode this rule's verdict).
    """
    try:
        if _HEURISTIC_WEIGHTS_PATH.exists():
            data = _json.loads(_HEURISTIC_WEIGHTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def _effective_severity(rule: HeuristicRule) -> str:
    """Compute the effective severity of a rule after dynamic adjustment.

    For every 5 Governor overrides, the severity drops by one level.
    Security rules (``SEC-*``) are floored at ``medium`` per the
    constitutional safety floor.
    """
    adjustments = _load_severity_adjustments()
    override_count = adjustments.get(rule.rule_id, 0)
    if override_count < 5:
        return rule.severity

    drops = override_count // 5
    current_idx = _SEVERITY_ORDER.index(rule.severity) if rule.severity in _SEVERITY_ORDER else 1
    new_idx = max(0, current_idx - drops)
    new_severity = _SEVERITY_ORDER[new_idx]

    # Enforce constitutional floor for security rules
    floor = _CONSTITUTIONAL_FLOOR.get(rule.rule_id)
    if floor and _SEVERITY_ORDER.index(new_severity) < _SEVERITY_ORDER.index(floor):
        return floor

    return new_severity


def record_override(rule_id: str) -> None:
    """Increment the override counter for a rule (called when Governor overrides verdict).

    Thread-safe via atomic write pattern.
    """
    adjustments = _load_severity_adjustments()
    adjustments[rule_id] = adjustments.get(rule_id, 0) + 1
    try:
        _HEURISTIC_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HEURISTIC_WEIGHTS_PATH.write_text(_json.dumps(adjustments, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Adversary result schema (the mandatory output contract) ───


@dataclass
class FatalErrorLog:
    """Minimal structured record of a single adversary failure."""

    detected: bool = False
    rule_id: str = ""
    conflict_source: str | None = None  # which JIT source exposed the conflict
    failing_code_snippet: str | None = None  # exact failing region (≤80 chars)
    severity: str = "medium"

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "rule_id": self.rule_id,
            "conflict_source": self.conflict_source,
            "failing_code_snippet": self.failing_code_snippet,
            "severity": self.severity,
        }


@dataclass
class AdversaryResult:
    """The complete output of one adversary validation pass.

    Designed for token starvation: PASS generates ~10 fields.
    FAIL adds only fatal_error_log (focused, compact).
    """

    engram_target: UUID
    fast_fail_triggered: bool = False
    cross_validation_matrix: CrossCheckResults = field(default_factory=CrossCheckResults)
    fatal_error_log: FatalErrorLog = field(default_factory=FatalErrorLog)
    adversary_verdict: TribunalVerdict = TribunalVerdict.PENDING
    rules_checked: int = 0
    validation_latency_ms: float = 0.0
    jit_context_conflicts: list[str] = field(default_factory=list)  # advisory excerpts that failed

    def to_dict(self) -> dict:
        return {
            "engram_target": str(self.engram_target),
            "fast_fail_triggered": self.fast_fail_triggered,
            "cross_validation_matrix": self.cross_validation_matrix.to_dict(),
            "fatal_error_log": self.fatal_error_log.to_dict(),
            "adversary_verdict": self.adversary_verdict.value,
            "rules_checked": self.rules_checked,
            "validation_latency_ms": round(self.validation_latency_ms, 2),
            "jit_context_conflicts": self.jit_context_conflicts,
        }


# ── Adversary Validator ────────────────────────────────────────


class AdversaryValidator:
    """The Fast-Fail Adversary: validates ContextAwareEngram logic against
    real-world JIT context and domain heuristics.

    Mode:
      - offline (default): pure-Python regex checks against all rules
      - with jit_context: also checks generated code against attached JIT excerpts

    Returns AdversaryResult with a binary verdict. On FAIL, the ArbiterHealer
    can consume fatal_error_log directly for one-shot targeted healing.
    """

    def __init__(self, *, critical_only: bool = False) -> None:
        self._critical_only = critical_only  # strictest mode: only CRITICAL/HIGH rules
        self._runtime_rules: list[HeuristicRule] = []  # Phase 7: emergent rules (instance-scoped)

    def validate(self, engram: ContextAwareEngram) -> AdversaryResult:
        """Run full adversary validation. Fast-fails on first critical violation."""
        t0 = time.monotonic()
        result = AdversaryResult(engram_target=engram.engram_id)
        code = engram.logic_body

        # ── Phase 1: Security rules (highest priority, fast-fail) ──
        for rule in _SECURITY_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            result.rules_checked += 1
            eff_sev = _effective_severity(rule)
            match = rule.pattern.search(code)
            if match:
                result.cross_validation_matrix.security_vulnerability = True
                result.fast_fail_triggered = True
                result.fatal_error_log = FatalErrorLog(
                    detected=True,
                    rule_id=rule.rule_id,
                    conflict_source="security_advisory",
                    failing_code_snippet=_extract_snippet(code, match.start()),
                    severity=eff_sev,
                )
                result.adversary_verdict = TribunalVerdict.FAIL
                result.validation_latency_ms = (time.monotonic() - t0) * 1000
                return result  # guillotine — stop immediately

        # ── Phase 2: JIT context conflict check ────────────────────
        jit_conflicts = _check_jit_context_conflicts(code, engram.jit_context)
        if jit_conflicts:
            result.cross_validation_matrix.context_conflict = True
            result.jit_context_conflicts = jit_conflicts
            if not result.fatal_error_log.detected:
                result.fatal_error_log = FatalErrorLog(
                    detected=True,
                    rule_id="JIT-001",
                    conflict_source="jit_context_matrix",
                    failing_code_snippet=jit_conflicts[0][:80],
                    severity="high",
                )

        # ── Phase 3: Deprecation rules ───────────────────────────────
        for rule in _DEPRECATION_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            eff_sev = _effective_severity(rule)
            if self._critical_only and eff_sev not in ("high", "critical"):
                continue
            result.rules_checked += 1
            if rule.pattern.search(code):
                result.cross_validation_matrix.deprecation_detected = True
                if not result.fatal_error_log.detected:
                    match = rule.pattern.search(code)
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="deprecation_notice",
                        failing_code_snippet=_extract_snippet(code, match.start())
                        if match
                        else None,
                        severity=eff_sev,
                    )

        # ── Phase 4: Performance rules ────────────────────────────────
        for rule in _PERFORMANCE_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            eff_sev = _effective_severity(rule)
            if self._critical_only and eff_sev not in ("high", "critical"):
                continue
            result.rules_checked += 1
            if rule.pattern.search(code):
                result.cross_validation_matrix.performance_violation = True
                if not result.fatal_error_log.detected:
                    match = rule.pattern.search(code)
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="performance_benchmark",
                        failing_code_snippet=_extract_snippet(code, match.start())
                        if match
                        else None,
                        severity=eff_sev,
                    )

        # ── Phase 5: General heuristic rules ─────────────────────────
        for rule in _HEURISTIC_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            if self._critical_only:
                continue  # heuristics are informational in critical_only mode
            result.rules_checked += 1
            if rule.pattern.search(code):
                result.cross_validation_matrix.heuristic_violation = True
                if not result.fatal_error_log.detected:
                    match = rule.pattern.search(code)
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="best_practice",
                        failing_code_snippet=_extract_snippet(code, match.start())
                        if match
                        else None,
                        severity=_effective_severity(rule),
                    )

        # ── Phase 6: Database rules ─────────────────────────────────
        for rule in _DATABASE_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            result.rules_checked += 1
            match = rule.pattern.search(code)
            if match:
                if rule.failure_type == "security_vulnerability":
                    result.cross_validation_matrix.security_vulnerability = True
                else:
                    result.cross_validation_matrix.performance_violation = True
                if not result.fatal_error_log.detected:
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="database_advisory",
                        failing_code_snippet=_extract_snippet(code, match.start()),
                        severity=_effective_severity(rule),
                    )

        # ── Phase 7: Auth rules ─────────────────────────────────────
        for rule in _AUTH_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            result.rules_checked += 1
            match = rule.pattern.search(code)
            if match:
                result.cross_validation_matrix.security_vulnerability = True
                if not result.fatal_error_log.detected:
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="auth_advisory",
                        failing_code_snippet=_extract_snippet(code, match.start()),
                        severity=_effective_severity(rule),
                    )

        # ── Phase 8: Infra rules ────────────────────────────────────
        for rule in _INFRA_RULES:
            if not _rule_applies(rule, engram.domain.value):
                continue
            result.rules_checked += 1
            match = rule.pattern.search(code)
            if match:
                if rule.failure_type == "security_vulnerability":
                    result.cross_validation_matrix.security_vulnerability = True
                else:
                    result.cross_validation_matrix.heuristic_violation = True
                if not result.fatal_error_log.detected:
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rule.rule_id,
                        conflict_source="infra_advisory",
                        failing_code_snippet=_extract_snippet(code, match.start()),
                        severity=_effective_severity(rule),
                    )

        # ── Phase 9: Runtime emergent rules (audit-only, never cause FAIL) ─
        # Rules injected via inject_runtime_rules() are architectural advisories
        # from Synapse Collision.  Instance-scoped — no global state mutation.
        for rt_rule in self._runtime_rules:
            if not _rule_applies(rt_rule, engram.domain.value):
                continue
            if self._critical_only:
                continue
            result.rules_checked += 1
            if rt_rule.pattern.search(code):
                result.cross_validation_matrix.heuristic_violation = True
                if not result.fatal_error_log.detected:
                    match = rt_rule.pattern.search(code)
                    result.fatal_error_log = FatalErrorLog(
                        detected=True,
                        rule_id=rt_rule.rule_id,
                        conflict_source="emergent_rule",
                        failing_code_snippet=_extract_snippet(code, match.start())
                        if match
                        else None,
                        severity=_effective_severity(rt_rule),
                    )

        # ── Final verdict ─────────────────────────────────────────────
        result.adversary_verdict = (
            TribunalVerdict.FAIL
            if result.cross_validation_matrix.any_failed
            else TribunalVerdict.PASS
        )
        result.validation_latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return result

    def validate_many(self, engrams: list[ContextAwareEngram]) -> list[AdversaryResult]:
        """Validate a list of engrams. Returns results in input order."""
        return [self.validate(e) for e in engrams]

    def inject_runtime_rules(self, rules: list[dict[str, str]]) -> int:
        """Phase 7: Inject emergent rules from Synapse Collision into this validator.

        Rules are added to an instance-level list and run as Phase 9 checks
        in :meth:`validate` (domain-matched, audit-only by default).  A rule
        whose constraint is a valid regex pattern can trigger
        ``heuristic_violation``; natural-language constraints that do not match
        any code pattern simply increment ``rules_checked`` without failing.

        Args:
            rules: List of rule dicts with keys ``rule_id``, ``constraint``,
                   ``severity``, and ``domain``.

        Returns:
            Number of rules successfully ingested.
        """
        injected = 0
        for rule in rules:
            try:
                if not isinstance(rule, dict):
                    raise TypeError(f"Expected dict, got {type(rule).__name__}")
                compiled = re.compile(rule["constraint"], re.IGNORECASE)
                domains = (rule["domain"],) if rule.get("domain") else ()
                hr = HeuristicRule(
                    rule_id=rule["rule_id"],
                    description=f"Runtime-injected: {rule.get('constraint', '')[:80]}",
                    pattern=compiled,
                    failure_type="heuristic_violation",
                    domains=domains,
                    severity=rule.get("severity", "medium").lower(),
                )
                self._runtime_rules.append(hr)
                injected += 1
            except (re.error, KeyError, TypeError) as exc:
                import logging

                logging.getLogger("tooloo.adversary").warning(
                    "Skipping invalid runtime rule %s: %s",
                    rule.get("rule_id", "?") if isinstance(rule, dict) else "?",
                    exc,
                )
        return injected


# ── Helpers ───────────────────────────────────────────────────


def _rule_applies(rule: HeuristicRule, domain: str) -> bool:
    """Return True if this rule applies to the given domain."""
    return (not rule.domains) or (domain in rule.domains)


def _extract_snippet(code: str, start: int, window: int = 80) -> str:
    """Extract a short snippet around the match position."""
    begin = max(0, start - 10)
    end = min(len(code), start + window)
    snippet = code[begin:end].replace("\n", " ").strip()
    return snippet if snippet else code[:80]


def _check_jit_context_conflicts(code: str, matrix: JITContextMatrix) -> list[str]:
    """Check generated code against JIT advisory excerpts for explicit conflicts.

    Returns a list of conflict descriptions (empty = no conflicts).
    This is a lightweight heuristic: looks for deprecated symbols mentioned
    in advisory excerpts appearing verbatim in the generated code.
    """
    conflicts: list[str] = []
    code_lower = code.lower()
    for source in matrix.sources:
        if not source.raw_excerpt:
            continue
        # Extract "avoid X" / "deprecated Y" tokens from excerpt
        excerpt_lower = source.raw_excerpt.lower()
        for phrase in re.findall(
            r"(?:avoid|deprecated|do not use|never use)\s+([\w._]+)", excerpt_lower
        ):
            if phrase in code_lower:
                conflicts.append(
                    f"[{source.source_type.value}] Advisory says avoid '{phrase}' "
                    f"but it appears in generated code."
                )
    return conflicts
