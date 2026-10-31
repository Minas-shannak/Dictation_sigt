"""Local server for the dictation app.

Serves index.html and the API: YouTube videos and uploaded videos are both
transcribed from their actual audio (yt-dlp or the uploaded file, then
faster-whisper) in background jobs, and uploaded videos are stored and served.
Run with `python app.py`.
"""

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_file, send_from_directory

import config
from ai.transcriber import current_device
from services import migration, uploads
from services.audio_extractor import convert_for_browser, extracted_audio, remove_stale_temp_files
from services.jobs import Job, JobManager
from services.transcript_cache import Source, delete_cached, get_transcript, is_valid_video_id, youtube_source
from services.youtube_audio import downloaded_audio, extract_video_id

PORT = 8000
BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES  # larger uploads are refused before saving

jobs = JobManager()


@app.before_request
def _canonical_host():
    """Send http://localhost:<port>/... to http://127.0.0.1:<port>/... (same path and query).

    Browsers keep saved progress per address, so using both would split it. 307 keeps the
    method and body. A host without a port (Flask's test client) is left alone.
    """
    host, _, port = request.host.partition(":")
    if host.lower() == "localhost" and port:
        query = request.query_string.decode("latin-1")
        return redirect(f"http://{config.CANONICAL_HOST}:{port}{request.path}" + (f"?{query}" if query else ""), code=307)


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/config")
def api_config():
    """Playback settings the page needs (all tunable values live in config.py)."""
    return jsonify({"pad_before": config.PAD_BEFORE_SECONDS, "pad_after": config.PAD_AFTER_SECONDS,
                    "data_format_version": config.DATA_FORMAT_VERSION})


# --- One-time clean start (services/migration.py) ------------------------------------------

@app.get("/api/migration")
def api_migration():
    return jsonify(migration.plan())


@app.post("/api/migration/browser-backup")
def api_browser_backup():
    """The page sends its old localStorage data here before clearing it."""
    body = request.get_json(silent=True) or {}
    try:
        path = migration.save_browser_backup(body.get("items"), str(body.get("origin", "")))
    except (ValueError, TypeError):
        return _error("bad_backup", "The backup data was not in the expected format.", 400)
    return jsonify({"path": path})


@app.get("/api/migration/browser-backup/<name>")
def api_get_browser_backup(name):
    """A saved localStorage backup, for restoring it (see README)."""
    path = migration.browser_backup_path(name)
    if path is None:
        return _error("not_found", "No such backup file.", 404)
    return send_file(path, mimetype="application/json")


# --- Videos and jobs -------------------------------------------------------------------

def _error(code: str, message: str, status: int):
    return jsonify({"error": code, "message": message}), status


@app.errorhandler(uploads.UploadError)
def _upload_error(e):
    return _error(e.code, e.user_message, e.status)


@app.errorhandler(413)
def _too_large(e):
    limit = config.MAX_UPLOAD_BYTES / 1024 ** 3
    return _error("too_large", f"The file is larger than the {limit:g} GB limit.", 413)


def _transcribe(job: Job, source: Source, audio, language):
    """Run get_transcript for a job; `audio` is the extract_audio callable (a context manager)."""
    def on_stage(stage):
        job.set_stage(stage)
        if stage == "transcribing":
            job.device, job.compute_type = current_device()

    transcript = get_transcript(source, audio, language, on_progress=job.set_progress, on_stage=on_stage)
    if job.device is None:  # served from the cache: report how it was made
        job.device, job.compute_type, job.from_cache = transcript["device"], transcript["compute_type"], True
    return transcript


def start_transcription(entry: dict, language: str) -> Job:
    """Queue (or join) the transcription of a stored video."""
    source = Source("local", entry["sha256"], entry["original_filename"])
    language = language or None

    def work(job: Job):
        path = uploads.media_path(source.video_id)  # at run time: a conversion may have replaced the file

        @contextmanager
        def audio():
            job.set_stage("extracting_audio")
            with extracted_audio(str(path), check=job.check) as wav:
                yield wav

        return _transcribe(job, source, audio, language)

    return jobs.submit("transcribe", ("transcribe", source.video_id, language), work)


def start_youtube_transcription(youtube_id: str, language: str) -> Job:
    """Queue (or join) transcribing a YouTube video from its audio (never its captions)."""
    source = youtube_source(youtube_id)
    language = language or None

    def work(job: Job):
        @contextmanager
        def audio():
            # Only runs when the transcript isn't cached: download, then extract 16 kHz audio.
            job.set_stage("downloading_audio")
            with downloaded_audio(youtube_id, check=job.check, on_progress=job.set_progress,
                                  on_info=lambda info: setattr(job, "title", info.get("title"))) as path:
                job.set_stage("extracting_audio")
                with extracted_audio(path, check=job.check) as wav:
                    yield wav

        return _transcribe(job, source, audio, language)

    return jobs.submit("transcribe", ("transcribe", source.video_id, language), work)


@app.post("/api/youtube")
def api_youtube():
    body = request.get_json(silent=True) or {}
    youtube_id = extract_video_id(body.get("url", ""))
    if not youtube_id:
        return _error("invalid_link", "That doesn't look like a YouTube video link.", 400)
    job = start_youtube_transcription(youtube_id, (body.get("language") or "").strip())
    video = {"video_id": f"yt:{youtube_id}", "youtube_id": youtube_id, "browser_playable": True}
    return jsonify({"video": video, "job": jobs.status(job.id)})


def start_conversion(video_id: str) -> Job:
    """Queue (or join) converting a stored video to H.264 + AAC MP4."""
    def work(job: Job):
        src = uploads.media_path(video_id)
        dest = src.with_name(f"{uploads.get_entry(video_id)['sha256']}.converted.mp4")
        job.set_stage("converting")
        convert_for_browser(str(src), str(dest), check=job.check, on_progress=job.set_progress)
        return uploads.public(uploads.replace_with_converted(video_id, dest))

    return jobs.submit("convert", ("convert", video_id), work)


@app.post("/api/uploads")
def api_upload():
    file = request.files.get("file")
    if file is None:
        return _error("no_file", "No file was uploaded.", 400)
    entry = uploads.save_upload(file.stream, file.filename)
    job = start_transcription(entry, request.form.get("language", "en").strip())
    return jsonify({"video": uploads.public(entry), "job": jobs.status(job.id)})


@app.post("/api/local/<video_id>/open")
def api_open_local(video_id):
    """Open a stored video again (Recent videos): resets its retention clock and loads its transcript."""
    entry = uploads.mark_opened(video_id)
    language = ((request.get_json(silent=True) or {}).get("language") or "en").strip()
    job = start_transcription(entry, language)
    return jsonify({"video": uploads.public(entry), "job": jobs.status(job.id)})


@app.post("/api/local/<video_id>/convert")
def api_convert(video_id):
    uploads.media_path(video_id)  # 404/410 before queueing anything
    job = start_conversion(video_id)
    return jsonify({"job": jobs.status(job.id)})


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    status = jobs.status(job_id)
    if status is None:
        return _error("job_not_found", "This job is no longer known (the server may have restarted). Start it again.", 404)
    return jsonify(status)


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel_job(job_id):
    if jobs.get(job_id) is None:
        return _error("job_not_found", "This job is no longer known.", 404)
    jobs.cancel(job_id)
    return jsonify(jobs.status(job_id))


@app.delete("/api/videos/<video_id>")
def api_delete_video(video_id):
    """Delete everything the app stores for a video: cached transcripts (all variants) and,
    for an upload, the stored file. Jobs for it are cancelled first."""
    if not is_valid_video_id(video_id):
        return _error("invalid_id", "That is not a video ID this app uses.", 400)
    cancelled = jobs.cancel_where(lambda key: isinstance(key, tuple) and len(key) > 1 and key[1] == video_id)
    if not jobs.wait_finished(cancelled, timeout=60):
        return _error("busy", "A job for this video is still stopping. Try again in a moment.", 409)
    removed = {"cancelled_jobs": cancelled, "cache": delete_cached(video_id), "files": []}
    if video_id.startswith("local:"):
        removed["files"] = uploads.delete(video_id)
    return jsonify({"deleted": video_id, **removed})


@app.get("/media/<video_id>")
def media(video_id):
    # conditional=True answers HTTP Range requests, which the <video> element needs for seeking.
    return send_file(uploads.media_path(video_id), conditional=True)


def _retention_loop():
    """Once a day while running (the startup cleanup happens before the server starts)."""
    while True:
        threading.Event().wait(config.RETENTION_CHECK_SECONDS)
        try:
            uploads.cleanup()
            remove_stale_temp_files()
        except Exception:
            logging.getLogger(__name__).exception("Upload cleanup failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    migration.run()  # once: back up and clear the caption-based version's cache
    uploads.cleanup(startup=True)
    remove_stale_temp_files()
    threading.Thread(target=_retention_loop, name="upload-retention", daemon=True).start()
    logging.getLogger(__name__).info("Open http://%s:%d in your browser (localhost:%d redirects there)",
                                     config.CANONICAL_HOST, PORT, PORT)
    app.run(host=config.CANONICAL_HOST, port=PORT, debug=False, threaded=True)
