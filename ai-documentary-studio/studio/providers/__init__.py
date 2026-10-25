"""Provider registry and status overview shown in Settings."""
from __future__ import annotations

from . import secrets
from .llm import LLMS, ProviderError, extract_json, get_llm
from .speech import STT, TTS, get_stt, get_tts

# Pricing changes; shown as guidance with a pointer to the official page.
COST_NOTES = {
    "template": "Free, offline.",
    "anthropic": "Paid per token. Opus 5.5 was listed at $4 / $20 per million input / output tokens; "
                 "web search is billed per search. A 60-second script is typically a few cents. "
                 "Check current prices at https://www.anthropic.com/pricing",
    "ollama": "Free, runs on your Mac. 8B models need about 6 GB of free RAM.",
    "mock": "Free, offline (silent placeholder).",
    "macos_say": "Free, offline. Voice quality depends on the installed voice.",
    "estimate": "Free, offline.",
    "whisper_cpp": "Free, offline. The small model (about 470 MB) is a good balance for Arabic on an M3.",
    "none": "No search provider - add sources manually.",
    "anthropic_web_search": "Uses your Anthropic key; search results are sent to and from Anthropic.",
    "placeholder": "Free, offline. Generates labelled placeholder frames; import your own images/videos.",
}

SEARCH = {
    "none": {"label": "Manual sources only", "external": False},
    "anthropic_web_search": {"label": "Claude web search (cloud)", "external": True},
}
IMAGE = {"placeholder": {"label": "Local placeholder frames (no AI)", "external": False}}
VIDEO = {"none": {"label": "Not configured - import video files", "external": False}}
TRANSLATION = {"llm": {"label": "Use the selected language model", "external": None},
               "manual": {"label": "Manual translation only", "external": False}}


def overview(settings: dict) -> dict:
    sel = settings["providers"]
    def entry(name, label, external, ok_why):
        ok, why = ok_why
        return {"name": name, "label": label, "external": external, "available": ok, "detail": why,
                "cost": COST_NOTES.get(name, "")}
    out = {
        "llm": [entry(p.name, p.label, p.external, p.available(settings)) for p in LLMS.values()],
        "tts": [entry(p.name, p.label, p.external, p.available(settings)) for p in TTS.values()],
        "stt": [entry(p.name, p.label, p.external, p.available(settings)) for p in STT.values()],
        "search": [entry(k, v["label"], v["external"],
                         (True, "") if k == "none" else LLMS["anthropic"].available(settings))
                   for k, v in SEARCH.items()],
        "image": [entry(k, v["label"], v["external"], (True, "")) for k, v in IMAGE.items()],
        "video": [entry(k, v["label"], v["external"], (True, "")) for k, v in VIDEO.items()],
        "translation": [entry(k, v["label"], v["external"], (True, "")) for k, v in TRANSLATION.items()],
    }
    return {"selected": sel, "providers": out, "secrets": secrets.status()}


__all__ = ["overview", "get_llm", "get_tts", "get_stt", "ProviderError", "extract_json", "secrets"]
