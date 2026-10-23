"""DeerFlow skill covers on the public cover service (docs/template-library-and-dialect.md §6.8).

Unlike template covers the whitelist is not fixed at startup: users publish and unpublish
skills at any time. ``SkillCoverIndex`` rescans the configured roots and swaps in a new
``{url path: file}`` dict; a request is still only an exact dict lookup (I-TL8).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from cfgpu_mcp.public_http import SkillCoverIndex, build_public_app
from cfgpu_mcp.settings import load_settings


def _skill(root: Path, rel: str, covers: dict[str, bytes] | None = None) -> Path:
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    for name, data in (covers or {}).items():
        (skill_dir / "covers").mkdir(exist_ok=True)
        (skill_dir / "covers" / name).write_bytes(data)
    return skill_dir


@pytest.fixture
def roots(tmp_path) -> dict[str, Path]:
    public = tmp_path / "public"
    published = tmp_path / "public_published"
    _skill(public, "cf-dream/branding/brandkit", {"1.webp": b"webp", "2.mp4": b"mp4", "notes.txt": b"txt"})
    _skill(published, "u_coffee", {"a.png": b"png", "b.JPEG": b"jpeg"})
    brand = public / "cf-dream/branding/brandkit"
    (brand / "references").mkdir()
    (brand / "references" / "r.png").write_bytes(b"not a cover")
    (brand / "scripts").mkdir()
    # A covers/ dir that is not directly under a skill dir.
    (brand / "references" / "covers").mkdir()
    (brand / "references" / "covers" / "deep.png").write_bytes(b"deep")
    # A nested SKILL.md is not a skill (DeerFlow stops at the package boundary), and the
    # reviewer's eval fixtures are excluded by the DeerFlow loader too.
    _skill(public, "skill-reviewer/evals/fixtures/bad", {"x.png": b"fixture"})
    # A covers/ dir with no SKILL.md beside it.
    (public / "loose" / "covers").mkdir(parents=True)
    (public / "loose" / "covers" / "l.png").write_bytes(b"loose")
    # Symlinks: a file escaping the root, and a covers dir that is itself a link.
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")
    os.symlink(secret, brand / "covers" / "escape.png")
    linked = _skill(public, "linked")
    outside = tmp_path / "outside-covers"
    outside.mkdir()
    (outside / "o.png").write_bytes(b"outside")
    os.symlink(outside, linked / "covers")
    return {"public": public, "public_published": published}


@pytest.fixture
def index(roots) -> SkillCoverIndex:
    idx = SkillCoverIndex(roots)
    idx.refresh()
    return idx


@pytest.fixture
def client(index) -> TestClient:
    return TestClient(build_public_app({}, cache_max_age=600, skill_covers=index))


def test_collects_only_media_files_directly_in_a_skill_covers_dir(index):
    assert set(index.files) == {
        "public/cf-dream/branding/brandkit/covers/1.webp",
        "public/cf-dream/branding/brandkit/covers/2.mp4",
        "public_published/u_coffee/covers/a.png",
        "public_published/u_coffee/covers/b.JPEG",
    }


@pytest.mark.parametrize(
    ("path", "media_type", "body"),
    [
        ("/skill-covers/public/cf-dream/branding/brandkit/covers/1.webp", "image/webp", b"webp"),
        ("/skill-covers/public/cf-dream/branding/brandkit/covers/2.mp4", "video/mp4", b"mp4"),
        ("/skill-covers/public_published/u_coffee/covers/a.png", "image/png", b"png"),
        ("/skill-covers/public_published/u_coffee/covers/b.JPEG", "image/jpeg", b"jpeg"),
    ],
)
def test_serves_covers_with_public_headers(client, path, media_type, body):
    resp = client.get(path + "?v=3")  # the cache-busting query is ignored
    assert resp.status_code == 200
    assert resp.content == body
    assert resp.headers["content-type"] == media_type
    assert resp.headers["cache-control"] == "public, max-age=600"
    assert resp.headers["access-control-allow-origin"] == "*"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert client.head(path).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/skill-covers/public/cf-dream/branding/brandkit/SKILL.md",
        "/skill-covers/public/cf-dream/branding/brandkit/covers/notes.txt",
        "/skill-covers/public/cf-dream/branding/brandkit/references/r.png",
        "/skill-covers/public/cf-dream/branding/brandkit/references/covers/deep.png",
        "/skill-covers/public/cf-dream/branding/brandkit/covers/escape.png",
        "/skill-covers/public/linked/covers/o.png",
        "/skill-covers/public/skill-reviewer/evals/fixtures/bad/covers/x.png",
        "/skill-covers/public/loose/covers/l.png",
        "/skill-covers/public/cf-dream/branding/brandkit/covers/%2e%2e/SKILL.md",
        "/skill-covers/public/cf-dream/branding/brandkit/covers/",
        "/skill-covers/custom/u_coffee/covers/a.png",
        "/skill-covers/",
    ],
)
def test_anything_else_is_404(client, path):
    assert client.get(path).status_code == 404


def test_only_reads_are_allowed(client):
    assert client.post("/skill-covers/public_published/u_coffee/covers/a.png").status_code == 405


def test_rescan_picks_up_publish_and_unpublish(roots, index, client):
    _skill(roots["public_published"], "u_new", {"n.webp": b"new"})
    (roots["public_published"] / "u_coffee" / "covers" / "a.png").unlink()
    assert client.get("/skill-covers/public_published/u_new/covers/n.webp").status_code == 404  # not yet
    index.refresh()
    assert client.get("/skill-covers/public_published/u_new/covers/n.webp").status_code == 200
    assert client.get("/skill-covers/public_published/u_coffee/covers/a.png").status_code == 404


def test_file_removed_between_scans_is_404_not_500(roots, index, client):
    (roots["public_published"] / "u_coffee" / "covers" / "a.png").unlink()
    assert client.get("/skill-covers/public_published/u_coffee/covers/a.png").status_code == 404


def test_unreadable_root_keeps_its_previous_result(roots, index):
    # A shared-disk blip must not blank every card; the other root still rescans.
    before = {k for k in index.files if k.startswith("public/")}
    roots["public"].rename(roots["public"].with_name("gone"))
    _skill(roots["public_published"], "u_new", {"n.webp": b"new"})
    index.refresh()
    assert {k for k in index.files if k.startswith("public/")} == before
    assert "public_published/u_new/covers/n.webp" in index.files


def test_unset_skill_roots_registers_no_route():
    client = TestClient(build_public_app({}, cache_max_age=600))
    assert client.get("/skill-covers/public/x/covers/1.webp").status_code == 404
    assert "skill" not in client.get("/healthz").text


def test_healthz_counts_skill_covers(client):
    assert client.get("/healthz").text == "ok 0 covers, 4 skill covers"


def test_root_alias_must_be_a_plain_name(tmp_path):
    for alias in ("", "a/b", "..", "UP"):
        with pytest.raises(ValueError, match="alias"):
            SkillCoverIndex({alias: tmp_path})


def test_background_rescan_runs_on_the_app_lifespan(roots):
    index = SkillCoverIndex(roots)
    app = build_public_app({}, cache_max_age=600, skill_covers=index, skill_rescan_interval=0.05)
    with TestClient(app) as client:
        _skill(roots["public_published"], "u_new", {"n.webp": b"new"})
        for _ in range(100):
            if client.get("/skill-covers/public_published/u_new/covers/n.webp").status_code == 200:
                break
            time.sleep(0.02)
        else:
            pytest.fail("background rescan never picked up the new cover")


def test_settings_load_skill_roots(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
    cfg.write_text(yaml.safe_dump({"public_http": {"skill_roots": {"public": "/srv/public"}, "skill_rescan_interval": 30}}))
    loaded = load_settings().public_http
    assert (loaded.skill_roots, loaded.skill_rescan_interval) == ({"public": "/srv/public"}, 30)
    cfg.write_text(yaml.safe_dump({"public_http": {}}))
    assert load_settings().public_http.skill_roots is None
