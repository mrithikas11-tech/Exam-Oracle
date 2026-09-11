# Modiqo Rote — Muscle-memory layer

**Brief's role:** "watches what your agent does when a task succeeds, and turns that successful run into deterministic, reusable code … make your project's second, third, and tenth run visibly faster, cheaper, or more reliable than the first — this is the 'proof of compounding' judges will look for." Remix allowed: "Modiqo capturing a hotdata.dev query pattern instead of a RocketRide action."
**Exam Oracle's role:** `load-course` (replayed per course) and `run-backtest` (replayed ~10×) as fixed Plays; the failure-and-resume moment; published to the Playoffs catalog.

## What it really is `[DOCS]`

- **Recording:** `rote init <ws> --seq` opens a workspace; each `rote proc run <cmd>` (shell), adapter call (API) or browser action is saved as a numbered, immutable, jq-queryable record (`@1`, `@2`). In a coding-agent harness, `$play explore <outcome>` does the same through the agent.
- **Crystallizing:** `rote workspace export <ws> --params <names>` runs five stages — filter (drop failed attempts), reify (hard-coded values → typed inputs), resolve (`@N` references → dependency graph), fingerprint (hash the APIs used), generate.
- **Artifact:** `main.ts` (YAML header with `parameters` and a `steps:` graph, then TypeScript for output) plus `deps.toml` listing required tools. Steps are `process.exec` with `argv`, `timeout_ms` (default 30 s), and `for_each` / `max_concurrency` for fan-out.
- **Replay:** re-runs the graph with new inputs; the model does not re-reason. "Deterministic in method," not in output. Runs locally inside the rote process.
- **FAQ:** a failed step is named with its evidence; dependent steps are blocked; completed steps stay in the record; a resume continues from the incomplete part. Records "API calls, local commands/file operations, and browser actions." The hello Play cut reasoning tokens 14,900 → 300 (one measured example). macOS (Apple Silicon and Intel) and Linux x86_64. Works with Claude Code, Codex, Cursor, Kimi, Hermes Agent, Pi; other harnesses use the CLI.

## Interfaces `[DOCS]`

- Install options: `curl -fsSL https://getrote.dev/playoffs/install.sh | sh` (Play sidekick pinned to v0.4.98; needs Python 3.10+ and `uv`); `getrote.dev/install`; the rote-releases script. hello requires rote ≥ 0.62.0 and Python 3 on PATH.
- Login: `rote login --provider github|google`; `rote whoami --check`. Headless login claim tokens expire in 30 minutes.
- Run: `rote play run <target> [key=value …] [--output=json] [-y|--yes] [--dry-run] [--max-concurrency N] [--resume <run_id>]`.
- Lifecycle: `rote play inspect`, `rote play lint main.ts`, `rote play pending write` (mandatory stub before export), `rote play release`, `rote registry play push <path> <slug> [--private]`.
- Scheduling: `play recurring schedule --reference modiqo/hello@0.2.2 --cadence daily --for 30d`.
- Credentials: `rote token set NAME --stdin`; Plays name credentials but never contain them.
- Adapters: `rote adapter new <id> <openapi-url>`; `rote adapter new-from-mcp` (wrap MCP endpoints); `rote flow run`.
- Evidence: `rote trace --html` (Gantt of a run), `rote stats` (token and latency summary), `play-journey view --active` (live exploration graph at 127.0.0.1:52050); "birth certificates" record token savings.
- In harnesses: Claude Code `/play …`, Codex/Cursor `$play …`, Kimi `/skill:play …`; warm-up `$play run hello`.

## Rules that shape our design `[DOCS]`

- A trace will not compile if work ran in a plain shell outside rote, results were pasted instead of referenced with `@N`, the same call was repeated with different results, or it depends on hidden state from earlier runs.
- Writes must be declared; destructive steps belong behind an explicit `apply=true` input; API steps must be listed in `requires_endpoints` to publish.
- Failure lanes: expected absence → exit 0 with `{"ok":true,"warning":…}` (degraded); hard fault → step fails, dependents BLOCKED, `--resume` offered.
- Publishing requires a clean run from `/tmp` → no `./scripts/…` paths; embed scripts or declare them as tools in `deps.toml`.
- Automated calls need `--yes` (each run otherwise asks for approval); prompting subcommands need their own consent flags.
- Quote non-string defaults (`'20'`); a bare `console.log` in `main.ts` fails lint (`FLOW_OUTPUT_BARE_CONSOLE_LOG`); versions are immutable — bump on every change.
- There is no documented per-run token command; build the "cheaper" chart from run 1's harness token count and replays' JSON (wall time; model tokens near zero because steps are commands) `[TEST]`.
- Ignore press claims of "identical outputs" and Salesforce connectors (they contradict the FAQ).

## How Exam Oracle uses Rote fully `[DESIGN]`

- All course-specific logic in generic scripts driven by inputs (a Play replays a fixed graph; improvisation for course 1 would be baked in).
- Two Plays: `load-course` and `run-backtest` (details in `03-architecture/rote-plays.md`).
- Checks as exit codes: numbering and point totals = hard faults; missing solutions = degraded.
- Raise `timeout_ms` for downloads, extraction and Cognee calls.
- Call Plays from Python / shell with `--yes --output=json`; parse stdout.
- Demo: resume after a deliberate hard fault on course 3; `rote trace --html`; `rote stats`; publish `load-course` to the Community catalog.
- Optional: `rote adapter new` from hotdata's OpenAPI spec (adds API fingerprint drift detection).

## Smoke test (15 min)

1. Install; `rote login`; `rote whoami --check`.
2. `rote play run https://play.modiqo.ai/modiqo/hello@0.2.2 --output=json --yes` twice; note which timing/token fields the JSON contains.
3. `rote init smoke --seq`; `rote proc run curl -s <OCW course-1 URL>`; `rote query @1 …`; `rote play pending write smoke …`; `rote workspace export smoke --params course_url`; `rote play lint main.ts`.
4. `rote play run main.ts course_url=<course-2 URL>`, then a bad URL; confirm BLOCKED and that `--resume <run_id>` works.
5. Call the same command from Python `subprocess`; confirm the exit code and JSON parse.

Go/no-go on steps 4 and 5 before designing further. **If it fails:** keep the generic scripts as the recipe and use Rote to record the run-and-check steps around them.

## Sources

- https://www.modiqo.ai/faq · https://www.modiqo.ai/blog/the-playoffs · https://www.modiqo.ai/feed
- https://modiqo.ai/docs/03-create-your-first-play.md · https://modiqo.ai/docs/06-anatomy-of-a-play.md · https://modiqo.ai/docs/07-modalities.md · https://modiqo.ai/docs/08-workspace-and-caus.md · https://modiqo.ai/docs/09-reference.md
- https://modiqo.ai/blog/extract-a-reusable-procedure-from-an-agent-trace.md · https://modiqo.ai/blog/play-cheat-sheet.md · https://modiqo.ai/blog/what-rote-is.md · https://modiqo.ai/agent/tutorial.md
- https://github.com/modiqo/play · https://github.com/modiqo/rote-releases · https://play.modiqo.ai/modiqo/hello
- Playoffs judging criteria (third-party): progressiverobot.com/2026/08/30/rote-playoffs-modiqo-ai-hackathon-registration
