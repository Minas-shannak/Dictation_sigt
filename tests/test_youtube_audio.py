"""services.youtube_audio and the YouTube job route, without network access (yt-dlp is faked)."""

import os
import time
from pathlib import Path

import pytest
import yt_dlp

import app as app_module
import config
import services.youtube_audio as ya
from services.youtube_audio import YouTubeError, downloaded_audio, extract_video_id, friendly_error


# --- Video IDs ---------------------------------------------------------------------------

def test_extract_video_id_formats():
    vid = "dQw4w9WgXcQ"
    for url in [
        f"https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/watch?v={vid}&t=42s",
        f"https://youtube.com/watch?feature=share&v={vid}",
        f"https://www.youtube.com/watch?v={vid}&list=PLgzTt0k8mXzGjD1gQXkqC3bE0GzYwBnSD",
        f"https://youtu.be/{vid}?t=10",
        f"https://www.youtube.com/shorts/{vid}",
        f"https://www.youtube.com/embed/{vid}?start=5",
        f"https://m.youtube.com/watch?v={vid}",
        f"youtube.com/watch?v={vid}",
        vid,
    ]:
        assert extract_video_id(url) == vid, url
    assert extract_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert extract_video_id("not a link") is None
    assert extract_video_id("https://www.youtube.com/watch?v=short") is None


# --- Errors ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text, code", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", "age_restricted"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "members_only"),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot", "blocked"),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", "blocked"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "unavailable"),
    ("ERROR: [youtube] abc: This video is not available", "unavailable"),
    ("ERROR: [youtube] aaaaaaaaaaa: This video is unavailable", "unavailable"),  # yt-dlp 2026.10.26 wording
    ("ERROR: [youtube] abc: Requested format is not available", "youtube_failed"),
    ("ERROR: something entirely new went wrong", "youtube_failed"),
])
def test_yt_dlp_errors_become_friendly_messages(text, code):
    err = friendly_error(yt_dlp.utils.DownloadError(text))
    assert err.code == code
    assert "ERROR" not in err.user_message and "[youtube]" not in err.user_message
    if code in ("youtube_failed", "unavailable"):
        assert "updating yt-dlp" in err.user_message  # the README explains how


# --- Download (fake yt-dlp) ---------------------------------------------------------------------

class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: writes a file, calling the progress hooks like the real one."""
    behaviour = "ok"
    info = {"title": "Me at the zoo", "duration": 19, "live_status": "not_live", "format": "251 - audio only"}
    seen_opts = None

    def __init__(self, opts):
        FakeYDL.seen_opts = opts
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if FakeYDL.behaviour == "unavailable":
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Video unavailable")
        if FakeYDL.behaviour == "live":
            return dict(FakeYDL.info, live_status="is_live")
        return dict(FakeYDL.info)

    def process_ie_result(self, info, download=True):
        if FakeYDL.behaviour == "stall":  # YouTube holding the connection: no progress at all
            time.sleep(5)
        path = self.opts["outtmpl"].replace("%(ext)s", "webm")
        with open(path + ".part", "wb") as f:
            for i in range(1, 11):
                f.write(b"x" * 1000)
                time.sleep(0.03)
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "downloading", "downloaded_bytes": i * 1000, "total_bytes": 10_000})
        os.replace(path + ".part", path)


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.behaviour = "ok"
    monkeypatch.setattr(ya.yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_download_yields_audio_and_deletes_the_temp_folder(fake_ydl):
    progress, infos = [], []
    with downloaded_audio("jNQXAC9IVRw", on_progress=progress.append, on_info=infos.append) as path:
        assert os.path.getsize(path) == 10_000
        folder = Path(path).parent
    assert not folder.exists()
    assert progress[-1] == 1.0 and progress == sorted(progress)
    assert infos[0]["title"] == "Me at the zoo"
    opts = fake_ydl.seen_opts
    assert opts["format"] == "bestaudio/best" and opts["noplaylist"] is True
    assert opts["writesubtitles"] is False and opts["writeautomaticsub"] is False  # never captions
    assert "cookiefile" not in opts and "cookiesfrombrowser" not in opts            # no login
    assert opts["js_runtimes"]["deno"]["path"].endswith("deno")


class Stop(BaseException):
    pass


def test_cancelled_download_removes_the_temp_folder(fake_ydl):
    calls = []

    def check():
        calls.append(1)
        if len(calls) == 2:
            raise Stop()
    folders_before = set(os.listdir(os.environ.get("TMPDIR", "/tmp")))
    with pytest.raises(Stop):
        with downloaded_audio("jNQXAC9IVRw", check=check):
            pass
    leftover = [f for f in set(os.listdir(os.environ.get("TMPDIR", "/tmp"))) - folders_before if f.startswith("dictation-youtube-")]
    assert leftover == []


@pytest.mark.parametrize("behaviour, code", [("unavailable", "unavailable"), ("live", "live")])
def test_download_errors(fake_ydl, behaviour, code):
    fake_ydl.behaviour = behaviour
    with pytest.raises(YouTubeError) as exc:
        with downloaded_audio("jNQXAC9IVRw"):
            pass
    assert exc.value.code == code


def test_invalid_id_is_rejected_before_any_download(fake_ydl):
    fake_ydl.seen_opts = None
    with pytest.raises(YouTubeError) as exc:
        with downloaded_audio("../../etc"):
            pass
    assert exc.value.code == "invalid_link" and fake_ydl.seen_opts is None


# --- The /api/youtube route ------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, fake_ydl):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    jobs = app_module.JobManager()
    monkeypatch.setattr(app_module, "jobs", jobs)
    seen = []

    def fake_get_transcript(source, extract_audio, language=None, processing_settings=None, on_progress=None, on_stage=None):
        seen.append((source.video_id, language))
        with extract_audio() as wav:  # fake download, then real FFmpeg on the (invalid) bytes
            pass
        return {"video_id": source.video_id, "device": "cuda", "compute_type": "float16", "sentences": []}

    monkeypatch.setattr(app_module, "get_transcript", fake_get_transcript)
    c = app_module.app.test_client()
    c.seen = seen
    yield c
    jobs.wait_idle()


def wait(client, job_id):
    for _ in range(200):
        st = client.get(f"/api/jobs/{job_id}").get_json()
        if st["state"] in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.05)
    raise TimeoutError


def test_youtube_route_validates_the_link(client):
    r = client.post("/api/youtube", json={"url": "https://example.com/watch?v=dQw4w9WgXcQ"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_link"


def test_youtube_route_runs_a_job_with_download_stage_and_title(client):
    r = client.post("/api/youtube", json={"url": "https://youtu.be/jNQXAC9IVRw", "language": "en"})
    body = r.get_json()
    assert body["video"] == {"video_id": "yt:jNQXAC9IVRw", "youtube_id": "jNQXAC9IVRw", "browser_playable": True}
    st = wait(client, body["job"]["id"])
    # The fake download is not real audio, so FFmpeg rejects it with a friendly message.
    assert st["state"] == "failed" and st["error"]["code"] == "unreadable"
    assert st["title"] == "Me at the zoo" and st["stage"] == "extracting_audio"
    assert client.seen == [("yt:jNQXAC9IVRw", "en")]


def test_youtube_failure_shows_the_friendly_message(client, fake_ydl):
    fake_ydl.behaviour = "unavailable"
    st = wait(client, client.post("/api/youtube", json={"url": "jNQXAC9IVRw"}).get_json()["job"]["id"])
    assert st["state"] == "failed" and st["error"]["code"] == "unavailable"


def test_app_code_never_imports_the_caption_library():
    root = Path(__file__).resolve().parents[1]
    for path in [root / "app.py", root / "config.py", *(root / "ai").glob("*.py"), *(root / "services").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "youtube_transcript_api" not in text and "YouTubeTranscriptApi" not in text, path


def test_cancel_is_quick_even_when_youtube_sends_nothing(fake_ydl):
    # Real case: YouTube held the connection 14 s without data, so yt-dlp's hook never ran.
    fake_ydl.behaviour = "stall"
    t0 = time.monotonic()

    def check():
        if time.monotonic() - t0 > 0.3:
            raise Stop()
    with pytest.raises(Stop):
        with downloaded_audio("jNQXAC9IVRw", check=check):
            pass
    assert time.monotonic() - t0 < 1.0
