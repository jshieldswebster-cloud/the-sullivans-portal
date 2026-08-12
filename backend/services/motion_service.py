"""
2.5D parallax motion synthesizer.

Procedural camera paths that respect rigid architecture — affine transforms
only (scale, translate). Foreground decor receives depth-weighted parallax;
background walls remain geometrically straight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from backend.config import (
    MOTION_FG_PARALLAX_FACTOR,
    MOTION_PAN_SHIFT_PX,
    MOTION_PUSH_IN_SCALE_END,
    MOTION_PUSH_IN_SCALE_START,
    MOTION_TILT_SHIFT_PX,
    REEL_FPS,
    REEL_HEIGHT,
    REEL_WIDTH,
)
from backend.models.depth_engine import LayerSegmentation

logger = logging.getLogger(__name__)


@dataclass
class MotionParams:
    motion: str = "push_in"
    duration_sec: float = 5.0
    fps: int = REEL_FPS
    pan_shift_px: int = MOTION_PAN_SHIFT_PX
    scale_start: float = MOTION_PUSH_IN_SCALE_START
    scale_end: float = MOTION_PUSH_IN_SCALE_END
    fg_parallax_factor: float = MOTION_FG_PARALLAX_FACTOR


def ease_in_out_cubic(t: float) -> float:
    """Smooth ease for stutter-free motion."""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - pow(-2.0 * t + 2.0, 3.0) / 2.0


def _normalize_motion(name: str) -> str:
    if name == "pan_left":
        return "pan_left_right"
    return name


class MotionService:
    """Synthesize parallax frame sequences from segmented depth layers."""

    def __init__(self, params: MotionParams | None = None) -> None:
        self.params = params or MotionParams()

    def _camera_state(self, t: float, motion: str) -> tuple[float, int, int]:
        """
        Return (scale, dx, dy) for the background rigid camera.

        t is eased progress in [0, 1].
        """
        motion = _normalize_motion(motion)
        p = self.params

        if motion == "pan_left_right":
            scale = 1.0
            dx = int((t - 0.5) * 2 * p.pan_shift_px)
            dy = 0
        elif motion == "tilt_up":
            scale = 1.0
            dx = 0
            dy = int((0.5 - t) * 2 * MOTION_TILT_SHIFT_PX)
        else:  # push_in
            scale = p.scale_start + (p.scale_end - p.scale_start) * t
            dx = 0
            dy = 0

        return scale, dx, dy

    def _apply_rigid_transform(
        self,
        layer: np.ndarray,
        *,
        scale: float,
        dx: int,
        dy: int,
    ) -> np.ndarray:
        """Uniform scale from optical center + translation — no perspective warp."""
        h, w = layer.shape[:2]
        center = (w / 2.0, h / 2.0)
        M = cv2.getRotationMatrix2D(center, 0, scale)
        M[0, 2] += dx
        M[1, 2] += dy
        return cv2.warpAffine(
            layer, M, (w, h), borderMode=cv2.BORDER_REPLICATE
        )

    def _fit_9_16(self, frame: np.ndarray) -> np.ndarray:
        """Center-crop and resize to 1080×1920 vertical reel."""
        h, w = frame.shape[:2]
        target_ratio = REEL_WIDTH / REEL_HEIGHT
        current_ratio = w / h

        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            x0 = (w - new_w) // 2
            cropped = frame[:, x0 : x0 + new_w]
        else:
            new_h = int(w / target_ratio)
            y0 = (h - new_h) // 2
            cropped = frame[y0 : y0 + new_h, :]

        return cv2.resize(
            cropped, (REEL_WIDTH, REEL_HEIGHT), interpolation=cv2.INTER_LANCZOS4
        )

    def synthesize_frames(
        self,
        layers: LayerSegmentation,
        *,
        motion: str | None = None,
        duration_sec: float | None = None,
        fps: int | None = None,
    ) -> list[np.ndarray]:
        """
        Render a smooth 2.5D parallax frame sequence.

        Background: rigid affine camera path.
        Foreground: depth-weighted parallax offset on decor elements.
        """
        motion = _normalize_motion(motion or self.params.motion)
        duration = duration_sec if duration_sec is not None else self.params.duration_sec
        fps = fps or self.params.fps
        total_frames = max(2, int(duration * fps))

        background = layers.background_bgr
        foreground = layers.foreground_bgr
        alpha = layers.foreground_alpha[..., None]
        fg_mask = layers.foreground_mask
        p = self.params

        frames: list[np.ndarray] = []
        for i in range(total_frames):
            linear_t = i / (total_frames - 1)
            t = ease_in_out_cubic(linear_t)

            scale, dx, dy = self._camera_state(t, motion)
            bg_frame = self._apply_rigid_transform(background, scale=scale, dx=dx, dy=dy)

            # Foreground parallax — decor shifts more than architecture
            fg_dx = int(dx * p.fg_parallax_factor)
            fg_dy = int(dy * p.fg_parallax_factor)

            if motion == "push_in":
                parallax_offset = fg_mask * p.pan_shift_px * (t - 0.5) * 0.4
                fg_dx += int(np.mean(parallax_offset) * 0.5)
                fg_dy += int(np.mean(parallax_offset) * 0.2)

            M_fg = np.float32([[1, 0, fg_dx], [0, 1, fg_dy]])
            h, w = foreground.shape[:2]
            fg_shifted = cv2.warpAffine(
                foreground, M_fg, (w, h), borderMode=cv2.BORDER_REPLICATE
            )
            alpha_shifted = cv2.warpAffine(
                (alpha.squeeze() * 255).astype(np.uint8),
                M_fg,
                (w, h),
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            ).astype(np.float32)[..., None] / 255.0

            composite = bg_frame.astype(np.float32)
            composite = composite * (1.0 - alpha_shifted) + fg_shifted.astype(np.float32) * alpha_shifted
            composite = np.clip(composite, 0, 255).astype(np.uint8)
            frames.append(self._fit_9_16(composite))

        logger.info(
            "Synthesized %d frames @ %dfps (%.1fs) motion=%s",
            len(frames),
            fps,
            duration,
            motion,
        )
        return frames
