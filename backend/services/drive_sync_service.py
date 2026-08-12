"""Drive sync orchestration — scan master folder, parse 3-post packages, review queue."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.config import DRIVE_POST_2_COUNT, EVENT_CATEGORIES, REVIEW_FOR_POSTING_QUEUE
from backend.database import (
    get_drive_project,
    get_drive_project_by_folder,
    list_drive_projects,
    update_drive_project,
    upsert_drive_project,
)
from backend.services.drive_service import DriveNotConnectedError, DriveService
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DriveSyncService:
    """Scan VV LUXE STUDIO Drive tree and maintain the review queue."""

    def __init__(self, drive: DriveService | None = None) -> None:
        self.drive = drive or DriveService()

    def sync_all(self, *, progress_cb: ProgressCallback | None = None) -> dict[str, Any]:
        if not self.drive.is_connected():
            raise DriveNotConnectedError("Connect Google Drive before syncing")

        def report(msg: str, pct: int) -> None:
            if progress_cb:
                progress_cb(msg, pct)

        report("Resolving master folder…", 5)
        master = self.drive.resolve_master_folder()
        report("Listing event categories…", 15)
        categories = self.drive.list_master_categories()

        scanned = 0
        upserted = 0
        warnings_total = 0

        for idx, cat in enumerate(categories):
            pct = 20 + int(60 * idx / max(len(categories), 1))
            report(f"Scanning {cat['name']}…", pct)
            for proj in self.drive.list_project_folders(cat["id"]):
                scanned += 1
                package = self.drive.parse_project_folder(proj["id"])
                upsert_drive_project(
                    self._project_row(
                        category=cat["name"],
                        event_name=proj["name"],
                        folder_id=proj["id"],
                        package=package,
                    )
                )
                upserted += 1
                warnings_total += len(package.get("warnings") or [])

        report("Sync complete", 100)
        summary = {
            "master_folder": master,
            "categories_found": len(categories),
            "projects_scanned": scanned,
            "projects_upserted": upserted,
            "warnings": warnings_total,
            "synced_at": _utc_now(),
        }
        logger.info("Drive sync finished: %s", summary)
        return summary

    def list_queue(
        self,
        *,
        status: str | None = "pending_review",
        category: str | None = None,
        queue_name: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = list_drive_projects(
            status=status,
            category=category,
            queue_name=queue_name,
        )
        return [self._enrich_project(p) for p in rows]

    def list_review_for_posting(
        self,
        *,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.list_queue(
            status="review_for_posting",
            queue_name=REVIEW_FOR_POSTING_QUEUE,
            category=category,
        )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = get_drive_project(project_id)
        if not row:
            return None
        enriched = self._enrich_project(row)
        preview_ids = []
        if row.get("cover_drive_id"):
            preview_ids.append(row["cover_drive_id"])
        preview_ids.extend((row.get("carousel_drive_ids") or [])[:4])
        reel_ids = row.get("reel_drive_ids") or []
        if reel_ids:
            preview_ids.append(reel_ids[0])
        enriched["previews"] = {
            "cover": None,
            "carousel": [],
            "reel_sample": None,
        }
        previews = self.drive.get_file_previews(preview_ids)
        by_id = {p["id"]: p for p in previews}
        if row.get("cover_drive_id"):
            enriched["previews"]["cover"] = by_id.get(row["cover_drive_id"])
        enriched["previews"]["carousel"] = [
            by_id[fid] for fid in (row.get("carousel_drive_ids") or [])[:8] if fid in by_id
        ]
        if reel_ids:
            enriched["previews"]["reel_sample"] = by_id.get(reel_ids[0])
        if row.get("status") == "review_for_posting":
            enriched["local_previews"] = self._local_staging_previews(row)
        return enriched

    def materialize_package(
        self,
        row: dict[str, Any],
        *,
        staging: bool = False,
        audio_track_id: str | None = None,
        title_bold: str | None = None,
        title_script: str | None = None,
    ) -> dict[str, Any]:
        """Download Drive assets and build Ideal Row (+ optional staging path)."""
        category = row["category"]
        event_name = row["event_name"]
        cover_id = row.get("cover_drive_id")
        carousel_ids = row.get("carousel_drive_ids") or []
        reel_ids = row.get("reel_drive_ids") or []

        if not cover_id or len(carousel_ids) != POST_2_CAROUSEL_COUNT:
            raise ValueError("Incomplete 3-post package")

        cover_data, _, _ = self.drive.download_bytes(cover_id)
        carousel_data = [self.drive.download_bytes(fid)[0] for fid in carousel_ids]

        reel_images: list[bytes] = []
        reel_videos: list[tuple[bytes, str]] = []
        for fid in reel_ids:
            data, name, mime = self.drive.download_bytes(fid)
            if mime.startswith("video/"):
                reel_videos.append((data, name))
            else:
                reel_images.append(data)

        if not reel_images and not reel_videos:
            raise ValueError("Post 3 needs at least one reel image or video")

        paths = review_for_posting_paths(category, event_name) if staging else ideal_row_paths(category, event_name)
        ideal = IdealRowService()
        effective_audio = audio_track_id or row.get("audio_track_id")
        titles = (
            {"title_bold": title_bold, "title_script": title_script}
            if title_bold or title_script
            else None
        )

        if reel_images:
            result = ideal.save_event_row(
                category=category,
                event_name=event_name,
                post_1_data=cover_data,
                post_2_files=carousel_data,
                post_3_files=reel_images,
                title_bold=title_bold,
                title_script=title_script,
                paths=paths,
                register_event=not staging,
            )
            reel_path = result.post_3["reel_output_path"]
            reel_url = UploadService.media_url_for(Path(reel_path))
            job = montage_jobs.create(
                meta={
                    "image_paths": result.post_3["image_paths"],
                    "motion_style": "auto",
                    "clip_duration_sec": 4.0,
                    "transition_sec": 0.8,
                    "audio_track_id": effective_audio,
                    "category": category,
                    "titles": titles,
                    "output_path": reel_path,
                    "output_url": reel_url,
                    "ideal_row": True,
                    "event_name": event_name,
                    "drive_project_id": row.get("id"),
                    "staging": staging,
                }
            )
            ideal_payload = result.to_dict()
            reel_job_id = job.id
        else:
            post_1 = ideal.save_post_1(paths, cover_data, category)
            post_2 = ideal.save_post_2(
                paths,
                carousel_data,
                category,
                title_bold=title_bold,
                title_script=title_script,
            )
            video_data, video_name = reel_videos[0]
            ext = Path(video_name).suffix or ".mp4"
            reel_path = str(paths["post_3"] / f"reel_drive{ext}")
            Path(reel_path).parent.mkdir(parents=True, exist_ok=True)
            Path(reel_path).write_bytes(video_data)
            reel_url = UploadService.media_url_for(Path(reel_path))
            ideal_payload = {
                "category": category,
                "event_name": event_name,
                "base_path": str(paths["base"]),
                "post_1": post_1,
                "post_2": post_2,
                "post_3": {"reel_output_path": reel_path, "image_paths": []},
            }
            reel_job_id = None

        local_paths = {
            "ideal_row_base": ideal_payload.get("base_path"),
            "reel_output_path": reel_path,
            "reel_output_url": reel_url,
            "post_1_url": ideal_payload.get("post_1", {}).get("url"),
            "carousel_slides": ideal_payload.get("post_2", {}).get("carousel_slides", []),
        }
        return {
            "ideal_row": ideal_payload,
            "reel_job_id": reel_job_id,
            "reel_output_url": reel_url,
            "local_paths": local_paths,
        }

    def set_audio_track(self, project_id: str, track_id: str) -> dict[str, Any]:
        if not get_drive_project(project_id):
            raise ValueError("Project not found")
        update_drive_project(project_id, audio_track_id=track_id)
        return self.get_project(project_id) or {}

    def approve_project(
        self,
        project_id: str,
        *,
        audio_track_id: str | None = None,
        title_bold: str | None = None,
        title_script: str | None = None,
        rerender_reel: bool = False,
    ) -> dict[str, Any]:
        row = get_drive_project(project_id)
        if not row:
            raise ValueError("Project not found")
        if row["status"] == "published":
            raise ValueError("Project already published")

        # Staged batch packages — promote to production and clear backlog
        if row["status"] == "review_for_posting":
            if rerender_reel:
                return self._rerender_staged_reel(row, audio_track_id=audio_track_id)
            return self._approve_staged_project(
                row,
                audio_track_id=audio_track_id,
            )

        category = row["category"]
        event_name = row["event_name"]
        if category not in EVENT_CATEGORIES:
            raise ValueError(f"Invalid category: {category}")

        materialized = self.materialize_package(
            row,
            staging=False,
            audio_track_id=audio_track_id,
            title_bold=title_bold,
            title_script=title_script,
        )
        ideal_payload = materialized["ideal_row"]
        reel_path = materialized["local_paths"]["reel_output_path"]
        reel_url = materialized["reel_output_url"]
        reel_job_id = materialized.get("reel_job_id")
        effective_audio = audio_track_id or row.get("audio_track_id")

        update_drive_project(
            project_id,
            status="approved",
            audio_track_id=effective_audio,
            local_paths=materialized.get("local_paths") or {},
            reel_job_id=reel_job_id,
            queue_name=None,
        )

        out = self.get_project(project_id) or row
        out["ideal_row"] = ideal_payload
        out["reel_job_id"] = reel_job_id
        out["reel_output_url"] = reel_url
        return out

    def _rerender_staged_reel(
        self,
        row: dict[str, Any],
        *,
        audio_track_id: str | None = None,
    ) -> dict[str, Any]:
        project_id = row["id"]
        category = row["category"]
        event_name = row["event_name"]
        effective_audio = audio_track_id or row.get("audio_track_id")

        if effective_audio:
            update_drive_project(project_id, audio_track_id=effective_audio)

        staging_path = row.get("staging_path")
        paths = (
            review_for_posting_paths(category, event_name)
            if not staging_path
            else {"post_3": Path(staging_path) / "Post_3", "base": Path(staging_path)}
        )
        post_3_dir = paths["post_3"]
        image_paths = sorted(
            str(p)
            for p in post_3_dir.glob("reel_*")
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        if not image_paths:
            raise ValueError("No reel frames in staging folder to re-render")

        local_paths = dict(row.get("local_paths") or {})
        reel_path = local_paths.get("reel_output_path") or str(post_3_dir / f"reel_{project_id[:8]}.mp4")
        reel_url = UploadService.media_url_for(Path(reel_path))
        job = montage_jobs.create(
            meta={
                "image_paths": image_paths,
                "motion_style": "auto",
                "clip_duration_sec": 4.0,
                "transition_sec": 0.8,
                "audio_track_id": effective_audio,
                "category": category,
                "output_path": reel_path,
                "output_url": reel_url,
                "ideal_row": True,
                "event_name": event_name,
                "drive_project_id": project_id,
                "staging": True,
            }
        )
        local_paths["reel_output_path"] = reel_path
        local_paths["reel_output_url"] = reel_url
        update_drive_project(project_id, reel_job_id=job.id, local_paths=local_paths)

        out = self.get_project(project_id) or row
        out["reel_job_id"] = job.id
        out["reel_output_url"] = reel_url
        return out

    def _approve_staged_project(
        self,
        row: dict[str, Any],
        *,
        audio_track_id: str | None = None,
    ) -> dict[str, Any]:
        from backend.services.daily_backlog_worker import promote_staging_to_production

        project_id = row["id"]
        category = row["category"]
        event_name = row["event_name"]
        effective_audio = audio_track_id or row.get("audio_track_id")

        if effective_audio and effective_audio != row.get("audio_track_id"):
            update_drive_project(project_id, audio_track_id=effective_audio)

        promoted = promote_staging_to_production(
            category,
            event_name,
            staging_path=row.get("staging_path"),
        )

        reel_job_id = row.get("reel_job_id")
        local_paths = dict(row.get("local_paths") or {})
        local_paths["production_path"] = promoted["production_path"]

        update_drive_project(
            project_id,
            status="approved",
            queue_name=None,
            local_paths=local_paths,
            reel_job_id=reel_job_id,
        )

        prod_paths = ideal_row_paths(category, event_name)
        ideal_payload = {
            "category": category,
            "event_name": event_name,
            "base_path": str(prod_paths["base"]),
            "post_1": {"url": promoted.get("post_1_url"), "path": str(prod_paths["post_1"])},
            "post_2": row.get("local_paths", {}).get("carousel_slides") and {
                "carousel_slides": row["local_paths"].get("carousel_slides"),
            } or {},
            "post_3": {
                "reel_output_path": local_paths.get("reel_output_path"),
                "image_paths": [],
            },
        }

        out = self.get_project(project_id) or row
        out["ideal_row"] = ideal_payload
        out["reel_job_id"] = reel_job_id
        out["reel_output_url"] = local_paths.get("reel_output_url")
        out["promoted"] = promoted
        return out

    def _project_row(
        self,
        *,
        category: str,
        event_name: str,
        folder_id: str,
        package: dict[str, Any],
    ) -> dict[str, Any]:
        existing = get_drive_project_by_folder(folder_id)
        now = _utc_now()
        keep_status = existing and existing["status"] in (
            "approved",
            "published",
            "review_for_posting",
            "processing",
        )
        return {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "drive_folder_id": folder_id,
            "category": category,
            "event_name": event_name,
            "status": existing["status"] if keep_status else "pending_review",
            "cover_drive_id": package.get("cover_drive_id"),
            "carousel_drive_ids": package.get("carousel_drive_ids") or [],
            "reel_drive_ids": package.get("reel_drive_ids") or [],
            "asset_count": package.get("asset_count") or 0,
            "parse_warnings": package.get("warnings") or [],
            "audio_track_id": existing.get("audio_track_id") if existing else None,
            "local_paths": existing.get("local_paths") if existing else {},
            "reel_job_id": existing.get("reel_job_id") if existing else None,
            "synced_at": now,
            "created_at": existing.get("created_at") if existing else now,
        }

    def _enrich_project(self, row: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        enriched["package_complete"] = bool(
            row.get("cover_drive_id")
            and len(row.get("carousel_drive_ids") or []) == DRIVE_POST_2_COUNT
            and (row.get("reel_drive_ids") or [])
        )
        enriched["queue_label"] = row.get("queue_name") or (
            REVIEW_FOR_POSTING_QUEUE if row.get("status") == "review_for_posting" else None
        )
        if row.get("status") == "review_for_posting":
            enriched["local_previews"] = self._local_staging_previews(row)
        return enriched

    def _local_staging_previews(self, row: dict[str, Any]) -> dict[str, Any]:
        local = row.get("local_paths") or {}
        staging = row.get("staging_path")
        previews: dict[str, Any] = {"cover_url": local.get("post_1_url"), "carousel": []}
        if local.get("carousel_slides"):
            previews["carousel"] = local["carousel_slides"]
        elif staging:
            base = Path(staging)
            cover = next(base.joinpath("Post_1").glob("cover*"), None)
            if cover and cover.is_file():
                previews["cover_url"] = UploadService.media_url_for(cover)
            carousel_dir = base / "Post_2" / "carousel"
            if carousel_dir.is_dir():
                previews["carousel"] = [
                    UploadService.media_url_for(p)
                    for p in sorted(carousel_dir.glob("*"))
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ]
        return previews
