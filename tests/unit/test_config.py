import asyncio
from pathlib import Path

import pytest
import yaml

import cfgpu_mcp.config as cfg_module
from cfgpu_mcp.config import get_task_repository, load_registry
from cfgpu_mcp.settings import DEFAULT_PROVIDER

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _expected_model_count() -> int:
    """Models a config.yaml with no ``providers:`` block can actually reach.

    Derived from the model dirs so the count never goes stale when models grow.
    "All models" means all *reachable* ones: a model declaring a provider this
    deployment hasn't configured is dropped at load time rather than offered and
    then failing at POST (see AdapterRegistry._has_provider), and the fixture
    below writes a config with no providers.
    """
    n = 0
    for p in sorted(MODELS_DIR.iterdir()):
        f = p / "adapter.yaml"
        if not f.exists():
            continue
        cfg = yaml.safe_load(f.read_text()) or {}
        provider = cfg.get("provider")
        if provider is None and cfg.get("extends"):
            # A variant inherits its parent's provider through the merge.
            parent = yaml.safe_load((MODELS_DIR / cfg["extends"] / "adapter.yaml").read_text()) or {}
            provider = parent.get("provider")
        if (provider or DEFAULT_PROVIDER) == DEFAULT_PROVIDER:
            n += 1
    return n


@pytest.fixture
def load(monkeypatch, tmp_path):
    """Load a registry in isolation from the developer's ambient config.yaml.

    enabled_models is the single config.yaml field (no env override). The temp
    config is pinned via CFGPU_CONFIG so ./config.yaml in the repo never leaks in,
    and the cached Settings singleton is dropped so the temp file is actually read.
    """
    monkeypatch.setattr(cfg_module, "_MODELS_DIR", MODELS_DIR)

    def _load(enabled_models=None, yaml_enabled=None):
        body = "" if yaml_enabled is None else "enabled_models:\n" + "".join(
            f"  - {m}\n" for m in yaml_enabled
        )
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body)
        monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
        cfg_module._settings = None
        return len(load_registry(enabled_models=enabled_models))

    yield _load
    cfg_module._settings = None  # don't leak our temp settings into later tests


def test_yaml_list_filters_models(load):
    assert load(yaml_enabled=["wan-2-0", "wan-2-0-fast"]) == 2


def test_code_arg_overrides_yaml(load):
    assert load(enabled_models=["doubao-seedream-5-0-lite"], yaml_enabled=["wan-2-0"]) == 1


def test_no_enabled_models_loads_all(load):
    assert load() == _expected_model_count()


def test_empty_yaml_list_loads_all(load):
    assert load(yaml_enabled=[]) == _expected_model_count()


@pytest.mark.asyncio
async def test_concurrent_repo_init_creates_one(monkeypatch):
    """A burst of concurrent get_task_repository() must open exactly one repo.

    Without the lock, every coroutine sees _repo is None and races to open a
    repository / run schema DDL — the Postgres "duplicate key (tasks)" crash.
    """
    calls = 0

    async def fake_create_task_repository(url, pool_min, pool_max):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # yield so racers interleave before _repo is set
        return object()

    monkeypatch.setattr(cfg_module, "_repo", None)
    monkeypatch.setattr(cfg_module, "create_task_repository", fake_create_task_repository)

    repos = await asyncio.gather(*(get_task_repository() for _ in range(10)))

    assert calls == 1
    assert len({id(r) for r in repos}) == 1  # all callers get the same instance
    cfg_module._repo = None  # don't leak the dummy into other tests
