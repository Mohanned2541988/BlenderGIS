# AI Documentary Studio

A local, single-user production studio for short **Arabic-narrated documentaries with English subtitles**
(Instagram Reels, TikTok, YouTube Shorts, landscape YouTube). It takes a project from idea → research →
script → storyboard & prompts → media → voice → bilingual subtitles → scene timeline → rendered MP4.

Runs entirely on your Mac. **No paid API is required** to open it, write, edit, import media, add narration,
make subtitles and render a finished video. AI providers are optional add-ons you switch on in Settings.

---

## 1. Quick start (macOS, Apple Silicon)

```bash
# one-time: tools
xcode-select --install          # provides python3 (3.9+) and git, if not already installed
brew install ffmpeg             # https://brew.sh if you don't have Homebrew

# get the code (this branch) and launch
git clone -b claude/macbook-power-on-d347sg https://github.com/Mohanned2541988/BlenderGIS.git
cd BlenderGIS/ai-documentary-studio
./run.sh                        # opens http://127.0.0.1:8765 in your browser
```

Or double-click **`Start AI Documentary Studio.command`** in Finder (the first time, right-click → Open to get
past Gatekeeper). Close the Terminal window or press `Ctrl+C` to quit.

`./run.sh --port 9000 --no-browser` changes the port / skips opening the browser.

Projects are saved in `~/Documents/AI Documentary Studio/` (change it in Settings or with `ADS_PROJECTS_DIR`).
App settings and logs live in `~/Library/Application Support/AI Documentary Studio/`.

## 2. Your first documentary

1. **Projects → Create sample project** builds a 25-second demo (original generated visuals, Arabic narration via
   the macOS Arabic voice if installed, bilingual subtitles, music bed). Open **Timeline** or **Export** to render it.
2. **New Documentary** — the wizard asks for title, topic, main question, audience, platform, aspect ratio
   (9:16 / 16:9 / 1:1), duration (30 s – 5 min or custom), languages, visual style, tone, research needs, budget and
   preferred providers. The project is saved immediately.
3. **Research** — add sources (URL, publisher, date, summary, quotations) and tick *reviewed* only when you have
   read them yourself. Record **claims** as Verified / Unverified / Interpretation / Reconstruction / Disputed with
   supporting and conflicting sources. Generate a research brief. Warnings list unsupported claims.
4. **Script Editor** — *Generate structure* (offline) creates a hook → question → context → evidence →
   counterpoint → conclusion outline; with Claude or Ollama enabled it drafts the full Arabic + English script
   from your sources. Edit any field, drag to reorder, duplicate, delete, or regenerate a single scene. Link claims.
5. **Storyboard** — *Generate storyboard* fills shot, camera, composition, lighting, palette, motion and
   image / video / negative prompts per scene from the style. Copy prompts into any generator; import the results.
   Pick a graphic card (title, chapter, lower third, source, quote, pull quote, timeline, map, statistic, end).
6. **Media Library** — drag files in or import by path (originals are copied, never modified). Preview, rename,
   approve / reject / mark for replacement, tag, assign to a scene, check for missing files.
7. **Voice & Subtitles** — import a recording per scene (or one continuous narration track), or generate it with
   the macOS voice. Then *Generate from script* (timed to the real audio length), or *Transcribe audio* with
   whisper.cpp for real timestamps; *Translate AR → EN*; correct times and text in the cue editor; export
   Arabic / English / bilingual SRT and WebVTT; preview with live subtitle tracks.
8. **Timeline** — drag scenes to reorder, set duration, transition (cut / fade / crossfade), Ken Burns motion,
   crop focus, overlay layer, sound effect, background music (looped, faded, ducked under narration).
   *Preview* renders a half-resolution draft.
9. **Export** — choose preset (1080×1920, 1920×1080, 1080×1080), fps, quality, burned-in subtitles
   (none / AR / EN / AR+EN), audio level, loudness normalization, encoder (x264 or Apple VideoToolbox), file name,
   output folder, sidecar SRTs, keep intermediates. *Check project* validates first; existing files are never
   overwritten without confirmation. Progress, ETA and Cancel are shown in the top bar.

## 3. Architecture (and why)

**Local web app**: a Python standard-library HTTP server bound to `127.0.0.1` + a framework-free HTML/CSS/JS UI.

* *Why not Electron/Tauri?* Both add a large toolchain (Node ~200 MB / Rust) without adding capability here —
  the heavy lifting is FFmpeg, and a browser tab gives a polished, responsive UI with native video/audio
  players, WebVTT subtitle tracks and correct Arabic text rendering for free. Zero pip dependencies means
  nothing to break on macOS updates. Wrapping it in Tauri later is straightforward (see roadmap).
* **SQLite per project** (WAL mode) for crash-safe autosave; every edit is committed immediately.
* **FFmpeg + libass** for all media work. libass (with HarfBuzz/FriBiDi, included in Homebrew's FFmpeg) shapes
  Arabic correctly and handles right-to-left order for subtitles *and* graphics cards.
* **Provider adapters** for language models, TTS, speech recognition, web search, translation, image/video.

```
ai-documentary-studio/
  run.sh, Start AI Documentary Studio.command   launchers
  studio/
    __main__.py      entry point (python3 -m studio)
    server.py        JSON API, static UI, range-enabled media streaming, security checks
    projects.py      project folders, manifest, autosave, duplicate/rename/package/status
    db.py            SQLite storage + schema migrations (PRAGMA user_version)
    media.py         import/probe/thumbnails/audio fixes/placeholders (ffprobe/ffmpeg)
    scriptwriter.py  script generation, scene regeneration, translation, research brief & warnings
    storyboard.py    style presets and prompt generation (offline)
    subtitles.py     SRT/VTT parse+write, cue timing, ASS styles
    graphics.py      title/chapter/lower-third/quote/source/timeline/map/statistic/end cards (ASS)
    render.py        timeline maths, validation, FFmpeg command builders, render pipeline
    workflows.py     TTS per scene, cues from script, transcription, translation, subtitle export
    jobs.py          background jobs with progress / ETA / cancel
    sample.py        sample project generator
    providers/       llm.py (template, Claude, Ollama), speech.py (mock, macOS say, whisper.cpp),
                     secrets.py (env vars + macOS Keychain), __init__.py (registry, costs)
  web/               index.html, app.css, app.js, i18n.js
  tests/             unittest suite
  scripts/           run_tests.sh, smoke_test.sh
```

Each project folder:

```
My_Documentary/
  project.json          manifest (app, schema_version, title, timestamps)
  database/project.db   scenes, sources, claims, assets, subtitle cues, renders
  research/brief.md     scripts/  storyboard/  subtitles/  timeline/  exports/  logs/
  media/images  media/videos  media/audio  media/music  media/graphics  media/.thumbs  media/.trash
```

## 4. Dependencies

| Required | Notes |
|---|---|
| macOS on Apple Silicon (also runs on Linux) | |
| Python 3.9+ | standard library only; tested on 3.9.25 and 3.13 |
| FFmpeg 6+ with libass, libx264, HarfBuzz, FriBiDi | `brew install ffmpeg` provides all of these |

| Optional | Enables |
|---|---|
| `pip3 install anthropic` (≥ 2.3) + `ANTHROPIC_API_KEY` | Claude: script drafting, scene rewrite, AR→EN translation, research brief, web search |
| [Ollama](https://ollama.com) + `ollama pull llama3.1:8b` | free local language model (needs ~6 GB free RAM; Arabic quality varies by model) |
| `brew install whisper-cpp` + a ggml model file | free local Arabic transcription for real subtitle timing |
| macOS Arabic system voice (e.g. *Majed*) | free offline Arabic TTS |

## 5. Configuring AI providers

Everything is in **Settings**. Each provider shows *ready / not ready*, *local* or *sends data to the internet*,
and a cost note. Nothing is sent anywhere unless you select a cloud provider **and** press a button that uses it.

* **Claude (Anthropic)** — `pip3 install anthropic`, then either `export ANTHROPIC_API_KEY=...` before `./run.sh`,
  or paste the key in Settings → *Save to Keychain* (stored in the macOS Keychain under
  `ai-documentary-studio`; never written to project or settings files). Default model `claude-opus-5-5`
  with effort `medium`, editable. Requests use the API's server-side fallback if a request is declined.
  What is sent: project brief, your notes/sources/claims, narration text. Media is never uploaded.
  Pricing changes — check https://www.anthropic.com/pricing.
* **Claude web search** (Settings → Web research) — results are added as *unreviewed* sources; only URLs the
  search tool actually returned are kept, so the model cannot invent citations.
* **Ollama** — install, `ollama pull llama3.1:8b`, select *Ollama* in Settings (URL/model editable).
* **whisper.cpp** — `brew install whisper-cpp`; download a model, e.g.
  `curl -L -o ~/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin`,
  set its path in Settings, choose *whisper.cpp* under Speech recognition.
* **macOS voices** — System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → add an
  Arabic voice; choose *macOS system voices* under Text-to-speech and pick the voice in Settings.
* **Image/video generation** — prompts are produced for you to use in any service; generated files are imported
  like any media (mark them *AI-generated* so the app reminds you to label reconstructions on screen).

## 6. Tests

```bash
./scripts/run_tests.sh      # unit + integration tests (46 tests; Claude adapter tests skip without the SDK)
./scripts/smoke_test.sh     # builds the sample in a temp folder and renders a real MP4 with local tools only
```

Results from the development run (Linux container, FFmpeg 6.1.1):

* `run_tests.sh`: **46 tests, all passing** with the `anthropic` SDK installed (43 pass + 3 skipped without it),
  on Python 3.13 and 3.9.25.
* `smoke_test.sh`: **passed** — sample rendered to a 25 s 1080×1920 H.264/AAC MP4.
* Full HTTP workflow (create → script → reorder/duplicate/delete → storyboard → research & warnings → upload &
  import media → mock TTS → subtitles → edit cues → export SRT/VTT/CSV → validate → render with crossfades and
  bilingual burned subtitles → overwrite protection → range download → duplicate → package): **passed**.
* UI walkthrough in headless Chromium (wizard, autosave, every view, RTL toggle, 390 px mobile width):
  **no console errors, no horizontal overflow**.

**Not tested** (no Mac or credentials in the build environment): the macOS `say` voices, Keychain storage,
whisper.cpp transcription, Apple VideoToolbox encoding, and live calls to Claude/Ollama. The Claude and Ollama
adapters were tested against local fake API servers using the real `anthropic` SDK (request shape, parsing,
refusal handling, citation filtering) — but not against the real services.

## 7. Implemented features

* Projects: create (wizard), open, autosave, duplicate, rename, package (.zip), export metadata (JSON),
  completion status, missing-file detection, versioned schema with migrations, crash-safe storage.
* Research: sources with review status, claims with 5 statuses + supporting/conflicting sources, notes,
  research brief (offline compiler or AI), fact-check warnings (unsourced, unreviewed, unlinked, [VERIFY]),
  optional Claude web search.
* Script: scene-based editor with every field from the brief, drag & drop + arrow reordering, duplicate, delete,
  insert after, per-scene AI regeneration, speaking-rate duration estimates, offline structure generator,
  AI drafting restricted to your sources, Markdown export.
* Storyboard: 7 visual styles, per-scene shot/camera/composition/lighting/palette/motion, image/video/negative
  prompts, reconstruction flag, graphic cards, CSV/JSON export, placeholder frames.
* Media: upload/drag-drop/import by path, thumbnails & waveforms, metadata, preview, rename, status, tags,
  filters, scene assignment, in-use warnings, non-destructive removal (to `media/.trash`), loudness normalization
  and silence trimming as new copies.
* Voice & subtitles: per-scene or continuous narration, recording import/replace, macOS TTS, mock TTS, whisper.cpp
  transcription, script-to-audio timing, AI translation with identical timing, cue editor (add/delete/shift),
  SRT/VTT import & export, bilingual SRT, burned-in styling (font, size, position, outline, box, colour,
  line length), live preview with WebVTT tracks.
* Timeline & render: scene timeline with durations from audio / manual / estimate, cut / fade / crossfade,
  Ken Burns (zoom in/out, pan 4 directions), crop focus, overlay layer, SFX, looping music with ducking,
  fades, graphics cards & animated on-screen text with correct Arabic shaping, film grain, loudness
  normalization, 3 presets, fps/quality/encoder options, validation (media, durations, subtitles, disk space,
  output folder, encoder), progress + ETA + cancel, overwrite confirmation, sidecar SRTs, render history,
  half-resolution previews.
* Interface: dark editorial design, English/Arabic interface with full RTL layout, keyboard-accessible controls,
  responsive down to phone width.
* Security: localhost-only server, Host-header check (DNS-rebinding), custom header required on writes
  (blocks cross-site requests), path-traversal protection, file-type validation, no code execution from
  project files, secrets only in env/Keychain, non-destructive deletes.

## 8. Limitations (honest list)

* It is a **scene-based editor**, not a full non-linear editor: one main visual per scene (plus one overlay
  layer), no multi-track keyframing, no trimming inside video clips beyond the scene length (videos start at
  0:00 and loop if shorter), no per-clip audio from source videos.
* Render speed: draft ≈ 2–3× the video length on the 4-core build machine; an M3 should be noticeably faster.
  *Standard/High* quality and film grain are slower. VideoToolbox is offered for speed but untested.
* "Generate from script" subtitle timing is proportional within each real narration clip — good, not
  word-accurate. Use whisper.cpp for real timestamps, then review.
* The offline template generator writes **placeholders**, not prose. AI-drafted scripts must be fact-checked;
  the app flags but cannot verify facts itself.
* No built-in image/video generation integration (prompts only) and no cloud TTS (e.g. ElevenLabs) yet —
  these need provider-specific verification before adding.
* Fonts: libass uses installed system fonts by name; check the licence of any font you publish with.
* Interface translation covers navigation and main headings; some detailed labels are English only.
* Deleting whole projects is done in Finder (the app deliberately never deletes project folders).

## 9. Roadmap (prioritized)

1. Word-level alignment (whisper.cpp `-ml 1` / forced alignment) for perfectly timed subtitles and karaoke-style
   captions popular on Reels/TikTok.
2. Clip trimming (in/out points) and keeping source-video audio; multiple visuals per scene with simple keyframes.
3. Cloud TTS adapters with verified Arabic support (e.g. ElevenLabs, Azure, Google) and voice preview.
4. Image/video generation adapters (after verifying current APIs/pricing), with automatic *AI-generated* labels.
5. Map and timeline graphics with real data (GeoJSON maps, animated date ranges), more card templates.
6. Tauri wrapper → a signed `.app` with a dock icon and native file pickers.
7. Project templates per series/brand (fonts, colours, intros/outros) and batch export of all aspect ratios.
8. Undo history per scene and project snapshots.
9. Full Arabic translation of every interface string.
