"""Shared helper: name every reference a probe request passes.

A model whose dialect is ``required`` (MiniMax H3 r2v) refuses a reference the prompt
never names, because its weights silently ignore one. Tests that probe something else —
caps, resolutions, durations — tag their references so that refusal does not mask
what they are testing.
"""

from __future__ import annotations

_SLOTS = (
    ("reference_images", "image_refs", "img"),
    ("reference_videos", "video_refs", "vid"),
    ("reference_audios", "audio_refs", "aud"),
)


def tag_references(kwargs: dict, adapter=None) -> dict:
    """``kwargs`` with a handle for each reference and a ``[[handle]]`` for it in the prompt.

    With ``adapter``, only kinds that model can name in a prompt are tagged — 万相 2.7
    r2v takes audio as a subject's voice, and naming it is refused.
    """
    nameable = None
    if adapter is not None:
        from cfgpu_mcp.adapters.prompt_render import dialect_for

        dialect = dialect_for(adapter)
        nameable = set(dialect.ref) if dialect else set()
    out = dict(kwargs)
    mentions: list[str] = []
    for slot, refs_name, stem in _SLOTS:
        items = out.get(slot) or []
        kind = stem.replace("img", "image").replace("vid", "video").replace("aud", "audio")
        if nameable is not None and kind not in nameable:
            continue
        if items and refs_name not in out:
            out[refs_name] = [f"{stem}{i}" for i in range(len(items))]
            mentions += [f"[[{h}]]" for h in out[refs_name]]
    if mentions:
        out["prompt"] = " ".join(mentions) + " " + (out.get("prompt") or "")
    return out
