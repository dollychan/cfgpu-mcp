from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import aiosqlite

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.client.cfgpu_client import CFGPUClient

_MODELS_DIR = Path(__file__).parent / "models"

# Module-level singletons (lazy-initialized)
_registry: AdapterRegistry | None = None
_client: CFGPUClient | None = None
_db: aiosqlite.Connection | None = None


def load_registry(enabled_models: list[str] | None = None) -> AdapterRegistry:
    """Create and load an AdapterRegistry.

    Priority: code argument > CFGPU_ENABLED_MODELS env var > all models.
    """
    # Importing adapters package triggers @register_python_adapter decorators
    import cfgpu_mcp.adapters  # noqa: F401

    if enabled_models is None:
        raw = os.getenv("CFGPU_ENABLED_MODELS", "").strip()
        enabled_models = [m.strip() for m in raw.split(",") if m.strip()] if raw else None

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


async def get_db() -> aiosqlite.Connection:
    """Return module-level singleton DB connection (opened on first call)."""
    global _db
    if _db is None:
        from cfgpu_mcp.client.db import get_db as _open_db
        _db = await _open_db()
    return _db


async def close() -> None:
    """Close shared resources (call on shutdown)."""
    global _client, _db
    if _client:
        await _client.close()
        _client = None
    if _db:
        await _db.close()
        _db = None
