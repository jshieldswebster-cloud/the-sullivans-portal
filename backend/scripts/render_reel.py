#!/usr/bin/env python3
"""
CLI: Generate 2.5D parallax reel from a single venue photograph.

Usage:
  python backend/scripts/render_reel.py ./uploads/venue.jpg --category Weddings
  python backend/scripts/render_reel.py photo.jpg --motion pan_left_right --duration 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import backend.services.video_service  # noqa: F401,E402
from backend.config import EVENT_CATEGORIES, VALID_MOTIONS, ensure_directories  # noqa: E402
from backend.services.video_service import VideoRenderPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 2.5D parallax video reel")
    parser.add_argument("image", help="Source photograph path")
    parser.add_argument(
        "--category",
        default="Weddings",
        choices=EVENT_CATEGORIES,
    )
    parser.add_argument(
        "--motion",
        default="push_in",
        choices=list(VALID_MOTIONS),
    )
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--fps", type=int, default=30, choices=[24, 30, 60])
    parser.add_argument("--audio", help="Optional audio track path")
    parser.add_argument("--output", help="Output MP4 path (optional)")
    parser.add_argument(
        "--depth-only",
        action="store_true",
        help="Save depth map preview only, skip video render",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    ensure_directories()
    pipeline = VideoRenderPipeline()
    pipeline.load()

    if args.depth_only:
        from backend.config import DEBUG_DIR

        out = DEBUG_DIR / "depth_map.png"
        pipeline.process_depth(image_path, save_debug=out)
        print(f"Depth map saved: {out}")
        return

    output = pipeline.render_reel(
        image_path,
        category=args.category,
        motion=args.motion,
        duration_sec=args.duration,
        fps=args.fps,
        audio_path=args.audio,
        output_path=args.output,
        save_depth_debug=False,
    )
    print(f"Reel rendered: {output}")


if __name__ == "__main__":
    main()
