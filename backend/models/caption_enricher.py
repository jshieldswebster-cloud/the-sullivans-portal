"""
Local vision-to-text extraction for caption enrichment.

Uses Moondream2 (default) or Florence-2 via PyTorch/MPS to extract structured
visual attributes from venue photographs before Llama 3 caption generation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from backend.config import (
    CAPTION_VISION_MODEL,
    FLORENCE2_MODEL_ID,
    MOONDREAM2_MODEL_ID,
    VISION_EXTRACTION_PROMPT,
    get_device,
)

logger = logging.getLogger(__name__)


@dataclass
class VisualExtraction:
    filepath: str
    raw_text: str
    model: str
    sections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def formatted(self) -> str:
        """Bullet-formatted context ready for Llama 3 injection."""
        if self.sections:
            lines: list[str] = []
            for heading, bullets in self.sections.items():
                lines.append(f"- {heading}")
                for bullet in bullets:
                    lines.append(f"  • {bullet}")
            return "\n".join(lines)
        return self.raw_text.strip()


class CaptionEnricher:
    """Extract structured visual context from venue photos."""

    SECTION_HEADERS = (
        "primary and accent color palettes",
        "table setup and decor features",
        "lighting ambiance",
        "architectural features",
    )

    def __init__(self, model_type: str | None = None) -> None:
        self.model_type = (model_type or CAPTION_VISION_MODEL).lower()
        self.device = get_device()
        self._model: Any = None
        self._processor: Any = None

    def load(self) -> None:
        if self.model_type == "florence2":
            self._load_florence2()
        else:
            self._load_moondream2()

    def _load_moondream2(self) -> None:
        from transformers import AutoModelForCausalLM

        logger.info("Loading Moondream2 %s on %s", MOONDREAM2_MODEL_ID, self.device)
        dtype = torch.float32 if self.device.type == "mps" else torch.float16
        self._model = AutoModelForCausalLM.from_pretrained(
            MOONDREAM2_MODEL_ID,
            trust_remote_code=True,
            torch_dtype=dtype if self.device.type != "cpu" else torch.float32,
        ).to(self.device)
        self._model.eval()

    def _load_florence2(self) -> None:
        from transformers import AutoModelForCausalLM, AutoProcessor

        logger.info("Loading Florence-2 %s on %s", FLORENCE2_MODEL_ID, self.device)
        self._processor = AutoProcessor.from_pretrained(
            FLORENCE2_MODEL_ID, trust_remote_code=True
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            FLORENCE2_MODEL_ID, trust_remote_code=True
        ).to(self.device)
        self._model.eval()

    def extract(self, image_path: str | Path) -> VisualExtraction:
        if self._model is None:
            self.load()

        path = str(image_path)
        image = Image.open(path).convert("RGB")

        if self.model_type == "florence2":
            raw = self._extract_florence2(image)
        else:
            raw = self._extract_moondream2(image)

        sections = self._parse_sections(raw)
        return VisualExtraction(
            filepath=path,
            raw_text=raw.strip(),
            model=self.model_type,
            sections=sections,
        )

    def _extract_moondream2(self, image: Image.Image) -> str:
        assert self._model is not None
        with torch.no_grad():
            enc_image = self._model.encode_image(image)
            answer = self._model.answer_question(
                enc_image,
                VISION_EXTRACTION_PROMPT,
                self._model.tokenizer,
            )
        return str(answer)

    def _extract_florence2(self, image: Image.Image) -> str:
        assert self._model is not None and self._processor is not None

        # Detailed caption pass, then structured VQA pass for venue specifics
        detailed = self._florence2_task(image, "<MORE_DETAILED_CAPTION>")
        structured = self._florence2_task(
            image,
            f"<VQA>{VISION_EXTRACTION_PROMPT}",
        )
        return f"{structured.strip()}\n\nAdditional scene detail: {detailed.strip()}"

    def _florence2_task(self, image: Image.Image, prompt: str) -> str:
        assert self._processor is not None and self._model is not None
        inputs = self._processor(text=prompt, images=image, return_tensors="pt").to(
            self.device
        )
        with torch.no_grad():
            generated = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=512,
            )
        return self._processor.batch_decode(generated, skip_special_tokens=True)[0]

    def _parse_sections(self, raw: str) -> dict[str, list[str]]:
        """Parse bullet output into named sections when possible."""
        sections: dict[str, list[str]] = {}
        current_key = "Visual observations"
        sections[current_key] = []

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            lower = stripped.lower().lstrip("-•* ").strip()
            matched_header = None
            for header in self.SECTION_HEADERS:
                if header in lower or lower.startswith(header):
                    matched_header = header.title()
                    break

            if matched_header:
                current_key = matched_header
                sections.setdefault(current_key, [])
                remainder = re.sub(
                    rf"^[-•*\s]*{re.escape(header)}[:\s]*",
                    "",
                    lower,
                    flags=re.IGNORECASE,
                ).strip()
                if remainder:
                    sections[current_key].append(remainder)
                continue

            bullet = stripped.lstrip("-•* ").strip()
            if bullet:
                sections.setdefault(current_key, []).append(bullet)

        return {k: v for k, v in sections.items() if v}
