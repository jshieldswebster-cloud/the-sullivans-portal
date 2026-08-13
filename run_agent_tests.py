#!/usr/bin/env python3
"""Continuous local runner for the manual Drive-folder → 3-post pipeline.

Boots studio backend services in-process (isolated SQLite + staging dirs),
simulates Browse Drive / Process Folder, then asserts generated post files and
carousel preview assets exist and match the programmed fixture content.

Retries automatically on failure. Exits 0 only after the target post is fully
verified.

Run:
  source .venv/bin/activate && python run_agent_tests.py
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from backend.config import (  # noqa: E402
    CAROUSEL_HEIGHT,
    CAROUSEL_WIDTH,
    STUDIO_PASSWORD,
    STUDIO_SECRET_KEY,
    STUDIO_USERNAME,
)
from backend.services.ideal_row_service import POST_2_CAROUSEL_COUNT, event_slug  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent_tests")

# ── Programmed target post ───────────────────────────────────────────────────

FOLDER_ID = "agent-test-folder"
EVENT_NAME = "Agent Test Wedding"
CATEGORY = "Weddings"
COVER_ID = "drive-cover"
CAROUSEL_IDS = [f"drive-car-{i}" for i in range(POST_2_CAROUSEL_COUNT)]
REEL_IDS = ["drive-reel-0"]

# Distinct solid colors so preview slides can be traced back to fixtures.
COVER_COLOR = (180, 40, 80)
CAROUSEL_COLORS = [
    (220, 30, 30),
    (30, 160, 50),
    (30, 70, 210),
    (210, 160, 20),
    (140, 40, 180),
    (20, 160, 170),
    (210, 90, 20),
    (80, 80, 80),
]
REEL_COLOR = (20, 80, 160)

JPEG_SIZE = (640, 800)


class VerificationError(RuntimeError):
    """Raised when generated assets are missing or do not match fixtures."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_programmed_jpeg(label: str, color: tuple[int, int, int]) -> bytes:
    """Build a unique JPEG whose bytes are the source of truth for assertions."""
    img = Image.new("RGB", JPEG_SIZE, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((8, 8, JPEG_SIZE[0] - 9, JPEG_SIZE[1] - 9), outline=(255, 255, 255), width=4)
    draw.text((24, 24), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def build_programmed_files() -> dict[str, tuple[bytes, str, str]]:
    files: dict[str, tuple[bytes, str, str]] = {
        COVER_ID: (make_programmed_jpeg("COVER", COVER_COLOR), "cover.jpg", "image/jpeg"),
    }
    for i, file_id in enumerate(CAROUSEL_IDS):
        files[file_id] = (
            make_programmed_jpeg(f"CAROUSEL-{i + 1}", CAROUSEL_COLORS[i]),
            f"carousel_{i + 1:02d}.jpg",
            "image/jpeg",
        )
    files[REEL_IDS[0]] = (
        make_programmed_jpeg("REEL-1", REEL_COLOR),
        "reel_01.jpg",
        "image/jpeg",
    )
    return files


PROGRAMMED_FILES = build_programmed_files()


class ProgrammedDrive:
    """In-memory Drive stand-in with a complete 3-post package."""

    def __init__(self) -> None:
        self._files = PROGRAMMED_FILES

    def is_configured(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return True

    def resolve_event_context(self, folder_id: str) -> dict[str, Any]:
        folder_id = (folder_id or "").strip()
        if folder_id != FOLDER_ID:
            raise FileNotFoundError(f"Google Drive folder not found: {folder_id}")
        return {
            "id": FOLDER_ID,
            "name": EVENT_NAME,
            "category": CATEGORY,
            "mime_type": "application/vnd.google-apps.folder",
        }

    def parse_project_folder(self, folder_id: str) -> dict[str, Any]:
        folder_id = (folder_id or "").strip()
        if folder_id != FOLDER_ID:
            return {
                "cover_drive_id": None,
                "carousel_drive_ids": [],
                "reel_drive_ids": [],
                "asset_count": 0,
                "image_count": 0,
                "video_count": 0,
                "warnings": [f"Unknown folder: {folder_id}"],
            }
        return {
            "cover_drive_id": COVER_ID,
            "carousel_drive_ids": list(CAROUSEL_IDS),
            "reel_drive_ids": list(REEL_IDS),
            "asset_count": 1 + len(CAROUSEL_IDS) + len(REEL_IDS),
            "image_count": 1 + len(CAROUSEL_IDS) + len(REEL_IDS),
            "video_count": 0,
            "warnings": [],
        }

    def download_bytes(self, file_id: str) -> tuple[bytes, str, str]:
        if file_id not in self._files:
            raise FileNotFoundError(f"Programmed Drive file not found: {file_id}")
        return self._files[file_id]

    def get_file_previews(self, file_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fid in file_ids or []:
            if fid not in self._files:
                continue
            _, name, mime = self._files[fid]
            out.append(
                {
                    "id": fid,
                    "name": name,
                    "mime_type": mime,
                    "thumbnail_link": f"https://agent.test/thumbs/{fid}.jpg",
                }
            )
        return out


def isolate_runtime(run_root: Path) -> Path:
    """Point config + database at an isolated tree so production data is untouched."""
    import backend.config as cfg
    import backend.database as database
    import backend.services.daily_backlog_worker as daily_backlog_worker
    import backend.services.ideal_row_service as ideal_row_service

    data_dir = run_root / "data"
    uploads = run_root / "uploads"
    review_dir = uploads / "Review_for_Posting"
    db_path = data_dir / "agent_test.db"

    cfg.DATA_DIR = data_dir
    cfg.UPLOADS_DIR = uploads
    cfg.REVIEW_FOR_POSTING_DIR = review_dir
    cfg.DATABASE_PATH = db_path
    cfg.VIDEOS_DIR = run_root / "output" / "videos"
    cfg.CAROUSELS_DIR = run_root / "output" / "carousels"
    cfg.CAPTIONS_DIR = run_root / "output" / "captions"
    cfg.DEBUG_DIR = run_root / "output" / "debug"

    database.DATABASE_PATH = db_path
    daily_backlog_worker.REVIEW_FOR_POSTING_DIR = review_dir
    ideal_row_service.UPLOADS_DIR = uploads

    data_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    return review_dir


def install_fake_drive() -> None:
    import backend.services.daily_backlog_worker as daily_backlog_worker
    import backend.services.drive_sync_service as drive_sync_service
    import backend.services.drive_service as drive_service

    daily_backlog_worker.DriveService = ProgrammedDrive  # type: ignore[misc, assignment]
    drive_sync_service.DriveService = ProgrammedDrive  # type: ignore[misc, assignment]
    drive_service.DriveService = ProgrammedDrive  # type: ignore[misc, assignment]


def stub_montage_job(store: Any, job_id: str) -> None:
    """Complete reel encode without ffmpeg/torch so the runner stays local and fast."""
    job = store.get(job_id)
    meta = (job.meta if job else None) or {}
    out = meta.get("output_path")
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"ftypisom")
    store.update(job_id, status="completed", progress=100, message="agent-test montage stub")


def register_job_handlers() -> None:
    from backend.services.daily_backlog_worker import run_daily_backlog_job
    from backend.services.job_queue import job_manager

    job_manager.register("daily_backlog", run_daily_backlog_job)
    job_manager.register("montage", stub_montage_job)
    job_manager.register("zip_export", lambda store, job_id: store.update(job_id, status="completed", progress=100))
    job_manager.register("drive_sync", lambda store, job_id: store.update(job_id, status="completed", progress=100))


def boot_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    from backend.database import init_db
    from backend.routers import studio as studio_router

    init_db()
    register_job_handlers()

    app = FastAPI(title="VV Luxe Agent Test")
    app.add_middleware(SessionMiddleware, secret_key=STUDIO_SECRET_KEY, max_age=86400)
    app.include_router(studio_router.router)
    app.include_router(studio_router.api)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    login = client.post(
        "/login",
        data={"username": STUDIO_USERNAME, "password": STUDIO_PASSWORD},
        follow_redirects=False,
    )
    if login.status_code not in (200, 302, 303):
        raise VerificationError(f"Login failed: {login.status_code} {login.text}")
    log.info("Backend services up (studio API + job queue). Logged in as %s.", STUDIO_USERNAME)
    return client


def wait_for_job(client, job_id: str, *, timeout_sec: float) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"/api/studio/jobs/{job_id}")
        if resp.status_code != 200:
            raise VerificationError(f"Job poll failed: {resp.status_code} {resp.text}")
        last = resp.json()
        status = last.get("status")
        if status == "completed":
            return last
        if status == "failed":
            raise VerificationError(
                f"Pipeline job failed: {last.get('error') or last.get('message')}"
            )
        time.sleep(0.2)
    raise VerificationError(
        f"Timed out waiting for job {job_id[:8]} (last status={last.get('status')!r})"
    )


def simulate_manual_folder_select(client, *, timeout_sec: float) -> dict[str, Any]:
    """POST the same endpoint the dashboard Process Folder button uses."""
    resp = client.post(
        "/api/studio/backlog/process-folder",
        json={"folder_id": FOLDER_ID, "force": True},
    )
    if resp.status_code != 200:
        raise VerificationError(
            f"process-folder HTTP {resp.status_code}: {resp.text}"
        )
    payload = resp.json()
    job_id = payload.get("job_id")
    if not job_id:
        raise VerificationError(f"process-folder did not return job_id: {payload}")
    log.info("Queued process-folder job %s for folder %s", job_id[:8], FOLDER_ID)
    job = wait_for_job(client, job_id, timeout_sec=timeout_sec)
    log.info("process-folder completed: %s", job.get("message"))
    return job


def _assert_exact_file(path: Path, expected: bytes, label: str) -> None:
    if not path.is_file():
        raise VerificationError(f"{label} missing: {path}")
    actual = path.read_bytes()
    if actual != expected:
        raise VerificationError(
            f"{label} bytes do not match programmed content "
            f"(got sha256={_sha256(actual)[:12]} expected={_sha256(expected)[:12]}) at {path}"
        )
    log.info("OK  %s matches programmed bytes (%d)", label, len(expected))


def _assert_jpeg(path: Path, *, width: int, height: int, label: str) -> Image.Image:
    if not path.is_file():
        raise VerificationError(f"{label} missing: {path}")
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        if rgb.size != (width, height):
            raise VerificationError(
                f"{label} size {rgb.size} != {(width, height)} at {path}"
            )
        loaded = rgb.copy()
    log.info("OK  %s exists (%dx%d)", label, width, height)
    return loaded


def _center_color(img: Image.Image) -> tuple[int, int, int]:
    x, y = img.size[0] // 2, img.size[1] // 2
    pixel = img.getpixel((x, y))
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def _colors_close(
    actual: tuple[int, int, int],
    expected: tuple[int, int, int],
    *,
    tol: int = 28,
) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def verify_generated_post(client) -> None:
    from backend.config import REVIEW_FOR_POSTING_DIR, category_slug
    from backend.database import get_drive_project_by_folder
    from backend.services.ideal_row_service import review_for_posting_paths

    paths = review_for_posting_paths(CATEGORY, EVENT_NAME)
    log.info("Verifying staging tree at %s", paths["base"])

    if not paths["base"].is_dir():
        raise VerificationError(f"Staging folder missing: {paths['base']}")

    expected_root = (
        REVIEW_FOR_POSTING_DIR
        / category_slug(CATEGORY)
        / event_slug(EVENT_NAME)
        / "Ideal_Row_Posts"
    )
    if paths["base"] != expected_root:
        raise VerificationError(f"Unexpected staging path {paths['base']} != {expected_root}")

    cover = paths["post_1"] / "cover.jpg"
    _assert_exact_file(cover, PROGRAMMED_FILES[COVER_ID][0], "Post 1 cover")

    for i, file_id in enumerate(CAROUSEL_IDS, start=1):
        photo = paths["post_2"] / f"photo_{i:02d}.jpg"
        _assert_exact_file(photo, PROGRAMMED_FILES[file_id][0], f"Post 2 photo_{i:02d}")

    carousel_dir = paths["post_2_carousel"]
    title = _assert_jpeg(
        carousel_dir / "slide_00_title.jpg",
        width=CAROUSEL_WIDTH,
        height=CAROUSEL_HEIGHT,
        label="Carousel title preview",
    )
    if title.getpixel((CAROUSEL_WIDTH // 2, CAROUSEL_HEIGHT // 2 - 80)) == (255, 255, 255):
        raise VerificationError("Carousel title slide looks blank")

    for i, color in enumerate(CAROUSEL_COLORS, start=1):
        slide = _assert_jpeg(
            carousel_dir / f"slide_{i:02d}.jpg",
            width=CAROUSEL_WIDTH,
            height=CAROUSEL_HEIGHT,
            label=f"Carousel preview slide_{i:02d}",
        )
        center = _center_color(slide)
        if not _colors_close(center, color):
            raise VerificationError(
                f"Carousel slide_{i:02d} center {center} does not match programmed color {color}"
            )
        log.info("OK  Carousel slide_%02d color matches programmed fixture", i)

    reel = paths["post_3"] / "reel_01.jpg"
    _assert_exact_file(reel, PROGRAMMED_FILES[REEL_IDS[0]][0], "Post 3 reel_01")

    row = get_drive_project_by_folder(FOLDER_ID)
    if not row:
        raise VerificationError("Drive project row missing after pipeline")
    if row.get("status") != "review_for_posting":
        raise VerificationError(f"Project status {row.get('status')!r} != 'review_for_posting'")
    if row.get("event_name") != EVENT_NAME or row.get("category") != CATEGORY:
        raise VerificationError(
            f"Project identity mismatch: {row.get('category')} / {row.get('event_name')}"
        )
    if row.get("cover_drive_id") != COVER_ID:
        raise VerificationError("Project cover_drive_id does not match programmed cover")
    if list(row.get("carousel_drive_ids") or []) != list(CAROUSEL_IDS):
        raise VerificationError("Project carousel_drive_ids do not match programmed set")
    if list(row.get("reel_drive_ids") or []) != list(REEL_IDS):
        raise VerificationError("Project reel_drive_ids do not match programmed set")
    log.info("OK  DB project %s is review_for_posting", row["id"][:8])

    listing = client.get(
        "/api/studio/drive/projects",
        params={"queue": "review_for_posting"},
    )
    if listing.status_code != 200:
        raise VerificationError(f"drive/projects HTTP {listing.status_code}: {listing.text}")
    projects = listing.json().get("projects") or []
    match = next((p for p in projects if p.get("id") == row["id"]), None)
    if not match:
        raise VerificationError("Staged project not listed in review_for_posting queue")

    detail = client.get(f"/api/studio/drive/projects/{row['id']}")
    if detail.status_code != 200:
        raise VerificationError(f"drive/projects/{{id}} HTTP {detail.status_code}: {detail.text}")
    body = detail.json()
    local = body.get("local_previews") or {}
    if not local.get("cover_url"):
        raise VerificationError("API local_previews.cover_url missing")
    carousel_urls = local.get("carousel") or []
    if len(carousel_urls) < 1 + POST_2_CAROUSEL_COUNT:
        raise VerificationError(
            f"API local_previews.carousel expected >= {1 + POST_2_CAROUSEL_COUNT}, got {len(carousel_urls)}"
        )
    log.info("OK  API local_previews cover + %d carousel slides", len(carousel_urls))


def reset_target_staging() -> None:
    """Clear leftover files from a failed attempt so verification cannot pass on stale output."""
    from backend.services.ideal_row_service import review_for_posting_paths

    base = review_for_posting_paths(CATEGORY, EVENT_NAME)["base"]
    if base.exists():
        shutil.rmtree(base)


def run_attempt(client, *, timeout_sec: float) -> None:
    reset_target_staging()
    simulate_manual_folder_select(client, timeout_sec=timeout_sec)
    verify_generated_post(client)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous local 3-post pipeline verifier")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Single attempt (still exits 0 only on full verification)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Stop after N failures (0 = retry forever until success)",
    )
    parser.add_argument("--retry-sec", type=float, default=2.0, help="Pause between failed attempts")
    parser.add_argument("--job-timeout", type=float, default=120.0, help="Seconds to wait for process-folder")
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Write isolated run files under .agent_test_run/ instead of a temp dir",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    max_attempts = 1 if args.once else max(0, args.max_attempts)

    if args.keep_artifacts:
        run_root = ROOT / ".agent_test_run"
        if run_root.exists():
            shutil.rmtree(run_root)
        run_root.mkdir(parents=True, exist_ok=True)
        tmp: tempfile.TemporaryDirectory | None = None
    else:
        tmp = tempfile.TemporaryDirectory(prefix="vv-agent-tests-")
        run_root = Path(tmp.name)

    isolate_runtime(run_root)
    install_fake_drive()

    attempt = 0
    client = None
    try:
        while True:
            attempt += 1
            log.info("── Attempt %d: process folder %s (%s / %s)", attempt, FOLDER_ID, CATEGORY, EVENT_NAME)
            try:
                if client is None:
                    client = boot_app()
                run_attempt(client, timeout_sec=args.job_timeout)
                log.info("SUCCESS — target post fully verified after %d attempt(s).", attempt)
                return 0
            except Exception as exc:
                log.exception("Attempt %d failed: %s", attempt, exc)
                if max_attempts and attempt >= max_attempts:
                    log.error("Giving up after %d attempt(s).", attempt)
                    return 1
                log.info("Retrying in %.1fs…", args.retry_sec)
                time.sleep(args.retry_sec)
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
