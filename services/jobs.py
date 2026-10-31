"""Background jobs: one worker thread and an in-memory queue.

Transcription and conversion take minutes, so they run here instead of inside an
HTTP request. Jobs run one at a time (one shared model, one GPU). Submitting the
same work (same key) while it is queued or running returns the existing job.

Cancelling sets a flag; the job notices at its next check (FFmpeg: about twice a
second; Whisper: after each decoded segment) and stops by raising JobCancelled.
JobCancelled is a BaseException, so code that handles ordinary errors (`except
Exception`) lets it through while `finally` blocks still delete temporary files.
Cache entries are only written after a step completes, so a cancelled job leaves
none. If the server stops, queued and running jobs are simply lost.
"""

import itertools
import logging
import queue
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, Hashable, Optional

import config

log = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong while processing this video."


class JobCancelled(BaseException):
    """Raised inside a job's work when it has been cancelled."""


class Job:
    def __init__(self, kind: str, key: Hashable, work: Callable[["Job"], Any]):
        self.id = uuid.uuid4().hex
        self.kind, self.key, self.work = kind, key, work
        self.state = "queued"            # queued, running, done, failed, cancelled
        self.stage: Optional[str] = None
        self.progress: Optional[float] = None
        self.device: Optional[str] = None
        self.compute_type: Optional[str] = None
        self.from_cache = False
        self.title: Optional[str] = None     # e.g. the YouTube title, once known
        self.error: Optional[Dict[str, str]] = None
        self.result: Any = None
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        self._cancel = threading.Event()

    # Called by the work function -------------------------------------------------
    def check(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def set_stage(self, stage: str) -> None:
        self.check()
        if stage != self.stage:
            self.stage, self.progress = stage, None
            log.info("Job %s (%s): %s", self.id[:8], self.kind, stage)

    def set_progress(self, fraction: float) -> None:
        self.check()
        self.progress = max(0.0, min(1.0, float(fraction)))

    # ------------------------------------------------------------------------------
    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def status(self, queue_position: Optional[int] = None) -> Dict:
        out = {"id": self.id, "kind": self.kind, "state": self.state, "stage": self.stage,
               "progress": self.progress, "device": self.device, "compute_type": self.compute_type,
               "from_cache": self.from_cache, "title": self.title, "error": self.error}
        if queue_position is not None:
            out["queue_position"] = queue_position
        if self.state == "done":
            out["result"] = self.result
        return out


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._finished = deque()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._order = itertools.count()
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    def submit(self, kind: str, key: Hashable, work: Callable[[Job], Any]) -> Job:
        """Queue `work(job)`, or return the queued/running job with the same key."""
        with self._lock:
            for job in self._jobs.values():
                if job.key == key and not job.finished:
                    log.info("Job %s (%s) already %s for %r; not starting another", job.id[:8], kind, job.state, key)
                    return job
            job = Job(kind, key, work)
            self._jobs[job.id] = job
            self._queue.put(job.id)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="job-worker", daemon=True)
                self._worker.start()
        log.info("Job %s (%s) queued for %r", job.id[:8], kind, key)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def status(self, job_id: str) -> Optional[Dict]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        position = None
        with self._lock:  # the worker changes states under the same lock
            if job.state == "queued":
                waiting = sorted((j for j in self._jobs.values() if j.state == "queued"), key=lambda j: j.created_at)
                position = waiting.index(job)
        return job.status(position)

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop. Returns False if it is unknown or already finished."""
        job = self._jobs.get(job_id)
        if job is None or job.finished:
            return False
        job._cancel.set()
        with self._lock:
            if job.state == "queued":  # never started: nothing to clean up
                self._finish(job, "cancelled")
        log.info("Job %s (%s) cancellation requested", job.id[:8], job.kind)
        return True

    def cancel_where(self, match: Callable[[Hashable], bool]) -> list:
        """Cancel every queued or running job whose key matches; returns their ids."""
        with self._lock:
            ids = [j.id for j in self._jobs.values() if not j.finished and match(j.key)]
        for job_id in ids:
            self.cancel(job_id)
        return ids

    def wait_finished(self, job_ids, timeout: float = 60.0) -> bool:
        """Wait until the given jobs have finished; False on timeout."""
        deadline = time.monotonic() + timeout
        while any((j := self._jobs.get(i)) is not None and not j.finished for i in job_ids):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Block until no job is queued or running (for tests and scripts)."""
        deadline = time.monotonic() + timeout
        while any(not j.finished for j in list(self._jobs.values())):
            if time.monotonic() > deadline:
                raise TimeoutError("jobs still running")
            time.sleep(0.02)

    def wait(self, job_id: str, timeout: float = 30.0) -> Dict:
        """Block until the job finishes (for tests and scripts)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self._jobs[job_id]
            if job.finished:
                return job.status()
            time.sleep(0.02)
        raise TimeoutError(job_id)

    # Worker ---------------------------------------------------------------------------
    def _finish(self, job: Job, state: str) -> None:
        """Mark a job finished (caller holds the lock) and forget the oldest finished ones."""
        job.state, job.finished_at = state, time.time()
        self._finished.append(job.id)
        while len(self._finished) > config.FINISHED_JOBS_KEPT:
            self._jobs.pop(self._finished.popleft(), None)

    def _run(self) -> None:
        while True:
            job = self._jobs.get(self._queue.get())
            with self._lock:
                if job is None or job.state != "queued":
                    continue  # cancelled while waiting, or forgotten
                job.state = "running"
            t0 = time.monotonic()
            try:
                job.check()
                result = job.work(job)
            except JobCancelled:
                with self._lock:
                    self._finish(job, "cancelled")
                log.info("Job %s (%s) cancelled after %.1fs", job.id[:8], job.kind, time.monotonic() - t0)
            except Exception as e:
                code, message = getattr(e, "code", None), getattr(e, "user_message", None)
                if code and message:
                    job.error = {"code": code, "message": message}
                    log.warning("Job %s (%s) failed: %s (%s)", job.id[:8], job.kind, code, message)
                else:
                    job.error = {"code": "internal_error", "message": GENERIC_ERROR}
                    log.exception("Job %s (%s) crashed", job.id[:8], job.kind)
                with self._lock:
                    self._finish(job, "failed")
            else:
                job.result, job.progress = result, 1.0
                with self._lock:
                    self._finish(job, "done")
                log.info("Job %s (%s) done in %.1fs", job.id[:8], job.kind, time.monotonic() - t0)
