"""Batch upload with category folder organization and CLIP classification."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from backend.config import EVENT_CATEGORIES, UNCATEGORIZED_LABEL, category_upload_dir
from backend.database import insert_image
from backend.models.classifier import ClassificationResult, EventClassifier

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


class UploadService:
    def __init__(self, classifier: EventClassifier | None = None) -> None:
        self.classifier = classifier or EventClassifier()

    def save_upload(
        self,
        filename: str,
        data: bytes,
        *,
        category: str | None = None,
    ) -> Path:
        ext = Path(filename).suffix.lower()
        safe_name = f"{uuid.uuid4().hex}{ext}" if category else Path(filename).name

        if category and category in EVENT_CATEGORIES:
            dest_dir = category_upload_dir(category)
        else:
            from backend.config import UPLOADS_DIR

            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            dest_dir = UPLOADS_DIR

        dest = dest_dir / safe_name
        dest.write_bytes(data)
        return dest

    def classify_and_store(
        self,
        filepath: Path,
        *,
        category: str | None = None,
    ) -> ClassificationResult:
        if self.classifier._model is None:
            self.classifier.load()

        if category and category in EVENT_CATEGORIES:
            result = self.classifier.classify_image(filepath)
            primary = category
            categories = list(dict.fromkeys([category, *result.categories]))
        else:
            result = self.classifier.classify_image(filepath)
            primary = result.primary_category
            categories = result.categories

        if primary == UNCATEGORIZED_LABEL and categories:
            primary = categories[0]

        insert_image(
            filename=filepath.name,
            filepath=str(filepath),
            categories=categories,
            primary_category=primary,
            confidence=result.confidence,
            metadata={"scores": result.scores},
        )
        result.primary_category = primary
        result.categories = categories
        return result

    def process_batch(
        self,
        files: list[tuple[str, bytes]],
        *,
        category: str | None = None,
    ) -> list[dict]:
        results = []
        for name, data in files:
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            path = self.save_upload(name, data, category=category)
            classification = self.classify_and_store(path, category=category)
            results.append(
                {
                    "filename": path.name,
                    "filepath": str(path),
                    "url": self.media_url_for(path),
                    "primary_category": classification.primary_category,
                    "categories": classification.categories,
                    "confidence": classification.confidence,
                    "is_uncategorized": classification.is_uncategorized,
                    "scores": classification.scores,
                }
            )
        return results

    @staticmethod
    def media_url_for(filepath: Path) -> str:
        """Build a /media/uploads URL for a file under the uploads tree."""
        from backend.config import UPLOADS_DIR

        try:
            rel = filepath.relative_to(UPLOADS_DIR)
            return f"/media/uploads/{rel.as_posix()}"
        except ValueError:
            return f"/media/uploads/{filepath.name}"

    @staticmethod
    def list_category_assets(category: str | None = None) -> list[dict]:
        """Scan category folders on disk (fallback when DB is empty)."""
        from backend.config import UPLOADS_DIR

        assets: list[dict] = []
        categories = [category] if category else EVENT_CATEGORIES

        for cat in categories:
            folder = category_upload_dir(cat)
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                assets.append(
                    {
                        "filename": path.name,
                        "filepath": str(path),
                        "url": f"/media/uploads/{folder.name}/{path.name}",
                        "primary_category": cat,
                    }
                )
        return assets
