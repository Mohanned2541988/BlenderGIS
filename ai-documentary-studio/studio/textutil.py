"""Text helpers: Arabic detection, duration estimates, subtitle line breaking."""
from __future__ import annotations

import re
import unicodedata

ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
# Arabic diacritics (tashkeel) and tatweel do not change word count or width much.
TASHKEEL_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?\u061f\u06d4\u2026])\s+|\n+")
CLAUSE_SPLIT_RE = re.compile(r"(?<=[,\u060c;\u061b:])\s+")

RLM = "\u200f"


def is_arabic(text: str) -> bool:
    """True when the majority of letters in text are Arabic script."""
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return False
    ar = sum(1 for c in letters if ARABIC_RE.match(c))
    return ar / len(letters) >= 0.5


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def word_count(text: str) -> int:
    return len([w for w in TASHKEEL_RE.sub("", normalize(text)).split() if any(c.isalnum() for c in w)])


def estimate_duration(text: str, wpm: float, pause: float = 0.0) -> float:
    """Narration duration estimate in seconds. Real duration depends on voice and pauses."""
    words = word_count(text)
    if words == 0:
        return 0.0
    sentences = max(1, len([s for s in SENTENCE_SPLIT_RE.split(normalize(text)) if s.strip()]))
    return round(words / max(wpm, 40) * 60.0 + pause + 0.25 * (sentences - 1), 2)


def display_len(text: str) -> int:
    return len(TASHKEEL_RE.sub("", text))


def split_subtitle_chunks(text: str, max_chars: int = 42, max_lines: int = 2) -> list[str]:
    """Break narration into subtitle cues of at most max_lines lines of max_chars."""
    text = normalize(text)
    if not text:
        return []
    budget = max_chars * max_lines
    pieces: list[str] = []
    for sentence in [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]:
        if display_len(sentence) <= budget:
            pieces.append(sentence)
            continue
        for clause in [c.strip() for c in CLAUSE_SPLIT_RE.split(sentence) if c.strip()]:
            if display_len(clause) <= budget:
                pieces.append(clause)
            else:
                pieces.extend(_split_words(clause, budget))
    # Merge very short neighbours so cues do not flash by.
    merged: list[str] = []
    for p in pieces:
        if merged and display_len(merged[-1]) + 1 + display_len(p) <= budget and display_len(p) < max_chars * 0.5:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return [wrap_lines(m, max_chars) for m in merged]


def _split_words(text: str, budget: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if display_len(cand) > budget and cur:
            out.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def wrap_lines(text: str, max_chars: int = 42) -> str:
    """Balanced two-line wrap; returns text with '\n' between lines."""
    text = normalize(text).replace("\n", " ")
    if display_len(text) <= max_chars:
        return text
    words = text.split()
    best, best_score = text, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        la, lb = display_len(a), display_len(b)
        over = max(0, la - max_chars) + max(0, lb - max_chars)
        score = (over, abs(la - lb))
        if best_score is None or score < best_score:
            best, best_score = a + "\n" + b, score
    return best


def slugify(text: str, fallback: str = "project") -> str:
    text = unicodedata.normalize("NFKD", text or "")
    ascii_text = text.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")[:48]
    return slug or fallback
