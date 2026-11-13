import copy
import json
from unittest.mock import MagicMock

import pytest

from cfgpu_mcp.error_normalization import normalize_error
from cfgpu_mcp.errors import CFGPUError, tool_error_dict, tool_error_result
from cfgpu_mcp.task_manager import Task, task_failed_error


@pytest.mark.parametrize("raw,expected", [
    ("GPT 异步任务失败: status=failed, message=Your prompt was blocked by the content safety policy.", "The prompt failed"),
    ("The request failed because the input image 'content[3]' 'content[4]' may contain real person. Request id: abc", "real person"),
    ("OutputVideoSensitiveContentDetected.PolicyViolation: The request failed because the output video may be related to copyright restrictions.", "copyright"),
    ("The parameter `content[3]` specified in the request is not valid: the parameter audio duration (seconds) specified in the request must be greater than or equal to 1.8 for model x", "1.8 seconds"),
    ("the parameter video total duration (seconds) specified in the request must be less than or equal to 30.2", "30.2 seconds"),
    ("image size must be at least 3686400 pixels", "3686400 pixels"),
    ("image size must be at most 16777216 pixels", "16777216 pixels"),
    ("error: expected the pixel to be at least 262144px, but received a 87x84px image instead.", "87x84"),
    ("InvalidParameter: Timeout while downloading url: https://example.test/private?Signature=secret", "timed out downloading"),
    ("content[6].image_url: media url unreachable.", "cannot access"),
    ("Video duration can not longer than 10.0s", "10.0 seconds"),
    ("任务处理超时，请稍后重试。", "generation timed out"),
])
def test_known_reasons_are_bilingual(raw, expected):
    result = normalize_error(raw)
    assert result and set(result) == {"zh", "en"}
    assert expected in result["en"]
    assert all("Signature=" not in text and "Request id:" not in text for text in result.values())


@pytest.mark.parametrize("raw", ["unknown upstream failure", "InvalidParameter: new reason", "", "success", "200", "OutputVideoSensitiveContentDetected.NewReason: new reason"])
def test_unknown_reason_is_not_normalized(raw):
    assert normalize_error(raw) is None
    assert tool_error_dict(CFGPUError("task_failed", raw))["message"] == raw
    assert tool_error_result(CFGPUError("task_failed", raw)) == tool_error_dict(CFGPUError("task_failed", raw))


def test_nested_reason_wins_without_mutating_error():
    body = {"error": {"code": "InvalidParameter", "message": "image size must be at least 3686400 pixels"}}
    snapshot = copy.deepcopy(body)
    err = CFGPUError("task_failed", "生成服务未返回可用结果", original=body, request_id="r-1")
    wire = tool_error_result(err)
    result = json.loads(wire.content[0].text)
    assert result["message"] == err.user_message
    assert "3686400" in wire.structuredContent["message_i18n"]["en"]
    assert result["request_id"] == "r-1"
    assert result["error_type"] == "task_failed"
    assert result["retryable"] is False
    assert err.user_message == "生成服务未返回可用结果"
    assert err.original == snapshot


def test_request_payload_is_not_treated_as_error():
    assert normalize_error("unknown", {"payload": {"prompt": "任务超时"}}) is None


@pytest.mark.parametrize("raw,expected", [
    ("任务超时", "上游生成任务超时，未返回结果。\nThe upstream generation timed out without a result."),
    ("The service encountered an unexpected internal error.", "上游服务发生内部错误。\nThe provider encountered an internal error."),
    ("调用生成服务失败", "上游生成失败，未提供具体原因。\nUpstream generation failed without a specific cause."),
    ("The request failed because the input text 'content[0]' may contain sensitive information.", "提示词未通过上游内容审核。\nThe prompt failed the provider's content safety review."),
    ("image size must be at least 3686400 pixels", "输出图片总像素不足，至少需要 3686400 像素。\nThe output image is too small; at least 3686400 pixels are required."),
])
def test_reason_only_variants_do_not_inherit_advice(raw, expected):
    zh, en = expected.split("\n")
    assert normalize_error(raw) == {"zh": zh, "en": en}


def test_explicit_upstream_advice_can_be_preserved():
    raw = "Upstream resources are temporarily busy. Please try again later."
    assert normalize_error(raw) == {"zh": "上游资源暂时繁忙。请稍后重试。", "en": "Upstream resources are temporarily busy. Try again later."}


def test_http_submission_error_is_normalized_only_when_presented():
    body = {"error": {"message": "The parameter `return_last_frame` specified in the request is not valid."}}
    err = CFGPUError.from_http_response(400, body)
    raw = err.user_message
    err.model_id = "test-model"
    wire = tool_error_result(err)
    result = json.loads(wire.content[0].text)
    assert result == err.to_tool_result_dict()
    assert result["message"].startswith(raw)
    assert "return_last_frame parameter is invalid" in wire.structuredContent["message_i18n"]["en"]
    assert "message_i18n" not in result
    assert err.user_message == raw
    assert err.original == body


@pytest.mark.asyncio
@pytest.mark.parametrize("name,service,args", [
    ("generate_image", "cfgpu_mcp.service.image.generate_image", {"prompt": "test"}),
    ("generate_video", "cfgpu_mcp.service.video.generate_video", {"prompt": "test"}),
    ("generate_audio", "cfgpu_mcp.service.audio.generate_audio", {"text": "test"}),
    ("task_status", "cfgpu_mcp.service.task.get_status", {"request_id": "r-1"}),
    ("task_wait", "cfgpu_mcp.service.task.wait_for_task", {"request_id": "r-1"}),
])
async def test_mcp_errors_separate_agent_content_from_client_translations(monkeypatch, name, service, args):
    from unittest.mock import AsyncMock
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult
    from cfgpu_mcp.tools import generate, tasks

    raw = "Invalid video_url. Request id: provider-123"
    err = CFGPUError("task_failed", raw, request_id="r-1")
    monkeypatch.setattr(service, AsyncMock(side_effect=err))
    mcp = FastMCP("error-i18n-test")
    generate.register(mcp)
    tasks.register(mcp)
    wire = await mcp.call_tool(name, args)
    assert isinstance(wire, CallToolResult)
    assert wire.isError is False  # failure is carried by content.error
    content = json.loads(wire.content[0].text)
    assert content == {"error": True, "error_type": "task_failed", "message": raw, "retryable": False, "request_id": "r-1"}
    assert wire.structuredContent == {"message_i18n": {"zh": "视频 URL 无效。", "en": "The video URL is invalid."}}
    # MessageStreamMiddleware merges these for client events without replacing
    # ToolMessage.content; locale selection remains the consumer's responsibility.
    event_content = {**content, **wire.structuredContent}
    assert event_content["message_i18n"]["en"] == "The video URL is invalid."
    assert content["message"] == raw


def test_failed_task_read_and_replay_preserve_raw_row():
    raw = "GPT 任务失败: Upstream network error: request timed out"
    task = Task({"id": "t-1", "adapter_id": "test", "status": "failed", "payload": {}, "result": None, "error": raw, "created_at": 0, "updated_at": 0})
    adapter = MagicMock()
    adapter.translate_task_failure.return_value = None
    for _ in range(2):
        err = task_failed_error(task, adapter, request_id="r-1")
        assert tool_error_dict(err)["message"] == raw
        assert "network request timed out" in tool_error_result(err).structuredContent["message_i18n"]["en"]
        assert err.user_message == raw
        assert task.error == raw


@pytest.mark.asyncio
async def test_task_status_normalizes_without_updating_database():
    from unittest.mock import AsyncMock
    from cfgpu_mcp.service import task as task_service
    from tests.unit.test_seedance_video_adapter import _make_2_5_adapter
    from tests.unit.test_task_service import _db_with_task, _patch_config

    raw = "The request failed because the output video may be related to copyright restrictions. Request id: upstream-123"
    db = await _db_with_task("failed", "doubao-seedance-2-5", error=raw, request_id="r-9")
    try:
        p_db, p_client, p_reg = _patch_config(db, MagicMock(get=AsyncMock()), _make_2_5_adapter())
        with p_db, p_client, p_reg:
            with pytest.raises(CFGPUError) as exc:
                await task_service.get_status("task-1")
        wire = tool_error_result(exc.value)
        result = json.loads(wire.content[0].text)
        assert result["message"] == raw
        assert "Request id:" not in wire.structuredContent["message_i18n"]["en"]
        assert result["request_id"] == "r-9"
        async with db.execute("SELECT error FROM tasks WHERE id = 'task-1'") as cur:
            assert (await cur.fetchone())[0] == raw
    finally:
        await db.close()


def test_nonterminal_poll_diagnostic_keeps_original_message():
    from cfgpu_mcp.task_manager import _last_error_dict

    raw = "Upstream resources are temporarily busy. Please try again later."
    err = CFGPUError("unknown", raw, retryable=True)
    result = _last_error_dict(err, consecutive_failures=2, elapsed=7)
    assert result["message"] == raw
    assert result["retryable"] is True
    assert result["consecutive_failures"] == 2
    assert result["elapsed"] == 7
    assert err.user_message == raw
