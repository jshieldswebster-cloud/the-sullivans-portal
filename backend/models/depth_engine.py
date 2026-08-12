"""
Depth Anything V2 depth mapping on Apple Silicon (MPS).

Generates normalized 16-bit depth matrices and separates foreground decor
from rigid background architecture.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image

from backend.config import (
    DEPTH_BILATERAL_D,
    DEPTH_BILATERAL_SIGMA_COLOR,
    DEPTH_BILATERAL_SIGMA_SPACE,
    DEPTH_CLAMP_HIGH,
    DEPTH_CLAMP_LOW,
    DEPTH_GAUSSIAN_KSIZE,
    DEPTH_MASK_BLUR_KSIZE,
    DEPTH_MODEL_ID,
    get_device,
)

logger = logging.getLogger(__name__)


@dataclass
class LayerSegmentation:
    """Foreground decor vs background architecture split."""

    foreground_bgr: np.ndarray
    background_bgr: np.ndarray
    foreground_alpha: np.ndarray  # float32 H×W, 0–1
    foreground_mask: np.ndarray  # float32 H×W, 0–1
    depth_normalized: np.ndarray  # float32 H×W, 0–1
    depth_16bit: np.ndarray  # uint16 H×W, 0–65535


class DepthEngine:
    """Depth Anything V2 inference and layer segmentation."""

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or DEPTH_MODEL_ID
        self.device = get_device()
        self._model: Any = None
        self._processor: Any = None

    def load(self) -> None:
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        logger.info("Loading Depth Anything V2 %s on %s", self.model_id, self.device)
        self._processor = AutoImageProcessor.from_pretrained(self.model_id)
        self._model = AutoModelForDepthEstimation.from_pretrained(self.model_id)
        self._model = self._model.to(self.device).eval()

    def generate_depth_map(self, image_path: str | Path) -> np.ndarray:
        """Return normalized float depth map (0=far, 1=near) at source resolution."""
        depth_16, normalized = self.generate_depth_map_16bit(image_path)
        return normalized

    def generate_depth_map_16bit(
        self, image_path: str | Path
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (uint16 depth matrix, float32 normalized depth).

        The 16-bit matrix stores normalized depth scaled to 0–65535.
        """
        if self._model is None:
            self.load()

        image = Image.open(image_path).convert("RGB")
        inputs = self._processor(images=image, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self._model(**inputs)
            depth = outputs.predicted_depth

        depth_np = depth.squeeze().float().cpu().numpy()
        normalized = (depth_np - depth_np.min()) / (
            depth_np.max() - depth_np.min() + 1e-8
        )
        depth_16 = (normalized * 65535.0).clip(0, 65535).astype(np.uint16)
        return depth_16, normalized.astype(np.float32)

    def smooth_depth_map(self, depth: np.ndarray) -> np.ndarray:
        """
        Edge-preserving smooth + Gaussian pass to soften depth boundaries.

        Reduces tearing around foreground objects (curtains, arches) during
        parallax projection.
        """
        depth_f = depth.astype(np.float32)
        depth_u8 = (depth_f * 255.0).clip(0, 255).astype(np.uint8)
        sigma_color = max(DEPTH_BILATERAL_SIGMA_COLOR * 255.0, 1.0)
        bilateral = cv2.bilateralFilter(
            depth_u8,
            DEPTH_BILATERAL_D,
            sigma_color,
            DEPTH_BILATERAL_SIGMA_SPACE,
        )
        k = DEPTH_GAUSSIAN_KSIZE | 1  # ensure odd
        smoothed = cv2.GaussianBlur(bilateral.astype(np.float32) / 255.0, (k, k), 0)
        return smoothed.clip(0.0, 1.0)

    @staticmethod
    def clamp_depth(depth: np.ndarray) -> np.ndarray:
        """Clamp extreme depth values to prevent pixel stretching."""
        return np.clip(depth, DEPTH_CLAMP_LOW, DEPTH_CLAMP_HIGH)

    def save_depth_preview(
        self,
        depth_16: np.ndarray,
        output_path: str | Path,
        *,
        colormap: bool = True,
    ) -> Path:
        """
        Save depth visualization to disk.

        Writes an 8-bit inferno colormap preview by default; also saves raw
        16-bit PNG alongside when output is ``depth_map.png``.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if colormap:
            norm = depth_16.astype(np.float32) / 65535.0
            colored = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
            cv2.imwrite(str(out), colored)
        else:
            cv2.imwrite(str(out), depth_16)

        raw_16 = out.with_name(out.stem + "_16bit.png")
        cv2.imwrite(str(raw_16), depth_16)
        logger.info("Depth preview: %s (16-bit: %s)", out, raw_16)
        return out

    def segment_layers(
        self,
        image_bgr: np.ndarray,
        depth: np.ndarray,
        *,
        fg_threshold: float | None = None,
    ) -> LayerSegmentation:
        """
        Separate foreground decor from background architecture.

        Uses adaptive percentile thresholding on the depth map so floral arches,
        table settings, and centerpieces parallax separately from walls and
        fixed lighting fixtures.
        """
        h, w = depth.shape[:2]
        if image_bgr.shape[:2] != (h, w):
            image_bgr = cv2.resize(image_bgr, (w, h), interpolation=cv2.INTER_LANCZOS4)

        if fg_threshold is None:
            fg_threshold = float(np.percentile(depth, 58))

        fg_mask = (depth >= fg_threshold).astype(np.float32)
        blur_k = DEPTH_MASK_BLUR_KSIZE | 1
        fg_mask = cv2.GaussianBlur(fg_mask, (blur_k, blur_k), 0)

        # Clean speckle — keep contiguous decor regions
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_binary = (fg_mask >= 0.5).astype(np.uint8)
        fg_binary = cv2.morphologyEx(fg_binary, cv2.MORPH_CLOSE, kernel)
        fg_binary = cv2.morphologyEx(fg_binary, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.GaussianBlur(fg_binary.astype(np.float32), (11, 11), 0)

        bg_mask = 1.0 - fg_mask
        background = (image_bgr.astype(np.float32) * bg_mask[..., None]).astype(np.uint8)
        foreground = image_bgr.copy()

        depth_16 = (depth * 65535.0).clip(0, 65535).astype(np.uint16)

        return LayerSegmentation(
            foreground_bgr=foreground,
            background_bgr=background,
            foreground_alpha=fg_mask,
            foreground_mask=fg_mask,
            depth_normalized=depth.astype(np.float32),
            depth_16bit=depth_16,
        )

    def process_image(self, image_path: str | Path) -> LayerSegmentation:
        """Full depth inference + segmentation pipeline."""
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            raise ValueError(f"Unable to read image: {image_path}")

        depth_16, normalized = self.generate_depth_map_16bit(image_path)
        depth = cv2.resize(normalized, (image_bgr.shape[1], image_bgr.shape[0]))
        depth = self.smooth_depth_map(depth)
        depth = self.clamp_depth(depth)
        layers = self.segment_layers(image_bgr, depth)
        layers.depth_16bit = (depth * 65535.0).clip(0, 65535).astype(np.uint16)
        return layers


# Backward-compatible alias used across the codebase
DepthParallaxEngine = DepthEngine
