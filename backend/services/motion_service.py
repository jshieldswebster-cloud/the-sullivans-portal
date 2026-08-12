"""
2.5D parallax motion synthesizer.

Procedural camera paths that respect rigid architecture — affine transforms
only (scale, translate). Depth-weighted sub-pixel warping reduces tearing at
foreground boundaries (curtains, arches, stanchions).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from backend.config import (
    DEPTH_CLAMP_HIGH,
    DEPTH_CLAMP_LOW,
    MOTION_FG_PARALLAX_FACTOR,
    MOTION_MAX_DISPLACEMENT_PX,
    MOTION_PAN_SHIFT_PX,
    MOTION_PUSH_IN_SCALE_END,
    MOTION_PUSH_IN_SCALE_START,
    MOTION_TILT_SHIFT_PX,
    REEL_FPS,
    REEL_HEIGHT,
    REEL_PRESERVE_NATIVE_RES,
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
    max_displacement_px: float = MOTION_MAX_DISPLACEMENT_PX
    preserve_native_res: bool = REEL_PRESERVE_NATIVE_RES


def ease_in_out_cubic(t: float) -> float:
    """Smooth ease-in-out for stutter-free motion."""
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

    def _camera_state(self, t: float, motion: str) -> tuple[float, float, float]:
        """Return (scale, dx, dy) for the rigid background camera."""
        motion = _normalize_motion(motion)
        p = self.params

        if motion == "pan_left_right":
            scale = 1.0
            dx = (t - 0.5) * 2.0 * p.pan_shift_px
            dy = 0.0
        elif motion == "tilt_up":
            scale = 1.0
            dx = 0.0
            dy = (0.5 - t) * 2.0 * MOTION_TILT_SHIFT_PX
        else:  # push_in — subtle ease-in-out zoom
            scale = p.scale_start + (p.scale_end - p.scale_start) * t
            dx = 0.0
            dy = 0.0

        return scale, dx, dy

    def _apply_rigid_transform(
        self,
        layer: np.ndarray,
        *,
        scale: float,
        dx: float,
        dy: float,
    ) -> np.ndarray:
        """Uniform scale from optical center + translation — no perspective warp."""
        h, w = layer.shape[:2]
        center = (w / 2.0, h / 2.0)
        M = cv2.getRotationMatrix2D(center, 0, scale)
        M[0, 2] += dx
        M[1, 2] += dy
        return cv2.warpAffine(
            layer, M, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE
        )

    def _depth_flow_field(
        self,
        depth: np.ndarray,
        *,
        t: float,
        dx: float,
        dy: float,
        motion: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Build sub-pixel displacement maps from clamped, smoothed depth.

        Displacement magnitude is capped by max_displacement_px.
        """
        p = self.params
        h, w = depth.shape[:2]
        depth_clamped = np.clip(depth, DEPTH_CLAMP_LOW, DEPTH_CLAMP_HIGH)
        centered = (depth_clamped - 0.5) * 2.0

        # Motion progress peaks mid-travel then returns — avoids edge stretch
        motion_weight = np.sin(t * np.pi)
        max_disp = p.max_displacement_px * motion_weight * p.fg_parallax_factor

        flow_x = centered * max_disp + dx * p.fg_parallax_factor
        flow_y = centered * max_disp * 0.35 + dy * p.fg_parallax_factor

        if motion == "push_in":
            push = (t - 0.5) * p.max_displacement_px * 0.25
            flow_x += centered * push
            flow_y += centered * push * 0.2

        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        map_x = np.ascontiguousarray(np.clip(xx + flow_x, 0, w - 1), dtype=np.float32)
        map_y = np.ascontiguousarray(np.clip(yy + flow_y, 0, h - 1), dtype=np.float32)
        return map_x, map_y

    def _warp_depth_parallax(
        self,
        layer: np.ndarray,
        depth: np.ndarray,
        alpha: np.ndarray,
        *,
        t: float,
        dx: float,
        dy: float,
        motion: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Depth-weighted remap for foreground with soft alpha."""
        map_x, map_y = self._depth_flow_field(depth, t=t, dx=dx, dy=dy, motion=motion)
        alpha_map = alpha.astype(np.float32)
        if alpha_map.ndim == 3:
            alpha_map = alpha_map.squeeze(-1)
        warped = cv2.remap(
            layer,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        alpha_warped = cv2.remap(
            alpha_map,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        return warped, alpha_warped

    def _fit_9_16(self, frame: np.ndarray) -> np.ndarray:
        """Center-crop to 9:16; preserve native resolution when configured."""
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

        if self.params.preserve_native_res:
            ch, cw = cropped.shape[:2]
            cw -= cw % 2
            ch -= ch % 2
            return cropped[:ch, :cw]

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
        Foreground: depth-weighted sub-pixel warp with soft alpha blending.
        """
        motion = _normalize_motion(motion or self.params.motion)
        duration = duration_sec if duration_sec is not None else self.params.duration_sec
        fps = fps or self.params.fps
        total_frames = max(2, int(duration * fps))

        background = layers.background_bgr
        foreground = layers.foreground_bgr
        alpha = layers.foreground_alpha
        depth = layers.depth_normalized
        p = self.params

        frames: list[np.ndarray] = []
        for i in range(total_frames):
            linear_t = i / (total_frames - 1)
            t = ease_in_out_cubic(linear_t)

            scale, dx, dy = self._camera_state(t, motion)
            bg_frame = self._apply_rigid_transform(
                background, scale=scale, dx=dx, dy=dy
            )

            fg_shifted, alpha_shifted = self._warp_depth_parallax(
                foreground,
                depth,
                alpha,
                t=t,
                dx=dx,
                dy=dy,
                motion=motion,
            )

            a = alpha_shifted[..., None]
            composite = bg_frame.astype(np.float32)
            composite = (
                composite * (1.0 - a) + fg_shifted.astype(np.float32) * a
            )
            composite = np.clip(composite, 0, 255).astype(np.uint8)
            frames.append(self._fit_9_16(composite))

        out_h, out_w = frames[0].shape[:2] if frames else (0, 0)
        logger.info(
            "Synthesized %d frames @ %dfps (%.1fs) motion=%s output=%dx%d max_disp=%.1fpx",
            len(frames),
            fps,
            duration,
            motion,
            out_w,
            out_h,
            p.max_displacement_px,
        )
        return frames
