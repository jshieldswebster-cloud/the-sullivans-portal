from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import EVENT_CATEGORIES, UNCATEGORIZED_LABEL

router = APIRouter(prefix="/api/captions", tags=["captions"])


class CaptionRequest(BaseModel):
    category: Optional[str] = None
    filepath: Optional[str] = None
    image_context: Optional[str] = None


@router.get("/health")
async def ollama_health():
    from backend.main import app_state

    ok = await app_state.caption_engine.health_check()
    return {"ollama_available": ok}


@router.post("/extract")
async def extract_visual_context(body: CaptionRequest):
    """Run Moondream2/Florence-2 vision extraction on an image."""
    from backend.main import app_state

    if not body.filepath:
        raise HTTPException(status_code=400, detail="filepath is required")

    try:
        extraction = app_state.caption_service.extract_visual_context(body.filepath)
        return {
            "filepath": extraction.filepath,
            "model": extraction.model,
            "raw_text": extraction.raw_text,
            "formatted": extraction.formatted,
            "sections": extraction.sections,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/generate")
async def generate_caption(body: CaptionRequest):
    from backend.main import app_state

    try:
        if body.filepath:
            result = await app_state.caption_service.generate_enriched_caption(
                body.filepath,
                category=body.category,
                use_enriched_prompt=True,
                save=True,
            )
            return {
                "category": result["category"],
                "caption": result["caption"],
                "visual_context": result["visual_extraction"]["formatted"],
                "classification": result["classification"],
                "saved_path": result.get("saved_path"),
            }

        if not body.category or body.category not in EVENT_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"category is required (one of {EVENT_CATEGORIES})",
            )

        caption = await app_state.render_service.generate_caption(
            body.category, image_context=body.image_context
        )
        return {"category": body.category, "caption": caption}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/generate-enriched")
async def generate_enriched_caption(body: CaptionRequest):
    """Full pipeline: classify + vision extract + Llama 3 caption."""
    from backend.main import app_state

    if not body.filepath:
        raise HTTPException(status_code=400, detail="filepath is required")

    try:
        result = await app_state.caption_service.generate_enriched_caption(
            body.filepath,
            category=body.category,
            use_enriched_prompt=True,
            save=True,
        )
        if result["category"] == UNCATEGORIZED_LABEL:
            result["warning"] = "Image below classification threshold; used best-guess category"
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
