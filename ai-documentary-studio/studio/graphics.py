"""Cinematic graphics templates rendered through libass (handles Arabic shaping/RTL).

Every card is a set of ASS events drawn over the scene's visual for its duration.
"""
from __future__ import annotations

from .subtitles import ass_color, ass_escape, ass_time
from .textutil import RLM, is_arabic, wrap_lines

CARD_TYPES = {
    "none": "No graphic",
    "title": "Documentary title card",
    "chapter": "Chapter heading",
    "lower_third": "Lower third",
    "source": "Source card",
    "quote": "Quotation card",
    "pull_quote": "Pull quote",
    "timeline": "Timeline marker",
    "map": "Map label card",
    "statistic": "Statistic card",
    "end": "End card",
}


# Cards that dim the whole frame. The renderer applies this as a colour multiply on the scene
# segment, which is far cheaper than blending a full-frame translucent ASS shape on every frame.
FULL_FRAME_DIM = {"title": 0.45, "end": 0.45, "statistic": 0.5}


def graphics_styles(g: dict, width: int) -> list[str]:
    k = width / 1080
    text = ass_color(g.get("text", "#F4F1EA"))
    accent = ass_color(g.get("accent", "#E8B04B"))
    shadow = ass_color("#000000", 0.35)
    def st(name, font, size, colour, bold=0, outline=0):
        return (f"Style: {name},{font},{int(size * k)},{colour},{colour},{shadow},{shadow},{bold},0,0,0,100,100,"
                f"0,0,1,{outline},2,5,0,0,0,1")
    return [
        st("GTitle", g.get("font_title", "Georgia"), g.get("title_size", 96), text, 1),
        st("GBody", g.get("font_body", "Arial"), g.get("body_size", 54), text),
        st("GAccent", g.get("font_body", "Arial"), int(g.get("body_size", 54) * 0.62), accent, 1),
        st("GTitleAR", g.get("font_ar", "Arial"), int(g.get("title_size", 96) * 0.95), text, 1),
        st("GBodyAR", g.get("font_ar", "Arial"), int(g.get("body_size", 54) * 1.05), text),
        "Style: GBox,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
    ]


def _t(text: str, width_chars: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    lines = []
    for para in text.split("\n"):
        wrapped = wrap_lines(para, width_chars) if len(para) > width_chars else para
        lines.extend(wrapped.split("\n"))
    out = "\n".join(lines)
    if is_arabic(out):
        out = "\n".join(RLM + l for l in out.split("\n"))
    return ass_escape(out)


def _rect(t0, t1, x, y, w, h, color, alpha, fade=(250, 250), layer=1):
    a = f"&H{int(alpha * 255):02X}&"
    return (f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},GBox,,0,0,0,,"
            f"{{\\an7\\pos({int(x)},{int(y)})\\p1\\bord0\\shad0\\1c{color}\\1a{a}\\fad({fade[0]},{fade[1]})}}"
            f"m 0 0 l {int(w)} 0 {int(w)} {int(h)} 0 {int(h)}{{\\p0}}")


def _txt(t0, t1, style, an, x, y, text, extra="", fade=(300, 250), layer=3):
    if not text:
        return None
    # libass honours whichever of \pos / \move comes first, so drop \pos when animating.
    pos = "" if "\\move(" in extra else f"\\pos({int(x)},{int(y)})"
    return (f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,"
            f"{{\\an{an}{pos}\\fad({fade[0]},{fade[1]}){extra}}}{text}")


def card_events(card: dict, t0: float, t1: float, width: int, height: int, g: dict) -> list[str]:
    ctype = (card or {}).get("type", "none")
    if ctype == "none" or t1 - t0 < 0.3:
        return []
    title, sub = card.get("title", ""), card.get("subtitle", "")
    ar = is_arabic(title + " " + sub) if card.get("lang", "auto") == "auto" else card.get("lang") == "ar"
    TS, BS = ("GTitleAR", "GBodyAR") if ar else ("GTitle", "GBody")
    k = width / 1080
    m = g.get("margin", 90) * k
    cx, cy = width / 2, height / 2
    accent = ass_color(g.get("accent", "#E8B04B"))
    panel = ass_color(g.get("panel", "#141414"))
    chars = 22 if width < height else 34
    ev: list[str | None] = []
    # Gentle slide-in for text.
    slide = lambda x0, y0, x1, y1: f"\\move({int(x0)},{int(y0)},{int(x1)},{int(y1)},0,450)"

    if ctype in ("title", "end"):
        ev.append(_rect(t0, t1, cx - 70 * k, cy - 40 * k - 150 * k, 140 * k, 8 * k, accent, 0))
        ev.append(_txt(t0, t1, TS, 5, cx, cy - 40 * k, _t(title, chars), slide(cx, cy, cx, cy - 40 * k)))
        ev.append(_txt(t0 + 0.3, t1, BS, 8, cx, cy + 90 * k, _t(sub, chars + 8)))
    elif ctype == "chapter":
        ev.append(_rect(t0, t1, 0, cy - 170 * k, width, 340 * k, panel, 0.25, layer=0))
        ev.append(_txt(t0, t1, "GAccent", 5, cx, cy - 95 * k, _t(sub or "", chars + 10)))
        ev.append(_txt(t0, t1, TS, 5, cx, cy + 20 * k, _t(title, chars)))
    elif ctype == "lower_third":
        y = height - (560 if width < height else 300) * k
        x = width - m if ar else m
        an_t, an_s = (9, 9) if ar else (7, 7)
        bar_x = width - m + 18 * k if ar else m - 30 * k
        ev.append(_rect(t0, t1, bar_x, y, 10 * k, 170 * k, accent, 0))
        ev.append(_txt(t0, t1, TS, an_t, x, y, _t(title, chars + 6), "\\fscx70\\fscy70"))
        ev.append(_txt(t0 + 0.2, t1, BS, an_s, x, y + 95 * k, _t(sub, chars + 12), "\\fscx85\\fscy85"))
    elif ctype in ("quote", "pull_quote"):
        ev.append(_rect(t0, t1, m * 0.6, cy - 380 * k, width - m * 1.2, 760 * k, panel, 0.2, layer=0))
        ev.append(_txt(t0, t1, "GTitle", 5, cx, cy - 300 * k, "\u201c", "\\fscx200\\fscy200\\1c" + accent))
        ev.append(_txt(t0, t1, BS if ctype == "quote" else TS, 5, cx, cy, _t(title, chars + 4),
                       "\\fscx92\\fscy92" if ctype == "pull_quote" else ""))
        ev.append(_txt(t0 + 0.4, t1, "GAccent", 5, cx, cy + 290 * k, _t(("\u2014 " + sub) if sub else "", chars + 10)))
    elif ctype == "source":
        y = height - (520 if width < height else 260) * k
        ev.append(_rect(t0, t1, m * 0.6, y - 30 * k, width - m * 1.2, 200 * k, panel, 0.15, layer=0))
        ev.append(_txt(t0, t1, "GAccent", 5, cx, y + 20 * k, "SOURCE" if not ar else ass_escape(RLM + "\u0627\u0644\u0645\u0635\u062f\u0631")))
        ev.append(_txt(t0, t1, BS, 5, cx, y + 100 * k, _t(title, chars + 14), "\\fscx80\\fscy80"))
    elif ctype == "timeline":
        y = height * 0.72
        ev.append(_rect(t0, t1, m, y, width - 2 * m, 6 * k, accent, 0))
        ev.append(_rect(t0, t1, cx - 18 * k, y - 15 * k, 36 * k, 36 * k, accent, 0))
        ev.append(_txt(t0, t1, TS, 2, cx, y - 50 * k, _t(title, chars)))
        ev.append(_txt(t0 + 0.3, t1, BS, 8, cx, y + 50 * k, _t(sub, chars + 8)))
    elif ctype == "map":
        ev.append(_rect(t0, t1, m, m * 2, 16 * k, 16 * k, accent, 0))
        x = width - m if ar else m * 1.6
        ev.append(_txt(t0, t1, TS, 9 if ar else 7, x, m * 2 - 30 * k, _t(title, chars), "\\fscx75\\fscy75"))
        ev.append(_txt(t0, t1, "GAccent", 9 if ar else 7, x, m * 2 + 60 * k, _t(sub, chars + 10)))
    elif ctype == "statistic":
        ev.append(_txt(t0, t1, "GTitle", 5, cx, cy - 60 * k, _t(title, 12),
                       "\\fscx230\\fscy230\\1c" + accent + "\\t(0,500,\\fscx200\\fscy200)"))
        ev.append(_txt(t0 + 0.3, t1, BS, 8, cx, cy + 120 * k, _t(sub, chars + 8)))
    return [e for e in ev if e]


def on_screen_text_events(text: str, t0: float, t1: float, width: int, height: int, g: dict) -> list[str]:
    """Simple kinetic caption near the top for scene on-screen text."""
    if not (text or "").strip():
        return []
    ar = is_arabic(text)
    k = width / 1080
    style = "GBodyAR" if ar else "GBody"
    y = (300 if width < height else 140) * k
    return [e for e in [
        _rect(t0, t1, 0, y - 100 * k, width, 190 * k, ass_color(g.get("panel", "#141414")), 0.45, layer=0),
        _rect(t0, t1, width / 2 - 60 * k, y - 70 * k, 120 * k, 6 * k, ass_color(g.get("accent", "#E8B04B")), 0),
        _txt(t0, t1, style, 8, width / 2, y - 40 * k, _t(text, 26 if width < height else 40),
             f"\\fscx90\\fscy90\\move({int(width/2)},{int(y)},{int(width/2)},{int(y-40*k)},0,400)"),
    ] if e]
