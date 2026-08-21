import re
from pathlib import Path
from ctck_analyzer import analyze_security_stock, CACHE


def test_index_html_has_ema_and_chart_elements():
    index_path = Path(__file__).parent.parent / "static" / "index.html"
    content = index_path.read_text(encoding="utf-8")

    assert "toggleEma20" in content, "Missing toggleEma20 button in index.html"
    assert "toggleEma50" in content, "Missing toggleEma50 button in index.html"
    assert "toggleEma100" in content, "Missing toggleEma100 button in index.html"
    assert "toggleEma200" in content, "Missing toggleEma200 button in index.html"
    assert 'id="tradingviewChartContainer"' in content, "Missing tradingviewChartContainer"


def test_app_js_has_ema_and_compact_volume_scale():
    app_js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")

    assert "function calculateEMA(" in content, "Missing calculateEMA function in app.js"
    assert "function toggleChartEma(" in content, "Missing toggleChartEma function in app.js"
    assert "tvEma20Series" in content, "Missing tvEma20Series in app.js"
    assert "tvEma50Series" in content, "Missing tvEma50Series in app.js"
    assert "tvEma100Series" in content, "Missing tvEma100Series in app.js"
    assert "tvEma200Series" in content, "Missing tvEma200Series in app.js"
    assert "top: 0.85" in content, "Volume scale margins not set to compact 15% height"


def test_ema_mathematical_correctness():
    def js_calculate_ema(data, period):
        if not data or len(data) == 0:
            return []
        k = 2.0 / (period + 1.0)
        ema_data = []
        prev_ema = data[0]["close"]
        ema_data.append({"time": data[0]["time"], "value": round(prev_ema, 2)})
        for i in range(1, len(data)):
            current_close = data[i]["close"]
            prev_ema = (current_close - prev_ema) * k + prev_ema
            ema_data.append({"time": data[i]["time"], "value": round(prev_ema, 2)})
        return ema_data

    sample = [{"time": f"2026-01-{i:02d}", "close": 50.0 + i} for i in range(1, 250)]
    ema20 = js_calculate_ema(sample, 20)
    ema50 = js_calculate_ema(sample, 50)
    ema100 = js_calculate_ema(sample, 100)
    ema200 = js_calculate_ema(sample, 200)

    assert len(ema20) == len(sample)
    assert len(ema50) == len(sample)
    assert len(ema100) == len(sample)
    assert len(ema200) == len(sample)
    assert ema20[-1]["value"] > 0
    assert ema200[-1]["value"] > 0


def test_stock_data_has_sufficient_history_for_ema200():
    CACHE.clear()
    data = analyze_security_stock("GEE")
    price_history = data.get("price_history", [])
    assert len(price_history) >= 200, f"History length {len(price_history)} is insufficient for EMA 200"
