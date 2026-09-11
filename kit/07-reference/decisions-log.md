# Decisions Log

Append new entries at the bottom: time, decision, why, who. Entries below were made while planning (2026-09-10/11) before anything was built.

## Idea selection

| # | Decision | Why |
|---|---|---|
| 1 | Rejected the brief's ten suggested ideas (support desk, research assistant, recruiting, fraud, finance, legal, networking, coding agent, smart home, sales) | Every team gets the same list; entries converge |
| 2 | Rejected "Told Once" (data analyst corrected once, with a replay "flinch") | Solid fit but least surprising; weak live reveal |
| 3 | Rejected "Patch Once" (vulnerability fix replayed across repos) | Product owner found the gap: an identical fix across repos is just automation; redesigned into pattern learning, then set aside for a student-facing idea |
| 4 | Rejected stock-market ideas ("Broken Promises", "Hidden Exposure Map", "Why did it move?") | Product owner wanted a student-geared direction |
| 5 | Rejected "Mistake Memory", "Degree Path", "Application Autopilot", "Office Hours Memory" | Useful but no moment where the room reacts |
| 6 | **Chose Exam Oracle** | Five-second hook; a sealed, checkable reveal; measurable "smarter" vs baselines; natural fit for all five tools |
| 7 | Added the student layer (preferences, syllabus, guidelines, feedback) | Product owner requirement, 2026-09-11 |

## Architecture (from reading the vendors' docs and source)

| # | Decision | Why |
|---|---|---|
| 8 | Build the app from RocketRide pipelines using the `tool_cognee`, `graph_hydradb`, `db_hotdata` nodes | These nodes exist in RocketRide's docs/source; sponsor-native; judges check real use |
| 9 | Hosted HydraDB is primary; open-source HydraDB optional | RocketRide's node speaks the hosted API; per-student collections and `infer:true` preferences fit the student layer |
| 10 | Do not connect Cognee to open-source HydraDB through the Neo4j provider | Adapter uses APOC, constraints, UUIDs, list properties; HydraDB rejects all; a ~100-line projector if the Cypher graph is wanted |
| 11 | Use Cognee's v1 API (`remember`/`recall`/`improve`/`forget`) with custom models, strict ontology, datasets with permissions, sessions + feedback | Current API; the brief asks for remember/recall; these features carry lessons and preferences |
| 12 | One throwaway hotdata database per backtest | Creation under a second with expiry; makes leakage impossible by construction |
| 13 | Writes to hotdata are loads with idempotency keys | hotdata SQL is read-only |
| 14 | Course-specific logic in generic scripts; Rote records inside a workspace | A Play replays a fixed graph; improvisation would be baked in |
| 15 | The LLM never predicts the exam | It may have seen OCW exams in training; predictions come from the signal model |
| 16 | Reveal exam = 6.003 Fall 2011 Final | 6.003 has Quiz 1, Quiz 2, Final across three terms with solutions (verified) |
| 17 | Where things run: RocketRide staging, hotdata cloud, hosted HydraDB, one reachable Cognee server, Rote on the laptop | RocketRide's Python node can't run subprocesses; deployed apps run on staging |
| 18 | Snyk MCP added via project config, not the global configure command | The configure command writes Claude's global rules file |

## Open at G0 (fill in during the event)

- Cognee location: ____ (Cloud / laptop + tunnel)
- Sponsor nodes present in `services-catalog.json`: ____
- Hosted HydraDB `query_graph` accepts Cypher: ____
- Cross-dataset Cognee recall merges: ____
- Rote replay from Python works: ____
- hotdata per-run index build time: ____
- Course 2: ____ (18.06 earlier finals counted: __ ) / 2.71 / none
- Answer key SHA-256 for 6.003 Final Fall 2011: ____ (time: ____)

## 2026-09-11 — Person B, G0 (contracts + SDK corrections)

| Time | Decision / finding | Why | Who |
|---|---|---|---|
| G0 | **Contracts written** in `contracts/`: `ledger-schema.sql`, `prediction.schema.json`, `student.md`, `README.md`, synthetic fixtures for course FX.101 (all ledger tables, a fake prediction, 3 fake runs, human-shaped answer keys). Validated: prediction fixture passes the schema; the DDL executes in DuckDB; every fixture CSV header matches its table. | Unblocks A (ledger columns) and C (prediction + runs shapes) | B |
| G0 | **Time is ordered by `(term_seq, session)`, not dates.** `term_seq = year*10 + {1 spring, 2 summer, 3 fall}`; a row is visible to target T iff earlier term, or same term and earlier session. | OCW calendars often give session numbers, not dates | B |
| G0 | **CONCERN — single-term materials.** OCW publishes lecture notes/psets for one term only, so earlier-term backtests cannot legitimately use them. Runs are tagged `feature_set = full` (target in the published term) or `exam_history` (only x1 and x7). The kit's M1 run (6.641 Final 2008 ← 2006) is an `exam_history` run. | Using a later term's lectures to predict an earlier exam leaks the future; the leakage check would fail | B |
| G0 | **CONCERN — answer-key labeling load.** Every scored backtest needs a human-labeled key (~10–13 exams). Added `runs.key_source` (human/machine); `score.py` refuses without a human key unless `--allow-machine-key`, which marks the run as machine-graded. | "The AI never grades itself" is otherwise silently broken | B |
| G0 | **Contract additions:** `run_labels` (training labels written after the seal), `echo_pairs` (homework-echo precomputed once instead of per-run search), `lessons` (ledger mirror of HydraDB lessons for charts), `guidelines`, `guideline_tests`, `courses`, `topics`, `exams`. | Refit needs labels without editing sealed predictions; per-run index builds are an untested cost | B |
| G0 | **SDK fact — hotdata:** `hotdata-framework` managed loads require **Parquet** (CSV rejected by this client); idempotency = table keys + `mode="upsert"`; SQL inside a managed DB names tables `"default"."public"."<t>"`; env `HOTDATA_API_KEY`, `HOTDATA_WORKSPACE`, `HOTDATA_API_URL`. `ledger-schema.sql` is a column contract, not executable in hotdata (read-only SQL). | Read from installed source 0.14.0 | B → A |
| G0 | **SDK fact — HydraDB:** package `hydradb-sdk` 2.1.4 imports as `hydra_db`; memories via `context.ingest(type="memory", memories=<JSON string>, upsert="true")`; item `{id,title,text,infer,custom_instructions,additional_metadata}`; **one collection per query** → student + shared = two queries. | Installed source + HydraDB docs llms.txt | B → C |
| G0 | **SDK fact — Cognee 1.5.4:** feedback is `cognee.session.add_feedback(...)` (no top-level `cognee.add_feedback`); default storage is inside the installed package → set `DATA_ROOT_DIRECTORY`/`SYSTEM_ROOT_DIRECTORY`; default posture is authentication required + multi-tenant. | Installed source | B |
| G0 | **Ownership:** B writes `oracle/cognee_tag.py` (tagging onto the strict topic list); A's `load-course` Play calls it. | Tagging is Cognee/model work; the Play only sequences it | B ↔ A |
| G0 | **BLOCKED — live smoke tests.** This machine has no sponsor credentials (hotdata, HydraDB, Cognee LLM key) and its GitHub token is expired. B's code is built and tested offline (DuckDB + local stores); live paths are gated and marked `LIVE [TEST]`. | Needs team keys + repo access | B → team |
| Build | **Contract: `run_labels` key `(run_id, topic_id)`** added to contracts/README.md (same map as `oracle/backend.py` `TABLE_KEYS`); `homework_vec` stays keyless. No fixture change (there is no `run_labels` fixture). | `score.py` writes `run_labels` after the seal and Rote replays must be idempotent, which needs an upsert key | B |
