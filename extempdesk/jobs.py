"""Background job registry so long filings don't block the UI."""

from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime


class Job:
    def __init__(self, kind: str, params: dict):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self.status = "running"
        self.started = datetime.now().strftime("%H:%M:%S")
        self.finished: str | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.lines: list[dict] = []
        self.done = 0
        self.total = 0
        self._lock = threading.Lock()
        self.log("job started")

    def log(self, message: str, done: int | None = None, total: int | None = None) -> None:
        with self._lock:
            self.lines.append({"t": datetime.now().strftime("%H:%M:%S"), "m": str(message)})
            if done is not None:
                self.done = int(done)
            if total is not None:
                self.total = int(total)

    def progress(self, message: str, **extra) -> None:
        self.log(message, extra.get("done"), extra.get("total"))

    def finish(self, result: dict | None = None, error: str | None = None) -> None:
        with self._lock:
            self.status = "error" if error else "done"
            self.result = result
            self.error = error
            self.finished = datetime.now().strftime("%H:%M:%S")
        if error:
            self.log(f"FAILED: {error}")
        else:
            self.log("job finished")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "started": self.started,
                "finished": self.finished,
                "done": self.done,
                "total": self.total,
                "result": self.result,
                "error": self.error,
                "log": self.lines[-400:],
            }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def run(self, kind: str, params: dict, target) -> Job:
        job = Job(kind, params)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > 25:
                old = self._order.pop(0)
                self._jobs.pop(old, None)

        def worker() -> None:
            try:
                job.finish(result=target(job.progress))
            except Exception as exc:  # keep the server alive no matter what
                job.finish(error=f"{type(exc).__name__}: {exc}")
                job.log(traceback.format_exc(limit=3))

        threading.Thread(target=worker, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))[:10]
        out = []
        for job_id in ids:
            job = self._jobs.get(job_id)
            if job:
                snap = job.snapshot()
                snap.pop("log", None)
                out.append(snap)
        return out
