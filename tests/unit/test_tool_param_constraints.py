"""``tool_param_constraints.json`` must not drift from the registry.

The file is the front-end's HITL parameter contract, and keeping it current has
been a hand-written instruction in ARCHITECTURE.md ("新增模型或调整参数时同步该
文件") with nothing enforcing it. Drift here is silent and one-directional: the
model roster the operator edits comes from this file, so a renamed model leaves
an entry nobody can select and a name nobody can send, while every test and every
live call keeps passing.

Scope is deliberately the machine-checkable half — the roster, the three
identifiers, and the ranges that exist as declarative adapter fields. The prose
notes are documentation and are left to review.
"""

import json
from pathlib import Path

import pytest

from cfgpu_mcp.config import load_registry

CONSTRAINTS = Path(__file__).parent.parent.parent / "tool_param_constraints.json"

#: tool name -> task_type, mirroring tool_registry._TOOL_TASK_TYPE.
_TOOLS = {
    "generate_video": "video",
    "generate_image": "image",
    "generate_audio": "audio",
    "understand_vision": "understand",
}

#: Known unresolved disagreements between the file and the adapter, kept visible
#: rather than dropped from the assertion.
#:
#: wan-video-fast: the file caps duration at 12s; neither wan-2-0-fast nor its
#: wan-2-0 parent declares max_duration_seconds, so the adapter resolves to the
#: 15s default. One of the two is wrong and the upstream docs are the tiebreak —
#: if 12 is right the adapter accepts 13-15 and burns a billed round trip on a
#: rejection it could have caught locally; if 15 is right the front-end caps
#: users 3 seconds short. Delete this entry once that is settled.
_UNRESOLVED_DURATION = {"wan-video-fast"}


@pytest.fixture(scope="module")
def constraints() -> dict:
    return json.loads(CONSTRAINTS.read_text())


@pytest.fixture(scope="module")
def registry():
    # The file documents the whole fleet, not one deployment: disabled_models is
    # a per-deployment blocklist and must not decide what the front-end knows.
    return load_registry(disabled_models=[])


@pytest.mark.parametrize("tool,task_type", _TOOLS.items())
def test_model_roster_matches_the_registry(constraints, registry, tool, task_type):
    live = {a.model_name for a in registry.list_all(task_type=task_type)}
    entries = {e["modelName"] for e in constraints[tool] if e["modelName"] != "auto"}
    assert entries == live


@pytest.mark.parametrize("tool,task_type", _TOOLS.items())
def test_auto_entry_enum_matches_the_registry(constraints, registry, tool, task_type):
    """The enum the operator picks from is what reaches the `model` tool param."""
    live = {a.model_name for a in registry.list_all(task_type=task_type)}
    auto = next(e for e in constraints[tool] if e["modelName"] == "auto")
    options = set(auto["args"]["model"]["options"])
    assert options == live | {"auto"}


@pytest.mark.parametrize("tool", _TOOLS)
def test_entry_identifiers_match_the_adapter(constraints, registry, tool):
    """modelName goes on the wire, modelId is a DB key, adapterId is internal.

    They are three different strings for several models (nano-pro / cf-pro), so a
    rename that updates only the one a reader happened to notice is the failure.
    """
    for entry in constraints[tool]:
        if entry["modelName"] == "auto":
            continue
        adapter = registry.get(entry["modelName"])
        assert (entry["adapterId"], entry["modelId"], entry["modelName"]) == (
            adapter.adapter_id,
            adapter.cfgpu_model_id,
            adapter.model_name,
        ), entry["modelName"]


def test_video_resolution_options_match_declared_resolutions(constraints, registry):
    for entry in constraints["generate_video"]:
        if entry["modelName"] == "auto":
            continue
        adapter = registry.get(entry["modelName"])
        options = (entry["args"].get("resolution") or {}).get("options")
        if options is None or adapter.resolutions is None:
            continue          # no local restriction declared on one side or the other
        assert sorted(options) == sorted(adapter.resolutions), entry["modelName"]


def test_video_duration_max_matches_declared_ceiling(constraints, registry):
    """A cap the front-end enforces but supports() does not costs a billed call."""
    for entry in constraints["generate_video"]:
        if entry["modelName"] in _UNRESOLVED_DURATION or entry["modelName"] == "auto":
            continue
        adapter = registry.get(entry["modelName"])
        declared = (entry["args"].get("duration_seconds") or {}).get("max")
        if declared is None:
            continue
        assert declared == adapter.max_duration_seconds, entry["modelName"]
