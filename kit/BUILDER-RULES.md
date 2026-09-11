# Builder Rules — Exam Oracle

Binding rules for any agent or person building Exam Oracle. Copy this file to the repository root as `CLAUDE.md` (Claude Code) and `AGENTS.md` (Codex and others) on the first commit.

## 1. Ground every API call

- Before writing code against a sponsor tool, read `04-tools/<tool>.md` **and** the live docs it links.
- In RocketRide, read the workspace's `.rocketride/docs/`, `services-catalog.json` and `schema/` before writing any pipeline. The official setup guide calls these "ground truth for component names, config fields and SDK signatures"; the public docs contradict themselves on node names.
- Never guess a function name, parameter, CLI flag or environment variable. If you cannot find it, say so and run the relevant smoke test.
- When docs contradict each other, trust the installed package / catalog, and log the conflict in `07-reference/decisions-log.md`.

## 2. Smoke test before building (gate G0)

- Each tool's first test (`05-build/smoke-tests.md`) must pass, or a fallback must be chosen and logged, before any feature depends on that tool.
- Do not "just start coding" around an unverified integration. Integration failures discovered at hour 5 end hackathons.

## 3. Pin versions

- `cognee==1.5.4` (four releases landed in the two weeks before the event).
- `hydradb-sdk>=2,<3` for hosted HydraDB. Open-source HydraDB image `ghcr.io/hydra-db/hydradb` v0.1.1 if used.
- RocketRide VS Code extension 1.2.0 (staging VSIX).
- Record every other version in `05-build/repo-layout-and-config.md` as you install.

## 4. Sealing is sacred

- Never read, ingest, embed, or open in code a sealed target exam or its solutions.
- Sealed files live outside the repository and are on the `load-course` skip list.
- Every backtest runs in its own hotdata database containing only rows dated before the target exam, and asserts that inside the run.
- Answer keys for hidden exams are labeled by a human **before** the prediction is made. The AI never grades itself.
- Every prediction prints a SHA-256 hash of its canonical JSON with a timestamp. The hash is shown on screen during the day and recomputed at the reveal.
- Backtests only go forward in time.
- Any code path that could read a sealed file is a bug of the highest severity.

## 5. Every sponsor tool does real, repeated work

- Each tool has a job in `03-architecture/stack-map.md`. Do not add calls just to show a logo, and do not remove a tool without replacing its job ("if removed" column).
- The judges will check that each tool is "actually doing work in your architecture — not just imported once."

## 6. Measure from the first run

- Every run appends to the hotdata ledger: tokens, seconds, checks passed first try, leakage_ok, scores for the model and both baselines.
- Charts show only measured numbers and label the number of runs (n). Never invent or smooth numbers.

## 7. Security by design (Snyk deducts points)

- Fetch course material only from `https://ocw.mit.edu`; re-check redirects.
- Name downloaded files by hash inside a fixed directory; never trust a remote filename.
- Parameterise every Cypher and SQL statement; never build queries from strings, and never execute LLM-written queries without a read-only guard.
- Secrets only in environment variables; `.env` is git-ignored in the first commit; commit a `.env.example` with empty values.
- Never disable certificate verification.
- Render LLM output as text, never as HTML (`dangerouslySetInnerHTML` is banned).
- Any tunnel from the cloud to a laptop carries a bearer token.
- Full checklist: `05-build/security-checklist.md`.

## 8. Student data

- Store only what a student gives: preferences, syllabus, guidelines, feedback, exam date, weekly hours.
- Each student's data is isolated (own Cognee dataset, own HydraDB collection). Shared course knowledge is read-only to students.
- No real student data at the hackathon. Demo students **Maya** and **Leo** are fictional and labeled as such on screen.

## 9. Honesty on stage

- Both baselines appear on every chart, including runs where a baseline wins.
- Say what was simulated (demo students) and what was real (MIT exams, backtests).
- Credit MIT OpenCourseWare (license CC BY-NC-SA) on the demo slide.

## 10. Milestones over features

- M1: first sealed prediction scored end to end (target 3:00).
- M2: final 6.003 prediction sealed and its hash shown (target 5:30).
- M3: backup demo video saved (target 7:15).
- When behind, cut from `05-build/cut-list.md` from the top. Never cut the "never cut" items.

## 11. Log decisions

- Any deviation from this kit gets an entry in `07-reference/decisions-log.md`: time, what changed, why, who.

## 12. Protect credits and time

- RocketRide pipeline runs draw on the promo credits; cap the Wave agent's `max_waves`.
- Price every Cognee bulk ingest with `dry_run=True` first; use `self_improvement=False` during bulk loads.
- hotdata includes a $100 sign-up credit; give throwaway databases an expiry.

## 13. Snyk setup caution

- `npx -y snyk@latest mcp configure --tool=claude-cli` writes into Claude's **global** rules file. Only run it on a machine dedicated to the hackathon; otherwise add the Snyk MCP block to the project's config by hand (`04-tools/snyk.md`).

## 14. When blocked

- State what is blocked, what you tried, and the fallback. Do not silently switch architecture; log the switch.
