# Staging Plan — Gated Build Order

Build in gates. Each gate has an entry condition, tasks, an exit test and a fallback. Do not start a gate's features until the previous gate's exit test passes (or its fallback is logged in `07-reference/decisions-log.md`). Times assume an 8-hour event with three people; see `timeline-and-roles.md`.

## Environments

| Environment | What lives there | Rules |
|---|---|---|
| **Local dev** | Laptop: repo, generic scripts, Rote workspace, RocketRide local engine in VS Code, optional open-source HydraDB, Cognee server (Plan B) | Everything runnable offline except cloud APIs |
| **Data staging** | `data/raw/` (hash-named downloads + manifest), hotdata `ledger`, hotdata `run-<id>` databases, Cognee `course-*` datasets, HydraDB `shared` | Ledger is append-only through idempotent loads; run databases expire in 2 h |
| **Sealed** | A folder **outside** the repo holding hidden exams and answer keys | Never read by code except `score.py` after a hash is written; never uploaded or tunneled |
| **App staging** | RocketRide staging: scratch app (developer ID), real app at @me then @team | Immutable versions; deploy only from green Readiness |
| **Demo freeze** | Tagged commit + frozen ledger snapshot + backup video | After G5 no schema changes; after G6 no code changes |

## G0 — Setup and first tests (0:00–0:45)

- **Entry:** accounts exist (brief's pre-work done).
- **Tasks:** run every smoke test in `smoke-tests.md` (split by role); download all OCW PDFs; count 18.06 earlier finals; move the 6.003 Fall 2011 Final out of reach and label its answer key; create repo with `BUILDER-RULES.md` as `CLAUDE.md`/`AGENTS.md`, `.gitignore` with `.env`, `.env.example`.
- **Decisions to log:** Cognee location (Cloud vs laptop + tunnel); sponsor nodes present in `services-catalog.json` (or HTTP-tool fallback); hosted HydraDB `query_graph` behaviour; Rote replay-from-Python (or wrap-scripts fallback); course 2 choice; hotdata index timing (per-run vs ledger indexes).
- **Exit test:** all six smoke tests passed or have a logged fallback; sealed files are not in the repo (`git ls-files` shows none).
- **If missed:** stop feature work; resolve the blocking tool first — integration debt compounds.

## G1 — Data in (0:45–2:00)

- **Tasks:** generic loader scripts; record `load-course` on 6.641 inside a Rote workspace; export and lint; 6.641 topic list → Cognee strict ontology and models; hotdata ledger tables and saved signal queries; RocketRide app skeleton + upload-syllabus pipeline; Snyk snapshot 1.
- **Exit test:** 6.641 items in the ledger with topic tags; `rote play lint` clean; one syllabus upload returns a topic list in the app.
- **If missed:** load 6.641 via the scripts directly (no Play yet) and record the Play in G2.

## G2 — First sealed prediction scored (by 3:00) — **Milestone M1**

- **Tasks:** replay `load-course` on course 2 (log cost); record `run-backtest`; first run 6.641 Final 2008 ← 2006 end to end: run DB → signals → leakage check → lessons → seal (hash) → score vs baselines → ledger row; dashboard pipeline shows one point on each chart.
- **Exit test:** one `runs` row with model/even/last-exam scores, hash, tokens, seconds, `leakage_ok = true`; dashboard renders it.
- **If missed:** simplify signals to x1, x2, x4 and baselines; add the rest in G3.

## G3 — The compounding loop (3:00–4:30)

- **Tasks:** lesson refit after every run → HydraDB `shared`; Cognee session feedback + `improve()`; guideline tests; homework-echo search; replay `load-course` on 6.003 with the skip list and capture the resume moment; student profiles (Maya, Leo), study-plan pipeline, feedback pipeline.
- **Exit test:** ≥ 5 runs on the chart; lessons change between runs (visible in HydraDB); Maya's plan uses her preferences; her "too wordy" changes the next answer.
- **If missed:** cut per `cut-list.md` (open-source HydraDB, practice questions, publish, vector echo) before touching the never-cut list.

## G4 — Reveal ready (4:30–5:30) — **Milestone M2**

- **Tasks:** all 6.003 runs; cold-start comparison (global β vs blank β on 6.003's first run); seal the Fall 2011 Final prediction and show its hash on the dashboard; reveal screen; "why" view (Cognee recall, or Cypher path if built); publish `load-course` to the catalog and export the trace.
- **Exit test:** hash on screen with timestamp; reveal pipeline recomputes it from the stored file and matches; answer key loaded only by the reveal path.
- **If missed:** reveal with the laptop script and a static screen; keep the hash ritual.

## G5 — Demo path (5:30–6:30)

- **Tasks:** practice questions in each student's style (stretch); freeze the model (no β changes after the reveal prediction); leakage audit (every run row `leakage_ok`); tag the commit; snapshot the ledger.
- **Exit test:** the full demo script runs start to finish once without edits.

## G6 — Ship (6:30–8:00) — **Milestone M3 by 7:15**

- **Tasks:** Snyk routine scan 2 + `aibom`; rehearse twice; record the backup video (offline copy); submission (`06-demo/submission-checklist.md`).
- **Exit test:** backup video saved; Snyk high/critical = 0 or justified in `.snyk`; submission sent.

## Freeze points

- After the reveal prediction is sealed (G4): no changes to β, signals or scoring code.
- After G5: no schema changes. After G6 start: no feature code; fixes only.
