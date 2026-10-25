"""Rule-based storyboard and prompt generation (works offline, no AI provider needed)."""
from __future__ import annotations

STYLES = {
    "editorial_collage": {
        "label": "Editorial collage",
        "look": "editorial mixed-media collage, layered archival photographs with torn paper edges, newspaper "
                "fragments, cut-out photography, halftone print texture, hand-drawn red annotation lines",
        "lighting": "soft top light with subtle paper shadows between layers",
        "palette": "charcoal black, aged newsprint cream, muted ochre, signal red accents",
        "camera": "slow parallax push between collage layers",
        "texture": "paper grain, subtle film grain, slight print misregistration",
        "negative": "glossy 3D render, plastic look, cartoon, garbled text, watermark, logo, extra fingers",
    },
    "historical_documentary": {
        "label": "Historical documentary",
        "look": "period-accurate historical scene, documentary photography aesthetic, natural composition",
        "lighting": "naturalistic window or overcast daylight, motivated practical lights",
        "palette": "desaturated earth tones, warm sepia highlights",
        "camera": "slow dolly or gentle Ken Burns push",
        "texture": "fine film grain, slight halation",
        "negative": "anachronistic objects, modern clothing, neon, fantasy elements, watermark, text",
    },
    "cinematic_realism": {
        "label": "Cinematic realism",
        "look": "cinematic photoreal frame, anamorphic lens character, shallow depth of field",
        "lighting": "low-key motivated lighting with strong contrast and soft falloff",
        "palette": "teal shadows, warm skin tones, restrained saturation",
        "camera": "slow push-in or lateral tracking move",
        "texture": "35mm film grain, gentle lens bloom",
        "negative": "oversaturated, HDR halos, cartoon, illustration, watermark, text, deformed hands",
    },
    "archival_investigation": {
        "label": "Archival investigation",
        "look": "evidence board composition: documents, maps, photographs pinned and connected with red string, "
                "magnifying-glass details, redacted paper",
        "lighting": "single desk lamp pool of warm light, dark surroundings",
        "palette": "manila folder beige, ink black, evidence-tag red",
        "camera": "macro slide across documents, rack focus",
        "texture": "paper fibres, coffee stains, typewriter ink",
        "negative": "readable fake document text presented as real, watermark, logo, glossy render",
    },
    "animated_infographic": {
        "label": "Animated infographic",
        "look": "clean flat vector infographic, bold geometric shapes, clear data hierarchy",
        "lighting": "flat even lighting, no harsh shadows",
        "palette": "deep charcoal background, off-white, one ochre accent, one cool blue accent",
        "camera": "static frame with elements animating in",
        "texture": "subtle paper noise",
        "negative": "photorealism, clutter, 3D bevel, gradients overload, garbled numbers, watermark",
    },
    "minimalist_explainer": {
        "label": "Minimalist explainer",
        "look": "minimalist composition, single subject, generous negative space",
        "lighting": "soft diffuse studio light",
        "palette": "monochrome charcoal with one accent color",
        "camera": "slow centered push-in",
        "texture": "very light grain",
        "negative": "busy background, clutter, text, watermark",
    },
    "photographic_essay": {
        "label": "Photographic essay",
        "look": "documentary photojournalism still, candid moment, decisive-moment composition",
        "lighting": "available natural light",
        "palette": "natural color with slightly lifted blacks",
        "camera": "Ken Burns drift across the photograph",
        "texture": "35mm film grain",
        "negative": "posed stock-photo look, oversaturation, watermark, text, distorted faces",
    },
}

PURPOSE_SHOTS = {
    "hook": ("striking close-up detail that raises a question", "fast-feeling push-in", "zoom_in"),
    "question": ("symbolic wide shot with a single focal subject", "slow push-in", "zoom_in"),
    "context": ("establishing wide shot of place and period", "slow lateral pan", "pan_right"),
    "development": ("medium shot showing the key actors or objects", "gentle drift", "pan_left"),
    "evidence": ("insert shot of documents, data or physical evidence", "slow pan across the evidence", "pan_down"),
    "counterpoint": ("contrasting composition, opposite angle or tone", "slow pull-out", "zoom_out"),
    "transition": ("textural interstitial frame (map, paper, sky)", "slow drift", "pan_up"),
    "conclusion": ("wide reflective frame with breathing room", "slow pull-out", "zoom_out"),
    "cta": ("clean background for end card text", "static", "none"),
}
PURPOSES = list(PURPOSE_SHOTS)

ORIENTATION = {"9:16": "vertical 9:16 frame, subject centered for mobile viewing",
               "16:9": "horizontal 16:9 widescreen frame, rule-of-thirds composition",
               "1:1": "square 1:1 frame, centered composition"}


def generate(scene: dict, meta: dict, index: int = 0) -> dict:
    """Fill a scene's storyboard fields from its script and the project style."""
    style = STYLES.get(meta.get("visual_style"), STYLES["editorial_collage"])
    purpose = scene.get("purpose") if scene.get("purpose") in PURPOSE_SHOTS else "development"
    shot, camera, motion = PURPOSE_SHOTS[purpose]
    subject = (scene.get("visual_description") or scene.get("narration_en") or scene.get("title")
               or meta.get("topic") or "the documentary subject").strip()
    subject = subject.rstrip(".")
    orient = ORIENTATION.get(meta.get("aspect_ratio"), ORIENTATION["9:16"])
    historical = meta.get("visual_style") in ("historical_documentary", "archival_investigation", "editorial_collage")
    image_prompt = (f"{style['look']}. Subject: {subject}. Shot: {shot}. {orient}. Lighting: {style['lighting']}. "
                    f"Color palette: {style['palette']}. Texture: {style['texture']}. "
                    f"Leave clean space for on-screen text. No text, no logos, no watermarks.")
    if historical and purpose in ("context", "development", "evidence"):
        image_prompt += " (If this depicts a real historical event, label the result on screen as an AI reconstruction.)"
    video_prompt = (f"{camera if camera != 'static' else 'locked-off static shot'}, {style['camera']}. "
                    f"{subject}. {style['look'].split(',')[0]}. Duration about "
                    f"{round(float(scene.get('duration') or 6))} seconds, smooth continuous motion, no cuts.")
    sb = dict(scene.get("storyboard") or {})
    sb.update({
        "shot": shot,
        "camera": camera,
        "composition": orient,
        "lighting": style["lighting"],
        "palette": style["palette"],
        "motion_direction": motion.replace("_", " "),
        "image_prompt": image_prompt,
        "video_prompt": video_prompt,
        "negative_prompt": style["negative"],
        # Never flipped on automatically: the editor decides what is a reconstruction.
        "ai_reconstruction": bool(sb.get("ai_reconstruction")),
        "generated": "rules",
    })
    return {"storyboard": sb, "motion": scene.get("motion") if scene.get("motion_locked") else motion}
