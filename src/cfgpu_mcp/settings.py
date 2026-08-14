"""Central configuration for cfgpu-mcp.

All non-secret configuration lives in ``config.yaml`` — it is the single source,
so each field has exactly one place to be set. The only values read from the
environment are the secret ``CFGPU_API_TOKEN`` and ``CFGPU_CONFIG`` /
``CFGPU_DOTENV`` (which point at the config and .env files themselves). The DB
URL can still come from the environment via the ``$VAR`` form on ``task_db.url``.

Precedence:  config.yaml  >  built-in defaults.

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


#: The built-in provider — the CFGPU API itself. Models that declare no
#: ``provider:`` belong to it, which is every model that predates this field.
DEFAULT_PROVIDER = "cfgpu"


@dataclass
class ProviderSettings:
    """One upstream API this server can talk to.

    Everything before this existed spoke to exactly one upstream (``cfgpu``),
    whose settings live at the top level of ``Settings`` and are mirrored into a
    synthesized provider entry by :func:`load_settings`. Extra providers are
    declared under ``providers:`` in config.yaml and exist so a model can be
    served by something else — the first is ``comfy``, a co-located gateway
    wrapping local ComfyUI weights.

    ``token_env`` is the *only* place a non-cfgpu provider's credential may come
    from. It is deliberately not the request ContextVar and deliberately not
    ``CFGPU_API_TOKEN``: see ``use_request_token`` on CFGPUClient.
    """

    name: str
    base_url: str
    auth_scheme: str = "bearer"        # "bearer" → `Bearer <t>`; "raw" → bare `<t>`
    token_env: str = "CFGPU_API_TOKEN"
    http_timeout: float | None = None  # None → inherit the top-level cfgpu_api value
    connect_timeout: float | None = None


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
    #: Keyed by provider name; always contains DEFAULT_PROVIDER.
    providers: dict[str, ProviderSettings] = field(default_factory=dict)


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

        s.providers = _parse_providers(data.get("providers") or {})

        enabled = data.get("enabled_models")
        if isinstance(enabled, str):
            enabled = [enabled]  # tolerate a scalar (forgot YAML list syntax)
        elif enabled is not None and not isinstance(enabled, list):
            raise ValueError(
                f"enabled_models must be a string or list, got {type(enabled).__name__}"
            )
        s.enabled_models = enabled or None  # [] / null → load all

    # The cfgpu provider is synthesized from the top-level cfgpu_api settings
    # rather than declared under `providers:`. Those fields predate providers and
    # remain the documented way to configure the CFGPU API, so having them mirror
    # into a provider entry keeps one place to set them; declaring `providers:
    # cfgpu:` explicitly is allowed and wins.
    s.providers.setdefault(
        DEFAULT_PROVIDER,
        ProviderSettings(
            name=DEFAULT_PROVIDER,
            base_url=s.base_url,
            auth_scheme="bearer",
            token_env="CFGPU_API_TOKEN",
            http_timeout=s.http_timeout,
            connect_timeout=s.connect_timeout,
        ),
    )

    # config.yaml is the single source for these fields — no env overrides, so
    # there is exactly one place to set them. The secret CFGPU_API_TOKEN stays in
    # the environment (never in config), and the DB URL can still be pulled from
    # the environment via the `$VAR` form on task_db.url (see _expand_env).
    return s


def _parse_providers(raw: object) -> dict[str, ProviderSettings]:
    """Parse the ``providers:`` mapping. Unknown keys are rejected, not ignored.

    A typo in ``token_env`` silently degrades into "provider has no credential",
    which surfaces as a 401 from the upstream — a long way from its cause. Since
    this block is hand-written and short, failing at load time is cheap.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"providers must be a mapping, got {type(raw).__name__}")

    out: dict[str, ProviderSettings] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"providers.{name} must be a mapping, got {type(cfg).__name__}")
        unknown = set(cfg) - {"base_url", "auth_scheme", "token_env", "http_timeout", "connect_timeout"}
        if unknown:
            raise ValueError(f"providers.{name} has unknown keys: {sorted(unknown)}")
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError(f"providers.{name}.base_url is required")
        auth_scheme = str(cfg.get("auth_scheme", "bearer")).lower()
        if auth_scheme not in ("bearer", "raw"):
            raise ValueError(
                f"providers.{name}.auth_scheme must be 'bearer' or 'raw', got {auth_scheme!r}"
            )
        token_env = cfg.get("token_env") or "CFGPU_API_TOKEN"
        if name != DEFAULT_PROVIDER and token_env == "CFGPU_API_TOKEN":
            # Pointing a third-party provider at the CFGPU token would ship the
            # user-facing CFGPU credential to a host that is not CFGPU. Nothing
            # downstream can detect that, so refuse it here.
            raise ValueError(
                f"providers.{name}.token_env must not be CFGPU_API_TOKEN — a non-cfgpu "
                f"provider must have its own credential, never the CFGPU one"
            )
        out[name] = ProviderSettings(
            name=name,
            base_url=str(base_url),
            auth_scheme=auth_scheme,
            token_env=str(token_env),
            http_timeout=(
                parse_positive_float(str(cfg["http_timeout"]), 120.0)
                if "http_timeout" in cfg else None
            ),
            connect_timeout=(
                parse_positive_float(str(cfg["connect_timeout"]), 10.0)
                if "connect_timeout" in cfg else None
            ),
        )
    return out
