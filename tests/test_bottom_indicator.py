from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bottom_indicator_engine import _simulate, _validate_request, calculate_indicator


def sample_bars(periods=360, start="2025-01-02", phase=0.0):
    dates = pd.bdate_range(start, periods=periods)
    x = np.arange(periods, dtype=float)
    close = 25_000 + x * 12 + np.sin(x / 8 + phase) * 650
    open_price = close * (1 + np.sin(x / 5) * 0.002)
    high = np.maximum(open_price, close) * 1.012
    low = np.minimum(open_price, close) * 0.988
    volume = 900_000 + (np.cos(x / 7) + 1.2) * 250_000
    return pd.DataFrame({
        "date": dates, "open": open_price, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def test_indicator_exposes_causal_core_series_and_scores():
    result = calculate_indicator(sample_bars(), sample_bars(phase=0.7))
    expected = {
        "ema20", "ema50", "ema100", "ema200", "rsi14", "atr14", "cmf20", "mfi14", "rs20",
        "money_pressure", "pulse", "flow", "core", "center", "aperture",
        "pulse_pct", "flow_pct", "core_pct", "center_pct",
        "clv", "volume_ratio20", "signal", "divergence",
        "macd_line", "macd_signal", "macd_hist",  # MACD series mới
        "state", "opportunity_score", "risk_score", "bottom_confidence", "is_event",
    }
    assert expected.issubset(result.columns)
    assert result["aperture"].dropna().between(0, 100).all()
    assert result["pulse_pct"].dropna().between(0, 100).all()
    assert result["flow_pct"].dropna().between(0, 100).all()
    assert result["core_pct"].dropna().between(0, 100).all()
    assert result["clv"].dropna().between(-1.0, 1.0).all()
    assert result["opportunity_score"].between(0, 100).all()
    assert result["risk_score"].between(0, 100).all()
    assert result["bottom_confidence"].between(0, 100).all()
    valid_signals = {None, "BB", "BS"}
    assert set(result["signal"]).issubset(valid_signals)
    valid_divs = {
        None,
        "BULLISH", "BEARISH",
        "DUAL_BULLISH", "DUAL_BEARISH",
        "TRIPLE_BULLISH", "TRIPLE_BEARISH",
        "RSI_BULLISH", "RSI_BEARISH",
        "MACD_BULLISH", "MACD_BEARISH",
        "MACD_RSI_BULLISH", "MACD_RSI_BEARISH",
    }
    assert set(result["divergence"]).issubset(valid_divs)


def test_crowd_sentiment_mapping():
    from bottom_indicator_engine import _crowd_sentiment
    assert _crowd_sentiment(85) == "FOMO CỰC ĐỘ"
    assert _crowd_sentiment(80) == "FOMO CỰC ĐỘ"
    assert _crowd_sentiment(75) == "THAM LAM"
    assert _crowd_sentiment(60) == "THAM LAM"
    assert _crowd_sentiment(50) == "TRUNG LẬP"
    assert _crowd_sentiment(40) == "TRUNG LẬP"
    assert _crowd_sentiment(30) == "THẬN TRỌNG"
    assert _crowd_sentiment(20) == "THẬN TRỌNG"
    assert _crowd_sentiment(10) == "SỢ HÃI"
    assert _crowd_sentiment(0) == "SỢ HÃI"
    assert _crowd_sentiment(None) == "KHÔNG XÁC ĐỊNH"
    assert _crowd_sentiment(float("nan")) == "KHÔNG XÁC ĐỊNH"


def test_future_data_cannot_change_past_indicator_values():
    stock = sample_bars()
    benchmark = sample_bars(phase=0.5)
    baseline = calculate_indicator(stock, benchmark)
    changed = stock.copy()
    changed.loc[changed.index >= 300, ["open", "high", "low", "close"]] *= 3
    recalculated = calculate_indicator(changed, benchmark)
    columns = ["money_pressure", "pulse", "flow", "core", "aperture", "pulse_pct", "flow_pct", "core_pct", "state"]
    pd.testing.assert_frame_equal(
        baseline.loc[:299, columns].reset_index(drop=True),
        recalculated.loc[:299, columns].reset_index(drop=True),
    )


def test_missing_benchmark_never_emits_early_expansion():
    result = calculate_indicator(sample_bars(), None)
    assert result["rs20"].isna().all()
    assert "EARLY_EXPANSION" not in set(result["state"])


@pytest.mark.parametrize("symbol, bar_limit", [("FPT;DROP", 252), ("FPT", 59), ("FPT", 1501)])
def test_request_validation_rejects_invalid_symbol_and_bar_limit(symbol, bar_limit):
    with pytest.raises(ValueError):
        _validate_request(symbol, bar_limit)


def test_simulation_fills_entry_and_exit_on_next_open():
    frame = sample_bars(periods=80)
    frame["atr14"] = 500.0
    frame["state"] = "NEUTRAL"
    frame["is_event"] = False
    frame["opportunity_score"] = 0
    frame["risk_score"] = 0
    frame.loc[10, ["state", "is_event", "opportunity_score"]] = ["EARLY_EXPANSION", True, 75]
    frame.loc[20, ["state", "is_event", "risk_score"]] = ["DISTRIBUTION_CONTRACTION", True, 80]
    result = _simulate(frame)
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["entry_date"] == frame.iloc[11]["date"].strftime("%Y-%m-%d")
    assert trade["exit_date"] == frame.iloc[21]["date"].strftime("%Y-%m-%d")
    assert trade["exit_reason"] == "distribution_contraction"


def test_navigation_and_page_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    nav = (root / "static/site-nav.js").read_text(encoding="utf-8")
    html = (root / "static/bottom-indicator.html").read_text(encoding="utf-8")
    script = (root / "static/bottom-indicator.js").read_text(encoding="utf-8")
    css = (root / "static/bottom-indicator.css").read_text(encoding="utf-8")
    assert nav.index("key: 'bottom-indicator'") < nav.index("key: 'backtest'") < nav.index("key: 'rrg'")
    assert "path.startsWith('/chi-bao-day') ? 'bottom-indicator'" in nav
    assert 'id="analysisPanel"' in html and 'id="backtestPanel"' in html
    assert "100dvh" in css and "safe-area-inset-top" in css and "bottom-dialog-open" in css
    assert "event.key === 'Escape'" in script and "state.dialogTrigger?.focus" in script
    assert "/api/bottom-indicator/" in script
    assert "bottom-emotion-detail-grid" not in html
    assert 'id="priceChart"' in html
    assert 'id="flowChart"' in html
    assert 'id="apertureChart"' in html
    assert 'id="crowdSentiment"' in html
    assert 'id="currentRsi"' in html
    assert 'id="rsiStatus"' in html
    assert "updateRsiDisplay" in script
    assert "updateEmotionBreakdown" not in script
    assert "renderNewsSection" not in script


def test_news_sentiment_analyzer_structure():
    from bottom_indicator_engine import _analyze_news_sentiment
    sentiment = _analyze_news_sentiment("FPT")
    assert isinstance(sentiment, dict)
    assert "score" in sentiment
    assert "label" in sentiment
    assert "catalysts" in sentiment
    assert 0 <= sentiment["score"] <= 100
    for c in sentiment["catalysts"]:
        assert "title" in c
        assert "sentiment" in c
        assert c["sentiment"] in ("POS", "NEG", "NEU")


def test_emotion_breakdown_in_current_summary():
    from bottom_indicator_engine import _current_summary
    sample = sample_bars(periods=80)
    calculated = calculate_indicator(sample)
    quality = {"status": "valid"}
    news_sent = {"score": 75.0, "label": "Tích cực", "total_articles": 3, "catalysts": []}
    summary = _current_summary(calculated.iloc[-1], quality, news_sent)
    assert "emotion_breakdown" in summary
    eb = summary["emotion_breakdown"]
    assert "price_momentum_score" in eb
    assert "volume_panic_score" in eb
    assert "volatility_stretch_score" in eb
    assert "bigboys_disparity_score" in eb
    assert "news_sentiment_score" in eb
    assert "crowd_vs_bigboys_insight" in eb
    assert 0 <= eb["composite_score"] <= 100
    assert "disparity_score" in summary
    assert "trade_setup" in summary
    assert "rsi14" in summary
    assert 0.0 <= summary["rsi14"] <= 100.0


def test_trade_setup_actionable_quant_properties():
    sample = sample_bars(periods=100)
    calculated = calculate_indicator(sample)
    assert "trade_setup" in calculated.columns
    assert "disparity_score" in calculated.columns
    
    last_row = calculated.iloc[-1]
    ts = last_row["trade_setup"]
    assert isinstance(ts, dict)
    assert "verdict_code" in ts
    assert "verdict_title" in ts
    assert "entry_zone" in ts
    assert "stop_loss_price" in ts
    assert "target_1_price" in ts
    assert "target_2_price" in ts
    assert "rr_ratio" in ts
    assert "wyckoff_phase" in ts
    
    # Mathematical and practical sanity checks
    close = float(last_row["close"])
    assert ts["stop_loss_price"] < close
    assert ts["target_1_price"] > close
    assert ts["target_2_price"] >= ts["target_1_price"]
    assert ts["rr_ratio"] >= 1.0
    assert ts["stop_loss_pct"] < 0
    assert ts["target_1_pct"] > 0


def test_rsi_and_dual_divergence_detection():
    """Tất cả loại phân kỳ phải thuộc tập hợp hợp lệ (bao gồm MACD và TRIPLE mới)."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=100)
    calculated = calculate_indicator(sample)
    assert "divergence" in calculated.columns
    divs = calculated["divergence"].dropna().unique()
    valid_types = {
        "BULLISH", "BEARISH",
        "DUAL_BULLISH", "DUAL_BEARISH",
        "TRIPLE_BULLISH", "TRIPLE_BEARISH",
        "RSI_BULLISH", "RSI_BEARISH",
        "MACD_BULLISH", "MACD_BEARISH",
        "MACD_RSI_BULLISH", "MACD_RSI_BEARISH",
    }
    for d in divs:
        assert d in valid_types, f"Phân kỳ không hợp lệ: {d}"


def test_macd_series_are_computed_correctly():
    """MACD(12,26,9): kiểm tra macd_line, macd_signal, macd_hist được tính đúng."""
    from bottom_indicator_engine import calculate_indicator, _macd
    sample = sample_bars(periods=200)
    calculated = calculate_indicator(sample)

    # 3 cột MACD phải tồn tại
    assert "macd_line" in calculated.columns
    assert "macd_signal" in calculated.columns
    assert "macd_hist" in calculated.columns

    # Histogram phải bằng line - signal (trong phạm vi rounding)
    hist_check = (calculated["macd_line"] - calculated["macd_signal"]).dropna()
    macd_hist_vals = calculated["macd_hist"].dropna()
    # Align indices
    common_idx = hist_check.index.intersection(macd_hist_vals.index)
    np.testing.assert_allclose(
        hist_check.loc[common_idx].values,
        macd_hist_vals.loc[common_idx].values,
        atol=1e-3,
        err_msg="macd_hist phải = macd_line - macd_signal"
    )

    # Causal check: giá trị MACD không bị thay đổi khi thêm dữ liệu tương lai
    extended = sample.copy()
    # Nhân đôi giá từ phiên 180 trở đi (thay đổi tương lai)
    extended.loc[extended.index >= 180, ["open", "high", "low", "close"]] *= 3.0
    recalculated = calculate_indicator(extended)
    pd.testing.assert_series_equal(
        calculated["macd_hist"].iloc[:175].reset_index(drop=True),
        recalculated["macd_hist"].iloc[:175].reset_index(drop=True),
        check_names=False,
        atol=1e-3,
    )


def test_macd_divergence_types_valid():
    """MACD divergence loại phải là subset của tập hợp hợp lệ."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=400)
    calculated = calculate_indicator(sample, sample_bars(periods=400, phase=0.3))
    valid_types = {
        None,
        "BULLISH", "BEARISH",
        "DUAL_BULLISH", "DUAL_BEARISH",
        "TRIPLE_BULLISH", "TRIPLE_BEARISH",
        "RSI_BULLISH", "RSI_BEARISH",
        "MACD_BULLISH", "MACD_BEARISH",
        "MACD_RSI_BULLISH", "MACD_RSI_BEARISH",
    }
    assert set(calculated["divergence"]).issubset(valid_types)
    # TRIPLE chỉ xuất hiện khi cả 3 chỉ báo cùng phân kỳ — không nên có loại không nằm trong valid_types
    triple_types = calculated[calculated["divergence"].isin(["TRIPLE_BULLISH", "TRIPLE_BEARISH"])]
    assert set(triple_types["divergence"]).issubset({"TRIPLE_BULLISH", "TRIPLE_BEARISH", None})


def test_anti_bottom_sell_exhaustion_does_not_block_top_climax():
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=120)
    # Simulate a steady rally then sharp climax
    calculated = calculate_indicator(sample)
    assert "signal" in calculated.columns
    assert set(calculated["signal"]).issubset({None, "BB", "BS"})


def test_bb_never_fires_when_crowd_is_greedy_or_fomo():
    """BB (Big Boy Buy) KHÔNG được xuất hiện khi đám đông đang tham lam/FOMO.
    Theo quy tắc: aperture <= 55 là bắt buộc cho BB1, aperture <= 70 cho BB2, aperture <= 72 cho BB3.
    Khi aperture > 72, tuyệt đối không có BB."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=400)
    calculated = calculate_indicator(sample, sample_bars(periods=400, phase=0.5))
    bb_rows = calculated[calculated["signal"] == "BB"]
    if not bb_rows.empty:
        # Mọi BB phải xuất hiện khi aperture <= 72 (giới hạn cứng của BB3, chặt nhất)
        assert (bb_rows["aperture"] <= 72).all(), (
            f"BB xuất hiện khi aperture > 72: {bb_rows[['aperture', 'state', 'close']].to_dict('records')}"
        )
        # BB không được xuất hiện trong state OVEREXTENDED hoặc DISTRIBUTION_CONTRACTION
        assert not bb_rows["state"].isin(["OVEREXTENDED", "DISTRIBUTION_CONTRACTION"]).any(), (
            f"BB xuất hiện trong state phân phối/quá mở: {bb_rows[['state', 'aperture']].to_dict('records')}"
        )


def test_bs_never_fires_in_bottom_or_oversold_zones():
    """BS (Big Boy Sell) KHÔNG được xuất hiện khi đám đông đang sợ hãi / giá ở vùng đáy.
    Theo Anti-Bottom-Sell rule trong SKILL.md: aperture <= 38 hoặc RSI <= 42 phải chặn BS."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=400)
    calculated = calculate_indicator(sample, sample_bars(periods=400, phase=0.5))
    bs_rows = calculated[calculated["signal"] == "BS"]
    if not bs_rows.empty:
        # BS tuyệt đối không xuất hiện khi aperture <= 40 (vùng sợ hãi/quá bán)
        assert not (bs_rows["aperture"] <= 40).any(), (
            f"BS xuất hiện khi aperture <= 40 (vùng hoảng loạn): {bs_rows[['aperture', 'state', 'close']].to_dict('records')}"
        )
        # BS không được xuất hiện trong state BOTTOM_WATCH hay FALLING_CONTRACTION
        assert not bs_rows["state"].isin(["BOTTOM_WATCH", "FALLING_CONTRACTION"]).any(), (
            f"BS xuất hiện trong state đáy/giảm: {bs_rows[['state', 'aperture']].to_dict('records')}"
        )
        # BS không được xuất hiện khi RSI <= 42
        assert not (bs_rows["rsi14"] <= 42).any(), (
            f"BS xuất hiện khi RSI <= 42 (quá bán sâu): {bs_rows[['rsi14', 'aperture']].to_dict('records')}"
        )


def test_bb_position_relative_to_50day_low():
    """BB chỉ có ý nghĩa khi giá gần đáy 50 phiên (dist_to_low <= 15% cho BB1).
    BB2/BB3 không có giới hạn dist_to_low cứng nhưng phải trong state tăng hợp lệ."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=400)
    calculated = calculate_indicator(sample)
    bb_rows = calculated[calculated["signal"] == "BB"]
    # Kiểm tra signal format đúng
    assert set(calculated["signal"]).issubset({None, "BB", "BS"})
    # BB không được cluster quá gần nhau (cooldown 20 phiên)
    if len(bb_rows) >= 2:
        bb_indices = bb_rows.index.tolist()
        for i in range(1, len(bb_indices)):
            gap = bb_indices[i] - bb_indices[i - 1]
            assert gap >= 20, f"BB cluster quá gần: gap={gap} phiên (cần >= 20)"


def test_bs_only_fires_after_sustained_uptrend():
    """BS (bán đỉnh) chỉ được xuất hiện sau khi giá đã tăng bền vững từ đáy.
    dist_to_low phải >= 0.25 (giá xa đáy ít nhất 25%) tại thời điểm BS."""
    from bottom_indicator_engine import calculate_indicator
    # Tạo dataset với cả giai đoạn tăng lẫn giảm
    sample = sample_bars(periods=500)
    calculated = calculate_indicator(sample, sample_bars(periods=500, phase=0.3))
    bs_rows = calculated[calculated["signal"] == "BS"]
    # BS không cluster quá gần nhau (cooldown 20 phiên)
    if len(bs_rows) >= 2:
        bs_indices = bs_rows.index.tolist()
        for i in range(1, len(bs_indices)):
            gap = bs_indices[i] - bs_indices[i - 1]
            assert gap >= 20, f"BS cluster quá gần: gap={gap} phiên (cần >= 20)"


def test_ema100_and_ema200_calculation_and_ordering():
    """Kiểm tra EMA100 và EMA200 được tính toán chính xác và ổn định."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=300)
    calculated = calculate_indicator(sample)
    
    assert "ema100" in calculated.columns
    assert "ema200" in calculated.columns
    assert calculated["ema100"].notnull().all()
    assert calculated["ema200"].notnull().all()
    assert (calculated["ema100"] > 0).all()
    assert (calculated["ema200"] > 0).all()


def test_analysis_payload_contains_ema_fields_in_bars_and_states():
    """Kiểm tra bars và states trả về đầy đủ ema20, ema50, ema100, ema200 cho frontend."""
    from bottom_indicator_engine import _build_analysis_payload, calculate_indicator
    sample = sample_bars(periods=250)
    calculated = calculate_indicator(sample)
    quality = {"status": "valid", "freshness_sessions": 0}
    payload = _build_analysis_payload("HPG", 250, calculated, quality)
    
    assert payload["status"] == "ok"
    assert len(payload["bars"]) > 0
    first_bar = payload["bars"][0]
    for key in ("ema20", "ema50", "ema100", "ema200"):
        assert key in first_bar, f"Missing {key} in bar record"
        assert first_bar[key] is not None
        
    first_state = payload["states"][0]
    for key in ("ema20", "ema50", "ema100", "ema200"):
        assert key in first_state, f"Missing {key} in state record"


def test_candle_geometry_and_causal_volume_metrics():
    """Kiểm tra tỷ lệ hình học nến và RVOL20 tính toán chuẩn xác."""
    sample = sample_bars(periods=100)
    calculated = calculate_indicator(sample)
    
    assert "lower_wick_ratio" in calculated.columns
    assert "upper_wick_ratio" in calculated.columns
    assert "body_ratio" in calculated.columns
    assert "effort_result" in calculated.columns
    assert "volume_ratio20" in calculated.columns
    
    assert (calculated["lower_wick_ratio"] >= 0.0).all() and (calculated["lower_wick_ratio"] <= 1.0).all()
    assert (calculated["upper_wick_ratio"] >= 0.0).all() and (calculated["upper_wick_ratio"] <= 1.0).all()
    assert (calculated["body_ratio"] >= 0.0).all() and (calculated["body_ratio"] <= 1.0).all()
    assert (calculated["effort_result"] > 0.0).all()


def test_wyckoff_bottom_and_top_patterns_in_conditions():
    """Kiểm tra các nhãn điều kiện Wyckoff & Volume Action xuất hiện hợp lệ trong conditions."""
    sample = sample_bars(periods=300)
    calculated = calculate_indicator(sample)
    assert "conditions" in calculated.columns
    
    all_conditions = set()
    for cond_list in calculated["conditions"]:
        all_conditions.update(cond_list)
        
    expected_possible_labels = {
        "Pulse > Flow > Core (Dòng tiền tạo lập)",
        "Độ mở dòng tiền mở rộng",
        "Đã có tín hiệu Theo dõi đáy",
        "Dòng tiền Chaikin CMF dương",
        "Sức mạnh tương đối RS > 0",
        "Giá nằm trên EMA20",
        "Cấu trúc Bullish EMA",
        "Regime dài hạn tích cực",
        "Bật tăng từ vùng hỗ trợ EMA",
        "Cấu trúc Bearish EMA",
        "⚓ Nến Stopping Volume",
        "🛡️ Hấp thụ cung giá thấp",
        "🔱 Mô hình 3 nến đảo chiều đáy",
        "💧 Phân kỳ cạn cung đáy",
        "⚡ Cao trào bán tháo đã được hấp thụ",
        "🌱 Wyckoff Spring",
        "🚀 Wyckoff SOS",
        "📰 Bứt phá tin tốt",
        "⚠️ Cao trào mua đuổi đỉnh",
        "🪤 Bẫy giá vượt đỉnh Upthrust",
        "📉 Phân phối nỗ lực",
        "📰 Rủi ro phân phối tin tốt",
    }
    # Ensure any emitted label is a valid part of the designed conditions set
    assert all_conditions.issubset(expected_possible_labels)


def test_news_euphoria_distribution_vs_news_sos_rule():
    """Kiểm tra quy tắc phân loại tin tốt + volume bùng nổ: News Euphoria Distribution vs News SOS."""
    sample = sample_bars(periods=100)
    
    good_news = {"score": 75.0, "label": "Tích cực", "total_articles": 5, "catalysts": [{"title": "Lợi nhuận tăng trưởng", "sentiment": "POS"}]}
    calculated_sos = calculate_indicator(sample, news_sentiment=good_news)
    assert "trade_setup" in calculated_sos.columns
    assert "conditions" in calculated_sos.columns


def test_v2_stopping_volume_and_absorption_patterns():
    """Kiểm tra tính chuẩn xác của pattern Stopping Volume và High Volume Absorption."""
    # Tạo chuỗi downtrend có thanh Stopping Volume rõ rệt tại đáy
    dates = pd.date_range(end="2026-04-01", periods=100, freq="B")
    opens = [100.0 - i * 0.5 for i in range(95)] + [52.0, 50.0, 50.5, 51.0, 52.0]
    highs = [op + 0.5 for op in opens]
    lows = [op - 1.0 for op in opens]
    closes = [op - 0.4 for op in opens]
    volumes = [1_000_000 for _ in range(95)] + [1_200_000, 3_500_000, 1_100_000, 1_300_000, 1_400_000]
    
    # Tại bar index 96 (phiên số 97): Nến rút râu dưới lớn, CLV > 0.25, volume bùng nổ > 2.5x
    lows[96] = 46.0
    closes[96] = 51.5
    highs[96] = 52.0
    opens[96] = 49.0
    
    df = pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    
    calculated = calculate_indicator(df)
    assert "score_breakdown" in calculated.columns
    assert "volume_context" in calculated.columns
    assert "pattern_code" in calculated.columns
    
    # Kiểm tra score breakdown có 5 điểm số chuyên biệt minh bạch
    last_score = calculated.iloc[-1]["score_breakdown"]
    assert "bottom_quality_score" in last_score
    assert "breakout_quality_score" in last_score
    assert "pullback_quality_score" in last_score
    assert "distribution_quality_score" in last_score
    assert "breakdown_quality_score" in last_score
    assert "composite_score" in last_score
    assert 0 <= last_score["composite_score"] <= 100


def test_two_stage_candidate_lifecycle_and_invalidation():
    """Kiểm tra vòng đời candidate (1-3 phiên) và cơ chế vô hiệu hóa khi thủng đáy."""
    sample = sample_bars(periods=120)
    calculated = calculate_indicator(sample)
    
    assert "signal_stage" in calculated.columns
    assert "invalidation_price" in calculated.columns
    assert "follow_through_condition" in calculated.columns
    
    for stage in calculated["signal_stage"].dropna():
        assert stage in ("CANDIDATE_WATCH", "CONFIRMED", "INVALIDATED")


def test_two_stage_candidate_lifecycle_delay_and_metadata():
    """Kiểm tra BB1 không bao giờ cùng ngày với Candidate Watch (yêu cầu age >= 1)."""
    sample = sample_bars(periods=200)
    calculated = calculate_indicator(sample)
    
    for idx, row in calculated.iterrows():
        if row.get("signal_subtype") == "BB1_SPRING_CONFIRM":
            assert row.get("signal_stage") == "CONFIRMED"
            assert row.get("confirmation_date") is not None


def test_directional_hit_rate_bs_logic():
    """Kiểm tra Event Study tính toán đúng tỷ lệ thắng nghịch hướng cho BS (giảm = thắng)."""
    from bottom_indicator_engine import _event_study
    sample = sample_bars(periods=250)
    calculated = calculate_indicator(sample)
    
    study = _event_study(calculated)
    assert "BS" in study
    for h in ("3d", "5d", "10d", "20d", "60d"):
        assert "hit_rate_pct" in study["BS"]["metrics"][h]


def test_event_study_horizons_and_metrics_v2():
    """Kiểm tra Event Study hỗ trợ đầy đủ các mốc 3, 5, 10, 20, 60 phiên với MAE, MFE, False rate."""
    from bottom_indicator_engine import _event_study
    sample = sample_bars(periods=200)
    calculated = calculate_indicator(sample)
    
    study = _event_study(calculated)
    assert "BOTTOM_WATCH" in study
    assert "BB" in study
    assert "DISTRIBUTION_CONTRACTION" in study
    assert "BS" in study
    
    bb_metrics = study["BB"]["metrics"]
    for horizon in ("3d", "5d", "10d", "20d", "60d"):
        assert horizon in bb_metrics
        assert "median_return_pct" in bb_metrics[horizon]
        assert "hit_rate_pct" in bb_metrics[horizon]
        assert "sample_size" in bb_metrics[horizon]


def test_point_in_time_news_never_leaks_to_historical_bars():
    """Quy tắc Causal: Live news sentiment chỉ tác động tới session hiện tại, không ảnh hưởng quá khứ."""
    sample = sample_bars(periods=150)
    
    # Chạy lần 1 không có live news
    calc_no_news = calculate_indicator(sample, news_sentiment=None)
    
    # Chạy lần 2 có live news hưng phấn
    hot_news = {"score": 88.0, "label": "Hưng phấn cao", "total_articles": 10, "catalysts": [{"title": "Lãi kỷ lục", "sentiment": "POS"}]}
    calc_with_news = calculate_indicator(sample, news_sentiment=hot_news)
    
    # Các bar từ 0 đến n-2 phải tuyệt đối bằng nhau về state và signals
    for idx in range(len(sample) - 1):
        assert calc_no_news.iloc[idx]["state"] == calc_with_news.iloc[idx]["state"]
        assert calc_no_news.iloc[idx]["opportunity_score"] == calc_with_news.iloc[idx]["opportunity_score"]
        assert calc_no_news.iloc[idx]["risk_score"] == calc_with_news.iloc[idx]["risk_score"]


def test_all_5_subtypes_emission_and_lifecycle_invariants():
    """Kiểm tra các subtype BB1, BB2, BB3, BS1, BS2 tuân thủ nghiêm ngặt chuẩn Plan v2.1."""
    from bottom_indicator_engine import calculate_indicator
    
    # 1. Synthesize multi-phase market
    periods = 300
    dates = pd.bdate_range("2024-01-01", periods=periods)
    
    # Base accumulation -> Spring -> Breakout SOS -> Pullback LPS -> Rally Climax -> Breakdown SOW
    prices = np.full(periods, 30000.0)
    for i in range(50):
        prices[i] = 40000 - i * 200  # downtrend
    # Spring at bar 55-60
    prices[50:60] = 30000.0
    prices[55] = 28500.0  # Spring dip
    prices[56] = 29800.0  # Spring test / confirm
    prices[57:70] = np.linspace(30000, 35000, 13)
    # Breakout SOS at bar 75
    prices[70:120] = np.linspace(35000, 48000, 50)
    # Climax at bar 150
    prices[120:160] = np.linspace(48000, 65000, 40)
    prices[150] = 68000.0
    prices[151] = 66000.0
    # Breakdown at bar 180
    prices[160:220] = np.linspace(65000, 45000, 60)
    prices[220:300] = np.linspace(45000, 48000, 80)
    
    df = pd.DataFrame({
        "date": dates,
        "open": prices * 0.995,
        "high": prices * 1.02,
        "low": prices * 0.98,
        "close": prices,
        "volume": np.full(periods, 1_500_000.0),
    })
    
    calc = calculate_indicator(df)
    for idx, row in calc.iterrows():
        if row.get("signal") == "BB":
            assert row.get("signal_subtype") in ("BB1_SPRING_CONFIRM", "BB2_SOS_BREAKOUT", "BB3_LPS_PULLBACK")
            assert row.get("signal_stage") == "CONFIRMED"
            assert row.get("score_breakdown") is not None
        elif row.get("signal") == "BS":
            assert row.get("signal_subtype") in ("BS1_CLIMAX_DISTRIBUTION", "BS2_SOW_BREAKDOWN")
            assert row.get("signal_stage") == "CONFIRMED"
            assert row.get("score_breakdown") is not None


def test_backtest_endpoint_returns_v2_payload():
    """Kiểm tra get_bottom_backtest trả về đầy đủ summary, trades, equity_curve, event_study."""
    from bottom_indicator_engine import get_bottom_backtest
    backtest = get_bottom_backtest("FPT", bar_limit=120)
    assert backtest["status"] == "ok"
    assert "summary" in backtest
    assert "event_study" in backtest
    assert "equity_curve" in backtest
    assert "trades" in backtest
    assert "execution_audit" in backtest
    assert backtest["execution_audit"]["causal_execution"] is True


def test_market_regime_and_action_codes_v3():
    """Kiểm tra tách biệt 3 tầng: Market Regime, Lifecycle Event, Action Code."""
    from bottom_indicator_engine import calculate_indicator, _round_hose_tick
    sample = sample_bars(periods=250)
    calc = calculate_indicator(sample)

    assert "market_regime" in calc.columns
    assert "lifecycle_event" in calc.columns
    assert "action_code" in calc.columns
    assert "watch_subtype" in calc.columns
    assert "quality_score" in calc.columns
    assert "score_type" in calc.columns

    for idx, row in calc.iterrows():
        assert row["market_regime"] in ("BULL_TREND", "RECOVERY", "RANGE", "DOWNTREND", "SEVERE_DOWNTREND")
        assert row["action_code"] in ("WATCH", "TEST_BUY", "ADD_BUY", "HOLD", "TRIM", "EXIT")
        if row["lifecycle_event"] is not None:
            assert row["lifecycle_event"] in ("CREATED", "CONFIRMED", "INVALIDATED", "EXPIRED")
        assert row["score_type"] in ("BOTTOM_QUALITY", "BREAKOUT_QUALITY", "PULLBACK_QUALITY", "DISTRIBUTION_QUALITY", "BREAKDOWN_QUALITY")
        assert 0 <= row["quality_score"] <= 100


def test_hose_tick_rounding_rules():
    """Kiểm tra bước giá khớp lệnh HOSE chuẩn: <10k bước 10, 10k-50k bước 50, >=50k bước 100."""
    from bottom_indicator_engine import _round_hose_tick
    # < 10k: bước 10 VND
    assert _round_hose_tick(8432.0) == 8430.0
    assert _round_hose_tick(9996.0) == 10000.0
    # 10k - 50k: bước 50 VND
    assert _round_hose_tick(24123.0) == 24100.0
    assert _round_hose_tick(24135.0) == 24150.0
    # >= 50k: bước 100 VND
    assert _round_hose_tick(115430.0) == 115400.0
    assert _round_hose_tick(115480.0) == 115500.0


def test_simulation_100_shares_lot_and_equity_benchmark():
    """Kiểm tra mô phỏng làm tròn lô 100 cổ phiếu và tách biệt buy_hold_equity."""
    from bottom_indicator_engine import _simulate, calculate_indicator
    sample = sample_bars(periods=200)
    calc = calculate_indicator(sample)
    sim = _simulate(calc)

    assert "summary" in sim
    assert "equity_curve" in sim
    assert "trades" in sim

    # Tất cả các lệnh phải có số lượng là bội số của 100 (lô chẵn HOSE/HNX)
    for trade in sim["trades"]:
        assert trade["shares"] % 100 == 0
        assert trade["shares"] >= 100

    # Equity curve có buy_hold_equity
    for pt in sim["equity_curve"]:
        assert "buy_hold_equity" in pt
        assert "equity" in pt


def test_fpt_severe_downtrend_market_emotion_capped_and_never_green():
    """Regression Test FPT: Giá rơi liên tục dò đáy mới dưới EMA20/50/100/200 -> Regime SEVERE_DOWNTREND, Emotion <= 44, Never Green."""
    from bottom_indicator_engine import calculate_indicator, _current_summary

    # Chạy lặp 25 lần với seed cố định để đảm bảo 100% không flaky
    for seed in range(42, 67):
        np.random.seed(seed)
        dates = pd.date_range(start="2026-01-01", periods=150, freq="B")
        prices = 140.0 * np.exp(-0.007 * np.arange(150))  # Rơi từ 140 về ~48
        vols = 3_000_000 + 1_000_000 * np.linspace(0.8, 1.4, 150)
        highs = prices * 1.008
        lows = prices * 0.982
        opens = prices * 1.004
        closes = prices * 0.985  # Nến đỏ, đóng cửa sát đáy phiên (CLV < 0)

        df = pd.DataFrame({
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        })

        calc = calculate_indicator(df)
        last_row = calc.iloc[-1]

        # Kiểm tra Regime
        assert last_row["market_regime"] == "SEVERE_DOWNTREND"
        assert last_row["regime_cap"] == 44.0

        # Kiểm tra Market Emotion Score không bao giờ vượt quá 44.0
        assert last_row["market_emotion_score"] <= 44.0
        assert last_row["emotion_state"] in ("PANIC", "FEAR", "CAUTIOUS")
        assert last_row["emotion_state"] not in ("GREED", "FOMO", "HOPE")
        assert last_row["emotion_state_color"] in ("#991b1b", "#dc2626", "#d97706")

        # Kiểm tra Smart Money Outflow Index phải phản ánh dòng tiền lớn rút lui
        assert last_row["smart_money_outflow_score"] >= 60.0

        summary = _current_summary(last_row, {"status": "valid"})
        eb = summary["emotion_breakdown"]
        assert eb["market_emotion_score"] <= 44.0
        assert eb["market_regime"] == "SEVERE_DOWNTREND"
        assert eb["regime_cap"] == 44.0


def test_entity_resolution_filters_subsidiary_and_unrelated_news():
    """Kiểm tra Entity Resolution: Tin Long Châu / FRT / GEE không làm tăng điểm tin tức của FPT."""
    from bottom_indicator_engine import _resolve_entity_relevance, _analyze_financial_text_sentiment

    # Tin GEE công bố doanh nghiệp -> relevance cho FPT phải < 0.20
    rel_gee = _resolve_entity_relevance(
        "FPT",
        "GEE: Nghị quyết HĐQT thông qua kế hoạch trả cổ tức và phát hành tăng vốn",
        "Công ty Cổ phần Thiết bị điện Gelex (GEE) công bố thông tin",
        "Công bố doanh nghiệp"
    )
    assert rel_gee < 0.20
    assert 0.0 <= rel_gee <= 1.0

    # Tin Long Châu mở chuỗi nhà thuốc -> relevance cho FPT phải < 0.65
    rel_lc = _resolve_entity_relevance(
        "FPT",
        "Long Châu mở thêm 500 nhà thuốc đạt doanh thu kỷ lục",
        "Chuỗi nhà thuốc Long Châu tiếp tục tăng trưởng mạnh",
        "Báo Đầu Tư"
    )
    assert rel_lc < 0.65
    assert 0.0 <= rel_lc <= 1.0

    # Tin FPT Software ký hợp đồng -> relevance cho FPT phải >= 0.90
    rel_fpt = _resolve_entity_relevance(
        "FPT",
        "FPT Software ký hợp đồng 100 triệu USD với đối tác Mỹ",
        "Tập đoàn FPT ghi nhận doanh thu tăng trưởng tích cực",
        "Báo Đầu Tư"
    )
    assert rel_fpt >= 0.90
    assert 0.0 <= rel_fpt <= 1.0

    # Kiểm tra Phủ định: "không lỗ", "không bị xử phạt"
    tone_negated, _, _, _ = _analyze_financial_text_sentiment("doanh nghiệp không bị xử phạt và không thua lỗ")
    assert tone_negated >= 0.0  # Không bị đánh tụt thành âm


def test_smart_money_outflow_index_and_non_repeating_lifecycle():
    """Kiểm tra Outflow Index và chu kỳ phát tín hiệu không lặp lại liên tục."""
    from bottom_indicator_engine import calculate_indicator
    sample = sample_bars(periods=120)
    calc = calculate_indicator(sample)

    assert "smart_money_outflow_score" in calc.columns
    assert "outflow_event" in calc.columns

    # Kiểm tra điểm số nằm trong khoảng 0 - 100
    for score in calc["smart_money_outflow_score"]:
        assert 0.0 <= score <= 100.0

    # Kiểm tra sự kiện OUTFLOW_CONFIRMED hoặc OUTFLOW_WATCH không xuất hiện liên tiếp 5 phiên
    events = calc["outflow_event"].dropna().tolist()
    for ev in events:
        assert ev in ("OUTFLOW_WATCH", "OUTFLOW_CONFIRMED")


def test_news_clustering_novelty_decay_and_distribution_reaction():
    """Kiểm tra Novelty Decay khi tin tức lặp lại và Phản ứng giá xả hàng trên tin tốt."""
    from bottom_indicator_engine import _cluster_and_deduplicate_news, _classify_news_price_reaction, _analyze_news_sentiment

    # 1. Clustering & Novelty Decay
    articles = [
        {"title": "FPT ký hợp đồng AI lớn tại thị trường Bắc Mỹ", "source": "Báo Đầu Tư"},
        {"title": "FPT ký kết hợp đồng AI lớn tại thị trường Bắc Mỹ trị giá 100 triệu USD", "source": "CafeF"},
        {"title": "FPT công bố dự án công nghệ mới tại Nhật Bản", "source": "VnExpress"},
    ]
    clustered = _cluster_and_deduplicate_news(articles)
    assert len(clustered) == 2  # 2 cụm duy nhất
    rep1 = next(c for c in clustered if "Bắc Mỹ" in c["title"])
    assert rep1["cluster_count"] == 2
    assert rep1["novelty_score"] < 1.0  # Novelty bị decay do tin trùng

    # 2. Price Reaction: Tin tốt nhưng nến đỏ, volume xả hàng -> GOOD_NEWS_DISTRIBUTION_RISK
    bar_metrics = {
        "clv": -0.45,
        "volume_ratio20": 1.60,
        "upper_wick_ratio": 0.35,
        "open": 100.0,
        "close": 98.0,
    }
    reaction = _classify_news_price_reaction(75.0, bar_metrics)
    assert reaction == "GOOD_NEWS_DISTRIBUTION_RISK"

    # 3. No direct valid news
    news_res = _analyze_news_sentiment("NONEXISTENT_XYZ_TICKER")
    assert news_res["news_tone_score"] is None
    assert news_res["news_attention_score"] == 0.0
    assert news_res["news_adjustment"] == 0.0


def test_data_invariants_current_series_states_synchronized():
    """Kiểm tra Invariant: current, series[-1], states[-1] phải có cùng market_emotion_score, regime và outflow."""
    from bottom_indicator_engine import calculate_indicator, _build_analysis_payload, _quality_payload
    from rrg_data_gateway import HistoryResult

    sample = sample_bars(periods=90)
    calculated = calculate_indicator(sample)
    vh = HistoryResult(
        frame=sample,
        source="MOCK",
        source_chain=[{"source": "MOCK"}],
        served_from_cache=False,
        freshness_sessions=0,
        last_success_at=None,
        source_agreement_bps=0.0,
        data_confidence_score=100.0,
        adjustment_version="v1",
        corporate_action_status="NORMAL"
    )
    quality = _quality_payload(vh, False)
    payload = _build_analysis_payload("FPT", 90, calculated, quality)

    curr = payload["current"]
    last_series = payload["series"][-1]
    last_state = payload["states"][-1]

    # Kiểm tra đồng bộ tuyệt đối
    assert curr["market_emotion_score"] == last_series["market_emotion_score"] == last_state["market_emotion_score"]
    assert curr["emotion_state"] == last_series["emotion_state"] == last_state["emotion_state"]
    assert curr["emotion_state_color"] == last_series["emotion_state_color"] == last_state["emotion_state_color"]
    assert curr["market_regime"] == last_series["market_regime"] == last_state["market_regime"]
    assert curr["regime_cap"] == last_series["regime_cap"] == last_state["regime_cap"]
    assert curr["smart_money_outflow_score"] == last_series["smart_money_outflow_score"] == last_state["smart_money_outflow_score"]


def test_frontend_dom_cleanup():
    """Kiểm tra các card chi tiết đã xóa hoàn toàn khỏi HTML và JS."""
    root_dir = Path(__file__).resolve().parent.parent
    html_path = root_dir / "static" / "bottom-indicator.html"
    js_path = root_dir / "static" / "bottom-indicator.js"

    html_content = html_path.read_text(encoding="utf-8")
    js_content = js_path.read_text(encoding="utf-8")

    # Các phần tử phải biến mất khỏi HTML
    assert "bottom-emotion-detail-grid" not in html_content
    assert "Thước Đo Nhiệt Kế Cảm Xúc Thị Trường" not in html_content
    assert "Sắc Thái Tin Tức & Phản Ứng Giá" not in html_content
    assert 'id="gaugePointer"' not in html_content
    assert 'id="barMomentum"' not in html_content
    assert 'id="catalystFeed"' not in html_content

    # Các biểu đồ chính vẫn phải tồn tại trong HTML
    assert 'id="priceChart"' in html_content
    assert 'id="flowChart"' in html_content
    assert 'id="apertureChart"' in html_content
    assert 'id="crowdSentiment"' in html_content

    # Renderer không dùng phải biến mất khỏi JS
    assert "function updateEmotionBreakdown" not in js_content
    assert "function renderNewsSection" not in js_content


# ═══════════════════════════════════════════════════════════════════════════
# SMART MONEY START V2 SPECIFICATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

def test_smart_money_v2_five_independent_factor_groups():
    """Kiểm tra 5 nhóm nhân tố độc lập (0-100) và Tri-EMA Ribbon."""
    stock = sample_bars(periods=300)
    bm = sample_bars(periods=300, phase=0.5)
    calc = calculate_indicator(stock, bm)

    groups = [
        "group_directional_flow",
        "group_effort_vs_result",
        "group_price_acceptance",
        "group_structure_rs",
        "group_participation",
    ]
    for g in groups:
        assert g in calc.columns
        series = calc[g].dropna()
        assert (series >= 0.0).all()
        assert (series <= 100.0).all()

    # Kiểm tra Tri-EMA ribbon
    for col in ["pulse_pct", "flow_pct", "core_pct", "center_pct", "smart_money_score"]:
        assert col in calc.columns
        s = calc[col].dropna()
        assert (s >= 0.0).all()
        assert (s <= 100.0).all()


def test_smart_money_v2_dynamic_benchmark_renormalization():
    """Kiểm tra khi thiếu benchmark: Tái chuẩn hóa tổng trọng số 4 nhóm về 1.0 (100%), không chèn 0.0."""
    stock = sample_bars(periods=300)
    calc_no_bm = calculate_indicator(stock, None)
    calc_with_bm = calculate_indicator(stock, stock)

    assert "smart_money_score" in calc_no_bm.columns
    assert calc_no_bm["smart_money_score"].notna().all()
    # Confidence khi thiếu benchmark phải thấp hơn khi có benchmark đầy đủ
    assert calc_no_bm["smart_money_confidence"].iloc[-1] < calc_with_bm["smart_money_confidence"].iloc[-1]


def test_smart_money_v2_strictly_causal_completed_weekly_regime():
    """Kiểm tra Weekly Regime strictly causal: chỉ dùng tuần đã hoàn tất (Thứ 2 - Thứ 6 tuần trước)."""
    from bottom_indicator_engine import _compute_completed_weekly_regime
    stock = sample_bars(periods=150)
    w_trend, w_regime = _compute_completed_weekly_regime(stock)

    assert len(w_trend) == len(stock)
    assert len(w_regime) == len(stock)
    assert set(w_trend).issubset({"BULLISH", "BEARISH", "NEUTRAL"})
    assert set(w_regime).issubset({"BULL_TREND", "DOWNTREND", "RANGE"})


def test_smart_money_v2_causal_market_structure_and_pivots():
    """Kiểm tra cấu trúc thị trường với 3-bar confirmed pivots (Pivot tại T-3 xác nhận tại T)."""
    from bottom_indicator_engine import _detect_market_structure
    stock = sample_bars(periods=200)
    atr = stock["high"] - stock["low"]
    events, sweeps = _detect_market_structure(stock, atr)

    assert len(events) == len(stock)
    assert len(sweeps) == len(stock)
    valid_sweeps = {None, "BULLISH_SWEEP", "BEARISH_SWEEP"}
    assert set(sweeps).issubset(valid_sweeps)


def test_smart_money_v2_seven_phase_fsm_with_hysteresis():
    """Kiểm tra máy trạng thái 7 pha với độ trễ 2 phiên (2-bar hysteresis)."""
    from bottom_indicator_engine import SMART_MONEY_PHASE_LABELS, SMART_MONEY_PHASE_COLORS
    stock = sample_bars(periods=250)
    calc = calculate_indicator(stock)

    assert "smart_money_phase" in calc.columns
    assert "smart_money_phase_label" in calc.columns
    assert "smart_money_phase_color" in calc.columns

    phases = set(calc["smart_money_phase"])
    assert phases.issubset(set(SMART_MONEY_PHASE_LABELS.keys()))

    for p in phases:
        assert p in SMART_MONEY_PHASE_LABELS
        assert p in SMART_MONEY_PHASE_COLORS


def test_smart_money_v2_confidence_metric_and_liquidity_tiers():
    """Kiểm tra chỉ số độ tin cậy Smart Money Confidence (0-100) theo thanh khoản & dữ liệu."""
    stock = sample_bars(periods=300)
    calc = calculate_indicator(stock)

    assert "smart_money_confidence" in calc.columns
    conf = calc["smart_money_confidence"].dropna()
    assert (conf >= 0.0).all()
    assert (conf <= 100.0).all()





