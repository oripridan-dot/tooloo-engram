# Engram V2 Training Camp — Full Report

| Field | Value |
|---|---|
| **Run ID** | `camp-full-ac1d0753` |
| **Mode** | `offline-mock` |
| **Started** | 2026-03-08T11:48:35.704691+00:00 |
| **Completed** | 2026-03-08T11:48:35.969525+00:00 |
| **Scenarios** | 11/11 passed |
| **Generated** | 2026-03-08 11:48:35 UTC |

---

## Camp KPIs

| KPI | Value | Gate | Status |
|---|---|---|---|
| Pass Rate | **100.0%** | ≥ 90% | ✅ |
| Avg Quality | **95.0** | ≥ 90.0 | ✅ |
| Adversary 1st-Pass | **100.0%** | ≥ 60% | ✅ |
| Avg Latency | **23.0 ms** | — | — |
| P99 Latency | **43.2 ms** | — | — |
| Total Heal Cycles | **9** | ≤ 3/scenario | ✅ |

### ✅ ALL REGRESSION GATES GREEN

---

## Per-Scenario Results

| Scenario | Level | Status | Quality | Latency (ms) | JIT | Heals | Adversary 1st |
|---|---|---|---|---|---|---|---|
| `L1-01` | L1 | ✅ | 95.0 | 19.6 | 6 | 1 | ✅ |
| `L1-02` | L1 | ✅ | 95.0 | 6.6 | 3 | 0 | ✅ |
| `L1-03` | L1 | ✅ | 95.0 | 8.8 | 4 | 0 | ✅ |
| `L1-04` | L1 | ✅ | 95.0 | 20.1 | 6 | 1 | ✅ |
| `L1-05` | L1 | ✅ | 95.0 | 23.6 | 6 | 1 | ✅ |
| `L2-01` | L2 | ✅ | 95.0 | 28.4 | 10 | 1 | ✅ |
| `L2-02` | L2 | ✅ | 95.0 | 29.1 | 9 | 1 | ✅ |
| `L2-03` | L2 | ✅ | 95.0 | 13.8 | 6 | 0 | ✅ |
| `L3-01` | L3 | ✅ | 95.0 | 43.2 | 12 | 2 | ✅ |
| `L3-02` | L3 | ✅ | 95.0 | 26.5 | 9 | 1 | ✅ |
| `L3-03` | L3 | ✅ | 95.0 | 33.1 | 11 | 1 | ✅ |

## Latency Histogram (ms)

```
  L1-01    █████████████                     19.6ms
  L1-02    ████                               6.6ms
  L1-03    ██████                             8.8ms
  L1-04    █████████████                     20.1ms
  L1-05    ████████████████                  23.6ms
  L2-01    ███████████████████               28.4ms
  L2-02    ████████████████████              29.1ms
  L2-03    █████████                         13.8ms
  L3-01    ██████████████████████████████    43.2ms
  L3-02    ██████████████████                26.5ms
  L3-03    ██████████████████████            33.1ms
```

## Level Breakdown

| Level | Scenarios | Passed | Avg Quality | Avg Latency |
|---|---|---|---|---|
| L1 | 5 | 5/5 | 95.0 | 15.7 ms |
| L2 | 3 | 3/3 | 95.0 | 23.8 ms |
| L3 | 3 | 3/3 | 95.0 | 34.3 ms |
