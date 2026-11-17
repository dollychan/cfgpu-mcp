"""Presentation-only, allowlisted upstream error translations.

Do not call this when persisting tasks. Unknown messages return None so callers
retain their existing error contract. Patterns describe observed reasons, never
generic codes such as InvalidParameter or GENERATION_PROVIDER_REQUEST_FAILED.
Describe the reported failure only. Advice is permitted only when explicitly
present in every upstream variant matched by that rule; never infer a remedy.

``MCP_RULES`` is the second table: messages this server writes itself (a missing
task, a lost submission, transport failures). Those are translated whole, advice
included, because the advice is ours and already sits in ``content.message`` — the
translation must say what the agent was told, no more and no less. Upstream wording
embedded in one of them (``请求参数错误：<raw>``) is carried verbatim, never rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Rule:
    key: str
    pattern: str
    zh: str
    en: str


RULES = (
    Rule("real_person_image", r"input image(?: '[^']+')* may contain real person", "上游因参考图片可能包含真人而拒绝请求。", "The provider rejected a reference image that may contain a real person."),
    Rule("real_person_video", r"input video(?: '[^']+')* may contain real person", "上游因参考视频可能包含真人而拒绝请求。", "The provider rejected a reference video that may contain a real person."),
    Rule("input_text_copyright", r"input text may be related to copyright restrictions", "输入文本可能涉及版权限制，被上游拒绝。", "The provider rejected input text due to possible copyright restrictions."),
    Rule("output_image_copyright", r"output image may be related to copyright restrictions", "生成图片可能涉及版权限制，被上游拦截。", "The generated image was blocked due to possible copyright restrictions."),
    Rule("output_video_copyright", r"output video may be related to copyright restrictions", "生成视频可能涉及版权限制，被上游拦截。", "The generated video was blocked due to possible copyright restrictions."),
    Rule("output_audio_copyright", r"output audio may be related to copyright restrictions", "生成音频可能涉及版权限制，被上游拦截。", "The generated audio was blocked due to possible copyright restrictions."),
    Rule("input_ip", r"Input data is suspected of being involved in IP infringement", "输入素材疑似涉及知识产权侵权，被上游拒绝。", "The provider rejected input suspected of intellectual property infringement."),
    Rule("input_text_safety", r"input text(?: '[^']+')* may contain sensitive information|Your prompt was blocked by the content safety policy|提示词不符合平台内容规范", "提示词未通过上游内容审核。", "The prompt failed the provider's content safety review."),
    Rule("input_safety", r"Your prompt or reference image was blocked by the content safety policy", "提示词或参考图片未通过内容审核。", "The prompt or a reference image failed content safety review."),
    Rule("output_image_safety", r"The generated image was filtered by the safety policy", "生成图片被上游安全策略拦截。", "The generated image was blocked by the provider's safety policy."),
    Rule("output_video_safety", r"output video may contain sensitive information", "生成视频未通过上游内容审核。", "The generated video failed the provider's content safety review."),
    Rule("output_audio_safety", r"output audio may contain sensitive information", "生成音频未通过上游内容审核。", "The generated audio failed the provider's content safety review."),
    Rule("safety", r"Your request was rejected by the safety system|内容审核未通过: 违规", "请求未通过上游内容审核。", "The request failed the provider's content safety review."),
    Rule("image_min_pixels", r"image size must be at least (?P<limit>\d+) pixels", "输出图片总像素不足，至少需要 {limit} 像素。", "The output image is too small; at least {limit} pixels are required."),
    Rule("image_max_pixels", r"image size must be at most (?P<limit>\d+) pixels", "输出图片总像素超限，最多允许 {limit} 像素。", "The output image is too large; at most {limit} pixels are allowed."),
    Rule("reference_image_pixels", r"expected the pixel to be at least (?P<limit>\d+)px, but received a (?P<size>\d+x\d+)px image", "参考图片尺寸为 {size}，总像素不足 {limit}。", "The reference image is {size}, below the minimum of {limit} pixels."),
    Rule("media_min_pixels", r"the parameter (?P<media>video|image) pixel count specified in the request must be greater than or equal to (?P<limit>\d+)", "参考素材（{media}）总像素不足，至少需要 {limit} 像素。", "The reference {media} has too few pixels; at least {limit} pixels are required."),
    Rule("media_max_duration", r"the parameter (?P<media>video total|video|audio) duration \(seconds\) specified in the request must be less than or equal to (?P<limit>[\d.]+)", "参考素材时长超限：{media} 时长不得超过 {limit} 秒。", "Reference duration exceeds the limit: {media} duration must not exceed {limit} seconds."),
    Rule("media_min_duration", r"the parameter (?P<media>audio|video) duration \(seconds\) specified in the request must be greater than or equal to (?P<limit>[\d.]+)", "参考素材（{media}）过短，至少需要 {limit} 秒。", "The reference {media} is too short; at least {limit} seconds are required."),
    Rule("video_max_duration", r"Video duration can not longer than (?P<limit>[\d.]+)s", "视频时长不得超过 {limit} 秒。", "Video duration must not exceed {limit} seconds."),
    Rule("audio_only", r"reference_audio cannot be the only reference input", "不能仅使用参考音频。", "Reference audio cannot be the only reference input."),
    Rule("return_last_frame", r"The parameter `return_last_frame` specified in the request is not valid\.", "上游判定 return_last_frame 参数无效。", "The provider reported that the return_last_frame parameter is invalid."),
    Rule("png_required", r"transparent background requires a PNG input", "透明背景处理需要有效的 PNG 输入。", "Transparent background processing requires valid PNG input."),
    Rule("layer_decomposition", r"the image content could not be processed for layer decomposition", "上游无法对该图片进行图层分解。", "The provider could not decompose this image into layers."),
    Rule("invalid_image", r"Invalid image file or mode for image (?P<index>\d+)", "第 {index} 张图片的文件或颜色模式不受支持。", "Image {index} has an invalid file format or color mode."),
    Rule("invalid_video_url", r"Invalid video_url\.", "视频 URL 无效。", "The video URL is invalid."),
    Rule("download_timeout", r"Timeout while downloading url:|failed to download \d+ image\(s\):.*context deadline exceeded", "上游下载参考素材超时。", "The provider timed out downloading reference media."),
    Rule("media_unreachable", r"media url unreachable\.", "上游无法访问素材 URL。", "The provider cannot access a media URL."),
    Rule("reference_images", r"最多支持 16 张参考图，且必须为 HTTP\(S\) 图片 URL", "参考图片最多 16 张，且必须使用 HTTP(S) 图片 URL。", "At most 16 reference images are supported, using HTTP(S) image URLs."),
    Rule("prompt_required", r"Field validation for 'Prompt' failed on the 'required' tag", "提示词缺失。", "The prompt is missing."),
    Rule("seconds_type", r"cannot unmarshal number into Go struct field VideoCreateRequest.seconds of type string", "上游要求 seconds 为字符串，但收到数字。", "The provider requires seconds as a string but received a number."),
    Rule("aspect_ratio_required", r"Aspect ratio must be specified when no first image and not video editing", "未提供首帧且非视频编辑时，必须指定宽高比。", "An aspect ratio is required without a first frame when not editing video."),
    Rule("media_required", r"Field required: input\.media", "上游缺少必填 input.media。", "The provider is missing required input.media."),
    Rule("edit_constraints", r"Seedance identified your task as video editing.*4 to 30 seconds.*`ratio` must be `adaptive`.*`duration` must be -1", "上游将任务识别为视频编辑，但参数不符合要求：输入视频须为 4–30 秒，aspect_ratio 须为 adaptive，duration_seconds 须为 -1；输出比例和时长跟随输入。", "The provider classified the task as video editing, but the parameters do not meet its requirements: the input video must be 4–30 seconds, aspect_ratio must be adaptive, and duration_seconds must be -1; output aspect ratio and duration follow the input."),
    Rule("model_unavailable", r"The model or endpoint [\w.-]+ does not exist or you do not have access to it|模型未上架", "所选模型不存在、未上架或当前账户无权访问。", "The selected model does not exist, is not published, or is inaccessible to this account."),
    Rule("network_timeout", r"Upstream network error: (?:request|network operation) timed out", "上游网络请求超时，未取得生成结果。", "The upstream network request timed out without a generation result."),
    Rule("generation_timeout", r"Upstream model timed out\. Try again later\.|任务处理超时，请稍后重试。|^任务超时$|\[节点\] gemini timeout\.\.\.", "上游生成任务超时，未返回结果。", "The upstream generation timed out without a result."),
    Rule("busy", r"Upstream resources are temporarily busy\. Please try again later\.", "上游资源暂时繁忙。请稍后重试。", "Upstream resources are temporarily busy. Try again later."),
    Rule("internal_error", r"The service encountered an unexpected internal error\.|Internal server error, Please contact the administrator|系统异常，请截图联系客服。", "上游服务发生内部错误。", "The provider encountered an internal error."),
    Rule("lease_exhausted", r"租约超时，已达最大重试次数", "上游任务租约超时且已耗尽重试次数。", "The upstream task lease expired and exhausted its retries."),
    Rule("generation_failed", r"Generation failed\. Please try again\.|任务处理失败，请稍后重试；如持续失败，请联系客服并提供任务ID。|^调用生成服务失败$", "上游生成失败，未提供具体原因。", "Upstream generation failed without a specific cause."),
    Rule("no_result", r"^生成服务未返回可用结果$", "上游未返回可用生成结果，具体原因未知。", "The provider returned no usable generation result and no specific cause."),
)

_COMPILED = tuple((rule, re.compile(rule.pattern)) for rule in RULES)


def match_error(message: str) -> tuple[Rule, dict[str, str]] | None:
    for rule, pattern in _COMPILED:
        match = pattern.search(message)
        if match:
            return rule, match.groupdict()
    return None


def _reasons(body: dict):
    """Read only error-bearing fields, never prompts, URLs or request payloads."""
    for key in ("error", "data", "task", "output", "response"):
        value = body.get(key)
        if isinstance(value, dict):
            yield from _reasons(value)
        elif key == "error" and isinstance(value, str):
            yield value
    for key in ("error_message", "error_msg", "message", "msg", "fail_reason", "failure_reason", "reason"):
        value = body.get(key)
        if isinstance(value, str):
            yield value


def normalize_error(message: str, original: dict | None = None) -> dict[str, str] | None:
    """Return client-only translations for a known reason, otherwise None."""
    for candidate in (*_reasons(original or {}), message):
        matched = match_error(candidate)
        if matched:
            rule, values = matched
            return {"zh": rule.zh.format(**values), "en": rule.en.format(**values)}
    return None


# ── MCP-side messages ────────────────────────────────────────────────────────
#
# Matched against this server's own wording, anchored at the start, so stored rows
# written before this table existed translate too. Each pattern mirrors one raise
# site; ``test_error_normalization.py`` builds them from the real code paths, so a
# reworded message fails a test instead of silently losing its translation.
# ``{detail}`` / ``{raw}`` carry text this server did not author (an exception
# string, the upstream's own message) and are inserted unchanged.

MCP_RULES = (
    Rule("task_not_found", r"^Task (?P<task_id>'[^']*'|\"[^\"]*\") not found\.$", "未找到 request_id 为 {task_id} 的任务。", "No task was found for request_id {task_id}."),
    Rule("request_id_missing", r"^缺少 request_id：", "缺少 request_id：请传入 generate_* 调用时使用的 request_id（未提供 request_id 的调用则传它返回的 task_id）。", "request_id is missing: pass the request_id used in the generate_* call (for a call that supplied none, pass the task_id it returned)."),
    Rule("request_id_row_unreadable", r"^request_id (?P<task_id>'[^']*'|\"[^\"]*\") 已被占用，但对应的任务行读不回来。", "request_id {task_id} 已被占用，但对应的任务行读不回来。请换一个 request_id 重新提交。", "request_id {task_id} is already in use, but its task record could not be read back. Submit again with a different request_id."),
    Rule("no_artifact", r"^Task reported success but returned no artifact URLs or inline media$", "上游报告任务成功，但没有返回任何产物 URL 或内联媒体。", "The provider reported success but returned no artifact URL or inline media."),
    Rule("sync_no_artifact", r"^同步媒体 API 返回成功，但没有返回任何产物 URL 或内联媒体。", "同步媒体 API 返回成功，但没有返回任何产物 URL 或内联媒体。", "The synchronous media API reported success but returned no artifact URL or inline media."),
    Rule("task_id_unparsed", r"^没能从上游响应中解析出 task_id，", "没能从上游响应中解析出 task_id，这次请求的结果无法取回，task_status 也查不到，请直接重新发起。", "No task_id could be parsed from the provider response, so this request's result cannot be retrieved and task_status cannot find it either. Submit the request again."),
    Rule("failed_without_message", r"^Task failed without error message$", "任务失败，没有错误信息。", "The task failed without an error message."),
    Rule("failed_without_detail", r"^Task failed \(upstream reported no error detail\)$", "任务失败，上游没有提供错误详情。", "The task failed; the provider reported no error detail."),
    Rule("failed_unparsed_reason", r"^Task failed\. 上游响应（截断）：", "任务失败，没能从上游响应中解析出失败原因。", "The task failed, and no failure reason could be parsed from the provider response."),
    Rule("auth_invalid", r"^API Token 无效或已过期，请检查 CFGPU_API_TOKEN。$", "API Token 无效或已过期，请检查 CFGPU_API_TOKEN。", "The API token is invalid or expired. Check CFGPU_API_TOKEN."),
    Rule("auth_missing", r"^缺少 CFGPU API Token：", "缺少 CFGPU API Token：请在请求头携带 Authorization，或设置 CFGPU_API_TOKEN。", "The CFGPU API token is missing: send it in the Authorization header, or set CFGPU_API_TOKEN."),
    Rule("auth_provider_missing", r"^provider (?P<provider>'[^']*') 缺少凭据：请设置环境变量 (?P<env>[^。]+)。", "provider {provider} 缺少凭据：请设置环境变量 {env}。（该 provider 不使用请求头里的用户 token，也不回退到 CFGPU_API_TOKEN。）", "Provider {provider} has no credentials: set the environment variable {env}. (This provider does not use the user token from the request header and does not fall back to CFGPU_API_TOKEN.)"),
    Rule("rate_limit", r"^请求过于频繁，请稍后重试。$", "请求过于频繁，请稍后重试。", "Too many requests. Try again later."),
    Rule("quota_exceeded", r"^账户配额不足，请检查余额。$", "账户配额不足，请检查余额。", "The account quota is insufficient. Check the balance."),
    Rule("content_blocked", r"^内容被审核拦截，请修改 prompt 后重试。$", "内容被审核拦截，请修改 prompt 后重试。", "The content was blocked by moderation. Revise the prompt and try again."),
    Rule("model_unavailable_mcp", r"^所选模型暂不可用，请尝试其他模型。$", "所选模型暂不可用，请尝试其他模型。", "The selected model is temporarily unavailable. Try another model."),
    Rule("connect_timeout", r"^(?P<elapsed>[\d.]+)s 内没能与 provider (?P<provider>'[^']*') 建立连接（上游还没收到任何请求），通常是服务端到上游的网络不通。可以稍后重试；如果持续失败，请告诉用户服务端网络需要检查。$", "{elapsed}s 内没能与 provider {provider} 建立连接（上游还没收到任何请求），通常是服务端到上游的网络不通。可以稍后重试；如果持续失败，请告诉用户服务端网络需要检查。", "Could not connect to provider {provider} within {elapsed}s (the provider has received nothing yet); usually the server cannot reach the provider over the network. You can retry later; if it keeps failing, tell the user the server's network needs checking."),
    Rule("request_timeout_sent", r"^请求超时（已耗时 (?P<elapsed>[\d.]+)s，上限 (?P<budget>[\d.]+)s，provider (?P<provider>'[^']*')）。请求已经发出但没等到回应。$", "请求超时（已耗时 {elapsed}s，上限 {budget}s，provider {provider}）。请求已经发出但没等到回应。", "The request timed out ({elapsed}s elapsed, limit {budget}s, provider {provider}). It was sent, but no response arrived."),
    Rule("request_timeout", r"^请求超时（已耗时 (?P<elapsed>[\d.]+)s，上限 (?P<budget>[\d.]+)s，provider (?P<provider>'[^']*')）。请稍后重试。$", "请求超时（已耗时 {elapsed}s，上限 {budget}s，provider {provider}）。请稍后重试。", "The request timed out ({elapsed}s elapsed, limit {budget}s, provider {provider}). Try again later."),
    Rule("network_failed", r"^网络请求失败：(?P<detail>.+)$", "网络请求失败：{detail}", "Network request failed: {detail}"),
    Rule("poll_processing_failed", r"^轮询响应处理失败：(?P<detail>.+)$", "轮询响应处理失败：{detail}", "Failed to process the poll response: {detail}"),
    Rule("invalid_params_raw", r"^请求参数错误：(?P<raw>.+)$", "请求参数错误：{raw}", "Invalid request parameters: {raw}"),
    Rule("task_failed_raw", r"^任务执行失败：(?P<raw>.+)$", "任务执行失败：{raw}", "Task execution failed: {raw}"),
    Rule("submission_lost_raw", r"^提交已被上游接受但结果无法取回：(?P<raw>.+)$", "提交已被上游接受但结果无法取回：{raw}", "The provider accepted the submission, but its result cannot be retrieved: {raw}"),
    Rule("unknown_http", r"^未知错误（HTTP (?P<status>\d+)）：(?P<raw>.+)$", "未知错误（HTTP {status}）：{raw}", "Unknown error (HTTP {status}): {raw}"),
)

#: The remedies ``TaskManager._record_submit_failure`` appends to a lost submission's
#: reason (``task_manager._submission_lost_remedy``). ``pattern`` is a literal suffix
#: here, not a regex: the reason in front is translated by the tables above and the two
#: halves are joined.
SUBMISSION_LOST_REMEDIES = (
    Rule("submission_lost_remedy", "这次请求的结果没能取回，task_status 也查不到，请直接重新发起。", "这次请求的结果没能取回，task_status 也查不到，请直接重新发起。", "This request's result could not be retrieved, and task_status cannot find it either. Submit the request again."),
    Rule("submission_lost_remedy_understand", "这次请求的回答没能取回，task_status 也查不到，请直接重新调用 understand_vision。如果是耗时过长导致的，可以降低 analysis_depth、缩短视频或收窄问题后再调用。", "这次请求的回答没能取回，task_status 也查不到，请直接重新调用 understand_vision。如果是耗时过长导致的，可以降低 analysis_depth、缩短视频或收窄问题后再调用。", "This request's answer could not be retrieved, and task_status cannot find it either. Call understand_vision again. If it took too long, lower analysis_depth, shorten the video, or narrow the question before calling again."),
)

_MCP_COMPILED = tuple((rule, re.compile(rule.pattern, re.S)) for rule in MCP_RULES)


def match_mcp_message(message: str) -> tuple[Rule, dict[str, str]] | None:
    for rule, pattern in _MCP_COMPILED:
        match = pattern.search(message)
        if match:
            return rule, match.groupdict()
    return None


def _translate(message: str, original: dict | None) -> dict[str, str] | None:
    # Upstream reasons first: an upstream message wrapped in one of our frames
    # (``任务执行失败：<raw>``) is better described by its own rule than echoed raw.
    translated = normalize_error(message, original)
    if translated is not None:
        return translated
    matched = match_mcp_message(message)
    if matched is None:
        return None
    rule, values = matched
    return {"zh": rule.zh.format(**values), "en": rule.en.format(**values)}


def localize_error(
    message: str, original: dict | None = None, error_type: str | None = None
) -> dict[str, str] | None:
    """``{zh, en}`` for an error message this server returns, or None if unknown.

    Covers upstream reasons (``RULES``) and this server's own wording (``MCP_RULES``).
    A ``submission_lost`` message is ``<reason><remedy>``: both halves must be known,
    since a translation that kept the remedy and dropped an untranslatable reason would
    tell a client user less than the agent was told.
    """
    if error_type == "submission_lost":
        for remedy in SUBMISSION_LOST_REMEDIES:
            if message.endswith(remedy.pattern):
                reason = message[: -len(remedy.pattern)]
                if not reason:
                    return {"zh": remedy.zh, "en": remedy.en}
                head = _translate(reason, original)
                if head is None:
                    return None
                return {"zh": head["zh"] + remedy.zh, "en": f"{head['en']} {remedy.en}"}
    return _translate(message, original)
