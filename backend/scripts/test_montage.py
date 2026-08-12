#!/usr/bin/env python3
"""
Test multi-image montage assembler (Ken Burns + xfade cross-fades).

Usage:
  python backend/scripts/test_montage.py uploads/*.png
  python backend/scripts/test_montage.py photo1.jpg photo2.jpg photo3.jpg --duration 4
  python backend/scripts/test_montage.py ./uploads/*.png --audio assets/audio/track.mp3
"""

from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import VIDEOS_DIR, ensure_directories  # noqa: E402
from backend.services.montage_service import MontageParams, MontageService  # noqa: E402


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(Path(m) for m in sorted(matches))
        else:
            p = Path(pattern)
            if p.is_file():
                paths.append(p)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble multi-photo vertical reel with Ken Burns + cross-fade"
    )
    parser.add_argument(
        "images",
        nargs="+",
        help="Image paths or glob patterns (e.g. uploads/*.png)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=4.0,
        help="Seconds per photo clip (default: 4)",
    )
    parser.add_argument(
        "--transition",
        type=float,
        default=0.8,
        help="Cross-fade duration in seconds (default: 0.8)",
    )
    parser.add_argument("--audio", type=Path, help="Optional background audio track")
    parser.add_argument(
        "--output",
        type=Path,
        default=VIDEOS_DIR / "montage_test.mp4",
        help="Output MP4 path",
    )
    parser.add_argument(
        "--no-videotoolbox",
        action="store_true",
        help="Fall back to software libx264 encoder",
    )
    args = parser.parse_args()

    paths = expand_paths(args.images)
    if not paths:
        print("No images found.", file=sys.stderr)
        sys.exit(1)

    ensure_directories()
    service = MontageService(
        params=MontageParams(
            clip_duration_sec=args.duration,
            transition_sec=args.transition,
        )
    )

    print(f"Assembling montage from {len(paths)} images...", file=sys.stderr)
    for p in paths:
        print(f"  • {p.name}", file=sys.stderr)

    t0 = time.perf_counter()
    result = service.assemble(
        paths,
        output_path=args.output,
        audio_path=args.audio,
        force_videotoolbox=not args.no_videotoolbox,
    )
    elapsed = time.perf_counter() - t0

    print()
    print("=" * 60)
    print("Multi-Image Montage — Complete")
    print("=" * 60)
    print(f"  Clips:      {result.clip_count}")
    print(f"  Duration:   {result.total_duration_sec:.2f}s")
    print(f"  Resolution: {result.width}x{result.height} @ {result.fps}fps")
    print(f"  Output:     {result.output_path}")
    print(f"  Time:       {elapsed:.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
