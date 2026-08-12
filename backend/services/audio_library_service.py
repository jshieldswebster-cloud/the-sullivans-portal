"""AI Audio & Vibe Matcher — mood-organized royalty-free track library."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from backend.config import (
    AUDIO_DIR,
    AUDIO_LIBRARY_EXTENSIONS,
    AUDIO_LIBRARY_PATH,
    AUDIO_VIBES,
)
from backend.services.settings_store import read_json, write_json

logger = logging.getLogger(__name__)

SESSION_SELECTED_TRACK = "studio_selected_audio_track"


def _default_library() -> dict[str, Any]:
    """Seed library metadata — tracks live under assets/audio/{vibe_id}/."""
    vibes: list[dict[str, Any]] = []
    seed_tracks = {
        "soft_romantic": [
            ("golden_hour", "Golden Hour"),
            ("velvet_vows", "Velvet Vows"),
        ],
        "upbeat_celebration": [
            ("champagne_toast", "Champagne Toast"),
            ("dance_floor", "Dance Floor"),
        ],
        "corporate_minimal": [
            ("boardroom_pulse", "Boardroom Pulse"),
            ("summit_drive", "Summit Drive"),
        ],
        "ambient_luxe": [
            ("marble_hall", "Marble Hall"),
            ("candlelight", "Candlelight"),
        ],
    }
    for vibe in AUDIO_VIBES:
        tracks = []
        for slug, title in seed_tracks.get(vibe["id"], []):
            track_id = f"{vibe['id']}__{slug}"
            rel = f"{vibe['id']}/{slug}.mp3"
            tracks.append(
                {
                    "id": track_id,
                    "title": title,
                    "filename": rel,
                    "vibe_id": vibe["id"],
                }
            )
        vibes.append({**vibe, "tracks": tracks})

    first_track = vibes[0]["tracks"][0]["id"] if vibes and vibes[0]["tracks"] else ""
    return {"default_track_id": first_track, "vibes": vibes}


def _library_path() -> Path:
    path = AUDIO_LIBRARY_PATH
    if not path.is_file():
        write_json(path, _default_library())
    return path


def load_library() -> dict[str, Any]:
    try:
        return read_json(_library_path(), default=_default_library())
    except OSError as exc:
        logger.warning("Audio library load failed, using defaults: %s", exc)
        return _default_library()


def probe_audio_duration(path: Path) -> float | None:
    """Return audio duration in seconds via ffprobe."""
    if not path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (OSError, ValueError):
        pass
    return None


def _ensure_placeholder_track(rel_path: str, *, duration_sec: float = 45.0) -> Path:
    """Create a short royalty-free placeholder tone if the track file is missing."""
    dest = AUDIO_DIR / rel_path
    if dest.is_file():
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    vibe = dest.parent.name
    freqs = {
        "soft_romantic": 220,
        "upbeat_celebration": 440,
        "corporate_minimal": 330,
        "ambient_luxe": 280,
    }
    freq = freqs.get(vibe, 300)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={duration_sec}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "6",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("Could not generate placeholder audio %s: %s", dest, result.stderr)
    else:
        logger.info("Created placeholder audio track → %s", dest)
    return dest


class AudioLibraryService:
    """Browse vibes, resolve tracks, and prepare audio for reel duration matching."""

    def bootstrap_tracks(self) -> None:
        """Ensure library JSON and placeholder audio files exist."""
        lib = load_library()
        for vibe in lib.get("vibes", []):
            for track in vibe.get("tracks", []):
                rel = track.get("filename", "")
                if rel:
                    _ensure_placeholder_track(rel)

    def list_vibes(self) -> list[dict[str, Any]]:
        lib = load_library()
        out: list[dict[str, Any]] = []
        for vibe in lib.get("vibes", []):
            tracks = []
            for t in vibe.get("tracks", []):
                path = AUDIO_DIR / t["filename"]
                duration = probe_audio_duration(path) if path.is_file() else None
                tracks.append(
                    {
                        **t,
                        "path": str(path) if path.is_file() else None,
                        "url": f"/media/audio/{t['filename']}" if path.is_file() else None,
                        "duration_sec": duration,
                        "available": path.is_file(),
                    }
                )
            out.append(
                {
                    "id": vibe["id"],
                    "label": vibe["label"],
                    "description": vibe.get("description", ""),
                    "tracks": tracks,
                }
            )
        return out

    def get_track(self, track_id: str) -> dict[str, Any] | None:
        for vibe in self.list_vibes():
            for track in vibe["tracks"]:
                if track["id"] == track_id:
                    return {**track, "vibe_label": vibe["label"]}
        return None

    def resolve_track_path(self, track_id: str | None) -> Path | None:
        if not track_id:
            return None
        track = self.get_track(track_id)
        if not track:
            return None
        path = AUDIO_DIR / track["filename"]
        if not path.is_file():
            path = _ensure_placeholder_track(track["filename"])
        return path if path.is_file() else None

    def resolve_default_track_path(self) -> Path | None:
        lib = load_library()
        track_id = lib.get("default_track_id")
        if track_id:
            resolved = self.resolve_track_path(track_id)
            if resolved:
                return resolved
        for vibe in lib.get("vibes", []):
            for track in vibe.get("tracks", []):
                resolved = self.resolve_track_path(track["id"])
                if resolved:
                    return resolved
        return None

    def resolve_for_reel(
        self,
        *,
        audio_path: str | Path | None = None,
        audio_track_id: str | None = None,
    ) -> Path | None:
        """Pick explicit path, vibe track, or library default."""
        if audio_path:
            path = Path(audio_path)
            if path.is_file():
                return path
        if audio_track_id:
            resolved = self.resolve_track_path(audio_track_id)
            if resolved:
                return resolved
        return self.resolve_default_track_path()

    def set_default_track(self, track_id: str) -> bool:
        if not self.get_track(track_id):
            return False
        lib = load_library()
        lib["default_track_id"] = track_id
        write_json(AUDIO_LIBRARY_PATH, lib)
        return True

    def library_summary(self) -> dict[str, Any]:
        lib = load_library()
        vibes = self.list_vibes()
        total = sum(len(v["tracks"]) for v in vibes)
        available = sum(1 for v in vibes for t in v["tracks"] if t["available"])
        return {
            "default_track_id": lib.get("default_track_id"),
            "vibe_count": len(vibes),
            "track_count": total,
            "available_count": available,
            "vibes": vibes,
        }
