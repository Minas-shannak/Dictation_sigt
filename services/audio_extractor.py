"""Probe media files with ffprobe, extract 16 kHz mono WAV audio and convert videos with FFmpeg.

The extracted WAV is a temporary file that exists only inside the
`extracted_audio()` context and is deleted afterwards, even on failure or
cancellation.

Long FFmpeg runs take an optional `check()` callback, called about twice a
second; it stops the run by raising (the job system uses this to cancel).
"""

import json
import logging
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

import config

log = logging.getLogger(__name__)

Check = Callable[[], None]
# Temporary files and folders the app creates in the system temp folder.
TEMP_PREFIXES = ("dictation-audio-", "dictation-youtube-")


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


def remove_stale_temp_files(max_age_seconds: float = 6 * 60 * 60) -> int:
    """Delete the app's temp files left by a server that was killed mid-job.

    Normally `finally` blocks delete them; a hard kill skips those. Only items older
    than max_age_seconds are touched, so a running job's files are never removed.
    """
    removed = 0
    folder = tempfile.gettempdir()
    limit = time.time() - max_age_seconds
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not name.startswith(TEMP_PREFIXES):
            continue
        try:
            if os.path.getmtime(path) >= limit:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
            log.info("Removed stale temp item %s", path)
        except FileNotFoundError:
            pass
    return removed


def run_ffmpeg(args: list, timeout: float, check: Optional[Check] = None,
               on_time: Optional[Callable[[float], None]] = None) -> Tuple[int, str]:
    """Run ffmpeg with `args`; returns (exit code, stderr).

    check() is called about twice a second and may raise to stop the run (the process
    is killed). on_time(seconds) receives the output position, for progress.
    Raises subprocess.TimeoutExpired after `timeout` seconds.
    """
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if on_time:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += args
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if on_time else subprocess.DEVNULL, stderr=err)
        deadline = time.monotonic() + timeout
        try:
            selector = None
            if on_time:
                selector = selectors.DefaultSelector()
                selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                if check:
                    check()
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if selector is None:
                    try:
                        proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if not selector.select(timeout=0.5):
                    continue
                line = proc.stdout.readline()
                if not line:  # ffmpeg closed its output: it is finishing
                    proc.wait()
                    break
                key, _, value = line.decode(errors="replace").strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    on_time(int(value) / 1e6)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            if proc.stdout:
                proc.stdout.close()
        err.seek(0)
        return proc.returncode, err.read().decode(errors="replace").strip()


@contextmanager
def extracted_audio(path: str, check: Optional[Check] = None) -> Iterator[str]:
    """Yield the path of a temporary 16 kHz mono WAV of the file's audio, deleted on exit."""
    probe(path)
    fd, wav_path = tempfile.mkstemp(prefix="dictation-audio-", suffix=".wav")
    os.close(fd)
    try:
        args = ["-i", str(path), "-vn", "-ac", "1", "-ar", str(config.AUDIO_SAMPLE_RATE), "-c:a", "pcm_s16le", wav_path]
        try:
            code, stderr = run_ffmpeg(args, config.FFMPEG_TIMEOUT_SECONDS, check)
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out after %ss extracting audio from %s", config.FFMPEG_TIMEOUT_SECONDS, path)
            raise AudioError("ffmpeg_timeout", "Extracting the audio took too long and was stopped.")
        if code != 0 or Path(wav_path).stat().st_size == 0:
            log.error("ffmpeg failed extracting audio from %s (exit %s): %s", path, code, stderr)
            raise AudioError("ffmpeg_failed", "Could not extract the audio from this video.")
        log.info("Extracted audio from %s to %s (%d bytes)", path, wav_path, Path(wav_path).stat().st_size)
        yield wav_path
    finally:
        try:
            os.remove(wav_path)
        except FileNotFoundError:
            pass


def convert_for_browser(src: str, dest: str, check: Optional[Check] = None,
                        on_progress: Optional[Callable[[float], None]] = None) -> None:
    """Transcode a video to H.264 + AAC MP4 (plays in every browser) at `dest`.

    Writes to a temporary file next to `dest` and renames it only after the result
    probes as a playable H.264 video of the same length; on any failure or
    cancellation the temporary file is removed and `dest` is untouched.
    """
    source = probe(src)
    tmp = f"{dest}.converting.mp4"
    args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", config.CONVERT_X264_PRESET, "-crf", str(config.CONVERT_X264_CRF),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", config.CONVERT_AUDIO_BITRATE,
            "-movflags", "+faststart", tmp]

    def on_time(seconds: float) -> None:
        if on_progress and source.duration > 0:
            on_progress(min(1.0, seconds / source.duration))

    try:
        try:
            code, stderr = run_ffmpeg(args, config.FFMPEG_CONVERT_TIMEOUT_SECONDS, check, on_time)
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out after %ss converting %s", config.FFMPEG_CONVERT_TIMEOUT_SECONDS, src)
            raise AudioError("ffmpeg_timeout", "Converting the video took too long and was stopped.")
        if code != 0:
            log.error("ffmpeg failed converting %s (exit %s): %s", src, code, stderr)
            raise AudioError("convert_failed", "Could not convert this video.")
        result = probe(tmp)
        if result.video_codec != "h264" or abs(result.duration - source.duration) > 2.0:
            log.error("Converted file %s is not as expected: %s (source %.1fs)", tmp, result, source.duration)
            raise AudioError("convert_failed", "Could not convert this video.")
        os.replace(tmp, dest)
        log.info("Converted %s to %s (H.264 + AAC, %.1fs)", src, dest, result.duration)
        if on_progress:
            on_progress(1.0)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
