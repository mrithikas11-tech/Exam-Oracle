# B_README: Model & Memory (Person B)

What Person B built for Exam Oracle, how to run it offline, how to take it live, how to run a run list, and what
A and C need from it. The contracts in `contracts/` override the kit wherever they differ.

**Status (2026-09-11, after integration).** Everything runs and is tested offline (`pytest`, full suite green).
Decisions D1-D9 are in the code except what section 9 lists as deferred. Local DuckDB stands in for hotdata and
local JSON files stand in for HydraDB. The build machine has no sponsor credentials, so no live call has ever run:
hotdata, HydraDB, Cognee and the optional LLM calls are gated by environment variables and marked `# LIVE [TEST]`
in the code (list in section 7). No real exam content and no human answer key exists yet: the real courses are
checked as STRUCTURE only (`data/courses/*`, `tests/test_structure.py`, `tests/test_real_visibility.py`).

## 1. What B built

| Area | Module (`python -m oracle.<x>`) | Job |
|---|---|---|
| Foundation | `config` | paths, env and `.env`, backend/store selection, the `SEALED_DIR` rule, id and OCW-URL validators, canonical JSON + SHA-256, exit codes, `ScriptParser` (a usage error prints JSON and exits 1) |
| | `ordering` | the time rule: `term_seq`, visibility by `(term_seq, session)`, `feature_set` |
| | `backend` | the ledger and per-run databases: `LocalDuckDBBackend` (offline) or `HotdataBackend` (live); SQL uses `{{table}}` and `$param` |
| | `lessons_store` | the beta lessons, one version per `run_seq`: local JSONL or HydraDB `shared`; the cold-start weights |
| Play 2 steps | `make_run_db`, `run_signals`, `leakage_check`, `read_lessons`, `rank_and_seal`, `score`, `append_ledger`, `refit_lessons`, `store_lessons`, `cognee_feedback` | one script per Rote step; the signal, baseline and leakage SQL is in `sql/` |
| Orchestration | `backtest` | Play 2 as one command, or one `--step` of it |
| | `run_list` | a CSV of backtests, validated forward in time, then run in order |
| | `fixture_ledger` | rebuilds the LOCAL ledger from the synthetic course FX.101 |
| Course structure | `structure` | checks `data/courses/<course>/` and maps it to ledger tables (`load_course`, A's only entry point; `--check` exits 3 on a bad folder) |
| Precompute | `echo`, `text_clean` | `echo_pairs` (the input of x3, computed causally); the LaTeX-stripped `text_clean` columns |
| Cognee / HydraDB | `models`, `ontology`, `cognee_tag`, `students` | DataPoint models, the topic ontology, tagging exam problems onto the fixed topic list, student preferences, feedback, the self-description box (D9: `describe` / `extract` / `confirm`) and system prompts |
| Contracts | `contracts/` | ledger DDL, prediction schema, student contract, learning-profile schema, course-structure contract, FX.101 fixtures (owner: B) |

The model decisions in the code (full text in `kit/07-reference/decisions-log.md`):
- **D1/D2 time:** visible iff an earlier term, or the same term and an earlier session. An exam that shares a
  calendar row with a lecture gets the last delivered session + 1, so "after exam E" is `session >= E.session`.
  Homework is visible by its due session.
- **D3:** `feature_set = full` iff the target is in the published term, else `exam_history`. Nothing is forced to
  zero: every signal comes from the rows the target can see.
- **D4:** beta is refit only on `full`, non-cold-start runs.
- **D5:** x1..x7 are z-scored per run across the course's predictable topics before beta, in sealing and refit
  alike. Predictions keep the raw x and record `"standardization": "zscore_per_run_v1"`.
- **D6:** T00 ("Off-list") is never ranked, predicted, or counted in K or a signal. Its answer-key points stay in
  the scoring denominator (`off_list_pts`).
- **D7:** `homework_analogous` guidance gives x7 to the topics with visible homework, at a learned trust
  (default 0.5). `score` writes its `guideline_tests`.

Tests live in `tests/`. `conftest.py` forces local mode, a temporary `ORACLE_LOCAL_DIR` and
`SEALED_DIR = contracts/fixtures/sealed`, and removes live credentials, so no test reaches a service or an LLM.

## 2. Offline quick start

Verified from the repo root on 2026-09-11. `fixture_ledger --replace` wipes every table of the local ledger, so
point `ORACLE_LOCAL_DIR` at a demo folder:

```bash
cd exam-oracle
export ORACLE_SKIP_DOTENV=1 ORACLE_BACKEND=local LESSONS_STORE=local STUDENT_STORE=local
export ORACLE_LOCAL_DIR=/tmp/oracle-demo            # default: <repo>/.local-backend (git-ignored)
export SEALED_DIR="$PWD/contracts/fixtures/sealed"   # the only in-repo sealed folder allowed: synthetic FX.101 keys
.venv/bin/python -m oracle.config                    # resolved settings, no secret values
.venv/bin/python -m oracle.fixture_ledger --replace  # ledger = contracts/fixtures/*.csv
.venv/bin/python -m oracle.echo --course FX.101 --prune
.venv/bin/python -m oracle.run_list --list data/run_lists/fixture.csv --dry-run
.venv/bin/python -m oracle.run_list --list data/run_lists/fixture.csv
.venv/bin/python -m pytest -q -p no:cacheprovider
```

The fixture list makes five runs (four evidence runs and one cold-start twin), all exit 0, and writes lessons
versions 1, 2, 3 and 5. The twin writes none. Versions 1-3 keep the cold-start weights: runs 1-2 are
`exam_history`, which refit leaves out (D4), and run 3 is the only full run so far, while a refit needs at least 2.
Version 5 is the first refit, and refit's warning says why whenever it keeps the weights. Every script prints ONE
JSON object on stdout.

Where things land under `ORACLE_LOCAL_DIR`:

| Path | What |
|---|---|
| `ledger.duckdb` | the local ledger (every contract table) |
| `predictions/<run_id>.prediction.json` and `.sha256` | the sealed prediction (pretty JSON) and the SHA-256 of its canonical form |
| `predictions/<run_id>.score.json` | what `score` printed (written only after the seal) |
| `lessons.jsonl` | the local lessons store, append-only |
| `backtests/<run_id>.attempts.jsonl` | one line per chain attempt (feeds `checks_first_try`) |
| `runs/run-<run_id>.duckdb` | the per-run database, dropped at the end of every chain |
| `students/`, `cognee/` | the local student store; Cognee's data and system roots |

`predictions/` stays on the laptop even in live mode. Back it up: the submission needs the sealed file.

## 3. One backtest

```bash
.venv/bin/python -m oracle.backtest --course FX.101 --target-exam FX.101-quiz1-2021F --run-seq 1
```

`run_id = r<run_seq>-<target_exam>`. The chain is make_run_db → run_signals → leakage_check → read_lessons →
rank_and_seal → score → append_ledger → refit_lessons → store_lessons → cognee_feedback → drop_run_db. Each step is
that script's own `main()`, so its JSON and exit code are exactly what `python -m oracle.<step>` prints. The chain
stops at the first non-zero exit, exits with THAT code and prints the failing step's JSON as `evidence`;
`drop_run_db` runs anyway. A degraded step (exit 0 with `warning` or `skipped`) does not stop it.

Flags:
- `--cold-start`: the blank-weights twin. It seals and scores a comparison, and writes no lessons version.
- `--allow-machine-key`: with no human key, score against the ledger's tags and stamp `key_source = machine`.
- `--allow-machine-labels`: let refit train on machine-keyed runs. Off by default: the AI never grades itself.
- `--made-at ISO`: the seal timestamp. A replay keeps the one already sealed.
- `--seal-only`: stop after rank_and_seal, then drop the run database. Nothing is scored and no answer key is
  opened. A later plain run of the same run_id is a replay: the same lessons and the kept `made_at` give the same
  hash, and it goes on to score. Used for the reveal (section 6).
- `--frozen`: score and record, but skip refit_lessons and store_lessons. Beta is frozen once the reveal
  prediction is sealed (kit/05-build/staging-plan.md).

Guards built into the chain:
- A run_seq already held by another run_id is refused (exit 1) before anything runs, because a lessons version
  IS a run_seq.
- `read_lessons --before-run-seq N`, so a replay of run N never uses lessons learned from run N or later.
- The run facts are measured, never estimated. `seconds` is the wall clock from make_run_db to the end of score.
  `tokens` is 0 unless refit's optional LLM rewording spends some. `checks_first_try` turns false once any
  earlier attempt of that run_id failed.

Exit codes (`oracle/config.py`):

| Code | Meaning | Raised by |
|---|---|---|
| 0 | ok, possibly degraded (`warning` / `skipped`) | every step |
| 1 | hard fault; also every usage error | any script |
| 2 | skip-listed (sealed) URL or sealed exam refused | A's `ocw_index`, `cognee_tag` |
| 3 | invalid answer key, or validation failed | `score`, `cognee_tag` |
| 4 | leakage | `leakage_check`, `rank_and_seal` |
| 5 | prediction unsealed or tampered: refused | `score`, `append_ledger` |
| 6 | no human answer key | `score` |
| 7 | no `LLM_API_KEY` for a live or dry Cognee run | `cognee_tag` |

### One step at a time

`--step NAME` runs only that step with the same parameters and prints that step's own JSON. Earlier outputs are
passed as inline JSON, a file path, or `-` (stdin, once). The full sequence (checked by
`tests/test_e2e.py::test_chain_equals_the_step_by_step_commands`, which gets the same hashes and lessons as the chain):

```bash
B=".venv/bin/python -m oracle.backtest --course FX.101 --target-exam FX.101-final-2022F --run-seq 5"
$B --step make_run_db                                              > make_run_db.json
$B --step run_signals   --run-db make_run_db.json                  > run_signals.json
$B --step leakage_check --run-db make_run_db.json                  > leakage_check.json
$B --step read_lessons                                             > read_lessons.json
$B --step rank_and_seal --signals run_signals.json --lessons read_lessons.json > rank_and_seal.json
$B --step score                                                    > score.json
$B --step append_ledger --leakage leakage_check.json --score score.json --tokens 0 --checks-first-try true
$B --step refit_lessons                                            > refit_lessons.json
$B --step store_lessons --refit refit_lessons.json
$B --step cognee_feedback
$B --step drop_run_db   --run-db make_run_db.json
```

On hotdata, always pass make_run_db's output as `--run-db`. Database names are not unique there, so the default
lookup by name is ambiguous. In step mode `seconds` stays NULL unless you pass a measured value; never pass an
estimate.

## 4. Recording Play 2 in Rote

Record from the repo root inside a Rote workspace (`rote init oracle --seq`): one `rote proc run` per step,
referencing earlier outputs as `@N` and never pasting values (kit/03-architecture/rote-plays.md). The Play's
parameters are `course`, `target_exam` and `run_seq` (+ `cold_start`). The target's time comes from its ledger
`exams` row and its key from `SEALED_DIR`, so there is no `target_date` or `sealed_key_path` parameter.

```
rote proc run .venv/bin/python -m oracle.backtest --course <c> --target-exam <e> --run-seq <n> --step make_run_db  # @1
rote proc run .venv/bin/python -m oracle.backtest --course <c> --target-exam <e> --run-seq <n> --step run_signals --run-db @1
...                                                                     # one record per step, in the order of section 3
```

Not verified (LIVE [TEST]):
- **How Rote splices `@N` into an argument.** If it cannot, record the whole chain as ONE step
  (`rote proc run .venv/bin/python -m oracle.backtest ...`). The chain's own JSON still names the failing step and
  carries its evidence, but the Play loses per-step BLOCKED/`--resume`.
- **Publishing from `/tmp`.** There is no `pyproject.toml` yet, so `python -m oracle.*` works from the repo root
  only. Running from anywhere else also needs `ORACLE_REPO_ROOT`.

## 5. Run lists

`python -m oracle.run_list --list <csv> [--dry-run] [--from-seq N] [--through-seq M] [--seal-only | --frozen]
[--allow-machine-key] [--allow-machine-labels]`

The file is a CSV with one header and one row per backtest; lines starting with `#` are comments. The columns are
`run_seq` (integer, strictly increasing down the file, gaps allowed), `course`, `target_exam`, `cold_start`
(`true`/`false`) and an optional `feature_set` annotation, which is checked. Other columns (e.g. `notes`) are
ignored. Only rows with `--from-seq <= run_seq <= --through-seq` run; `--seal-only` and `--frozen` are passed to
every backtest the list runs.

Validation happens before the first run. Any violation exits 1 and nothing runs; every problem is listed:
- **Target:** it is in the ledger's `exams`, in the row's course, and that course has a `courses` row.
- **run_seq:** strictly increasing, and not held by another run_id in the ledger's `runs`.
- **Forward in time within a course:** every non-twin run is strictly later, by `(term_seq, session)`, than every
  earlier non-twin run of the course. That covers the list's rows AND the runs already in the ledger. A list row
  whose run_id is already in the ledger is a replay, and its `cold_start` must match.
- **Forward in time across courses, by term:** lessons transfer across courses, so a non-twin run may not target
  an earlier term than an earlier run of another course. Runs of two courses in the same term are not ordered
  against each other, because sessions are numbered per course.
- **Cold-start twin:** it repeats an earlier non-twin target, one twin per target; its own time is exempt.
- **`feature_set` annotation:** if present, it must equal what the ordering rule gives.

There is no side-track column and no exemption. The D8 history side track is made of ordinary `exam_history`
rows (`cold_start = false`) whose run_seqs sit at their place in time, so the checks above apply to them unchanged.

Procedure:
1. A has loaded the ledger, and `python -m oracle.echo --course <c> --prune` has run for every course in the list.
2. Run with `--dry-run` and fix every problem it lists.
3. Run the list. The first failure stops it. The list exits with that run's code, and the JSON carries
   `failed_run`, `failed_step` and `evidence`.
4. Fix the cause, then rerun the same list (or pass `--from-seq N`). Runs that already ran are replays: the same
   lessons (`--before-run-seq`), the same `made_at` and therefore the same hash. Their ledger rows are upserted,
   not duplicated.
5. Never renumber a run that has run. Add new rows with new `run_seq`s.

`data/run_lists/fixture.csv` is the FX.101 demo. The real lists (decision D8, `contracts/course-structure.md`
§10) are `data/run_lists/real_chain.csv` and `data/run_lists/history_side_track.csv`. `REAL_TEMPLATE.csv` is the
kit's superseded list: never run it.

| run_seq | Target | List | feature_set |
|---|---|---|---|
| 1-3 | 6.641 Final 2006 → Quiz 1 2008 → Final 2008 (the kit's former M1) | side track | exam_history |
| 10 | 6.641 Midterm 2009 (`quiz1` slot) = **M1** | chain | full |
| 20 | 6.641 Final 2009 | chain | full |
| 21-23 | 6.003 Quiz 1 → Quiz 2 → Final, Spring 2010 | side track | exam_history |
| 30-60 | 18.06 Exams 1, 2, 3 and Final, Spring 2010 | chain | full |
| 70, 71 | 6.003 Quiz 1 Fall 2011, then its cold-start twin | chain | full |
| 80, 90 | 6.003 Quiz 2 and Quiz 3, Fall 2011 | chain | full |
| 100, 101 | 6.003 Final Fall 2011 (THE REVEAL, sealed), then its cold-start twin | chain | full |

The run_seq gaps put every side-track run at its place in time. So `read_lessons --before-run-seq N` hands each
run only lessons learned from earlier terms, and both lists pass `run_list`'s checks together, in either order.
The side-track runs never train (D4), but each writes a lessons version that carries the earlier full runs'
weights: 1-3 the cold-start weights, 21-23 those of version 20. Plot them apart, labelled "history-only".
`tests/test_e2e.py::test_real_lists_are_forward_lists` and `tests/test_structure.py` check both lists against
`data/courses/*`. `tests/test_real_visibility.py` builds the run database of all 18 runs from the real
structure and checks each one against D1/D2/D3.

Order on the real ledger: the side track first if you want it (`--list history_side_track.csv --through-seq 3`),
then the chain with `--through-seq 20`, then side-track rows 21-23, then the chain with `--from-seq 30
--through-seq 90`, then section 6. `run_list` refuses any order that would break forward-in-time.

## 6. The reveal (6.003-final-2011F)

D8: the reveal's main run and its cold-start twin are both sealed before either is scored, and beta is frozen once
the reveal prediction is sealed (kit/05-build/staging-plan.md). A plain chain scores straight after its seal, so
the reveal rows run in passes. With `real_chain.csv` (reveal = run 100, twin = run 101):

0. **Everything before the reveal:** `python -m oracle.run_list --list data/run_lists/real_chain.csv --through-seq 90`.
1. **Seal, hours before:** `python -m oracle.run_list --list data/run_lists/real_chain.csv --from-seq 100 --seal-only`. Each backtest stops
   after rank_and_seal and drops its run database. Nothing is scored and no answer key is opened. Both
   `predictions/<run_id>.sha256` files exist, and every `predictions` row carries `hash` and `made_at`, which is what
   the dashboard's hash card reads. There is no `runs` row yet: it needs a `key_source`.
2. **On stage, recompute the hash** from the stored file, in Python, because the canonical form depends on
   Python's float formatting. It must equal `predictions/<run_id>.sha256`:
   ```bash
   .venv/bin/python -c "import hashlib,json,sys; o=json.load(open(sys.argv[1],encoding='utf-8')); print(hashlib.sha256(json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest())" "$ORACLE_LOCAL_DIR/predictions/<run_id>.prediction.json"
   ```
3. **Then score and record:** `python -m oracle.run_list --list data/run_lists/real_chain.csv --from-seq 100 --frozen`. Both rows replay:
   the same lessons (`--before-run-seq`) and the kept `made_at` reproduce the sealed hash (rank_and_seal refuses a
   different prediction for a sealed run_id), then score, append_ledger and cognee_feedback run. refit_lessons and
   store_lessons are skipped (`--frozen`; the twin skips them anyway).

`tests/test_e2e.py::test_reveal_seals_main_and_twin_before_either_is_scored` runs exactly these passes on the
fixture's sealed exam, FX.101-final-2022F, with a file-open spy: no answer key is opened while sealing, the replays
keep both seals, each key is read only after its hash file exists, and no lessons version is written.

Step-by-step fallback (one Rote record per step, section 3): for each of the two run_ids, run make_run_db,
run_signals, leakage_check, read_lessons, rank_and_seal and drop_run_db, and keep the JSON files. On stage, run
`--step score`, then `--step append_ledger --leakage leakage_check.json --score score.json --tokens 0
--checks-first-try true` (pass `false` if any earlier attempt of this run_id failed). Do not run refit_lessons or
store_lessons after the reveal.

If a chain is run anyway while `SEALED_DIR` has no key, it stops at `score` with exit 6, after sealing. The
prediction stays sealed, no `runs` row is written, and step 3 finishes it later.

D8's third arm, beta_G frozen (the lessons frozen after 18.06, used as a third prediction on the 6.003 targets), is
DEFERRED: it is in neither the code nor the list (section 9).

## 7. Going live

Environment variables (names only; `.env.example` lists them all, and `.env` is git-ignored):

| Variable | Live value | Effect |
|---|---|---|
| `ORACLE_BACKEND` | `hotdata` | the ledger and the per-run databases go to hotdata |
| `HOTDATA_API_KEY`, `HOTDATA_WORKSPACE`, `HOTDATA_API_URL` (optional) | team keys | read by `HotdataClient.from_env()` |
| `HOTDATA_LEDGER_DB` | `ledger` at first, then the ledger's id | the ledger is found by name or created (every table and key, no expiry). Names are not unique, so switch to the id once it exists |
| `LESSONS_STORE` | `hydradb` | lessons go to HydraDB collection `shared` (`lesson_<signal>_r<seq>` plus the pointer `lessons_latest`) |
| `STUDENT_STORE` | `hydradb` | one HydraDB collection per student (`s<n>`) |
| `HYDRADB_API_KEY`, `HYDRADB_BASE_URL` (optional), `HYDRADB_DATABASE` (`exam-oracle`) | team keys | `HydraDB(token=..., base_url=...)` |
| `LLM_API_KEY` (+ `LLM_PROVIDER`, `LLM_MODEL`, `LLM_ENDPOINT`, `EMBEDDING_API_KEY`) | team keys | enables `cognee_tag` (exit 7 without it), `cognee_feedback` (`skipped` without it), and refit's optional statement rewording |
| `SEALED_DIR` | a folder OUTSIDE the repo | the human answer keys. `score` refuses a folder inside the repo (the fixture folder is the only exception). Never upload it or put it behind a tunnel |
| `ORACLE_LOCAL_DIR` | optional | predictions, attempts and the Cognee roots stay here in live mode too |
| `ORACLE_REPO_ROOT` | the repo path | needed when the package runs from outside the repo (a Rote Play from `/tmp`) |

Order of operations:
1. `python -m oracle.config` to check the resolved settings.
2. The G0 smoke tests in kit/05-build/smoke-tests.md: B runs Cognee and HydraDB, A runs hotdata.
3. A loads the ledger (section 8). Then run `python -m oracle.echo --course <c> --prune` once per course.
4. `python -m oracle.run_list --list <list> --dry-run`, then run the list.

These paths are written against the installed SDK source and have never run against the service (LIVE [TEST]):
- **hotdata (`oracle/backend.py`):**
  - finding or creating the ledger by name
  - `load_managed_table` (Parquet, upsert)
  - `execute_sql`: a truncated result is kept as a warning, and refit stops on it
  - the run-database `expires_at` format
  - `delete_managed_database`
- **SQL (`sql/*.sql`):** checked on DuckDB only. The hotdata dialect is untested for `power`, `concat_ws`,
  `least`, `nullif`, IN/NOT IN and scalar subqueries, a LIMIT inside a CTE, a LEFT JOIN on inequalities, and
  `CAST($p AS INTEGER)` with a NULL parameter.
- **echo on hotdata (`oracle/echo.py`):**
  - the BM25 score column `_score`
  - the `'default.public.<t>'` table literal
  - `vector_search` vs `vector_distance` (`--vector-style`)
  - the index builds
- **HydraDB (`oracle/lessons_store.py`, `oracle/students.py`):**
  - whether `context.inspect` serves memories, and the `context.list` fallback
  - what `query` returns for an inferred preference
  - Ingest answers 202 (accepted, not finished), so `read_lessons` also reads the ledger's `lessons` mirror and
    takes the highest version.
- **Cognee (`oracle/cognee_tag.py`, `oracle/cognee_feedback.py`):**
  - `remember` with the `ExamProblem` graph model
  - reading the dataset graph as the default user
  - the dry-run cost estimate
  - `session.add_feedback` followed by `improve`
- **Refit rewording (`oracle/refit_lessons.py`):** an OpenAI-compatible `chat.completions.create` call. A rewording
  is kept only if every number in it survives.

## 8. Handoffs

### To A (data, load-course, Rote)
- **Load these ledger tables** per `contracts/ledger-schema.sql`: `courses`, `topics`, `lectures`, `exams`,
  `exam_items`, `homework_items`, `homework_vec`, `guidelines`. Each frame must have exactly the contract columns:
  `backend.load_table` rejects extras and enforces NOT NULL. The table keys are in `contracts/README.md`.
- **Source files:** `data/courses/<course>/` for 6.641, 18.06 and 6.003, read ONLY through
  `oracle.structure.load_course(course)` (it raises `StructureError` on a bad folder). Its `tables` are the
  ledger-typed `courses`, `topics`, `lectures`, `exams` and `guidelines`, with every loader rule already applied:
  - review sessions and `role = excluded` exams (6.641-final-1995S, 6.003-quiz3-2010S) are dropped;
  - T00 is kept, with NULL bounds;
  - `topics.first_lecture/last_lecture` hold SESSION numbers.
  Run `python -m oracle.structure --course <c> --check` before every load. `contracts/course-structure.md` is the
  full contract, including the 10-point handoff and the per-course loader rules: 6.641 `LOADER-PACKET` (load only
  the `packet_sections` pages) and 18.06 `A-X1-FILES` / `A-HWTEXT`.
- **From PDFs, extract only** problem numbers, sub-parts, points, problem text and homework text, joined on
  `exam_id` for exams and `(term, set)` for homework. Build `exam_items` only for `exam_sources` rows. Set
  `homework_items.session = homework_due_session(term, set)`, and skip sets whose `loadable` is false.
- **Sessions follow B's decision D2:**
  - 6.003 Fall 2011: Q1 = 9, Q2 = 14, Q3 = 20, Final = 26.
  - 6.641 Spring 2009: Midterm = 12, Final = 26.
  - 18.06 Spring 2010: 12, 25, 37 and 40.
  - Other-term exams use ordinals: 1000, 2000, 3000, and 9000 for the final.
  - `6.003-final-2011F` has `sealed = true`. It is an `exams` row only: its items are never loaded, and its URLs
    are in that course's `skip_list.txt`.
- **18.06 x1 papers** live on `https://web.mit.edu/18.06/www/`, which is not OCW. The structure check accepts
  those URLs, but `validate_ocw_url` still refuses them. Extending the fetch allowlist is A's decision and needs
  its own decisions-log line.
- **Load `exams` before tagging.** `python -m oracle.cognee_tag` reads exam metadata and the `sealed` flag from
  the ledger, and exits 2 on a sealed exam. When re-tagging, replace that exam's `exam_items` rows: an upsert leaves
  stale tags behind.
- **Fill `text_clean`** with `oracle.text_clean.clean_or_none`.
- **Strip `extraction.uncertainties` from the research extraction before any load.** It still describes target
  exams.
- **`echo` runs after `homework_items` and `exam_items` are loaded:** one command per course (B's step).
- **T00 ("Off-list"):** load it; the strict Cognee ontology needs it. D6 is in the code, so it is never ranked or
  predicted, and points tagged T00 stay in the scoring denominator.
- **Recording Play 2:** section 4. `notify_rocketride`, the Play's last step, is not part of `oracle.backtest`.

### To C (app, dashboard, reveal)
- **Tables:**

  | Table | What it holds |
  |---|---|
  | `runs` | one row per scored run: `model_pts`, `even_pts`, `lastexam_pts`, `recall_k`, `brier`, `key_source`, `cold_start`, `feature_set`, `lessons_run_seq`, `hash`, `made_at`, `tokens`, `seconds`, `checks_first_try`, `leakage_ok` |
  | `predictions` | one row per topic, with `hash` and `made_at` from the moment of sealing, before any score |
  | `lessons` | beta per `run_seq` and signal (the compounding chart) |
  | `run_labels` | per topic: `y`, `key_points`, `key_source` |
- **Charts:**
  - Label `key_source = machine` runs as machine-graded.
  - A cold-start twin (`cold_start = true`) is a comparison and has no lessons version.
  - A history side-track run (D8) is a `feature_set = exam_history` run with `cold_start = false`. Every
    real-chain run is `full`, so `feature_set` tells them apart. Plot it apart, labelled "history-only". Its
    lessons version only carries earlier weights (D4), so draw the compounding chart from the `full` runs'
    versions.
  - Show both baselines on every chart and label n.
  - A NULL `tokens` or `seconds` means the value was not measured.
  - `recall_k` may be NULL: no predicted topic was on the key.
- **Score JSON** (`predictions/<run_id>.score.json`) has these keys since D6/D7:
  - `off_list_pts`: the share of key points on T00;
  - `guideline_tests`: homework_analogous verdicts, match / partial / miss;
  - `guideline_tests_note`: why none were written.
  The prediction object carries `"standardization": "zscore_per_run_v1"` (D5), and `p` is computed from
  z-scored signals, while `signals` shows the raw values.
- **Building the UI without real data:** `python -m oracle.fixture_ledger --replace` gives three fake `runs` rows
  (FAKEHASH1..3). Running the fixture list replaces them with real, measured fixture runs.
- **The prediction object:** the schema is `contracts/prediction.schema.json`, and
  `contracts/fixtures/prediction.json` is an example.
  - Topics are sorted by rank, each with `p`, `in_top_k` and its raw signals.
  - `baselines.even` and `baselines.last_exam` are topic lists.
  - `weights` holds the lessons that were used.
- **Before the reveal:** the hash card reads `predictions`, since there is no `runs` row until the run is scored.
  Recompute the hash on stage in Python (section 6), not in JavaScript.
- **The answer key** is loaded into the ledger only by the reveal pipeline, on stage. The laptop fallback is
  `--step score` (section 6).
- **Students:**
  - The contract is `contracts/student.md`.
  - The CLI is `python -m oracle.students save --profile F | feedback --event F | read --student-id sN`.
  - Render any LLM text as text, never as HTML.
  - The self-description box (D9, `contracts/student.md` §7):
    - `python -m oracle.students describe | extract | confirm`;
    - the proposal shape is `contracts/learning-profile.schema.json`;
    - cap the box at 1,000 characters, show each chip with its quote, and never log quotes from sensitive chips;
    - "delete my data" must call both Cognee `forget` and a HydraDB delete, which B has not written yet.

## 9. What is done, and what is deferred

In the code and tested offline:
- D1-D7 (section 1). Among them: the x5 `>=` fix, and the `COALESCE(prev, 1)` window fallback, so the 6.003 Quiz
  2/3 windows are derived, not stored.
- D8: `real_chain.csv` and `history_side_track.csv`, and `--seal-only`, `--frozen` and `--through-seq` for the
  reveal. `tests/test_real_visibility.py` checks all 18 real targets against D1/D2/D3, with the sealed Final
  never visible.
- D9: the self-description box.

DEFERRED, not in the code:
- **The beta_G arm (D8):** the lessons frozen after 18.06, used as a third prediction on the 6.003 targets. It is
  in neither the code nor the run lists. It would plug into `Backtest.lessons_skip_reason()`,
  `step_read_lessons` (the lessons a prediction is made with) and `SEAL_SUMMARY_KEYS`, and `runs` would need an
  arm column (a contract change).
- **Every live path** (section 7): hotdata, HydraDB, Cognee, the LLM extractor and refit rewording. They are
  written against the installed SDK source and marked `LIVE [TEST]`; none has run.
- **Real data from A:** A's loader is not written. There are no real `exam_items`, `homework_items`,
  `homework_vec` or `echo_pairs` yet, so no real backtest has run; `tests/test_real_visibility.py` stands in
  synthetic items with the real times. The 18.06 x1 papers also wait on A's allowlist decision (section 8).
- **Human answer keys:** every scored real target needs one, in a `SEALED_DIR` outside the repo. `score` refuses
  without one (exit 6), unless `--allow-machine-key`, which marks the run machine-graded.

Other open items:
- **tau:** `backtest` does not pass `--tau` to `run_signals` (default 0.02), so the tau that `echo` recommends
  cannot reach x3 yet.
- **Guideline tests:** only `homework_analogous` has a writer. Trust for `cumulative`, `emphasis_window` and
  `coverage` stays at 0.5.
- **Students:** there is no HydraDB delete for "delete my data". The Cognee `StudentProfile` has no goals or
  adjustments fields, so Cognee cannot reason on the confirmed self-description values.
- **Packaging** for Rote Plays that run from `/tmp`.
