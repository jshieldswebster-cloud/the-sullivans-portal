"""Localized luxury caption generation via local Ollama."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from backend.config import (
    CAPTION_ENRICHED_PROMPT,
    CAPTION_SYSTEM_PROMPT,
    CAPTIONS_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)

logger = logging.getLogger(__name__)


class CaptionEngine:
    """Generate VV LUXE marketing captions using Ollama (Llama 3 / Mistral)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_MODEL

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def build_prompt(
        self,
        category: str,
        image_context: str | None = None,
        *,
        use_enriched_prompt: bool = False,
    ) -> str:
        if use_enriched_prompt and image_context:
            return CAPTION_ENRICHED_PROMPT.format(
                category=category,
                visual_context=image_context.strip(),
            )

        system = CAPTION_SYSTEM_PROMPT.format(category=category)
        context_line = ""
        if image_context:
            context_line = f"\n\nScene context from vision analysis:\n{image_context.strip()}"
        return (
            f"{system}{context_line}\n\n"
            "Write one caption only. Use two short paragraphs separated by a blank line. "
            "End with a booking CTA and 4-6 hashtags on the final line."
        )

    async def generate(
        self,
        category: str,
        *,
        image_context: str | None = None,
        use_enriched_prompt: bool = False,
        temperature: float = 0.7,
    ) -> str:
        enriched = use_enriched_prompt or bool(image_context)
        prompt = self.build_prompt(
            category,
            image_context,
            use_enriched_prompt=enriched and bool(image_context),
        )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 512},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()

        caption = data.get("response", "").strip()
        if not caption:
            raise RuntimeError("Ollama returned empty caption")
        return caption

    async def generate_and_save(
        self,
        category: str,
        *,
        image_context: str | None = None,
        filename: str | None = None,
        use_enriched_prompt: bool = False,
    ) -> Path:
        caption = await self.generate(
            category,
            image_context=image_context,
            use_enriched_prompt=use_enriched_prompt,
        )
        CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
        safe = (filename or category.lower().replace(" ", "_")) + ".txt"
        path = CAPTIONS_DIR / safe
        path.write_text(caption, encoding="utf-8")
        logger.info("Saved caption: %s", path)
        return path
