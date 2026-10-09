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
