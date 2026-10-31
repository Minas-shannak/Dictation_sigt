"""Deleting a video: DELETE /api/videos/<id> (cache variants, uploaded file, jobs, ID validation)."""

import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

import app as app_module
import config
from services import transcript_cache, uploads

YT = "yt:jNQXAC9IVRw"
OTHER = "yt:arj7oStGLkU"
VIDEO = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2"]
TONE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "ASR_CACHE_DIR", tmp_path / "cache" / "asr")
    monkeypatch.setattr(config, "TRANSCRIPT_CACHE_DIR", tmp_path / "cache" / "transcripts")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    jobs = app_module.JobManager()
    monkeypatch.setattr(app_module, "jobs", jobs)
    for folder in ("asr", "transcripts"):
        (tmp_path / "cache" / folder).mkdir(parents=True)
    # Two model/settings variants of the video, plus another video's entries.
    for vid in (YT, OTHER):
        stem = vid.replace(":", "-")
        for key in ("aaaa1111", "bbbb2222"):
            (tmp_path / "cache" / "asr" / f"{stem}__{key}.json").write_text("{}")
            (tmp_path / "cache" / "transcripts" / f"{stem}__{key}.json").write_text("{}")
    # Things that must never be touched.
    (tmp_path / "cache" / "jNQXAC9IVRw.json").write_text("{}")                  # an old caption file
    (tmp_path / "backup" / "pre-audio-pipeline-x" / "cache").mkdir(parents=True)
    (tmp_path / "backup" / "pre-audio-pipeline-x" / "cache" / "yt-jNQXAC9IVRw__aaaa1111.json").write_text("{}")
    c = app_module.app.test_client()
    c.root, c.jobs = tmp_path, jobs
    yield c
    jobs.wait_idle()


def names(folder):
    return sorted(p.name for p in Path(folder).iterdir()) if Path(folder).exists() else []


def test_youtube_video_deletes_every_cached_variant_and_nothing_else(env):
    r = env.delete(f"/api/videos/{YT}")
    assert r.status_code == 200
    body = r.get_json()
    assert sorted(body["cache"]) == ["asr/yt-jNQXAC9IVRw__aaaa1111.json", "asr/yt-jNQXAC9IVRw__bbbb2222.json",
                                     "transcripts/yt-jNQXAC9IVRw__aaaa1111.json", "transcripts/yt-jNQXAC9IVRw__bbbb2222.json"]
    assert body["files"] == [] and body["cancelled_jobs"] == []
    assert names(env.root / "cache" / "asr") == ["yt-arj7oStGLkU__aaaa1111.json", "yt-arj7oStGLkU__bbbb2222.json"]
    assert names(env.root / "cache" / "transcripts") == ["yt-arj7oStGLkU__aaaa1111.json", "yt-arj7oStGLkU__bbbb2222.json"]
    assert (env.root / "cache" / "jNQXAC9IVRw.json").exists()
    assert names(env.root / "backup" / "pre-audio-pipeline-x" / "cache") == ["yt-jNQXAC9IVRw__aaaa1111.json"]
    # Deleting again is harmless.
    assert env.delete(f"/api/videos/{YT}").get_json()["cache"] == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_uploaded_video_deletes_file_index_entry_and_transcripts(env, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *VIDEO, *TONE, "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    entry = uploads.save_upload(io.BytesIO(src.read_bytes()), "talk.mp4")
    vid, stem = entry["video_id"], entry["video_id"].replace(":", "-")
    (env.root / "cache" / "asr" / f"{stem}__k1.json").write_text("{}")
    (env.root / "cache" / "transcripts" / f"{stem}__k2.json").write_text("{}")
    (env.root / "uploads" / f"{entry['sha256']}.converted.mp4.converting.mp4").write_text("half")  # leftover
    r = env.delete(f"/api/videos/{vid}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["files"] == sorted([f"{entry['sha256']}.converted.mp4.converting.mp4", f"{entry['sha256']}.mp4"])
    assert sorted(body["cache"]) == [f"asr/{stem}__k1.json", f"transcripts/{stem}__k2.json"]
    assert names(env.root / "uploads") == ["index.json"]
    assert json.loads((env.root / "uploads" / "index.json").read_text()) == {}
    assert env.get(f"/media/{vid}").status_code == 404


def test_a_running_job_is_cancelled_before_anything_is_deleted(env):
    started, order = threading.Event(), []

    def work(job):
        started.set()
        try:
            for _ in range(500):
                job.check()
                time.sleep(0.01)
        finally:
            # The cache files still exist while the job winds down.
            order.append(("job stopped", (env.root / "cache" / "asr" / "yt-jNQXAC9IVRw__aaaa1111.json").exists()))
    job = env.jobs.submit("transcribe", ("transcribe", YT, "en"), work)
    other = env.jobs.submit("transcribe", ("transcribe", OTHER, "en"), lambda j: "other video")
    started.wait(2)
    r = env.delete(f"/api/videos/{YT}")
    assert r.status_code == 200 and r.get_json()["cancelled_jobs"] == [job.id]
    assert env.jobs.status(job.id)["state"] == "cancelled"
    assert order == [("job stopped", True)]  # it stopped first; deletion came after
    assert env.jobs.wait(other.id)["state"] == "done"  # other videos' jobs are left alone


@pytest.mark.parametrize("bad", [
    "jNQXAC9IVRw",                        # bare ID (no prefix)
    "yt:short",
    "yt:jNQXAC9IVRw/../../x",
    "local:" + "0" * 63,
    "local:" + "Z" * 64,
    "local:..%2F..%2Fetc",
    "cache:anything",
    "yt:jNQXAC9IVRw__aaaa1111",
    "..",
])
def test_invalid_ids_and_path_tricks_are_rejected(env, bad):
    r = env.delete(f"/api/videos/{bad}")
    assert r.status_code in (400, 404, 405)
    # Nothing was deleted anywhere.
    assert len(names(env.root / "cache" / "asr")) == 4 and len(names(env.root / "cache" / "transcripts")) == 4


def test_delete_helpers_refuse_invalid_ids():
    with pytest.raises(ValueError):
        transcript_cache.delete_cached("yt:../../etc")
    with pytest.raises(uploads.UploadError):
        uploads.delete("local:../../etc/passwd")
