#!/usr/bin/env python3
"""
Test 2.5D depth parallax render pipeline with Apple Silicon hardware encoding.

Usage:
  python backend/scripts/test_depth_render.py ./uploads/photo.jpg --motion push_in --duration 5
  python backend/scripts/test_depth_render.py ./photo.jpg --motion pan_left_right --fps 60
  python backend/scripts/test_depth_render.py ./photo.jpg --motion tilt_up --no-videotoolbox
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import (  # noqa: E402
    DEBUG_DIR,
    REEL_FPS,
    VALID_MOTIONS,
    VIDEOS_DIR,
    ensure_directories,
)
from backend.models.depth_engine import DepthEngine  # noqa: E402
from backend.services.ffmpeg_renderer import FFmpegRenderer  # noqa: E402
from backend.services.motion_service import MotionParams, MotionService  # noqa: E402
from backend.services.video_service import VideoRenderPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test depth map generation and 2.5D parallax reel render"
    )
    parser.add_argument("image", type=Path, help="Source venue photograph")
    parser.add_argument(
        "--motion",
        default="push_in",
        choices=list(VALID_MOTIONS),
        help="Camera motion preset (default: push_in)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Reel duration in seconds (default: 5)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=REEL_FPS,
        choices=[24, 30, 60],
        help="Frame rate (default: 30)",
    )
    parser.add_argument("--audio", type=Path, help="Optional audio track override")
    parser.add_argument(
        "--depth-out",
        type=Path,
        default=DEBUG_DIR / "depth_map.png",
        help="Depth preview output path",
    )
    parser.add_argument(
        "--video-out",
        type=Path,
        default=VIDEOS_DIR / "reel_test.mp4",
        help="Rendered reel output path",
    )
    parser.add_argument(
        "--no-videotoolbox",
        action="store_true",
        help="Fall back to software libx264 encoder",
    )
    args = parser.parse_args()

    image = args.image.resolve()
    if not image.is_file():
        print(f"Image not found: {image}", file=sys.stderr)
        sys.exit(1)

    ensure_directories()
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    depth_engine = DepthEngine()
    motion = MotionService(
        MotionParams(motion=args.motion, duration_sec=args.duration, fps=args.fps)
    )
    ffmpeg = FFmpegRenderer()
    pipeline = VideoRenderPipeline(
        depth_engine=depth_engine,
        motion_service=motion,
        ffmpeg_renderer=ffmpeg,
    )

    print(f"Device: {depth_engine.device}", file=sys.stderr)
    print("Loading Depth Anything V2...", file=sys.stderr)
    t0 = time.perf_counter()
    pipeline.load()

    print("Generating depth map + layer segmentation...", file=sys.stderr)
    layers = pipeline.process_depth(image, save_debug=args.depth_out)
    depth_elapsed = time.perf_counter() - t0
    print(f"  Depth map → {args.depth_out}", file=sys.stderr)
    print(f"  16-bit raw  → {args.depth_out.with_name(args.depth_out.stem + '_16bit.png')}", file=sys.stderr)
    print(f"  Depth range: min={layers.depth_normalized.min():.3f} max={layers.depth_normalized.max():.3f}", file=sys.stderr)
    print(f"  Foreground coverage: {layers.foreground_mask.mean():.1%}", file=sys.stderr)
    print(f"  Depth inference: {depth_elapsed:.2f}s", file=sys.stderr)

    print(f"Synthesizing {args.duration}s @ {args.fps}fps motion={args.motion}...", file=sys.stderr)
    t1 = time.perf_counter()
    frames = motion.synthesize_frames(
        layers, motion=args.motion, duration_sec=args.duration, fps=args.fps
    )
    motion_elapsed = time.perf_counter() - t1
    print(f"  {len(frames)} frames in {motion_elapsed:.2f}s", file=sys.stderr)

    print("Encoding with FFmpeg VideoToolbox...", file=sys.stderr)
    t2 = time.perf_counter()
    try:
        encoder_args = ffmpeg.get_encoder_args(
            force_videotoolbox=not args.no_videotoolbox
        )
        print(f"  Encoder: {' '.join(encoder_args)}", file=sys.stderr)
        output = ffmpeg.encode_frames(
            frames,
            args.video_out,
            fps=args.fps,
            audio_path=args.audio,
            force_videotoolbox=not args.no_videotoolbox,
        )
    except RuntimeError as exc:
        if not args.no_videotoolbox:
            print(f"VideoToolbox failed ({exc}); retrying with libx264...", file=sys.stderr)
            output = ffmpeg.encode_frames(
                frames,
                args.video_out,
                fps=args.fps,
                audio_path=args.audio,
                force_videotoolbox=False,
            )
        else:
            raise
    encode_elapsed = time.perf_counter() - t2

    total = time.perf_counter() - t0
    print()
    print("=" * 60)
    print("2.5D Parallax Render — Complete")
    print("=" * 60)
    print(f"  Input:      {image.name}")
    print(f"  Motion:     {args.motion} ({args.duration}s @ {args.fps}fps)")
    print(f"  Depth map:  {args.depth_out}")
    print(f"  Reel:       {output}")
    print(f"  Encoder:    {ffmpeg.encoder_name}")
    print(f"  Timing:     depth={depth_elapsed:.2f}s motion={motion_elapsed:.2f}s encode={encode_elapsed:.2f}s total={total:.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
