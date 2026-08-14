from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.client.cfgpu_client import CFGPUClient
from cfgpu_mcp.client.repository import TaskRepository, create_task_repository
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.settings import DEFAULT_PROVIDER, Settings, load_settings

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter

_MODELS_DIR = Path(__file__).parent / "models"

# Module-level singletons (lazy-initialized)
_settings: Settings | None = None
_registry: AdapterRegistry | None = None
_clients: dict[str, CFGPUClient] = {}   # provider name → client (one pool each)
_repo: TaskRepository | None = None

# Serializes the async repo singleton init: without it, a burst of concurrent
# tool calls all see _repo is None and each open a repository / run schema DDL,
# racing on Postgres' CREATE TABLE (pg_type unique index → "duplicate key").
_repo_lock = asyncio.Lock()


def get_settings() -> Settings:
    """Return module-level singleton Settings (config.yaml + env overrides)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def load_registry(enabled_models: list[str] | None = None) -> AdapterRegistry:
    """Create and load an AdapterRegistry.

    Priority: code argument > config.yaml (enabled_models) > all models.
    """
    # Importing adapters package triggers @register_python_adapter decorators
    import cfgpu_mcp.adapters  # noqa: F401

    if enabled_models is None:
        enabled_models = get_settings().enabled_models

    registry = AdapterRegistry(
        model_dir=_MODELS_DIR,
        enabled_models=enabled_models,
        # Models pointing at an unconfigured provider are dropped rather than
        # offered — see AdapterRegistry._has_provider.
        available_providers=set(get_settings().providers),
    )
    registry.load()
    return registry


def get_registry(enabled_models: list[str] | None = None) -> AdapterRegistry:
    """Return module-level singleton registry (created on first call)."""
    global _registry
    if _registry is None:
        _registry = load_registry(enabled_models)
    return _registry


def get_client(provider: str = DEFAULT_PROVIDER) -> CFGPUClient:
    """Return the singleton HTTP client for ``provider`` (one pool per upstream).

    For the default ``cfgpu`` provider: shared connection pool, no baked-in token
    — the token is resolved per request from the ContextVar (see
    cfgpu_mcp.context). base_url/timeouts come from settings (config.yaml); the
    token stays out of config entirely.

    Other providers are separate hosts. They never read the request ContextVar
    and never fall back to CFGPU_API_TOKEN; their credential comes only from
    their own ``token_env`` (see CFGPUClient._resolve_token).
    """
    client = _clients.get(provider)
    if client is not None:
        return client

    s = get_settings()
    cfg = s.providers.get(provider)
    if cfg is None:
        # Reachable only if a model declares a provider the registry did not
        # filter out (e.g. a hand-built registry in a test). Say what to add.
        raise CFGPUError(
            error_type="invalid_params",
            user_message=(
                f"未配置 provider {provider!r}：请在 config.yaml 的 providers: 下声明它"
                f"（base_url / auth_scheme / token_env）。"
            ),
            original={"provider": provider, "configured": sorted(s.providers)},
        )

    is_cfgpu = provider == DEFAULT_PROVIDER
    client = CFGPUClient(
        base_url=cfg.base_url,
        http_timeout=cfg.http_timeout if cfg.http_timeout is not None else s.http_timeout,
        connect_timeout=(
            cfg.connect_timeout if cfg.connect_timeout is not None else s.connect_timeout
        ),
        auth_scheme=cfg.auth_scheme,
        token_env=cfg.token_env,
        use_request_token=is_cfgpu,
        provider=provider,
    )
    _clients[provider] = client
    return client


def client_for(adapter: "ModelAdapter") -> CFGPUClient:
    """Resolve the client that serves ``adapter``'s upstream.

    This is the resolver handed to ``TaskManager``. It has to be a *function of
    the adapter* rather than a client chosen up front because ``task_status`` /
    ``task_wait`` receive only a task_id: which provider owns that task is not
    known until the DB row has been read and its adapter looked up, which happens
    inside TaskManager.
    """
    return get_client(getattr(adapter, "provider", DEFAULT_PROVIDER))


async def get_task_repository() -> TaskRepository:
    """Return module-level singleton TaskRepository (opened on first call).

    Backend is chosen by the ``task_db.url`` scheme from config.yaml.
    """
    global _repo
    if _repo is None:
        async with _repo_lock:
            if _repo is None:  # double-check: another coroutine may have won the lock
                s = get_settings()
                _repo = await create_task_repository(
                    s.task_db_url,
                    pool_min=s.task_db_pool_min,
                    pool_max=s.task_db_pool_max,
                )
    return _repo


async def close() -> None:
    """Close shared resources (call on shutdown)."""
    global _repo
    for client in list(_clients.values()):
        await client.close()
    _clients.clear()
    if _repo:
        await _repo.close()
        _repo = None
