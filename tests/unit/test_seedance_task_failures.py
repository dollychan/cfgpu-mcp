"""Seedance 2.5's asynchronous task-type failures are reclassified as caller-fixable.

``InvalidParameter.TaskTypeConstraint`` / ``TaskTypeMismatch`` arrive after the task
was created, so they reach the caller as a failed row. As ``task_failed`` they read as
"generation failed" and invite a retry of the identical call, which can never succeed —
the cause is the request itself. These pin that every path reporting such a row says
``invalid_params`` with a parameter-level remedy, upstream wording first, and that the
stored row keeps the upstream wording untouched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.task_manager import Task, _extract_error_message, task_failed_error
from tests.unit.test_seedance_video_adapter import _make_2_5_adapter
from tests.unit.test_task_service import _db_with_task, _patch_config

_CONSTRAINT = (
    "InvalidParameter.TaskTypeConstraint: The parameter ratio specified in the request "
    "is not valid for task type edit"
)
_MISMATCH = (
    "InvalidParameter.TaskTypeMismatch: The specified omni_reference_task_type does not "
    "match the task type inferred from the request"
)


def _failed(error: str, adapter_id: str = "doubao-seedance-2-5") -> Task:
    return Task({
        "id": "task-1", "adapter_id": adapter_id, "status": "failed",
        "payload": {}, "result": None, "error": error,
        "created_at": 0.0, "updated_at": 0.0,
    })


def test_poll_extraction_keeps_the_symbolic_code():
    # Ark's failed-task body: without the code the reason is generic prose, and the
    # code is the only stable token translate_task_failure can key on.
    resp = {
        "id": "cgt-1",
        "status": "failed",
        "error": {
            "code": "InvalidParameter.TaskTypeConstraint",
            "message": "The parameter ratio specified in the request is not valid for task type edit",
        },
    }
    assert _extract_error_message(resp) == _CONSTRAINT


def test_extraction_does_not_repeat_a_code_the_message_already_carries():
    resp = {"error": {"code": "X.Y", "message": "X.Y: already here"}}
    assert _extract_error_message(resp) == "X.Y: already here"


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (_CONSTRAINT, 'aspect_ratio 设为 "adaptive"'),
        (_MISMATCH, "omni_reference_task_type"),
    ],
    ids=["constraint", "mismatch"],
)
def test_task_type_failures_become_invalid_params_with_a_remedy(error, needle):
    err = task_failed_error(_failed(error), _make_2_5_adapter(), model_id="doubao-seedance-2-5")

    assert err.error_type == "invalid_params"
    assert err.retryable is False
    assert err.user_message.startswith(error)  # upstream wording first, verbatim
    assert needle in err.user_message
    # The card documents the sub-task constraint table, so the hint stays.
    assert "请根据校验原因调整通用参数" in err.to_tool_result_dict()["message"]


# Verbatim from production (2026-09-28): the relay passed Ark's prose without its code.
_RELAYED_CONSTRAINT = (
    "The parameter duration specified in the request is not valid. Seedance identified "
    "your task as video editing based on your prompt. For this task type, the output "
    "ratio and duration follow the input video selected by the model for editing, and "
    "the video selected must satisfy the duration requirement of 4 to 30 seconds. "
    "Issues: [0] duration must be -1. "
)


def test_a_constraint_relayed_without_its_code_is_still_recognised():
    err = task_failed_error(_failed(_RELAYED_CONSTRAINT), _make_2_5_adapter())

    assert err.error_type == "invalid_params"
    assert err.user_message.startswith(_RELAYED_CONSTRAINT)
    assert "duration_seconds 设为 -1" in err.user_message


def test_poll_extraction_passes_a_code_less_error_through():
    resp = {"status": "failed", "error": {"message": _RELAYED_CONSTRAINT}}
    assert _extract_error_message(resp) == _RELAYED_CONSTRAINT


def test_other_failures_stay_task_failed():
    err = task_failed_error(
        _failed("OutputVideoSensitiveContentDetected: blocked"), _make_2_5_adapter()
    )
    assert err.error_type == "task_failed"
    assert err.user_message == "OutputVideoSensitiveContentDetected: blocked"


def test_a_model_without_a_translation_keeps_task_failed():
    adapter = MagicMock()
    adapter.translate_task_failure.return_value = None
    err = task_failed_error(_failed(_CONSTRAINT, adapter_id="other"), adapter)
    assert err.error_type == "task_failed"


@pytest.mark.asyncio
async def test_task_status_reports_the_translation_and_leaves_the_row_alone():
    db = await _db_with_task(
        "failed", "doubao-seedance-2-5", error=_MISMATCH, request_id="r-9"
    )
    try:
        p_db, p_client, p_reg = _patch_config(db, MagicMock(get=AsyncMock()), _make_2_5_adapter())
        with p_db, p_client, p_reg:
            with pytest.raises(Exception) as exc:
                await task_service.get_status("task-1")

        assert exc.value.error_type == "invalid_params"
        assert exc.value.request_id == "r-9"
        assert exc.value.model_id == "doubao-seedance-2-5"
        async with db.execute("SELECT error FROM tasks WHERE id = 'task-1'") as cur:
            (stored,) = await cur.fetchone()
        assert stored == _MISMATCH
    finally:
        await db.close()
