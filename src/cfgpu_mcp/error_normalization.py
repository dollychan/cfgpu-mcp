"""Presentation-only, allowlisted upstream error translations.

Do not call this when persisting tasks. Unknown messages return None so callers
retain their existing error contract. Patterns describe observed reasons, never
generic codes such as InvalidParameter or GENERATION_PROVIDER_REQUEST_FAILED.
Describe the reported failure only. Advice is permitted only when explicitly
present in every upstream variant matched by that rule; never infer a remedy.
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
