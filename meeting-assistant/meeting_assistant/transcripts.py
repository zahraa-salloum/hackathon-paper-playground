"""Parse user-supplied transcripts without inventing speaker identities or times."""

import json
import math
import re

TIMESTAMP = r"(?:\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
CUE = re.compile(rf"^\s*({TIMESTAMP})\s*-->\s*({TIMESTAMP})(?:\s+.*)?$")
STAMPED = re.compile(rf"^\s*\[?({TIMESTAMP})\]?\s+(.*)$")


def seconds(value):
    if isinstance(value, bool):
        raise ValueError("Timestamps must be numbers or hh:mm:ss strings.")
    if isinstance(value, str) and ":" in value:
        parts = value.replace(",", ".").split(":")
        if len(parts) not in (2, 3):
            raise ValueError("Use mm:ss or hh:mm:ss timestamps.")
        values = [float(p) for p in parts]
        if any(v < 0 for v in values) or any(v >= 60 for v in values[1:]):
            raise ValueError("Minutes and seconds must be less than 60.")
        value = sum(v * 60 ** i for i, v in enumerate(reversed(values)))
    value = float(value)
    if not math.isfinite(value) or value < 0 or value > 7 * 86400:
        raise ValueError("Timestamps must be finite, nonnegative, and within seven days.")
    return round(value, 3)


def clean_segment(item):
    if not isinstance(item, dict):
        raise ValueError("Every transcript segment must be an object.")
    text = item.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Every segment needs nonempty text.")
    if len(text) > 20000:
        raise ValueError("A single passage cannot exceed 20,000 characters.")
    start = seconds(item.get("start", 0))
    end = seconds(item.get("end", start))
    if end < start:
        raise ValueError("A segment cannot end before it starts.")
    return {"start": start, "end": end, "text": text.strip(),
            "speaker": str(item.get("speaker", "Unverified speaker"))[:160],
            "channel": "import"}


def split_speaker(text):
    match = re.match(r"^([^:\n]{1,60}):\s+(.+)$", text, flags=re.S)
    if match and not match.group(1).lower().startswith(("http", "note", "decision", "action")):
        return match.group(1), match.group(2)
    return "Unverified speaker", text


def parse_transcript(text, format="auto"):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste or upload a transcript first.")
    if len(text) > 2_000_000:
        raise ValueError("Transcripts are limited to two million characters per import.")
    if format not in {"auto", "text", "srt", "vtt", "json"}:
        raise ValueError("Choose text, SRT, VTT, or JSON.")
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if format == "auto":
        format = "json" if text.startswith(("[", "{")) and not STAMPED.match(text.splitlines()[0]) else "srt" if "-->" in text else "text"
    if format == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid transcript JSON at line {exc.lineno}.") from None
        if isinstance(payload, dict):
            payload = payload.get("segments", payload.get("transcript"))
        if not isinstance(payload, list) or not payload:
            raise ValueError("JSON must be a nonempty segment list or an object with a segments list.")
        segments = [clean_segment(s) for s in payload]
    elif format in {"srt", "vtt"}:
        segments = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            cue = CUE.match(line)
            if not cue:
                continue
            body = []
            for following in lines[index + 1:]:
                if not following.strip() or CUE.match(following):
                    break
                body.append(following.strip())
            content = " ".join(body)
            voice = re.match(r"<v\s+([^>]+)>(.*)", content)
            speaker = voice.group(1) if voice else "Unverified speaker"
            content = re.sub(r"<[^>]*>", "", voice.group(2) if voice else content)
            if speaker == "Unverified speaker":
                speaker, content = split_speaker(content)
            segments.append(clean_segment({"start": cue.group(1), "end": cue.group(2), "text": content, "speaker": speaker}))
        if not segments:
            raise ValueError("No timestamped cues found. Check the SRT/VTT format.")
    else:
        segments = []
        for paragraph in re.split(r"\n+", text):
            if not paragraph.strip():
                continue
            match = STAMPED.match(paragraph)
            stamp, content = (seconds(match.group(1)), match.group(2)) if match else (0, paragraph)
            speaker, content = split_speaker(content)
            segments.append(clean_segment({"start": stamp, "end": stamp, "text": content, "speaker": speaker}))
    if len(segments) > 10000:
        raise ValueError("Import at most 10,000 passages at a time.")
    return segments
