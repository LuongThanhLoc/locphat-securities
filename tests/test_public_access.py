import pytest
from fastapi.testclient import TestClient
import app as application
from base64 import b64decode
from pathlib import Path


@pytest.fixture()
def client():
    return TestClient(application.app)


@pytest.mark.parametrize("path", [
    "/",
    "/heatmap",
    "/bubbles",
    "/calendar",
    "/macro",
    "/economic-calendar",
    "/watchlist",
    "/chi-bao-day",
    "/backtest",
    "/rrg",
    "/stock/FPT",
    "/static/bubbles.html",
    "/static/calendar.html",
    "/static/watchlist.html",
    "/static/bottom-indicator.html",
    "/static/backtest.html",
    "/static/rrg.html",
    "/static/macro.html",
    "/static/heatmap.html",
    "/static/index.html",
])
def test_all_pages_are_publicly_accessible_without_redirect(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200, f"Path {path} returned status {response.status_code}"


@pytest.mark.parametrize("path", [
    "/api/all_stocks",
    "/api/search_suggest?q=FPT",
    "/api/stocks",
])
def test_public_apis_return_200_for_guests(client, path):
    response = client.get(path)
    assert response.status_code == 200, f"API {path} returned status {response.status_code}"


@pytest.mark.parametrize("path", ["/api/macro-tickers", "/api/market-ribbon"])
def test_data_dependent_public_apis_never_require_an_account(client, path):
    response = client.get(path)
    assert response.status_code not in {401, 403}


@pytest.mark.parametrize("method,path", [
    ("get", "/auth"),
    ("get", "/api/watchlist"),
    ("post", "/api/watchlist/sync"),
])
def test_removed_account_routes_return_404(client, method, path):
    response = getattr(client, method)(path, follow_redirects=False)
    assert response.status_code == 404


def test_removed_integrations_do_not_reappear_in_runtime_files():
    root = Path(__file__).resolve().parents[1]
    encoded_terms = (
        "c3VwYWJhc2U=", "c3VwZXJiYXNl", "Y2xvdWRmbGFyZQ==", "dHVybnN0aWxl",
        "bHBhdXRo", "ZnJlZW1pdW1fdjE=", "YXV0aF9yYXRlX2xpbWl0X3NlY3JldA==",
    )
    forbidden = tuple(b64decode(term).decode("ascii") for term in encoded_terms)
    candidates = [root / "app.py", root / "requirements.txt", root / "render.yaml"]
    candidates.extend(path for path in (root / "static").rglob("*") if path.is_file())
    if (root / ".env").exists():
        candidates.append(root / ".env")

    violations = []
    for path in candidates:
        if path.suffix in {".woff", ".woff2", ".png", ".jpg", ".ico"}:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in forbidden:
            if term in content:
                violations.append(f"{path.relative_to(root)}: {term}")

    assert not violations, "Legacy integration references remain: " + ", ".join(violations)


@pytest.mark.parametrize("page", ["index.html", "rrg.html", "backtest.html"])
def test_icon_assets_are_self_hosted(page):
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / page).read_text(encoding="utf-8")
    assert '/static/vendor/fontawesome/css/fontawesome.min.css' in html
    assert '/static/vendor/fontawesome/css/solid.min.css' in html
    assert (root / "static/vendor/fontawesome/webfonts/fa-solid-900.woff2").is_file()


def test_watchlist_remains_browser_local_and_uses_only_quote_api():
    root = Path(__file__).resolve().parents[1]
    script = (root / "static/watchlist.js").read_text(encoding="utf-8")
    assert "localStorage.getItem(WATCHLIST_KEY)" in script
    assert "localStorage.setItem(WATCHLIST_KEY" in script
    assert "/api/watchlist/quotes?symbols=" in script
    assert "/api/" + "watchlist/sync" not in script
