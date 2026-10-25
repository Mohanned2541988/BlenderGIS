import json
import subprocess
import threading
import urllib.error
import urllib.request

from helpers import TempHome, needs_ffmpeg


class Providers(TempHome):
    def test_extract_json_variants(self):
        from studio.providers import ProviderError, extract_json
        self.assertEqual(extract_json('```json\n{"scenes": [1, 2]}\n```'), {"scenes": [1, 2]})
        self.assertEqual(extract_json('Here: [{"a": "x]y"}] done'), [{"a": "x]y"}])
        self.assertEqual(extract_json('{"a": [1]} trailing [2]'), {"a": [1]})
        with self.assertRaises(ProviderError):
            extract_json("no json here")

    def test_template_provider_refuses_prose_and_overview(self):
        from studio import config
        from studio.providers import ProviderError, get_llm, overview
        st = config.load_settings()
        with self.assertRaises(ProviderError):
            get_llm(st).complete("s", "p", st)
        ov = overview(st)
        self.assertEqual(set(ov["providers"]), {"llm", "tts", "stt", "search", "image", "video", "translation"})
        anth = next(p for p in ov["providers"]["llm"] if p["name"] == "anthropic")
        self.assertTrue(anth["external"])
        self.assertIn("ANTHROPIC_API_KEY", ov["secrets"])

    def test_unknown_secret_names_rejected(self):
        from studio.providers import secrets
        self.assertEqual(secrets.get_secret("PATH"), (None, None))
        with self.assertRaises(ValueError):
            secrets.set_secret("PATH", "x")


class OllamaAdapter(TempHome):
    """Ollama's native /api/tags and /api/chat endpoints, served by a local fake."""

    def test_availability_and_chat(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from studio import config
        from studio.providers.llm import LLMS
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"models": [{"name": "llama3.1:8b"}]})

            def do_POST(self):
                seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self._send({"message": {"role": "assistant", "content": '{"ok": true}'}})

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            st = config.save_settings({"ollama": {"url": f"http://127.0.0.1:{srv.server_address[1]}"}})
            ok, _ = LLMS["ollama"].available(st)
            self.assertTrue(ok)
            self.assertEqual(LLMS["ollama"].complete("sys", "hi", st, want_json=True), '{"ok": true}')
            self.assertEqual(seen[0]["format"], "json")
            self.assertEqual(seen[0]["messages"][0], {"role": "system", "content": "sys"})
            st = config.save_settings({"ollama": {"model": "missing-model"}})
            self.assertFalse(LLMS["ollama"].available(st)[0])
        finally:
            srv.shutdown()
            srv.server_close()


@needs_ffmpeg
class Media(TempHome):
    def test_import_probe_thumbnail_and_missing(self):
        from studio import media
        p = self.make_project()
        img = self.tmp / "in.png"
        self.ffmpeg("-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1", str(img))
        a = media.import_path(p, str(img))
        self.assertEqual((a["type"], a["info"]["width"]), ("image", 640))
        self.assertTrue(p.path(a["thumb"]).exists())
        self.assertTrue(img.exists())  # original untouched
        bad = self.tmp / "fake.png"
        bad.write_text("not an image")
        with self.assertRaises(media.MediaError):
            media.import_path(p, str(bad))
        with self.assertRaises(media.MediaError):
            media.import_path(p, "relative.png")
        with self.assertRaises(media.MediaError):
            media.import_bytes(p, "script.sh", iter([b"x"]))
        self.assertEqual(p.missing_assets(), [])
        p.path(a["path"]).unlink()
        self.assertEqual([m["id"] for m in p.missing_assets()], [a["id"]])


@needs_ffmpeg
class Server(TempHome):
    def setUp(self):
        super().setUp()
        from studio.server import make_server
        self.srv = make_server(0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        h = {"X-Studio": "1", "Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_security_guards(self):
        self.assertEqual(self.req("POST", "/api/projects", {}, {"X-Studio": ""})[0], 403)
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "attacker.example"})[0], 403)
        code, p = self.req("POST", "/api/projects", {"title": "API"})
        self.assertEqual(code, 200)
        self.assertIn(self.req("GET", f"/media/{p['id']}/../../etc/passwd")[0], (400, 404))
        self.assertEqual(self.req("GET", "/api/projects/..%2Fx")[0], 404)

    def test_scene_api_and_conflicts(self):
        _, p = self.req("POST", "/api/projects", {"title": "API"})
        base = f"/api/projects/{p['id']}"
        _, s1 = self.req("POST", base + "/scenes", {})
        _, s2 = self.req("POST", base + "/scenes", {"after_id": s1["id"], "scene": {"title": "Second"}})
        code, _ = self.req("PUT", base + f"/scenes/{s1['id']}", {"duration": "abc"})
        self.assertEqual(code, 400)
        _, upd = self.req("PUT", base + f"/scenes/{s1['id']}", {"storyboard": {"shot": "wide"}, "evil": 1})
        self.assertEqual(upd["storyboard"]["shot"], "wide")
        self.assertNotIn("evil", upd)
        self.assertEqual(self.req("POST", base + "/scenes/reorder", {"ids": [s2["id"]]})[0], 400)
        self.assertEqual(self.req("POST", base + "/script/generate", {})[1]["code"], "confirm_replace")
        self.assertEqual(self.req("POST", base + "/claims", {"text": " "})[0], 400)


@needs_ffmpeg
class SmokeRender(TempHome):
    """End-to-end: local media + subtitles -> real MP4, no paid APIs."""

    def test_render_short_video(self):
        from studio import config, media, render, workflows
        from studio.projects import default_scene
        st = config.load_settings()
        p = self.make_project(aspect_ratio="1:1")
        img, wav = self.tmp / "img.png", self.tmp / "n.wav"
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=s=800x600", "-frames:v", "1", str(img))
        self.ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=1.5", str(wav))
        vis, narr = media.import_path(p, str(img)), media.import_path(p, str(wav))
        p.db.put("scene", default_scene(narration_ar="مرحبا بكم في هذا الفيلم.", narration_en="Welcome to this film.",
                                        media={"visual_asset_id": vis["id"], "narration_asset_id": narr["id"]},
                                        transition={"type": "crossfade", "duration": 0.4},
                                        card={"type": "title", "title": "عنوان", "subtitle": "Title"}))
        p.db.put("scene", default_scene(narration_en="The end.", duration=1.5, motion="zoom_out",
                                        media={"visual_asset_id": vis["id"]}))
        counts = workflows.cues_from_script(p, st)
        self.assertGreater(counts["ar"], 0)
        rec = render.render(p, {"quality": "draft", "fps": 24, "subtitle_mode": "bilingual", "preset": "square"}, st)
        out = rec["output"]
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                          "-show_format", out], capture_output=True, text=True).stdout)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual(kinds["video"]["codec_name"], "h264")
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (1080, 1080))
        self.assertEqual(kinds["audio"]["codec_name"], "aac")
        self.assertAlmostEqual(float(info["format"]["duration"]), rec["duration"], delta=0.15)
        self.assertEqual(len(rec["sidecars"]), 2)
        with self.assertRaises(render.RenderConflict):
            render.render(p, {"quality": "draft", "preset": "square"}, st)
        self.assertEqual(list((p.root / "timeline").glob("render_*")), [])  # intermediates cleaned
