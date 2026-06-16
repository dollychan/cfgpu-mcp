# Import Python Adapters to trigger @register_python_adapter before Registry.load()
from cfgpu_mcp.adapters import (  # noqa: F401
    seedance_video,
    seedream,
    async_image,
    happyhorse_video,
    kling_video,
)

from cfgpu_mcp.adapters.base import ModelAdapter, PollConfig, register_python_adapter
from cfgpu_mcp.adapters.generic import GenericAdapter
from cfgpu_mcp.adapters.registry import AdapterRegistry

__all__ = [
    "ModelAdapter",
    "PollConfig",
    "GenericAdapter",
    "AdapterRegistry",
    "register_python_adapter",
]
