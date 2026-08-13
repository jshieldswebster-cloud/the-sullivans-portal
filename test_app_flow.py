#!/usr/bin/env python3
"""App-flow tests: mocked Google OAuth, drive backlog APIs, frontend project bind.

Run:
  python test_app_flow.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.testclient import TestClient

from backend.config import EVENT_CATEGORIES, STUDIO_PASSWORD, STUDIO_SECRET_KEY, STUDIO_USERNAME
from backend.database import init_db, upsert_drive_project
import backend.database as database
from backend.routers import studio as studio_router
from backend.services.drive_service import EXACT_GOOGLE_REDIRECT_URI


PROJECT_ID = "11111111-2222-3333-4444-555555555555"
POSTING_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
COVER_ID = "cover-file-1"
CAROUSEL_IDS = [f"carousel-{i}" for i in range(8)]
REEL_IDS = ["reel-file-1", "reel-file-2"]


class FakeOAuthCredentials:
    token = "ya29.mock-access-token"
    refresh_token = "1//mock-refresh-token"
    token_uri = "https://oauth2.googleapis.com/token"
    client_id = "mock-client.apps.googleusercontent.com"
    client_secret = "mock-secret"
    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/drive.readonly",
    ]


class FakeOAuthFlow:
    """Stand-in for google_auth_oauthlib.flow.Flow (no live Google calls)."""

    def __init__(self, *args, **kwargs):
        self.redirect_uri = kwargs.get("redirect_uri") or EXACT_GOOGLE_REDIRECT_URI
        self.code_verifier = kwargs.get("code_verifier") or ("v" * 64)
        self.credentials = FakeOAuthCredentials()
        if kwargs.get("autogenerate_code_verifier"):
            self.code_verifier = "v" * 64

    @classmethod
    def from_client_config(cls, client_config, scopes, **kwargs):
        return cls(**kwargs)

    def authorization_url(self, **kwargs):
        from urllib.parse import urlencode

        state = kwargs.get("state") or "test-state"
        query = urlencode(
            {
                "response_type": "code",
                "client_id": FakeOAuthCredentials.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "openid https://www.googleapis.com/auth/drive.readonly",
                "access_type": kwargs.get("access_type", "offline"),
                "prompt": kwargs.get("prompt", "consent"),
                "state": state,
                "code_challenge": "pkce-challenge",
                "code_challenge_method": "S256",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}", state

    def fetch_token(self, **kwargs):
        if not kwargs.get("code"):
            raise ValueError("authorization code required")
        return {
            "access_token": self.credentials.token,
            "refresh_token": self.credentials.refresh_token,
        }


def _preview(file_id: str) -> dict:
    return {
        "id": file_id,
        "name": f"{file_id}.jpg",
        "mime_type": "image/jpeg",
        "thumbnail_link": f"https://example.test/thumbs/{file_id}.jpg",
    }


def fake_get_file_previews(self, file_ids):
    return [_preview(fid) for fid in (file_ids or [])]


def _seed_projects() -> None:
    upsert_drive_project(
        {
            "id": PROJECT_ID,
            "drive_folder_id": "folder-backlog-1",
            "category": "Weddings",
            "event_name": "The Johnson Wedding",
            "status": "pending_review",
            "cover_drive_id": COVER_ID,
            "carousel_drive_ids": CAROUSEL_IDS,
            "reel_drive_ids": REEL_IDS,
            "asset_count": 11,
            "parse_warnings": [],
        }
    )
    upsert_drive_project(
        {
            "id": POSTING_ID,
            "drive_folder_id": "folder-posting-1",
            "category": "Birthdays",
            "event_name": "Maya's 30th",
            "status": "review_for_posting",
            "queue_name": "Review for Posting",
            "cover_drive_id": "posting-cover",
            "carousel_drive_ids": [f"p2-{i}" for i in range(8)],
            "reel_drive_ids": ["p3-1"],
            "asset_count": 10,
            "batch_date": "2026-08-13",
        }
    )


def _frontend_select_project(posting: list, backlog: list, preferred_id: str | None) -> dict | None:
    """Mirrors driveReviewEnsureSelection / driveReviewFirstAvailable in drive_review.js."""
    combined = [("posting", p) for p in posting] + [("backlog", p) for p in backlog]
    if preferred_id:
        for queue, project in combined:
            if project.get("id") == preferred_id:
                return {**project, "_queue": queue}
    if posting:
        return {**posting[0], "_queue": "posting"}
    if backlog:
        return {**backlog[0], "_queue": "backlog"}
    return None


class AppFlowTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    client: TestClient
    patches: list

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(cls.tmp.name)
        db_path = tmp_path / "test.db"
        token_path = tmp_path / "drive_oauth_token.json"

        database.DATABASE_PATH = db_path
        init_db()
        _seed_projects()

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=STUDIO_SECRET_KEY, max_age=86400)
        app.include_router(studio_router.router)
        app.include_router(studio_router.api)

        fake_job = MagicMock()
        fake_job.id = "job-mock-1"
        fake_job.status = "pending"

        cls.patches = [
            patch("backend.services.drive_service.GOOGLE_DRIVE_CLIENT_ID", FakeOAuthCredentials.client_id),
            patch("backend.services.drive_service.GOOGLE_DRIVE_CLIENT_SECRET", FakeOAuthCredentials.client_secret),
            patch("backend.services.drive_service.GOOGLE_DRIVE_OAUTH_TOKEN_PATH", token_path),
            patch("backend.services.drive_service.DriveService.get_file_previews", fake_get_file_previews),
            patch("backend.services.drive_service.DriveService.is_connected", return_value=True),
            patch("backend.services.drive_service.DriveService.is_configured", return_value=True),
            patch("google_auth_oauthlib.flow.Flow", FakeOAuthFlow),
            patch("backend.routers.studio.montage_jobs.create_typed", return_value=fake_job),
        ]
        for p in cls.patches:
            p.start()

        cls.client = TestClient(app)
        login = cls.client.post(
            "/login",
            data={"username": STUDIO_USERNAME, "password": STUDIO_PASSWORD},
            follow_redirects=False,
        )
        if login.status_code not in (200, 302, 303):
            raise RuntimeError(f"Login failed: {login.status_code} {login.text}")

    @classmethod
    def tearDownClass(cls) -> None:
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def test_oauth_start_does_not_500_and_uses_exact_redirect_uri(self) -> None:
        res = self.client.get("/api/studio/drive/oauth/start", follow_redirects=False)
        self.assertNotEqual(res.status_code, 500, res.text)
        self.assertEqual(res.status_code, 302)
        location = res.headers.get("location") or ""
        self.assertTrue(
            location.startswith("https://accounts.google.com/o/oauth2/v2/auth"),
            location,
        )
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        redirect = params.get("redirect_uri", [""])[0]
        self.assertEqual(redirect, EXACT_GOOGLE_REDIRECT_URI)
        self.assertFalse(redirect.endswith("/"))
        self.assertIn("state", params)
        self.assertEqual(params.get("access_type", [""])[0], "offline")
        self.assertEqual(params.get("prompt", [""])[0], "consent")

    def test_oauth_callback_exchanges_code_without_google(self) -> None:
        start = self.client.get("/api/studio/drive/oauth/start", follow_redirects=False)
        self.assertEqual(start.status_code, 302)
        state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
        self.assertTrue(state)

        callback = self.client.get(
            f"/auth/callback?code=4/mock-auth-code&state={state}",
            follow_redirects=False,
        )
        self.assertNotEqual(callback.status_code, 500, callback.text)
        self.assertEqual(callback.status_code, 302)
        loc = callback.headers.get("location") or ""
        self.assertNotIn("exchange_failed", loc)
        self.assertIn("drive=connected", loc)

        from backend.config import GOOGLE_DRIVE_OAUTH_SETTINGS_KEY
        from backend.database import get_studio_setting
        from backend.services.drive_service import _load_oauth_token

        stored = get_studio_setting(GOOGLE_DRIVE_OAUTH_SETTINGS_KEY, default={})
        self.assertEqual(stored.get("encoding"), "fernet-v1")
        self.assertTrue(stored.get("payload"))
        self.assertNotIn(FakeOAuthCredentials.refresh_token, json.dumps(stored))

        loaded = _load_oauth_token()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["refresh_token"], FakeOAuthCredentials.refresh_token)
        self.assertEqual(loaded["token"], FakeOAuthCredentials.token)

    def test_refresh_token_survives_google_omitting_it_on_renewal(self) -> None:
        from backend.services.drive_service import _load_oauth_token, _save_oauth_token

        _save_oauth_token(
            {
                "token": "ya29.old-access",
                "refresh_token": "1//keep-me-forever",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": FakeOAuthCredentials.client_id,
                "client_secret": FakeOAuthCredentials.client_secret,
                "scopes": FakeOAuthCredentials.scopes,
            }
        )
        _save_oauth_token(
            {
                "token": "ya29.new-access",
                "refresh_token": None,
            }
        )
        loaded = _load_oauth_token()
        self.assertEqual(loaded["refresh_token"], "1//keep-me-forever")
        self.assertEqual(loaded["token"], "ya29.new-access")

    def test_refresh_access_token_uses_stored_refresh_token(self) -> None:
        from datetime import datetime, timedelta, timezone

        from backend.services.drive_service import DriveService, _save_oauth_token

        _save_oauth_token(
            {
                "token": "ya29.expired",
                "refresh_token": "1//keep-me-forever",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": FakeOAuthCredentials.client_id,
                "client_secret": FakeOAuthCredentials.client_secret,
                "scopes": FakeOAuthCredentials.scopes,
                "expiry": "2020-01-01T00:00:00Z",
            }
        )

        class _RefreshedCreds:
            token = "ya29.renewed"
            refresh_token = None
            token_uri = "https://oauth2.googleapis.com/token"
            client_id = FakeOAuthCredentials.client_id
            client_secret = FakeOAuthCredentials.client_secret
            scopes = FakeOAuthCredentials.scopes
            expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            valid = False

            def refresh(self, _request):
                self.token = "ya29.renewed"
                self.valid = True

            def to_json(self):
                import json as _json

                return _json.dumps(
                    {
                        "token": self.token,
                        "refresh_token": self.refresh_token,
                        "token_uri": self.token_uri,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scopes": self.scopes,
                        "expiry": self.expiry.replace(tzinfo=None).isoformat() + "Z",
                    }
                )

        svc = DriveService()
        with patch.object(svc, "_credentials_from_token_data", return_value=_RefreshedCreds()):
            self.assertTrue(svc.refresh_access_token(force=True))

        from backend.services.drive_service import _load_oauth_token

        loaded = _load_oauth_token()
        self.assertEqual(loaded["token"], "ya29.renewed")
        self.assertEqual(loaded["refresh_token"], "1//keep-me-forever")

    def test_backlog_status_returns_active_project_id(self) -> None:
        res = self.client.get("/api/studio/backlog/status")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertNotIn("error", body)
        self.assertIn("active_project_id", body)
        self.assertEqual(body["active_project_id"], POSTING_ID)
        self.assertGreaterEqual(body.get("review_queue_count") or 0, 1)
        queue = body.get("review_queue") or []
        self.assertTrue(any(p["id"] == POSTING_ID for p in queue))
        self.assertEqual(queue[0]["event_name"], "Maya's 30th")
        self.assertEqual(queue[0]["category"], "Birthdays")

    def test_drive_backlog_list_and_active_id(self) -> None:
        res = self.client.get("/api/studio/drive/projects?status=pending_review")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertGreaterEqual(body.get("count") or 0, 1)
        self.assertEqual(body.get("active_project_id"), PROJECT_ID)
        projects = body.get("projects") or []
        match = next((p for p in projects if p["id"] == PROJECT_ID), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["event_name"], "The Johnson Wedding")
        self.assertEqual(match["category"], "Weddings")
        self.assertEqual(match["cover_drive_id"], COVER_ID)

    def test_get_project_loads_editor_fields_not_found_or_500(self) -> None:
        res = self.client.get(f"/api/studio/drive/projects/{PROJECT_ID}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("Project not found", res.text)
        body = res.json()
        self.assertEqual(body["id"], PROJECT_ID)
        self.assertEqual(body["event_name"], "The Johnson Wedding")
        self.assertEqual(body["category"], "Weddings")
        self.assertEqual(body["cover_drive_id"], COVER_ID)
        self.assertEqual(len(body.get("carousel_drive_ids") or []), 8)
        self.assertIn("previews", body)
        self.assertEqual(body["previews"]["cover"]["id"], COVER_ID)
        self.assertEqual(len(body["previews"]["carousel"]), 8)
        self.assertGreaterEqual(len(body["previews"]["reel"]), 1)

    def test_missing_project_is_404_not_500(self) -> None:
        res = self.client.get("/api/studio/drive/projects/not-a-real-project")
        self.assertEqual(res.status_code, 404)
        self.assertNotEqual(res.status_code, 500)
        self.assertEqual(res.json().get("error"), "Project not found")

        placeholder = self.client.get("/api/studio/drive/projects/undefined")
        self.assertEqual(placeholder.status_code, 404)
        self.assertNotEqual(placeholder.status_code, 500)

    def test_frontend_state_selects_active_project_without_not_found(self) -> None:
        status = self.client.get("/api/studio/backlog/status").json()
        listing = self.client.get("/api/studio/drive/projects?status=pending_review").json()
        posting = status.get("review_queue") or []
        backlog = listing.get("projects") or []

        selected = _frontend_select_project(
            posting,
            backlog,
            preferred_id="stale-or-missing-id",
        )
        self.assertIsNotNone(selected)
        self.assertIn(selected["_queue"], ("posting", "backlog"))

        detail = self.client.get(f"/api/studio/drive/projects/{selected['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertNotEqual(detail.status_code, 500)
        self.assertNotIn("Project not found", detail.text)
        payload = detail.json()
        self.assertTrue(payload.get("event_name"))
        self.assertIn(payload.get("category"), EVENT_CATEGORIES)
        self.assertTrue(payload.get("cover_drive_id") or (payload.get("previews") or {}).get("cover"))

        default = _frontend_select_project(posting, backlog, preferred_id=None)
        self.assertEqual(default["id"], status["active_project_id"])

    def test_sync_and_daily_batch_enqueue_without_500(self) -> None:
        sync = self.client.post("/api/studio/drive/sync", json={})
        self.assertNotEqual(sync.status_code, 500, sync.text)
        self.assertEqual(sync.status_code, 200)
        self.assertIn("job_id", sync.json())

        batch = self.client.post("/api/studio/backlog/run-daily", json={"force": True})
        self.assertNotEqual(batch.status_code, 500, batch.text)
        self.assertEqual(batch.status_code, 200)
        self.assertIn("job_id", batch.json())

        alias = self.client.post("/api/studio/daily-batch", json={"force": True})
        self.assertNotEqual(alias.status_code, 500, alias.text)
        self.assertEqual(alias.status_code, 200)
        self.assertIn("job_id", alias.json())

        missing = self.client.post("/api/studio/backlog/process-folder", json={})
        self.assertEqual(missing.status_code, 400)

        manual = self.client.post(
            "/api/studio/backlog/process-folder",
            json={"folder_id": "https://drive.google.com/drive/folders/1abcFolderXYZ"},
        )
        self.assertNotEqual(manual.status_code, 500, manual.text)
        self.assertEqual(manual.status_code, 200)
        self.assertIn("job_id", manual.json())
        self.assertEqual(manual.json().get("folder_id"), "1abcFolderXYZ")

        canva = self.client.get("/api/studio/canva/status")
        self.assertEqual(canva.status_code, 200, canva.text)
        self.assertIn("configured", canva.json())
        self.assertIn("redirect_uri", canva.json())

    def test_dashboard_and_frontend_bind_helpers_exist(self) -> None:
        page = self.client.get("/dashboard", follow_redirects=False)
        self.assertIn(page.status_code, (200, 302))
        if page.status_code == 200:
            html = page.text
            self.assertIn('id="event-name"', html)
            self.assertIn('id="backlog-browse-btn"', html)
            self.assertIn('id="backlog-folder-id"', html)
            self.assertIn('id="canva-connect-btn"', html)
            self.assertIn("Connect Canva", html)
            self.assertIn("drive-review-grid", html)
            self.assertNotIn("Internal Server Error", html)

        review_js = (ROOT / "backend/web/static/js/drive_review.js").read_text(encoding="utf-8")
        dashboard_js = (ROOT / "backend/web/static/js/dashboard.js").read_text(encoding="utf-8")
        self.assertIn("function driveReviewEnsureSelection", review_js)
        self.assertIn("function driveReviewFirstAvailable", review_js)
        self.assertIn("applyDriveProjectToEditor", review_js)
        self.assertIn("applyDriveProjectToEditor", dashboard_js)
        self.assertIn("eventName.value = project.event_name", dashboard_js)
        self.assertIn("setActiveCategory(project.category)", dashboard_js)
        self.assertIn("cover_drive_id", dashboard_js)
        self.assertIn("carousel_drive_ids", dashboard_js)
        self.assertIn('JSON.stringify({ force: true })', review_js)
        self.assertIn("driveReviewStartBacklogBatch", review_js)
        self.assertIn("driveReviewProcessFolder", review_js)
        self.assertIn("/backlog/process-folder", review_js)
        self.assertIn('mode: "project"', review_js)
        self.assertIn("driveReviewRefreshCanvaStatus", review_js)
        self.assertIn("/canva/status", review_js)

    def test_parse_project_reuses_last_carousel_when_no_reel_media(self) -> None:
        from backend.services.drive_service import DRIVE_POST_2_COUNT, DriveService

        images = [
            {
                "id": f"img-{i}",
                "name": f"{i:02d}.jpg",
                "kind": "image",
                "modified_time": f"2026-01-{i+1:02d}",
            }
            for i in range(1 + DRIVE_POST_2_COUNT)
        ]
        svc = DriveService()
        with patch.object(svc, "list_folder_assets", return_value=images):
            package = svc.parse_project_folder("folder-nine-photos")
        self.assertEqual(package["cover_drive_id"], "img-0")
        self.assertEqual(len(package["carousel_drive_ids"]), DRIVE_POST_2_COUNT)
        self.assertTrue(package["reel_drive_ids"])
        self.assertEqual(package["reel_drive_ids"][0], package["carousel_drive_ids"][-1])


class CloudPipelineTests(unittest.TestCase):
    """Drive backlog worker helpers: package completeness, error patches, Canva send."""

    def test_classify_error_maps_to_patches(self) -> None:
        from backend.services.canva_service import CanvaNotConfiguredError
        from backend.services.drive_service import DriveNotConnectedError
        from backend.workers.cloud_pipeline_worker import classify_error

        self.assertEqual(classify_error(DriveNotConnectedError("nope")), "drive_auth")
        self.assertEqual(classify_error(CanvaNotConfiguredError("missing")), "canva_config")
        self.assertEqual(classify_error(RuntimeError("Folder is not ready to stage")), "incomplete_package")
        self.assertEqual(classify_error(RuntimeError("Canva unauthorized (401)")), "canva_auth")
        self.assertEqual(classify_error(RuntimeError("timed out waiting")), "backoff")
        self.assertEqual(classify_error(RuntimeError("unexpected boom")), "retry")

    def test_already_sent_to_canva(self) -> None:
        from backend.workers.cloud_pipeline_worker import already_sent_to_canva

        self.assertFalse(already_sent_to_canva({}))
        self.assertFalse(already_sent_to_canva({"local_paths": {"canva": {"complete": False}}}))
        self.assertTrue(
            already_sent_to_canva(
                {"local_paths": {"canva": {"sent_at": "2026-08-13T00:00:00Z", "complete": True}}}
            )
        )

    def test_collect_package_files_and_canva_connect(self) -> None:
        from backend.services.canva_service import CanvaNotConfiguredError, CanvaService, collect_package_files

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name) / "Ideal_Row_Posts"
        post1 = base / "Post_1"
        post2 = base / "Post_2"
        carousel = post2 / "carousel"
        post3 = base / "Post_3"
        for folder in (post1, post2, carousel, post3):
            folder.mkdir(parents=True, exist_ok=True)
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xd9"
        )
        (post1 / "cover.jpg").write_bytes(jpeg)
        for i in range(1, 9):
            (post2 / f"photo_{i:02d}.jpg").write_bytes(jpeg)
        (carousel / "slide_00_title.jpg").write_bytes(jpeg)
        for i in range(1, 9):
            (carousel / f"slide_{i:02d}.jpg").write_bytes(jpeg)
        (post3 / "reel_01.jpg").write_bytes(jpeg)

        files = collect_package_files("Weddings", "Test", staging_path=str(base))
        self.assertTrue(files["complete"], files["missing"])
        self.assertEqual(len(files["photos"]), 8)
        self.assertEqual(len(files["slides"]), 9)

        with self.assertRaises(CanvaNotConfiguredError):
            CanvaService().send_package(category="Weddings", event_name="Test", files=files)

        class FakeResp:
            def __init__(self, code: int, payload: dict):
                self.status_code = code
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

        class FakeClient:
            def post(self, url, **kwargs):
                if "oauth/token" in url:
                    return FakeResp(
                        200,
                        {
                            "access_token": "new-access",
                            "refresh_token": "rotated-refresh",
                            "expires_in": 14400,
                            "token_type": "Bearer",
                            "scope": "asset:write design:content:write",
                        },
                    )
                if "asset-uploads" in url:
                    return FakeResp(
                        200,
                        {
                            "job": {
                                "id": "job-1",
                                "status": "success",
                                "asset": {"id": "asset-1", "name": "cover.jpg"},
                            }
                        },
                    )
                if url.endswith("/autofills"):
                    return FakeResp(
                        200,
                        {
                            "job": {
                                "id": "af-1",
                                "status": "success",
                                "result": {
                                    "type": "create_design",
                                    "design": {
                                        "id": "draft-1",
                                        "title": "Test",
                                        "urls": {"edit_url": "https://canva.com/design/draft-1"},
                                    },
                                },
                            }
                        },
                    )
                if url.endswith("/designs"):
                    return FakeResp(200, {"design": {"id": "design-1", "title": "Test", "urls": {}}})
                return FakeResp(400, {"error": url})

            def get(self, url, **kwargs):
                if "dataset" in url:
                    return FakeResp(
                        200,
                        {
                            "dataset": {
                                "cover": {"type": "image"},
                                "title": {"type": "text"},
                            }
                        },
                    )
                if "autofills/" in url:
                    return FakeResp(
                        200,
                        {
                            "job": {
                                "id": "af-1",
                                "status": "success",
                                "result": {
                                    "design": {"id": "draft-1", "title": "Test", "urls": {}},
                                },
                            }
                        },
                    )
                return FakeResp(
                    200,
                    {"job": {"id": "job-1", "status": "success", "asset": {"id": "asset-1"}}},
                )

        svc = CanvaService(client=FakeClient(), access_token="test-token")
        result = svc.send_package(category="Weddings", event_name="Test", files=files)
        self.assertEqual(result["channel"], "canva_autofill")
        self.assertTrue(result["complete"])
        self.assertTrue(result["assets"])
        self.assertTrue(result["drafts"])

    def test_canva_oauth_pkce_and_token_refresh(self) -> None:
        from backend.services import canva_service as canva_mod
        from backend.services.canva_service import CanvaService

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        token_path = Path(tmp.name) / "canva_oauth_token.json"
        store: dict = {}

        class FakeResp:
            def __init__(self, code: int, payload: dict):
                self.status_code = code
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

        class TokenClient:
            def post(self, url, **kwargs):
                return FakeResp(
                    200,
                    {
                        "access_token": "access-2",
                        "refresh_token": "refresh-2",
                        "expires_in": 14400,
                        "token_type": "Bearer",
                    },
                )

        with patch.object(canva_mod, "CANVA_CLIENT_ID", "OC-test"), patch.object(
            canva_mod, "CANVA_CLIENT_SECRET", "cnvca-test"
        ), patch.object(canva_mod, "CANVA_OAUTH_TOKEN_PATH", token_path), patch.object(
            canva_mod, "get_studio_setting", lambda key, default=None: store.get(key, default or {})
        ), patch.object(
            canva_mod, "set_studio_setting", lambda key, value: store.__setitem__(key, value)
        ):
            svc = CanvaService(client=TokenClient())
            url, verifier = svc.oauth_start_url(state="abc")
            self.assertIn("code_challenge=", url)
            self.assertIn("code_challenge_method=s256", url)
            self.assertIn("client_id=OC-test", url)
            self.assertIn("redirect_uri=", url)
            self.assertGreaterEqual(len(verifier), 43)
            svc.oauth_exchange("auth-code", code_verifier=verifier)
            self.assertTrue(svc.is_connected())
            rotated = svc.refresh_access_token(force=True)
            self.assertTrue(rotated)


def main() -> int:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(AppFlowTests))
    suite.addTests(loader.loadTestsFromTestCase(CloudPipelineTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
