# Repository Layout and Configuration (suggested)

```
exam-oracle/
├── CLAUDE.md                  # = BUILDER-RULES.md
├── AGENTS.md                  # = BUILDER-RULES.md
├── README.md                  # what it is, how to run, credits (MIT OCW, CC BY-NC-SA), Snyk status
├── .gitignore                 # .env, data/raw/, sealed paths, __pycache__, node_modules
├── .env.example               # names only, no values
├── .snyk                      # ignores with written reasons (if any)
├── kit/                       # this build kit, unchanged
├── oracle/                    # Python package (installable so Rote Plays run from /tmp)
│   ├── ocw_index.py  ocw_download.py  extract_text.py  split_problems.py  validate.py
│   ├── load_hotdata.py  cognee_remember.py  notify_rocketride.py
│   ├── make_run_db.py  run_signals.py  leakage_check.py  read_lessons.py
│   ├── rank_and_seal.py  score.py  append_ledger.py  refit_lessons.py  store_lessons.py
│   ├── cognee_feedback.py  projector.py (optional: Cognee → open-source HydraDB)
│   ├── models.py              # Cognee DataPoint models
│   └── config.py              # reads env; allowlist; paths
├── sql/                       # saved signal queries (x1..x7), baselines, charts
├── plays/
│   ├── load-course/           # main.ts, deps.toml (exported by Rote)
│   └── run-backtest/
├── rocketride/                # .pipe files for P1–P6 and the app (apps/exam-oracle-ui/)
├── data/
│   ├── raw/<course>/          # hash-named PDFs (git-ignored) + manifest.csv (committed)
│   └── topics/<course>.csv    # fixed topic lists (committed)
├── skip_list.txt              # sealed URLs and hashes (committed; contents are not the files)
└── docs/                      # architecture slide, Snyk screenshots, trace exports
```

The sealed folder (hidden exams, `answer_key.csv`) lives **outside** this repository, e.g. `~/exam-oracle-sealed/`.

## `.env.example`

```
# LLM
LLM_API_KEY=
LLM_EXTRACTION_MODEL=
EMBEDDING_API_KEY=

# Cognee
COGNEE_BASE_URL=            # Cognee Cloud URL or tunnel URL
COGNEE_API_TOKEN=           # if Cloud/REST auth is used — name per Cognee docs [TEST]
PERSONALIZATION_ENABLED=true

# HydraDB (hosted)
HYDRADB_API_KEY=
HYDRADB_DATABASE=exam-oracle

# HydraDB (open source, optional)
HYDRADB_OSS_BOLT_URL=bolt://127.0.0.1:7687
HYDRADB_OSS_TOKEN_FILE=

# hotdata — exact variable names per hotdata-framework docs [TEST]
HOTDATA_API_TOKEN=
HOTDATA_WORKSPACE_ID=
HOTDATA_LEDGER_DB=ledger

# RocketRide (docs use both names — check your workspace .env)
ROCKETRIDE_APIKEY=
ROCKETRIDE_WEBHOOK_URL=
ROCKETRIDE_WEBHOOK_KEY=

# Tunnel (Plan B only)
TUNNEL_BEARER_TOKEN=

# Snyk
SNYK_TOKEN=

# Paths
SEALED_DIR=                 # outside the repo
```

## Versions to record

| Component | Version | Note |
|---|---|---|
| cognee | 1.5.4 | pinned |
| hydradb-sdk | >=2,<3 | |
| HydraDB image (optional) | v0.1.1 | `ghcr.io/hydra-db/hydradb` |
| RocketRide extension | 1.2.0 | staging VSIX |
| rote | ≥ 0.62.0 (hello requirement); sidekick v0.4.98 via playoffs installer | |
| hotdata CLI / hotdata-framework / hotdata-langchain | record at install (langchain seen at 0.16.0) | |
| Python | 3.12 | Cognee supports 3.10–3.14 |
| Node / pnpm | record at install | RocketRide app uses pnpm only |

## Conventions

- Ids and names: `03-architecture/data-model.md`.
- Every script: reads config from env, logs JSON to stdout, uses documented exit codes (Rote lanes).
- Commit the topic lists and saved SQL; never commit raw PDFs or anything from the sealed folder.
