"""Loopback-only HTTP application. No framework, account, or cloud is required."""

import hmac
import json
import mimetypes
import re
import secrets
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .capture import CaptureManager
from .demo import seed_demo
from .providers import transcribe_audio
from .retrieval import RetrievalEngine
from .storage import Store
from .transcripts import clean_segment, parse_transcript

STATIC = Path(__file__).parent / "static"
API_KEYS = ("transcription_api_key", "embedding_api_key", "chat_api_key")
MAX_JSON = 3 * 1024 * 1024
MAX_AUDIO = 50 * 1024 * 1024


def public_settings(settings):
    result = {k: v for k, v in settings.items() if k not in API_KEYS}
    for key in API_KEYS:
        result[key + "_configured"] = bool(settings.get(key))
    return result


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data_dir):
        if address[0] != "127.0.0.1":
            raise ValueError("The application must bind to 127.0.0.1.")
        self.store = Store(Path(data_dir))
        self.capture = CaptureManager(self.store)
        self.engine = RetrievalEngine(self.store)
        self.token = secrets.token_urlsafe(32)
        self.mutation_lock = threading.RLock()
        super().__init__(address, Handler)

    def server_close(self):
        self.capture.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Minutes/" + __version__
    sys_version = ""

    def log_message(self, format, *args):
        # Do not log questions, file names, tokens in query strings, or provider keys.
        pass

    def send_bytes(self, data, content_type, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, value, status=200, extra=None):
        self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status, extra)

    def read_body(self, max_bytes=MAX_JSON):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked request uploads are not supported.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length.") from None
        if not 0 <= length <= max_bytes:
            raise ValueError(f"Upload exceeds the {max_bytes // 1024 // 1024} MB limit.")
        self.connection.settimeout(60)
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("The upload was interrupted. Try again.")
        return body

    def read_json(self, allow_empty=False):
        try:
            raw = self.read_body()
            value = {} if allow_empty and not raw else json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body must contain valid UTF-8 JSON.") from None
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def check_access(self, path, query):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            self.json({"error": "This application only accepts local requests."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            self.json({"error": "Cross-origin requests are not allowed."}, 403)
            return False
        if path.startswith("/api/"):
            token = self.headers.get("X-App-Token", "")
            if path.startswith("/api/audio/"):
                token = token or query.get("token", [""])[0]
            if not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
                self.json({"error": "Your local session expired. Reload the application."}, 403)
                return False
        return True

    def dispatch(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if not self.check_access(path, query):
                return
            self.route(path, query)
        except KeyError:
            self.json({"error": "The requested meeting or resource no longer exists."}, 404)
        except (ValueError, TypeError) as exc:
            self.json({"error": self.safe_error(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            # Known provider/capture errors are actionable; unexpected errors are not dumped.
            if exc.__class__.__module__ in {"meeting_assistant.providers", "meeting_assistant.capture"}:
                self.json({"error": self.safe_error(exc)}, 422)
            else:
                self.json({"error": "The operation could not finish. Check your provider settings and try again."}, 500)

    def safe_error(self, exc):
        message = str(exc)[:1000]
        for key in API_KEYS:
            secret = self.server.store.get_settings().get(key)
            if secret:
                message = message.replace(secret, "[redacted]")
        return message

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_HEAD = dispatch

    def route(self, path, query):
        store = self.server.store
        method = self.command
        if method in {"GET", "HEAD"} and path in {"/", "/index.html", "/app.css", "/app.js", "/static/app.css", "/static/app.js"}:
            name = "index.html" if path in {"/", "/index.html"} else path.rsplit("/", 1)[1]
            content = (STATIC / name).read_text(encoding="utf-8")
            if name == "index.html":
                content = content.replace("__APP_TOKEN__", json.dumps(self.server.token))
            self.send_bytes(content.encode("utf-8"), {"index.html": "text/html", "app.css": "text/css", "app.js": "text/javascript"}[name] + "; charset=utf-8")
        elif path == "/api/health" and method == "GET":
            self.json({"status": "ok", "version": __version__})
        elif path == "/api/settings" and method == "GET":
            self.json(public_settings(store.get_settings()))
        elif path == "/api/settings" and method == "PUT":
            if self.server.capture.status()["active"]:
                raise ValueError("Stop recording before changing providers or audio settings.")
            data = self.read_json()
            for key in list(data):
                if key.endswith("_configured"):
                    data.pop(key)
            store.save_settings(data)
            self.json(public_settings(store.get_settings()))
        elif path == "/api/meetings" and method == "GET":
            self.json(store.list_meetings())
        elif path == "/api/meetings" and method == "POST":
            data = self.read_json()
            if data.get("consent") is not True:
                raise ValueError("Confirm you have permission to save this meeting.")
            title = data.get("title", "")
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                raise ValueError("Use a meeting title between 1 and 200 characters.")
            self.json(store.create_meeting(title.strip(), consent=True, description=str(data.get("description", ""))[:2000]), 201)
        elif path == "/api/demo" and method == "POST":
            self.read_json(allow_empty=True)
            with self.server.mutation_lock:
                self.json(seed_demo(store), 201)
        elif path == "/api/devices" and method == "GET":
            self.json(self.server.capture.devices())
        elif path == "/api/capture" and method == "GET":
            self.json(self.server.capture.status())
        elif path == "/api/capture/start" and method == "POST":
            data = self.read_json()
            allowed = {"meeting_id", "microphone_id", "speaker_id", "capture_microphone", "capture_system", "consent"}
            if set(data) - allowed:
                raise ValueError("Unknown recording option.")
            with self.server.mutation_lock:
                self.json(self.server.capture.start(**data))
        elif path == "/api/capture/stop" and method == "POST":
            self.read_json(allow_empty=True)
            with self.server.mutation_lock:
                self.json(self.server.capture.stop())
        elif path == "/api/ask" and method == "POST":
            data = self.read_json()
            question = data.get("question", "")
            if not isinstance(question, str) or not question.strip() or len(question) > 2000:
                raise ValueError("Ask a question between 1 and 2,000 characters.")
            current_id = data.get("meeting_id")
            if not current_id:
                raise ValueError("Select a meeting to save this conversation.")
            store.get_meeting(current_id)
            ids = data.get("meeting_ids")
            if ids is not None:
                if not isinstance(ids, list) or not ids or len(ids) > 100 or not all(isinstance(i, str) for i in ids):
                    raise ValueError("Choose at least one meeting for the search scope.")
                for mid in ids:
                    store.get_meeting(mid)
            strategy = data.get("strategy", "contextual")
            if strategy not in {"lexical", "basic", "contextual"}:
                raise ValueError("Unknown retrieval strategy.")
            history = store.list_messages(current_id)[-8:]
            result = self.server.engine.answer(question.strip(), meeting_ids=ids, history=history, strategy=strategy)
            store.add_message(current_id, "user", question.strip())
            store.add_message(current_id, "assistant", result["answer"], metadata=result)
            self.json(result)
        elif path.startswith("/api/audio/") and method in {"GET", "HEAD"}:
            self.audio(path)
        elif re.fullmatch(r"/api/meetings/[^/]+(?:/(?:messages|transcript|audio|export))?", path):
            pieces = path.split("/")
            mid = pieces[3]
            action = pieces[4] if len(pieces) == 5 else ""
            meeting = store.get_meeting(mid)
            if method == "GET" and action in {"", "export"}:
                if action == "export":
                    meeting = dict(meeting, messages=store.list_messages(mid))
                self.json(meeting, extra={"Content-Disposition": f'attachment; filename="meeting-{mid}.json"'} if action == "export" else None)
            elif method == "GET" and action == "messages":
                self.json(store.list_messages(mid))
            elif method == "DELETE" and not action:
                with self.server.mutation_lock:
                    state = self.server.capture.status()
                    if state["active"] and state["meeting_id"] == mid:
                        raise ValueError("Stop recording before deleting this meeting.")
                    store.delete_meeting(mid)
                self.json({"deleted": True})
            elif method == "POST" and action == "transcript":
                data = self.read_json()
                segments = parse_transcript(data.get("text"), data.get("format", "auto"))
                store.add_segments(mid, segments)
                self.json({"imported": len(segments), "meeting": store.get_meeting(mid)}, 201)
            elif method == "POST" and action == "audio":
                self.import_audio(mid, query)
            else:
                self.json({"error": "Method not allowed."}, 405)
        else:
            self.json({"error": "Resource not found."}, 404)

    def import_audio(self, mid, query):
        if query.get("consent", [""])[0] != "true":
            raise ValueError("Confirm permission to transcribe and save this audio.")
        settings = self.server.store.get_settings()
        if settings.get("transcription_provider") == "manual":
            raise ValueError("Choose a transcription provider in Settings before uploading audio.")
        suffix = Path(query.get("filename", ["audio.wav"])[0]).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}:
            raise ValueError("Upload WAV, MP3, M4A, FLAC, OGG, WebM, or MP4 audio.")
        raw = self.read_body(MAX_AUDIO)
        if not raw:
            raise ValueError("The audio file is empty.")
        directory = Path(self.server.store.data_dir) / "audio" / mid
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (uuid.uuid4().hex + suffix)
        target.write_bytes(raw)
        successful = False
        try:
            parts = transcribe_audio(target, settings)
            segments = []
            for part in parts:
                segment = clean_segment(part)
                if settings.get("retain_audio", True):
                    segment["audio_file"] = target.name
                    segment["audio_offset"] = segment["start"]
                segments.append(segment)
            if not segments:
                raise ValueError("No speech was detected in this audio.")
            self.server.store.add_segments(mid, segments)
            successful = True
            self.json({"imported": len(segments), "meeting": self.server.store.get_meeting(mid)}, 201)
        finally:
            if not successful or not settings.get("retain_audio", True):
                target.unlink(missing_ok=True)

    def audio(self, path):
        parts = path.split("/")
        if len(parts) != 5:
            raise KeyError(path)
        mid, filename = parts[3:]
        meeting = self.server.store.get_meeting(mid)
        if not filename or Path(filename).name != filename or "\\" in filename or ":" in filename:
            raise KeyError(path)
        if not any(s.get("audio_file") == filename for s in meeting["segments"]):
            raise KeyError(path)
        target = Path(self.server.store.data_dir) / "audio" / mid / filename
        if not target.is_file():
            raise KeyError(path)
        data = target.read_bytes()
        length = len(data)
        extra = {"Accept-Ranges": "bytes"}
        status = 200
        request_range = self.headers.get("Range")
        if request_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", request_range)
            if not match or not any(match.groups()):
                self.send_bytes(b"", "application/octet-stream", 416, {"Content-Range": f"bytes */{length}"})
                return
            start = int(match.group(1)) if match.group(1) else max(0, length - int(match.group(2)))
            end = min(length - 1, int(match.group(2))) if match.group(1) and match.group(2) else length - 1
            if start >= length or start > end:
                self.send_bytes(b"", "application/octet-stream", 416, {"Content-Range": f"bytes */{length}"})
                return
            data = data[start:end + 1]
            extra["Content-Range"] = f"bytes {start}-{end}/{length}"
            status = 206
        self.send_bytes(data, mimetypes.guess_type(filename)[0] or "application/octet-stream", status, extra)
