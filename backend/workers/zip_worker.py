"""Instagram ZIP packaging worker — non-blocking export pipeline."""

from __future__ import annotations

import logging

from backend.services.job_queue import JobStoreAdapter

logger = logging.getLogger(__name__)


def run_zip_export_job(store: JobStoreAdapter, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return

    meta = job.meta
    category = meta["category"]
    event_name = meta["event_name"]
    reel_path = meta.get("reel_path")

    try:
        store.update(job_id, status="running", progress=10, message="Optimizing cover…")

        from backend.main import app_state
        from backend.services.instagram_export_service import InstagramExportService

        export_service = InstagramExportService()

        store.update(job_id, progress=40, message="Building carousel ZIP…")

        result = export_service.prepare_package_sync(
            category,
            event_name,
            reel_path=reel_path,
            caption_engine=app_state.caption_engine,
        )

        store.update(
            job_id,
            status="completed",
            progress=100,
            message="Instagram package ready",
            output_path=result.export_base,
            meta={**meta, "export": result.to_dict()},
        )
    except Exception as exc:
        logger.exception("ZIP export job %s failed", job_id)
        store.update(
            job_id,
            status="failed",
            progress=0,
            message="Export failed",
            error=str(exc),
        )
