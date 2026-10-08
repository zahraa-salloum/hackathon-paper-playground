from array import array
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave

from meeting_assistant import capture, providers


class FakeStore:
    def __init__(self, path, retain_audio=True, previous=None):
        self.data_dir = Path(path)
        self.settings = {"transcription_provider": "openai", "transcription_model": "whisper-1",
                         "transcription_base_url": "http://localhost:9999/v1", "retain_audio": retain_audio, "chunk_seconds": 1}
        self.meeting = {"id": "meeting-test", "consent": True, "status": "ready", "segments": list(previous or [])}
        self.lock = threading.Lock()

    def get_meeting(self, meeting_id):
        if meeting_id != self.meeting["id"]:
            raise KeyError(meeting_id)
        return self.meeting

    def get_settings(self):
        return self.settings.copy()

    def update_meeting(self, meeting_id, **fields):
        with self.lock:
            self.meeting.update(fields)

    def add_segments(self, meeting_id, segments):
        with self.lock:
            self.meeting["segments"].extend(segments)


class FakeRecorder:
    def __init__(self, manager, frames, fail=False):
        self.manager = manager
        self.frames = frames
        self.fail = fail
        self.read = False
        self.waiting = threading.Event()
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed.set()

    def record(self, numframes):
        if self.fail:
            raise RuntimeError("secret-example-key private backend error")
        if not self.read:
            self.read = True
            return self.frames
        self.waiting.set()
        self.manager._stop.wait(2)
        return []


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FakeStore(self.temp.name)
        self.manager = capture.CaptureManager(self.store)
        self.manager.SAMPLE_RATE = 10
        self.manager.STOP_WAIT_SECONDS = 2
        self.addCleanup(self.manager.close)
        # Tests exercise no operating-system audio or COM facilities.
        audio_context = patch.object(capture, "_audio_thread_context", side_effect=lambda: nullcontext())
        audio_context.start()
        self.addCleanup(audio_context.stop)

    def fake_devices(self, frames=12, fail=False):
        recorders = {}
        devices = {}
        for channel in ("microphone", "system"):
            recorder = FakeRecorder(self.manager, [[0.5, 0.1]] * frames, fail=fail)
            device = SimpleNamespace(id=channel, name=channel.title(), isloopback=channel == "system", recorder=MagicMock(return_value=recorder))
            recorders[channel] = recorder
            devices[channel] = device
        soundcard = MagicMock()
        soundcard.default_microphone.return_value = devices["microphone"]
        soundcard.default_speaker.return_value = SimpleNamespace(id="system", name="Speakers")
        soundcard.get_microphone.side_effect = lambda id, include_loopback=False: devices["system"] if include_loopback else devices["microphone"]
        soundcard.get_speaker.return_value = soundcard.default_speaker.return_value
        soundcard.all_microphones.side_effect = lambda include_loopback=False: list(devices.values()) if include_loopback else [devices["microphone"]]
        soundcard.all_speakers.return_value = [soundcard.default_speaker.return_value]
        return soundcard, recorders, devices

    def wait_until_done(self):
        self.manager._processor.join(timeout=3)
        self.assertFalse(self.manager.status()["active"], self.manager.status())

    def test_recording_consent_checked_before_touching_hardware(self):
        with patch.object(capture, "_load_soundcard") as loader:
            for consent in (False, "true", 1):
                with self.subTest(consent=consent), self.assertRaisesRegex(capture.CaptureError, "consent"):
                    self.manager.start("meeting-test", consent=consent)
            loader.assert_not_called()

    def test_meeting_consent_and_channel_selection_are_required(self):
        self.store.meeting["consent"] = False
        with self.assertRaisesRegex(capture.CaptureError, "consent"):
            self.manager.start("meeting-test", consent=True)
        with self.assertRaisesRegex(capture.CaptureError, "at least one"):
            self.manager.start("meeting-test", capture_microphone=False, capture_system=False, consent=True)

    def test_transcription_config_and_external_consent_checked_before_hardware(self):
        self.store.settings["transcription_provider"] = "manual"
        with patch.object(capture, "_load_soundcard") as loader:
            with self.assertRaises(providers.ProviderError):
                self.manager.start("meeting-test", consent=True)
            self.store.settings.update(transcription_provider="openai", transcription_base_url="https://api.openai.com/v1")
            with self.assertRaisesRegex(providers.ProviderError, "external processing consent"):
                self.manager.start("meeting-test", consent=True)
            loader.assert_not_called()

    def test_devices_failure_is_safe_and_actionable(self):
        with patch.object(capture, "_load_soundcard", side_effect=capture.CaptureError("Install capture dependencies")):
            status = self.manager.devices()
        self.assertFalse(status["available"])
        self.assertIn("Install", status["error"])

    def test_pcm_stereo_mix_clipping_and_wav_header(self):
        pcm = capture._to_pcm16([[1, -1], [2, 2], [-2, -2], [float("nan"), 0]])
        values = array("h")
        values.frombytes(pcm)
        self.assertEqual(values.tolist(), [0, 32767, -32767, 0])
        output = self.store.data_dir / "test.wav"
        capture._write_wav(output, pcm, 10)
        with wave.open(str(output), "rb") as audio:
            self.assertEqual((audio.getframerate(), audio.getsampwidth(), audio.getnchannels(), audio.getnframes()), (10, 2, 1, 4))

    def test_separate_channels_continuous_timestamps_and_partial_flush(self):
        self.store.meeting["segments"] = [{"start": 0, "end": 15, "text": "Previously"}]
        soundcard, recorders, devices = self.fake_devices()
        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", return_value=[{"start": 0.1, "end": 0.5, "text": "Captured"}]):
            status = self.manager.start("meeting-test", capture_system=True, consent=True)
            self.assertEqual(status["channels"], ["microphone", "system"])
            self.assertTrue(recorders["microphone"].waiting.wait(2))
            self.assertTrue(recorders["system"].waiting.wait(2))
            stopped = self.manager.stop()
        self.assertFalse(stopped["active"])
        self.assertEqual(stopped["segments"], 4)
        self.assertEqual(self.store.meeting["status"], "completed")
        for channel in ("microphone", "system"):
            parts = [part for part in self.store.meeting["segments"] if part.get("channel") == channel]
            self.assertEqual(len(parts), 2)
            self.assertAlmostEqual(parts[1]["start"] - parts[0]["start"], 1.0, places=3)
            self.assertGreaterEqual(parts[0]["start"], 15.1)
            self.assertAlmostEqual(parts[1]["end"] - parts[1]["start"], 0.1, places=3)
            self.assertEqual(parts[0]["audio_offset"], 0.1)
            self.assertTrue(recorders[channel].closed.is_set())
            self.assertNotIn("channels", devices[channel].recorder.call_args.kwargs)
            for index, part in enumerate(parts):
                with wave.open(str(self.store.data_dir / "audio" / "meeting-test" / part["audio_file"]), "rb") as audio:
                    self.assertEqual(audio.getnframes(), 10 if index == 0 else 2)
        self.assertEqual(len(list((self.store.data_dir / "audio" / "meeting-test").glob("*.wav"))), 4)

    def test_no_retention_removes_audio_after_transcription(self):
        self.store.settings["retain_audio"] = False
        soundcard, recorders, _ = self.fake_devices(frames=2)
        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", return_value=[{"start": 0, "end": 0.2, "text": "Captured"}]):
            self.manager.start("meeting-test", consent=True)
            self.assertTrue(recorders["microphone"].waiting.wait(2))
            self.manager.stop()
        self.assertFalse(list((self.store.data_dir / "audio" / "meeting-test").glob("*.wav")))
        self.assertNotIn("audio_file", self.store.meeting["segments"][0])

    def test_transcription_error_stops_capture_cleans_audio_and_redacts_key(self):
        self.store.settings.update(retain_audio=False, transcription_api_key="secret-example-key")
        soundcard, _, _ = self.fake_devices()
        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", side_effect=providers.ProviderError("Bad secret-example-key")):
            self.manager.start("meeting-test", consent=True)
            self.wait_until_done()
        status = self.manager.status()
        self.assertEqual(status["state"], "error")
        self.assertNotIn("secret-example-key", status["error"])
        self.assertEqual(self.store.meeting["status"], "error")
        self.assertFalse(list((self.store.data_dir / "audio" / "meeting-test").glob("*.wav")))

    def test_device_error_does_not_disclose_backend_details(self):
        soundcard, _, _ = self.fake_devices(fail=True)
        with patch.object(capture, "_load_soundcard", return_value=soundcard):
            self.manager.start("meeting-test", consent=True)
            self.wait_until_done()
        self.assertIn("Microphone recording failed", self.manager.status()["error"])
        self.assertNotIn("secret-example-key", self.manager.status()["error"])

    def test_stop_returns_stopping_until_slow_transcription_finishes(self):
        self.manager.STOP_WAIT_SECONDS = 0.02
        soundcard, recorders, _ = self.fake_devices(frames=2)
        in_transcription = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def transcribe(*args):
            in_transcription.set()
            release.wait(3)
            return [{"start": 0, "end": 0.2, "text": "Flushed"}]

        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", side_effect=transcribe):
            self.manager.start("meeting-test", consent=True)
            self.assertTrue(recorders["microphone"].waiting.wait(2))
            status = self.manager.stop()
            self.assertTrue(in_transcription.wait(2))
            self.assertTrue(status["active"])
            self.assertEqual(status["state"], "stopping")
            with self.assertRaisesRegex(capture.CaptureError, "already active"):
                self.manager.start("meeting-test", consent=True)
            release.set()
            self.wait_until_done()
        self.assertEqual(self.manager.status()["segments"], 1)

    def test_loopback_failure_does_not_silently_record_microphone(self):
        soundcard, _, devices = self.fake_devices()
        soundcard.all_microphones.side_effect = lambda **kwargs: [devices["microphone"]]
        soundcard.get_microphone.side_effect = lambda **kwargs: devices["microphone"]
        with patch.object(capture, "_load_soundcard", return_value=soundcard), self.assertRaisesRegex(capture.CaptureError, "loopback"):
            self.manager.start("meeting-test", capture_system=True, capture_microphone=False, consent=True)
        self.assertFalse(self.manager.status()["active"])

    def test_close_drains_transcription_before_returning(self):
        self.manager.STOP_WAIT_SECONDS = 0.01
        soundcard, recorders, _ = self.fake_devices(frames=2)
        processing = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        self.addCleanup(release.set)

        def transcribe(*args):
            processing.set()
            release.wait(3)
            return [{"start": 0, "end": 0.2, "text": "Saved before shutdown"}]

        def close():
            self.manager.close()
            closed.set()

        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", side_effect=transcribe):
            self.manager.start("meeting-test", consent=True)
            self.assertTrue(recorders["microphone"].waiting.wait(2))
            shutdown = threading.Thread(target=close, daemon=True)
            shutdown.start()
            self.assertTrue(processing.wait(2))
            self.assertFalse(closed.wait(0.05))
            release.set()
            shutdown.join(2)
        self.assertTrue(closed.is_set())
        self.assertEqual(self.store.meeting["segments"][0]["text"], "Saved before shutdown")

    def test_backlog_stops_capture_without_unbounded_queue(self):
        self.manager.MAX_PENDING_CHUNKS = 1
        self.store.settings["retain_audio"] = False
        soundcard, _, _ = self.fake_devices(frames=80)
        release = threading.Event()
        self.addCleanup(release.set)

        def transcribe(*args):
            release.wait(2)
            return []

        with patch.object(capture, "_load_soundcard", return_value=soundcard), patch.object(providers, "transcribe_audio", side_effect=transcribe):
            self.manager.start("meeting-test", consent=True)
            self.assertTrue(self.manager._stop.wait(2))
            self.assertLessEqual(self.manager.status()["pending_chunks"], 1)
            release.set()
            self.wait_until_done()
        self.assertIn("cannot keep up", self.manager.status()["error"])
        self.assertFalse(list((self.store.data_dir / "audio" / "meeting-test").glob("*.wav")))


if __name__ == "__main__":
    unittest.main()
