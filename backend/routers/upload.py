from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.config import EVENT_CATEGORIES, UPLOADS_DIR
from backend.database import list_images
from backend.services.upload_service import UploadService

router = APIRouter(prefix="/api/upload", tags=["upload"])


def get_upload_service() -> UploadService:
    from backend.main import app_state

    return app_state.upload_service


@router.post("/batch")
async def upload_batch(files: list[UploadFile] = File(...)):
    service = get_upload_service()
    payload = []
    for f in files:
        data = await f.read()
        payload.append((f.filename or "upload.jpg", data))
    results = service.process_batch(payload)
    return {"uploaded": len(results), "images": results}


@router.get("/images")
async def get_images(category: Optional[str] = None):
    rows = list_images(category=category)
    return {"count": len(rows), "images": rows, "categories": EVENT_CATEGORIES}


@router.get("/file/{filename}")
async def get_upload_file(filename: str):
    path = (UPLOADS_DIR / Path(filename).name).resolve()
    if not path.is_file() or UPLOADS_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)
