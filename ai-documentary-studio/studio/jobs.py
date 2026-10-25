"""Background jobs (rendering, TTS, transcription, AI calls) with progress and cancel."""
from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid

log = logging.getLogger("studio")


class Job:
    def __init__(self, kind: str, project_id: str | None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.project_id = project_id
        self.status = "queued"
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None
        self.error_code = None
        self.started = time.time()
        self.finished = None
        self.cancelled = False
        self.proc = None
        self._lock = threading.Lock()

    def update(self, progress=None, message=None):
        with self._lock:
            if progress is not None:
                self.progress = max(self.progress, min(1.0, float(progress)))
            if message is not None:
                self.message = message

    def cancel(self):
        self.cancelled = True
        p = self.proc
        if p is not None:
            try:
                p.terminate()
            except OSError:
                pass

    def to_dict(self):
        elapsed = (self.finished or time.time()) - self.started
        eta = None
        if self.status == "running" and 0.05 < self.progress < 1:
            eta = round(elapsed / self.progress * (1 - self.progress))
        return {"id": self.id, "kind": self.kind, "project_id": self.project_id, "status": self.status,
                "progress": round(self.progress, 4), "message": self.message, "result": self.result,
                "error": self.error, "error_code": self.error_code, "elapsed": round(elapsed, 1), "eta": eta}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, project_id: str | None, fn) -> Job:
        job = Job(kind, project_id)
        with self._lock:
            self.jobs[job.id] = job
            # Keep memory bounded.
            done = [j for j in self.jobs.values() if j.finished and time.time() - j.finished > 3600]
            for j in done:
                self.jobs.pop(j.id, None)

        def runner():
            job.status = "running"
            try:
                job.result = fn(job)
                job.status = "cancelled" if job.cancelled else "done"
                job.progress = 1.0 if not job.cancelled else job.progress
            except Exception as e:  # reported to the UI
                job.status = "cancelled" if job.cancelled else "failed"
                job.error = str(e)
                job.error_code = type(e).__name__
                if job.cancelled:
                    log.info("job %s (%s) cancelled", job.id, kind)
                else:
                    log.error("job %s (%s) failed: %s\n%s", job.id, kind, e, traceback.format_exc())
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
        return job

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def active(self, project_id: str | None = None):
        return [j.to_dict() for j in self.jobs.values()
                if j.status in ("queued", "running") and (project_id is None or j.project_id == project_id)]


JOBS = JobManager()
