# Smoke Tests — Gate G0

Run these before building features. Split by role (A = loader/Rote/hotdata, B = Cognee/HydraDB, C = RocketRide/Snyk). Record each result and any fallback in `07-reference/decisions-log.md`. Full context for each tool is in `04-tools/<tool>.md`.

## RocketRide (C) — 15 min

| Step | Expect | If not |
|---|---|---|
| `pnpm exec rocketride login`; open `.env` | Non-empty `ROCKETRIDE_APIKEY` (or `ROCKETRIDE_AUTH`) | Re-run login; report if it keeps clearing |
| Search `services-catalog.json` for `tool_cognee`, `graph_hydradb`, `db_hotdata` | All three present | Fallback: Wave agent calls REST APIs via `tool_http_request` |
| `rocketride validate`; run a webhook → response pipe on staging | Response returned | Check source type (`send()` for webhook) |
| Chat → Wave pipe (LLM + `memory_internal` + three sponsor tools): "remember X; recall X; get_schema on both databases" | All three calls succeed from staging | Unreachable Cognee → Cloud or tunnel; unreachable HydraDB/hotdata → check keys/secrets |
| Upload one OCW PDF through `core/parser` | Readable Markdown (math may be garbled) | Try `llamaparse` |
| Deploy scratch app to @me; trigger a pipeline from App.tsx | Works; credits drop | Readiness panel; developer ID; `authenticated:false` |

## Cognee (B) — 15 min

| Step | Expect | If not |
|---|---|---|
| venv (3.12); `pip install "cognee[docs]==1.5.4"`; `LLM_API_KEY` | Imports | — |
| `remember` two problems with custom model + `node_set`, first `dry_run=True` | Cost estimate; then graph written | Check model definitions against 1.5.4 source |
| `get_graph_data()` | Topics only from the list; shared topic = one node | Set `identity_fields`; use strict ontology |
| Users admin/s1/s2; grant course dataset read | s1 recalls course + own; s1 → s2 raises `PermissionDeniedError` | If merge fails: two recalls, merge in agent |
| Session → `add_feedback("too wordy", 2)` → `improve()` with `PERSONALIZATION_ENABLED=true` | Preference text changed | Use HydraDB inferred preference for the demo |
| `forget(everything=True, user=s2)` | s1's data and sessions intact | Exclude deletion from demo |
| One MIT PDF through pypdf | Text readable enough to tag | `AdvancedPdfLoader` / docling, or tag from titles + text |

## HydraDB (B) — 15 min

| Step | Expect | If not |
|---|---|---|
| `pip install "hydradb-sdk>=2,<3"`; create database `exam-oracle` | OK | Check tier/keys |
| `collection=s1`: explicit pref (`infer:false`, fixed id), feedback (`infer:true`) | Both stored and recalled; inferred preference readable | — |
| Update pref by same id | Replaced, not duplicated | Use delete+store |
| Lesson in `shared`; query with `recency_bias`; one query across `s1`+`shared` | Works / note if cross-collection fails | Two queries |
| From RocketRide: `get_schema`, `query_graph` | Learn whether Cypher is accepted | "Why" view via Cognee or open source |
| (Optional) open-source container: UNWIND vertex+edge batches over Bolt, bounded path read, `algo.SSpaths`, lesson chain + `strong` read, restart persistence, one Cognee node via projector | All pass | Drop the Cypher graph (cut-list item 1) |

## hotdata (A) — 15 min

| Step | Expect | If not |
|---|---|---|
| `brew install hotdata-dev/tap/cli`; `hotdata auth register`; `workspaces list` | Workspace visible | — |
| Create expiring DB; load 50 homework rows; time | Seconds | — |
| Text index + vector index on separate tables, `--async`; time jobs | Build time noted | If slow: index once in ledger, date-filter queries |
| `bm25_search`, `vector_search`, fused CTE | Ranked results | BM25 only |
| Load a result from DB A into DB B (`--result-id`) | Works or not (note it) | Filter in worker, upload Parquet |
| Bulk-create 30 DBs | Fast | Create per run lazily |
| Inline ledger append twice with same `idempotency_key` | One row | Dedupe in worker |
| `hotdata-framework` query → DataFrame; `manage usage` | Works; credits visible | CLI with `-o json` |

## Rote (A) — 15 min

| Step | Expect | If not |
|---|---|---|
| Install; `rote login`; `rote whoami --check` | Logged in | Headless claim token expires in 30 min |
| `rote play run https://play.modiqo.ai/modiqo/hello@0.2.2 --output=json --yes` ×2 | JSON with timing (note token fields) | — |
| `rote init smoke --seq`; `rote proc run curl -s <OCW URL>`; pending write; export `--params course_url`; lint | Clean `main.ts` | Follow the compile rules in `04-tools/rote.md` |
| Replay with course-2 URL; then a bad URL | Success; then BLOCKED with evidence | — |
| `--resume <run_id>` after fixing | Continues | — |
| Call from Python `subprocess`; parse JSON; check exit code | Parsed; non-zero on failure | Fallback: generic scripts are the recipe; Rote records run-and-check around them |

## Snyk (C) — 10 min

| Step | Expect | If not |
|---|---|---|
| Enable Snyk Code in Settings | Enabled | `snyk code test` errors without it |
| Add MCP block to project config (not the global configure command); `snyk_auth`; `/mcp` shows tools | Tools listed | — |
| `snyk code test --report --project-name=exam-oracle` on the new repo | First snapshot | — |

## Data (A + labeler) — during G0

- All OCW PDFs for the chosen courses downloaded to `data/raw/<course>/` (hash-named) with `manifest.csv`.
- 18.06 Study Materials: count earlier finals → choose course 2.
- 6.003 Fall 2011 Final + solutions stored outside the repo; answer key labeled (`answer_key.csv`), points sum checked, SHA-256 logged.
