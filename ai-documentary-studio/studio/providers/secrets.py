"""Credential lookup: environment variables first, then the macOS Keychain.

Secrets are never written to settings.json or project files, and never logged.
"""
from __future__ import annotations

import os
import subprocess

from ..config import IS_MAC

SERVICE = "ai-documentary-studio"
KNOWN = {
    "ANTHROPIC_API_KEY": "Anthropic API key (Claude: scriptwriting, translation, research search)",
}


def get_secret(name: str) -> tuple[str | None, str | None]:
    """Return (value, where) - where is 'env' or 'keychain'."""
    if name not in KNOWN:
        return None, None
    v = os.environ.get(name)
    if v:
        return v.strip(), "env"
    if IS_MAC:
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), "keychain"
        except (OSError, subprocess.SubprocessError):
            pass
    return None, None


def set_secret(name: str, value: str) -> str:
    if name not in KNOWN:
        raise ValueError(f"Unknown credential {name}")
    if not IS_MAC:
        raise RuntimeError("Secure storage is only available on macOS (Keychain). "
                           f"Set the environment variable {name} instead.")
    value = (value or "").strip()
    if not value or "\n" in value:
        raise ValueError("Empty or invalid value")
    # -U updates an existing item. The value is passed as an argument to the system tool;
    # it is not written to disk or logs by this app.
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("Keychain refused to store the credential: " + r.stderr.strip())
    return "keychain"


def delete_secret(name: str) -> bool:
    if not IS_MAC or name not in KNOWN:
        return False
    r = subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def status() -> dict:
    return {name: {"label": label, "configured": get_secret(name)[0] is not None, "source": get_secret(name)[1]}
            for name, label in KNOWN.items()}
