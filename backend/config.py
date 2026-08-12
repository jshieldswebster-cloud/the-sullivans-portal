"""Application configuration and Apple Silicon device detection."""

from __future__ import annotations

import os
from pathlib import Path

import torch

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = ROOT_DIR / "data"
UPLOADS_DIR = ROOT_DIR / "uploads"
OUTPUT_DIR = ROOT_DIR / "output"
ASSETS_DIR = ROOT_DIR / "assets"

VIDEOS_DIR = OUTPUT_DIR / "videos"
CAROUSELS_DIR = OUTPUT_DIR / "carousels"
CAPTIONS_DIR = OUTPUT_DIR / "captions"

DATABASE_PATH = DATA_DIR / "vv_luxe.db"

FONTS_DIR = ASSETS_DIR / "fonts"
LOGOS_DIR = ASSETS_DIR / "logos"
AUDIO_DIR = ASSETS_DIR / "audio"

# Event categories for zero-shot CLIP classification
EVENT_CATEGORIES: list[str] = [
    "Baby Shower",
    "Birthday",
    "Corporate",
    "Weddings",
    "Legacy Receptions",
]

UNCATEGORIZED_LABEL = "Uncategorized"
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.35

# Weighted descriptive prompt phrases per category (luxury venue context)
CATEGORY_PROMPT_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "Baby Shower": [
        ("pastel decor", 1.0),
        ("balloon arches", 1.2),
        ("baby shower setup", 1.3),
        ("gentle floral centerpieces", 1.1),
        ("cake table", 1.0),
        ("soft lighting", 0.9),
    ],
    "Birthday": [
        ("birthday party setup", 1.3),
        ("milestone celebration decor", 1.2),
        ("LED neon signs", 1.1),
        ("dramatic accent lighting", 1.0),
        ("cocktail tables", 1.0),
    ],
    "Corporate": [
        ("corporate banquet", 1.3),
        ("formal presentation layout", 1.2),
        ("modular seating", 1.0),
        ("institutional event", 1.0),
        ("clean professional setup", 1.1),
    ],
    "Weddings": [
        ("wedding reception", 1.3),
        ("white floral arrangements", 1.2),
        ("elegant head table", 1.1),
        ("gold chafing dishes", 1.0),
        ("luxury table settings", 1.2),
        ("bridal decor", 1.3),
    ],
    "Legacy Receptions": [
        ("memorial reception", 1.3),
        ("intimate family gathering banquet", 1.2),
        ("warm formal seating", 1.0),
        ("understated luxury decor", 1.1),
    ],
}

# CLIP prompt wrapper — frames each phrase as a luxury venue photograph
CLIP_PROMPT_TEMPLATE = "luxury event venue photograph featuring {phrase}"

# Legacy alias kept for imports; built dynamically from weighted phrases
CATEGORY_PROMPTS: dict[str, list[str]] = {
    category: [
        CLIP_PROMPT_TEMPLATE.format(phrase=phrase)
        for phrase, _weight in phrases
    ]
    for category, phrases in CATEGORY_PROMPT_WEIGHTS.items()
}

# Ollama settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:8b")

# Vision model selection: "clip" | "florence2"
VISION_MODEL = os.getenv("VISION_MODEL", "clip")

# Caption enrichment vision backend: "moondream2" | "florence2"
CAPTION_VISION_MODEL = os.getenv("CAPTION_VISION_MODEL", "moondream2")
MOONDREAM2_MODEL_ID = os.getenv("MOONDREAM2_MODEL_ID", "vikhyatk/moondream2")
FLORENCE2_MODEL_ID = os.getenv("FLORENCE2_MODEL_ID", "microsoft/Florence-2-base")

DEBUG_DIR = OUTPUT_DIR / "debug"

# Depth model: Small (default) or Base via env
DEPTH_MODEL_ID = os.getenv("DEPTH_MODEL_ID", "depth-anything/Depth-Anything-V2-Small-hf")
DEPTH_MODEL_BASE_ID = "depth-anything/Depth-Anything-V2-Base-hf"

# Video output (9:16 reels)
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_FPS = 30
REEL_FPS_HIGH = 60
REEL_DURATION_SEC = 8.0

# 2.5D motion presets (rigid architecture — affine only, no generative warp)
MOTION_PUSH_IN_SCALE_START = 1.0
MOTION_PUSH_IN_SCALE_END = 1.15
MOTION_PAN_SHIFT_PX = 20
MOTION_TILT_SHIFT_PX = 18
MOTION_FG_PARALLAX_FACTOR = 0.35
MOTION_EASE = "ease_in_out_cubic"

VALID_MOTIONS = ("push_in", "pan_left_right", "pan_left", "tilt_up")

# Carousel output (4:5 Instagram/LinkedIn)
CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350

# Branding defaults
DEFAULT_LOGO_OPACITY = 0.85
DEFAULT_LOGO_MARGIN = 48
DEFAULT_LOGO_ANCHOR = "bottom-right"

CAPTION_SYSTEM_PROMPT = """You are an elite luxury venue marketing director for VV LUXE, an exclusive event space in Richmond, California. Write a sophisticated, engaging caption for a {category} event showcase. Highlight intentional design, mature elegance, and high-end hospitality. Include clean paragraph spacing, a clear call-to-action to book a venue tour, and relevant high-converting local hashtags (e.g., #RichmondCAEvents #BayAreaVenue #BayAreaEvents #VVLUXE)."""

VISION_EXTRACTION_PROMPT = """Analyze this high-resolution event venue photograph. Extract specific visual elements into bullet points:
- Primary and accent color palettes
- Table setup and decor features (e.g., chafing dishes, floral arches, glassware, linens)
- Lighting ambiance (e.g., warm uplighting, natural sunlight, neon accent)
- Architectural features (e.g., exposed structure, drapery, outdoor court)"""

CAPTION_ENRICHED_PROMPT = """You are the senior publicist for VV LUXE, an intentional luxury event space in Richmond, California.
Generate a sophisticated, high-converting social media caption for a {category} post.

Visual Context detected in photo:
{visual_context}

Writing Rules:
- Highlight the specific visual details observed (colors, decor, lighting) to make the caption ultra-authentic.
- Maintain a mature, elevated, and welcoming tone.
- Emphasize bespoke event design and seamless hospitality.
- Include clean spacing, a call-to-action to schedule a venue walk-through, and relevant local hashtags (#RichmondCAEvents #BayAreaLuxury #BayAreaVenue #VVLUXE).
- Do not output meta-commentary or introductory filler."""


def get_device() -> torch.device:
    """Select best available PyTorch device for Apple Silicon."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def ensure_directories() -> None:
    """Create runtime directories if missing."""
    for path in (
        DATA_DIR,
        UPLOADS_DIR,
        VIDEOS_DIR,
        CAROUSELS_DIR,
        CAPTIONS_DIR,
        DEBUG_DIR,
        FONTS_DIR,
        LOGOS_DIR,
        AUDIO_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
