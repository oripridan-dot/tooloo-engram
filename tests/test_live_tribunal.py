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
from experiments.project_engram.engram.delta_sync import DeltaSyncBus, MutationEventType
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


# ═══════════════════════════════════════════════════════════════
#  SECTION 4 — WebSocket Delta-Sync payload tests
# ═══════════════════════════════════════════════════════════════

class TestDeltaSyncWebSocketPayload:
    """Validates ENGRAM_MUTATION_COMMIT and ENGRAM_MUTATION_PENDING payloads.

    These tests verify the UI-facing contract: the DeltaSyncBus must emit
    well-formed event envelopes that the WebSocket client can consume for a
    hot-swap without a page reload.

    The mock-mode variants run without any API key.
    The @live_only variants force a real heal cycle with Gemini.
    """

    # ── Mock-mode: pure structural validation ─────────────────

    def test_pending_payload_emitted_on_adversary_fail_mock(self) -> None:
        """ENGRAM_MUTATION_PENDING must be emitted when adversary flags a violation."""
        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )

        pending_events = []
        bus = DeltaSyncBus()
        bus.subscribe(
            MutationEventType.ENGRAM_MUTATION_PENDING,
            lambda e: pending_events.append(e),
        )

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=MockArbiterLLM()),
            bus=bus,
            max_heal_cycles=3,
        )

        graph = EngramGraph(decay_radius=3)
        poisoned = ContextAwareEngram(
            intent="Hardcoded secret — adversary must catch it",
            ast_signature="def get_api_key():",
            logic_body='api_key = "sk-hardcoded-1234abcdef"',
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="config/secrets.py",
            mandate_level="L1",
        )
        graph.add_engram(poisoned)
        tribunal.run(graph, poisoned)

        assert len(pending_events) >= 1, (
            "ENGRAM_MUTATION_PENDING must be emitted when adversary fails"
        )
        payload = pending_events[0].payload
        assert "target_engrams" in payload, "Payload must have target_engrams"
        assert "mutation_reason" in payload, "Payload must have mutation_reason"
        assert "ui_directive" in payload, "Payload must have ui_directive"
        assert payload["ui_directive"] == "soft_lock", (
            f"Expected ui_directive=soft_lock, got {payload['ui_directive']!r}"
        )

    def test_commit_payload_structure_on_heal_mock(self) -> None:
        """ENGRAM_MUTATION_COMMIT payload must have all UI-required fields after mock heal."""
        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )

        commit_events = []
        bus = DeltaSyncBus()
        bus.subscribe(
            MutationEventType.ENGRAM_MUTATION_COMMIT,
            lambda e: commit_events.append(e),
        )

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=MockArbiterLLM()),
            bus=bus,
            max_heal_cycles=2,
        )

        graph = EngramGraph(decay_radius=3)
        poisoned = ContextAwareEngram(
            intent="Hardcoded secret — mock arbiter will heal it",
            ast_signature="def get_secret():",
            logic_body='SECRET = "hardcoded_value_here"',
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="auth/secret.py",
            mandate_level="L1",
        )
        graph.add_engram(poisoned)
        result = tribunal.run(graph, poisoned)

        assert result.heal_cycles >= 1, "Poisoned engram must have triggered at least 1 heal"
        assert len(commit_events) >= 1, (
            "ENGRAM_MUTATION_COMMIT must be emitted after a successful heal"
        )

        # ── Validate UI contract fields ──────────────────────
        commit_payload = commit_events[0].payload
        required_top_level = {"dropped_nodes", "upserted_nodes", "repointed_edges",
                               "heal_latency_ms", "heal_cycle"}
        missing = required_top_level - set(commit_payload.keys())
        assert not missing, (
            f"COMMIT payload missing UI-required fields: {missing}\n"
            f"Got keys: {set(commit_payload.keys())}"
        )
        assert isinstance(commit_payload["dropped_nodes"], list), (
            "dropped_nodes must be a list (node IDs the UI should remove)"
        )
        assert isinstance(commit_payload["upserted_nodes"], list), (
            "upserted_nodes must be a list of healed engram descriptors"
        )
        assert commit_payload["heal_cycle"] >= 1, "heal_cycle must be ≥ 1"
        assert commit_payload["heal_latency_ms"] >= 0.0, (
            "heal_latency_ms must be non-negative"
        )
        # Each upserted node must carry the fields the UI BrandVault component needs
        for node in commit_payload["upserted_nodes"]:
            for field_name in ("engram_id", "domain", "intent", "module_path",
                               "mandate_level", "tribunal_verdict", "confidence_score"):
                assert field_name in node, (
                    f"upserted_node missing UI field '{field_name}': {node}"
                )

    def test_no_commit_emitted_on_clean_engram_mock(self) -> None:
        """A clean engram that passes adversary must NOT emit COMMIT (no heal needed)."""
        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )

        commit_events = []
        bus = DeltaSyncBus()
        bus.subscribe(
            MutationEventType.ENGRAM_MUTATION_COMMIT,
            lambda e: commit_events.append(e),
        )

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=MockArbiterLLM()),
            bus=bus,
            max_heal_cycles=2,
        )

        graph = EngramGraph(decay_radius=3)
        clean = ContextAwareEngram(
            intent="Clean rate limiter — no violations",
            ast_signature="def rate_limit(max_req: int, window_s: int):",
            logic_body=(
                "import os\nfrom datetime import UTC, datetime\n\n"
                "def rate_limit(max_req: int, window_s: int):\n"
                "    limit = int(os.environ.get('RATE_LIMIT', max_req))\n"
                "    return limit\n"
            ),
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="middleware/rate_limit.py",
            mandate_level="L1",
        )
        graph.add_engram(clean)
        result = tribunal.run(graph, clean)

        assert result.passed, "Clean engram must pass"
        assert result.heal_cycles == 0, "Clean engram must require zero heal cycles"
        assert len(commit_events) == 0, (
            "COMMIT must NOT be emitted for a clean engram (no mutation occurred)"
        )

    def test_event_log_replay_buffer_populated(self) -> None:
        """DeltaSyncBus replay buffer must contain recent events for reconnect hydration."""
        from experiments.project_engram.engram.arbiter import ArbiterHealer, MockArbiterLLM
        from experiments.project_engram.engram.jit_context import (
            JITContextAnchor,
            MockContextFetcher,
        )

        bus = DeltaSyncBus()
        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=MockContextFetcher()),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=MockArbiterLLM()),
            bus=bus,
            max_heal_cycles=2,
        )

        graph = EngramGraph(decay_radius=3)
        poisoned = ContextAwareEngram(
            intent="Inject flaw to trigger event log",
            ast_signature="def bad():",
            logic_body='token = "hardcoded_jwt_secret"',
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path="auth/token.py",
            mandate_level="L2",
        )
        graph.add_engram(poisoned)
        tribunal.run(graph, poisoned)

        recent = bus.get_recent_events(limit=20)
        assert len(recent) >= 1, (
            "Replay buffer must be non-empty after a tribunal run with events"
        )
        event_types = {e.event_type for e in recent}
        assert MutationEventType.ENGRAM_MUTATION_PENDING in event_types, (
            "Replay buffer must contain the PENDING event for reconnect hydration"
        )
        # Verify each event has a serialisable to_dict() — required for WS fan-out
        for event in recent:
            d = event.to_dict()
            assert "event_type" in d
            assert "timestamp" in d
            assert "event_id" in d
            assert "payload" in d

    # ── Live-mode: real Gemini heal cycle + WS payload ─────────

    @live_only
    def test_commit_payload_from_live_healed_engram(self) -> None:
        """COMMIT payload after live Gemini heal must have fully-populated upserted_nodes.

        This is the critical UI-severance test: the payload the WebSocket emits
        must allow the frontend to hot-swap the broken engram without a page reload.
        Forces an L4-level mandate so the heal is non-trivial.
        """
        from training_camp.scenarios import get_scenarios

        llm = LiveLLM()
        bus = DeltaSyncBus()
        commit_events = []
        bus.subscribe(
            MutationEventType.ENGRAM_MUTATION_COMMIT,
            lambda e: commit_events.append(e),
        )

        tribunal = TribunalOrchestrator(
            anchor=JITContextAnchor(fetcher=LiveContextFetcher(llm=llm)),
            validator=AdversaryValidator(),
            healer=ArbiterHealer(llm=LiveArbiterLLM(llm=llm)),
            bus=bus,
            max_heal_cycles=3,
        )

        # Use an L4 scenario's adversary seed to guarantee a real heal
        l4_scenarios = [s for s in get_scenarios() if s.level.value == "L4" and s.adversary_seeds]
        assert l4_scenarios, "Need at least one L4 scenario with adversary seeds"
        scenario = l4_scenarios[0]
        seed = scenario.adversary_seeds[0]

        graph = EngramGraph(decay_radius=4)
        poisoned = ContextAwareEngram(
            intent=f"{scenario.title} [LIVE POISON:{seed.rule_id}]",
            ast_signature=f"def poisoned_{seed.rule_id.lower().replace('-', '_')}():",
            logic_body=seed.poisoned_code,
            domain=Domain.BACKEND,
            language=Language.PYTHON,
            module_path=f"live_test/{seed.rule_id}.py",
            mandate_level="L4",
        )
        graph.add_engram(poisoned)
        result = tribunal.run(graph, poisoned)

        assert result.passed, (
            f"Live tribunal failed to heal poisoned L4 engram: "
            f"rule={seed.rule_id}, cycles={result.heal_cycles}"
        )
        assert result.heal_cycles >= 1, "Live L4 poison must require at least 1 heal"
        assert len(commit_events) >= 1, (
            "ENGRAM_MUTATION_COMMIT must be emitted after live Gemini heal"
        )

        commit_payload = commit_events[0].payload
        assert len(commit_payload["dropped_nodes"]) == 1, (
            "Exactly one node must be dropped (the v1 broken engram)"
        )
        assert len(commit_payload["upserted_nodes"]) == 1, (
            "Exactly one node must be upserted (the v2 healed engram)"
        )

        healed_node = commit_payload["upserted_nodes"][0]
        # Verify all UI BrandVault fields are present
        for field_name in ("engram_id", "domain", "intent", "module_path",
                           "mandate_level", "tribunal_verdict", "confidence_score"):
            assert field_name in healed_node, (
                f"Live healed node missing UI field '{field_name}': {healed_node}"
            )

        assert healed_node["tribunal_verdict"] in ("PASS", "FAIL", "PENDING"), (
            f"tribunal_verdict must be a known value: {healed_node['tribunal_verdict']!r}"
        )
        assert 0.0 <= healed_node["confidence_score"] <= 100.0, (
            f"confidence_score out of range: {healed_node['confidence_score']}"
        )

        import json
        # Whole payload must be JSON-serialisable — required for WebSocket fan-out
        commit_json = json.dumps(commit_events[0].to_dict())
        assert len(commit_json) > 0, "COMMIT event must serialise to non-empty JSON"

        print(
            f"\n  [live delta-sync] {scenario.scenario_id} · rule={seed.rule_id} · "
            f"heal_cycles={result.heal_cycles} · "
            f"heal_latency={commit_payload['heal_latency_ms']:.1f}ms"
        )
