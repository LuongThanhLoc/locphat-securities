from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_macro_ui_exposes_verification_instead_of_forecast():
    html = (ROOT / "static/macro.html").read_text(encoding="utf-8")
    script = (ROOT / "static/macro.js").read_text(encoding="utf-8")
    assert "DỰ BÁO" not in html
    assert 'id="dialogVerificationVal"' in html
    assert "Nguồn chính thức" in script
    assert "Nguồn tổng hợp" in script
    assert "N/A" in script


def test_market_ribbon_is_continuous_safe_and_session_aware():
    html = (ROOT / "static/macro.html").read_text(encoding="utf-8")
    css = (ROOT / "static/market-ribbon.css").read_text(encoding="utf-8")
    script = (ROOT / "static/market-ribbon.js").read_text(encoding="utf-8")
    assert "/api/market-ribbon" in script
    assert "items.length !== 32" in script
    assert "payload?.membership?.count" in script
    assert "replaceChildren" in script and "textContent" in script
    assert "new AbortController()" in script and "visibilitychange" in script
    assert "refresh_after_seconds" in script
    assert "ticker-group" in html or "ticker-group" in script
    assert "market-ribbon-marquee" in css and "translate3d(-50%" in css
    assert "animation-play-state: paused" in css
    assert "prefers-reduced-motion: reduce" in css


def test_home_and_macro_share_the_same_market_ribbon_runtime():
    home = (ROOT / "static/index.html").read_text(encoding="utf-8")
    macro = (ROOT / "static/macro.html").read_text(encoding="utf-8")
    shared_script = (ROOT / "static/market-ribbon.js").read_text(encoding="utf-8")
    shared_css = (ROOT / "static/market-ribbon.css").read_text(encoding="utf-8")
    for html in (home, macro):
        assert "data-market-ribbon" in html
        assert "/static/market-ribbon.css?v=20260814_v1" in html
        assert "/static/market-ribbon.js?v=20260814_v1" in html
    assert "/api/market-ribbon" in shared_script
    assert "items.length !== 32" in shared_script
    assert "width / 28" in shared_script
    assert "visibilitychange" in shared_script
    assert "textContent" in shared_script and "replaceChildren" in shared_script
    assert "market-ribbon-marquee" in shared_css
    assert "prefers-reduced-motion: reduce" in shared_css


def test_macro_ui_cancels_stale_requests_and_escapes_source_data():
    script = (ROOT / "static/macro.js").read_text(encoding="utf-8")
    assert "new AbortController()" in script
    assert "state.controller?.abort()" in script
    assert "requestId !== state.requestId" in script
    assert ".replaceAll('&', '&amp;')" in script
    assert "esc(event.title_vi || event.title)" in script
    assert "textContent = event.overview_vi" in script


def test_macro_dialog_is_above_navigation_and_mobile_safe():
    html = (ROOT / "static/macro.html").read_text(encoding="utf-8")
    css = (ROOT / "static/macro.css").read_text(encoding="utf-8")
    assert html.index("/static/auth.js") < html.index("/static/site-nav.js")
    assert "z-index: 100000" in css
    assert "100dvh" in css
    assert "100vi" in css
    assert "env(safe-area-inset-top)" in css
    assert "env(safe-area-inset-bottom)" in css


def test_macro_refresh_is_authenticated_non_blocking_api():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    script = (ROOT / "static/macro.js").read_text(encoding="utf-8")
    assert '@app.post("/api/macro-refresh", status_code=202)' in app
    assert "Depends(require_user)" in app
    assert "window.LPAuth.api('/api/macro-refresh', {method: 'POST'})" in script
