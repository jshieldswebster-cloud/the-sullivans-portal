from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import CLASSIFICATION_CONFIDENCE_THRESHOLD, EVENT_CATEGORIES

router = APIRouter(prefix="/api/classify", tags=["classify"])


class ClassifyRequest(BaseModel):
    filepath: str
    threshold: float = Field(
        default=CLASSIFICATION_CONFIDENCE_THRESHOLD, ge=0.0, le=1.0
    )
    top_k: int = Field(default=3, ge=1, le=5)


@router.get("/categories")
async def get_categories():
    return {"categories": EVENT_CATEGORIES}


@router.post("/single")
async def classify_single(body: ClassifyRequest):
    from backend.main import app_state

    path = Path(body.filepath)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    if app_state.classifier._model is None:
        app_state.classifier.load()

    result = app_state.classifier.classify_image(
        path, threshold=body.threshold, top_k=body.top_k
    )
    return {
        "filepath": result.filepath,
        "primary_category": result.primary_category,
        "categories": result.categories,
        "confidence": result.confidence,
        "is_uncategorized": result.is_uncategorized,
        "scores": result.scores,
    }
