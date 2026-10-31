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
  leadIn: 0.2,        // start playing this many seconds before the line
  tail: 0.15,         // and stop this many seconds after it
  offsetMin: -0.5,
  offsetMax: 0.5,
  offsetStep: 0.1,
};
const TYPING = {
  maskChar: "•",
  completePauseMs: 600,   // show the finished line this long before moving on
  wrongFlashMs: 400,      // a wrong key shows in red this long
  wrongSpaceMark: "␣",    // how a wrongly typed space appears in the word box
};
const STORE_PREFIX = "dictation:";

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
  loadVideo($("urlInput").value.trim(), $("langInput").value.trim() || "en");
});

async function loadVideo(url, lang) {
  $("loadError").textContent = "";
  if (!url) return;
  let res, body;
  try {
    res = await fetch(`/api/transcript?url=${encodeURIComponent(url)}&lang=${encodeURIComponent(lang)}`);
    body = await res.json();
  } catch (err) {
    $("loadError").textContent = "Could not reach the local server. Is `python app.py` running?";
    return;
  }
  if (!res.ok) {
    let msg = body.message || "Something went wrong.";
    if (body.available && body.available.length) {
      msg += "\nAvailable: " + body.available.map((a) => `${a.code} (${a.name}, ${a.source})`).join(", ");
    }
    $("loadError").textContent = msg;
    return;
  }
  if (!body.lines.length) { $("loadError").textContent = "The captions for this video are empty."; return; }

  state.data = body;
  state.rec = videoRecord(body.videoId);
  state.rec.lineCount = body.lines.length;
  state.rec.lang = lang;
  state.rec.lastOpened = Date.now();
  saveVideoRecord(state.rec);
  state.index = Math.min(state.rec.lineIndex, body.lines.length - 1);

  $("loadScreen").classList.add("hidden");
  $("practiceScreen").classList.remove("hidden");
  $("videoTitle").textContent = state.rec.title || body.videoId;
  renderOffset();
  renderStats(null);
  renderMissed();
  createPlayer(body.videoId);
  showLine(state.index);
  focusTyper();
}

function renderRecent() {
  const recs = allVideoRecords();
  $("recent").classList.toggle("hidden", recs.length === 0);
  const list = $("recentList");
  list.textContent = "";
  for (const rec of recs) {
    const li = document.createElement("li");
    const avg = videoAverages(rec);
    li.innerHTML = `<span class="title"></span><span class="muted"></span><span class="muted"></span>`;
    li.children[0].textContent = rec.title || rec.id;
    li.children[1].textContent = `line ${rec.lineIndex + 1} of ${rec.lineCount}`;
    li.children[2].textContent = avg.acc === null ? "no stats yet" : `${avg.acc}% accuracy`;
    li.addEventListener("click", () => {
      $("urlInput").value = `https://www.youtube.com/watch?v=${rec.id}`;
      $("langInput").value = rec.lang || "en";
      loadVideo(rec.id, rec.lang || "en");
    });
    list.appendChild(li);
  }
}
renderRecent();

$("backBtn").addEventListener("click", () => {
  stopWatching();
  if (state.player) { state.player.destroy(); state.player = null; state.playerReady = false; }
  renderPlayPause(false);
  $("player").replaceWith(Object.assign(document.createElement("div"), { id: "player" }));
  $("playerError").classList.add("hidden");
  $("practiceScreen").classList.add("hidden");
  $("loadScreen").classList.remove("hidden");
  renderRecent();
});

// ---- YouTube player ---------------------------------------------------------------------
let ytApiReady = new Promise((resolve) => { window.onYouTubeIframeAPIReady = resolve; });
(function loadYouTubeApi() {
  const s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(s);
})();

async function createPlayer(videoId) {
  await ytApiReady;
  state.playerReady = false;
  state.player = new YT.Player("player", {
    videoId,
    playerVars: {
      controls: 0, disablekb: 1, rel: 0, playsinline: 1, iv_load_policy: 3,
      cc_load_policy: 0, origin: location.origin,
    },
    events: {
      onReady: () => {
        state.playerReady = true;
        hideYouTubeCaptions();
        playLine(state.index);
        const title = state.player.getVideoData().title;
        if (title && state.rec) {
          state.rec.title = title;
          saveVideoRecord(state.rec);
          $("videoTitle").textContent = title;
        }
      },
      onStateChange: (e) => {
        renderPlayPause(isPlayingState(e.data));
        if (e.data === YT.PlayerState.PLAYING) {
          hideYouTubeCaptions();
          if (state.playingSince === null) state.playingSince = performance.now();
        } else {
          stopPlaybackClock();
        }
      },
      onError: (e) => {
        const messages = {
          2: "Invalid video ID.",
          5: "This video can't be played in an HTML5 player.",
          100: "This video was removed or is private.",
          101: "The owner doesn't allow this video to be embedded, so it can't be played here.",
          150: "The owner doesn't allow this video to be embedded, so it can't be played here.",
        };
        $("playerError").textContent = (messages[e.data] || `Player error ${e.data}.`) +
          (e.data === 101 || e.data === 150 ? "\nTry another video." : "");
        $("playerError").classList.remove("hidden");
      },
    },
  });
}

// Playback time after the first keystroke is excluded from the line's typing time.
function stopPlaybackClock() {
  if (state.playingSince === null) return;
  const now = performance.now();
  const c = state.line;
  if (c && c.firstKeyAt !== null) c.playMs += now - Math.max(state.playingSince, c.firstKeyAt);
  state.playingSince = null;
}

// YouTube's own subtitles would give the answer away.
function hideYouTubeCaptions() {
  try { state.player.unloadModule("captions"); state.player.unloadModule("cc"); } catch {}
}

function lineWindow(i) {
  const line = state.data.lines[i];
  const offset = state.rec.offset || 0;
  return { from: Math.max(0, line.start + offset - PLAY.leadIn), to: line.start + line.duration + offset + PLAY.tail };
}

function playLine(i, rate = 1) {
  if (!state.playerReady || !state.data) return;
  const { from, to } = lineWindow(i);
  stopWatching();
  state.player.setPlaybackRate(rate);
  state.player.seekTo(from, true);
  state.player.playVideo();
  state.lineFinished = false;
  // "armed" ignores stale getCurrentTime() values reported before the seek lands.
  state.watch = { from, to, armed: false };
  state.rafId = requestAnimationFrame(watchPlayback);
}

// Buffering right after a seek counts as playing: it's about to start.
function isPlayingState(s) { return s === YT.PlayerState.PLAYING || s === YT.PlayerState.BUFFERING; }

// Enter / play button: pause if playing, continue the current line if it was paused partway,
// otherwise replay it from the start.
function togglePlay() {
  if (!state.playerReady) return;
  if (isPlayingState(state.player.getPlayerState())) {
    stopWatching();
    state.player.pauseVideo();
    return;
  }
  const { from, to } = lineWindow(state.index);
  const t = state.player.getCurrentTime();
  if (state.lineFinished || t < from || t >= to) { playLine(state.index); return; }
  stopWatching();
  state.player.playVideo();
  state.watch = { from: t, to, armed: false };
  state.rafId = requestAnimationFrame(watchPlayback);
}

function renderPlayPause(playing) {
  $("playIcon").classList.toggle("hidden", playing);
  $("pauseIcon").classList.toggle("hidden", !playing);
}

function watchPlayback() {
  const w = state.watch;
  if (!w || !state.player) return;
  const t = state.player.getCurrentTime();
  if (!w.armed && t >= w.from - 0.05 && t < w.to) w.armed = true;
  if (w.armed && t >= w.to) {
    state.player.pauseVideo();
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
  state.slots = buildSlots(state.data.lines[i].text);
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

function goToLine(i) {
  if (!state.data) return;
  i = Math.max(0, Math.min(state.data.lines.length - 1, i));
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
    if (!wordEl) { wordEl = document.createElement("span"); wordEl.className = "word"; box.appendChild(wordEl); }
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
  if (state.player && state.player.getPlayerState && state.player.getPlayerState() === YT.PlayerState.PLAYING) {
    state.playingSince = performance.now();  // keep the clock running for whatever comes next
  }
  state.rec.stats[state.index] = entry;
  saveVideoRecord(state.rec);
  renderStats(c.keys > 0 ? entry : null);
}

// Averages over completed lines, weighted by length.
function videoAverages(rec) {
  let chars = 0, ms = 0, correct = 0, keys = 0;
  for (const s of Object.values(rec.stats || {})) { chars += s.chars; ms += s.ms; correct += s.correct; keys += s.keys; }
  return { wpm: wpm(chars, ms), acc: pct(correct, keys), lines: Object.keys(rec.stats || {}).length };
}

function renderStats(lineEntry) {
  const fmt = (w, a) => `${w === null ? "–" : w} WPM · ${a === null ? "–" : a + "%"}`;
  $("lineStats").textContent = lineEntry ? fmt(wpm(lineEntry.chars, lineEntry.ms), pct(lineEntry.correct, lineEntry.keys)) : "–";
  const avg = videoAverages(state.rec);
  $("videoStats").textContent = avg.lines ? `${fmt(avg.wpm, avg.acc)} over ${avg.lines} lines` : "–";
}

// "N/M" is the current line; the bar fills with finished lines.
function renderProgress() {
  const total = state.data.lines.length;
  $("progressCount").textContent = `${state.index + 1}/${total}`;
  const done = Object.keys(state.rec.stats).filter((k) => Number(k) < total).length;
  $("progressFill").style.width = `${(100 * done) / total}%`;
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
    if (state.index + 1 < state.data.lines.length) goToLine(state.index + 1);
  }, TYPING.completePauseMs);
}

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

// For words the auto-captions got wrong.
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
  $("offsetValue").textContent = (v > 0 ? "+" : "") + v.toFixed(1) + "s";
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
  $("muteBtn").textContent = sound.muted ? "🔇" : "🔊";
  $("volume").value = sound.volume;
}

function toggleMute() {
  sound.muted = !sound.muted;
  saveJSON("sound", sound);
  renderSound();
}

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
function renderThemeButton() { $("themeBtn").textContent = currentTheme() === "dark" ? "☀️" : "🌙"; }
$("themeBtn").addEventListener("mousedown", (e) => e.preventDefault());  // keep typing focus
$("themeBtn").addEventListener("click", () => {
  const theme = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  saveJSON("theme", theme);
  renderThemeButton();
});
systemDark.addEventListener("change", renderThemeButton);
renderThemeButton();
