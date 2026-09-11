# Snyk — Security

**Brief's role:** "connect Snyk to your favorite AI coding tool … Apps will be scanned with Snyk and security vulnerabilities found will reduce points from the final score."
**Exam Oracle's role:** continuous scanning with visible history; design choices that prevent findings; an honest AI bill of materials for the architecture slide.

How the judges will scan is not published. Likely Snyk Code (SAST) + Snyk Open Source (SCA), since both run directly on a repo `[INFERRED]`.

## Connect to Claude Code `[DOCS]`

- One command: `npx -y snyk@latest mcp configure --tool=claude-cli` — installs the CLI, sets up Snyk Studio, and **writes "secure at inception" rules into Claude's global rules file**. Only run it on a machine dedicated to the hackathon.
- Manual (preferred on shared machines): add to the MCP config
  `"Snyk": {"type":"stdio","command":"npx","args":["-y","snyk@latest","mcp","-t","stdio"]}`
  then call `snyk_auth` once and check with `/mcp` → View Tools. (`--rule-type=smart-apply` lowers token cost but lets more insecure code through.)
- Tools the agent gets: `snyk_code_scan`, `snyk_sca_scan`, `snyk_iac_scan`, `snyk_container_scan`, `snyk_sbom_scan`, `snyk_aibom`, `snyk_package_health_check`, `snyk_trust`.
- Plugin option: `claude plugin install https://github.com/snyk/claude-plugin-snyk` — SAST + SCA on every file edit, blocks the reply for up to 3 fix rounds, adds `/snyk-fix`; needs `uv`; works on free tier — but burns Code tests.

## Commands `[DOCS]`

Enable first: Snyk web app → Settings → Snyk Code → Enabled.

| Product | Command |
|---|---|
| SAST | `snyk code test --report --project-name=exam-oracle` |
| SCA | `pip install -r requirements.txt` (or `npm ci`) first, then `snyk test --all-projects --command=python3` and `snyk monitor --all-projects` (or `--skip-unresolved=true`) |
| IaC | `snyk iac test --report` (Terraform, CloudFormation, Kubernetes, ARM; docker-compose likely not scanned) |
| Container | `snyk container test <img> --file=Dockerfile` |
| AI-BOM | `snyk aibom --html` (Python and JS) |
| Agent config | `uvx snyk-agent-scan@latest` (needs `SNYK_TOKEN`; sends tool descriptions to Snyk) |

Free plan per month: Code 100 tests, IaC 300, Container 100, max 5 projects; Open Source listed as 200 (a docs snippet said 400 — plan on 200). Tests on public repos don't count toward limits.

## Findings to design out `[DOCS]` rule names; fixes `[DESIGN]`

| Snyk rule | Where it would appear | Prevention |
|---|---|---|
| SSRF (CWE-918) | Course URL input | Allowlist `https://ocw.mit.edu`; parse with `urllib.parse`; disable redirects or re-check each hop |
| Path Traversal (CWE-23), Tar Slip | Downloaded filenames | Name files by SHA-256 inside a fixed directory; never use the remote name |
| SQL / Code / NoSQL Injection | hotdata SQL, Cypher | Parameters only (`$param`); never f-strings; LLM-written SQL only through a read-only path |
| Hardcoded Secret / Credentials | Keys | Environment variables; `.env` git-ignored from the first commit; `.env.example` committed |
| Improper Certificate Validation | HTTP clients | Never `verify=False` |
| Debug Mode Enabled | Worker HTTP layer | Off |
| XSS / `dangerouslySetInnerHTML` | React app rendering LLM output | Render as text |
| Open Redirect | App links | No redirects from user input |
| Insecure JWT / JWT `none` | Tunnel auth | Use a random bearer token; validate strictly |

Not covered by Snyk rules (on us): prompt injection, an unauthenticated tunnel, cross-student data access.

## Routine (10 minutes, at hour 1 and hour 6)

1. Pull; `pip install` / `npm ci`; `snyk auth`.
2. `snyk code test --report --project-name=exam-oracle`.
3. `snyk test --all-projects`; `snyk monitor --all-projects`.
4. Fix high/critical. Any remaining ignore goes in `.snyk` with a written reason.
5. Re-run step 2; commit.

Each `--report` run adds a snapshot, so the project shows "security history over time"; `monitor` keeps snapshots too. Screenshot both for the submission.

## Beyond hygiene

- `snyk aibom --html` → an honest diagram of models, agent libraries and MCP servers in the stack (architecture slide).
- `snyk_package_health_check` before choosing a PDF library.
- Natural compounding tie-in: store each fixed finding as a lesson the coding agent checks before its next change `[INFERRED]` (optional; don't force it).

## Sources

- https://docs.snyk.io/agent-security/agentic-security-with-snyk-studio/quickstart-guides/claude-code-guide
- https://docs.snyk.io/agent-security/agentic-security-with-snyk-studio/getting-started-with-snyk-studio
- https://github.com/snyk/claude-plugin-snyk
- https://docs.snyk.io/scan-fix-and-prevent/scan-with-snyk/snyk-code/configure-snyk-code.md
- https://docs.snyk.io/developer-tools/snyk-cli/scan-and-maintain-projects-using-the-cli/snyk-cli-for-snyk-code/scan-source-code-with-snyk-code-using-the-cli.md
- https://docs.snyk.io/supported-languages/supported-languages-list/python/snyk-cli-for-python.md
- https://docs.snyk.io/developer-tools/snyk-cli/scan-and-maintain-projects-using-the-cli/monitor-your-projects-at-regular-intervals.md
- https://docs.snyk.io/developer-tools/snyk-cli/commands/iac-test.md · https://docs.snyk.io/developer-tools/snyk-cli/commands/container-test.md · https://docs.snyk.io/developer-tools/snyk-cli/commands/aibom.md
- https://snyk.io/plans/ · https://docs.snyk.io/snyk-data-and-governance/what-counts-as-a-test
- https://docs.snyk.io/scan-fix-and-prevent/scan-with-snyk/snyk-code/snyk-code-security-rules/python-rules.md
- https://docs.snyk.io/scan-fix-and-prevent/scan-with-snyk/snyk-code/snyk-code-security-rules/javascript-and-typescript-rules.md
- https://github.com/snyk/agent-scan
