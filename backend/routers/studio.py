"""Studio web UI routes: pages, uploads, async montage jobs."""

from __future__ import annotations

import logging
import secrets
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.config import (
    EVENT_CATEGORIES,
    LOGO_ANCHORS,
    MONTAGE_MOTION_OPTIONS,
    MONTAGE_TRANSITION_SEC,
    STUDIO_BRAND_NAME,
    STUDIO_TAGLINE,
    TOUR_COLOR_PALETTES,
    VIDEOS_DIR,
)
from backend.database import list_images
from backend.models.carousel_compositor import CarouselCompositor
from backend.services.title_service import TitleService
from backend.services.upload_service import UploadService
from backend.services.auth_service import (
    authenticate_login,
    get_current_user,
    grant_vault_access,
    login_user,
    logout_user,
    require_user,
    require_user_or_redirect,
    require_vault_access,
    revoke_vault_access,
    SESSION_PORTFOLIO_ID,
    validate_vault_session,
)
from backend.services.ideal_row_service import IdealRowService, POST_2_CAROUSEL_COUNT
from backend.services.instagram_export_service import InstagramExportService
from backend.services.client_gallery_service import (
    ClientGalleryService,
    verify_access_code,
)
from backend.services.tour_mode_service import TourModeService
from backend.services.audio_library_service import AudioLibraryService, SESSION_SELECTED_TRACK
from backend.services.watermark_service import WatermarkService, save_watermark_settings
from backend.services.montage_jobs import montage_jobs
from backend.services.studio_state_service import StudioStateService
from backend.services.drive_service import (
    DriveNotConfiguredError,
    DriveNotConnectedError,
    DriveService,
)
from backend.services.drive_sync_service import DriveSyncService
from backend.services.daily_backlog_worker import DailyBacklogWorker
from backend.database import count_running_studio_jobs

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

router = APIRouter(tags=["studio"])
api = APIRouter(prefix="/api/studio", tags=["studio-api"])

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


def _page_ctx(request: Request, username: str, *, active: str = "", **extra):
    return {
        "request": request,
        "username": username,
        "brand_name": STUDIO_BRAND_NAME,
        "brand_tagline": STUDIO_TAGLINE,
        "active": active,
        **extra,
    }


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, home: bool = False):
    if home:
        logout_user(request)
    elif get_current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": None, "brand_name": STUDIO_BRAND_NAME, "brand_tagline": STUDIO_TAGLINE},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if authenticate_login(request, username, password):
        login_user(request, username)
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": "Invalid credentials. Please try again.", "brand_name": STUDIO_BRAND_NAME, "brand_tagline": STUDIO_TAGLINE},
        status_code=401,
    )


@router.get("/home")
async def home_page(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _page_ctx(
            request,
            auth,
            active="dashboard",
            motion_options=list(MONTAGE_MOTION_OPTIONS.keys()),
            categories=EVENT_CATEGORIES,
        ),
    )


@router.get("/new-post", response_class=HTMLResponse)
async def new_post(request: Request):
    """Alias for the Ideal Row / review dashboard."""
    return await dashboard(request)


@router.get("/event-portal", response_class=HTMLResponse)
async def event_portal(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "event_portal.html",
        _page_ctx(request, auth, active="portal"),
    )


@router.get("/grid-preview", response_class=HTMLResponse)
async def grid_preview_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "grid_preview.html",
        _page_ctx(request, auth, active="grid", categories=EVENT_CATEGORIES),
    )


def _feature_page(
    request: Request,
    auth: str,
    *,
    active: str,
    page_title: str,
    page_description: str,
):
    return templates.TemplateResponse(
        request,
        "includes/page_shell.html",
        _page_ctx(
            request,
            auth,
            active=active,
            page_title=page_title,
            page_description=page_description,
        ),
    )


@router.get("/content-calendar", response_class=HTMLResponse)
async def content_calendar_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return _feature_page(
        request,
        auth,
        active="calendar",
        page_title="Content Calendar",
        page_description="Plan and schedule Ideal Row posts across your event calendar.",
    )


@router.get("/brand-vault", response_class=HTMLResponse)
async def brand_vault_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return _feature_page(
        request,
        auth,
        active="vault",
        page_title="Brand Vault",
        page_description="Central library for logos, fonts, and Sullivan Portal brand assets.",
    )


@router.get("/audio-library", response_class=HTMLResponse)
async def audio_library_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "audio_library.html",
        _page_ctx(request, auth, active="audio"),
    )


@router.get("/watermark", response_class=HTMLResponse)
async def watermark_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "watermark.html",
        _page_ctx(
            request,
            auth,
            active="watermark",
            logo_anchors=LOGO_ANCHORS,
        ),
    )


def _client_gallery_ctx(request: Request, **extra):
    verified = validate_vault_session(request)
    portfolio_id = request.session.get(SESSION_PORTFOLIO_ID, "")
    return {
        "request": request,
        "brand_name": STUDIO_BRAND_NAME,
        "brand_tagline": STUDIO_TAGLINE,
        "verified": verified,
        "portfolio_id": portfolio_id,
        **extra,
    }


def _render_client_gallery(request: Request, *, error: str | None = None):
    svc = ClientGalleryService()
    events = svc.list_unlockable_events()
    preselect = request.query_params.get("event", "")
    return templates.TemplateResponse(
        request,
        "client_gallery.html",
        _client_gallery_ctx(
            request,
            events=events,
            preselect_event=preselect,
            error=error,
        ),
    )


@router.get("/client-gallery", response_class=HTMLResponse)
@router.get("/secure-view", response_class=HTMLResponse)
async def client_gallery_page(request: Request):
    return _render_client_gallery(request)


@router.post("/client-gallery/access", response_class=HTMLResponse)
@router.post("/secure-view/access", response_class=HTMLResponse)
async def client_gallery_access(
    request: Request,
    portfolio_id: str = Form(...),
    access_code: str = Form(...),
):
    if not verify_access_code(portfolio_id.strip(), access_code):
        return _render_client_gallery(request, error="Invalid access code for this event.")
    grant_vault_access(request, portfolio_id.strip())
    return RedirectResponse("/client-gallery", status_code=302)


@router.post("/client-gallery/exit")
@router.post("/secure-view/exit")
async def client_gallery_exit(request: Request):
    revoke_vault_access(request)
    return RedirectResponse("/client-gallery", status_code=302)


@router.get("/client-portal")
async def client_portal_redirect():
    return RedirectResponse("/client-gallery", status_code=302)


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return _feature_page(
        request,
        auth,
        active="analytics",
        page_title="Analytics",
        page_description="Track post performance and content pipeline metrics.",
    )


@router.get("/tour-mode", response_class=HTMLResponse)
async def tour_mode_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "tour_mode.html",
        _page_ctx(
            request,
            auth,
            active="tour",
            categories=EVENT_CATEGORIES,
            color_palettes=TOUR_COLOR_PALETTES,
        ),
    )


@router.get("/vincent-studio", response_class=HTMLResponse)
async def vincent_studio_page(request: Request):
    auth = require_user_or_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return templates.TemplateResponse(
        request,
        "vincent_studio.html",
        _page_ctx(request, auth, active="vincent", categories=EVENT_CATEGORIES),
    )


@api.get("/session")
async def session_info(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {"authenticated": True, "username": user}


@api.post("/upload")
async def upload_photos(
    request: Request,
    files: list[UploadFile] = File(...),
    category: str = Form(...),
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)

    from backend.main import app_state

    service: UploadService = app_state.upload_service
    payload = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            continue
        payload.append((f.filename or "upload.jpg", await f.read()))

    if not payload:
        return JSONResponse({"error": "No valid images uploaded"}, status_code=400)

    saved = service.process_batch(payload, category=category)
    files_out = [
        {
            "id": item["filename"],
            "filename": item["filename"],
            "url": item["url"],
            "path": item["filepath"],
            "category": item["primary_category"],
        }
        for item in saved
    ]
    return {"uploaded": len(files_out), "files": files_out, "category": category}


@api.get("/assets")
async def list_studio_assets(request: Request, category: Optional[str] = None):
    require_user(request)
    rows = list_images(category=category)
    assets = []
    for row in rows:
        filepath = Path(row["filepath"])
        assets.append(
            {
                "filename": row["filename"],
                "path": row["filepath"],
                "url": UploadService.media_url_for(filepath),
                "category": row.get("primary_category"),
                "created_at": row.get("created_at"),
            }
        )
    if not assets:
        assets = UploadService.list_category_assets(category)
    return {"count": len(assets), "assets": assets, "categories": EVENT_CATEGORIES}


@api.get("/tour/portfolios")
async def tour_list_portfolios(
    request: Request,
    category: Optional[str] = None,
    palette: Optional[str] = None,
):
    require_user(request)
    svc = TourModeService()
    return svc.list_portfolios(category=category or None, palette=palette or None)


@api.get("/tour/portfolios/{portfolio_id}")
async def tour_get_portfolio(request: Request, portfolio_id: str):
    require_user(request)
    svc = TourModeService()
    portfolio = svc.get_portfolio(portfolio_id)
    if not portfolio:
        return JSONResponse({"error": "Portfolio not found"}, status_code=404)
    return portfolio


@api.get("/client-gallery/event")
async def client_gallery_event(request: Request):
    portfolio_id = require_vault_access(request)
    svc = ClientGalleryService()
    portfolio = svc.get_portfolio_for_client(portfolio_id)
    if not portfolio:
        return JSONResponse({"error": "Event gallery not found"}, status_code=404)
    return portfolio


# ── Google Drive ─────────────────────────────────────────────────────────────


@api.get("/drive/status")
async def drive_status(request: Request):
    require_user(request)
    return DriveService().status()


@api.get("/drive/oauth/start")
async def drive_oauth_start(request: Request, return_to: Optional[str] = None):
    require_user(request)
    svc = DriveService()
    if not svc.is_configured():
        return JSONResponse(
            {"error": "Google Drive is not configured. Set GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET."},
            status_code=503,
        )
    state = secrets.token_urlsafe(32)
    request.session["drive_oauth_state"] = state
    request.session["drive_oauth_return"] = return_to or "/dashboard"
    try:
        url, code_verifier = svc.oauth_start_url(state=state)
    except Exception:
        logger.exception("Drive OAuth start failed")
        return JSONResponse(
            {"error": "Failed to start Google Drive connection. Please try again."},
            status_code=500,
        )
    request.session["drive_oauth_code_verifier"] = code_verifier
    return RedirectResponse(url, status_code=302)


@api.get("/drive/oauth/callback")
async def drive_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/dashboard?drive_error={error}", status_code=302)
    expected = request.session.pop("drive_oauth_state", None)
    if not expected or not secrets.compare_digest(expected, state or ""):
        return RedirectResponse("/dashboard?drive_error=invalid_state", status_code=302)
    if not code:
        return RedirectResponse("/dashboard?drive_error=no_code", status_code=302)
    code_verifier = request.session.pop("drive_oauth_code_verifier", None)
    try:
        DriveService().oauth_exchange(code, code_verifier=code_verifier)
    except Exception as exc:
        logger.exception("Drive OAuth callback failed")
        return RedirectResponse("/dashboard?drive_error=exchange_failed", status_code=302)
    return_to = request.session.pop("drive_oauth_return", "/dashboard")
    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(f"{return_to}{sep}drive=connected", status_code=302)


@router.get("/auth/callback")
async def google_auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Forced Google OAuth redirect target: https://studio.vvluxe.com/auth/callback."""
    return await drive_oauth_callback(request, code=code, state=state, error=error)


@api.post("/drive/disconnect")
async def drive_disconnect(request: Request):
    require_user(request)
    DriveService().disconnect()
    return {"disconnected": True}


@api.get("/drive/folders")
async def drive_list_folders(request: Request, parent_id: Optional[str] = None):
    require_user(request)
    try:
        folders = DriveService().list_folders(parent_id=parent_id)
        return {"folders": folders, "parent_id": parent_id or "root"}
    except DriveNotConnectedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except DriveNotConfiguredError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:
        logger.exception("Drive list folders failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.get("/drive/search")
async def drive_search_category(request: Request, category: str):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    try:
        folders = DriveService().search_category_folders(category)
        return {"category": category, "folders": folders}
    except DriveNotConnectedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        logger.exception("Drive category search failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.get("/drive/files")
async def drive_list_files(request: Request, folder_id: str):
    require_user(request)
    if not folder_id:
        return JSONResponse({"error": "folder_id required"}, status_code=400)
    try:
        svc = DriveService()
        folder = svc.get_folder_meta(folder_id)
        files = svc.list_images(folder_id)
        return {
            "folder_id": folder_id,
            "folder_name": folder.get("name") if folder else None,
            "files": files,
            "count": len(files),
        }
    except DriveNotConnectedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        logger.exception("Drive list files failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.post("/drive/import")
async def drive_import_files(request: Request):
    require_user(request)
    body = await request.json()
    file_ids = body.get("file_ids") or []
    folder_id = body.get("folder_id")
    category = (body.get("category") or "").strip()
    event_name = (body.get("event_name") or "").strip()

    if not category or category not in EVENT_CATEGORIES:
        return JSONResponse({"error": "Valid category required"}, status_code=400)
    if not event_name:
        return JSONResponse({"error": "event_name required"}, status_code=400)

    try:
        svc = DriveService()
        if folder_id and not file_ids:
            limit = body.get("limit")
            imported = svc.import_folder_images(
                folder_id,
                category=category,
                event_name=event_name,
                limit=int(limit) if limit else None,
            )
        else:
            if not file_ids:
                return JSONResponse({"error": "file_ids or folder_id required"}, status_code=400)
            imported = svc.import_files(file_ids, category=category, event_name=event_name)
        return {"imported": len(imported), "files": imported}
    except DriveNotConnectedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        logger.exception("Drive import failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.post("/drive/sync")
async def drive_sync_start(request: Request):
    require_user(request)
    try:
        svc = DriveService()
        if not svc.is_connected():
            return JSONResponse({"error": "Connect Google Drive first"}, status_code=401)
        job = montage_jobs.create_typed(
            "drive_sync",
            meta={"requested_by": get_current_user(request)},
        )
        return {"job_id": job.id, "status": job.status}
    except DriveNotConfiguredError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:
        logger.exception("Drive sync enqueue failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.get("/drive/projects")
async def drive_list_projects(
    request: Request,
    status: Optional[str] = None,
    category: Optional[str] = None,
    queue: Optional[str] = None,
):
    require_user(request)
    if category and category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    svc = DriveSyncService()
    if queue == "review_for_posting" or queue == "Review for Posting":
        projects = svc.list_review_for_posting(category=category)
    else:
        projects = svc.list_queue(
            status=status or "pending_review",
            category=category,
        )
    return {
        "projects": projects,
        "count": len(projects),
        "active_project_id": projects[0]["id"] if projects else None,
    }


@api.get("/backlog/status")
async def backlog_status(request: Request):
    require_user(request)
    worker = DailyBacklogWorker()
    queue = DriveSyncService().list_review_for_posting()
    pending = DriveSyncService().list_queue(status="pending_review")
    active = None
    if queue:
        active = queue[0].get("id")
    elif pending:
        active = pending[0].get("id")
    return {
        **worker.status(),
        "review_queue_count": len(queue),
        "review_queue": queue,
        "pending_count": len(pending),
        "active_project_id": active,
    }


@api.post("/backlog/run-daily")
@api.post("/daily-batch")
async def backlog_run_daily(request: Request):
    """Manually run the daily Drive → Review for Posting batch (always forced)."""
    require_user(request)
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        job = montage_jobs.create_typed(
            "daily_backlog",
            meta={
                "requested_by": get_current_user(request),
                # Button clicks must run even if the startup scheduler already used today's quota.
                "force": True if body.get("force") is None else bool(body.get("force")),
            },
        )
        return {"job_id": job.id, "status": job.status, "queued": True}
    except Exception as exc:
        logger.exception("Daily backlog enqueue failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.get("/drive/projects/{project_id}")
async def drive_get_project(request: Request, project_id: str):
    require_user(request)
    project = DriveSyncService().get_project(project_id)
    if not project_id or project_id in ("undefined", "null") or not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return project


@api.patch("/drive/projects/{project_id}")
async def drive_update_project(request: Request, project_id: str):
    require_user(request)
    body = await request.json()
    track_id = (body.get("audio_track_id") or "").strip()
    if track_id:
        try:
            return DriveSyncService().set_audio_track(project_id, track_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse({"error": "audio_track_id required"}, status_code=400)


@api.post("/drive/projects/{project_id}/approve")
async def drive_approve_project(request: Request, project_id: str):
    require_user(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    audio_track_id = body.get("audio_track_id") or request.session.get(SESSION_SELECTED_TRACK)
    rerender = bool(body.get("rerender_reel"))
    try:
        result = DriveSyncService().approve_project(
            project_id,
            audio_track_id=audio_track_id,
            title_bold=body.get("title_bold"),
            title_script=body.get("title_script"),
            rerender_reel=rerender,
        )
        return result
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except DriveNotConnectedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        logger.exception("Drive project approve failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@api.post("/titles/generate")
async def generate_titles(
    request: Request,
    image_paths: list[str] = Form(...),
    category: Optional[str] = Form(None),
):
    require_user(request)
    paths = [p for p in image_paths if Path(p).is_file()]
    if not paths:
        return JSONResponse({"error": "No valid image paths"}, status_code=400)

    from backend.main import app_state

    title_service = TitleService(
        classifier=app_state.classifier,
        enricher=app_state.caption_enricher,
        caption_engine=app_state.caption_engine,
    )
    result = await title_service.generate_for_batch(paths, category=category)
    return result


@api.post("/carousel/generate")
async def generate_carousel(
    request: Request,
    image_paths: list[str] = Form(...),
    category: str = Form(...),
    title_bold: Optional[str] = Form(None),
    title_script: Optional[str] = Form(None),
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": "Invalid category"}, status_code=400)

    paths = [p for p in image_paths if Path(p).is_file()]
    if not paths:
        return JSONResponse({"error": "No valid image paths"}, status_code=400)

    compositor = CarouselCompositor()
    slides = compositor.build_carousel(
        paths,
        category,
        title_bold=title_bold,
        title_script=title_script,
    )
    from backend.config import CAROUSELS_DIR

    slide_urls = []
    for slide in slides:
        try:
            rel = slide.relative_to(CAROUSELS_DIR)
            slide_urls.append(f"/media/carousels/{rel.as_posix()}")
        except ValueError:
            slide_urls.append(f"/media/carousels/{slide.name}")

    return {"slide_count": len(slide_urls), "slides": slide_urls, "category": category}


@api.post("/upload-audio")
async def upload_audio(
    request: Request,
    file: UploadFile = File(...),
):
    require_user(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_AUDIO_EXT:
        return JSONResponse({"error": "Unsupported audio format"}, status_code=400)

    from backend.config import AUDIO_DIR

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"studio_{uuid.uuid4().hex}{ext}"
    dest = AUDIO_DIR / safe_name
    dest.write_bytes(await file.read())
    return {"filename": file.filename, "path": str(dest)}


@api.get("/audio/library")
async def audio_library_list(request: Request):
    require_user(request)
    svc = AudioLibraryService()
    svc.bootstrap_tracks()
    return svc.library_summary()


@api.post("/audio/select")
async def audio_select_track(request: Request):
    require_user(request)
    body = await request.json()
    track_id = (body.get("track_id") or "").strip()
    if not track_id:
        return JSONResponse({"error": "track_id required"}, status_code=400)
    svc = AudioLibraryService()
    if not svc.get_track(track_id):
        return JSONResponse({"error": "Track not found"}, status_code=404)
    request.session[SESSION_SELECTED_TRACK] = track_id
    svc.set_default_track(track_id)
    track = svc.get_track(track_id)
    return {"selected_track_id": track_id, "track": track}


@api.get("/audio/selected")
async def audio_selected_track(request: Request):
    require_user(request)
    track_id = request.session.get(SESSION_SELECTED_TRACK)
    svc = AudioLibraryService()
    lib = svc.library_summary()
    effective = track_id or lib.get("default_track_id")
    track = svc.get_track(effective) if effective else None
    return {"selected_track_id": effective, "track": track}


@api.get("/watermark/settings")
async def watermark_get_settings(request: Request):
    require_user(request)
    return WatermarkService().get_settings()


@api.post("/watermark/settings")
async def watermark_save_settings(request: Request):
    require_user(request)
    body = await request.json()
    svc = WatermarkService()
    before = svc.get_settings()
    updated = save_watermark_settings(body)
    refreshed = svc.get_settings()
    refreshed["logos"] = refreshed.get("logos") or before.get("logos")
    return refreshed


@api.get("/jobs/{job_id}")
async def get_studio_job(request: Request, job_id: str):
    require_user(request)
    job = montage_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return montage_jobs.to_dict(job)


@api.get("/jobs")
async def list_studio_jobs(request: Request, job_type: Optional[str] = None):
    require_user(request)
    from backend.services.job_queue import job_manager

    return {"jobs": job_manager.list_recent(job_type=job_type), "running": count_running_studio_jobs()}


@api.get("/calendar")
async def list_calendar(request: Request):
    require_user(request)
    return {"entries": StudioStateService().list_calendar()}


@api.post("/calendar")
async def save_calendar_entry(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
    scheduled_at: str = Form(...),
    post_number: Optional[int] = Form(None),
    notes: str = Form(""),
):
    require_user(request)
    entry = StudioStateService().save_calendar_entry(
        category=category,
        event_name=event_name,
        scheduled_at=scheduled_at,
        post_number=post_number,
        notes=notes,
    )
    return entry


@api.post("/montage/generate")
async def generate_montage(
    request: Request,
    image_paths: list[str] = Form(...),
    motion_style: str = Form("auto"),
    clip_duration_sec: float = Form(4.0),
    transition_sec: float = Form(0.8),
    audio_path: Optional[str] = Form(None),
    audio_track_id: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    title_bold: Optional[str] = Form(None),
    title_script: Optional[str] = Form(None),
):
    require_user(request)

    paths = [p for p in image_paths if Path(p).is_file()]
    if not paths:
        return JSONResponse({"error": "No valid image paths provided"}, status_code=400)

    titles = None
    if title_bold or title_script:
        titles = {"title_bold": title_bold, "title_script": title_script}

    if not audio_track_id and not audio_path:
        audio_track_id = request.session.get(SESSION_SELECTED_TRACK)

    job = montage_jobs.create(
        meta={
            "image_paths": paths,
            "motion_style": motion_style,
            "clip_duration_sec": clip_duration_sec,
            "transition_sec": transition_sec,
            "audio_path": audio_path or None,
            "audio_track_id": audio_track_id or None,
            "category": category,
            "titles": titles,
        }
    )
    return {"job_id": job.id, "status": job.status}


@api.get("/montage/jobs/{job_id}")
async def get_montage_job(request: Request, job_id: str):
    require_user(request)
    job = montage_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return montage_jobs.to_dict(job)


@api.post("/ideal-row/save")
async def save_ideal_row(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
    post_1: UploadFile = File(...),
    post_2: list[UploadFile] = File(...),
    post_3: list[UploadFile] = File(...),
    motion_style: str = Form("auto"),
    clip_duration_sec: float = Form(4.0),
    transition_sec: float = Form(0.8),
    audio_path: Optional[str] = Form(None),
    audio_track_id: Optional[str] = Form(None),
    title_bold: Optional[str] = Form(None),
    title_script: Optional[str] = Form(None),
):
    """Save the three-post Ideal Row package and kick off Post 3 reel render."""
    require_user(request)

    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)

    if len(post_2) != POST_2_CAROUSEL_COUNT:
        return JSONResponse(
            {"error": f"Post 2 requires exactly {POST_2_CAROUSEL_COUNT} photos (got {len(post_2)})"},
            status_code=400,
        )

    if not post_3:
        return JSONResponse({"error": "Post 3 requires at least one reel photo"}, status_code=400)

    ext_ok = ALLOWED_IMAGE_EXT

    p1_ext = Path(post_1.filename or "").suffix.lower()
    if p1_ext not in ext_ok:
        return JSONResponse({"error": "Post 1 must be a JPG or PNG image"}, status_code=400)

    post_2_data: list[bytes] = []
    for f in post_2:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ext_ok:
            return JSONResponse({"error": "Post 2 files must be JPG or PNG"}, status_code=400)
        post_2_data.append(await f.read())

    post_3_data: list[bytes] = []
    for f in post_3:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ext_ok:
            return JSONResponse({"error": "Post 3 files must be JPG or PNG"}, status_code=400)
        post_3_data.append(await f.read())

    post_1_data = await post_1.read()

    service = IdealRowService()
    try:
        result = service.save_event_row(
            category=category,
            event_name=event_name,
            post_1_data=post_1_data,
            post_2_files=post_2_data,
            post_3_files=post_3_data,
            title_bold=title_bold,
            title_script=title_script,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    reel_path = result.post_3["reel_output_path"]
    reel_url = UploadService.media_url_for(Path(reel_path))

    titles = None
    if title_bold or title_script:
        titles = {"title_bold": title_bold, "title_script": title_script}

    if not audio_track_id and not audio_path:
        audio_track_id = request.session.get(SESSION_SELECTED_TRACK)

    job = montage_jobs.create(
        meta={
            "image_paths": result.post_3["image_paths"],
            "motion_style": motion_style,
            "clip_duration_sec": clip_duration_sec,
            "transition_sec": transition_sec,
            "audio_path": audio_path or None,
            "audio_track_id": audio_track_id or None,
            "category": category,
            "titles": titles,
            "output_path": reel_path,
            "output_url": reel_url,
            "ideal_row": True,
            "event_name": event_name,
        }
    )

    payload = result.to_dict()
    payload["reel_job_id"] = job.id
    payload["reel_output_url"] = reel_url
    return payload


@api.post("/ideal-row/prepare-instagram")
async def prepare_instagram_export(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
    reel_job_id: Optional[str] = Form(None),
    background: Optional[str] = Form("1"),
):
    """Package Post 1 cover, Post 2 carousel ZIP, and Post 3 reel for Instagram."""
    require_user(request)

    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)

    export_service = InstagramExportService()
    if not export_service.row_exists(category, event_name):
        return JSONResponse(
            {"error": "Event row not found — save the Ideal Row first"},
            status_code=404,
        )

    reel_path: str | None = None
    if reel_job_id:
        job = montage_jobs.get(reel_job_id)
        if not job:
            return JSONResponse({"error": "Reel job not found"}, status_code=404)
        if job.status in ("running", "pending"):
            return JSONResponse(
                {
                    "error": "Reel still rendering",
                    "reel_job_id": reel_job_id,
                    "status": job.status,
                    "progress": job.progress,
                    "message": job.message,
                },
                status_code=409,
            )
        if job.status == "failed":
            return JSONResponse(
                {"error": job.error or "Reel render failed"},
                status_code=400,
            )
        if job.output_path:
            reel_path = job.output_path

    use_background = background not in ("0", "false", "False")

    if use_background:
        zip_job = montage_jobs.create_typed(
            "zip_export",
            meta={
                "category": category,
                "event_name": event_name,
                "reel_path": reel_path,
            },
        )
        return {
            "zip_job_id": zip_job.id,
            "status": "queued",
            "message": "Instagram packaging queued",
        }

    try:
        from backend.main import app_state

        result = await export_service.prepare_package(
            category,
            event_name,
            reel_path=reel_path,
            caption_engine=app_state.caption_engine,
        )
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    return result.to_dict()


@api.get("/vincent/checklist")
async def vincent_get_checklist(
    request: Request,
    category: str,
    event_name: str,
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    svc = VincentService()
    return svc.get_checklist(category, event_name)


@api.post("/vincent/checklist")
async def vincent_save_checklist(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
    state_json: str = Form(...),
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    import json

    try:
        state = json.loads(state_json)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid checklist state JSON"}, status_code=400)
    svc = VincentService()
    return svc.save_checklist_state(category, event_name, state)


@api.post("/vincent/batch-upload")
async def vincent_batch_upload(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
    files: list[UploadFile] = File(...),
    shot_id: Optional[str] = Form(None),
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    if not event_name.strip():
        return JSONResponse({"error": "Event name required"}, status_code=400)

    payload: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if data:
            payload.append((f.filename or "upload.jpg", data))

    if not payload:
        return JSONResponse({"error": "No valid files received"}, status_code=400)

    svc = VincentService()
    try:
        saved = svc.batch_upload(category, event_name.strip(), payload, shot_id=shot_id or None)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return {"uploaded": len(saved), "files": saved}


@api.post("/vincent/push-ideal-row")
async def vincent_push_ideal_row(
    request: Request,
    category: str = Form(...),
    event_name: str = Form(...),
):
    require_user(request)
    if category not in EVENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category: {category}"}, status_code=400)
    svc = VincentService()
    try:
        result = svc.push_to_ideal_row(category, event_name.strip())
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return result
