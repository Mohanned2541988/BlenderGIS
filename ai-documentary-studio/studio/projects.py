"""Project lifecycle: create, open, list, duplicate, rename, package, status."""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path

from . import APP_NAME, APP_VERSION, PROJECT_SCHEMA_VERSION, config
from .db import ProjectDB, new_id

log = logging.getLogger("studio")

PROJECT_DIRS = [
    "database", "research", "scripts", "storyboard",
    "media/images", "media/videos", "media/audio", "media/music", "media/graphics", "media/.thumbs",
    "subtitles", "timeline", "exports", "logs",
]

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
VISUAL_STYLES = ["editorial_collage", "historical_documentary", "cinematic_realism", "archival_investigation",
                 "animated_infographic", "minimalist_explainer", "photographic_essay"]

DEFAULT_SUBTITLE_STYLE = {
    "font_ar": "Arial", "font_en": "Arial", "size": 58, "position": "bottom", "margin_v": 140,
    "outline": 3, "shadow": 0, "box": True, "box_opacity": 0.55, "color": "#FFFFFF",
    "max_chars": 42, "max_lines": 2, "min_cue_seconds": 1.0,
}
DEFAULT_GRAPHICS_STYLE = {
    "font_title": "Georgia", "font_body": "Arial", "font_ar": "Arial",
    "accent": "#E8B04B", "text": "#F4F1EA", "panel": "#141414", "margin": 90, "title_size": 96, "body_size": 54,
}
DEFAULT_TIMELINE = {
    "music_asset_id": None, "music_volume_db": -18, "music_duck": True,
    "narration_asset_id": None, "narration_volume_db": 0,
    "fade_in_start": True, "fade_out_end": True, "default_transition": "cut", "transition_seconds": 0.5,
}


def default_scene(**over) -> dict:
    scene = {
        "title": "", "purpose": "context", "narration_ar": "", "narration_en": "",
        "duration": None, "on_screen_text": "", "visual_description": "", "required_assets": "",
        "source_ids": [], "claim_ids": [], "factual": True, "sound_notes": "",
        "transition": {"type": "cut", "duration": 0.5},
        "motion": "zoom_in", "crop": {"x": 0.5, "y": 0.5},
        "card": {"type": "none", "title": "", "subtitle": "", "lang": "auto"},
        "storyboard": {
            "shot": "", "camera": "", "composition": "", "lighting": "", "palette": "", "motion_direction": "",
            "image_prompt": "", "video_prompt": "", "negative_prompt": "", "ai_reconstruction": False,
        },
        "media": {"visual_asset_id": None, "overlay_asset_id": None, "narration_asset_id": None, "sfx_asset_id": None},
        "asset_status": "missing",
        "generated_by": "user",
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(scene.get(k), dict):
            scene[k] = {**scene[k], **v}
        else:
            scene[k] = v
    return scene


class PathError(ValueError):
    pass


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        manifest = self.root / "project.json"
        if not manifest.exists():
            raise FileNotFoundError(f"Not a project folder: {self.root}")
        self.manifest = json.loads(manifest.read_text("utf-8"))
        if not isinstance(self.manifest, dict) or self.manifest.get("app") != APP_NAME:
            raise ValueError("project.json is not an AI Documentary Studio project")
        for d in PROJECT_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.db = ProjectDB(self.root / "database" / "project.db")
        self.id = self.root.name
        self._write_lock = threading.Lock()
        self._migrate_manifest()

    # -- paths --------------------------------------------------------------
    def path(self, rel: str) -> Path:
        """Resolve a project-relative path, refusing anything outside the project."""
        if not rel or Path(rel).is_absolute() or "\x00" in rel:
            raise PathError(f"invalid project path: {rel!r}")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PathError(f"path escapes project: {rel!r}")
        return p

    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.root).as_posix()

    # -- metadata -----------------------------------------------------------
    def _migrate_manifest(self):
        v = int(self.manifest.get("schema_version", 0))
        if v < PROJECT_SCHEMA_VERSION:
            # v0 -> v1: nothing structural yet; record the upgrade.
            self.manifest["schema_version"] = PROJECT_SCHEMA_VERSION
            self.save_manifest()

    def save_manifest(self):
        with self._write_lock:
            self.manifest["updated"] = time.time()
            self.manifest["app_version"] = APP_VERSION
            p = self.root / "project.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(p)

    @property
    def meta(self) -> dict:
        return self.db.get_meta("settings", {})

    def update_meta(self, changes: dict) -> dict:
        allowed = set(self.meta) | {"subtitle_style", "graphics_style", "timeline"}
        meta = self.meta
        for k, v in changes.items():
            if k not in allowed:
                continue
            if isinstance(v, dict) and isinstance(meta.get(k), dict):
                meta[k] = {**meta[k], **v}
            else:
                meta[k] = v
        if meta.get("aspect_ratio") not in ASPECTS:
            meta["aspect_ratio"] = "9:16"
        self.db.set_meta("settings", meta)
        if "title" in changes:
            self.manifest["title"] = meta["title"]
        self.save_manifest()
        return meta

    def resolution(self) -> tuple[int, int]:
        return ASPECTS.get(self.meta.get("aspect_ratio"), ASPECTS["9:16"])

    # -- scenes -------------------------------------------------------------
    def scenes(self) -> list[dict]:
        return self.db.list("scene")

    def touch(self):
        self.save_manifest()

    def log_event(self, msg: str):
        try:
            with open(self.root / "logs" / "project.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    # -- assets -------------------------------------------------------------
    def asset_usage(self, asset_id: str) -> list[str]:
        used = []
        for s in self.scenes():
            if asset_id in (s.get("media") or {}).values():
                used.append(s["id"])
        tl = self.meta.get("timeline", {})
        if asset_id in (tl.get("music_asset_id"), tl.get("narration_asset_id")):
            used.append("timeline")
        return used

    def missing_assets(self) -> list[dict]:
        out = []
        for a in self.db.list("asset"):
            try:
                if not self.path(a["path"]).exists():
                    out.append(a)
            except PathError:
                out.append(a)
        return out

    # -- status -------------------------------------------------------------
    def status(self) -> dict:
        scenes = self.scenes()
        tl = self.meta.get("timeline", {})
        has_narr = lambda s: bool(s["media"].get("narration_asset_id")) or bool(tl.get("narration_asset_id"))
        steps = {
            "research": bool(self.db.list("source")),
            "script": bool(scenes) and all((s.get("narration_ar") or s.get("narration_en")) for s in scenes),
            "storyboard": bool(scenes) and all(s["storyboard"].get("image_prompt") for s in scenes),
            "media": bool(scenes) and all(s["media"].get("visual_asset_id") or s["card"].get("type") != "none"
                                          for s in scenes),
            "voice": bool(scenes) and all(has_narr(s) for s in scenes),
            "subtitles": bool(self.db.list("cue")),
            "render": any(r.get("status") == "done" for r in self.db.list("render")),
        }
        return {"steps": steps, "percent": round(100 * sum(steps.values()) / len(steps)),
                "missing_assets": len(self.missing_assets()), "scene_count": len(scenes)}

    def snapshot(self) -> dict:
        return {
            "id": self.id, "root": str(self.root), "manifest": self.manifest, "meta": self.meta,
            "scenes": self.scenes(), "sources": self.db.list("source"), "claims": self.db.list("claim"),
            "assets": self.db.list("asset"), "renders": self.db.list("render"),
            "cue_counts": _count_by(self.db.list("cue"), "lang"), "status": self.status(),
        }

    def export_metadata(self) -> Path:
        data = self.snapshot()
        data["cues"] = self.db.list("cue")
        data["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out = self.root / "exports" / f"{self.id}_metadata.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        return out

    def package(self) -> Path:
        """Zip the project (minus exports and render scratch) for backup or transfer."""
        out = self.root / "exports" / f"{self.id}_package_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        self.backup_db(self.root / "timeline" / "_package_db.sqlite")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in self.root.rglob("*"):
                rel = p.relative_to(self.root).as_posix()
                if p.is_dir() or rel.startswith(("exports/", "timeline/render_")) or rel.startswith("database/"):
                    continue
                if rel == "timeline/_package_db.sqlite":
                    z.write(p, "database/project.db")
                    continue
                z.write(p, rel)
        (self.root / "timeline" / "_package_db.sqlite").unlink(missing_ok=True)
        return out

    def backup_db(self, dest: Path):
        dest_conn = sqlite3.connect(str(dest))
        with dest_conn:
            self.db._conn.backup(dest_conn)
        dest_conn.close()


def _count_by(items, key):
    out = {}
    for i in items:
        out[i.get(key)] = out.get(i.get(key), 0) + 1
    return out


# -- registry ---------------------------------------------------------------
_open: dict[str, Project] = {}
_reg_lock = threading.Lock()


def _unique_folder(base: Path, slug: str) -> Path:
    p = base / slug
    i = 2
    while p.exists():
        p = base / f"{slug}_{i}"
        i += 1
    return p


def create_project(params: dict, base: Path | None = None) -> Project:
    from .textutil import slugify
    base = Path(base) if base else config.projects_dir()
    title = (params.get("title") or "").strip() or "Untitled documentary"
    root = _unique_folder(base, slugify(title, "documentary"))
    root.mkdir(parents=True)
    manifest = {
        "app": APP_NAME, "schema_version": PROJECT_SCHEMA_VERSION, "uid": uuid.uuid4().hex,
        "title": title, "created": time.time(), "updated": time.time(), "app_version": APP_VERSION,
    }
    (root / "project.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
    proj = Project(root)
    aspect = params.get("aspect_ratio") if params.get("aspect_ratio") in ASPECTS else "9:16"
    try:
        duration = max(5.0, min(3600.0, float(params.get("target_duration") or 60)))
    except (TypeError, ValueError):
        duration = 60.0
    meta = {
        "title": title,
        "topic": params.get("topic", ""),
        "main_question": params.get("main_question", ""),
        "audience": params.get("audience", ""),
        "platform": params.get("platform", "instagram_reels"),
        "aspect_ratio": aspect,
        "target_duration": duration,
        "narration_language": params.get("narration_language", "ar"),
        "subtitle_language": params.get("subtitle_language", "en"),
        "visual_style": params.get("visual_style") if params.get("visual_style") in VISUAL_STYLES else "editorial_collage",
        "tone": params.get("tone", "investigative, calm, precise"),
        "research_requirements": params.get("research_requirements", ""),
        "budget": params.get("budget", "free / local only"),
        "preferred_providers": params.get("preferred_providers", ""),
        "notes": "",
        "subtitle_style": dict(DEFAULT_SUBTITLE_STYLE),
        "graphics_style": dict(DEFAULT_GRAPHICS_STYLE),
        "timeline": dict(DEFAULT_TIMELINE),
    }
    proj.db.set_meta("settings", meta)
    proj.log_event(f"project created: {title}")
    with _reg_lock:
        _open[proj.id] = proj
    return proj


def _is_project_dir(p: Path) -> bool:
    return p.is_dir() and (p / "project.json").exists()


def list_projects(base: Path | None = None) -> list[dict]:
    base = Path(base) if base else config.projects_dir()
    out = []
    for p in sorted(base.iterdir()) if base.exists() else []:
        if not _is_project_dir(p):
            continue
        try:
            m = json.loads((p / "project.json").read_text("utf-8"))
            if m.get("app") != APP_NAME:
                continue
            out.append({"id": p.name, "title": m.get("title", p.name), "updated": m.get("updated", 0),
                        "created": m.get("created", 0), "path": str(p)})
        except (OSError, ValueError):
            out.append({"id": p.name, "title": p.name, "updated": 0, "error": "project.json unreadable", "path": str(p)})
    return sorted(out, key=lambda x: -x["updated"])


def get_project(pid: str, base: Path | None = None) -> Project:
    base = Path(base) if base else config.projects_dir()
    if not pid or "/" in pid or "\\" in pid or pid.startswith("."):
        raise PathError("invalid project id")
    with _reg_lock:
        proj = _open.get(pid)
        if proj and proj.root.parent == base.resolve():
            return proj
        proj = Project(base / pid)
        _open[pid] = proj
        return proj


def duplicate_project(proj: Project, new_title: str | None = None) -> Project:
    from .textutil import slugify
    title = new_title or f"{proj.manifest.get('title', proj.id)} (copy)"
    dest = _unique_folder(proj.root.parent, slugify(title, "documentary"))
    shutil.copytree(proj.root, dest, ignore=shutil.ignore_patterns("project.db*", "render_*", "exports"))
    (dest / "exports").mkdir(exist_ok=True)
    (dest / "database").mkdir(exist_ok=True)
    proj.backup_db(dest / "database" / "project.db")
    m = json.loads((dest / "project.json").read_text("utf-8"))
    m.update(uid=uuid.uuid4().hex, title=title, created=time.time(), updated=time.time())
    (dest / "project.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), "utf-8")
    new = Project(dest)
    new.update_meta({"title": title})
    with _reg_lock:
        _open[new.id] = new
    return new


def close_all():
    with _reg_lock:
        for p in _open.values():
            p.db.close()
        _open.clear()


__all__ = ["Project", "create_project", "list_projects", "get_project", "duplicate_project",
           "default_scene", "new_id", "ASPECTS", "VISUAL_STYLES", "PathError"]
