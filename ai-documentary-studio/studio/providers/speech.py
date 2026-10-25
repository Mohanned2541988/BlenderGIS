"""Speech adapters: text-to-speech and speech-to-text.

TTS: mock (silent placeholder of estimated length), macos_say (built-in macOS voices, offline)
STT: estimate (no transcription; timing spread over real audio length), whisper_cpp (local whisper.cpp CLI)
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..config import IS_MAC
from ..media import MediaError, run_ffmpeg, which
from ..subtitles import parse_srt
from ..textutil import estimate_duration, is_arabic
from .llm import ProviderError

VOICE_RE = re.compile(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]+)\s+#")


class MockTTS:
    name = "mock"
    label = "Silent placeholder (testing)"
    external = False

    def available(self, settings):
        return True, "Creates silent audio of the estimated narration length - for timing tests only."

    def voices(self, settings):
        return [{"name": "silent", "lang": "any"}]

    def synthesize(self, text, lang, out_wav: Path, settings):
        rate = settings.get("speaking_rate_ar_wpm" if lang == "ar" else "speaking_rate_en_wpm", 140)
        d = max(1.0, estimate_duration(text, rate))
        run_ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{d:.2f}", str(out_wav)])
        return {"mock": True, "voice": "silent"}


class MacSayTTS:
    name = "macos_say"
    label = "macOS system voices (offline, free)"
    external = False

    def available(self, settings):
        if not IS_MAC or not which("say"):
            return False, "Only available on macOS"
        return True, "Uses voices installed in System Settings > Accessibility > Spoken Content"

    def voices(self, settings):
        if not which("say"):
            return []
        r = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20)
        out = []
        for line in r.stdout.splitlines():
            m = VOICE_RE.match(line)
            if m:
                out.append({"name": m.group(1).strip(), "lang": m.group(2).replace("-", "_")})
        return out

    def pick_voice(self, lang, settings):
        chosen = (settings.get("tts_voices") or {}).get(lang)
        voices = self.voices(settings)
        if chosen and any(v["name"] == chosen for v in voices):
            return chosen
        for v in voices:
            if v["lang"].lower().startswith(lang):
                return v["name"]
        if lang == "ar":
            raise ProviderError("No Arabic voice is installed. Open System Settings > Accessibility > "
                                "Spoken Content > System Voice > Manage Voices and add an Arabic voice "
                                "(e.g. Majed), then reload.")
        raise ProviderError(f"No voice installed for language '{lang}'.")

    def synthesize(self, text, lang, out_wav: Path, settings):
        voice = self.pick_voice(lang, settings)
        rate = int(settings.get("speaking_rate_ar_wpm" if lang == "ar" else "speaking_rate_en_wpm", 150))
        with tempfile.TemporaryDirectory() as td:
            txt = Path(td) / "in.txt"
            txt.write_text(text, "utf-8")
            aiff = Path(td) / "out.aiff"
            r = subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), "-f", str(txt)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not aiff.exists():
                raise ProviderError("macOS 'say' failed: " + r.stderr.strip())
            run_ffmpeg(["-i", str(aiff), "-ar", "48000", "-ac", "2", str(out_wav)])
        return {"voice": voice, "rate": rate}


class EstimateSTT:
    name = "estimate"
    label = "No transcription (spread text over real audio length)"
    external = False

    def available(self, settings):
        return True, "Uses your script text; timing follows the real audio duration, not word-level alignment."

    def transcribe(self, wav, lang, settings):
        raise ProviderError("Transcription is not configured. Install whisper.cpp (see Settings) or use "
                            "'Generate from script'.")


class WhisperCppSTT:
    name = "whisper_cpp"
    label = "whisper.cpp (local, free, Apple Silicon accelerated)"
    external = False

    def _bin(self, settings):
        return which(settings["whisper_cpp"].get("binary") or "whisper-cli")

    def available(self, settings):
        if not self._bin(settings):
            return False, "whisper-cli not found. Install with: brew install whisper-cpp"
        mp = settings["whisper_cpp"].get("model_path")
        if not mp or not Path(mp).expanduser().exists():
            return False, "Set a ggml model path in Settings (e.g. ggml-small.bin, about 470 MB)"
        return True, Path(mp).name

    def transcribe(self, audio: Path, lang, settings):
        ok, why = self.available(settings)
        if not ok:
            raise ProviderError(why)
        cfg = settings["whisper_cpp"]
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "in.wav"
            run_ffmpeg(["-i", str(audio), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
            prefix = Path(td) / "out"
            cmd = [self._bin(settings), "-m", str(Path(cfg["model_path"]).expanduser()), "-f", str(wav),
                   "-l", lang, "-osrt", "-of", str(prefix), "-t", str(int(cfg.get("threads") or 4)), "-np"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            srt = prefix.with_suffix(".srt")
            if r.returncode != 0 or not srt.exists():
                raise ProviderError("whisper.cpp failed: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
            return parse_srt(srt.read_text("utf-8", errors="replace"))


TTS = {p.name: p for p in (MockTTS(), MacSayTTS())}
STT = {p.name: p for p in (EstimateSTT(), WhisperCppSTT())}


def get_tts(settings):
    return TTS.get(settings["providers"].get("tts"), TTS["mock"])


def get_stt(settings):
    return STT.get(settings["providers"].get("stt"), STT["estimate"])


def guess_lang(text: str) -> str:
    return "ar" if is_arabic(text) else "en"


__all__ = ["TTS", "STT", "get_tts", "get_stt", "MediaError", "guess_lang"]
