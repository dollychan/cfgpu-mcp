# Import Python Adapters to trigger @register_python_adapter before Registry.load()
from cfgpu_mcp.adapters import wan_video, seedream  # noqa: F401

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
