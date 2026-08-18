"""These tests submit real, billed generations. They run only when asked to.

The gate used to be "is ``CFGPU_API_TOKEN`` set?", which failed on both counts.

*It was not a decision.* ``.env`` carries a real token, and ``load_settings()`` loads
it into ``os.environ`` — so the answer was "yes" for anyone with a working checkout.
Having a credential is not the same as consenting to spend money right now.

*It was not even stable.* ``_load_dotenv()`` runs at import time, via the module-level
``_apply_disabled_tools(mcp)`` in ``server.py``. This hook fires after collection has
finished, so whether the token was visible depended on whether some *other* test module
had imported ``cfgpu_mcp.server`` during collection — ``tests/unit/test_server.py`` does.
Result: ``pytest tests/integration/...`` skipped, while a full ``pytest`` ran the same
tests for real. Same tests, same environment, opposite behaviour, decided by the
selection set.

An explicit variable is both a decision and independent of import order. Running these
needs a reachable ``DATABASE_URL`` and upstream credit; the extra export is the point.
"""
import os
import pytest

ENABLE_VAR = "CFGPU_RUN_INTEGRATION"


def pytest_collection_modifyitems(items):
    if os.getenv(ENABLE_VAR):
        return
    skip = pytest.mark.skip(
        reason=f"set {ENABLE_VAR}=1 to run integration tests (submits real, billed generations)"
    )
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)
