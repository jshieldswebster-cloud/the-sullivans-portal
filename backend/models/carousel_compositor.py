"""
Automated 4:5 carousel compositor with brand typography and logo overlay.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from backend.config import (
    CAROUSEL_HEIGHT,
    CAROUSEL_WIDTH,
    CAROUSELS_DIR,
    DEFAULT_LOGO_ANCHOR,
    DEFAULT_LOGO_MARGIN,
    DEFAULT_LOGO_OPACITY,
    FONTS_DIR,
    LOGOS_DIR,
)

logger = logging.getLogger(__name__)

ANCHORS = {
    "top-left": (0, 0),
    "top-right": (1, 0),
    "bottom-left": (0, 1),
    "bottom-right": (1, 1),
    "center": (0.5, 0.5),
}


class CarouselCompositor:
    """Build multi-slide 4:5 carousels per event category."""

    def __init__(
        self,
        *,
        font_path: str | Path | None = None,
        logo_path: str | Path | None = None,
    ) -> None:
        self.font_path = self._resolve_font(font_path)
        self.logo_path = self._resolve_logo(logo_path)

    def _resolve_font(self, font_path: str | Path | None) -> Path | None:
        if font_path and Path(font_path).exists():
            return Path(font_path)
        for ext in ("*.otf", "*.ttf", "*.OTF", "*.TTF"):
            matches = list(FONTS_DIR.glob(ext))
            if matches:
                return matches[0]
        return None

    def _resolve_logo(self, logo_path: str | Path | None) -> Path | None:
        if logo_path and Path(logo_path).exists():
            return Path(logo_path)
        for ext in ("*.png", "*.webp", "*.svg"):
            matches = list(LOGOS_DIR.glob(ext))
            if matches:
                return matches[0]
        return None

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if self.font_path:
            return ImageFont.truetype(str(self.font_path), size)
        return ImageFont.load_default()

    def _fit_4_5(self, image: Image.Image) -> Image.Image:
        return ImageOps.fit(
            image,
            (CAROUSEL_WIDTH, CAROUSEL_HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    def _apply_logo(
        self,
        canvas: Image.Image,
        *,
        opacity: float = DEFAULT_LOGO_OPACITY,
        margin: int = DEFAULT_LOGO_MARGIN,
        anchor: str = DEFAULT_LOGO_ANCHOR,
    ) -> Image.Image:
        if not self.logo_path or not self.logo_path.exists():
            return canvas

        logo = Image.open(self.logo_path).convert("RGBA")
        max_w = int(CAROUSEL_WIDTH * 0.22)
        ratio = max_w / logo.width
        logo = logo.resize(
            (max_w, int(logo.height * ratio)), Image.Resampling.LANCZOS
        )

        if opacity < 1.0:
            alpha = logo.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            logo.putalpha(alpha)

        ax, ay = ANCHORS.get(anchor, ANCHORS["bottom-right"])
        x = int(margin if ax == 0 else CAROUSEL_WIDTH - logo.width - margin if ax == 1 else (CAROUSEL_WIDTH - logo.width) / 2)
        y = int(margin if ay == 0 else CAROUSEL_HEIGHT - logo.height - margin if ay == 1 else (CAROUSEL_HEIGHT - logo.height) / 2)

        base = canvas.convert("RGBA")
        base.paste(logo, (x, y), logo)
        return base.convert("RGB")

    def create_title_slide(
        self,
        category: str,
        *,
        subtitle: str = "VV LUXE · Richmond, California",
    ) -> Image.Image:
        canvas = Image.new("RGB", (CAROUSEL_WIDTH, CAROUSEL_HEIGHT), (18, 16, 20))
        draw = ImageDraw.Draw(canvas)

        title_font = self._load_font(72)
        sub_font = self._load_font(32)
        accent = (201, 169, 110)

        draw.text(
            (CAROUSEL_WIDTH // 2, CAROUSEL_HEIGHT // 2 - 60),
            category.upper(),
            font=title_font,
            fill=accent,
            anchor="mm",
        )
        draw.text(
            (CAROUSEL_WIDTH // 2, CAROUSEL_HEIGHT // 2 + 40),
            subtitle,
            font=sub_font,
            fill=(230, 226, 218),
            anchor="mm",
        )

        line_w = 120
        draw.line(
            [
                (CAROUSEL_WIDTH // 2 - line_w // 2, CAROUSEL_HEIGHT // 2 + 90),
                (CAROUSEL_WIDTH // 2 + line_w // 2, CAROUSEL_HEIGHT // 2 + 90),
            ],
            fill=accent,
            width=2,
        )
        return canvas

    def create_photo_slide(self, image_path: str | Path) -> Image.Image:
        image = Image.open(image_path).convert("RGB")
        canvas = self._fit_4_5(image)
        return self._apply_logo(canvas)

    def build_carousel(
        self,
        image_paths: list[str | Path],
        category: str,
        *,
        output_dir: str | Path | None = None,
    ) -> list[Path]:
        """Generate title slide + one slide per image."""
        out_dir = Path(output_dir or CAROUSELS_DIR / category.lower().replace(" ", "_"))
        out_dir.mkdir(parents=True, exist_ok=True)

        slides: list[Path] = []

        title = self.create_title_slide(category)
        title = self._apply_logo(title, anchor="top-left", opacity=0.9)
        title_path = out_dir / "slide_00_title.jpg"
        title.save(title_path, quality=95, subsampling=0)
        slides.append(title_path)

        for idx, img_path in enumerate(image_paths, start=1):
            slide = self.create_photo_slide(img_path)
            slide_path = out_dir / f"slide_{idx:02d}.jpg"
            slide.save(slide_path, quality=95, subsampling=0)
            slides.append(slide_path)

        logger.info("Carousel (%d slides) → %s", len(slides), out_dir)
        return slides
