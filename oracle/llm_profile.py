"""Bring your own LLM: choose the model provider that every Cognee call uses (tagging, feedback, lesson wording).

    exam-oracle llm --provider claude            # Claude (default model claude-opus-5), local embeddings, key prompt
    exam-oracle llm --provider chatgpt           # OpenAI (Cognee's defaults: gpt-5-mini + text-embedding-3-large)
    exam-oracle llm --provider gemini --model <litellm gemini model id>
    exam-oracle llm --provider ollama --model <model> --endpoint <url>        # a local model, no key
    exam-oracle llm --provider custom --model <model> --endpoint <url>        # any OpenAI-compatible server
    exam-oracle llm --show                       # the active choice (never prints a key)
    exam-oracle llm --check                      # one tiny request through the chosen provider

Settings are written to <repo>/.env (git-ignored, mode 600) under Cognee 1.5.4's own names (LLMConfig /
EmbeddingConfig): LLM_PROVIDER, LLM_MODEL, LLM_ENDPOINT, LLM_API_KEY, EMBEDDING_PROVIDER, EMBEDDING_MODEL,
EMBEDDING_DIMENSIONS, EMBEDDING_API_KEY. The key is read with a hidden prompt (or --key-stdin), never echoed.
Embeddings: Claude has no embedding API, so non-OpenAI presets default to local fastembed (free, no key);
`--embeddings openai` uses OpenAI embeddings instead (asks for an OpenAI key).
"""
from __future__ import annotations

import getpass
import os
import re
import stat
import sys

from oracle import config as structure_config

from .loader_config import OracleError, emit

LOCAL_EMBEDDINGS = {"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
                    "EMBEDDING_DIMENSIONS": "384"}
OPENAI_EMBEDDINGS = {"EMBEDDING_PROVIDER": "openai", "EMBEDDING_MODEL": "openai/text-embedding-3-large",
                     "EMBEDDING_DIMENSIONS": "3072"}
# provider -> (Cognee LLM_PROVIDER, default model or None = --model required, needs a key, needs --endpoint)
PRESETS = {
    "chatgpt": ("openai", "openai/gpt-5-mini", True, False),
    "openai": ("openai", "openai/gpt-5-mini", True, False),
    "claude": ("anthropic", "claude-opus-5", True, False),
    "anthropic": ("anthropic", "claude-opus-5", True, False),
    "gemini": ("gemini", None, True, False),
    "ollama": ("ollama", None, False, True),
    "custom": ("custom", None, True, True),
}
MANAGED = ("LLM_PROVIDER", "LLM_MODEL", "LLM_ENDPOINT", "LLM_API_KEY", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
           "EMBEDDING_DIMENSIONS", "EMBEDDING_API_KEY", "EMBEDDING_ENDPOINT")
SECRETS = ("LLM_API_KEY", "EMBEDDING_API_KEY")


def add_args(p):
    p.add_argument("--provider", choices=sorted(PRESETS), help="which LLM to use")
    p.add_argument("--model", help="model id (defaults: claude-opus-5 for claude, openai/gpt-5-mini for chatgpt)")
    p.add_argument("--endpoint", help="base URL for ollama / custom (OpenAI-compatible) servers")
    p.add_argument("--embeddings", choices=["local", "openai"], help="embedding provider (default: openai for "
                   "chatgpt, local fastembed for everything else)")
    p.add_argument("--key-stdin", action="store_true", help="read the API key from stdin instead of a prompt")
    p.add_argument("--keep-key", action="store_true", help="keep the LLM_API_KEY already in .env")
    p.add_argument("--show", action="store_true", help="print the active choice (keys are never printed)")
    p.add_argument("--check", action="store_true", help="send one tiny request through the chosen provider")


def dotenv_path():
    return structure_config.DOTENV_PATH


def read_dotenv() -> dict[str, str]:
    path = dotenv_path()
    return structure_config.parse_dotenv(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_dotenv(updates: dict[str, str | None]) -> None:
    """Set/replace (or remove, for None) the managed keys in .env; every other line is kept as written."""
    path = dotenv_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    kept = [ln for ln in lines if not ((m := pattern.match(ln)) and m.group(1) in updates)]
    kept += [f"{k}={v}" for k, v in updates.items() if v]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def ask_secret(label: str, from_stdin: bool) -> str:
    value = sys.stdin.readline().strip() if from_stdin else getpass.getpass(f"{label}: ").strip()
    if not value or any(c.isspace() for c in value):
        raise OracleError(f"{label}: empty or malformed value; nothing was written")
    return value


def active() -> dict[str, str]:
    env = {**read_dotenv(), **{k: os.environ[k] for k in MANAGED if os.environ.get(k)}}
    shown = {k: env.get(k, "") for k in MANAGED if k not in SECRETS}
    shown.update({k: ("set" if env.get(k) else "not set") for k in SECRETS})
    shown["LLM_PROVIDER"] = shown["LLM_PROVIDER"] or "openai (Cognee default)"
    shown["LLM_MODEL"] = shown["LLM_MODEL"] or "openai/gpt-5-mini (Cognee default)"
    return shown


def check() -> dict:
    """One tiny completion through litellm (the library Cognee uses underneath) with the active settings."""
    env = {**read_dotenv(), **{k: os.environ[k] for k in MANAGED if os.environ.get(k)}}
    provider = env.get("LLM_PROVIDER") or "openai"
    model = env.get("LLM_MODEL") or "openai/gpt-5-mini"
    bare = model.split("/", 1)[1] if model.startswith(f"{provider}/") else model
    litellm_provider = "openai" if provider == "custom" else provider
    from .tag import run_captured  # litellm prints help banners to stdout; keep this command's one JSON line

    outcome: dict = {}

    def call() -> int:
        import litellm

        litellm.suppress_debug_info = True
        try:
            litellm.completion(model=f"{litellm_provider}/{bare}", messages=[{"role": "user", "content": "Reply OK."}],
                               max_tokens=256, api_key=env.get("LLM_API_KEY") or None,
                               api_base=env.get("LLM_ENDPOINT") or None, timeout=60)
        except Exception as err:  # report the provider's error class and message, never the key
            outcome["error"] = type(err).__name__
            outcome["detail"] = str(err).replace(env.get("LLM_API_KEY") or "\0", "<key>")[:300]
            return 1
        return 0

    code, _, _ = run_captured(call)
    if code != 0:
        return {"ok": False, "provider": provider, "model": model, **outcome}
    return {"ok": True, "provider": provider, "model": model}


def run(args) -> int:
    if args.show:
        emit({"ok": True, "dotenv": str(dotenv_path()), "llm": active()})
        return 0
    if args.check and not args.provider:
        result = check()
        emit(result)
        return 0 if result["ok"] else 1
    if not args.provider:
        raise OracleError("choose --provider (or use --show / --check)")
    cognee_provider, default_model, needs_key, needs_endpoint = PRESETS[args.provider]
    model = args.model or default_model
    if not model:
        raise OracleError(f"--model is required for --provider {args.provider}")
    if needs_endpoint and not args.endpoint:
        raise OracleError(f"--endpoint is required for --provider {args.provider}")
    embeddings = args.embeddings or ("openai" if cognee_provider == "openai" else "local")
    updates: dict[str, str | None] = {"LLM_PROVIDER": cognee_provider, "LLM_MODEL": model,
                                      "LLM_ENDPOINT": args.endpoint, "EMBEDDING_ENDPOINT": None,
                                      **(OPENAI_EMBEDDINGS if embeddings == "openai" else LOCAL_EMBEDDINGS)}
    if needs_key and not args.keep_key:
        updates["LLM_API_KEY"] = ask_secret(f"{args.provider} API key (hidden)", args.key_stdin)
    elif not needs_key:
        updates["LLM_API_KEY"] = "local"  # Cognee requires a non-empty key even for local servers
    if embeddings == "openai" and cognee_provider != "openai":
        updates["EMBEDDING_API_KEY"] = ask_secret("OpenAI API key for embeddings (hidden)", args.key_stdin)
    else:
        updates["EMBEDDING_API_KEY"] = None
    write_dotenv(updates)
    result = {"ok": True, "dotenv": str(dotenv_path()), "llm": active()}
    if args.check:
        for key in MANAGED:  # the new values must win over anything exported in this shell
            os.environ.pop(key, None)
        result["check"] = check()
    emit(result)
    return 0 if result.get("check", {"ok": True})["ok"] else 1
