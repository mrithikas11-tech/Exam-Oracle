---
type: google-doc
source_url: "https://docs.google.com/document/d/14FdPbLDZUuUei7JtLK-oQJeivwzej8ik-_PgCrs1EmY/edit"
source_type: google-doc
source_id: "14FdPbLDZUuUei7JtLK-oQJeivwzej8ik-_PgCrs1EmY"
title: "brainstorm-target-doc"
last_synced: "2026-09-11T08:44:52.886479"
freshness: fresh
tags: [operations, google-doc]
---

# brainstorm-target-doc

> [!info] Source: [Google Doc](https://docs.google.com/document/d/14FdPbLDZUuUei7JtLK-oQJeivwzej8ik-_PgCrs1EmY/edit)
> Last synced: 2026-09-11 08:44
Data and AI Hackathon: From Memory to Muscle Memory

Hackathon Problem Statement & Official Builder Guide

Event Details

Information

Format

8-Hour Build Sprint

Date

Sep 11, 2026

Venue

[AWS Builder Loft](https://www.google.com/maps/place/AWS+Builder+Loft/data=!4m2!3m1!19sChIJYU1rSACBhYAR8gX_suu-tiA)

Track Partners

RocketRide.ai · HydraDB · hotdata.dev · Cognee.ai · [Modiqo.ai](http://modiqo.ai) · Snyk

# 1. Theme

Most AI agents today are smart once and forgetful forever. They can reason brilliantly in a single session, but the moment that session ends, everything they learned what worked, what the user prefers, what the data actually looks like evaporates. The next task starts from zero, at full token cost, with full risk of repeating yesterday's mistakes.

Compound Agents is about building systems where every run makes the next run smarter, cheaper, and more reliable. That requires five things working together as one cohesive stack, not five disconnected API keys:

- Structure: Turning messy raw data into something an agent can actually reason over.
- Memory: A durable, queryable substrate where that structure lives and compounds over time.
- Insight: The ability to query, join, and analyze live and historical data on demand.
- Motion: Turning what the agent knows into real actions across real tools.
- Muscle Memory: Remembering how a task was done successfully, so it doesn't have to be rediscovered from scratch next time.

Teams at this hackathon will build a product, agent, or workflow that shows the full loop: an agent that learns something, stores it, queries it, acts on it, and gets more reliable every time it does.

# 2. Mandated Tech Stack

Every submitted project must integrate all five sponsor technologies in a meaningful, load-bearing way. Judges will specifically check that each tool is actually doing work in your architecture not just imported once and never called again.

The five tools are designed to stack: each one owns a distinct layer, and they are built to be combined rather than compete with each other. Below is what each tool is for and where it slots into your application.

## 🧩 Cognee.ai   The Memory Construction Layer

Cognee is an open-source memory platform for AI agents. It ingests raw, messy data documents, tickets, conversations, code, Slack threads, PDFs and runs it through an ECL pipeline (Extract, Cognify, Load) that turns unstructured input into a structured knowledge graph with embeddings and relationships.

- Point Cognee at your raw data sources (files, APIs, conversation logs, whatever your idea produces) and let it extract entities and relationships automatically instead of hand-writing a schema.
- Use Cognee's remember / recall primitives to turn a single interaction into a durable memory unit rather than a one-off chat turn.
- Treat Cognee as the layer that decides what gets remembered and how it's structured the graph it builds is what downstream layers store and query.

## 🗄️ HydraDB   The Memory Storage & Serving Layer

HydraDB is an object-store-native graph database purpose-built as the context substrate for AI systems. It gives you snapshot-consistent OpenCypher queries and fast multi-hop graph traversal over the structured memory your agent accumulates.

- Use HydraDB as the durable home for the graph Cognee constructs this is where memory actually lives across sessions, users, and time, not just within a single run.
- Query it with Cypher for relationship-aware retrieval (multi-hop) instead of flat similarity search "what changed since last session," "who owns this," "what's blocking this" are HydraDB questions, not vector-search questions.
- Write to it continuously as your agent operates so memory compounds instead of resetting between runs.

Cognee vs. HydraDB, in one line: Cognee decides what's worth remembering and shapes it into a graph; HydraDB is where that graph is durably stored and served at low latency. Most winning projects will use them together Cognee as the front door for ingestion, HydraDB as the backing store.

## ⚡ hotdata.dev   The Live Query & Analytics Layer

hotdata.dev is an ultra-high-concurrency execution layer that gives every agent an isolated, ephemeral environment to run SQL, vector, full-text, and geospatial queries against your existing data without waiting on shared compute.

- Give your agent fast, isolated, ad-hoc query power over structured or semi-structured data (a product catalog, a transaction log, a location dataset, a live leaderboard) that doesn't need to go through the memory graph.
- Use it for the "what's happening in the data right now" questions search, aggregate, join, filter that complement HydraDB's relationship-aware recall with raw analytical horsepower.
- Load a file directly (e.g. Parquet/CSV) into an instant database for fast scratch work, or query existing sources in place.

## 🚀 RocketRide.ai   The Motion / Orchestration Layer

RocketRide is the AI orchestration and agent-execution layer it's how your system turns "what the agent knows" into "what the agent does": tool calls, multi-step task execution, real API actions.

- Wire RocketRide as the execution engine that reads from HydraDB memory and hotdata.dev query results, decides the next action, and executes it.
- Use it to orchestrate calls to external tools/APIs the actual "motion": sending a message, booking something, updating a record, triggering a workflow.
- If your project has multiple steps or chained tool calls, this is the layer that sequences and executes them.

## 🔁 Modiqo.ai (Rote)   The Muscle-Memory / Reliability Layer

Modiqo's Rote is a local execution layer that watches what your agent does when a task succeeds, and turns that successful run into deterministic, reusable code no server infrastructure, sidecars, or SDKs required to connect it to your APIs.

- Sit Rote underneath your RocketRide execution loop: the first time a task succeeds, Rote captures the working path (API calls, sequence, parameters).
- On repeat runs of the same task, replay the captured deterministic workflow instead of re-reasoning from scratch cutting token usage and reserving inference for genuinely novel parts of the task.
- Use it to make your project's second, third, and tenth run visibly faster, cheaper, or more reliable than the first this is the "proof of compounding" judges will look for.

Please complete your setup today before arriving by visiting the [Rote Playoffs guide](https://www.modiqo.ai/blog/the-playoffs). Make sure to complete the following requirements:

- Signup + install.
- Run 'hello world' warm-up play.

Sign into the Discord channel and say 'ready & warmed up'.

## Snyk

Snyk is a developer-first cybersecurity platform that helps teams build fast without compromising on security.

[Sign-up to Snyk for free](https://app.snyk.io/signup?utm_source=evt_260101_aiseceng_meetups_amer_sf_2026&utm_medium=aisecurity-engineer&utm_campaign=sfhackathon), connect Snyk to your favorite AI coding tool (Claude, Codex, Cursor, etc) and use it to ensure your app doesn’t have any security vulnerabilities (Apps will be scanned with Snyk and security vulnerabilities found will reduce points from the final score)

Note on Judging: Every layer must do real, repeated work over the course of the 8 hours not a one-line SDK import that's never called again. The strongest projects will visibly get better at their task as the hackathon progresses, because memory (Cognee/HydraDB), insight (hotdata.dev), muscle memory (Modiqo), and security (Snyk) are all compounding underneath the agent's actions (RocketRide).

# 3. Reference Architecture

This is the suggested shape most winning projects will follow. The five tools are stackable each layer is independently useful, but combining them is what creates winning entries.

Data Flow in One Line:Cognee turns raw data into structured memory ➔ HydraDB stores and serves that memory durably ➔ hotdata.dev adds fast ad-hoc query power over live/structured data ➔ RocketRide decides and acts on both ➔ Modiqo captures what worked so the next run doesn't have to rediscover it ➔ The loop compounds.

Teams are encouraged to remix the stack (e.g., hotdata.dev feeding HydraDB, or Modiqo capturing a hotdata.dev query pattern instead of a RocketRide action) as long as all five are load-bearing.

# 4. Suggested Project Ideas

Pick one, remix one, or bring your own. Every idea below utilizes all five mandated technologies.

Project Idea

Cognee (Memory)

HydraDB (Storage)

hotdata.dev (Live Query)

RocketRide.ai (Motion)

Modiqo.ai (Muscle Memory)

Compounding Support Desk

Structures every ticket, reply, and resolution into entities and relationships

Stores customer + ticket + resolution graph across sessions

Queries live ticket volume, SLA breaches, and product-area trends

Executes replies, escalations, and record updates

Captures resolution playbooks as reusable, deterministic fixes for repeat issues

Self-Improving Research Assistant

Extracts claims, sources, and contradictions from new papers/articles

Persists the evolving research graph across sessions

Runs ad-hoc queries over a live feed of new papers/signals

Fetches, reads, summarizes, and files findings autonomously

Turns a successful research workflow (search ➔ filter ➔ summarize) into a reusable pipeline

Recruiting Copilot That Gets Faster

Builds candidate–skill–role relationship graph from resumes/applications

Stores pipeline history and past outreach outcomes

Searches/filters live candidate activity and role requirements

Sends outreach, schedules interviews

Captures the outreach sequence that got a reply and replays it for similar candidates

Fraud/Anomaly Graph Agent

Extracts entities (accounts, devices, merchants) from transaction narratives

Models the entity-relationship graph for multi-hop fraud detection

Runs fast SQL/geo queries over live transaction streams

Triggers holds, alerts, or human-review hand-offs

Captures investigation steps that confirmed fraud as a reusable triage workflow

Personal Finance Memory Agent

Structures spend history and goals into a personal graph

Remembers spending patterns and goals over time

Queries live price, market, and spend data on demand

Nudges or executes budgeting actions

Turns a successful budgeting intervention into a repeatable, deterministic nudge routine

Legal/Contract Research Agent

Extracts clauses, precedents, and obligations from filings

Stores the clause / precedent / obligation graph long-term

Searches live incoming filings/contracts for relevant matches

Automates document assembly and redlines

Captures a successful redline pattern as a reusable clause-fix workflow

Event Networking Matchmaker

Builds attendee interest / connection graph from check-ins and profiles

Persists the connection graph across the event

Queries live check-in, location, and interest-tap data

Sends real-time "you should meet X" nudges

Captures which nudge sequences led to real meetups and reuses them

AI Coding Agent with a Real Memory

Structures codebase history, decisions, and past errors

Stores codebase and decision graph across sessions

Queries live CI results, test coverage, or repo activity

Executes fixes, PRs, or refactors

Captures a successful debugging path and replays it when the same error appears

Smart Home / IoT Orchestrator

Extracts household routines and preferences from usage logs

Remembers routines and preferences as a long-term graph

Queries live sensor events (motion, temp, door/window)

Executes actual automations (lights, locks, thermostat)

Captures automation sequence that resolved a conflict and reuses it next time

Sales Deal Memory Agent

Structures call notes, emails, and CRM activity into a deal graph

Stores deal history and stakeholder relationships over time

Queries live pipeline/account data for risk signals

Drafts follow-ups and updates CRM records

Captures the follow-up sequence that moved a deal forward and reuses it on similar deals

# 5. Setup Checklist

Complete this checklist before the event starts 8 hours fast, don't burn the first hour on account creation!

- RocketRide.ai: Create an account on [staging.rocketride.ai](http://staging.rocketride.ai) and add credits using the coupon code given at the hackathon & generate an API key.Full Set up guide: [Guide](https://docs.google.com/document/d/1BWLk8x0EPWEO41vrC0wnK679EYRAy1uYe4gOPX8M31s/edit?tab=t.0)Full Step by step tutorial: [Youtube Tutorial](https://www.youtube.com/watch?v=IFPQmniW8OA&t=56s)
- HydraDB: Create an instance (cloud or local via the open-source repo) and confirm your connection string.
- hotdata.dev: Install the CLI (brew install hotdata-dev/tap/cli), register, and connect at least one data source.
- Cognee.ai: Install the SDK (self-hosted, Docker, or Cognee Cloud) and confirm you can run a basic ingest.
- Modiqo.ai (Rote): Get access to Rote and confirm it can connect to at least one of your target APIs.
- [Snyk.io](http://snyk.io): Create a [free account](https://app.snyk.io/signup?utm_source=evt_260101_aiseceng_meetups_amer_sf_2026&utm_medium=aisecurity-engineer&utm_campaign=sfhackathon), and ensure your project is free of security vulnerabilities

# 6. Pre-Hackathon Warm-up

Please complete your setup today before arriving by visiting the [Rote Playoffs guide](https://www.modiqo.ai/blog/the-playoffs). Make sure to complete the following requirements:

- Signup + install.
- Run 'hello world' warm-up play.

Sign into the Discord channel and say 'ready & warmed up'.


<!-- nova-wikilinks -->
## Related

[[associate-will|Will]] · [[phd-hv-power-electronics-design|PhD: Hv Power Electronics Design]] · [[_seal-brain-dashboard|SEAL Brain]]
