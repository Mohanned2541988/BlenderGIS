"""Script generation, scene regeneration, translation and research briefs.

With the offline template provider these functions build editable scaffolding only; they never
invent facts. With an AI provider, the prompts restrict the model to the project's own sources.
"""
from __future__ import annotations

import json
import time

from .projects import Project, default_scene
from .providers import ProviderError, extract_json, get_llm
from .storyboard import PURPOSES
from .textutil import estimate_duration, word_count

BEATS = ["hook", "question", "context", "development", "evidence", "counterpoint", "conclusion", "cta"]

TEMPLATE_TEXT = {
    "hook": ("[افتتاحية: صورة أو حقيقة مفاجئة عن {topic} تشد المشاهد في أول ثانيتين]",
             "[Hook: a surprising image or fact about {topic} that grabs attention in two seconds]"),
    "question": ("[السؤال المركزي: {question}]", "[Central question: {question}]"),
    "context": ("[السياق: ما الذي يجب أن يعرفه المشاهد عن {topic} قبل أن نبدأ؟]",
                "[Context: what the viewer needs to know about {topic} before we start]"),
    "development": ("[تطور القصة: الحدث أو الشخص التالي، مع مصدر موثّق]",
                    "[Development: the next event or person, backed by a source]"),
    "evidence": ("[الدليل: وثيقة أو رقم أو اقتباس من مصدر موثّق]",
                 "[Evidence: a document, figure or quotation from a verified source]"),
    "counterpoint": ("[الرأي الآخر: ما الذي يقوله المعارضون أو ما الذي لا نعرفه بعد؟]",
                     "[Counterpoint: what critics say, or what remains unknown]"),
    "conclusion": ("[الخلاصة: الإجابة عن السؤال المركزي، بحذر ودقة]",
                   "[Conclusion: answer the central question, carefully and precisely]"),
    "cta": ("[دعوة: تابعنا لمزيد من القصص]", "[Call to action: follow for more stories]"),
}

SYSTEM_SCRIPT = """You are a senior documentary scriptwriter producing short explainer documentaries in the style \
of Vox explainers and investigative visual essays. Narration is Modern Standard Arabic, written to be read aloud: \
short sentences, clear rhythm, no stacked clauses. Every scene also has a faithful English translation.

Factual rules - these are strict:
- Use ONLY facts supported by the sources and claims provided. Cite them by their ids in source_ids / claim_ids.
- If the story needs a fact that is not in the sources, do not state it as fact: phrase it as an open question \
or write [VERIFY: ...] in the narration so the editor sees it.
- Never invent quotations, statistics, dates, names or citations.
- Mark factual=true for scenes making factual claims.

Respond with ONLY JSON, no commentary."""


def _sources_block(proj: Project) -> str:
    srcs = proj.db.list("source")
    claims = proj.db.list("claim")
    lines = ["SOURCES:"]
    for s in srcs:
        lines.append(f"- id={s['id']} | {s.get('title','')} | {s.get('url','')} | {s.get('published','')} | "
                     f"reviewed_by_editor={bool(s.get('reviewed'))} | notes: {(s.get('summary') or '')[:600]}")
        for q in (s.get("quotes") or [])[:5]:
            lines.append(f"    quote: \"{q[:300]}\"")
    if not srcs:
        lines.append("(none provided - do not state specific facts)")
    lines.append("CLAIMS:")
    for c in claims:
        lines.append(f"- id={c['id']} | status={c.get('status')} | {c.get('text','')} | sources={c.get('source_ids')}")
    notes = proj.meta.get("notes") or ""
    if notes:
        lines.append("RESEARCH NOTES:\n" + notes[:6000])
    return "\n".join(lines)


def _brief(meta: dict) -> str:
    return (f"Title: {meta.get('title')}\nTopic: {meta.get('topic')}\nMain question: {meta.get('main_question')}\n"
            f"Audience: {meta.get('audience')}\nPlatform: {meta.get('platform')} ({meta.get('aspect_ratio')})\n"
            f"Target duration: {meta.get('target_duration')} seconds\nTone: {meta.get('tone')}\n"
            f"Visual style: {meta.get('visual_style')}")


SCENE_SCHEMA = ('{"title": str, "purpose": one of ' + json.dumps(PURPOSES) + ', "narration_ar": str, '
                '"narration_en": str, "on_screen_text": str (max 6 words), "visual_description": str, '
                '"required_assets": str, "sound_notes": str, "transition": "cut"|"fade"|"crossfade", '
                '"source_ids": [ids], "claim_ids": [ids], "factual": bool}')


def _clean_scene(raw: dict, proj: Project, settings: dict) -> dict:
    src_ids = {s["id"] for s in proj.db.list("source")}
    claim_ids = {c["id"] for c in proj.db.list("claim")}
    purpose = raw.get("purpose") if raw.get("purpose") in PURPOSES else "development"
    trans = raw.get("transition") if raw.get("transition") in ("cut", "fade", "crossfade") else "cut"
    sc = default_scene(
        title=str(raw.get("title") or "")[:120], purpose=purpose,
        narration_ar=str(raw.get("narration_ar") or ""), narration_en=str(raw.get("narration_en") or ""),
        on_screen_text=str(raw.get("on_screen_text") or "")[:120],
        visual_description=str(raw.get("visual_description") or ""),
        required_assets=str(raw.get("required_assets") or ""), sound_notes=str(raw.get("sound_notes") or ""),
        transition={"type": trans, "duration": 0.5},
        source_ids=[i for i in raw.get("source_ids") or [] if i in src_ids],
        claim_ids=[i for i in raw.get("claim_ids") or [] if i in claim_ids],
        factual=bool(raw.get("factual", True)), generated_by=get_llm(settings).name,
    )
    return sc


def template_script(meta: dict) -> list[dict]:
    target = float(meta.get("target_duration") or 60)
    n = max(4, min(30, round(target / 9)))
    middle = n - 6
    beats = BEATS[:3] + [b for i in range(max(0, middle)) for b in [("development", "evidence")[i % 2]]] + \
        ["counterpoint", "conclusion", "cta"]
    if n < 6:
        beats = ["hook", "question", "context", "evidence", "conclusion"] if n == 5 else \
            ["hook", "context", "evidence", "conclusion"]
    per = round(target / n, 1)
    fmt = {"topic": meta.get("topic") or meta.get("title") or "the topic",
           "question": meta.get("main_question") or "?"}
    scenes = []
    for i, b in enumerate(beats):
        ar, en = TEMPLATE_TEXT[b]
        scenes.append(default_scene(
            title=f"{i + 1}. {b.capitalize()}", purpose=b, narration_ar=ar.format(**fmt),
            narration_en=en.format(**fmt), duration=per,
            visual_description=f"{b} visual for {fmt['topic']}",
            transition={"type": "crossfade" if b in ("context", "conclusion") else "cut", "duration": 0.5},
            card={"type": "title", "title": meta.get("title", ""), "subtitle": meta.get("main_question", ""),
                  "lang": "auto"} if i == 0 else
                 ({"type": "end", "title": meta.get("title", ""), "subtitle": "", "lang": "auto"}
                  if b == "cta" else {"type": "none", "title": "", "subtitle": "", "lang": "auto"}),
            factual=b in ("context", "development", "evidence", "counterpoint"),
            generated_by="template"))
    return scenes


def generate_script(proj: Project, settings: dict, replace: bool) -> dict:
    meta = proj.meta
    llm = get_llm(settings)
    if not llm.is_ai:
        scenes = template_script(meta)
    else:
        wpm = settings.get("speaking_rate_ar_wpm", 130)
        words = int(float(meta.get("target_duration") or 60) / 60 * wpm)
        prompt = (f"{_brief(meta)}\n\n{_sources_block(proj)}\n\nWrite the full script as JSON: "
                  f'{{"scenes": [ {SCENE_SCHEMA}, ... ]}}\n'
                  f"Total Arabic narration about {words} words. Structure: opening hook, central question, "
                  f"context, progressive development with evidence, counterpoint, conclusion, and a short call to "
                  f"action if suitable for {meta.get('platform')}. Each scene 5-12 seconds of narration.")
        data = extract_json(llm.complete(SYSTEM_SCRIPT, prompt, settings, max_tokens=16000, want_json=True))
        raw = data.get("scenes") if isinstance(data, dict) else data
        if not isinstance(raw, list) or not raw:
            raise ProviderError("The model returned no scenes.")
        scenes = [_clean_scene(r, proj, settings) for r in raw if isinstance(r, dict)]
    if replace:
        proj.db.delete_kind("scene")
        proj.db.delete_kind("cue", lambda c: c.get("scene_id"))
    saved = [proj.db.put("scene", s) for s in scenes]
    proj.log_event(f"script generated with {llm.name}: {len(saved)} scenes")
    (proj.root / "scripts" / f"script_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps(saved, indent=2, ensure_ascii=False), "utf-8")
    return {"scenes": saved, "provider": llm.name, "ai": llm.is_ai}


def regenerate_scene(proj: Project, scene_id: str, settings: dict, instruction: str = "") -> dict:
    llm = get_llm(settings)
    if not llm.is_ai:
        raise ProviderError("Scene regeneration needs an AI provider (Claude or Ollama) in Settings.")
    scenes = proj.scenes()
    idx = next((i for i, s in enumerate(scenes) if s["id"] == scene_id), None)
    if idx is None:
        raise KeyError(scene_id)
    ctx = lambda s: {k: s.get(k) for k in ("title", "purpose", "narration_ar", "narration_en")} if s else None
    prompt = (f"{_brief(proj.meta)}\n\n{_sources_block(proj)}\n\n"
              f"Previous scene: {json.dumps(ctx(scenes[idx-1]) if idx else None, ensure_ascii=False)}\n"
              f"Current scene: {json.dumps(ctx(scenes[idx]), ensure_ascii=False)}\n"
              f"Next scene: {json.dumps(ctx(scenes[idx+1]) if idx + 1 < len(scenes) else None, ensure_ascii=False)}\n"
              f"Editor instruction: {instruction or 'Improve clarity and rhythm; keep the same purpose.'}\n\n"
              f"Rewrite ONLY the current scene. Return one JSON object: {SCENE_SCHEMA}")
    raw = extract_json(llm.complete(SYSTEM_SCRIPT, prompt, settings, max_tokens=4000, want_json=True))
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    new = _clean_scene(raw, proj, settings)
    cur = scenes[idx]
    for k in ("title", "purpose", "narration_ar", "narration_en", "on_screen_text", "visual_description",
              "required_assets", "sound_notes", "source_ids", "claim_ids", "factual", "generated_by"):
        cur[k] = new[k]
    cur["previous_version"] = {k: scenes[idx].get(k) for k in ("narration_ar", "narration_en")}
    return proj.db.put("scene", cur)


def translate_texts(texts: list[str], settings: dict, src="Arabic", dst="English") -> list[str]:
    llm = get_llm(settings)
    if not llm.is_ai or settings["providers"].get("translation") == "manual":
        raise ProviderError("Automatic translation needs an AI provider. Enter translations manually or "
                            "configure Claude/Ollama in Settings.")
    if not texts:
        return []
    system = (f"You translate documentary narration from {src} to {dst} for subtitles. Be faithful and "
              f"concise; keep names and numbers exact; do not add information. Respond with ONLY a JSON array "
              f"of strings, same length and order as the input.")
    out = extract_json(llm.complete(system, json.dumps(texts, ensure_ascii=False), settings, max_tokens=8000,
                                    want_json=True))
    if isinstance(out, dict):
        out = next((v for v in out.values() if isinstance(v, list)), [])
    if not isinstance(out, list) or len(out) != len(texts):
        raise ProviderError("Translation came back with a different number of lines; please retry.")
    return [str(x) for x in out]


def research_brief(proj: Project, settings: dict) -> dict:
    meta = proj.meta
    srcs = proj.db.list("source")
    claims = proj.db.list("claim")
    llm = get_llm(settings)
    sections = ["Background", "Timeline", "Key events", "Important people and organizations", "Main arguments",
                "Counterarguments", "Unresolved questions", "Source list"]
    if llm.is_ai and srcs:
        system = ("You are a careful documentary researcher. Write a research brief in English (Markdown) using "
                  "ONLY the provided sources, notes and claims. Cite every factual sentence with the source id in "
                  "square brackets, e.g. [src_ab12]. Label claims as Verified / Unverified / Interpretation / "
                  "Disputed exactly as given; flag conflicting evidence. If a section has no supporting material, "
                  "write 'No sourced material yet.' Never invent facts or citations. Sources not reviewed by the "
                  "editor must be described as 'not yet reviewed'.")
        prompt = f"{_brief(meta)}\n\n{_sources_block(proj)}\n\nSections, in order: {', '.join(sections)}."
        text = llm.complete(system, prompt, settings, max_tokens=12000)
        origin = llm.name
    else:
        parts = [f"# Research brief - {meta.get('title')}", "",
                 f"**Central question:** {meta.get('main_question') or '-'}", "",
                 "_Compiled from your own notes and sources (no AI). Empty sections need research._", ""]
        by_status = {}
        for c in claims:
            by_status.setdefault(c.get("status", "unverified"), []).append(c)
        parts += ["## Background", meta.get("notes") or "No notes yet.", ""]
        dated = sorted([s for s in srcs if s.get("published")], key=lambda s: s["published"])
        parts += ["## Timeline"] + ([f"- {s['published']}: {s.get('title')} [{s['id']}]" for s in dated]
                                    or ["No dated sources yet."]) + [""]
        for title, status in (("Key events (verified claims)", "verified"), ("Main arguments (interpretations)",
                              "interpretation"), ("Counterarguments / disputed", "disputed"),
                              ("Unresolved questions (unverified)", "unverified")):
            items = by_status.get(status, [])
            parts += [f"## {title}"] + ([f"- {c['text']} " + " ".join(f"[{i}]" for i in c.get("source_ids", []))
                                         for c in items] or ["Nothing recorded."]) + [""]
        parts += ["## Important people and organizations", "Add them in your notes.", "", "## Source list"]
        parts += [f"- [{s['id']}] {s.get('title')} - {s.get('url') or 'no URL'}"
                  f"{' (' + s['published'] + ')' if s.get('published') else ''}"
                  f"{'' if s.get('reviewed') else ' - NOT YET REVIEWED'}" for s in srcs] or ["No sources yet."]
        text = "\n".join(parts)
        origin = "template"
    path = proj.root / "research" / "brief.md"
    path.write_text(text, "utf-8")
    proj.db.set_meta("research_brief", {"text": text, "origin": origin, "updated": time.time()})
    return {"text": text, "origin": origin, "path": str(path)}


def research_warnings(proj: Project) -> list[dict]:
    srcs = {s["id"]: s for s in proj.db.list("source")}
    claims = {c["id"]: c for c in proj.db.list("claim")}
    out = []
    for c in claims.values():
        if c.get("status") == "verified" and not c.get("source_ids"):
            out.append({"level": "error", "msg": f"Claim marked verified has no source: \"{c['text'][:80]}\""})
        unreviewed = [i for i in c.get("source_ids", []) if i in srcs and not srcs[i].get("reviewed")]
        if c.get("status") == "verified" and unreviewed:
            out.append({"level": "warn", "msg": f"Verified claim relies on sources you have not marked reviewed: "
                                                f"\"{c['text'][:80]}\""})
        if c.get("conflicting_source_ids"):
            out.append({"level": "warn", "msg": f"Conflicting evidence recorded for: \"{c['text'][:80]}\""})
    for i, s in enumerate(proj.scenes(), 1):
        if s.get("factual") and (s.get("narration_ar") or s.get("narration_en")) and not s.get("claim_ids"):
            out.append({"level": "warn", "msg": f"Scene {i} makes factual statements but links no claim."})
        for cid in s.get("claim_ids", []):
            c = claims.get(cid)
            if c and c.get("status") in ("unverified", "disputed"):
                out.append({"level": "warn", "msg": f"Scene {i} uses a {c['status']} claim: \"{c['text'][:60]}\""})
        if "[VERIFY" in (s.get("narration_ar", "") + s.get("narration_en", "")):
            out.append({"level": "error", "msg": f"Scene {i} contains a [VERIFY] marker."})
    return out


def scene_estimates(scene: dict, settings: dict) -> dict:
    return {
        "ar_words": word_count(scene.get("narration_ar", "")),
        "en_words": word_count(scene.get("narration_en", "")),
        "ar_seconds": estimate_duration(scene.get("narration_ar", ""), settings.get("speaking_rate_ar_wpm", 130)),
        "en_seconds": estimate_duration(scene.get("narration_en", ""), settings.get("speaking_rate_en_wpm", 150)),
    }
