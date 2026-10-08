"""Thread-safe local SQLite archive for meetings, evidence, and settings."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


DEFAULT_SETTINGS = {
    "transcription_provider": "manual",
    "transcription_model": "tiny",
    "transcription_base_url": "https://api.openai.com/v1",
    "transcription_api_key": "",
    "chat_provider": "extractive",
    "chat_model": "",
    "chat_base_url": "http://localhost:11434/v1",
    "chat_api_key": "",
    "embedding_provider": "local",
    "embedding_model": "",
    "embedding_base_url": "https://api.openai.com/v1",
    "embedding_api_key": "",
    "external_processing_consent": False,
    "retain_audio": True,
    "language": "",
    "chunk_seconds": 8,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Open one short-lived connection per operation; safe for HTTP/capture threads.

    API keys are stored locally in the database. POSIX permission bits are
    restricted where supported; use OS account/disk protection on Windows.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "archive.sqlite3"
        self._restrict_permissions(self.data_dir, 0o700)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready', consent INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
                    speaker TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT 'import',
                    audio_file TEXT, audio_offset REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS segments_meeting_time
                    ON segments(meeting_id, start, end);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(segments)")}
            if "audio_offset" not in columns:
                db.execute("ALTER TABLE segments ADD COLUMN audio_offset REAL NOT NULL DEFAULT 0")
            try:
                db.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
                        text, content='segments', content_rowid='rowid',
                        tokenize='porter unicode61'
                    );
                    CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
                        INSERT INTO segments_fts(rowid,text) VALUES(new.rowid,new.text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
                        INSERT INTO segments_fts(segments_fts,rowid,text)
                            VALUES('delete',old.rowid,old.text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
                        INSERT INTO segments_fts(segments_fts,rowid,text)
                            VALUES('delete',old.rowid,old.text);
                        INSERT INTO segments_fts(rowid,text) VALUES(new.rowid,new.text);
                    END;
                """)
                # Rebuild also recovers archives created by SQLite without FTS5.
                db.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
                self.has_fts = True
            except sqlite3.OperationalError:
                self.has_fts = False
        self._protect_database()

    @staticmethod
    def _restrict_permissions(path, mode):
        if os.name != "nt":
            try:
                Path(path).chmod(mode)
            except OSError:
                pass

    def _protect_database(self):
        for suffix in ("", "-wal", "-shm"):
            self._restrict_permissions(Path(str(self.db_path) + suffix), 0o600)

    def _connect(self):
        # The context manager below closes connections (sqlite's own does not).
        return _Connection(self.db_path)

    @staticmethod
    def _meeting(db, meeting_id):
        row = db.execute("""SELECT m.*, (SELECT COUNT(*) FROM segments s
            WHERE s.meeting_id=m.id) AS segment_count FROM meetings m WHERE m.id=?""",
                         (str(meeting_id),)).fetchone()
        if row is None:
            raise KeyError("Meeting not found")
        result = dict(row)
        result["consent"] = bool(result["consent"])
        return result

    def list_meetings(self):
        with self._connect() as db:
            rows = db.execute("""SELECT m.*, (SELECT COUNT(*) FROM segments s
                WHERE s.meeting_id=m.id) AS segment_count FROM meetings m
                ORDER BY m.created_at DESC, m.rowid DESC""").fetchall()
        return [dict(row, consent=bool(row["consent"])) for row in rows]

    def create_meeting(self, title, consent=True, description=""):
        title = _text(title, "Title", 300).strip()
        if not title:
            raise ValueError("A meeting title is required")
        description = _text(description, "Description", 10000)
        if not isinstance(consent, bool):
            raise ValueError("Consent must be true or false")
        meeting_id = uuid4().hex
        with self._connect() as db:
            db.execute("""INSERT INTO meetings
                (id,title,description,created_at,status,consent) VALUES(?,?,?,?,?,?)""",
                       (meeting_id, title, description, _now(), "ready", int(consent)))
            result = self._meeting(db, meeting_id)
        return result

    def get_meeting(self, meeting_id):
        with self._connect() as db:
            result = self._meeting(db, meeting_id)
            result["segments"] = [dict(r) for r in db.execute(
                "SELECT * FROM segments WHERE meeting_id=? ORDER BY start,end,rowid",
                (str(meeting_id),))]
        return result

    def update_meeting(self, meeting_id, **fields):
        allowed = {"title", "description", "status", "consent"}
        if set(fields) - allowed:
            raise ValueError("Unknown meeting field")
        for key in ("title", "description", "status"):
            if key in fields:
                fields[key] = _text(fields[key], key.title(), 10000 if key == "description" else 300)
                if key in ("title", "status"):
                    fields[key] = fields[key].strip()
                    if not fields[key]:
                        raise ValueError(f"{key.title()} cannot be empty")
        if "consent" in fields and not isinstance(fields["consent"], bool):
            raise ValueError("Consent must be true or false")
        with self._connect() as db:
            self._meeting(db, meeting_id)
            if fields:
                db.execute("UPDATE meetings SET " + ",".join(k + "=?" for k in fields)
                           + " WHERE id=?", [*fields.values(), str(meeting_id)])
            result = self._meeting(db, meeting_id)
        return result

    def delete_meeting(self, meeting_id):
        with self._connect() as db:
            meeting = self._meeting(db, meeting_id)
            db.execute("DELETE FROM meetings WHERE id=?", (meeting["id"],))
        # IDs are generated here, but check again before touching the filesystem.
        if not re.fullmatch(r"[a-f0-9]{32}", meeting["id"]):
            return
        audio_root = self.data_dir / "audio"
        target = audio_root / meeting["id"]
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            resolved = target.resolve()
            # Refuse redirected roots/junctions; remove only this exact owned path.
            if audio_root.resolve() == audio_root and resolved == target:
                shutil.rmtree(target)

    def add_segments(self, meeting_id, segments):
        if not isinstance(segments, (list, tuple)):
            raise ValueError("Segments must be a list")
        values = []
        for item in segments:
            if not isinstance(item, dict):
                raise ValueError("Each segment must be an object")
            body = _text(item.get("text", ""), "Transcript text", 100000).strip()
            if not body:
                continue
            try:
                start = float(item.get("start", 0))
                end = float(item.get("end", start))
            except (TypeError, ValueError):
                raise ValueError("Segment timestamps must be numbers") from None
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                raise ValueError("Segment timestamps must be finite with 0 <= start <= end")
            channel = item.get("channel", "import")
            if channel not in ("microphone", "system", "import"):
                raise ValueError("Unknown transcript channel")
            speaker = _text(item.get("speaker") or "", "Speaker", 300)
            audio_file = item.get("audio_file")
            if audio_file is not None:
                audio_file = _text(audio_file, "Audio filename", 255)
                if not audio_file or "/" in audio_file or "\\" in audio_file or audio_file in (".", "..") or ":" in audio_file:
                    raise ValueError("Audio filename must be a filename without folders")
            try:
                audio_offset = float(item.get("audio_offset", 0))
            except (TypeError, ValueError):
                raise ValueError("Audio offset must be a nonnegative number") from None
            if not math.isfinite(audio_offset) or audio_offset < 0:
                raise ValueError("Audio offset must be a nonnegative number")
            values.append((uuid4().hex, str(meeting_id), start, end, body, speaker, channel, audio_file, audio_offset))
        with self._connect() as db:
            self._meeting(db, meeting_id)
            db.executemany("""INSERT INTO segments
                (id,meeting_id,start,end,text,speaker,channel,audio_file,audio_offset) VALUES(?,?,?,?,?,?,?,?,?)""", values)
        return [{"id": v[0], "meeting_id": v[1], "start": v[2], "end": v[3], "text": v[4],
                 "speaker": v[5], "channel": v[6], "audio_file": v[7], "audio_offset": v[8]} for v in values]

    def get_settings(self):
        with self._connect() as db:
            result = dict(DEFAULT_SETTINGS)
            for row in db.execute("SELECT key,value FROM settings"):
                if row["key"] in DEFAULT_SETTINGS:
                    result[row["key"]] = json.loads(row["value"])
        return result

    def save_settings(self, settings):
        if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
            raise ValueError("Unknown or invalid settings")
        choices = {
            "transcription_provider": {"manual", "faster_whisper", "openai"},
            "chat_provider": {"extractive", "ollama", "openai"},
            "embedding_provider": {"local", "sentence_transformers", "openai"},
        }
        for key, value in settings.items():
            if key in choices:
                if not isinstance(value, str) or value not in choices[key]:
                    raise ValueError("Invalid provider selection")
            elif key in ("external_processing_consent", "retain_audio"):
                if not isinstance(value, bool):
                    raise ValueError("Consent and audio retention must be true or false")
            elif key == "chunk_seconds":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 2 <= value <= 60:
                    raise ValueError("Chunk duration must be between 2 and 60 seconds")
            else:
                _text(value, "Setting", 4096)
        with self._connect() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(k, json.dumps(v)) for k, v in settings.items()])
        self._protect_database()
        return self.get_settings()

    def list_messages(self, meeting_id):
        with self._connect() as db:
            self._meeting(db, meeting_id)
            rows = db.execute("SELECT * FROM messages WHERE meeting_id=? ORDER BY rowid", (str(meeting_id),)).fetchall()
        return [dict(row, metadata=json.loads(row["metadata"])) for row in rows]

    def add_message(self, meeting_id, role, content, metadata=None):
        if role not in ("user", "assistant", "system"):
            raise ValueError("Unknown message role")
        content = _text(content, "Message", 200000)
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Message metadata must be an object")
        encoded = json.dumps(metadata or {}, allow_nan=False)
        row = {"id": uuid4().hex, "meeting_id": str(meeting_id), "role": role,
               "content": content, "metadata": metadata or {}, "created_at": _now()}
        with self._connect() as db:
            self._meeting(db, meeting_id)
            db.execute("INSERT INTO messages(id,meeting_id,role,content,metadata,created_at) VALUES(?,?,?,?,?,?)",
                       (row["id"], str(meeting_id), role, content, encoded, row["created_at"]))
        return row

    @staticmethod
    def _scope(meeting_ids):
        if meeting_ids is None:
            return "", []
        if isinstance(meeting_ids, str) or not isinstance(meeting_ids, (list, tuple, set)):
            raise ValueError("Meeting scope must be a list of IDs")
        ids = list(dict.fromkeys(str(item) for item in meeting_ids))
        if len(ids) > 500:
            raise ValueError("Choose at most 500 meetings in one scope")
        if not ids:
            return " AND 0", []
        return " AND s.meeting_id IN (" + ",".join("?" for _ in ids) + ")", ids

    def all_segments(self, meeting_ids=None):
        scope, params = self._scope(meeting_ids)
        with self._connect() as db:
            return [dict(row) for row in db.execute("""SELECT s.*,m.title AS meeting_title,
                m.created_at AS meeting_created_at FROM segments s JOIN meetings m ON m.id=s.meeting_id
                WHERE 1=1""" + scope + " ORDER BY m.created_at DESC,m.rowid DESC,s.start,s.end,s.rowid", params)]

    def search(self, query, meeting_ids=None, limit=12):
        query = _text(query, "Search query", 20000)
        tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", query, re.UNICODE)))[:32]
        if not tokens:
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Search limit must be between 1 and 500")
        scope, params = self._scope(meeting_ids)
        with self._connect() as db:
            if self.has_fts:
                # Quote only parsed tokens, never interpolate user FTS operators.
                match = " OR ".join('"' + token + '"' for token in tokens)
                rows = db.execute("""SELECT s.*,m.title AS meeting_title,
                    m.created_at AS meeting_created_at, -bm25(segments_fts) AS score
                    FROM segments_fts JOIN segments s ON s.rowid=segments_fts.rowid
                    JOIN meetings m ON m.id=s.meeting_id WHERE segments_fts MATCH ?"""
                                  + scope + " ORDER BY score DESC,s.start LIMIT ?", [match, *params, limit]).fetchall()
            else:
                conditions = " OR ".join("lower(s.text) LIKE ?" for _ in tokens)
                rows = db.execute("""SELECT s.*,m.title AS meeting_title,
                    m.created_at AS meeting_created_at, 1.0 AS score
                    FROM segments s JOIN meetings m ON m.id=s.meeting_id WHERE (""" + conditions + ")"
                                  + scope + " ORDER BY m.created_at DESC,s.start LIMIT ?",
                                  [*("%" + token.lower() + "%" for token in tokens), *params, limit]).fetchall()
        return [dict(row) for row in rows]


def _text(value, name, maximum):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} must be text of at most {maximum} characters")
    return value


class _Connection:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")

    def __enter__(self):
        return self.db

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
