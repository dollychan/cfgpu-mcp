"""Central configuration for cfgpu-mcp.

All non-secret configuration lives in ``config.yaml``; the only value that stays
in the environment is the secret ``CFGPU_API_TOKEN`` (and ``CFGPU_CONFIG``, which
points at the config file itself). Legacy environment variables remain honored as
per-field *overrides* for backward compatibility.

Precedence:  environment override  >  config.yaml  >  built-in defaults.

A missing config file is fine — everything falls back to defaults so stdio works
zero-config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_BASE_URL = "https://www.cfgpu.com/userapi/v1"
_DEFAULT_TASK_DB_URL = "sqlite:///~/.cfgpu/tasks.db"


@dataclass
class HttpSettings:
    host: str = "0.0.0.0"
    port: int = 8080
    stateless: bool = True


@dataclass
class Settings:
    transport: str = "stdio"  # "stdio" | "streamable-http"
    http: HttpSettings = field(default_factory=HttpSettings)
    base_url: str = _DEFAULT_BASE_URL
    http_timeout: float = 120.0
    connect_timeout: float = 10.0
    task_db_url: str = _DEFAULT_TASK_DB_URL
    task_db_pool_min: int = 1   # Postgres connection pool (ignored by SQLite)
    task_db_pool_max: int = 10
    enabled_models: list[str] | None = None  # None / [] = load all (whitelist override)


def _config_path() -> Path | None:
    """Locate config.yaml: CFGPU_CONFIG env, else ./config.yaml. None if absent."""
    raw = os.getenv("CFGPU_CONFIG")
    if raw:
        return Path(raw).expanduser()
    default = Path.cwd() / "config.yaml"
    return default if default.exists() else None


def _expand_env(value: str) -> str:
    """Expand a leading ``$VAR`` / ``${VAR}`` reference to its environment value.

    Lets config.yaml keep the DB URL (which carries a password) out of the file:
    ``url: $DATABASE_URL`` resolves to the env var at load time. Raises if the
    referenced variable is unset, so a typo fails loudly instead of handing the
    literal ``"$DATABASE_URL"`` to the DB driver.
    """
    if not isinstance(value, str):
        return value
    name = None
    if value.startswith("${") and value.endswith("}"):
        name = value[2:-1]
    elif value.startswith("$"):
        name = value[1:]
    if name is None:
        return value
    resolved = os.getenv(name)
    if resolved is None:
        raise ValueError(f"task_db.url references ${name}, but that environment variable is not set")
    return resolved


def parse_positive_float(raw: str | None, fallback: float) -> float:
    """Parse a positive float; fall back on missing/invalid. Shared by client._env_float."""
    if not raw:
        return fallback
    try:
        val = float(raw)
        return val if val > 0 else fallback
    except ValueError:
        return fallback


def _load_dotenv() -> None:
    """Load a local ``.env`` into the process environment, if present.

    Lets secrets (CFGPU_API_TOKEN, the DB URL referenced by ``$DATABASE_URL``)
    live in a gitignored ``.env`` instead of being exported by hand each run.
    ``override=False`` keeps the real environment authoritative, so an explicitly
    exported var still wins over ``.env`` — matching our precedence (env > yaml).
    A missing python-dotenv degrades silently: ``.env`` is a convenience, not a
    requirement, and stdio must still work zero-config.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = Path(os.getenv("CFGPU_DOTENV", ".env")).expanduser()
    if path.exists():
        load_dotenv(path, override=False)


def load_settings() -> Settings:
    """Build Settings from defaults < config.yaml < environment overrides."""
    _load_dotenv()
    s = Settings()

    path = _config_path()
    if path and path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        s.transport = data.get("transport", s.transport)

        for key, value in (data.get("http") or {}).items():
            if hasattr(s.http, key):
                setattr(s.http, key, value)

        api = data.get("cfgpu_api") or {}
        s.base_url = api.get("base_url", s.base_url)
        # Route through parse_positive_float so a non-positive yaml value (e.g. 0)
        # falls back to the default instead of being silently dropped downstream.
        if "http_timeout" in api:
            s.http_timeout = parse_positive_float(str(api["http_timeout"]), s.http_timeout)
        if "connect_timeout" in api:
            s.connect_timeout = parse_positive_float(str(api["connect_timeout"]), s.connect_timeout)

        task_db = data.get("task_db") or {}
        s.task_db_url = _expand_env(task_db.get("url", s.task_db_url))
        s.task_db_pool_min = int(task_db.get("pool_min", s.task_db_pool_min))
        s.task_db_pool_max = int(task_db.get("pool_max", s.task_db_pool_max))

        enabled = data.get("enabled_models")
        if isinstance(enabled, str):
            enabled = [enabled]  # tolerate a scalar (forgot YAML list syntax)
        elif enabled is not None and not isinstance(enabled, list):
            raise ValueError(
                f"enabled_models must be a string or list, got {type(enabled).__name__}"
            )
        s.enabled_models = enabled or None  # [] / null → load all

    # ── Environment overrides (backward compatibility) ────────────────────────
    s.transport = os.getenv("CFGPU_TRANSPORT", s.transport)
    s.base_url = os.getenv("CFGPU_BASE_URL", s.base_url)
    s.http_timeout = parse_positive_float(os.getenv("CFGPU_HTTP_TIMEOUT"), s.http_timeout)
    s.connect_timeout = parse_positive_float(os.getenv("CFGPU_CONNECT_TIMEOUT"), s.connect_timeout)

    # task_db: explicit URL wins; else legacy CFGPU_DB_PATH → sqlite URL
    db_url = os.getenv("CFGPU_TASK_DB_URL")
    if db_url:
        s.task_db_url = db_url
    elif os.getenv("CFGPU_DB_PATH"):
        s.task_db_url = f"sqlite:///{os.environ['CFGPU_DB_PATH']}"

    env_models = os.getenv("CFGPU_ENABLED_MODELS", "").strip()
    if env_models:
        s.enabled_models = [m.strip() for m in env_models.split(",") if m.strip()]

    return s
