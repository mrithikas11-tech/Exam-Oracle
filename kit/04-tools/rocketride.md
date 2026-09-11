# RocketRide.ai — Motion layer

**Brief's role:** "the AI orchestration and agent-execution layer … tool calls, multi-step task execution, real API actions … reads from HydraDB memory and hotdata.dev query results, decides the next action, and executes it."
**Exam Oracle's role:** the student-facing app and the agent pipelines that call Cognee, HydraDB and hotdata through RocketRide's own nodes.

Read `01-brief/rocketride-setup-guide-original.md` first (the official hackathon guide).

## What it really is `[DOCS]`

- An open-source (MIT) pipeline runtime: multithreaded C++ core running Python nodes; pipelines are JSON `.pipe` files. The same pipeline runs locally (VS Code local engine or Docker on `ws://localhost:5565`), on-prem, or in the cloud.
- The hackathon uses the **staging** cloud (`https://staging.rocketride.ai`): apps are React (`src/App.tsx`) in `.rrapp` workspaces with five tabs (Dashboard, Design, Package, Store, Deploy), deployed as immutable versions to @me / @team. Every pipeline run draws on the organization's token balance (redeem the promo code).
- That deployed apps' pipeline runs execute on the staging cloud engine, not the laptop, is `[INFERRED]` from the credits line.

## Node catalog (relevant) `[DOCS]`

- LLMs: 15+ providers.
- Documents: `core/parser` (one `data` input; `text`/`table`/`image` outputs; output flattened to Markdown; page boundaries lost), `ocr`, `llamaparse`, `reducto`, `extract_data`.
- Agents: `agent_rocketride` ("Wave" — plans parallel tool calls with keyed memory; experimental), CrewAI, LangChain, Deep Agent.
- Tools: `tool_http_request` (agent-only; regex URL allowlist; 10 req/s, 5 concurrent), `tool_python` (RestrictedPython; no network, no subprocess; `hashlib` and `json` allowed), `tool_pipe` (a sub-pipeline as one tool).
- **Sponsor nodes:** `tool_cognee` (`cognee.remember`, `cognee.recall`, `cognee.memory_status`; `base_url` default `http://localhost:8000`; no data lanes → agent-only), `graph_hydradb` (`store_memory`, `recall_memory`, `query_graph`, `get_schema`; talks to the **hosted** HydraDB API with api_key/database/collection; docs page says "no query language" yet lists `query_graph`), `db_hotdata` (`get_data`, `execute`, `load_data`, `build_index`; database ephemeral per run by default — set `database_id` to persist; `allow_execute` for raw SQL; needs an LLM for text-to-SQL; an `answers` lane can log agent answers).
- Also: memory nodes, 11 vector stores, webhook source (automatic public POST URL + auth key), Slack node.
- **Docs contradict themselves** (`components` vs `nodes`; `parse` vs `core/parser`; HydraDB as `graph_hydradb` vs `db_hydradb://`). Trust the workspace's `services-catalog.json`.

## Interfaces `[DOCS]`

- `.pipe`: `components[]`, each `{id, provider, config, input:[{lane, from}], control:[{classType, from}]}`; the tool→agent link is declared on the tool.
- Python client: `client.use(filepath|pipeline)`, then `send`, `chat(Question)`, `send_files`, `get_task_status`, `terminate`. TypeScript: same calls; in the browser pass the `pipeline` object, not a file path.
- CLI: `rocketride validate | start | upload | status | stop | store`; workspace auth `pnpm exec rocketride login`.
- Secrets: `${ENV_VAR}` substitution in node config; env var is `ROCKETRIDE_APIKEY` in some docs and `ROCKETRIDE_AUTH` in others — check your `.env`.
- MCP: any running pipeline can be exposed as an MCP tool (`pip install rocketride-mcp`) — Rote could wrap it with `rote adapter new-from-mcp` `[INFERRED seam]`.
- Tracing in the VS Code extension shows token usage for every call.

## How Exam Oracle uses RocketRide fully `[DESIGN]`

- Build the app from pipelines that put the three sponsor nodes on the canvas (see `03-architecture/rocketride-pipelines.md`): upload-syllabus, study-plan, feedback, dashboard, reveal, course-loaded webhook.
- Use Wave for planning multi-tool answers; `tool_python` for the SHA-256 seal and chart shaping; parser/LlamaParse for student uploads; webhooks to connect the laptop's Rote Plays.
- Use tracing screenshots (tokens per call) as sponsor-native evidence on the "cheaper" slide.
- Don't use RocketRide for bulk OCW loading — that belongs to Rote on the laptop (RocketRide's Python node cannot run subprocesses, so it cannot call Rote).

## Hackathon deployment steps (from the official guide) `[DOCS]`

1. Download the staging VSIX (`https://staging.rocketride.ai/client/vscode`, currently `rocketride-1.2.0.vsix`); remove any existing RocketRide extension; Install from VSIX; reload.
2. Connection settings → Cloud → Use custom server `https://staging.rocketride.ai` → sign in (OAuth) → **Save** (otherwise it reverts).
3. Redeem the promo code at staging.rocketride.ai (landing page "Have a promo code?").
4. Monitor → Apps → + New app → Full screen → name (lowercase, letters/digits/_/-) → Create App.
5. On a scratch app, Deploy tab → register the organization's developer ID (letters and underscores only) — do this once before the real app.
6. Package tab Readiness all green (app id, display name, icon, README, include paths); keep strict type checking on.
7. Deploy → + Deploy → publish to @me (test) or @team (share); open staging.rocketride.ai to launch the published tile (close the App Builder panel so the dev overlay doesn't mask it).

## Gotchas `[DOCS]`

- "No authorization provided" / signed out minutes later → `.env` rewritten with empty key → `pnpm exec rocketride login`.
- Preview "Sign in required" wall → `"authenticated": false` in the app's package.json.
- App scaffolded before the developer ID was claimed → rename `appManifest.id` in package.json and `id` in `src/AppDescriptor.ts` (must match).
- Deploy failed → open the card; failing phase at top, reason on the last line.
- Wave needs exactly one LLM and one memory wired in; cap `max_waves` (default 10).
- `chat` source needs `chat()`, `webhook` needs `send()`; a mismatch returns nothing.
- Pipe pnpm, never npm, for the app toolchain.

## Smoke test (15 min)

1. `pnpm exec rocketride login`; confirm `.env` has a non-empty key.
2. Search `services-catalog.json` for `tool_cognee`, `graph_hydradb`, `db_hotdata` (the installed 1.2.0 may differ from docs).
3. `rocketride validate`, then run a one-hop webhook → response pipe on staging.
4. Run a chat → Wave pipe with an LLM, `memory_internal`, and all three sponsor tools: "remember X; recall X; get_schema on both databases" — proves the staging engine can reach each backend.
5. `rocketride upload` one OCW PDF through the parser; look at the Markdown.
6. Deploy the scratch app to @me; trigger one pipeline from App.tsx; confirm the credit balance drops.

**If it fails:** sponsor nodes missing → the Wave agent calls each service's REST API through `tool_http_request`; staging can't reach Cognee → Cognee Cloud or tunnel; Wave unstable → LangChain agent node.

## Sources

- https://docs.rocketride.org/ (nodes, pipeline-reference, concepts/agents-tools-skills, concepts/runtime-engine, concepts/security-model, develop/python, develop/typescript, self-hosting, troubleshooting, nodes/tool_cognee, nodes/graph_hydradb, nodes/db_hotdata, nodes/agent_rocketride, nodes/core/parser, nodes/webhook/webhook, nodes/tool_python, ide-extensions/vscode)
- https://github.com/rocketride-org/rocketride-server (nodes/src/nodes/*/services.json)
- `01-brief/rocketride-setup-guide-original.md`
- YouTube walkthrough (title only was retrievable): https://www.youtube.com/watch?v=IFPQmniW8OA
