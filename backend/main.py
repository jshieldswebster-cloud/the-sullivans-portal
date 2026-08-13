"""
VV LUXE Studio — FastAPI backend entry point.

Initializes local inference models (CLIP/Florence-2, Depth Anything V2, Ollama)
and exposes REST endpoints for the Electron frontend.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# Allow running as `python backend/main.py` or `uvicorn backend.main:app`
ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import (  # noqa: E402
    AUDIO_DIR,
    CAPTIONS_DIR,
    CAROUSELS_DIR,
    DATA_DIR,
    JOB_QUEUE_MAX_WORKERS,
    LOGOS_DIR,
    OUTPUT_DIR,
    SESSION_MAX_AGE_SEC,
    STUDIO_SECRET_KEY,
    UPLOADS_DIR,
    VIDEOS_DIR,
    ensure_directories,
    get_device,
)
from backend.database import count_running_studio_jobs, init_db  # noqa: E402
from backend.models.caption_engine import CaptionEngine
from backend.models.caption_enricher import CaptionEnricher
from backend.models.carousel_compositor import CarouselCompositor
from backend.models.classifier import EventClassifier
from backend.models.depth_engine import DepthParallaxEngine
from backend.routers import captions, classify, render, studio, upload  # noqa: E402
from backend.services.caption_service import CaptionService  # noqa: E402
from backend.services.render_service import RenderService  # noqa: E402
from backend.services.upload_service import UploadService  # noqa: E402
import backend.services.video_service  # noqa: F401 — DepthEngine render compat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vv-luxe")


def ensure_runtime_directories() -> None:
    """Create output, upload, and media directories before StaticFiles mounts."""
    for path in (
        DATA_DIR,
        OUTPUT_DIR,
        UPLOADS_DIR,
        VIDEOS_DIR,
        CAROUSELS_DIR,
        CAPTIONS_DIR,
        AUDIO_DIR,
        LOGOS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


@dataclass
class AppState:
    classifier: EventClassifier
    depth_engine: DepthParallaxEngine
    carousel: CarouselCompositor
    caption_engine: CaptionEngine
    caption_enricher: CaptionEnricher
    caption_service: CaptionService
    upload_service: UploadService
    render_service: RenderService
    device: torch.device
    models_loaded: bool = False


app_state = AppState(
    classifier=EventClassifier(),
    depth_engine=DepthParallaxEngine(),
    carousel=CarouselCompositor(),
    caption_engine=CaptionEngine(),
    caption_enricher=CaptionEnricher(),
    caption_service=CaptionService(),
    upload_service=UploadService(),
    render_service=RenderService(),
    device=get_device(),
)


def initialize_models(*, eager: bool = False) -> None:
    """Load ML models. Set eager=True to load all at startup."""
    ensure_directories()
    init_db()

    from backend.services.audio_library_service import AudioLibraryService
    from backend.services.ffmpeg_diagnostics import run_ffmpeg_diagnostics
    from backend.services.job_queue import job_manager
    from backend.services.studio_state_service import StudioStateService
    from backend.services.daily_backlog_worker import daily_backlog_scheduler
    from backend.services.drive_service import drive_token_refresh_scheduler
    from backend.services.canva_service import canva_token_refresh_scheduler

    AudioLibraryService().bootstrap_tracks()
    StudioStateService().bootstrap()
    ffmpeg_diag = run_ffmpeg_diagnostics(test_encode=True)
    if not ffmpeg_diag.ffmpeg_available:
        logger.error("FFmpeg unavailable — video rendering will fail")
    elif ffmpeg_diag.probe_errors:
        for err in ffmpeg_diag.probe_errors:
            logger.warning("FFmpeg diagnostic: %s", err)

    job_manager.ensure_handlers()
    daily_backlog_scheduler.start()
    drive_token_refresh_scheduler.start()
    canva_token_refresh_scheduler.start()
    logger.info("Job queue ready (%d workers)", JOB_QUEUE_MAX_WORKERS)

    logger.info("PyTorch device: %s", app_state.device)
    logger.info("MPS available: %s", torch.backends.mps.is_available())

    if eager:
        logger.info("Eager-loading CLIP classifier...")
        app_state.classifier.load()
        logger.info("Eager-loading Depth Anything V2...")
        app_state.depth_engine.load()
        app_state.upload_service.classifier = app_state.classifier
        app_state.render_service.depth = app_state.depth_engine
        app_state.render_service.carousel = app_state.carousel
        app_state.render_service.captions = app_state.caption_engine
        app_state.render_service.caption_service = app_state.caption_service
        app_state.caption_service.classifier = app_state.classifier
        app_state.caption_service.enricher = app_state.caption_enricher
        app_state.caption_service.caption_engine = app_state.caption_engine

    app_state.models_loaded = True
    logger.info("Backend ready.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    eager = app.state.eager_load if hasattr(app.state, "eager_load") else False
    initialize_models(eager=eager)
    yield
    from backend.services.job_queue import job_manager
    from backend.services.daily_backlog_worker import daily_backlog_scheduler
    from backend.services.drive_service import drive_token_refresh_scheduler
    from backend.services.canva_service import canva_token_refresh_scheduler

    job_manager.shutdown(wait=True)
    daily_backlog_scheduler.stop()
    drive_token_refresh_scheduler.stop()
    canva_token_refresh_scheduler.stop()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="VV LUXE Studio API",
    description="VV LUXE Studio · Sullivan Portal — in-house content production",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=STUDIO_SECRET_KEY, max_age=SESSION_MAX_AGE_SEC)

app.include_router(studio.router)
app.include_router(studio.api)
app.include_router(upload.router)
app.include_router(classify.router)
app.include_router(render.router)
app.include_router(captions.router)

ensure_runtime_directories()

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
app.mount("/media/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/media/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="videos")
app.mount("/media/carousels", StaticFiles(directory=str(CAROUSELS_DIR)), name="carousels")
app.mount("/media/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")
app.mount("/media/logos", StaticFiles(directory=str(LOGOS_DIR)), name="logos")


@app.get("/api/health")
async def health():
    from backend.services.ffmpeg_diagnostics import get_diagnostics
    from backend.services.job_queue import job_manager

    ollama_ok = await app_state.caption_engine.health_check()
    ffmpeg = get_diagnostics()
    return {
        "status": "ok",
        "device": str(app_state.device),
        "mps": torch.backends.mps.is_available(),
        "models_loaded": app_state.models_loaded,
        "ollama_available": ollama_ok,
        "ffmpeg": ffmpeg.to_dict() if ffmpeg else None,
        "jobs_running": count_running_studio_jobs(),
        "job_workers": JOB_QUEUE_MAX_WORKERS,
    }


@app.post("/api/models/load")
async def load_models():
    """Lazy-load all inference models on demand."""
    if not app_state.classifier._model:
        app_state.classifier.load()
    if not app_state.depth_engine._model:
        app_state.depth_engine.load()
    app_state.upload_service.classifier = app_state.classifier
    app_state.render_service.depth = app_state.depth_engine
    app_state.render_service.caption_service = app_state.caption_service
    app_state.caption_service.classifier = app_state.classifier
    app_state.caption_service.enricher = app_state.caption_enricher
    return {"status": "models_loaded"}


if __name__ == "__main__":
    app.state.eager_load = True
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        factory=False,
    )
