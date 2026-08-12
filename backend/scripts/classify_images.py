#!/usr/bin/env python3
"""
CLI: Classify venue images into VV LUXE event categories.

Usage:
  python backend/scripts/classify_images.py ./photos/*.jpg
  python backend/scripts/classify_images.py --dir ./uploads --threshold 0.25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import CLASSIFICATION_CONFIDENCE_THRESHOLD  # noqa: E402
from backend.database import init_db, insert_image  # noqa: E402
from backend.models.classifier import EventClassifier  # noqa: E402


def collect_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.dir:
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            paths.extend(Path(args.dir).glob(ext))
    paths.extend(Path(p) for p in args.files)
    return sorted(set(p.resolve() for p in paths if p.exists()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify venue event photos")
    parser.add_argument("files", nargs="*", help="Image file paths")
    parser.add_argument("--dir", help="Directory to scan for images")
    parser.add_argument(
        "--threshold",
        type=float,
        default=CLASSIFICATION_CONFIDENCE_THRESHOLD,
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--model", choices=["clip", "florence2"], default="clip")
    parser.add_argument("--save-db", action="store_true", help="Persist to SQLite")
    args = parser.parse_args()

    paths = collect_paths(args)
    if not paths:
        print("No images found.", file=sys.stderr)
        sys.exit(1)

    init_db()
    classifier = EventClassifier(model_type=args.model)
    classifier.load()

    results = []
    for path in paths:
        result = classifier.classify_image(
            path, threshold=args.threshold, top_k=args.top_k
        )
        row = {
            "filepath": str(path),
            "primary_category": result.primary_category,
            "categories": result.categories,
            "confidence": round(result.confidence, 4),
            "is_uncategorized": result.is_uncategorized,
            "scores": {k: round(v, 4) for k, v in result.scores.items()},
        }
        results.append(row)
        print(json.dumps(row, indent=2))

        if args.save_db:
            insert_image(
                filename=path.name,
                filepath=str(path),
                categories=result.categories,
                primary_category=result.primary_category,
                confidence=result.confidence,
                metadata={"scores": result.scores},
            )

    print(f"\nClassified {len(results)} image(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
