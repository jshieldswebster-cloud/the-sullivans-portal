"""
Local zero-shot event classification using CLIP or Florence-2.

Assigns each image to one or more of the five VV LUXE venue categories.
Images below the confidence threshold are flagged as Uncategorized.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from backend.config import (
    CATEGORY_PROMPT_WEIGHTS,
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    CLIP_PROMPT_TEMPLATE,
    EVENT_CATEGORIES,
    UNCATEGORIZED_LABEL,
    VISION_MODEL,
    get_device,
)

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    filepath: str
    primary_category: str
    categories: list[str]
    scores: dict[str, float]
    confidence: float
    is_uncategorized: bool = False
    prompt_scores: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


class EventClassifier:
    """Zero-shot venue event classifier (CLIP default, Florence-2 optional)."""

    def __init__(self, model_type: str | None = None) -> None:
        self.model_type = (model_type or VISION_MODEL).lower()
        self.device = get_device()
        self._model: Any = None
        self._processor: Any = None
        self._text_features: torch.Tensor | None = None
        self._label_map: list[str] = []
        self._weight_map: list[float] = []
        self._phrase_map: list[str] = []

    def load(self) -> None:
        if self.model_type == "florence2":
            self._load_florence2()
        else:
            self._load_clip()

    def _load_clip(self) -> None:
        from transformers import CLIPModel, CLIPProcessor

        model_id = "openai/clip-vit-base-patch32"
        logger.info("Loading CLIP model %s on %s", model_id, self.device)
        self._processor = CLIPProcessor.from_pretrained(model_id)
        self._model = CLIPModel.from_pretrained(model_id).to(self.device)
        self._model.eval()

        labels: list[str] = []
        prompts: list[str] = []
        weights: list[float] = []
        phrases: list[str] = []

        for category, weighted_phrases in CATEGORY_PROMPT_WEIGHTS.items():
            for phrase, weight in weighted_phrases:
                labels.append(category)
                prompts.append(CLIP_PROMPT_TEMPLATE.format(phrase=phrase))
                weights.append(weight)
                phrases.append(phrase)

        self._label_map = labels
        self._weight_map = weights
        self._phrase_map = phrases

        with torch.no_grad():
            inputs = self._processor(
                text=prompts, return_tensors="pt", padding=True, truncation=True
            ).to(self.device)
            text_features = self._model.get_text_features(**inputs)
            self._text_features = text_features / text_features.norm(
                dim=-1, keepdim=True
            )

    def _load_florence2(self) -> None:
        from transformers import AutoModelForCausalLM, AutoProcessor

        model_id = "microsoft/Florence-2-base"
        logger.info("Loading Florence-2 model %s on %s", model_id, self.device)
        self._processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True
        ).to(self.device)
        self._model.eval()

    def _aggregate_weighted_scores(
        self, raw_scores: list[float]
    ) -> tuple[dict[str, float], dict[str, list[tuple[str, float]]]]:
        """Weighted mean aggregation per category with per-phrase breakdown."""
        weighted_sums: dict[str, float] = {cat: 0.0 for cat in EVENT_CATEGORIES}
        weight_totals: dict[str, float] = {cat: 0.0 for cat in EVENT_CATEGORIES}
        prompt_breakdown: dict[str, list[tuple[str, float]]] = {
            cat: [] for cat in EVENT_CATEGORIES
        }

        for idx, label in enumerate(self._label_map):
            score = raw_scores[idx]
            weight = self._weight_map[idx]
            phrase = self._phrase_map[idx]
            weighted_sums[label] += weight * score
            weight_totals[label] += weight
            prompt_breakdown[label].append((phrase, score))

        category_scores: dict[str, float] = {}
        for cat in EVENT_CATEGORIES:
            if weight_totals[cat] > 0:
                category_scores[cat] = weighted_sums[cat] / weight_totals[cat]
            else:
                category_scores[cat] = 0.0

        return category_scores, prompt_breakdown

    def _resolve_classification(
        self,
        category_scores: dict[str, float],
        *,
        threshold: float,
        top_k: int,
        prompt_breakdown: dict[str, list[tuple[str, float]]] | None = None,
    ) -> tuple[str, list[str], float, bool]:
        ranked = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        best_cat, best_score = ranked[0]

        if best_score < threshold:
            return UNCATEGORIZED_LABEL, [UNCATEGORIZED_LABEL], best_score, True

        selected = [cat for cat, score in ranked if score >= threshold][:top_k]
        return best_cat, selected, best_score, False

    def classify_image(
        self,
        image_path: str | Path,
        *,
        threshold: float = CLASSIFICATION_CONFIDENCE_THRESHOLD,
        top_k: int = 3,
    ) -> ClassificationResult:
        if self._model is None:
            self.load()

        path = str(image_path)
        image = Image.open(path).convert("RGB")

        if self.model_type == "florence2":
            return self._classify_florence2(path, image, threshold, top_k)
        return self._classify_clip(path, image, threshold, top_k)

    def _classify_clip(
        self,
        path: str,
        image: Image.Image,
        threshold: float,
        top_k: int,
    ) -> ClassificationResult:
        assert self._processor is not None and self._model is not None
        assert self._text_features is not None

        with torch.no_grad():
            inputs = self._processor(images=image, return_tensors="pt").to(self.device)
            image_features = self._model.get_image_features(**inputs)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            similarities = (image_features @ self._text_features.T).squeeze(0)

        raw_scores = [float(similarities[idx].item()) for idx in range(len(self._label_map))]
        category_scores, prompt_breakdown = self._aggregate_weighted_scores(raw_scores)

        primary, selected, confidence, is_uncategorized = self._resolve_classification(
            category_scores, threshold=threshold, top_k=top_k
        )

        return ClassificationResult(
            filepath=path,
            primary_category=primary,
            categories=selected,
            scores=category_scores,
            confidence=confidence,
            is_uncategorized=is_uncategorized,
            prompt_scores=prompt_breakdown,
        )

    def _classify_florence2(
        self,
        path: str,
        image: Image.Image,
        threshold: float,
        top_k: int,
    ) -> ClassificationResult:
        assert self._processor is not None and self._model is not None

        descriptor_hint = ", ".join(
            phrase
            for phrases in CATEGORY_PROMPT_WEIGHTS.values()
            for phrase, _ in phrases[:2]
        )
        prompt = (
            "<CAPTION_TO_PHRASE_GROUNDING>Describe this luxury event venue: "
            + descriptor_hint
        )
        inputs = self._processor(text=prompt, images=image, return_tensors="pt").to(
            self.device
        )

        with torch.no_grad():
            generated = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=128,
            )
        text = self._processor.batch_decode(generated, skip_special_tokens=True)[0]
        text_lower = text.lower()

        weighted_sums: dict[str, float] = {cat: 0.0 for cat in EVENT_CATEGORIES}
        weight_totals: dict[str, float] = {cat: 0.0 for cat in EVENT_CATEGORIES}
        prompt_breakdown: dict[str, list[tuple[str, float]]] = {
            cat: [] for cat in EVENT_CATEGORIES
        }

        for category, weighted_phrases in CATEGORY_PROMPT_WEIGHTS.items():
            for phrase, weight in weighted_phrases:
                phrase_lower = phrase.lower()
                tokens = [t.strip() for t in phrase_lower.split(",")]
                if len(tokens) == 1:
                    tokens = phrase_lower.split()
                hits = sum(1 for token in tokens if token and token in text_lower)
                score = min(hits / max(len(tokens), 1), 1.0)
                weighted_sums[category] += weight * score
                weight_totals[category] += weight
                prompt_breakdown[category].append((phrase, score))

        category_scores = {
            cat: (weighted_sums[cat] / weight_totals[cat] if weight_totals[cat] else 0.0)
            for cat in EVENT_CATEGORIES
        }

        primary, selected, confidence, is_uncategorized = self._resolve_classification(
            category_scores, threshold=threshold, top_k=top_k
        )

        return ClassificationResult(
            filepath=path,
            primary_category=primary,
            categories=selected,
            scores=category_scores,
            confidence=float(confidence),
            is_uncategorized=is_uncategorized,
            prompt_scores=prompt_breakdown,
        )

    def classify_batch(
        self,
        image_paths: list[str | Path],
        *,
        threshold: float = CLASSIFICATION_CONFIDENCE_THRESHOLD,
        top_k: int = 3,
    ) -> list[ClassificationResult]:
        return [
            self.classify_image(p, threshold=threshold, top_k=top_k)
            for p in image_paths
        ]

    def confidence_matrix(
        self, image_paths: list[str | Path], *, threshold: float | None = None
    ) -> list[dict[str, Any]]:
        """Return structured results suitable for matrix display."""
        threshold = threshold or CLASSIFICATION_CONFIDENCE_THRESHOLD
        results = self.classify_batch(image_paths, threshold=threshold)
        matrix: list[dict[str, Any]] = []

        for result in results:
            row: dict[str, Any] = {
                "filepath": result.filepath,
                "filename": Path(result.filepath).name,
                "primary_category": result.primary_category,
                "confidence": round(result.confidence, 4),
                "is_uncategorized": result.is_uncategorized,
                "scores": {
                    cat: round(result.scores.get(cat, 0.0), 4)
                    for cat in EVENT_CATEGORIES
                },
            }
            matrix.append(row)

        return matrix
