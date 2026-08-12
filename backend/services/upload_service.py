"""Batch upload and classification orchestration."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.config import UPLOADS_DIR
from backend.database import insert_image
from backend.models.classifier import ClassificationResult, EventClassifier

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


class UploadService:
    def __init__(self, classifier: EventClassifier | None = None) -> None:
        self.classifier = classifier or EventClassifier()

    def save_upload(self, filename: str, data: bytes) -> Path:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / Path(filename).name
        dest.write_bytes(data)
        return dest

    def classify_and_store(self, filepath: Path) -> ClassificationResult:
        if self.classifier._model is None:
            self.classifier.load()

        result = self.classifier.classify_image(filepath)
        insert_image(
            filename=filepath.name,
            filepath=str(filepath),
            categories=result.categories,
            primary_category=result.primary_category,
            confidence=result.confidence,
            metadata={"scores": result.scores},
        )
        return result

    def process_batch(self, files: list[tuple[str, bytes]]) -> list[dict]:
        results = []
        for name, data in files:
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            path = self.save_upload(name, data)
            classification = self.classify_and_store(path)
            results.append(
                {
                    "filename": path.name,
                    "filepath": str(path),
                    "primary_category": classification.primary_category,
                    "categories": classification.categories,
                    "confidence": classification.confidence,
                    "is_uncategorized": classification.is_uncategorized,
                    "scores": classification.scores,
                }
            )
        return results
