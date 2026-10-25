"""Entry point: python3 -m studio [--port 8765] [--no-browser]"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import APP_NAME, APP_VERSION, config, media
from .projects import close_all
from .server import make_server, setup_logging


def main(argv=None):
    if sys.version_info < (3, 9):
        print("AI Documentary Studio needs Python 3.9 or newer.")
        return 1
    ap = argparse.ArgumentParser(prog="studio", description=APP_NAME)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    setup_logging()
    feats = media.ffmpeg_features()
    if not feats.get("available"):
        print("WARNING: FFmpeg was not found. Install it with:  brew install ffmpeg")
        print("         The app will open, but media import and rendering will not work.")
    elif not feats.get("libass"):
        print("WARNING: this FFmpeg build lacks libass - burned-in subtitles and graphics will fail.")
    srv = None
    for port in range(args.port, args.port + 20):
        try:
            srv = make_server(port)
            break
        except OSError:
            continue
    if srv is None:
        print("Could not find a free port.")
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Projects folder: {config.projects_dir()}")
    print(f"Open {url}  (press Ctrl+C to quit)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        srv.server_close()
        close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
