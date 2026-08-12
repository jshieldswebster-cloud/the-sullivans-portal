#!/usr/bin/env python3
"""
End-to-end caption pipeline test: classify → vision extract → Llama 3 caption.

Usage:
  python backend/test_captioning.py ./test-photos/venue_01.jpg
  python backend/test_captioning.py ./uploads/photo.jpg --category Weddings
  python backend/test_captioning.py ./photo.jpg --vision florence2 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.models.caption_enricher import CaptionEnricher
from backend.services.caption_service import CaptionService  # noqa: E402


def print_report(result: dict) -> None:
    cls = result["classification"]
    vision = result["visual_extraction"]

    print("=" * 72)
    print(f"Image:     {result['filename']}")
    print(f"Category:  {result['category']}")
    print(f"Confidence: {cls['confidence']:.3f}  (uncategorized={cls['is_uncategorized']})")
    print()
    print("Classification scores:")
    for cat, score in sorted(cls["scores"].items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:<22} {score:.3f}")
    print()
    print(f"Vision model: {vision['model']}")
    print("-" * 72)
    print("Extracted visual attributes:")
    print(vision["formatted"])
    print("-" * 72)
    print("Generated caption (Llama 3 via Ollama):")
    print()
    print(result["caption"])
    print("=" * 72)


async def run(args: argparse.Namespace) -> dict:
    service = CaptionService(enricher=CaptionEnricher(model_type=args.vision))
    return await service.generate_enriched_caption(
        args.image,
        category=args.category,
        use_enriched_prompt=True,
        save=args.save,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test enriched caption pipeline for a venue photograph"
    )
    parser.add_argument("image", type=Path, help="Path to test image")
    parser.add_argument(
        "--category",
        help="Override auto-detected event category",
    )
    parser.add_argument(
        "--vision",
        choices=["moondream2", "florence2"],
        default="moondream2",
        help="Vision extraction backend (default: moondream2)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full result as JSON",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save caption to output/captions/",
    )
    args = parser.parse_args()

    image = args.image.resolve()
    if not image.is_file():
        print(f"Image not found: {image}", file=sys.stderr)
        sys.exit(1)

    print(f"Running caption pipeline on {image.name}...", file=sys.stderr)
    result = asyncio.run(run(args))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
