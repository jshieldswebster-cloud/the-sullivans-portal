"""Background worker — full Google Drive scan and review queue refresh."""

from __future__ import annotations

import logging

from backend.services.drive_sync_service import DriveSyncService
from backend.services.job_queue import JobStoreAdapter

logger = logging.getLogger(__name__)


def run_drive_sync_job(store: JobStoreAdapter, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return

    try:
        store.update(job_id, status="running", progress=5, message="Starting Drive sync…")
        svc = DriveSyncService()

        def progress(msg: str, pct: int) -> None:
            store.update(job_id, progress=min(pct, 95), message=msg)

        summary = svc.sync_all(progress_cb=progress)
        store.update(
            job_id,
            status="completed",
            progress=100,
            message=(
                f"Synced {summary['projects_scanned']} projects "
                f"across {summary['categories_found']} categories"
            ),
            meta={**(job.meta or {}), "summary": summary},
        )
    except Exception as exc:
        logger.exception("Drive sync job failed")
        store.update(
            job_id,
            status="failed",
            progress=0,
            message="Drive sync failed",
            error=str(exc),
        )
