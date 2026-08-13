"""Daily backlog batch worker — 3 posts/day from VV LUXE STUDIO Drive into Review for Posting."""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from backend.config import (
    DAILY_BACKLOG_ENABLED,
    DAILY_BACKLOG_POSTS_PER_DAY,
    DAILY_BACKLOG_RUN_HOUR_UTC,
    DAILY_BACKLOG_SETTINGS_KEY,
    GOOGLE_DRIVE_MASTER_FOLDER_ID,
    GOOGLE_DRIVE_MASTER_FOLDER_NAME,
    REVIEW_FOR_POSTING_QUEUE,
    REVIEW_FOR_POSTING_DIR,
)
from backend.database import (
    get_drive_project,
    get_drive_project_by_folder,
    get_studio_setting,
    list_unbatched_drive_folder_ids,
    set_studio_setting,
    upsert_drive_project,
    update_drive_project,
)
from backend.services.drive_service import DriveNotConnectedError, DriveService
from backend.services.drive_sync_service import DriveSyncService
from backend.services.ideal_row_service import (
    IdealRowService,
    POST_2_CAROUSEL_COUNT,
    ideal_row_paths,
    review_for_posting_paths,
)
from backend.services.job_queue import montage_jobs
from backend.services.upload_service import UploadService

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int], None]

_PROCESSED_STATUSES = frozenset(
    {"review_for_posting", "approved", "published", "processing"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyBacklogWorker:
    """Select unprocessed Drive projects and materialize 3-post staging packages per day."""

    def __init__(
        self,
        drive: DriveService | None = None,
        sync: DriveSyncService | None = None,
    ) -> None:
        self.drive = drive or DriveService()
        self.sync = sync or DriveSyncService(self.drive)

    def load_state(self) -> dict[str, Any]:
        return get_studio_setting(
            DAILY_BACKLOG_SETTINGS_KEY,
            default={
                "last_run_date": None,
                "processed_today": 0,
                "project_ids_today": [],
                "last_summary": {},
            },
        )

    def save_state(self, state: dict[str, Any]) -> None:
        set_studio_setting(DAILY_BACKLOG_SETTINGS_KEY, state)

    def remaining_quota(self, *, force: bool = False) -> int:
        if force:
            return DAILY_BACKLOG_POSTS_PER_DAY
        state = self.load_state()
        today = _today_utc()
        if state.get("last_run_date") != today:
            return DAILY_BACKLOG_POSTS_PER_DAY
        processed = int(state.get("processed_today") or 0)
        return max(0, DAILY_BACKLOG_POSTS_PER_DAY - processed)

    def status(self) -> dict[str, Any]:
        state = self.load_state()
        return {
            "enabled": DAILY_BACKLOG_ENABLED,
            "posts_per_day": DAILY_BACKLOG_POSTS_PER_DAY,
            "run_hour_utc": DAILY_BACKLOG_RUN_HOUR_UTC,
            "remaining_today": self.remaining_quota(),
            "staging_dir": str(REVIEW_FOR_POSTING_DIR),
            "queue_name": REVIEW_FOR_POSTING_QUEUE,
            "master_folder_id": GOOGLE_DRIVE_MASTER_FOLDER_ID,
            "master_folder_name": GOOGLE_DRIVE_MASTER_FOLDER_NAME,
            **state,
        }

    def select_candidates(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        skip_ids = list_unbatched_drive_folder_ids()
        candidates: list[dict[str, Any]] = []

        # Prefer already-synced backlog rows the dashboard is showing.
        from backend.database import list_drive_projects

        for row in list_drive_projects(status="pending_review", limit=200):
            folder_id = row.get("drive_folder_id")
            if not folder_id or folder_id in skip_ids:
                continue
            package = {
                "cover_drive_id": row.get("cover_drive_id"),
                "carousel_drive_ids": row.get("carousel_drive_ids") or [],
                "reel_drive_ids": row.get("reel_drive_ids") or [],
                "asset_count": row.get("asset_count") or 0,
                "warnings": row.get("parse_warnings") or [],
            }
            if package.get("warnings"):
                continue
            if not package.get("cover_drive_id"):
                continue
            if len(package.get("carousel_drive_ids") or []) != POST_2_CAROUSEL_COUNT:
                continue
            if not package.get("reel_drive_ids"):
                continue
            candidates.append(
                {
                    "drive_folder_id": folder_id,
                    "category": row["category"],
                    "event_name": row["event_name"],
                    "modified_time": row.get("synced_at") or "",
                    "package": package,
                    "project_id": row["id"],
                }
            )
            skip_ids.add(folder_id)
            if len(candidates) >= limit:
                return candidates[:limit]

        if not self.drive.is_connected():
            if candidates:
                return candidates[:limit]
            raise DriveNotConnectedError("Google Drive not connected")

        master = self.drive.resolve_master_folder()
        logger.info(
            "Daily backlog scanning master folder %s (%s)",
            master.get("name", GOOGLE_DRIVE_MASTER_FOLDER_NAME),
            master["id"],
        )
        for cat in self.drive.list_master_categories():
            for proj in self.drive.list_project_folders(cat["id"]):
                if proj["id"] in skip_ids:
                    continue
                package = self.drive.parse_project_folder(proj["id"])
                if package.get("warnings"):
                    continue
                if not package.get("cover_drive_id"):
                    continue
                if len(package.get("carousel_drive_ids") or []) != POST_2_CAROUSEL_COUNT:
                    continue
                if not package.get("reel_drive_ids"):
                    continue
                candidates.append(
                    {
                        "drive_folder_id": proj["id"],
                        "category": cat["name"],
                        "event_name": proj["name"],
                        "modified_time": proj.get("modified_time") or "",
                        "package": package,
                    }
                )
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

        candidates.sort(key=lambda c: (c["modified_time"], c["event_name"]))
        return candidates[:limit]

    def process_candidate(self, candidate: dict[str, Any], *, batch_date: str) -> dict[str, Any]:
        folder_id = candidate["drive_folder_id"]
        category = candidate["category"]
        event_name = candidate["event_name"]
        package = candidate["package"]

        existing = get_drive_project_by_folder(folder_id)
        project_id = existing["id"] if existing else str(uuid.uuid4())
        now = _utc_now()

        upsert_drive_project(
            {
                "id": project_id,
                "drive_folder_id": folder_id,
                "category": category,
                "event_name": event_name,
                "status": "processing",
                "cover_drive_id": package.get("cover_drive_id"),
                "carousel_drive_ids": package.get("carousel_drive_ids") or [],
                "reel_drive_ids": package.get("reel_drive_ids") or [],
                "asset_count": package.get("asset_count") or 0,
                "parse_warnings": package.get("warnings") or [],
                "audio_track_id": existing.get("audio_track_id") if existing else None,
                "local_paths": {},
                "reel_job_id": None,
                "synced_at": now,
                "created_at": existing.get("created_at") if existing else now,
                "queue_name": REVIEW_FOR_POSTING_QUEUE,
                "staging_path": None,
                "batch_date": batch_date,
            }
        )

        row = get_drive_project(project_id)
        if not row:
            raise RuntimeError("Failed to load project after upsert")

        materialized = self.sync.materialize_package(
            row,
            staging=True,
            audio_track_id=row.get("audio_track_id"),
        )
        staging_base = materialized["ideal_row"]["base_path"]

        update_drive_project(
            project_id,
            status="review_for_posting",
            queue_name=REVIEW_FOR_POSTING_QUEUE,
            staging_path=staging_base,
            batch_date=batch_date,
            local_paths=materialized.get("local_paths") or {},
            reel_job_id=materialized.get("reel_job_id"),
        )

        logger.info(
            "Daily backlog staged → %s / %s (%s)",
            category,
            event_name,
            staging_base,
        )
        return {
            "project_id": project_id,
            "category": category,
            "event_name": event_name,
            "staging_path": staging_base,
            "reel_job_id": materialized.get("reel_job_id"),
        }

    def run_daily_batch(
        self,
        *,
        progress_cb: ProgressCallback | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        def report(msg: str, pct: int) -> None:
            if progress_cb:
                progress_cb(msg, pct)

        if not DAILY_BACKLOG_ENABLED and not force:
            return {
                "skipped": True,
                "processed": 0,
                "reason": "Daily backlog disabled",
                "message": "Daily backlog is disabled on this server",
            }

        if not self.drive.is_connected():
            raise DriveNotConnectedError("Connect Google Drive before running daily backlog")

        quota = self.remaining_quota(force=force)
        if quota <= 0:
            summary = {
                "skipped": True,
                "processed": 0,
                "reason": f"Daily quota reached ({DAILY_BACKLOG_POSTS_PER_DAY}/day)",
                "remaining_today": 0,
                "message": f"Daily quota already reached ({DAILY_BACKLOG_POSTS_PER_DAY}/day). Re-run with force to stage more.",
            }
            return summary

        report("Scanning Drive for unprocessed projects…", 10)
        candidates = self.select_candidates(quota)
        if not candidates:
            summary = {
                "processed": 0,
                "remaining_today": quota,
                "message": "No complete unprocessed projects found in the Drive backlog",
            }
            return summary

        batch_date = _today_utc()
        state = self.load_state()
        if state.get("last_run_date") != batch_date:
            state["processed_today"] = 0
            state["project_ids_today"] = []
        state["last_run_date"] = batch_date

        processed: list[dict[str, Any]] = []
        total = len(candidates)
        for idx, candidate in enumerate(candidates):
            pct = 20 + int(70 * idx / max(total, 1))
            report(
                f"Building package {idx + 1}/{total}: {candidate['event_name']}…",
                pct,
            )
            try:
                result = self.process_candidate(candidate, batch_date=batch_date)
                processed.append(result)
                state["processed_today"] = int(state.get("processed_today") or 0) + 1
                ids = list(state.get("project_ids_today") or [])
                ids.append(result["project_id"])
                state["project_ids_today"] = ids
            except Exception as exc:
                logger.exception(
                    "Daily backlog failed for %s: %s",
                    candidate.get("event_name"),
                    exc,
                )
                folder_id = candidate["drive_folder_id"]
                existing = get_drive_project_by_folder(folder_id)
                if existing:
                    update_drive_project(
                        existing["id"],
                        status="pending_review",
                        queue_name=None,
                    )

        remaining = max(0, DAILY_BACKLOG_POSTS_PER_DAY - int(state.get("processed_today") or 0))
        summary = {
            "processed": len(processed),
            "remaining_today": remaining,
            "batch_date": batch_date,
            "projects": processed,
            "quota": DAILY_BACKLOG_POSTS_PER_DAY,
        }
        state["last_summary"] = summary
        self.save_state(state)

        report(f"Daily batch complete — {len(processed)} packages staged", 100)
        logger.info("Daily backlog batch finished: %s", summary)
        return summary


def promote_staging_to_production(
    category: str,
    event_name: str,
    *,
    staging_path: str | None = None,
) -> dict[str, str]:
    """Copy staged Ideal Row into permanent uploads tree."""
    src_base = Path(staging_path) if staging_path else review_for_posting_paths(category, event_name)["base"]
    if not src_base.is_dir():
        raise FileNotFoundError(f"Staging folder not found: {src_base}")

    dest_paths = ideal_row_paths(category, event_name)
    dest_base = dest_paths["base"]
    if dest_base.exists():
        shutil.rmtree(dest_base)
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_base, dest_base)

    from backend.services.studio_state_service import StudioStateService

    StudioStateService().register_ideal_row_event(
        category=category,
        event_name=event_name,
        ideal_row_path=str(dest_base),
        metadata={"promoted_from_staging": str(src_base)},
    )

    return {
        "staging_path": str(src_base),
        "production_path": str(dest_base),
        "post_1_url": UploadService.media_url_for(cover) if (cover := next(dest_paths["post_1"].glob("cover*"), None)) else "",
    }


class DailyBacklogScheduler:
    """Background thread — run daily batch at configured UTC hour."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not DAILY_BACKLOG_ENABLED:
            logger.info("Daily backlog scheduler disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="daily-backlog-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Daily backlog scheduler started (hour=%02d UTC, %d/day)",
            DAILY_BACKLOG_RUN_HOUR_UTC,
            DAILY_BACKLOG_POSTS_PER_DAY,
        )

    def stop(self) -> None:
        self._stop.set()

    def _seconds_until_next_run(self) -> float:
        now = datetime.now(timezone.utc)
        target = now.replace(
            hour=DAILY_BACKLOG_RUN_HOUR_UTC,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now >= target:
            target = target + timedelta(days=1)
        return max(60.0, (target - now).total_seconds())

    def _loop(self) -> None:
        worker = DailyBacklogWorker()
        # Run on startup if today's quota not met and Drive connected
        try:
            if worker.remaining_quota() > 0 and worker.drive.is_connected():
                logger.info("Running startup daily backlog check…")
                worker.run_daily_batch()
        except DriveNotConnectedError:
            logger.info("Drive not connected — skipping startup backlog run")
        except Exception:
            logger.exception("Startup daily backlog run failed")

        while not self._stop.is_set():
            wait_sec = self._seconds_until_next_run()
            logger.debug("Next daily backlog run in %.0f seconds", wait_sec)
            if self._stop.wait(timeout=min(wait_sec, 3600.0)):
                break
            if datetime.now(timezone.utc).hour != DAILY_BACKLOG_RUN_HOUR_UTC:
                continue
            try:
                worker.run_daily_batch()
            except DriveNotConnectedError:
                logger.warning("Scheduled backlog skipped — Drive not connected")
            except Exception:
                logger.exception("Scheduled daily backlog run failed")


def run_daily_backlog_job(store: Any, job_id: str) -> None:
    """Job queue handler for manual/scheduled daily backlog runs."""
    job = store.get(job_id)
    if not job:
        return
    force = bool((job.meta or {}).get("force"))
    worker = DailyBacklogWorker()

    def progress(msg: str, pct: int) -> None:
        store.update(job_id, progress=min(pct, 95), message=msg)

    try:
        store.update(job_id, status="running", progress=5, message="Starting daily backlog…")
        summary = worker.run_daily_batch(progress_cb=progress, force=force)
        store.update(
            job_id,
            status="completed",
            progress=100,
            message=summary.get("message") or f"Staged {summary.get('processed', 0)} packages",
            meta={**(job.meta or {}), "summary": summary},
        )
    except Exception as exc:
        logger.exception("Daily backlog job failed")
        store.update(
            job_id,
            status="failed",
            progress=0,
            message="Daily backlog failed",
            error=str(exc),
        )


# Singleton scheduler — started from main.py lifespan
daily_backlog_scheduler = DailyBacklogScheduler()
