# Role A — Loader: status, runbook, handoffs

Built 2026-09-11 on the `person-a-loader` branch. Everything below was run and checked; numbers are measured.

## Task status (from `kit/05-build/timeline-and-roles.md`)

| Task | Status | Evidence |
|---|---|---|
| Rote smoke test | Done | Record → export → lint → replay on a new course → injected fault → BLOCKED → `--resume` → called from Python `subprocess` (exit code checked). Registry `hello` Play not run (see decisions log). |
| hotdata smoke test | Done | 50-row load 1.3 s; keyed upsert twice = 1 row; BM25 index ~1 s; vector index ~2 s; `bm25_search`, `vector_search`, RRF-fused CTE all return ranked rows; cross-database `--result-id` load: not supported (CLI: result must belong to the target database) |
| Download all PDFs | Done | 6.641: 53 files, 6.003: 73, 2.71: 38 — hash-named under `$ORACLE_DATA_DIR/raw/<course>/`, manifests in `data/manifests/` |
| Count 18.06 earlier finals | Done: **0** | The Spring 2010 Study Materials page has no PDFs → course 2 = **2.71 Optics** (1 final, but Quiz 1 in 2004F/2008S/2012S/2014S and Quiz 2 in 2009S/2012S/2014S → quiz-based runs) |
| Generic split / check scripts | Done | `oracle/` — same code for all courses; exams split with points and printed totals; real exam dates read from the PDFs |
| Record `load-course` on 6.641; export + lint | Done | Workspace `eo-load-course`, 10 recorded steps, 14.4 s; exported, generalised, `rote play lint` passes; `docs/traces/load-course-6.641-recording.html` |
| hotdata ledger loads | Done | Ledger `exam_oracle_ledger` (see counts below) |
| Replay on course 2; log cost | Done | 2.71 replay: 10/10 steps, 11.2 s, 0 model tokens; row in `ledger.course_loads`; `docs/evidence/replay-2.71.txt` |
| Replay on 6.003 with skip list; resume moment | Done | 2 sealed resources excluded; first run with a **deliberately injected** fault (ledger catalog pointed at a non-existent name) → `load` FAILED with evidence, `tags`/`summary`/`notify` BLOCKED; fixed; `--resume` restored 6 steps and ran 4; `docs/evidence/replay-6.003-*.txt`; `docs/traces/load-course-replays-2.71-6.003.html` |
| Publish `load-course`; export trace | Released locally + traces exported. **Registry push waiting for approval** (it publishes to the Rote catalog and needs a Rote handle). |
| `run-backtest` Play | Not started — role B records it (`kit/05-build/timeline-and-roles.md`, 2:00–3:00) |

## Ledger contents after the loads

| Table | 6.641 | 2.71 | 6.003 |
|---|---|---|---|
| `exam_items` (one row per problem until tagged) | 23 (7 exams) | 20 (8 exams) | 52 (10 exams) |
| `homework_items` / `homework_vec` | 35 | 29 | 81 |
| `lectures` | 19 | 12 | 25 |

`course_loads`: 6.641 agent 14.4 s · 2.71 replay 11.2 s · 6.003 replay 19.7 s (includes the pause before the resume) · 6.641 replay 8.2 s (post-release smoke run; downloads cached from the recording).
Every exam's points sum to its printed total (or 100 when points are percentages/unprinted → `points_source=equal`).
No row, text or metadata for `6.003-final-2011F` exists anywhere.

## Runbook

```bash
brew install poppler hotdata-dev/tap/cli && hotdata auth login
uv tool install git+https://github.com/mrithikas11-tech/Exam-Oracle
export ORACLE_DATA_DIR=~/exam-oracle-data
exam-oracle ledger-init
cp -R plays/load-course ~/.rote/flows/
cd /tmp && rote play run load-course course=6.003 course_url=https://ocw.mit.edu/courses/6-003-signals-and-systems-fall-2011/
# after a failure: fix, then repeat the same command with --resume <run_id> (params are required again)
```

Individual steps are also plain commands: `exam-oracle index|download|extract|split|validate|load|cognee|tags|summary|notify --course <c>`.
Every command prints one JSON object and uses the kit's exit lanes: 0 ok (a `warning` field = degraded),
1 hard fault, 2 sealed URL/hash, 3 validation failed.

**Resume demo on stage:** `HOTDATA_LEDGER_CATALOG=exam_oracle_ledger_unreachable rote play run load-course course=… course_url=…`
→ BLOCKED; then the same command without the variable plus `--resume <run_id>`. Say it is injected.
A genuine alternative: 18.01 (Fall 2006) has 14 scanned exam PDFs; `validate` exits 3 on them, and
`exam-oracle extract --course 18.01 --ocr auto` (tesseract) is the repair step.

## Handoffs

**To B (Cognee / HydraDB / signals):**
- Items to tag: `$ORACLE_DATA_DIR/work/<course>/cognee_items.jsonl` — one item per exam problem (+ its solution text),
  homework problem and lecture; `node_set = [course, term, doctype]`; ids match the ledger keys.
- Wire Cognee by adding `oracle/cognee_ingest.py` with `remember_items(items, dataset_name, dry_run) -> dict`;
  the `cognee` step calls it when `LLM_API_KEY` or `COGNEE_BASE_URL` is set (a raise = hard fault).
- Write tags to `$ORACLE_DATA_DIR/work/<course>/topic_tags.csv` (`item_id,topic`, one row per topic);
  `exam-oracle tags --course <c>` then upserts `(problem, topic)` rows with points split equally and deletes the `_untagged` rows.
- Ledger keys are declared for your tables too: `runs(run_id)`, `predictions(run_id, topic)`, `guidelines(guideline_id)`,
  `guideline_tests(guideline_id, run_id)`, `students(student_id)`. Load with `hotdata databases load --catalog exam_oracle_ledger --table <t> --file <csv> --mode upsert`.
- Leakage: every row has an ISO `date` (from the exam PDF where printed — `date_source` in the manifests); build `run-<id>` databases from `date < target`.

**To C (RocketRide):**
- Set `ROCKETRIDE_WEBHOOK_URL` and `ROCKETRIDE_WEBHOOK_KEY`; `notify` POSTs
  `{"event":"course-loaded","course":…,"summary":{…course_loads row…},"validate":{…}}`.
  The auth header defaults to `Authorization: Bearer <key>` — override with `ROCKETRIDE_WEBHOOK_AUTH_HEADER` once you see the node's convention.
- Dashboard "cheaper" chart: `SELECT * FROM exam_oracle_ledger.public.course_loads`.

## Known limits

- Topic tags are empty until B's Cognee step runs (items carry `topic = _untagged`).
- The 6.641 recording's agent reasoning tokens were not measured (`agent_tokens = -1`); replays use no model tokens.
- Homework and lecture dates are assumed from the term start (`oracle/config.py`); exam dates come from the PDFs except
  6.641 Final 2008/2009 and 2.71 Quiz 1 2004F/2008S, Quiz 2 2009S (assumed; fill `oracle/exam_dates.csv` if the labeler finds them).
- Skip-list hashes of the sealed PDFs are not filled in — only a human with the sealed folder should add them.
