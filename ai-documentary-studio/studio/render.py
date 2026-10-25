"""Scene-based timeline and FFmpeg render pipeline.

Pipeline: each scene -> an intermediate H.264 segment (visual + motion + scene narration + fades),
then one assembly pass joins segments (cuts / crossfades), mixes music and a global narration
track, burns subtitles and graphics (libass), and encodes the final MP4.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import graphics, subtitles
from .media import MediaError, ffmpeg_bin, which
from .projects import ASPECTS, PathError, Project
from .textutil import estimate_duration

log = logging.getLogger("studio")

PRESETS = {"vertical": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}
QUALITY = {  # x264 preset, CRF, zoompan supersampling
    "draft": ("veryfast", 26, 1.25),
    "standard": ("medium", 20, 1.5),
    "high": ("slow", 17, 2.0),
}
FPS_ALLOWED = {24, 25, 30, 50, 60}
NARRATION_LEAD = 0.25
NARRATION_TAIL = 0.45
MIN_SCENE = 1.0


class RenderError(RuntimeError):
    pass


class RenderConflict(RenderError):
    """Output file already exists and overwrite was not confirmed."""


class Cancelled(RenderError):
    pass


# -- timeline -----------------------------------------------------------------
def _assets(proj: Project) -> dict:
    return {a["id"]: a for a in proj.db.list("asset")}


def scene_duration(scene: dict, assets: dict, settings: dict) -> tuple[float, str]:
    """Effective duration and where it came from: narration audio, manual value, or estimate."""
    narr = assets.get((scene.get("media") or {}).get("narration_asset_id"))
    manual = scene.get("duration")
    try:
        manual = float(manual) if manual not in (None, "") else None
    except (TypeError, ValueError):
        manual = None
    tail = NARRATION_TAIL
    if (scene.get("transition") or {}).get("type") == "crossfade":
        tail = max(tail, float(scene["transition"].get("duration", 0.5)) + 0.15)
    if narr and (narr.get("info") or {}).get("duration"):
        audio = narr["info"]["duration"] + NARRATION_LEAD + tail
        if manual and manual > audio:
            return round(manual, 3), "manual (longer than narration)"
        return round(audio, 3), "narration audio"
    if manual and manual > 0:
        return round(max(MIN_SCENE, manual), 3), "manual"
    text = scene.get("narration_ar") or scene.get("narration_en") or ""
    lang_rate = settings.get("speaking_rate_ar_wpm", 130) if scene.get("narration_ar") else \
        settings.get("speaking_rate_en_wpm", 150)
    est = estimate_duration(text, lang_rate, settings.get("scene_pause_seconds", 0.6))
    return round(max(3.0 if not text else MIN_SCENE, est + NARRATION_LEAD), 3), "estimate"


def compute_timeline(scenes: list[dict], assets: dict, settings: dict) -> dict:
    entries, t = [], 0.0
    for i, s in enumerate(scenes):
        dur, source = scene_duration(s, assets, settings)
        trans = dict(s.get("transition") or {"type": "cut"})
        if i == len(scenes) - 1:
            trans = {"type": "cut", "duration": 0}
        tdur = float(trans.get("duration") or 0.5)
        if trans.get("type") == "crossfade":
            tdur = min(tdur, dur / 2 - 0.05)
            # The next scene must also be long enough to overlap.
            if i + 1 < len(scenes):
                nxt, _ = scene_duration(scenes[i + 1], assets, settings)
                tdur = min(tdur, nxt / 2 - 0.05)
            if tdur < 0.1:
                trans = {"type": "cut", "duration": 0}
        entries.append({"scene_id": s["id"], "index": i, "start": round(t, 3), "duration": dur,
                        "duration_source": source, "transition": {"type": trans.get("type", "cut"),
                                                                  "duration": round(tdur, 3)}})
        overlap = tdur if trans.get("type") == "crossfade" else 0.0
        t += dur - overlap
    total = round(entries[-1]["start"] + entries[-1]["duration"], 3) if entries else 0.0
    return {"entries": entries, "total": total}


def absolute_cues(proj: Project, timeline: dict, lang: str) -> list[dict]:
    starts = {e["scene_id"]: e["start"] for e in timeline["entries"]}
    out = []
    for c in proj.db.list("cue"):
        if c.get("lang") != lang:
            continue
        sid = c.get("scene_id")
        if sid:
            if sid not in starts:
                continue
            offset = starts[sid]
        else:
            offset = 0.0
        out.append({"id": c["id"], "scene_id": sid, "start": round(c["start"] + offset, 3),
                    "end": round(c["end"] + offset, 3), "text": c["text"], "origin": c.get("origin", "")})
    return sorted(out, key=lambda c: c["start"])


# -- validation ---------------------------------------------------------------
def normalize_options(opts: dict, proj: Project) -> dict:
    o = dict(opts or {})
    preset = o.get("preset") or "project"
    if preset == "project":
        w, h = proj.resolution()
    elif preset in PRESETS:
        w, h = PRESETS[preset]
    else:
        raise RenderError(f"Unknown export preset: {preset}")
    if o.get("preview"):
        w, h = int(w / 2) // 2 * 2, int(h / 2) // 2 * 2
    try:
        fps = int(o.get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30
    quality = o.get("quality") if o.get("quality") in QUALITY else "standard"
    if o.get("preview"):
        quality = "draft"
    out_dir = o.get("output_dir") or str(proj.root / "exports")
    name = (o.get("filename") or proj.id).strip()
    name = re.sub(r'[\\/:*?"<>|\x00]', "_", name)
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    return {
        "preset": preset, "width": w, "height": h, "fps": fps, "quality": quality,
        "subtitle_mode": o.get("subtitle_mode") if o.get("subtitle_mode") in ("none", "ar", "en", "bilingual") else "none",
        "sidecar_srt": bool(o.get("sidecar_srt", True)) and not o.get("preview"),
        "audio_level_db": max(-20.0, min(12.0, float(o.get("audio_level_db") or 0))),
        "normalize_audio": bool(o.get("normalize_audio", True)),
        "graphics": bool(o.get("graphics", True)),
        "film_grain": bool(o.get("film_grain", False)),
        "encoder": o.get("encoder") if o.get("encoder") in ("libx264", "h264_videotoolbox") else "libx264",
        "output_dir": out_dir, "filename": name, "overwrite": bool(o.get("overwrite")),
        "retain_intermediates": bool(o.get("retain_intermediates")), "preview": bool(o.get("preview")),
        "scene_ids": o.get("scene_ids") or None,
    }


def validate(proj: Project, opts: dict, settings: dict) -> dict:
    errors, warnings = [], []
    try:
        o = normalize_options(opts, proj)
    except (RenderError, ValueError) as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "options": None}
    scenes = proj.scenes()
    if o["scene_ids"]:
        scenes = [s for s in scenes if s["id"] in set(o["scene_ids"])]
    assets = _assets(proj)
    if not scenes:
        errors.append("The project has no scenes to render.")
    if o["fps"] not in FPS_ALLOWED:
        errors.append(f"Frame rate {o['fps']} is not supported (choose 24, 25, 30, 50 or 60).")
    if not which("ffmpeg"):
        errors.append("FFmpeg is not installed (brew install ffmpeg).")
    for i, s in enumerate(scenes, 1):
        label = f"Scene {i}" + (f" ({s.get('title')})" if s.get("title") else "")
        media = s.get("media") or {}
        for slot in ("visual_asset_id", "overlay_asset_id", "narration_asset_id", "sfx_asset_id"):
            aid = media.get(slot)
            if not aid:
                continue
            a = assets.get(aid)
            if not a:
                errors.append(f"{label}: linked {slot.split('_')[0]} asset no longer exists.")
                continue
            try:
                if not proj.path(a["path"]).exists():
                    errors.append(f"{label}: file missing on disk: {a['path']}")
            except PathError:
                errors.append(f"{label}: invalid asset path.")
            if a.get("status") == "rejected":
                warnings.append(f"{label}: uses an asset marked as rejected ({a.get('name')}).")
        if not media.get("visual_asset_id") and (s.get("card") or {}).get("type", "none") == "none":
            warnings.append(f"{label}: no visual - a plain dark background will be used.")
        dur, src = scene_duration(s, assets, settings)
        if dur <= 0:
            errors.append(f"{label}: invalid duration.")
        if src == "estimate":
            warnings.append(f"{label}: duration is estimated from text ({dur:.1f}s); add narration audio for exact timing.")
        if s.get("factual") and not s.get("claim_ids") and (s.get("narration_ar") or s.get("narration_en")):
            warnings.append(f"{label}: contains narration marked factual with no linked research claim.")
        sb = s.get("storyboard") or {}
        va = assets.get(media.get("visual_asset_id")) or {}
        if (sb.get("ai_reconstruction") or va.get("ai_generated")) and "reconstruction" not in \
                (s.get("on_screen_text", "") + (s.get("card") or {}).get("subtitle", "")).lower() and \
                "إعادة تصور" not in s.get("on_screen_text", ""):
            warnings.append(f"{label}: AI-generated reconstruction is not labelled on screen.")
    tl = proj.meta.get("timeline", {})
    for key in ("music_asset_id", "narration_asset_id"):
        aid = tl.get(key)
        if aid:
            a = assets.get(aid)
            if not a or not proj.path(a["path"]).exists():
                errors.append(f"Timeline {key.split('_')[0]} file is missing.")
    timeline = compute_timeline(scenes, assets, settings) if scenes else {"entries": [], "total": 0}
    if o["subtitle_mode"] != "none" or o["sidecar_srt"]:
        langs = ["ar", "en"] if o["subtitle_mode"] in ("bilingual", "none") else [o["subtitle_mode"]]
        for lang in langs:
            cues = absolute_cues(proj, timeline, lang)
            if not cues:
                if o["subtitle_mode"] in (lang, "bilingual"):
                    errors.append(f"No {lang.upper()} subtitles exist yet - generate them in Voice & Subtitles.")
                continue
            for p in subtitles.validate_cues(cues)[:5]:
                warnings.append(f"{lang.upper()} subtitles: {p}")
            if cues[-1]["end"] > timeline["total"] + 0.5:
                warnings.append(f"{lang.upper()} subtitles run past the end of the video.")
    out_dir = Path(o["output_dir"]).expanduser()
    if not out_dir.is_absolute():
        errors.append("Output folder must be an absolute path.")
    elif not out_dir.exists():
        errors.append(f"Output folder does not exist: {out_dir}")
    else:
        need = timeline["total"] * (12e6 / 8) * 3 + 200e6
        free = shutil.disk_usage(out_dir).free
        if free < need:
            errors.append(f"Not enough disk space: about {need/1e9:.1f} GB needed, {free/1e9:.1f} GB free.")
        if (out_dir / o["filename"]).exists() and not o["overwrite"]:
            warnings.append(f"{o['filename']} already exists - you will be asked before it is replaced.")
    if o["encoder"] == "h264_videotoolbox" and "videotoolbox" not in _encoders():
        errors.append("VideoToolbox encoder is not available in this FFmpeg build.")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "options": o,
            "timeline": timeline}


_ENC_CACHE: str | None = None


def _encoders() -> str:
    global _ENC_CACHE
    if _ENC_CACHE is None:
        try:
            _ENC_CACHE = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True,
                                        text=True, timeout=10).stdout
        except (OSError, MediaError, subprocess.SubprocessError):
            _ENC_CACHE = ""
    return _ENC_CACHE


# -- command construction -----------------------------------------------------
MOTION = {
    "none": ("1", "(iw-iw/zoom)*{cx}", "(ih-ih/zoom)*{cy}"),
    "zoom_in": ("1+0.12*on/{n}", "(iw-iw/zoom)*{cx}", "(ih-ih/zoom)*{cy}"),
    "zoom_out": ("1.12-0.12*on/{n}", "(iw-iw/zoom)*{cx}", "(ih-ih/zoom)*{cy}"),
    "pan_left": ("1.12", "(iw-iw/zoom)*(1-on/{n})", "(ih-ih/zoom)*{cy}"),
    "pan_right": ("1.12", "(iw-iw/zoom)*on/{n}", "(ih-ih/zoom)*{cy}"),
    "pan_up": ("1.12", "(iw-iw/zoom)*{cx}", "(ih-ih/zoom)*(1-on/{n})"),
    "pan_down": ("1.12", "(iw-iw/zoom)*{cx}", "(ih-ih/zoom)*on/{n}"),
}


def _enc_args(o: dict, final: bool) -> list[str]:
    preset, crf, _ = QUALITY[o["quality"]]
    if not final:
        # Intermediates: fast preset, higher quality to limit generation loss before the final encode.
        preset, crf = "veryfast", max(12, crf - 5)
    if final and o["encoder"] == "h264_videotoolbox":
        kbps = {"draft": 4000, "standard": 10000, "high": 16000}[o["quality"]]
        v = ["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k", "-profile:v", "high"]
    else:
        v = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high"]
    return v + ["-pix_fmt", "yuv420p", "-r", str(o["fps"]), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-ac", "2"]


def segment_command(spec: dict, o: dict, out: Path) -> list[str]:
    """Build ffmpeg args (without the binary) for one scene segment.

    spec keys: duration, visual(path|None), visual_type(image|video|graphics|None), motion, crop{x,y},
               narration(path|None), overlay(path|None), sfx(path|None), fade_in, fade_out, fade_duration
    """
    W, H, fps = o["width"], o["height"], o["fps"]
    d = float(spec["duration"])
    n = max(1, int(round(d * fps)))
    cx = min(1.0, max(0.0, float((spec.get("crop") or {}).get("x", 0.5))))
    cy = min(1.0, max(0.0, float((spec.get("crop") or {}).get("y", 0.5))))
    args: list[str] = []
    filters: list[str] = []
    idx = 0
    vt = spec.get("visual_type")
    if spec.get("visual") and vt in ("image", "graphics"):
        args += ["-i", str(spec["visual"])]
        ss = QUALITY[o["quality"]][2]
        sw, sh = int(W * ss) // 2 * 2, int(H * ss) // 2 * 2
        z, x, y = MOTION.get(spec.get("motion") or "none", MOTION["none"])
        fmt = dict(n=n, cx=cx, cy=cy)
        filters.append(
            f"[{idx}:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},setsar=1,"
            f"zoompan=z='{z.format(**fmt)}':x='{x.format(**fmt)}':y='{y.format(**fmt)}':d={n}:s={W}x{H}:fps={fps},"
            f"setsar=1,format=yuv420p[v0]")
    elif spec.get("visual") and vt == "video":
        args += ["-stream_loop", "-1", "-i", str(spec["visual"])]
        filters.append(
            f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:(iw-{W})*{cx}:(ih-{H})*{cy},setsar=1,fps={fps},trim=duration={d:.3f},"
            f"setpts=PTS-STARTPTS,format=yuv420p[v0]")
    else:
        args += ["-f", "lavfi", "-i", f"color=c=0x161412:s={W}x{H}:r={fps}:d={d:.3f}"]
        filters.append(f"[{idx}:v]format=yuv420p[v0]")
    idx += 1
    vlabel = "v0"
    if spec.get("overlay"):
        args += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(spec["overlay"])]
        filters.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                       f"format=rgba,colorchannelmixer=aa=0.35[ov]")
        filters.append(f"[{vlabel}][ov]overlay=0:0:shortest=1,format=yuv420p[v1]")
        vlabel = "v1"
        idx += 1
    post = []
    dim = float(spec.get("dim") or 0)
    if dim > 0:
        k = round(1 - dim, 3)
        post.append(f"colorchannelmixer=rr={k}:gg={k}:bb={k}")
    fd = float(spec.get("fade_duration") or 0.5)
    if spec.get("fade_in"):
        post.append(f"fade=t=in:st=0:d={fd:.3f}")
    if spec.get("fade_out"):
        post.append(f"fade=t=out:st={max(0, d - fd):.3f}:d={fd:.3f}")
    post.append(f"trim=duration={d:.3f},setpts=PTS-STARTPTS")
    filters.append(f"[{vlabel}]{','.join(post)}[vout]")

    alabels = []
    if spec.get("narration"):
        args += ["-i", str(spec["narration"])]
        lead = int(NARRATION_LEAD * 1000)
        filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"adelay=delays={lead}:all=1[narr]")
        alabels.append("[narr]")
        idx += 1
    if spec.get("sfx"):
        args += ["-i", str(spec["sfx"])]
        filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=-6dB[sfx]")
        alabels.append("[sfx]")
        idx += 1
    args += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    alabels.append(f"[{idx}:a]")
    afilt = []
    if spec.get("fade_in"):
        afilt.append(f"afade=t=in:st=0:d={min(fd, 0.3):.3f}")
    if spec.get("fade_out"):
        afilt.append(f"afade=t=out:st={max(0, d - fd):.3f}:d={fd:.3f}")
    afilt.append(f"apad,atrim=0:{d:.3f},asetpts=PTS-STARTPTS")
    filters.append(f"{''.join(alabels)}amix=inputs={len(alabels)}:duration=longest:normalize=0,"
                   f"{','.join(afilt)}[aout]")
    return args + ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
                   "-t", f"{d:.3f}"] + _enc_args(o, final=False) + [str(out)]


def assemble_command(segments: list[dict], o: dict, total: float, out: Path, ass_name: str | None = None,
                     music: dict | None = None, narration: dict | None = None) -> list[str]:
    """Join segments with cuts/crossfades, mix music/narration, burn ASS, encode final MP4.

    segments: [{path, duration, transition{type,duration}}] in order.
    music/narration: {path, volume_db, duck(bool)}.
    """
    args: list[str] = []
    for s in segments:
        args += ["-i", str(s["path"])]
    # concat outputs a 1/1000000 time base while encoded segments use e.g. 1/15360, and xfade
    # refuses mismatched inputs, so every segment is normalised to AVTB first.
    filters = [f"[{i}:v]fps={o['fps']},settb=AVTB,setpts=PTS-STARTPTS[s{i}v];"
               f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[s{i}a]" for i in range(len(segments))]
    v, a = "[s0v]", "[s0a]"
    length = float(segments[0]["duration"])
    for i in range(1, len(segments)):
        prev_t = segments[i - 1]["transition"]
        if prev_t.get("type") == "crossfade":
            td = float(prev_t["duration"])
            offset = length - td
            filters.append(f"{v}[s{i}v]xfade=transition=fade:duration={td:.3f}:offset={offset:.3f}[vx{i}]")
            filters.append(f"{a}[s{i}a]acrossfade=d={td:.3f}:c1=tri:c2=tri[ax{i}]")
            length = length + float(segments[i]["duration"]) - td
        else:
            filters.append(f"{v}{a}[s{i}v][s{i}a]concat=n=2:v=1:a=1[vx{i}][ax{i}]")
            length += float(segments[i]["duration"])
        v, a = f"[vx{i}]", f"[ax{i}]"
    idx = len(segments)
    mix = [a]
    if narration:
        args += ["-i", str(narration["path"])]
        filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"volume={float(narration.get('volume_db', 0)):.1f}dB,apad,atrim=0:{total:.3f}[gnarr]")
        mix.append("[gnarr]")
        idx += 1
    voice = "[voice]"
    if len(mix) > 1:
        filters.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[voice]")
    else:
        filters.append(f"{a}anull[voice]")
    final_a = voice
    if music:
        args += ["-stream_loop", "-1", "-i", str(music["path"])]
        fo = max(0.0, total - 2.0)
        filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},"
                       f"asetpts=PTS-STARTPTS,volume={float(music.get('volume_db', -18)):.1f}dB,"
                       f"afade=t=in:st=0:d=1.5,afade=t=out:st={fo:.3f}:d=2[mus]")
        if music.get("duck", True):
            filters.append("[voice]asplit=2[voicemix][voicesc]")
            filters.append("[mus][voicesc]sidechaincompress=threshold=0.04:ratio=6:attack=20:release=400[musd]")
            filters.append("[voicemix][musd]amix=inputs=2:duration=first:normalize=0[amixed]")
        else:
            filters.append("[voice][mus]amix=inputs=2:duration=first:normalize=0[amixed]")
        final_a = "[amixed]"
        idx += 1
    apost = [f"volume={o['audio_level_db']:.1f}dB"]
    if o.get("normalize_audio"):
        apost.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    apost.append("aresample=48000")
    filters.append(f"{final_a}{','.join(apost)}[aout]")
    vpost = ["noise=alls=6:allf=t"] if o.get("film_grain") else []
    if ass_name:
        vpost.append(f"ass={ass_name}")
    vpost.append("format=yuv420p")
    filters.append(f"{v}{','.join(vpost)}[vout]")
    return args + ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
                   "-t", f"{total:.3f}"] + _enc_args(o, final=True) + ["-movflags", "+faststart", str(out)]


def build_ass(proj: Project, timeline: dict, o: dict, scenes_by_id: dict) -> str | None:
    W, H = o["width"], o["height"]
    meta = proj.meta
    sstyle = meta.get("subtitle_style", {})
    gstyle = meta.get("graphics_style", {})
    events = []
    if o["graphics"]:
        for e in timeline["entries"]:
            s = scenes_by_id[e["scene_id"]]
            t0, t1 = e["start"], e["start"] + e["duration"]
            events += graphics.card_events(s.get("card") or {}, t0, t1, W, H, gstyle)
            if (s.get("card") or {}).get("type", "none") == "none":
                events += graphics.on_screen_text_events(s.get("on_screen_text", ""), t0, t1, W, H, gstyle)
    mode = o["subtitle_mode"]
    if mode == "ar":
        events += subtitles.subtitle_events(absolute_cues(proj, timeline, "ar"), "SubAR")
    elif mode == "en":
        events += subtitles.subtitle_events(absolute_cues(proj, timeline, "en"), "SubEN")
    elif mode == "bilingual":
        events += subtitles.bilingual_events(absolute_cues(proj, timeline, "ar"),
                                             absolute_cues(proj, timeline, "en"), sstyle, W)
    if not events:
        return None
    styles = subtitles.subtitle_styles(sstyle, W) + graphics.graphics_styles(gstyle, W)
    return subtitles.ass_header(W, H, styles) + "\n" + "\n".join(events) + "\n"


# -- execution ----------------------------------------------------------------
def run_with_progress(args: list[str], duration: float, on_progress, cwd: Path, job=None):
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1",
           "-nostats"] + args
    log.debug("ffmpeg %s", " ".join(cmd))
    # stderr goes to a temp file so a chatty FFmpeg can never block on a full pipe.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errf, \
            subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True, cwd=cwd) as proc:
        if job is not None:
            job.proc = proc
        for line in proc.stdout:
            if job is not None and job.cancelled:
                proc.terminate()
                break
            if line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    t = int(line.split("=", 1)[1]) / 1e6
                    on_progress(min(1.0, t / max(duration, 0.01)))
                except ValueError:
                    pass
        proc.wait()
        errf.seek(0)
        err = errf.read()
    if job is not None:
        job.proc = None
        if job.cancelled:
            raise Cancelled("Render cancelled")
    if proc.returncode != 0:
        lines = [l for l in err.strip().splitlines() if l.strip()]
        raise RenderError("FFmpeg failed: " + (lines[-1] if lines else f"exit code {proc.returncode}"))


def render(proj: Project, opts: dict, settings: dict, job=None) -> dict:
    report = lambda p, msg: job.update(progress=p, message=msg) if job else None
    v = validate(proj, opts, settings)
    if not v["ok"]:
        raise RenderError("Cannot render:\n- " + "\n- ".join(v["errors"]))
    o = v["options"]
    out_dir = Path(o["output_dir"]).expanduser()
    out = out_dir / o["filename"]
    if out.exists() and not o["overwrite"]:
        raise RenderConflict(f"{out.name} already exists in {out_dir}")
    scenes = proj.scenes()
    if o["scene_ids"]:
        scenes = [s for s in scenes if s["id"] in set(o["scene_ids"])]
    assets = _assets(proj)
    timeline = compute_timeline(scenes, assets, settings)
    by_id = {s["id"]: s for s in scenes}
    meta = proj.meta
    tlset = meta.get("timeline", {})
    work = proj.path(f"timeline/render_{time.strftime('%Y%m%d_%H%M%S')}")
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    rec = proj.db.put("render", {"status": "running", "output": str(out), "options": o, "started": started,
                                 "preview": o["preview"]})
    tmp_out = work / ("final_" + o["filename"])
    try:
        segs = []
        n = len(timeline["entries"])
        def apath(aid):
            a = assets.get(aid)
            return (proj.path(a["path"]), a) if a else (None, None)
        for i, e in enumerate(timeline["entries"]):
            if job and job.cancelled:
                raise Cancelled("Render cancelled")
            s = by_id[e["scene_id"]]
            media = s.get("media") or {}
            vis, va = apath(media.get("visual_asset_id"))
            prev_t = timeline["entries"][i - 1]["transition"]["type"] if i else None
            spec = {
                "duration": e["duration"], "visual": vis, "visual_type": va["type"] if va else None,
                "motion": s.get("motion", "zoom_in"), "crop": s.get("crop"),
                "narration": apath(media.get("narration_asset_id"))[0],
                "overlay": apath(media.get("overlay_asset_id"))[0], "sfx": apath(media.get("sfx_asset_id"))[0],
                "fade_in": prev_t == "fade" or (i == 0 and tlset.get("fade_in_start", True)),
                "fade_out": e["transition"]["type"] == "fade" or (i == n - 1 and tlset.get("fade_out_end", True)),
                "fade_duration": e["transition"]["duration"] if e["transition"]["type"] == "fade" else 0.5,
                "dim": graphics.FULL_FRAME_DIM.get((s.get("card") or {}).get("type")) if o["graphics"] else 0,
            }
            seg = work / f"seg_{i:03d}.mp4"
            base, span = 0.02 + 0.6 * i / n, 0.6 / n
            report(base, f"Rendering scene {i + 1} of {n}")
            run_with_progress(segment_command(spec, o, seg), e["duration"],
                              lambda p, b=base, sp=span, k=i: report(b + sp * p, f"Rendering scene {k + 1} of {n}"),
                              work, job)
            segs.append({"path": seg.name, "duration": e["duration"], "transition": e["transition"]})
        ass_text = build_ass(proj, timeline, o, by_id)
        ass_name = None
        if ass_text:
            (work / "overlay.ass").write_text(ass_text, "utf-8")
            ass_name = "overlay.ass"
        music = narration = None
        if tlset.get("music_asset_id") in assets:
            music = {"path": proj.path(assets[tlset["music_asset_id"]]["path"]),
                     "volume_db": tlset.get("music_volume_db", -18), "duck": tlset.get("music_duck", True)}
        if tlset.get("narration_asset_id") in assets:
            narration = {"path": proj.path(assets[tlset["narration_asset_id"]]["path"]),
                         "volume_db": tlset.get("narration_volume_db", 0)}
        report(0.63, "Assembling timeline, mixing audio, burning subtitles")
        run_with_progress(assemble_command(segs, o, timeline["total"], tmp_out.name, ass_name, music, narration),
                          timeline["total"], lambda p: report(0.63 + 0.35 * p, "Encoding final video"), work, job)
        if out.exists() and not o["overwrite"]:
            raise RenderConflict(f"{out.name} appeared while rendering; not replacing it")
        shutil.move(str(tmp_out), str(out))
        sidecars = []
        if o["sidecar_srt"]:
            for lang in ("ar", "en"):
                cues = absolute_cues(proj, timeline, lang)
                if cues:
                    p = out.with_suffix(f".{lang}.srt")
                    p.write_text(subtitles.to_srt(cues), "utf-8")
                    sidecars.append(str(p))
        rec.update(status="done", finished=time.time(), duration=timeline["total"], sidecars=sidecars,
                   elapsed=round(time.time() - started, 1))
        proj.db.put("render", rec)
        proj.log_event(f"render done -> {out}")
        report(1.0, "Done")
        return rec
    except Exception as e:
        rec.update(status="cancelled" if isinstance(e, Cancelled) else "failed", error=str(e), finished=time.time())
        proj.db.put("render", rec)
        proj.log_event(f"render failed: {e}")
        raise
    finally:
        if not o["retain_intermediates"]:
            shutil.rmtree(work, ignore_errors=True)
        else:
            rec["work_dir"] = str(work)
            proj.db.put("render", rec)


__all__ = ["compute_timeline", "validate", "render", "segment_command", "assemble_command", "absolute_cues",
           "scene_duration", "RenderError", "RenderConflict", "Cancelled", "PRESETS", "ASPECTS"]
