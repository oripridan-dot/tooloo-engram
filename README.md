# tooloo-engram — TooLoo V2 Engram Ecosystem
> **Foundation for TooLoo V2.** The graph-native, JIT-anchored, self-healing AI generation engine.

> **Status snapshot (2026-03-08):** Phase 1 live benchmark complete. Verified: **100 tests passing (2 live-only skips)**. Core+Engram global snapshot: **3029 tests total (2929 core + 100 engram)**.

This repository is the dedicated proving ground for the **Engram Architecture** — the technology that makes TooLoo V2 unmatched in accuracy, speed, and real-world awareness.

---

## What is the Engram Ecosystem?

The Engram Architecture replaces the traditional "file generation" model of AI coding with a **living knowledge graph**. Every piece of logic is a node (a `ContextAwareEngram`). Every dependency is a typed edge (`SynapticEdge`). The entire codebase is a **Directed Acyclic Graph (DAG)** that heals itself, stays anchored to real-world facts, and delivers only validated output to the user.

### V2 Pillars

| Pillar | Component | What it does |
|--------|-----------|-------------|
| **Reality Anchoring** | `JITContextAnchor` | Fetches live docs, deprecation notices, security advisories into every node before generation |
| **Multi-Agent Tribunal** | `AdversaryValidator` → `ArbiterHealer` | Scout fetches truth, Adversary fast-fails bad code (binary JSON), Arbiter surgically heals via Mitosis |
| **Zero-Downtime Mitosis** | `ArbiterHealer` | Broken node cloned → healed → edges repointed → v1 garbage collected — user sees nothing |
| **Delta Sync** | `DeltaSyncBus` | WebSocket micro-deltas (PENDING/COMMIT/FAILED) so UI hot-swaps nodes surgically |
| **Graph Awareness** | `GraphAwareness` | Every action calculates blast radius; macro-state hash detects drift |

---

## KPI: TooLoo V2 Target Output

> After this training camp, TooLoo V2 must be able to generate a **fully working, complex web app** that:
> - Responds in **near-real-time** (p99 < 50ms for interactive operations)
> - Has **multi-modal capabilities** (real-time audio, visual, interactive)
> - Is **multi-module** (frontend + backend + worker + config, all in one graph)
> - Achieves **CAS ≥ 97** on every generated artifact
> - Has **zero regressions** in data performance vs Track A/B Phase 1 benchmarks

---

## Repository Structure

```
tooloo-engram/
├── engram_v2/              # V2 engine (imports from experiments/project_engram)
│   ├── __init__.py         # Public API surface
│   └── orchestrator.py     # High-level V2 mandate runner
├── usecases/               # Real-world use-case scenarios
│   ├── uc_01_realtime_collab.py    # Real-time collaborative editor
│   ├── uc_02_auth_service.py       # Secure auth service (JWT + refresh)
│   ├── uc_03_websocket_feed.py     # Live data feed (WebSocket + CRDT)
│   ├── uc_04_multimodal_ui.py      # Multi-modal UI (mic + canvas + chat)
│   └── uc_05_full_stack_app.py     # Full-stack app (all modules combined)
├── training_camp/          # Benchmark + training system
│   ├── __init__.py
│   ├── camp_runner.py      # Master training loop
│   ├── scenarios.py        # L1/L2/L3 scenario registry
│   ├── metrics.py          # Regression-proof metric collection
│   └── ledger.py           # Training ledger (JSONL persistence)
├── tests/                  # All V2 component tests
│   ├── test_jit_context.py
│   ├── test_adversary.py
│   ├── test_arbiter.py
│   ├── test_delta_sync.py
│   ├── test_tribunal_orchestrator.py
│   ├── test_training_camp.py
│   └── test_usecases.py
├── benchmarks/             # V2-specific benchmarks
│   └── v2_tribunal_bench.py
├── pyproject.toml
└── README.md               # This file
```

---

## Benchmark Status

| Phase | Status | Key Metric |
|-------|--------|-----------|
| Track A vs B (Phase 1) | ✅ DONE | Track B: −53% time, −63% cost, +1.1 quality |
| V2 Tribunal Validation | 🔄 IN PROGRESS | Target: adversary latency < 10ms (offline) |
| V2 Real-World Use Cases | 🔄 IN PROGRESS | Target: 5 use cases × L3 quality ≥ 97 |
| V2 Full-Stack App | ⏳ PLANNED | KPI completion gate |

---

## Gemini Consultation Input

If you are consulting Gemini Web before enabling Engram in production, provide:

1. `/workspaces/tooloo-core/GEMINI_SYSTEM_REVIEW.md` (primary decision packet)
2. `/workspaces/tooloo-core/docs/ROADMAP.md` (delivery context)
3. `/workspaces/tooloo-core/TOOLOO_MASTER_PLAN.md` (historical + governance context)

Ask for a **GO / HOLD / REJECT** migration verdict with a phased P0/P1/P2 plan and explicit risk controls.

---

## Quick Start

```bash
# Run the training camp
python -m tooloo_engram.training_camp.camp_runner

# Run all V2 tests
pytest tooloo-engram/tests/ -v

# Run V2 tribunal benchmark
python tooloo-engram/benchmarks/v2_tribunal_bench.py
```
