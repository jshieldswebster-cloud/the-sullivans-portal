"""
Full 2.5D video render pipeline: depth → motion → FFmpeg encode.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.config import DEBUG_DIR, REEL_FPS, VIDEOS_DIR
from backend.models.depth_engine import DepthEngine, LayerSegmentation
from backend.services.ffmpeg_renderer import FFmpegRenderer
from backend.services.motion_service import MotionParams, MotionService

logger = logging.getLogger(__name__)


class VideoRenderPipeline:
    """Orchestrates depth mapping, parallax synthesis, and hardware encoding."""

    def __init__(
        self,
        depth_engine: DepthEngine | None = None,
        motion_service: MotionService | None = None,
        ffmpeg_renderer: FFmpegRenderer | None = None,
    ) -> None:
        self.depth = depth_engine or DepthEngine()
        self.motion = motion_service or MotionService()
        self.ffmpeg = ffmpeg_renderer or FFmpegRenderer()

    def load(self) -> None:
        self.depth.load()

    def process_depth(
        self,
        image_path: str | Path,
        *,
        save_debug: Path | None = None,
    ) -> LayerSegmentation:
        """Generate depth map, segment layers, optionally save debug preview."""
        if self.depth._model is None:
            self.depth.load()

        layers = self.depth.process_image(image_path)

        if save_debug:
            self.depth.save_depth_preview(layers.depth_16bit, save_debug)

        return layers

    def render_parallax_frames(
        self,
        image_path: str | Path,
        *,
        motion: str = "push_in",
        duration_sec: float = 5.0,
        fps: int = REEL_FPS,
    ) -> list:
        layers = self.process_depth(image_path)
        return self.motion.synthesize_frames(
            layers, motion=motion, duration_sec=duration_sec, fps=fps
        )

    def encode_video(
        self,
        frames: list,
        output_path: str | Path,
        *,
        fps: int = REEL_FPS,
        audio_path: str | Path | None = None,
    ) -> Path:
        return self.ffmpeg.encode_frames(
            frames, output_path, fps=fps, audio_path=audio_path
        )

    def render_reel(
        self,
        image_path: str | Path,
        *,
        category: str = "event",
        motion: str = "push_in",
        duration_sec: float = 5.0,
        fps: int = REEL_FPS,
        audio_path: str | Path | None = None,
        output_path: str | Path | None = None,
        save_depth_debug: bool = False,
    ) -> Path:
        """Full pipeline: depth → parallax frames → VideoToolbox MP4."""
        debug_path = DEBUG_DIR / "depth_map.png" if save_depth_debug else None
        layers = self.process_depth(image_path, save_debug=debug_path)

        frames = self.motion.synthesize_frames(
            layers, motion=motion, duration_sec=duration_sec, fps=fps
        )

        if output_path:
            out = Path(output_path)
        else:
            safe_cat = category.lower().replace(" ", "_")
            stem = Path(image_path).stem
            out = VIDEOS_DIR / f"{safe_cat}_{stem}_reel.mp4"

        return self.ffmpeg.encode_frames(
            frames, out, fps=fps, audio_path=audio_path
        )


# Extend DepthEngine with pipeline methods for backward compatibility
def _patch_depth_engine_compat() -> None:
    def render_parallax_frames(
        self,
        image_path,
        *,
        motion: str = "push_in",
        max_shift_px: int = 28,  # noqa: ARG001 — legacy param
        duration_sec: float = 5.0,
        fps: int = REEL_FPS,
    ):
        pipeline = VideoRenderPipeline(depth_engine=self)
        return pipeline.render_parallax_frames(
            image_path, motion=motion, duration_sec=duration_sec, fps=fps
        )

    def encode_video(self, frames, output_path, *, audio_path=None, fps: int = REEL_FPS):
        return FFmpegRenderer().encode_frames(
            frames, output_path, fps=fps, audio_path=audio_path
        )

    def render_reel(
        self,
        image_path,
        *,
        category: str = "event",
        motion: str = "push_in",
        audio_path=None,
        duration_sec: float = 5.0,
        fps: int = REEL_FPS,
    ):
        return VideoRenderPipeline(depth_engine=self).render_reel(
            image_path,
            category=category,
            motion=motion,
            audio_path=audio_path,
            duration_sec=duration_sec,
            fps=fps,
        )

    DepthEngine.render_parallax_frames = render_parallax_frames  # type: ignore[method-assign]
    DepthEngine.encode_video = encode_video  # type: ignore[method-assign]
    DepthEngine.render_reel = render_reel  # type: ignore[method-assign]


_patch_depth_engine_compat()
