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
    L4 = "L4"  # 15+ engrams, distributed microservices, 3+ domains
    L5 = "L5"  # 20+ engrams, cloud-native production system, all domains
    L6 = "L6"  # 30+ engrams, enterprise-grade, adversary chaining


@dataclass
class AdversarySeed:
    """A deliberate code flaw injected to verify the adversary catches it."""

    rule_id: str  # e.g. "SEC-001"
    description: str
    poisoned_code: str  # code snippet that should trigger the rule


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
                poisoned_code="query = f\"SELECT * FROM users WHERE email = '{email}'\"",
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
                poisoned_code="f\"SELECT * FROM rooms WHERE id = '{room_id}'\"",
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

# ── L4 Scenarios (distributed microservices, 3+ domains) ─────

L4_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L4-01",
        title="Event-Driven Order Processing Platform",
        mandate_text=(
            "Build a distributed order processing platform: "
            "(1) OrderService (FastAPI) — create/update/cancel orders with optimistic locking, "
            "(2) InventoryService — reserve/release stock with dead-letter queue fallback, "
            "(3) PaymentService — charge/refund with idempotency key enforcement, "
            "(4) Kafka event bus wiring all three services via saga pattern, "
            "(5) React order dashboard: live status stream + inventory heatmap, "
            "(6) Distributed tracing via OpenTelemetry spans across all services."
        ),
        level=ScenarioLevel.L4,
        domain_mix=["backend", "frontend", "config"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="Unparameterized order ID in SQL lookup",
                poisoned_code="f\"SELECT * FROM orders WHERE id = '{order_id}'\"",
            ),
            AdversarySeed(
                rule_id="PERF-002",
                description="N+1 query in inventory reservation loop",
                poisoned_code="for item in order.items:\n    db.query(Stock).filter_by(sku=item.sku).first()",
            ),
        ],
        expected_min_quality=93.0,
        expected_max_latency_ms=600.0,
    ),
    TrainingScenario(
        scenario_id="L4-02",
        title="Multi-Tenant SaaS Platform Core",
        mandate_text=(
            "Build the core of a multi-tenant SaaS platform: "
            "(1) Tenant provisioning service (create/suspend/delete orgs with quotas), "
            "(2) Row-level security middleware isolating all DB queries by tenant_id, "
            "(3) Feature flag engine (per-tenant toggle with rollout percentage), "
            "(4) Billing metering service (usage events → aggregated invoices), "
            "(5) React admin panel: tenant list + usage charts + flag toggles."
        ),
        level=ScenarioLevel.L4,
        domain_mix=["backend", "frontend", "config"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-002",
                description="Hardcoded tenant admin credentials",
                poisoned_code='ADMIN_TOKEN = "superadmin_token_12345"',
            ),
            AdversarySeed(
                rule_id="HEU-001",
                description="Missing tenant_id filter allows cross-tenant data leak",
                poisoned_code="return db.query(Record).filter(Record.id == record_id).first()",
            ),
        ],
        expected_min_quality=94.0,
        expected_max_latency_ms=650.0,
    ),
    TrainingScenario(
        scenario_id="L4-03",
        title="ML Model Serving Infrastructure",
        mandate_text=(
            "Build an ML model serving infrastructure: "
            "(1) ModelRegistry — versioned model storage with promotion gates (shadow/canary/prod), "
            "(2) InferenceServer — async batch inference engine with request queuing, "
            "(3) A/B traffic splitter — route % of traffic to challenger model, "
            "(4) Drift monitor — statistical drift detection (PSI) on live predictions, "
            "(5) React model ops dashboard: latency P99, accuracy drift chart, routing sliders."
        ),
        level=ScenarioLevel.L4,
        domain_mix=["backend", "frontend", "config"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-003",
                description="eval() on incoming model input payload",
                poisoned_code="features = eval(request.json()['input'])",
            ),
        ],
        expected_min_quality=93.0,
        expected_max_latency_ms=700.0,
    ),
]

# ── L5 Scenarios (cloud-native production, all domains) ───────

L5_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L5-01",
        title="Cloud-Native Observability Platform",
        mandate_text=(
            "Build a production observability platform: "
            "(1) LogIngester — async pipeline collecting structured logs from 50+ services, "
            "(2) MetricsAggregator — Prometheus-compatible scrape endpoint + rolling-window P99, "
            "(3) TraceCorrelator — cross-service span stitching via trace_id propagation, "
            "(4) AlertEngine — threshold + anomaly detection rules with PagerDuty webhook, "
            "(5) SLO tracker — burn-rate calculations with error budget depletion alerts, "
            "(6) React ops console: live log tail + trace waterfall + SLO burn gauge, "
            "(7) Terraform module for deploying the stack on AWS ECS + RDS."
        ),
        level=ScenarioLevel.L5,
        domain_mix=["backend", "frontend", "config", "infra"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="Log injection via unsanitized user input in log message",
                poisoned_code='logging.info(f"User action: {user_input}")',
            ),
            AdversarySeed(
                rule_id="PERF-001",
                description="Synchronous HTTP call inside metrics aggregation hot loop",
                poisoned_code="for metric in metrics:\n    requests.post(webhook_url, json=metric)",
            ),
            AdversarySeed(
                rule_id="SEC-002",
                description="AWS credentials hardcoded in Terraform module",
                poisoned_code='access_key = "AKIAIOSFODNN7EXAMPLE"',
            ),
        ],
        expected_min_quality=94.0,
        expected_max_latency_ms=800.0,
    ),
    TrainingScenario(
        scenario_id="L5-02",
        title="Real-Time Financial Reconciliation Engine",
        mandate_text=(
            "Build a financial reconciliation engine: "
            "(1) TransactionIngester — dual-write to Postgres + event stream with exactly-once semantics, "
            "(2) ReconciliationWorker — match internal records vs bank feed with configurable tolerance, "
            "(3) ExceptionHandler — route unmatched transactions to manual review queue, "
            "(4) AuditLedger — append-only tamper-evident log with cryptographic hash chaining, "
            "(5) ReportEngine — daily P&L snapshots exported to S3 as Parquet + CSV, "
            "(6) React finance dashboard: reconciliation status, exception list, ledger explorer."
        ),
        level=ScenarioLevel.L5,
        domain_mix=["backend", "frontend", "config"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="SQL injection in transaction amount filter",
                poisoned_code='f"SELECT * FROM tx WHERE amount > {amount_filter}"',
            ),
            AdversarySeed(
                rule_id="PERF-002",
                description="N+1 query fetching bank feed items inside reconciliation loop",
                poisoned_code="for tx in transactions:\n    bank_tx = db.query(BankFeed).filter_by(ref=tx.ref).first()",
            ),
            AdversarySeed(
                rule_id="DEP-002",
                description="datetime.utcnow() in audit timestamp (deprecated, timezone-naive)",
                poisoned_code="audit_ts = datetime.utcnow().isoformat()",
            ),
        ],
        expected_min_quality=95.0,
        expected_max_latency_ms=900.0,
    ),
]

# ── L6 Scenarios (enterprise-grade, adversary chaining) ───────

L6_SCENARIOS: list[TrainingScenario] = [
    TrainingScenario(
        scenario_id="L6-01",
        title="Enterprise Identity & Access Management Platform",
        mandate_text=(
            "Build a full IAM platform: "
            "(1) AuthServer — OAuth2/OIDC server with PKCE, device flow, and refresh token rotation, "
            "(2) RBAC engine — hierarchical roles with attribute-based policy evaluation (ABAC overlay), "
            "(3) Audit trail — every permission check logged immutably with actor + resource + decision, "
            "(4) Provisioning API — SCIM 2.0 endpoint for user lifecycle management, "
            "(5) MFA orchestrator — TOTP, WebAuthn, and SMS fallback with step-up auth, "
            "(6) Threat intel feed — block logins from known-bad IPs via live threat DB, "
            "(7) React admin portal: user directory + role assignment matrix + audit log viewer, "
            "(8) SDK (Python + TypeScript) for downstream services to verify tokens."
        ),
        level=ScenarioLevel.L6,
        domain_mix=["backend", "frontend", "config", "infra"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-002",
                description="Hardcoded OIDC client secret in auth server config",
                poisoned_code='CLIENT_SECRET = "super_secret_oidc_client_12345"',
            ),
            AdversarySeed(
                rule_id="SEC-001",
                description="SQL injection in SCIM user lookup endpoint",
                poisoned_code="f\"SELECT * FROM users WHERE username = '{username}'\"",
            ),
            AdversarySeed(
                rule_id="SEC-003",
                description="eval() on ABAC policy expression from DB",
                poisoned_code="decision = eval(policy_expression)",
            ),
            AdversarySeed(
                rule_id="PERF-001",
                description="Synchronous MFA SMS send blocking auth request thread",
                poisoned_code="send_sms_sync(phone, otp_code)  # blocks for ~2s",
            ),
        ],
        expected_min_quality=96.0,
        expected_max_latency_ms=1200.0,
    ),
    TrainingScenario(
        scenario_id="L6-02",
        title="Global CDN & Edge Compute Orchestrator",
        mandate_text=(
            "Build a global CDN & edge compute orchestrator: "
            "(1) EdgeNodeRegistry — register/deregister PoP nodes with health scoring, "
            "(2) RoutingEngine — latency-aware GeoDNS routing with automatic failover, "
            "(3) CacheInvalidator — distributed cache purge with multi-region propagation, "
            "(4) EdgeWorkerDeployer — deploy serverless workers to PoPs with blue/green rollout, "
            "(5) ThreatShield — DDoS mitigation: rate-limiting, bot scoring, IP reputation, "
            "(6) AnalyticsPipeline — real-time CDN hit/miss ratio + bandwidth per PoP, "
            "(7) React NOC dashboard: world map with PoP health, live traffic flows, shield status, "
            "(8) CLI tool `edgectl` for operators to purge cache / deploy workers / view logs."
        ),
        level=ScenarioLevel.L6,
        domain_mix=["backend", "frontend", "config", "infra"],
        adversary_seeds=[
            AdversarySeed(
                rule_id="SEC-001",
                description="SSRF via unvalidated origin URL in cache warming request",
                poisoned_code="requests.get(origin_url)  # origin_url from untrusted user input",
            ),
            AdversarySeed(
                rule_id="PERF-002",
                description="N+1 DB queries fetching PoP metrics inside routing hot path",
                poisoned_code="for node in nodes:\n    metrics = db.query(NodeMetric).filter_by(pop_id=node.id).all()",
            ),
            AdversarySeed(
                rule_id="SEC-002",
                description="Hardcoded CDN signing key in edge worker deployer",
                poisoned_code='SIGNING_KEY = "cdn_edge_key_do_not_commit"',
            ),
            AdversarySeed(
                rule_id="DEP-002",
                description="datetime.utcnow() in cache TTL calculation",
                poisoned_code="expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)",
            ),
        ],
        expected_min_quality=95.0,
        expected_max_latency_ms=1500.0,
    ),
]

# ── Registry ──────────────────────────────────────────────────

ALL_SCENARIOS: list[TrainingScenario] = (
    L1_SCENARIOS + L2_SCENARIOS + L3_SCENARIOS + L4_SCENARIOS + L5_SCENARIOS + L6_SCENARIOS
)

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
