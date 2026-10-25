"""Exercises the Claude adapter against a local fake API (no network, no key). Skipped without the SDK."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import TempHome

try:
    import anthropic  # noqa: F401
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def message(content, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}}


class Fake(BaseHTTPRequestHandler):
    requests = []
    reply = None
    router = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        reply = Fake.router(body) if Fake.router else Fake.reply
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(HAS_SDK, "anthropic SDK not installed (optional)")
class AnthropicAdapter(TempHome):
    def setUp(self):
        super().setUp()
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.old = {k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")}
        os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        Fake.requests.clear()
        Fake.router = None

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        super().tearDown()

    def settings(self):
        from studio import config
        return config.save_settings({"providers": {"llm": "anthropic"}})

    def test_script_generation_request_and_parsing(self):
        from studio import scriptwriter
        p = self.make_project(target_duration=20)
        src = p.db.put("source", {"title": "Archive", "url": "https://example.org/a"})
        scenes = [{"title": "Hook", "purpose": "hook", "narration_ar": "نص", "narration_en": "Text",
                   "source_ids": [src["id"], "src_invented"], "transition": "crossfade", "factual": True}]
        Fake.reply = message([{"type": "text", "text": "```json\n" + json.dumps({"scenes": scenes}) + "\n```"}])
        r = scriptwriter.generate_script(p, self.settings(), replace=True)
        self.assertTrue(r["ai"])
        s = p.scenes()[0]
        self.assertEqual(s["source_ids"], [src["id"]])  # unknown ids dropped
        self.assertEqual(s["transition"]["type"], "crossfade")
        req = Fake.requests[-1]
        self.assertEqual(req["path"].split("?")[0], "/v1/messages")
        self.assertEqual(req["body"]["model"], "claude-opus-5-5")
        self.assertEqual(req["body"]["output_config"], {"effort": "medium"})
        self.assertEqual(req["body"]["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", req["headers"].get("anthropic-beta", ""))
        self.assertIn("Archive", req["body"]["messages"][0]["content"])

    def test_refusal_is_reported(self):
        from studio.providers import ProviderError, get_llm
        Fake.reply = message([], stop="refusal")
        st = self.settings()
        with self.assertRaises(ProviderError):
            get_llm(st).complete("s", "p", st)

    def test_web_search_keeps_only_real_result_urls(self):
        from studio.providers.llm import LLMS
        Fake.reply = message([
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "x"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                {"type": "web_search_result", "url": "https://real.example/a", "title": "Real A",
                 "encrypted_content": "e", "page_age": "2024-01-01"}]},
            {"type": "text", "text": json.dumps([
                {"url": "https://real.example/a", "title": "Real A", "summary": "Says A."},
                {"url": "https://invented.example/b", "title": "Invented", "summary": "Never searched."}])},
        ])
        out = LLMS["anthropic"].web_search("x", self.settings())
        self.assertEqual([r["url"] for r in out], ["https://real.example/a"])
        self.assertEqual(out[0]["summary"], "Says A.")
        self.assertEqual(Fake.requests[-1]["body"]["tools"][0]["type"], "web_search_20260209")


    def test_autopilot_topic_to_video(self):
        """Topic in -> planned, researched, scripted, voiced, subtitled, rendered project out."""
        import shutil
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg not installed")
        from studio import autopilot

        def route(body):
            system = json.dumps(body.get("system", ""))
            user = json.dumps(body["messages"][0]["content"], ensure_ascii=False)
            if body.get("tools"):
                return message([
                    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "q"}},
                    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                        {"type": "web_search_result", "url": "https://real.example/aral", "title": "Aral Sea",
                         "encrypted_content": "e", "page_age": "2023"}]},
                    {"type": "text", "text": json.dumps([{"url": "https://real.example/aral", "title": "Aral Sea",
                                                         "summary": "The lake shrank after rivers were diverted."}])}])
            if "plan short documentary" in system:
                return message([{"type": "text", "text": json.dumps({
                    "title_en": "The Vanishing Sea", "title_ar": "البحر الذي اختفى", "main_question": "Why did it shrink?",
                    "audience": "Students", "tone": "calm", "visual_style": "archival_investigation",
                    "queries": ["Aral Sea shrinking causes"]})}])
            if "extract checkable factual claims" in system:
                sid = user.split("id=")[1].split(" ")[0]
                return message([{"type": "text", "text": json.dumps([
                    {"text": "The Aral Sea shrank after its rivers were diverted.", "source_ids": [sid]},
                    {"text": "Invented claim with no source.", "source_ids": ["src_fake"]}])}])
            sid = user.split("id=")[1].split(" ")[0]
            return message([{"type": "text", "text": json.dumps({"scenes": [
                {"title": "Hook", "purpose": "hook", "narration_ar": "كان هنا بحر كامل.", "narration_en": "A whole sea was here.",
                 "on_screen_text": "", "transition": "cut", "factual": False},
                {"title": "Cause", "purpose": "evidence", "narration_ar": "حُوّلت مياه الأنهار للزراعة.",
                 "narration_en": "River water was diverted for farming.", "on_screen_text": "1960",
                 "source_ids": [sid], "transition": "crossfade", "factual": True},
                {"title": "End", "purpose": "conclusion", "narration_ar": "فماذا نتعلم؟", "narration_en": "So what do we learn?",
                 "transition": "cut", "factual": False}]})}])

        Fake.router = route
        st = self.settings()
        r = autopilot.run({"topic": "Aral Sea", "target_duration": 20, "aspect_ratio": "1:1", "quality": "draft",
                           "web_search": True}, st)
        self.assertTrue(r["ai_script"])
        self.assertEqual((r["sources"], r["claims"], r["scenes"]), (1, 1, 3))  # invented claim dropped
        from studio import projects
        p = projects.get_project(r["project_id"])
        self.assertEqual(p.meta["title"], "البحر الذي اختفى — The Vanishing Sea")
        self.assertEqual(p.meta["visual_style"], "archival_investigation")
        self.assertTrue(all(c["status"] == "unverified" for c in p.db.list("claim")))
        self.assertTrue(all(s["media"]["visual_asset_id"] and s["media"]["narration_asset_id"] for s in p.scenes()))
        self.assertEqual(p.scenes()[0]["card"]["type"], "title")
        self.assertEqual(p.scenes()[1]["card"]["type"], "statistic")
        self.assertTrue(p.db.list("cue"))
        self.assertTrue(r["render"] and __import__("os").path.exists(r["render"]["output"]))
