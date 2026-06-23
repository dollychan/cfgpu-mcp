from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, register_python_adapter
from cfgpu_mcp.tool_registry import NormalizedResult, UnderstandVisionInput

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import (
        GenerateAudioInput,
        GenerateImageInput,
        GenerateVideoInput,
    )

_DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


@register_python_adapter
class QwenVisionAdapter(ModelAdapter):
    """Python Adapter for Qwen3-VL vision-language models (image/video understanding).

    Speaks the OpenAI-compatible ``/model/v1/chat/completions`` API: the unified
    request is mapped to a single user turn whose ``content`` is an array of
    ``text`` / ``image_url`` / ``video_url`` parts. Synchronous (is_async: false) —
    the answer is returned directly in the POST response under
    ``choices[0].message.content``. Thinking variants additionally surface their
    chain-of-thought under ``message.reasoning_content``, which we carry through on
    the assistant ``message`` object alongside ``content``.

    Registered under ``qwen3-vl-30b-a3b-thinking``; sibling Qwen3-VL models reuse
    this class via the registry extends-chain with their own ``cfgpu_model_id``.
    """

    adapter_id = "qwen3-vl-30b-a3b-thinking"

    def build_payload(
        self,
        req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput",
    ) -> dict:
        assert isinstance(req, UnderstandVisionInput)

        content: list[dict] = [{"type": "text", "text": req.prompt}]
        for url in req.images or []:
            content.append({"type": "image_url", "image_url": {"url": url}})
        if req.video:
            content.append({"type": "video_url", "video_url": {"url": req.video}})

        messages = [
            {"role": "system", "content": req.system_prompt or _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "messages": messages,
            "stream": False,
        }
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        choices = resp.get("choices") or []
        raw = choices[0].get("message", {}) if choices else {}
        message: dict = {
            "role": raw.get("role", "assistant"),
            "content": raw.get("content") or "",
        }
        # Thinking models surface chain-of-thought; only carry it when present.
        reasoning = raw.get("reasoning_content")
        if reasoning:
            message["reasoning_content"] = reasoning
        return NormalizedResult(
            urls=[],                       # understanding returns text, not media
            expires_at=None,               # text answers don't expire
            task_id=None,                  # synchronous model has no task_id
            model_used=resp.get("model"),
            seed=None,
            usage=resp.get("usage"),
            response_id=resp.get("id"),
            message=message,
        )
