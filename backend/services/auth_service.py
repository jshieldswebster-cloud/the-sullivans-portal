"""Encrypted session auth and secure client vault tokens."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict
from threading import Lock
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.config import (
    CLIENT_VAULT_TTL_SEC,
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW_SEC,
    SESSION_MAX_AGE_SEC,
    STUDIO_PASSWORD,
    STUDIO_SECRET_KEY,
    STUDIO_USERNAME,
)

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "studio_user"
SESSION_ISSUED_KEY = "studio_issued_at"
SESSION_TOKEN_KEY = "studio_session_token"
SESSION_CSRF_KEY = "studio_csrf"

# Client gallery vault session keys
SESSION_VERIFIED = "client_gallery_verified"
SESSION_PORTFOLIO_ID = "client_gallery_portfolio_id"
SESSION_VAULT_TOKEN = "client_vault_token"
SESSION_VAULT_EXPIRES = "client_vault_expires"

_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_lock = Lock()
_gallery_access: dict[str, list[float]] = defaultdict(list)
_gallery_lock = Lock()


def _sign_payload(payload: str) -> str:
    return hmac.new(
        STUDIO_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _verify_signed_token(token: str, expected_prefix: str) -> dict[str, str] | None:
    """Validate HMAC-signed token. Returns parsed fields or None."""
    if not token or ":" not in token:
        return None
    try:
        payload, sig = token.rsplit(":", 1)
        if not hmac.compare_digest(_sign_payload(payload), sig):
            return None
        parts = payload.split(":")
        if len(parts) < 3 or parts[0] != expected_prefix:
            return None
        expires = int(parts[-1])
        if time.time() > expires:
            return None
        return {
            "prefix": parts[0],
            "subject": parts[1],
            "issued": parts[2] if len(parts) > 3 else "",
            "expires": str(expires),
        }
    except (ValueError, IndexError):
        return None


def create_session_token(username: str) -> str:
    issued = int(time.time())
    expires = issued + SESSION_MAX_AGE_SEC
    payload = f"studio:{username}:{issued}:{expires}"
    return f"{payload}:{_sign_payload(payload)}"


def create_vault_token(portfolio_id: str) -> tuple[str, int]:
    issued = int(time.time())
    expires = issued + CLIENT_VAULT_TTL_SEC
    payload = f"vault:{portfolio_id}:{issued}:{expires}"
    token = f"{payload}:{_sign_payload(payload)}"
    return token, expires


def _check_rate_limit(
    bucket: dict[str, list[float]],
    lock: Lock,
    key: str,
    *,
    limit: int,
    window_sec: int,
) -> None:
    now = time.time()
    with lock:
        attempts = [t for t in bucket[key] if now - t < window_sec]
        if len(attempts) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait and try again.",
            )
        attempts.append(now)
        bucket[key] = attempts


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def authenticate(username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(username, STUDIO_USERNAME)
    pass_ok = secrets.compare_digest(password, STUDIO_PASSWORD)
    return user_ok and pass_ok


def login_user(request: Request, username: str) -> str:
    """Establish authenticated studio session with signed token."""
    issued = int(time.time())
    token = create_session_token(username)
    csrf = secrets.token_urlsafe(32)
    request.session[SESSION_USER_KEY] = username
    request.session[SESSION_ISSUED_KEY] = issued
    request.session[SESSION_TOKEN_KEY] = token
    request.session[SESSION_CSRF_KEY] = csrf
    logger.info("Studio login: %s", username)
    return csrf


def logout_user(request: Request) -> None:
    request.session.clear()


def validate_studio_session(request: Request) -> bool:
    """Verify session integrity including signed token and expiry."""
    username = request.session.get(SESSION_USER_KEY)
    token = request.session.get(SESSION_TOKEN_KEY)
    if not username or not token:
        return False
    parsed = _verify_signed_token(token, "studio")
    if not parsed or parsed["subject"] != username:
        return False
    return True


def get_current_user(request: Request) -> str | None:
    if not validate_studio_session(request):
        return None
    return request.session.get(SESSION_USER_KEY)


def require_user(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_user_or_redirect(request: Request) -> str | RedirectResponse:
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return user


def authenticate_login(request: Request, username: str, password: str) -> bool:
    """Rate-limited login attempt."""
    _check_rate_limit(
        _login_attempts,
        _login_lock,
        _client_ip(request),
        limit=LOGIN_RATE_LIMIT,
        window_sec=LOGIN_RATE_WINDOW_SEC,
    )
    return authenticate(username, password)


def verify_csrf(request: Request, token: str | None) -> bool:
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


# ── Client gallery vault sessions ─────────────────────────────────────────────


def grant_vault_access(request: Request, portfolio_id: str) -> None:
    """Issue signed vault token for view-only gallery access."""
    token, expires = create_vault_token(portfolio_id)
    request.session[SESSION_VERIFIED] = True
    request.session[SESSION_PORTFOLIO_ID] = portfolio_id
    request.session[SESSION_VAULT_TOKEN] = token
    request.session[SESSION_VAULT_EXPIRES] = expires
    logger.info("Vault access granted: %s (expires %s)", portfolio_id, expires)


def revoke_vault_access(request: Request) -> None:
    for key in (
        SESSION_VERIFIED,
        SESSION_PORTFOLIO_ID,
        SESSION_VAULT_TOKEN,
        SESSION_VAULT_EXPIRES,
    ):
        request.session.pop(key, None)


def validate_vault_session(request: Request) -> bool:
    """Verify client gallery vault token has not expired or been tampered."""
    if not request.session.get(SESSION_VERIFIED):
        return False
    portfolio_id = request.session.get(SESSION_PORTFOLIO_ID)
    token = request.session.get(SESSION_VAULT_TOKEN)
    if not portfolio_id or not token:
        return False
    parsed = _verify_signed_token(token, "vault")
    if not parsed or parsed["subject"] != portfolio_id:
        return False
    expires = request.session.get(SESSION_VAULT_EXPIRES)
    if expires and time.time() > int(expires):
        revoke_vault_access(request)
        return False
    return True


def require_vault_access(request: Request) -> str:
    """Rate-limited vault API access; returns portfolio_id."""
    _check_rate_limit(
        _gallery_access,
        _gallery_lock,
        _client_ip(request),
        limit=LOGIN_RATE_LIMIT * 3,
        window_sec=LOGIN_RATE_WINDOW_SEC,
    )
    if not validate_vault_session(request):
        raise HTTPException(status_code=401, detail="Vault access denied or expired")
    portfolio_id = request.session.get(SESSION_PORTFOLIO_ID)
    if not portfolio_id:
        raise HTTPException(status_code=401, detail="No event selected")
    return portfolio_id
