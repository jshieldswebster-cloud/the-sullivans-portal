"""Automated Watermark Customizer — logo overlay settings for FFmpeg reels."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.config import (
    DEFAULT_LOGO_ANCHOR,
    DEFAULT_LOGO_MARGIN,
    DEFAULT_LOGO_OPACITY,
    LOGOS_DIR,
    MONTAGE_LOGO_WIDTH_RATIO,
    REEL_HEIGHT,
    REEL_WIDTH,
    WATERMARK_SETTINGS_PATH,
)
from backend.services.logo_overlay import DEFAULT_LOGO_FILENAME, resolve_logo_path
from backend.services.settings_store import read_json, write_json

logger = logging.getLogger(__name__)


def _default_settings() -> dict[str, Any]:
    return {
        "logo_filename": DEFAULT_LOGO_FILENAME,
        "opacity": DEFAULT_LOGO_OPACITY,
        "scale": MONTAGE_LOGO_WIDTH_RATIO,
        "anchor": DEFAULT_LOGO_ANCHOR,
        "margin": DEFAULT_LOGO_MARGIN,
    }


def load_watermark_settings() -> dict[str, Any]:
    path = WATERMARK_SETTINGS_PATH
    if not path.is_file():
        defaults = _default_settings()
        write_json(path, defaults)
        return defaults
    raw = read_json(path, default=_default_settings())
    return {**_default_settings(), **raw}


def save_watermark_settings(settings: dict[str, Any]) -> dict[str, Any]:
    current = load_watermark_settings()
    allowed_anchors = {
        "top-left",
        "top-center",
        "top-right",
        "bottom-left",
        "bottom-right",
        "center",
    }
    anchor = settings.get("anchor", current["anchor"])
    if anchor not in allowed_anchors:
        anchor = current["anchor"]

    opacity = float(settings.get("opacity", current["opacity"]))
    opacity = max(0.05, min(1.0, opacity))

    scale = float(settings.get("scale", current["scale"]))
    scale = max(0.08, min(0.5, scale))

    margin = int(settings.get("margin", current["margin"]))
    margin = max(16, min(120, margin))

    logo_filename = settings.get("logo_filename", current["logo_filename"])
    logo_path = LOGOS_DIR / logo_filename
    if not logo_path.is_file():
        logo_filename = current["logo_filename"]

    updated = {
        "logo_filename": logo_filename,
        "opacity": round(opacity, 3),
        "scale": round(scale, 3),
        "anchor": anchor,
        "margin": margin,
    }
    write_json(WATERMARK_SETTINGS_PATH, updated)
    try:
        from backend.services.studio_state_service import StudioStateService

        StudioStateService().persist_watermark_settings(updated)
    except Exception as exc:
        logger.warning("Could not mirror watermark settings to database: %s", exc)
    return updated


class WatermarkService:
    """Manage logo assets and overlay settings for montage renders."""

    def list_logos(self) -> list[dict[str, Any]]:
        logos: list[dict[str, Any]] = []
        for ext in ("*.png", "*.webp", "*.jpg", "*.jpeg"):
            for path in sorted(LOGOS_DIR.glob(ext)):
                logos.append(
                    {
                        "filename": path.name,
                        "url": f"/media/logos/{path.name}",
                        "size_bytes": path.stat().st_size,
                        "preferred": path.name == DEFAULT_LOGO_FILENAME,
                    }
                )
        return logos

    def get_settings(self) -> dict[str, Any]:
        settings = load_watermark_settings()
        logo_path = LOGOS_DIR / settings["logo_filename"]
        if not logo_path.is_file():
            resolved = resolve_logo_path()
            if resolved:
                settings["logo_filename"] = resolved.name
                settings["logo_url"] = f"/media/logos/{resolved.name}"
            else:
                settings["logo_url"] = None
        else:
            settings["logo_url"] = f"/media/logos/{logo_path.name}"
        settings["logos"] = self.list_logos()
        settings["frame_width"] = REEL_WIDTH
        settings["frame_height"] = REEL_HEIGHT
        return settings

    def overlay_params(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merged settings for FFmpeg apply_logo_overlay."""
        base = load_watermark_settings()
        if overrides:
            base = {**base, **{k: v for k, v in overrides.items() if v is not None}}
        logo_path = resolve_logo_path(LOGOS_DIR / base["logo_filename"])
        return {
            "logo_path": logo_path,
            "opacity": base["opacity"],
            "anchor": base["anchor"],
            "margin": base["margin"],
            "logo_width_ratio": base["scale"],
        }
