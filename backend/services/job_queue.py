"""Enterprise background job queue — concurrent, persistent, non-blocking workers."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from backend.config import JOB_QUEUE_MAX_WORKERS
from backend.database import (
    create_studio_job,
    get_studio_job,
    list_studio_jobs,
    update_studio_job,
)

logger = logging.getLogger(__name__)

JobHandler = Callable[["JobStoreAdapter", str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MontageJob:
    """Backward-compatible job view for studio routes."""

    id: str
    status: str = "pending"
    progress: int = 0
    message: str = "Queued"
    output_path: str | None = None
    output_url: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_utc_now)
    completed_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> MontageJob:
        return cls(
            id=row["id"],
            status=row["status"],
            progress=int(row.get("progress") or 0),
            message=row.get("message") or "Queued",
            output_path=row.get("output_path"),
            output_url=row.get("output_url"),
            error=row.get("error"),
            created_at=row.get("created_at") or _utc_now(),
            completed_at=row.get("completed_at"),
            meta=row.get("meta") or {},
        )


class JobStoreAdapter:
    """Adapter passed to workers — persists every update to SQLite."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def get(self, job_id: str) -> MontageJob | None:
        if job_id != self.job_id:
            return montage_jobs.get(job_id)
        row = get_studio_job(job_id)
        return MontageJob.from_row(row) if row else None

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        output_path: str | None = None,
        output_url: str | None = None,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        update_studio_job(
            job_id,
            status=status,
            progress=progress,
            message=message,
            output_path=output_path,
            output_url=output_url,
            error=error,
            meta=meta,
        )


class BackgroundJobManager:
    """Thread-pool job queue with SQLite persistence and typed handlers."""

    def __init__(self, max_workers: int = JOB_QUEUE_MAX_WORKERS) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="vv-job",
        )
        self._handlers: dict[str, JobHandler] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._shutdown = False

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler
        logger.info("Registered job handler: %s", job_type)

    def enqueue(self, job_type: str, meta: dict[str, Any] | None = None) -> str:
        if self._shutdown:
            raise RuntimeError("Job queue is shutting down")
        if job_type not in self._handlers:
            raise ValueError(f"No handler registered for job type: {job_type}")

        job_id = str(uuid.uuid4())
        create_studio_job(job_id, job_type, meta=meta or {})
        adapter = JobStoreAdapter(job_id)
        future = self._executor.submit(self._run_job, job_type, adapter, job_id)
        with self._lock:
            self._futures[job_id] = future
            future.add_done_callback(lambda _f: self._futures.pop(job_id, None))
        logger.info("Enqueued %s job %s", job_type, job_id[:8])
        return job_id

    def _run_job(self, job_type: str, store: JobStoreAdapter, job_id: str) -> None:
        handler = self._handlers[job_type]
        try:
            update_studio_job(job_id, status="running", message="Processing…")
            handler(store, job_id)
        except Exception as exc:
            logger.exception("Job %s (%s) crashed", job_id[:8], job_type)
            update_studio_job(
                job_id,
                status="failed",
                progress=0,
                message="Job crashed",
                error=str(exc),
            )

    def get(self, job_id: str) -> MontageJob | None:
        row = get_studio_job(job_id)
        return MontageJob.from_row(row) if row else None

    def to_dict(self, job: MontageJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "output_path": job.output_path,
            "output_url": job.output_url,
            "error": job.error,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "meta": job.meta,
        }

    def list_recent(self, job_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return list_studio_jobs(job_type=job_type, limit=limit)

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        self._shutdown = True
        logger.info("Shutting down job queue (wait=%s)", wait)
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


class MontageJobStore:
    """Backward-compatible facade over BackgroundJobManager."""

    def __init__(self, manager: BackgroundJobManager) -> None:
        self._manager = manager

    def create(self, *, meta: dict[str, Any] | None = None) -> MontageJob:
        job_id = self._manager.enqueue("montage", meta=meta or {})
        job = self._manager.get(job_id)
        if not job:
            raise RuntimeError("Failed to create montage job")
        return job

    def create_typed(self, job_type: str, *, meta: dict[str, Any] | None = None) -> MontageJob:
        job_id = self._manager.enqueue(job_type, meta=meta or {})
        job = self._manager.get(job_id)
        if not job:
            raise RuntimeError(f"Failed to create {job_type} job")
        return job

    def get(self, job_id: str) -> MontageJob | None:
        return self._manager.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        output_path: str | None = None,
        output_url: str | None = None,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        update_studio_job(
            job_id,
            status=status,
            progress=progress,
            message=message,
            output_path=output_path,
            output_url=output_url,
            error=error,
            meta=meta,
        )

    def to_dict(self, job: MontageJob) -> dict[str, Any]:
        return self._manager.to_dict(job)


# Singleton queue — handlers registered at startup
job_manager = BackgroundJobManager()
montage_jobs = MontageJobStore(job_manager)


def run_job_in_background(job_id: str, worker: JobHandler) -> None:
    """Legacy helper — prefer job_manager.enqueue() for new code."""
    job_manager.register("_legacy_", worker)
    update_studio_job(job_id, status="running")
    adapter = JobStoreAdapter(job_id)
    job_manager._executor.submit(job_manager._run_job, "_legacy_", adapter, job_id)
