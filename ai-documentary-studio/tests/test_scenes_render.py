from pathlib import Path

from helpers import TempHome


def S(**kw):
    from studio.projects import default_scene
    return default_scene(**kw)


class Scenes(TempHome):
    def test_order_insert_reorder_delete(self):
        p = self.make_project()
        a = p.db.put("scene", S(title="A"))
        b = p.db.put("scene", S(title="B"))
        c = p.db.insert_after("scene", a["id"], S(title="C"))
        self.assertEqual([s["title"] for s in p.scenes()], ["A", "C", "B"])
        p.db.reorder("scene", [b["id"], a["id"], c["id"]])
        self.assertEqual([s["title"] for s in p.scenes()], ["B", "A", "C"])
        p.db.delete("scene", a["id"])
        self.assertEqual([s["title"] for s in p.scenes()], ["B", "C"])
        p.db.put("scene", dict(p.db.get("scene", b["id"]), title="B2"))
        self.assertEqual(p.scenes()[0]["title"], "B2")  # update keeps position

    def test_template_script_structure(self):
        from studio import scriptwriter
        for dur, n in ((30, 4), (45, 5), (60, 7), (300, 30)):
            sc = scriptwriter.template_script({"target_duration": dur, "title": "T", "topic": "x"})
            self.assertEqual(len(sc), n, dur)
            self.assertEqual(sc[0]["purpose"], "hook")
            self.assertIn(sc[-1]["purpose"], ("conclusion", "cta"))
            self.assertTrue(all(s["generated_by"] == "template" for s in sc))

    def test_generate_script_offline_and_storyboard(self):
        from studio import config, scriptwriter, storyboard
        p = self.make_project(target_duration=60)
        r = scriptwriter.generate_script(p, config.load_settings(), replace=True)
        self.assertFalse(r["ai"])
        self.assertEqual(len(p.scenes()), 7)
        s = p.scenes()[2]
        upd = storyboard.generate(s, p.meta, 2)
        sb = upd["storyboard"]
        for k in ("shot", "camera", "composition", "lighting", "palette", "image_prompt", "video_prompt",
                  "negative_prompt"):
            self.assertTrue(sb[k], k)
        self.assertIn("9:16", sb["image_prompt"])
        self.assertFalse(sb["ai_reconstruction"])

    def test_research_warnings(self):
        from studio import scriptwriter
        p = self.make_project()
        src = p.db.put("source", {"title": "Src", "reviewed": False})
        c1 = p.db.put("claim", {"text": "No source verified", "status": "verified", "source_ids": []})
        c2 = p.db.put("claim", {"text": "Unreviewed", "status": "verified", "source_ids": [src["id"]]})
        p.db.put("scene", S(narration_en="Fact.", factual=True))
        p.db.put("scene", S(narration_en="[VERIFY: date] It happened.", claim_ids=[c2["id"]]))
        msgs = " | ".join(w["msg"] for w in scriptwriter.research_warnings(p))
        self.assertIn("no source", msgs)
        self.assertIn("not marked reviewed", msgs)
        self.assertIn("links no claim", msgs)
        self.assertIn("[VERIFY]", msgs)
        self.assertTrue(c1["id"])


class Timeline(TempHome):
    def assets(self, p):
        return {a["id"]: a for a in p.db.list("asset")}

    def test_durations_and_crossfade_overlap(self):
        from studio import config, render
        st = config.load_settings()
        p = self.make_project()
        narr = p.db.put("asset", {"type": "audio", "path": "media/audio/x.wav", "info": {"duration": 4.0}})
        p.db.put("scene", S(media={"narration_asset_id": narr["id"]}, transition={"type": "crossfade", "duration": 0.5}))
        p.db.put("scene", S(duration=3, transition={"type": "fade", "duration": 0.5}))
        p.db.put("scene", S(narration_en="One two three four five six seven eight nine ten."))
        tl = render.compute_timeline(p.scenes(), self.assets(p), st)
        e = tl["entries"]
        self.assertEqual(e[0]["duration_source"], "narration audio")
        self.assertAlmostEqual(e[0]["duration"], 4.0 + render.NARRATION_LEAD + 0.65, places=2)
        self.assertEqual(e[1]["duration_source"], "manual")
        self.assertAlmostEqual(e[1]["start"], e[0]["duration"] - 0.5, places=3)  # crossfade overlap
        self.assertEqual(e[2]["duration_source"], "estimate")
        self.assertEqual(e[2]["transition"]["type"], "cut")  # last scene never transitions out
        self.assertAlmostEqual(tl["total"], e[2]["start"] + e[2]["duration"], places=3)

    def test_manual_longer_than_narration_wins(self):
        from studio import config, render
        assets = {"a": {"info": {"duration": 2.0}}}
        d, src = render.scene_duration(S(duration=9, media={"narration_asset_id": "a"}), assets, config.load_settings())
        self.assertEqual((d, src), (9.0, "manual (longer than narration)"))

    def test_segment_command_construction(self):
        from studio import render
        p = self.make_project()
        o = render.normalize_options({"quality": "draft", "fps": 25}, p)
        args = render.segment_command({"duration": 4, "visual": Path("/x/img.png"), "visual_type": "image",
                                       "motion": "pan_left", "narration": Path("/x/n.wav"), "fade_in": True,
                                       "fade_out": False}, o, Path("/x/out.mp4"))
        fc = args[args.index("-filter_complex") + 1]
        self.assertIn("zoompan=", fc)
        self.assertIn("d=100", fc)  # 4 s * 25 fps
        self.assertIn("s=1080x1920", fc)
        self.assertIn("fade=t=in", fc)
        self.assertNotIn("fade=t=out", fc)
        self.assertIn("adelay=delays=250:all=1", fc)
        self.assertEqual(args[-1], "/x/out.mp4")
        self.assertIn("libx264", args)
        vid = render.segment_command({"duration": 2, "visual": Path("/x/v.mov"), "visual_type": "video"}, o, Path("o.mp4"))
        self.assertIn("-stream_loop", vid)
        blank = render.segment_command({"duration": 2}, o, Path("o.mp4"))
        self.assertIn("color=c=0x161412:s=1080x1920:r=25:d=2.000", blank)

    def test_assemble_command_offsets(self):
        from studio import render
        p = self.make_project()
        o = render.normalize_options({"preset": "landscape"}, p)
        segs = [{"path": "a.mp4", "duration": 5, "transition": {"type": "crossfade", "duration": 1}},
                {"path": "b.mp4", "duration": 4, "transition": {"type": "cut", "duration": 0}},
                {"path": "c.mp4", "duration": 3, "transition": {"type": "cut", "duration": 0}}]
        args = render.assemble_command(segs, o, 11, "out.mp4", "overlay.ass",
                                       music={"path": "m.wav", "volume_db": -20, "duck": True})
        fc = args[args.index("-filter_complex") + 1]
        self.assertIn("xfade=transition=fade:duration=1.000:offset=4.000", fc)
        self.assertIn("concat=n=2:v=1:a=1", fc)
        self.assertIn("settb=AVTB", fc)
        self.assertIn("sidechaincompress", fc)
        self.assertIn("ass=overlay.ass", fc)
        self.assertIn("loudnorm", fc)
        self.assertIn("+faststart", args)
        self.assertEqual(args[args.index("-t") + 1], "11.000")

    def test_export_options(self):
        from studio import render
        p = self.make_project(aspect_ratio="16:9")
        o = render.normalize_options({"preset": "project", "filename": 'bad/na:me', "subtitle_mode": "zz"}, p)
        self.assertEqual((o["width"], o["height"]), (1920, 1080))
        self.assertEqual(o["filename"], "bad_na_me.mp4")
        self.assertEqual(o["subtitle_mode"], "none")
        self.assertEqual(render.normalize_options({"preset": "square"}, p)["width"], 1080)
        prev = render.normalize_options({"preview": True, "quality": "high"}, p)
        self.assertEqual((prev["width"], prev["quality"]), (960, "draft"))
        with self.assertRaises(render.RenderError):
            render.normalize_options({"preset": "8k"}, p)

    def test_validation_catches_problems(self):
        from studio import config, render
        st = config.load_settings()
        p = self.make_project()
        v = render.validate(p, {}, st)
        self.assertFalse(v["ok"])
        self.assertIn("no scenes", v["errors"][0])
        gone = p.db.put("asset", {"type": "image", "path": "media/images/missing.png", "name": "m", "info": {}})
        p.db.put("scene", S(media={"visual_asset_id": gone["id"]}, narration_en="x"))
        p.db.put("scene", S(media={"visual_asset_id": "ast_deleted"}))
        v = render.validate(p, {"subtitle_mode": "ar", "fps": 23, "output_dir": "relative/dir"}, st)
        errs = " | ".join(v["errors"])
        self.assertIn("file missing on disk", errs)
        self.assertIn("no longer exists", errs)
        self.assertIn("Frame rate 23", errs)
        self.assertIn("No AR subtitles", errs)
        self.assertIn("absolute path", errs)
