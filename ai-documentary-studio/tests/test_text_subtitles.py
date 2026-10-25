import unittest

import helpers  # noqa: F401  (sets sys.path)
from studio import subtitles, textutil
from studio.textutil import RLM

AR = "في عام ١٥٦٩ نشر جيراردوس مركاتور خريطة صُممت للملاحة البحرية، وهي ما زالت تُستخدم حتى اليوم في كثير من التطبيقات."


class TextHandling(unittest.TestCase):
    def test_arabic_detection(self):
        self.assertTrue(textutil.is_arabic("مرحبا بكم"))
        self.assertTrue(textutil.is_arabic("مرحبا NASA"))
        self.assertFalse(textutil.is_arabic("Hello مرحبا world again"))
        self.assertFalse(textutil.is_arabic("123"))

    def test_word_count_ignores_diacritics(self):
        self.assertEqual(textutil.word_count("مَرْحَبًا بِكُمْ"), 2)
        self.assertEqual(textutil.word_count("Hello, world — again!"), 3)

    def test_duration_estimate_scales_with_rate(self):
        slow = textutil.estimate_duration(AR, 100)
        fast = textutil.estimate_duration(AR, 200)
        self.assertGreater(slow, fast)
        self.assertEqual(textutil.estimate_duration("", 130), 0.0)

    def test_wrap_balanced_and_bounded(self):
        out = textutil.wrap_lines(AR[:80], 42)
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l) <= 45 for l in lines))

    def test_chunks_respect_budget(self):
        chunks = textutil.split_subtitle_chunks(AR + " " + AR, 42, 2)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            for line in c.split("\n"):
                self.assertLessEqual(textutil.display_len(line), 50)

    def test_slugify(self):
        self.assertEqual(textutil.slugify("The Lost City!"), "The_Lost_City")
        self.assertEqual(textutil.slugify("مدينة"), "project")


class SubtitleFormats(unittest.TestCase):
    cues = [{"start": 0.25, "end": 2.5, "text": "هل تعلم؟"}, {"start": 2.6, "end": 61.123, "text": "Line one\nLine two"}]

    def test_time_format_roundtrip(self):
        self.assertEqual(subtitles.fmt_time(3661.5), "01:01:01,500")
        self.assertAlmostEqual(subtitles.parse_time("01:01:01,500"), 3661.5)
        self.assertAlmostEqual(subtitles.parse_time("00:01.250"), 1.25)
        with self.assertRaises(subtitles.SubtitleError):
            subtitles.parse_time("1:2")

    def test_srt_roundtrip_with_arabic(self):
        srt = subtitles.to_srt(self.cues)
        self.assertIn(RLM + "هل تعلم؟", srt)  # RTL mark for players
        self.assertIn("00:00:02,600 --> 00:01:01,123", srt)
        back = subtitles.parse_srt(srt)
        self.assertEqual([c["text"] for c in back], ["هل تعلم؟", "Line one\nLine two"])
        self.assertAlmostEqual(back[1]["end"], 61.123)
        srt.encode("utf-8")

    def test_vtt_roundtrip_and_bom(self):
        vtt = subtitles.to_vtt(self.cues)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:00.250 --> 00:00:02.500", vtt)
        back = subtitles.parse_srt("﻿" + vtt)
        self.assertEqual(len(back), 2)

    def test_parse_rejects_reversed_cue(self):
        with self.assertRaises(subtitles.SubtitleError):
            subtitles.parse_srt("1\n00:00:05,000 --> 00:00:04,000\nx\n")

    def test_validate_flags_problems(self):
        probs = subtitles.validate_cues([{"start": 0, "end": 2, "text": "a"}, {"start": 1, "end": 3, "text": ""},
                                         {"start": 4, "end": 4, "text": "x" * 70}])
        joined = " ".join(probs)
        self.assertIn("overlaps", joined)
        self.assertIn("empty", joined)
        self.assertIn("end is not after start", joined)
        self.assertIn("longer than 60", joined)
        self.assertEqual(subtitles.validate_cues(self.cues), [])

    def test_distribute_fills_span_in_order(self):
        cues = subtitles.distribute(AR + " " + AR, 1.0, 11.0)
        self.assertGreater(len(cues), 1)
        self.assertAlmostEqual(cues[0]["start"], 1.0)
        self.assertLessEqual(cues[-1]["end"], 11.0)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"])
        self.assertEqual(subtitles.validate_cues(cues), [])

    def test_bilingual_pairs_by_time(self):
        ar = [{"start": 0, "end": 2, "text": "أ"}, {"start": 2, "end": 4, "text": "ب"}]
        en = [{"start": 0.1, "end": 1.9, "text": "A"}, {"start": 2.1, "end": 3.9, "text": "B"}]
        out = subtitles.bilingual(ar, en)
        self.assertEqual([c["text"] for c in out], ["أ\nA", "ب\nB"])

    def test_ass_helpers(self):
        self.assertEqual(subtitles.ass_color("#E8B04B"), "&H004BB0E8")
        self.assertEqual(subtitles.ass_color("bad", 1.0), "&HFFFFFFFF")
        self.assertEqual(subtitles.ass_escape("a{b}\nc"), "a(b)\\Nc")
        self.assertEqual(subtitles.ass_time(3725.456), "1:02:05.46")
        ev = subtitles.subtitle_events([{"start": 0, "end": 1, "text": "مرحبا"}], "SubAR")[0]
        self.assertIn(RLM + "مرحبا", ev)


if __name__ == "__main__":
    unittest.main()
