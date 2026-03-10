"""
MandatePipeline — Dual-Track Context Pipeline for V2.

Implements the Continuous Virtual Context Window described in Law 14 / Chapter 17:

  Track A  (Foreground)    — gemini-3.1-flash.
    Handles immediate operator interaction.
    Context = last 3-5 turns + current active LogicEngram (strict budget).
    Emits NarrativeFrame JSON via SSE.

  Track B  (Shadow Weaver) — gemini-3.1-flash-lite.
    Runs asynchronously in a daemon thread.
    Compresses rolling history into dense ContextTensor objects.
    Polls Vertex AI Vector Search (tooloo-memory) for SOTA grounding.
    Periodically injects its ContextTensor into Track A's context envelope.

Architecture:
    MandateEnvelope     — the canonical context wrapper for a single mandate turn
    ForegroundPipeline  — synchronous, emits frames immediately
    ShadowWeaver        — async background compressor + SOTA poller
    MandatePipeline     — orchestrates both tracks

Constitutional compliance:
  Law 14 (PLATINUM Time Awareness): any op > 500 ms emits temporal_fill frame.
  Law 16 (Lossless JIT): context is never hoarded; dissolves after mandate.
  Law 17 (Stateless Processor Fluidity): processors receive full context at
          execution time and have zero persistent member state.
  Law 19 (Epistemic Humility): confidence < 0.85 trips circuit breaker.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, AsyncGenerator
from uuid import UUID, uuid4

from .schema import ContextTensor, LogicEngram

if TYPE_CHECKING:
    from .epigenetic_infusion import SynapseCollisionEngine
    from .graph_store import EngramGraph

log = logging.getLogger(__name__)

# ── Track identifiers ─────────────────────────────────────────

FOREGROUND_MODEL = "gemini-3.1-flash"
SHADOW_MODEL = "gemini-3.1-flash-lite"
DEEP_LOGIC_MODEL = "gemini-3.1-pro"

CONFIDENCE_CIRCUIT_BREAKER = 0.85  # Law 19
CLARIFICATION_THRESHOLD = 0.60      # Law 18
TEMPORAL_FILL_THRESHOLD_MS = 500    # Law 14
FOREGROUND_WINDOW = 5               # max turns in immediate context


# ── NarrativeFrame (inline — avoids circular import with facade) ──

class FramePhase(StrEnum):
    THINKING = "thinking"
    TEMPORAL_FILL = "temporal_fill"
    VISUAL_ANCHOR = "visual_anchor"
    RESPONSE = "response"
    ERROR = "error"
    CLARIFICATION_REQUEST = "clarification_request"
    SHADOW_SYNC = "shadow_sync"          # Track B injected a new ContextTensor
    CONFIDENCE_UPDATE = "confidence_update"


@dataclass
class NarrativeFrame:
    """
    Atomic SSE payload emitted by the Foreground pipeline.

    Mirrors the production NarrativeFrame used in facade_api.py but
    defined here so mandate_pipeline is self-contained within tooloo-engram.
    """

    phase: FramePhase
    content: str
    confidence: float = 1.0
    mandate_id: UUID = field(default_factory=uuid4)
    frame_id: UUID = field(default_factory=uuid4)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sensory_cues: dict = field(default_factory=dict)
    assets: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "content": self.content,
            "confidence": self.confidence,
            "mandate_id": str(self.mandate_id),
            "frame_id": str(self.frame_id),
            "timestamp": self.timestamp,
            "sensory_cues": self.sensory_cues,
            "assets": self.assets,
        }

    def to_sse(self) -> str:
        """Format as a Server-Sent Event string."""
        import json
        return f"data: {json.dumps(self.to_dict())}\n\n"


# ── Mandate envelope ──────────────────────────────────────────

@dataclass
class ConversationTurn:
    """A single operator ↔ system exchange."""

    role: str   # "operator" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    engram_id: UUID | None = None    # active LogicEngram during this turn


@dataclass
class MandateEnvelope:
    """
    The canonical context wrapper for a single mandate execution.

    Dissolves after mandate completion (Law 16 — Lossless JIT).
    """

    operator_mandate: str
    mandate_id: UUID = field(default_factory=uuid4)
    active_engram: LogicEngram | None = None
    conversation_history: list[ConversationTurn] = field(default_factory=list)
    shadow_tensor: ContextTensor | None = None   # latest injection from Track B
    confidence: float = 1.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def foreground_window(self) -> list[ConversationTurn]:
        """Return the last FOREGROUND_WINDOW turns for Track A context injection."""
        return self.conversation_history[-FOREGROUND_WINDOW:]

    def record_turn(self, role: str, content: str, engram_id: UUID | None = None) -> None:
        self.conversation_history.append(
            ConversationTurn(role=role, content=content, engram_id=engram_id)
        )

    def absorb_shadow_tensor(self, tensor: ContextTensor) -> None:
        """Inject the latest ContextTensor from Track B into this envelope."""
        self.shadow_tensor = tensor
        log.debug("mandate %s absorbed shadow tensor %s", self.mandate_id, tensor.tensor_id)

    def to_context_payload(self) -> dict:
        """
        Assemble the minimal context payload for Track A.

        Sends: recent turns + active engram intent/signature + shadow tensor summary.
        Never sends full code bodies (token budget discipline — Law 16).
        """
        payload: dict = {
            "mandate": self.operator_mandate,
            "confidence": self.confidence,
            "recent_turns": [
                {"role": t.role, "content": t.content}
                for t in self.foreground_window()
            ],
        }
        if self.active_engram:
            payload["active_engram"] = {
                "intent": self.active_engram.intent,
                "ast_signature": self.active_engram.ast_signature,
                "domain": self.active_engram.domain.value,
                "module_path": self.active_engram.module_path,
            }
        if self.shadow_tensor and self.shadow_tensor.assembled_prompt:
            payload["sota_context"] = self.shadow_tensor.assembled_prompt[:2000]  # hard cap
        return payload


# ── Mock LLM interface (swappable for live Vertex AI) ─────────


class LLMBackend:
    """Abstract interface — swap for live google-genai client in production."""

    def generate(self, prompt: str, model: str, thinking_budget: int = 0) -> str:  # noqa: ARG002
        return f"[{model}] mock response for: {prompt[:60]}"

    async def generate_async(
        self, prompt: str, model: str, thinking_budget: int = 0
    ) -> str:
        return self.generate(prompt, model, thinking_budget)


# ── Track A: Foreground Pipeline ──────────────────────────────


class ForegroundPipeline:
    """
    Synchronous foreground handler (Track A).

    Uses gemini-3.1-flash with strict context window.
    Emits NarrativeFrame events.
    """

    def __init__(self, llm: LLMBackend | None = None) -> None:
        self._llm = llm or LLMBackend()

    def _emit_temporal_fill(self, envelope: MandateEnvelope, operation: str) -> NarrativeFrame:
        """Law 14: emit temporal_fill before any op that may exceed 500 ms."""
        return NarrativeFrame(
            phase=FramePhase.TEMPORAL_FILL,
            content=f"Analysing {operation}…",
            confidence=envelope.confidence,
            mandate_id=envelope.mandate_id,
            sensory_cues={"resistance_level": 1 - envelope.confidence},
        )

    def process(
        self, envelope: MandateEnvelope
    ) -> list[NarrativeFrame]:
        """Process one mandate turn. Returns ordered list of NarrativeFrames."""
        frames: list[NarrativeFrame] = []

        # Law 19: confidence gate
        if envelope.confidence < CONFIDENCE_CIRCUIT_BREAKER:
            if envelope.confidence < CLARIFICATION_THRESHOLD:
                # Law 18: request clarification
                frames.append(
                    NarrativeFrame(
                        phase=FramePhase.CLARIFICATION_REQUEST,
                        content=(
                            "I need a bit more detail to proceed confidently. "
                            "Could you clarify the scope or intent of this mandate?"
                        ),
                        confidence=envelope.confidence,
                        mandate_id=envelope.mandate_id,
                    )
                )
                return frames
            # Between 0.60-0.85: warn but proceed
            frames.append(
                NarrativeFrame(
                    phase=FramePhase.CONFIDENCE_UPDATE,
                    content=f"Confidence at {envelope.confidence:.0%}. Proceeding with caution.",
                    confidence=envelope.confidence,
                    mandate_id=envelope.mandate_id,
                )
            )

        # Temporal fill (Law 14)
        frames.append(self._emit_temporal_fill(envelope, "mandate"))

        # Shadow sync notification
        if envelope.shadow_tensor:
            frames.append(
                NarrativeFrame(
                    phase=FramePhase.SHADOW_SYNC,
                    content="SOTA context synchronised from memory vault.",
                    confidence=envelope.confidence,
                    mandate_id=envelope.mandate_id,
                )
            )

        # Core LLM call (Track A — fast, minimal context)
        t0 = time.monotonic()
        context_payload = envelope.to_context_payload()
        prompt = _build_foreground_prompt(context_payload)
        response = self._llm.generate(prompt, model=FOREGROUND_MODEL, thinking_budget=0)
        latency_ms = (time.monotonic() - t0) * 1000

        frames.append(
            NarrativeFrame(
                phase=FramePhase.RESPONSE,
                content=response,
                confidence=envelope.confidence,
                mandate_id=envelope.mandate_id,
                sensory_cues={"latency_ms": round(latency_ms, 1)},
            )
        )
        envelope.record_turn("system", response)
        return frames

    async def process_streaming(
        self, envelope: MandateEnvelope
    ) -> AsyncGenerator[NarrativeFrame, None]:
        """Async streaming variant — yields frames as they are produced."""
        for frame in self.process(envelope):
            yield frame
            await asyncio.sleep(0)  # yield control to event loop


# ── Track B: Shadow Weaver ────────────────────────────────────


class ShadowWeaver:
    """
    Background context compressor + SOTA poller (Track B).

    Runs as a daemon thread. Periodically:
      1. Compresses rolling conversation history into a ContextTensor.
      2. Queries SynapseCollisionEngine (tooloo-memory / Vertex AI Vector Search)
         for the most relevant .cog.json patterns for the active mandate.
      3. Injects the resulting ContextTensor into the active MandateEnvelope.

    Uses gemini-3.1-flash-lite for cost efficiency (thinking_budget=0).
    New patterns discovered during compression are auto-embedded (epigenetic infusion)
    via SynapseCollisionEngine.
    """

    def __init__(
        self,
        llm: LLMBackend | None = None,
        poll_interval_s: float = 10.0,
        synapse_engine: "SynapseCollisionEngine | None" = None,
    ) -> None:
        self._llm = llm or LLMBackend()
        self._poll_interval = poll_interval_s
        self._synapse = synapse_engine
        self._active_envelope: MandateEnvelope | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def attach(self, envelope: MandateEnvelope) -> None:
        """Attach to an active mandate envelope and start the background thread."""
        self._active_envelope = envelope
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="shadow-weaver")
        self._thread.start()
        log.debug("ShadowWeaver attached to mandate %s", envelope.mandate_id)

    def detach(self) -> None:
        """Signal stop and join the thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._active_envelope = None
        log.debug("ShadowWeaver detached")

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            if self._active_envelope is None:
                break
            try:
                tensor = self._compress_and_fetch(self._active_envelope)
                self._active_envelope.absorb_shadow_tensor(tensor)
            except Exception as exc:  # noqa: BLE001
                log.warning("ShadowWeaver cycle error: %s", exc)

    def _compress_and_fetch(self, envelope: MandateEnvelope) -> ContextTensor:
        """Compress history and fetch SOTA context (Law 16 — JIT, dissolve after)."""
        turns = envelope.conversation_history
        history_summary = "\n".join(
            f"{t.role}: {t.content[:200]}" for t in turns[-20:]
        )

        # Compress history via flash-lite (thinking_budget=0 for cost)
        compression_prompt = (
            f"Compress the following conversation into a dense context summary "
            f"of ≤500 tokens, preserving all technical decisions and intent:\n{history_summary}"
        )
        compressed = self._llm.generate(
            compression_prompt, model=SHADOW_MODEL, thinking_budget=0
        )

        # SOTA grounding — query SynapseCollisionEngine (tooloo-memory / Vertex AI)
        sota_context = self._query_memory(envelope.operator_mandate)

        assembled_prompt = f"{compressed}\n\n--- SOTA Context ---\n{sota_context}"

        return ContextTensor(
            target_engrams=[e.engram_id for e in [envelope.active_engram] if e],
            dependency_subgraph_json="{}",
            intent_chain=[t.content[:100] for t in turns[-3:]],
            token_budget=2000,
            assembled_prompt=assembled_prompt,
        )

    def _query_memory(self, mandate_text: str) -> str:
        """
        Query tooloo-memory for relevant .cog.json patterns.

        If SynapseCollisionEngine is wired, uses its Vertex AI Vector Search
        backend for ANN retrieval.  Falls back to stub in offline mode.
        """
        if self._synapse is None:
            return "[SOTA stub — SynapseCollisionEngine not wired; connect to tooloo-memory]"
        cogs = self._synapse.query_memory(mandate_text, num_neighbors=5)
        if not cogs:
            return "[No relevant patterns in tooloo-memory for this mandate yet]"
        lines = []
        for cog in cogs:
            lines.append(
                f"### {cog.title}\n"
                f"Frameworks: {', '.join(cog.frameworks)}\n"
                f"Pattern: {cog.pattern_summary[:300]}\n"
            )
        return "\n".join(lines)


# ── MandatePipeline (public entry point) ─────────────────────


class MandatePipeline:
    """
    Dual-track mandate execution engine.

    Wires ForegroundPipeline (Track A) and ShadowWeaver (Track B).
    Provides synchronous process() and async process_async() interfaces.

    Pass a SynapseCollisionEngine to enable live Vertex AI Vector Search
    memory grounding in Track B's shadow cycles.
    """

    def __init__(
        self,
        llm: LLMBackend | None = None,
        shadow_poll_interval_s: float = 10.0,
        synapse_engine: "SynapseCollisionEngine | None" = None,
    ) -> None:
        self._foreground = ForegroundPipeline(llm)
        self._shadow = ShadowWeaver(llm, shadow_poll_interval_s, synapse_engine)

    def start_mandate(self, operator_mandate: str) -> MandateEnvelope:
        """Create a new MandateEnvelope and attach the ShadowWeaver."""
        envelope = MandateEnvelope(operator_mandate=operator_mandate)
        envelope.record_turn("operator", operator_mandate)
        self._shadow.attach(envelope)
        return envelope

    def process(self, envelope: MandateEnvelope) -> list[NarrativeFrame]:
        """Run Track A on the active envelope. Returns all emitted frames."""
        return self._foreground.process(envelope)

    async def process_async(
        self, envelope: MandateEnvelope
    ) -> AsyncGenerator[NarrativeFrame, None]:
        """Async streaming variant of process()."""
        async for frame in self._foreground.process_streaming(envelope):
            yield frame

    def close_mandate(self, envelope: MandateEnvelope) -> None:
        """Detach ShadowWeaver and dissolve the envelope (Law 16)."""
        self._shadow.detach()
        log.info("mandate %s closed — context dissolved", envelope.mandate_id)


# ── Helpers ───────────────────────────────────────────────────

def _build_foreground_prompt(context: dict) -> str:
    """Construct a minimal, token-budgeted prompt for Track A."""
    lines = [f"Mandate: {context['mandate']}"]
    if context.get("active_engram"):
        ae = context["active_engram"]
        lines.append(f"Active Engram: [{ae['domain']}] {ae['intent']}")
        lines.append(f"Signature: {ae['ast_signature']}")
    if context.get("sota_context"):
        lines.append(f"\nSOTA Context:\n{context['sota_context']}")
    if context.get("recent_turns"):
        lines.append("\nRecent Context:")
        for turn in context["recent_turns"][-3:]:
            lines.append(f"  {turn['role']}: {turn['content'][:200]}")
    return "\n".join(lines)


# NOTE: _fetch_sota_stub is retained only for backward-compat unit tests.
# Live code paths use ShadowWeaver._query_memory() which delegates to
# SynapseCollisionEngine → VertexVectorBackend (Vertex AI Vector Search 2.0).
def _fetch_sota_stub(mandate: str) -> str:  # noqa: ARG001
    return "[SOTA stub — SynapseCollisionEngine not wired]"
