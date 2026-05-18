import os
import pytest
from pathlib import Path
from cfgpu_mcp.config import load_registry

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _load(enabled_models=None, env_value=None) -> int:
    original = os.environ.get("CFGPU_ENABLED_MODELS")
    try:
        if env_value is not None:
            os.environ["CFGPU_ENABLED_MODELS"] = env_value
        elif "CFGPU_ENABLED_MODELS" in os.environ:
            del os.environ["CFGPU_ENABLED_MODELS"]

        # Patch models dir to use our test models
        import cfgpu_mcp.config as cfg_module
        original_dir = cfg_module._MODELS_DIR
        cfg_module._MODELS_DIR = MODELS_DIR
        try:
            registry = load_registry(enabled_models=enabled_models)
            return len(registry)
        finally:
            cfg_module._MODELS_DIR = original_dir
    finally:
        if original is not None:
            os.environ["CFGPU_ENABLED_MODELS"] = original
        elif "CFGPU_ENABLED_MODELS" in os.environ and env_value is not None:
            del os.environ["CFGPU_ENABLED_MODELS"]


def test_env_var_parsed_by_comma():
    count = _load(env_value="wan-2-0,wan-2-0-fast")
    assert count == 2


def test_code_arg_overrides_env_var():
    count = _load(enabled_models=["doubao-seedream-5-0-lite"], env_value="wan-2-0")
    assert count == 1


def test_no_config_loads_all_models():
    count = _load()
    assert count == 10


def test_empty_env_var_loads_all_models():
    count = _load(env_value="")
    assert count == 10
