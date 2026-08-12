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

# Allow running as `python backend/main.py` or `uvicorn backend.main:app`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import ensure_directories, get_device  # noqa: E402
from backend.database import init_db  # noqa: E402
from backend.models.caption_engine import CaptionEngine
from backend.models.caption_enricher import CaptionEnricher
from backend.models.carousel_compositor import CarouselCompositor
from backend.models.classifier import EventClassifier
from backend.models.depth_engine import DepthParallaxEngine
from backend.routers import captions, classify, render, upload  # noqa: E402
from backend.services.caption_service import CaptionService  # noqa: E402
from backend.services.render_service import RenderService  # noqa: E402
from backend.services.upload_service import UploadService  # noqa: E402
import backend.services.video_service  # noqa: F401 — DepthEngine render compat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vv-luxe")


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


app = FastAPI(
    title="VV LUXE Studio API",
    description="Local AI pipeline for venue content production",
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

app.include_router(upload.router)
app.include_router(classify.router)
app.include_router(render.router)
app.include_router(captions.router)


@app.get("/api/health")
async def health():
    ollama_ok = await app_state.caption_engine.health_check()
    return {
        "status": "ok",
        "device": str(app_state.device),
        "mps": torch.backends.mps.is_available(),
        "models_loaded": app_state.models_loaded,
        "ollama_available": ollama_ok,
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
        host="127.0.0.1",
        port=8765,
        reload=False,
        factory=False,
    )
