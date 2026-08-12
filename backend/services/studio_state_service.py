"""Persistent studio state — events, calendar, categories, settings sync."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.config import (
    CONTENT_CALENDAR_PATH,
    EVENT_CATEGORIES,
    STUDIO_SETTINGS_PATH,
    category_slug,
)
from backend.database import (
    delete_calendar_entry,
    get_studio_setting,
    list_calendar_entries,
    list_events,
    set_studio_setting,
    upsert_calendar_entry,
    upsert_event,
)
from backend.services.settings_store import read_json, write_json

logger = logging.getLogger(__name__)

SETTINGS_WATERMARK = "watermark"
SETTINGS_AUDIO = "audio_library"
SETTINGS_CATEGORIES = "event_categories"


class StudioStateService:
    """Central persistence for events, calendar, and studio configuration."""

    def sync_categories_to_db(self) -> list[str]:
        """Persist current event categories; allow JSON override."""
        stored = get_studio_setting(SETTINGS_CATEGORIES, {})
        categories = stored.get("categories") or list(EVENT_CATEGORIES)
        write_json(
            STUDIO_SETTINGS_PATH,
            {"event_categories": categories, "source": "database"},
        )
        set_studio_setting(SETTINGS_CATEGORIES, {"categories": categories})
        return categories

    def get_categories(self) -> list[str]:
        stored = get_studio_setting(SETTINGS_CATEGORIES, {})
        return stored.get("categories") or list(EVENT_CATEGORIES)

    def save_categories(self, categories: list[str]) -> list[str]:
        clean = [c.strip() for c in categories if c.strip()]
        set_studio_setting(SETTINGS_CATEGORIES, {"categories": clean})
        write_json(STUDIO_SETTINGS_PATH, {"event_categories": clean})
        return clean

    def register_ideal_row_event(
        self,
        *,
        category: str,
        event_name: str,
        ideal_row_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        slug = event_name.replace(" ", "_")
        event_id = f"{category_slug(category)}__{slug}"
        upsert_event(
            event_id,
            category=category,
            event_slug=slug,
            event_name=event_name,
            ideal_row_path=ideal_row_path,
            metadata=metadata,
        )
        return event_id

    def list_registered_events(self, category: str | None = None) -> list[dict[str, Any]]:
        return list_events(category=category)

    def migrate_calendar_json_to_db(self) -> int:
        """Import legacy content_calendar.json into SQLite if present."""
        raw = read_json(CONTENT_CALENDAR_PATH, default={"entries": []})
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        count = 0
        for entry in entries:
            entry_id = entry.get("id") or str(uuid.uuid4())
            upsert_calendar_entry(
                entry_id,
                category=entry.get("category", ""),
                event_name=entry.get("event_name", ""),
                scheduled_at=entry.get("scheduled_at", ""),
                post_number=entry.get("post_number"),
                event_id=entry.get("event_id"),
                status=entry.get("status", "scheduled"),
                notes=entry.get("notes", ""),
            )
            count += 1
        return count

    def list_calendar(self, **kwargs: Any) -> list[dict[str, Any]]:
        entries = list_calendar_entries(**kwargs)
        if not entries:
            self.migrate_calendar_json_to_db()
            entries = list_calendar_entries(**kwargs)
        return entries

    def save_calendar_entry(
        self,
        *,
        category: str,
        event_name: str,
        scheduled_at: str,
        post_number: int | None = None,
        event_id: str | None = None,
        status: str = "scheduled",
        notes: str = "",
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        eid = entry_id or str(uuid.uuid4())
        upsert_calendar_entry(
            eid,
            category=category,
            event_name=event_name,
            scheduled_at=scheduled_at,
            post_number=post_number,
            event_id=event_id,
            status=status,
            notes=notes,
        )
        self._export_calendar_json()
        return {"id": eid, "category": category, "event_name": event_name, "scheduled_at": scheduled_at}

    def remove_calendar_entry(self, entry_id: str) -> None:
        delete_calendar_entry(entry_id)
        self._export_calendar_json()

    def _export_calendar_json(self) -> None:
        """Keep JSON backup in sync with DB for portability."""
        entries = list_calendar_entries()
        write_json(CONTENT_CALENDAR_PATH, {"entries": entries})

    def persist_watermark_settings(self, settings: dict[str, Any]) -> None:
        set_studio_setting(SETTINGS_WATERMARK, settings)

    def get_watermark_settings_backup(self) -> dict[str, Any]:
        return get_studio_setting(SETTINGS_WATERMARK, {})

    def bootstrap(self) -> None:
        """Initialize persistent state on startup."""
        self.sync_categories_to_db()
        migrated = self.migrate_calendar_json_to_db()
        if migrated:
            logger.info("Migrated %d calendar entries from JSON to database", migrated)
