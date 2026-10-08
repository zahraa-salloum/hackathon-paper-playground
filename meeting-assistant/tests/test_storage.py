import concurrent.futures
import os
from pathlib import Path
import tempfile
import unittest

from meeting_assistant.storage import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "archive")
        self.meeting = self.store.create_meeting("Planning", consent=True)
        self.mid = self.meeting["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_segments_messages_and_persistence(self):
        added = self.store.add_segments(self.mid, [{"start": 20, "end": 25, "text": "Launch on Friday",
            "speaker": "Speaker A", "channel": "microphone", "audio_file": "chunk.wav", "audio_offset": 1.5},
            {"start": 1, "end": 2, "text": "Agenda"}])
        reopened = Store(self.store.data_dir)
        meeting = reopened.get_meeting(self.mid)
        self.assertEqual(meeting["segment_count"], 2)
        self.assertEqual(meeting["segments"][0]["text"], "Agenda")
        self.assertEqual(meeting["segments"][1]["audio_offset"], 1.5)
        self.assertEqual(meeting["segments"][1]["id"], added[0]["id"])
        reopened.add_message(self.mid, "assistant", "Friday [E1]", {"citations": [{"id": "E1"}]})
        self.assertEqual(reopened.list_messages(self.mid)[0]["metadata"]["citations"][0]["id"], "E1")
        self.assertTrue(meeting["consent"])

    def test_invalid_batch_is_atomic(self):
        with self.assertRaises(ValueError):
            self.store.add_segments(self.mid, [{"text": "valid"}, {"text": "bad", "start": 5, "end": 1}])
        self.assertEqual(self.store.get_meeting(self.mid)["segment_count"], 0)
        for invalid in (float("inf"), float("nan"), -1):
            with self.assertRaises(ValueError):
                self.store.add_segments(self.mid, [{"text": "bad", "start": invalid}])
        with self.assertRaises(ValueError):
            self.store.add_segments(self.mid, [{"text": "bad", "audio_file": "../outside.wav"}])

    def test_safe_fts_queries_and_empty_scope(self):
        second = self.store.create_meeting("Another")
        self.store.add_segments(self.mid, [{"text": "Launch ownership belongs to Maya"}])
        self.store.add_segments(second["id"], [{"text": "Launch on Monday"}])
        self.assertEqual(len(self.store.search("launch")), 2)
        hits = self.store.search('launch OR " * NEAR( DROP TABLE meetings;', [self.mid])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["meeting_id"], self.mid)
        self.assertEqual(self.store.search("launch", []), [])
        self.assertEqual(self.store.all_segments([]), [])
        self.assertEqual(self.store.search('***"()'), [])
        self.assertEqual(len(self.store.list_meetings()), 2)
        self.store.has_fts = False
        self.assertEqual(len(self.store.search("launch", [self.mid])), 1)

    def test_settings_preserve_keys_and_validate(self):
        self.store.save_settings({"chat_api_key": "test-secret", "chat_model": "model-one"})
        self.store.save_settings({"chat_model": "model-two"})
        self.assertEqual(self.store.get_settings()["chat_api_key"], "test-secret")
        self.assertEqual(Store(self.store.data_dir).get_settings()["chat_model"], "model-two")
        with self.assertRaises(ValueError):
            self.store.save_settings({"external_processing_consent": "true"})
        with self.assertRaises(ValueError):
            self.store.save_settings({"chunk_seconds": 0})
        with self.assertRaises(ValueError):
            self.store.save_settings({"chat_provider": "unknown"})
        self.store.save_settings({"chat_api_key": ""})
        self.assertEqual(self.store.get_settings()["chat_api_key"], "")
        if os.name != "nt":
            self.assertEqual(self.store.db_path.stat().st_mode & 0o777, 0o600)

    def test_delete_cascades_and_only_removes_owned_audio(self):
        self.store.add_segments(self.mid, [{"text": "Launch Friday"}])
        self.store.add_message(self.mid, "user", "When?")
        owned = self.store.data_dir / "audio" / self.mid
        owned.mkdir(parents=True)
        (owned / "chunk.wav").write_bytes(b"test")
        unrelated = self.store.data_dir / "audio" / "keep.wav"
        unrelated.write_bytes(b"keep")
        self.store.delete_meeting(self.mid)
        self.assertFalse(owned.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.store.search("launch"), [])
        self.assertEqual(self.store.all_segments(), [])
        with self.assertRaises(KeyError):
            self.store.list_messages(self.mid)

    def test_missing_ids_and_updates(self):
        for operation in (lambda: self.store.get_meeting("missing"),
                          lambda: self.store.add_segments("missing", []),
                          lambda: self.store.update_meeting("missing", title="x"),
                          lambda: self.store.delete_meeting("missing")):
            with self.assertRaises(KeyError):
                operation()
        changed = self.store.update_meeting(self.mid, title="New", status="archived")
        self.assertEqual(changed["title"], "New")
        with self.assertRaises(ValueError):
            self.store.update_meeting(self.mid, id="changed")

    def test_parallel_capture_writes(self):
        def insert(index):
            return self.store.add_segments(self.mid, [{"text": f"Segment number {index}", "start": index, "end": index + 1}])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(insert, range(20)))
        self.assertEqual(self.store.get_meeting(self.mid)["segment_count"], 20)
        self.assertEqual(len(self.store.search("segment", limit=50)), 20)


if __name__ == "__main__":
    unittest.main()
