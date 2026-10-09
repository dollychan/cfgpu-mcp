"""Image scenario tasks are claimed exactly when ``supports()`` accepts their shape.

``text_to_image`` / ``image_to_image`` / ``multi_image_fusion`` name nothing but how many
reference images a request carries, so ``supports()`` already decides them. Checked both
ways, like the video scenario tasks in ``test_video_inputs.py``: a model that does the
job without saying so is invisible to an agent filtering by task, and no request ever
fails to reveal it.
"""

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.tool_registry import GenerateImageInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _image_adapters():
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    return sorted(registry.list_all(task_type="image"), key=lambda a: a.adapter_id)


_ADAPTERS = _image_adapters()

# task → the reference-image count that is that task's request shape
_SCENARIO_TASKS = {"text_to_image": 0, "image_to_image": 1, "multi_image_fusion": 2}


def _accepts(adapter, count: int) -> bool:
    refs = [f"https://example.com/ref/{i}.png" for i in range(count)] or None
    return adapter.supports(GenerateImageInput(prompt="a cat", reference_images=refs))[0]


@pytest.mark.parametrize("task", sorted(_SCENARIO_TASKS))
@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_scenario_tasks_are_claimed_exactly_when_supports_accepts_them(adapter, task):
    reachable = _accepts(adapter, _SCENARIO_TASKS[task])
    claims = task in adapter.tasks
    assert reachable == claims, (
        f"{adapter.adapter_id}: supports() {'accepts' if reachable else 'refuses'} "
        f"{_SCENARIO_TASKS[task]} reference image(s) but {task} is "
        f"{'claimed' if claims else 'not claimed'}"
    )


# ── inputs: declared limits ⟺ supports(), for image and understanding models ──
#
# The reference ceilings used to be constants inside seedream.py / wan_image.py and the
# region cap a separate max_regions_per_image key: per-model facts the agent could not
# see. They are adapter.yaml inputs now, and these probes keep code and declaration one.


def _all_inputs_adapters():
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    return sorted(
        registry.list_all(task_type="image") + registry.list_all(task_type="understand"),
        key=lambda a: a.adapter_id,
    )


_WITH_INPUTS = _all_inputs_adapters()


@pytest.mark.parametrize("adapter", _WITH_INPUTS, ids=lambda a: a.adapter_id)
def test_every_image_and_understanding_model_declares_its_inputs(adapter):
    assert adapter.inputs is not None, adapter.adapter_id


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_reference_image_ceiling_is_exactly_the_declared_max(adapter):
    limit = adapter.inputs["reference_images"].max
    if limit is None:
        assert _accepts(adapter, 16)
        return
    assert _accepts(adapter, limit)
    ok, reason = adapter.supports(GenerateImageInput(
        prompt="a cat", reference_images=[f"https://example.com/{i}.png" for i in range(limit + 1)],
    ))
    assert not ok and f"at most {limit} reference_images" in reason


@pytest.mark.parametrize("adapter", _WITH_INPUTS, ids=lambda a: a.adapter_id)
def test_a_regions_slot_and_the_region_task_imply_each_other(adapter):
    """The task says the model reads boxes; the slot says how many. One without the
    other is either a gate that refuses what the profile promises, or a limit on
    material the model never takes."""
    task = "region_edit" if adapter.task_type == "image" else "region_understanding"
    assert ("regions" in adapter.inputs) == (task in adapter.tasks), adapter.adapter_id


def test_undeclared_material_is_refused_by_the_base():
    from cfgpu_mcp.adapters.inputs import check_declared_inputs

    ok, reason = check_declared_inputs(
        "m", {}, GenerateImageInput(prompt="x", reference_images=["https://example.com/a.png"]),
        refuse_undeclared=True,
    )
    assert not ok and reason == "m does not accept reference_images"


def test_the_retired_region_key_is_refused_at_load():
    with pytest.raises(ValueError, match="moved into the inputs"):
        from cfgpu_mcp.adapters.async_image import GptImage2Adapter

        GptImage2Adapter.from_config({
            "adapter_id": "x", "cfgpu_model_id": "x", "task_type": "image",
            "endpoint": "/i", "max_regions_per_image": 2,
        })
