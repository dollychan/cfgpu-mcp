from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, register_python_adapter
from cfgpu_mcp.adapters.regions import output_contract, render_prompt
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
    """Python Adapter for Qwen3.6-Plus vision-language models (image/video understanding).

    Speaks the OpenAI-compatible ``/model/v1/chat/completions`` API: the unified
    request is mapped to a single user turn whose ``content`` is an array of
    ``text`` / ``image_url`` / ``video_url`` parts. Synchronous (is_async: false) —
    the answer is returned directly in the POST response under
    ``choices[0].message.content``. Thinking variants additionally surface their
    chain-of-thought under ``message.reasoning_content``, which we carry through on
    the assistant ``message`` object alongside ``content``.

    Registered under ``qwen-3-6-plus``; sibling Qwen3.6 models reuse
    this class via the registry extends-chain with their own ``cfgpu_model_id``.

    Reads regions (``region_understand``) the same way the Seedream editor writes them:
    a ``<bbox>`` tag on the [0, 999] grid, embedded in the prompt. That symmetry is the
    load-bearing part — asking "what is inside this box" and then editing that box are
    one conversation, and both ends speak the same coordinates, so nothing between them
    has to translate. It is also why marking up an image never requires rendering the
    marks *onto* it: rasterising a box so a model can infer it back out of the pixels is
    a lossy round trip past a number it will happily read directly.
    """

    adapter_id = "qwen-3-6-plus"

    def build_payload(
        self,
        req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput",
    ) -> dict:
        assert isinstance(req, UnderstandVisionInput)

        prompt = req.prompt
        if req.regions:
            if "region_understand" not in self.capabilities:
                raise ValueError(
                    f"{self.adapter_id} does not support regions, and regions are never "
                    f"silently ignored — an answer about the whole image, presented as "
                    f"an answer about the marked one, is worse than no answer."
                )
            # Names and notes are included here (unlike the editing path): this model
            # reads rather than paints, so there is nothing to leak into, and an answer
            # phrased as "标记3 里是…" hands back the caller's own word for the region.
            prompt = render_prompt(prompt, req.regions, req.image_refs, include_names=True)
            prompt = f"{prompt}\n\n{output_contract(req.regions)}"

        content: list[dict] = [{"type": "text", "text": prompt}]
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
