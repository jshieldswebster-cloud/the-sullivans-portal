"""Tour Portfolio Presentation Mode — scan Ideal Row events and classify color palettes."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from backend.config import EVENT_CATEGORIES, TOUR_COLOR_PALETTES, UPLOADS_DIR, category_slug
from backend.services.ideal_row_service import IDEAL_ROW_ROOT, POST_2_CAROUSEL_COUNT, media_url

logger = logging.getLogger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}


def _event_display_name(folder_name: str) -> str:
    return folder_name.replace("_", " ").strip()


def _portfolio_id(category: str, event_slug: str) -> str:
    return f"{category_slug(category)}__{event_slug}"


def _list_sorted_images(directory: Path, prefix: str | None = None) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    if prefix:
        files = [p for p in files if p.name.lower().startswith(prefix.lower())]
    return sorted(files, key=lambda p: p.name.lower())


def _find_reel(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    videos = sorted(
        [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return videos[0] if videos else None


def _rgb_stats(image_path: Path) -> tuple[float, float, float, float]:
    """Return average R, G, B (0–255) and saturation (0–1)."""
    with Image.open(image_path) as img:
        thumb = img.convert("RGB").resize((64, 64))
        pixels = list(thumb.getdata())
    if not pixels:
        return 128.0, 128.0, 128.0, 0.0
    rs = [p[0] for p in pixels]
    gs = [p[1] for p in pixels]
    bs = [p[2] for p in pixels]
    r, g, b = sum(rs) / len(rs), sum(gs) / len(gs), sum(bs) / len(bs)
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / mx if mx else 0.0
    return r, g, b, sat


def classify_color_palettes(image_path: Path) -> list[str]:
    """Map cover image colors to one or more tour palette ids."""
    try:
        r, g, b, sat = _rgb_stats(image_path)
    except OSError:
        return ["blush_neutrals"]

    brightness = (r + g + b) / 3.0
    matches: list[str] = []

    # Willow Green & Cream — green-forward with warm cream luminance
    if g >= r * 0.95 and g >= b * 0.9 and 40 <= g <= 180:
        matches.append("willow_cream")
    if sat < 0.22 and brightness >= 175:
        matches.append("willow_cream")

    # Black & White — low saturation, high contrast neutrals
    if sat < 0.14:
        matches.append("black_white")

    # Gold & White — warm yellow/gold highlights
    if r >= 150 and g >= 120 and b <= r * 0.82 and sat >= 0.12:
        matches.append("gold_white")
    if brightness >= 200 and r >= 180 and g >= 160:
        matches.append("gold_white")

    # Blush & Luxury Neutrals — pink blush or soft taupe
    if r >= g * 1.05 and r >= b * 1.08 and 90 <= r <= 220:
        matches.append("blush_neutrals")
    if sat < 0.28 and 110 <= brightness <= 190:
        matches.append("blush_neutrals")

    if not matches:
        matches.append("blush_neutrals")
    return list(dict.fromkeys(matches))


def _load_portfolio(category: str, event_dir: Path) -> dict[str, Any] | None:
    ideal = event_dir / IDEAL_ROW_ROOT
    post_1_dir = ideal / "Post_1"
    post_2_dir = ideal / "Post_2"
    post_3_dir = ideal / "Post_3"
    if not ideal.is_dir():
        return None

    covers = _list_sorted_images(post_1_dir, "cover")
    if not covers:
        covers = _list_sorted_images(post_1_dir)
    if not covers:
        return None

    cover = covers[0]
    details = _list_sorted_images(post_2_dir, "photo")
    if len(details) < POST_2_CAROUSEL_COUNT:
        details = _list_sorted_images(post_2_dir)[:POST_2_CAROUSEL_COUNT]
    reel = _find_reel(post_3_dir)
    reel_stills = _list_sorted_images(post_3_dir, "reel")

    slug = event_dir.name
    palettes = classify_color_palettes(cover)

    return {
        "id": _portfolio_id(category, slug),
        "category": category,
        "event_name": _event_display_name(slug),
        "event_slug": slug,
        "palettes": palettes,
        "cover": {"path": str(cover), "url": media_url(cover), "filename": cover.name},
        "details": [
            {"path": str(p), "url": media_url(p), "filename": p.name, "index": i + 1}
            for i, p in enumerate(details)
        ],
        "detail_count": len(details),
        "reel": {
            "path": str(reel),
            "url": media_url(reel),
            "filename": reel.name,
        }
        if reel
        else None,
        "reel_stills": [{"url": media_url(p), "filename": p.name} for p in reel_stills[:6]],
    }


class TourModeService:
    def scan_portfolios(self) -> list[dict[str, Any]]:
        portfolios: list[dict[str, Any]] = []
        for category in EVENT_CATEGORIES:
            cat_dir = UPLOADS_DIR / category_slug(category)
            if not cat_dir.is_dir():
                continue
            for event_dir in sorted(cat_dir.iterdir()):
                if not event_dir.is_dir() or event_dir.name.startswith("."):
                    continue
                if event_dir.name in ("Vincent_Ingest", IDEAL_ROW_ROOT):
                    continue
                portfolio = _load_portfolio(category, event_dir)
                if portfolio:
                    portfolios.append(portfolio)
        return portfolios

    def list_portfolios(
        self,
        *,
        category: str | None = None,
        palette: str | None = None,
    ) -> dict[str, Any]:
        items = self.scan_portfolios()
        if category and category in EVENT_CATEGORIES:
            items = [p for p in items if p["category"] == category]
        if palette:
            items = [p for p in items if palette in p.get("palettes", [])]
        return {
            "count": len(items),
            "categories": EVENT_CATEGORIES,
            "palettes": TOUR_COLOR_PALETTES,
            "portfolios": items,
        }

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        match = re.match(r"^(.+)__(.+)$", portfolio_id)
        if not match:
            return None
        cat_slug, event_slug_name = match.group(1), match.group(2)
        category = next((c for c in EVENT_CATEGORIES if category_slug(c) == cat_slug), None)
        if not category:
            return None
        event_dir = UPLOADS_DIR / cat_slug / event_slug_name
        return _load_portfolio(category, event_dir)
