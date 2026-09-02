from cfgpu_mcp.errors import CFGPUError


def test_auth_error_from_401():
    err = CFGPUError.from_http_response(401, {})
    assert err.error_type == "auth"
    assert err.retryable is False


def test_rate_limit_from_429():
    err = CFGPUError.from_http_response(429, {})
    assert err.error_type == "rate_limit"
    assert err.retryable is True


def test_invalid_params_from_400():
    err = CFGPUError.from_http_response(400, {"message": "bad field"})
    assert err.error_type == "invalid_params"
    assert err.retryable is False


def test_content_blocked_from_body_code():
    err = CFGPUError.from_http_response(400, {"error": {"code": "content_blocked", "message": "blocked"}})
    assert err.error_type == "content_blocked"
    assert err.retryable is False


def test_quota_exceeded_from_body_code():
    err = CFGPUError.from_http_response(400, {"error": {"code": "quota_exceeded"}})
    assert err.error_type == "quota_exceeded"
    assert err.retryable is False


def test_unknown_for_unrecognized_status():
    err = CFGPUError.from_http_response(503, {})
    assert err.error_type == "unknown"
    assert err.retryable is True


def test_dead_endpoint_phrase_is_non_retryable():
    # Upstream returns HTTP 200 with an error body for a retired endpoint.
    body = {"error": {"message": "The model or endpoint foo-250415 does not exist or you do not have access to it."}}
    err = CFGPUError.from_http_response(200, body)
    assert err.error_type == "model_unavailable"
    assert err.retryable is False


def test_model_not_found_code_is_non_retryable():
    err = CFGPUError.from_http_response(404, {"error": {"code": "model_not_found", "message": "gone"}})
    assert err.error_type == "model_unavailable"
    assert err.retryable is False


def test_user_message_is_nonempty():
    for status in (400, 401, 403, 429, 500, 503):
        err = CFGPUError.from_http_response(status, {})
        assert err.user_message


def test_original_preserved():
    body = {"error": {"code": "quota_exceeded", "message": "no credit"}}
    err = CFGPUError.from_http_response(400, body)
    assert err.original == body


def test_a_live_task_can_no_longer_be_expressed_as_an_error():
    """★ The ``CFGPUError.timeout(task_id, elapsed)`` constructor is gone on purpose.

    It existed for "the wait ran out but the task is alive", which is not a failure of
    anything: the task was created, it is running, and the caller's next move is the
    same ``task_status(task_id)`` it would make after ``wait=False``. Reporting it as
    ``error: True`` is what forced every caller to reconstruct "is this over?" from
    ``error_type`` x ``task_id`` — see ``TaskManager.wait`` / ``WaitOutcome``, which
    returns the task instead. Pinned as an absence so nothing reaches for it again.
    """
    assert not hasattr(CFGPUError, "timeout")


def test_outcome_unknown_is_absent_unless_explicitly_set():
    """It is a claim about billing, so it must never appear by default."""
    d = CFGPUError(error_type="task_failed", user_message="x").to_tool_result_dict()
    assert "outcome_unknown" not in d


# ── card.md hint in to_tool_result_dict ──────────────────────────────────────

def test_card_hint_for_invalid_params():
    err = CFGPUError(error_type="invalid_params", user_message="参数错误", model_id="wan-2-0")
    d = err.to_tool_result_dict()
    assert "get_model_card" in d["message"]
    assert "wan-2-0" in d["message"]
    assert d["model_id"] == "wan-2-0"


def test_card_hint_for_model_unavailable():
    err = CFGPUError(error_type="model_unavailable", user_message="模型不可用", model_id="gpt-image-2")
    d = err.to_tool_result_dict()
    assert "get_model_card" in d["message"]


def test_card_hint_for_content_blocked():
    err = CFGPUError(error_type="content_blocked", user_message="内容被拦截", model_id="doubao-seedream-5-0-lite")
    d = err.to_tool_result_dict()
    assert "get_model_card" in d["message"]


def test_no_card_hint_for_auth():
    err = CFGPUError(error_type="auth", user_message="Token 无效", model_id="wan-2-0")
    d = err.to_tool_result_dict()
    assert "get_model_card" not in d["message"]


def test_no_card_hint_for_timeout():
    err = CFGPUError(error_type="timeout", user_message="超时", model_id="wan-2-0")
    d = err.to_tool_result_dict()
    assert "get_model_card" not in d["message"]


def test_no_card_hint_for_rate_limit():
    err = CFGPUError(error_type="rate_limit", user_message="限流", model_id="wan-2-0")
    d = err.to_tool_result_dict()
    assert "get_model_card" not in d["message"]


def test_no_card_hint_without_model_id():
    err = CFGPUError(error_type="invalid_params", user_message="参数错误")
    d = err.to_tool_result_dict()
    assert "get_model_card" not in d["message"]
    assert "model_id" not in d


# ── request_id correlation echo ──────────────────────────────────────────────

def test_request_id_echoed_in_tool_result():
    err = CFGPUError(error_type="task_failed", user_message="失败", request_id="r-1")
    assert err.to_tool_result_dict()["request_id"] == "r-1"


def test_request_id_absent_when_not_set():
    err = CFGPUError(error_type="task_failed", user_message="失败")
    assert "request_id" not in err.to_tool_result_dict()


# ── card_hint suppression ────────────────────────────────────────────────────
# Some invalid_params failures name opaque upstream-only fields the model card
# cannot explain. The flag lets a raiser with a concrete remedy turn the generic
# hint off.

def test_card_hint_suppressed_when_explicitly_disabled():
    err = CFGPUError(
        error_type="invalid_params",
        user_message="参数错误",
        model_id="MiniMax/speech-2.8-hd",
        card_hint=False,
    )
    d = err.to_tool_result_dict()
    assert "get_model_card" not in d["message"]
    # The model is still reported — only the hint sentence is dropped.
    assert d["model_id"] == "MiniMax/speech-2.8-hd"


def test_card_hint_defaults_to_error_type_behaviour():
    """Omitting the flag preserves the existing per-error_type default exactly."""
    err = CFGPUError(error_type="invalid_params", user_message="参数错误", model_id="wan-2-0")
    assert "get_model_card" in err.to_tool_result_dict()["message"]


def test_card_hint_true_does_not_force_hint_on_unhinted_type():
    """The flag can only suppress; it never adds a hint to a type that has none."""
    err = CFGPUError(error_type="auth", user_message="Token 无效", model_id="wan-2-0", card_hint=True)
    assert "get_model_card" not in err.to_tool_result_dict()["message"]
