"""Local inference model registry."""

from backend.models.caption_engine import CaptionEngine
from backend.models.caption_enricher import CaptionEnricher
from backend.models.carousel_compositor import CarouselCompositor
from backend.models.classifier import EventClassifier
from backend.models.depth_engine import DepthParallaxEngine

__all__ = [
    "EventClassifier",
    "DepthParallaxEngine",
    "CarouselCompositor",
    "CaptionEngine",
    "CaptionEnricher",
]
