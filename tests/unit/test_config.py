from pathlib import Path

import pytest

import cfgpu_mcp.config as cfg_module
from cfgpu_mcp.config import load_registry

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _expected_model_count() -> int:
    """Derive from the model dirs so the count never goes stale when models grow."""
    return sum(1 for p in MODELS_DIR.iterdir() if (p / "adapter.yaml").exists())


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
