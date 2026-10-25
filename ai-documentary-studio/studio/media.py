"""Media helpers built on ffprobe/ffmpeg: probing, thumbnails, import, audio fixes."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from .db import new_id
from .projects import PathError, Project

log = logging.getLogger("studio")

EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"},
    "video": {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".aiff", ".aif", ".flac", ".ogg", ".opus"},
}
FOLDER_FOR = {"image": "media/images", "video": "media/videos", "audio": "media/audio",
              "music": "media/music", "graphics": "media/graphics"}
MAX_IMPORT_BYTES = 4 * 1024 ** 3


class MediaError(RuntimeError):
    pass


def which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    # Homebrew paths are often missing from GUI-launched environments on macOS.
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        cand = Path(prefix) / name
        if cand.exists():
            return str(cand)
    return None


def ffmpeg_bin() -> str:
    b = which("ffmpeg")
    if not b:
        raise MediaError("FFmpeg not found. Install it with: brew install ffmpeg")
    return b


def ffprobe_bin() -> str:
    b = which("ffprobe")
    if not b:
        raise MediaError("ffprobe not found. Install it with: brew install ffmpeg")
    return b


def ffmpeg_features() -> dict:
    b = which("ffmpeg")
    if not b:
        return {"available": False}
    try:
        conf = subprocess.run([b, "-hide_banner", "-buildconf"], capture_output=True, text=True, timeout=10).stdout
        ver = subprocess.run([b, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "error": str(e)}
    return {
        "available": True, "path": b, "version": ver.splitlines()[0] if ver else "",
        "libass": "--enable-libass" in conf, "libx264": "--enable-libx264" in conf,
        "harfbuzz": "--enable-libharfbuzz" in conf, "fribidi": "--enable-libfribidi" in conf,
        "videotoolbox": "--enable-videotoolbox" in conf or "videotoolbox" in conf,
    }


def kind_for(path: Path) -> str | None:
    ext = Path(path).suffix.lower()
    for kind, exts in EXTENSIONS.items():
        if ext in exts:
            return kind
    return None


def probe(path: Path) -> dict:
    try:
        r = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise MediaError(f"ffprobe timed out on {Path(path).name}")
    if r.returncode != 0:
        raise MediaError(f"Unsupported or damaged media file: {Path(path).name}")
    data = json.loads(r.stdout or "{}")
    info = {"duration": None, "width": None, "height": None, "codec": None, "has_audio": False,
            "has_video": False, "sample_rate": None, "size": None}
    fmt = data.get("format", {})
    try:
        info["duration"] = round(float(fmt.get("duration")), 3)
    except (TypeError, ValueError):
        pass
    try:
        info["size"] = int(fmt.get("size"))
    except (TypeError, ValueError):
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info["has_video"]:
            info.update(has_video=True, width=s.get("width"), height=s.get("height"), codec=s.get("codec_name"))
            if s.get("disposition", {}).get("attached_pic"):
                info["has_video"] = False
        elif s.get("codec_type") == "audio" and not info["has_audio"]:
            info.update(has_audio=True, sample_rate=s.get("sample_rate"))
            info["codec"] = info["codec"] or s.get("codec_name")
    return info


def run_ffmpeg(args: list[str], timeout: float = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if r.returncode != 0:
        raise MediaError("FFmpeg failed: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
    return r


def make_thumbnail(proj: Project, asset: dict) -> str | None:
    src = proj.path(asset["path"])
    out = proj.path(f"media/.thumbs/{asset['id']}.jpg")
    try:
        if asset["type"] in ("image", "graphics"):
            run_ffmpeg(["-i", str(src), "-vf", "scale=360:-2", "-frames:v", "1", str(out)], timeout=60)
        elif asset["type"] == "video":
            seek = str(min(1.0, (asset.get("info", {}).get("duration") or 2) / 3))
            run_ffmpeg(["-ss", seek, "-i", str(src), "-vf", "scale=360:-2", "-frames:v", "1", str(out)], timeout=60)
        elif asset["type"] in ("audio", "music"):
            run_ffmpeg(["-i", str(src), "-filter_complex",
                        "aformat=channel_layouts=mono,showwavespic=s=360x120:colors=#E8B04B",
                        "-frames:v", "1", str(out)], timeout=60)
        else:
            return None
    except (MediaError, subprocess.TimeoutExpired) as e:
        log.warning("thumbnail failed for %s: %s", asset["id"], e)
        return None
    return proj.rel(out)


def _unique_dest(folder: Path, name: str) -> Path:
    name = "".join(c for c in Path(name).name if c not in '<>:"|?*\\/\x00').strip() or "file"
    dest = folder / name
    stem, ext = dest.stem, dest.suffix
    i = 2
    while dest.exists():
        dest = folder / f"{stem}_{i}{ext}"
        i += 1
    return dest


def register_file(proj: Project, dest: Path, kind: str, original_name: str, scene_id=None, origin="import",
                  extra: dict | None = None) -> dict:
    """Probe a file already inside the project and add it as an asset record."""
    try:
        info = probe(dest)
    except MediaError:
        dest.unlink(missing_ok=True)
        raise
    if kind in ("image", "graphics") and not info["width"]:
        dest.unlink(missing_ok=True)
        raise MediaError(f"{original_name} is not a readable image")
    if kind in ("audio", "music") and not info["has_audio"]:
        dest.unlink(missing_ok=True)
        raise MediaError(f"{original_name} contains no audio stream")
    if kind == "video" and not info["has_video"]:
        dest.unlink(missing_ok=True)
        raise MediaError(f"{original_name} contains no video stream")
    asset = {
        "id": new_id("ast"), "name": Path(original_name).stem, "original_name": original_name,
        "type": kind, "path": proj.rel(dest), "info": info, "scene_id": scene_id, "status": "pending",
        "tags": [], "origin": origin, "imported": time.time(), "thumb": None, "ai_generated": False,
    }
    if extra:
        asset.update(extra)
    asset["thumb"] = make_thumbnail(proj, asset)
    proj.db.put("asset", asset)
    proj.log_event(f"asset added {asset['id']} ({kind}) from {origin}")
    return asset


def import_bytes(proj: Project, filename: str, data_iter, as_kind: str | None = None, scene_id=None) -> dict:
    kind = kind_for(Path(filename))
    if not kind:
        raise MediaError(f"Unsupported file type: {Path(filename).suffix or filename}")
    target_kind = as_kind if as_kind in FOLDER_FOR else kind
    if target_kind in ("music",) and kind != "audio":
        raise MediaError("Music must be an audio file")
    if target_kind == "graphics" and kind != "image":
        raise MediaError("Graphics must be image files")
    dest = _unique_dest(proj.path(FOLDER_FOR[target_kind]), filename)
    written = 0
    with open(dest, "wb") as f:
        for chunk in data_iter:
            written += len(chunk)
            if written > MAX_IMPORT_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise MediaError("File too large (limit 4 GB)")
            f.write(chunk)
    return register_file(proj, dest, target_kind, Path(filename).name, scene_id)


def import_path(proj: Project, src: str, as_kind: str | None = None, scene_id=None) -> dict:
    """Copy a local file into the project. The original file is never modified."""
    p = Path(src).expanduser()
    if not p.is_absolute():
        raise MediaError("Please give an absolute file path")
    if not p.is_file():
        raise MediaError(f"File not found: {p}")
    if p.stat().st_size > MAX_IMPORT_BYTES:
        raise MediaError("File too large (limit 4 GB)")

    def chunks():
        with open(p, "rb") as f:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    return
                yield b
    return import_bytes(proj, p.name, chunks(), as_kind, scene_id)


def derived_audio(proj: Project, asset: dict, op: str) -> dict:
    """Create a processed copy of an audio asset (original kept)."""
    src = proj.path(asset["path"])
    folder = proj.path(FOLDER_FOR.get(asset["type"], "media/audio"))
    if op == "normalize":
        filt, suffix = "loudnorm=I=-16:TP=-1.5:LRA=11", "norm"
    elif op == "trim_silence":
        filt = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
                "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse")
        suffix = "trim"
    else:
        raise MediaError(f"unknown audio operation {op}")
    dest = _unique_dest(folder, f"{Path(asset['path']).stem}_{suffix}.wav")
    run_ffmpeg(["-i", str(src), "-af", filt, "-ar", "48000", "-ac", "2", str(dest)], timeout=600)
    new = register_file(proj, dest, asset["type"], dest.name, asset.get("scene_id"), origin=f"{op}:{asset['id']}")
    return new


def placeholder_image(proj: Project, label: str, scene_id=None, color="0x2a2622") -> dict:
    """Generate a plain local placeholder frame (clearly marked) so renders work without media."""
    w, h = proj.resolution()
    dest = _unique_dest(proj.path("media/graphics"), f"placeholder_{scene_id or 'x'}.png")
    run_ffmpeg(["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0={color}:c1=0x0d0d0d:x0=0:y0=0:x1={w}:y1={h}:d=1",
                "-frames:v", "1", "-vf", "noise=alls=8:allf=t", str(dest)], timeout=60)
    return register_file(proj, dest, "graphics", dest.name, scene_id, origin="placeholder",
                         extra={"name": f"PLACEHOLDER - {label}"[:80], "placeholder": True})
