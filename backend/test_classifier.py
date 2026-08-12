#!/usr/bin/env python3
"""
Test CLIP/Florence-2 classifier on a folder and print the confidence matrix.

Usage:
  python backend/test_classifier.py ./uploads
  python backend/test_classifier.py ./test-photos --model clip --threshold 0.35
  python backend/test_classifier.py ./test-photos --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import (  # noqa: E402
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    EVENT_CATEGORIES,
    UNCATEGORIZED_LABEL,
)
from backend.models.classifier import EventClassifier  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


def collect_images(folder: Path) -> list[Path]:
    paths: list[Path] = []
    for ext in IMAGE_EXTENSIONS:
        paths.extend(folder.glob(f"*{ext}"))
        paths.extend(folder.glob(f"*{ext.upper()}"))
    return sorted(set(paths))


def print_matrix(rows: list[dict], threshold: float) -> None:
    col_width = 12
    name_width = 28

    header = f"{'Filename':<{name_width}}"
    for cat in EVENT_CATEGORIES:
        short = cat[: col_width - 1]
        header += f" {short:>{col_width}}"
    header += f" {'Primary':>{col_width}} {'Flag':>6}"
    print(header)
    print("-" * len(header))

    for row in rows:
        line = f"{row['filename']:<{name_width}}"
        for cat in EVENT_CATEGORIES:
            score = row["scores"].get(cat, 0.0)
            marker = "*" if score >= threshold else " "
            line += f" {score:>{col_width - 1}.3f}{marker}"
        primary = row["primary_category"][: col_width - 1]
        flag = "UNCAT" if row["is_uncategorized"] else "OK"
        line += f" {primary:>{col_width}} {flag:>6}"
        print(line)

    print()
    print(f"Threshold: {threshold:.2f}  (* = meets threshold)")
    print(f"Total images: {len(rows)}")
    uncategorized = sum(1 for r in rows if r["is_uncategorized"])
    print(f"Uncategorized: {uncategorized}")
    print(f"Classified: {len(rows) - uncategorized}")

    print("\nPer-category assignment counts:")
    for cat in EVENT_CATEGORIES + [UNCATEGORIZED_LABEL]:
        count = sum(1 for r in rows if r["primary_category"] == cat)
        if count:
            print(f"  {cat}: {count}")


def print_detailed(results_rows: list[dict], classifier: EventClassifier, paths: list[Path], threshold: float) -> None:
    """Print per-phrase breakdown for each image."""
    full_results = classifier.classify_batch(paths, threshold=threshold)
    print("\n--- Per-Phrase Breakdown ---")
    for result in full_results:
        print(f"\n{Path(result.filepath).name} → {result.primary_category} ({result.confidence:.3f})")
        for cat in EVENT_CATEGORIES:
            phrases = result.prompt_scores.get(cat, [])
            if not phrases:
                continue
            phrase_str = ", ".join(f"{p}:{s:.2f}" for p, s in phrases)
            avg = result.scores.get(cat, 0.0)
            print(f"  {cat} (avg {avg:.3f}): {phrase_str}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test venue image classifier and print confidence matrix"
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing test photographs",
    )
    parser.add_argument(
        "--model",
        choices=["clip", "florence2"],
        default="clip",
        help="Vision model backend (default: clip)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=CLASSIFICATION_CONFIDENCE_THRESHOLD,
        help=f"Minimum confidence to assign a category (default: {CLASSIFICATION_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of table",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include per-phrase score breakdown",
    )
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        sys.exit(1)

    paths = collect_images(folder)
    if not paths:
        print(f"No images found in {folder}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.model.upper()} classifier...", file=sys.stderr)
    classifier = EventClassifier(model_type=args.model)
    classifier.load()

    rows = classifier.confidence_matrix(paths, threshold=args.threshold)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_matrix(rows, args.threshold)
        if args.verbose:
            print_detailed(rows, classifier, paths, args.threshold)


if __name__ == "__main__":
    main()
