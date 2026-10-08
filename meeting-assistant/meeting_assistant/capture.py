"""Consent-gated microphone and system-loopback capture with separate workers.

SoundCard is imported only when devices or recording are requested. A bounded
queue separates hardware reads from slow transcription. Stop flushes each
partial chunk and drains that queue before marking the meeting complete.
"""
from __future__ import annotations

from array import array
from contextlib import contextmanager
import importlib
import math
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
import wave

from . import providers


class CaptureError(RuntimeError):
    """A recording failure that is safe to display to the user."""


@contextmanager
def _audio_thread_context():
    """WASAPI uses COM, which must be initialized separately on each thread."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    result = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    # RPC_E_CHANGED_MODE means a native window already initialized this thread
    # in an STA; it can still use COM and we must leave its reference untouched.
    if result not in (0, 1, -2147417850):
        raise CaptureError("Cannot initialize the Windows audio thread. Restart the application and try again.")
    try:
        yield
    finally:
        if result in (0, 1):
            ole32.CoUninitialize()


def _load_soundcard():
    try:
        return importlib.import_module("soundcard")
    except ImportError:
        raise CaptureError('Audio capture requires SoundCard. Install with: pip install -e ".[capture]"') from None
    except Exception:
        raise CaptureError("Cannot initialize the audio backend. Check your operating system audio service and recording permissions.") from None


def _to_pcm16(frames):
    """Mix all input channels into clipped little-endian signed 16-bit PCM."""
    values = frames.tolist() if hasattr(frames, "tolist") else frames
    pcm = array("h")
    for frame in values:
        sample = sum(frame) / len(frame) if isinstance(frame, (list, tuple)) and frame else frame
        sample = float(sample)
        if not math.isfinite(sample):
            sample = 0.0
        pcm.append(round(max(-1.0, min(1.0, sample)) * 32767))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def _write_wav(path, pcm, sample_rate):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)


class CaptureManager:
    SAMPLE_RATE = 16000
    BLOCK_SECONDS = 0.1
    MAX_PENDING_CHUNKS = 32
    STOP_WAIT_SECONDS = 10

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._active = False
        self._state = "idle"
        self._meeting_id = None
        self._channels = []
        self._segments = 0
        self._error = None
        self._started = None
        self._ended = None
        self._settings = {}
        self._stop = threading.Event()
        self._producers_done = threading.Event()
        self._queue = queue.Queue(maxsize=self.MAX_PENDING_CHUNKS)
        self._workers = []
        self._processor = None

    def devices(self):
        try:
            with _audio_thread_context():
                soundcard = _load_soundcard()
                microphones = [{"id": str(device.id), "name": str(device.name)}
                               for device in soundcard.all_microphones(include_loopback=False)]
                speakers = [{"id": str(device.id), "name": str(device.name)} for device in soundcard.all_speakers()]
            return {"microphones": microphones, "speakers": speakers, "available": True}
        except CaptureError as exc:
            return {"microphones": [], "speakers": [], "available": False, "error": str(exc)}
        except Exception:
            return {"microphones": [], "speakers": [], "available": False,
                    "error": "Cannot enumerate audio devices. Check operating system audio permissions and reconnect the device."}

    def status(self):
        with self._lock:
            elapsed = 0.0 if self._started is None else max(0.0, (self._ended or time.monotonic()) - self._started)
            return {"active": self._active, "meeting_id": self._meeting_id, "elapsed": round(elapsed, 2),
                    "segments": self._segments, "error": self._error, "channels": list(self._channels),
                    "state": self._state, "pending_chunks": self._queue.qsize()}

    def _set_error(self, message):
        with self._lock:
            if self._error is None:
                self._error = providers.safe_error(message, self._settings)
            if self._ended is None:
                self._ended = time.monotonic()
            self._state = "stopping"
            self._stop.set()

    @staticmethod
    def _select_devices(soundcard, microphone_id, speaker_id, capture_microphone, capture_system):
        selected = []
        try:
            if capture_microphone:
                microphone = soundcard.get_microphone(id=microphone_id, include_loopback=False) if microphone_id else soundcard.default_microphone()
                if microphone is None:
                    raise CaptureError("No microphone is available. Connect one or disable microphone capture.")
                selected.append(("microphone", microphone))
            if capture_system:
                speaker = soundcard.get_speaker(id=speaker_id) if speaker_id else soundcard.default_speaker()
                if speaker is None:
                    raise CaptureError("No speaker output is available. Select an output device or disable system capture.")
                # Linux monitor IDs differ from speaker IDs; prefer explicit loopback
                # discovery, then the native SoundCard lookup (WASAPI uses speaker ID).
                loopbacks = soundcard.all_microphones(include_loopback=True)
                candidates = [mic for mic in loopbacks if getattr(mic, "isloopback", False)
                              and (str(mic.id) == str(speaker.id) or str(mic.id) == str(speaker.id) + ".monitor")]
                device = candidates[0] if candidates else soundcard.get_microphone(id=str(speaker.id), include_loopback=True)
                if device is None or not getattr(device, "isloopback", False):
                    raise CaptureError("This output has no system-loopback input. Use a supported Windows/PulseAudio device, or install a virtual audio input on macOS.")
                selected.append(("system", device))
            return selected
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Cannot open the selected audio devices. Refresh the device list and check microphone/system-audio permissions.") from None

    def start(self, meeting_id, microphone_id=None, speaker_id=None, capture_microphone=True,
              capture_system=False, consent=False):
        with self._lifecycle_lock:
            with self._lock:
                if self._active:
                    raise CaptureError("A recording is already active or finishing. Stop it and wait for transcription to finish.")
            if consent is not True:
                raise CaptureError("Confirm recording consent from all participants before starting capture.")
            if not isinstance(capture_microphone, bool) or not isinstance(capture_system, bool):
                raise CaptureError("Capture channel selections must be true or false.")
            if not capture_microphone and not capture_system:
                raise CaptureError("Select at least one audio channel to record.")
            meeting = self.store.get_meeting(meeting_id)
            if not meeting.get("consent"):
                raise CaptureError("This meeting does not have recording consent. Create a meeting with participant consent first.")
            settings = dict(self.store.get_settings())
            providers.validate_transcription_settings(settings)
            try:
                chunk_seconds = float(settings.get("chunk_seconds", 8))
                if not math.isfinite(chunk_seconds) or not 1 <= chunk_seconds <= 60:
                    raise ValueError("invalid duration")
            except (TypeError, ValueError):
                raise CaptureError("Recording chunk duration must be between 1 and 60 seconds.") from None
            # The stored meeting ID must also be a single safe directory component.
            audio_root = (Path(self.store.data_dir) / "audio").resolve()
            output = (audio_root / str(meeting_id)).resolve()
            if output.parent != audio_root or not str(meeting_id) or str(meeting_id) in (".", ".."):
                raise CaptureError("Invalid recording meeting ID.")
            with _audio_thread_context():
                soundcard = _load_soundcard()
                selected = self._select_devices(soundcard, microphone_id, speaker_id, capture_microphone, capture_system)
            try:
                output.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise CaptureError("Cannot create the recording folder. Check the data-folder permissions and free disk space.") from None
            self.store.update_meeting(meeting_id, status="recording")
            with self._lock:
                self._settings = settings
                self._meeting_id = meeting_id
                self._channels = [channel for channel, _ in selected]
                self._segments = 0
                self._error = None
                self._started = time.monotonic()
                self._ended = None
                self._state = "recording"
                self._active = True
                self._stop = threading.Event()
                self._producers_done = threading.Event()
                self._queue = queue.Queue(maxsize=self.MAX_PENDING_CHUNKS)
                self._remaining = len(selected)
                self._timeline_offset = max((float(segment.get("end", 0)) for segment in meeting.get("segments", [])), default=0)
                self._session_id = uuid.uuid4().hex[:12]
                self._workers = [threading.Thread(target=self._capture_channel, args=(channel, device, output, chunk_seconds),
                                                  name="capture-" + channel, daemon=True) for channel, device in selected]
                self._processor = threading.Thread(target=self._process_chunks, name="meeting-transcription", daemon=True)
            try:
                self._processor.start()
            except RuntimeError:
                self._set_error("Cannot start the transcription worker. Close unused applications and try again.")
                with self._lock:
                    self._processor = None
                    self._state = "error"
                    self._active = False
                self.store.update_meeting(meeting_id, status="error")
                raise CaptureError(self._error) from None
            started = 0
            try:
                for worker in self._workers:
                    worker.start()
                    started += 1
            except RuntimeError:
                with self._lock:
                    self._remaining -= len(self._workers) - started
                    if self._remaining == 0:
                        self._producers_done.set()
                self._set_error("Cannot start audio worker threads. Close unused applications and try again.")
            return self.status()

    def _enqueue(self, channel, output, sequence, pcm, start):
        path = output / f"{channel}_{self._session_id}_{sequence:06d}.wav"
        try:
            _write_wav(path, pcm, self.SAMPLE_RATE)
            self._queue.put_nowait((channel, path, start, len(pcm) / (2 * self.SAMPLE_RATE)))
        except queue.Full:
            if not self._settings.get("retain_audio", True):
                path.unlink(missing_ok=True)
            self._set_error("Transcription cannot keep up with recording. Capture stopped to avoid losing audio. Choose a faster model or longer chunks.")
        except OSError:
            if not self._settings.get("retain_audio", True):
                path.unlink(missing_ok=True)
            self._set_error("Cannot save the audio chunk. Check free disk space and recording-folder permissions.")

    def _capture_channel(self, channel, device, output, chunk_seconds):
        pcm = bytearray()
        chunk_bytes = int(self.SAMPLE_RATE * chunk_seconds) * 2
        frame_cursor = 0
        sequence = 0
        stream_start = None
        try:
            # Keep native input channels here: forcing mono is unreliable on
            # WASAPI. Mix down only after reading samples from the device.
            with _audio_thread_context(), device.recorder(samplerate=self.SAMPLE_RATE, blocksize=int(self.SAMPLE_RATE * self.BLOCK_SECONDS * 2)) as recorder:
                stream_start = self._timeline_offset + max(0, time.monotonic() - self._started)
                while not self._stop.is_set():
                    block = recorder.record(numframes=max(1, int(self.SAMPLE_RATE * self.BLOCK_SECONDS)))
                    block_pcm = _to_pcm16(block)
                    if not block_pcm:
                        self._stop.wait(0.02)
                        continue
                    pcm.extend(block_pcm)
                    while len(pcm) >= chunk_bytes:
                        self._enqueue(channel, output, sequence, bytes(pcm[:chunk_bytes]), stream_start + frame_cursor / self.SAMPLE_RATE)
                        del pcm[:chunk_bytes]
                        frame_cursor += chunk_bytes // 2
                        sequence += 1
        except Exception:
            self._set_error(f"{channel.capitalize()} recording failed. Check the selected device and operating system audio permissions.")
        finally:
            try:
                if pcm and stream_start is not None:
                    self._enqueue(channel, output, sequence, bytes(pcm), stream_start + frame_cursor / self.SAMPLE_RATE)
            except Exception:
                self._set_error("Cannot flush the final audio chunk. Check recording-folder permissions and free disk space.")
            with self._lock:
                self._remaining -= 1
                if self._remaining == 0:
                    self._producers_done.set()

    def _process_chunks(self):
        transcription_failed = False
        try:
            while not self._producers_done.is_set() or not self._queue.empty():
                try:
                    channel, path, chunk_start, duration = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    if not transcription_failed:
                        segments = providers.transcribe_audio(path, self._settings)
                        enriched = []
                        for segment in segments:
                            start = max(0, min(float(segment["start"]), duration))
                            end = max(start, min(float(segment["end"]), duration))
                            item = {"start": round(chunk_start + start, 3), "end": round(chunk_start + end, 3),
                                    "text": segment["text"], "speaker": "Microphone" if channel == "microphone" else "System audio",
                                    "channel": channel}
                            if self._settings.get("retain_audio", True):
                                item.update(audio_file=path.name, audio_offset=round(start, 3))
                            enriched.append(item)
                        if enriched:
                            self.store.add_segments(self._meeting_id, enriched)
                            with self._lock:
                                self._segments += len(enriched)
                except providers.ProviderError as exc:
                    transcription_failed = True
                    self._set_error(str(exc))
                except Exception:
                    transcription_failed = True
                    self._set_error("Cannot transcribe or save the recording. Check the provider settings, database permissions, and free disk space.")
                finally:
                    if not self._settings.get("retain_audio", True):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            self._set_error("Cannot remove a temporary audio chunk. Check data-folder permissions and delete it manually.")
                    self._queue.task_done()
        finally:
            try:
                self.store.update_meeting(self._meeting_id, status="error" if self._error else "completed")
            except Exception:
                self._set_error("Cannot update the meeting status. Check database permissions and free disk space.")
            with self._lock:
                self._ended = self._ended or time.monotonic()
                self._state = "error" if self._error else "completed"
                self._active = False

    def stop(self):
        with self._lifecycle_lock:
            with self._lock:
                if not self._active:
                    return self.status()
                self._stop.set()
                self._ended = self._ended or time.monotonic()
                self._state = "stopping"
                processor = self._processor
            if processor is not None and processor is not threading.current_thread():
                processor.join(timeout=self.STOP_WAIT_SECONDS)
            return self.status()

    def close(self):
        self.stop()
        # HTTP stop stays responsive while the UI polls. Application shutdown
        # must instead drain all accepted chunks before the database is closed.
        processor = self._processor
        if processor is not None and processor is not threading.current_thread():
            processor.join()
        return self.status()
