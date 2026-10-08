/* Minutes: dependency-free local workspace. All imported/model text is rendered as text. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { meetings: [], meeting: null, messages: [], settings: {}, capture: { active: false }, selected: new Set(), importMode: "text", asking: false, pollBusy: false, selectionVersion: 0 };
const settingsKeys = ["transcription_provider", "transcription_model", "transcription_base_url", "chat_provider", "chat_model", "chat_base_url", "embedding_provider", "embedding_model", "embedding_base_url", "language", "chunk_seconds"];
const secretKeys = ["transcription_api_key", "chat_api_key", "embedding_api_key"];
let toastTimer;

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined && content !== null) node.textContent = String(content);
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function textButton(label, className, handler, iconName) {
  const button = element("button", className);
  button.type = "button";
  if (iconName) button.append(icon(iconName));
  button.append(document.createTextNode(label));
  button.addEventListener("click", handler);
  return button;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-App-Token", window.APP_TOKEN);
  if (options.body && typeof options.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try { response = await fetch(`/api${path}`, { ...options, headers, cache: "no-store" }); }
  catch { throw new Error("The local app is unreachable. Check that the Minutes server is still running, then try again."); }
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error("The local app returned an unreadable response. Try again or check the server terminal."); }
  if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : `Request failed (${response.status}).`);
  return data;
}

function notify(message, type = "") {
  clearTimeout(toastTimer);
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast ${type}`.trim();
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, type === "error" ? 11000 : 6000);
}

function report(error) { notify(error.message || "Something went wrong. Please try again.", "error"); }
function on(selector, type, handler) {
  $(selector).addEventListener(type, (event) => {
    try { Promise.resolve(handler(event)).catch(report); } catch (error) { report(error); }
  });
}

async function busy(button, label, callback) {
  const children = [...button.childNodes];
  button.disabled = true;
  button.replaceChildren(element("span", "spinner"), document.createTextNode(label));
  try { return await callback(); }
  finally { button.replaceChildren(...children); button.disabled = false; }
}

function formError(form, error) {
  const target = $(".form-error", form);
  target.textContent = error ? error.message || String(error) : "";
  target.hidden = !error;
}

function openDialog(id) {
  const dialog = $(id);
  const form = $("form", dialog);
  if (form) formError(form, null);
  if (!dialog.open) dialog.showModal();
}

function time(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(whole / 3600);
  return [ ...(hours ? [hours] : []), Math.floor(whole / 60) % 60, whole % 60 ].map((v, i) => i === 0 && hours ? String(v) : String(v).padStart(2, "0")).join(":");
}

function shortDate(value) {
  if (!value) return "Just added";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Saved meeting";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function fullDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Saved locally";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function channelBadge(channel) {
  const value = ["microphone", "system", "import"].includes(channel) ? channel : "import";
  const badge = element("span", `badge source-badge ${value}`);
  badge.append(icon(value === "microphone" ? "mic" : value === "system" ? "monitor" : "file"), document.createTextNode(value === "microphone" ? "Mic" : value === "system" ? "System" : "Imported"));
  return badge;
}

function renderArchive() {
  const query = $("#archive-search").value.trim().toLocaleLowerCase();
  const list = $("#meeting-list");
  list.replaceChildren();
  $("#meeting-count").textContent = state.meetings.length;
  const filtered = state.meetings.filter(meeting => `${meeting.title} ${meeting.description || ""}`.toLocaleLowerCase().includes(query));
  if (!filtered.length) list.append(element("p", "archive-empty", query ? "No meetings match your search." : "Your next conversation starts here. Create a meeting or explore the samples below."));
  for (const meeting of filtered) {
    const button = textButton("", `meeting-item${meeting.id === state.meeting?.id ? " active" : ""}`, () => openMeeting(meeting.id).catch(report));
    if (meeting.id === state.meeting?.id) button.setAttribute("aria-current", "page");
    const copy = element("span", "meeting-item-copy");
    copy.append(element("span", "meeting-item-title", meeting.title || "Untitled meeting"));
    const meta = element("span", "meeting-item-meta");
    meta.append(element("span", "", shortDate(meeting.created_at)), element("span", "mini-dot"), element("span", "", `${Number(meeting.segment_count) || 0} passages`));
    copy.append(meta);
    button.append(icon("file"), copy);
    button.title = meeting.title || "Untitled meeting";
    list.append(button);
  }
}

async function refreshMeetings() {
  state.meetings = await api("/meetings");
  state.selected = new Set([...state.selected].filter(id => state.meetings.some(m => m.id === id)));
  renderArchive();
  renderScopeLabel();
}

function showWelcome() {
  state.meeting = null;
  state.messages = [];
  $("#workspace").hidden = true;
  $("#welcome").hidden = false;
  $("#page-loading").hidden = true;
  document.title = "Minutes — Context-aware meeting assistant";
  renderArchive();
}

async function openMeeting(id, options = {}) {
  const version = ++state.selectionVersion;
  const [meeting, messages] = await Promise.all([api(`/meetings/${encodeURIComponent(id)}`), api(`/meetings/${encodeURIComponent(id)}/messages`)]);
  if (version !== state.selectionVersion) return;
  state.meeting = meeting;
  state.messages = messages;
  $("#transcript-search").value = "";
  $("#welcome").hidden = true;
  $("#workspace").hidden = false;
  $("#page-loading").hidden = true;
  $("#sidebar").classList.remove("open");
  $("#toggle-sidebar").setAttribute("aria-expanded", "false");
  renderMeeting();
  renderConversation();
  renderArchive();
  document.title = `${meeting.title} · Minutes`;
  if (options.segmentId) revealSegment(options.segmentId, options.audioOffset);
}

function renderMeeting() {
  const meeting = state.meeting;
  if (!meeting) return;
  $("#meeting-title").textContent = meeting.title || "Untitled meeting";
  $("#meeting-description").textContent = meeting.description || "";
  $("#meeting-description").hidden = !meeting.description;
  $("#demo-badge").hidden = !/^Synthetic demo/.test(String(meeting.description || ""));
  const segments = meeting.segments || [];
  const duration = segments.reduce((max, segment) => Math.max(max, Number(segment.end) || Number(segment.start) || 0), 0);
  const meta = $("#meeting-meta");
  meta.replaceChildren();
  const parts = [fullDate(meeting.created_at), duration ? `${time(duration)} recorded` : "Ready for a conversation", `${segments.length} passage${segments.length === 1 ? "" : "s"}`];
  parts.forEach((part, index) => { if (index) meta.append(element("span", "divider", "·")); meta.append(element("span", "", part)); });
  $("#transcript-count").textContent = segments.length ? `${segments.length} timestamped passages` : "Your meeting, word for word";
  $("#open-record").disabled = Boolean(state.capture.active);
  $("#delete-meeting").disabled = Boolean(state.capture.active && state.capture.meeting_id === meeting.id);
  renderTranscript();
  renderScopeLabel();
}

function appendHighlightedText(node, text, query) {
  const source = String(text || "");
  if (!query) { node.textContent = source; return; }
  const lower = source.toLocaleLowerCase();
  let start = 0;
  let index;
  while ((index = lower.indexOf(query, start)) !== -1) {
    node.append(document.createTextNode(source.slice(start, index)), element("mark", "", source.slice(index, index + query.length)));
    start = index + query.length;
  }
  node.append(document.createTextNode(source.slice(start)));
}

function renderTranscript() {
  const list = $("#transcript-list");
  const oldScroll = list.scrollTop;
  list.replaceChildren();
  const all = state.meeting?.segments || [];
  const query = $("#transcript-search").value.trim().toLocaleLowerCase();
  const segments = all.filter(segment => `${segment.text || ""} ${segment.speaker || ""}`.toLocaleLowerCase().includes(query));
  $("#search-count").textContent = query ? `${segments.length} found` : "";
  if (!segments.length) {
    const empty = element("div", "empty-panel");
    empty.append(icon(query ? "search" : "file"), element("h3", "", query ? "No matching passages" : "A conversation belongs here."), element("p", "", query ? "Try another word or phrase in this transcript." : "Import a transcript or record your meeting to start building its context."));
    if (!query) empty.append(textButton("Import a conversation", "button", showImport, "upload"));
    list.append(empty);
    return;
  }
  for (const segment of segments) {
    const row = element("article", "transcript-segment");
    row.dataset.segmentId = String(segment.id);
    row.tabIndex = -1;
    const header = element("div", "segment-header");
    const speaker = segment.speaker || (segment.channel === "microphone" ? "Microphone" : segment.channel === "system" ? "System audio" : "Speaker");
    const avatar = element("span", "speaker-avatar", speaker.split(/\s+/).slice(0, 2).map(word => word[0]).join("").toLocaleUpperCase());
    avatar.setAttribute("aria-hidden", "true");
    const name = element("span", "speaker-name", speaker);
    name.title = `${speaker} (unverified label)`;
    header.append(avatar, name, channelBadge(segment.channel));
    if (segment.audio_file) {
      const play = textButton(`↗ ${time(segment.start)}`, "segment-time playable", () => playSegment(row, segment));
      play.setAttribute("aria-label", `Play audio from ${time(segment.start)}`);
      header.append(play);
    } else header.append(element("span", "segment-time", time(segment.start)));
    const body = element("p", "segment-text");
    appendHighlightedText(body, segment.text, query);
    row.append(header, body);
    list.append(row);
  }
  list.scrollTop = oldScroll;
}

function playSegment(row, segment, offset) {
  let audio = $("audio", row);
  if (!audio) {
    audio = element("audio", "segment-audio");
    audio.controls = true;
    audio.preload = "metadata";
    const basename = String(segment.audio_file).split(/[\\/]/).pop();
    audio.src = `/api/audio/${encodeURIComponent(state.meeting.id)}/${encodeURIComponent(basename)}?token=${encodeURIComponent(window.APP_TOKEN)}`;
    audio.setAttribute("aria-label", `Saved audio for ${segment.speaker || "speaker"} at ${time(segment.start)}`);
    audio.addEventListener("error", () => notify("This audio is unavailable. It may have been removed or saved without audio retention.", "error"), { once: true });
    row.append(audio);
  }
  const seek = Math.max(0, Number(offset ?? segment.audio_offset ?? segment.start) || 0);
  const play = () => {
    audio.currentTime = Number.isFinite(audio.duration) ? Math.min(seek, Math.max(0, audio.duration - .1)) : seek;
    audio.play().catch(() => { /* Browser may require a second user gesture after a source navigation. */ });
  };
  if (audio.readyState >= 1) play();
  else audio.addEventListener("loadedmetadata", play, { once: true });
}

function revealSegment(id, offset) {
  $("#transcript-search").value = "";
  renderTranscript();
  const row = $$(".transcript-segment").find(node => node.dataset.segmentId === String(id));
  if (!row) { notify("The source passage is no longer available.", "error"); return; }
  row.classList.add("highlight");
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.focus({ preventScroll: true });
  setTimeout(() => row.classList.remove("highlight"), 7000);
  const segment = state.meeting.segments.find(item => String(item.id) === String(id));
  if (segment?.audio_file) playSegment(row, segment, offset);
}

async function followEvidence(citation) {
  if (state.meeting?.id === citation.meeting_id) revealSegment(citation.segment_id, citation.audio_offset);
  else await openMeeting(citation.meeting_id, { segmentId: citation.segment_id, audioOffset: citation.audio_offset });
}

function metadata(message) {
  if (!message.metadata) return message;
  if (typeof message.metadata === "object") return { ...message, ...message.metadata };
  try { return { ...message, ...JSON.parse(message.metadata) }; } catch { return message; }
}

function renderMessage(message) {
  const data = metadata(message);
  const user = message.role === "user";
  const container = element("article", `message message-${user ? "user" : "assistant"}`);
  const label = element("div", "message-label");
  if (!user) label.append(icon("spark"));
  label.append(document.createTextNode(user ? "You" : "Minutes"));
  container.append(label, element("p", "message-text", message.content || data.answer || ""));
  if (!user && data.mode) container.append(element("span", "answer-mode", data.mode === "extractive" ? "Extractive answer · quoted evidence" : `${data.mode} answer`));
  if (!user && data.citations?.length) {
    container.append(element("h4", "evidence-heading", `${data.citations.length} source${data.citations.length === 1 ? "" : "s"} · open to verify`));
    for (const citation of data.citations) {
      const card = textButton("", "evidence-card", () => followEvidence(citation).catch(report));
      const top = element("span", "evidence-top");
      top.append(element("span", "evidence-id", citation.id || "Source"), element("span", "evidence-meeting", citation.meeting_title || "Meeting"), element("span", "evidence-time", `${time(citation.start)} ↗`));
      card.append(top, element("span", "evidence-quote", citation.text));
      card.setAttribute("aria-label", `Open ${citation.id || "source"} in ${citation.meeting_title || "meeting"} at ${time(citation.start)}: ${String(citation.text || "").slice(0, 120)}`);
      container.append(card);
    }
  }
  if (!user && data.steps?.length) {
    const details = element("details", "retrieval-details");
    details.append(element("summary", "", "How this answer was found"));
    const steps = element("ol", "retrieval-steps");
    for (const step of data.steps) {
      const item = element("li");
      item.append(element("strong", "", String(step.stage || "Retrieval").replace(/_/g, " ")), element("span", "", step.detail || ""));
      steps.append(item);
    }
    details.append(steps);
    container.append(details);
  }
  return container;
}

function renderConversation() {
  const conversation = $("#conversation");
  conversation.replaceChildren();
  if (!state.messages.length) {
    const welcome = element("div", "chat-welcome");
    const welcomeIcon = element("span", "chat-welcome-icon");
    welcomeIcon.append(icon("spark"));
    welcome.append(welcomeIcon, element("h3", "", "The answer is in the conversation."), element("p", "", "Ask a question, trace a decision, or pick up where the last meeting left off. Every source is one click away."), element("span", "suggestion-label", "A FEW PLACES TO START"));
    for (const suggestion of ["What decisions did we make?", "What are the next steps and who owns them?", "What changed since our last meeting?"]) {
      const button = textButton(suggestion, "suggestion", () => { $("#question").value = suggestion; $("#question").focus(); });
      button.append(icon("arrow"));
      welcome.append(button);
    }
    conversation.append(welcome);
  } else for (const message of state.messages) conversation.append(renderMessage(message));
  if (state.asking) {
    const pending = element("div", "pending-message");
    pending.append(element("span", "spinner"), document.createTextNode("Following the evidence…"));
    conversation.append(pending);
  }
  conversation.scrollTop = conversation.scrollHeight;
}

function renderScopeLabel() {
  const selected = $("#ask-scope").value === "selected";
  $("#choose-meetings").hidden = !selected;
  $("#selected-scope-label").hidden = !selected;
  $("#selected-scope-label").textContent = state.selected.size ? `${state.selected.size} selected: ${state.meetings.filter(m => state.selected.has(m.id)).map(m => m.title).join(" · ")}` : "Choose one or more meetings to search.";
}

function showScope() {
  const list = $("#scope-meetings");
  list.replaceChildren();
  for (const meeting of state.meetings) {
    const label = element("label", "check-label");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.name = "meeting_ids";
    checkbox.value = meeting.id;
    checkbox.checked = state.selected.has(meeting.id);
    const text = element("span");
    text.append(element("strong", "", meeting.title), element("small", "", `${shortDate(meeting.created_at)} · ${meeting.segment_count || 0} passages`));
    label.append(checkbox, text);
    list.append(label);
  }
  openDialog("#scope-dialog");
}

function showCreate() { $("#create-form").reset(); openDialog("#create-dialog"); }

async function loadDemo(button) {
  await busy(button, "Opening samples…", async () => {
    const result = await api("/demo", { method: "POST" });
    await refreshMeetings();
    const first = result.meetings?.[0];
    if (first) await openMeeting(typeof first === "string" ? first : first.id);
    else if (state.meetings[0]) await openMeeting(state.meetings[0].id);
    notify("Sample meetings are ready. Try a question across all meetings.", "success");
  });
}

function setImportMode(mode) {
  state.importMode = mode;
  $("#text-import-fields").hidden = mode !== "text";
  $("#audio-import-fields").hidden = mode !== "audio";
  $("#import-submit").textContent = mode === "audio" ? "Upload & transcribe" : "Import transcript";
  for (const type of ["text", "audio"]) {
    $(`#import-${type}-tab`).classList.toggle("selected", type === mode);
    $(`#import-${type}-tab`).setAttribute("aria-pressed", String(type === mode));
  }
}

function showImport() {
  if (!state.meeting) return;
  $("#import-form").reset();
  setImportMode("text");
  const provider = state.settings.transcription_provider || "manual";
  $("#audio-provider-note").textContent = provider === "manual" ? "Audio needs a transcription provider. Configure faster-whisper or an OpenAI-compatible API in Settings first." : `Using ${provider === "faster_whisper" ? "local faster-whisper" : "your OpenAI-compatible API"}. Transcription may take several minutes. ${state.settings.retain_audio ? "Audio will be retained on this device." : "Audio will be removed after transcription."}`;
  openDialog("#import-dialog");
}

async function showRecord() {
  if (!state.meeting) return;
  if (state.capture.active) { notify("A recording is already running or finishing. Stop it and wait for transcription to finish before starting another."); return; }
  $("#record-form").reset();
  $("#capture-microphone").checked = true;
  $("#capture-system").checked = false;
  $("#microphone-device").disabled = false;
  $("#speaker-device").disabled = true;
  $("#record-submit").disabled = true;
  $("#device-status").textContent = "Finding audio devices…";
  $("#device-status").className = "info-box";
  openDialog("#record-dialog");
  try {
    const devices = await api("/devices");
    for (const [selector, options] of [["#microphone-device", devices.microphones || []], ["#speaker-device", devices.speakers || []]]) {
      const select = $(selector);
      select.replaceChildren();
      const defaultOption = element("option", "", "System default");
      defaultOption.value = "";
      select.append(defaultOption);
      for (const device of options) { const option = element("option", "", device.name); option.value = String(device.id); select.append(option); }
    }
    const manual = !state.settings.transcription_provider || state.settings.transcription_provider === "manual";
    $("#device-status").textContent = !devices.available ? devices.error || "Audio capture is unavailable. Install the optional audio dependencies listed in the README." : manual ? "Choose a transcription provider in Settings before recording." : "Devices are ready. Use headphones to reduce microphone and system audio overlap.";
    $("#device-status").classList.toggle("error", !devices.available || manual);
    $("#record-submit").disabled = !devices.available || manual;
  } catch (error) { $("#device-status").textContent = error.message; $("#device-status").classList.add("error"); }
}

function setupSecretControls() {
  for (const key of secretKeys) {
    const field = $(`[name="${key}"]`, $("#settings-form"));
    const label = element("label", "check-label clear-key-label full-width");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.name = `${key}_clear`;
    checkbox.addEventListener("change", () => { field.disabled = checkbox.checked; if (checkbox.checked) field.value = ""; });
    label.append(checkbox, element("span", "", "Clear the stored API key"));
    field.parentElement.after(label);
  }
}

async function showSettings() {
  state.settings = await api("/settings");
  const form = $("#settings-form");
  for (const key of settingsKeys) form.elements.namedItem(key).value = state.settings[key] ?? (key === "chunk_seconds" ? 8 : "");
  for (const key of ["retain_audio", "external_processing_consent"]) form.elements.namedItem(key).checked = Boolean(state.settings[key]);
  for (const key of secretKeys) {
    const field = form.elements.namedItem(key);
    field.value = "";
    field.disabled = false;
    field.placeholder = state.settings[`${key}_configured`] ? "Stored key · leave blank to keep" : "Not configured";
    $(".key-note", field.parentElement).textContent = state.settings[`${key}_configured`] ? "A key is saved locally." : "No key is currently saved.";
    const clear = form.elements.namedItem(`${key}_clear`);
    clear.checked = false;
    clear.parentElement.hidden = !state.settings[`${key}_configured`];
  }
  openDialog("#settings-dialog");
}

function updateProviderLabel() {
  const provider = state.settings.chat_provider || "extractive";
  $("#provider-label").textContent = provider === "extractive" ? "Local mode" : provider === "ollama" ? "Ollama" : "API mode";
}

function renderCapture() {
  const capture = state.capture;
  const finishing = capture.state === "stopping";
  $("#capture-banner").hidden = !capture.active;
  $("#capture-banner strong").textContent = finishing ? "Finishing transcription" : "Recording";
  const title = state.meetings.find(meeting => meeting.id === capture.meeting_id)?.title || "Meeting";
  const channels = (capture.channels || []).map(channel => channel === "microphone" ? "Mic" : channel === "system" ? "System" : channel).join(" + ");
  $("#recording-description").textContent = finishing ? `${title} · ${capture.pending_chunks || 0} chunks remaining` : `${title} · ${time(capture.elapsed)}${channels ? ` · ${channels}` : ""}`;
  $("#stop-recording").disabled = finishing;
  $("#stop-recording").textContent = finishing ? "Saving…" : "Stop & save";
  $("#open-record").disabled = Boolean(capture.active);
  $("#delete-meeting").disabled = Boolean(capture.active && capture.meeting_id === state.meeting?.id);
}

async function pollCapture() {
  if (state.pollBusy) return;
  state.pollBusy = true;
  try {
    const previous = state.capture;
    state.capture = await api("/capture");
    renderCapture();
    if (state.capture.error && state.capture.error !== previous.error) notify(state.capture.error, "error");
    const changed = state.capture.segments !== previous.segments || (previous.active && !state.capture.active);
    if (changed && (state.capture.meeting_id || previous.meeting_id)) {
      const id = state.capture.meeting_id || previous.meeting_id;
      await refreshMeetings();
      if (state.meeting?.id === id) {
        const updated = await api(`/meetings/${encodeURIComponent(id)}`);
        if (state.meeting?.id === id) { state.meeting = updated; renderMeeting(); }
      }
      if (previous.active && !state.capture.active && !state.capture.error) notify("Recording saved. Your transcript is ready.", "success");
    }
    $("#connection-status").replaceChildren(element("span", "status-dot"), document.createTextNode("Local workspace"));
  } catch {
    $("#connection-status").textContent = "Server disconnected";
  } finally { state.pollBusy = false; }
}

on("#new-meeting", "click", showCreate);
on("#welcome-new", "click", showCreate);
on("#brand-link", "click", event => { event.preventDefault(); ++state.selectionVersion; showWelcome(); });
on("#load-demo", "click", event => loadDemo(event.currentTarget));
on("#welcome-demo", "click", event => loadDemo(event.currentTarget));
on("#archive-search", "input", renderArchive);
on("#transcript-search", "input", renderTranscript);
on("#open-import", "click", showImport);
on("#open-record", "click", showRecord);
on("#open-settings", "click", showSettings);
on("#import-text-tab", "click", () => setImportMode("text"));
on("#import-audio-tab", "click", () => setImportMode("audio"));
on("#choose-meetings", "click", showScope);
on("#ask-scope", "change", () => { renderScopeLabel(); if ($("#ask-scope").value === "selected") showScope(); });
on("#capture-microphone", "change", () => { $("#microphone-device").disabled = !$("#capture-microphone").checked; });
on("#capture-system", "change", () => { $("#speaker-device").disabled = !$("#capture-system").checked; });
on("#toggle-sidebar", "click", () => { const open = $("#sidebar").classList.toggle("open"); $("#toggle-sidebar").setAttribute("aria-expanded", String(open)); });

document.addEventListener("click", event => {
  if (window.innerWidth <= 720 && $("#sidebar").classList.contains("open") && !event.target.closest("#sidebar, #toggle-sidebar")) { $("#sidebar").classList.remove("open"); $("#toggle-sidebar").setAttribute("aria-expanded", "false"); }
});

for (const dialog of $$("dialog")) {
  for (const close of $$("[data-close]", dialog)) close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
}

document.addEventListener("keydown", event => {
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest("input, textarea, select, [contenteditable=true]") && !$$("dialog").some(dialog => dialog.open)) { event.preventDefault(); showCreate(); }
  if (event.key === "Escape") { $("#sidebar").classList.remove("open"); $("#toggle-sidebar").setAttribute("aria-expanded", "false"); }
});

on("#create-form", "submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, null);
  const data = new FormData(form);
  try {
    const title = String(data.get("title") || "").trim();
    if (!title) throw new Error("Give this meeting a title.");
    if (!data.get("consent")) throw new Error("Participant permission is required to save a meeting.");
    await busy($("button[type=submit]", form), "Creating…", async () => {
      const meeting = await api("/meetings", { method: "POST", body: JSON.stringify({ title, description: String(data.get("description") || "").trim(), consent: true }) });
      await refreshMeetings();
      await openMeeting(meeting.id);
      $("#create-dialog").close();
      notify("Meeting created. Import a transcript or start a recording.", "success");
    });
  } catch (error) { formError(form, error); }
});

on("#transcript-file", "change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { formError($("#import-form"), "This transcript is over 2 MB. Split it into smaller files before importing."); return; }
  $("#import-text").value = await file.text();
  const extension = file.name.split(".").pop().toLowerCase();
  $("#import-format").value = ["srt", "vtt", "json"].includes(extension) ? extension : "auto";
  formError($("#import-form"), null);
});

on("#import-form", "submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, null);
  const id = state.meeting?.id;
  try {
    if (!id) throw new Error("Select a meeting first.");
    if (!$("#import-consent").checked) throw new Error("Permission is required before importing this conversation.");
    const audio = state.importMode === "audio";
    const file = $("#audio-file").files[0];
    const transcript = $("#import-text").value.trim();
    if (audio && !file) throw new Error("Choose an audio file to upload.");
    if (audio && (!state.settings.transcription_provider || state.settings.transcription_provider === "manual")) throw new Error("Set an audio transcription provider in Settings first.");
    if (!audio && !transcript) throw new Error("Choose a transcript file or paste some text.");
    await busy($("#import-submit"), audio ? "Transcribing audio…" : "Importing…", async () => {
      if (audio) await api(`/meetings/${encodeURIComponent(id)}/audio?filename=${encodeURIComponent(file.name)}&consent=true`, { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
      else await api(`/meetings/${encodeURIComponent(id)}/transcript`, { method: "POST", body: JSON.stringify({ text: transcript, format: $("#import-format").value }) });
      await refreshMeetings();
      if (state.meeting?.id === id) await openMeeting(id);
      $("#import-dialog").close();
      notify(audio ? "Audio transcribed and added to your meeting." : "Transcript imported. Your meeting is ready to explore.", "success");
    });
  } catch (error) { formError(form, error); }
});

on("#record-form", "submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, null);
  try {
    if (!$("#record-consent").checked) throw new Error("Participant consent is required before recording.");
    if (!$("#capture-microphone").checked && !$("#capture-system").checked) throw new Error("Select at least one audio source.");
    await busy($("#record-submit"), "Starting…", async () => {
      state.capture = await api("/capture/start", { method: "POST", body: JSON.stringify({ meeting_id: state.meeting.id, microphone_id: $("#microphone-device").value || null, speaker_id: $("#speaker-device").value || null, capture_microphone: $("#capture-microphone").checked, capture_system: $("#capture-system").checked, consent: true }) });
      renderCapture();
      $("#record-dialog").close();
      notify("Recording started. The transcript will appear as audio is processed.", "success");
    });
  } catch (error) { formError(form, error); }
});

on("#stop-recording", "click", async () => {
  try { await busy($("#stop-recording"), "Finishing…", async () => { state.capture = await api("/capture/stop", { method: "POST" }); }); }
  finally { renderCapture(); }
  await refreshMeetings();
  if (state.meeting?.id === state.capture.meeting_id) {
    state.meeting = await api(`/meetings/${encodeURIComponent(state.meeting.id)}`);
    renderMeeting();
  }
  notify(state.capture.active ? "Finishing the remaining audio. Your transcript will update when it is ready." : "Recording saved.", state.capture.active ? "" : "success");
});

on("#settings-form", "submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, null);
  try {
    const settings = {};
    for (const key of settingsKeys) settings[key] = form.elements.namedItem(key).value.trim();
    settings.chunk_seconds = Number(settings.chunk_seconds);
    for (const key of ["retain_audio", "external_processing_consent"]) settings[key] = form.elements.namedItem(key).checked;
    for (const key of secretKeys) {
      if (form.elements.namedItem(`${key}_clear`).checked) settings[key] = "";
      else if (form.elements.namedItem(key).value.trim()) settings[key] = form.elements.namedItem(key).value.trim();
    }
    await busy($("button[type=submit]", form), "Saving…", async () => {
      await api("/settings", { method: "PUT", body: JSON.stringify(settings) });
      state.settings = await api("/settings");
      updateProviderLabel();
      $("#settings-dialog").close();
      for (const key of secretKeys) form.elements.namedItem(key).value = "";
      notify("Settings saved on this device.", "success");
    });
  } catch (error) { formError(form, error); }
});

on('#settings-form [name="transcription_provider"]', "change", event => {
  const field = $('#settings-form [name="transcription_model"]');
  if (event.target.value === "openai" && ["", "tiny", "base", "small", "medium", "large-v3"].includes(field.value)) field.value = "whisper-1";
  else if (event.target.value === "faster_whisper" && ["", "whisper-1"].includes(field.value)) field.value = "tiny";
});

on('#settings-form [name="chat_provider"]', "change", event => {
  const field = $('#settings-form [name="chat_base_url"]');
  if (event.target.value === "openai" && ["", "http://localhost:11434/v1"].includes(field.value)) field.value = "https://api.openai.com/v1";
  else if (event.target.value === "ollama" && ["", "https://api.openai.com/v1"].includes(field.value)) field.value = "http://localhost:11434/v1";
});

on("#scope-form", "submit", event => {
  event.preventDefault();
  const selected = $$('#scope-meetings input:checked').map(input => input.value);
  if (!selected.length) { formError(event.currentTarget, "Select at least one meeting."); return; }
  state.selected = new Set(selected);
  renderScopeLabel();
  $("#scope-dialog").close();
});

on("#ask-form", "submit", async event => {
  event.preventDefault();
  if (state.asking) return;
  const question = $("#question").value.trim();
  const id = state.meeting?.id;
  if (!question || !id) return;
  const scope = $("#ask-scope").value;
  if (scope === "selected" && !state.selected.size) { showScope(); return; }
  const meetingIds = scope === "all" ? null : scope === "selected" ? [...state.selected] : [id];
  state.asking = true;
  $("#ask-submit").disabled = true;
  $("#ask-hint").textContent = "Reading the relevant passages…";
  state.messages.push({ role: "user", content: question });
  $("#question").value = "";
  renderConversation();
  try {
    const result = await api("/ask", { method: "POST", body: JSON.stringify({ question, meeting_ids: meetingIds, meeting_id: id, strategy: "contextual" }) });
    if (state.meeting?.id === id) state.messages.push({ role: "assistant", content: result.answer, metadata: result });
    else notify("Your answer is saved in the meeting where you asked the question.", "success");
  } catch (error) {
    report(error);
    if (state.meeting?.id === id) {
      $("#question").value = question;
      try { state.messages = await api(`/meetings/${encodeURIComponent(id)}/messages`); }
      catch { state.messages = state.messages.filter((message, index, array) => !(index === array.length - 1 && message.role === "user" && message.content === question)); }
    }
  } finally {
    state.asking = false;
    $("#ask-submit").disabled = false;
    $("#ask-hint").textContent = "Every answer starts with evidence.";
    renderConversation();
  }
});

on("#question", "keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#ask-form").requestSubmit(); }
});

on("#export-meeting", "click", async event => {
  const id = state.meeting?.id;
  if (!id) return;
  await busy(event.currentTarget, "", async () => {
    const data = await api(`/meetings/${encodeURIComponent(id)}/export`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = element("a");
    anchor.href = url;
    anchor.download = `${String(state.meeting.title || "meeting").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 120)}.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    notify("Meeting export downloaded.", "success");
  });
});

on("#delete-meeting", "click", () => {
  if (!state.meeting) return;
  $("#delete-description").textContent = `“${state.meeting.title}” will be deleted. This cannot be undone.`;
  openDialog("#delete-dialog");
});

on("#delete-form", "submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  formError(form, null);
  const id = state.meeting?.id;
  if (!id) return;
  try {
    await busy($("button[type=submit]", form), "Deleting…", async () => {
      await api(`/meetings/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.selected.delete(id);
      await refreshMeetings();
      if (state.meetings[0]) await openMeeting(state.meetings[0].id);
      else showWelcome();
      $("#delete-dialog").close();
      notify("Meeting deleted.", "success");
    });
  } catch (error) { formError(form, error); }
});

async function initialize() {
  $("#page-loading").hidden = false;
  try {
    const [meetings, settings, capture] = await Promise.all([api("/meetings"), api("/settings"), api("/capture")]);
    state.meetings = meetings;
    state.settings = settings;
    state.capture = capture;
    renderArchive();
    updateProviderLabel();
    renderCapture();
    if (meetings.length) await openMeeting(capture.active ? capture.meeting_id : meetings[0].id);
    else showWelcome();
  } catch (error) {
    const loading = $("#page-loading");
    loading.className = "offline-state";
    loading.replaceChildren(element("h2", "", "Let’s reconnect your workspace."), element("p", "", error.message), textButton("Try again", "button button-primary", initialize));
  }
}

setupSecretControls();
initialize();
setInterval(pollCapture, 3000);
