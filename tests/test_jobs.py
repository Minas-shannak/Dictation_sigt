"""services.jobs: queueing, de-duplication, status, errors and cancellation."""

import threading
import time

import pytest

from services.jobs import JobCancelled, JobManager


class FriendlyError(Exception):
    def __init__(self):
        super().__init__("boom")
        self.code, self.user_message = "no_audio", "This video has no audio."


@pytest.fixture
def jobs():
    return JobManager()


def test_job_runs_and_returns_its_result(jobs):
    def work(job):
        job.set_stage("transcribing")
        job.set_progress(0.5)
        job.device, job.compute_type = "cuda", "float16"
        return {"sentences": 3}
    job = jobs.submit("transcribe", "a", work)
    status = jobs.wait(job.id)
    assert status["state"] == "done" and status["result"] == {"sentences": 3}
    assert (status["stage"], status["progress"], status["device"], status["compute_type"]) == ("transcribing", 1.0, "cuda", "float16")


def test_jobs_run_one_at_a_time_in_order(jobs):
    running, order, peak = [], [], [0]
    lock = threading.Lock()

    def make(name):
        def work(job):
            with lock:
                running.append(name)
                peak[0] = max(peak[0], len(running))
            time.sleep(0.05)
            order.append(name)
            with lock:
                running.remove(name)
        return work
    ids = [jobs.submit("transcribe", name, make(name)).id for name in "abcd"]
    for i in ids:
        jobs.wait(i)
    assert order == list("abcd") and peak[0] == 1


def test_same_key_while_queued_or_running_returns_the_same_job(jobs):
    gate = threading.Event()
    first = jobs.submit("transcribe", "same", lambda job: gate.wait(5))
    second = jobs.submit("transcribe", "same", lambda job: "never runs")
    other = jobs.submit("transcribe", "other", lambda job: "ok")
    assert second is first and other is not first
    gate.set()
    jobs.wait(first.id)
    # Once finished, the same key starts a new job.
    assert jobs.submit("transcribe", "same", lambda job: "again") is not first


def test_queued_job_reports_its_position_and_can_be_cancelled(jobs):
    gate, started = threading.Event(), threading.Event()
    running = jobs.submit("transcribe", "a", lambda job: (started.set(), gate.wait(5)))
    started.wait(2)
    queued = jobs.submit("transcribe", "b", lambda job: pytest.fail("cancelled job ran"))
    assert jobs.status(queued.id)["state"] == "queued" and jobs.status(queued.id)["queue_position"] == 0
    assert jobs.cancel(queued.id) is True
    assert jobs.status(queued.id)["state"] == "cancelled"
    gate.set()
    jobs.wait(running.id)
    time.sleep(0.1)
    assert jobs.status(queued.id)["state"] == "cancelled"


def test_running_job_stops_at_its_next_check_and_cleans_up(jobs):
    started, cleaned = threading.Event(), threading.Event()

    def work(job):
        try:
            started.set()
            for i in range(500):
                job.set_progress(i / 500)  # checks for cancellation
                time.sleep(0.01)
        finally:
            cleaned.set()
    job = jobs.submit("transcribe", "x", work)
    started.wait(2)
    assert jobs.cancel(job.id)
    status = jobs.wait(job.id)
    assert status["state"] == "cancelled" and cleaned.is_set() and "result" not in status


def test_cancellation_passes_through_code_that_catches_exceptions(jobs):
    # The transcriber wraps ordinary errors (`except Exception`); a cancel must not become a failure.
    def work(job):
        job._cancel.set()
        try:
            job.check()
        except Exception:
            return "swallowed"
    assert jobs.wait(jobs.submit("transcribe", "y", work).id)["state"] == "cancelled"


def test_known_errors_show_their_message_and_unknown_ones_a_generic_one(jobs):
    def friendly(job):
        raise FriendlyError()

    def crash(job):
        raise KeyError("internal detail")
    a = jobs.wait(jobs.submit("transcribe", "f", friendly).id)
    b = jobs.wait(jobs.submit("transcribe", "c", crash).id)
    assert a["state"] == "failed" and a["error"] == {"code": "no_audio", "message": "This video has no audio."}
    assert b["state"] == "failed" and b["error"]["code"] == "internal_error"
    assert "internal detail" not in b["error"]["message"] and "KeyError" not in b["error"]["message"]


def test_finished_jobs_are_forgotten_after_the_history_limit(jobs, monkeypatch):
    import config
    monkeypatch.setattr(config, "FINISHED_JOBS_KEPT", 3)
    ids = [jobs.submit("transcribe", i, lambda job: None).id for i in range(5)]
    jobs.wait(ids[-1])  # jobs run in order, so all are finished
    assert jobs.status(ids[0]) is None and jobs.status(ids[-1])["state"] == "done"


def test_cancelling_a_finished_or_unknown_job_is_refused(jobs):
    job = jobs.submit("transcribe", "z", lambda job: 1)
    jobs.wait(job.id)
    assert jobs.cancel(job.id) is False and jobs.cancel("nope") is False
