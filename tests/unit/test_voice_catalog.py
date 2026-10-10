"""voices.yaml schema, the language vocabulary, and the card → voices.yaml migration."""

import importlib.util
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cfgpu_mcp.voice_catalog import (
    VoiceCatalog,
    language_matches,
    load_language_vocabulary,
)

ROOT = Path(__file__).parent.parent.parent


def _migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_voice_catalogs", ROOT / "scripts" / "migrate_voice_catalogs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog(**voice_overrides):
    voice = {
        "voice": "Japanese_KindLady",
        "label": "Kind Lady",
        "languages": ["ja"],
        "gender": "female",
    }
    voice.update(voice_overrides)
    return {"schema_version": 1, "voices": [voice]}


# ── language vocabulary ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("spelling", "code"),
    [
        ("English", "en"),
        ("英文", "en"),
        ("英语", "en"),
        ("en", "en"),
        ("美式英语", "en-US"),
        ("中文", "zh"),
        ("粤语", "yue"),
        ("Cantonese", "yue"),
        ("日文", "ja"),
        ("西班牙语", "es"),
    ],
)
def test_every_card_and_caller_spelling_resolves_to_one_code(spelling, code):
    assert load_language_vocabulary().resolve(spelling) == code


def test_unknown_language_spelling_resolves_to_nothing():
    assert load_language_vocabulary().resolve("克林贡语") is None


def test_language_match_is_by_subtag_prefix():
    assert language_matches(["en-US"], "en")
    assert language_matches(["zh", "zh-sichuan"], "zh-sichuan")
    assert not language_matches(["en"], "en-US")
    # Cantonese is its own language, not a Chinese subtag.
    assert not language_matches(["yue"], "zh")


# ── voices.yaml schema ──────────────────────────────────────────────────────

def test_a_well_formed_catalog_validates():
    catalog = VoiceCatalog.model_validate(_catalog(tags=["温柔"], default=True))
    assert catalog.voices[0].default is True


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"gender": None}, "gender"),
        ({"gender": "child"}, "gender"),
        ({"languages": ["klingon"]}, "language codes not in"),
        ({"languages": []}, "languages"),
        ({"voice": "   "}, "blank"),
        ({"tags": ["温柔", "温柔"]}, "duplicate"),
        ({"style": "warm"}, "Extra inputs"),
    ],
)
def test_malformed_voice_entries_are_rejected(override, message):
    with pytest.raises(ValidationError, match=message):
        VoiceCatalog.model_validate(_catalog(**override))


def test_duplicate_voice_ids_and_two_defaults_are_rejected():
    raw = _catalog(default=True)
    raw["voices"].append(dict(raw["voices"][0]))
    with pytest.raises(ValidationError, match="duplicate voice ids"):
        VoiceCatalog.model_validate(raw)

    raw["voices"][1]["voice"] = "Korean_CalmLady"
    with pytest.raises(ValidationError, match="at most one default"):
        VoiceCatalog.model_validate(raw)


def test_voice_handle_is_kept_byte_for_byte():
    handle = "Cantonese_ProfessionalHost（F) "
    catalog = VoiceCatalog.model_validate(_catalog(voice=handle, languages=["yue"]))
    assert catalog.voices[0].voice == handle


# ── import from the model_audio_voices export ───────────────────────────────

def _row(**overrides):
    row = {
        "id": 1,
        "vendor_code": "minimax",
        "voice_id": "Japanese_KindLady",
        "voice_name": "Kind Lady - 温柔妩媚,舒缓从容",
        "is_custom": 0,
        "user_id": 0,
        "language": "日语",
        "description": "一个温柔的女声。",
        "gender": "女",
        "age_group": "青年",
        "accent": "标准口音",
        "tags": '["日语", "标准口音", "女", "青年", "游戏与RPG", "豆包同款,猫箱同款", "通用场景"]',
        "sort": 10,
        "updated_at": "28/9/2026 10:50:49.187",
        "is_delete": 0,
    }
    row.update(overrides)
    return row


def _build(rows, vendor="minimax"):
    migration = _migration()
    return migration.build_catalog(rows, vendor, load_language_vocabulary())


def test_import_maps_fields_and_keeps_only_selection_tags():
    catalog, notes = _build([_row()])
    voice = catalog["voices"][0]
    assert voice == {
        "voice": "Japanese_KindLady",
        "label": "Kind Lady",
        "languages": ["ja"],
        "gender": "female",
        "age": "young",
        "accent": None,
        "description": "一个温柔的女声。",
        # traits from the name, then scenario tags; gender/age/language/accent/platform dropped
        "tags": ["温柔妩媚", "舒缓从容", "游戏与RPG"],
    }
    assert notes == {}
    assert catalog["source"] == "model_audio_voices export, vendor minimax, updated 2026-09-28"


@pytest.mark.parametrize(
    ("language", "accent", "tags", "codes", "kept_accent"),
    [
        ("英语", "英语-美音", "[]", ["en-US"], None),
        ("英语", "英语-英格兰口音", "[]", ["en-GB"], None),
        ("英语", "英语-非洲口音", "[]", ["en"], "英语-非洲口音"),
        ("葡萄牙语", None, '["巴西葡萄牙语"]', ["pt-BR"], None),
        ("西班牙文", None, '["墨西哥西语"]', ["es-MX"], None),
        ("中文 (粤语)", "标准口音", "[]", ["yue"], None),
        ("中文 (普通话)", "中国台湾口音", "[]", ["zh"], "中国台湾口音"),
        # A variant tag for another language must not relabel the voice.
        ("日语", None, '["巴西葡萄牙语"]', ["ja"], None),
    ],
)
def test_import_derives_language_codes_and_leftover_accent(language, accent, tags, codes, kept_accent):
    voice = _build([_row(language=language, accent=accent, tags=tags)])[0]["voices"][0]
    assert voice["languages"] == codes
    assert voice["accent"] == kept_accent


def test_import_keeps_only_live_system_voices_in_export_sort_order():
    rows = [
        _row(id=3, voice_id="c", sort=100),
        _row(id=2, voice_id="b", sort=10),
        _row(id=1, voice_id="a", sort=10),
        _row(id=4, voice_id="deleted", is_delete=1),
        _row(id=5, voice_id="custom", is_custom=1, user_id=42),
        _row(id=6, voice_id="other-vendor", vendor_code="volcengine"),
    ]
    catalog, _ = _build(rows)
    assert [v["voice"] for v in catalog["voices"]] == ["a", "b", "c"]


def test_import_keeps_handles_byte_for_byte():
    handle = "Cantonese_ProfessionalHost（F) "
    voice = _build([_row(voice_id=handle, language="中文 (粤语)")])[0]["voices"][0]
    assert voice["voice"] == handle


def test_import_marks_the_adapter_default_and_flags_shared_labels():
    rows = [
        _row(id=1, vendor_code="volcengine", voice_id="zh_female_xiaohe_uranus_bigtts",
             voice_name="小何 2.0", language="中文 (普通话)", accent=None, tags=None),
        _row(id=2, vendor_code="volcengine", voice_id="zh_male_sophie_uranus_bigtts",
             voice_name="魅力苏菲 2.0", language="中文 (普通话)", gender="男", accent=None, tags=None),
        _row(id=3, vendor_code="volcengine", voice_id="zh_female_sophie_uranus_bigtts",
             voice_name="魅力苏菲 2.0", language="中文 (普通话)", accent=None, tags=None),
    ]
    catalog, notes = _build(rows, vendor="volcengine")
    assert [v.get("default", False) for v in catalog["voices"]] == [True, False, False]
    assert set(notes) == {"zh_male_sophie_uranus_bigtts", "zh_female_sophie_uranus_bigtts"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("language", "克林贡语", "unmapped language"),
        ("gender", "未知", "unmapped gender"),
        ("age_group", "婴儿", "unmapped age_group"),
    ],
)
def test_import_fails_loudly_on_values_it_cannot_map(field, value, message):
    with pytest.raises(ValueError, match=message):
        _build([_row(**{field: value})])


def test_rendered_catalog_round_trips_through_the_schema():
    migration = _migration()
    rows = [
        _row(id=1),
        _row(id=2, voice_id="Norwegian_1", language="挪威语", tags="[]"),  # YAML would read `no` as false
        _row(id=3, voice_id="x", description=None, accent="中国台湾口音", language="中文 (普通话)"),
    ]
    catalog, notes = migration.build_catalog(rows, "minimax", load_language_vocabulary())
    raw = yaml.safe_load(migration.render(catalog, notes, "minimax-speech-2-8-hd"))
    loaded = VoiceCatalog.model_validate(raw)
    assert [v.languages for v in loaded.voices] == [["ja"], ["no"], ["zh"]]
    assert loaded.voices[2].accent == "中国台湾口音"
    assert loaded.voices[2].description is None


def test_script_defaults_match_the_adapters():
    from cfgpu_mcp.adapters.audio_tts import _MINIMAX_DEFAULT_VOICE, _SEED_DEFAULT_VOICE

    assert _migration().DEFAULT_VOICES == {
        "minimax-speech-2-8-hd": _MINIMAX_DEFAULT_VOICE,
        "seed-tts-2-0": _SEED_DEFAULT_VOICE,
    }
