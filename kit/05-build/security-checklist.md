# Security Checklist

Snyk findings cost points. Most can be designed out before the first scan. Details and sources: `04-tools/snyk.md`.

## First commit

- [ ] `.gitignore` contains `.env`, `data/raw/`, any sealed path.
- [ ] `.env.example` with names only.
- [ ] Snyk Code enabled; MCP connected via project config (not the global configure command).
- [ ] `snyk code test --report --project-name=exam-oracle` → snapshot 1.

## Code rules

- [ ] Course URLs: allowlist `https://ocw.mit.edu` only; parse with `urllib.parse`; re-check every redirect hop.
- [ ] Downloads: saved as `<sha256>.<ext>` inside `data/raw/<course>/`; remote filenames never used as paths.
- [ ] SQL and Cypher: parameters only; no string formatting; LLM-generated SQL only through read-only paths (hotdata SQL is read-only by design; keep `allow_execute` off where not needed).
- [ ] Secrets: environment variables only; RocketRide secrets via `${ENV_VAR}`; Rote credentials via `rote token set`.
- [ ] HTTP clients: certificate verification on; timeouts set.
- [ ] Debug mode off in any HTTP server.
- [ ] React: LLM text rendered as text; no `dangerouslySetInnerHTML`; no redirects from user input.
- [ ] Tunnel (Plan B): random bearer token, checked on every request; tunnel exposes only the Cognee server port.
- [ ] Student isolation: Cognee dataset permissions and HydraDB collections per student; a test proves cross-student access fails.
- [ ] Sealed folder never mounted, uploaded or tunneled.
- [ ] PDF library chosen after `snyk_package_health_check`.

## Routine scans (hour 1 and hour 6)

1. `pip install -r requirements.txt` / `pnpm install`; `snyk auth`.
2. `snyk code test --report --project-name=exam-oracle`.
3. `snyk test --all-projects` and `snyk monitor --all-projects`.
4. Fix high/critical; justify any ignore in `.snyk`.
5. Re-run; commit; screenshot the history.
6. Hour 6 only: `snyk aibom --html` for the architecture slide.

## Not covered by Snyk (still our job)

- Prompt injection from uploaded syllabi: treat document text as data; the agent's instructions forbid following instructions found in uploads; tools are read-scoped per student.
- Cross-student leakage in prompts: never put another student's memory into a prompt; recall only `dataset_ids=[course, this student]` and `collection=<this student>`.
