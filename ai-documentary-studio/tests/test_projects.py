import json
import sqlite3

from helpers import TempHome


class ProjectLifecycle(TempHome):
    def test_create_writes_structure_and_manifest(self):
        p = self.make_project(title="مدينة مفقودة Lost City")
        for d in ("database", "research", "scripts", "storyboard", "media/images", "media/videos", "media/audio",
                  "media/music", "media/graphics", "subtitles", "timeline", "exports", "logs"):
            self.assertTrue((p.root / d).is_dir(), d)
        m = json.loads((p.root / "project.json").read_text("utf-8"))
        self.assertEqual(m["app"], "AI Documentary Studio")
        self.assertEqual(m["schema_version"], 1)
        self.assertEqual(p.meta["title"], "مدينة مفقودة Lost City")
        self.assertEqual(p.resolution(), (1080, 1920))

    def test_invalid_inputs_get_safe_defaults(self):
        p = self.make_project(aspect_ratio="4:3", target_duration="abc", visual_style="nope")
        self.assertEqual(p.meta["aspect_ratio"], "9:16")
        self.assertEqual(p.meta["target_duration"], 60.0)
        self.assertEqual(p.meta["visual_style"], "editorial_collage")

    def test_unique_folders_for_same_title(self):
        a, b = self.make_project(title="Same"), self.make_project(title="Same")
        self.assertNotEqual(a.root, b.root)

    def test_reopen_after_close_preserves_state(self):
        from studio import projects
        p = self.make_project()
        sc = p.db.put("scene", projects.default_scene(title="One", narration_ar="نص"))
        p.update_meta({"notes": "research notes"})
        pid = p.id
        projects.close_all()
        q = projects.get_project(pid)
        self.assertEqual(q.scenes()[0]["title"], "One")
        self.assertEqual(q.scenes()[0]["id"], sc["id"])
        self.assertEqual(q.meta["notes"], "research notes")

    def test_recovery_without_clean_shutdown(self):
        """Committed writes survive when the process dies without closing the DB."""
        from studio import projects
        from studio.db import ProjectDB
        p = self.make_project()
        p.db.put("scene", projects.default_scene(title="Before crash"))
        # Simulate a crash: open a second, independent connection without closing the first.
        other = ProjectDB(p.root / "database" / "project.db")
        self.assertEqual(other.list("scene")[0]["title"], "Before crash")
        other.close()

    def test_newer_schema_is_refused(self):
        from studio import projects
        p = self.make_project()
        projects.close_all()
        con = sqlite3.connect(p.root / "database" / "project.db")
        con.execute("PRAGMA user_version=99")
        con.commit()
        con.close()
        with self.assertRaises(RuntimeError):
            projects.get_project(p.id)

    def test_list_duplicate_rename(self):
        from studio import projects
        p = self.make_project(title="Original")
        p.db.put("scene", projects.default_scene(title="S1"))
        dup = projects.duplicate_project(p, "Copy of original")
        self.assertEqual(len(dup.scenes()), 1)
        self.assertNotEqual(dup.manifest["uid"], p.manifest["uid"])
        p.update_meta({"title": "Renamed"})
        titles = {x["title"] for x in projects.list_projects()}
        self.assertEqual(titles, {"Renamed", "Copy of original"})

    def test_rejects_non_project_folder(self):
        from studio import config, projects
        bad = config.projects_dir() / "not_a_project"
        bad.mkdir()
        (bad / "project.json").write_text('{"app": "something else"}')
        self.assertEqual([x for x in projects.list_projects() if x["id"] == "not_a_project"], [])
        with self.assertRaises(ValueError):
            projects.Project(bad)

    def test_path_resolution_blocks_escape(self):
        from studio.projects import PathError
        p = self.make_project()
        self.assertEqual(p.path("media/images/a.png"), p.root / "media" / "images" / "a.png")
        for bad in ("../outside.txt", "/etc/passwd", "media/../../x", "", "a\x00b"):
            with self.assertRaises(PathError, msg=bad):
                p.path(bad)
        from studio import projects
        for bad_id in ("../x", "a/b", ".hidden"):
            with self.assertRaises(PathError):
                projects.get_project(bad_id)

    def test_status_and_package(self):
        import zipfile
        from studio import projects
        p = self.make_project()
        self.assertEqual(p.status()["percent"], 0)
        p.db.put("source", {"title": "S", "url": "https://example.org"})
        p.db.put("scene", projects.default_scene(narration_ar="نص"))
        st = p.status()["steps"]
        self.assertTrue(st["research"] and st["script"])
        z = p.package()
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
        self.assertIn("project.json", names)
        self.assertIn("database/project.db", names)
        self.assertFalse(any(n.startswith("exports/") for n in names))
        meta = p.export_metadata()
        self.assertEqual(json.loads(meta.read_text("utf-8"))["scenes"][0]["narration_ar"], "نص")


class Settings(TempHome):
    def test_settings_roundtrip_never_stores_secrets(self):
        from studio import config
        s = config.save_settings({"speaking_rate_ar_wpm": 120, "anthropic": {"model": "m", "api_key": "sk-x"}})
        self.assertEqual(s["speaking_rate_ar_wpm"], 120)
        raw = (config.app_support_dir() / "settings.json").read_text()
        self.assertNotIn("sk-x", raw)
        self.assertEqual(config.load_settings()["anthropic"]["model"], "m")
