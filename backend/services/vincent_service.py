"""Vincent's in-house photography hub — ingest, checklist, Ideal Row push."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from backend.config import EVENT_CATEGORIES, UPLOADS_DIR, category_slug
from backend.services.ideal_row_service import (
    POST_2_CAROUSEL_COUNT,
    event_slug,
    ideal_row_paths,
    media_url,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vincent_checklists"

# 3-Post Ideal Row shot checklist for Vincent
VINCENT_SHOT_CHECKLIST: list[dict[str, Any]] = [
    {
        "id": "p1_establishing",
        "post": 1,
        "label": "Wide Establishing Shot / Event Cover Angle",
        "hint": "Full-room hero frame for Post 1 cover",
    },
    {
        "id": "p2_table_settings",
        "post": 2,
        "label": "Table Settings",
        "hint": "Place settings, linens, charger plates",
    },
    {
        "id": "p2_florals",
        "post": 2,
        "label": "Florals & Centerpieces",
        "hint": "Bouquets, arches, bud vases",
    },
    {
        "id": "p2_signage",
        "post": 2,
        "label": "Custom Signage",
        "hint": "Welcome signs, seating charts, branded elements",
    },
    {
        "id": "p2_lighting",
        "post": 2,
        "label": "Lighting & Ambiance",
        "hint": "Uplighting, candles, natural light play",
    },
    {
        "id": "p2_textures",
        "post": 2,
        "label": "Textures & Materials",
        "hint": "Fabric, wood, metal, glass close-ups",
    },
    {
        "id": "p2_food_bar",
        "post": 2,
        "label": "Food Display / Bar",
        "hint": "Catering staging, dessert table, bar setup",
    },
    {
        "id": "p2_guest_detail",
        "post": 2,
        "label": "Guest Experience Detail",
        "hint": "Favors, programs, curated touchpoints",
    },
    {
        "id": "p2_venue_angle",
        "post": 2,
        "label": "Signature Venue Angle",
        "hint": "Architectural frame unique to the event",
    },
    {
        "id": "p3_hero_motion",
        "post": 3,
        "label": "Hero Motion Clip / Still",
        "hint": "Opening reel frame — push-in or pan",
    },
    {
        "id": "p3_guest_energy",
        "post": 3,
        "label": "Guest Energy Moment",
        "hint": "Candid celebration for reel sequence",
    },
    {
        "id": "p3_detail_pan",
        "post": 3,
        "label": "Detail Pan / Macro",
        "hint": "Slow detail pass for reel b-roll",
    },
    {
        "id": "p3_finale",
        "post": 3,
        "label": "Finale / Send-off Shot",
        "hint": "Closing reel frame with brand presence",
    },
]


def vincent_ingest_dir(category: str, event_name: str) -> Path:
    base = UPLOADS_DIR / category_slug(category) / event_slug(event_name) / "Vincent_Ingest"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _checklist_path(category: str, event_name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{category_slug(category)}__{event_slug(event_name)}.json"
    return DATA_DIR / key


def default_checklist_state() -> dict[str, Any]:
    return {
        shot["id"]: {"checked": False, "file_path": None, "file_url": None}
        for shot in VINCENT_SHOT_CHECKLIST
    }


class VincentService:
    ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}

    def get_checklist(self, category: str, event_name: str) -> dict[str, Any]:
        path = _checklist_path(category, event_name)
        state = default_checklist_state()
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            state.update(saved.get("shots", {}))
        return {
            "category": category,
            "event_name": event_name,
            "shots": VINCENT_SHOT_CHECKLIST,
            "state": state,
            "progress": self._progress(state),
        }

    def save_checklist_state(
        self,
        category: str,
        event_name: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        path = _checklist_path(category, event_name)
        payload = {
            "category": category,
            "event_name": event_name,
            "shots": state,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.get_checklist(category, event_name)

    def _progress(self, state: dict[str, Any]) -> dict[str, Any]:
        post1 = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 1]
        post2 = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 2]
        post3 = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 3]

        def done(items: list[dict]) -> int:
            return sum(1 for i in items if state.get(i["id"], {}).get("checked"))

        return {
            "post_1": {"done": done(post1), "total": len(post1)},
            "post_2": {"done": done(post2), "total": len(post2)},
            "post_3": {"done": done(post3), "total": len(post3)},
        }

    def batch_upload(
        self,
        category: str,
        event_name: str,
        files: list[tuple[str, bytes]],
        *,
        shot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if category not in EVENT_CATEGORIES:
            raise ValueError(f"Invalid category: {category}")

        dest_root = vincent_ingest_dir(category, event_name)
        if shot_id:
            dest_root = dest_root / shot_id
        dest_root.mkdir(parents=True, exist_ok=True)

        saved: list[dict[str, Any]] = []
        for name, data in files:
            ext = Path(name).suffix.lower()
            if ext not in self.ALLOWED_EXT:
                continue
            safe = f"{uuid.uuid4().hex}{ext}"
            dest = dest_root / safe
            dest.write_bytes(data)
            entry = {
                "filename": safe,
                "path": str(dest),
                "url": media_url(dest),
                "shot_id": shot_id,
            }
            saved.append(entry)

        if shot_id and saved and len(saved) == 1:
            path = _checklist_path(category, event_name)
            state = default_checklist_state()
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                state.update(raw.get("shots", {}))
            state[shot_id] = {
                "checked": True,
                "file_path": saved[0]["path"],
                "file_url": saved[0]["url"],
            }
            self.save_checklist_state(category, event_name, state)

        return saved

    def push_to_ideal_row(self, category: str, event_name: str) -> dict[str, Any]:
        """Copy checklist-assigned assets into Ideal_Row_Posts folders."""
        checklist = self.get_checklist(category, event_name)
        state = checklist["state"]
        paths = ideal_row_paths(category, event_name)

        post1_shots = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 1]
        post2_shots = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 2]
        post3_shots = [s for s in VINCENT_SHOT_CHECKLIST if s["post"] == 3]

        p1 = next((state.get(s["id"], {}) for s in post1_shots if state.get(s["id"], {}).get("file_path")), None)
        if not p1 or not p1.get("file_path"):
            raise ValueError("Post 1 cover shot not assigned — check off and upload establishing shot")

        p2_files = [
            state[s["id"]]["file_path"]
            for s in post2_shots
            if state.get(s["id"], {}).get("file_path")
        ]
        if len(p2_files) < POST_2_CAROUSEL_COUNT:
            raise ValueError(
                f"Post 2 needs {POST_2_CAROUSEL_COUNT} detail shots assigned (have {len(p2_files)})"
            )
        p2_files = p2_files[:POST_2_CAROUSEL_COUNT]

        p3_files = [
            state[s["id"]]["file_path"]
            for s in post3_shots
            if state.get(s["id"], {}).get("file_path")
        ]
        if not p3_files:
            raise ValueError("Post 3 needs at least one reel asset assigned")

        paths["post_1"].mkdir(parents=True, exist_ok=True)
        paths["post_2"].mkdir(parents=True, exist_ok=True)
        paths["post_3"].mkdir(parents=True, exist_ok=True)

        cover_dest = paths["post_1"] / f"cover{Path(p1['file_path']).suffix}"
        shutil.copy2(p1["file_path"], cover_dest)

        for idx, src in enumerate(p2_files, start=1):
            ext = Path(src).suffix
            shutil.copy2(src, paths["post_2"] / f"photo_{idx:02d}{ext}")

        for idx, src in enumerate(p3_files, start=1):
            ext = Path(src).suffix
            shutil.copy2(src, paths["post_3"] / f"reel_{idx:02d}{ext}")

        return {
            "category": category,
            "event_name": event_name,
            "base_path": str(paths["base"]),
            "post_1": str(cover_dest),
            "post_2_count": len(p2_files),
            "post_3_count": len(p3_files),
            "dashboard_url": f"/dashboard?category={category}&event={event_name}",
        }
