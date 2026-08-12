"""Secure client gallery — PIN gate and view-only event presentation."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from backend.config import CLIENT_GALLERY_DEFAULT_PIN, CLIENT_GALLERY_PINS_PATH
from backend.services.settings_store import read_json, write_json
from backend.services.tour_mode_service import TourModeService

logger = logging.getLogger(__name__)

# Re-export vault session keys from auth_service for backward compatibility
from backend.services.auth_service import (  # noqa: E402
    SESSION_PORTFOLIO_ID,
    SESSION_VERIFIED,
)


def _pins_file() -> Path:
    path = CLIENT_GALLERY_PINS_PATH
    if not path.is_file():
        sample = {
            "_comment": "Map portfolio_id to event PIN. Example: weddings__The_Johnson_Wedding",
            "weddings__The_Johnson_Wedding": "482910",
        }
        write_json(path, sample)
    return path


def load_event_pins() -> dict[str, str]:
    try:
        raw = read_json(_pins_file(), default={})
        return {k: str(v) for k, v in raw.items() if not k.startswith("_")}
    except OSError as exc:
        logger.warning("Could not load client gallery pins: %s", exc)
        return {}


def verify_access_code(portfolio_id: str, pin: str) -> bool:
    """Return True if PIN unlocks the given event portfolio."""
    if not portfolio_id or not pin:
        return False
    svc = TourModeService()
    if not svc.get_portfolio(portfolio_id):
        return False

    pin = pin.strip()
    event_pin = load_event_pins().get(portfolio_id)
    if event_pin:
        return secrets.compare_digest(pin, event_pin)
    return secrets.compare_digest(pin, CLIENT_GALLERY_DEFAULT_PIN)


def sanitize_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    """Strip filesystem paths — clients receive URLs only."""
    out = {
        "id": portfolio["id"],
        "category": portfolio["category"],
        "event_name": portfolio["event_name"],
        "detail_count": portfolio.get("detail_count", 0),
        "cover": {"url": portfolio["cover"]["url"]},
        "details": [{"url": d["url"], "index": d.get("index", i + 1)} for i, d in enumerate(portfolio.get("details", []))],
        "reel": None,
        "reel_stills": [{"url": s["url"]} for s in portfolio.get("reel_stills", [])],
    }
    if portfolio.get("reel"):
        out["reel"] = {"url": portfolio["reel"]["url"]}
    return out


class ClientGalleryService:
    def get_portfolio_for_client(self, portfolio_id: str) -> dict[str, Any] | None:
        raw = TourModeService().get_portfolio(portfolio_id)
        if not raw:
            return None
        return sanitize_portfolio(raw)

    def list_unlockable_events(self) -> list[dict[str, str]]:
        """Public metadata for PIN gate dropdown (no media URLs)."""
        items = TourModeService().scan_portfolios()
        return [
            {"id": p["id"], "event_name": p["event_name"], "category": p["category"]}
            for p in items
        ]
