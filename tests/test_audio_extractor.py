"""services.audio_extractor against small media files generated with FFmpeg."""

import os
import shutil
import subprocess

import pytest

from services.audio_extractor import AudioError, extracted_audio, probe

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed")

TONE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2"]
VIDEO = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2"]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")

    def make(name, *args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(d / name)], check=True)
        return str(d / name)

    files = {
        "mp4": make("v.mp4", *VIDEO, *TONE, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        "webm": make("v.webm", *VIDEO, *TONE, "-shortest", "-c:v", "libvpx-vp9", "-c:a", "libopus"),
        "mov": make("v.mov", *VIDEO, *TONE, "-shortest", "-c:v", "mpeg4", "-c:a", "aac"),
        "noaudio": make("silent.mp4", *VIDEO, "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    }
    (d / "corrupt.mp4").write_bytes(os.urandom(50_000))
    (d / "empty.mp4").write_bytes(b"")
    files["corrupt"], files["empty"] = str(d / "corrupt.mp4"), str(d / "empty.mp4")
    return files


@pytest.mark.parametrize("kind, video_codec, playable", [("mp4", "h264", True), ("webm", "vp9", True), ("mov", "mpeg4", False)])
def test_probe_reads_containers_and_playability(media, kind, video_codec, playable):
    p = probe(media[kind])
    assert p.has_audio and p.duration == pytest.approx(2.0, abs=0.1)
    assert p.video_codec == video_codec
    assert p.browser_playable is playable


@pytest.mark.parametrize("kind, code", [("noaudio", "no_audio"), ("corrupt", "unreadable"), ("empty", "unreadable")])
def test_probe_rejects_bad_files_with_friendly_errors(media, kind, code):
    with pytest.raises(AudioError) as exc:
        probe(media[kind])
    assert exc.value.code == code
    assert "Traceback" not in exc.value.user_message


@pytest.mark.parametrize("kind", ["mp4", "webm", "mov"])
def test_extracts_16khz_mono_wav_and_deletes_it(media, kind):
    with extracted_audio(media[kind]) as wav:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels",
                              "-of", "csv=p=0", wav], capture_output=True, text=True, check=True).stdout.strip()
        assert out == "pcm_s16le,16000,1"
        path = wav
    assert not os.path.exists(path)


def test_temp_wav_is_deleted_when_the_caller_fails(media):
    with pytest.raises(RuntimeError):
        with extracted_audio(media["mp4"]) as wav:
            path = wav
            raise RuntimeError("transcription failed")
    assert not os.path.exists(path)


def test_ffmpeg_failure_is_reported_and_cleaned_up(media, monkeypatch):
    real_run = subprocess.run
    made = []

    def failing_ffmpeg(cmd, **kw):
        if cmd[0] == "ffmpeg":
            made.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 1, "", "simulated decoder error")
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", failing_ffmpeg)
    with pytest.raises(AudioError) as exc:
        with extracted_audio(media["mp4"]):
            pass
    assert exc.value.code == "ffmpeg_failed"
    assert made and not os.path.exists(made[0])


def test_ffmpeg_timeout_is_reported_and_cleaned_up(media, monkeypatch):
    real_run = subprocess.run
    made = []

    def slow_ffmpeg(cmd, **kw):
        if cmd[0] == "ffmpeg":
            made.append(cmd[-1])
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", slow_ffmpeg)
    with pytest.raises(AudioError) as exc:
        with extracted_audio(media["mp4"]):
            pass
    assert exc.value.code == "ffmpeg_timeout"
    assert made and not os.path.exists(made[0])
