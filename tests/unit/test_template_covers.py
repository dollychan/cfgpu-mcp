"""Template covers: credits → bundle, and cover URLs in results.

Design: docs/template-library-and-dialect.md §6.1 / Q-TL4. The files themselves are served
under ``/covers/`` by cfdream-file-server (its ``template_covers`` mount); the MCP results
turn the bundle's cover paths into URLs only when a public base is set.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from cfgpu_mcp import config as cfg_module
from cfgpu_mcp import template_library
from cfgpu_mcp.service import template as template_service
from cfgpu_mcp.settings import Settings, load_settings
from cfgpu_mcp.template_library.bundle import BundleError, compile_source, dump_bundle, load_bundle

_CATALOG = {"schema_version": 1, "catalog_version": 1, "facets": {"genres": ["photography"]}}


def _record(record_id: str = "case-test-0001", **overrides) -> dict:
    base = {
        "id": record_id,
        "version": 1,
        "kind": "case",
        "media": "image",
        "title": "Studio packshot",
        "genres": ["photography"],
        "source": {"origin": "unit-test", "license": "first-party"},
        "content": "A tea box in a minimal studio, soft light.",
    }
    base.update(overrides)
    return base


def _tree(root: Path, records: list[dict], credits: dict | None, files: list[str] = ()) -> Path:
    (root / "records" / "case").mkdir(parents=True)
    (root / "catalog.yaml").write_text(yaml.safe_dump(_CATALOG), encoding="utf-8")
    for rec in records:
        path = root / "records" / "case" / f"{rec['id']}.v{rec['version']}.yaml"
        path.write_text(yaml.safe_dump(rec), encoding="utf-8")
    covers = root / "covers"
    covers.mkdir()
    for key in files:
        (covers / key).parent.mkdir(parents=True, exist_ok=True)
        (covers / key).write_bytes(b"\xff\xd8\xff fake jpeg")
    if credits is not None:
        (covers / "credits.yaml").write_text(yaml.safe_dump({"notice": "n", "covers": credits}), encoding="utf-8")
    return root


_CREDIT = {"origin": "someone/repo case 1", "author": "@someone", "url": "https://x.example/1", "usage": "reference-only"}


# ── credits.yaml → bundle ─────────────────────────────────────────────────────


def test_credits_become_the_record_cover(tmp_path):
    key = "case-test-0001/1.jpg"
    bundle = compile_source(_tree(tmp_path, [_record()], {key: _CREDIT}, [key]))
    cover = bundle.records[0].cover
    assert cover.path == f"covers/{key}"
    assert (cover.origin, cover.author, cover.source_url, cover.usage) == (
        "someone/repo case 1",
        "@someone",
        "https://x.example/1",
        "reference-only",
    )


def test_cover_is_not_part_of_the_immutable_fields(tmp_path):
    """Adding a cover to a published version must not count as changing it."""
    first = compile_source(_tree(tmp_path / "a", [_record()], None))
    key = "case-test-0001/1.jpg"
    compile_source(_tree(tmp_path / "b", [_record()], {key: _CREDIT}, [key]), previous=first)


@pytest.mark.parametrize(
    ("credits", "files", "record_extra", "expected"),
    [
        ({"case-nope-0001/1.jpg": _CREDIT}, ["case-nope-0001/1.jpg"], {}, "no record"),
        ({"case-test-0001/1.jpg": _CREDIT}, [], {}, "file is missing"),
        ({"case-test-0001/1.jpg": {**_CREDIT, "usage": "free"}}, ["case-test-0001/1.jpg"], {}, "usage"),
        ({"case-test-0001/1.jpg": {**_CREDIT, "origin": None}}, ["case-test-0001/1.jpg"], {}, "origin"),
        ({"case-test-0001/1.png": _CREDIT}, ["case-test-0001/1.png"], {}, "cover type"),
        (
            {"case-test-0001/1.jpg": _CREDIT, "case-test-0001/1.webp": _CREDIT},
            ["case-test-0001/1.jpg", "case-test-0001/1.webp"],
            {},
            "already has the cover",
        ),
        (None, [], {"cover": {"path": "covers/case-test-0001/1.jpg", "origin": "x"}}, "not in the record"),
    ],
)
def test_bad_cover_declarations_are_refused(tmp_path, credits, files, record_extra, expected):
    with pytest.raises(BundleError, match=expected):
        compile_source(_tree(tmp_path, [_record(**record_extra)], credits, files))


# ── the shipped covers ────────────────────────────────────────────────────────

#: ``templates/covers`` in this checkout — what cfdream-file-server's template_covers mount serves.
COVERS_DIR = Path(__file__).resolve().parents[2] / "templates" / "covers"


def test_shipped_covers_all_exist():
    """Every cover the shipped bundle names exists in the checkout's templates/covers."""
    named = {r.cover.path for r in load_bundle().records if r.cover}
    assert named
    assert {p for p in named if not (COVERS_DIR / p.removeprefix("covers/")).is_file()} == set()


# ── cover URLs in search / get results ────────────────────────────────────────


@pytest.fixture
def library(tmp_path, monkeypatch):
    key = "case-test-0001/1.jpg"
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(dump_bundle(compile_source(_tree(tmp_path / "src", [_record()], {key: _CREDIT}, [key]))))
    template_library.reset_index()
    real_get_index = template_library.get_index
    monkeypatch.setattr(template_service, "get_index", lambda: real_get_index(bundle_path))
    settings = Settings()
    monkeypatch.setattr(cfg_module, "_settings", settings)
    yield settings
    template_library.reset_index()


def test_results_carry_the_cover_url_and_its_credit(library):
    library.public_http.public_base_url = "https://assets.example.com/"
    card = asyncio.run(template_service.search_templates())["results"][0]
    assert card["cover"] == {
        "url": "https://assets.example.com/covers/case-test-0001/1.jpg",
        "usage": "reference-only",
        "origin": "someone/repo case 1",
        "author": "@someone",
        "source_url": "https://x.example/1",
    }
    full = asyncio.run(template_service.get_template("case-test-0001"))
    assert full["cover"] == card["cover"]


def test_no_public_base_means_no_cover(library):
    """Without a reachable base there is no URL to give; never hand out a bare path."""
    assert asyncio.run(template_service.search_templates())["results"][0]["cover"] is None
    assert asyncio.run(template_service.get_template("case-test-0001"))["cover"] is None


# ── settings ──────────────────────────────────────────────────────────────────


def test_public_http_settings_load_and_reject_typos(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
    cfg.write_text(yaml.safe_dump({"public_http": {"public_base_url": "https://a.example"}}))
    assert load_settings().public_http.public_base_url == "https://a.example"
    cfg.write_text(yaml.safe_dump({"public_http": {"public_url": "https://a.example"}}))
    with pytest.raises(ValueError, match="public_url"):
        load_settings()


def test_moved_cover_service_keys_are_ignored_with_a_warning(tmp_path, monkeypatch, caplog):
    """The cover service moved to cfdream-file-server; an old config.yaml must still load."""
    cfg = tmp_path / "config.yaml"
    monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
    old = {"host": "0.0.0.0", "port": 8090, "covers_dir": "/x", "cache_max_age": 1, "skill_roots": {"public": "/p"}, "skill_rescan_interval": 5}
    cfg.write_text(yaml.safe_dump({"public_http": {**old, "public_base_url": "https://a.example"}}))
    with caplog.at_level("WARNING", logger="cfgpu_mcp.settings"):
        loaded = load_settings().public_http
    assert loaded.public_base_url == "https://a.example"
    assert "cfdream-file-server" in caplog.text
