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

from flask import Flask, jsonify, request, send_file, send_from_directory

import config
from ai.transcriber import current_device
from services import uploads
from services.audio_extractor import convert_for_browser, extracted_audio, remove_stale_temp_files
from services.jobs import Job, JobManager
from services.transcript_cache import Source, get_transcript, youtube_source
from services.youtube_audio import downloaded_audio, extract_video_id

PORT = 8000
BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES  # larger uploads are refused before saving

jobs = JobManager()


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


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
    uploads.cleanup(startup=True)
    remove_stale_temp_files()
    threading.Thread(target=_retention_loop, name="upload-retention", daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
