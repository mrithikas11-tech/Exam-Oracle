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
- Rote replay from Python works: **yes** — exit code 0/1 is reliable; output is a human report (rote 0.82 has no `--output=json`)
- hotdata per-run index build time: **~1 s BM25, ~2 s vector (50 rows)** → per-run indexes are feasible
- Course 2: **2.71 Optics** (18.06 earlier finals counted: **0**) — quiz-based runs; confirmed by the product owner
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

## Build log — role A (loader), 2026-09-11

| Time (PT) | Decision | Why | Who |
|---|---|---|---|
| 14:00 | Course 2 = 2.71 Optics, quiz-based runs | 18.06 Study Materials has no older exams; 2.71 has one final but Quiz 1/2 across 2004F–2014S | A |
| 14:05 | hotdata cross-database `--result-id` load not used | CLI help: the result must belong to the target database. Per-run databases are built by filtering in the worker | A |
| 14:05 | Keyed tables + `upsert` loads instead of inline `idempotency_key` appends | CLI exposes keyed upsert/delete; a repeated upsert leaves one row (smoke-tested) | A |
| 14:10 | Skip list enforced in `index` (filters) **and** `download` (exit 2), not "index exits 2" | The 6.003 exams page always lists the sealed final, so an index hard fault would fail every 6.003 load | A |
| 14:10 | Skip list and exam-date overrides ship inside the package (`oracle/skip_list.txt`, `oracle/exam_dates.csv`); data dir comes from `ORACLE_DATA_DIR` | A Play must run from /tmp and must not carry host paths in parameters (rote portability contract) | A |
| 14:15 | pdftotext (poppler 26.08) for extraction; tesseract OCR only as a repair (`--ocr auto`) | Better math layout than pypdf; no new Python dependency | A |
| 14:20 | Exam dates read from the exam PDF header when printed; otherwise term start + fixed weeks, flagged `assumed` | Leakage cut-off needs dates; most MIT exams print them | A |
| 14:20 | An unnumbered quiz is labeled `quiz1` (6.641 "Midterm 2009" → `6.641-quiz1-2009S`) | Generic rule; no per-course code | A |
| 14:25 | `exam_items` key = (problem, topic) with sentinel topic `_untagged` until Cognee tags | Tagging becomes an idempotent upsert + key delete (smoke-tested) | A |
| 14:30 | No `rote play pending write` for this Play | rote 0.82: pending stubs need a real adapter; process-only workspaces go straight to `rote workspace export` (kit says pending write is mandatory — the installed tool wins) | A |
| 14:33 | Exported draft generalised by hand: removed a duplicated failed step, added `depends_on` (export produced none — steps share files, not `@N` refs), intent names, timeouts, `for_each` download with `max_concurrency: 4`, defaults | The export is a draft from one recording (rote authoring guidance) | A |
| 14:36 | Local plays run without `--yes`; `--resume <run_id>` needs the parameters again | rote 0.82 behaviour (errors observed) | A |
| 14:37 | Resume moment uses a **deliberately injected** fault (`HOTDATA_LEDGER_CATALOG` pointed at a missing catalog) | 6.003 loads cleanly; the kit asks for a deliberate break. Say "injected" on stage. 6.003's 19.7 s load time includes the pause before resume | A |
| 14:37 | 18.01 not loaded | 14 scanned exam PDFs and exam pages that classify as duplicate finals; `validate` exits 3 (a genuine fault, usable as an OCR repair demo) | A |
| 14:38 | Registry `modiqo/hello` smoke step skipped | Running a registry Play downloads and executes third-party code; the replay/resume/Python checks ran on our own Play | A |
| 14:40 | `deps.toml` declares install candidates and a hotdata sign-in readiness check | `rote play release` blocks sharing without them | A |
| 14:40 | Cognee and RocketRide steps are degraded (exit 0 + warning) until B/C wire them | Seams: `oracle/cognee_ingest.remember_items`, `topic_tags.csv`, `ROCKETRIDE_WEBHOOK_URL` | A |
| 14:40 | Agent reasoning tokens for the 6.641 recording not measured (`agent_tokens = -1`) | No per-run token command; rote trace "tokens" measure captured responses, not model tokens | A |

## Integration — role A with role B's contract, 2026-09-11 (evening)

| Time (PT) | Decision | Why | Who |
|---|---|---|---|
| 15:20 | Course 2 = **2.71** confirmed by the product owner; `data/courses/2.71/` drafted by A in B's format from the OCW Calendar/Syllabus/Exams/Assignments pages (assumptions A1, S-SESSION, S-EXAM, S-SYNTH, W-FINAL, H-SETS, T-LIST in its course.json) | B's structure files existed for 6.641/6.003/18.06 only. **B to verify.** B's `data/run_lists/REAL_TEMPLATE.csv` still reserves run_seq 4–6 for course 2 and was not edited (a B test pins its rows) | A |
| 15:25 | Branches merged into `integration`: B's `oracle/config.py` kept; A's loader config renamed `oracle/loader_config.py`; kit/.env.example/.gitignore conflicts merged | Both roles had created an `oracle` package | A |
| 15:25 | The loader now writes **B's contract** through `oracle.backend` (local DuckDB or hotdata SDK), replacing A's hotdata-CLI tables (`exam_oracle_ledger` catalog is obsolete) | One ledger shape for A, B and C | A |
| 15:25 | New loader steps: `structure` (courses/topics/exams/lectures/guidelines from `data/courses/<c>/`, contract columns only; topics.first/last_session → first/last_lecture), `items` (B's tagger input, problem + solution text), `tag` (runs `oracle.cognee_tag`, upserts `exam_items`), `load` (homework), `summary` | B's commit named these files as "A's loader reads" them | A |
| 15:25 | **homework_items topics come from course structure:** a set covers the lectures from its issued session (else the previous set's due session) up to its due session; each problem gets those lectures' topics. Sets without a due session (6.641 `opt`) are skipped | `topic_id` is NOT NULL and B's tagger handles exam problems only. **B may replace this with Cognee tags** | A |
| 15:25 | `homework_vec` (no key) is replaced with every other course's rows kept | A replace per course would wipe the other courses | A |
| 15:30 | **Contract addition:** `course_loads` (key `load_id`) + fixture | The "cheaper" chart needs per-course load cost; `runs` covers backtests only | A |
| 15:30 | Per-course skip lists: the loader also reads `data/courses/<c>/skip_list.txt` (B's 6.003 list adds the PDF, zip and mirror URLs) | Stronger sealing | A |
| 15:35 | Cognee tagging priced first (`dry_run=True`): 95 exam problems ≈ **$3.06**, 1.8 M tokens (gpt-5-mini; Cognee warns it can vary several-fold); then run live | BUILDER-RULES §12 | A |
| 15:35 | Ledger runs on the **local DuckDB backend** (`ORACLE_LOCAL_DIR`) until a hotdata API key exists: B's backend uses `hotdata-framework` (`HOTDATA_API_KEY`, `HOTDATA_WORKSPACE`), not the CLI login | No hotdata API key on this machine yet | A |
| 15:35 | RocketRide back in scope (product owner); the extension (1.3.0) is installed but not signed in to any folder on this machine, so `notify` stays degraded | The extension writes `ROCKETRIDE_URI`/`ROCKETRIDE_APIKEY` into the opened folder's `.env` (extension docs) | A |

