"""Application configuration and Apple Silicon device detection."""

from __future__ import annotations

import os
from pathlib import Path

import torch

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    """Load KEY=VALUE pairs from project .env (does not override existing env)."""
    env_path = ROOT_DIR / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()
def _env_path(key: str, default: Path) -> Path:
    raw = (os.getenv(key) or "").strip()
    return Path(raw) if raw else default


BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = _env_path("DATA_DIR", ROOT_DIR / "data")
UPLOADS_DIR = _env_path("UPLOADS_DIR", ROOT_DIR / "uploads")
OUTPUT_DIR = _env_path("OUTPUT_DIR", ROOT_DIR / "output")
ASSETS_DIR = ROOT_DIR / "assets"

VIDEOS_DIR = OUTPUT_DIR / "videos"
CAROUSELS_DIR = OUTPUT_DIR / "carousels"
CAPTIONS_DIR = OUTPUT_DIR / "captions"

DATABASE_PATH = _env_path("DATABASE_PATH", DATA_DIR / "vv_luxe.db")

FONTS_DIR = ASSETS_DIR / "fonts"
LOGOS_DIR = ASSETS_DIR / "logos"
AUDIO_DIR = ASSETS_DIR / "audio"

# Event categories for zero-shot CLIP classification and upload folders
EVENT_CATEGORIES: list[str] = [
    "Birthdays",
    "Weddings",
    "Baby Showers",
    "Venue",
    "Grad Party",
    "Corporate",
]

UNCATEGORIZED_LABEL = "Uncategorized"
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.35

# Weighted descriptive prompt phrases per category (luxury venue context)
CATEGORY_PROMPT_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "Baby Showers": [
        ("pastel decor", 1.0),
        ("balloon arches", 1.2),
        ("baby shower setup", 1.3),
        ("gentle floral centerpieces", 1.1),
        ("cake table", 1.0),
        ("soft lighting", 0.9),
    ],
    "Birthdays": [
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
    "Venue": [
        ("luxury event venue interior", 1.3),
        ("banquet hall architecture", 1.2),
        ("ambient uplighting", 1.1),
        ("empty venue setup", 1.0),
        ("Richmond California event space", 1.0),
    ],
    "Grad Party": [
        ("graduation celebration", 1.3),
        ("school colors decor", 1.1),
        ("milestone party setup", 1.2),
        ("photo backdrop", 1.0),
        ("festive banquet tables", 1.0),
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
MOTION_PUSH_IN_SCALE_START = 1.05
MOTION_PUSH_IN_SCALE_END = 1.08
MOTION_PAN_SHIFT_PX = 16
MOTION_TILT_SHIFT_PX = 14
MOTION_FG_PARALLAX_FACTOR = 0.28
MOTION_MAX_DISPLACEMENT_PX = 16
MOTION_EASE = "ease_in_out_cubic"

# Depth map smoothing & clamping (reduces tearing at fg/bg boundaries)
DEPTH_BILATERAL_D = 9
DEPTH_BILATERAL_SIGMA_COLOR = 0.06
DEPTH_BILATERAL_SIGMA_SPACE = 9
DEPTH_GAUSSIAN_KSIZE = 7
DEPTH_CLAMP_LOW = 0.08
DEPTH_CLAMP_HIGH = 0.90
DEPTH_MASK_BLUR_KSIZE = 31

# Encoding — high-bitrate VideoToolbox defaults
FFMPEG_VIDEO_BITRATE = "18M"
FFMPEG_VIDEO_MAXRATE = "22M"
FFMPEG_VIDEO_BUFSIZE = "36M"
FFMPEG_COLOR_FLAGS = [
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-colorspace", "bt709",
    "-color_range", "tv",
]

# When True, 9:16 crop keeps native pixel dimensions (no downscale to 1080×1920)
REEL_PRESERVE_NATIVE_RES = True

VALID_MOTIONS = ("push_in", "pan_left_right", "pan_left", "tilt_up")

# Multi-image montage assembler (Ken Burns + xfade — no depth warping)
MONTAGE_CLIP_DURATION_SEC = 4.0
MONTAGE_TRANSITION_SEC = 0.8
MONTAGE_KEN_BURNS_HEADROOM = 1.08
MONTAGE_KEN_BURNS_PAN_PX = 14
KEN_BURNS_MOTIONS = ("push_in", "pan_right", "pan_left", "push_in", "pan_up")
MONTAGE_MOTION_OPTIONS = {
    "auto": KEN_BURNS_MOTIONS,
    "push_in": ("push_in",),
    "pan_left": ("pan_left",),
    "pan_right": ("pan_right",),
    "pan_up": ("pan_up",),
}

# Studio web UI auth (override in production via env)
STUDIO_USERNAME = os.getenv("STUDIO_USERNAME", "vvluxe")
STUDIO_PASSWORD = os.getenv("STUDIO_PASSWORD", "vvluxe")
STUDIO_SECRET_KEY = os.getenv("STUDIO_SECRET_KEY", "vv-luxe-local-dev-secret-change-me")
SESSION_MAX_AGE_SEC = int(os.getenv("SESSION_MAX_AGE_SEC", "86400"))
CLIENT_VAULT_TTL_SEC = int(os.getenv("CLIENT_VAULT_TTL_SEC", "14400"))
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "10"))
LOGIN_RATE_WINDOW_SEC = int(os.getenv("LOGIN_RATE_WINDOW_SEC", "300"))

# Background job queue
JOB_QUEUE_MAX_WORKERS = int(os.getenv("JOB_QUEUE_MAX_WORKERS", "3"))

# FFmpeg encoder strategy: auto | videotoolbox | libx264
FFMPEG_ENCODER_MODE = os.getenv("FFMPEG_ENCODER", "auto")

# Persistent studio state paths
STUDIO_SETTINGS_PATH = DATA_DIR / "studio_settings.json"
CONTENT_CALENDAR_PATH = DATA_DIR / "content_calendar.json"
VINCENT_CHECKLISTS_DIR = DATA_DIR / "vincent_checklists"

# Google Drive integration
GOOGLE_DRIVE_CLIENT_ID = os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")
GOOGLE_DRIVE_CLIENT_SECRET = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")
# Forced production callback — exact Google Cloud Console value, no trailing slash.
GOOGLE_REDIRECT_URI = "https://studio.vvluxe.com/auth/callback"
GOOGLE_DRIVE_REDIRECT_URI = GOOGLE_REDIRECT_URI
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_DRIVE_MASTER_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_MASTER_FOLDER_NAME", "VV LUXE STUDIO")
GOOGLE_DRIVE_MASTER_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_MASTER_FOLDER_ID",
    "1z0DoWAcuOP7FfRZX8WXqfCDJozPhkg9-",
)
# Legacy alias — always points at the VV LUXE STUDIO master folder
GOOGLE_DRIVE_ROOT_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_ROOT_FOLDER_ID",
    GOOGLE_DRIVE_MASTER_FOLDER_ID,
)
GOOGLE_DRIVE_INDEX_PATH = DATA_DIR / "drive_index.json"
GOOGLE_DRIVE_OAUTH_TOKEN_PATH = DATA_DIR / "drive_oauth_token.json"
GOOGLE_DRIVE_REFRESH_TOKEN = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
GOOGLE_DRIVE_OAUTH_SETTINGS_KEY = "google_drive_oauth"
# Access tokens last ~60 minutes; refresh in the background before they lapse.
GOOGLE_DRIVE_TOKEN_REFRESH_INTERVAL_SEC = int(
    os.getenv("GOOGLE_DRIVE_TOKEN_REFRESH_INTERVAL_SEC", "2700")
)
# Web-app OAuth: OpenID identity scopes plus Drive read. Google may add openid
# on the token response even when not requested — relax strict scope matching.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    *GOOGLE_DRIVE_SCOPES,
]
GOOGLE_OAUTH_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_CERTS_URI = "https://www.googleapis.com/oauth2/v1/certs"
DRIVE_POST_2_COUNT = 8

# Daily backlog batch processor — 3 posts/day from VV LUXE STUDIO Drive
REVIEW_FOR_POSTING_QUEUE = "Review for Posting"
REVIEW_FOR_POSTING_DIR = UPLOADS_DIR / "Review_for_Posting"
DAILY_BACKLOG_POSTS_PER_DAY = int(os.getenv("DAILY_BACKLOG_POSTS_PER_DAY", "3"))
DAILY_BACKLOG_ENABLED = os.getenv("DAILY_BACKLOG_ENABLED", "1").lower() in ("1", "true", "yes")
DAILY_BACKLOG_RUN_HOUR_UTC = int(os.getenv("DAILY_BACKLOG_RUN_HOUR_UTC", "14"))  # 6 AM PT ≈ 14 UTC
DAILY_BACKLOG_SETTINGS_KEY = "daily_backlog_state"

# Render / cloud pipeline worker — Drive backlog → 3-post package → Canva
CLOUD_WORKER_POLL_SEC = int(os.getenv("CLOUD_WORKER_POLL_SEC", "300"))
CLOUD_WORKER_MAX_PER_TICK = int(os.getenv("CLOUD_WORKER_MAX_PER_TICK", "5"))
CLOUD_WORKER_EVENT_RETRY_SEC = float(os.getenv("CLOUD_WORKER_EVENT_RETRY_SEC", "8"))
CLOUD_WORKER_EVENT_MAX_ATTEMPTS = int(os.getenv("CLOUD_WORKER_EVENT_MAX_ATTEMPTS", "0"))  # 0 = until success
CLOUD_WORKER_REEL_WAIT_SEC = float(os.getenv("CLOUD_WORKER_REEL_WAIT_SEC", "90"))
CLOUD_WORKER_SETTINGS_KEY = "cloud_pipeline_worker"
CLOUD_WORKER_ERROR_LOG_KEY = "cloud_pipeline_errors"

# Canva Connect API — OAuth PKCE + autofill drafts
# Register this exact redirect URL in the Canva Developer Portal (no trailing slash).
CANVA_API_BASE = os.getenv("CANVA_API_BASE", "https://api.canva.com/rest/v1").rstrip("/")
CANVA_AUTH_URI = os.getenv("CANVA_AUTH_URI", "https://www.canva.com/api/oauth/authorize")
CANVA_TOKEN_URL = os.getenv("CANVA_TOKEN_URL", "https://api.canva.com/rest/v1/oauth/token")
CANVA_REDIRECT_URI = os.getenv(
    "CANVA_REDIRECT_URI",
    "https://studio.vvluxe.com/canva/callback",
).strip().rstrip("/")
CANVA_CLIENT_ID = os.getenv("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET", "")
CANVA_ACCESS_TOKEN = os.getenv("CANVA_ACCESS_TOKEN", "")
CANVA_REFRESH_TOKEN = os.getenv("CANVA_REFRESH_TOKEN", "")
CANVA_WEBHOOK_URL = os.getenv("CANVA_WEBHOOK_URL", "")
CANVA_OAUTH_SETTINGS_KEY = "canva_oauth"
CANVA_OAUTH_TOKEN_PATH = DATA_DIR / "canva_oauth_token.json"
# Access tokens last 4 hours; refresh in the background before they lapse.
CANVA_TOKEN_REFRESH_INTERVAL_SEC = int(os.getenv("CANVA_TOKEN_REFRESH_INTERVAL_SEC", "5400"))
CANVA_SCOPES = os.getenv(
    "CANVA_SCOPES",
    "asset:read asset:write design:content:write design:meta:read "
    "brandtemplate:content:read brandtemplate:meta:read",
)
CANVA_BRAND_TEMPLATE_ID = os.getenv("CANVA_BRAND_TEMPLATE_ID", "")
CANVA_BRAND_TEMPLATE_POST2_ID = os.getenv("CANVA_BRAND_TEMPLATE_POST2_ID", "")
CANVA_BRAND_TEMPLATE_POST3_ID = os.getenv("CANVA_BRAND_TEMPLATE_POST3_ID", "")
CANVA_DESIGN_PRESET = os.getenv("CANVA_DESIGN_PRESET", "instagramPost")

# VV LUXE Studio branding — title + location subtitle
STUDIO_BRAND_NAME = os.getenv("STUDIO_BRAND_NAME", "VV LUXE Studio")
STUDIO_TAGLINE = os.getenv("STUDIO_TAGLINE", "Sullivan Portal")

# Tour Portfolio Presentation Mode — color palette filters
TOUR_COLOR_PALETTES: list[dict[str, str]] = [
    {"id": "willow_cream", "label": "Willow Green & Cream"},
    {"id": "black_white", "label": "Black & White"},
    {"id": "gold_white", "label": "Gold & White"},
    {"id": "blush_neutrals", "label": "Blush & Luxury Neutrals"},
]

# Secure client gallery — view-only event access
CLIENT_GALLERY_DEFAULT_PIN = os.getenv("CLIENT_GALLERY_DEFAULT_PIN", "sullivan")
CLIENT_GALLERY_PINS_PATH = DATA_DIR / "client_gallery_pins.json"

# Carousel output (4:5 Instagram/LinkedIn)
CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350

# Branding defaults (montage reels use top-center logo)
DEFAULT_LOGO_OPACITY = 0.88
DEFAULT_LOGO_MARGIN = 56
DEFAULT_LOGO_ANCHOR = "top-center"
MONTAGE_LOGO_WIDTH_RATIO = 0.26
LOGO_ANCHORS: list[dict[str, str]] = [
    {"id": "top-left", "label": "Top Left"},
    {"id": "top-center", "label": "Top Center"},
    {"id": "top-right", "label": "Top Right"},
    {"id": "bottom-left", "label": "Bottom Left"},
    {"id": "bottom-right", "label": "Bottom Right"},
    {"id": "center", "label": "Center"},
]
WATERMARK_SETTINGS_PATH = DATA_DIR / "watermark_settings.json"

# AI Audio & Vibe Matcher — royalty-free track library
AUDIO_LIBRARY_PATH = DATA_DIR / "audio_library.json"
AUDIO_LIBRARY_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac")
AUDIO_VIBES: list[dict[str, str]] = [
    {
        "id": "soft_romantic",
        "label": "Soft Romantic",
        "description": "Warm strings and gentle piano — perfect for weddings and intimate celebrations.",
    },
    {
        "id": "upbeat_celebration",
        "label": "Upbeat Celebration",
        "description": "Bright, energetic rhythms for birthdays, galas, and high-energy moments.",
    },
    {
        "id": "corporate_minimal",
        "label": "Corporate Minimal",
        "description": "Clean, modern tones for conferences, brand launches, and professional events.",
    },
    {
        "id": "ambient_luxe",
        "label": "Ambient Luxe",
        "description": "Atmospheric textures and subtle beats for venue tours and decor showcases.",
    },
]

CAPTION_SYSTEM_PROMPT = """You are an elite luxury event marketing director for Sullivan Portal, an in-house production suite crafting premium social content. Write a sophisticated, engaging caption for a {category} event showcase. Highlight intentional design, mature elegance, and high-end hospitality. Include clean paragraph spacing, a clear call-to-action, and relevant hashtags (e.g., #SullivanPortal #InHouseProduction #LuxuryEvents)."""

VISION_EXTRACTION_PROMPT = """Analyze this high-resolution event venue photograph. Extract specific visual elements into bullet points:
- Primary and accent color palettes
- Table setup and decor features (e.g., chafing dishes, floral arches, glassware, linens)
- Lighting ambiance (e.g., warm uplighting, natural sunlight, neon accent)
- Architectural features (e.g., exposed structure, drapery, outdoor court)"""

CAPTION_ENRICHED_PROMPT = """You are the senior publicist for Sullivan Portal, an in-house production suite.
Generate a sophisticated, high-converting social media caption for a {category} post.

Visual Context detected in photo:
{visual_context}

Writing Rules:
- Highlight the specific visual details observed (colors, decor, lighting) to make the caption ultra-authentic.
- Maintain a mature, elevated, and welcoming tone.
- Emphasize bespoke event design and seamless hospitality.
- Include clean spacing, a call-to-action, and relevant hashtags (#SullivanPortal #InHouseProduction #LuxuryEvents).
- Do not output meta-commentary or introductory filler."""

TITLE_BATCH_PROMPT = """You are the creative director for Sullivan Portal, an in-house production suite.
Analyze this batch of {category} event photos and craft premium Instagram reel copy.

Visual context from the photo batch:
{visual_context}

Return EXACTLY three lines in this format (no extra text):
BOLD: <2-4 word punchy headline in ALL CAPS>
SCRIPT: <2-5 word elegant script-style subtitle in Title Case>
CAPTION: <One refined sentence for the reel description, max 120 characters>"""


def category_slug(name: str) -> str:
    """Filesystem-safe folder name for an event category."""
    return name.lower().replace(" ", "_")


def category_upload_dir(category: str) -> Path:
    """Return uploads subdirectory for a category, creating it if needed."""
    folder = UPLOADS_DIR / category_slug(category)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_device() -> torch.device:
    """Select best available PyTorch device for Apple Silicon."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def ensure_directories() -> None:
    """Create runtime directories if missing."""
    from backend.services.logo_overlay import ensure_default_logo

    for path in (
        DATA_DIR,
        UPLOADS_DIR,
        REVIEW_FOR_POSTING_DIR,
        VIDEOS_DIR,
        CAROUSELS_DIR,
        CAPTIONS_DIR,
        DEBUG_DIR,
        FONTS_DIR,
        LOGOS_DIR,
        AUDIO_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)

    for category in EVENT_CATEGORIES:
        category_upload_dir(category)

    ensure_default_logo()
