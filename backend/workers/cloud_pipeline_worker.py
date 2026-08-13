#!/usr/bin/env python3
"""24/7 Render worker: Drive backlog → 3-post package → Canva.

Polls Google Drive, materializes complete event packages, and delivers them
to Canva. On failure it logs the traceback, applies an operational patch
(token refresh, re-parse, re-download, backoff), and retries that event
until the package is built and sent.

Does not rewrite application source on the dyno (Render's filesystem is
ephemeral). Source-level auto-fix stays in auto_fix_loop.py on a laptop.

Run locally:
  python -m backend.workers.cloud_pipeline_worker --once

Render:
  worker: python -m backend.workers.cloud_pipeline_worker
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import (  # noqa: E402
    CLOUD_WORKER_ERROR_LOG_KEY,
    CLOUD_WORKER_EVENT_MAX_ATTEMPTS,
    CLOUD_WORKER_EVENT_RETRY_SEC,
    CLOUD_WORKER_MAX_PER_TICK,
    CLOUD_WORKER_POLL_SEC,
    CLOUD_WORKER_REEL_WAIT_SEC,
    CLOUD_WORKER_SETTINGS_KEY,
    ensure_directories,
)
from backend.database import (  # noqa: E402
    get_studio_setting,
    init_db,
    list_drive_projects,
    set_studio_setting,
    update_drive_project,
)
from backend.services.canva_service import (  # noqa: E402
    CanvaDeliveryError,
    CanvaNotConfiguredError,
    CanvaService,
    collect_package_files,
)
from backend.services.daily_backlog_worker import (  # noqa: E402
    DailyBacklogWorker,
    _package_ready,
    _today_utc,
)
from backend.services.drive_service import DriveNotConnectedError, DriveService  # noqa: E402
from backend.services.drive_sync_service import DriveSyncService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cloud-pipeline")

_STOP = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_error(exc: BaseException) -> str:
    """Map a pipeline exception to a remediation patch."""
    if isinstance(exc, DriveNotConnectedError):
        return "drive_auth"
    if isinstance(exc, CanvaNotConfiguredError):
        return "canva_config"
    msg = str(exc).lower()
    if "401" in msg or "unauthorized" in msg or "not connected" in msg:
        if "canva" in msg:
            return "canva_auth"
        return "drive_auth"
    if "not ready" in msg or "incomplete" in msg or "missing" in msg:
        return "incomplete_package"
    if "429" in msg or "rate" in msg or "timeout" in msg or "timed out" in msg:
        return "backoff"
    if "canva" in msg:
        return "canva_retry"
    return "retry"


def already_sent_to_canva(row: dict[str, Any]) -> bool:
    canva = (row.get("local_paths") or {}).get("canva") or {}
    return bool(canva.get("sent_at") and canva.get("complete"))


def _append_error_log(entry: dict[str, Any]) -> None:
    state = get_studio_setting(CLOUD_WORKER_ERROR_LOG_KEY, default={"entries": []}) or {}
    entries = list(state.get("entries") or [])
    entries.append(entry)
    set_studio_setting(CLOUD_WORKER_ERROR_LOG_KEY, {"entries": entries[-50:], "updated_at": _utc_now()})


def apply_patch(kind: str, *, drive: DriveService, canva: CanvaService) -> None:
    """Operational heal — refresh creds, back off, then the caller retries the event."""
    logger.info("Applying pipeline patch: %s", kind)
    if kind == "drive_auth":
        try:
            drive.refresh_access_token(force=True)
        except Exception:
            logger.exception("Drive token refresh failed")
        time.sleep(max(CLOUD_WORKER_EVENT_RETRY_SEC, 15.0))
        return
    if kind == "canva_auth":
        try:
            canva.refresh_access_token(force=True)
        except Exception:
            logger.exception("Canva token refresh failed")
        time.sleep(CLOUD_WORKER_EVENT_RETRY_SEC)
        return
    if kind == "canva_config":
        time.sleep(max(CLOUD_WORKER_EVENT_RETRY_SEC, 30.0))
        return
    if kind == "incomplete_package":
        time.sleep(max(CLOUD_WORKER_EVENT_RETRY_SEC, 20.0))
        return
    if kind == "backoff":
        time.sleep(max(CLOUD_WORKER_EVENT_RETRY_SEC * 3, 30.0))
        return
    time.sleep(CLOUD_WORKER_EVENT_RETRY_SEC)


class CloudPipelineWorker:
    """Monitor Drive, build packages, deliver to Canva, retry until success."""

    def __init__(self) -> None:
        self.drive = DriveService()
        self.sync = DriveSyncService(self.drive)
        self.backlog = DailyBacklogWorker(drive=self.drive, sync=self.sync)
        self.canva = CanvaService()

    def boot(self) -> None:
        init_db()
        ensure_directories()
        from backend.services.job_queue import job_manager
        from backend.services.drive_service import drive_token_refresh_scheduler
        from backend.services.canva_service import canva_token_refresh_scheduler

        job_manager.ensure_handlers()
        drive_token_refresh_scheduler.start()
        canva_token_refresh_scheduler.start()
        logger.info(
            "Cloud pipeline worker ready (poll=%ss, max/tick=%s)",
            CLOUD_WORKER_POLL_SEC,
            CLOUD_WORKER_MAX_PER_TICK,
        )

    def save_tick(self, summary: dict[str, Any]) -> None:
        set_studio_setting(
            CLOUD_WORKER_SETTINGS_KEY,
            {"last_tick": _utc_now(), **summary},
        )

    def tick(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "synced": 0,
            "processed": 0,
            "delivered": 0,
            "skipped": 0,
            "errors": 0,
            "message": "",
        }
        if not self.drive.is_connected():
            try:
                self.drive.refresh_access_token(force=True)
            except Exception as exc:
                summary["message"] = f"Drive not connected: {exc}"
                logger.warning("%s", summary["message"])
                self.save_tick(summary)
                return summary

        try:
            sync_summary = self.sync.sync_all()
            summary["synced"] = int(sync_summary.get("projects_scanned") or 0)
        except DriveNotConnectedError as exc:
            summary["message"] = str(exc)
            self.save_tick(summary)
            return summary
        except Exception:
            logger.exception("Drive sync failed — continuing with known backlog rows")
            summary["errors"] += 1

        delivered = 0
        processed = 0
        skipped = 0
        errors = 0
        for row in self._iter_work_items():
            if _STOP:
                break
            if already_sent_to_canva(row):
                skipped += 1
                continue
            try:
                self.process_event_until_sent(row)
                processed += 1
                delivered += 1
            except Exception:
                errors += 1
                logger.exception("Giving up this tick for %s", row.get("event_name"))
        summary.update(
            {
                "processed": processed,
                "delivered": delivered,
                "skipped": skipped,
                "errors": errors,
                "message": f"tick complete delivered={delivered} errors={errors}",
            }
        )
        self.save_tick(summary)
        logger.info("%s", summary["message"])
        return summary

    def _iter_work_items(self) -> list[dict[str, Any]]:
        pending = list_drive_projects(status="pending_review", limit=200)
        staged = list_drive_projects(status="review_for_posting", limit=200)
        items: list[dict[str, Any]] = []
        for row in pending + staged:
            if already_sent_to_canva(row):
                continue
            package = {
                "cover_drive_id": row.get("cover_drive_id"),
                "carousel_drive_ids": row.get("carousel_drive_ids") or [],
                "reel_drive_ids": row.get("reel_drive_ids") or [],
            }
            if row.get("status") == "pending_review" and not _package_ready(package):
                continue
            items.append(row)
            if len(items) >= CLOUD_WORKER_MAX_PER_TICK:
                break
        return items

    def process_event_until_sent(self, row: dict[str, Any]) -> dict[str, Any]:
        """Retry one event until the 3-post package exists and Canva accepts it."""
        attempt = 0
        max_attempts = CLOUD_WORKER_EVENT_MAX_ATTEMPTS
        last_exc: BaseException | None = None
        while not _STOP:
            attempt += 1
            try:
                result = self._build_and_send(row)
                logger.info(
                    "Delivered %s / %s to Canva after %d attempt(s)",
                    row.get("category"),
                    row.get("event_name"),
                    attempt,
                )
                return result
            except Exception as exc:
                last_exc = exc
                tb = traceback.format_exc()
                kind = classify_error(exc)
                logger.error(
                    "Pipeline error for %s (attempt %d, patch=%s): %s",
                    row.get("event_name"),
                    attempt,
                    kind,
                    exc,
                )
                _append_error_log(
                    {
                        "at": _utc_now(),
                        "event_name": row.get("event_name"),
                        "folder_id": row.get("drive_folder_id"),
                        "attempt": attempt,
                        "patch": kind,
                        "error": str(exc),
                        "traceback": tb[-8000:],
                    }
                )
                if max_attempts and attempt >= max_attempts:
                    raise
                apply_patch(kind, drive=self.drive, canva=self.canva)
                refreshed = None
                folder_id = row.get("drive_folder_id")
                if folder_id:
                    from backend.database import get_drive_project_by_folder

                    refreshed = get_drive_project_by_folder(folder_id)
                if refreshed:
                    row = refreshed
        raise RuntimeError(f"Worker stopping; last error: {last_exc}")

    def _build_and_send(self, row: dict[str, Any]) -> dict[str, Any]:
        folder_id = row.get("drive_folder_id") or ""
        if row.get("status") != "review_for_posting":
            staged = self.backlog.process_folder(
                folder_id,
                category=row.get("category"),
                event_name=row.get("event_name"),
            )
            from backend.database import get_drive_project

            row = get_drive_project(staged["project_id"]) or row

        files = collect_package_files(
            row["category"],
            row["event_name"],
            staging_path=row.get("staging_path"),
        )
        if not files["complete"]:
            deadline = time.time() + CLOUD_WORKER_REEL_WAIT_SEC
            while time.time() < deadline and not files["complete"]:
                time.sleep(2.0)
                files = collect_package_files(
                    row["category"],
                    row["event_name"],
                    staging_path=row.get("staging_path"),
                )
            if not files["complete"]:
                raise RuntimeError(
                    "Package not ready: " + "; ".join(files.get("missing") or [])
                )

        delivery = self.canva.send_package(
            category=row["category"],
            event_name=row["event_name"],
            files=files,
        )
        local_paths = dict(row.get("local_paths") or {})
        local_paths["canva"] = delivery
        update_drive_project(row["id"], local_paths=local_paths, status="published")
        return delivery


def _handle_stop(signum: int, _frame: Any) -> None:
    global _STOP
    logger.info("Received signal %s — stopping after current event", signum)
    _STOP = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VV Luxe cloud Drive→Canva worker")
    parser.add_argument("--once", action="store_true", help="Run a single poll tick and exit")
    parser.add_argument("--poll-sec", type=float, default=float(CLOUD_WORKER_POLL_SEC))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    worker = CloudPipelineWorker()
    worker.boot()

    if args.once:
        worker.tick()
        return 0

    logger.info("Entering 24/7 loop (interval=%ss). Set CLOUD_WORKER_POLL_SEC to change.", args.poll_sec)
    while not _STOP:
        try:
            worker.tick()
        except Exception:
            logger.exception("Tick crashed — backing off")
            time.sleep(max(args.poll_sec, 30.0))
            continue
        slept = 0.0
        while slept < args.poll_sec and not _STOP:
            time.sleep(min(5.0, args.poll_sec - slept))
            slept += 5.0
    logger.info("Cloud pipeline worker stopped")
    return 0


if __name__ == "__main__":
    # Avoid treating an empty Render PORT as a reason to exit.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
