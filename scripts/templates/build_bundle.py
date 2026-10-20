"""Validate ``templates/`` and compile it into the shipped bundle (design §6.5).

This is the only writer of ``src/cfgpu_mcp/template_library/templates.bundle.json``.
Run it after changing any record or ``catalog.yaml``; ``tests/unit/test_template_library.py``
fails when the committed bundle no longer matches the source tree.

    python scripts/templates/build_bundle.py            # validate + write the bundle
    python scripts/templates/build_bundle.py --check    # validate only, exit 1 if stale
    python scripts/templates/build_bundle.py --source templates/_staging --check
                                                        # review drafts before promoting
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfgpu_mcp.template_library.bundle import (  # noqa: E402
    BUNDLE_PATH,
    BundleError,
    compile_source,
    dump_bundle,
    load_bundle,
)


def known_models() -> set[str]:
    from cfgpu_mcp.config import load_registry

    return {adapter.model_name for adapter in load_registry(disabled_models=[]).list_all()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "templates")
    parser.add_argument("--check", action="store_true", help="validate only; exit 1 if the bundle is stale")
    args = parser.parse_args()

    staging = args.source.resolve() != (REPO_ROOT / "templates").resolve()
    previous = None if staging or not BUNDLE_PATH.exists() else load_bundle()
    try:
        bundle = compile_source(
            args.source,
            previous=previous,
            known_models=known_models(),
            catalog_path=REPO_ROOT / "templates" / "catalog.yaml",
        )
    except BundleError as exc:
        print(exc, file=sys.stderr)
        return 1

    text = dump_bundle(bundle)
    print(f"{len(bundle.records)} records, catalog_version {bundle.catalog_version}")
    if staging or args.check:
        if not staging and (not BUNDLE_PATH.exists() or BUNDLE_PATH.read_text(encoding="utf-8") != text):
            print("bundle is stale: run scripts/templates/build_bundle.py", file=sys.stderr)
            return 1
        return 0
    BUNDLE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {BUNDLE_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
