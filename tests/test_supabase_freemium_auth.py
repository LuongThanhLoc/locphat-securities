from pathlib import Path

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as application
import supabase_auth


@pytest.fixture()
def client(monkeypatch):
    for name in (
        "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY",
        "TURNSTILE_SITE_KEY", "RENDER", "RENDER_EXTERNAL_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return TestClient(application.app)


def test_public_pages_and_auth_status_are_available(client):
    assert client.get("/").status_code == 200
    assert client.get("/heatmap").status_code == 200
    assert client.get("/api/auth/me").json() == {
        "authenticated": False, "user": None, "csrf_token": None,
    }


@pytest.mark.parametrize("path", [
    "/bubbles", "/calendar", "/watchlist", "/backtest", "/rrg", "/stock/FPT",
    "/static/bubbles.html", "/static/calendar.html", "/static/watchlist.html",
    "/static/backtest.html", "/static/rrg.html",
])
def test_guest_cannot_bypass_premium_html(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth?next=")


@pytest.mark.parametrize("path", [
    "/api/all_stocks", "/api/search_suggest?q=FPT", "/api/stocks",
    "/api/analyze/FPT", "/api/quant/FPT", "/api/ai_news/FPT",
    "/api/watchlist", "/api/watchlist/quotes?symbols=FPT", "/api/rrg/data",
    "/api/heatmap/ai_insight", "/api/backtest/rsi/FPT",
])
def test_guest_premium_api_fails_closed(client, path):
    assert client.get(path).status_code == 401


def test_guest_weekly_ai_fails_closed(client):
    assert client.post("/api/heatmap/weekly_analysis").status_code == 401


@pytest.mark.parametrize("value, expected", [
    ("/stock/FPT", "/stock/FPT"),
    ("https://evil.example", "/"),
    ("//evil.example", "/"),
    ("/\\evil", "/"),
])
def test_next_url_is_same_site_only(value, expected):
    assert application._safe_next(value) == expected


@pytest.mark.parametrize("username", ["ab", "admin", "tên-có-dấu", "has space", ".leading"])
def test_invalid_or_reserved_username_is_rejected(username):
    with pytest.raises(HTTPException) as exc:
        supabase_auth.normalize_username(username)
    assert exc.value.status_code == 422


def test_password_confirmation_and_minimum_length():
    with pytest.raises(HTTPException):
        supabase_auth.validate_password("0123456789", "different1")
    with pytest.raises(HTTPException):
        supabase_auth.validate_password("short", "short")
    supabase_auth.validate_password("correct-horse", "correct-horse")


def test_opaque_server_secret_is_never_sent_as_bearer(monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_example")
    headers = supabase_auth._auth_headers(service=True)
    assert headers["apikey"] == "sb_secret_example"
    assert "Authorization" not in headers


def test_registration_rate_limit_fails_before_calling_supabase(monkeypatch):
    monkeypatch.setattr(supabase_auth, "_attempt_count", lambda *args, **kwargs: 5)
    with pytest.raises(HTTPException) as exc:
        supabase_auth.signup(object(), "investor", "investor@example.com", "correct-horse", "correct-horse", "captcha")
    assert exc.value.status_code == 429


def test_csrf_is_required_for_mutations():
    user = supabase_auth.AuthUser("id", "i@example.com", "investor", "access", "expected")
    class Request:
        headers = {"x-csrf-token": "wrong"}
    with pytest.raises(HTTPException) as exc:
        supabase_auth.require_csrf(Request(), user)
    assert exc.value.status_code == 403


def test_admin_role_only_comes_from_trusted_app_metadata():
    claims = {
        "sub": "user-1",
        "email": "admin@example.com",
        "user_metadata": {"username": "thanhloc", "role": "admin"},
        "app_metadata": {"role": "admin"},
    }
    assert supabase_auth.user_from_claims(claims, "access", "csrf").role == "admin"
    claims["app_metadata"] = {}
    assert supabase_auth.user_from_claims(claims, "access", "csrf").role == "user"


def test_require_admin_rejects_regular_user(monkeypatch):
    regular = supabase_auth.AuthUser("id", "u@example.com", "investor", "access", "csrf")
    monkeypatch.setattr(supabase_auth, "authenticate_request", lambda request: regular)
    with pytest.raises(HTTPException) as exc:
        supabase_auth.require_admin(object())
    assert exc.value.status_code == 403


def test_admin_status_endpoint_requires_admin(monkeypatch):
    regular = supabase_auth.AuthUser("id", "u@example.com", "investor", "access", "csrf")
    monkeypatch.setattr(supabase_auth, "authenticate_request", lambda request: regular)
    with TestClient(application.app) as client:
        assert client.get("/api/auth/admin").status_code == 403


def test_jwt_verifies_signature_issuer_audience_and_expiry(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    class Key:
        key = public_key
    class Client:
        def get_signing_key_from_jwt(self, token):
            return Key()
    monkeypatch.setattr(supabase_auth, "_get_jwks_client", lambda: Client())
    claims = {
        "sub": "user-1", "aud": "authenticated",
        "iss": "https://project.supabase.co/auth/v1", "exp": 4_102_444_800,
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})
    assert supabase_auth.decode_access_token(token)["sub"] == "user-1"
    bad = jwt.encode({**claims, "iss": "https://evil.example"}, private_key, algorithm="RS256", headers={"kid": "test"})
    with pytest.raises(jwt.InvalidIssuerError):
        supabase_auth.decode_access_token(bad)


def test_frontend_contract_blocks_search_and_includes_responsive_dialog():
    root = Path(__file__).resolve().parents[1]
    nav = (root / "static/site-nav.js").read_text()
    auth = (root / "static/auth.js").read_text()
    css = (root / "static/auth.css").read_text()
    assert "LPAuth?.isAuthenticated()" in nav
    assert nav.index("LPAuth?.isAuthenticated()") < nav.index("fetch(`/api/search_suggest")
    assert "onSuccess: openAiReport" in (root / "static/heatmap.js").read_text()
    assert "position:fixed" in css and "100dvh" in css and "safe-area-inset" in css
    assert "e.key==='Escape'" in auth and "function trap" in auth


def test_register_sets_httponly_session_cookies(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "publishable")
    session = {
        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
        "user": {"id": "user-1", "email": "investor@example.com", "user_metadata": {"username": "investor"}},
    }
    monkeypatch.setattr(supabase_auth, "signup", lambda *args, **kwargs: session)
    with TestClient(application.app) as client:
        response = client.post("/api/auth/register", json={
            "username": "investor", "email": "investor@example.com",
            "password": "correct-horse", "password_confirmation": "correct-horse",
            "turnstile_token": "test-token",
        })
    assert response.status_code == 201
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 3
    assert all("HttpOnly" in cookie for cookie in cookies)
    assert all("SameSite=lax" in cookie for cookie in cookies)
    assert not any("access" in response.text or "refresh" in response.text for _ in [0])
