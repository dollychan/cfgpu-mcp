import os
import pytest


def pytest_collection_modifyitems(items):
    """Skip all integration tests when CFGPU_API_TOKEN is not set."""
    if not os.getenv("CFGPU_API_TOKEN"):
        skip = pytest.mark.skip(reason="CFGPU_API_TOKEN not set")
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip)
