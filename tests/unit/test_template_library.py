"""Template library: schema, CI gate, bundle integrity, search, get, and exposure.

Design: docs/template-library-and-dialect.md §6. Invariants pinned here: I-TL1
(published versions immutable), I-TL2 (no write path), I-TL5 (no dialect in content),
I-TL6 (deterministic order), I-TL7 (bundle re-validated at load), I-DL5 (dialect never
reaches the caller).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml

from cfgpu_mcp import template_library
from cfgpu_mcp.service import template as template_service
from cfgpu_mcp.template_library.bundle import (
    BUNDLE_PATH,
    BundleError,
    compile_source,
    dump_bundle,
    load_bundle,
    parse_bundle,
)
from cfgpu_mcp.template_library.dialect_guard import find_dialect, markup_problems
from cfgpu_mcp.template_library.index import TemplateIndex
from cfgpu_mcp.template_library.schema import COVER_MEDIA_TYPES, TemplateRecord
from cfgpu_mcp.template_library.tokenize import query_terms

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "templates"

_CATALOG = {
    "schema_version": 1,
    "catalog_version": 1,
    "facets": {"genres": ["product-food", "photography"], "scenes": ["commerce"], "styles": ["realistic", "3d"]},
}


def _record(**overrides) -> dict:
    base = {
        "id": "case-test-0001",
        "version": 1,
        "kind": "case",
        "media": "image",
        "title": "Matcha packshot",
        "summary": "A premium tea box on a neutral studio background",
        "genres": ["product-food"],
        "tags": ["packaging"],
        "source": {"origin": "unit-test", "license": "first-party"},
        "content": "A matcha box in a minimal studio, soft light, the front reads \"AURAE\".",
    }
    base.update(overrides)
    return base


def _write_tree(root: Path, records: list[dict], catalog: dict | None = None) -> Path:
    (root / "records").mkdir(parents=True, exist_ok=True)
    (root / "catalog.yaml").write_text(yaml.safe_dump(catalog or _CATALOG), encoding="utf-8")
    for rec in records:
        folder = root / "records" / rec["kind"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{rec['id']}.v{rec['version']}.yaml").write_text(
            yaml.safe_dump(rec, allow_unicode=True), encoding="utf-8"
        )
    return root


def _index(tmp_path: Path, records: list[dict], catalog: dict | None = None) -> TemplateIndex:
    return TemplateIndex(compile_source(_write_tree(tmp_path / "src", records, catalog)))


# ── the shipped bundle ────────────────────────────────────────────────────────


def test_committed_bundle_matches_the_source_tree():
    """CI gate: editing templates/ without rebuilding the bundle must fail here."""
    built = dump_bundle(compile_source(SOURCE, previous=load_bundle()))
    assert BUNDLE_PATH.read_text(encoding="utf-8") == built, "run scripts/templates/build_bundle.py"


def test_every_cover_belongs_to_a_record_version():
    """templates/covers/<id>/<version>.<ext> mirrors the OSS key; an orphan would upload a dead file."""
    keys = {(r.id, r.version) for r in load_bundle().records}
    covers = sorted((SOURCE / "covers").glob("*/*"))
    assert covers, "templates/covers is empty"
    orphans = [
        p
        for p in covers
        if p.suffix not in COVER_MEDIA_TYPES or (p.parent.name, int(p.stem)) not in keys
    ]
    assert not orphans, orphans


def test_every_cover_is_credited_as_reference_only():
    """Covers are third-party examples: each needs its source and the reference-only mark."""
    covers_dir = SOURCE / "covers"
    files = {p.relative_to(covers_dir).as_posix() for p in covers_dir.glob("*/*") if p.suffix in COVER_MEDIA_TYPES}
    credits = yaml.safe_load((covers_dir / "credits.yaml").read_text(encoding="utf-8"))
    assert "版权归" in credits["notice"] and "reference only" in credits["notice"]
    assert set(credits["covers"]) == files
    for key, entry in credits["covers"].items():
        assert entry["origin"] and entry["usage"] == "reference-only", key


def test_shipped_records_name_only_real_models():
    from cfgpu_mcp.config import load_registry

    models = {a.model_name for a in load_registry(disabled_models=[]).list_all()}
    compile_source(SOURCE, known_models=models)  # raises on an unknown verified_on


def test_shipped_library_never_exposes_dialect():
    """I-DL5 / I-TL5 over every record as the caller would receive it."""
    index = TemplateIndex(load_bundle())
    for record in index.bundle.records:
        payload = json.dumps(index.get(record.id, record.version), ensure_ascii=False)
        assert "dialect_variants" not in payload
        # The unified notation is allowed in content (judged by markup_problems); any
        # model's own spelling is not.
        hits = [h for h in find_dialect(record.content or "") if h.rule != "unified-notation"]
        assert not hits, record.id
        assert not markup_problems(record.content or "", record.media, [s.name for s in record.slots]), record.id


# ── dialect guard ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "keep <Picture 1> as the opening frame",
        "use <<<image_1>>> for the face",
        "[Image 2] wears the jacket",
        "character1 walks in",
        "把图片1的人物放进视频2",
        "参考图1的构图",
        "@图片1 作为首帧",
        "[Shot 1] wide establishing shot",
        "I2VA\n<Picture 1> a city at dusk",
        "integrated_multimodal_description: rain",
        "第1个镜头[0-3秒] 景别：全景",
        "[0-5秒]：女孩转身",
        "the box shows [[m_a1]]",
    ],
)
def test_dialect_guard_catches_model_spellings(text):
    assert find_dialect(text)


@pytest.mark.parametrize(
    "text",
    [
        "生成[商品名]电商主图，卖点为[卖点1]、[卖点2]。",
        "the image on the left shows a cafe",
        "For reference images, state what must be preserved.",
        "地图1号线站台",
        "a 3:4 portrait, 1024x1536",
    ],
)
def test_dialect_guard_leaves_neutral_text_alone(text):
    assert not find_dialect(text)


# ── schema and the CI gate ────────────────────────────────────────────────────


def test_withdrawn_record_carries_a_reason_and_no_content():
    with pytest.raises(ValueError, match="must not carry content"):
        TemplateRecord.model_validate(_record(status="withdrawn", withdrawn_reason="license"))
    with pytest.raises(ValueError, match="withdrawn_reason"):
        TemplateRecord.model_validate(_record(status="withdrawn", content=None))
    ok = TemplateRecord.model_validate(_record(status="withdrawn", content=None, withdrawn_reason="license"))
    assert ok.content is None


def test_unlisted_license_is_refused():
    with pytest.raises(ValueError, match="whitelist"):
        TemplateRecord.model_validate(_record(source={"origin": "reddit", "license": "unknown"}))


def test_gate_reports_every_problem(tmp_path):
    records = [
        _record(content="keep <Picture 1> here"),
        _record(id="case-test-0002", genres=["not-a-genre"]),
        _record(id="case-test-0003", verified_on=["no-such-model"]),
    ]
    with pytest.raises(BundleError) as err:
        compile_source(_write_tree(tmp_path, records), known_models={"cf-image-2"})
    text = "\n".join(err.value.problems)
    assert "model dialect (h3-reference-tag)" in text
    assert "genres not in catalog.yaml" in text
    assert "unknown models ['no-such-model']" in text


def test_gate_requires_file_name_to_match_identity(tmp_path):
    root = _write_tree(tmp_path, [_record()])
    path = root / "records" / "case" / "case-test-0001.v1.yaml"
    path.rename(path.with_name("renamed.yaml"))
    with pytest.raises(BundleError, match="file name must be"):
        compile_source(root)


def test_published_content_is_immutable(tmp_path):
    """I-TL1: a published (id, version) keeps its content; changes need a new version."""
    first = compile_source(_write_tree(tmp_path / "a", [_record()]))
    changed = _write_tree(tmp_path / "b", [_record(content="something else entirely")])
    with pytest.raises(BundleError, match="publish version 2 instead"):
        compile_source(changed, previous=first)

    metadata_only = _write_tree(tmp_path / "c", [_record(title="Renamed", tags=["x"], rank=3)])
    compile_source(metadata_only, previous=first)  # metadata may change

    new_version = _write_tree(tmp_path / "d", [_record(), _record(version=2, content="v2 text")])
    compile_source(new_version, previous=first)


def test_published_record_cannot_be_deleted_only_withdrawn(tmp_path):
    first = compile_source(_write_tree(tmp_path / "a", [_record()]))
    with pytest.raises(BundleError, match="withdraw it instead of deleting"):
        compile_source(_write_tree(tmp_path / "b", [_record(id="case-test-0009")]), previous=first)
    withdrawn = _record(status="withdrawn", content=None, withdrawn_reason="source license unclear")
    compile_source(_write_tree(tmp_path / "c", [withdrawn]), previous=first)


# ── bundle integrity (I-TL7) ──────────────────────────────────────────────────


def test_bundle_round_trips(tmp_path):
    bundle = compile_source(_write_tree(tmp_path, [_record()]))
    again = parse_bundle(dump_bundle(bundle))
    assert again.records == bundle.records
    assert again.checksum == bundle.checksum


def test_tampered_bundle_is_refused(tmp_path):
    data = json.loads(dump_bundle(compile_source(_write_tree(tmp_path, [_record()]))))
    data["records"][0]["content"] = "quietly edited"
    with pytest.raises(BundleError, match="checksum"):
        parse_bundle(json.dumps(data))


def test_broken_bundle_reads_as_unavailable_not_empty(tmp_path, monkeypatch):
    broken = tmp_path / "bundle.json"
    broken.write_text("{not json", encoding="utf-8")
    template_library.reset_index()
    real_get_index = template_library.get_index
    monkeypatch.setattr(template_service, "get_index", lambda: real_get_index(broken))
    try:
        result = asyncio.run(template_service.search_templates(query="tea"))
    finally:
        template_library.reset_index()
    assert result["available"] is False
    assert "could not be loaded" in result["reason"]


# ── search ────────────────────────────────────────────────────────────────────


def _corpus() -> list[dict]:
    return [
        _record(id="case-a", rank=3, title="Matcha packshot", verified_on=["cf-image-2"]),
        _record(id="case-b", rank=1, title="Coffee bag packshot", summary="咖啡豆包装棚拍", content="A coffee bag."),
        _record(
            id="style-c",
            rank=2,
            kind="style",
            title="Realistic photography",
            summary="写实摄影方向",
            genres=["photography"],
            styles=["realistic"],
            scenes=["commerce"],
            content="Guidance: real lenses, natural light.",
        ),
        _record(id="case-old", rank=0, status="deprecated", title="Old matcha", content="old"),
    ]


def test_browse_mode_follows_rank_and_skips_non_active(tmp_path):
    result = _index(tmp_path, _corpus()).search()
    assert result["mode"] == "browse"
    assert [c["id"] for c in result["results"]] == ["case-b", "style-c", "case-a"]


def test_keyword_search_scores_and_reports_terms(tmp_path):
    result = _index(tmp_path, _corpus()).search(query="matcha zebra")
    assert [c["id"] for c in result["results"]] == ["case-a"]
    assert result["matched_terms"] == ["matcha"]
    assert result["missing_terms"] == ["zebra"]


def test_chinese_query_uses_bigrams_and_single_characters(tmp_path):
    index = _index(tmp_path, _corpus())
    assert [c["id"] for c in index.search(query="咖啡包装")["results"]] == ["case-b"]
    assert [c["id"] for c in index.search(query="啡")["results"]] == ["case-b"]
    assert query_terms("咖啡包装") == ["咖啡", "啡包", "包装"]


def test_facets_are_any_of_within_and_all_across(tmp_path):
    index = _index(tmp_path, _corpus())
    assert {c["id"] for c in index.search(genres=["photography", "product-food"])["results"]} == {"case-a", "case-b", "style-c"}
    assert [c["id"] for c in index.search(genres=["photography"], styles=["realistic"])["results"]] == ["style-c"]
    assert index.search(genres=["product-food"], styles=["realistic"])["results"] == []
    assert [c["id"] for c in index.search(kind="style")["results"]] == ["style-c"]


def test_unknown_facet_value_lists_the_vocabulary(tmp_path):
    with pytest.raises(ValueError, match=r"valid values: \['product-food', 'photography'\]"):
        _index(tmp_path, _corpus()).search(genres=["food"])


def test_model_boost_prefers_verified_records_without_excluding_others(tmp_path):
    index = _index(tmp_path, _corpus())
    plain = [c["id"] for c in index.search(query="packshot")["results"]]
    boosted = [c["id"] for c in index.search(query="packshot", model="cf-image-2")["results"]]
    assert set(plain) == set(boosted) == {"case-a", "case-b"}
    assert boosted[0] == "case-a"
    assert index.search(model="cf-image-2")["results"][0]["id"] == "case-a"  # browse too


def test_order_is_deterministic_and_paging_is_complete(tmp_path):
    """I-TL6: same bundle + same query = same order; pages tile the result exactly."""
    records = [_record(id=f"case-p{i:02d}", rank=5, title="Same title") for i in range(7)]
    index = _index(tmp_path, records)
    first = index.search(query="same title", limit=3)
    assert first == index.search(query="same title", limit=3)
    seen, cursor = [], 0
    while cursor is not None:
        page = index.search(query="same title", limit=3, cursor=cursor)
        seen.extend(c["id"] for c in page["results"])
        cursor = page["next_cursor"]
    assert seen == sorted(seen) and len(seen) == 7  # ties broken by rank, then id


def test_limit_is_bounded(tmp_path):
    with pytest.raises(ValueError, match="limit"):
        _index(tmp_path, _corpus()).search(limit=21)


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_returns_latest_active_or_exact_version(tmp_path):
    records = [_record(), _record(version=2, content="second version")]
    index = _index(tmp_path, records)
    assert index.get("case-test-0001")["version"] == 2
    assert index.get("case-test-0001", 1)["content"].startswith("A matcha box")
    with pytest.raises(ValueError, match="no template 'case-test-0001@9'"):
        index.get("case-test-0001", 9)


def test_deprecated_is_not_searchable_but_still_retrievable(tmp_path):
    index = _index(tmp_path, _corpus())
    assert "case-old" not in {c["id"] for c in index.search(query="old matcha")["results"]}
    assert index.get("case-old")["status"] == "deprecated"


def test_withdrawn_returns_only_the_notice(tmp_path):
    withdrawn = _record(status="withdrawn", content=None, withdrawn_reason="source license unclear")
    got = _index(tmp_path, [withdrawn]).get("case-test-0001")
    assert got["status"] == "withdrawn"
    assert "content" not in got and got["withdrawn_reason"] == "source license unclear"


def test_dialect_variants_never_leave_the_server(tmp_path):
    """I-DL5: vendor renderings are server-internal, from search and from get alike."""
    record = _record(dialect_variants={"h3": "<Picture 1> matcha box"})
    index = _index(tmp_path, [record])
    for payload in (index.search(query="matcha"), index.get("case-test-0001")):
        text = json.dumps(payload, ensure_ascii=False)
        assert "dialect_variants" not in text and "<Picture 1>" not in text


# ── service + exposure ────────────────────────────────────────────────────────


def test_service_folds_kind_and_rejects_unknown_model():
    result = asyncio.run(template_service.search_templates(kind=" Template ", limit=1))
    assert result["available"] and result["results"][0]["kind"] == "template"
    with pytest.raises(ValueError, match="unknown model"):
        asyncio.run(template_service.search_templates(model="no-such-model"))


def test_service_accepts_internal_model_ids_and_boosts_by_public_name():
    result = asyncio.run(template_service.search_templates(model="gpt-image-2", limit=1))
    assert "cf-image-2" in result["results"][0]["verified_on"]


def test_tools_are_registered_on_every_exposure():
    from cfgpu_mcp.server import mcp
    from cfgpu_mcp.tool_registry import get_anthropic_tools

    mcp_names = {tool.name for tool in mcp._tool_manager.list_tools()}
    anthropic_names = {tool["name"] for tool in get_anthropic_tools()}
    for name in ("search_templates", "get_template"):
        assert name in anthropic_names
        if name not in mcp_names:
            pytest.skip(f"{name} disabled by the local config")


def test_no_write_tool_is_exposed():
    """I-TL2: the library has no write path for any caller."""
    from cfgpu_mcp.tool_registry import _REGISTRY

    names = {name for name, _ in _REGISTRY}
    assert not {n for n in names if "template" in n} - {"search_templates", "get_template"}


def test_dispatcher_routes_both_tools():
    from cfgpu_mcp.agent.dispatcher import dispatch_tool

    found = asyncio.run(dispatch_tool("search_templates", {"query": "水墨", "limit": 1}))
    got = asyncio.run(dispatch_tool("get_template", {"template_id": found["results"][0]["id"]}))
    assert got["content"]


def test_staging_drafts_are_not_shipped(tmp_path):
    """Drafts under templates/_staging never reach the bundle (compile reads records/ only)."""
    root = _write_tree(tmp_path / "src", [_record()])
    staging = root / "_staging" / "records" / "case"
    staging.mkdir(parents=True)
    shutil.copy(root / "records" / "case" / "case-test-0001.v1.yaml", staging / "case-draft-0001.v1.yaml")
    assert [r.id for r in compile_source(root).records] == ["case-test-0001"]


# ── unified structure markers in content ─────────────────────────────────────


@pytest.mark.parametrize("content, media", [
    ("[[shot 0-3]] [主角]登场 [[say S1: [台词]]] [[text: [片名]]] [[sound: 风声]] [[music: [配乐]]]", "video"),
    ("[[shot [开始秒]-[结束秒]]] 镜头一 [[shot [开始秒2]-[结束秒2]]] 镜头二", "video"),
    ("海报，标题 [[text: [标题]]]", "image"),
    ("没有任何标记的普通正文，[主角]在走", "video"),
])
def test_markers_a_media_renders_are_allowed(content, media):
    slots = ["主角", "台词", "片名", "配乐", "开始秒", "结束秒", "开始秒2", "结束秒2", "标题"]
    assert markup_problems(content, media, slots) == []


@pytest.mark.parametrize("content, media, fragment", [
    ("[[m_hero]] 走路", "video", "material handle"),
    ("[[hero]] 走路", "video", "material handle"),
    ("[[shot 0-3]] 海报", "image", "Shot markers are not rendered for image"),
    ("[[say: 你好]]", "image", "Say markers"),
    ("[[shot 3-1]] 走", "video", "malformed"),
    ("[[shot 0-3]] a [[shot 2-5]] b", "video", "malformed"),
    ("[[say S1: [未声明的槽]]]", "video", "malformed"),
])
def test_markers_that_would_not_render_are_refused(content, media, fragment):
    problems = markup_problems(content, media, [])
    assert problems and fragment in problems[0]


def test_the_bundle_builder_reports_bad_markup(tmp_path):
    src = tmp_path / "templates"
    shutil.copytree(SOURCE, src)
    bad = src / "records" / "template" / "tpl-video-short-drama-15s.v3.yaml"
    text = (src / "records" / "template" / "tpl-video-short-drama-15s.v2.yaml").read_text(encoding="utf-8")
    bad.write_text(text.replace("version: 2", "version: 3").replace("[[shot 6-10]]", "[[shot 2-10]]"), encoding="utf-8")
    with pytest.raises(BundleError, match="malformed marker"):
        compile_source(src)


def test_v2_templates_render_once_filled():
    """A filled-in v2 template is a prompt generate_video accepts and renders."""
    from cfgpu_mcp.adapters.prompt_render import render_for
    from cfgpu_mcp.config import load_registry
    from cfgpu_mcp.tool_registry import GenerateVideoInput

    index = TemplateIndex(load_bundle())
    record = index.get("tpl-video-short-drama-15s")
    assert record["version"] == 2
    prompt = record["content"]
    for slot in record["slots"]:
        prompt = prompt.replace(f"[{slot['name']}]", "小林" if "角色" in slot["name"] else "好")
    adapter = load_registry(disabled_models=[]).get("MiniMax-H3")
    req = GenerateVideoInput(model="MiniMax-H3", prompt=prompt, duration_seconds=15, resolution=None)
    rendered = render_for(adapter, req).req.prompt
    assert rendered.startswith("integrated_multimodal_description: [Shot 1]")
    assert "[Shot 3] At 00:11.000," in rendered and "(S2) says: <d>[Chinese] 好</d>" in rendered
    assert "[[" not in rendered
