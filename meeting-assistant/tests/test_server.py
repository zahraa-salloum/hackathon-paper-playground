import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from meeting_assistant.server import AppServer


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = AppServer(("127.0.0.1", 0), Path(cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)
        cls.temp.cleanup()

    def request(self, path, method="GET", data=None, headers=None, authorized=True):
        merged = {"X-App-Token": self.server.token} if authorized else {}
        merged.update(headers or {})
        if data is not None:
            data = json.dumps(data).encode()
            merged["Content-Type"] = "application/json"
        try:
            response = urlopen(Request(self.base + path, data=data, headers=merged, method=method), timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            content = json.loads(raw) if response.headers.get("Content-Type", "").startswith("application/json") else raw.decode()
            return response.status, content

    def test_end_to_end_import_ask_export_delete(self):
        status, meeting = self.request("/api/meetings", "POST", {"title": "Test release", "consent": True})
        self.assertEqual(status, 201)
        mid = meeting["id"]
        status, body = self.request(f"/api/meetings/{mid}/transcript", "POST", {"text": "[00:10] Speaker A: The release deadline is November 12. Nora owns release testing."})
        self.assertEqual(status, 201)
        self.assertEqual(body["imported"], 1)
        status, answer = self.request("/api/ask", "POST", {"question": "What is the release deadline?", "meeting_id": mid, "meeting_ids": [mid]})
        self.assertEqual(status, 200, answer)
        self.assertIn("November 12", answer["answer"])
        self.assertEqual(answer["citations"][0]["meeting_id"], mid)
        status, exported = self.request(f"/api/meetings/{mid}/export")
        self.assertEqual(len(exported["messages"]), 2)
        self.assertEqual(self.request(f"/api/meetings/{mid}", "DELETE")[0], 200)
        self.assertEqual(self.request(f"/api/meetings/{mid}")[0], 404)

    def test_consent_and_request_isolation(self):
        self.assertEqual(self.request("/api/meetings", "POST", {"title": "Missing consent"})[0], 400)
        self.assertEqual(self.request("/api/meetings", authorized=False)[0], 403)
        self.assertEqual(self.request("/api/meetings", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/meetings", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/../README.md")[0], 404)

    def test_keys_redacted_preserved_and_clearable(self):
        status, settings = self.request("/api/settings", "PUT", {"chat_api_key": "secret-for-test"})
        self.assertEqual(status, 200)
        self.assertNotIn("secret-for-test", json.dumps(settings))
        self.assertTrue(settings["chat_api_key_configured"])
        self.request("/api/settings", "PUT", {"retain_audio": False})
        self.assertEqual(self.server.store.get_settings()["chat_api_key"], "secret-for-test")
        self.request("/api/settings", "PUT", {"chat_api_key": "", "retain_audio": True})
        self.assertFalse(self.request("/api/settings")[1]["chat_api_key_configured"])

    def test_demo_idempotency_and_followup(self):
        first = self.request("/api/demo", "POST", {})[1]
        second = self.request("/api/demo", "POST", {})[1]
        self.assertEqual({m["id"] for m in first["meetings"]}, {m["id"] for m in second["meetings"]})
        mid = first["meetings"][0]["id"]
        status, result = self.request("/api/ask", "POST", {"meeting_id": mid, "meeting_ids": None, "question": "Why did the Atlas pilot launch date change?"})
        self.assertEqual(status, 200)
        self.assertTrue(result["citations"])

    def test_bodyless_actions_used_by_frontend(self):
        self.assertEqual(self.request("/api/demo", "POST")[0], 201)
        self.assertEqual(self.request("/api/capture/stop", "POST")[0], 200)

    def test_token_injected_and_static_available(self):
        status, page = self.request("/", authorized=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, page)
        self.assertNotIn("__APP_TOKEN__", page)
        self.assertEqual(self.request("/app.js", authorized=False)[0], 200)

    def test_audio_requires_consent_and_provider(self):
        mid = self.request("/api/meetings", "POST", {"title": "Audio test", "consent": True})[1]["id"]
        self.assertEqual(self.request(f"/api/meetings/{mid}/audio?filename=x.wav", "POST", {})[0], 400)
        self.assertEqual(self.request(f"/api/meetings/{mid}/audio?filename=x.wav&consent=true", "POST", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
