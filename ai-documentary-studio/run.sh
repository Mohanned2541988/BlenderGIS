#!/usr/bin/env bash
# Launch AI Documentary Studio: checks dependencies, starts the local server, opens the browser.
# Usage: ./run.sh [--port 8765] [--no-browser]
set -euo pipefail
cd "$(dirname "$0")"

# GUI-launched shells on macOS often miss Homebrew's paths.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9+ is required. Install it with:  xcode-select --install   (or: brew install python)"
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg is not installed. It is needed for media import and rendering."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "Install it now with Homebrew (brew install ffmpeg)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then brew install ffmpeg; fi
  else
    echo "Install Homebrew from https://brew.sh, then run: brew install ffmpeg"
  fi
fi

exec "$PY" -m studio "$@"
