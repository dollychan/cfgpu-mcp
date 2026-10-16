"""Official prompt template library: shared, read-only, served from an in-memory index.

See ``docs/template-library-and-dialect.md`` §6. The library is public to every caller
and carries no user identity (D-IR5); a user's private templates are that user's custom
skills on the host side, never records here.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from cfgpu_mcp.template_library.bundle import BUNDLE_PATH, BundleError, load_bundle
from cfgpu_mcp.template_library.index import TemplateIndex

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_index: Optional[TemplateIndex] = None
_load_error: Optional[str] = None


def get_index(path: Path = BUNDLE_PATH) -> TemplateIndex:
    """The process-wide index, loaded on first use.

    A bundle that fails validation raises every time rather than being cached as empty:
    "the library is broken" must never read as "nothing matched".
    """
    global _index, _load_error
    if _index is not None:
        return _index
    with _lock:
        if _index is None:
            try:
                _index = TemplateIndex(load_bundle(path))
                _load_error = None
            except (OSError, ValueError, BundleError) as exc:
                _load_error = str(exc)
                logger.error("template library failed to load from %s: %s", path, exc)
                raise
    return _index


def reset_index() -> None:
    """Drop the cached index (tests; a future hot-reload swaps it the same way)."""
    global _index, _load_error
    with _lock:
        _index = None
        _load_error = None


__all__ = ["TemplateIndex", "get_index", "reset_index"]
