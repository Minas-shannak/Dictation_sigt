"""YouTube audio for transcription, with yt-dlp (audio-only stream, no captions).

Isolated so it can be disabled or replaced without touching anything else.
Only what YouTube serves publicly is downloaded: no cookies or login, and nothing
that gets around age gates, private or members-only videos, or DRM. The audio
file lives in a temporary folder only while it is needed, then is deleted.

yt-dlp needs a JavaScript runtime for YouTube's challenge: the pip-installed
Deno next to the venv's Python (see requirements.txt). yt-dlp breaks when
YouTube changes; the error message then suggests updating it.
"""

import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional
from urllib.parse import parse_qs, urlparse

import yt_dlp

log = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
UPDATE_HINT = "Try updating yt-dlp (see the README), or upload the video file instead."

# yt-dlp error text (lowercase) -> (code, message for the user). First match wins.
ERRORS = [
    (("private video",), "private", "This video is private."),
    (("confirm your age", "age-restricted", "inappropriate for some users"), "age_restricted",
     "This video is age-restricted, so its audio can't be downloaded here."),
    (("members-only", "members only", "join this channel"), "members_only", "This video is for channel members only."),
    (("drm",), "drm", "This video is DRM-protected, so its audio can't be downloaded."),
    (("not a bot", "http error 429", "too many requests"), "blocked",
     "YouTube is refusing downloads from this computer right now. Wait a while, or upload the video file instead."),
    (("video unavailable", "this video is unavailable", "this video is not available", "has been removed", "does not exist",
      "no longer available", "not made this video available in your country"), "unavailable",
     "This video is unavailable (removed, private, or blocked in your country). " + UPDATE_HINT),
]


class YouTubeError(Exception):
    """A YouTube problem with a message that is safe to show to the user."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code, self.user_message = code, user_message


def extract_video_id(url: str) -> Optional[str]:
    """The 11-character video ID from a YouTube link (or a bare ID), or None."""
    url = (url or "").strip()
    if VIDEO_ID_RE.match(url):
        return url
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in parsed.path.split("/") if p]

    candidate = None
    if host == "youtu.be" and parts:
        candidate = parts[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if parts[:1] == ["watch"]:
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate = parts[1]
    if candidate and VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def friendly_error(err: BaseException) -> YouTubeError:
    text = str(err).lower()
    for needles, code, message in ERRORS:
        if any(n in text for n in needles):
            return YouTubeError(code, message)
    return YouTubeError("youtube_failed", "YouTube audio is unavailable right now. " + UPDATE_HINT)


def deno_path() -> Optional[str]:
    """The Deno binary installed in the venv (pip package `deno`), else one on the PATH."""
    venv_deno = Path(sys.executable).with_name("deno")
    return str(venv_deno) if venv_deno.exists() else shutil.which("deno")


class _Logger:
    """Sends yt-dlp's output to logging instead of the console."""

    def debug(self, msg):
        log.debug(msg)

    def info(self, msg):
        log.debug(msg)

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)

    def error(self, msg):
        log.error("yt-dlp: %s", msg)


class _Stopped(BaseException):
    """Raised inside yt-dlp's progress hook to end an abandoned download thread."""


@contextmanager
def downloaded_audio(video_id: str, check: Optional[Callable[[], None]] = None,
                     on_progress: Optional[Callable[[float], None]] = None,
                     on_info: Optional[Callable[[Dict], None]] = None) -> Iterator[str]:
    """Yield the path of the video's audio-only stream, downloaded to a temporary folder.

    check() is called about four times a second and may raise to stop the download.
    yt-dlp runs in a background thread, because YouTube can hold a connection for many
    seconds without any progress (and so without any chance to check); a stopped
    download is abandoned at once and its thread ends at its next progress update.
    on_progress(fraction) reports the downloaded share; on_info(info) receives yt-dlp's
    metadata (title, duration) before downloading. The folder is deleted afterwards,
    also on failure or cancellation. Raises YouTubeError with a user-facing message.
    """
    if not VIDEO_ID_RE.match(video_id or ""):
        raise YouTubeError("invalid_link", "That doesn't look like a YouTube video link.")
    folder = tempfile.mkdtemp(prefix="dictation-youtube-")
    stop = threading.Event()
    shared: Dict = {"progress": None, "info": None, "error": None}

    def hook(d):
        if stop.is_set():
            raise _Stopped()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                shared["progress"] = min(1.0, d.get("downloaded_bytes", 0) / total)

    opts = {
        "format": "bestaudio/best",          # audio only when YouTube offers it (it always has)
        "noplaylist": True,
        "outtmpl": os.path.join(folder, "audio.%(ext)s"),
        "writesubtitles": False,
        "writeautomaticsub": False,
        "quiet": True,
        "noprogress": True,
        "logger": _Logger(),
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 3,
    }
    deno = deno_path()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    else:
        log.warning("No Deno found for yt-dlp's YouTube challenge; some videos may fail. "
                    "Install it in the venv: uv pip install -r requirements.txt")
    url = f"https://www.youtube.com/watch?v={video_id}"
    info_ready, go_on = threading.Event(), threading.Event()

    def work():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                shared["info"] = ydl.extract_info(url, download=False)
                info_ready.set()
                go_on.wait()  # the caller checks the info (live stream?) first
                if stop.is_set():
                    return
                ydl.process_ie_result(shared["info"], download=True)
        except BaseException as e:  # reported to the caller (or ignored after a stop)
            shared["error"] = e
        finally:
            info_ready.set()

    thread = threading.Thread(target=work, name=f"yt-dlp-{video_id}", daemon=True)
    try:
        thread.start()
        while not info_ready.wait(0.25):
            if check:
                check()
        info = shared["info"]
        if info is not None:
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise YouTubeError("live", "Live streams can't be transcribed. Try again after the stream has ended.")
            if on_info:
                on_info(info)
            log.info("Downloading audio of %s (%r, %ss, format %s)", video_id, info.get("title"),
                     info.get("duration"), info.get("format"))
        go_on.set()
        while thread.is_alive():
            thread.join(0.25)
            if check:
                check()
            if on_progress and shared["progress"] is not None:
                on_progress(shared["progress"])
        error = shared["error"]
        if isinstance(error, yt_dlp.utils.DownloadError):
            log.error("yt-dlp failed for %s: %s", video_id, error)
            raise friendly_error(error) from error
        if error is not None:
            raise error
        files = [f for f in os.listdir(folder) if not f.endswith((".part", ".ytdl"))]
        if not files:
            log.error("yt-dlp finished for %s but left no audio file in %s", video_id, folder)
            raise YouTubeError("youtube_failed", "YouTube audio is unavailable right now. " + UPDATE_HINT)
        path = os.path.join(folder, files[0])
        if on_progress:
            on_progress(1.0)
        log.info("Downloaded audio of %s: %s (%d bytes)", video_id, files[0], os.path.getsize(path))
        yield path
    finally:
        stop.set()      # an abandoned download ends at its next progress update
        go_on.set()
        shutil.rmtree(folder, ignore_errors=True)
