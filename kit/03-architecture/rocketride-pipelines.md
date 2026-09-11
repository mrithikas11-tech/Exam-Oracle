# RocketRide Pipelines and App Screens

Node and field names below follow RocketRide's public docs, which contradict themselves in places. **Before building, check each name in the workspace's `services-catalog.json`** (the setup guide's instruction). `[DOCS]` = listed in docs; exact config fields `[TEST]`.

## Global settings

- Secrets via `${ENV_VAR}` substitution in node config `[DOCS]`: LLM key, HydraDB API key, hotdata token/workspace, Cognee base URL + token.
- Wave agent (`agent_rocketride`) needs exactly one LLM and one memory node wired in; cap `max_waves` (default 10) to protect credits `[DOCS]`.
- `tool_cognee` and `tool_http_request` have no data lanes: only an agent can call them `[DOCS]`.
- `db_hotdata`: set `database_id` to the ledger so reads persist across runs; `allow_execute` only where raw SQL is needed; the node uses an LLM for text-to-SQL `[DOCS]`.
- Source type must match the call: `chat` source ↔ `client.chat()`, `webhook` source ↔ `client.send()`; a mismatch returns nothing `[DOCS]`.
- HTTP tool: 10 requests/second, 5 concurrent `[DOCS]`.
- Tracing shows token usage per call — capture screenshots for the "cheaper" slide `[DOCS]`.

## Pipelines

### P1 `upload-syllabus`
- **Source:** webhook (the app posts the file) — or `send_files` from the app.
- **Nodes:** `core/parser` (text/table/image; output flattened to Markdown, page boundaries lost) or `llamaparse` for equation-heavy PDFs → Wave agent with LLM + memory + `tool_cognee` + `graph_hydradb`.
- **Agent instructions (outline):** extract topics (12–20), exam windows, guidelines with numeric params using the Syllabus schema; `remember` into `student-<id>`; store the syllabus as knowledge in the student's HydraDB collection; return the topic list for the student to confirm.
- **Output:** JSON `{topics[], exams[], guidelines[]}` rendered for confirmation.

### P2 `study-plan` (chat)
- **Source:** chat.
- **Nodes:** Wave agent with LLM + `memory_internal` + tools: `graph_hydradb` (`recall_memory` on the student's collection), `db_hotdata` (`get_data`: latest prediction for the course, `students` row), `tool_cognee` (`recall` over course + student datasets), `tool_pipe` (optional sub-pipeline "schedule plan").
- **Agent instructions (outline):** use only the sealed prediction's ranked topics; schedule by p × points share into the days until the exam within weekly hours; write in the student's style (order, length, modality, pace); cite why each top topic ranks high using lessons; never claim certainty.
- **Guardrails:** max_waves ≤ 6; response length logged; no HTML in output.

### P3 `feedback`
- **Source:** webhook from the feedback button.
- **Nodes:** Wave agent (or direct node chain if `graph_hydradb` exposes a lane) → `graph_hydradb.store_memory` with `infer:true` in the student's collection; Cognee feedback via `tool_cognee` if exposed, otherwise the laptop worker calls `add_feedback` + `improve()` (webhook out).
- **Output:** the updated preference text (for the demo's before/after).

### P4 `dashboard`
- **Source:** webhook/timer from the app.
- **Nodes:** `db_hotdata` read of `ledger.runs` and `ledger.predictions` → `tool_python` shapes chart series.
- **Output:** series for smarter (model/even/last-exam points covered by run_seq), cheaper (tokens and seconds per course load and per run), reliable (checks_first_try, brier), n.

### P5 `reveal`
- **Source:** chat or button (on stage only).
- **Nodes:** `tool_python` (RestrictedPython; `hashlib` and `json` allowed; no network) recomputes SHA-256 of the stored canonical prediction; `db_hotdata` fetches the answer key rows (loaded to the ledger only at reveal time) → per-problem hit/miss with points.

### P6 `course-loaded` (webhook)
- **Source:** webhook with auth key, called by the laptop after `load-course` or `run-backtest`.
- **Nodes:** triggers P4 refresh; optionally posts a message (Slack node) "6.003 loaded: 212 items, 4.1 min, 0 model tokens".

## App screens (React in `src/App.tsx`, deployed to @me then @team)

| Screen | Shows | Calls |
|---|---|---|
| Onboard | Preferences form, exam date, hours, syllabus/guideline upload, confirmation of extracted topics | P1 |
| My plan | Day-by-day plan in the student's style; "too wordy" / "helped" / rating | P2, P3 |
| Practice (stretch) | Practice questions in the professor's style | P2 variant |
| Dashboard | Three charts with baselines and n; the sealed hash card; course library with load cost | P4 |
| Reveal | Hash recompute; real exam problems as hits/misses with points; "why" per hit | P5 (+ Cognee recall or Cypher path) |

## Deployment notes (from the official guide) `[DOCS]`

- Claim the developer ID on a scratch app first; app ids are `<developerId>.<name>`; developer IDs allow letters and underscores only.
- Readiness panel must be all green before deploy; a missing include path fails the server build.
- Versions are immutable; publish to @me to test, @team to share.
- While the local watch runs, the dev overlay hides the deployed build — close the App Builder panel to verify a published version.
- `"authenticated": true` in `appManifest` shows a sign-in wall in the preview; set false and handle signed-out state.
- "No authorization provided" → `pnpm exec rocketride login`.
- Calling pipelines from the browser: pass the pipeline object, not a file path (TypeScript SDK) `[DOCS]`.
