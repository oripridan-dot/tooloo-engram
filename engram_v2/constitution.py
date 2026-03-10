"""
ConstitutionalGate — The supreme validation gate for graph mutations.

Enforces the 20 Constitutional Laws + OWASP Top 10 + license whitelist on
every LogicEngram before it may be persisted to the EngramGraph.

Architecture:
    SOTAGate            — mandatory blocking gate (intercepts before AdversaryValidator)
                          Checks freshness of JIT context; spins up ghost-fetch drone
                          if confidence < 0.85 or any source is expired.
    LicenseGate         — validates dependency licences against the whitelist
    OWASPGate           — scans logic_body for OWASP Top 10 patterns
    CapabilityMatrixGate — validates mutations against the DNA capability matrix
    ConstitutionalGate  — aggregates all gates; single call returns GateVerdict

SOTAGate integration with Vertex AI Vector Search / tooloo-memory:
    1. Extract framework signatures from engram.logic_body
    2. Look up matching .cog.json entries in JITContextAnchor (local PsycheBank cache)
    3. If stale (TTL expired) or confidence < SOTA_CONFIDENCE_THRESHOLD:
         → halt graph execution
         → dispatch GhostFetchDrone (ephemeral; no local file writes)
         → inject discovery into ContextTensor
         → route to SynapseCollisionEngine for auto-embedding (epigenetic infusion)
    4. Only after SOTA is fresh does the AdversaryValidator proceed

The SOTAGate makes the engine autopoietic (self-maintaining, self-expanding):
    - Lower latency over time: cached .cog.json retrieved in milliseconds next time
    - Smaller LLM footprint: validated SOTA patterns prevent hallucination
    - Self-healing baselines: stale memories are silently patched in background
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .schema import (
    ContextAwareEngram,
    ContextTensor,
    Domain,
    JITContextMatrix,
    JITSource,
    JITSourceType,
    LogicEngram,
    TribunalVerdict,
)

if TYPE_CHECKING:
    from .jit_context import JITContextAnchor

log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────

SOTA_CONFIDENCE_THRESHOLD = 0.85        # Law 19: circuit breaker
SOTA_FRESHNESS_TTL_HOURS = 24           # re-fetch if older than this
OWASP_CRITICAL_BLOCKS = True            # critical findings always block
LICENSE_BLOCK_ON_UNKNOWN = False        # unknown licences are flagged, not blocked


# ── OWASP Top 10 Patterns ─────────────────────────────────────

@dataclass(frozen=True)
class OWASPRule:
    rule_id: str
    owasp_category: str
    pattern: re.Pattern  # type: ignore[type-arg]
    severity: str        # critical | high | medium | low
    description: str


_NON_TEST_DOMAINS: tuple[str, ...] = (
    Domain.BACKEND, Domain.FRONTEND, Domain.AUTH,
    Domain.INFRA, Domain.API_CONTRACT, Domain.DATABASE, Domain.CONFIG,
)

_OWASP_RULES: list[OWASPRule] = [
    OWASPRule(
        "A01-BAC",
        "A01:2021-Broken Access Control",
        re.compile(r"is_admin\s*=\s*True|role\s*=\s*['\"]admin['\"]", re.I),
        "high",
        "Hardcoded privilege escalation — use RBAC",
    ),
    OWASPRule(
        "A02-CRYPTO",
        "A02:2021-Cryptographic Failures",
        re.compile(r"\bMD5\b|\bSHA1\b|hashlib\.(md5|sha1)\s*\(", re.I),
        "high",
        "Weak hash algorithm — use SHA-256 or Argon2",
    ),
    OWASPRule(
        "A03-SQLI",
        "A03:2021-Injection",
        re.compile(r"cursor\.execute\s*\(\s*[fF]['\"]|%\s*\(.*?\)|\.format\s*\("),
        "critical",
        "Potential SQL injection — use parameterised queries",
    ),
    OWASPRule(
        "A03-CMDI",
        "A03:2021-Injection",
        re.compile(r"\bos\.system\s*\(|\bsubprocess\.(call|run|Popen)\s*\(.*?shell\s*=\s*True"),
        "critical",
        "Shell injection via shell=True or os.system",
    ),
    OWASPRule(
        "A03-EVAL",
        "A03:2021-Injection",
        re.compile(r"\beval\s*\(|\bexec\s*\("),
        "critical",
        "eval()/exec() — code injection risk",
    ),
    OWASPRule(
        "A03-XSS",
        "A03:2021-Injection",
        re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML"),
        "high",
        "Direct innerHTML mutation — XSS risk, use sanitised methods",
    ),
    OWASPRule(
        "A05-MISCONFIG",
        "A05:2021-Security Misconfiguration",
        re.compile(r"DEBUG\s*=\s*True|CORS.*allow_origins\s*=\s*\[?\s*['\"]?\*", re.I),
        "medium",
        "Debug mode or wildcard CORS in non-test code",
    ),
    OWASPRule(
        "A06-OUTDATED",
        "A06:2021-Vulnerable Components",
        re.compile(r"pickle\.load|yaml\.load\s*\((?!.*Loader)"),
        "critical",
        "Unsafe deserialisation (pickle.load / yaml.load without SafeLoader)",
    ),
    OWASPRule(
        "A07-AUTH",
        "A07:2021-Authentication Failures",
        re.compile(r"password\s*=\s*['\"][^'\"]{6,}['\"]|JWT_SECRET\s*=\s*['\"][^'\"]+['\"]"),
        "high",
        "Hardcoded credential or secret",
    ),
    OWASPRule(
        "A10-SSRF",
        "A10:2021-SSRF",
        re.compile(r"requests\.get\s*\(\s*(?:url|request\.|f['\"]http)"),
        "medium",
        "Potential SSRF — validate and allowlist URLs before fetching",
    ),
]


# ── License Whitelist ─────────────────────────────────────────

_LICENSE_WHITELIST: frozenset[str] = frozenset({
    "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause",
    "isc", "0bsd", "unlicense", "cc0-1.0", "wtfpl", "zlib",
    "mpl-2.0", "lgpl-2.1", "lgpl-3.0", "artistic-2.0",
    "bsl-1.0", "postgresql", "python-2.0",
})

_LICENSE_REVIEW: frozenset[str] = frozenset({
    "gpl-2.0", "gpl-3.0", "agpl-3.0", "sspl-1.0",
    "eupl-1.1", "eupl-1.2", "cpal-1.0",
})

_LICENSE_BLOCK_REASONS: dict[str, str] = {
    "gpl-2.0":  "GPL-2.0 requires all derivative works to be open-sourced under the same license.",
    "gpl-3.0":  "GPL-3.0 (copyleft) requires derivative works to be open-sourced under GPL-3.0.",
    "agpl-3.0": "AGPL-3.0 requires open-sourcing even for network-accessed (SaaS) software.",
    "sspl-1.0": "SSPL restricts offering the software as a service without open-sourcing the stack.",
}

# Framework dependency extractor patterns
_IMPORT_PATTERNS = re.compile(
    r"(?:from|import)\s+([\w.]+)|require\s*\(['\"]([^'\"]+)['\"]\)",
    re.MULTILINE,
)


# ── Gate result types ─────────────────────────────────────────

class GateOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"       # non-blocking advisory
    SOTA_REQUIRED = "SOTA_REQUIRED"   # gate halted pending SOTA refresh


@dataclass
class OWASPFinding:
    rule_id: str
    owasp_category: str
    severity: str
    description: str
    snippet: str = ""
    line: int | None = None


@dataclass
class LicenseFinding:
    dependency: str
    license_id: str
    verdict: str        # "blocked" | "review" | "unknown"
    reason: str = ""


@dataclass
class SOTAGateResult:
    """Result of the SOTA freshness check."""

    outcome: GateOutcome
    checked_frameworks: list[str] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)
    new_context_injected: bool = False
    ghost_fetch_dispatched: bool = False
    confidence_before: float = 1.0
    confidence_after: float = 1.0
    latency_ms: float = 0.0


@dataclass
class GateVerdict:
    """
    The aggregated verdict from every gate in ConstitutionalGate.

    A single FAIL in any gate blocks the engram from being persisted.
    WARN findings are recorded but do not block.
    """

    verdict: GateOutcome
    engram_id: UUID
    owasp_findings: list[OWASPFinding] = field(default_factory=list)
    license_findings: list[LicenseFinding] = field(default_factory=list)
    sota_result: SOTAGateResult | None = None
    capability_violations: list[str] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def is_clean(self) -> bool:
        return self.verdict == GateOutcome.PASS and not self.owasp_findings

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "engram_id": str(self.engram_id),
            "is_clean": self.is_clean,
            "owasp_findings": [
                {
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "description": f.description,
                    "owasp_category": f.owasp_category,
                }
                for f in self.owasp_findings
            ],
            "license_findings": [
                {"dependency": lf.dependency, "license_id": lf.license_id, "verdict": lf.verdict}
                for lf in self.license_findings
            ],
            "sota_result": {
                "outcome": self.sota_result.outcome.value,
                "stale_sources": self.sota_result.stale_sources,
                "ghost_fetch_dispatched": self.sota_result.ghost_fetch_dispatched,
                "confidence_after": self.sota_result.confidence_after,
            } if self.sota_result else None,
            "capability_violations": self.capability_violations,
            "block_reasons": self.block_reasons,
            "warnings": self.warnings,
            "evaluated_at": self.evaluated_at,
        }


# ── DNA Capability Matrix (stub — reads from tooloo-dna in production) ───


# Default capability matrix — overridden by live tooloo-dna fetch in production
_DEFAULT_CAPABILITY_MATRIX: dict[str, list[str]] = {
    # Permitted operations per domain
    Domain.BACKEND:      ["read", "write", "delete", "execute_query"],
    Domain.FRONTEND:     ["read", "write", "render"],
    Domain.TEST:         ["read", "write", "execute_test"],
    Domain.CONFIG:       ["read", "write"],
    Domain.DATABASE:     ["read", "write", "delete", "execute_query", "migrate"],
    Domain.AUTH:         ["read", "write", "validate_token", "hash_password"],
    Domain.INFRA:        ["read", "write", "deploy", "scale"],
    Domain.API_CONTRACT: ["read", "write", "validate_schema"],
}

# Forbidden operations that no domain may ever grant
_GLOBALLY_FORBIDDEN: frozenset[str] = frozenset({
    "escalate_privilege",
    "bypass_auth",
    "write_to_host_filesystem",    # Law — no direct local file writes
    "exec_arbitrary_shell",
    "disable_owasp_gate",
})


# ── SOTAGate ──────────────────────────────────────────────────


class SOTAGate:
    """
    Mandatory blocking gate that intercepts before AdversaryValidator.

    Flow:
      1. Extract framework signatures from engram.logic_body
      2. Query JITContextAnchor for cached PsycheBank freshness
      3. If stale/missing → dispatch GhostFetchDrone → inject into ContextTensor
      4. Route new discoveries to SynapseCollisionEngine (epigenetic infusion)
      5. Return SOTAGateResult

    When outcome is SOTA_REQUIRED, the ConstitutionalGate halts the mutation
    until the async enrichment completes and the gate is re-run.
    """

    def __init__(
        self,
        jit_anchor: "JITContextAnchor | None" = None,
        synapse_engine: "SynapseCollisionEngineProtocol | None" = None,
    ) -> None:
        self._jit = jit_anchor
        self._synapse = synapse_engine

    def check(
        self,
        engram: LogicEngram,
        jit_matrix: JITContextMatrix | None = None,
    ) -> SOTAGateResult:
        """
        Run the SOTA freshness check for a single engram.

        Parameters
        ----------
        engram:     The engram to validate.
        jit_matrix: The current JIT context attached to this engram (if any).

        Returns
        -------
        SOTAGateResult — PASS if context is fresh, SOTA_REQUIRED if stale.
        """
        t0 = time.monotonic()
        frameworks = _extract_framework_signatures(engram.logic_body)
        result = SOTAGateResult(
            outcome=GateOutcome.PASS,
            checked_frameworks=frameworks,
        )

        # Assess freshness from attached JIT matrix
        stale: list[str] = []
        confidence = 1.0

        if jit_matrix:
            if jit_matrix.any_expired or jit_matrix.is_stale:
                stale = [s.source_type.value for s in jit_matrix.sources if s.is_expired]
                confidence = max(0.0, 1.0 - (len(stale) / max(1, len(jit_matrix.sources))))
        elif frameworks:
            # No JIT matrix at all and there are dependencies — treat as fully stale
            stale = frameworks
            confidence = 0.5

        result.stale_sources = stale
        result.confidence_before = confidence

        if confidence < SOTA_CONFIDENCE_THRESHOLD or stale:
            log.info(
                "SOTAGate: confidence=%.2f stale=%s — dispatching ghost fetch for engram %s",
                confidence,
                stale,
                engram.engram_id,
            )
            result.ghost_fetch_dispatched = True
            new_context = self._dispatch_ghost_fetch(engram, frameworks)

            if new_context and self._synapse:
                # Epigenetic infusion — embed new discovery into tooloo-memory
                self._synapse.embed_discovery(engram, new_context)
                result.new_context_injected = True
                result.confidence_after = 1.0
                result.outcome = GateOutcome.PASS
                log.info(
                    "SOTAGate: enrichment complete for engram %s — gate PASS",
                    engram.engram_id,
                )
            else:
                # Ghost fetch returned nothing useful — halt the mutation
                result.confidence_after = confidence
                result.outcome = GateOutcome.SOTA_REQUIRED
                log.warning(
                    "SOTAGate: no SOTA data available — mutation halted for engram %s",
                    engram.engram_id,
                )
        else:
            result.confidence_after = confidence

        result.latency_ms = (time.monotonic() - t0) * 1000
        return result

    def _dispatch_ghost_fetch(
        self, engram: LogicEngram, frameworks: list[str]
    ) -> str | None:
        """
        Dispatch an ephemeral GhostFetchDrone to retrieve live SOTA data.

        In production this calls core/engine/ghost_fetch.py GhostFetchDispatcher.
        Returns the raw SOTA text or None if retrieval fails.
        """
        # Production: delegate to GhostFetchDispatcher in DMZ drone
        # Here we return a stub that the SynapseCollisionEngine can embed
        if not frameworks:
            return None
        framework_list = ", ".join(frameworks[:5])
        log.debug("GhostFetchDrone stub: querying SOTA for %s", framework_list)
        # Return stub — real implementation routes to DMZ/drones/ghost_fetch_drone.py
        return (
            f"SOTA stub for [{framework_list}] — "
            f"real fetch would query Vertex AI Vector Search + live registries"
        )


# ── OWASPGate ─────────────────────────────────────────────────


class OWASPGate:
    """Scans logic_body for OWASP Top 10 patterns."""

    def scan(self, engram: LogicEngram) -> list[OWASPFinding]:
        """Return all OWASP findings. Test-domain engrams skip injection rules."""
        findings: list[OWASPFinding] = []
        body = engram.logic_body

        for rule in _OWASP_RULES:
            # Skip injection / credential checks for TEST domain
            if engram.domain == Domain.TEST and rule.rule_id in (
                "A03-EVAL", "A03-CMDI", "A03-SQLI", "A07-AUTH"
            ):
                continue
            for match in rule.pattern.finditer(body):
                line = body[: match.start()].count("\n") + 1
                findings.append(
                    OWASPFinding(
                        rule_id=rule.rule_id,
                        owasp_category=rule.owasp_category,
                        severity=rule.severity,
                        description=rule.description,
                        snippet=match.group(0)[:120],
                        line=line,
                    )
                )
        return findings


# ── LicenseGate ───────────────────────────────────────────────


class LicenseGate:
    """Validates dependency licences against the whitelist."""

    def check(self, dependencies: dict[str, str]) -> list[LicenseFinding]:
        """
        Parameters
        ----------
        dependencies: {package_name: license_spdx_id}

        Returns a list of LicenseFinding for anything not in the whitelist.
        """
        findings: list[LicenseFinding] = []
        for dep, lic in dependencies.items():
            lic_lower = lic.lower().strip()
            if lic_lower in _LICENSE_WHITELIST:
                continue
            if lic_lower in _LICENSE_REVIEW:
                findings.append(
                    LicenseFinding(
                        dependency=dep,
                        license_id=lic,
                        verdict="blocked",
                        reason=_LICENSE_BLOCK_REASONS.get(
                            lic_lower,
                            f"{lic} requires legal review before use.",
                        ),
                    )
                )
            else:
                if LICENSE_BLOCK_ON_UNKNOWN:
                    findings.append(
                        LicenseFinding(
                            dependency=dep,
                            license_id=lic,
                            verdict="unknown",
                            reason=f"License '{lic}' is not in the approved whitelist.",
                        )
                    )
                else:
                    findings.append(
                        LicenseFinding(
                            dependency=dep,
                            license_id=lic,
                            verdict="unknown",
                            reason=f"License '{lic}' requires manual approval.",
                        )
                    )
        return findings


# ── CapabilityMatrixGate ──────────────────────────────────────


class CapabilityMatrixGate:
    """
    Validates graph mutations against the DNA capability matrix.

    Reads from tooloo-dna in production (MCP call to capability endpoint).
    Falls back to _DEFAULT_CAPABILITY_MATRIX in offline/test mode.
    """

    def __init__(self, matrix: dict[str, list[str]] | None = None) -> None:
        self._matrix = matrix or _DEFAULT_CAPABILITY_MATRIX

    def validate(
        self,
        engram: LogicEngram,
        requested_ops: list[str] | None = None,
    ) -> list[str]:
        """
        Return a list of violations (empty = no violations).

        Checks:
          1. No globally forbidden operations
          2. Requested operations are permitted for the engram's domain
        """
        violations: list[str] = []
        ops = requested_ops or []

        for op in ops:
            if op in _GLOBALLY_FORBIDDEN:
                violations.append(
                    f"Globally forbidden operation '{op}' requested by engram {engram.engram_id}"
                )

        domain_perms = self._matrix.get(engram.domain.value, [])
        for op in ops:
            if op not in _GLOBALLY_FORBIDDEN and op not in domain_perms:
                violations.append(
                    f"Operation '{op}' is not permitted for domain '{engram.domain.value}'"
                )

        return violations


# ── SynapseCollisionEngineProtocol ───────────────────────────


class SynapseCollisionEngineProtocol:
    """
    Abstract interface — implemented by epigenetic_infusion.SynapseCollisionEngine.

    Defined here so ConstitutionalGate has no circular import.
    """

    def embed_discovery(self, engram: LogicEngram, sota_text: str) -> None:
        """Push a new SOTA discovery into tooloo-memory (Vertex AI Vector Search)."""
        raise NotImplementedError


# ── ConstitutionalGate (aggregator) ─────────────────────────


class ConstitutionalGate:
    """
    The supreme, non-negotiable validation gate.

    Every LogicEngram passes through all sub-gates before it may be
    persisted to the EngramGraph. A single FAIL blocks persistence.

    Gates (in order):
      1. SOTAGate        — mandatory SOTA freshness (halts if stale)
      2. OWASPGate       — OWASP Top 10 security scan
      3. LicenseGate     — dependency licence whitelist
      4. CapabilityGate  — DNA matrix compliance

    DESIGN: This gate is non-negotiable and cannot be overridden by any
    Law, Wave, or Mandate. Only constitutional amendment may modify it.
    """

    def __init__(
        self,
        jit_anchor: "JITContextAnchor | None" = None,
        synapse_engine: "SynapseCollisionEngineProtocol | None" = None,
        capability_matrix: dict[str, list[str]] | None = None,
    ) -> None:
        self._sota = SOTAGate(jit_anchor, synapse_engine)
        self._owasp = OWASPGate()
        self._license = LicenseGate()
        self._capability = CapabilityMatrixGate(capability_matrix)

    def evaluate(
        self,
        engram: LogicEngram,
        jit_matrix: JITContextMatrix | None = None,
        dependencies: dict[str, str] | None = None,
        requested_ops: list[str] | None = None,
    ) -> GateVerdict:
        """
        Run all gates against a LogicEngram.

        Parameters
        ----------
        engram:        The engram to evaluate.
        jit_matrix:    Attached JIT context (for SOTA freshness check).
        dependencies:  {package: spdx_license} dict (for LicenseGate).
        requested_ops: Operations this engram intends to perform (for CapabilityGate).

        Returns
        -------
        GateVerdict — PASS only if every gate passes.
        """
        verdict = GateVerdict(verdict=GateOutcome.PASS, engram_id=engram.engram_id)

        # ── Gate 1: SOTAGate (mandatory, blocking) ────────────
        sota_result = self._sota.check(engram, jit_matrix)
        verdict.sota_result = sota_result
        if sota_result.outcome == GateOutcome.SOTA_REQUIRED:
            verdict.verdict = GateOutcome.FAIL
            verdict.block_reasons.append(
                f"SOTAGate: SOTA context is stale for frameworks "
                f"{sota_result.stale_sources}. Awaiting ghost-fetch enrichment."
            )
            # Short-circuit — no point running further gates on stale context
            return verdict

        # ── Gate 2: OWASP ────────────────────────────────────
        findings = self._owasp.scan(engram)
        verdict.owasp_findings = findings
        critical = [f for f in findings if f.severity == "critical"]
        high = [f for f in findings if f.severity == "high"]
        if critical and OWASP_CRITICAL_BLOCKS:
            verdict.verdict = GateOutcome.FAIL
            for f in critical:
                verdict.block_reasons.append(f"OWASP {f.rule_id} (critical): {f.description}")
        elif high:
            for f in high:
                verdict.warnings.append(f"OWASP {f.rule_id} (high): {f.description}")

        # ── Gate 3: License ───────────────────────────────────
        if dependencies:
            lic_findings = self._license.check(dependencies)
            verdict.license_findings = lic_findings
            blocked_lic = [lf for lf in lic_findings if lf.verdict == "blocked"]
            if blocked_lic:
                verdict.verdict = GateOutcome.FAIL
                for lf in blocked_lic:
                    verdict.block_reasons.append(
                        f"License block [{lf.dependency}={lf.license_id}]: {lf.reason}"
                    )
            for lf in [lf for lf in lic_findings if lf.verdict == "unknown"]:
                verdict.warnings.append(
                    f"License review needed: {lf.dependency} ({lf.license_id})"
                )

        # ── Gate 4: Capability Matrix ─────────────────────────
        cap_violations = self._capability.validate(engram, requested_ops)
        verdict.capability_violations = cap_violations
        if cap_violations:
            verdict.verdict = GateOutcome.FAIL
            verdict.block_reasons.extend(cap_violations)

        if verdict.verdict != GateOutcome.FAIL and verdict.warnings:
            verdict.verdict = GateOutcome.WARN

        log.debug(
            "ConstitutionalGate: engram=%s verdict=%s blocks=%d warnings=%d",
            engram.engram_id,
            verdict.verdict,
            len(verdict.block_reasons),
            len(verdict.warnings),
        )
        return verdict


# ── Helpers ───────────────────────────────────────────────────


def _extract_framework_signatures(code: str) -> list[str]:
    """
    Extract top-level framework/library names from Python or TypeScript code.

    Used by SOTAGate to determine which PsycheBank entries to check.
    """
    frameworks: set[str] = set()
    for match in _IMPORT_PATTERNS.finditer(code):
        pkg = (match.group(1) or match.group(2) or "").strip()
        if pkg:
            root = pkg.split(".")[0].split("/")[0]
            # Skip stdlib-ish one-character or very short names
            if len(root) > 2:
                frameworks.add(root.lower())
    # Filter out common internal modules
    _INTERNAL = frozenset({"__future__", "typing", "dataclasses", "pathlib", "os",
                            "sys", "re", "json", "time", "logging", "threading",
                            "datetime", "uuid", "hashlib", "abc", "enum"})
    return sorted(frameworks - _INTERNAL)
