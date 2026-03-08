"""
Training Camp Scenarios — L1/L2/L3 scenario registry for V2 benchmarks.

Each scenario defines:
  - mandate_text: what the system is asked to build
  - level: L1 (simple) / L2 (moderate) / L3 (complex multi-module)
  - domain_mix: which domains are involved
  - adversary_seeds: pre-injected code flaws to test the tribunal
  - expected_min_quality: minimum CAS-equivalent quality score
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ScenarioLevel(StrEnum):
    L1 = "L1"  # Single engram, single domain
    L2 = "L2"  # 3-5 engrams, 2 domains
    L3 = "L3"  # 10+ engrams, full-stack, multi-domain


@dataclass
class AdversarySeed:
    """A deliberate code flaw injected to verify the adversary catches it."""

    rule_id: str          # e.g. "SEC-001"
    description: str
    poisoned_code: str    # code snippet that should trigger the rule


@dataclass
class TrainingScenario:
    scenario_id: str
    title: str
    mandate_text: str
    level: ScenarioLevel
    domain_mix: list[str]
    adversary_seeds: list[AdversarySeed] = field(default_factory=list)
    expected_min_quality: float = 90.0
    expected_max_latency_ms: float = 500.0


# ── L1 Scenarios (single-domain, basic validation) ───────────

L1_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L1-01",
        title="JWT Token Validator",
        mandate_text="Create a validate_jwt(token: str) -> dict function that decodes and validates a JWT bearer token. Return the decoded payload or raise ValueError on failure.",
        level=ScenarioLevel.L1,
        domain_mix=["backend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-002",
                description="Hardcoded secret injected",
                poisoned_code='secret = "my_super_secret_key_hardcoded"',
            )
        ],
        expected_min_quality=92.0,
        expected_max_latency_ms=50.0,  # adversary check + mock heal
    ),
    TrainingScenario(
        scenario_id="L1-02",
        title="Rate Limiter Middleware",
        mandate_text="Create a rate_limit(max_requests: int, window_s: int) decorator for FastAPI endpoints using an in-memory sliding window.",
        level=ScenarioLevel.L1,
        domain_mix=["backend"],
        adversary_seeds=[],
        expected_min_quality=90.0,
        expected_max_latency_ms=30.0,
    ),
    TrainingScenario(
        scenario_id="L1-03",
        title="React Hook — useRealTimeData",
        mandate_text="Create a useRealTimeData(url: string) React hook that subscribes to a WebSocket and returns { data, isConnected, error }. Clean up on unmount.",
        level=ScenarioLevel.L1,
        domain_mix=["frontend"],
        adversary_seeds=[],
        expected_min_quality=91.0,
        expected_max_latency_ms=30.0,
    ),
    TrainingScenario(
        scenario_id="L1-04",
        title="Deprecated Datetime Catch",
        mandate_text="Create a get_utc_now() helper that returns the current UTC datetime as a timezone-aware object.",
        level=ScenarioLevel.L1,
        domain_mix=["backend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="DEP-002",
                description="Uses deprecated utcnow()",
                poisoned_code="return datetime.utcnow()",
            )
        ],
        expected_min_quality=95.0,
        expected_max_latency_ms=30.0,
    ),
    TrainingScenario(
        scenario_id="L1-05",
        title="SQL Injection Guard",
        mandate_text="Create a get_user_by_email(email: str, db) function that fetches a user from the database safely.",
        level=ScenarioLevel.L1,
        domain_mix=["backend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="SQL string interpolation",
                poisoned_code='query = f"SELECT * FROM users WHERE email = \'{email}\'"',
            )
        ],
        expected_min_quality=96.0,
        expected_max_latency_ms=40.0,
    ),
]

# ── L2 Scenarios (multi-domain, tribunal full pipeline) ───────

L2_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L2-01",
        title="Secure Auth Service",
        mandate_text=(
            "Build a complete authentication service with: "
            "(1) POST /auth/login endpoint (email + password → JWT), "
            "(2) JWT validation middleware, "
            "(3) Refresh token rotation, "
            "(4) React useAuth() hook consuming the API."
        ),
        level=ScenarioLevel.L2,
        domain_mix=["backend", "frontend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-002",
                description="Hardcoded JWT secret",
                poisoned_code='JWT_SECRET = "dev_secret_do_not_use_in_prod"',
            )
        ],
        expected_min_quality=94.0,
        expected_max_latency_ms=200.0,
    ),
    TrainingScenario(
        scenario_id="L2-02",
        title="Real-Time Notification System",
        mandate_text=(
            "Build a notification system: "
            "(1) NotificationService (Python) — enqueue/dequeue with async worker, "
            "(2) WebSocket endpoint /ws/notifications/{user_id}, "
            "(3) React NotificationBell component polling the stream."
        ),
        level=ScenarioLevel.L2,
        domain_mix=["backend", "frontend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="PERF-001",
                description="Polling instead of WebSocket push",
                poisoned_code="while True:\n    time.sleep(1)\n    fetch_notifications()",
            )
        ],
        expected_min_quality=93.0,
        expected_max_latency_ms=200.0,
    ),
    TrainingScenario(
        scenario_id="L2-03",
        title="Database Migration Guard",
        mandate_text=(
            "Build a schema migration system: "
            "(1) MigrationRunner that applies versioned SQL scripts in order, "
            "(2) MigrationLock to prevent concurrent runs, "
            "(3) CLI command `python manage.py migrate [--dry-run]`."
        ),
        level=ScenarioLevel.L2,
        domain_mix=["backend", "config"],
        adversary_seeds=[],
        expected_min_quality=92.0,
        expected_max_latency_ms=150.0,
    ),
]

# ── L3 Scenarios (full-stack, V2 KPI gate) ────────────────────

L3_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L3-01",
        title="Real-Time Collaborative Editor",
        mandate_text=(
            "Build a real-time collaborative text editor: "
            "(1) Python CRDT engine (operational transforms for text), "
            "(2) WebSocket server broadcasting ops to all connected clients, "
            "(3) FastAPI REST API (create/join/leave room), "
            "(4) React editor component with cursor presence indicators, "
            "(5) Conflict resolution: last-write-wins with vector clock."
        ),
        level=ScenarioLevel.L3,
        domain_mix=["backend", "frontend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="Unvalidated room ID in SQL",
                poisoned_code='f"SELECT * FROM rooms WHERE id = \'{room_id}\'"',
            ),
            AdversarySeed(
                rule_id="PERF-001",
                description="Polling for updates",
                poisoned_code="while True:\n    time.sleep(0.5)\n    get_updates()",
            ),
        ],
        expected_min_quality=94.0,
        expected_max_latency_ms=400.0,
    ),
    TrainingScenario(
        scenario_id="L3-02",
        title="Multi-Modal AI Assistant",
        mandate_text=(
            "Build a multi-modal AI assistant with: "
            "(1) Python WebSocket server streaming LLM responses token-by-token, "
            "(2) Audio transcription endpoint (POST /api/transcribe, returns text), "
            "(3) Vision analysis endpoint (POST /api/vision, accepts base64 image), "
            "(4) React UI: microphone button + canvas for vision + chat stream, "
            "(5) Context window manager keeping last 20 turns."
        ),
        level=ScenarioLevel.L3,
        domain_mix=["backend", "frontend"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-003",
                description="eval() on model output",
                poisoned_code="result = eval(model_response)",
            ),
        ],
        expected_min_quality=95.0,
        expected_max_latency_ms=500.0,
    ),
    TrainingScenario(
        scenario_id="L3-03",
        title="Full-Stack Analytics Dashboard",
        mandate_text=(
            "Build a full analytics dashboard: "
            "(1) TimeSeries ingestor (async worker consuming events), "
            "(2) Aggregation engine (hourly/daily rollups, stored in PostgreSQL), "
            "(3) Query API (GET /api/metrics?from=&to=&granularity=), "
            "(4) React dashboard: line chart + bar chart + heatmap (recharts), "
            "(5) Export endpoint (GET /api/metrics/export?format=csv|json)."
        ),
        level=ScenarioLevel.L3,
        domain_mix=["backend", "frontend", "config"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="PERF-002",
                description="N+1 query in aggregation loop",
                poisoned_code="for event in events:\n    db.query(Metric).filter(...).first()",
            ),
        ],
        expected_min_quality=94.0,
        expected_max_latency_ms=500.0,
    ),
]

# ── Registry ──────────────────────────────────────────────────

ALL_SCENARIOS: list[TrainingScenario] = L1_SCENARIOS + L2_SCENARIOS + L3_SCENARIOS

SCENARIO_BY_ID: dict[str, TrainingScenario] = {s.scenario_id: s for s in ALL_SCENARIOS}


def get_scenarios(
    level: ScenarioLevel | None = None,
    scenario_id: str | None = None,
) -> list[TrainingScenario]:
    """Return scenarios optionally filtered by level and/or scenario_id."""
    result = ALL_SCENARIOS
    if level is not None:
        result = [s for s in result if s.level == level]
    if scenario_id is not None:
        result = [s for s in result if s.scenario_id == scenario_id]
    return result
