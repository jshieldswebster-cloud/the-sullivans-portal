"""Canva Connect OAuth, token refresh, asset upload, and autofill drafts."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.config import (
    CANVA_ACCESS_TOKEN,
    CANVA_API_BASE,
    CANVA_AUTH_URI,
    CANVA_BRAND_TEMPLATE_ID,
    CANVA_BRAND_TEMPLATE_POST2_ID,
    CANVA_BRAND_TEMPLATE_POST3_ID,
    CANVA_CLIENT_ID,
    CANVA_CLIENT_SECRET,
    CANVA_DESIGN_PRESET,
    CANVA_OAUTH_SETTINGS_KEY,
    CANVA_OAUTH_TOKEN_PATH,
    CANVA_REDIRECT_URI,
    CANVA_REFRESH_TOKEN,
    CANVA_SCOPES,
    CANVA_TOKEN_REFRESH_INTERVAL_SEC,
    CANVA_TOKEN_URL,
    CANVA_WEBHOOK_URL,
    STUDIO_SECRET_KEY,
)
from backend.database import get_studio_setting, set_studio_setting
from backend.services.ideal_row_service import POST_2_CAROUSEL_COUNT, review_for_posting_paths

logger = logging.getLogger(__name__)

_TOKEN_FERNET_SALT = b"vv-luxe-canva-oauth-v1"
_token_lock = threading.Lock()
_REFRESH_SKEW_SEC = 600  # refresh 10 minutes before expiry


class CanvaNotConfiguredError(RuntimeError):
    """Client ID/secret missing or user has not completed OAuth."""


class CanvaNotConnectedError(RuntimeError):
    """OAuth client is configured but no refresh token is stored yet."""


class CanvaDeliveryError(RuntimeError):
    """Canva upload or autofill failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def collect_package_files(
    category: str,
    event_name: str,
    *,
    staging_path: str | None = None,
) -> dict[str, Any]:
    """Locate Post 1/2/3 files on disk for Canva upload."""
    if staging_path:
        base = Path(staging_path)
        paths = {
            "base": base,
            "post_1": base / "Post_1",
            "post_2": base / "Post_2",
            "post_3": base / "Post_3",
            "post_2_carousel": base / "Post_2" / "carousel",
        }
    else:
        paths = review_for_posting_paths(category, event_name)

    cover = next(paths["post_1"].glob("cover*"), None) if paths["post_1"].is_dir() else None
    photos = (
        sorted(p for p in paths["post_2"].glob("photo_*") if p.is_file())
        if paths["post_2"].is_dir()
        else []
    )
    slides = (
        sorted(p for p in paths["post_2_carousel"].glob("slide_*.jpg") if p.is_file())
        if paths["post_2_carousel"].is_dir()
        else []
    )
    reel_frames = (
        sorted(
            p
            for p in paths["post_3"].glob("reel_*")
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        if paths["post_3"].is_dir()
        else []
    )
    reel_mp4 = None
    if paths["post_3"].is_dir():
        reel_mp4 = next(
            (p for p in sorted(paths["post_3"].glob("*.mp4")) if p.is_file() and p.stat().st_size > 8),
            None,
        )

    missing: list[str] = []
    if not cover or not cover.is_file():
        missing.append("Post 1 cover")
    if len(photos) < POST_2_CAROUSEL_COUNT:
        missing.append(f"Post 2 photos ({len(photos)}/{POST_2_CAROUSEL_COUNT})")
    if len(slides) < 1 + POST_2_CAROUSEL_COUNT:
        missing.append(f"carousel previews ({len(slides)})")
    if not reel_frames:
        missing.append("Post 3 reel frames")
    return {
        "base": paths["base"],
        "cover": cover,
        "photos": photos,
        "slides": slides,
        "reel_frames": reel_frames,
        "reel_mp4": reel_mp4,
        "missing": missing,
        "complete": not missing,
    }


def _token_fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(STUDIO_SECRET_KEY.encode("utf-8") + _TOKEN_FERNET_SALT).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_token_payload(token_data: dict[str, Any]) -> dict[str, str]:
    blob = json.dumps(token_data, separators=(",", ":")).encode("utf-8")
    return {
        "encoding": "fernet-v1",
        "payload": _token_fernet().encrypt(blob).decode("ascii"),
    }


def _decrypt_token_payload(envelope: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(envelope, dict) or envelope.get("encoding") != "fernet-v1":
        if isinstance(envelope, dict) and (envelope.get("refresh_token") or envelope.get("access_token")):
            return envelope
        return None
    ciphertext = envelope.get("payload")
    if not ciphertext:
        return None
    try:
        raw = _token_fernet().decrypt(ciphertext.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to decrypt stored Canva OAuth token: %s", exc)
        return None


def _merge_oauth_tokens(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep a durable refresh token. Canva issues a new one on every refresh (single-use)."""
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value is not None and value != "":
            merged[key] = value
    return merged


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _basic_auth_header() -> dict[str, str]:
    if not CANVA_CLIENT_ID or not CANVA_CLIENT_SECRET:
        raise CanvaNotConfiguredError("Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET")
    raw = f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}".encode("utf-8")
    return {
        "Authorization": "Basic " + base64.b64encode(raw).decode("ascii"),
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _read_db_token() -> dict[str, Any] | None:
    try:
        stored = get_studio_setting(CANVA_OAUTH_SETTINGS_KEY, default={}) or {}
    except Exception:
        return None
    return _decrypt_token_payload(stored)


def _read_file_token() -> dict[str, Any] | None:
    path = CANVA_OAUTH_TOKEN_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _decrypt_token_payload(data) or (data if data.get("refresh_token") else None)


def _write_file_token(token_data: dict[str, Any]) -> None:
    CANVA_OAUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANVA_OAUTH_TOKEN_PATH.write_text(
        json.dumps(_encrypt_token_payload(token_data)),
        encoding="utf-8",
    )
    try:
        os.chmod(CANVA_OAUTH_TOKEN_PATH, 0o600)
    except OSError:
        logger.debug("Could not chmod Canva OAuth token file")


def _load_oauth_token() -> dict[str, Any] | None:
    persist = False
    with _token_lock:
        stored = _read_db_token()
        file_data = _read_file_token()
        env_refresh = (CANVA_REFRESH_TOKEN or "").strip()
        merged: dict[str, Any] | None = None
        if file_data:
            merged = _merge_oauth_tokens(merged, file_data)
        if stored:
            merged = _merge_oauth_tokens(merged, stored)
        if env_refresh and not (merged or {}).get("refresh_token"):
            merged = _merge_oauth_tokens(merged, {"refresh_token": env_refresh})
        if CANVA_ACCESS_TOKEN and not (merged or {}).get("access_token"):
            merged = _merge_oauth_tokens(merged, {"access_token": CANVA_ACCESS_TOKEN.strip()})
        persist = bool(merged and merged.get("refresh_token") and not stored)
    if persist and merged:
        try:
            _save_oauth_token(merged)
        except Exception:
            logger.debug("Could not persist Canva OAuth token to database", exc_info=True)
    return merged


def _save_oauth_token(token_data: dict[str, Any]) -> None:
    with _token_lock:
        existing = _read_db_token() or _read_file_token() or {}
        merged = _merge_oauth_tokens(existing, token_data)
        if not merged.get("refresh_token"):
            raise CanvaNotConfiguredError(
                "Canva did not issue a refresh token. Reconnect Canva so the consent "
                "screen can grant offline access."
            )
        set_studio_setting(CANVA_OAUTH_SETTINGS_KEY, _encrypt_token_payload(merged))
        _write_file_token(merged)


def _clear_oauth_token() -> None:
    with _token_lock:
        try:
            set_studio_setting(CANVA_OAUTH_SETTINGS_KEY, {})
        except Exception:
            logger.debug("Could not clear Canva OAuth setting")
        try:
            if CANVA_OAUTH_TOKEN_PATH.is_file():
                CANVA_OAUTH_TOKEN_PATH.unlink()
        except OSError:
            logger.debug("Could not remove Canva OAuth token file")


def _token_needs_refresh(token_data: dict[str, Any], *, force: bool = False) -> bool:
    if force or not token_data.get("access_token"):
        return True
    expires_at = token_data.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= _utc_now_dt() + timedelta(seconds=_REFRESH_SKEW_SEC)


class CanvaService:
    """OAuth2 (PKCE) + Connect API: upload assets and autofill draft designs."""

    def __init__(self, *, client: httpx.Client | None = None, access_token: str | None = None) -> None:
        self._client = client
        self._access_token_override = (access_token or "").strip() or None

    def is_configured(self) -> bool:
        if self._access_token_override:
            return True
        return bool(CANVA_CLIENT_ID and CANVA_CLIENT_SECRET)

    def is_connected(self) -> bool:
        if self._access_token_override:
            return True
        data = _load_oauth_token() or {}
        return bool(data.get("refresh_token") or data.get("access_token"))

    def status(self) -> dict[str, Any]:
        data = _load_oauth_token() or {}
        return {
            "configured": self.is_configured(),
            "connected": self.is_connected(),
            "has_refresh_token": bool(data.get("refresh_token")),
            "has_access_token": bool(data.get("access_token")),
            "expires_at": data.get("expires_at"),
            "redirect_uri": CANVA_REDIRECT_URI,
            "brand_template_id": CANVA_BRAND_TEMPLATE_ID or None,
            "scopes": CANVA_SCOPES,
        }

    def oauth_start_url(self, *, state: str) -> tuple[str, str]:
        if not self.is_configured():
            raise CanvaNotConfiguredError("Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET")
        verifier, challenge = _pkce_pair()
        query = urlencode(
            {
                "code_challenge": challenge,
                "code_challenge_method": "s256",
                "scope": CANVA_SCOPES,
                "response_type": "code",
                "client_id": CANVA_CLIENT_ID,
                "state": state,
                "redirect_uri": CANVA_REDIRECT_URI,
            }
        )
        return f"{CANVA_AUTH_URI}?{query}", verifier

    def oauth_exchange(self, code: str, *, code_verifier: str | None = None) -> None:
        if not code:
            raise CanvaDeliveryError("authorization code required")
        if not code_verifier:
            raise CanvaDeliveryError("PKCE code_verifier missing — restart Canva connect")
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": CANVA_REDIRECT_URI,
            }
        )
        _save_oauth_token(self._normalize_token_payload(payload))
        logger.info("Canva OAuth connected (refresh token stored)")

    def disconnect(self) -> None:
        _clear_oauth_token()

    def refresh_access_token(self, *, force: bool = False) -> bool:
        """Mint a new access token. Canva refresh tokens are single-use — persist immediately."""
        token_data = _load_oauth_token() or {}
        if not token_data.get("refresh_token"):
            raise CanvaNotConnectedError(
                "Canva not connected — open Connect Canva once so a refresh token can be stored"
            )
        if not _token_needs_refresh(token_data, force=force):
            return False
        payload = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
            }
        )
        _save_oauth_token(self._normalize_token_payload(payload, existing=token_data))
        logger.info("Canva access token refreshed")
        return True

    def ensure_access_token(self) -> str:
        if self._access_token_override:
            return self._access_token_override
        token_data = _load_oauth_token() or {}
        if token_data.get("refresh_token") and _token_needs_refresh(token_data):
            self.refresh_access_token(force=False)
            token_data = _load_oauth_token() or {}
        access = (token_data.get("access_token") or "").strip()
        if not access:
            raise CanvaNotConnectedError("Canva access token missing — reconnect Canva")
        return access

    def _normalize_token_payload(
        self,
        payload: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        expires_in = int(payload.get("expires_in") or 14400)
        expires_at = (_utc_now_dt() + timedelta(seconds=expires_in)).isoformat()
        out = {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token") or (existing or {}).get("refresh_token"),
            "token_type": payload.get("token_type") or "Bearer",
            "scope": payload.get("scope"),
            "expires_in": expires_in,
            "expires_at": expires_at,
            "updated_at": _utc_now(),
        }
        return {k: v for k, v in out.items() if v is not None}

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30.0)
        resp = client.post(CANVA_TOKEN_URL, headers=_basic_auth_header(), data=data)
        if resp.status_code >= 400:
            raise CanvaDeliveryError(
                f"Canva token request failed: {resp.status_code} {resp.text[:400]}"
            )
        payload = resp.json() or {}
        if not payload.get("access_token"):
            raise CanvaDeliveryError("Canva token response missing access_token")
        return payload

    def send_package(
        self,
        *,
        category: str,
        event_name: str,
        files: dict[str, Any],
    ) -> dict[str, Any]:
        if not files.get("complete"):
            raise CanvaDeliveryError(
                "Package is not complete: " + "; ".join(files.get("missing") or [])
            )
        webhook = (CANVA_WEBHOOK_URL or "").strip()
        if self.is_configured() and self.is_connected():
            try:
                return self._send_via_connect(category, event_name, files)
            except (CanvaDeliveryError, CanvaNotConnectedError) as exc:
                if webhook:
                    logger.warning("Canva Connect failed (%s) — falling back to webhook", exc)
                    return self._send_via_webhook(category, event_name, files, webhook)
                raise
        if webhook:
            return self._send_via_webhook(category, event_name, files, webhook)
        if not self.is_configured():
            raise CanvaNotConfiguredError("Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET")
        raise CanvaNotConnectedError(
            "Connect Canva once (dashboard → Connect Canva) so a refresh token can be stored"
        )

    def _send_via_connect(
        self,
        category: str,
        event_name: str,
        files: dict[str, Any],
    ) -> dict[str, Any]:
        token = self.ensure_access_token()
        uploads: list[dict[str, Any]] = []
        to_upload: list[tuple[str, Path]] = [("cover", files["cover"])]
        for idx, slide in enumerate(files["slides"], start=0):
            to_upload.append((f"carousel_{idx:02d}", slide))
        if files.get("reel_mp4"):
            to_upload.append(("reel", files["reel_mp4"]))
        else:
            for idx, frame in enumerate(files["reel_frames"][:3], start=1):
                to_upload.append((f"reel_frame_{idx:02d}", frame))

        for role, path in to_upload:
            asset = self._upload_asset(path, token)
            uploads.append({"role": role, "path": str(path), **asset})
            token = self.ensure_access_token()

        drafts = self._autofill_event_drafts(
            category=category,
            event_name=event_name,
            uploads=uploads,
            files=files,
            token=token,
        )
        result = {
            "channel": "canva_autofill",
            "sent_at": _utc_now(),
            "event_name": event_name,
            "category": category,
            "assets": uploads,
            "drafts": drafts,
            "complete": True,
        }
        logger.info(
            "Canva autofill delivered %s (%d assets, %d drafts)",
            event_name,
            len(uploads),
            len(drafts),
        )
        return result

    def _autofill_event_drafts(
        self,
        *,
        category: str,
        event_name: str,
        uploads: list[dict[str, Any]],
        files: dict[str, Any],
        token: str,
    ) -> list[dict[str, Any]]:
        by_role = {u["role"]: u for u in uploads}
        templates = [
            ("post_1", CANVA_BRAND_TEMPLATE_ID, [by_role.get("cover")]),
            (
                "post_2",
                CANVA_BRAND_TEMPLATE_POST2_ID or CANVA_BRAND_TEMPLATE_ID,
                [u for u in uploads if str(u.get("role", "")).startswith("carousel_")],
            ),
            (
                "post_3",
                CANVA_BRAND_TEMPLATE_POST3_ID or CANVA_BRAND_TEMPLATE_ID,
                [by_role.get("reel")]
                + [u for u in uploads if str(u.get("role", "")).startswith("reel_frame_")],
            ),
        ]
        drafts: list[dict[str, Any]] = []
        for post_key, template_id, assets in templates:
            asset_ids = [a.get("asset_id") for a in assets if a and a.get("asset_id")]
            if not asset_ids:
                continue
            title = f"{event_name} · {post_key.replace('_', ' ').title()}"
            if template_id:
                try:
                    draft = self.autofill_design(
                        brand_template_id=template_id,
                        title=title,
                        event_name=event_name,
                        category=category,
                        asset_ids=asset_ids,
                        token=token,
                    )
                    drafts.append({"post": post_key, **draft})
                    continue
                except Exception:
                    logger.exception("Autofill failed for %s — creating a plain draft", post_key)
            try:
                design = self._create_design(asset_ids[0], title, token)
                drafts.append({"post": post_key, "channel": "create_design", **design})
            except Exception:
                logger.exception("Canva draft create failed for %s", post_key)
        if not drafts and by_role.get("cover"):
            design = self._create_design(by_role["cover"]["asset_id"], event_name, token)
            drafts.append({"post": "post_1", "channel": "create_design", **design})
        return drafts

    def autofill_design(
        self,
        *,
        brand_template_id: str,
        title: str,
        event_name: str,
        category: str,
        asset_ids: list[str],
        token: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/autofills then poll until the draft design exists."""
        token = token or self.ensure_access_token()
        dataset = self._brand_template_dataset(brand_template_id, token)
        data = self._build_autofill_data(dataset, asset_ids, event_name=event_name, category=category)
        client = self._client or httpx.Client(timeout=60.0)
        resp = client.post(
            f"{CANVA_API_BASE}/autofills",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "type": "create_from_brand_template",
                "brand_template_id": brand_template_id,
                "title": title,
                "data": data,
            },
        )
        if resp.status_code == 401:
            token = self.refresh_access_token(force=True) or True
            token = self.ensure_access_token()
            resp = client.post(
                f"{CANVA_API_BASE}/autofills",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "type": "create_from_brand_template",
                    "brand_template_id": brand_template_id,
                    "title": title,
                    "data": data,
                },
            )
        if resp.status_code >= 400:
            raise CanvaDeliveryError(f"Canva autofill failed: {resp.status_code} {resp.text[:400]}")
        job = (resp.json() or {}).get("job") or {}
        job_id = job.get("id")
        if job.get("status") == "success":
            return self._autofill_result(job, brand_template_id)
        if not job_id:
            raise CanvaDeliveryError(f"Canva autofill missing job id: {resp.text[:400]}")
        return self._poll_autofill_job(job_id, token, brand_template_id=brand_template_id)

    def _brand_template_dataset(self, template_id: str, token: str) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30.0)
        resp = client.get(
            f"{CANVA_API_BASE}/brand-templates/{template_id}/dataset",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            logger.warning("Brand template dataset unavailable (%s) — using heuristic fields", resp.status_code)
            return {}
        body = resp.json() or {}
        return body.get("dataset") or body

    def _build_autofill_data(
        self,
        dataset: dict[str, Any],
        asset_ids: list[str],
        *,
        event_name: str,
        category: str,
    ) -> dict[str, Any]:
        image_fields: list[str] = []
        text_fields: list[str] = []
        video_fields: list[str] = []
        for name, spec in (dataset or {}).items():
            kind = spec.get("type") if isinstance(spec, dict) else spec
            kind = str(kind or "").lower()
            if kind == "image":
                image_fields.append(name)
            elif kind == "video":
                video_fields.append(name)
            elif kind == "text":
                text_fields.append(name)

        data: dict[str, Any] = {}
        for field, asset_id in zip(image_fields, asset_ids):
            data[field] = {"type": "image", "asset_id": asset_id}
        if video_fields and asset_ids:
            data[video_fields[0]] = {"type": "video", "asset_id": asset_ids[-1]}

        text_values = [event_name, category, f"{event_name} · {category}"]
        name_hints = ("title", "name", "event", "headline", "text", "caption")
        prioritized = [f for f in text_fields if any(h in f.lower() for h in name_hints)] + [
            f for f in text_fields if f not in []
        ]
        seen: set[str] = set()
        ordered_text = [f for f in prioritized if not (f in seen or seen.add(f))]
        for field, value in zip(ordered_text, text_values):
            data[field] = {"type": "text", "text": value}

        if not data and asset_ids:
            # Dataset unknown — Canva silently skips unknown keys; send common names.
            data["cover"] = {"type": "image", "asset_id": asset_ids[0]}
            data["image"] = {"type": "image", "asset_id": asset_ids[0]}
            data["title"] = {"type": "text", "text": event_name}
            data["event_name"] = {"type": "text", "text": event_name}
        return data

    def _poll_autofill_job(
        self,
        job_id: str,
        token: str,
        *,
        brand_template_id: str,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30.0)
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = client.get(
                f"{CANVA_API_BASE}/autofills/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code >= 400:
                raise CanvaDeliveryError(f"Canva autofill poll failed: {resp.status_code} {resp.text[:300]}")
            job = (resp.json() or {}).get("job") or {}
            status = job.get("status")
            if status == "success":
                return self._autofill_result(job, brand_template_id)
            if status == "failed":
                raise CanvaDeliveryError(f"Canva autofill job failed: {job.get('error') or job}")
            time.sleep(0.7)
        raise CanvaDeliveryError(f"Timed out waiting for Canva autofill job {job_id}")

    def _autofill_result(self, job: dict[str, Any], brand_template_id: str) -> dict[str, Any]:
        result = job.get("result") or {}
        design = result.get("design") or {}
        return {
            "channel": "autofill",
            "job_id": job.get("id"),
            "brand_template_id": brand_template_id,
            "id": design.get("id"),
            "title": design.get("title"),
            "urls": design.get("urls") or {},
        }

    def _upload_asset(self, path: Path, token: str) -> dict[str, Any]:
        name_b64 = base64.b64encode(path.name.encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Asset-Upload-Metadata": json.dumps({"name_base64": name_b64}),
        }
        client = self._client or httpx.Client(timeout=120.0)
        resp = client.post(f"{CANVA_API_BASE}/asset-uploads", headers=headers, content=path.read_bytes())
        if resp.status_code == 401:
            self.refresh_access_token(force=True)
            token = self.ensure_access_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = client.post(f"{CANVA_API_BASE}/asset-uploads", headers=headers, content=path.read_bytes())
        if resp.status_code == 401:
            raise CanvaDeliveryError("Canva unauthorized (401) during asset upload")
        if resp.status_code >= 400:
            raise CanvaDeliveryError(f"Canva upload failed: {resp.status_code} {resp.text[:400]}")
        job = (resp.json() or {}).get("job") or {}
        job_id = job.get("id")
        if job.get("status") == "success" and job.get("asset"):
            asset = job["asset"]
            return {"job_id": job_id, "asset_id": asset.get("id"), "name": asset.get("name")}
        if not job_id:
            raise CanvaDeliveryError(f"Canva upload missing job id: {resp.text[:400]}")
        return self._poll_upload_job(job_id, token)

    def _poll_upload_job(self, job_id: str, token: str, *, timeout: float = 60.0) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30.0)
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = client.get(
                f"{CANVA_API_BASE}/asset-uploads/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code >= 400:
                raise CanvaDeliveryError(f"Canva upload poll failed: {resp.status_code} {resp.text[:300]}")
            job = (resp.json() or {}).get("job") or {}
            status = job.get("status")
            if status == "success":
                asset = job.get("asset") or {}
                return {"job_id": job_id, "asset_id": asset.get("id"), "name": asset.get("name")}
            if status == "failed":
                raise CanvaDeliveryError(f"Canva upload job failed: {job.get('error') or job}")
            time.sleep(0.6)
        raise CanvaDeliveryError(f"Timed out waiting for Canva upload job {job_id}")

    def _create_design(self, asset_id: str, title: str, token: str) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30.0)
        resp = client.post(
            f"{CANVA_API_BASE}/designs",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "design_type": {"type": "preset", "name": CANVA_DESIGN_PRESET},
                "asset_id": asset_id,
                "title": title,
            },
        )
        if resp.status_code >= 400:
            raise CanvaDeliveryError(f"Canva create design failed: {resp.status_code} {resp.text[:300]}")
        design = (resp.json() or {}).get("design") or resp.json()
        return {
            "id": design.get("id"),
            "title": design.get("title") or title,
            "urls": design.get("urls") or {},
        }

    def _send_via_webhook(
        self,
        category: str,
        event_name: str,
        files: dict[str, Any],
        webhook: str,
    ) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=120.0)
        attachments: list[tuple[str, tuple[str, bytes, str]]] = []
        ordered = [("cover", files["cover"])] + [
            (f"slide_{i:02d}", p) for i, p in enumerate(files["slides"])
        ]
        if files.get("reel_mp4"):
            ordered.append(("reel", files["reel_mp4"]))
        for field, path in ordered:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            attachments.append((field, (path.name, path.read_bytes(), mime)))
        resp = client.post(
            webhook,
            data={"category": category, "event_name": event_name, "source": "vv-luxe-cloud-worker"},
            files=attachments,
        )
        if resp.status_code >= 400:
            raise CanvaDeliveryError(f"Canva webhook failed: {resp.status_code} {resp.text[:300]}")
        logger.info("Canva webhook delivered %s (%d files)", event_name, len(attachments))
        return {
            "channel": "webhook",
            "sent_at": _utc_now(),
            "event_name": event_name,
            "category": category,
            "webhook_status": resp.status_code,
            "file_count": len(attachments),
            "complete": True,
        }


class CanvaTokenRefreshScheduler:
    """Renew Canva access tokens in the background before the 4-hour expiry."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="canva-token-refresh",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Canva token refresh scheduler started (every %ds)",
            CANVA_TOKEN_REFRESH_INTERVAL_SEC,
        )

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        svc = CanvaService()
        self._refresh_once(svc)
        interval = max(60, CANVA_TOKEN_REFRESH_INTERVAL_SEC)
        while not self._stop.is_set():
            if self._stop.wait(timeout=interval):
                break
            self._refresh_once(svc)

    @staticmethod
    def _refresh_once(svc: CanvaService) -> None:
        try:
            if not svc.is_configured() or not svc.is_connected():
                return
            data = _load_oauth_token() or {}
            if not data.get("refresh_token"):
                return
            svc.refresh_access_token(force=True)
        except CanvaNotConnectedError:
            logger.debug("Canva OAuth not connected — skip background token refresh")
        except Exception:
            logger.exception("Background Canva token refresh failed")


canva_token_refresh_scheduler = CanvaTokenRefreshScheduler()
