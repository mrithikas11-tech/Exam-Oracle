# Architecture

## Where everything runs

Decide this in the first 45 minutes. A laptop stack behind a cloud-deployed app fails silently.

| Piece | Runs on | Why | Fallback |
|---|---|---|---|
| RocketRide app + pipelines | RocketRide staging (cloud) | Deployed apps are served from staging; pipeline runs draw on promo credits `[DOCS]` | Local RocketRide engine in VS Code (`ws://localhost:5565`) during development `[DOCS]` |
| hotdata | hotdata cloud | Reachable from both laptop and RocketRide; $100 sign-up credit `[DOCS]` | — |
| HydraDB (hosted) — **primary** | api.hydradb.com, free "Ship" tier | RocketRide's `graph_hydradb` node talks to this API; per-student collections `[DOCS]` | Open-source HydraDB, laptop only |
| Cognee | One Cognee server reachable by both RocketRide and the laptop | `tool_cognee` calls a Cognee server by `base_url` (default `http://localhost:8000`) `[DOCS]` | **Plan A:** Cognee Cloud if available. **Plan B:** Cognee REST server on the laptop behind an authenticated tunnel (cloudflared or ngrok) |
| Rote Plays + generic scripts | Laptop (macOS/Linux) | Rote runs locally; RocketRide's Python node has no network/subprocess, so it cannot run Rote `[DOCS]` | — |
| Open-source HydraDB — optional | Laptop Docker | Only for the Cypher "why" path view | Cognee `recall` explains the path in words |

## System diagram

```
                       ┌──────────────── RocketRide staging (cloud) ─────────────────┐
  Student ── app ────▶ │ Screens: Onboard · My plan · Practice · Dashboard · Reveal   │
                       │ Pipelines: upload-syllabus · study-plan · feedback ·         │
                       │            dashboard · reveal · course-loaded (webhook)      │
                       │ Nodes: Wave agent · parser / LlamaParse · tool_cognee ·      │
                       │        graph_hydradb · db_hotdata · tool_python · tool_pipe  │
                       └───────┬────────────────────┬────────────────────┬────────────┘
                               │                    │                    │
                       Cognee server         HydraDB (hosted)       hotdata (cloud)
                       course-<code> (shared) collection shared      ledger (permanent)
                       student-<id> (private) collection <student>   run-<id> (expires 2h)
                               ▲                    ▲                    ▲
                               │                    │                    │
                       ┌───────┴────────────────────┴────────────────────┴────────┐
                       │ Laptop                                                   │
                       │  Rote Play load-course   (per course, replayed)          │
                       │  Rote Play run-backtest  (per run, replayed ~10×)        │
                       │  generic scripts: fetch · split · check · load · score   │
                       │  webhook → RocketRide "course-loaded"                    │
                       │  optional: open-source HydraDB (Docker) for "why" view   │
                       │  sealed/ folder (outside repo) — never read by code      │
                       └──────────────────────────────────────────────────────────┘
```

## Responsibilities in one sentence each

- **Cognee** turns raw documents into typed, topic-tagged knowledge; keeps course (shared) and student (private) datasets; turns scored sessions into lessons and preferences.
- **HydraDB (hosted)** durably stores and serves lessons, predictions and one memory collection per student, with recency-weighted retrieval.
- **hotdata** holds all tabular facts, computes the seven signals, finds homework echoes, isolates each backtest in its own database, and serves the scoreboard.
- **RocketRide** is the student-facing app and the agent pipelines that call the three data tools through their native nodes.
- **Rote** replays course loading and backtesting as fixed, cheap, inspectable Plays, and shows failure + resume.
- **Snyk** keeps the code clean continuously and documents it.

## Data flows

### F1 — Load a course (laptop, Rote)
1. `rote play run load-course course_url=<ocw> skip_list=<file> --yes --output=json`
2. Fetch index → list PDFs → download each (hash-named) → extract text → split into problems with points → validate (hard/degraded checks).
3. Load rows into hotdata `ledger` (`exam_items`, `homework_items`, `homework_vec`, `lectures`) with `load --mode replace|append`.
4. `remember` each item into Cognee dataset `course-<code>` with the custom graph model and strict ontology, `node_set=[course, term, doctype]`, `self_improvement=False`.
5. POST the RocketRide webhook `course-loaded` (auth key) so the dashboard refreshes.

### F2 — Run a backtest (laptop, Rote)
1. `rote play run run-backtest course=<c> target_exam=<id> --yes --output=json`
2. Create hotdata `run-<id>` (expires 2 h); load only rows dated before the target exam; build indexes (or use ledger indexes with date filters).
3. Run the saved signal queries; assert no leakage.
4. Read current lessons from HydraDB `shared` (recency-biased).
5. Rank topics; write the prediction; print SHA-256.
6. Score against the sealed answer key (read only now) and both baselines.
7. Append the run row to the ledger (idempotency key = run id).
8. Refit lessons; store new lesson memories in HydraDB `shared` with this run as evidence.
9. Score the run's Cognee session (`add_feedback`) and `improve()`.
10. Webhook → RocketRide dashboard refresh.

### F3 — Student uploads a syllabus (cloud, RocketRide)
Webhook/upload → parser (or LlamaParse) → Wave agent → `tool_cognee.remember` into `student-<id>` with the `Syllabus` model → `graph_hydradb.store_memory` (knowledge in the student's collection) → topic list and exam windows shown back to the student for confirmation.

### F4 — Student asks for a plan (cloud, RocketRide)
Chat → Wave agent → `graph_hydradb.recall_memory` (student's collection: preferences) → `db_hotdata.get_data` (latest sealed prediction for the course; `students` row) → `tool_cognee.recall` over `course-<code>` + `student-<id>` (why topics rank high; matching past problems) → the agent writes a plan in the student's style → answer's length logged.

### F5 — Feedback (cloud, RocketRide)
Button/text → `graph_hydradb.store_memory` with `infer:true` in the student's collection → Cognee `add_feedback` on the session → `improve()` (may be triggered from the laptop worker if the node doesn't expose it) → next F4 answer changes.

### F6 — Dashboard and reveal (cloud, RocketRide)
Dashboard pipeline reads the ledger (`runs`) via `db_hotdata` → three charts. Reveal pipeline: `tool_python` recomputes the hash of the stored prediction → loads the answer key (only for the reveal exam, only on stage) → marks hits/misses per problem with points.

## Network and trust boundaries

- The only inbound path to the laptop (Plan B) is the tunnel to the Cognee server, protected by a bearer token.
- RocketRide → HydraDB/hotdata use API keys stored as RocketRide secrets (`${ENV_VAR}` substitution) `[DOCS]`.
- The sealed folder is never mounted, uploaded or tunneled.

## Decisions to record at G0

1. Cognee location: Plan A (Cloud) or Plan B (laptop + tunnel).
2. RocketRide sponsor nodes present in `services-catalog.json`? If not → the agent calls REST APIs via `tool_http_request` (agent-only) instead.
3. Hosted HydraDB Cypher via `query_graph`? If not → "why" view uses Cognee, or open-source HydraDB on the laptop.
4. Rote replay from Python works? If not → keep the generic scripts as the recipe and use Rote to record the run-and-check steps around them.
