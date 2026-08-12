"""Category-tailored Instagram captions for the 3-post Ideal Row."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import EVENT_CATEGORIES

logger = logging.getLogger(__name__)

# (title_bold, title_script, body) per post role — fallback when Ollama is offline
IDEAL_ROW_CAPTION_TEMPLATES: dict[str, dict[str, tuple[str, str, str]]] = {
    "Birthdays": {
        "post_1": ("CELEBRATE", "In Style", "Milestone birthdays deserve a Sullivan Portal moment."),
        "post_2": ("THE DETAILS", "Every Corner", "Eight looks at the decor that set the tone for an unforgettable night."),
        "post_3": ("THE ENERGY", "In Motion", "Relive the celebration — book your birthday walk-through today."),
    },
    "Weddings": {
        "post_1": ("FOREVER", "Begins Here", "Your wedding story deserves a venue as intentional as your love."),
        "post_2": ("EVERY DETAIL", "Curated", "From florals to tablescapes — swipe through eight moments of pure elegance."),
        "post_3": ("YOUR DAY", "In Motion", "A cinematic glimpse of forever. Crafted by Sullivan Portal."),
    },
    "Baby Showers": {
        "post_1": ("SWEET", "Arrivals", "Soft luxury for life's most tender celebrations."),
        "post_2": ("PASTEL PERFECTION", "Every Detail", "Eight frames of gentle decor crafted with care."),
        "post_3": ("THE JOY", "In Motion", "Celebrate the arrival in motion — book your shower consultation today."),
    },
    "Venue": {
        "post_1": ("THE SPACE", "Speaks Luxury", "An intentional event destination — discover Sullivan Portal."),
        "post_2": ("DESIGN DETAILS", "Up Close", "Eight perspectives on the architecture, lighting, and ambiance."),
        "post_3": ("THE EXPERIENCE", "In Motion", "See the venue come alive — schedule your private walk-through."),
    },
    "Grad Party": {
        "post_1": ("MILESTONE", "Achieved", "Honor the graduate with a celebration elevated in-house."),
        "post_2": ("THE SETUP", "Styled", "Eight detail shots capturing school spirit and sophisticated decor."),
        "post_3": ("THE MOMENT", "In Motion", "The celebration, captured. Book your grad party consultation today."),
    },
    "Corporate": {
        "post_1": ("ELEVATED", "Gatherings", "Professional events with boutique hospitality at Sullivan Portal."),
        "post_2": ("THE PRODUCTION", "Refined", "Eight frames of polished presentation, seating, and branded decor."),
        "post_3": ("THE IMPACT", "In Motion", "Your event, professionally captured. Inquire for corporate availability."),
    },
}

HASHTAGS = "#SullivanPortal #InHouseProduction #LuxuryEvents #EventPhotography"

OLLAMA_ROW_CAPTION_PROMPT = """You are the social media director for Sullivan Portal, an in-house production suite.
Write Instagram copy for a 3-post "Ideal Row" about a {category} event named "{event_name}".

Visual context:
{visual_context}

Return EXACTLY these 9 lines (no extra text):
POST1_BOLD: <2-4 word ALL CAPS headline>
POST1_SCRIPT: <2-5 word elegant script-style subtitle in Title Case>
POST1_CAPTION: <one sentence, max 100 chars>
POST2_BOLD: <2-4 word ALL CAPS headline about decor/details>
POST2_SCRIPT: <2-5 word script subtitle>
POST2_CAPTION: <one sentence inviting swipe, max 100 chars>
POST3_BOLD: <2-4 word ALL CAPS headline for reel>
POST3_SCRIPT: <2-5 word script subtitle>
POST3_CAPTION: <one sentence with booking CTA, max 120 chars>"""


@dataclass
class PostCaption:
    post_key: str
    title_bold: str
    title_script: str
    body: str
    formatted_html: str
    instagram_text: str
    source: str = "template"

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_key": self.post_key,
            "title_bold": self.title_bold,
            "title_script": self.title_script,
            "body": self.body,
            "formatted_html": self.formatted_html,
            "instagram_text": self.instagram_text,
            "source": self.source,
        }


def _format_html(bold: str, script: str) -> str:
    return (
        f'<span class="title-bold">{bold.strip()}</span> '
        f'<span class="title-script">{script.strip()}</span>'
    )


def _format_instagram_text(bold: str, script: str, body: str, event_name: str) -> str:
    return (
        f"{bold.strip().upper()}\n"
        f"{script.strip()}\n\n"
        f"{body.strip()}\n\n"
        f"{event_name.strip()} · Sullivan Portal\n"
        f"{HASHTAGS}"
    )


def caption_from_template(
    category: str,
    post_key: str,
    event_name: str,
) -> PostCaption:
    templates = IDEAL_ROW_CAPTION_TEMPLATES.get(category, IDEAL_ROW_CAPTION_TEMPLATES["Venue"])
    bold, script, body = templates.get(
        post_key,
        ("Sullivan Portal", "In-House", "Luxury events, intentionally designed."),
    )
    return PostCaption(
        post_key=post_key,
        title_bold=bold,
        title_script=script,
        body=body,
        formatted_html=_format_html(bold, script),
        instagram_text=_format_instagram_text(bold, script, body, event_name),
        source="template",
    )


def _parse_row_caption_response(raw: str) -> dict[str, dict[str, str]]:
    keys = (
        "POST1_BOLD", "POST1_SCRIPT", "POST1_CAPTION",
        "POST2_BOLD", "POST2_SCRIPT", "POST2_CAPTION",
        "POST3_BOLD", "POST3_SCRIPT", "POST3_CAPTION",
    )
    parsed: dict[str, str] = {k: "" for k in keys}
    for line in raw.splitlines():
        stripped = line.strip()
        for key in keys:
            if stripped.upper().startswith(f"{key}:"):
                parsed[key] = stripped.split(":", 1)[1].strip()

    return {
        "post_1": {
            "bold": parsed["POST1_BOLD"],
            "script": parsed["POST1_SCRIPT"],
            "body": parsed["POST1_CAPTION"],
        },
        "post_2": {
            "bold": parsed["POST2_BOLD"],
            "script": parsed["POST2_SCRIPT"],
            "body": parsed["POST2_CAPTION"],
        },
        "post_3": {
            "bold": parsed["POST3_BOLD"],
            "script": parsed["POST3_SCRIPT"],
            "body": parsed["POST3_CAPTION"],
        },
    }


async def generate_ideal_row_captions(
    category: str,
    event_name: str,
    *,
    image_paths: list[str | Path] | None = None,
    caption_engine: Any | None = None,
    visual_context: str | None = None,
) -> dict[str, PostCaption]:
    """Generate captions for all three posts; falls back to category templates."""
    result: dict[str, PostCaption] = {}

    if caption_engine is None:
        for key in ("post_1", "post_2", "post_3"):
            result[key] = caption_from_template(category, key, event_name)
        return result

    ollama_ok = await caption_engine.health_check()
    if not ollama_ok:
        logger.warning("Ollama unavailable — using Ideal Row caption templates")
        for key in ("post_1", "post_2", "post_3"):
            result[key] = caption_from_template(category, key, event_name)
        return result

    ctx = visual_context or f"Luxury {category} event by Sullivan Portal."
    prompt = OLLAMA_ROW_CAPTION_PROMPT.format(
        category=category,
        event_name=event_name,
        visual_context=ctx,
    )
    payload = {
        "model": caption_engine.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.72, "num_predict": 400},
    }

    try:
        import httpx

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{caption_engine.base_url.rstrip('/')}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
        parsed = _parse_row_caption_response(raw)
    except Exception as exc:
        logger.warning("Ideal Row caption generation failed: %s", exc)
        for key in ("post_1", "post_2", "post_3"):
            result[key] = caption_from_template(category, key, event_name)
        return result

    for post_key, fields in parsed.items():
        if not fields.get("bold"):
            result[post_key] = caption_from_template(category, post_key, event_name)
            continue
        result[post_key] = PostCaption(
            post_key=post_key,
            title_bold=fields["bold"],
            title_script=fields["script"] or "Sullivan Portal",
            body=fields["body"],
            formatted_html=_format_html(fields["bold"], fields["script"] or "Sullivan Portal"),
            instagram_text=_format_instagram_text(
                fields["bold"], fields["script"] or "Sullivan Portal", fields["body"], event_name
            ),
            source="ollama",
        )

    return result


def write_caption_files(dest_dir: Path, caption: PostCaption) -> dict[str, str]:
    """Write caption artifacts into a post export folder; return paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    plain = dest_dir / "caption.txt"
    ig = dest_dir / "caption_instagram.txt"
    typo = dest_dir / "caption_typography.html"

    plain.write_text(
        f"{caption.title_bold.upper()}\n{caption.title_script}\n\n{caption.body}\n",
        encoding="utf-8",
    )
    ig.write_text(caption.instagram_text, encoding="utf-8")
    typo.write_text(
        f"{caption.formatted_html}\n<p>{caption.body}</p>",
        encoding="utf-8",
    )

    return {
        "caption_txt": str(plain),
        "caption_instagram_txt": str(ig),
        "caption_typography_html": str(typo),
    }
