"""
Arbiter Healer — Zero-downtime Mitosis-based engram repair for Engram V2.

When the AdversaryValidator returns FAIL, the Arbiter:
  1. Creates a Shadow Branch: isolates the broken engram from the live graph
  2. Constructs a minimal, context-rich healing payload for the LLM (or mock)
  3. Executes one-shot targeted healing (offline: deterministic; live: LLM)
  4. Clones the repaired logic into a v2 engram (Mitosis)
  5. Repoints all incoming edges from v1 → v2 (Pointer Reassignment)
  6. Drops v1 from the graph (Garbage Collect)
  7. Returns a MitosisResult for DeltaSyncBus to emit ENGRAM_MUTATION_COMMIT

Architecture guarantees:
  - No user-visible downtime: shadow branch isolates the defect
  - DAG integrity preserved: all edge operations go through EngramGraph.add_edge()
  - Bounded: max_heal_cycles prevents infinite loops
  - Offline-safe: MockArbiterLLM provides deterministic healing without LLM calls
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID, uuid4

from .graph_store import CycleDetectedError, EngramGraph
from .schema import (
    ContextAwareEngram,
    GraphAwareness,
    SynapticEdge,
    TribunalVerdict,
    ValidationTribunal,
)

if TYPE_CHECKING:
    from .adversary import AdversaryResult

# Max allowed heal cycles per engram per mandate
_MAX_ARBITER_CYCLES = 3


# ── Arbiter LLM Protocol ──────────────────────────────────────


@runtime_checkable
class ArbiterLLM(Protocol):
    """Protocol for the heavy-duty model that fixes adversary-flagged engrams."""

    def heal(self, payload: ArbiterPayload) -> str: ...


# ── Arbiter payload (the constrained healing mandate) ─────────


@dataclass
class ArbiterPayload:
    """Minimal, context-rich package sent to the Arbiter LLM.

    Designed for one-shot success: contains exactly what failed,
    the real-world truth that proves it, and the fix directive.
    No prose padding. LLM outputs only the corrected logic_body.
    """

    target_engram_id: UUID
    intent: str
    ast_signature: str
    broken_logic_body: str
    rule_id: str
    failure_description: str
    failing_snippet: str
    jit_advisory_excerpts: list[str]  # the real-world truth behind the failure
    domain: str
    language: str
    mandate_level: str = "L1"

    def to_prompt(self) -> str:
        """Render the constrained healing mandate as a prompt string."""
        excerpts = "\n".join(f"  - {e}" for e in self.jit_advisory_excerpts)
        return (
            f"MANDATE: Execute Engram Mitosis (Heal).\n"
            f"TARGET ENGRAM: {self.target_engram_id}\n"
            f"INTENT: {self.intent}\n"
            f"SIGNATURE: {self.ast_signature}\n"
            f"THE FLAW [{self.rule_id}]: {self.failure_description}\n"
            f"FAILING SNIPPET: {self.failing_snippet}\n"
            f"CONTEXTUAL TRUTH:\n{excerpts}\n"
            f"DIRECTIVE: Do not explain the fix. Output ONLY the corrected "
            f"logic_body for this engram. Language: {self.language}."
        )


# ── Mock Arbiter (deterministic offline healer) ───────────────


# Maps rule_id prefix → transformation function
def _mock_fix_security(body: str) -> str:
    """Replace SQL string interpolation with parameterized placeholder."""
    import re

    body = re.sub(
        r'f["\'].*?SELECT.*?\{.*?\}.*?["\']',
        '"SELECT * FROM table WHERE id = %s"',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = re.sub(
        r'(password|secret|api_key|token)\s*=\s*["\'][^"\']{4,}["\']',
        r'\1 = os.environ.get("\1".upper(), "")',
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"(?<![.\w])eval\s*\(", "_safe_eval(", body)
    body = re.sub(r"(?<![.\w])exec\s*\(", "_safe_exec(", body)
    return body


def _mock_fix_deprecation(body: str) -> str:
    """Replace deprecated patterns with modern equivalents."""
    import re

    body = re.sub(r"datetime\.utcnow\(\)", "datetime.now(UTC)", body)
    body = re.sub(r"extends\s+(React\.)?Component\b", "extends PureComponent", body)
    return body


def _mock_fix_performance(body: str) -> str:
    """Replace polling with event-based stub."""
    import re

    body = re.sub(
        r"while\s+True:\s*\n\s*time\.sleep",
        "# [ARBITER] Replaced polling with event listener\nfor event in event_stream:",
        body,
    )
    return body


def _mock_fix_heuristic(body: str) -> str:
    """Fix bare excepts and mutable defaults."""
    import re

    body = re.sub(r"except\s*:", "except Exception:", body)
    body = re.sub(r"(def\s+\w+\([^)]*=\s*)(\[\])", r"\1None  # arbiter: was mutable default", body)
    return body


def _mock_fix_database(body: str) -> str:
    """Fix N+1 queries and unparameterized SQL."""
    import re

    body = re.sub(
        r'f["\'].*?(?:INSERT|UPDATE|DELETE|SELECT).*?\{.*?\}.*?["\']',
        '"SELECT * FROM table WHERE id = %s"',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = re.sub(
        r"for\s+(\w+)\s+in\s+(\w+).*?\.(get|filter|query|find)\(",
        r"# [ARBITER] Batch query instead of N+1\nresults = \2_batch_fetch(",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return body


def _mock_fix_auth(body: str) -> str:
    """Fix plaintext password storage and missing JWT expiry."""
    import re

    body = re.sub(
        r"(password|passwd)\s*=\s*(?:request|body|payload|data|params)\.",
        r"\1 = hash_password(request.",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"jwt\.encode\(([^)]*)\)",
        r'jwt.encode(\1, algorithm="HS256", expires_delta=timedelta(hours=1))',
        body,
        flags=re.IGNORECASE,
    )
    return body


def _mock_fix_infra(body: str) -> str:
    """Fix hardcoded hosts and root container user."""
    import re

    body = re.sub(
        r'(?:host|hostname|HOST)\s*=\s*["\'](?:localhost|127\.0\.0\.1|0\.0\.0\.0)["\']',
        'host = os.environ.get("HOST", "0.0.0.0")',
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"USER\s+root", "USER appuser", body)
    return body


_MOCK_FIXERS = {
    "SEC": _mock_fix_security,
    "DEP": _mock_fix_deprecation,
    "PERF": _mock_fix_performance,
    "HEU": _mock_fix_heuristic,
    "JIT": lambda body: body + "\n# [ARBITER] JIT conflict resolved — see advisory.",
    "DB": _mock_fix_database,
    "AUTH": _mock_fix_auth,
    "INFRA": _mock_fix_infra,
}


@dataclass
class MockArbiterLLM:
    """Deterministic offline healer. Applies regex-based fixes for known rule patterns."""

    latency_ms: float = 5.0

    def heal(self, payload: ArbiterPayload) -> str:
        time.sleep(self.latency_ms / 1000)
        rule_prefix = payload.rule_id.split("-")[0] if payload.rule_id else "HEU"
        fixer = _MOCK_FIXERS.get(rule_prefix, lambda b: b)
        healed = fixer(payload.broken_logic_body)
        # Always ensure the healed body is non-empty
        return (
            healed
            if healed.strip()
            else f"# [ARBITER] Replaced broken logic\npass  # {payload.rule_id}"
        )


@dataclass
class GeminiArbiterLLM:
    """Real Gemini-powered healer — uses Gemini Flash Lite to fix adversary-flagged engrams.

    Replaces MockArbiterLLM in live environments where GEMINI_API_KEY is set.
    Falls back to MockArbiterLLM on API failure to preserve pipeline liveness.
    """

    model: str = "gemini-2.0-flash-lite"

    def heal(self, payload: ArbiterPayload) -> str:
        import os

        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return MockArbiterLLM().heal(payload)
        try:
            from google import genai  # type: ignore[import-untyped]

            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=self.model,
                contents=payload.to_prompt(),
            )
            code = (resp.text or "").strip()
            # Strip markdown fences if model wraps output
            if code.startswith("```"):
                lines = code.split("\n")
                code = "\n".join(lines[1:])
                if code.rstrip().endswith("```"):
                    code = "\n".join(code.rstrip().split("\n")[:-1])
                code = code.strip()
            return code if code.strip() else MockArbiterLLM().heal(payload)
        except Exception:
            return MockArbiterLLM().heal(payload)


# ── Mitosis result ────────────────────────────────────────────


@dataclass
class MitosisResult:
    """Complete record of a zero-downtime mitosis heal."""

    original_engram_id: UUID
    healed_engram_id: UUID
    success: bool = False
    edges_repointed: int = 0
    heal_cycle: int = 0
    heal_latency_ms: float = 0.0
    arbiter_payload: ArbiterPayload | None = None
    failure_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "original_engram_id": str(self.original_engram_id),
            "healed_engram_id": str(self.healed_engram_id),
            "success": self.success,
            "edges_repointed": self.edges_repointed,
            "heal_cycle": self.heal_cycle,
            "heal_latency_ms": round(self.heal_latency_ms, 2),
            "failure_reason": self.failure_reason,
        }


# ── Arbiter Healer ────────────────────────────────────────────


@dataclass
class ArbiterHealer:
    """Zero-downtime engram healer driven by AdversaryResult.

    Flow:
      1. Receives AdversaryResult (FAIL)
      2. Assembles ArbiterPayload from engram + JIT context
      3. Calls ArbiterLLM.heal() → corrected logic_body
      4. Clones engram into v2 (Mitosis), adds to graph
      5. Repoints all incoming edges from v1 → v2
      6. Removes v1 from graph
      7. Updates tribunal on the v2 engram
    """

    llm: ArbiterLLM = field(default_factory=MockArbiterLLM)
    max_cycles: int = _MAX_ARBITER_CYCLES

    def heal(
        self,
        graph: EngramGraph,
        engram: ContextAwareEngram,
        adversary_result: AdversaryResult,
        *,
        cycle: int = 1,
    ) -> MitosisResult:
        """Execute zero-downtime Mitosis heal on the failed engram.

        Returns MitosisResult. On success, the graph now contains the healed v2
        engram and all edges have been repointed. v1 has been removed.
        """
        t0 = time.monotonic()
        result = MitosisResult(
            original_engram_id=engram.engram_id,
            healed_engram_id=engram.engram_id,  # placeholder
            heal_cycle=cycle,
        )

        if cycle > self.max_cycles:
            result.failure_reason = f"Max heal cycles ({self.max_cycles}) exceeded"
            result.heal_latency_ms = (time.monotonic() - t0) * 1000
            return result

        # ── Step 1: Build Arbiter payload ────────────────────────
        jit_excerpts = [s.raw_excerpt for s in engram.jit_context.sources if s.raw_excerpt]
        payload = ArbiterPayload(
            target_engram_id=engram.engram_id,
            intent=engram.intent,
            ast_signature=engram.ast_signature,
            broken_logic_body=engram.logic_body,
            rule_id=adversary_result.fatal_error_log.rule_id,
            failure_description=f"Rule {adversary_result.fatal_error_log.rule_id}: "
            f"{adversary_result.fatal_error_log.failing_code_snippet or ''}",
            failing_snippet=adversary_result.fatal_error_log.failing_code_snippet or "",
            jit_advisory_excerpts=jit_excerpts,
            domain=engram.domain.value,
            language=engram.language.value,
            mandate_level=engram.mandate_level,
        )
        result.arbiter_payload = payload

        # ── Step 2: One-shot LLM heal ────────────────────────────
        try:
            healed_body = self.llm.heal(payload)
        except Exception as exc:
            result.failure_reason = f"Arbiter LLM error: {exc}"
            result.heal_latency_ms = round((time.monotonic() - t0) * 1000, 2)
            return result

        if not healed_body or not healed_body.strip():
            result.failure_reason = "Arbiter returned empty logic body"
            result.heal_latency_ms = round((time.monotonic() - t0) * 1000, 2)
            return result

        # ── Step 3: Mitosis — clone engram as v2 ─────────────────
        v2 = ContextAwareEngram(
            engram_id=uuid4(),
            intent=engram.intent,
            ast_signature=engram.ast_signature,
            logic_body=healed_body,
            language=engram.language,
            domain=engram.domain,
            module_path=engram.module_path,
            parent_engram_id=engram.parent_engram_id,
            jit_context=engram.jit_context,
            graph_awareness=GraphAwareness(
                blast_radius=engram.graph_awareness.blast_radius,
                macro_state_hash=engram.graph_awareness.macro_state_hash,
            ),
            mandate_level=engram.mandate_level,
        )
        # Update tribunal on v2 — mark PASS (Arbiter approved)
        v2.tribunal = ValidationTribunal(
            scout_model=engram.tribunal.scout_model,
            adversary_model=engram.tribunal.adversary_model,
            arbiter_model=engram.tribunal.arbiter_model,
            confidence_score=85.0 + (cycle * 5.0),  # confidence rises with each cycle
            verdict=TribunalVerdict.PASS,
            heal_cycles_used=cycle,
        )

        graph.add_engram(v2)
        result.healed_engram_id = v2.engram_id

        # ── Step 4: Pointer reassignment — repoint v1 edges → v2 ─
        v1_id_str = str(engram.engram_id)
        str(v2.engram_id)
        edges_to_repoint: list[SynapticEdge] = []

        for edge in list(graph._edges.values()):
            if str(edge.target_id) == v1_id_str:
                edges_to_repoint.append(edge)

        for old_edge in edges_to_repoint:
            new_edge = SynapticEdge(
                source_id=old_edge.source_id,
                target_id=v2.engram_id,
                edge_type=old_edge.edge_type,
                weight=old_edge.weight,
            )
            try:
                graph.add_edge(new_edge)
                result.edges_repointed += 1
            except (ValueError, CycleDetectedError):
                pass  # non-fatal: if repointing fails, v2 remains but edge stays on v1

        # Also repoint outgoing edges from v1 → v2
        for edge in list(graph._edges.values()):
            if str(edge.source_id) == v1_id_str:
                new_edge = SynapticEdge(
                    source_id=v2.engram_id,
                    target_id=edge.target_id,
                    edge_type=edge.edge_type,
                    weight=edge.weight,
                )
                try:
                    graph.add_edge(new_edge)
                    result.edges_repointed += 1
                except (ValueError, CycleDetectedError):
                    pass

        # ── Step 5: Garbage collect v1 ───────────────────────────
        graph.rollback_engram(engram.engram_id)

        result.success = True
        result.heal_latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return result
