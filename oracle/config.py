"""Configuration, repository paths, validators and script conventions.

Spec:
  * kit/05-build/repo-layout-and-config.md — env names, paths, `.env` git-ignored.
  * contracts/README.md "Loading and querying" (hotdata env names, corrected against the SDK) and
    "Ground truth" (SEALED_DIR must be outside the repo; the only exception is contracts/fixtures/sealed/).
  * kit/05-build/security-checklist.md — fetch only from https://ocw.mit.edu; secrets only in env.
  * kit/03-architecture/rote-plays.md — scripts print ONE JSON object and use documented exit codes.
  * contracts/prediction.schema.json — canonical JSON + SHA-256 (the hash is never inside the object).

Environment (all optional; defaults in brackets):
  ORACLE_BACKEND      local | hotdata                                      [local]
  LESSONS_STORE       local | hydradb                                      [local]
  STUDENT_STORE       local | hydradb                                      [local]
  ORACLE_LOCAL_DIR    folder of the offline backend and stores             [<repo>/.local-backend]
  ORACLE_REPO_ROOT    repo root if the package is installed non-editable   [parent of oracle/]
  ORACLE_SKIP_DOTENV  "1" = do not read <repo>/.env
  SEALED_DIR          human answer keys, OUTSIDE the repo (resolve_sealed_dir)
  HOTDATA_LEDGER_DB   hotdata ledger database name (or id, once known)     [ledger]
  HYDRADB_DATABASE    HydraDB database                                     [exam-oracle]
  HOTDATA_API_KEY, HOTDATA_WORKSPACE, HOTDATA_API_URL  read by hotdata_framework.HotdataClient.from_env()
  HYDRADB_API_KEY, HYDRADB_BASE_URL                    read by the HydraDB stores
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

PACKAGE_DIR = Path(__file__).resolve().parent


def _repo_root() -> Path:
    override = os.environ.get("ORACLE_REPO_ROOT", "").strip()
    return Path(override).expanduser().resolve() if override else PACKAGE_DIR.parent


REPO_ROOT: Path = _repo_root()
CONTRACTS_DIR = REPO_ROOT / "contracts"
LEDGER_SCHEMA_PATH = CONTRACTS_DIR / "ledger-schema.sql"
PREDICTION_SCHEMA_PATH = CONTRACTS_DIR / "prediction.schema.json"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
FIXTURE_SEALED_DIR = FIXTURES_DIR / "sealed"
SQL_DIR = REPO_ROOT / "sql"
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
TOPICS_DIR = DATA_DIR / "topics"
DEFAULT_LOCAL_DIR = REPO_ROOT / ".local-backend"
DOTENV_PATH = REPO_ROOT / ".env"

OCW_HOST = "ocw.mit.edu"
HYDRADB_SHARED_COLLECTION = "shared"
ID_PATTERN = r"^[A-Za-z0-9._-]+$"
SECRET_ENV = ("HOTDATA_API_KEY", "HOTDATA_WORKSPACE", "HYDRADB_API_KEY", "LLM_API_KEY", "EMBEDDING_API_KEY")

# Exit codes (kit/03-architecture/rote-plays.md). A degraded-but-ok step exits 0 with {"ok": true, "warning": ...}.
EXIT_OK = 0
EXIT_HARD = 1        # hard fault: HTTP error, load failure, service error, bad config
EXIT_SKIP_LIST = 2   # a skip-listed (sealed) URL was encountered
EXIT_VALIDATION = 3  # problem numbering not 1..N or points != printed total
EXIT_LEAKAGE = 4     # a run database contains a row the target exam may not see

_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class ConfigError(ValueError):
    """Invalid or missing configuration."""


class SealViolation(RuntimeError):
    """Sealed material was requested out of order (kit/BUILDER-RULES.md §4: highest-severity bug)."""


# ------------------------------------------------------------------ .env (python-dotenv may be absent)
_DOTENV_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
_QUOTED_VALUE = re.compile(r"""(['"])(.*?)\1(?:\s+#.*)?""")
_dotenv_loaded = False


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines: blank lines and `#` comments skipped, optional `export`, single/double
    quotes stripped, and an unquoted value ends at the first whitespace-preceded `#`. No interpolation."""
    pairs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _DOTENV_LINE.fullmatch(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        quoted = _QUOTED_VALUE.fullmatch(value)
        pairs[key] = quoted.group(2) if quoted else re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return pairs


def load_dotenv(path: str | os.PathLike | None = None, *, override: bool = False) -> dict[str, str]:
    """Export the non-empty pairs of `path` (default <repo>/.env) into os.environ. Variables already set
    in the environment win unless override=True. Returns everything parsed."""
    dotenv = Path(path) if path is not None else DOTENV_PATH
    if not dotenv.is_file():
        return {}
    pairs = parse_dotenv(dotenv.read_text(encoding="utf-8"))
    for key, value in pairs.items():
        if value and (override or not os.environ.get(key)):
            os.environ[key] = value
    return pairs


def _ensure_dotenv() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        _dotenv_loaded = True
        if os.environ.get("ORACLE_SKIP_DOTENV", "").strip() != "1":
            load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable (after loading <repo>/.env once). Empty counts as unset."""
    _ensure_dotenv()
    value = os.environ.get(name, "").strip()
    return value or default


# ------------------------------------------------------------------ validators
def is_valid_id(value: object, *, max_len: int = 200) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= max_len
            and _ID_RE.fullmatch(value) is not None and value.strip(".") != "")


def validate_id(value: object, *, what: str = "id", max_len: int = 200) -> str:
    """Return `value` if it matches ^[A-Za-z0-9._-]+$ (not only dots, at most max_len chars), else ValueError.

    The one gate for anything rendered into hotdata SQL, used as a HydraDB id, or put in a file name
    (course, exam_id, run_id, hw_id, topic_id, student_id, database names)."""
    if not is_valid_id(value, max_len=max_len):
        raise ValueError(f"invalid {what}: {value!r} (allowed: letters, digits, '.', '_', '-')")
    return value  # type: ignore[return-value]


def validate_ocw_url(url: object) -> str:
    """Return `url` if it is https://ocw.mit.edu/... (port 443 only, no userinfo, no whitespace or control
    characters), else ValueError. Call it on the first URL AND on every redirect hop (security-checklist.md)."""
    if not isinstance(url, str) or not url or any(ord(c) <= 0x20 or ord(c) == 0x7F or c in '\\"<>`' for c in url):
        raise ValueError(f"rejected URL {url!r}: empty, or contains whitespace/control/unsafe characters")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"rejected URL {url!r}: bad port") from exc
    if (parts.scheme != "https" or (parts.hostname or "") != OCW_HOST or port not in (None, 443)
            or parts.username is not None or parts.password is not None):
        raise ValueError(f"rejected URL {url!r}: only https://{OCW_HOST}/ is allowed")
    return url


def is_ocw_url(url: object) -> bool:
    try:
        validate_ocw_url(url)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ sealing
def _is_within(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or below it — also on case-insensitive filesystems (macOS)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        pass
    if parent.exists():
        for ancestor in (child, *child.parents):
            if ancestor.exists() and os.path.samefile(ancestor, parent):
                return True
    return False


def resolve_sealed_dir(raw: str | os.PathLike | None = None) -> Path:
    """Resolve SEALED_DIR (or `raw`) and enforce the sealing rule (contracts/README.md "Ground truth"):
    it must be OUTSIDE the repository; the only exception is contracts/fixtures/sealed/ (synthetic FX.101
    keys). Symlinks are resolved first, so a link that points back into the repo is rejected too."""
    value = str(raw) if raw is not None else env("SEALED_DIR")
    if not value:
        raise ConfigError("SEALED_DIR is not set; point it at a folder outside the repository")
    path = Path(value).expanduser().resolve()
    fixture = FIXTURE_SEALED_DIR.resolve()
    if _is_within(path, REPO_ROOT.resolve()) and not _is_within(path, fixture):
        raise ConfigError(f"SEALED_DIR {path} is inside the repository {REPO_ROOT}; sealed material must "
                          f"live outside it (the only allowed in-repo folder is {fixture})")
    return path


def sealed_answer_key_path(exam_id: str, *, hash_file: str | os.PathLike,
                           sealed_dir: str | os.PathLike | None = None) -> Path:
    """Path of the human answer key `answer_key_<exam_id>.csv`, released ONLY after the prediction's hash
    file exists and starts with a SHA-256 hex digest (contracts/README.md "Ground truth": score.py reads the
    key only after the hash is written). Does not open the key. Raises SealViolation otherwise."""
    validate_id(exam_id, what="exam_id")
    hash_path = Path(hash_file)
    try:
        head = hash_path.read_text(encoding="utf-8").strip()[:64]
    except OSError:
        head = ""
    if not _HEX64_RE.fullmatch(head):
        raise SealViolation(f"refusing to locate the answer key for {exam_id}: no sealed prediction hash at {hash_path}")
    return resolve_sealed_dir(sealed_dir) / f"answer_key_{exam_id}.csv"


# ------------------------------------------------------------------ settings
@dataclass(frozen=True)
class Settings:
    backend: str            # local | hotdata
    lessons_store: str      # local | hydradb
    student_store: str      # local | hydradb
    local_dir: Path
    hotdata_ledger_db: str
    hydradb_database: str
    sealed_dir_raw: str | None

    @property
    def cognee_data_root(self) -> Path:
        return self.local_dir / "cognee" / "data"

    @property
    def cognee_system_root(self) -> Path:
        return self.local_dir / "cognee" / "system"

    def sealed_dir(self) -> Path:
        """SEALED_DIR, validated by resolve_sealed_dir (ConfigError if unset or inside the repo)."""
        return resolve_sealed_dir(self.sealed_dir_raw or "")

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to print: no secret values, only whether each secret is present."""
        return {
            "repo_root": str(REPO_ROOT), "backend": self.backend, "lessons_store": self.lessons_store,
            "student_store": self.student_store, "local_dir": str(self.local_dir),
            "hotdata_ledger_db": self.hotdata_ledger_db, "hydradb_database": self.hydradb_database,
            "secrets_present": {name: bool(env(name)) for name in SECRET_ENV},
        }


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    value = (env(name) or default).lower()
    if value not in allowed:
        raise ConfigError(f"{name}={value!r}; expected one of: {', '.join(allowed)}")
    return value


def get_settings() -> Settings:
    """Settings from the environment (+ <repo>/.env once). Read fresh on every call, so tests can monkeypatch."""
    try:
        ledger = validate_id(env("HOTDATA_LEDGER_DB") or "ledger", what="HOTDATA_LEDGER_DB")
        hydra = validate_id(env("HYDRADB_DATABASE") or "exam-oracle", what="HYDRADB_DATABASE")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return Settings(
        backend=_choice("ORACLE_BACKEND", ("local", "hotdata"), "local"),
        lessons_store=_choice("LESSONS_STORE", ("local", "hydradb"), "local"),
        student_store=_choice("STUDENT_STORE", ("local", "hydradb"), "local"),
        local_dir=Path(env("ORACLE_LOCAL_DIR") or DEFAULT_LOCAL_DIR).expanduser().resolve(),
        hotdata_ledger_db=ledger,
        hydradb_database=hydra,
        sealed_dir_raw=env("SEALED_DIR"),
    )


def configure_cognee_env(settings: Settings | None = None) -> dict[str, str]:
    """Point Cognee's storage at <LOCAL_DIR>/cognee/{data,system} unless DATA_ROOT_DIRECTORY /
    SYSTEM_ROOT_DIRECTORY are already set, create the folders, and return the effective values.

    Call BEFORE `import cognee`: its default is a folder inside the installed package (contracts/README.md,
    Cognee 1.5.4). Raises RuntimeError if cognee is already imported while a variable is still unset."""
    s = settings or get_settings()
    wanted = {"DATA_ROOT_DIRECTORY": s.cognee_data_root, "SYSTEM_ROOT_DIRECTORY": s.cognee_system_root}
    missing = [key for key in wanted if not os.environ.get(key, "").strip()]
    if missing and "cognee" in sys.modules:
        raise RuntimeError(f"configure_cognee_env() must run before importing cognee (unset: {', '.join(missing)})")
    for key in missing:
        os.environ[key] = str(wanted[key])
    effective = {key: os.environ[key] for key in wanted}
    for folder in effective.values():
        Path(folder).mkdir(parents=True, exist_ok=True)
    return effective


# ------------------------------------------------------------------ script conventions
def canonical_json(obj: Any) -> bytes:
    """The exact bytes that are hashed (contracts/prediction.schema.json): sorted keys, no whitespace,
    UTF-8, ensure_ascii=False. NaN/Infinity are rejected (they are not JSON)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def emit(obj: Mapping[str, Any]) -> None:
    """Print ONE JSON object on stdout (rote-plays.md). Non-JSON values (Paths) are stringified."""
    sys.stdout.write(json.dumps(dict(obj), sort_keys=True, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m oracle.config",
                                     description="Print the resolved configuration (no secret values) as one JSON object.")
    parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        emit({"ok": False, "error": str(exc)})
        return EXIT_HARD
    out: dict[str, Any] = {"ok": True, **settings.public_dict()}
    try:
        out.update(sealed_dir=str(settings.sealed_dir()), sealed_dir_ok=True)
    except ConfigError as exc:
        out.update(sealed_dir_ok=False, sealed_dir_error=str(exc))
    emit(out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
