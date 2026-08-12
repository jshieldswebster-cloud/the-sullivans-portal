"""
Multi-image video montage assembler.

Applies subtle Ken Burns motion to each photo, cross-fades clips with FFmpeg
xfade, and encodes to 1080×1920 at high bitrate — no depth-map warping.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from backend.config import (
    KEN_BURNS_MOTIONS,
    MONTAGE_CLIP_DURATION_SEC,
    MONTAGE_KEN_BURNS_HEADROOM,
    MONTAGE_KEN_BURNS_PAN_PX,
    MONTAGE_TRANSITION_SEC,
    REEL_FPS,
    REEL_HEIGHT,
    REEL_WIDTH,
    VIDEOS_DIR,
)
from backend.services.ffmpeg_renderer import FFmpegRenderer

logger = logging.getLogger(__name__)


def ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - pow(-2.0 * t + 2.0, 3.0) / 2.0


@dataclass
class MontageParams:
    clip_duration_sec: float = MONTAGE_CLIP_DURATION_SEC
    transition_sec: float = MONTAGE_TRANSITION_SEC
    fps: int = REEL_FPS
    width: int = REEL_WIDTH
    height: int = REEL_HEIGHT
    ken_burns_headroom: float = MONTAGE_KEN_BURNS_HEADROOM
    ken_burns_pan_px: int = MONTAGE_KEN_BURNS_PAN_PX
    motions: tuple[str, ...] = KEN_BURNS_MOTIONS


@dataclass
class MontageResult:
    output_path: str
    image_paths: list[str]
    clip_count: int
    total_duration_sec: float
    width: int
    height: int
    fps: int


class MontageService:
    """Assemble multi-photo vertical reels with Ken Burns + cross-fade."""

    def __init__(
        self,
        params: MontageParams | None = None,
        ffmpeg: FFmpegRenderer | None = None,
    ) -> None:
        self.params = params or MontageParams()
        self.ffmpeg = ffmpeg or FFmpegRenderer()

    def _motion_for_index(self, index: int) -> str:
        motions = self.params.motions
        return motions[index % len(motions)]

    def _pan_offset(self, t: float, motion: str) -> tuple[float, float]:
        """Return (px, py) pan offset at eased time t ∈ [0, 1]."""
        pan = self.params.ken_burns_pan_px
        if motion == "pan_right":
            return ((t - 0.5) * 2 * pan, 0.0)
        if motion == "pan_left":
            return ((0.5 - t) * 2 * pan, 0.0)
        if motion == "pan_up":
            return (0.0, (0.5 - t) * 2 * pan * 0.6)
        # push_in — gentle drift
        return ((t - 0.5) * pan * 0.35, (t - 0.5) * pan * 0.15)

    def fit_source_with_headroom(self, image_path: str | Path) -> np.ndarray:
        """Center-crop to 9:16 and upscale with Ken Burns headroom."""
        img = cv2.imread(str(image_path))
        if img is None:
            raise ValueError(f"Unable to read image: {image_path}")

        p = self.params
        headroom = p.ken_burns_headroom
        target_w = int(p.width * headroom)
        target_h = int(p.height * headroom)

        h, w = img.shape[:2]
        target_ratio = p.width / p.height
        current_ratio = w / h

        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            x0 = (w - new_w) // 2
            cropped = img[:, x0 : x0 + new_w]
        else:
            new_h = int(w / target_ratio)
            y0 = (h - new_h) // 2
            cropped = img[y0 : y0 + new_h, :]

        return cv2.resize(
            cropped, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4
        )

    def render_ken_burns_frames(
        self,
        source: np.ndarray,
        *,
        motion: str = "push_in",
        duration_sec: float | None = None,
        fps: int | None = None,
    ) -> list[np.ndarray]:
        """Generate Ken Burns frame sequence for one photo."""
        p = self.params
        duration = duration_sec or p.clip_duration_sec
        fps = fps or p.fps
        total = max(2, int(duration * fps))
        headroom = p.ken_burns_headroom
        sh, sw = source.shape[:2]

        frames: list[np.ndarray] = []
        for i in range(total):
            linear_t = i / (total - 1)
            t = ease_in_out_cubic(linear_t)

            visible_frac = 1.0 - t * (1.0 - 1.0 / headroom)
            crop_w = max(1, int(sw * visible_frac))
            crop_h = max(1, int(sh * visible_frac))

            pan_x, pan_y = self._pan_offset(t, motion)
            cx = sw / 2.0 + pan_x
            cy = sh / 2.0 + pan_y
            x0 = int(np.clip(cx - crop_w / 2, 0, sw - crop_w))
            y0 = int(np.clip(cy - crop_h / 2, 0, sh - crop_h))

            crop = source[y0 : y0 + crop_h, x0 : x0 + crop_w]
            frame = cv2.resize(
                crop, (p.width, p.height), interpolation=cv2.INTER_LANCZOS4
            )
            frames.append(frame)

        return frames

    def total_duration(self, clip_count: int) -> float:
        """Compute montage duration accounting for cross-fade overlaps."""
        if clip_count <= 0:
            return 0.0
        p = self.params
        if clip_count == 1:
            return p.clip_duration_sec
        return (
            clip_count * p.clip_duration_sec
            - (clip_count - 1) * p.transition_sec
        )

    def assemble(
        self,
        image_paths: list[str | Path],
        *,
        output_path: str | Path | None = None,
        audio_path: str | Path | None = None,
        audio_track_id: str | None = None,
        logo_path: str | Path | None = None,
        logo_opacity: float | None = None,
        logo_anchor: str | None = None,
        logo_width_ratio: float | None = None,
        logo_margin: int | None = None,
        force_videotoolbox: bool = True,
    ) -> MontageResult:
        """
        Full montage pipeline: Ken Burns per image → xfade stitch → logo → audio mux.
        """
        paths = [Path(p) for p in image_paths]
        if not paths:
            raise ValueError("At least one image path is required")
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"Image not found: {path}")

        p = self.params
        out = Path(output_path or VIDEOS_DIR / "montage_reel.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_files: list[Path] = []

            for idx, img_path in enumerate(paths):
                source = self.fit_source_with_headroom(img_path)
                motion = self._motion_for_index(idx)
                frames = self.render_ken_burns_frames(source, motion=motion)
                clip_path = tmp / f"clip_{idx:03d}.mp4"
                self.ffmpeg.encode_frames(
                    frames,
                    clip_path,
                    fps=p.fps,
                    force_videotoolbox=force_videotoolbox,
                )
                clip_files.append(clip_path)
                logger.info(
                    "Clip %d/%d (%s, motion=%s, %d frames)",
                    idx + 1,
                    len(paths),
                    img_path.name,
                    motion,
                    len(frames),
                )

            if len(clip_files) == 1:
                silent = clip_files[0]
            else:
                silent = tmp / "xfade_silent.mp4"
                self.ffmpeg.assemble_xfade(
                    clip_files,
                    silent,
                    transition_sec=p.transition_sec,
                    clip_duration_sec=p.clip_duration_sec,
                    fps=p.fps,
                )

            from backend.services.audio_library_service import AudioLibraryService
            from backend.services.logo_overlay import ensure_default_logo, resolve_logo_path
            from backend.services.watermark_service import WatermarkService

            wm_svc = WatermarkService()
            overrides: dict = {}
            if logo_opacity is not None:
                overrides["opacity"] = logo_opacity
            if logo_anchor is not None:
                overrides["anchor"] = logo_anchor
            if logo_width_ratio is not None:
                overrides["scale"] = logo_width_ratio
            if logo_margin is not None:
                overrides["margin"] = logo_margin
            if logo_path:
                overrides["logo_filename"] = Path(logo_path).name

            wm = wm_svc.overlay_params(overrides or None)
            resolved_logo = resolve_logo_path(logo_path) if logo_path else wm["logo_path"]
            if resolved_logo is None:
                resolved_logo = ensure_default_logo()

            branded = tmp / "branded_silent.mp4"
            self.ffmpeg.apply_logo_overlay(
                silent,
                branded,
                resolved_logo,
                opacity=wm["opacity"],
                anchor=wm["anchor"],
                margin=wm["margin"],
                logo_width_ratio=wm["logo_width_ratio"],
            )

            audio_svc = AudioLibraryService()
            resolved_audio = audio_svc.resolve_for_reel(
                audio_path=audio_path,
                audio_track_id=audio_track_id,
            )
            reel_duration = self.total_duration(len(paths))

            self.ffmpeg.mux_audio(
                branded,
                out,
                audio_path=resolved_audio,
                duration_sec=reel_duration,
            )

        duration = self.total_duration(len(paths))
        logger.info(
            "Montage complete → %s (%d clips, %.2fs @ %dx%d)",
            out,
            len(paths),
            duration,
            p.width,
            p.height,
        )
        return MontageResult(
            output_path=str(out),
            image_paths=[str(p) for p in paths],
            clip_count=len(paths),
            total_duration_sec=duration,
            width=p.width,
            height=p.height,
            fps=p.fps,
        )
