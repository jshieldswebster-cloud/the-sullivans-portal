"""AI-powered reel title and typography suggestions for photo batches."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from backend.config import EVENT_CATEGORIES, TITLE_BATCH_PROMPT, UNCATEGORIZED_LABEL
from backend.models.caption_engine import CaptionEngine
from backend.models.caption_enricher import CaptionEnricher
from backend.models.classifier import EventClassifier

logger = logging.getLogger(__name__)

FALLBACK_TITLES: dict[str, tuple[str, str, str]] = {
    "Birthdays": ("CELEBRATE", "In Style", "An unforgettable birthday by Sullivan Portal."),
    "Weddings": ("FOREVER", "Begins Here", "Timeless elegance for your special day."),
    "Baby Showers": ("SWEET", "Arrivals", "Soft luxury for life's sweetest moments."),
    "Venue": ("THE SPACE", "Speaks Luxury", "Discover Sullivan Portal in-house production suite."),
    "Grad Party": ("MILESTONE", "Moments", "Celebrate achievement in elevated style."),
    "Corporate": ("ELEVATED", "Gatherings", "Professional events with boutique hospitality."),
}


class TitleService:
    """Suggest catchy reel titles and formatted caption copy from photo batches."""

    def __init__(
        self,
        classifier: EventClassifier | None = None,
        enricher: CaptionEnricher | None = None,
        caption_engine: CaptionEngine | None = None,
    ) -> None:
        self.classifier = classifier or EventClassifier()
        self.enricher = enricher or CaptionEnricher()
        self.caption_engine = caption_engine or CaptionEngine()

    def _resolve_category(
        self,
        image_paths: list[str | Path],
        category: str | None,
    ) -> str:
        if category and category in EVENT_CATEGORIES:
            return category
        if not image_paths:
            return EVENT_CATEGORIES[0]
        if self.classifier._model is None:
            self.classifier.load()
        result = self.classifier.classify_image(image_paths[0])
        if result.primary_category != UNCATEGORIZED_LABEL:
            return result.primary_category
        ranked = sorted(result.scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[0][0] if ranked else EVENT_CATEGORIES[0]

    def _extract_batch_context(self, image_paths: list[str | Path]) -> str:
        if not image_paths:
            return "Luxury venue photography with warm lighting and refined decor."
        if self.enricher._model is None:
            try:
                self.enricher.load()
            except Exception:
                return f"Batch of {len(image_paths)} luxury event venue photographs."

        snippets: list[str] = []
        for path in image_paths[:3]:
            try:
                extraction = self.enricher.extract(path)
                if extraction.formatted:
                    snippets.append(extraction.formatted.strip())
            except Exception as exc:
                logger.debug("Vision extract skipped for %s: %s", path, exc)

        if snippets:
            return "\n\n".join(snippets)
        return f"Batch of {len(image_paths)} luxury event venue photographs."

    @staticmethod
    def _parse_title_response(raw: str) -> dict[str, str]:
        bold = script = caption = ""
        for line in raw.splitlines():
            upper = line.strip()
            if upper.upper().startswith("BOLD:"):
                bold = upper.split(":", 1)[1].strip()
            elif upper.upper().startswith("SCRIPT:"):
                script = upper.split(":", 1)[1].strip()
            elif upper.upper().startswith("CAPTION:"):
                caption = upper.split(":", 1)[1].strip()

        if not bold:
            match = re.search(r"BOLD:\s*(.+)", raw, re.IGNORECASE)
            bold = match.group(1).strip() if match else ""
        if not script:
            match = re.search(r"SCRIPT:\s*(.+)", raw, re.IGNORECASE)
            script = match.group(1).strip() if match else ""
        if not caption:
            match = re.search(r"CAPTION:\s*(.+)", raw, re.IGNORECASE)
            caption = match.group(1).strip() if match else ""

        return {"title_bold": bold, "title_script": script, "caption": caption}

    @staticmethod
    def _format_html(title_bold: str, title_script: str) -> str:
        bold = title_bold.strip() or "Sullivan Portal"
        script = title_script.strip() or "In-House"
        return (
            f'<span class="title-bold">{bold}</span> '
            f'<span class="title-script">{script}</span>'
        )

    def _fallback(self, category: str) -> dict[str, Any]:
        bold, script, caption = FALLBACK_TITLES.get(
            category, ("Sullivan Portal", "In-House", "Luxury events, intentionally designed.")
        )
        return {
            "category": category,
            "title_bold": bold,
            "title_script": script,
            "caption": caption,
            "formatted_html": self._format_html(bold, script),
            "source": "template",
        }

    async def generate_for_batch(
        self,
        image_paths: list[str | Path],
        *,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Analyze a photo batch and return title + typography suggestions."""
        paths = [Path(p) for p in image_paths if Path(p).is_file()]
        resolved = self._resolve_category(paths, category)
        visual_context = self._extract_batch_context(paths)

        ollama_ok = await self.caption_engine.health_check()
        if not ollama_ok:
            logger.warning("Ollama unavailable — using template titles")
            result = self._fallback(resolved)
            result["image_count"] = len(paths)
            return result

        prompt = TITLE_BATCH_PROMPT.format(
            category=resolved,
            visual_context=visual_context,
        )
        payload = {
            "model": self.caption_engine.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.75, "num_predict": 256},
        }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{self.caption_engine.base_url}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("Title generation failed: %s", exc)
            result = self._fallback(resolved)
            result["image_count"] = len(paths)
            return result

        parsed = self._parse_title_response(raw)
        if not parsed["title_bold"]:
            result = self._fallback(resolved)
            result["image_count"] = len(paths)
            return result

        return {
            "category": resolved,
            "title_bold": parsed["title_bold"],
            "title_script": parsed["title_script"],
            "caption": parsed["caption"],
            "formatted_html": self._format_html(
                parsed["title_bold"], parsed["title_script"]
            ),
            "image_count": len(paths),
            "source": "ollama",
        }
