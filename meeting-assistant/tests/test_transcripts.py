import unittest

from meeting_assistant.transcripts import parse_transcript, seconds


class TranscriptTests(unittest.TestCase):
    def test_srt_keeps_times_and_unverified_label(self):
        result = parse_transcript("1\n00:01:02,500 --> 00:01:04,200\nAlex: Ship on Friday.\n\n2\n00:01:07,000 --> 00:01:10,000\nConfirmed.")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["start"], 62.5)
        self.assertEqual(result[0]["end"], 64.2)
        self.assertEqual(result[0]["speaker"], "Alex")
        self.assertEqual(result[1]["speaker"], "Unverified speaker")

    def test_vtt_voice_markup_and_settings(self):
        result = parse_transcript("WEBVTT\n\n00:02.000 --> 00:05.500 align:start\n<v Guest>Keep the archive <b>local</b>.</v>")
        self.assertEqual(result[0]["text"], "Keep the archive local.")
        self.assertEqual(result[0]["speaker"], "Guest")

    def test_plain_and_stamped_text(self):
        plain = parse_transcript("A passage without timestamps.\nAnother passage.")
        self.assertEqual([p["start"] for p in plain], [0, 0])
        stamped = parse_transcript("[02:13] Speaker B: A recorded decision.")
        self.assertEqual(stamped[0]["start"], 133)
        self.assertEqual(stamped[0]["speaker"], "Speaker B")

    def test_export_json_reimport_excludes_internal_fields(self):
        result = parse_transcript('{"segments":[{"start":1,"end":2,"text":"hello","audio_file":"../../secret"}]}')
        self.assertNotIn("audio_file", result[0])

    def test_bad_input_rejected(self):
        for text, fmt in [("", "auto"), ('[]', 'json'), ('{"segments":[{"text":"x","start":3,"end":2}]}', 'json'), ('not subtitles', 'vtt')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_transcript(text, fmt)
        for value in (float("nan"), float("inf"), -1, "00:99:00", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                seconds(value)


if __name__ == "__main__":
    unittest.main()
