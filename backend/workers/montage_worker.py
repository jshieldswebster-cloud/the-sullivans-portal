"""Montage render worker — Ken Burns, watermark, audio mux."""

from __future__ import annotations

import logging
from pathlib import Path

from backend.config import MONTAGE_MOTION_OPTIONS, MONTAGE_TRANSITION_SEC, VIDEOS_DIR
from backend.services.job_queue import JobStoreAdapter
from backend.services.montage_jobs import resolve_render_options
from backend.services.montage_service import MontageParams, MontageService

logger = logging.getLogger(__name__)


def run_montage_job(store: JobStoreAdapter, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return

    try:
        store.update(job_id, status="running", progress=5, message="Preparing photos…")
        meta = job.meta
        image_paths = meta["image_paths"]
        motion_key = meta.get("motion_style", "auto")
        motions = MONTAGE_MOTION_OPTIONS.get(motion_key, MONTAGE_MOTION_OPTIONS["auto"])

        params = MontageParams(
            clip_duration_sec=meta.get("clip_duration_sec", 4.0),
            transition_sec=meta.get("transition_sec", MONTAGE_TRANSITION_SEC),
            motions=motions,
        )
        service = MontageService(params=params)

        store.update(job_id, progress=40, message="Rendering Ken Burns clips…")
        output_name = f"reel_{job_id[:8]}.mp4"
        custom_output = meta.get("output_path")
        output_path = Path(custom_output) if custom_output else VIDEOS_DIR / output_name

        store.update(job_id, progress=65, message="Applying brand logo & encoding…")
        render_opts = resolve_render_options(meta)
        result = service.assemble(
            image_paths,
            output_path=output_path,
            **render_opts,
        )

        output_url = meta.get("output_url")
        if not output_url:
            if custom_output:
                try:
                    from backend.config import VIDEOS_DIR as _VID

                    rel = Path(result.output_path).relative_to(_VID.parent)
                    output_url = f"/media/uploads/{rel.as_posix()}"
                except ValueError:
                    from backend.services.upload_service import UploadService

                    output_url = UploadService.media_url_for(Path(result.output_path))
            else:
                output_url = f"/media/videos/{output_name}"

        store.update(
            job_id,
            status="completed",
            progress=100,
            message="Render complete",
            output_path=result.output_path,
            output_url=output_url,
            meta={**meta, "titles": meta.get("titles")},
        )
    except Exception as exc:
        logger.exception("Montage job %s failed", job_id)
        store.update(
            job_id,
            status="failed",
            progress=0,
            message="Render failed",
            error=str(exc),
        )
