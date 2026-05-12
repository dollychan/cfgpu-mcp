from __future__ import annotations

from pathlib import Path

import yaml

from cfgpu_mcp.adapters.base import (
    ModelAdapter,
    get_python_adapters,
)
from cfgpu_mcp.adapters.generic import GenericAdapter


class AdapterRegistry:
    def __init__(
        self,
        model_dir: Path,
        enabled_models: list[str] | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.enabled_models: set[str] | None = (
            set(m.strip() for m in enabled_models if m.strip())
            if enabled_models is not None
            else None
        )
        self._by_adapter_id:     dict[str, ModelAdapter] = {}
        self._by_cfgpu_model_id: dict[str, ModelAdapter] = {}
        self._by_display_name:   dict[str, ModelAdapter] = {}

    # ── Loading ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        raw_configs: dict[str, dict] = {}
        for path in self.model_dir.iterdir():
            yaml_file = path / "adapter.yaml"
            if path.is_dir() and yaml_file.exists():
                raw_configs[path.name] = yaml.safe_load(yaml_file.read_text())

        for _dir_name, config in raw_configs.items():
            try:
                merged = self._merge_extends(config, raw_configs)
            except KeyError as e:
                raise ValueError(
                    f"adapter {config.get('adapter_id')!r} has extends={config.get('extends')!r} "
                    f"but that model is not found: {e}"
                ) from e

            adapter = self._instantiate(merged)
            if self._is_enabled(adapter):
                self._register(adapter)

    def _merge_extends(self, config: dict, all_configs: dict) -> dict:
        parent_id = config.get("extends")
        if not parent_id:
            return dict(config)
        parent_raw = all_configs[parent_id]  # KeyError → bubbles up with context
        parent = self._merge_extends(parent_raw, all_configs)  # single level only; recursion safe here
        merged: dict = dict(parent)
        for key, val in config.items():
            if key == "extends":
                continue
            if isinstance(val, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **val}   # field-level merge (e.g. poll_config)
            else:
                merged[key] = val
        # Preserve extends so _instantiate can resolve the parent's Python Adapter class
        merged["extends"] = parent_id
        return merged

    def _instantiate(self, config: dict) -> ModelAdapter:
        python_adapters = get_python_adapters()
        adapter_id = config.get("adapter_id", "")
        extends = config.get("extends")

        # Method B: prefer Python Adapter for this adapter_id
        cls = python_adapters.get(adapter_id)
        # Follow extends chain to find parent's Python Adapter
        if cls is None and extends:
            cls = python_adapters.get(extends)
        # Fall back to GenericAdapter
        if cls is None:
            cls = GenericAdapter

        return cls.from_config(config)

    def _is_enabled(self, adapter: ModelAdapter) -> bool:
        if self.enabled_models is None:
            return True
        return bool(
            self.enabled_models & {adapter.adapter_id, adapter.cfgpu_model_id}
        )

    def _register(self, adapter: ModelAdapter) -> None:
        self._by_adapter_id[adapter.adapter_id] = adapter
        self._by_cfgpu_model_id[adapter.cfgpu_model_id] = adapter
        self._by_display_name[adapter.display_name] = adapter

    # ── Lookup ───────────────────────────────────────────────────────────────

    def get(self, key: str) -> ModelAdapter:
        adapter = (
            self._by_adapter_id.get(key)
            or self._by_cfgpu_model_id.get(key)
            or self._by_display_name.get(key)
        )
        if adapter is None:
            available = list(self._by_adapter_id.keys())
            raise KeyError(f"Model {key!r} not found. Available: {available}")
        return adapter

    def list_all(self, task_type: str | None = None) -> list[ModelAdapter]:
        adapters = list(self._by_adapter_id.values())
        if task_type:
            adapters = [a for a in adapters if a.task_type == task_type]
        return adapters

    def __len__(self) -> int:
        return len(self._by_adapter_id)
