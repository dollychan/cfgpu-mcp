import asyncio
from pathlib import Path

import pytest
import yaml

import cfgpu_mcp.config as cfg_module
from cfgpu_mcp.config import get_task_repository, load_registry
from cfgpu_mcp.settings import DEFAULT_PROVIDER, load_settings

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
def write_config(monkeypatch, tmp_path):
    """Pin CFGPU_CONFIG at a temp config.yaml and drop the Settings singleton.

    Keeps the developer's ambient ./config.yaml from leaking into these tests
    (disabled_models / disabled_tools have no env override — config.yaml is their
    single source, so the file that gets read has to be ours).
    """
    monkeypatch.setattr(cfg_module, "_MODELS_DIR", MODELS_DIR)

    def _write(body: str = ""):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body)
        monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
        cfg_module._settings = None
        return cfg

    yield _write
    cfg_module._settings = None  # don't leak our temp settings into later tests


@pytest.fixture
def load(write_config):
    def _load(enabled_models=None, yaml_disabled=None):
        body = "" if yaml_disabled is None else "disabled_models:\n" + "".join(
            f"  - {m}\n" for m in yaml_disabled
        )
        write_config(body)
        return len(load_registry(enabled_models=enabled_models))

    return _load


def test_yaml_list_drops_models(load):
    assert load(yaml_disabled=["wan-2-0", "wan-2-0-fast"]) == _expected_model_count() - 2


def test_yaml_blocklist_applies_on_top_of_code_allowlist(load):
    """The two compose — the blocklist is not bypassed by an explicit allowlist."""
    assert load(enabled_models=["wan-2-0", "wan-2-0-fast"], yaml_disabled=["wan-2-0"]) == 1


def test_no_disabled_models_loads_all(load):
    assert load() == _expected_model_count()


def test_empty_yaml_list_loads_all(load):
    assert load(yaml_disabled=[]) == _expected_model_count()


def test_leftover_enabled_models_is_rejected(write_config):
    """A stale whitelist means the *opposite* of what the key now says — fail loudly."""
    write_config("enabled_models:\n  - wan-2-0\n")
    with pytest.raises(ValueError, match="disabled_models"):
        load_settings()


def test_empty_leftover_enabled_models_is_tolerated(write_config):
    """An empty leftover excluded nothing, so upgrading must not break the server."""
    write_config("enabled_models:\n")
    assert load_settings().disabled_models is None


def test_disabled_models_scalar_is_tolerated(write_config):
    write_config("disabled_models: wan-2-0\n")
    assert load_settings().disabled_models == ["wan-2-0"]


def test_disabled_tools_parsed(write_config):
    write_config("disabled_tools:\n  - generate_audio\n  - understand_vision\n")
    assert load_settings().disabled_tools == ["generate_audio", "understand_vision"]


def test_disabled_tools_wrong_type_rejected(write_config):
    write_config("disabled_tools:\n  generate_audio: true\n")
    with pytest.raises(ValueError, match="disabled_tools"):
        load_settings()


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
