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
    enabled_models: list[str] | None = None  # None / [] = load all (whitelist override)


def _config_path() -> Path | None:
    """Locate config.yaml: CFGPU_CONFIG env, else ./config.yaml. None if absent."""
    raw = os.getenv("CFGPU_CONFIG")
    if raw:
        return Path(raw).expanduser()
    default = Path.cwd() / "config.yaml"
    return default if default.exists() else None


def _float(raw: str | None, fallback: float) -> float:
    """Parse a positive float; fall back on missing/invalid (mirrors client._env_float)."""
    if not raw:
        return fallback
    try:
        val = float(raw)
        return val if val > 0 else fallback
    except ValueError:
        return fallback


def load_settings() -> Settings:
    """Build Settings from defaults < config.yaml < environment overrides."""
    s = Settings()

    path = _config_path()
    if path and path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        s.transport = data.get("transport", s.transport)

        http = data.get("http") or {}
        s.http = HttpSettings(
            host=http.get("host", HttpSettings.host),
            port=http.get("port", HttpSettings.port),
            stateless=http.get("stateless", HttpSettings.stateless),
        )

        api = data.get("cfgpu_api") or {}
        s.base_url = api.get("base_url", s.base_url)
        s.http_timeout = float(api.get("http_timeout", s.http_timeout))
        s.connect_timeout = float(api.get("connect_timeout", s.connect_timeout))

        task_db = data.get("task_db") or {}
        s.task_db_url = task_db.get("url", s.task_db_url)

        enabled = data.get("enabled_models")
        s.enabled_models = enabled or None  # [] / null → load all

    # ── Environment overrides (backward compatibility) ────────────────────────
    s.transport = os.getenv("CFGPU_TRANSPORT", s.transport)
    s.base_url = os.getenv("CFGPU_BASE_URL", s.base_url)
    s.http_timeout = _float(os.getenv("CFGPU_HTTP_TIMEOUT"), s.http_timeout)
    s.connect_timeout = _float(os.getenv("CFGPU_CONNECT_TIMEOUT"), s.connect_timeout)

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
