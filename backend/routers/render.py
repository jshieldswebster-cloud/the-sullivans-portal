from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import EVENT_CATEGORIES

router = APIRouter(prefix="/api/render", tags=["render"])


class ReelRequest(BaseModel):
    filepath: str
    category: str
    motion: str = Field(
        default="push_in",
        pattern="^(push_in|pan_left_right|pan_left|tilt_up)$",
    )
    duration_sec: float = Field(default=5.0, ge=1.0, le=30.0)
    fps: int = Field(default=30, ge=24, le=60)


class CategoryRequest(BaseModel):
    category: str


@router.post("/reel")
async def render_reel(body: ReelRequest):
    from backend.main import app_state

    if body.category not in EVENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    try:
        output = await app_state.render_service.generate_reel(
            body.filepath,
            body.category,
            motion=body.motion,
            duration_sec=body.duration_sec,
            fps=body.fps,
        )
        return {"output_path": output}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/carousel")
async def render_carousel(body: CategoryRequest):
    from backend.main import app_state

    if body.category not in EVENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    try:
        slides = await app_state.render_service.generate_carousel(body.category)
        return {"slides": slides, "count": len(slides)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bundle")
async def render_bundle(body: CategoryRequest):
    """Generate reel + carousel + caption for a category in one call."""
    from backend.main import app_state

    if body.category not in EVENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    try:
        result = await app_state.render_service.generate_category_bundle(
            body.category
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
