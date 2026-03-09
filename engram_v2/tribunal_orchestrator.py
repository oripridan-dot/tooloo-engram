"""
Tribunal Orchestrator — The V2 Three-Model Validation Pipeline.

Combines JIT Context Anchor + Adversary Validator + Arbiter Healer +
Delta Sync Bus into a single, coherent pipeline for every ContextAwareEngram.

This is the core V2 differentiator: before any engram is committed to the graph,
it passes through the full tribunal. The user sees only validated, reality-anchored
nodes — never the intermediate failure states.

Pipeline:
    ContextAwareEngram
        ↓ JITContextAnchor.anchor()        [Reality Anchor]
        ↓ AdversaryValidator.validate()    [Fast-Fail Check]
        ↓ (FAIL) → ArbiterHealer.heal()    [Zero-Downtime Mitosis]
        ↓ DeltaSyncBus events              [UI Delta Sync]
        → committed ContextAwareEngram
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .adversary import AdversaryValidator
from .arbiter import ArbiterHealer, MockArbiterLLM
from .delta_sync import DeltaSyncBus
from .jit_context import JITContextAnchor, MockContextFetcher
from .schema import ContextAwareEngram, TribunalVerdict

if TYPE_CHECKING:
    from uuid import UUID

    from .graph_store import EngramGraph


@dataclass
class TribunalRunResult:
    """Full record of a single engram's tribunal run."""

    engram_id: UUID
    final_engram_id: UUID  # may differ from engram_id if mitosis occurred
    passed: bool = False
    heal_cycles: int = 0
    jit_sources_added: int = 0
    adversary_rules_checked: int = 0
    total_latency_ms: float = 0.0
    pipeline_stages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engram_id": str(self.engram_id),
            "final_engram_id": str(self.final_engram_id),
            "passed": self.passed,
            "heal_cycles": self.heal_cycles,
            "jit_sources_added": self.jit_sources_added,
            "adversary_rules_checked": self.adversary_rules_checked,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "pipeline_stages": self.pipeline_stages,
        }


class TribunalOrchestrator:
    """Drives any ContextAwareEngram through the full V2 validation tribunal.

    Stateless: does not hold graph references. The caller passes the graph
    per invocation, consistent with Law 17 (Stateless Agent Fluidity).
    """

    def __init__(
        self,
        *,
        anchor: JITContextAnchor | None = None,
        validator: AdversaryValidator | None = None,
        healer: ArbiterHealer | None = None,
        bus: DeltaSyncBus | None = None,
        max_heal_cycles: int = 3,
    ) -> None:
        self._anchor = anchor or JITContextAnchor(fetcher=MockContextFetcher())
        self._validator = validator or AdversaryValidator()
        self._healer = healer or ArbiterHealer(llm=MockArbiterLLM())
        self._bus = bus or DeltaSyncBus()
        self._max_heal_cycles = max_heal_cycles

    def run(
        self,
        graph: EngramGraph,
        engram: ContextAwareEngram,
    ) -> TribunalRunResult:
        """Run the full tribunal pipeline for a single engram.

        Side effects:
          - engram.jit_context is populated
          - If adversary fails: healed v2 replaces v1 in graph
          - DeltaSyncBus events are emitted at each stage
        """
        t0 = time.monotonic()
        run_result = TribunalRunResult(
            engram_id=engram.engram_id,
            final_engram_id=engram.engram_id,
        )

        # Stage 1: JIT Reality Anchor
        anchor_result = self._anchor.anchor(engram)
        run_result.jit_sources_added = anchor_result.sources_added
        run_result.pipeline_stages.append(f"JIT_ANCHOR:sources={anchor_result.sources_added}")

        # Stage 2: Adversary validation
        adv_result = self._validator.validate(engram)
        run_result.adversary_rules_checked = adv_result.rules_checked
        run_result.pipeline_stages.append(
            f"ADVERSARY:{adv_result.adversary_verdict.value}:rules={adv_result.rules_checked}"
        )

        if adv_result.adversary_verdict == TribunalVerdict.PASS:
            # Happy path: commit immediately
            engram.tribunal.verdict = TribunalVerdict.PASS
            engram.tribunal.confidence_score = 95.0
            run_result.passed = True
            run_result.total_latency_ms = round((time.monotonic() - t0) * 1000, 2)
            return run_result

        # Stage 3: Adversary FAIL → emit PENDING + initiate Mitosis heal
        self._bus.emit_pending(
            [engram.engram_id],
            reason=adv_result.fatal_error_log.rule_id or "adversary_fail",
        )
        run_result.pipeline_stages.append("DELTA_SYNC:PENDING")

        current_engram = engram
        for cycle in range(1, self._max_heal_cycles + 1):
            mitosis = self._healer.heal(graph, current_engram, adv_result, cycle=cycle)
            run_result.heal_cycles = cycle
            run_result.pipeline_stages.append(
                f"ARBITER_HEAL:cycle={cycle}:success={mitosis.success}"
            )

            if mitosis.success:
                # Get the healed v2 from graph
                v2 = graph.get_engram(mitosis.healed_engram_id)
                v2_ctx = v2 if isinstance(v2, ContextAwareEngram) else None
                self._bus.emit_commit(mitosis, v2_ctx)
                run_result.pipeline_stages.append("DELTA_SYNC:COMMIT")
                run_result.final_engram_id = mitosis.healed_engram_id
                run_result.passed = True
                break

            # Heal failed — re-validate and try again if cycles remain
            if cycle < self._max_heal_cycles:
                # Re-fetch healed engram (may still be in graph under original id)
                maybe_rehealed = graph.get_engram(current_engram.engram_id)
                if maybe_rehealed and isinstance(maybe_rehealed, ContextAwareEngram):
                    adv_result = self._validator.validate(maybe_rehealed)
        else:
            # All cycles exhausted
            self._bus.emit_failed(
                engram.engram_id,
                reason=mitosis.failure_reason if mitosis else "unknown",
                cycles_used=self._max_heal_cycles,
            )
            run_result.pipeline_stages.append("DELTA_SYNC:FAILED")

        run_result.total_latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return run_result

    def run_batch(
        self,
        graph: EngramGraph,
        engrams: list[ContextAwareEngram],
    ) -> list[TribunalRunResult]:
        """Run tribunal for a list of engrams. Sequential (safe for DAG ordering)."""
        return [self.run(graph, e) for e in engrams]
