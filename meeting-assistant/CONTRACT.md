# Internal integration contract

Project package: `meeting_assistant`, Python 3.11+, stdlib-only default. SQLite archive. Local HTTP app on 127.0.0.1. Optional pywebview native window. Do not edit the parent project's files.

## Storage / retrieval

`Store(data_dir: Path)` in `storage.py`. Methods: `list_meetings()`; `create_meeting(title, consent=True, description='')`; `get_meeting(id)` returns meeting with `segments`; `delete_meeting(id)`; `add_segments(meeting_id, segments)`; `update_meeting(id, **fields)`; `get_settings()`; `save_settings(dict)`; `list_messages(meeting_id)`; `add_message(meeting_id, role, content, metadata=None)`; `search(query, meeting_ids=None, limit=12)`; `all_segments(meeting_ids=None)`.

Meeting: `{id,title,description,created_at,status,consent,segment_count}`. Segment: `{id,meeting_id,start,end,text,speaker,channel,audio_file?}`. Time in seconds. Source channel microphone/system/import; speaker always an unverified label. Missing ids raise KeyError. Meeting deletions remove rows/messages and owned audio folder `data_dir/audio/<id>`.

Settings keys: `transcription_provider`: manual|faster_whisper|openai; `transcription_model`: tiny; `transcription_base_url`: https://api.openai.com/v1; `transcription_api_key`: ''; `chat_provider`: extractive|ollama|openai; `chat_model`: ''; `chat_base_url`: http://localhost:11434/v1; `chat_api_key`: ''; `embedding_provider`: local|sentence_transformers|openai; `embedding_model`: ''; `embedding_base_url`: https://api.openai.com/v1; `embedding_api_key`: ''; `external_processing_consent`: false; `retain_audio`: true; `language`: ''; `chunk_seconds`: 8. Settings updates must preserve existing API keys when omitted. Browser only sees key-configured flags, never key values.

`RetrievalEngine(store).answer(question, meeting_ids=None, history=None, strategy='contextual')` in retrieval.py returns `{answer,citations,steps,insufficient_evidence,mode}`. Citations: `{id: 'E1',segment_id,meeting_id,meeting_title,start,end,text,channel,speaker}`. Steps: `{stage,detail}`. Calls `providers.embed_texts(texts, settings)` when configured; optionally `providers.generate_answer(question,evidence,settings,history=None)` where evidence is citation dictionaries. All returned citations must correspond to actual selected evidence. Default answer is extractive. strategy lexical|basic|contextual for evaluation.

## Providers / capture

`providers.transcribe_audio(path, settings)` returns `[{start,end,text}]`. `providers.embed_texts(texts,settings)` returns vectors or None for built-in local. `providers.generate_answer(question,evidence,settings,history=None)` returns string or None for extractive. Exceptions have actionable safe messages. Explicit consent required before remote processing; loopback providers do not require external consent. No key values in errors.

`CaptureManager(store)` in capture.py. `devices()` returns `{microphones:[{id,name}],speakers:[{id,name}],available:bool,error?:str}`. `start(meeting_id, microphone_id=None, speaker_id=None, capture_microphone=True, capture_system=False, consent=False)` returns status; `stop()` returns status; `status()` returns `{active,meeting_id,elapsed,segments,error,channels}`. Separate soundcard worker for each channel; chunk files under data_dir/audio/<meeting_id>. Consent checked at start, transcriber configured required, timestamp relative to recording start, flush/stop and update meeting status. `close()` calls stop.

## HTTP / frontend

API JSON success payload is direct object/list; errors `{error: message}` with non-2xx. Browser fetch must send `X-App-Token: window.APP_TOKEN` on every API call. Token injected into index at `__APP_TOKEN__` placeholder. All API requests except audio must use header. Audio playable at `/api/audio/<meeting_id>/<basename>?token=...`.

GET `/api/meetings`; POST `/api/meetings` `{title,description?,consent:true}`; GET/DELETE `/api/meetings/<id>`; GET `/api/meetings/<id>/messages`; POST `/api/meetings/<id>/transcript` `{text,format:'auto'|'text'|'srt'|'vtt'|'json'}`; POST `/api/meetings/<id>/audio?filename=name.wav` raw bytes with `Content-Type:application/octet-stream`; GET `/api/meetings/<id>/export` returns full JSON; GET/PUT `/api/settings`; POST `/api/demo` seeds demo idempotently and returns `{meetings:[...]}`; GET `/api/devices`; GET `/api/capture`; POST `/api/capture/start` capture kwargs; POST `/api/capture/stop`; POST `/api/ask` `{question,meeting_ids:null|[ids],meeting_id:active-id, strategy?:'contextual'}` returns engine answer plus persists conversation. GET `/api/health` returns `{status:'ok',version:'1.0.0'}`.

UI: vanilla HTML/CSS/JS, no CDNs, clear demo status, archive sidebar, meeting transcript, evidence Q&A, current/selected/all scope, create/import/delete/export, settings modal with provider choices/consent/audio retention, recording separate channel toggles/device selectors, live status poll. Clicking evidence scrolls/highlights segment and offers audio when saved. Recording and uploads require explicit consent. Untrusted text uses textContent. Responsive and keyboard accessible. UI can use SVG icons (no external assets).
