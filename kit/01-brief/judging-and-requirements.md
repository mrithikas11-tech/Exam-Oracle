# Judging and Requirements

Extracted from `hackathon-brief-original.md` (the organizers' document). Quotes are verbatim.

## Event

- **Name:** Data and AI Hackathon: From Memory to Muscle Memory
- **Format:** 8-hour build sprint
- **Date:** Sep 11, 2026
- **Venue:** AWS Builder Loft
- **Track partners:** RocketRide.ai · HydraDB · hotdata.dev · Cognee.ai · Modiqo.ai · Snyk

## Theme: Compound Agents

> "Compound Agents is about building systems where every run makes the next run smarter, cheaper, and more reliable. That requires five things working together as one cohesive stack, not five disconnected API keys."

| Pillar | Brief's definition | Sponsor tool |
|---|---|---|
| Structure | "Turning messy raw data into something an agent can actually reason over." | Cognee |
| Memory | "A durable, queryable substrate where that structure lives and compounds over time." | HydraDB |
| Insight | "The ability to query, join, and analyze live and historical data on demand." | hotdata.dev |
| Motion | "Turning what the agent knows into real actions across real tools." | RocketRide.ai |
| Muscle Memory | "Remembering how a task was done successfully, so it doesn't have to be rediscovered from scratch next time." | Modiqo Rote |

> "Teams at this hackathon will build a product, agent, or workflow that shows the full loop: an agent that learns something, stores it, queries it, acts on it, and gets more reliable every time it does."

## Hard requirements

1. > "Every submitted project must integrate all five sponsor technologies in a meaningful, load-bearing way. Judges will specifically check that each tool is actually doing work in your architecture not just imported once and never called again."
2. > "Every layer must do real, repeated work over the course of the 8 hours not a one-line SDK import that's never called again. The strongest projects will visibly get better at their task as the hackathon progresses."
3. **Snyk:** > "Apps will be scanned with Snyk and security vulnerabilities found will reduce points from the final score." Teams should connect Snyk to their AI coding tool.
4. **Rote warm-up before arriving:** sign up + install, run the "hello world" warm-up play, sign into Discord and post "ready & warmed up".

## Reference architecture (from the brief)

> "Cognee turns raw data into structured memory ➔ HydraDB stores and serves that memory durably ➔ hotdata.dev adds fast ad-hoc query power over live/structured data ➔ RocketRide decides and acts on both ➔ Modiqo captures what worked so the next run doesn't have to rediscover it ➔ The loop compounds."

Remixing is explicitly allowed: > "e.g., hotdata.dev feeding HydraDB, or Modiqo capturing a hotdata.dev query pattern instead of a RocketRide action — as long as all five are load-bearing."

Specific guidance per tool in the brief:
- Cognee: "Use Cognee's remember / recall primitives"; "Treat Cognee as the layer that decides what gets remembered and how it's structured."
- HydraDB: "Query it with Cypher for relationship-aware retrieval (multi-hop)"; "Write to it continuously as your agent operates so memory compounds."
- hotdata: "Load a file directly (e.g. Parquet/CSV) into an instant database for fast scratch work."
- RocketRide: "the execution engine that reads from HydraDB memory and hotdata.dev query results, decides the next action, and executes it."
- Rote: "make your project's second, third, and tenth run visibly faster, cheaper, or more reliable than the first — this is the 'proof of compounding' judges will look for."

Note: the brief pitches HydraDB as Cypher-queried. Research found the RocketRide HydraDB node talks to the *hosted* HydraDB memory API instead (see `04-tools/hydradb.md`). Exam Oracle uses the hosted API as its core and keeps the Cypher database optional — be ready to explain this to the HydraDB judge.

## Setup checklist from the brief

- RocketRide: account on staging.rocketride.ai, redeem the hackathon coupon code, generate an API key. Guide in `rocketride-setup-guide-original.md`.
- HydraDB: create an instance (cloud, or local via the open-source repo) and confirm the connection string.
- hotdata.dev: `brew install hotdata-dev/tap/cli`, register, connect at least one data source.
- Cognee: install the SDK (self-hosted, Docker, or Cognee Cloud) and confirm a basic ingest.
- Modiqo Rote: get access; confirm it can connect to at least one target API.
- Snyk: free account; ensure the project is free of vulnerabilities.

## What we infer the judges will reward `[INFERRED]`

1. A chart that visibly improves across runs made during the day.
2. Each sponsor tool's work being visible (call counts, traces, the "if removed" argument).
3. Proof the improvement is not just caching (cross-course transfer; the system refusing or repairing a stale replay).
4. A clean Snyk scan with visible history.
5. A demo a non-specialist understands in five seconds.

## Unknown

- Rubric weights and judge names.
- Demo length (the plan assumes 3 minutes) and submission format (repo link? video? form?).
- Whether sponsors provide hosted instances (Cognee Cloud, HydraDB cloud) at the venue.
Ask the organizers at check-in.
