"""Subtitle cues: SRT/WebVTT parsing and writing, estimated timing, ASS styling."""
from __future__ import annotations

import re

from .textutil import RLM, display_len, is_arabic, normalize, split_subtitle_chunks

TIME_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
ARROW_RE = re.compile(r"(\S+)\s*-->\s*(\S+)")


class SubtitleError(ValueError):
    pass


def fmt_time(t: float, sep: str = ",") -> str:
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def parse_time(s: str) -> float:
    s = s.strip()
    m = TIME_RE.fullmatch(s)
    if not m:
        # WebVTT allows mm:ss.mmm
        m2 = re.fullmatch(r"(\d{1,2}):(\d{1,2})[,.](\d{1,3})", s)
        if not m2:
            raise SubtitleError(f"bad timestamp: {s}")
        mm, ss, ms = m2.groups()
        return int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000
    h, mm, ss, ms = m.groups()
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict]:
    """Parse SRT or WebVTT text into cues [{start, end, text}]."""
    text = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []
    for block in blocks:
        lines = block.split("\n")
        if lines[0].startswith("NOTE"):
            continue
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        m = ARROW_RE.search(lines[idx])
        if not m:
            raise SubtitleError(f"bad timing line: {lines[idx]}")
        start, end = parse_time(m.group(1)), parse_time(m.group(2))
        body = "\n".join(l.strip() for l in lines[idx + 1:] if l.strip())
        body = re.sub(r"</?[^>]+>", "", body).replace(RLM, "")
        if end <= start:
            raise SubtitleError(f"cue ends before it starts at {m.group(1)}")
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    return cues


def validate_cues(cues: list[dict]) -> list[str]:
    problems = []
    prev_end = -1.0
    for i, c in enumerate(sorted(cues, key=lambda c: c["start"]), 1):
        if c["end"] <= c["start"]:
            problems.append(f"cue {i}: end is not after start")
        if c["start"] < prev_end - 0.001:
            problems.append(f"cue {i}: overlaps previous cue")
        if not normalize(c.get("text", "")):
            problems.append(f"cue {i}: empty text")
        for line in c.get("text", "").split("\n"):
            if display_len(line) > 60:
                problems.append(f"cue {i}: line longer than 60 characters")
                break
        prev_end = max(prev_end, c["end"])
    return problems


def _rtl(text: str) -> str:
    """Prefix Arabic lines with RLM so players pick right-to-left base direction."""
    return "\n".join(RLM + l if is_arabic(l) and not l.startswith(RLM) else l for l in text.split("\n"))


def to_srt(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(sorted(cues, key=lambda c: c["start"]), 1):
        out.append(f"{i}\n{fmt_time(c['start'])} --> {fmt_time(c['end'])}\n{_rtl(c['text'])}\n")
    return "\n".join(out)


def to_vtt(cues: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for c in sorted(cues, key=lambda c: c["start"]):
        out.append(f"{fmt_time(c['start'], '.')} --> {fmt_time(c['end'], '.')}")
        out.append(_rtl(c["text"]))
        out.append("")
    return "\n".join(out)


def bilingual(ar: list[dict], en: list[dict]) -> list[dict]:
    """Arabic cue timing with the overlapping English text underneath."""
    out = []
    for c in sorted(ar, key=lambda c: c["start"]):
        mid_hits = [e["text"] for e in en if c["start"] <= (e["start"] + e["end"]) / 2 < c["end"]]
        if not mid_hits:
            mid_hits = [e["text"] for e in en if e["start"] < c["end"] and e["end"] > c["start"]][:1]
        text = c["text"] + ("\n" + " ".join(t.replace("\n", " ") for t in mid_hits) if mid_hits else "")
        out.append({"start": c["start"], "end": c["end"], "text": text})
    return out


def distribute(text: str, start: float, end: float, max_chars=42, max_lines=2, min_len=1.0) -> list[dict]:
    """Spread text chunks over [start, end] proportionally to their length."""
    chunks = split_subtitle_chunks(text, max_chars, max_lines)
    if not chunks or end - start < 0.2:
        return []
    weights = [max(display_len(c), 8) for c in chunks]
    total = sum(weights)
    span = end - start
    cues, t = [], start
    for c, w in zip(chunks, weights):
        d = span * w / total
        cues.append({"start": round(t, 3), "end": round(t + d, 3), "text": c})
        t += d
    # Merge cues shorter than min_len into the previous one when possible.
    merged = []
    for c in cues:
        if merged and c["end"] - c["start"] < min_len:
            merged[-1]["end"] = c["end"]
            merged[-1]["text"] = merged[-1]["text"].replace("\n", " ") + " " + c["text"].replace("\n", " ")
        else:
            merged.append(c)
    for c in merged:
        c["end"] = round(c["end"] - 0.04, 3)  # tiny gap so players do not merge cues
    return merged


def shift(cues: list[dict], offset: float) -> list[dict]:
    return [dict(c, start=round(max(0.0, c["start"] + offset), 3), end=round(max(0.0, c["end"] + offset), 3))
            for c in cues]


# -- ASS (libass) -------------------------------------------------------------
def ass_color(hex_color: str, alpha: float = 0.0) -> str:
    """#RRGGBB + alpha (0 opaque .. 1 transparent) -> &HAABBGGRR."""
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = max(0, min(255, int(round(alpha * 255))))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_escape(text: str) -> str:
    text = text.replace("\\", "\u29f5").replace("{", "(").replace("}", ")")
    return text.replace("\n", "\\N")


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_header(width: int, height: int, styles: list[str]) -> str:
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])


def subtitle_styles(style: dict, width: int) -> list[str]:
    size = int(style.get("size", 58) * width / 1080)
    outline = style.get("outline", 3)
    box = bool(style.get("box", True))
    border_style = 3 if box else 1
    back = ass_color("#000000", 1 - float(style.get("box_opacity", 0.55)))
    prim = ass_color(style.get("color", "#FFFFFF"))
    outline_col = back if box else ass_color("#000000")
    align = {"bottom": 2, "middle": 5, "top": 8}.get(style.get("position", "bottom"), 2)
    mv = int(style.get("margin_v", 140) * width / 1080)
    mlr = int(70 * width / 1080)
    def line(name, font, sz):
        return (f"Style: {name},{font},{sz},{prim},{prim},{outline_col},{back},0,0,0,0,100,100,0,0,"
                f"{border_style},{outline if not box else max(1, outline // 2) * 4},{style.get('shadow', 0)},"
                f"{align},{mlr},{mlr},{mv},1")
    return [line("SubAR", style.get("font_ar", "Arial"), int(size * 1.08)),
            line("SubEN", style.get("font_en", "Arial"), size),
            line("SubENsmall", style.get("font_en", "Arial"), int(size * 0.78))]


def subtitle_events(cues: list[dict], style_name: str) -> list[str]:
    lines = []
    for c in cues:
        text = c["text"]
        if is_arabic(text.split("\n")[0]):
            text = _rtl(text)
        lines.append(f"Dialogue: 5,{ass_time(c['start'])},{ass_time(c['end'])},{style_name},,0,0,0,,"
                     f"{ass_escape(text)}")
    return lines


def bilingual_events(ar: list[dict], en: list[dict], style: dict, width: int) -> list[str]:
    """Arabic line above, English below, as two independent tracks."""
    mv = int(style.get("margin_v", 140) * width / 1080)
    size = int(style.get("size", 58) * width / 1080)
    lines = []
    for c in en:
        lines.append(f"Dialogue: 5,{ass_time(c['start'])},{ass_time(c['end'])},SubENsmall,,0,0,{mv},,"
                     f"{ass_escape(c['text'].replace(chr(10), ' '))}")
    ar_mv = mv + int(size * 1.6)
    for c in ar:
        lines.append(f"Dialogue: 5,{ass_time(c['start'])},{ass_time(c['end'])},SubAR,,0,0,{ar_mv},,"
                     f"{ass_escape(_rtl(c['text'].replace(chr(10), ' ')))}")
    return lines
