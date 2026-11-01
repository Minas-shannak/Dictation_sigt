// ---- Config --------------------------------------------------------------------
// None of these are reserved by Chrome, so preventDefault() works for all of them.
const SHORTCUTS = {
  replay:     { key: "Tab" },
  replaySlow: { key: "Tab", shift: true },
  nextLine:   { key: "ArrowRight", ctrl: true },
  prevLine:   { key: "ArrowLeft", ctrl: true },
  hint:       { key: "h", ctrl: true },
  acceptWord: { key: "Enter", ctrl: true },
  playPause:  { key: "Enter" },          // plain Enter; Ctrl+Enter is acceptWord
  deleteBack: { key: "Backspace" },
  toggleMute: { key: "m", ctrl: true },
};
const MAX_WRONG_ATTEMPTS = 3;  // wrong keys on one letter before it's revealed automatically
const SLOW_RATE = 0.75;
const PLAY = {
  padBefore: 0.1,     // seconds before the first word and after the last one;
  padAfter: 0.25,     // replaced by the server's values (config.py) at startup
  offsetMin: -0.5,
  offsetMax: 0.5,
  offsetStep: 0.1,
};
const TYPING = {
  maskChar: "·",
  completePauseMs: 600,   // show the finished line this long before moving on
  wrongFlashMs: 400,      // a wrong key shows in red this long
  wrongSpaceMark: "␣",    // how a wrongly typed space appears in the word box
};
const STORE_PREFIX = "dictation:";
const DATA_FORMAT_VERSION = 2;  // the audio-based version; older saved data is backed up and cleared once

// ---- Text normalization ----------------------------------------------------------
const PLAIN_ASCII = {
  "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"',
  "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
};
// Lowercase, strip accents, map curly quotes and dashes to ASCII.
function normalizeText(s) {
  return s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/./gu, (c) => PLAIN_ASCII[c] || c);
}
// Letters and digits are typed (plus the spaces between words); punctuation is skipped.
const isTypeable = (normalized) => /^[\p{L}\p{N}]+$/u.test(normalized);

// ---- Storage (one record per video) ------------------------------------------------
function loadJSON(key, fallback) {
  try { const v = localStorage.getItem(STORE_PREFIX + key); return v ? JSON.parse(v) : fallback; }
  catch { return fallback; }
}
function saveJSON(key, value) {
  try { localStorage.setItem(STORE_PREFIX + key, JSON.stringify(value)); } catch {}
}
// stats: { [lineIndex]: { chars, ms, correct, keys } }, redoing a line overwrites it.
// missed: [{ word, count }]
function videoRecord(id) {
  return Object.assign({ id, title: "", lang: "en", lineIndex: 0, lineCount: 0, offset: 0, lastOpened: 0,
                         stats: {}, missed: [] },
                       loadJSON("video:" + id, {}));
}
function allVideoRecords() {
  const recs = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith(STORE_PREFIX + "video:")) recs.push(videoRecord(key.slice((STORE_PREFIX + "video:").length)));
    }
  } catch {}
  return recs.sort((a, b) => b.lastOpened - a.lastOpened);
}
function saveVideoRecord(rec) { saveJSON("video:" + rec.id, rec); }

// ---- State ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const state = {
  data: null,        // { videoId, lang, source, lines }
  rec: null,         // persisted per-video record
  index: 0,          // current line
  player: null,
  playerReady: false,
  playerRunning: false, // frames advancing (not paused, not buffering)
  watch: null,       // { from, to, armed } while a line is playing
  rafId: 0,
  lineFinished: false, // the current line played to its end (Enter then replays it)
  slots: [],         // one per character of the current line
  cursor: -1,        // slot index to type next, -1 when the line is done
  completing: false, // during the pause after a finished line
  wrongAt: -1,       // slot currently flashing red
  wrongKey: "",      // the wrong key shown in red in the word box
  wrongTimer: 0,
  lineToken: 0,      // bumps on every line change, to cancel stale timers
  hintStreak: 0,     // consecutive Ctrl+H presses with no typing in between
  wrongAttempts: 0,  // wrong keys on the slot under the cursor
  line: null,       // per-line counters, see newLineCounters()
  playingSince: null,
};

function newLineCounters() {
  return { firstKeyAt: null, playMs: 0, correct: 0, keys: 0, missedWords: new Set() };
}

// ---- Loading ---------------------------------------------------------------------------
$("loadForm").addEventListener("submit", (e) => {
  e.preventDefault();
  loadVideo($("urlInput").value.trim(), $("langInput").value.trim());  // empty language = detect
});

// YouTube: the server downloads the audio and transcribes it in a background job,
// shown in the same processing panel as uploads.
async function loadVideo(url, lang) {
  $("loadError").textContent = "";
  if (!url) return;
  let res, body;
  try {
    res = await fetch("/api/youtube", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, language: lang }),
    });
    body = await res.json();
  } catch (err) {
    $("loadError").textContent = "Could not reach the local server. Is `python app.py` running?";
    return;
  }
  if (!res.ok) { $("loadError").textContent = body.message || "Something went wrong."; return; }
  resetProcessing(body.video.youtube_id, "starting");
  startLocalJob(body);
}

// data: the unified transcript ({video_id, transcript_version, sentences: [{start, end,
// play_start, text, unclear, ...}]}); makePlayer(container, events) -> player.
function startPractice(data, lang, makePlayer, info = {}) {
  state.data = data;
  state.rec = videoRecord(data.video_id);
  // Saved progress belongs to one version of the transcript. If it was regenerated (other
  // sentences), only the position is reset; the statistics are kept.
  if (state.rec.transcriptVersion && state.rec.transcriptVersion !== data.transcript_version) {
    state.rec.archivedStats = (state.rec.archivedStats || []).concat(Object.values(state.rec.stats || {}));
    state.rec.stats = {};
    state.rec.lineIndex = 0;
    showNotice("The transcript of this video was regenerated, so your position was reset. Your statistics were kept.");
  }
  state.rec.transcriptVersion = data.transcript_version;
  if (info.title) state.rec.title = info.title;
  state.rec.lineCount = data.sentences.length;
  state.rec.lang = lang;
  state.rec.lastOpened = Date.now();
  saveVideoRecord(state.rec);
  state.index = Math.min(state.rec.lineIndex, data.sentences.length - 1);

  $("loadScreen").classList.add("hidden");
  $("completeScreen").classList.add("hidden");
  $("practiceScreen").classList.remove("hidden");
  $("videoTitle").textContent = state.rec.title || data.video_id;
  $("asrInfo").textContent = info.asr || "";
  renderOffset();
  renderStats(null);
  renderMissed();
  createPlayer(makePlayer);
  showLine(state.index);
  focusTyper();
}

// Finished lines of the current transcript version; the last line finished means "end of tape".
function finishedLines(rec) {
  return Object.keys(rec.stats || {}).filter((k) => Number(k) < rec.lineCount).length;
}
function reachedEnd(rec) {
  return rec.lineCount > 0 && !!(rec.stats || {})[rec.lineCount - 1];
}

// A cassette whose reels show progress: the left reel shrinks and the right one grows as lines
// are finished. kind: "" (in progress), "done" or "expired" (colors the label).
const REEL = { min: 22, max: 58 };  // reel diameters in px
function cassette(title, fraction, kind = "") {
  const el = document.createElement("div");
  el.className = "cassette";
  el.innerHTML = `<div class="cassetteLabel"><span class="cassetteTitle"></span></div>` +
    `<div class="cassetteWindow" role="img"><span class="reelSlot"><span class="reel"><span></span></span></span>` +
    `<span class="tapeLine"></span><span class="reelSlot"><span class="reel"><span></span></span></span></div>`;
  if (kind) el.firstChild.classList.add(kind);
  el.querySelector(".cassetteTitle").textContent = title;
  const f = Math.max(0, Math.min(1, fraction || 0));
  const [left, right] = el.querySelectorAll(".reel");
  const size = (x) => `${Math.round(REEL.min + (REEL.max - REEL.min) * x)}px`;
  left.style.width = left.style.height = size(1 - f);
  right.style.width = right.style.height = size(f);
  el.querySelector(".cassetteWindow").setAttribute("aria-label", `${Math.round(f * 100)}% of the lines finished`);
  return el;
}

const TRASH_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/>' +
  '<path d="M6 6l1 15h10l1-15"/><path d="M10 11v6M14 11v6"/></svg>';

function renderRecent() {
  // A video waiting out its undo window is already gone from the list.
  const recs = allVideoRecords().filter((r) => !pendingDelete || r.id !== pendingDelete.rec.id);
  $("recent").classList.toggle("hidden", recs.length === 0);
  const list = $("recentList");
  list.textContent = "";
  for (const rec of recs) {
    const li = document.createElement("li");
    const avg = videoAverages(rec);
    const done = reachedEnd(rec);
    const tape = cassette(videoName(rec), rec.lineCount ? finishedLines(rec) / rec.lineCount : 0,
                          rec.expired ? "expired" : done ? "done" : "");
    const del = Object.assign(document.createElement("button"), {
      className: "deleteBtn", type: "button", title: "Delete this video", innerHTML: TRASH_ICON });
    del.setAttribute("aria-label", "Delete this video");
    tape.firstChild.appendChild(del);
    const foot = document.createElement("div");
    foot.className = "cassetteFoot";
    foot.innerHTML = `<span class="info"></span><span class="go"></span>`;
    const accText = avg.acc === null ? "–" : `${avg.acc}%`;
    foot.children[0].textContent = rec.expired ? "File expired, upload it again"
      : done ? `Done · ${avg.wpm === null ? "–" : avg.wpm} WPM · ${accText}`
      : `Line ${rec.lineIndex + 1} / ${rec.lineCount}` + (avg.acc === null ? "" : ` · ${accText}`);
    foot.children[1].textContent = rec.expired ? "" : done ? "Open →" : "Resume →";
    tape.appendChild(foot);
    li.appendChild(tape);
    const local = rec.id.startsWith("local:");
    li.addEventListener("click", () => {
      $("langInput").value = rec.lang || "en";
      if (local) { openLocalVideo(rec); return; }
      const youtubeId = rec.id.replace(/^yt:/, "");  // records from before Phase 6 use the bare ID
      $("urlInput").value = `https://www.youtube.com/watch?v=${youtubeId}`;
      loadVideo(youtubeId, rec.lang || "en");
    });
    del.addEventListener("click", (e) => { e.stopPropagation(); askDelete(rec); });
    list.appendChild(li);
  }
}

// ---- Notices ------------------------------------------------------------------------------
// action: optional { label, onClick } shown as a button (e.g. Undo).
function showNotice(text, action = null) {
  $("noticeText").textContent = text;
  $("noticeAction").classList.toggle("hidden", !action);
  $("noticeAction").textContent = action ? action.label : "";
  $("noticeAction").onclick = action ? action.onClick : null;
  $("notice").classList.remove("hidden");
}
function hideNotice() { $("notice").classList.add("hidden"); }
$("noticeClose").addEventListener("click", hideNotice);

// ---- Deleting a video -----------------------------------------------------------------------
// After confirming, the entry disappears and an Undo notice shows for UNDO_SECONDS; only then is
// anything deleted (server: cached transcripts of every variant and, for an upload, the stored
// file; browser: the video's record with its progress, stats, missed words and offset). The
// pending deletion lives only in memory, so closing or reloading the page cancels it.
const UNDO_SECONDS = 8;
let pendingDelete = null;  // { rec, timer }

function videoName(rec) { return rec.title || rec.id; }

function askDelete(rec) {
  const what = rec.id.startsWith("local:")
    ? "its saved progress, statistics, missed words, transcripts and the uploaded video file are deleted."
    : "its saved progress, statistics, missed words and transcripts are deleted (the video on YouTube is not affected).";
  $("deleteText").textContent = `Delete "${videoName(rec)}"? Everything the app keeps for it: ${what}`;
  $("deleteDialog").returnValue = "";
  $("deleteDialog").onclose = () => { if ($("deleteDialog").returnValue === "delete") startDelete(rec); };
  $("deleteDialog").showModal();
  $("deleteCancel").focus();
}
$("deleteCancel").addEventListener("click", () => $("deleteDialog").close("cancel"));
$("deleteConfirm").addEventListener("click", () => $("deleteDialog").close("delete"));

function startDelete(rec) {
  if (pendingDelete) commitDelete();  // one at a time: finish the earlier one now
  pendingDelete = { rec, timer: setTimeout(commitDelete, UNDO_SECONDS * 1000) };
  renderRecent();
  showNotice(`"${videoName(rec)}" will be deleted.`, { label: "Undo", onClick: undoDelete });
}

function undoDelete() {
  if (!pendingDelete) return;
  clearTimeout(pendingDelete.timer);
  pendingDelete = null;
  hideNotice();
  renderRecent();
}

async function commitDelete() {
  if (!pendingDelete) return;
  const { rec, timer } = pendingDelete;
  clearTimeout(timer);
  pendingDelete = null;
  hideNotice();
  // Leave the video first if it is open or being processed here.
  if (state.data && state.data.video_id === rec.id && !$("practiceScreen").classList.contains("hidden")) $("backBtn").click();
  if (local.video && local.video.video_id === rec.id) {
    local.job = local.convertJob = null;
    $("processing").classList.add("hidden");
  }
  if (rec.id.includes(":")) {  // records from before Phase 6 (bare IDs) have nothing on the server
    try {
      const res = await fetch(`/api/videos/${encodeURIComponent(rec.id)}`, { method: "DELETE" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.message || `HTTP ${res.status}`);
    } catch (err) {
      showNotice(`"${videoName(rec)}" could not be deleted: ${err.message}`);
      renderRecent();
      return;
    }
  }
  try { localStorage.removeItem(STORE_PREFIX + "video:" + rec.id); } catch {}
  renderRecent();
}

// ---- One-time clean start ---------------------------------------------------------------------
// Progress saved by the caption-based version (records keyed by a bare YouTube ID) doesn't
// match the audio transcripts. On the first visit it is sent to the server's backup folder,
// downloaded as a file too, and only then removed. Sound and theme settings stay.
async function migrateOldData() {
  if (loadJSON("dataVersion", 0) >= DATA_FORMAT_VERSION) return;
  const keys = [];
  try { for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i)); } catch { return; }
  const videoPrefix = STORE_PREFIX + "video:";
  const old = keys.filter((k) => k.startsWith(videoPrefix) && !k.slice(videoPrefix.length).includes(":"));
  if (!old.length) { saveJSON("dataVersion", DATA_FORMAT_VERSION); return; }
  const items = {};
  for (const k of keys) if (k.startsWith(STORE_PREFIX)) items[k] = localStorage.getItem(k);
  let path;
  try {
    const res = await fetch("/api/migration/browser-backup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, origin: location.origin }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    path = (await res.json()).path;
  } catch {
    showNotice("Your old progress could not be backed up, so it was left as it is. Make sure `python app.py` is running, then reload.");
    return;
  }
  const blob = new Blob([JSON.stringify({ origin: location.origin, saved_at: new Date().toISOString(), items }, null, 1)],
                        { type: "application/json" });
  const link = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: "dictation-old-progress-backup.json",
  });
  document.body.appendChild(link);
  link.click();
  link.remove();
  for (const k of old) localStorage.removeItem(k);
  saveJSON("dataVersion", DATA_FORMAT_VERSION);
  showNotice(`Transcripts now come from the audio, so your old caption-based progress (${old.length} ` +
    `video${old.length === 1 ? "" : "s"}) was backed up to ${path} (and downloaded) and cleared. ` +
    "Your sound and theme settings were kept.");
}

fetch("/api/config").then((r) => r.json()).then((c) => {
  PLAY.padBefore = c.pad_before;
  PLAY.padAfter = c.pad_after;
}).catch(() => {});
migrateOldData().finally(renderRecent);

$("backBtn").addEventListener("click", () => {
  stopWatching();
  if (state.player) { state.player.destroy(); state.player = null; state.playerReady = false; state.playerRunning = false; }
  $("asrInfo").textContent = "";
  renderPlayPause(false);
  $("completeScreen").classList.add("hidden");
  $("player").replaceWith(Object.assign(document.createElement("div"), { id: "player" }));
  $("playerError").classList.add("hidden");
  $("practiceScreen").classList.add("hidden");
  $("loadScreen").classList.remove("hidden");
  renderRecent();
});

// ---- Local videos ------------------------------------------------------------------------
// Upload, then poll the transcription job (about once a second). Converting a format the
// browser can't play is a separate job. The server runs jobs one at a time.
const STAGES = {
  uploading: "Uploading",
  opening: "Opening",
  starting: "Starting",
  downloading_audio: "Downloading audio",
  queued: "Waiting for another video to finish",
  extracting_audio: "Extracting audio",
  loading_model: "Loading the AI model (first run only, can take a while)",
  transcribing: "Transcribing audio",
  processing: "Processing transcript",
  preparing: "Preparing dictation exercises",
  converting: "Converting the video",
};
const local = { video: null, job: null, convertJob: null, transcript: null, xhr: null };

$("fileInput").addEventListener("change", () => {
  const file = $("fileInput").files[0];
  $("fileInput").value = "";
  if (file) uploadVideo(file);
});

function uploadVideo(file) {
  resetProcessing(file.name);
  const form = new FormData();
  form.append("file", file);
  form.append("language", $("langInput").value.trim());
  const xhr = new XMLHttpRequest();
  local.xhr = xhr;
  xhr.open("POST", "/api/uploads");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) renderProc("uploading", e.loaded / e.total); };
  xhr.onload = () => {
    local.xhr = null;
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status !== 200) { processingError(body.message || `Upload failed (${xhr.status}).`); return; }
    startLocalJob(body);
  };
  xhr.onerror = () => { local.xhr = null; processingError("Could not reach the local server. Is `python app.py` running?"); };
  xhr.send(form);
}

// Recent videos: a stored upload is opened again through the server (it may have expired).
async function openLocalVideo(rec) {
  $("loadError").textContent = "";
  let res, body;
  try {
    res = await fetch(`/api/local/${encodeURIComponent(rec.id)}/open`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: $("langInput").value.trim() }),
    });
    body = await res.json();
  } catch {
    $("loadError").textContent = "Could not reach the local server. Is `python app.py` running?";
    return;
  }
  if (!res.ok) {
    if (body.error === "expired" || body.error === "not_found") { rec.expired = true; saveVideoRecord(rec); renderRecent(); }
    $("loadError").textContent = body.message || "This video could not be opened.";
    return;
  }
  if (rec.expired) { rec.expired = false; saveVideoRecord(rec); }
  resetProcessing(rec.title || body.video.original_filename, "opening");
  startLocalJob(body);
}

function startLocalJob(body) {
  local.video = body.video;
  local.transcript = null;
  local.convertJob = null;
  local.job = body.job.id;
  renderCodecWarning();
  pollJob(body.job.id, onTranscribeStatus);
}

function pollJob(id, onStatus) {
  const tick = async () => {
    if (local.job !== id && local.convertJob !== id) return;  // cancelled or replaced
    let res, st;
    try { res = await fetch(`/api/jobs/${id}`); st = await res.json(); }
    catch { processingError("Lost contact with the local server."); return; }
    if (!res.ok) { processingError(st.message); return; }
    onStatus(st);
    if (!["done", "failed", "cancelled"].includes(st.state)) setTimeout(tick, 1000);
  };
  tick();
}

function onTranscribeStatus(st) {
  if (st.state === "queued") renderProc("queued", null, st.queue_position ? ` (${st.queue_position} ahead)` : "");
  else if (st.state === "running") {
    const measured = st.stage === "transcribing" || st.stage === "downloading_audio";
    renderProc(st.stage || "extracting_audio", measured ? st.progress : null);
  }
  if (st.title) $("procTitle").textContent = st.title;
  if (st.device) {
    $("procDevice").textContent = st.from_cache ? "loaded from cache"
      : st.device === "cpu" ? `CPU (${st.compute_type})` : `GPU (${st.device}, ${st.compute_type})`;
    $("procCpu").classList.toggle("hidden", st.device !== "cpu" || st.from_cache || st.state !== "running");
  }
  if (st.state === "done") { local.job = null; local.transcript = st.result; maybeStartLocal(); }
  else if (st.state === "failed") processingError(st.error.message);
  else if (st.state === "cancelled") processingError("Cancelled.");
}

function renderCodecWarning() {
  const v = local.video;
  $("procCodec").classList.toggle("hidden", v.browser_playable);
  $("procCodecText").textContent = `This video's format (${v.video_codec}) may not play in the browser. ` +
    "Its audio is transcribed anyway; convert it to practice with the video.";
  $("convertBtn").disabled = !!local.convertJob;
}

$("convertBtn").addEventListener("click", async () => {
  let res, body;
  try {
    res = await fetch(`/api/local/${encodeURIComponent(local.video.video_id)}/convert`, { method: "POST" });
    body = await res.json();
  } catch { $("convertStatus").textContent = "Could not reach the local server."; return; }
  if (!res.ok) { $("convertStatus").textContent = body.message; return; }
  local.convertJob = body.job.id;
  renderCodecWarning();
  pollJob(body.job.id, onConvertStatus);
});

function onConvertStatus(st) {
  const pct = st.progress === null ? "" : ` ${Math.round(st.progress * 100)}%`;
  if (st.state === "queued") $("convertStatus").textContent = "Waiting to convert…";
  else if (st.state === "running") $("convertStatus").textContent = `Converting…${pct}`;
  else if (st.state === "done") {
    local.convertJob = null;
    local.video = st.result;
    $("convertStatus").textContent = "";
    renderCodecWarning();
    maybeStartLocal();
  } else {
    local.convertJob = null;
    $("convertStatus").textContent = st.state === "cancelled" ? "Conversion cancelled." : st.error.message;
    renderCodecWarning();
  }
}

// Practice starts when the transcript is ready and the browser can play the video.
function maybeStartLocal() {
  const t = local.transcript;
  if (!t) return;
  if (!local.video.browser_playable) {
    renderProc(null, null, local.convertJob ? "Transcript ready. Waiting for the conversion…"
      : "Transcript ready. Convert the video to practice with it.");
    return;
  }
  const asr = `${t.model} · ${t.device === "cpu" ? "CPU" : "GPU"} ${t.compute_type}`;
  const youtubeId = local.video.youtube_id;
  $("processing").classList.add("hidden");
  startPractice(t, $("langInput").value.trim(),
    youtubeId ? (box, events) => makeYouTubePlayer(box, youtubeId, events)
              : (box, events) => makeHtml5Player(box, `/media/${encodeURIComponent(t.video_id)}`, events),
    { title: local.video.original_filename, asr });
}

$("cancelBtn").addEventListener("click", () => {
  if (local.xhr) local.xhr.abort();
  for (const id of [local.job, local.convertJob]) {
    if (id) fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).catch(() => {});
  }
  local.job = local.convertJob = local.xhr = null;
  processingError("Cancelled.");
});

function resetProcessing(title, stage = "uploading") {
  $("loadError").textContent = "";
  $("procTitle").textContent = title;
  $("procDevice").textContent = "";
  $("procCpu").classList.add("hidden");
  $("procCodec").classList.add("hidden");
  $("convertStatus").textContent = "";
  renderProc(stage, stage === "uploading" ? 0 : null);
  $("processing").classList.remove("hidden");
}

// stage: a STAGES key (or null for just `extra`); fraction: 0..1, or null when unknown.
function renderProc(stage, fraction, extra = "") {
  const pct = fraction === null || fraction === undefined ? "" : ` ${Math.round(fraction * 100)}%`;
  $("procStage").textContent = (stage ? STAGES[stage] || stage : "") + pct + extra;
  $("procFill").classList.toggle("indeterminate", stage !== null && (fraction === null || fraction === undefined));
  $("procFill").style.width = fraction === null || fraction === undefined ? "" : `${fraction * 100}%`;
}

function processingError(message) {
  local.job = local.convertJob = null;
  $("processing").classList.add("hidden");
  $("loadError").textContent = message;
}

// ---- Players ---------------------------------------------------------------------------
// The dictation engine uses one small interface, whatever the video source:
//   play(), pause(), seekTo(seconds), getCurrentTime(), getDuration(), isPlaying(),
//   setRate(rate), destroy()
// and reports through events: onReady(title), onPlayingChange(playing, running), onError(message).
// "playing" includes buffering (for the play/pause icon); "running" means frames are actually
// advancing (the clock that keeps playback time out of the typing time).
let ytApiReady = new Promise((resolve) => { window.onYouTubeIframeAPIReady = resolve; });
(function loadYouTubeApi() {
  const s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(s);
})();

async function makeYouTubePlayer(container, videoId, events) {
  await ytApiReady;
  let yt;
  // YouTube's own subtitles would give the answer away.
  const hideCaptions = () => { try { yt.unloadModule("captions"); yt.unloadModule("cc"); } catch {} };
  // Buffering right after a seek counts as playing: it's about to start.
  const playing = (s) => s === YT.PlayerState.PLAYING || s === YT.PlayerState.BUFFERING;
  const messages = {
    2: "Invalid video ID.",
    5: "This video can't be played in an HTML5 player.",
    100: "This video was removed or is private.",
    101: "The owner doesn't allow this video to be embedded, so it can't be played here.\nTry another video.",
    150: "The owner doesn't allow this video to be embedded, so it can't be played here.\nTry another video.",
  };
  // A pause requested while YouTube is still buffering after a seek only takes effect when
  // the buffering ends (up to ~0.6 s later). Remember the request so the player reports
  // "paused" right away (icon, Enter) and pauses again if YouTube starts playing anyway.
  let pauseRequested = false;
  yt = new YT.Player(container, {
    videoId,
    playerVars: {
      controls: 0, disablekb: 1, rel: 0, playsinline: 1, iv_load_policy: 3,
      cc_load_policy: 0, origin: location.origin,
    },
    events: {
      onReady: () => { hideCaptions(); events.onReady(yt.getVideoData().title); },
      onStateChange: (e) => {
        if (e.data === YT.PlayerState.PLAYING) {
          hideCaptions();
          if (pauseRequested) { yt.pauseVideo(); return; }
        }
        events.onPlayingChange(playing(e.data) && !pauseRequested, e.data === YT.PlayerState.PLAYING);
      },
      onError: (e) => events.onError(messages[e.data] || `Player error ${e.data}.`),
    },
  });
  return {
    play: () => { pauseRequested = false; yt.playVideo(); },
    pause: () => { pauseRequested = true; yt.pauseVideo(); events.onPlayingChange(false, false); },
    seekTo: (t) => yt.seekTo(t, true),
    getCurrentTime: () => yt.getCurrentTime(),
    getDuration: () => yt.getDuration(),
    isPlaying: () => !pauseRequested && playing(yt.getPlayerState()),
    setRate: (r) => yt.setPlaybackRate(r),
    destroy: () => yt.destroy(),
  };
}

function makeHtml5Player(container, src, events) {
  const video = document.createElement("video");
  Object.assign(video, { src, preload: "auto", playsInline: true, controls: false });
  container.appendChild(video);
  const playing = () => !video.paused && !video.ended;
  video.addEventListener("loadedmetadata", () => events.onReady(null), { once: true });
  for (const name of ["playing", "pause", "ended", "waiting"]) {
    video.addEventListener(name, () => events.onPlayingChange(playing(), name === "playing"));
  }
  video.addEventListener("error", () => events.onError(video.error && video.error.code === 4
    ? "This browser can't play this video's format. Go back and use Convert."
    : "The video could not be loaded."));
  return {
    play: () => { video.play().catch(() => {}); },
    pause: () => video.pause(),
    seekTo: (t) => { video.currentTime = t; },
    getCurrentTime: () => video.currentTime,
    getDuration: () => video.duration || 0,
    isPlaying: playing,
    setRate: (r) => { video.playbackRate = r; },
    destroy: () => { video.pause(); video.removeAttribute("src"); video.load(); video.remove(); },
  };
}

async function createPlayer(makePlayer) {
  state.playerReady = false;
  const player = await makePlayer($("player"), {
    onReady: (title) => {
      state.playerReady = true;
      playLine(state.index);
      if (title && state.rec) {
        state.rec.title = title;
        saveVideoRecord(state.rec);
        $("videoTitle").textContent = title;
      }
    },
    onPlayingChange: (playing, running) => {
      renderPlayPause(playing);
      state.playerRunning = running;
      if (running) {
        if (state.playingSince === null) state.playingSince = performance.now();
      } else {
        stopPlaybackClock();
      }
    },
    onError: (message) => {
      $("playerError").textContent = message;
      $("playerError").classList.remove("hidden");
    },
  });
  state.player = player;
}

// Playback time after the first keystroke is excluded from the line's typing time.
function stopPlaybackClock() {
  if (state.playingSince === null) return;
  const now = performance.now();
  const c = state.line;
  if (c && c.firstKeyAt !== null) c.playMs += now - Math.max(state.playingSince, c.firstKeyAt);
  state.playingSince = null;
}

// The part of the video a sentence plays: padded a little (word timestamps are estimates),
// but never into the previous sentence's last word or the next one's first word. It starts
// at play_start, which is later than start when Whisper stretched the first word over the
// music before it. The per-video offset applies on top.
function lineWindow(i) {
  const list = state.data.sentences;
  const s = list[i], prev = list[i - 1], next = list[i + 1];
  let from = (s.play_start ?? s.start) - PLAY.padBefore;
  let to = s.end + PLAY.padAfter;
  if (prev) from = Math.max(from, prev.end);
  if (next) to = Math.min(to, next.start);
  to = Math.max(to, s.end);  // overlapping timestamps: never cut this sentence's own last word
  const offset = state.rec.offset || 0;
  return { from: Math.max(0, from + offset), to: to + offset };
}

function playLine(i, rate = 1) {
  if (!state.playerReady || !state.data) return;
  const { from, to } = lineWindow(i);
  stopWatching();
  state.player.setRate(rate);
  state.player.seekTo(from);
  state.player.play();
  state.lineFinished = false;
  // "armed" ignores stale getCurrentTime() values reported before the seek lands.
  state.watch = { from, to, armed: false };
  state.rafId = requestAnimationFrame(watchPlayback);
}

// Enter / play button: pause if playing, continue the current line if it was paused partway,
// otherwise replay it from the start.
function togglePlay() {
  if (!state.playerReady) return;
  if (state.player.isPlaying()) {
    stopWatching();
    state.player.pause();
    return;
  }
  const { from, to } = lineWindow(state.index);
  const t = state.player.getCurrentTime();
  if (state.lineFinished || t < from || t >= to) { playLine(state.index); return; }
  stopWatching();
  state.player.play();
  state.watch = { from: t, to, armed: false };
  state.rafId = requestAnimationFrame(watchPlayback);
}

function renderPlayPause(playing) {
  $("playIcon").classList.toggle("hidden", playing);
  $("pauseIcon").classList.toggle("hidden", !playing);
  renderScreenLabel();
}

// The small label on the video, e.g. "SIDE A · LINE 014 · PAUSED".
function renderScreenLabel() {
  if (!state.data) return;
  const playing = !$("pauseIcon").classList.contains("hidden");
  $("screenLabel").textContent = `SIDE A · LINE ${String(state.index + 1).padStart(3, "0")} · ${playing ? "PLAYING" : "PAUSED"}`;
}

function watchPlayback() {
  const w = state.watch;
  if (!w || !state.player) return;
  const t = state.player.getCurrentTime();
  if (!w.armed && t >= w.from - 0.05 && t < w.to) w.armed = true;
  if (w.armed && t >= w.to) {
    state.player.pause();
    state.watch = null;
    state.lineFinished = true;
    return;
  }
  state.rafId = requestAnimationFrame(watchPlayback);
}

function stopWatching() {
  cancelAnimationFrame(state.rafId);
  state.watch = null;
}

// ---- Lines ----------------------------------------------------------------------------------
function showLine(i) {
  state.index = i;
  state.lineToken++;
  state.lineFinished = false;
  state.rec.lineIndex = i;
  saveVideoRecord(state.rec);
  state.slots = buildSlots(state.data.sentences[i].text);
  $("unclearBadge").classList.toggle("hidden", !state.data.sentences[i].unclear);
  state.cursor = nextCursor();
  state.completing = false;
  state.wrongAt = -1;
  state.wrongKey = "";
  state.hintStreak = 0;
  state.wrongAttempts = 0;
  state.line = newLineCounters();
  renderProgress();
  renderLine();
  if (state.cursor < 0) completeLine();  // nothing typeable on this line
}

// Previous / Next. Stats are only recorded when a sentence is completed, so a sentence
// skipped with Next (for example one marked "unclear audio") never counts in WPM/accuracy.
function goToLine(i) {
  if (!state.data) return;
  i = Math.max(0, Math.min(state.data.sentences.length - 1, i));
  showLine(i);
  playLine(i);
}

// ---- Dictation ---------------------------------------------------------------------------
// A space slot belongs to the word before it: typing it finishes that word.
// Only one space is typed between two spoken words, so a space next to a punctuation-only
// token ("know -- you") is filled in, and the line ends on its last letter.
function buildSlots(text) {
  const slots = [];
  let word = 0, inWord = false;
  for (const ch of text.normalize("NFC")) {
    if (/\s/.test(ch)) {
      slots.push({ ch: " ", norm: " ", space: true, word: inWord ? word : word - 1, typeable: false, revealed: true, how: null });
      if (inWord) word++;
      inWord = false;
      continue;
    }
    inWord = true;
    const norm = normalizeText(ch);
    const typeable = isTypeable(norm);
    slots.push({ ch, norm, space: false, word, typeable, revealed: !typeable, how: null });
  }
  let letterSinceSpace = false;
  slots.forEach((s, i) => {
    if (!s.space) { if (s.typeable) letterSinceSpace = true; return; }
    if (letterSinceSpace && slots.slice(i + 1).some((t) => t.typeable)) {
      s.typeable = true;
      s.revealed = false;
      letterSinceSpace = false;
    }
  });
  return slots;
}

function nextCursor() {
  return state.slots.findIndex((s) => !s.revealed);
}

function currentWord() {
  return state.cursor < 0 || state.completing ? null : state.slots[state.cursor].word;
}

// Teal: finished words. Orange: the current word so far. Purple: hinted (stays purple).
// Dim dots: not typed yet.
function slotClass(s, i) {
  if (s.how === "hinted" || s.how === "autoHinted") return "hinted";
  if (s.how === "accepted") return "accepted";
  const word = currentWord();
  if (word === null) return "done";
  if (i < state.cursor) return s.word === word ? "typing" : "done";
  return s.revealed ? "pending" : "masked";
}

function renderLine() {
  const box = $("lineText");
  box.textContent = "";
  const current = currentWord();  // underlined
  let wordEl = null;
  state.slots.forEach((s, i) => {
    const el = document.createElement("span");
    el.className = "ch " + slotClass(s, i);
    // Spaces get a visible slot so the block cursor can sit on them.
    el.textContent = s.space ? "\u00a0" : (s.revealed ? s.ch : TYPING.maskChar);
    if (i === state.cursor && !state.completing) el.classList.add("cursor");
    if (i === state.wrongAt) el.classList.add("wrong");
    if (s.space) {
      box.appendChild(el);
      box.appendChild(document.createElement("wbr"));
      wordEl = null;
      return;
    }
    if (!wordEl) {
      wordEl = document.createElement("span");
      wordEl.className = s.word === current ? "word current" : "word";
      box.appendChild(wordEl);
    }
    wordEl.appendChild(el);
  });
  renderWordBox();
}

// The word box shows only the current word as typed so far, plus a wrong key in red.
function renderWordBox() {
  const out = $("currentWord");
  out.textContent = "";
  const word = currentWord();
  if (word !== null) {
    out.textContent = state.slots.filter((s, i) => s.word === word && !s.space && i < state.cursor)
      .map((s) => s.ch).join("").replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
  }
  if (state.wrongKey) {
    out.appendChild(Object.assign(document.createElement("span"), { className: "wrongKey", textContent: state.wrongKey }));
  }
  if (!out.textContent && document.activeElement !== typer) {
    out.appendChild(Object.assign(document.createElement("span"), { className: "placeholder", textContent: "click here to type" }));
  }
  // Wrong keys on the current letter so far (the letter is revealed at MAX_WRONG_ATTEMPTS).
  const tries = state.wrongAttempts;
  $("wordBox").classList.toggle("error", tries > 0);
  $("wordStatus").classList.toggle("hidden", tries === 0);
  $("wordStatus").textContent = tries ? `Wrong letter · ${tries} of ${MAX_WRONG_ATTEMPTS} tries before a hint` : "";
}

function handleTyped(ch) {
  if (state.completing || state.cursor < 0) return;
  const typed = /\s/.test(ch) ? " " : normalizeText(ch);
  if (typed !== " " && !isTypeable(typed)) return;  // punctuation keys are ignored
  state.hintStreak = 0;
  const c = state.line;
  if (c.firstKeyAt === null) c.firstKeyAt = performance.now();
  c.keys++;
  const slot = state.slots[state.cursor];
  if (typed === slot.norm) {
    c.correct++;
    slot.revealed = true;
    slot.how = "typed";
    playSound("key");
    advance();
  } else {
    markMissed(slot.word);
    state.wrongAttempts++;
    if (state.wrongAttempts >= MAX_WRONG_ATTEMPTS) {
      // Same as one Ctrl+H: revealed, not counted as correct, word already marked missed.
      slot.revealed = true;
      slot.how = "autoHinted";
      playSound("hint");
      advance();
    } else {
      playSound("error");
      flashWrong(state.cursor, typed === " " ? TYPING.wrongSpaceMark : ch);
    }
  }
}

// The word as written, without surrounding punctuation.
function wordText(wordIndex) {
  return state.slots.filter((s) => s.word === wordIndex).map((s) => s.ch).join("")
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
}

// Saved once per word per attempt at a line.
function markMissed(wordIndex) {
  const c = state.line;
  if (c.missedWords.has(wordIndex)) return;
  c.missedWords.add(wordIndex);
  const word = wordText(wordIndex);
  if (!word) return;
  const existing = state.rec.missed.find((m) => normalizeText(m.word) === normalizeText(word));
  if (existing) existing.count++;
  else state.rec.missed.push({ word, count: 1 });
  saveVideoRecord(state.rec);
  renderMissed();
}

function renderMissed() {
  const missed = state.rec.missed;
  $("missedCount").textContent = missed.length;
  const list = $("missedList");
  list.textContent = "";
  for (const m of [...missed].sort((a, b) => b.count - a.count)) {
    const li = document.createElement("li");
    li.textContent = m.word;
    if (m.count > 1) li.appendChild(Object.assign(document.createElement("span"), { textContent: "×" + m.count }));
    list.appendChild(li);
  }
}

// ---- Stats -----------------------------------------------------------------------------------
function wpm(chars, ms) { return ms > 0 ? Math.round((chars / 5) / (ms / 60000)) : null; }
function pct(a, b) { return b > 0 ? Math.round((100 * a) / b) : null; }

function recordLineStats() {
  stopPlaybackClock();
  const c = state.line;
  const ms = c.firstKeyAt === null ? 0 : Math.max(0, performance.now() - c.firstKeyAt - c.playMs);
  // WPM uses the characters finished by typing (each counted once, however often it was
  // retyped); accuracy uses every keystroke.
  const chars = state.slots.filter((s) => s.typeable && s.how === "typed").length;
  const entry = { chars, ms: Math.round(ms), correct: c.correct, keys: c.keys };
  if (state.playerRunning) {
    state.playingSince = performance.now();  // keep the clock running for whatever comes next
  }
  state.rec.stats[state.index] = entry;
  saveVideoRecord(state.rec);
  renderStats(c.keys > 0 ? entry : null);
}

// Averages over completed lines, weighted by length.
// Statistics kept from an earlier version of the transcript (archivedStats) still count.
function videoAverages(rec) {
  let chars = 0, ms = 0, correct = 0, keys = 0;
  const all = Object.values(rec.stats || {}).concat(rec.archivedStats || []);
  for (const s of all) { chars += s.chars; ms += s.ms; correct += s.correct; keys += s.keys; }
  return { wpm: wpm(chars, ms), acc: pct(correct, keys), lines: all.length };
}

function renderStats(lineEntry) {
  const fmt = (w, a) => `${w === null ? "–" : w} WPM · ${a === null ? "–" : a + "%"}`;
  $("lineStats").textContent = lineEntry ? fmt(wpm(lineEntry.chars, lineEntry.ms), pct(lineEntry.correct, lineEntry.keys)) : "–";
  const avg = videoAverages(state.rec);
  $("videoWpm").textContent = avg.wpm === null ? "–" : avg.wpm;
  $("videoAcc").textContent = avg.acc === null ? "–" : `${avg.acc}%`;
  $("videoStats").textContent = avg.lines ? `Average over ${avg.lines} line${avg.lines === 1 ? "" : "s"}` : "No finished lines yet";
}

// The split-flap counter is the current line; the bar fills with finished lines.
function renderProgress() {
  const total = state.data.sentences.length;
  const digits = String(state.index + 1).padStart(Math.max(3, String(total).length), "0");
  $("progressCount").replaceChildren(...[...digits].map((d) => Object.assign(document.createElement("span"), { textContent: d })));
  $("progressCount").setAttribute("aria-label", `Line ${state.index + 1} of ${total}`);
  $("progressTotal").textContent = `/ ${total}`;
  $("progressFill").style.width = `${(100 * finishedLines(state.rec)) / total}%`;
  renderScreenLabel();
}

function advance() {
  state.cursor = nextCursor();
  state.wrongAttempts = 0;
  // A red key belongs to the position it was typed at.
  clearTimeout(state.wrongTimer);
  state.wrongAt = -1;
  state.wrongKey = "";
  renderLine();
  if (state.cursor < 0) completeLine();
}

// The slot flashes red and the wrong key shows in red in the word box, then both clear.
function flashWrong(i, key) {
  state.wrongAt = i;
  state.wrongKey = key;
  renderLine();
  clearTimeout(state.wrongTimer);
  const token = state.lineToken;
  state.wrongTimer = setTimeout(() => {
    if (state.lineToken !== token) return;
    state.wrongAt = -1;
    state.wrongKey = "";
    renderLine();
  }, TYPING.wrongFlashMs);
}

function completeLine() {
  state.completing = true;
  state.slots.forEach((s) => { s.revealed = true; });
  state.wrongAt = -1;
  state.wrongKey = "";
  renderLine();
  recordLineStats();
  renderProgress();
  const token = state.lineToken;
  setTimeout(() => {
    if (state.lineToken !== token) return;  // I already moved elsewhere
    if (state.index + 1 < state.data.sentences.length) goToLine(state.index + 1);
    else if (!$("practiceScreen").classList.contains("hidden")) showComplete();
  }, TYPING.completePauseMs);
}

// ---- Video complete ---------------------------------------------------------------------------
// Shown when the last line is finished. "Back to the last line" returns to practice as it was.
const MISS_DOTS_MAX = 10;
function showComplete() {
  const rec = state.rec, total = state.data.sentences.length, done = finishedLines(rec);
  const avg = videoAverages(rec);
  const duration = state.player ? state.player.getDuration() : 0;
  $("completeLabel").textContent = `Side A · ${done} / ${total} lines finished`;
  $("completeMeta").textContent = videoName(rec) + (duration ? ` · ${formatTime(duration)}` : "");
  $("completeCassette").replaceChildren(cassette(videoName(rec), done / total, "done"));
  $("doneWpm").textContent = avg.wpm === null ? "–" : avg.wpm;
  $("doneAcc").textContent = avg.acc === null ? "–" : `${avg.acc}%`;
  const ms = Object.values(rec.stats || {}).concat(rec.archivedStats || []).reduce((sum, s) => sum + s.ms, 0);
  $("doneTime").textContent = formatTime(ms / 1000);
  $("doneMissed").textContent = rec.missed.length;
  const n = rec.missed.length;
  $("doneWordsSummary").textContent = n ? `${n} word${n === 1 ? "" : "s"}, most missed first` : "";
  const body = $("doneWords");
  body.textContent = "";
  for (const m of [...rec.missed].sort((a, b) => b.count - a.count)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="word"></td><td><span class="missDots" role="img"></span></td>`;
    tr.children[0].textContent = m.word;
    const dots = tr.querySelector(".missDots");
    dots.setAttribute("aria-label", `${m.count} miss${m.count === 1 ? "" : "es"}`);
    for (let i = 0; i < Math.min(m.count, MISS_DOTS_MAX); i++) dots.appendChild(document.createElement("i"));
    if (m.count > MISS_DOTS_MAX) dots.append(`×${m.count}`);
    body.appendChild(tr);
  }
  if (!n) body.innerHTML = `<tr><td colspan="2" class="muted">No missed words on this tape.</td></tr>`;
  $("practiceScreen").classList.add("hidden");
  $("completeScreen").classList.remove("hidden");
  window.scrollTo(0, 0);
}
$("doneHome").addEventListener("click", () => $("backBtn").click());
$("doneBack").addEventListener("click", () => {
  $("completeScreen").classList.add("hidden");
  $("practiceScreen").classList.remove("hidden");
  focusTyper();
});

// Unrevealed slots of the word under the cursor, optionally including its trailing space.
function remainingSlotsOfCurrentWord(withSpace) {
  const word = state.slots[state.cursor].word;
  return state.slots.filter((s) => s.word === word && !s.revealed && (withSpace || !s.space));
}

// First press reveals the next letter; a second press in a row reveals the rest of the word
// (the space after it still has to be typed).
function hint() {
  if (state.completing || state.cursor < 0) return;
  const rest = remainingSlotsOfCurrentWord(false);
  const targets = state.hintStreak >= 1 && rest.length ? rest : [state.slots[state.cursor]];
  state.hintStreak = state.hintStreak >= 1 ? 0 : 1;
  markMissed(state.slots[state.cursor].word);
  playSound("hint");
  targets.forEach((s) => { s.revealed = true; s.how = "hinted"; });
  advance();
}

// Backspace: un-type the last entered letter or space of this line, even a correct or hinted one.
// Punctuation is never entered, so it's stepped over and goes dim again with the letter before it.
function deleteBack() {
  if (state.completing || state.cursor < 0) return;
  let j = state.cursor - 1;
  while (j >= 0 && !state.slots[j].typeable) j--;
  if (j < 0) return;  // start of the line
  const slot = state.slots[j];
  slot.revealed = false;
  slot.how = null;    // a retyped hinted letter shows as typed; the missed-words record stays
  state.hintStreak = 0;
  advance();          // cursor goes back to j and the mistake counter resets
}

// For words the transcript got wrong.
function acceptWord() {
  if (state.completing || state.cursor < 0) return;
  state.hintStreak = 0;
  remainingSlotsOfCurrentWord(true).forEach((s) => { s.revealed = true; s.how = "accepted"; });
  advance();
}

const ACTIONS = {
  replay: () => playLine(state.index),
  replaySlow: () => playLine(state.index, SLOW_RATE),
  nextLine: () => goToLine(state.index + 1),
  prevLine: () => goToLine(state.index - 1),
  hint,
  acceptWord,
  playPause: togglePlay,
  deleteBack,
  toggleMute,
};

function matchShortcut(e) {
  for (const [name, s] of Object.entries(SHORTCUTS)) {
    if (e.key.toLowerCase() === s.key.toLowerCase() && e.ctrlKey === !!s.ctrl &&
        e.shiftKey === !!s.shift && !e.altKey && !e.metaKey) return name;
  }
  return null;
}

document.addEventListener("keydown", (e) => {
  if ($("practiceScreen").classList.contains("hidden") || e.isComposing) return;
  const name = matchShortcut(e);
  if (!name) return;
  e.preventDefault();
  focusTyper();
  ACTIONS[name]();
}, true);

const typer = $("typer");
function focusTyper() { typer.focus({ preventScroll: true }); }

typer.addEventListener("input", (e) => {
  if (e.isComposing) return;  // dead keys / IME: wait for compositionend
  const value = typer.value;
  typer.value = "";
  for (const ch of value) handleTyped(ch);
});
typer.addEventListener("compositionend", () => {
  const value = typer.value;
  typer.value = "";
  for (const ch of value) handleTyped(ch);
});
typer.addEventListener("focus", () => { $("wordBox").classList.add("focused"); renderWordBox(); });
typer.addEventListener("blur", () => { $("wordBox").classList.remove("focused"); renderWordBox(); });

// Clicking the text, the word box, the player or a button keeps keyboard focus on the hidden input.
$("practiceScreen").addEventListener("mousedown", (e) => {
  if (e.target.closest("button, #dictation, #wordBox, #playerShield")) {
    e.preventDefault();
    focusTyper();
  }
});

$("prevBtn").addEventListener("click", ACTIONS.prevLine);
$("nextBtn").addEventListener("click", ACTIONS.nextLine);
$("replayBtn").addEventListener("click", ACTIONS.replay);
$("slowBtn").addEventListener("click", ACTIONS.replaySlow);
$("hintBtn").addEventListener("click", ACTIONS.hint);
$("playPauseBtn").addEventListener("click", togglePlay);
$("playerShield").addEventListener("click", ACTIONS.replay);

// Fullscreen covers the whole page, so the sentence and word box stay visible.
$("fullscreenBtn").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
});

// ---- Video time ----------------------------------------------------------------------------
function formatTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
setInterval(() => {
  if (!state.playerReady || !state.player || $("practiceScreen").classList.contains("hidden")) return;
  $("timeText").textContent = `${formatTime(state.player.getCurrentTime())} / ${formatTime(state.player.getDuration())}`;
}, 250);

// ---- Timing offset ------------------------------------------------------------------------
function renderOffset() {
  const v = state.rec.offset || 0;
  $("offsetValue").textContent = `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)} s`;
}
function changeOffset(delta) {
  const v = Math.round(((state.rec.offset || 0) + delta) * 10) / 10;
  state.rec.offset = Math.max(PLAY.offsetMin, Math.min(PLAY.offsetMax, v));
  saveVideoRecord(state.rec);
  renderOffset();
}
$("offsetDown").addEventListener("click", () => changeOffset(-PLAY.offsetStep));
$("offsetUp").addEventListener("click", () => changeOffset(PLAY.offsetStep));

// ---- Typing sounds ---------------------------------------------------------------------------
// Files are decoded once into AudioBuffers; each event plays a new AudioBufferSourceNode,
// so fast keystrokes overlap instead of cutting each other off.
const SOUNDS = {
  key:   { url: "/static/sounds/key.mp3", rateJitter: 0.05 },  // playbackRate 0.95–1.05
  error: { url: "/static/sounds/error.mp3" },
  hint:  { url: "/static/sounds/hint.wav" },
};
const sound = Object.assign({ muted: false, volume: 50 }, loadJSON("sound", {}));
let audioCtx = null, masterGain = null;
const soundBuffers = {};

// Decoding doesn't need a user gesture when done in an OfflineAudioContext, and the
// resulting AudioBuffers can be played by the real AudioContext later.
(function loadSounds() {
  const decoder = new OfflineAudioContext(2, 1, 44100);
  for (const [name, { url }] of Object.entries(SOUNDS)) {
    fetch(url)
      .then((res) => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.arrayBuffer(); })
      .then((data) => decoder.decodeAudioData(data))
      .then((buffer) => { soundBuffers[name] = buffer; })
      .catch((err) => console.warn(`Typing sound "${name}" (${url}) failed to load, skipping it:`, err.message));
  }
})();

// Created on the first keystroke, since browsers block audio before a user gesture.
function getAudio() {
  if (!audioCtx) {
    audioCtx = new AudioContext({ latencyHint: "interactive" });
    masterGain = audioCtx.createGain();
    masterGain.connect(audioCtx.destination);
    applyVolume();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playSound(name) {
  if (sound.muted || sound.volume === 0) return;
  const ctx = getAudio();
  const buffer = soundBuffers[name];
  if (!buffer) return;  // failed to load (already warned) or still loading
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const jitter = SOUNDS[name].rateJitter || 0;
  src.playbackRate.value = 1 + (Math.random() * 2 - 1) * jitter;
  src.connect(masterGain);
  src.start();
}

function applyVolume() {
  if (masterGain) masterGain.gain.value = sound.volume / 100;
}

function renderSound() {
  $("soundOnIcon").classList.toggle("hidden", sound.muted);
  $("soundOffIcon").classList.toggle("hidden", !sound.muted);
  $("muteBtn").setAttribute("aria-pressed", String(sound.muted));
  $("muteBtn").title = `Typing sounds ${sound.muted ? "off" : "on"} (Ctrl+M)`;
  $("volume").value = sound.volume;
}

function toggleMute() {
  sound.muted = !sound.muted;
  saveJSON("sound", sound);
  renderSound();
}

$("muteBtn").addEventListener("mousedown", (e) => e.preventDefault());  // keep typing focus
$("muteBtn").addEventListener("click", toggleMute);
$("volume").addEventListener("input", () => {
  sound.volume = Number($("volume").value);
  saveJSON("sound", sound);
  applyVolume();
});
$("volume").addEventListener("change", focusTyper);  // hand the keyboard back after dragging
renderSound();

// ---- Theme -----------------------------------------------------------------------------------
// Follows the system setting until the button is used; the choice is then saved.
const systemDark = matchMedia("(prefers-color-scheme: dark)");
function currentTheme() { return document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light"); }
function renderThemeButton() {
  const dark = currentTheme() === "dark";
  $("sunIcon").classList.toggle("hidden", !dark);
  $("moonIcon").classList.toggle("hidden", dark);
}
$("themeBtn").addEventListener("mousedown", (e) => e.preventDefault());  // keep typing focus
$("themeBtn").addEventListener("click", () => {
  const theme = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  saveJSON("theme", theme);
  renderThemeButton();
});
systemDark.addEventListener("change", renderThemeButton);
renderThemeButton();
