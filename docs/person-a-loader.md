# Role A — Loader: status, runbook, handoffs

Branch `integration` = role A's loader merged with role B's model & memory (`person-b`). Everything below was run
and checked on 2026-09-11; numbers are measured.

## How the loader fits B's contract

```
index ─▶ download (×N, 4 at a time) ─▶ extract ─▶ split ─▶ validate ─┬─▶ items ─┐
                                                                       └─▶ load  ─┤
structure (data/courses/<course>/ → courses, topics, exams, lectures, guidelines) ┴─▶ tag (Cognee → exam_items) ─▶ summary ─▶ notify
```

| Step (`exam-oracle …`) | Writes | Notes |
|---|---|---|
| `index`, `download`, `extract`, `split`, `validate` | `$ORACLE_DATA_DIR/{raw,work}/<course>/` | OCW only; skip list = builtin + `data/courses/<c>/skip_list.txt`; exit 2 on a sealed URL/hash, 3 on bad numbering/points |
| `structure` | `courses`, `topics`, `exams`, `lectures`, `guidelines` | from B's `data/courses/<c>/`; contract columns only; the sealed exam gets its `exams` row (metadata) and never any items |
| `items` | `work/<c>/items.json` | B's tagger input: one item per exam problem (+ its solution text) |
| `tag` | `exam_items` | runs `oracle.cognee_tag` (Cognee, strict topic list) and upserts its rows; degraded without `LLM_API_KEY`; `--dry-run` prices it |
| `load` | `homework_items`, `homework_vec` | topics from the lectures each problem set covers (see decisions log) |
| `summary` | `course_loads` | the "cheaper" chart (contract addition) |
| `notify` | RocketRide webhook | degraded until `ROCKETRIDE_WEBHOOK_URL` is set |

All writes go through `oracle.backend` (B): the local DuckDB ledger (`ORACLE_BACKEND=local`, default) or hotdata
(`ORACLE_BACKEND=hotdata`, needs `HOTDATA_API_KEY` + `HOTDATA_WORKSPACE`).

## Status

| Task | Status | Evidence |
|---|---|---|
| Rote + hotdata smoke tests | Done | see `kit/07-reference/decisions-log.md` (G0 answers) |
| Download all PDFs; 18.06 count | Done | 164 PDFs (6.641 53, 6.003 73, 2.71 38); 18.06 has 0 earlier finals → course 2 = **2.71** (confirmed) |
| Generic split/check scripts | Done | `oracle/` |
| `load-course` Play: record, export, lint, replay, resume | Done | `plays/load-course/` (v0.1.2 = the integrated graph), `docs/traces/`, `docs/evidence/` |
| Integration with B's contract | Done | merged branch, B's 120 tests pass; `data/courses/2.71/` drafted (B to verify) |
| Cognee tagging (with B's tagger) | Priced (~$3.06 for 95 problems) and run live — results below | |
| hotdata as the live ledger | **Waiting for a hotdata API key** (B's backend uses the SDK, not the CLI login) | |
| RocketRide notify | **Waiting** — the extension is installed but not signed in to a folder on this machine | |
| Publish `load-course` to the catalog | Waiting for your go-ahead (needs a Rote handle or org) | |

## Ledger after the integrated loads (local backend)

| | 6.641 | 2.71 | 6.003 |
|---|---|---|---|
| exams (incl. sealed metadata row) | 10 | 8 | 12 (1 sealed) |
| lectures / topics / guidelines | 25 / 19 / 6 | 25 / 18 / 0 | 25 / 19 / 3 |
| exam problems handed to the tagger | 23 | 20 | 52 |
| homework problems → homework_items rows | 30 → 68 | 29 → 68 | 81 → 129 |

## Runbook

```bash
brew install poppler                                  # pdftotext
uv tool install --force --editable <your checkout>    # exam-oracle + B's modules; editable so data/courses and contracts are found
export ORACLE_DATA_DIR=~/exam-oracle-data             # raw/ and work/
export ORACLE_LOCAL_DIR=~/exam-oracle-data/local-backend   # local ledger + Cognee storage (or ORACLE_BACKEND=hotdata)
# LLM_API_KEY goes in <checkout>/.env (git-ignored)
exam-oracle ledger-init
cp -R plays/load-course ~/.rote/flows/
cd /tmp && rote play run load-course course=2.71 course_url=https://ocw.mit.edu/courses/2-71-optics-spring-2014/
```

Every command prints one JSON object; exit 0 ok (a `warning` = degraded), 1 hard fault, 2 sealed, 3 validation.
After a failure: fix, then re-run the same command with `--resume <run_id>` (parameters are required again).

## Handoffs

**To B:**
- `structure` reads your `data/courses/<c>/` files exactly as committed; `topics.first/last_session` fill the contract's `first/last_lecture`.
- `data/courses/2.71/` is A's draft in your format — please verify, and add 2.71 rows to the real run list (run_seq 4–6).
- Homework topics are structural (lecture window of each set); replace with Cognee tags if you prefer — `load` is the only writer.
- `course_loads` was added to the contract (+ key, README line, fixture).

**To C (dashboard):** `SELECT * FROM {{course_loads}}` for the "cheaper" chart; `runs` for the other two. The `notify`
payload is `{"event":"course-loaded","course":…,"summary":{…},"validate":{…}}`.

## Known limits

- The 6.641 recording's agent tokens were not measured (`agent_tokens` NULL).
- Homework/lecture dates for the ledger come from B's structure files; exam dates from the PDFs or B's files.
- Skip-list hashes of the sealed PDFs are not filled in — only a human with the sealed folder should add them.
