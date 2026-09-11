"""Exam Oracle — one package for the loader (role A) and the model & memory (role B).

Role A — loader (the `exam-oracle` CLI, driven by the Rote Play `load-course`):
  loader_config  OCW allowlist, skip list, data paths, exit codes of the loader
  ocw_index / ocw_download / extract_text / split_problems / validate   OCW -> checked problems
  structure / items / tag / load_ledger / summary / notify_rocketride  -> ledger (contracts/) and Cognee

Role B — foundation modules (other scripts build on these):
  config        paths, env (.env), backend/store selection, SEALED_DIR rule, validators, script conventions
  ordering      the time-ordering rule (term_seq, session) and feature_set
  backend       ledger + per-run databases: LocalDuckDBBackend (offline) | HotdataBackend (live)
  lessons_store the beta weights ("lessons"): LocalJSONLessonsStore | HydraDBLessonsStore

Exit codes everywhere: 0 ok (a "warning" field = degraded), 1 hard fault, 2 sealed, 3 validation, 4 leakage.
The contracts in contracts/ override kit/ wherever they differ.
"""

__version__ = "0.2.0"
