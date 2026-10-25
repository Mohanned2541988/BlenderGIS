#!/usr/bin/env bash
# Smoke test: builds the sample project in a temporary folder and renders a real MP4 using only
# local tools (FFmpeg; macOS voice if installed). No paid APIs, does not touch your real projects.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ads_smoke.XXXXXX")"
export ADS_HOME="$TMP/home" ADS_PROJECTS_DIR="$TMP/projects"
python3 - <<'EOF'
import json, subprocess, sys, time
sys.path.insert(0, ".")
from studio import config, render, sample
t = time.time()
proj, voiced = sample.build()
print(f"sample project built in {time.time() - t:.1f}s (narration: {'macOS voice' if voiced else 'silent placeholder'})")
st = config.load_settings()
v = render.validate(proj, {"subtitle_mode": "bilingual", "quality": "draft"}, st)
assert v["ok"], v["errors"]
t = time.time()
rec = render.render(proj, {"subtitle_mode": "bilingual", "quality": "draft"}, st)
info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                                  rec["output"]], capture_output=True, text=True, check=True).stdout)
streams = {s["codec_type"]: s for s in info["streams"]}
assert streams["video"]["codec_name"] == "h264" and streams["audio"]["codec_name"] == "aac"
print(f"rendered {float(info['format']['duration']):.1f}s {streams['video']['width']}x{streams['video']['height']} "
      f"in {time.time() - t:.1f}s")
print("OUTPUT:", rec["output"])
print("SMOKE TEST PASSED")
EOF
echo "Temporary files: $TMP (delete when done)"
