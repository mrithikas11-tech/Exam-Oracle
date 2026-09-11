"""Shared pytest fixtures for Exam Oracle. Offline only: nothing here reaches a network service.

* oracle_env (session, autouse): a temporary ORACLE_LOCAL_DIR, local backend and stores, SEALED_DIR =
  contracts/fixtures/sealed, <repo>/.env skipped and live credentials removed, so no LIVE path can activate.
* build_fixture_ledger(): (re)build the local ledger from contracts/fixtures/*.csv.
* fixture_ledger: a freshly rebuilt ledger -> (backend, handle).
* sealed_dir: the synthetic sealed folder (contracts/fixtures/sealed).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "contracts" / "fixtures"
FIXTURE_SEALED_DIR = FIXTURES_DIR / "sealed"
LIVE_ENV_VARS = ("HOTDATA_API_KEY", "HOTDATA_WORKSPACE", "HOTDATA_API_URL", "HYDRADB_API_KEY",
                 "HYDRADB_BASE_URL", "LLM_API_KEY", "EMBEDDING_API_KEY")


@pytest.fixture(scope="session", autouse=True)
def oracle_env(tmp_path_factory: pytest.TempPathFactory):
    """Session-wide offline environment; yields the temporary LOCAL_DIR."""
    local_dir = tmp_path_factory.mktemp("local-backend")
    patch = pytest.MonkeyPatch()
    for name in LIVE_ENV_VARS + ("HOTDATA_LEDGER_DB", "HYDRADB_DATABASE"):
        patch.delenv(name, raising=False)
    patch.setenv("ORACLE_SKIP_DOTENV", "1")
    patch.setenv("ORACLE_LOCAL_DIR", str(local_dir))
    patch.setenv("ORACLE_BACKEND", "local")
    patch.setenv("LESSONS_STORE", "local")
    patch.setenv("STUDENT_STORE", "local")
    patch.setenv("SEALED_DIR", str(FIXTURE_SEALED_DIR))
    yield local_dir
    patch.undo()


def build_fixture_ledger(backend=None):
    """Replace every contract table of the local ledger: with the fixture CSV rows when
    contracts/fixtures/<table>.csv exists (typed from the DDL), else with no rows. Returns (backend, handle).
    The logic lives in oracle.fixture_ledger (also `python -m oracle.fixture_ledger --replace`)."""
    from oracle.fixture_ledger import load_fixture_ledger

    be, handle, _ = load_fixture_ledger(backend)
    return be, handle


@pytest.fixture
def fixture_ledger():
    return build_fixture_ledger()


@pytest.fixture
def sealed_dir() -> Path:
    return FIXTURE_SEALED_DIR.resolve()
