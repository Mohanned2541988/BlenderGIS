"""Voice-over and subtitle workflows that combine providers, media and the timeline."""
from __future__ import annotations

import time

from . import render, subtitles
from .media import FOLDER_FOR, _unique_dest, register_file
from .projects import Project
from .providers import ProviderError, get_stt, get_tts
from .render import NARRATION_LEAD
from .scriptwriter import translate_texts


def tts_scene(proj: Project, scene_id: str, lang: str, settings: dict) -> dict:
    scene = proj.db.get("scene", scene_id)
    if not scene:
        raise KeyError(scene_id)
    text = (scene.get("narration_ar" if lang == "ar" else "narration_en") or "").strip()
    if not text:
        raise ProviderError(f"Scene has no {'Arabic' if lang == 'ar' else 'English'} narration text.")
    if text.startswith("[") and text.endswith("]"):
        raise ProviderError("This scene still contains template placeholder text - write the narration first.")
    tts = get_tts(settings)
    dest = _unique_dest(proj.path(FOLDER_FOR["audio"]), f"narration_{scene_id}_{lang}.wav")
    info = tts.synthesize(text, lang, dest, settings)
    asset = register_file(proj, dest, "audio", dest.name, scene_id, origin=f"tts:{tts.name}",
                          extra={"name": f"Narration {lang.upper()} - {scene.get('title') or scene_id}",
                                 "tts": info, "lang": lang, "placeholder": bool(info.get("mock"))})
    scene["media"]["narration_asset_id"] = asset["id"]
    proj.db.put("scene", scene)
    return asset


def _scene_spans(proj: Project, settings: dict):
    scenes = proj.scenes()
    assets = {a["id"]: a for a in proj.db.list("asset")}
    tl = render.compute_timeline(scenes, assets, settings)
    for s, e in zip(scenes, tl["entries"]):
        narr = assets.get(s["media"].get("narration_asset_id"))
        if narr and (narr.get("info") or {}).get("duration"):
            yield s, NARRATION_LEAD, NARRATION_LEAD + narr["info"]["duration"], narr, "script timed to narration audio"
        else:
            yield s, 0.2, max(0.5, e["duration"] - 0.3), None, "estimated from text"


def cues_from_script(proj: Project, settings: dict, langs=("ar", "en")) -> dict:
    """Spread each scene's narration text across its real (or estimated) narration span."""
    style = proj.meta.get("subtitle_style", {})
    counts = {}
    for lang in langs:
        proj.db.delete_kind("cue", lambda c, l=lang: c.get("lang") == l)
        n = 0
        for s, t0, t1, _narr, origin in _scene_spans(proj, settings):
            text = s.get("narration_ar" if lang == "ar" else "narration_en") or ""
            if text.strip().startswith("[") and text.strip().endswith("]"):
                continue
            for c in subtitles.distribute(text, t0, t1, style.get("max_chars", 42), style.get("max_lines", 2),
                                          style.get("min_cue_seconds", 1.0)):
                proj.db.put("cue", dict(c, lang=lang, scene_id=s["id"], origin=origin))
                n += 1
        counts[lang] = n
    return counts


def transcribe(proj: Project, settings: dict, lang: str = "ar") -> dict:
    """Transcribe the real narration audio (per scene, or the global narration track)."""
    stt = get_stt(settings)
    tl = proj.meta.get("timeline", {})
    assets = {a["id"]: a for a in proj.db.list("asset")}
    made = 0
    if tl.get("narration_asset_id") in assets:
        cues = stt.transcribe(proj.path(assets[tl["narration_asset_id"]]["path"]), lang, settings)
        proj.db.delete_kind("cue", lambda c: c.get("lang") == lang)
        for c in cues:
            proj.db.put("cue", dict(c, lang=lang, scene_id=None, origin=f"transcribed:{stt.name}"))
        return {"lang": lang, "cues": len(cues), "mode": "global narration"}
    todo = [(s, t0, narr) for s, t0, _t1, narr, _o in _scene_spans(proj, settings) if narr]
    if not todo:
        raise ProviderError("No narration audio to transcribe. Import or generate narration first.")
    results = []
    for s, t0, narr in todo:
        results.append((s, t0, stt.transcribe(proj.path(narr["path"]), lang, settings)))
    proj.db.delete_kind("cue", lambda c: c.get("lang") == lang and c.get("scene_id") in {s["id"] for s, _, _ in todo})
    for s, t0, cues in results:
        for c in subtitles.shift(cues, t0):
            proj.db.put("cue", dict(c, lang=lang, scene_id=s["id"], origin=f"transcribed:{stt.name}"))
            made += 1
    return {"lang": lang, "cues": made, "mode": f"{len(todo)} scene clips"}


def translate_cues(proj: Project, settings: dict, src="ar", dst="en") -> dict:
    """Create target-language cues with identical timing by translating each source cue."""
    cues = [c for c in proj.db.list("cue") if c.get("lang") == src]
    if not cues:
        raise ProviderError("No source subtitles to translate.")
    texts = translate_texts([c["text"].replace("\n", " ") for c in cues], settings,
                            "Arabic" if src == "ar" else "English", "English" if dst == "en" else "Arabic")
    proj.db.delete_kind("cue", lambda c: c.get("lang") == dst)
    style = proj.meta.get("subtitle_style", {})
    from .textutil import wrap_lines
    for c, t in zip(cues, texts):
        proj.db.put("cue", {"lang": dst, "scene_id": c.get("scene_id"), "start": c["start"], "end": c["end"],
                            "text": wrap_lines(t, style.get("max_chars", 42)), "origin": "translated"})
    return {"lang": dst, "cues": len(cues)}


def replace_cues(proj: Project, lang: str, cues: list[dict], timeline_abs: bool, settings: dict) -> int:
    """Save cues edited in the UI. Times arrive absolute; scene-linked cues are stored relative."""
    starts = {}
    if timeline_abs:
        scenes = proj.scenes()
        assets = {a["id"]: a for a in proj.db.list("asset")}
        starts = {e["scene_id"]: e["start"] for e in render.compute_timeline(scenes, assets, settings)["entries"]}
    clean = []
    for c in cues:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Every cue needs numeric start and end times")
        if end <= start:
            raise ValueError(f"Cue at {start:.2f}s ends before it starts")
        sid = c.get("scene_id") if c.get("scene_id") in starts else None
        off = starts.get(sid, 0.0)
        clean.append({"lang": lang, "scene_id": sid, "start": round(start - off, 3), "end": round(end - off, 3),
                      "text": str(c.get("text", "")).strip(), "origin": c.get("origin") or "edited"})
    proj.db.delete_kind("cue", lambda c: c.get("lang") == lang)
    for c in clean:
        proj.db.put("cue", c)
    return len(clean)


def export_subtitles(proj: Project, settings: dict) -> list[str]:
    scenes = proj.scenes()
    assets = {a["id"]: a for a in proj.db.list("asset")}
    tl = render.compute_timeline(scenes, assets, settings)
    ar, en = render.absolute_cues(proj, tl, "ar"), render.absolute_cues(proj, tl, "en")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    written = []
    outputs = {"ar.srt": (ar, subtitles.to_srt), "en.srt": (en, subtitles.to_srt), "ar.vtt": (ar, subtitles.to_vtt),
               "en.vtt": (en, subtitles.to_vtt)}
    if ar and en:
        outputs["bilingual.srt"] = (subtitles.bilingual(ar, en), subtitles.to_srt)
    for suffix, (cues, fn) in outputs.items():
        if cues:
            p = proj.root / "subtitles" / f"{proj.id}_{stamp}.{suffix}"
            p.write_text(fn(cues), "utf-8")
            written.append(str(p))
    return written
