#!/usr/bin/env bash
# Runs the automated test suite (standard-library unittest; FFmpeg-dependent tests skip if it is missing).
set -euo pipefail
cd "$(dirname "$0")/../tests"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec python3 -m unittest discover -s . -p "test_*.py" -v
