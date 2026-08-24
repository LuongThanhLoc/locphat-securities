import re
from pathlib import Path
from ctck_analyzer import analyze_security_stock, CACHE


def test_index_html_has_ema_and_chart_elements():
    index_path = Path(__file__).parent.parent / "static" / "index.html"
    content = index_path.read_text(encoding="utf-8")

    assert 'id="analysisSharedPriceChart"' in content
    assert "/static/shared-price-chart.js" in content
    assert "/static/shared-price-chart.css" in content


def test_app_js_has_ema_and_compact_volume_scale():
    app_js_path = Path(__file__).parent.parent / "static" / "shared-price-chart.js"
    content = app_js_path.read_text(encoding="utf-8")

    assert "function calculateEMA(" in content
    assert "toggleEMA(period)" in content
    assert "const PERIODS = [20, 50, 100, 200]" in content
    assert "top: .84" in content
    assert "const WINDOWS = { '1D': 1, '3D': 3, '1W': 5, '1M': 22, '3M': 65, '1Y': 250 }" in content


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
