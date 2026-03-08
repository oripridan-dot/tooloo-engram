## ✅ PASS  `L2-01` — Secure Auth Service

| Field | Value |
|---|---|
| **Level** | L2 |
| **Domains** | `backend, frontend` |
| **Engrams** | 3 |
| **Edges** | 0 |
| **Latency** | 26.2 ms |
| **Quality Score** | 95.00 |
| **JIT Sources Anchored** | 10 |
| **Adversary Rules Checked** | 23 |
| **Heal Cycles** | 1 |
| **Adversary First Pass** | ✅ Yes |
| **Timestamp** | 2026-03-08T12:47:45.538393+00:00 |

### Mandate

> Build a complete authentication service with: (1) POST /auth/login endpoint (email + password → JWT), (2) JWT validation middleware, (3) Refresh token rotation, (4) React useAuth() hook consuming the API.

### Adversary Seeds (Injected Flaws)

| Rule ID | Description |
|---|---|
| `SEC-002` | Hardcoded JWT secret |

### Tribunal Pipeline Results

| Engram | Passed | Heals | JIT | Rules | Latency (ms) | Stages |
|---|---|---|---|---|---|---|
| `d97ddf82` | ✅ | 0 | 3 | 13 | 6.4 | JIT_ANCHOR:sources=3 → ADVERSARY:PASS:rules=13 |
| `aa430dc9` | ✅ | 0 | 4 | 8 | 8.4 | JIT_ANCHOR:sources=4 → ADVERSARY:PASS:rules=8 |
