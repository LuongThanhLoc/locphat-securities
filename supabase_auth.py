"""Supabase Auth BFF for Lộc Phát Securities.

The browser never receives Supabase tokens. Access and refresh tokens live in
HttpOnly cookies, while FastAPI validates access JWTs through Supabase JWKS and
forwards the user token to PostgREST so Row Level Security remains effective.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
import requests
from fastapi import HTTPException, Request, Response
from jwt import PyJWKClient


ACCESS_COOKIE = "lp_sb_access"
REFRESH_COOKIE = "lp_sb_refresh"
CSRF_COOKIE = "lp_csrf"
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,22}[a-z0-9]$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RESERVED_USERNAMES = {
    "admin", "administrator", "api", "auth", "help", "locphat", "root",
    "security", "support", "system", "webmaster",
}

_jwks_lock = threading.Lock()
_jwks_client: Optional[PyJWKClient] = None


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    username: str
    access_token: str
    csrf_token: str
    role: str = "user"


def _env(name: str, *, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise HTTPException(status_code=503, detail="Dịch vụ tài khoản chưa được cấu hình.")
    return value


def configured() -> bool:
    return all(os.getenv(name, "").strip() for name in (
        "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY",
    ))


def _base_url() -> str:
    return _env("SUPABASE_URL").rstrip("/")


def _publishable_key() -> str:
    return _env("SUPABASE_PUBLISHABLE_KEY")


def _secret_key() -> str:
    return _env("SUPABASE_SECRET_KEY")


def _is_production() -> bool:
    return bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_ENV") == "production")


def _auth_headers(token: Optional[str] = None, *, service: bool = False) -> dict[str, str]:
    key = _secret_key() if service else _publishable_key()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Opaque sb_secret_* keys authenticate through `apikey`. They are not JWTs
    # and Supabase rejects them in Authorization: Bearer. Legacy service_role
    # JWTs remain compatible when explicitly supplied as the token instead.
    return headers


def normalize_username(value: str) -> str:
    username = str(value or "").strip().lower()
    if not USERNAME_RE.fullmatch(username) or username in RESERVED_USERNAMES:
        raise HTTPException(
            status_code=422,
            detail="Tên hiển thị phải dài 3–24 ký tự và chỉ gồm chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang.",
        )
    return username


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="Địa chỉ email không hợp lệ.")
    return email


def validate_password(password: str, confirmation: str) -> None:
    if password != confirmation:
        raise HTTPException(status_code=422, detail="Hai mật khẩu chưa trùng nhau.")
    if not 10 <= len(password) <= 128:
        raise HTTPException(status_code=422, detail="Mật khẩu phải dài từ 10 đến 128 ký tự.")


def _request_json(method: str, url: str, *, headers: dict[str, str], payload: Optional[dict] = None,
                  params: Optional[dict] = None, timeout: int = 12) -> tuple[int, Any]:
    try:
        response = requests.request(method, url, headers=headers, json=payload, params=params, timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Dịch vụ tài khoản tạm thời không phản hồi.") from exc


def _error_message(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or body.get("error_description") or fallback)
    return fallback


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _identity_hash(value: str) -> str:
    pepper = os.getenv("AUTH_RATE_LIMIT_SECRET") or os.getenv("SUPABASE_SECRET_KEY", "")
    return hashlib.sha256(f"{pepper}:{value}".encode()).hexdigest()


def _postgrest(method: str, path: str, *, token: Optional[str] = None, service: bool = False,
               payload: Optional[Any] = None, params: Optional[dict] = None,
               prefer: Optional[str] = None) -> tuple[int, Any]:
    headers = _auth_headers(token, service=service)
    if prefer:
        headers["Prefer"] = prefer
    return _request_json(method, f"{_base_url()}/rest/v1/{path}", headers=headers, payload=payload, params=params)


def _attempt_count(request: Request, action: str, *, minutes: int, email: str = "") -> int:
    if not os.getenv("SUPABASE_SECRET_KEY", "").strip():
        return 0
    params = {
        "select": "id",
        "ip_hash": f"eq.{_identity_hash(_client_ip(request))}",
        "action": f"eq.{action}",
        "created_at": f"gte.{(datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec='seconds')}",
        "limit": "21",
    }
    if email:
        params["email_hash"] = f"eq.{_identity_hash(email)}"
        params["succeeded"] = "eq.false"
    status, body = _postgrest("GET", "auth_login_attempts", service=True, params=params)
    if status >= 400 or not isinstance(body, list):
        return 0
    return len(body)


def _record_auth_attempt(request: Request, action: str, email: str, succeeded: bool) -> None:
    if not os.getenv("SUPABASE_SECRET_KEY", "").strip():
        return
    _postgrest(
        "POST", "auth_login_attempts", service=True,
        payload={
            "action": action,
            "ip_hash": _identity_hash(_client_ip(request)),
            "email_hash": _identity_hash(email),
            "succeeded": bool(succeeded),
        },
        prefer="return=minimal",
    )


def captcha_required(request: Request, email_value: str) -> bool:
    try:
        email = normalize_email(email_value)
    except HTTPException:
        return False
    return _attempt_count(request, "login", minutes=15, email=email) >= 3


def _captcha_security(token: str) -> dict[str, str]:
    return {"captcha_token": str(token or "")}


def signup(request: Request, username_value: str, email_value: str, password: str,
           confirmation: str, captcha_token: str) -> dict[str, Any]:
    username = normalize_username(username_value)
    email = normalize_email(email_value)
    validate_password(password, confirmation)
    if _attempt_count(request, "register", minutes=60) >= 5:
        raise HTTPException(status_code=429, detail="Quá nhiều lần đăng ký. Vui lòng thử lại sau một giờ.")
    if not captcha_token:
        raise HTTPException(status_code=422, detail="Vui lòng hoàn tất CAPTCHA.")
    payload = {
        "email": email,
        "password": password,
        "data": {"username": username},
        "gotrue_meta_security": _captcha_security(captcha_token),
    }
    status, body = _request_json("POST", f"{_base_url()}/auth/v1/signup", headers=_auth_headers(), payload=payload)
    _record_auth_attempt(request, "register", email, status < 400)
    if status >= 400:
        message = _error_message(body, "Không thể tạo tài khoản với thông tin này.")
        if "captcha" in message.lower():
            message = "CAPTCHA không hợp lệ hoặc đã hết hạn."
        elif "already" in message.lower() or "unique" in message.lower():
            message = "Email hoặc tên hiển thị đã được sử dụng."
        raise HTTPException(status_code=409 if status in (400, 422) else status, detail=message)
    if not isinstance(body, dict) or not body.get("access_token") or not body.get("refresh_token"):
        raise HTTPException(status_code=503, detail="Supabase chưa tắt xác minh email; tài khoản chưa thể đăng nhập ngay.")
    return body


def login(request: Request, email_value: str, password: str, captcha_token: str) -> dict[str, Any]:
    email = normalize_email(email_value)
    failures = _attempt_count(request, "login", minutes=15, email=email)
    if _attempt_count(request, "login", minutes=15) >= 20:
        raise HTTPException(status_code=429, detail="Quá nhiều lần đăng nhập. Vui lòng thử lại sau 15 phút.")
    if failures >= 3 and not captcha_token:
        raise HTTPException(status_code=403, detail={"code": "captcha_required", "message": "Vui lòng hoàn tất CAPTCHA."})
    # Supabase CAPTCHA may be configured globally, so the frontend obtains an
    # interaction-only token even before the challenge becomes visibly forced.
    if not captcha_token:
        raise HTTPException(status_code=403, detail={"code": "captcha_required", "message": "Đang xác minh trình duyệt. Vui lòng thử lại."})
    payload = {
        "email": email,
        "password": password,
        "gotrue_meta_security": _captcha_security(captcha_token),
    }
    status, body = _request_json(
        "POST", f"{_base_url()}/auth/v1/token",
        headers=_auth_headers(), params={"grant_type": "password"}, payload=payload,
    )
    succeeded = status < 400 and isinstance(body, dict) and bool(body.get("access_token"))
    _record_auth_attempt(request, "login", email, succeeded)
    if not succeeded:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng.")
    return body


def refresh_session(refresh_token: str) -> Optional[dict[str, Any]]:
    if not refresh_token or not configured():
        return None
    status, body = _request_json(
        "POST", f"{_base_url()}/auth/v1/token", headers=_auth_headers(),
        params={"grant_type": "refresh_token"}, payload={"refresh_token": refresh_token},
    )
    if status >= 400 or not isinstance(body, dict) or not body.get("access_token"):
        return None
    return body


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    with _jwks_lock:
        if _jwks_client is None:
            _jwks_client = PyJWKClient(f"{_base_url()}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=300)
        return _jwks_client


def decode_access_token(token: str) -> dict[str, Any]:
    if not token:
        raise jwt.InvalidTokenError("missing token")
    header = jwt.get_unverified_header(token)
    algorithm = str(header.get("alg") or "")
    if algorithm not in {"RS256", "ES256"}:
        raise jwt.InvalidAlgorithmError("unsupported Supabase signing algorithm")
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=[algorithm],
        audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
        issuer=f"{_base_url()}/auth/v1",
        options={"require": ["exp", "sub", "aud", "iss"]},
    )


def _csrf_for_request(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE, "")


def user_from_claims(claims: dict[str, Any], access_token: str, csrf_token: str) -> AuthUser:
    metadata = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
    app_metadata = claims.get("app_metadata") if isinstance(claims.get("app_metadata"), dict) else {}
    return AuthUser(
        id=str(claims.get("sub") or ""),
        email=str(claims.get("email") or ""),
        username=str(metadata.get("username") or "Nhà đầu tư"),
        access_token=access_token,
        csrf_token=csrf_token,
        role="admin" if app_metadata.get("role") == "admin" else "user",
    )


def authenticate_request(request: Request) -> Optional[AuthUser]:
    cached = getattr(request.state, "auth_user", None)
    if cached:
        return cached
    token = request.cookies.get(ACCESS_COOKIE, "")
    if not token or not configured():
        return None
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    user = user_from_claims(claims, token, _csrf_for_request(request))
    request.state.auth_user = user
    return user


def require_user(request: Request) -> AuthUser:
    user = authenticate_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Bạn cần đăng nhập để sử dụng tính năng này.")
    return user


def require_admin(request: Request) -> AuthUser:
    """Require a server-issued role from trusted Supabase app metadata."""
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị.")
    return user


def require_csrf(request: Request, user: AuthUser) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    if not user.csrf_token or not supplied or not secrets.compare_digest(user.csrf_token, supplied):
        raise HTTPException(status_code=403, detail="Phiên bảo mật không hợp lệ. Vui lòng tải lại trang.")


def set_session_cookies(response: Response, payload: dict[str, Any], csrf_token: Optional[str] = None) -> str:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    expires_in = max(60, int(payload.get("expires_in") or 3600))
    csrf = csrf_token or secrets.token_urlsafe(24)
    secure = _is_production()
    response.set_cookie(ACCESS_COOKIE, access, max_age=expires_in, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=30 * 86400, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=30 * 86400, httponly=True, secure=secure, samesite="lax", path="/")
    return csrf


def clear_session_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def logout_remote(user: AuthUser) -> None:
    _request_json("POST", f"{_base_url()}/auth/v1/logout", headers=_auth_headers(user.access_token), payload={})


def list_watchlist(user: AuthUser) -> list[dict[str, Any]]:
    status, body = _postgrest(
        "GET", "watchlist_items", token=user.access_token,
        params={"select": "symbol,company_name,exchange,note,ai_analysis,added_at,updated_at", "order": "added_at.desc"},
    )
    if status >= 400 or not isinstance(body, list):
        raise HTTPException(status_code=502, detail="Không tải được danh mục tài khoản.")
    return body


def sync_watchlist(user: AuthUser, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status, body = _postgrest(
        "POST", "rpc/sync_watchlist", token=user.access_token,
        payload={"items": items}, prefer="return=representation",
    )
    if status >= 400:
        raise HTTPException(status_code=502, detail="Không đồng bộ được danh mục tài khoản.")
    return body if isinstance(body, list) else list_watchlist(user)
