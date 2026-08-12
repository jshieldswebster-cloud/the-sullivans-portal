"""Logo resolution and default asset bootstrap for video/image branding."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.config import (
    DEFAULT_LOGO_ANCHOR,
    DEFAULT_LOGO_MARGIN,
    DEFAULT_LOGO_OPACITY,
    LOGOS_DIR,
    REEL_WIDTH,
)

logger = logging.getLogger(__name__)

DEFAULT_LOGO_FILENAME = "vv_luxe_logo.png"


def resolve_logo_path(explicit: str | Path | None = None) -> Path | None:
    """Return the first available logo path (explicit path or assets/logos/)."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
    preferred = LOGOS_DIR / DEFAULT_LOGO_FILENAME
    if preferred.is_file():
        return preferred
    for ext in ("*.png", "*.webp"):
        matches = sorted(LOGOS_DIR.glob(ext))
        if matches:
            return matches[0]
    return None


def ensure_default_logo() -> Path:
    """Create a minimal transparent gold wordmark if no logo exists."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOGOS_DIR / DEFAULT_LOGO_FILENAME
    if dest.is_file():
        return dest

    width = int(REEL_WIDTH * 0.28)
    height = int(width * 0.32)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    green = (47, 82, 51, int(255 * DEFAULT_LOGO_OPACITY))
    cream = (247, 244, 238, 200)

    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia.ttf", 48)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia Italic.ttf", 24)
    except OSError:
        title_font = ImageFont.load_default()
        sub_font = title_font

    draw.text((width // 2, height // 2 - 18), "VV LUXE Studio", font=title_font, fill=green, anchor="mm")
    draw.text((width // 2, height // 2 + 28), "Sullivan Portal", font=sub_font, fill=cream, anchor="mm")

    canvas.save(dest, "PNG")
    logger.info("Created default logo → %s", dest)
    return dest


def logo_overlay_position(
    *,
    anchor: str = DEFAULT_LOGO_ANCHOR,
    margin: int = DEFAULT_LOGO_MARGIN,
) -> str:
    """FFmpeg overlay x/y expressions for the given anchor."""
    anchors = {
        "top-left": (f"{margin}", f"{margin}"),
        "top-center": ("(W-w)/2", f"{margin}"),
        "top-right": (f"W-w-{margin}", f"{margin}"),
        "bottom-left": (f"{margin}", f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }
    return anchors.get(anchor, anchors["top-center"])
