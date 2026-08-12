"""Instagram Export Package — strict 3-post binding, captions, structured folders."""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from backend.config import CAROUSEL_HEIGHT, CAROUSEL_WIDTH, REEL_HEIGHT, REEL_WIDTH
from backend.services.ideal_row_service import (
    POST_2_CAROUSEL_COUNT,
    ideal_row_paths,
    media_url,
)
from backend.services.instagram_captions import (
    PostCaption,
    generate_ideal_row_captions,
    write_caption_files,
)
from backend.services.logo_overlay import resolve_logo_path

logger = logging.getLogger(__name__)

INSTAGRAM_EXPORT_DIR = "Instagram_Export"
POST_1_EXPORT = "Post_1"
POST_2_EXPORT = "Post_2"
POST_3_EXPORT = "Post_3"
MANIFEST_FILENAME = "manifest.json"

# Strict Ideal Row asset roles
POST_1_ROLE = "event_type_cover"       # exactly 1 image
POST_2_ROLE = "details_decor_carousel"  # exactly 8 images → 1.jpg … 8.jpg
POST_3_ROLE = "branded_reel"            # 1 hardware-encoded MP4 + logo overlay

IG_COVER_WIDTH = CAROUSEL_WIDTH
IG_COVER_HEIGHT = CAROUSEL_HEIGHT
IG_JPEG_QUALITY = 92


@dataclass
class RowAssetBundle:
    """Validated source assets bound to Ideal Row post roles."""

    post_1_cover: Path
    post_2_photos: list[Path]
    post_3_reel: Path

    def validate(self) -> None:
        if not self.post_1_cover.is_file():
            raise FileNotFoundError("Post 1: Event Type Cover Image is missing")
        if len(self.post_2_photos) != POST_2_CAROUSEL_COUNT:
            raise FileNotFoundError(
                f"Post 2: requires exactly {POST_2_CAROUSEL_COUNT} Details & Decor photos "
                f"(found {len(self.post_2_photos)})"
            )
        if not self.post_3_reel.is_file() or self.post_3_reel.suffix.lower() != ".mp4":
            raise FileNotFoundError(
                "Post 3: branded reel MP4 not ready — wait for VideoToolbox render with logo overlay"
            )


@dataclass
class InstagramExportResult:
    category: str
    event_name: str
    export_base: str
    post_1: dict[str, Any]
    post_2: dict[str, Any]
    post_3: dict[str, Any]
    captions: dict[str, Any] = field(default_factory=dict)
    manifest_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "event_name": self.event_name,
            "export_base": self.export_base,
            "post_1": self.post_1,
            "post_2": self.post_2,
            "post_3": self.post_3,
            "captions": self.captions,
            "manifest_url": self.manifest_url,
        }


class InstagramExportService:
    """Build downloadable, caption-ready Instagram packages from a saved Ideal Row."""

    ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

    def export_paths(self, category: str, event_name: str) -> dict[str, Path]:
        row = ideal_row_paths(category, event_name)
        export_base = row["base"] / INSTAGRAM_EXPORT_DIR
        return {
            "export_base": export_base,
            "post_1": export_base / POST_1_EXPORT,
            "post_2": export_base / POST_2_EXPORT,
            "post_3": export_base / POST_3_EXPORT,
            "row_post_1": row["post_1"],
            "row_post_2": row["post_2"],
            "row_post_3": row["post_3"],
        }

    def bind_row_assets(
        self,
        category: str,
        event_name: str,
        *,
        reel_path: str | Path | None = None,
    ) -> RowAssetBundle:
        """Map saved Ideal Row folders to strict Post 1 / 2 / 3 assets."""
        paths = self.export_paths(category, event_name)

        cover = self._find_cover_source(paths["row_post_1"])
        if not cover:
            raise FileNotFoundError(
                "Post 1 (Event Type Cover): no cover image in Ideal_Row_Posts/Post_1/"
            )

        carousel = self._find_carousel_photos(paths["row_post_2"])
        if len(carousel) != POST_2_CAROUSEL_COUNT:
            raise FileNotFoundError(
                f"Post 2 (Details & Decor): need exactly {POST_2_CAROUSEL_COUNT} photos "
                f"in Ideal_Row_Posts/Post_2/ (found {len(carousel)})"
            )

        reel = Path(reel_path) if reel_path else self._find_reel_source(paths["row_post_3"])
        if not reel or not reel.is_file():
            raise FileNotFoundError(
                "Post 3 (Branded Reel): MP4 not found — montage must complete with logo overlay"
            )

        bundle = RowAssetBundle(
            post_1_cover=cover,
            post_2_photos=carousel,
            post_3_reel=reel,
        )
        bundle.validate()
        return bundle

    def _find_cover_source(self, row_post_1: Path) -> Path | None:
        for pattern in ("cover.*", "cover_*.*"):
            matches = sorted(row_post_1.glob(pattern))
            if matches:
                return matches[0]
        images = sorted(
            p for p in row_post_1.iterdir()
            if p.is_file() and p.suffix.lower() in self.ALLOWED_IMAGE_EXT
        )
        return images[0] if len(images) == 1 else (images[0] if images else None)

    def _find_carousel_photos(self, row_post_2: Path) -> list[Path]:
        photos = sorted(row_post_2.glob("photo_*.jpg")) + sorted(row_post_2.glob("photo_*.png"))
        if len(photos) >= POST_2_CAROUSEL_COUNT:
            return photos[:POST_2_CAROUSEL_COUNT]
        fallback = sorted(
            p for p in row_post_2.iterdir()
            if p.is_file()
            and p.suffix.lower() in self.ALLOWED_IMAGE_EXT
            and p.parent.name != "carousel"
        )
        return fallback[:POST_2_CAROUSEL_COUNT]

    def _find_reel_source(self, row_post_3: Path) -> Path | None:
        reels = sorted(row_post_3.glob("reel*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if reels:
            return reels[0]
        any_mp4 = sorted(row_post_3.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        return any_mp4[0] if any_mp4 else None

    def _fit_for_instagram(self, source: Path) -> Image.Image:
        img = Image.open(source).convert("RGB")
        return ImageOps.fit(
            img,
            (IG_COVER_WIDTH, IG_COVER_HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    def export_post_1_cover(self, source: Path, dest_dir: Path) -> Path:
        """Post 1: single optimized Event Type Cover JPEG."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "cover_instagram.jpg"
        fitted = self._fit_for_instagram(source)
        fitted.save(out, "JPEG", quality=IG_JPEG_QUALITY, optimize=True, subsampling=0)
        logger.info("Post 1 export → %s", out)
        return out

    def export_post_2_carousel(
        self,
        photo_paths: list[Path],
        dest_dir: Path,
    ) -> tuple[Path, list[Path]]:
        """
        Post 2: exactly 8 sequentially named JPEGs (1.jpg … 8.jpg) + carousel.zip.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        numbered: list[Path] = []

        for idx, photo in enumerate(photo_paths[:POST_2_CAROUSEL_COUNT], start=1):
            fitted = self._fit_for_instagram(photo)
            out = dest_dir / f"{idx}.jpg"
            fitted.save(out, "JPEG", quality=IG_JPEG_QUALITY, optimize=True)
            numbered.append(out)

        zip_path = dest_dir / "carousel.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in numbered:
                zf.write(path, arcname=path.name)

        logger.info("Post 2 export → %d photos + %s", len(numbered), zip_path.name)
        return zip_path, numbered

    def export_post_3_reel(self, source: Path, dest_dir: Path) -> Path:
        """Post 3: branded hardware-encoded reel (logo applied during montage)."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "reel.mp4"
        shutil.copy2(source, out)
        logger.info("Post 3 export → %s (%dx%d, logo overlay via montage pipeline)", out, REEL_WIDTH, REEL_HEIGHT)
        return out

    def bundle_post_folder(self, post_dir: Path, zip_name: str) -> Path:
        """Zip all files in a post export folder for one-click download."""
        post_dir.mkdir(parents=True, exist_ok=True)
        zip_path = post_dir / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(post_dir.iterdir()):
                if file.is_file() and file.name != zip_name:
                    zf.write(file, arcname=file.name)
        logger.info("Post bundle → %s", zip_path)
        return zip_path

    def _logo_metadata(self) -> dict[str, Any]:
        logo = resolve_logo_path()
        return {
            "logo_path": str(logo) if logo else None,
            "logo_source": "assets/logos/",
            "logo_applied_in_pipeline": True,
        }

    def _attach_caption_meta(
        self,
        post_meta: dict[str, Any],
        caption: PostCaption,
        caption_paths: dict[str, str],
        folder: Path,
    ) -> dict[str, Any]:
        post_meta["folder"] = str(folder)
        post_meta["caption"] = caption.to_dict()
        post_meta["caption_url"] = media_url(Path(caption_paths["caption_instagram_txt"]))
        post_meta["caption_download_url"] = media_url(Path(caption_paths["caption_instagram_txt"]))
        post_meta["caption_typography_url"] = media_url(Path(caption_paths["caption_typography_html"]))
        return post_meta

    def _write_manifest(
        self,
        export_base: Path,
        category: str,
        event_name: str,
        post_1: dict[str, Any],
        post_2: dict[str, Any],
        post_3: dict[str, Any],
    ) -> Path:
        manifest = {
            "event_name": event_name,
            "category": category,
            "ideal_row_posts": {
                "post_1": {"role": POST_1_ROLE, "asset": "cover_instagram.jpg", **post_1},
                "post_2": {"role": POST_2_ROLE, "assets": "1.jpg–8.jpg + carousel.zip", **post_2},
                "post_3": {"role": POST_3_ROLE, "asset": "reel.mp4", "logo": "assets/logos/", **post_3},
            },
        }
        path = export_base / MANIFEST_FILENAME
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    async def prepare_package(
        self,
        category: str,
        event_name: str,
        *,
        reel_path: str | Path | None = None,
        caption_engine: Any | None = None,
        visual_context: str | None = None,
    ) -> InstagramExportResult:
        """Full automated export: bind assets, package files, generate captions."""
        paths = self.export_paths(category, event_name)
        paths["export_base"].mkdir(parents=True, exist_ok=True)

        bundle = self.bind_row_assets(category, event_name, reel_path=reel_path)

        # ── Post 1: Event Type Cover ──────────────────────────────────────────
        cover_out = self.export_post_1_cover(bundle.post_1_cover, paths["post_1"])

        # ── Post 2: 8-photo carousel ────────────────────────────────────────
        zip_out, numbered_photos = self.export_post_2_carousel(
            bundle.post_2_photos, paths["post_2"]
        )

        # ── Post 3: branded reel ────────────────────────────────────────────
        reel_out = self.export_post_3_reel(bundle.post_3_reel, paths["post_3"])

        # ── Captions for all three posts ────────────────────────────────────
        image_paths = [str(bundle.post_1_cover)] + [str(p) for p in bundle.post_2_photos]
        captions = await generate_ideal_row_captions(
            category,
            event_name,
            image_paths=image_paths,
            caption_engine=caption_engine,
            visual_context=visual_context,
        )

        cap1_paths = write_caption_files(paths["post_1"], captions["post_1"])
        cap2_paths = write_caption_files(paths["post_2"], captions["post_2"])
        cap3_paths = write_caption_files(paths["post_3"], captions["post_3"])

        # One-click per-post bundles (Post 2 uses carousel.zip as primary asset)
        post_1_bundle = self.bundle_post_folder(paths["post_1"], "post_1_instagram.zip")
        post_3_bundle = self.bundle_post_folder(paths["post_3"], "post_3_instagram.zip")
        logo_meta = self._logo_metadata()

        post_1_meta: dict[str, Any] = {
            "role": POST_1_ROLE,
            "path": str(cover_out),
            "url": media_url(cover_out),
            "filename": cover_out.name,
            "width": IG_COVER_WIDTH,
            "height": IG_COVER_HEIGHT,
            "bundle_url": media_url(post_1_bundle),
            "bundle_filename": post_1_bundle.name,
        }
        post_1_meta = self._attach_caption_meta(
            post_1_meta, captions["post_1"], cap1_paths, paths["post_1"]
        )

        post_2_meta: dict[str, Any] = {
            "role": POST_2_ROLE,
            "path": str(zip_out),
            "url": media_url(zip_out),
            "filename": zip_out.name,
            "photo_count": POST_2_CAROUSEL_COUNT,
            "bundle_url": media_url(zip_out),
            "bundle_filename": zip_out.name,
            "photos": [
                {"index": i + 1, "path": str(p), "url": media_url(p), "filename": p.name}
                for i, p in enumerate(numbered_photos)
            ],
        }
        post_2_meta = self._attach_caption_meta(
            post_2_meta, captions["post_2"], cap2_paths, paths["post_2"]
        )

        post_3_meta: dict[str, Any] = {
            "role": POST_3_ROLE,
            "path": str(reel_out),
            "url": media_url(reel_out),
            "filename": reel_out.name,
            "width": REEL_WIDTH,
            "height": REEL_HEIGHT,
            "logo_overlay": True,
            "encoder": "hevc_videotoolbox / h264_videotoolbox",
            **logo_meta,
            "bundle_url": media_url(post_3_bundle),
            "bundle_filename": post_3_bundle.name,
        }
        post_3_meta = self._attach_caption_meta(
            post_3_meta, captions["post_3"], cap3_paths, paths["post_3"]
        )

        manifest_path = self._write_manifest(
            paths["export_base"],
            category,
            event_name,
            post_1_meta,
            post_2_meta,
            post_3_meta,
        )

        return InstagramExportResult(
            category=category,
            event_name=event_name,
            export_base=str(paths["export_base"]),
            post_1=post_1_meta,
            post_2=post_2_meta,
            post_3=post_3_meta,
            captions={
                "post_1": captions["post_1"].to_dict(),
                "post_2": captions["post_2"].to_dict(),
                "post_3": captions["post_3"].to_dict(),
            },
            manifest_url=media_url(manifest_path),
        )

    def row_exists(self, category: str, event_name: str) -> bool:
        return ideal_row_paths(category, event_name)["base"].is_dir()

    # Sync wrapper for callers that cannot await
    def prepare_package_sync(
        self,
        category: str,
        event_name: str,
        *,
        reel_path: str | Path | None = None,
        caption_engine: Any | None = None,
    ) -> InstagramExportResult:
        import asyncio

        return asyncio.run(
            self.prepare_package(
                category,
                event_name,
                reel_path=reel_path,
                caption_engine=caption_engine,
            )
        )
