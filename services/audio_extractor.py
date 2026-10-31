"""Probe media files with ffprobe and extract 16 kHz mono WAV audio with FFmpeg.

The extracted WAV is a temporary file that exists only inside the
`extracted_audio()` context and is deleted afterwards, even on failure.
"""

import json
import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import config

log = logging.getLogger(__name__)


class AudioError(Exception):
    """A media problem with a message that is safe to show to the user."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code, self.user_message = code, user_message


@dataclass
class ProbeResult:
    duration: float
    format_name: str
    audio_codec: Optional[str]
    video_codec: Optional[str]

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def browser_playable(self) -> bool:
        """True if Chrome can play the video stream (audio-only files count as playable)."""
        return self.video_codec is None or self.video_codec in config.BROWSER_PLAYABLE_VIDEO_CODECS


def probe(path: str) -> ProbeResult:
    """Inspect a media file. Raises AudioError if it is unreadable, empty or has no audio."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=config.FFPROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.error("ffprobe timed out on %s", path)
        raise AudioError("unreadable", "This file could not be read (inspecting it took too long).")
    if out.returncode != 0:
        log.warning("ffprobe failed on %s: %s", path, out.stderr.strip())
        raise AudioError("unreadable", "This file is corrupted or not a supported video format.")

    info = json.loads(out.stdout or "{}")
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    # Cover art in MP3/M4A shows up as a one-frame video stream; it isn't a video.
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(fmt.get("duration") or (audio or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0

    result = ProbeResult(
        duration=duration,
        format_name=fmt.get("format_name", ""),
        audio_codec=audio.get("codec_name") if audio else None,
        video_codec=video.get("codec_name") if video else None,
    )
    log.info("Probed %s: format=%s duration=%.1fs audio=%s video=%s",
             path, result.format_name, result.duration, result.audio_codec, result.video_codec)
    if not result.has_audio:
        raise AudioError("no_audio", "This video has no audio.")
    if result.duration <= 0:
        raise AudioError("empty", "This file is empty or has zero length.")
    return result


@contextmanager
def extracted_audio(path: str) -> Iterator[str]:
    """Yield the path of a temporary 16 kHz mono WAV of the file's audio, deleted on exit."""
    probe(path)
    fd, wav_path = tempfile.mkstemp(prefix="dictation-audio-", suffix=".wav")
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
               "-vn", "-ac", "1", "-ar", str(config.AUDIO_SAMPLE_RATE), "-c:a", "pcm_s16le", wav_path]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=config.FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out after %ss extracting audio from %s", config.FFMPEG_TIMEOUT_SECONDS, path)
            raise AudioError("ffmpeg_timeout", "Extracting the audio took too long and was stopped.")
        if out.returncode != 0 or Path(wav_path).stat().st_size == 0:
            log.error("ffmpeg failed extracting audio from %s (exit %s): %s", path, out.returncode, out.stderr.strip())
            raise AudioError("ffmpeg_failed", "Could not extract the audio from this video.")
        log.info("Extracted audio from %s to %s (%d bytes)", path, wav_path, Path(wav_path).stat().st_size)
        yield wav_path
    finally:
        try:
            os.remove(wav_path)
        except FileNotFoundError:
            pass
