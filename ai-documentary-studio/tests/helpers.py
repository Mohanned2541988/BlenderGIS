import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = unittest.skipUnless(HAS_FFMPEG, "FFmpeg not installed")


class TempHome(unittest.TestCase):
    """Isolates settings and projects in a temporary folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ads_test_"))
        self._env = {k: os.environ.get(k) for k in ("ADS_HOME", "ADS_PROJECTS_DIR")}
        os.environ["ADS_HOME"] = str(self.tmp / "home")
        os.environ["ADS_PROJECTS_DIR"] = str(self.tmp / "projects")
        from studio import projects
        projects.close_all()

    def tearDown(self):
        from studio import projects
        projects.close_all()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_project(self, **kw):
        from studio import projects
        params = {"title": "Test film", "aspect_ratio": "9:16", "target_duration": 30}
        params.update(kw)
        return projects.create_project(params)

    def ffmpeg(self, *args):
        import subprocess
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)
