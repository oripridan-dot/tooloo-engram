"""
Live Tribunal Tests — Validates the full V2 tribunal pipeline with real Gemini calls.

These tests require:
  - GEMINI_API_KEY environment variable (from .env or shell)
  - Network access

Each test hits the actual Gemini 2.0 Flash API. Costs are tracked and capped.
Total expected cost for the full live suite: ~$0.01–$0.05.

Skipped automatically when GEMINI_API_KEY is not present.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# ── Path bootstrap ───────────────────────────────────────────
_workspace = Path(__file__).parent.parent.parent
_tooloo_engram_root = Path(__file__).parent.parent
for _p in [str(_workspace), str(_tooloo_engram_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── API key gate ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_workspace / ".env")
except ImportError:
    pass

_HAS_API_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("TOOLOO_GEMINI_API_KEY"))

live_only = pytest.mark.skipif(
    not _HAS_API_KEY,
    reason="GEMINI_API_KEY not set — skipping live Gemini tests",
)


# ── Imports ───────────────────────────────────────────────────
from live_adapters import LiveArbiterLLM, LiveContextFetcher
from training_camp.scenarios import ALL_SCENARIOS, get_scenarios

from experiments.project_engram.engram.adversary import AdversaryValidator
from experiments.project_engram.engram.arbiter import ArbiterHealer, ArbiterPayload
from experiments.project_engram.engram.delta_sync import DeltaSyncBus
from experiments.project_engram.engram.graph_store import EngramGraph
from experiments.project_engram.engram.jit_context import JITContextAnchor
from experiments.project_engram.engram.schema import (
    ContextAwareEngram,
    Domain,
    JITSourceType,
    Language,
)
from experiments.project_engram.engram.tribunal_orchestrator import TribunalOrchestrator
from experiments.project_engram.harness.live_llm import LiveLLM


def _make_payload(
    rule_id: str,
    failure_description: str,
    failing_snippet: str,
    advisory_excerpts: list[str],
    *,
    domain: str = "backend",
    language: str = "python",
    intent: str = "test engram",
) -> ArbiterPayload:
    """Helper to create a minimal ArbiterPayload for unit tests."""
    return ArbiterPayload(
        target_engram_id=uuid4(),
        intent=intent,
        ast_signature=f"def test_{rule_id.lower().replace('-', '_')}():",
        broken_logic_body=failing_snippet,
        rule_id=rule_id,
        failure_description=failure_description,
        failing_snippet=failing_snippet,
        jit_advisory_excerpts=advisory_excerpts,
        domain=domain,
        language=language,
    )


# ═══════════════════════════════════════════════════════════════
#  SECTION 1 — LiveArbiterLLM unit tests
# ═══════════════════════════════════════════════════════════════

class TestLiveArbiterLLM:

    @live_only
    def test_heals_hardcoded_secret(self) -> None:
        """LiveArbiterLLM must rewrite a hardcoded secret to env-var pattern."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)

        payload = _make_payload(
            rule_id="SEC-002",
            failure_description="Hardcoded secret detected",
            failing_snippet='secret = "my_super_secret_key"',
            advisory_excerpts=[
                "Secrets must never be hardcoded.",
                "Use os.environ.get(...) or a secrets vault.",
                "Hardcoded secrets are extracted by static analysis.",
            ],
            intent="JWT token validator",
        )

        healed = arbiter.heal(payload)

        assert healed.strip(), "Healed output must not be empty"
        assert "my_super_secret_key" not in healed, (
            f"Healed output still contains hardcoded secret: {healed!r}"
        )

    @live_only
    def test_heals_deprecated_utcnow(self) -> None:
        """LiveArbiterLLM must replace datetime.utcnow() with datetime.now(UTC)."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)

        payload = _make_payload(
            rule_id="DEP-002",
            failure_description="datetime.utcnow() is deprecated in Python 3.12+",
            failing_snippet="ts = datetime.utcnow()",
            advisory_excerpts=[
                "datetime.utcnow() is deprecated since Python 3.12.",
                "Use datetime.now(UTC) with from datetime import UTC.",
                "utcnow() returns a naive datetime — silently incorrect.",
            ],
            intent="Timestamp generator",
        )

        healed = arbiter.heal(payload)
        assert healed.strip()
        assert "utcnow" not in healed, (
            f"Healed output still contains deprecated utcnow: {healed!r}"
        )
        assert any(tok in healed for tok in ("UTC", "timezone", "tzinfo", "now(")), (
            f"Healed output does not use timezone-aware pattern: {healed!r}"
        )

    @live_only
    def test_heals_bare_except(self) -> None:
        """LiveArbiterLLM must replace bare except: pass with explicit catch."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)

        payload = _make_payload(
            rule_id="HEU-001",
            failure_description="Bare except clause silences all errors",
            failing_snippet="try:\n    do_work()\nexcept:\n    pass",
            advisory_excerpts=[
                "bare except: catches even SystemExit and KeyboardInterrupt.",
                "Always use except Exception as e and log or re-raise.",
            ],
            intent="Error handler",
        )

        healed = arbiter.heal(payload)
        assert healed.strip()
        assert "except:" not in healed or "except Exception" in healed, (
            f"Healed output still uses bare except: {healed!r}"
        )

    @live_only
    def test_output_has_no_markdown_fences(self) -> None:
        """LiveArbiterLLM must strip markdown code fences from the response."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)

        payload = _make_payload(
            rule_id="SEC-001",
            failure_description="XSS: dangerouslySetInnerHTML without sanitisation",
            failing_snippet='<div dangerouslySetInnerHTML={{__html: userInput}} />',
            advisory_excerpts=[
                "Never pass unsanitised user input to dangerouslySetInnerHTML.",
                "Use DOMPurify.sanitize() before assignment.",
            ],
            domain="frontend",
            language="tsx",
            intent="User profile renderer",
        )

        healed = arbiter.heal(payload)
        assert not healed.startswith("```"), (
            f"Healed output still has markdown fence: {healed[:80]!r}"
        )



# ═══════════════════════════════════════════════════════════════
#  SECTION 2 — LiveContextFetcher unit tests
# ═══════════════════════════════════════════════════════════════

class TestLiveContextFetcher:

    @live_only
    def test_backend_security_advisory(self) -> None:
        """Returns a non-empty JITSource for BACKEND × SECURITY_ADVISORY."""
        llm = LiveLLM()
        fetcher = LiveContextFetcher(llm=llm)

        result = fetcher.fetch(
            source_type=JITSourceType.SECURITY_ADVISORY,
            intent_keyword="jwt token validation",
            domain=Domain.BACKEND.value,
        )

        assert result.source_type == JITSourceType.SECURITY_ADVISORY
        assert len(result.raw_excerpt) >= 20, "Excerpt must be non-trivial"
        assert result.content_hash, "content_hash must be set"
        assert result.version_locked, "version_locked must be set"

    @live_only
    def test_database_best_practice(self) -> None:
        """Returns a non-empty JITSource for DATABASE × BEST_PRACTICE."""
        llm = LiveLLM()
        fetcher = LiveContextFetcher(llm=llm)

        result = fetcher.fetch(
            source_type=JITSourceType.BEST_PRACTICE,
            intent_keyword="sql query optimisation",
            domain=Domain.BACKEND.value,
        )

        assert result.source_type == JITSourceType.BEST_PRACTICE
        assert len(result.raw_excerpt) >= 20

    @live_only
    def test_frontend_deprecation_notice(self) -> None:
        """Returns a non-empty JITSource for FRONTEND × DEPRECATION_NOTICE."""
        llm = LiveLLM()
        fetcher = LiveContextFetcher(llm=llm)

        result = fetcher.fetch(
            source_type=JITSourceType.DEPRECATION_NOTICE,
            intent_keyword="",
            domain=Domain.FRONTEND.value,
        )

        assert result.source_type == JITSourceType.DEPRECATION_NOTICE
        assert len(result.raw_excerpt) >= 10

    @live_only
    def test_get_sources_for_domain_returns_multiple(self) -> None:
        """get_sources_for_domain should return multiple sources per domain."""
        llm = LiveLLM()
        fetcher = LiveContextFetcher(llm=llm)

        sources = fetcher.get_sources_for_domain(Domain.BACKEND, intent_hint="jwt")

        assert len(sources) >= 2, "Should return at least 2 sources per domain"
        # All should have trust_level set (default from schema)
        for s in sources:
            assert s.content_hash, "Each source must have a hash"

    @live_only
    def test_fetch_error_gracefully_degrades(self, monkeypatch) -> None:
        """If Gemini fails, LiveContextFetcher returns a degraded-but-valid JITSource."""
        llm = LiveLLM()
        fetcher = LiveContextFetcher(llm=llm)

        # Force a failure
        def boom(*args, **kwargs):
            raise RuntimeError("simulated Gemini failure")

        monkeypatch.setattr(llm, "query", boom)

        result = fetcher.fetch(
            source_type=JITSourceType.SECURITY_ADVISORY,
            intent_keyword="",
            domain=Domain.BACKEND.value,
        )
        assert result.source_type == JITSourceType.SECURITY_ADVISORY
        assert "unavailable" in result.raw_excerpt.lower() or len(result.raw_excerpt) >= 4


# ═══════════════════════════════════════════════════════════════
#  SECTION 3 — Full TribunalOrchestrator with live adapters
# ═══════════════════════════════════════════════════════════════

class TestLiveTribunal:

    @live_only
    def test_l1_01_jwt_tribunal_live(self) -> None:
        """Full tribunal pipeline on L1-01 with live Gemini arbiter."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)
        fetcher = LiveContextFetcher(llm=llm)

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=fetcher),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=arbiter),
            bus=DeltaSyncBus(),
            max_heal_cycles=2,
        )

        graph = EngramGraph(decay_radius=3)
        engram = ContextAwareEngram(
            intent="JWT token validator — backend",
            ast_signature="def validate_jwt(token: str) -> dict:",
            logic_body=(
                "import jwt\n"
                "SECRET = os.environ.get('JWT_SECRET', '')\n"
                "def validate_jwt(token: str) -> dict:\n"
                "    return jwt.decode(token, SECRET, algorithms=['HS256'])\n"
            ),
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="auth/jwt.py",
            mandate_level="L1",
        )
        graph.add_engram(engram)

        result = tribunal.run(graph, engram)
        assert result.passed, (
            f"L1-01 clean engram failed: adversary_rules={result.adversary_rules_checked}, "
            f"heal_cycles={result.heal_cycles}"
        )

    @live_only
    def test_poisoned_engram_caught_and_healed_live(self) -> None:
        """A hardcoded secret must be caught by adversary and healed by live Gemini."""
        llm = LiveLLM()
        arbiter = LiveArbiterLLM(llm=llm)
        fetcher = LiveContextFetcher(llm=llm)

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=fetcher),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=arbiter),
            bus=DeltaSyncBus(),
            max_heal_cycles=3,
        )

        graph = EngramGraph(decay_radius=3)
        poisoned = ContextAwareEngram(
            intent="L1-01 POISONED — hardcoded secret",
            ast_signature="def get_secret():",
            logic_body='secret = "my_super_secret_key_hardcoded"',
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="auth/secret.py",
            mandate_level="L1",
        )
        graph.add_engram(poisoned)

        result = tribunal.run(graph, poisoned)
        # Adversary must flag the hardcoded secret (result passes only after healer fixes it)
        assert result.adversary_rules_checked >= 1, (
            "Adversary must have checked the engram"
        )
        # After healing cycles: final must pass
        assert result.passed, (
            f"Poisoned engram not healed within {result.heal_cycles} cycles: "
            f"heal_cycles={result.heal_cycles}"
        )

    @live_only
    def test_live_tribunal_cost_stays_under_budget(self) -> None:
        """Running 3 tribunal calls must stay under $0.10 total cost."""
        llm = LiveLLM(budget_cap_usd=0.10)

        arbiter = LiveArbiterLLM(llm=llm)
        fetcher = LiveContextFetcher(llm=llm)

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=fetcher),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=arbiter),
            bus=DeltaSyncBus(),
            max_heal_cycles=1,
        )

        scenarios = get_scenarios(level=None)[:3]  # L1-01, L1-02, L1-03

        for s in scenarios:
            graph = EngramGraph(decay_radius=3)
            engram = ContextAwareEngram(
                intent=s.title,
                ast_signature=f"def {s.scenario_id.lower().replace('-', '_')}():",
                logic_body=f"# {s.mandate_text[:60]}\npass",
                domain=Domain(s.domain_mix[0])
                if s.domain_mix[0] in Domain.__members__.values()
                else Domain.BACKEND,
                language=Language.PYTHON,
                module_path=f"{s.scenario_id.lower()}.py",
                mandate_level=s.level.value,
            )
            graph.add_engram(engram)
            tribunal.run(graph, engram)

        total_cost = llm.total_cost
        assert total_cost <= 0.10, (
            f"Live tribunal cost exceeded $0.10: ${total_cost:.4f}"
        )
        print(f"\n  [live budget] total cost for 3 scenarios: ${total_cost:.5f}")
