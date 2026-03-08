"""
Live Engram V2 Adapters — Wires the real Gemini LLM into the Tribunal pipeline.

Provides:
  LiveArbiterLLM     — ArbiterLLM protocol implementation backed by Gemini
  LiveContextFetcher — ContextFetcher protocol implementation using Gemini
                       for real-world knowledge synthesis (no raw HTTP scraping)

Both honour the existing Protocol interfaces so the TribunalOrchestrator
can use real or mock implementations without any changes.

Usage:
    from tooloo_engram.live_adapters import LiveArbiterLLM, LiveContextFetcher
    from experiments.project_engram.engram.tribunal_orchestrator import TribunalOrchestrator

    llm = LiveArbiterLLM()                    # reads GEMINI_API_KEY from env
    fetcher = LiveContextFetcher(llm=llm.llm) # re-uses same client
    tribunal = TribunalOrchestrator(
        anchor=JITContextAnchor(fetcher=fetcher),
        healer=ArbiterHealer(llm=llm),
    )
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Workspace-root on path
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from experiments.project_engram.engram.arbiter import ArbiterPayload
from experiments.project_engram.engram.jit_context import ContextFetcher
from experiments.project_engram.engram.schema import (
    ContextAwareEngram,
    Domain,
    JITSource,
    JITSourceType,
)
from experiments.project_engram.harness.live_llm import LiveLLM

# ── Domain → query templates for JIT context synthesis ──────

_DOMAIN_QUERIES: dict[Domain, list[tuple[JITSourceType, str]]] = {
    Domain.BACKEND: [
        (JITSourceType.SECURITY_ADVISORY, "OWASP Top 10 backend security vulnerabilities Python 2025 — list the top risks and how to prevent them"),
        (JITSourceType.BEST_PRACTICE, "Python async FastAPI backend best practices 2025 — concise bullet points"),
        (JITSourceType.DEPRECATION_NOTICE, "Python 3.12 deprecated APIs and breaking changes — list what to avoid"),
        (JITSourceType.PERFORMANCE_BENCHMARK, "FastAPI Python backend performance bottlenecks and remedies — concise list"),
    ],
    Domain.FRONTEND: [
        (JITSourceType.SECURITY_ADVISORY, "React XSS vulnerabilities and prevention techniques 2025 — concise list"),
        (JITSourceType.BEST_PRACTICE, "React hooks best practices 2025 — concise bullet points"),
        (JITSourceType.DEPRECATION_NOTICE, "React deprecated APIs 2025 — what to avoid"),
        (JITSourceType.PERFORMANCE_BENCHMARK, "React rendering performance best practices — concise list"),
    ],
    Domain.TEST: [
        (JITSourceType.BEST_PRACTICE, "pytest best practices 2025 — fixtures, parameterize, isolation patterns"),
        (JITSourceType.DEPRECATION_NOTICE, "pytest deprecated patterns — what to avoid"),
    ],
    Domain.CONFIG: [
        (JITSourceType.SECURITY_ADVISORY, "Configuration security: secrets in env vars, never hardcoded — best practices"),
        (JITSourceType.BEST_PRACTICE, "12-factor app configuration best practices — concise list"),
    ],
}

# Intent keyword → additional security advisory query
_INTENT_ADVISORY_QUERIES: dict[str, str] = {
    "jwt": "JWT token validation security vulnerabilities — hardcoded secrets, algorithm confusion, expiry bypass",
    "sql": "SQL injection prevention — parameterized queries, ORM safe patterns",
    "websocket": "WebSocket security vulnerabilities — origin validation, message sanitisation",
    "upload": "File upload security vulnerabilities — MIME validation, size limits, path traversal",
    "auth": "Authentication bypass vulnerabilities — session fixation, brute force, credential stuffing",
    "password": "Password storage security — bcrypt vs argon2, salting, rainbow tables",
    "crypto": "Cryptography pitfalls Python — weak algorithms, IV reuse, key exposure",
    "cache": "Cache poisoning vulnerabilities — key collision, TTL bypass",
    "rate": "Rate limiting bypass vulnerabilities — IP spoofing, header manipulation",
    "realtime": "WebSocket and SSE security — DDoS, message flooding, authentication",
}


@dataclass
class LiveContextFetcher:
    """Synthesises real-world JIT context using Gemini knowledge.

    Uses Gemini to generate authoritative, concise advisories for each
    domain/source_type combination. Results are deterministically hashed
    so the JITContextMatrix can detect changes.

    Falls back to MockContextFetcher excerpts if Gemini is unavailable.
    """

    llm: LiveLLM = field(default_factory=LiveLLM)
    budget_per_fetch_usd: float = 0.005  # ~1 Gemini flash call

    _SYSTEM = (
        "You are a senior security and software quality engineer. "
        "Output ONLY a bullet-point list of 4-6 concise, actionable facts. "
        "No prose, no headings, no preamble. Each bullet must be ≤ 20 words. "
        "Focus on what developers must AVOID or ALWAYS DO."
    )

    def fetch(
        self,
        source_type: JITSourceType,
        intent_keyword: str = "",
        domain: str = "backend",
    ) -> JITSource:
        """Synthesise a JITSource by querying Gemini for domain/type-specific advisories.

        Signature matches the ContextFetcher Protocol so this can be passed
        directly to JITContextAnchor(fetcher=LiveContextFetcher(...)).
        """
        # Coerce domain string to enum for query lookup (fall back to BACKEND)
        try:
            domain_enum = Domain(domain)
        except ValueError:
            domain_enum = Domain.BACKEND
        query = self._build_query(source_type, domain_enum, intent_keyword)
        t0 = time.monotonic()

        try:
            excerpt = self.llm.query(
                system=self._SYSTEM,
                prompt=query,
                model_tier="flash",
                temperature=0.1,
                max_output_tokens=256,
            )
        except Exception:
            # Graceful degradation
            excerpt = f"[LiveFetch unavailable] Use safe patterns for {source_type.value} in {domain}."

        latency_ms = (time.monotonic() - t0) * 1000
        content_hash = hashlib.sha256(excerpt.encode()).hexdigest()[:16]

        return JITSource(
            source_type=source_type,
            url=f"gemini://live-context/{domain}/{source_type.value}",
            version_locked="gemini-2.0-flash",
            content_hash=content_hash,
            raw_excerpt=excerpt[:500],
            ttl_hours=24,
        )

    def _build_query(self, source_type: JITSourceType, domain: Domain, intent_hint: str) -> str:
        """Build the Gemini query for this domain×source_type combination."""
        # Check if we have a pre-built query template
        domain_queries = _DOMAIN_QUERIES.get(domain, _DOMAIN_QUERIES[Domain.BACKEND])
        for stype, query in domain_queries:
            if stype == source_type:
                # Append intent hint if it matches a known advisory
                for keyword, advisory in _INTENT_ADVISORY_QUERIES.items():
                    if keyword in intent_hint.lower():
                        return f"{query}\n\nAdditional focus: {advisory}"
                return query
        # Fallback generic query
        return (
            f"Provide a concise security and quality advisory for "
            f"{source_type.value} in {domain.value} software development context. "
            f"Focus on what to AVOID. Intent: {intent_hint[:100]}"
        )

    def get_sources_for_domain(
        self,
        domain: Domain,
        intent_hint: str = "",
    ) -> list[JITSource]:
        """Fetch all JIT sources for a given domain."""
        queries = _DOMAIN_QUERIES.get(domain, _DOMAIN_QUERIES[Domain.BACKEND])
        return [
            self.fetch(source_type, intent_hint, domain.value)
            for source_type, _ in queries
        ]


# ── Live Arbiter LLM ──────────────────────────────────────────

_ARBITER_SYSTEM = """\
You are a precision code surgery engine. You receive a broken code snippet with:
- The exact rule it violates
- The contextual truth proving why it's wrong
- The directive to fix it

OUTPUT: Only the corrected logic_body — pure code, no markdown, no explanation.
The fix must be minimal and surgical. Do not rewrite unrelated code.
Language: {language}
"""

_ARBITER_FEW_SHOT = """\
EXAMPLES:
Input brokencode: api_key = "sk-hardcoded-1234"
Rule: SEC-002 (hardcoded secret)
Fixed: api_key = os.environ.get("API_KEY", "")

Input brokencode: ts = datetime.utcnow()
Rule: DEP-002 (deprecated utcnow)
Fixed: ts = datetime.now(UTC)

Input brokencode: except:\n    pass
Rule: HEU-001 (bare except)
Fixed: except Exception as e:\n    logger.warning("Error: %s", e)
"""


@dataclass
class LiveArbiterLLM:
    """Production Arbiter backed by Gemini — implements the ArbiterLLM protocol.

    Uses gemini-2.0-flash for speed. One-shot heal with few-shot examples
    and real JIT advisory context to maximise first-pass success rate.
    """

    llm: LiveLLM = field(default_factory=LiveLLM)
    model_tier: str = "flash"

    def heal(self, payload: ArbiterPayload) -> str:
        """Send the broken engram + advisory context to Gemini for one-shot repair."""
        system = _ARBITER_SYSTEM.format(language=payload.language)
        prompt = f"{_ARBITER_FEW_SHOT}\n\n{payload.to_prompt()}"

        healed = self.llm.query(
            system=system,
            prompt=prompt,
            model_tier=self.model_tier,
            temperature=0.05,  # near-deterministic for code surgery
            max_output_tokens=512,
        )
        # Strip markdown code fences if Gemini adds them
        if healed.startswith("```"):
            lines = healed.split("\n")
            inner = [
                l for l in lines
                if not l.startswith("```")
            ]
            healed = "\n".join(inner).strip()
        return healed
