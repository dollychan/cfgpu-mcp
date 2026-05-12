import pytest
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


def test_user_message_is_nonempty():
    for status in (400, 401, 403, 429, 500, 503):
        err = CFGPUError.from_http_response(status, {})
        assert err.user_message


def test_original_preserved():
    body = {"error": {"code": "quota_exceeded", "message": "no credit"}}
    err = CFGPUError.from_http_response(400, body)
    assert err.original == body


def test_timeout_factory():
    err = CFGPUError.timeout("task-123", 300)
    assert err.error_type == "timeout"
    assert err.retryable is False
    assert "task-123" in err.user_message
