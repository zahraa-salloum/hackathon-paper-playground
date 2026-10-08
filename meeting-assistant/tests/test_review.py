"""Regression checks from integration review: imported audio and request auth."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from meeting_assistant.server import AppServer


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = AppServer(("127.0.0.1", 0), Path(cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)
        cls.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None, authorized=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        merged = {"X-App-Token": self.server.token} if authorized else {}
        merged.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def test_imported_audio_keeps_source_offset_for_playback(self):
        mid = self.server.store.create_meeting("Imported audio offset")["id"]
        self.server.store.save_settings({"transcription_provider": "faster_whisper", "retain_audio": True})
        try:
            with patch("meeting_assistant.server.transcribe_audio", return_value=[
                {"start": 5.5, "end": 8.0, "text": "Evidence occurs later in the source file."},
                {"start": 13.2, "end": 15.0, "text": "A second statement."},
            ]):
                status, _, raw = self.request(f"/api/meetings/{mid}/audio?filename=sample.wav&consent=true",
                                              "POST", b"mock-audio-bytes", {"Content-Type": "application/octet-stream"})
            self.assertEqual(status, 201, raw)
            segments = json.loads(raw)["meeting"]["segments"]
            self.assertEqual([s["audio_offset"] for s in segments], [5.5, 13.2])
        finally:
            self.server.store.delete_meeting(mid)

    def test_non_ascii_token_is_rejected_with_http_response(self):
        status, _, _ = self.request("/api/audio/missing/missing.wav?token=%C3%A9", authorized=False)
        self.assertEqual(status, 403)

    def test_audio_range_auth_and_reference_membership(self):
        mid = self.server.store.create_meeting("Audio range")["id"]
        folder = self.server.store.data_dir / "audio" / mid
        folder.mkdir(parents=True)
        (folder / "record.wav").write_bytes(b"0123456789")
        (folder / "unreferenced.wav").write_bytes(b"private")
        self.server.store.add_segments(mid, [{"text": "A retained source", "start": 0, "end": 1,
                                             "audio_file": "record.wav"}])
        try:
            path = f"/api/audio/{mid}/record.wav"
            self.assertEqual(self.request(path, authorized=False)[0], 403)
            status, headers, body = self.request(path + "?token=" + self.server.token,
                                                  headers={"Range": "bytes=2-5"}, authorized=False)
            self.assertEqual(status, 206)
            self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(body, b"2345")
            self.assertEqual(self.request(path, headers={"Range": "bytes=50-"})[0], 416)
            self.assertEqual(self.request(f"/api/audio/{mid}/unreferenced.wav")[0], 404)
        finally:
            self.server.store.delete_meeting(mid)


if __name__ == "__main__":
    unittest.main()
