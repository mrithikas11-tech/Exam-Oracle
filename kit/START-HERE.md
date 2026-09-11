# Exam Oracle — Build Kit

**Status:** plan only. Nothing has been built. Written 2026-09-11 for the *Data and AI Hackathon: From Memory to Muscle Memory* (theme: Compound Agents), AWS Builder Loft, 8-hour build sprint.

**Who this is for:** any builder with zero prior context — a fresh Claude Code / chat session, NOVA, Codex, or a human teammate. Everything needed to build, stage, and demo the project is in this folder. Nothing here depends on the conversation that produced it.

---

## The 60-second version

- **The hackathon** requires one product that uses five sponsor tools, each doing real, repeated work: **Cognee** (structure), **HydraDB** (memory), **hotdata.dev** (insight), **RocketRide.ai** (motion), **Modiqo Rote** (muscle memory). **Snyk** scans the app and deducts points for vulnerabilities. Judges look for proof that "every run makes the next run smarter, cheaper, and more reliable."
- **The product — Exam Oracle:** a study planner for students. It reads a course's syllabus, lecture notes, problem sets and past exams (MIT OpenCourseWare), learns *how that professor writes exams*, predicts which topics will appear on an upcoming exam, and turns that into a study plan shaped to *how each student likes to learn*.
- **The showpiece:** a real MIT final (6.003 Signals & Systems, Fall 2011) is hidden from the system. It predicts the exam, the prediction is sealed with a SHA-256 hash hours in advance, and the real exam is revealed on stage with hits and misses scored in exam points.
- **Proof of compounding:** three live charts — *smarter* (share of exam points covered vs two honest baselines), *cheaper* (tokens/minutes per new course once Rote replays the loading), *more reliable* (first-try check pass rate, calibration) — plus a student whose feedback ("too wordy") visibly changes the next answer.
- **It learns on four levels:** per course, across courses (lessons carry to a course it has never seen), per professor's word (does stated guidance match real exams?), per student (preferences + feedback).

## Reading order

1. `BUILDER-RULES.md` — binding rules for whoever builds. Copy it to the repo root as `CLAUDE.md` and `AGENTS.md`.
2. `01-brief/judging-and-requirements.md` — what the hackathon demands and how it is judged.
3. `02-product/product-spec.md` — what we are building and why it wins.
4. `03-architecture/architecture.md` and `03-architecture/stack-map.md` — where everything runs and how the tools form one stack.
5. `04-tools/<tool>.md` — read your own tool completely; skim the rest. These are grounded in the vendors' real docs and source.
6. `05-build/staging-plan.md` and `05-build/smoke-tests.md` — the gated build order. **Do not build features before gate G0 passes.**
7. Everything else as needed (`MANIFEST.md` lists every file).

## Legend used throughout

| Tag | Meaning |
|---|---|
| `[DOCS]` | Verified in the vendor's docs or source on 2026-09-10/11. URL in the tool file or `07-reference/sources.md`. |
| `[TEST]` | Not confirmed. Run the smoke test before depending on it. |
| `[DESIGN]` | Our decision. Change it if needed, but log why in `07-reference/decisions-log.md`. |
| `[INFERRED]` | Reasoned from docs, not stated in them. Treat like `[TEST]`. |

## First actions for a builder with no context

1. Read `BUILDER-RULES.md` in full.
2. Ask the humans four things: team size and skills; start time and demo time; who owns roles A / B / C (see `05-build/timeline-and-roles.md`); whether **Cognee Cloud** access is available at the venue.
3. Run gate **G0** in `05-build/staging-plan.md` using `05-build/smoke-tests.md`. Record every result and every fallback chosen in `07-reference/decisions-log.md`.
4. Build gate by gate. Hit milestones **M1 → M2 → M3** in order. When behind, use `05-build/cut-list.md`.

## If you are NOVA (SEAL Lab's agent)

- **This is not a SEAL quest.** Do not create a quest, KSS row, YBR, Drive folder, or Clan Life entry for it. SEAL-specific operational rules (quest claims, Drive markers, roster rules) do not apply.
- The core discipline still applies: ground every claim in this kit or the live vendor docs, never invent an API, name gaps instead of filling them.
- When this kit and today's vendor docs disagree about an API, **the installed package / live docs win** (vendors shipped several releases in the weeks before the event). Log the discrepancy in `07-reference/decisions-log.md`.

## The six things most likely to change the design (all `[TEST]`)

1. Whether RocketRide's cloud engine can reach the Cognee server (Cognee Cloud, or a laptop server behind an authenticated tunnel).
2. Whether the RocketRide extension you installed (1.2.0) actually contains the `tool_cognee`, `graph_hydradb`, `db_hotdata` nodes listed in the docs.
3. Whether hosted HydraDB's `query_graph` accepts Cypher (docs disagree).
4. Whether one Cognee `recall` can merge a shared course dataset and a private student dataset.
5. Whether a Rote Play replays with new inputs when called from Python with `--yes --output=json`.
6. hotdata index-build time per throwaway database.

Each has a fallback in `05-build/risks-and-fallbacks.md`.

## One-page visual version

`07-reference/plan-v2.html` — the same plan as a single page (open in any browser).
