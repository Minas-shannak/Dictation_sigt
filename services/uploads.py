"""Uploaded videos: validated storage under content-hash names, an index, and retention.

A video is stored as uploads/<sha256><ext>, never under the user's file name, so
names can't escape the folder or collide. Its identity is the SHA-256 of the
uploaded bytes ("local:<sha256>"), computed while saving; uploading the same file
again finds the existing entry and its cached transcript.

uploads/index.json records each video's original name, codecs and last_opened.
Videos not opened for UPLOAD_RETENTION_DAYS are deleted (their small transcripts
stay in the cache); the index keeps them as "expired" so Recent videos can say so.
"""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional

from werkzeug.utils import secure_filename

import config
from services.audio_extractor import AudioError, probe
from services.transcript_cache import write_json_atomic

log = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
_lock = threading.RLock()


class UploadError(Exception):
    """An upload or stored-video problem with a message that is safe to show to the user."""

    def __init__(self, code: str, user_message: str, status: int = 400):
        super().__init__(user_message)
        self.code, self.user_message, self.status = code, user_message, status


def _dir() -> Path:
    return Path(config.UPLOAD_DIR)


def _index_path() -> Path:
    return _dir() / "index.json"


def _load_index() -> Dict[str, Dict]:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        log.error("uploads/index.json is unreadable (%s); starting a new index", e)
        return {}


def _save_index(index: Dict[str, Dict]) -> None:
    write_json_atomic(_index_path(), index)


def public(entry: Dict) -> Dict:
    """The fields the browser needs."""
    keys = ("video_id", "original_filename", "duration", "video_codec", "audio_codec", "browser_playable",
            "converted", "expired", "last_opened")
    return {k: entry.get(k) for k in keys}


def save_upload(stream: BinaryIO, filename: str) -> Dict:
    """Store an uploaded video after checking its extension and its actual content.

    The bytes are hashed while they are written, so large files are read once.
    Raises UploadError with a user-facing message if the file is rejected.
    """
    original = secure_filename(filename or "") or "video"
    ext = Path(original).suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise UploadError("unsupported_type", "Only MP4, MOV and WebM videos can be uploaded.")
    _dir().mkdir(parents=True, exist_ok=True)
    tmp = _dir() / f"incoming-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:  # Flask checks Content-Length first; this covers the rest
                    raise UploadError("too_large", f"The file is larger than the {config.MAX_UPLOAD_BYTES // 1024 ** 3} GB limit.", 413)
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadError("empty", "The uploaded file is empty.")
        try:
            info = probe(str(tmp))
        except AudioError as e:
            raise UploadError(e.code, e.user_message)
        if info.format_name not in config.ALLOWED_CONTAINERS:
            log.warning("Rejected upload %r: container %s", original, info.format_name)
            raise UploadError("unsupported_type", "Only MP4, MOV and WebM videos can be uploaded.")
        if info.video_codec is None:
            raise UploadError("no_video", "This file has no video track. Upload a video file.")
        sha = digest.hexdigest()
        with _lock:
            index = _load_index()
            entry = index.get(sha)
            if entry and not entry.get("expired") and (_dir() / entry["stored_name"]).exists():
                log.info("Upload %r is already stored as %s", original, entry["stored_name"])
            else:
                stored = f"{sha}{ext}"
                os.replace(tmp, _dir() / stored)
                entry = {"video_id": f"local:{sha}", "sha256": sha, "stored_name": stored, "size": size,
                         "duration": info.duration, "video_codec": info.video_codec, "audio_codec": info.audio_codec,
                         "browser_playable": info.browser_playable, "converted": False, "expired": False,
                         "uploaded_at": time.time()}
                log.info("Stored upload %r as %s (%d bytes, %s/%s)", original, stored, size,
                         info.video_codec, info.audio_codec)
            entry["original_filename"] = original
            entry["last_opened"] = time.time()
            index[sha] = entry
            _save_index(index)
            return dict(entry)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def _sha(video_id: str) -> str:
    prefix, _, sha = video_id.partition(":")
    if prefix != "local" or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise UploadError("not_found", "This video was not found.", 404)
    return sha


def get_entry(video_id: str) -> Dict:
    entry = _load_index().get(_sha(video_id))
    if entry is None:
        raise UploadError("not_found", "This video was not found. Upload it again.", 404)
    return dict(entry)


def media_path(video_id: str) -> Path:
    """Path of a stored video; UploadError if unknown or expired."""
    entry = get_entry(video_id)
    path = _dir() / (entry.get("stored_name") or "")
    if entry.get("expired") or not entry.get("stored_name") or not path.is_file():
        raise UploadError("expired", "This video file has expired (not opened for "
                          f"{config.UPLOAD_RETENTION_DAYS} days). Upload it again.", 410)
    return path


def mark_opened(video_id: str) -> Dict:
    """Record that the video was opened (resets its retention clock)."""
    media_path(video_id)
    with _lock:
        index = _load_index()
        entry = index[_sha(video_id)]
        entry["last_opened"] = time.time()
        _save_index(index)
        return dict(entry)


def replace_with_converted(video_id: str, converted: Path) -> Dict:
    """Make a converted H.264 MP4 the stored video, deleting the original (no duplicate kept).

    The video_id stays the hash of the original upload, so cached transcripts still match.
    """
    with _lock:
        index = _load_index()
        sha = _sha(video_id)
        entry = index[sha]
        old = _dir() / entry["stored_name"]
        new_name = f"{sha}.mp4"
        os.replace(converted, _dir() / new_name)
        if old.name != new_name:
            old.unlink(missing_ok=True)
        info = probe(str(_dir() / new_name))
        entry.update(stored_name=new_name, video_codec=info.video_codec, audio_codec=info.audio_codec,
                     browser_playable=info.browser_playable, converted=True, duration=info.duration,
                     size=(_dir() / new_name).stat().st_size)
        _save_index(index)
        log.info("Replaced %s with converted %s", old.name, new_name)
        return dict(entry)


def cleanup(now: Optional[float] = None, startup: bool = False) -> List[str]:
    """Delete videos not opened for UPLOAD_RETENTION_DAYS; return their video_ids.

    Also removes leftovers of interrupted uploads and conversions: all of them at
    startup (nothing can be in progress then), otherwise those older than a day.
    """
    now = time.time() if now is None else now
    limit = now - config.UPLOAD_RETENTION_DAYS * 24 * 60 * 60
    removed = []
    with _lock:
        if not _dir().exists():
            return removed
        index = _load_index()
        for sha, entry in index.items():
            if entry.get("expired") or entry.get("last_opened", now) >= limit:
                continue
            path = _dir() / entry["stored_name"]
            path.unlink(missing_ok=True)
            entry.update(expired=True, stored_name=None)
            removed.append(entry["video_id"])
            log.info("Deleted %s (%s): not opened for %d days; its transcript stays cached",
                     path.name, entry.get("original_filename"), config.UPLOAD_RETENTION_DAYS)
        if removed:
            _save_index(index)
        for leftover in list(_dir().glob("incoming-*.part")) + list(_dir().glob("*.converting.mp4")):
            if startup or leftover.stat().st_mtime < now - 24 * 60 * 60:
                leftover.unlink(missing_ok=True)
                log.info("Removed leftover %s", leftover.name)
    return removed
