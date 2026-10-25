"""Local HTTP server: JSON API + static UI + media streaming. Binds to 127.0.0.1 only."""
from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import APP_NAME, APP_VERSION, config, media, render, scriptwriter, storyboard, subtitles, workflows
from .graphics import CARD_TYPES
from .jobs import JOBS
from .media import MediaError
from .projects import (ASPECTS, VISUAL_STYLES, PathError, create_project, default_scene, duplicate_project,
                       get_project, list_projects)
from .providers import ProviderError, get_llm, get_tts, overview, secrets
from .providers.llm import LLMS

log = logging.getLogger("studio")
WEB = Path(__file__).resolve().parent.parent / "web"
MAX_JSON = 20 * 1024 * 1024

SCENE_FIELDS = {"title", "purpose", "narration_ar", "narration_en", "duration", "on_screen_text",
                "visual_description", "required_assets", "source_ids", "claim_ids", "factual", "sound_notes",
                "transition", "motion", "motion_locked", "crop", "card", "storyboard", "media", "asset_status"}
SOURCE_FIELDS = {"title", "url", "published", "publisher", "summary", "quotes", "reviewed", "kind", "notes",
                 "access"}
CLAIM_FIELDS = {"text", "status", "source_ids", "conflicting_source_ids", "notes"}
CLAIM_STATUSES = {"verified", "unverified", "interpretation", "reconstruction", "disputed"}
ASSET_FIELDS = {"name", "status", "scene_id", "tags", "ai_generated", "notes"}


class ApiError(Exception):
    def __init__(self, status, msg, code=None, extra=None):
        super().__init__(msg)
        self.status, self.code, self.extra = status, code, extra or {}


def _merge_fields(rec: dict, changes: dict, allowed: set) -> dict:
    for k, v in (changes or {}).items():
        if k not in allowed:
            continue
        if isinstance(v, dict) and isinstance(rec.get(k), dict):
            rec[k] = {**rec[k], **v}
        else:
            rec[k] = v
    return rec


def _settings():
    return config.load_settings()


def _timeline(proj):
    s = _settings()
    scenes = proj.scenes()
    assets = {a["id"]: a for a in proj.db.list("asset")}
    return render.compute_timeline(scenes, assets, s)


# -- route handlers -------------------------------------------------------------
def h_status(req, q, body):
    s = _settings()
    return {"app": APP_NAME, "version": APP_VERSION, "ffmpeg": media.ffmpeg_features(),
            "ffprobe": bool(media.which("ffprobe")), "projects_dir": str(config.projects_dir()),
            "is_mac": config.IS_MAC, "llm": get_llm(s).name, "llm_is_ai": get_llm(s).is_ai,
            "visual_styles": {k: v["label"] for k, v in storyboard.STYLES.items()},
            "purposes": storyboard.PURPOSES, "card_types": CARD_TYPES, "aspects": list(ASPECTS),
            "motions": list(render.MOTION), "presets": render.PRESETS, "jobs": JOBS.active()}


def h_settings_get(req, q, body):
    return _settings()


def h_settings_put(req, q, body):
    allowed = set(config.DEFAULT_SETTINGS)
    return config.save_settings({k: v for k, v in body.items() if k in allowed})


def h_providers(req, q, body):
    return overview(_settings())


def h_secret_set(req, q, body):
    where = secrets.set_secret(body.get("name", ""), body.get("value", ""))
    return {"ok": True, "stored_in": where}


def h_secret_del(req, q, body, name):
    return {"ok": secrets.delete_secret(name)}


def h_voices(req, q, body):
    s = _settings()
    return {"provider": get_tts(s).name, "voices": get_tts(s).voices(s)}


def h_provider_test(req, q, body):
    s = _settings()
    kind = body.get("kind")
    if kind == "llm":
        llm = get_llm(s)
        if not llm.is_ai:
            return {"ok": True, "detail": "Template provider needs no test."}
        text = llm.complete("Reply with exactly: OK", "Connection test", s, max_tokens=200)
        return {"ok": "OK" in text.upper(), "detail": text[:200]}
    raise ApiError(400, "Only the language-model connection can be tested from here.")


def h_sample(req, q, body):
    from . import sample

    def run(job):
        job.update(0.1, "Building sample project (generating media and narration)")
        proj, voiced = sample.build()
        return {"id": proj.id, "voiced": voiced}
    return JOBS.start("sample", None, run).to_dict()


def h_projects(req, q, body):
    return list_projects()


def h_project_create(req, q, body):
    p = create_project(body)
    return p.snapshot()


def P(pid):
    try:
        return get_project(pid)
    except (FileNotFoundError, PathError):
        raise ApiError(404, "Project not found")


def h_project(req, q, body, pid):
    return P(pid).snapshot()


def h_project_patch(req, q, body, pid):
    proj = P(pid)
    proj.update_meta(body)
    return proj.snapshot()


def h_project_dup(req, q, body, pid):
    return duplicate_project(P(pid), body.get("title")).snapshot()


def h_project_rename(req, q, body, pid):
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError(400, "Title cannot be empty")
    proj = P(pid)
    proj.update_meta({"title": title})
    return proj.snapshot()


def h_project_package(req, q, body, pid):
    return {"path": str(P(pid).package())}


def h_project_metadata(req, q, body, pid):
    return {"path": str(P(pid).export_metadata())}


# scenes
def h_scene_add(req, q, body, pid):
    proj = P(pid)
    sc = default_scene(**{k: v for k, v in (body.get("scene") or {}).items() if k in SCENE_FIELDS})
    rec = proj.db.insert_after("scene", body.get("after_id"), sc)
    proj.touch()
    return rec


def h_scene_put(req, q, body, pid, sid):
    proj = P(pid)
    rec = proj.db.get("scene", sid)
    if not rec:
        raise ApiError(404, "Scene not found")
    if "duration" in body and body["duration"] not in (None, ""):
        try:
            body["duration"] = max(0.5, min(600.0, float(body["duration"])))
        except (TypeError, ValueError):
            raise ApiError(400, "Duration must be a number of seconds")
    rec = proj.db.put("scene", _merge_fields(rec, body, SCENE_FIELDS))
    proj.touch()
    return rec


def h_scene_del(req, q, body, pid, sid):
    proj = P(pid)
    if not proj.db.delete("scene", sid):
        raise ApiError(404, "Scene not found")
    proj.db.delete_kind("cue", lambda c: c.get("scene_id") == sid)
    proj.touch()
    return {"ok": True}


def h_scene_dup(req, q, body, pid, sid):
    proj = P(pid)
    rec = proj.db.get("scene", sid)
    if not rec:
        raise ApiError(404, "Scene not found")
    copy = {k: v for k, v in rec.items() if k != "id"}
    copy["title"] = (rec.get("title") or "Scene") + " (copy)"
    return proj.db.insert_after("scene", sid, copy)


def h_scene_reorder(req, q, body, pid):
    proj = P(pid)
    ids = body.get("ids") or []
    existing = {s["id"] for s in proj.scenes()}
    if set(ids) != existing:
        raise ApiError(400, "Reorder list must contain every scene exactly once")
    proj.db.reorder("scene", ids)
    proj.touch()
    return proj.scenes()


def h_scene_regen(req, q, body, pid, sid):
    proj = P(pid)
    job = JOBS.start("regenerate_scene", pid, lambda j: scriptwriter.regenerate_scene(
        proj, sid, _settings(), body.get("instruction", "")))
    return job.to_dict()


def h_script_generate(req, q, body, pid):
    proj = P(pid)
    if proj.scenes() and not body.get("replace") and not body.get("append"):
        raise ApiError(409, "The project already has scenes. Confirm replace or append.", "confirm_replace")
    job = JOBS.start("generate_script", pid, lambda j: scriptwriter.generate_script(
        proj, _settings(), bool(body.get("replace"))))
    return job.to_dict()


def h_storyboard_generate(req, q, body, pid):
    proj = P(pid)
    only = set(body.get("scene_ids") or [])
    meta = proj.meta
    out = []
    for i, s in enumerate(proj.scenes()):
        if only and s["id"] not in only:
            continue
        s.update(storyboard.generate(s, meta, i))
        out.append(proj.db.put("scene", s))
    proj.touch()
    return {"updated": len(out)}


def h_export_scenes(req, q, body, pid):
    proj = P(pid)
    fmt = (q.get("format") or ["json"])[0]
    scenes = proj.scenes()
    tl = _timeline(proj)
    timing = {e["scene_id"]: e for e in tl["entries"]}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if fmt == "csv":
        buf = io.StringIO()
        cols = ["number", "id", "title", "purpose", "start", "duration", "narration_ar", "narration_en",
                "on_screen_text", "visual_description", "shot", "camera", "image_prompt", "video_prompt",
                "negative_prompt", "ai_reconstruction", "transition", "sound_notes", "source_ids"]
        w = csv.writer(buf)
        w.writerow(cols)
        for i, s in enumerate(scenes, 1):
            sb = s.get("storyboard") or {}
            t = timing.get(s["id"], {})
            w.writerow([i, s["id"], s.get("title"), s.get("purpose"), t.get("start"), t.get("duration"),
                        s.get("narration_ar"), s.get("narration_en"), s.get("on_screen_text"),
                        s.get("visual_description"), sb.get("shot"), sb.get("camera"), sb.get("image_prompt"),
                        sb.get("video_prompt"), sb.get("negative_prompt"), sb.get("ai_reconstruction"),
                        (s.get("transition") or {}).get("type"), s.get("sound_notes"), " ".join(s.get("source_ids", []))])
        data, name, ctype = "\ufeff" + buf.getvalue(), f"storyboard_{stamp}.csv", "text/csv"
    elif fmt == "md":
        lines = [f"# {proj.meta.get('title')} - script & storyboard", ""]
        for i, s in enumerate(scenes, 1):
            sb = s.get("storyboard") or {}
            t = timing.get(s["id"], {})
            lines += [f"## Scene {i}: {s.get('title') or ''}", f"*{s.get('purpose')} - {t.get('duration')}s*", "",
                      f"**AR:** {s.get('narration_ar')}", "", f"**EN:** {s.get('narration_en')}", "",
                      f"**On screen:** {s.get('on_screen_text')}", f"**Visual:** {s.get('visual_description')}",
                      f"**Image prompt:** {sb.get('image_prompt')}", f"**Video prompt:** {sb.get('video_prompt')}",
                      f"**Negative:** {sb.get('negative_prompt')}",
                      f"**AI reconstruction:** {'yes - label on screen' if sb.get('ai_reconstruction') else 'no'}", ""]
        data, name, ctype = "\n".join(lines), f"script_{stamp}.md", "text/markdown"
    else:
        data = json.dumps({"project": proj.meta.get("title"), "timeline": tl, "scenes": scenes}, indent=2,
                          ensure_ascii=False)
        name, ctype = f"scenes_{stamp}.json", "application/json"
    path = proj.root / "storyboard" / name
    path.write_text(data, "utf-8")
    return {"path": str(path), "url": f"/media/{pid}/storyboard/{urllib.parse.quote(name)}", "type": ctype}


# research
def _rec_put(proj, kind, rid, body, allowed):
    rec = proj.db.get(kind, rid) if rid else {}
    if rid and not rec:
        raise ApiError(404, f"{kind} not found")
    rec = _merge_fields(rec or {}, body, allowed)
    if kind == "claim":
        if rec.get("status") not in CLAIM_STATUSES:
            rec["status"] = "unverified"
        if not (rec.get("text") or "").strip():
            raise ApiError(400, "Claim text is required")
    if kind == "source":
        url = (rec.get("url") or "").strip()
        if url and not re.match(r"^https?://", url):
            raise ApiError(400, "Source URL must start with http:// or https://")
        if not (rec.get("title") or url):
            raise ApiError(400, "A source needs a title or URL")
        rec.setdefault("reviewed", False)
        rec.setdefault("added_by", "user")
        if isinstance(rec.get("quotes"), str):
            rec["quotes"] = [l.strip() for l in rec["quotes"].split("\n") if l.strip()]
    rec = proj.db.put(kind, rec)
    proj.touch()
    return rec


def h_source_add(req, q, body, pid):
    return _rec_put(P(pid), "source", None, body, SOURCE_FIELDS)


def h_source_put(req, q, body, pid, rid):
    return _rec_put(P(pid), "source", rid, body, SOURCE_FIELDS)


def h_source_del(req, q, body, pid, rid):
    proj = P(pid)
    proj.db.delete("source", rid)
    for c in proj.db.list("claim"):
        if rid in c.get("source_ids", []) or rid in c.get("conflicting_source_ids", []):
            c["source_ids"] = [i for i in c.get("source_ids", []) if i != rid]
            c["conflicting_source_ids"] = [i for i in c.get("conflicting_source_ids", []) if i != rid]
            proj.db.put("claim", c)
    for s in proj.scenes():
        if rid in s.get("source_ids", []):
            s["source_ids"].remove(rid)
            proj.db.put("scene", s)
    return {"ok": True}


def h_claim_add(req, q, body, pid):
    return _rec_put(P(pid), "claim", None, body, CLAIM_FIELDS)


def h_claim_put(req, q, body, pid, rid):
    return _rec_put(P(pid), "claim", rid, body, CLAIM_FIELDS)


def h_claim_del(req, q, body, pid, rid):
    proj = P(pid)
    proj.db.delete("claim", rid)
    for s in proj.scenes():
        if rid in s.get("claim_ids", []):
            s["claim_ids"].remove(rid)
            proj.db.put("scene", s)
    return {"ok": True}


def h_brief(req, q, body, pid):
    proj = P(pid)
    job = JOBS.start("research_brief", pid, lambda j: scriptwriter.research_brief(proj, _settings()))
    return job.to_dict()


def h_research_get(req, q, body, pid):
    proj = P(pid)
    return {"brief": proj.db.get_meta("research_brief"), "warnings": scriptwriter.research_warnings(proj)}


def h_research_search(req, q, body, pid):
    s = _settings()
    if s["providers"].get("search") != "anthropic_web_search":
        raise ApiError(400, "No web-search provider configured. Add sources manually, or enable Claude web "
                            "search in Settings.")
    query = (body.get("query") or "").strip()
    if not query:
        raise ApiError(400, "Enter a search query")
    proj = P(pid)

    def run(job):
        results = LLMS["anthropic"].web_search(query, s)
        added = []
        for r in results:
            if any(x.get("url") == r["url"] for x in proj.db.list("source")):
                continue
            added.append(proj.db.put("source", {**r, "reviewed": False, "added_by": "web_search",
                                                "quotes": [], "query": query}))
        return {"found": len(results), "added": len(added)}
    return JOBS.start("web_search", pid, run).to_dict()


# assets
def h_asset_import_path(req, q, body, pid):
    return media.import_path(P(pid), body.get("path", ""), body.get("kind"), body.get("scene_id"))


def h_asset_put(req, q, body, pid, aid):
    proj = P(pid)
    rec = proj.db.get("asset", aid)
    if not rec:
        raise ApiError(404, "Asset not found")
    if body.get("status") and body["status"] not in ("pending", "approved", "rejected", "replace"):
        raise ApiError(400, "Invalid status")
    return proj.db.put("asset", _merge_fields(rec, body, ASSET_FIELDS))


def h_asset_del(req, q, body, pid, aid):
    proj = P(pid)
    rec = proj.db.get("asset", aid)
    if not rec:
        raise ApiError(404, "Asset not found")
    used = proj.asset_usage(aid)
    if used and not body.get("confirm_in_use"):
        raise ApiError(409, f"This asset is used in {len(used)} place(s) on the timeline.", "asset_in_use",
                       {"used_by": used})
    if not body.get("confirm"):
        raise ApiError(409, "Confirm deletion of the project copy.", "confirm_delete")
    for s in proj.scenes():
        changed = False
        for k, v in list(s["media"].items()):
            if v == aid:
                s["media"][k] = None
                changed = True
        if changed:
            proj.db.put("scene", s)
    tl = proj.meta.get("timeline", {})
    for k in ("music_asset_id", "narration_asset_id"):
        if tl.get(k) == aid:
            tl[k] = None
    proj.update_meta({"timeline": tl})
    # Only the copy inside the project is removed; files imported from elsewhere stay untouched.
    # Removed files go to a trash folder inside the project instead of being destroyed.
    try:
        src = proj.path(rec["path"])
        if src.exists():
            trash = proj.path("media/.trash")
            trash.mkdir(exist_ok=True)
            shutil.move(str(src), str(trash / f"{aid}_{src.name}"))
    except PathError:
        pass
    proj.db.delete("asset", aid)
    return {"ok": True}


def h_asset_process(req, q, body, pid, aid):
    proj = P(pid)
    rec = proj.db.get("asset", aid)
    if not rec or rec["type"] not in ("audio", "music"):
        raise ApiError(400, "Audio asset required")
    return JOBS.start("audio_" + body.get("op", ""), pid,
                      lambda j: media.derived_audio(proj, rec, body.get("op"))).to_dict()


def h_asset_placeholder(req, q, body, pid):
    proj = P(pid)
    sid = body.get("scene_id")
    scene = proj.db.get("scene", sid) if sid else None
    a = media.placeholder_image(proj, (scene or {}).get("title") or "frame", sid)
    if scene and not scene["media"].get("visual_asset_id"):
        scene["media"]["visual_asset_id"] = a["id"]
        proj.db.put("scene", scene)
    return a


def h_asset_check(req, q, body, pid):
    proj = P(pid)
    return {"missing": proj.missing_assets()}


# voice
def h_tts_scene(req, q, body, pid, sid):
    proj = P(pid)
    lang = body.get("lang") or "ar"
    return JOBS.start("tts", pid, lambda j: workflows.tts_scene(proj, sid, lang, _settings())).to_dict()


def h_tts_all(req, q, body, pid):
    proj = P(pid)
    lang = body.get("lang") or "ar"
    only_missing = body.get("only_missing", True)

    def run(job):
        scenes = [s for s in proj.scenes() if not (only_missing and s["media"].get("narration_asset_id"))]
        done, errors = 0, []
        for i, s in enumerate(scenes):
            if job.cancelled:
                break
            job.update(i / max(1, len(scenes)), f"Voice {i + 1} of {len(scenes)}")
            try:
                workflows.tts_scene(proj, s["id"], lang, _settings())
                done += 1
            except (ProviderError, MediaError) as e:
                errors.append(f"{s.get('title') or s['id']}: {e}")
        return {"generated": done, "errors": errors}
    return JOBS.start("tts_all", pid, run).to_dict()


# subtitles
def h_subs_get(req, q, body, pid):
    proj = P(pid)
    tl = _timeline(proj)
    ar, en = render.absolute_cues(proj, tl, "ar"), render.absolute_cues(proj, tl, "en")
    return {"timeline": tl, "ar": ar, "en": en, "problems": {"ar": subtitles.validate_cues(ar),
                                                             "en": subtitles.validate_cues(en)},
            "vtt": {"ar": subtitles.to_vtt(ar), "en": subtitles.to_vtt(en)}}


def h_subs_put(req, q, body, pid, lang):
    if lang not in ("ar", "en"):
        raise ApiError(400, "Language must be ar or en")
    n = workflows.replace_cues(P(pid), lang, body.get("cues") or [], True, _settings())
    return {"saved": n}


def h_subs_from_script(req, q, body, pid):
    langs = [l for l in (body.get("langs") or ["ar", "en"]) if l in ("ar", "en")]
    return workflows.cues_from_script(P(pid), _settings(), langs)


def h_subs_transcribe(req, q, body, pid):
    proj = P(pid)
    lang = body.get("lang") or "ar"
    return JOBS.start("transcribe", pid, lambda j: workflows.transcribe(proj, _settings(), lang)).to_dict()


def h_subs_translate(req, q, body, pid):
    proj = P(pid)
    return JOBS.start("translate", pid, lambda j: workflows.translate_cues(proj, _settings())).to_dict()


def h_subs_import(req, q, body, pid):
    lang = body.get("lang")
    if lang not in ("ar", "en"):
        raise ApiError(400, "Language must be ar or en")
    cues = subtitles.parse_srt(body.get("text", ""))
    if not cues:
        raise ApiError(400, "No subtitle cues found in that file")
    proj = P(pid)
    proj.db.delete_kind("cue", lambda c: c.get("lang") == lang)
    for c in cues:
        proj.db.put("cue", dict(c, lang=lang, scene_id=None, origin="imported"))
    return {"imported": len(cues)}


def h_subs_export(req, q, body, pid):
    return {"files": workflows.export_subtitles(P(pid), _settings())}


# timeline & render
def h_timeline(req, q, body, pid):
    proj = P(pid)
    return {"timeline": _timeline(proj), "settings": proj.meta.get("timeline", {})}


def h_render_validate(req, q, body, pid):
    v = render.validate(P(pid), body, _settings())
    return {k: v[k] for k in ("ok", "errors", "warnings", "options")}


def h_render(req, q, body, pid):
    proj = P(pid)
    s = _settings()
    v = render.validate(proj, body, s)
    if not v["ok"]:
        raise ApiError(400, "Fix these problems before rendering", "validation", {"errors": v["errors"]})
    o = v["options"]
    if (Path(o["output_dir"]).expanduser() / o["filename"]).exists() and not o["overwrite"]:
        raise ApiError(409, f"{o['filename']} already exists. Replace it?", "exists")
    kind = "preview" if body.get("preview") else "render"
    return JOBS.start(kind, pid, lambda j: render.render(proj, body, s, j)).to_dict()


def h_render_download(req, q, body, pid, rid):
    proj = P(pid)
    rec = proj.db.get("render", rid)
    if not rec or rec.get("status") != "done":
        raise ApiError(404, "Render not found")
    return FileResponse(Path(rec["output"]))


def h_jobs(req, q, body):
    pid = (q.get("project") or [None])[0]
    return JOBS.active(pid)


def h_job(req, q, body, jid):
    j = JOBS.get(jid)
    if not j:
        raise ApiError(404, "Job not found")
    return j.to_dict()


def h_job_cancel(req, q, body, jid):
    j = JOBS.get(jid)
    if not j:
        raise ApiError(404, "Job not found")
    j.cancel()
    return j.to_dict()


class FileResponse:
    def __init__(self, path: Path, download=False):
        self.path, self.download = path, download


ROUTES = [
    ("GET", r"/api/status", h_status),
    ("GET", r"/api/settings", h_settings_get),
    ("PUT", r"/api/settings", h_settings_put),
    ("GET", r"/api/providers", h_providers),
    ("POST", r"/api/providers/secret", h_secret_set),
    ("DELETE", r"/api/providers/secret/([A-Z_]+)", h_secret_del),
    ("POST", r"/api/providers/test", h_provider_test),
    ("GET", r"/api/tts/voices", h_voices),
    ("GET", r"/api/projects", h_projects),
    ("POST", r"/api/projects", h_project_create),
    ("POST", r"/api/sample", h_sample),
    ("GET", r"/api/projects/([^/]+)", h_project),
    ("PATCH", r"/api/projects/([^/]+)", h_project_patch),
    ("POST", r"/api/projects/([^/]+)/duplicate", h_project_dup),
    ("POST", r"/api/projects/([^/]+)/rename", h_project_rename),
    ("POST", r"/api/projects/([^/]+)/package", h_project_package),
    ("POST", r"/api/projects/([^/]+)/export-metadata", h_project_metadata),
    ("POST", r"/api/projects/([^/]+)/scenes", h_scene_add),
    ("POST", r"/api/projects/([^/]+)/scenes/reorder", h_scene_reorder),
    ("PUT", r"/api/projects/([^/]+)/scenes/([^/]+)", h_scene_put),
    ("DELETE", r"/api/projects/([^/]+)/scenes/([^/]+)", h_scene_del),
    ("POST", r"/api/projects/([^/]+)/scenes/([^/]+)/duplicate", h_scene_dup),
    ("POST", r"/api/projects/([^/]+)/scenes/([^/]+)/regenerate", h_scene_regen),
    ("POST", r"/api/projects/([^/]+)/scenes/([^/]+)/tts", h_tts_scene),
    ("POST", r"/api/projects/([^/]+)/script/generate", h_script_generate),
    ("POST", r"/api/projects/([^/]+)/storyboard/generate", h_storyboard_generate),
    ("GET", r"/api/projects/([^/]+)/export/scenes", h_export_scenes),
    ("POST", r"/api/projects/([^/]+)/sources", h_source_add),
    ("PUT", r"/api/projects/([^/]+)/sources/([^/]+)", h_source_put),
    ("DELETE", r"/api/projects/([^/]+)/sources/([^/]+)", h_source_del),
    ("POST", r"/api/projects/([^/]+)/claims", h_claim_add),
    ("PUT", r"/api/projects/([^/]+)/claims/([^/]+)", h_claim_put),
    ("DELETE", r"/api/projects/([^/]+)/claims/([^/]+)", h_claim_del),
    ("GET", r"/api/projects/([^/]+)/research", h_research_get),
    ("POST", r"/api/projects/([^/]+)/research/brief", h_brief),
    ("POST", r"/api/projects/([^/]+)/research/search", h_research_search),
    ("POST", r"/api/projects/([^/]+)/assets/import-path", h_asset_import_path),
    ("POST", r"/api/projects/([^/]+)/assets/placeholder", h_asset_placeholder),
    ("GET", r"/api/projects/([^/]+)/assets/check", h_asset_check),
    ("PUT", r"/api/projects/([^/]+)/assets/([^/]+)", h_asset_put),
    ("DELETE", r"/api/projects/([^/]+)/assets/([^/]+)", h_asset_del),
    ("POST", r"/api/projects/([^/]+)/assets/([^/]+)/process", h_asset_process),
    ("POST", r"/api/projects/([^/]+)/tts/all", h_tts_all),
    ("GET", r"/api/projects/([^/]+)/subtitles", h_subs_get),
    ("PUT", r"/api/projects/([^/]+)/subtitles/(ar|en)", h_subs_put),
    ("POST", r"/api/projects/([^/]+)/subtitles/from-script", h_subs_from_script),
    ("POST", r"/api/projects/([^/]+)/subtitles/transcribe", h_subs_transcribe),
    ("POST", r"/api/projects/([^/]+)/subtitles/translate", h_subs_translate),
    ("POST", r"/api/projects/([^/]+)/subtitles/import", h_subs_import),
    ("POST", r"/api/projects/([^/]+)/subtitles/export", h_subs_export),
    ("GET", r"/api/projects/([^/]+)/timeline", h_timeline),
    ("POST", r"/api/projects/([^/]+)/render/validate", h_render_validate),
    ("POST", r"/api/projects/([^/]+)/render", h_render),
    ("GET", r"/api/projects/([^/]+)/renders/([^/]+)/file", h_render_download),
    ("GET", r"/api/jobs", h_jobs),
    ("GET", r"/api/jobs/([^/]+)", h_job),
    ("POST", r"/api/jobs/([^/]+)/cancel", h_job_cancel),
]
COMPILED = [(m, re.compile("^" + p + "$"), h) for m, p, h in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "AIDocStudio/" + APP_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- security ---------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, msg, code=None, extra=None):
        self._send_json(status, {"error": msg, "code": code, **(extra or {})})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def _dispatch(self, method):
        if not self._host_ok():
            return self._error(403, "Forbidden host")
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        if method in ("POST", "PUT", "PATCH", "DELETE") and self.headers.get("X-Studio") != "1":
            # Custom header blocks cross-site form posts / simple requests from other origins.
            return self._error(403, "Missing X-Studio header")
        try:
            if path.startswith("/media/") and method in ("GET", "HEAD"):
                return self._serve_media(path, method)
            if method == "POST" and re.match(r"^/api/projects/[^/]+/assets/upload$", path):
                return self._upload(path, query)
            for m, rx, h in COMPILED:
                if m != method:
                    continue
                mt = rx.match(path)
                if mt:
                    body = self._read_json() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                    result = h(self, query, body, *mt.groups())
                    if isinstance(result, FileResponse):
                        return self._serve_file(result.path, method, download=True)
                    return self._send_json(200, result)
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                return self._serve_static(path, method)
            return self._error(404, "Not found")
        except ApiError as e:
            return self._error(e.status, str(e), e.code, e.extra)
        except (PathError, MediaError, ProviderError, ValueError, render.RenderError) as e:
            status = 409 if isinstance(e, render.RenderConflict) else 400
            return self._error(status, str(e), type(e).__name__)
        except (KeyError, FileNotFoundError) as e:
            return self._error(404, f"Not found: {e}")
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:
            log.exception("unhandled error on %s %s", method, path)
            return self._error(500, f"Internal error: {e}")

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_JSON:
            raise ApiError(413, "Request too large")
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "Invalid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "JSON object expected")
        return data

    def _upload(self, path, query):
        pid = path.split("/")[3]
        proj = P(pid)
        filename = (query.get("filename") or [""])[0]
        n = int(self.headers.get("Content-Length") or 0)
        if not filename or n <= 0:
            raise ApiError(400, "filename and file body required")

        def chunks():
            left = n
            while left > 0:
                b = self.rfile.read(min(1024 * 1024, left))
                if not b:
                    break
                left -= len(b)
                yield b
        asset = media.import_bytes(proj, filename, chunks(), (query.get("kind") or [None])[0],
                                   (query.get("scene_id") or [None])[0])
        sid = (query.get("scene_id") or [None])[0]
        slot = (query.get("slot") or [None])[0]
        if sid and slot in ("visual_asset_id", "overlay_asset_id", "narration_asset_id", "sfx_asset_id"):
            sc = proj.db.get("scene", sid)
            if sc:
                sc["media"][slot] = asset["id"]
                proj.db.put("scene", sc)
        return self._send_json(200, asset)

    def _serve_static(self, path, method):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        p = (WEB / rel).resolve()
        if WEB.resolve() not in p.parents or not p.is_file():
            p = WEB / "index.html"  # single-page app fallback
        return self._serve_file(p, method)

    def _serve_media(self, path, method):
        parts = path.split("/", 3)
        if len(parts) < 4:
            return self._error(404, "Not found")
        proj = P(parts[2])
        return self._serve_file(proj.path(parts[3]), method)

    def _serve_file(self, p: Path, method, download=False):
        if not p.is_file():
            return self._error(404, "File not found")
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(p.name)}")
        self.end_headers()
        if method == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                b = f.read(min(256 * 1024, left))
                if not b:
                    break
                try:
                    self.wfile.write(b)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(b)


def setup_logging():
    logdir = config.app_support_dir() / "logs"
    logdir.mkdir(exist_ok=True)
    handler = logging.FileHandler(logdir / "studio.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger("studio")
    root.setLevel(logging.DEBUG if os.environ.get("ADS_DEBUG") else logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)


def make_server(port: int) -> ThreadingHTTPServer:
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/vtt", ".vtt")
    mimetypes.add_type("application/x-subrip", ".srt")
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv
