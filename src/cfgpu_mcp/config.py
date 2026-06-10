from __future__ import annotations

import os
from pathlib import Path

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.client.cfgpu_client import CFGPUClient
from cfgpu_mcp.client.repository import TaskRepository, create_task_repository
from cfgpu_mcp.settings import Settings, load_settings

_MODELS_DIR = Path(__file__).parent / "models"

# Module-level singletons (lazy-initialized)
_settings: Settings | None = None
_registry: AdapterRegistry | None = None
_client: CFGPUClient | None = None
_repo: TaskRepository | None = None


def get_settings() -> Settings:
    """Return module-level singleton Settings (config.yaml + env overrides)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def load_registry(enabled_models: list[str] | None = None) -> AdapterRegistry:
    """Create and load an AdapterRegistry.

    Priority: code argument > CFGPU_ENABLED_MODELS env var > config.yaml > all models.
    """
    # Importing adapters package triggers @register_python_adapter decorators
    import cfgpu_mcp.adapters  # noqa: F401

    if enabled_models is None:
        raw = os.getenv("CFGPU_ENABLED_MODELS", "").strip()
        if raw:
            enabled_models = [m.strip() for m in raw.split(",") if m.strip()]
        else:
            enabled_models = get_settings().enabled_models

    registry = AdapterRegistry(model_dir=_MODELS_DIR, enabled_models=enabled_models)
    registry.load()
    return registry


def get_registry(enabled_models: list[str] | None = None) -> AdapterRegistry:
    """Return module-level singleton registry (created on first call)."""
    global _registry
    if _registry is None:
        _registry = load_registry(enabled_models)
    return _registry


def get_client() -> CFGPUClient:
    """Return module-level singleton HTTP client."""
    global _client
    if _client is None:
        _client = CFGPUClient()
    return _client


async def get_task_repository() -> TaskRepository:
    """Return module-level singleton TaskRepository (opened on first call).

    Backend is chosen by the ``task_db.url`` scheme from config.yaml.
    """
    global _repo
    if _repo is None:
        _repo = await create_task_repository(get_settings().task_db_url)
    return _repo


async def close() -> None:
    """Close shared resources (call on shutdown)."""
    global _client, _repo
    if _client:
        await _client.close()
        _client = None
    if _repo:
        await _repo.close()
        _repo = None
