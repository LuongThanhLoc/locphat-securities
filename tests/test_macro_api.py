from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as application
import macro_calendar_engine
import market_ribbon_service
from supabase_auth import AuthUser, require_user


TEST_USER = AuthUser(
    id="macro-user", email="macro@example.test", username="macro_user",
    access_token="test-token", csrf_token="macro-csrf", role="user",
)


def test_macro_api_v2_contract_and_async_refresh(monkeypatch):
    calendar = {
        "schema_version": 2, "success": True, "events": [], "counts": {},
        "coverage": {}, "data_quality": {"no_synthetic_data": True},
    }
    tickers = {"schema_version": 1, "items": [], "membership": {"count": 30}}
    monkeypatch.setattr(macro_calendar_engine, "get_macro_calendar", lambda **kwargs: calendar)
    monkeypatch.setattr(market_ribbon_service, "get_market_ribbon", lambda: tickers)
    monkeypatch.setattr(macro_calendar_engine, "request_macro_refresh", lambda: {"state": "queued"})
    application.app.dependency_overrides[require_user] = lambda: TEST_USER
    try:
        with TestClient(application.app) as client:
            response = client.get("/api/macro-calendar?country=USD")
            assert response.status_code == 200
            assert response.json()["schema_version"] == 2
            assert response.json()["data_quality"]["no_synthetic_data"] is True

            ticker_response = client.get("/api/macro-tickers")
            assert ticker_response.status_code == 200
            assert ticker_response.json() == tickers
            assert ticker_response.headers["deprecation"] == "true"

            ribbon_response = client.get("/api/market-ribbon")
            assert ribbon_response.status_code == 200
            assert ribbon_response.json() == tickers

            refresh = client.post("/api/macro-refresh", headers={"X-CSRF-Token": "macro-csrf"})
            assert refresh.status_code == 202
            assert refresh.json()["refresh"]["state"] == "queued"
    finally:
        application.app.dependency_overrides.clear()


def test_macro_api_maps_validation_to_400(monkeypatch):
    def invalid(**kwargs):
        raise ValueError("Khoảng lịch phải từ 0 đến 93 ngày.")

    monkeypatch.setattr(macro_calendar_engine, "get_macro_calendar", invalid)
    application.app.dependency_overrides[require_user] = lambda: TEST_USER
    try:
        with TestClient(application.app) as client:
            response = client.get("/api/macro-calendar?start_date=2026-01-01&end_date=2026-12-31")
            assert response.status_code == 400
            assert "93 ngày" in response.json()["detail"]
    finally:
        application.app.dependency_overrides.clear()


def test_macro_refresh_requires_matching_csrf():
    application.app.dependency_overrides[require_user] = lambda: TEST_USER
    try:
        with TestClient(application.app) as client:
            assert client.post("/api/macro-refresh").status_code == 403
    finally:
        application.app.dependency_overrides.clear()
