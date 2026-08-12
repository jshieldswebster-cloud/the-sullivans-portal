"""Montage job helpers — render options extraction (queue lives in job_queue.py)."""

from __future__ import annotations

from typing import Any

from backend.services.job_queue import montage_jobs, run_job_in_background

__all__ = ["montage_jobs", "run_job_in_background", "resolve_render_options"]


def resolve_render_options(meta: dict[str, Any]) -> dict[str, Any]:
    """Extract audio vibe track and watermark settings from montage job metadata."""
    return {
        "audio_path": meta.get("audio_path"),
        "audio_track_id": meta.get("audio_track_id"),
        "logo_path": meta.get("logo_path"),
        "logo_opacity": meta.get("logo_opacity"),
        "logo_anchor": meta.get("logo_anchor"),
        "logo_width_ratio": meta.get("logo_width_ratio"),
        "logo_margin": meta.get("logo_margin"),
    }
