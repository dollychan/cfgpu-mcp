from __future__ import annotations

from typing import Literal


ErrorType = Literal[
    "rate_limit",
    "quota_exceeded",
    "content_blocked",
    "invalid_params",
    "model_unavailable",
    "task_failed",
    "auth",
    "timeout",
    "unknown",
]

_RETRYABLE: set[ErrorType] = {"rate_limit", "model_unavailable", "unknown"}

_CARD_HINT_TYPES: set[ErrorType] = {"invalid_params", "model_unavailable", "content_blocked"}

_HTTP_STATUS_MAP: dict[int, ErrorType] = {
    401: "auth",
    403: "auth",
    429: "rate_limit",
    400: "invalid_params",
    422: "invalid_params",
}

_BODY_CODE_MAP: dict[str, ErrorType] = {
    "quota_exceeded": "quota_exceeded",
    "content_blocked": "content_blocked",
    "content_policy_violation": "content_blocked",
    "model_not_found": "model_unavailable",
    "model_unavailable": "model_unavailable",
    "task_failed": "task_failed",
    "invalid_request": "invalid_params",
}

# Body codes / message phrases that mean the endpoint is permanently gone or the
# token has no access to it. Upstream sometimes signals this with HTTP 200 + an
# error body (OpenAI-compatible shape), so it can't be inferred from the status.
# These must NOT be retryable — retrying a dead endpoint never succeeds.
_DEAD_ENDPOINT_CODES: set[str] = {"model_not_found"}
_DEAD_ENDPOINT_PHRASES: tuple[str, ...] = (
    "does not exist",
    "do not have access",
    "does not have access",
    "no access to",
)


def _is_dead_endpoint(code: str, raw_msg: str) -> bool:
    if code in _DEAD_ENDPOINT_CODES:
        return True
    msg = raw_msg.lower()
    return any(phrase in msg for phrase in _DEAD_ENDPOINT_PHRASES)


class CFGPUError(Exception):
    def __init__(
        self,
        error_type: ErrorType,
        user_message: str,
        original: dict | None = None,
        retryable: bool | None = None,
        model_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.error_type: ErrorType = error_type
        self.user_message = user_message
        self.original: dict = original or {}
        self.retryable: bool = retryable if retryable is not None else error_type in _RETRYABLE
        # Agent-facing model identifier (the global-unique cfgpu_model_id, exposed
        # as ``model_id``). Never the internal adapter_id — see get_model_card hint.
        self.model_id: str | None = model_id
        # Caller-supplied correlation handle (see tool_registry.stamp_request_id),
        # echoed on the error result so a failure can be joined back to the request.
        self.request_id: str | None = request_id

    def __repr__(self) -> str:
        return f"CFGPUError(type={self.error_type!r}, retryable={self.retryable}, msg={self.user_message!r})"

    @staticmethod
    def from_http_response(status: int, body: dict) -> "CFGPUError":
        # 优先从响应体中的 code/type 字段识别错误类型
        code = (
            body.get("error", {}).get("code")
            or body.get("error", {}).get("type")
            or body.get("code")
            or ""
        )
        error_type: ErrorType = (
            _BODY_CODE_MAP.get(str(code))
            or _HTTP_STATUS_MAP.get(status)
            or "unknown"
        )
        raw_msg = (
            body.get("error", {}).get("message")
            or body.get("message")
            or f"HTTP {status}"
        )
        # A retired/inaccessible endpoint is permanent — classify it as
        # model_unavailable and force retryable=False so callers fail fast
        # instead of retrying a model that will never come back.
        retryable: bool | None = None
        if _is_dead_endpoint(str(code), str(raw_msg)):
            error_type = "model_unavailable"
            retryable = False
        user_messages: dict[ErrorType, str] = {
            "rate_limit": "请求过于频繁，请稍后重试。",
            "quota_exceeded": "账户配额不足，请检查余额。",
            "content_blocked": "内容被审核拦截，请修改 prompt 后重试。",
            "invalid_params": f"请求参数错误：{raw_msg}",
            "model_unavailable": "所选模型暂不可用，请尝试其他模型。",
            "task_failed": f"任务执行失败：{raw_msg}",
            "auth": "API Token 无效或已过期，请检查 CFGPU_API_TOKEN。",
            "timeout": "等待超时，任务可能仍在运行，可用 task_status 查询。",
            "unknown": f"未知错误（HTTP {status}）：{raw_msg}",
        }
        return CFGPUError(
            error_type=error_type,
            user_message=user_messages[error_type],
            original=body,
            retryable=retryable,
        )

    def to_tool_result_dict(self) -> dict:
        message = self.user_message
        if self.model_id and self.error_type in _CARD_HINT_TYPES:
            message += f" 请调用 get_model_card 获取模型 {self.model_id} 的详细参数说明和使用示例。"
        result: dict = {
            "error": True,
            "error_type": self.error_type,
            "message": message,
            "retryable": self.retryable,
        }
        if self.model_id:
            result["model_id"] = self.model_id
        # Surface task_id (carried in ``original`` by task_failed / timeout errors)
        # so the caller can re-query or report the exact failed task.
        task_id = self.original.get("task_id")
        if task_id:
            result["task_id"] = task_id
        # Echo the caller's correlation handle so a failed generate/task can be joined
        # back to the originating request (works even for sync models with no task_id).
        if self.request_id:
            result["request_id"] = self.request_id
        return result

    @staticmethod
    def timeout(task_id: str, elapsed: int) -> "CFGPUError":
        return CFGPUError(
            error_type="timeout",
            user_message=f"等待超时（{elapsed}s），任务 {task_id} 可能仍在运行，可用 task_status 查询。",
            original={"task_id": task_id, "elapsed": elapsed},
            retryable=False,
        )


def tool_error_dict(e: Exception) -> dict:
    """Convert any exception to a structured dict suitable as a tool result.

    Returning an error dict (instead of raising) ensures the LLM always sees
    the failure reason in the tool result content rather than an opaque protocol error.
    """
    if isinstance(e, CFGPUError):
        return e.to_tool_result_dict()
    return {"error": True, "message": str(e)}
