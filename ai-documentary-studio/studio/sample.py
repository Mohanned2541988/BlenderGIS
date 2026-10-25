"""Builds a small sample project from original text and procedurally generated media.

Everything here is created locally by FFmpeg (no third-party assets), so it is free to use.
Narration uses the macOS Arabic voice when available, otherwise a silent placeholder.
"""
from __future__ import annotations

from pathlib import Path

from . import config, media, storyboard, workflows
from .projects import create_project, default_scene
from .providers.speech import MacSayTTS

SCENES = [
    dict(title="Hook", purpose="hook",
         narration_ar="هل تبدو غرينلاند بحجم أفريقيا على الخريطة؟ الحقيقة مختلفة تمامًا.",
         narration_en="Does Greenland look as big as Africa on the map? The truth is very different.",
         card={"type": "title", "title": "كيف تخدعنا الخرائط", "subtitle": "How maps mislead us"},
         visual="mandelbrot=s=1280x1280:maxiter=300:start_scale=2.5", motion="zoom_in"),
    dict(title="Central question", purpose="question",
         narration_ar="لماذا تبدو الخرائط التي نراها كل يوم مشوّهة؟",
         narration_en="Why do the maps we see every day look distorted?",
         on_screen_text="لماذا؟", visual="cellauto=s=1280x1280:rule=110", motion="pan_right",
         transition={"type": "crossfade", "duration": 0.6}),
    dict(title="Mercator", purpose="context",
         narration_ar="في عام ١٥٦٩ نشر جيراردوس مركاتور خريطة صُممت للملاحة البحرية.",
         narration_en="In 1569, Gerardus Mercator published a map designed for sea navigation.",
         card={"type": "lower_third", "title": "Gerardus Mercator", "subtitle": "Cartographer · 1512–1594"},
         visual="gradients=s=1280x1280:c0=0x3a2c1e:c1=0x0d0d0d:c2=0x6b5427:n=3:speed=0.01", motion="pan_left",
         claim=0),
    dict(title="The distortion", purpose="evidence",
         narration_ar="خطوطه المستقيمة ساعدت البحّارة، لكنها تضخّم المساحات القريبة من القطبين.",
         narration_en="Its straight lines helped sailors, but they inflate areas near the poles.",
         card={"type": "statistic", "title": "×14", "subtitle": "Africa vs Greenland (land area, approx.)"},
         visual="life=s=320x320:mold=10:ratio=0.2:death_color=#1a1408:life_color=#e8b04b", motion="zoom_out",
         claim=1, transition={"type": "fade", "duration": 0.5}),
    dict(title="Conclusion", purpose="conclusion",
         narration_ar="كل خريطة هي اختيار. والسؤال الحقيقي: من يختار؟",
         narration_en="Every map is a choice. The real question is: who chooses?",
         card={"type": "end", "title": "كل خريطة هي اختيار", "subtitle": "Every map is a choice"},
         visual="gradients=s=1280x1280:c0=0x161412:c1=0x2a2118:n=2:speed=0.02", motion="none"),
]

CLAIMS = [
    "Gerardus Mercator published his world map projection in 1569.",
    "Africa's land area is roughly 14 times that of Greenland, although they look similar on Mercator maps.",
]


def build(base: Path | None = None, settings: dict | None = None, title: str = "Sample - How maps mislead us"):
    settings = settings or config.load_settings()
    proj = create_project({
        "title": title, "topic": "Why the Mercator projection distorts the size of countries",
        "main_question": "Why do everyday maps distort the world?", "audience": "Curious Arabic-speaking viewers",
        "platform": "instagram_reels", "aspect_ratio": "9:16", "target_duration": 35,
        "narration_language": "ar", "subtitle_language": "bilingual", "visual_style": "editorial_collage",
        "tone": "curious, precise", "budget": "free / local only",
    }, base)
    src = proj.db.put("source", {
        "title": "Mercator projection - Wikipedia", "url": "https://en.wikipedia.org/wiki/Mercator_projection",
        "publisher": "Wikipedia", "published": "", "reviewed": False, "added_by": "sample",
        "summary": "Sample source added by the app. Open it and verify the claims before publishing.", "quotes": [],
    })
    claims = [proj.db.put("claim", {"text": c, "status": "unverified", "source_ids": [src["id"]],
                                    "conflicting_source_ids": []}) for c in CLAIMS]
    say = MacSayTTS()
    use_say = say.available(settings)[0]
    if use_say:
        try:
            say.pick_voice("ar", settings)
        except Exception:
            use_say = False
    tts_settings = dict(settings, providers=dict(settings["providers"], tts="macos_say" if use_say else "mock"))
    for i, spec in enumerate(SCENES):
        s = default_scene(title=spec["title"], purpose=spec["purpose"], narration_ar=spec["narration_ar"],
                          narration_en=spec["narration_en"], on_screen_text=spec.get("on_screen_text", ""),
                          motion=spec["motion"], motion_locked=True, generated_by="sample",
                          visual_description=spec["narration_en"])
        if spec.get("card"):
            s["card"] = {**s["card"], **spec["card"], "lang": "auto"}
        if spec.get("transition"):
            s["transition"] = spec["transition"]
        if "claim" in spec:
            s["claim_ids"] = [claims[spec["claim"]]["id"]]
            s["source_ids"] = [src["id"]]
        else:
            s["factual"] = False
        s.update(storyboard.generate(s, proj.meta, i))
        s["motion"] = spec["motion"]
        s = proj.db.put("scene", s)
        img = proj.path(f"media/graphics/sample_{i + 1}.png")
        media.run_ffmpeg(["-f", "lavfi", "-i", spec["visual"], "-frames:v", "1", "-vf", "scale=1280:1280,eq=saturation=0.3:brightness=-0.12:contrast=0.9,"
                          "colorbalance=rs=0.12:gs=0.04:bs=-0.1,noise=alls=10:allf=t",
                          str(img)], timeout=120)
        a = media.register_file(proj, img, "graphics", img.name, s["id"], origin="sample",
                                extra={"name": f"Sample texture {i + 1} (generated)", "status": "approved"})
        s["media"]["visual_asset_id"] = a["id"]
        proj.db.put("scene", s)
        workflows.tts_scene(proj, s["id"], "ar", tts_settings)
    # Original ambient bed: two soft detuned sines with slow tremolo.
    music = proj.path("media/music/sample_ambient.wav")
    media.run_ffmpeg(["-f", "lavfi", "-i", "sine=f=110:d=45", "-f", "lavfi", "-i", "sine=f=164.8:d=45",
                      "-filter_complex", "[0][1]amix=inputs=2,tremolo=f=0.2:d=0.6,volume=0.5,afade=t=in:d=2",
                      "-ac", "2", "-ar", "48000", str(music)], timeout=120)
    m = media.register_file(proj, music, "music", music.name, None, origin="sample",
                            extra={"name": "Sample ambient bed (generated)", "status": "approved"})
    proj.update_meta({"timeline": {"music_asset_id": m["id"], "music_volume_db": -20}})
    workflows.cues_from_script(proj, settings)
    proj.log_event(f"sample project built (narration: {'macOS voice' if use_say else 'silent placeholder'})")
    return proj, use_say
