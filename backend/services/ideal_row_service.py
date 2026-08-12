"""Ideal Row content strategy — three-post event package storage and generation."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import UPLOADS_DIR, category_slug
from backend.database import insert_image
from backend.models.carousel_compositor import CarouselCompositor
from backend.services.upload_service import UploadService

logger = logging.getLogger(__name__)

POST_1_DIR = "Post_1"
POST_2_DIR = "Post_2"
POST_3_DIR = "Post_3"
IDEAL_ROW_ROOT = "Ideal_Row_Posts"
POST_2_CAROUSEL_COUNT = 8


def event_slug(name: str) -> str:
    """Convert event name to folder slug, e.g. 'The Johnson Wedding' → 'The_Johnson_Wedding'."""
    cleaned = re.sub(r"[^\w\s-]", "", name.strip())
    cleaned = re.sub(r"[\s_-]+", "_", cleaned)
    parts = [p.capitalize() for p in cleaned.split("_") if p]
    return "_".join(parts) if parts else "Untitled_Event"


def ideal_row_paths(category: str, event_name: str, *, base_root: Path | None = None) -> dict[str, Path]:
    """Return Post_1 / Post_2 / Post_3 directory paths for an event row."""
    root = base_root if base_root is not None else UPLOADS_DIR
    base = (
        root
        / category_slug(category)
        / event_slug(event_name)
        / IDEAL_ROW_ROOT
    )
    return {
        "base": base,
        "post_1": base / POST_1_DIR,
        "post_2": base / POST_2_DIR,
        "post_3": base / POST_3_DIR,
        "post_2_carousel": base / POST_2_DIR / "carousel",
    }


def review_for_posting_paths(category: str, event_name: str) -> dict[str, Path]:
    """Staging paths under uploads/Review_for_Posting/."""
    from backend.config import REVIEW_FOR_POSTING_DIR

    return ideal_row_paths(category, event_name, base_root=REVIEW_FOR_POSTING_DIR)


def media_url(filepath: Path) -> str:
    return UploadService.media_url_for(filepath)


@dataclass
class IdealRowSaveResult:
    category: str
    event_name: str
    event_slug: str
    base_path: str
    post_1: dict[str, Any]
    post_2: dict[str, Any]
    post_3: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "event_name": self.event_name,
            "event_slug": self.event_slug,
            "base_path": self.base_path,
            "post_1": self.post_1,
            "post_2": self.post_2,
            "post_3": self.post_3,
        }


class IdealRowService:
    """Persist and generate the three-post Ideal Row package."""

    ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

    def _save_file(self, dest_dir: Path, data: bytes, *, prefix: str, index: int = 0) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = ".jpg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:2] == b"\xff\xd8":
            ext = ".jpg"
        name = f"{prefix}{index:02d}{ext}" if index else f"{prefix}{ext}"
        dest = dest_dir / name
        dest.write_bytes(data)
        return dest

    def save_post_1(self, paths: dict[str, Path], data: bytes, category: str) -> dict[str, Any]:
        dest = self._save_file(paths["post_1"], data, prefix="cover")
        insert_image(
            filename=dest.name,
            filepath=str(dest),
            categories=[category],
            primary_category=category,
            confidence=1.0,
            metadata={"ideal_row": "post_1"},
        )
        return {"path": str(dest), "url": media_url(dest), "filename": dest.name}

    def save_post_2(
        self,
        paths: dict[str, Path],
        files: list[bytes],
        category: str,
        *,
        title_bold: str | None = None,
        title_script: str | None = None,
    ) -> dict[str, Any]:
        if len(files) != POST_2_CAROUSEL_COUNT:
            raise ValueError(f"Post 2 requires exactly {POST_2_CAROUSEL_COUNT} photos")

        saved_paths: list[Path] = []
        for idx, data in enumerate(files, start=1):
            dest = self._save_file(paths["post_2"], data, prefix="photo_", index=idx)
            saved_paths.append(dest)
            insert_image(
                filename=dest.name,
                filepath=str(dest),
                categories=[category],
                primary_category=category,
                confidence=1.0,
                metadata={"ideal_row": "post_2", "slide": idx},
            )

        carousel_dir = paths["post_2_carousel"]
        compositor = CarouselCompositor()
        slides = compositor.build_carousel(
            saved_paths,
            category,
            output_dir=carousel_dir,
            title_bold=title_bold,
            title_script=title_script,
        )
        slide_urls = [media_url(s) for s in slides]

        return {
            "photos": [
                {"path": str(p), "url": media_url(p), "filename": p.name}
                for p in saved_paths
            ],
            "carousel_slides": slide_urls,
            "slide_count": len(slide_urls),
        }

    def save_post_3(
        self,
        paths: dict[str, Path],
        files: list[bytes],
        category: str,
    ) -> dict[str, Any]:
        if not files:
            raise ValueError("Post 3 requires at least one photo for the reel")

        saved_paths: list[Path] = []
        for idx, data in enumerate(files, start=1):
            dest = self._save_file(paths["post_3"], data, prefix="reel_", index=idx)
            saved_paths.append(dest)
            insert_image(
                filename=dest.name,
                filepath=str(dest),
                categories=[category],
                primary_category=category,
                confidence=1.0,
                metadata={"ideal_row": "post_3", "reel_frame": idx},
            )

        reel_output = paths["post_3"] / f"reel_{uuid.uuid4().hex[:8]}.mp4"
        return {
            "photos": [
                {"path": str(p), "url": media_url(p), "filename": p.name}
                for p in saved_paths
            ],
            "image_paths": [str(p) for p in saved_paths],
            "reel_output_path": str(reel_output),
        }

    def save_event_row(
        self,
        *,
        category: str,
        event_name: str,
        post_1_data: bytes,
        post_2_files: list[bytes],
        post_3_files: list[bytes],
        title_bold: str | None = None,
        title_script: str | None = None,
        paths: dict[str, Path] | None = None,
        register_event: bool = True,
    ) -> IdealRowSaveResult:
        if not event_name.strip():
            raise ValueError("Event name is required")
        if not post_1_data:
            raise ValueError("Post 1 cover image is required")
        if len(post_2_files) != POST_2_CAROUSEL_COUNT:
            raise ValueError(f"Post 2 requires exactly {POST_2_CAROUSEL_COUNT} photos")
        if not post_3_files:
            raise ValueError("Post 3 requires at least one reel photo")

        slug = event_slug(event_name)
        row_paths = paths or ideal_row_paths(category, event_name)

        post_1 = self.save_post_1(row_paths, post_1_data, category)
        post_2 = self.save_post_2(
            row_paths,
            post_2_files,
            category,
            title_bold=title_bold,
            title_script=title_script,
        )
        post_3 = self.save_post_3(row_paths, post_3_files, category)

        if register_event:
            try:
                from backend.services.studio_state_service import StudioStateService

                StudioStateService().register_ideal_row_event(
                    category=category,
                    event_name=event_name.strip(),
                    ideal_row_path=str(row_paths["base"]),
                    metadata={"post_1": post_1.get("filename"), "post_3_count": len(post_3.get("image_paths", []))},
                )
            except Exception as exc:
                logger.warning("Could not register event in studio state DB: %s", exc)

        logger.info("Ideal Row saved → %s", row_paths["base"])
        return IdealRowSaveResult(
            category=category,
            event_name=event_name.strip(),
            event_slug=slug,
            base_path=str(row_paths["base"]),
            post_1=post_1,
            post_2=post_2,
            post_3=post_3,
        )
