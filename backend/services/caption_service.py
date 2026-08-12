"""
Orchestrates classification, vision extraction, and enriched Llama 3 captions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.config import UNCATEGORIZED_LABEL
from backend.models.caption_engine import CaptionEngine
from backend.models.caption_enricher import CaptionEnricher, VisualExtraction
from backend.models.classifier import EventClassifier, ClassificationResult

logger = logging.getLogger(__name__)


class CaptionService:
    """Full caption pipeline: classify → vision extract → Ollama generate."""

    def __init__(
        self,
        classifier: EventClassifier | None = None,
        enricher: CaptionEnricher | None = None,
        caption_engine: CaptionEngine | None = None,
    ) -> None:
        self.classifier = classifier or EventClassifier()
        self.enricher = enricher or CaptionEnricher()
        self.caption_engine = caption_engine or CaptionEngine()

    def classify_image(self, image_path: str | Path) -> ClassificationResult:
        if self.classifier._model is None:
            self.classifier.load()
        return self.classifier.classify_image(image_path)

    def extract_visual_context(self, image_path: str | Path) -> VisualExtraction:
        if self.enricher._model is None:
            self.enricher.load()
        return self.enricher.extract(image_path)

    async def generate_enriched_caption(
        self,
        image_path: str | Path,
        *,
        category: str | None = None,
        use_enriched_prompt: bool = True,
        save: bool = False,
    ) -> dict[str, Any]:
        """
        Run full pipeline for a single image.

        Returns category, visual extraction, and generated caption.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        classification = self.classify_image(path)
        resolved_category = category or classification.primary_category

        if resolved_category == UNCATEGORIZED_LABEL:
            resolved_category = classification.primary_category
            if resolved_category == UNCATEGORIZED_LABEL:
                ranked = sorted(
                    classification.scores.items(), key=lambda x: x[1], reverse=True
                )
                if ranked:
                    resolved_category = ranked[0][0]

        extraction = self.extract_visual_context(path)
        visual_context = extraction.formatted

        caption = await self.caption_engine.generate(
            resolved_category,
            image_context=visual_context,
            use_enriched_prompt=use_enriched_prompt,
        )

        result: dict[str, Any] = {
            "filepath": str(path),
            "filename": path.name,
            "category": resolved_category,
            "classification": {
                "primary_category": classification.primary_category,
                "confidence": classification.confidence,
                "is_uncategorized": classification.is_uncategorized,
                "scores": classification.scores,
            },
            "visual_extraction": {
                "model": extraction.model,
                "raw_text": extraction.raw_text,
                "formatted": visual_context,
                "sections": extraction.sections,
            },
            "caption": caption,
        }

        if save:
            safe = f"{resolved_category.lower().replace(' ', '_')}_{path.stem}"
            saved = await self.caption_engine.generate_and_save(
                resolved_category,
                image_context=visual_context,
                filename=safe,
                use_enriched_prompt=use_enriched_prompt,
            )
            result["saved_path"] = str(saved)

        return result

    async def generate_category_caption(
        self,
        category: str,
        *,
        image_path: str | Path | None = None,
        image_context: str | None = None,
    ) -> str:
        """
        Generate caption for a category, optionally enriched from a specific image.
        """
        context = image_context
        if image_path and not context:
            extraction = self.extract_visual_context(image_path)
            context = extraction.formatted

        return await self.caption_engine.generate(
            category,
            image_context=context,
            use_enriched_prompt=bool(context),
        )
