from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter
from cfgpu_mcp.tool_registry import NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

_PLACEHOLDER_RE = re.compile(r"\{(\w+)(?:\|default:([^}]*))?\}")


def _render(template: Any, ctx: dict) -> Any:
    """Recursively render DSL placeholders in strings, lists, and dicts."""
    if isinstance(template, str):
        def _replace(m: re.Match) -> str:
            key, default = m.group(1), m.group(2)
            val = ctx.get(key)
            if val is None:
                return "" if default is None else default
            return str(val)
        return _PLACEHOLDER_RE.sub(_replace, template)
    if isinstance(template, list):
        return [_render(item, ctx) for item in template]
    if isinstance(template, dict):
        return {k: _render(v, ctx) for k, v in template.items()}
    return template


class GenericAdapter(ModelAdapter):
    """YAML-driven adapter for models with simple payload mappings."""

    _payload_mapping: dict
    _response_url_key: str   # dotted path to URL in response, e.g. "data.0.url"

    @classmethod
    def from_config(cls, config: dict) -> "GenericAdapter":
        instance: GenericAdapter = super().from_config(config)  # type: ignore[assignment]
        instance._payload_mapping = config.get("payload_mapping", {})
        instance._response_url_key = config.get("response_url_key", "data.0.url")
        return instance

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        ctx = req.model_dump(exclude_none=True)
        ctx["cfgpu_model_id"] = self.cfgpu_model_id
        payload = _render(self._payload_mapping, ctx)
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        url = self._extract(resp, self._response_url_key)
        urls = [url] if isinstance(url, str) and url else []
        return NormalizedResult(
            urls=urls,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            task_id=resp.get("id"),
            model_used=resp.get("model"),
            seed=None,
            cost_tokens=self._extract(resp, "usage.total_tokens"),
        )

    @staticmethod
    def _extract(obj: Any, path: str) -> Any:
        """Extract a value from a nested dict/list using a dotted path."""
        for key in path.split("."):
            if obj is None:
                return None
            if isinstance(obj, list):
                try:
                    obj = obj[int(key)]
                except (ValueError, IndexError):
                    return None
            elif isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        return obj
