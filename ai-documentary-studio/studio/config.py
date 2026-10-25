"""Application-level settings (non-secret) and well-known paths."""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
from pathlib import Path

log = logging.getLogger("studio")

IS_MAC = platform.system() == "Darwin"


def app_support_dir() -> Path:
    override = os.environ.get("ADS_HOME")
    if override:
        p = Path(override).expanduser()
    elif IS_MAC:
        p = Path.home() / "Library" / "Application Support" / "AI Documentary Studio"
    else:
        p = Path.home() / ".config" / "ai-documentary-studio"
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULT_SETTINGS = {
    "projects_dir": "",  # empty -> default below
    "ui_language": "en",
    "speaking_rate_ar_wpm": 130,
    "speaking_rate_en_wpm": 150,
    "scene_pause_seconds": 0.6,
    "providers": {
        "llm": "template",          # template | anthropic | ollama
        "tts": "mock",              # mock | macos_say
        "stt": "estimate",          # estimate | whisper_cpp
        "search": "none",           # none | anthropic_web_search
        "image": "placeholder",     # placeholder (local cards only)
        "video": "none",
        "translation": "llm",       # llm | manual
    },
    "anthropic": {"model": "claude-opus-5-5", "effort": "medium"},
    "ollama": {"url": "http://127.0.0.1:11434", "model": "llama3.1:8b"},
    "whisper_cpp": {"binary": "whisper-cli", "model_path": "", "threads": 4},
    "tts_voices": {"ar": "", "en": ""},
}

_lock = threading.Lock()


def _settings_path() -> Path:
    return app_support_dir() / "settings.json"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    with _lock:
        p = _settings_path()
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError) as e:
                log.warning("settings unreadable, using defaults: %s", e)
        return _merge(DEFAULT_SETTINGS, data)


def save_settings(new: dict) -> dict:
    merged = _merge(load_settings(), new)
    # Never let anything that looks like a secret land in the plain settings file.
    for section in merged.values():
        if isinstance(section, dict):
            for key in list(section):
                if "key" in key.lower() or "secret" in key.lower() or "token" in key.lower():
                    section.pop(key)
    with _lock:
        p = _settings_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(p)
    return merged


def projects_dir() -> Path:
    env = os.environ.get("ADS_PROJECTS_DIR")
    configured = load_settings().get("projects_dir")
    if env:
        p = Path(env).expanduser()
    elif configured:
        p = Path(configured).expanduser()
    else:
        p = Path.home() / "Documents" / "AI Documentary Studio"
    p.mkdir(parents=True, exist_ok=True)
    return p
