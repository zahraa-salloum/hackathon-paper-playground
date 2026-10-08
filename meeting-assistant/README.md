# Minutes — Context Aware Meeting Assistant

A local meeting archive that turns timestamped transcripts into searchable evidence. Import an existing meeting, record microphone and computer audio separately, and ask questions about one meeting or several sessions. Answers link back to stored transcript passages and, when retained, their audio.

Built from the **Context Aware Meeting Assistant** semester-project proposal. This directory is a standalone Python project; the surrounding Paper to Playground repository is a separate project.

## Start in one minute

Use **Python 3.11 or newer**. Python 3.11/3.12 is recommended if you plan to install speech or desktop extras; third-party wheel availability varies on newer Python releases.

```powershell
cd meeting-assistant
python -m meeting_assistant --demo
```

The browser opens at **http://127.0.0.1:8765**. The core app, demo, imports, local retrieval, evaluation, and tests require **no pip packages, API keys, or network**. You can also double-click `launch.bat` on Windows, or run `./launch.ps1 -Demo` in PowerShell.

Select **Atlas · Planning**, choose **All meetings**, and ask:

- “How did the Atlas pilot launch date change, and why?”
- “Who owns the CSV importer?”
- “Was a marketing budget approved?”
- “What is the office Wi-Fi password?” — absent evidence should be reported.

The three demo meetings are original synthetic fixtures, clearly labeled in the interface. Speaker names in imported text are unverified labels. No microphone starts automatically.

### Useful launch options

```powershell
python -m meeting_assistant --demo --data-dir .data
python -m meeting_assistant --port 8766 --no-browser
python -m meeting_assistant --help
```

The default archive is `~/.meeting-assistant/` (on Windows, typically `C:\Users\<you>\.meeting-assistant`). Override it with `--data-dir` or `MINUTES_DATA_DIR`. Stop the server with **Ctrl+C** after stopping any active recording and waiting for transcription to finish. Closing a browser tab does not stop the Python application.

## Features

| Proposal requirement | Implementation |
|---|---|
| Local desktop use alongside meeting platforms | Local loopback application; optional native desktop window; no Teams/Meet account integration required |
| Microphone and computer audio | Separate SoundCard capture workers, source labels, chunked WAV files and timestamped transcription |
| Live and archived meetings | Live transcript polling, SQLite session archive, saved conversations |
| Current, selected, or multiple meetings | Explicit scope controls; evidence expansion stays within the selected scope |
| Evidence-backed answers | Extractive mode quotes stored passages; optional model synthesis must reference valid evidence IDs |
| Contextual multi-step retrieval | Prior question context, first-pass ranking, neighboring passages and related sessions, bounded evidence selection |
| Configurable models | Local Whisper and Sentence Transformers; local Ollama chat; compatible remote transcription, embedding and chat APIs |
| Consent and archive control | Explicit recording/import permission; separate remote processing consent; audio retention setting; export/delete |
| Reproducible evaluation | Fixed synthetic fixture, gold passages, three retrieval strategies, per-question metrics |

The proposal's **optional YouTube/Twitch audience-comment extension is not included**. The core meeting assistant works independently of those accounts.

## Install optional capabilities

Create a virtual environment before installing extras:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[capture,transcription]"
```

If PowerShell activation is disabled, run `.\.venv\Scripts\python.exe` directly instead of changing your machine's execution policy.

On macOS/Linux, activate with `source .venv/bin/activate`. Install only the extras you need:

| Extra | Command | Purpose |
|---|---|---|
| Audio devices | `python -m pip install -e ".[capture]"` | SoundCard plus NumPy |
| Local speech | `python -m pip install -e ".[transcription]"` | faster-whisper on CPU |
| Local embeddings | `python -m pip install -e ".[embeddings]"` | Sentence Transformers |
| Native window | `python -m pip install -e ".[desktop]"` | pywebview; run with `--desktop` |
| All optional features | `python -m pip install -e ".[all]"` | All of the above |

Optional versions are bounded compatibility ranges, not a fully reproducible model environment. Capture/model dependencies and native browser backends must support your Python and OS. The core test suite uses only the standard library. Model weights download on first use unless you select an existing local model directory; download them before an offline demonstration.

### Record a meeting

1. In **Settings**, choose a transcription provider and model. For local speech, select **faster-whisper** and `tiny` (or a local model path). For API transcription, use a supported model such as `whisper-1` and its endpoint/key.
2. Create a meeting and confirm you have permission to save its content.
3. Open recording controls. Select microphone, system audio, or both, and choose devices. Confirm participant consent before starting.
4. Keep the recording indicator visible. Transcript passages appear as chunks finish processing. The default chunk duration is eight seconds.
5. Stop recording and wait for pending transcription to finish. If processing falls behind the bounded queue, capture stops and reports the issue.

The two channels remain separate. Microphone audio may still contain loudspeaker bleed; headphones help. Channel separation is **not speaker diarization or identity verification**. No biometric speaker identification is implemented. Streaming is chunk based, so transcription appears with chunk duration plus model latency; it is not word-by-word captioning.

Windows uses WASAPI loopback through SoundCard; PulseAudio systems expose monitor inputs. macOS commonly needs a virtual loopback audio device. OS permissions and actual device behavior need verification on the target machine. Native pywebview also depends on the platform's webview runtime (for example, WebView2 on Windows).

### Import and manage meetings

Import plain text, timestamped text, SRT, VTT, or JSON. Paste text or select a file. JSON can be a segment list or an exported object with `segments`:

```json
{
  "segments": [
    {"start": 12.5, "end": 24.2, "speaker": "Speaker A", "text": "The pilot moves to October 22."}
  ]
}
```

Timestamped text uses `[01:23] Speaker A: Discussion text`. Plain text without timestamps uses `0:00` as an **unknown-time placeholder**, not an inferred recording time. SRT/VTT and JSON preserve supplied timestamps. Imports append to the selected meeting. Imported speaker labels remain unverified. Reimporting an export restores transcript content; it does not restore saved chat messages or audio files.

Audio upload accepts WAV, MP3, M4A, FLAC, OGG, WebM, and MP4, up to 50 MiB locally. API transcription is limited to 24 MiB per file by this implementation. Configure a transcriber first. Uploaded audio is transcribed synchronously; longer files can take time. Enable **Retain audio** before upload to preserve playback. Each upload's timestamps are relative to that uploaded file; recordings started later continue after the meeting's latest transcript endpoint.

**Export** downloads meeting metadata, segments, and saved chat as JSON. **Delete** removes that meeting's transcript, messages, and owned audio directory. It does not remove unrelated files or provider-side data. To back up the whole archive, stop the app and copy its data directory, including the SQLite database and `audio/` folder. The database also contains provider settings and locally stored credentials; protect the backup accordingly.

## Provider settings

The three provider choices are independent. The default is manual transcript import, local word matching, and extractive answers.

| Capability | Provider | Model / endpoint |
|---|---|---|
| Transcription | Local faster-whisper | `tiny`, `base`, or local model folder; CPU int8 |
| Transcription | OpenAI-compatible API | `whisper-1` for segment timestamps; `https://api.openai.com/v1` or a compatible endpoint |
| Embeddings | Built-in local | Word overlap plus an explicit synonym map; **not a trained embedding model** |
| Embeddings | Sentence Transformers | `all-MiniLM-L6-v2` or local model path |
| Embeddings | OpenAI-compatible API | `text-embedding-3-small` or a model supported by your endpoint |
| Chat | Extractive | Exact transcript excerpts; no generated factual synthesis |
| Chat | Ollama | Installed local model name, `http://localhost:11434/v1` |
| Chat | OpenAI-compatible API | Your account's supported chat model, `https://api.openai.com/v1` or compatible endpoint |

For Ollama, install Ollama separately, download a chat model that fits your computer, start its service, and enter that exact model name. An API key is generally unnecessary for a local Ollama endpoint. Some providers differ in supported request fields, response formats, timestamps, or context size; “compatible” does not guarantee every service/model works unchanged.

Remote providers require HTTPS and **external processing consent**. Loopback endpoints on this computer do not require external consent. Depending on enabled providers, a remote service receives audio, bounded transcript passages for embeddings, or the question/history plus selected evidence for chat. External embedding candidate text is limited to the first 8,000 characters of each of at most 256 candidate passages per query. Embeddings are recalculated per query; this can incur repeated API cost. Provider errors fall back to local retrieval where possible and are disclosed in the retrieval steps.

Keys are stored in the local SQLite database, omitted from API responses, and redacted from user-visible errors. Blank key inputs preserve the existing key; use the **clear stored key** control to delete one. Storage is not encrypted by the application or integrated with an OS credential vault. POSIX permissions are restricted where available; on Windows, use account and disk protection. This prototype is intended for one trusted local user, not shared hosting or Internet exposure.

## How the evidence workflow works

1. **Scope:** restrict to the current meeting, checked meetings, or all archived meetings.
2. **Context:** for a follow-up, add up to two earlier user questions. Transcript content is treated as source data, not execution instructions.
3. **Search:** SQLite FTS5 retrieves candidate passages. A safe local fallback works if FTS5 is unavailable. Word overlap and explicit synonym normalization rank candidates; optional configured embeddings add semantic scores.
4. **Expand:** inspect neighbors of the top three hits within two minutes, then related passages from other in-scope sessions. This is a fixed, bounded evidence-gathering workflow, not an unrestricted autonomous agent.
5. **Select:** return at most six passages, with preference for evidence from multiple sessions.
6. **Answer:** quote exact stored text by default. Optional chat synthesis is accepted only when paragraph citations reference selected evidence IDs; otherwise, return source excerpts. Clicking an evidence card opens its transcript passage and retained audio.

The original and revised launch-date statements remain visible with meeting provenance, so a reader can compare them. The default extractive mode does not automatically adjudicate every conflict or decide that the newest transcript statement is true. Generated synthesis is prompted to reconcile statements and acknowledge missing evidence, but checking citation IDs **does not prove factual entailment or answer accuracy**. Answers may be incomplete when retrieval misses relevant passages. Speaker labels are not verified identities. Speech recognition can introduce errors.

## Tests and evaluation

```powershell
python -m unittest discover -s tests -v
python evaluation/run.py
```

Tests cover transcript formats, archive persistence/deletion, transaction validation, concurrent writes, retrieval scope/follow-ups/citations, consent gates, mocked provider requests, mocked two-channel capture, and HTTP workflows. Hardware capture and paid APIs are deliberately mocked, so passing tests do not establish live-device or provider compatibility.

The evaluation creates its own temporary archive, runs ten labeled questions against keyword, single-pass, and contextual retrieval, and writes `evaluation/results.local.json`. A measured example is included in `evaluation/results.json`; rerun it on your machine. The fixture measures gold-passage recall, rank, abstention, scope, and runtime. It is a small synthetic demonstration, **not proof that the research hypothesis holds on real meetings**. See [the evaluation protocol](evaluation/README.md) for a consented study comparing accuracy and manual retrieval effort.

## Project layout

```text
meeting-assistant/
  meeting_assistant/
    __main__.py        command-line launcher and optional desktop window
    server.py          local HTTP API, request isolation, imports and playback
    storage.py         SQLite archive, full-text index and settings
    transcripts.py     text/SRT/VTT/JSON parsing and validation
    retrieval.py       scoped search, evidence expansion and cited answers
    providers.py       optional local and remote model adapters
    capture.py         separate capture workers and transcription queue
    demo.py            original synthetic meeting fixture
    static/            self-contained browser interface
  tests/               standard-library automated tests
  evaluation/          fixed questions, gold evidence, metrics and protocol
  pyproject.toml       installable package and optional dependencies
  launch.bat           Windows launcher
  launch.ps1           PowerShell launcher
```

## Troubleshooting

| Symptom | Action |
|---|---|
| `python` is not found | Install Python, enable its PATH option, or invoke your Python executable directly |
| Port is in use | Start with `--port 8766` |
| Missing SoundCard / faster-whisper | Install the relevant extra in the same Python environment used to launch |
| Model package cannot install | Use Python 3.11/3.12 in a fresh virtual environment and consult that library's supported platforms |
| No system audio | Select the output used by the meeting app; check loopback support and OS permissions |
| Silence or duplicate speech | Check devices/volume; use headphones to reduce microphone bleed |
| Transcription queue grows | Use a smaller/faster local model or a faster API; stop and let pending chunks finish |
| API returns 401/404/429 | Check key, base URL, model access, and quota; provider bodies are intentionally not echoed |
| No answer found | Check scope, add/import the relevant meeting, use more specific wording, or enable suitable embeddings |
| UI says session expired | Reload the page after restarting the server |
| Local model needs Internet | Initial weight download needs a connection; choose a cached local model path afterward |

## Design limits and remaining validation

This is a working semester-project prototype. Full-disk encryption, enterprise authentication, encrypted backups, automated speaker identification, audio echo cancellation, background recovery of failed transcription jobs, and streaming-platform OAuth connections are outside this implementation. The server binds only to `127.0.0.1`; do not expose it through a reverse proxy as a multi-user service.

Live microphones, loopback routing, native pywebview startup, local model downloads/inference, and paid model calls require validation with the target hardware/accounts. No real meeting was recorded during implementation. The included tests and demo exercise the complete offline path and mocked integration boundaries.

## Primary library/API references

- [SoundCard documentation](https://soundcard.readthedocs.io/en/latest/)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Sentence Transformers](https://sbert.net/)
- [OpenAI transcription API](https://platform.openai.com/docs/api-reference/audio/createTranscription)
- [OpenAI embeddings API](https://platform.openai.com/docs/api-reference/embeddings/create)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [pywebview](https://pywebview.flowrl.com/)
