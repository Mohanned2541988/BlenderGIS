"""Autopilot: topic in, finished documentary out.

Runs the whole pipeline as one background job:
plan project -> web research -> claims -> script -> storyboard -> visuals -> cards -> narration
-> subtitles -> render. Every step uses whichever providers are configured; with no AI provider the
script is editable placeholder text and the result says so.
"""
from __future__ import annotations

import json
import re

from . import media, render, scriptwriter, storyboard, workflows
from .projects import ASPECTS, VISUAL_STYLES, create_project
from .providers import ProviderError, extract_json, get_llm, get_tts
from .providers.llm import LLMS
from .providers.speech import MacSayTTS

PLATFORM_FOR = {"9:16": "instagram_reels", "16:9": "youtube", "1:1": "instagram_reels"}
PRESET_FOR = {"9:16": "vertical", "16:9": "landscape", "1:1": "square"}

PLAN_SYSTEM = """You plan short documentary explainers for Arabic-speaking audiences on social video.
Respond with ONLY a JSON object, no commentary."""

CLAIMS_SYSTEM = """You extract checkable factual claims for a documentary from research sources.
Use ONLY what the sources' summaries say; never add facts. Respond with ONLY a JSON array."""

# Palette pairs (dark, accent) per visual style for generated backgrounds.
BACKGROUNDS = {
    "editorial_collage": [("0x1e1914", "0x6b5427"), ("0x141414", "0x5a2a20"), ("0x1b1a17", "0x3f4a3c")],
    "historical_documentary": [("0x1c1610", "0x6e5434"), ("0x15120e", "0x4d3b26")],
    "cinematic_realism": [("0x0e1417", "0x2e4a52"), ("0x161210", "0x5a3e2b")],
    "archival_investigation": [("0x18140f", "0x6b5a3a"), ("0x120f0d", "0x5a2a20")],
    "animated_infographic": [("0x121416", "0x2f4a66"), ("0x141414", "0x6b5427")],
    "minimalist_explainer": [("0x111111", "0x333333"), ("0x141414", "0x6b5427")],
    "photographic_essay": [("0x141312", "0x4a4540"), ("0x101214", "0x34404a")],
}
TEXTURES = ["noise=alls=14:allf=t", "noise=alls=8:allf=t,boxblur=2", "noise=alls=20:allf=t+u,eq=contrast=0.9"]


def _plan(topic: str, settings: dict, duration: float, aspect: str) -> dict:
    """Project details. With an AI provider they are drafted from the topic; otherwise sensible defaults."""
    plan = {"title": topic, "title_ar": "", "topic": topic, "main_question": "", "audience": "Curious Arabic-speaking viewers aged 18-35",
            "tone": "curious, precise, calm", "visual_style": "editorial_collage", "queries": [topic]}
    llm = get_llm(settings)
    if not llm.is_ai:
        plan["main_question"] = f"What is the real story behind {topic}?"
        return plan
    prompt = (f"Topic: {topic}\nFormat: {aspect}, about {int(duration)} seconds, Arabic narration with English "
              f"subtitles.\nReturn JSON with keys: title_en (short, catchy), title_ar (Arabic), main_question "
              f"(one sentence, English), audience, tone, visual_style (one of {VISUAL_STYLES}), "
              f"queries (2-3 web search queries in English to research the topic).")
    try:
        data = extract_json(llm.complete(PLAN_SYSTEM, prompt, settings, max_tokens=2000, want_json=True))
    except ProviderError:
        return plan
    if isinstance(data, dict):
        plan.update({
            "title": str(data.get("title_en") or topic)[:120], "title_ar": str(data.get("title_ar") or "")[:120],
            "main_question": str(data.get("main_question") or "")[:300],
            "audience": str(data.get("audience") or plan["audience"])[:300],
            "tone": str(data.get("tone") or plan["tone"])[:200],
            "visual_style": data.get("visual_style") if data.get("visual_style") in VISUAL_STYLES else "editorial_collage",
            "queries": [str(q) for q in (data.get("queries") or [])][:3] or [topic],
        })
    return plan


def _research(proj, plan: dict, settings: dict, use_search: bool, job) -> dict:
    found = 0
    anth = LLMS["anthropic"]
    if use_search and anth.available(settings)[0]:
        seen = set()
        for i, q in enumerate(plan["queries"]):
            job and job.update(message=f"Researching: {q}")
            try:
                results = anth.web_search(q, settings, max_results=5)
            except ProviderError as e:
                proj.log_event(f"autopilot search failed for {q!r}: {e}")
                continue
            for r in results:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                proj.db.put("source", {**r, "reviewed": False, "added_by": "web_search", "quotes": [], "query": q})
                found += 1
    claims = 0
    llm = get_llm(settings)
    srcs = proj.db.list("source")
    if llm.is_ai and srcs:
        job and job.update(message="Extracting claims from sources")
        listing = "\n".join(f"- id={s['id']} | {s.get('title')} | {s.get('summary') or ''}" for s in srcs)
        prompt = (f"Topic: {plan['topic']}\nSources:\n{listing}\n\nReturn a JSON array (max 10) of objects "
                  f'{{"text": one factual sentence in English, "source_ids": [ids from the list]}}.')
        try:
            raw = extract_json(llm.complete(CLAIMS_SYSTEM, prompt, settings, max_tokens=4000, want_json=True))
        except ProviderError:
            raw = []
        ids = {s["id"] for s in srcs}
        for c in raw if isinstance(raw, list) else []:
            if not isinstance(c, dict) or not str(c.get("text") or "").strip():
                continue
            sid = [i for i in c.get("source_ids") or [] if i in ids]
            if not sid:
                continue  # a claim with no real source is dropped rather than invented
            # Always unverified: nobody has read the sources yet.
            proj.db.put("claim", {"text": str(c["text"])[:500], "status": "unverified", "source_ids": sid,
                                  "conflicting_source_ids": []})
            claims += 1
    return {"sources": found, "claims": claims}


def _background(proj, scene: dict, idx: int, style: str) -> dict:
    """Generated textured background (clearly named as such) used until real footage is imported."""
    w, h = proj.resolution()
    dark, accent = BACKGROUNDS.get(style, BACKGROUNDS["editorial_collage"])[idx % 2 if style != "editorial_collage" else idx % 3]
    tex = TEXTURES[idx % len(TEXTURES)]
    x0, y0 = (0, 0) if idx % 2 == 0 else (w, 0)
    dest = proj.path(f"media/graphics/auto_bg_{scene['id']}.png")
    media.run_ffmpeg(["-f", "lavfi", "-i",
                      f"gradients=s={w}x{h}:c0={dark}:c1={accent}:c2=0x0b0b0b:n=3:x0={x0}:y0={y0}:x1={w - x0}:y1={h}:d=1",
                      "-frames:v", "1", "-vf", f"{tex},vignette=PI/4", str(dest)], timeout=120)
    return media.register_file(proj, dest, "graphics", dest.name, scene["id"], origin="autopilot",
                               extra={"name": f"Auto background {idx + 1} (generated)", "placeholder": True,
                                      "status": "replace"})


def _auto_card(scene: dict, idx: int, n: int, plan: dict) -> dict | None:
    if idx == 0:
        return {"type": "title", "title": plan.get("title_ar") or plan["title"],
                "subtitle": plan["title"] if plan.get("title_ar") else plan.get("main_question", ""), "lang": "auto"}
    if idx == n - 1:
        return {"type": "end", "title": plan.get("title_ar") or plan["title"], "subtitle": plan["title"]
                if plan.get("title_ar") else "", "lang": "auto"}
    ost = (scene.get("on_screen_text") or "").strip()
    if scene.get("purpose") == "evidence" and re.search(r"\d", ost) and len(ost) <= 14:
        return {"type": "statistic", "title": ost, "subtitle": scene.get("narration_en", "")[:60], "lang": "auto"}
    return None


def _tts_settings(settings: dict) -> dict:
    """Use the configured voice; if that is the silent mock, upgrade to a macOS Arabic voice when present."""
    if get_tts(settings).name != "mock":
        return settings
    say = MacSayTTS()
    if say.available(settings)[0]:
        try:
            say.pick_voice("ar", settings)
            return dict(settings, providers=dict(settings["providers"], tts="macos_say"))
        except ProviderError:
            pass
    return settings


class _SubJob:
    """Maps a sub-step's 0..1 progress into a slice of the parent job; cancel and process pass through."""

    def __init__(self, job, start: float, span: float):
        self._job, self._start, self._span = job, start, span

    @property
    def cancelled(self):
        return self._job.cancelled

    @property
    def proc(self):
        return self._job.proc

    @proc.setter
    def proc(self, p):
        self._job.proc = p

    def update(self, progress=None, message=None):
        self._job.update(progress=None if progress is None else self._start + self._span * progress, message=message)


def run(params: dict, settings: dict, job=None) -> dict:
    topic = (params.get("topic") or "").strip()
    if not topic:
        raise ValueError("Enter a topic")
    aspect = params.get("aspect_ratio") if params.get("aspect_ratio") in ASPECTS else "9:16"
    try:
        duration = max(15.0, min(600.0, float(params.get("target_duration") or 60)))
    except (TypeError, ValueError):
        duration = 60.0
    def step(p, m):
        if job is None:
            return
        if job.cancelled:
            raise render.Cancelled("Autopilot cancelled")
        job.update(progress=p, message=m)
    llm = get_llm(settings)
    notes = []

    step(0.02, "Planning the documentary")
    plan = _plan(topic, settings, duration, aspect)
    title = plan["title"] if not plan.get("title_ar") else f"{plan['title_ar']} — {plan['title']}"
    proj = create_project({
        "title": title, "topic": topic, "main_question": plan["main_question"], "audience": plan["audience"],
        "platform": PLATFORM_FOR[aspect], "aspect_ratio": aspect, "target_duration": duration,
        "narration_language": "ar", "subtitle_language": "bilingual", "visual_style": plan["visual_style"],
        "tone": plan["tone"], "budget": params.get("budget", ""),
        "research_requirements": "Auto-generated: every source and claim is unverified until you review it.",
        "preferred_providers": f"language model: {llm.name}",
    })
    proj.log_event(f"autopilot started for topic {topic!r} with llm={llm.name}")

    step(0.08, "Researching sources")
    res = _research(proj, plan, settings, bool(params.get("web_search", True)), job)
    if not res["sources"]:
        notes.append("No web sources were added (web search needs Claude). Add sources in Research and "
                     "regenerate the script for a sourced version.")

    step(0.25, "Writing the script")
    script = scriptwriter.generate_script(proj, settings, replace=True)
    if not script["ai"]:
        notes.append("No AI language model is configured, so the narration is placeholder text. Configure "
                     "Claude or Ollama in Settings and run Autopilot again for a written script.")
    scenes = proj.scenes()

    step(0.35, "Building the storyboard and visuals")
    meta = proj.meta
    for i, s in enumerate(scenes):
        s.update(storyboard.generate(s, meta, i))
        bg = _background(proj, s, i, meta.get("visual_style"))
        s["media"]["visual_asset_id"] = bg["id"]
        s["asset_status"] = "replace"
        card = _auto_card(s, i, len(scenes), plan)
        if card:
            s["card"] = card
        proj.db.put("scene", s)
    notes.append("Visuals are generated textured backgrounds (marked 'needs replacement'); use the storyboard "
                 "prompts to create or find real images, then import them.")

    tts_settings = _tts_settings(settings)
    tts_name = get_tts(tts_settings).name
    for i, s in enumerate(scenes):
        step(0.45 + 0.2 * i / max(1, len(scenes)), f"Recording narration {i + 1} of {len(scenes)}")
        try:
            workflows.tts_scene(proj, s["id"], "ar", tts_settings)
        except ProviderError:
            pass  # placeholder text is skipped; the scene keeps its estimated duration
    if tts_name == "mock":
        notes.append("Narration is silent placeholder audio (no Arabic voice found). Install an Arabic macOS voice "
                     "or import recordings, then re-render.")

    step(0.66, "Timing subtitles")
    cues = workflows.cues_from_script(proj, settings)
    sub_mode = ("bilingual" if cues.get("ar") and cues.get("en") else
                "ar" if cues.get("ar") else "en" if cues.get("en") else "none")

    result = {"project_id": proj.id, "title": title, "llm": llm.name, "ai_script": script["ai"],
              "sources": res["sources"], "claims": res["claims"], "scenes": len(scenes), "voice": tts_name,
              "render": None, "notes": notes}
    if params.get("render", True):
        step(0.68, "Rendering the video")
        opts = {"preset": PRESET_FOR[aspect], "quality": params.get("quality") or "standard",
                "subtitle_mode": sub_mode, "film_grain": False, "filename": proj.id}

        rec = render.render(proj, opts, settings, _SubJob(job, 0.68, 0.32) if job else None)
        result["render"] = {"id": rec["id"], "output": rec["output"], "duration": rec["duration"]}
    proj.log_event("autopilot finished: " + json.dumps({k: result[k] for k in ("sources", "claims", "scenes", "voice")}))
    return result
