# Rote Plays

How Rote really works `[DOCS]`: commands run **inside a Rote workspace** are saved as numbered, immutable records (`@1`, `@2`, …). `rote workspace export` compiles the successful ones into a Play — `main.ts` with a YAML header (typed `parameters`, a `steps:` graph) plus `deps.toml` — through five stages: filter failed attempts, reify hard-coded values into inputs, resolve `@N` references into dependencies, fingerprint the APIs used, generate. Replay re-runs that graph with new inputs; the model does not re-reason. "Deterministic in method, not in output."

Consequence: **anything improvised per course will be baked in.** All course-specific logic must live in generic scripts driven by inputs.

## Generic scripts (the recipe's building blocks) `[DESIGN]`

| Script | Input | Output | Exit codes |
|---|---|---|---|
| `ocw_index.py` | `course_url`, `skip_list` | JSON list of document URLs with type/term/date | 2 = skip-listed URL encountered (hard) |
| `ocw_download.py` | one URL | file saved as `data/raw/<course>/<sha256>.pdf`, manifest row | 1 = HTTP error (hard) |
| `extract_text.py` | file | text file | 0 always; warning JSON if text empty (degraded) |
| `split_problems.py` | exam text | problems JSON with numbers and points | — |
| `validate.py` | problems JSON | check report | 3 = numbering not 1…N or points ≠ printed total (hard); degraded warning if solutions file missing |
| `load_hotdata.py` | rows | load into ledger (replace/append) | 1 = load failed (hard) |
| `cognee_remember.py` | items | tagged items (topics) written back as rows | 1 = service error (hard); as built (`oracle/cognee_tag.py`): 2 = sealed exam, 3 = tags outside the topic list, 7 = no `LLM_API_KEY` |
| `notify_rocketride.py` | message | webhook POST | degraded on failure |
| `make_run_db.py` | course, target_exam | `run-<id>` created with pre-exam rows | 1 hard |
| `run_signals.py` | run db | signals table (JSON) | — |
| `leakage_check.py` | run db, target date | ok/fail | 4 = leakage (hard) |
| `read_lessons.py` | — | current β from HydraDB | — |
| `rank_and_seal.py` | signals, β | prediction JSON + SHA-256 | — |
| `score.py` | prediction, sealed key path | scores vs baselines | reads key only after hash written; 3 = invalid key, 5 = no seal or tampered (refused), 6 = no human key (hard) |
| `append_ledger.py` | run row | ledger append (idempotency key) | 5 = sealed prediction missing or tampered (hard) |
| `refit_lessons.py` | all runs | new β + statements | — |
| `store_lessons.py` | lessons | HydraDB `shared` memories | — |
| `cognee_feedback.py` | run session, score | `add_feedback` + `improve()` | degraded on failure |

As built (Person B, 2026-09-11): every exit code is a constant in `oracle/config.py` (`EXIT_*`), and every script's usage error prints one JSON object and exits 1 (argparse's own 2 would read as the skip-list lane). `oracle/backtest.py` runs Play 2 as one command, or one `--step` per Rote record, and exits with the failing step's code; `oracle/run_list.py` runs a run list forward in time only.

Package the scripts so a Play runs from `/tmp` (required to publish): install them as a tool declared in `deps.toml`, or embed with `python3 -c`. No `./scripts/...` relative paths `[DOCS]`.

## Play 1 — `load-course`

- **Parameters:** `course_url` (string), `skip_list` (path), `course` (string).
- **Steps:** `ocw_index` → `for_each` doc (with `max_concurrency` 4) `ocw_download` → `extract_text` → `split_problems` (exams only) → `validate` → `load_hotdata` → `cognee_remember` → `load_hotdata` (topic tags) → `notify_rocketride`.
- **Timeouts:** default step timeout is 30 s — raise `timeout_ms` for download, extract and Cognee steps `[DOCS]`.
- **Failure lanes `[DOCS]`:** expected absence (missing solutions) exits 0 with `{"ok":true,"warning":...}` → shown as degraded; hard faults (skip-list hit, numbering, points, load failure) fail the step, dependents show BLOCKED, `--resume <run_id>` offered.
- **Outputs:** JSON summary (items loaded, degraded count, seconds). Tokens: none on replay (all steps are commands; the Cognee call spends extraction tokens inside Cognee — log those separately from agent reasoning tokens).

## Play 2 — `run-backtest`

- **Parameters:** `course`, `target_exam`, `target_date`, `sealed_key_path`.
- **Steps:** `make_run_db` → `run_signals` → `leakage_check` → `read_lessons` → `rank_and_seal` → `score` → `append_ledger` → `refit_lessons` → `store_lessons` → `cognee_feedback` → `notify_rocketride`.
- **As built (`python -m oracle.backtest`):** parameters `course`, `target_exam`, `run_seq` (+ `cold_start`). The target's time comes from its ledger `exams` row (the ordering rule), not a date, and the key from `SEALED_DIR/answer_key_<exam_id>.csv`. A `drop_run_db` step closes the chain. See `B_README.md` for the per-step recording.
- **Why it matters:** the same fixed method, replayed ~10 times during the day, while the lessons it reads keep changing — that is the compounding chart. The seal is deterministic by construction.

## Recording procedure `[DOCS]`

```
rote init oracle --seq
rote proc run python3 -m oracle.ocw_index --course-url <url> --skip-list <file>
rote proc run ...                 # one reading per step; reference outputs as @N, never paste values
rote play pending write load-course ...   # mandatory stub before export
rote workspace export oracle --params course_url,skip_list,course
rote play lint main.ts
rote play run main.ts course_url=<course-2 url> skip_list=<file> course=<code> --yes --output=json
```

A trace will not compile if: work ran in a plain shell outside Rote; results were pasted instead of referenced; the same call was repeated with different results; it depends on hidden state from earlier runs `[DOCS]`.

## Calling a Play from code

- `rote play run <target> key=value ... --output=json --yes` — JSON output is "canonical JSON for scripts and CI" `[DOCS]`; non-zero exit on failure `[TEST]`.
- Credentials: `rote token set HYDRADB_API_KEY --stdin` (Plays name credentials, never contain them) `[DOCS]`.
- Non-string defaults must be quoted (`'20'`); a bare `console.log` in `main.ts` fails lint (`FLOW_OUTPUT_BARE_CONSOLE_LOG`); versions are immutable — bump on every change `[DOCS]`.

## Demo moments for the Modiqo judge

1. **Cheaper:** course 1 loaded by the agent (reasoning tokens from the coding agent's usage), courses 2–3 by Play replay (near-zero model tokens; seconds from JSON). No per-run token command is documented; `rote stats` gives token/latency summaries `[DOCS]`.
2. **Resume:** break course 3 mid-run (e.g., a scanned PDF fails `validate`), show BLOCKED with evidence, fix, `--resume <run_id>`.
3. **Trace:** `rote trace --html` Gantt of a run.
4. **Publish:** `rote registry play push <path> <slug>` to the Community catalog after a clean run from `/tmp` `[DOCS]`. Playoffs judging (third-party report) weighted "does it run / understandable / do others adopt it".
