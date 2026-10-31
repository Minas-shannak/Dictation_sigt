"""Upload, job, conversion and media endpoints (Flask test client, generated media, fake transcription)."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import app as app_module
import config
from services import uploads

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")

TONE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2"]
VIDEO = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2"]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("upload-media")

    def make(name, *args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(d / name)], check=True)
        return (d / name).read_bytes()

    return {
        "mp4": make("v.mp4", *VIDEO, *TONE, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"),
        "webm": make("v.webm", *VIDEO, *TONE, "-shortest", "-c:v", "libvpx-vp9", "-c:a", "libopus"),
        "hevc": make("h.mov", *VIDEO, *TONE, "-shortest", "-c:v", "libx265", "-tag:v", "hvc1", "-c:a", "aac"),
        "noaudio": make("n.mp4", *VIDEO, "-c:v", "libx264", "-pix_fmt", "yuv420p"),
        "audio_only": make("a.mp4", *TONE, "-c:a", "aac"),
        "corrupt": os.urandom(40_000),
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "jobs", app_module.JobManager())
    calls = []

    def fake_get_transcript(source, extract_audio, language=None, processing_settings=None, on_progress=None, on_stage=None):
        on_stage("extracting_audio")
        with extract_audio() as wav:  # real FFmpeg extraction from the stored upload
            assert os.path.getsize(wav) > 0
        on_stage("transcribing")
        on_progress(1.0)
        calls.append((source, language))
        return {"video_id": source.video_id, "original_filename": source.original_filename, "device": "cuda",
                "compute_type": "float16", "sentences": [{"index": 0, "text": "Hello.", "start": 0.0, "end": 1.0, "words": []}]}

    monkeypatch.setattr(app_module, "get_transcript", fake_get_transcript)
    c = app_module.app.test_client()
    c.calls, c.tmp = calls, tmp_path
    return c


def upload(client, data, name, language="en"):
    return client.post("/api/uploads", data={"file": (io.BytesIO(data), name), "language": language},
                       content_type="multipart/form-data")


def wait_job(client, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").get_json()
        if status["state"] in ("done", "failed", "cancelled"):
            return status
        time.sleep(0.05)
    raise TimeoutError


def stored_files(client):
    d = Path(config.UPLOAD_DIR)
    return sorted(p.name for p in d.iterdir() if p.name != "index.json") if d.exists() else []


# --- Upload and transcription job ---------------------------------------------------------

def test_upload_stores_by_content_hash_and_transcribes(client, media):
    r = upload(client, media["mp4"], "My Talk.mp4")
    assert r.status_code == 200
    body = r.get_json()
    sha = hashlib.sha256(media["mp4"]).hexdigest()
    assert body["video"]["video_id"] == f"local:{sha}"
    assert body["video"]["original_filename"] == "My_Talk.mp4" and body["video"]["browser_playable"] is True
    status = wait_job(client, body["job"]["id"])
    assert status["state"] == "done" and status["result"]["sentences"][0]["text"] == "Hello."
    assert status["device"] == "cuda" and status["from_cache"] is True  # no model was loaded in this test
    assert stored_files(client) == [f"{sha}.mp4"]
    assert client.calls[0][0].video_id == f"local:{sha}" and client.calls[0][1] == "en"


def test_same_file_under_another_name_is_stored_once(client, media):
    a = upload(client, media["webm"], "first.webm").get_json()
    wait_job(client, a["job"]["id"])
    b = upload(client, media["webm"], "renamed copy.webm").get_json()
    assert a["video"]["video_id"] == b["video"]["video_id"]
    assert b["video"]["original_filename"] == "renamed_copy.webm"
    assert len(stored_files(client)) == 1


def test_file_names_cannot_escape_the_upload_folder(client, media):
    r = upload(client, media["mp4"], "../../../../etc/evil.mp4")
    assert r.status_code == 200
    sha = hashlib.sha256(media["mp4"]).hexdigest()
    assert stored_files(client) == [f"{sha}.mp4"]
    assert not (client.tmp / "etc").exists() and not (client.tmp.parent / "evil.mp4").exists()


@pytest.mark.parametrize("kind, name, status, code", [
    ("mp4", "notes.txt", 400, "unsupported_type"),          # extension
    ("corrupt", "broken.mp4", 400, "unreadable"),           # content, not the extension, decides
    ("noaudio", "silent.mp4", 400, "no_audio"),
    ("audio_only", "song.mp4", 400, "no_video"),
])
def test_rejected_uploads_leave_nothing_behind(client, media, kind, name, status, code):
    r = upload(client, media[kind], name)
    assert r.status_code == status and r.get_json()["error"] == code
    assert "Traceback" not in r.get_json()["message"]
    assert stored_files(client) == []


def test_too_large_is_refused_before_saving_with_the_limit(client, media, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "MAX_CONTENT_LENGTH", 10_000)
    r = upload(client, media["mp4"], "big.mp4")
    assert r.status_code == 413
    assert r.get_json() == {"error": "too_large", "message": "The file is larger than the 2 GB limit."}
    assert stored_files(client) == []


def test_missing_file_field(client):
    r = client.post("/api/uploads", data={"language": "en"}, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"] == "no_file"


# --- Media, conversion, jobs ------------------------------------------------------------------

def test_media_supports_range_requests(client, media):
    vid = upload(client, media["mp4"], "v.mp4").get_json()["video"]["video_id"]
    full = client.get(f"/media/{vid}")
    assert full.status_code == 200 and full.data == media["mp4"]
    part = client.get(f"/media/{vid}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.data == media["mp4"][:100]


@pytest.mark.parametrize("bad", ["local:xyz", "yt:abc", "local:" + "0" * 64, "local:../../etc/passwd"])
def test_unknown_media_is_404(client, bad):
    assert client.get(f"/media/{bad}").status_code == 404


def test_hevc_upload_is_flagged_and_convert_replaces_it_keeping_the_id(client, media):
    body = upload(client, media["hevc"], "phone.mov").get_json()
    vid = body["video"]["video_id"]
    assert body["video"]["browser_playable"] is False and body["video"]["video_codec"] == "hevc"
    wait_job(client, body["job"]["id"])
    r = client.post(f"/api/local/{vid}/convert")
    status = wait_job(client, r.get_json()["job"]["id"], timeout=60)
    assert status["state"] == "done", status
    assert status["result"]["video_id"] == vid  # same id: the cached transcript still matches
    assert (status["result"]["video_codec"], status["result"]["browser_playable"], status["result"]["converted"]) == ("h264", True, True)
    sha = vid.split(":")[1]
    assert stored_files(client) == [f"{sha}.mp4"]  # the .mov is gone, no duplicate kept
    served = client.get(f"/media/{vid}")
    assert served.status_code == 200 and served.data[4:8] == b"ftyp"


def test_job_endpoints(client, media):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    body = upload(client, media["mp4"], "v.mp4").get_json()
    done = wait_job(client, body["job"]["id"])
    r = client.post(f"/api/jobs/{body['job']['id']}/cancel")
    assert r.status_code == 200 and r.get_json()["state"] == done["state"] == "done"


def test_cancelling_a_conversion_leaves_the_original(client, media, monkeypatch):
    body = upload(client, media["hevc"], "phone.mov").get_json()
    wait_job(client, body["job"]["id"])
    vid = body["video"]["video_id"]
    real_convert = app_module.convert_for_browser

    def slow_convert(src, dest, check=None, on_progress=None):
        for _ in range(200):
            check()
            time.sleep(0.02)
        real_convert(src, dest, check, on_progress)
    monkeypatch.setattr(app_module, "convert_for_browser", slow_convert)
    job_id = client.post(f"/api/local/{vid}/convert").get_json()["job"]["id"]
    time.sleep(0.2)
    client.post(f"/api/jobs/{job_id}/cancel")
    assert wait_job(client, job_id)["state"] == "cancelled"
    sha = vid.split(":")[1]
    assert stored_files(client) == [f"{sha}.mov"]
    assert uploads.get_entry(vid)["converted"] is False


# --- Opening again and retention -----------------------------------------------------------------

def test_open_resets_the_retention_clock_and_expired_files_say_so(client, media):
    body = upload(client, media["mp4"], "v.mp4").get_json()
    vid = body["video"]["video_id"]
    wait_job(client, body["job"]["id"])
    cache_file = Path(config.CACHE_DIR) / "transcripts" / "keep.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{}")

    # 29 days later it is kept; opening it resets the clock.
    assert uploads.cleanup(now=time.time() + 29 * 86400) == []
    r = client.post(f"/api/local/{vid}/open", json={"language": "en"})
    assert r.status_code == 200 and wait_job(client, r.get_json()["job"]["id"])["state"] == "done"

    # 31 days without opening: the video file goes, the transcript cache stays.
    assert uploads.cleanup(now=time.time() + 31 * 86400) == [vid]
    assert stored_files(client) == [] and cache_file.exists()
    r = client.post(f"/api/local/{vid}/open", json={})
    assert r.status_code == 410 and r.get_json()["error"] == "expired"
    assert "Upload it again" in r.get_json()["message"]
    assert client.get(f"/media/{vid}").status_code == 410
    # Uploading the same file again revives it.
    again = upload(client, media["mp4"], "v.mp4").get_json()
    assert again["video"]["video_id"] == vid and again["video"]["expired"] is False
    assert client.get(f"/media/{vid}").status_code == 200


def test_startup_cleanup_removes_interrupted_uploads(client):
    d = Path(config.UPLOAD_DIR)
    d.mkdir(parents=True)
    (d / "incoming-abc.part").write_bytes(b"half")
    (d / "x.converted.mp4.converting.mp4").write_bytes(b"half")
    uploads.cleanup()  # recent leftovers are kept while running (an upload may be in progress)
    assert len(list(d.iterdir())) == 2
    uploads.cleanup(startup=True)
    assert list(d.iterdir()) == []
