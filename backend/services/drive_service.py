"""Google Drive integration — OAuth2, service account, folder browse, image import."""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from backend.config import (
    DRIVE_POST_2_COUNT,
    EVENT_CATEGORIES,
    GOOGLE_DRIVE_CLIENT_ID,
    GOOGLE_DRIVE_CLIENT_SECRET,
    GOOGLE_DRIVE_INDEX_PATH,
    GOOGLE_DRIVE_MASTER_FOLDER_ID,
    GOOGLE_DRIVE_MASTER_FOLDER_NAME,
    GOOGLE_DRIVE_OAUTH_TOKEN_PATH,
    GOOGLE_DRIVE_REFRESH_TOKEN,
    GOOGLE_REDIRECT_URI,
    GOOGLE_DRIVE_ROOT_FOLDER_ID,
    GOOGLE_DRIVE_SCOPES,
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON,
    UPLOADS_DIR,
    category_slug,
)
from backend.services.settings_store import read_json, write_json
from backend.services.upload_service import UploadService

logger = logging.getLogger(__name__)

IMAGE_MIME_PREFIX = "image/"
VIDEO_MIME_PREFIX = "video/"
ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
ALLOWED_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/webm",
}
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveNotConfiguredError(RuntimeError):
    pass


class DriveNotConnectedError(RuntimeError):
    pass


def _credentials_configured() -> bool:
    if GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON and Path(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON).is_file():
        return True
    if GOOGLE_DRIVE_REFRESH_TOKEN and GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET:
        return True
    return bool(GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET)


def _load_oauth_token() -> dict[str, Any] | None:
    if GOOGLE_DRIVE_REFRESH_TOKEN:
        return {
            "refresh_token": GOOGLE_DRIVE_REFRESH_TOKEN,
            "client_id": GOOGLE_DRIVE_CLIENT_ID,
            "client_secret": GOOGLE_DRIVE_CLIENT_SECRET,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    if GOOGLE_DRIVE_OAUTH_TOKEN_PATH.is_file():
        return read_json(GOOGLE_DRIVE_OAUTH_TOKEN_PATH, default=None)
    return None


def _save_oauth_token(token_data: dict[str, Any]) -> None:
    write_json(GOOGLE_DRIVE_OAUTH_TOKEN_PATH, token_data)


class DriveService:
    """Browse Google Drive event folders and stream images into the studio pipeline."""

    def __init__(self) -> None:
        self._service = None

    # ── Auth ────────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return _credentials_configured()

    def auth_mode(self) -> str | None:
        if GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON and Path(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON).is_file():
            return "service_account"
        if _load_oauth_token():
            return "oauth"
        if GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET:
            return "oauth_pending"
        return None

    def is_connected(self) -> bool:
        if not self.is_configured():
            return False
        try:
            self._get_service()
            return True
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        master: dict[str, Any] | None = None
        if GOOGLE_DRIVE_MASTER_FOLDER_ID:
            master = {
                "id": GOOGLE_DRIVE_MASTER_FOLDER_ID,
                "name": GOOGLE_DRIVE_MASTER_FOLDER_NAME,
                "source": "configured",
            }
        if self.is_connected():
            try:
                master = self.resolve_master_folder()
            except Exception as exc:
                logger.warning("Could not resolve master folder: %s", exc)
        return {
            "configured": self.is_configured(),
            "connected": self.is_connected(),
            "auth_mode": self.auth_mode(),
            "master_folder_name": GOOGLE_DRIVE_MASTER_FOLDER_NAME,
            "master_folder_id": GOOGLE_DRIVE_MASTER_FOLDER_ID,
            "master_folder": master,
            "root_folder_id": GOOGLE_DRIVE_ROOT_FOLDER_ID,
            "categories": EVENT_CATEGORIES,
        }

    def _read_index(self) -> dict[str, Any]:
        return read_json(GOOGLE_DRIVE_INDEX_PATH, default={}) or {}

    def _write_index(self, data: dict[str, Any]) -> None:
        write_json(GOOGLE_DRIVE_INDEX_PATH, data)

    def resolve_master_folder(self) -> dict[str, Any]:
        """Return the configured VV LUXE STUDIO master folder (ID from env/config)."""
        index = self._read_index()

        if GOOGLE_DRIVE_MASTER_FOLDER_ID:
            folder: dict[str, Any] = {
                "id": GOOGLE_DRIVE_MASTER_FOLDER_ID,
                "name": GOOGLE_DRIVE_MASTER_FOLDER_NAME,
                "source": "configured",
                "cached": index.get("master_folder_id") == GOOGLE_DRIVE_MASTER_FOLDER_ID,
            }
            if self.is_connected():
                meta = self.get_folder_meta(GOOGLE_DRIVE_MASTER_FOLDER_ID)
                if meta:
                    folder["name"] = meta.get("name") or GOOGLE_DRIVE_MASTER_FOLDER_NAME
                    folder["verified"] = True
                else:
                    logger.warning(
                        "Configured master folder ID not accessible: %s",
                        GOOGLE_DRIVE_MASTER_FOLDER_ID,
                    )
            self._write_index(
                {
                    **index,
                    "master_folder_id": GOOGLE_DRIVE_MASTER_FOLDER_ID,
                    "master_folder_name": folder["name"],
                }
            )
            return folder

        cached_id = index.get("master_folder_id")
        if cached_id:
            meta = self.get_folder_meta(cached_id)
            if meta and meta.get("name") == GOOGLE_DRIVE_MASTER_FOLDER_NAME:
                return {"id": cached_id, "name": meta["name"], "cached": True, "source": "cache"}

        if GOOGLE_DRIVE_ROOT_FOLDER_ID and GOOGLE_DRIVE_ROOT_FOLDER_ID != "root":
            meta = self.get_folder_meta(GOOGLE_DRIVE_ROOT_FOLDER_ID)
            if meta:
                folder = {
                    "id": meta["id"],
                    "name": meta.get("name"),
                    "cached": False,
                    "source": "root_folder_id",
                }
                self._write_index({**index, "master_folder_id": folder["id"]})
                return folder

        service = self._get_service()
        q = (
            f"mimeType = '{FOLDER_MIME}' and trashed = false "
            f"and name = '{GOOGLE_DRIVE_MASTER_FOLDER_NAME}'"
        )
        results = (
            service.files()
            .list(q=q, spaces="drive", fields="files(id, name)", pageSize=5)
            .execute()
        )
        files = results.get("files", [])
        if not files:
            raise FileNotFoundError(
                f'Google Drive folder "{GOOGLE_DRIVE_MASTER_FOLDER_NAME}" not found'
            )
        folder = {"id": files[0]["id"], "name": files[0]["name"], "cached": False, "source": "search"}
        self._write_index({**index, "master_folder_id": folder["id"]})
        return folder

    def _master_folder_id(self) -> str:
        if GOOGLE_DRIVE_MASTER_FOLDER_ID:
            return GOOGLE_DRIVE_MASTER_FOLDER_ID
        return self.resolve_master_folder()["id"]

    def _match_category(self, folder_name: str) -> str | None:
        name = folder_name.strip().lower()
        for cat in EVENT_CATEGORIES:
            if cat.lower() == name:
                return cat
        for cat in EVENT_CATEGORIES:
            if cat.lower() in name or name in cat.lower():
                return cat
        return None

    def list_master_categories(self) -> list[dict[str, Any]]:
        """Subfolders of the master folder that map to event categories."""
        master_id = self._master_folder_id()
        folders = self.list_folders(parent_id=master_id)
        matched: list[dict[str, Any]] = []
        for f in folders:
            cat = self._match_category(f["name"])
            if cat:
                matched.append({**f, "category": cat, "name": cat})
        return matched

    def list_project_folders(self, category_folder_id: str) -> list[dict[str, Any]]:
        """Client project folders inside an event-type category folder."""
        return self.list_folders(parent_id=category_folder_id)

    def list_folder_assets(self, folder_id: str) -> list[dict[str, Any]]:
        """Images and videos in a folder, sorted by modified time then name."""
        service = self._get_service()
        q = f"'{folder_id}' in parents and trashed = false"
        results = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType, size, modifiedTime, thumbnailLink)",
                orderBy="modifiedTime",
                pageSize=500,
            )
            .execute()
        )
        assets: list[dict[str, Any]] = []
        for f in results.get("files", []):
            mime = f.get("mimeType", "")
            if mime == FOLDER_MIME:
                continue
            if mime == "application/vnd.google-apps.photo":
                mime = "image/jpeg"
            is_image = mime.startswith(IMAGE_MIME_PREFIX) or mime in ALLOWED_IMAGE_MIMES
            is_video = mime.startswith(VIDEO_MIME_PREFIX) or mime in ALLOWED_VIDEO_MIMES
            if not is_image and not is_video:
                continue
            assets.append(
                {
                    "id": f["id"],
                    "name": f["name"],
                    "mime_type": mime,
                    "kind": "video" if is_video else "image",
                    "size": int(f.get("size") or 0),
                    "modified_time": f.get("modifiedTime"),
                    "thumbnail_link": f.get("thumbnailLink"),
                }
            )
        assets.sort(key=lambda a: (a.get("modified_time") or "", a.get("name") or ""))
        return assets

    def parse_project_folder(self, folder_id: str) -> dict[str, Any]:
        """Split flat client folder assets into cover / carousel / reel slots."""
        assets = self.list_folder_assets(folder_id)
        images = [a for a in assets if a["kind"] == "image"]
        videos = [a for a in assets if a["kind"] == "video"]
        warnings: list[str] = []

        cover_id: str | None = None
        carousel_ids: list[str] = []
        reel_ids: list[str] = []

        if not images and not videos:
            warnings.append("No photos or videos found in project folder")
        elif not images:
            warnings.append("No images found — cover and carousel require photos")
            reel_ids = [v["id"] for v in videos]
        else:
            cover_id = images[0]["id"]
            carousel_ids = [img["id"] for img in images[1 : 1 + DRIVE_POST_2_COUNT]]
            reel_image_ids = [img["id"] for img in images[1 + DRIVE_POST_2_COUNT :]]
            reel_ids = reel_image_ids + [v["id"] for v in videos]

            if len(images) < 1 + DRIVE_POST_2_COUNT:
                warnings.append(
                    f"Carousel requires {DRIVE_POST_2_COUNT} photos after cover "
                    f"(found {max(0, len(images) - 1)})"
                )
            if not reel_ids:
                warnings.append("Post 3 reel requires at least one remaining photo or video")

        return {
            "cover_drive_id": cover_id,
            "carousel_drive_ids": carousel_ids,
            "reel_drive_ids": reel_ids,
            "asset_count": len(assets),
            "image_count": len(images),
            "video_count": len(videos),
            "warnings": warnings,
        }

    def oauth_start_url(self, *, state: str) -> str:
        if not GOOGLE_DRIVE_CLIENT_ID or not GOOGLE_DRIVE_CLIENT_SECRET:
            raise DriveNotConfiguredError(
                "Set GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET"
            )
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_DRIVE_CLIENT_ID,
                    "client_secret": GOOGLE_DRIVE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI],
                }
            },
            scopes=GOOGLE_DRIVE_SCOPES,
            redirect_uri=GOOGLE_REDIRECT_URI,
        )
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return url

    def oauth_exchange(self, code: str) -> None:
        if not GOOGLE_DRIVE_CLIENT_ID or not GOOGLE_DRIVE_CLIENT_SECRET:
            raise DriveNotConfiguredError("Google Drive OAuth is not configured")
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_DRIVE_CLIENT_ID,
                    "client_secret": GOOGLE_DRIVE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI],
                }
            },
            scopes=GOOGLE_DRIVE_SCOPES,
            redirect_uri=GOOGLE_REDIRECT_URI,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        _save_oauth_token(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or GOOGLE_DRIVE_SCOPES),
            }
        )
        self._service = None
        logger.info("Google Drive OAuth connected")

    def disconnect(self) -> None:
        if GOOGLE_DRIVE_OAUTH_TOKEN_PATH.is_file():
            GOOGLE_DRIVE_OAUTH_TOKEN_PATH.unlink(missing_ok=True)
        self._service = None

    def _build_credentials(self):
        if GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON:
            path = Path(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)
            if path.is_file():
                from google.oauth2 import service_account

                return service_account.Credentials.from_service_account_file(
                    str(path),
                    scopes=GOOGLE_DRIVE_SCOPES,
                )

        token_data = _load_oauth_token()
        if not token_data:
            raise DriveNotConnectedError(
                "Google Drive not connected — complete OAuth or set a service account"
            )

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", GOOGLE_DRIVE_CLIENT_ID),
            client_secret=token_data.get("client_secret", GOOGLE_DRIVE_CLIENT_SECRET),
            scopes=token_data.get("scopes", GOOGLE_DRIVE_SCOPES),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_oauth_token(
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": list(creds.scopes or GOOGLE_DRIVE_SCOPES),
                }
            )
        return creds

    def _get_service(self):
        if self._service is not None:
            return self._service
        from googleapiclient.discovery import build

        creds = self._build_credentials()
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # ── Browse ──────────────────────────────────────────────────────────────

    def list_folders(self, *, parent_id: str | None = None) -> list[dict[str, Any]]:
        service = self._get_service()
        if parent_id:
            parent = parent_id
        else:
            try:
                parent = self._master_folder_id()
            except Exception:
                parent = GOOGLE_DRIVE_MASTER_FOLDER_ID or GOOGLE_DRIVE_ROOT_FOLDER_ID
        q = (
            f"'{parent}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        results = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name, modifiedTime, parents)",
                orderBy="name",
                pageSize=200,
            )
            .execute()
        )
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "modified_time": f.get("modifiedTime"),
                "parent_id": parent,
            }
            for f in results.get("files", [])
        ]

    def search_category_folders(self, category: str) -> list[dict[str, Any]]:
        """Find the category folder under the VV LUXE STUDIO master root."""
        if category not in EVENT_CATEGORIES:
            return []
        try:
            master_id = self._master_folder_id()
        except Exception:
            master_id = GOOGLE_DRIVE_MASTER_FOLDER_ID or GOOGLE_DRIVE_ROOT_FOLDER_ID
        service = self._get_service()
        q = (
            f"'{master_id}' in parents and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false and name = '{category}'"
        )
        results = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name, modifiedTime, parents)",
                orderBy="name",
                pageSize=20,
            )
            .execute()
        )
        out: list[dict[str, Any]] = []
        for f in results.get("files", []):
            out.append(
                {
                    "id": f["id"],
                    "name": f["name"],
                    "modified_time": f.get("modifiedTime"),
                    "category": category,
                }
            )
        if out:
            return out
        for folder in self.list_folders(parent_id=master_id):
            if self._match_category(folder["name"]) == category:
                out.append({**folder, "category": category, "name": category})
        return out

    def list_images(self, folder_id: str) -> list[dict[str, Any]]:
        service = self._get_service()
        q = (
            f"'{folder_id}' in parents and trashed = false and "
            f"(mimeType contains '{IMAGE_MIME_PREFIX}' or "
            f"mimeType = 'application/vnd.google-apps.photo')"
        )
        results = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType, size, modifiedTime, thumbnailLink)",
                orderBy="name",
                pageSize=500,
            )
            .execute()
        )
        files = []
        for f in results.get("files", []):
            mime = f.get("mimeType", "")
            if mime == "application/vnd.google-apps.photo":
                mime = "image/jpeg"
            if not mime.startswith(IMAGE_MIME_PREFIX) and mime not in ALLOWED_IMAGE_MIMES:
                continue
            files.append(
                {
                    "id": f["id"],
                    "name": f["name"],
                    "mime_type": mime,
                    "size": int(f.get("size") or 0),
                    "modified_time": f.get("modifiedTime"),
                    "thumbnail_link": f.get("thumbnailLink"),
                }
            )
        return files

    def get_folder_meta(self, folder_id: str) -> dict[str, Any] | None:
        service = self._get_service()
        try:
            meta = (
                service.files()
                .get(fileId=folder_id, fields="id, name, parents, mimeType")
                .execute()
            )
            return meta
        except Exception as exc:
            logger.warning("Drive folder meta failed for %s: %s", folder_id, exc)
            return None

    def get_file_previews(self, file_ids: list[str]) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        service = self._get_service()
        out: list[dict[str, Any]] = []
        for file_id in file_ids:
            try:
                meta = (
                    service.files()
                    .get(
                        fileId=file_id,
                        fields="id, name, mimeType, thumbnailLink, modifiedTime",
                    )
                    .execute()
                )
                out.append(
                    {
                        "id": meta["id"],
                        "name": meta.get("name"),
                        "mime_type": meta.get("mimeType"),
                        "thumbnail_link": meta.get("thumbnailLink"),
                    }
                )
            except Exception as exc:
                logger.warning("Drive file preview failed for %s: %s", file_id, exc)
        return out

    # ── Import ──────────────────────────────────────────────────────────────

    def download_bytes(self, file_id: str) -> tuple[bytes, str, str]:
        return self._download_file_bytes(file_id)

    def _download_file_bytes(self, file_id: str) -> tuple[bytes, str, str]:
        service = self._get_service()
        meta = service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
        name = meta.get("name") or f"drive_{file_id[:8]}.jpg"
        mime = meta.get("mimeType") or "image/jpeg"

        if mime.startswith("application/vnd.google-apps"):
            export_mime = "image/jpeg"
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
            mime = export_mime
            if not name.lower().endswith((".jpg", ".jpeg")):
                name = Path(name).stem + ".jpg"
        else:
            request = service.files().get_media(fileId=file_id)

        buffer = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue(), name, mime

    def import_files(
        self,
        file_ids: list[str],
        *,
        category: str,
        event_name: str,
    ) -> list[dict[str, Any]]:
        """Download Drive images to local uploads and return pipeline-ready metadata."""
        if not file_ids:
            return []

        slug = event_name.strip().replace(" ", "_")
        dest_dir = UPLOADS_DIR / category_slug(category) / slug / "drive_import"
        dest_dir.mkdir(parents=True, exist_ok=True)

        imported: list[dict[str, Any]] = []
        for file_id in file_ids:
            try:
                data, name, mime = self._download_file_bytes(file_id)
            except Exception as exc:
                logger.warning("Drive download failed %s: %s", file_id, exc)
                continue

            ext = mimetypes.guess_extension(mime) or Path(name).suffix or ".jpg"
            safe_name = f"drive_{uuid.uuid4().hex[:8]}{ext}"
            dest = dest_dir / safe_name
            dest.write_bytes(data)

            imported.append(
                {
                    "drive_id": file_id,
                    "filename": name,
                    "path": str(dest),
                    "url": UploadService.media_url_for(dest),
                    "size": len(data),
                    "mime_type": mime,
                }
            )

        logger.info(
            "Imported %d/%d Drive files → %s",
            len(imported),
            len(file_ids),
            dest_dir,
        )
        return imported

    def import_folder_images(
        self,
        folder_id: str,
        *,
        category: str,
        event_name: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        images = self.list_images(folder_id)
        if limit:
            images = images[:limit]
        ids = [img["id"] for img in images]
        return self.import_files(ids, category=category, event_name=event_name)
