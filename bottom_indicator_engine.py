"""Engine Chỉ Báo Đáy cho Lộc Phát Securities.

Mô hình chỉ sử dụng dữ liệu OHLCV ngày đã được kiểm định. Các điểm số là độ
đồng thuận của quy tắc, không phải xác suất và không phải khuyến nghị đầu tư.
"""

from __future__ import annotations

import copy
import math
import re
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd

from rrg_data_gateway import HistoryUnavailable, get_verified_history


FORMULA_VERSION = "bottom-indicator-v5.0.0"
SMART_MONEY_VERSION = "smart-money-start-v2.0"
DEFAULT_BAR_LIMIT = 748
MIN_BAR_LIMIT = 60
MAX_BAR_LIMIT = 1500
CACHE_TTL_SECONDS = 900
STATE_LABELS = {
    "NEUTRAL": "Trung tính",
    "FALLING_CONTRACTION": "Co hẹp, rủi ro giảm",
    "BOTTOM_WATCH": "Theo dõi tạo đáy (Candidate)",
    "TOP_WATCH": "Cảnh báo vùng đỉnh (Top Watch)",
    "EARLY_EXPANSION": "Cơ hội mở sớm",
    "CONFIRMED_EXPANSION": "Hồi phục được xác nhận",
    "OVEREXTENDED": "Quá mở, không đuổi giá",
    "DISTRIBUTION_CONTRACTION": "Co phân phối, rủi ro rơi lại",
}
SMART_MONEY_PHASE_LABELS = {
    "ACCUMULATION_WATCH": "Theo dõi gom hàng (Accumulation Watch)",
    "ACCUMULATION_CONFIRMED": "Xác nhận gom hàng (Accumulation Confirmed)",
    "MARKUP": "Đẩy giá (Markup)",
    "DISTRIBUTION_WATCH": "Cảnh báo phân phối (Distribution Watch)",
    "DISTRIBUTION_CONFIRMED": "Xác nhận phân phối (Distribution Confirmed)",
    "MARKDOWN": "Đè giá / Giảm (Markdown)",
    "NEUTRAL": "Trung tính / Chưa rõ xu hướng",
}
SMART_MONEY_PHASE_COLORS = {
    "ACCUMULATION_WATCH": "#38bdf8",
    "ACCUMULATION_CONFIRMED": "#10b981",
    "MARKUP": "#059669",
    "DISTRIBUTION_WATCH": "#fbbf24",
    "DISTRIBUTION_CONFIRMED": "#ef4444",
    "MARKDOWN": "#991b1b",
    "NEUTRAL": "#94a3b8",
}
REGIME_LABELS = {
    "BULL_TREND": "Xu hướng tăng (Bull Trend)",
    "RECOVERY": "Hồi phục / Tái tích lũy (Recovery)",
    "RANGE": "Đi ngang / Tích lũy (Range)",
    "DOWNTREND": "Xu hướng giảm (Downtrend)",
    "SEVERE_DOWNTREND": "Xu hướng giảm mạnh (Severe Downtrend)",
}
EMOTION_STATE_LABELS = {
    "PANIC": "Hoảng loạn",
    "FEAR": "Sợ hãi",
    "CAUTIOUS": "Thận trọng",
    "NEUTRAL": "Trung tính yếu",
    "HOPE": "Hy vọng / Hồi phục",
    "RELIEF_RALLY": "Hồi kỹ thuật – rủi ro bull trap",
    "GREED": "Tham lam",
    "FOMO": "FOMO cực độ",
}
EMOTION_STATE_COLORS = {
    "PANIC": "#991b1b",
    "FEAR": "#dc2626",
    "CAUTIOUS": "#d97706",
    "NEUTRAL": "#64748b",
    "HOPE": "#2563eb",
    "RELIEF_RALLY": "#0284c7",
    "GREED": "#087b50",
    "FOMO": "#8b5cf6",
}
ACTION_LABELS = {
    "WATCH": "Quan sát (Chờ xác nhận)",
    "TEST_BUY": "Mua thăm dò (20–30% vị thế)",
    "ADD_BUY": "Mua gia tăng (Tối đa 60–70% vốn)",
    "HOLD": "Nắm giữ (Chặn lãi Trailing Stop)",
    "TRIM": "Hạ tỷ trọng / Phòng thủ",
    "EXIT": "Thoát toàn bộ vị thế",
}
LIFECYCLE_LABELS = {
    "CREATED": "Phát hiện Candidate",
    "CONFIRMED": "Xác nhận tín hiệu",
    "INVALIDATED": "Vô hiệu hóa candidate",
    "EXPIRED": "Hết hiệu lực candidate",
}

_CACHE: dict[tuple[str, int, bool], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _rounded(value: Any, digits: int = 4) -> Optional[float]:
    number = _finite(value)
    return None if number is None else round(number, digits)


def _validate_request(symbol: str, bar_limit: int) -> tuple[str, int]:
    clean_symbol = str(symbol or "").upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", clean_symbol):
        raise ValueError("Mã cổ phiếu không hợp lệ.")
    try:
        clean_limit = int(bar_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("bar_limit phải là số nguyên.") from exc
    if not MIN_BAR_LIMIT <= clean_limit <= MAX_BAR_LIMIT:
        raise ValueError(f"bar_limit phải nằm trong khoảng {MIN_BAR_LIMIT}–{MAX_BAR_LIMIT}.")
    return clean_symbol, clean_limit


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    date_column = "date" if "date" in result else "time" if "time" in result else None
    if date_column is None:
        return pd.DataFrame()
    result["date"] = pd.to_datetime(result[date_column], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result.get(column), errors="coerce")
    result = result.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    valid = (
        (result[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (result["volume"] >= 0)
        & (result["high"] >= result[["open", "close", "low"]].max(axis=1))
        & (result["low"] <= result[["open", "close", "high"]].min(axis=1))
    )
    return (
        result.loc[valid, ["date", "open", "high", "low", "close", "volume"]]
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((loss == 0) & (gain > 0), 100.0)
    result = result.mask((loss == 0) & (gain == 0), 50.0)
    return result


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Tính MACD(fast, slow, signal) theo phương pháp causal EWM.

    Trả về (macd_line, signal_line, histogram).
    Histogram = macd_line - signal_line — được dùng để nhận diện phân kỳ (nhạy hơn MACD thuần).
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _cmf(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    spread = (frame["high"] - frame["low"]).replace(0, np.nan)
    multiplier = ((frame["close"] - frame["low"]) - (frame["high"] - frame["close"])) / spread
    return (multiplier.fillna(0) * frame["volume"]).rolling(period, min_periods=period).sum() / frame["volume"].rolling(period, min_periods=period).sum().replace(0, np.nan)


def _mfi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    raw_flow = typical * frame["volume"]
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0).rolling(period, min_periods=period).sum()
    negative = raw_flow.where(direction < 0, 0).rolling(period, min_periods=period).sum()
    ratio = positive / negative.replace(0, np.nan)
    result = 100 - 100 / (1 + ratio)
    return result.mask((negative == 0) & (positive > 0), 100.0)


def _robust_z(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    median = series.rolling(window, min_periods=min_periods).median()
    deviation = (series - median).abs()
    mad = deviation.rolling(window, min_periods=min_periods).median()
    denominator = (1.4826 * mad).replace(0, np.nan)
    return ((series - median) / denominator).clip(-3, 3).fillna(0.0)


def _rolling_robust_z(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    """Strictly causal rolling Robust Z-score using rolling Median and MAD."""
    median = series.rolling(window, min_periods=min_periods).median()
    deviation = (series - median).abs()
    mad = deviation.rolling(window, min_periods=min_periods).median()
    denominator = (1.4826 * mad).replace(0, np.nan)
    return ((series - median) / denominator).clip(-3.0, 3.0).fillna(0.0)


def _z_to_score_0_100(z: pd.Series, slope: float = 1.25) -> pd.Series:
    """Map Z-score (-3..+3) to continuous score 0..100 with 50 at center."""
    return (100.0 / (1.0 + np.exp(-slope * z.clip(-4.0, 4.0)))).clip(0.0, 100.0)


def _compute_completed_weekly_regime(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Causal weekly trend synthesized strictly from completed calendar weeks.
    
    At daily session T (e.g. Wednesday), only use the weekly bar ending on previous Friday.
    Returns (weekly_trend, weekly_regime).
    """
    if len(frame) < 10:
        return pd.Series("NEUTRAL", index=frame.index), pd.Series("RANGE", index=frame.index)
    
    df_copy = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    iso = df_copy["date"].dt.isocalendar()
    df_copy["iso_year"] = iso.year
    df_copy["iso_week"] = iso.week
    
    weekly_bars = df_copy.groupby(["iso_year", "iso_week"], as_index=False).agg({
        "date": "last",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).sort_values("date").reset_index(drop=True)
    
    weekly_bars["w_ema10"] = weekly_bars["close"].ewm(span=10, adjust=False).mean()
    weekly_bars["w_ema20"] = weekly_bars["close"].ewm(span=20, adjust=False).mean()
    weekly_bars["w_trend"] = np.where(
        weekly_bars["close"] > weekly_bars["w_ema10"], "BULLISH",
        np.where(weekly_bars["close"] < weekly_bars["w_ema20"], "BEARISH", "NEUTRAL")
    )
    weekly_bars["w_regime"] = np.where(
        (weekly_bars["close"] > weekly_bars["w_ema10"]) & (weekly_bars["w_ema10"] > weekly_bars["w_ema20"]), "BULL_TREND",
        np.where((weekly_bars["close"] < weekly_bars["w_ema10"]) & (weekly_bars["w_ema10"] < weekly_bars["w_ema20"]), "DOWNTREND", "RANGE")
    )
    
    week_lookup = {}
    for idx in range(1, len(weekly_bars)):
        prior_week_trend = weekly_bars.iloc[idx - 1]["w_trend"]
        prior_week_regime = weekly_bars.iloc[idx - 1]["w_regime"]
        curr_year = weekly_bars.iloc[idx]["iso_year"]
        curr_week = weekly_bars.iloc[idx]["iso_week"]
        week_lookup[(curr_year, curr_week)] = (prior_week_trend, prior_week_regime)
        
    daily_trends = []
    daily_regimes = []
    for _, row in df_copy.iterrows():
        k = (row["iso_year"], row["iso_week"])
        t, r = week_lookup.get(k, ("NEUTRAL", "RANGE"))
        daily_trends.append(t)
        daily_regimes.append(r)
        
    return pd.Series(daily_trends, index=frame.index), pd.Series(daily_regimes, index=frame.index)


def _detect_market_structure(
    frame: pd.DataFrame,
    atr_series: pd.Series,
) -> tuple[list[Optional[dict[str, Any]]], list[Optional[str]]]:
    """Causal market structure detection with 3-bar confirmed pivots.
    
    At bar i, a pivot at i-3 is confirmed if it is the extreme in [i-6 .. i].
    Returns (structure_events, liquidity_sweeps).
    """
    n = len(frame)
    structure_events: list[Optional[dict[str, Any]]] = [None] * n
    liquidity_sweeps: list[Optional[str]] = [None] * n
    
    confirmed_swing_highs: list[dict[str, Any]] = []
    confirmed_swing_lows: list[dict[str, Any]] = []
    
    highs = frame["high"].values
    lows = frame["low"].values
    closes = frame["close"].values
    dates = [d.strftime("%Y-%m-%d") for d in frame["date"]]
    
    last_struct_trend = "RANGE"
    
    for i in range(6, n):
        cur_atr = float(atr_series.iloc[i]) if _finite(atr_series.iloc[i]) is not None and atr_series.iloc[i] > 0 else float(closes[i] * 0.02)
        tol = 0.15 * cur_atr
        
        p_idx = i - 3
        is_pivot_high = (highs[p_idx] >= np.max(highs[i-6:p_idx])) and (highs[p_idx] >= np.max(highs[p_idx+1:i+1]))
        is_pivot_low = (lows[p_idx] <= np.min(lows[i-6:p_idx])) and (lows[p_idx] <= np.min(lows[p_idx+1:i+1]))
        
        if is_pivot_high:
            confirmed_swing_highs.append({
                "pivot_idx": p_idx,
                "pivot_date": dates[p_idx],
                "price": float(highs[p_idx]),
                "confirm_idx": i,
                "confirm_date": dates[i],
            })
            
        if is_pivot_low:
            confirmed_swing_lows.append({
                "pivot_idx": p_idx,
                "pivot_date": dates[p_idx],
                "price": float(lows[p_idx]),
                "confirm_idx": i,
                "confirm_date": dates[i],
            })
            
        sweep_event = None
        if confirmed_swing_lows:
            last_low = confirmed_swing_lows[-1]["price"]
            if lows[i] < last_low and closes[i] >= last_low - tol * 0.5:
                sweep_event = "BULLISH_SWEEP"
        if not sweep_event and confirmed_swing_highs:
            last_high = confirmed_swing_highs[-1]["price"]
            if highs[i] > last_high and closes[i] <= last_high + tol * 0.5:
                sweep_event = "BEARISH_SWEEP"
        liquidity_sweeps[i] = sweep_event
        
        struct_ev = None
        if confirmed_swing_highs:
            prior_sh = confirmed_swing_highs[-1]
            if closes[i] > prior_sh["price"] + tol:
                ev_type = "BULLISH_CHOCH" if last_struct_trend == "BEARISH" else "BULLISH_BOS"
                struct_ev = {
                    "type": ev_type,
                    "level": prior_sh["price"],
                    "pivot_date": prior_sh["pivot_date"],
                    "confirmation_date": dates[i],
                }
                last_struct_trend = "BULLISH"
                
        if not struct_ev and confirmed_swing_lows:
            prior_sl = confirmed_swing_lows[-1]
            if closes[i] < prior_sl["price"] - tol:
                ev_type = "BEARISH_CHOCH" if last_struct_trend == "BULLISH" else "BEARISH_BOS"
                struct_ev = {
                    "type": ev_type,
                    "level": prior_sl["price"],
                    "pivot_date": prior_sl["pivot_date"],
                    "confirmation_date": dates[i],
                }
                last_struct_trend = "BEARISH"
                
        structure_events[i] = struct_ev
        
    return structure_events, liquidity_sweeps


def _causal_percentile(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    def rank_current(values: np.ndarray) -> float:
        current = values[-1]
        finite = values[np.isfinite(values)]
        if not np.isfinite(current) or len(finite) == 0:
            return np.nan
        return float(np.count_nonzero(finite <= current) / len(finite) * 100)

    return series.rolling(window, min_periods=min_periods).apply(rank_current, raw=True)


def _map_emotion_state(score: Any, regime: str = "RANGE") -> tuple[str, str, str]:
    """Map Market Emotion score and regime to (state_code, state_label, state_color)."""
    val = _finite(score)
    if val is None:
        return "NEUTRAL", "Không xác định", "#64748b"
    s = float(val)
    if s < 20.0:
        return "PANIC", "Hoảng loạn", "#991b1b"
    elif s < 35.0:
        return "FEAR", "Sợ hãi", "#dc2626"
    elif s < 45.0:
        return "CAUTIOUS", "Thận trọng", "#d97706"
    elif s < 55.0:
        return "NEUTRAL", "Trung tính yếu", "#64748b"
    elif s < 65.0:
        if regime in ("SEVERE_DOWNTREND", "DOWNTREND"):
            return "RELIEF_RALLY", "Hồi kỹ thuật – rủi ro bull trap", "#0284c7"
        return "HOPE", "Hy vọng / Hồi phục", "#2563eb"
    elif s < 80.0:
        if regime == "BULL_TREND":
            return "GREED", "Tham lam", "#087b50"
        elif regime in ("SEVERE_DOWNTREND", "DOWNTREND"):
            return "RELIEF_RALLY", "Hồi kỹ thuật – rủi ro bull trap", "#0284c7"
        else:
            return "HOPE", "Hy vọng / Hồi phục", "#2563eb"
    else:
        if regime == "BULL_TREND":
            return "FOMO", "FOMO cực độ", "#8b5cf6"
        elif regime in ("SEVERE_DOWNTREND", "DOWNTREND"):
            return "RELIEF_RALLY", "Hồi kỹ thuật – rủi ro bull trap", "#0284c7"
        else:
            return "HOPE", "Hy vọng / Hồi phục", "#2563eb"


def _crowd_sentiment(aperture: Any, regime: Optional[str] = None) -> str:
    """Map Aperture percentile to crowd emotion label according to score and optional market regime."""
    val = _finite(aperture)
    if val is None:
        return "KHÔNG XÁC ĐỊNH"
    if regime is not None:
        _, label, _ = _map_emotion_state(val, regime)
        return label.upper() if label in ("Hoảng loạn", "Sợ hãi", "Thận trọng", "Trung tính yếu", "Tham lam", "FOMO cực độ") else label
    if val >= 80:
        return "FOMO CỰC ĐỘ"
    if val >= 60:
        return "THAM LAM"
    if val >= 40:
        return "TRUNG LẬP"
    if val >= 20:
        return "THẬN TRỌNG"
    return "SỢ HÃI"


def _align_relative_strength(frame: pd.DataFrame, benchmark: Optional[pd.DataFrame]) -> pd.Series:
    if benchmark is None or benchmark.empty:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    bench = _normalise_frame(benchmark)
    if bench.empty:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    bench = bench[["date", "close"]].rename(columns={"close": "benchmark_close"})
    aligned = frame[["date", "close"]].merge(bench, on="date", how="left")
    aligned["benchmark_close"] = aligned["benchmark_close"].ffill()
    stock_return = aligned["close"].pct_change(20, fill_method=None)
    benchmark_return = aligned["benchmark_close"].pct_change(20, fill_method=None)
    return pd.Series((stock_return - benchmark_return).to_numpy() * 100, index=frame.index)


from ai_advisor_engine import ISSUER_IDENTITY_REGISTRY, resolve_entity_relevance as _resolve_entity_relevance



NEWS_BULLISH_KEYWORDS = [
    "cổ tức", "tiền mặt", "tăng trưởng", "lợi nhuận", "kỷ lục", "vượt kế hoạch",
    "trúng thầu", "mở rộng", "fdi", "mua vào", "nâng hạng", "bứt phá", "tăng trần",
    "đối tác chiến lược", "tăng vốn", "doanh thu", "xuất khẩu", "triển vọng", "hồi phục",
    "gom mua", "lạc quan", "chốt quyền", "thặng dư", "chấp thuận", "khởi công",
    "bàn giao", "ký kết", "đột biến", "tích cực", "hưởng lợi", "tăng mạnh", "bùng nổ",
    "vượt đỉnh", "khả quan", "hút dòng tiền", "sôi động", "chia thưởng", "mua lại"
]

NEWS_BEARISH_KEYWORDS = [
    "giải chấp", "bị bán", "nợ xấu", "trái phiếu", "điều tra", "xử phạt", "vi phạm",
    "đình chỉ", "cảnh báo", "lỗ ròng", "suy giảm", "hủy niêm yết", "chậm nộp",
    "thanh tra", "khởi tố", "bắt tạm giam", "cắt margin", "hoãn", "bán tháo",
    "giảm sàn", "áp lực bán", "thua lỗ", "vỡ nợ", "hạn chế giao dịch", "nguy cơ",
    "khiếu nại", "thất bại", "giảm mạnh", "tiêu cực", "khó khăn", "rủi ro", "lao dốc"
]

NEGATION_PATTERNS = ["không", "chưa", "chẳng", "thoát", "không hề", "không có", "tránh", "ngừng", "không bị"]
CONTRAST_PATTERNS = ["nhưng", "tuy nhiên", "song", "dù", "mặc dù"]


def _analyze_financial_text_sentiment(text: str) -> tuple[float, str, list[str], list[str]]:
    """Phân tích sắc thái tài chính có ngữ cảnh phủ định và tương phản."""
    clean_text = text.lower()
    words = clean_text.split()

    pos_found = []
    neg_found = []
    pos_score = 0.0
    neg_score = 0.0

    # Contrast splitting
    has_contrast = any(f" {c} " in clean_text for c in CONTRAST_PATTERNS)
    segments = [clean_text]
    weights = [1.0]
    if has_contrast:
        for c in CONTRAST_PATTERNS:
            if f" {c} " in clean_text:
                parts = clean_text.split(f" {c} ", 1)
                segments = parts
                weights = [0.8, 1.5]
                break

    for seg, w in zip(segments, weights):
        seg_words = seg.split()
        for kw in NEWS_BULLISH_KEYWORDS:
            if kw in seg:
                pos_found.append(kw)
                pos_score += 1.0 * w
        for kw in NEWS_BEARISH_KEYWORDS:
            if kw in seg:
                # Check for negation in 1-4 words lookback
                is_negated = False
                kw_idx = -1
                for idx, word in enumerate(seg_words):
                    if kw in word or (idx + 1 < len(seg_words) and kw in f"{word} {seg_words[idx+1]}"):
                        kw_idx = idx
                        break
                if kw_idx > 0:
                    lookback = " ".join(seg_words[max(0, kw_idx - 3):kw_idx])
                    if any(neg_pat in lookback for neg_pat in NEGATION_PATTERNS):
                        is_negated = True

                if is_negated:
                    # Negated negative = positive/neutral confirmation (e.g. "không bị xử phạt")
                    pos_score += 0.5 * w
                else:
                    neg_found.append(kw)
                    neg_score += 1.2 * w

    net_diff = pos_score - neg_score
    tone = max(-1.0, min(1.0, net_diff / (pos_score + neg_score + 1.0)))

    # Event classification
    if any(k in clean_text for k in ("lợi nhuận", "doanh thu", "kết quả kinh doanh", "báo cáo tài chính", "lãi ròng", "bctc")):
        event_cat = "Kết quả kinh doanh"
    elif any(k in clean_text for k in ("cổ tức", "tiền mặt", "chia thưởng", "chốt quyền")):
        event_cat = "Cổ tức / Lợi nhuận"
    elif any(k in clean_text for k in ("hợp đồng", "trúng thầu", "dự án", "ký kết", "đối tác", "mở rộng")):
        event_cat = "Hợp đồng / Dự án"
    elif any(k in clean_text for k in ("tăng vốn", "phát hành", "chào bán", "esop", "trái phiếu")):
        event_cat = "Tăng vốn / Huy động"
    elif any(k in clean_text for k in ("cổ đông lớn", "nội bộ", "đăng ký mua", "đăng ký bán", "gom mua", "thoái vốn")):
        event_cat = "Giao dịch nội bộ"
    elif any(k in clean_text for k in ("xử phạt", "vi phạm", "thanh tra", "khởi tố", "đình chỉ", "hủy niêm yết")):
        event_cat = "Pháp lý / Cảnh báo"
    else:
        event_cat = "Tin tức chung"

    return tone, event_cat, list(dict.fromkeys(pos_found))[:3], list(dict.fromkeys(neg_found))[:3]


def _cluster_and_deduplicate_news(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gom nhóm các bài viết tương đồng trong 7 ngày thành một story cluster duy nhất."""
    if not articles:
        return []

    clusters: list[dict[str, Any]] = []
    for item in articles:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        title_words = set(re.findall(r'\w+', title.lower()))

        matched = False
        for cl in clusters:
            cl_words = cl["words"]
            intersection = len(title_words & cl_words)
            union = len(title_words | cl_words)
            min_len = min(len(title_words), len(cl_words))
            jaccard = intersection / max(union, 1)
            overlap = intersection / max(min_len, 1)
            if jaccard >= 0.50 or overlap >= 0.65:
                matched = True
                cl["count"] += 1
                break

        if not matched:
            clusters.append({
                "representative": item,
                "words": title_words,
                "count": 1,
            })

    result = []
    for cl in clusters:
        rep = copy.deepcopy(cl["representative"])
        cnt = cl["count"]
        rep["novelty_score"] = round(1.0 / math.sqrt(cnt), 2)
        rep["cluster_count"] = cnt
        result.append(rep)
    return result


def _classify_news_price_reaction(
    news_tone: Optional[float],
    bar_metrics: Optional[dict[str, Any]] = None,
) -> str:
    """Phân loại phản ứng giá và thanh khoản với tin tức."""
    if news_tone is None:
        return "NO_VALID_DIRECT_NEWS"
    if not bar_metrics:
        return "NEWS_UNCONFIRMED"

    clv = float(bar_metrics.get("clv") or 0.0)
    rvol = float(bar_metrics.get("volume_ratio20") or bar_metrics.get("rvol") or 1.0)
    upper_wick = float(bar_metrics.get("upper_wick_ratio") or 0.0)
    lower_wick = float(bar_metrics.get("lower_wick_ratio") or 0.0)
    close_px = float(bar_metrics.get("close") or 0.0)
    open_px = float(bar_metrics.get("open") or 0.0)
    gap_down = float(bar_metrics.get("gap_down") or 0.0)

    # 1. Good news confirmed: positive tone, elevated volume, strong close/CLV
    if news_tone >= 65.0 and rvol >= 1.30 and clv >= 0.40 and close_px >= open_px:
        return "GOOD_NEWS_CONFIRMED"

    # 2. Good news distribution risk: positive tone, heavy volume but poor close / heavy upper wick / selling
    if news_tone >= 65.0 and rvol >= 1.45 and (clv < 0.0 or upper_wick >= 0.30 or close_px < open_px * 0.99):
        return "GOOD_NEWS_DISTRIBUTION_RISK"

    # 3. Bad news confirmed selling: negative tone, panic selloff / gap down / negative CLV with volume
    if news_tone <= 35.0 and (gap_down >= 0.5 or clv <= -0.35) and rvol >= 1.35:
        return "BAD_NEWS_CONFIRMED_SELLING"

    # 4. Bad news capitulation watch: negative tone, heavy volume but strong lower wick recovery
    if news_tone <= 35.0 and rvol >= 1.70 and (lower_wick >= 0.28 or clv >= 0.15 or close_px > open_px):
        return "BAD_NEWS_CAPITULATION_WATCH"

    return "NEWS_UNCONFIRMED"


def _analyze_news_sentiment(symbol: str, current_bar: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Phân tích sắc thái tin tức thực thể trực tiếp và đo cảm xúc đám đông từ tin tức (News Crowd Emotion v4.1)."""
    clean_symbol = str(symbol or "").upper().strip()
    raw_articles: list[dict[str, Any]] = []

    try:
        from ai_advisor_engine import fetch_real_news_feed
        raw_articles = fetch_real_news_feed(clean_symbol)
    except Exception:
        raw_articles = []

    if not raw_articles:
        try:
            from market_data_provider import Company
            df = Company(clean_symbol).news()
            if df is not None and not df.empty:
                for _, r in df.head(10).iterrows():
                    raw_articles.append({
                        "title": str(r.get("newsTitle") or ""),
                        "snippet": str(r.get("newsTitle") or ""),
                        "article_url": str(r.get("newsSourceLink") or ""),
                        "published_at": str(r.get("publicDate") or ""),
                        "source": "Công bố doanh nghiệp",
                        "issuer_symbol": clean_symbol,
                    })
        except Exception:
            pass

    # 1. Entity Resolution: Filter only direct news with relevance >= 0.65
    valid_articles: list[dict[str, Any]] = []
    related_articles: list[dict[str, Any]] = []

    for item in raw_articles:
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        source = str(item.get("source") or "")
        issuer_sym = item.get("issuer_symbol")
        rel = _resolve_entity_relevance(clean_symbol, title, snippet, source, issuer_symbol=issuer_sym)
        item["entity_relevance"] = rel

        if rel >= 0.65:
            valid_articles.append(item)
        else:
            related_articles.append(item)

    # 2. Deduplication & Clustering
    clustered = _cluster_and_deduplicate_news(valid_articles)

    story_impacts: list[float] = []
    catalysts: list[dict[str, Any]] = []

    for item in clustered[:6]:
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        text = f"{title} {snippet}"
        tone, event_cat, pos_found, neg_found = _analyze_financial_text_sentiment(text)

        rel = float(item.get("entity_relevance", 1.0))
        novelty = float(item.get("novelty_score", 1.0))
        materiality = 1.0 if event_cat in ("Kết quả kinh doanh", "Cổ tức / Lợi nhuận", "Pháp lý / Cảnh báo") else 0.7
        credibility = 0.95 if "công bố" in str(item.get("source", "")).lower() or "vietcap" in str(item.get("source", "")).lower() else 0.85

        story_impact = tone * rel * materiality * credibility * novelty
        story_impacts.append(story_impact)

        sent = "POS" if tone > 0.15 else ("NEG" if tone < -0.15 else "NEU")
        catalysts.append({
            "title": title,
            "published_at": item.get("published_at") or item.get("timestamp") or "Mới cập nhật",
            "source": item.get("source") or "Tin tức thị trường",
            "url": item.get("article_url") or "",
            "sentiment": sent,
            "badge": event_cat,
            "entity_relevance": round(rel, 3),
            "entity_relevance_pct": round(rel * 100.0, 1),
            "novelty_score": round(novelty, 3),
            "novelty_score_pct": round(novelty * 100.0, 1),
            "tone_score": round((tone + 1.0) * 50.0, 1),
            "positive_keywords": pos_found,
            "negative_keywords": neg_found,
        })

    # If no direct valid news: score is None and adjustment is 0
    if not story_impacts:
        news_reaction = _classify_news_price_reaction(None, current_bar)
        # Populate catalysts with related articles (marked clearly) if available
        display_catalysts = []
        for r_item in related_articles[:3]:
            t = str(r_item.get("title") or "").strip()
            if t:
                r_rel = float(r_item.get("entity_relevance", 0.2))
                display_catalysts.append({
                    "title": t,
                    "published_at": r_item.get("published_at") or "Mới cập nhật",
                    "source": r_item.get("source") or "Tin công ty thành viên",
                    "url": r_item.get("article_url") or "",
                    "sentiment": "NEU",
                    "badge": "Tin công ty liên quan / Không tính điểm",
                    "entity_relevance": round(r_rel, 3),
                    "entity_relevance_pct": round(r_rel * 100.0, 1),
                    "novelty_score": 0.5,
                    "novelty_score_pct": 50.0,
                    "tone_score": 50.0,
                    "positive_keywords": [],
                    "negative_keywords": [],
                })

        return {
            "score": 50.0,
            "news_tone_score": None,
            "news_attention_score": 0.0,
            "news_adjustment": 0.0,
            "label": "Không có tin tức trực tiếp hợp lệ",
            "total_articles": len(raw_articles),
            "valid_direct_articles": 0,
            "news_reaction": news_reaction,
            "catalysts": display_catalysts,
        }

    mean_impact = sum(story_impacts) / len(story_impacts)
    news_tone_score = round(min(max(50.0 + 35.0 * mean_impact, 15.0), 92.0), 1)
    news_attention_score = round(min(len(clustered) * 25.0, 100.0), 1)
    attention_factor = min(max(news_attention_score / 50.0, 0.5), 1.2)
    raw_adjustment = (news_tone_score - 50.0) * 0.25 * attention_factor

    news_reaction = _classify_news_price_reaction(news_tone_score, current_bar)
    
    # Apply price reaction scaling to news_adjustment
    if news_reaction == "GOOD_NEWS_DISTRIBUTION_RISK":
        news_adjustment = round(-abs(raw_adjustment) * 0.5, 1)
    elif news_reaction == "BAD_NEWS_CAPITULATION_WATCH":
        news_adjustment = round(abs(raw_adjustment) * 0.5, 1)
    elif news_reaction == "NEWS_UNCONFIRMED":
        news_adjustment = round(raw_adjustment * 0.5, 1)
    else:
        news_adjustment = round(min(max(raw_adjustment, -10.0), 10.0), 1)

    if news_tone_score >= 68:
        label = "Tích cực — Nhiều thông tin hỗ trợ trực tiếp"
    elif news_tone_score >= 56:
        label = "Khá tích cực — Động lực tăng trưởng"
    elif news_tone_score >= 44:
        label = "Trung tính — Tin tức cân bằng"
    elif news_tone_score >= 32:
        label = "Thận trọng — Áp lực thông tin"
    else:
        label = "Tiêu cực — Cảnh báo rủi ro trực tiếp"

    return {
        "score": news_tone_score,
        "news_tone_score": news_tone_score,
        "news_attention_score": news_attention_score,
        "news_adjustment": news_adjustment,
        "label": label,
        "total_articles": len(raw_articles),
        "valid_direct_articles": len(valid_articles),
        "news_reaction": news_reaction,
        "catalysts": catalysts[:5],
    }


def _round_hose_tick(price: float) -> float:
    """Làm tròn bước giá theo quy định chuẩn của sàn HOSE:
    - Giá < 10,000 VND: Bước giá 10 VND
    - Giá 10,000 - 49,950 VND: Bước giá 50 VND
    - Giá >= 50,000 VND: Bước giá 100 VND
    """
    if price < 10000.0:
        step = 10.0
    elif price < 50000.0:
        step = 50.0
    else:
        step = 100.0
    return round(price / step) * step


def _generate_trade_setup(
    row: Any,
    state: str,
    signal: Optional[str],
    div_type: Optional[str],
    opportunity: float,
    risk: float,
    bullish_order: bool,
    is_spring: bool,
    is_sos: bool,
    roll_low20: Any,
    roll_high20: Any,
    vwap20: Any,
    disparity_score: Any,
    pattern_name: Optional[str] = None,
    action_code: str = "WATCH",
    signal_subtype: Optional[str] = None,
    signal_stage: Optional[str] = None,
    market_regime: str = "RANGE",
    veto_codes: Optional[list[str]] = None,
    watch_subtype: Optional[str] = None,
) -> dict[str, Any]:
    """Tạo lập kế hoạch giao dịch định lượng thực chiến cho nhà đầu tư."""
    close = float(row["close"])
    low_val = float(row["low"])
    high_val = float(row["high"])
    
    safe_low20 = float(roll_low20) if _finite(roll_low20) is not None else low_val
    safe_high20 = float(roll_high20) if _finite(roll_high20) is not None else high_val
    safe_vwap = float(vwap20) if _finite(vwap20) is not None else close
    safe_disparity = float(disparity_score) if _finite(disparity_score) is not None else 0.0

    ema20 = float(row["ema20"]) if _finite(row.get("ema20") if hasattr(row, "get") else getattr(row, "ema20", close)) is not None else close
    atr14 = float(row["atr14"]) if _finite(row.get("atr14") if hasattr(row, "get") else getattr(row, "atr14", close * 0.02)) is not None else close * 0.02
    aperture = float(row["aperture"]) if _finite(row.get("aperture") if hasattr(row, "get") else getattr(row, "aperture", 50.0)) is not None else 50.0
    pulse = float(row.get("pulse") if hasattr(row, "get") else getattr(row, "pulse", 0.0) or 0.0)
    flow = float(row.get("flow") if hasattr(row, "get") else getattr(row, "flow", 0.0) or 0.0)

    # 1. Determine Actionable Verdict & Tone based on Action Engine
    if action_code == "TEST_BUY" or signal_subtype == "BB1_SPRING_CONFIRM":
        verdict_code = "TEST_BUY"
        verdict_title = "MUA THĂM DÒ ĐÁY"
        verdict_badge = "MUA THĂM DÒ (20–30% VỐN)"
        verdict_tone = "expansion"
        position_size = "20–30% Tổng danh mục mục tiêu"
        advice = "Đã xác nhận hấp thụ cung đáy và lực cầu test thành công; mở vị thế mua thăm dò tại vùng hỗ trợ."
    elif action_code == "ADD_BUY" or signal_subtype in ("BB2_SOS_BREAKOUT", "BB3_LPS_PULLBACK"):
        if signal_subtype == "BB2_SOS_BREAKOUT":
            verdict_code = "ADD_BUY_SOS"
            verdict_title = "MUA GIA TĂNG BREAKOUT"
            verdict_badge = "MUA GIA TĂNG / VƯỢT ĐỈNH (30–40% VỐN)"
            verdict_tone = "expansion"
            position_size = "30–40% Vị thế / Gia tăng tỷ trọng theo Trend"
            advice = "Dòng tiền lớn đẩy giá bứt phá vượt cản (SOS); mở rộng vị thế khi có xung lực xác nhận."
        else:
            verdict_code = "ADD_BUY_LPS"
            verdict_title = "MUA GIA TĂNG PULLBACK"
            verdict_badge = "MUA KHI TEST HỖ TRỢ (30–35% VỐN)"
            verdict_tone = "expansion"
            position_size = "30–35% Vị thế / Mua điểm pullback lành mạnh"
            advice = "Giá test lại vùng hỗ trợ EMA20 với thanh khoản cạn kiệt (LPS); điểm mua gia tăng rủi ro thấp."
    elif action_code == "EXIT" or signal_subtype in ("BS1_CLIMAX_DISTRIBUTION", "BS2_SOW_BREAKDOWN"):
        if signal_subtype == "BS1_CLIMAX_DISTRIBUTION":
            verdict_code = "EXIT_CLIMAX"
            verdict_title = "CHỐT LỜI / THOÁT VỊ THẾ"
            verdict_badge = "BÁN CHỐT LỜI CAO TRÀO"
            verdict_tone = "contraction"
            position_size = "Bán 70–100% / Hiện thực hóa lợi nhuận"
            advice = "Xuất hiện cao trào mua đuổi hoặc nỗ lực phân phối vùng đỉnh; chủ động chốt lời bảo toàn vốn."
        else:
            verdict_code = "EXIT_BREAKDOWN"
            verdict_title = "CẮT LỖ / QUẢN TRỊ RỦI RO"
            verdict_badge = "BÁN CẮT LỖ / THỦNG NỀN"
            verdict_tone = "contraction"
            position_size = "Bán 100% / Đóng vị thế phòng ngừa rủi ro"
            advice = "Thủng nền hỗ trợ then chốt (SOW) với dòng tiền thoát ra mạnh; kỷ luật cắt lỗ dứt khoát."
    elif action_code == "TRIM":
        verdict_code = "TRIM"
        verdict_title = "HẠ TỶ TRỌNG PHÒNG THỦ"
        verdict_badge = "HẠ BỚT TỶ TRỌNG (BÁN 30–50%)"
        verdict_tone = "warning"
        position_size = "Hạ 30–50% Vị thế đang nắm giữ"
        advice = "Tín hiệu phân phối tiềm ẩn hoặc hưng phấn quá mức; hạ bớt tỷ trọng để kiểm soát rủi ro."
    elif action_code == "HOLD":
        verdict_code = "HOLD"
        verdict_title = "NẮM GIỮ THEO XU HƯỚNG"
        verdict_badge = "NẮM GIỮ THEO TREND"
        verdict_tone = "expansion"
        position_size = "Giữ 100% vị thế hiện tại / Nâng chặn lãi"
        advice = "Cổ phiếu duy trì cấu trúc tăng vững trên EMA20; tiếp tục nắm giữ và nâng dần mức chặn lãi."
    elif watch_subtype == "RECOVERY_BREAKOUT_WATCH":
        verdict_code = "WATCH"
        verdict_title = "THEO DÕI HỒI PHỤC DƯỚI MA200"
        verdict_badge = "THEO DÕI HỒI PHỤC (CHƯA VÀO TREND)"
        verdict_tone = "neutral"
        position_size = "0% Vị thế mới / Chưa xác nhận Trend vượt MA200"
        advice = "Cổ phiếu bứt phá nhưng nằm dưới EMA200 hoặc EMA200 còn giảm; theo dõi kiểm định, chưa vội mua theo xu hướng."
    else:
        verdict_code = "WATCH"
        verdict_title = "QUAN SÁT THỊ TRƯỜNG"
        verdict_badge = "QUAN SÁT CHỜ TÍN HIỆU XÁC NHẬN"
        verdict_tone = "neutral"
        position_size = "0% Vị thế mới / Theo dõi tạo nền"
        advice = "Chưa có tín hiệu xác nhận dòng tiền đủ chuẩn; kiên nhẫn quan sát chờ điểm vào an toàn."

    # 2. Price Step Rounding (Official HOSE Market Standards: 10 / 50 / 100 VND)
    step = 10.0 if close < 10000 else (50.0 if close < 50000 else 100.0)

    # 3. Entry Zone Calculation
    if verdict_code in ("TEST_BUY", "ADD_BUY_SOS", "ADD_BUY_LPS"):
        entry_low = _round_hose_tick(min(close * 0.985, ema20 * 0.99))
        entry_high = _round_hose_tick(max(close * 1.012, ema20 * 1.015))
    elif verdict_code == "HOLD":
        entry_low = _round_hose_tick(ema20 * 0.99)
        entry_high = _round_hose_tick(close * 1.01)
    else:
        entry_low = _round_hose_tick(min(safe_low20, close * 0.97))
        entry_high = _round_hose_tick(min(close, ema20))

    if entry_low >= entry_high:
        entry_high = _round_hose_tick(entry_low + step)

    # 4. Stop Loss / Invalidation Trigger Calculation
    base_stop = max(safe_low20 * 0.985, close - 1.8 * atr14)
    stop_loss_price = _round_hose_tick(base_stop)
    if stop_loss_price >= close * 0.985:
        stop_loss_price = _round_hose_tick(close * 0.95)
    stop_loss_pct = round((stop_loss_price / close - 1.0) * 100, 1)

    # 5. Target 1 & Target 2 (Strict Risk/Reward Calculation)
    risk_amt = max(close - stop_loss_price, close * 0.035)
    target_1_price = _round_hose_tick(close + max(risk_amt * 2.0, (safe_high20 - close) * 0.7))
    if target_1_price <= close * 1.03:
        target_1_price = _round_hose_tick(close + risk_amt * 2.0)
    target_1_pct = round((target_1_price / close - 1.0) * 100, 1)

    target_2_price = _round_hose_tick(close + max(risk_amt * 3.5, safe_high20 * 1.05 - close))
    if target_2_price <= target_1_price:
        target_2_price = _round_hose_tick(target_1_price + risk_amt * 1.5)
    target_2_pct = round((target_2_price / close - 1.0) * 100, 1)

    rr_ratio = round((target_1_price - close) / risk_amt, 1) if risk_amt > 0 else 2.0

    # 6. Wyckoff / Volume Action Phase Determination
    if pattern_name == "NEWS_EUPHORIA_DISTRIBUTION":
        wyckoff_phase = "Pha Phân phối: Bẫy xả hàng tin tốt bùng nổ (News Euphoria Distribution)"
    elif pattern_name == "UPTHRUST":
        wyckoff_phase = "Pha Phân phối: Bẫy giá vượt đỉnh thất bại (Upthrust - UTAD)"
    elif pattern_name == "BUYING_CLIMAX":
        wyckoff_phase = "Pha Phân phối: Cao trào mua đuổi đỉnh (Buying Climax)"
    elif pattern_name == "NEWS_SOS":
        wyckoff_phase = "Pha D/E: Bứt phá thực chất trên nền tin tốt (News SOS Breakout)"
    elif pattern_name == "STOPPING_VOLUME":
        wyckoff_phase = "Pha A/B: Nến Stopping Volume (Lực bán bị chặn đứng)"
    elif pattern_name == "HIGH_VOLUME_ABSORPTION":
        wyckoff_phase = "Pha B: Gom hàng hấp thụ (High-Volume Absorption Proxy)"
    elif pattern_name == "THREE_BAR_REVERSAL":
        wyckoff_phase = "Pha C: Mô hình 3 nến đảo chiều đáy (Three-Bar Reversal)"
    elif pattern_name == "CAPITULATION_ABSORBED":
        wyckoff_phase = "Pha A: Cao trào bán tháo đã hấp thụ (Capitulation Absorbed)"
    elif is_spring or (state == "BOTTOM_WATCH" and pulse > flow):
        wyckoff_phase = "Pha C: Rũ bỏ cạn cung (Wyckoff Spring Test)"
    elif is_sos or (state == "CONFIRMED_EXPANSION" and bullish_order):
        wyckoff_phase = "Pha D/E: Dòng tiền bứt phá (Sign of Strength - SOS)"
    elif state == "EARLY_EXPANSION":
        wyckoff_phase = "Pha D: Dòng tiền vào gom vượt cản (First Markup)"
    elif state == "BOTTOM_WATCH":
        wyckoff_phase = "Pha B: Tích lũy xây nền & Hấp thụ áp lực bán"
    elif state in ("OVEREXTENDED", "DISTRIBUTION_CONTRACTION"):
        wyckoff_phase = "Pha Phân phối: Áp lực chốt lời (Buying Climax / UTAD)"
    elif state == "FALLING_CONTRACTION":
        wyckoff_phase = "Pha A: Dò điểm dừng rơi (Stopping Volume Phase)"
    else:
        wyckoff_phase = "Pha Tích lũy / Tái tích lũy nền giá"

    # 7. Disparity Status
    if safe_disparity >= 20.0:
        disparity_status = "Gom đáy trong sợ hãi (Siêu chiết khấu)"
    elif safe_disparity <= -20.0:
        disparity_status = "Bẫy phân phối đỉnh (Đám đông FOMO)"
    else:
        disparity_status = "Cân bằng đồng thuận"

    return {
        "verdict_code": verdict_code,
        "verdict_title": verdict_title,
        "verdict_badge": verdict_badge,
        "verdict_tone": verdict_tone,
        "entry_zone": f"{entry_low:,.0f} – {entry_high:,.0f} đ",
        "entry_low": float(entry_low),
        "entry_high": float(entry_high),
        "stop_loss_price": float(stop_loss_price),
        "stop_loss_pct": stop_loss_pct,
        "stop_loss_text": f"{stop_loss_price:,.0f} đ ({stop_loss_pct:+.1f}%)",
        "target_1_price": float(target_1_price),
        "target_1_pct": target_1_pct,
        "target_1_text": f"{target_1_price:,.0f} đ ({target_1_pct:+.1f}%)",
        "target_2_price": float(target_2_price),
        "target_2_pct": target_2_pct,
        "target_2_text": f"{target_2_price:,.0f} đ ({target_2_pct:+.1f}%)",
        "target_3_price": float(target_2_price),
        "target_3_pct": target_2_pct,
        "target_3_text": f"{target_2_price:,.0f} đ ({target_2_pct:+.1f}%)",
        "rr_ratio": rr_ratio,
        "rr_ratio_text": f"1 : {rr_ratio:.1f}",
        "position_size": position_size,
        "wyckoff_phase": wyckoff_phase,
        "institutional_cost": f"{safe_vwap:,.0f} đ" if safe_vwap > 0 else "—",
        "institutional_cost_price": float(safe_vwap),
        "disparity_score": _rounded(safe_disparity, 1),
        "disparity_status": disparity_status,
        "action_advice": advice,
    }


def calculate_indicator(
    frame: pd.DataFrame,
    benchmark: Optional[pd.DataFrame] = None,
    news_sentiment: Optional[dict[str, Any]] = None,
) -> pd.DataFrame:
    """Tính toàn bộ chuỗi chỉ báo bằng dữ liệu quá khứ tại từng phiên."""
    result = _normalise_frame(frame)
    if len(result) < MIN_BAR_LIMIT:
        return result

    close = result["close"]
    volume = result["volume"]
    result["ema20"] = close.ewm(span=20, adjust=False).mean()
    result["ema50"] = close.ewm(span=50, adjust=False).mean()
    result["ema100"] = close.ewm(span=100, adjust=False).mean()
    result["ema200"] = close.ewm(span=200, adjust=False).mean()
    result["rsi14"] = _rsi(close)
    result["atr14"] = _atr(result)
    result["cmf20"] = _cmf(result)
    result["mfi14"] = _mfi(result)
    result["rs20"] = _align_relative_strength(result, benchmark)

    # MACD(12,26,9) — causal, dùng để detect phân kỳ giá/MACD Histogram
    _macd_line, _macd_signal, _macd_hist = _macd(close)
    result["macd_line"] = _macd_line.round(4)
    result["macd_signal"] = _macd_signal.round(4)
    result["macd_hist"] = _macd_hist.round(4)

    # 1. VSA Candle Geometry & Causal Relative Volume (RVol)
    high_low_diff = (result["high"] - result["low"]).replace(0, np.nan)
    clv = ((2.0 * result["close"] - result["high"] - result["low"]) / high_low_diff).fillna(0.0)
    
    # Causal RVOL20 & Volume Trends
    vol_sma20_prev = volume.rolling(20, min_periods=10).mean().shift(1).replace(0, np.nan)
    rvol = (volume / vol_sma20_prev).fillna(volume / volume.rolling(20, min_periods=5).mean().replace(0, np.nan)).clip(0.1, 6.0).fillna(1.0)
    vol_sma5 = volume.rolling(5, min_periods=3).mean()
    vol_trend5_20 = (vol_sma5 / vol_sma20_prev).fillna(1.0).clip(0.1, 5.0)
    vol_pct252 = volume.rolling(252, min_periods=20).rank(pct=True).fillna(0.5) * 100.0
    
    # Liquidity Tiers Metrics (60-session median traded value)
    traded_value = close * volume
    med_val_60 = traded_value.rolling(60, min_periods=10).median().fillna(1e9)
    zero_vol_ratio = (volume == 0).rolling(60, min_periods=10).mean().fillna(0.0)
    # Liquid >= 1B, Medium 1B-5B, Low 300M-1B (Watch only), Illiquid < 300M or zero_vol > 20%
    liquidity_pass = (med_val_60 >= 1_000_000_000) & (zero_vol_ratio <= 0.20)
    liquidity_watch_only = (med_val_60 >= 300_000_000) & (med_val_60 < 1_000_000_000) & (zero_vol_ratio <= 0.20)
    liquidity_blocked = (med_val_60 < 300_000_000) | (zero_vol_ratio > 0.20)
    
    # Corporate Action Gap Detection
    prev_close_bar = close.shift(1)
    overnight_gap = ((result["open"] - prev_close_bar) / prev_close_bar).abs().fillna(0.0)
    corp_gap_flag = (overnight_gap >= 0.15) & (rvol <= 2.5)
    is_corp_action_window = corp_gap_flag.rolling(3, min_periods=1).max().astype(bool)
    
    # Candle Wick & Body Geometry
    lower_wick = (np.minimum(result["open"], result["close"]) - result["low"]).clip(lower=0)
    upper_wick = (result["high"] - np.maximum(result["open"], result["close"])).clip(lower=0)
    body_size = (result["close"] - result["open"]).abs()
    
    result["lower_wick_ratio"] = (lower_wick / high_low_diff).fillna(0.0).clip(0.0, 1.0)
    result["upper_wick_ratio"] = (upper_wick / high_low_diff).fillna(0.0).clip(0.0, 1.0)
    result["body_ratio"] = (body_size / high_low_diff).fillna(0.0).clip(0.0, 1.0)
    
    spread_ratio = (result["high"] - result["low"]) / result["atr14"].replace(0, np.nan)
    effort_result = (rvol / (spread_ratio.clip(0.3, 3.0) + 0.1)).fillna(1.0)
    result["effort_result"] = effort_result
    result["vol_trend5_20"] = vol_trend5_20
    result["vol_pct252"] = vol_pct252
    result["liquidity_pass"] = liquidity_pass
    result["liquidity_watch_only"] = liquidity_watch_only
    result["liquidity_blocked"] = liquidity_blocked
    result["is_corp_action_window"] = is_corp_action_window
    vsa_absorption = clv * np.sqrt(rvol) * effort_result.clip(0.4, 2.5)

    # ─── 1. Five Independent Factor Groups (Smart Money Start V2) ─────────
    # Factor Group 1: Directional Flow (30%)
    cmf_z = _rolling_robust_z(result["cmf20"])
    cmf_score = _z_to_score_0_100(cmf_z)
    signed_vol_raw = clv * np.sqrt(rvol)
    signed_vol_z = _rolling_robust_z(signed_vol_raw)
    signed_vol_score = _z_to_score_0_100(signed_vol_z)
    up_vol = volume.where(close > close.shift(1), 0.0).rolling(20, min_periods=5).sum()
    total_vol = volume.rolling(20, min_periods=5).sum().replace(0, np.nan)
    up_vol_ratio = (up_vol / total_vol).fillna(0.5) * 100.0
    group_directional_flow = (0.40 * cmf_score + 0.35 * signed_vol_score + 0.25 * up_vol_ratio).clip(0.0, 100.0)

    # Factor Group 2: Effort vs Result (25%)
    spread_ratio = ((result["high"] - result["low"]) / result["atr14"].replace(0, np.nan)).fillna(1.0)
    effort_result = (rvol / (spread_ratio.clip(0.3, 3.0) + 0.1)).fillna(1.0)
    result["effort_result"] = effort_result
    vsa_base_z = _rolling_robust_z(clv * effort_result.clip(0.4, 2.5))
    effort_base_score = _z_to_score_0_100(vsa_base_z)
    abs_bonus = np.where((result["lower_wick_ratio"] >= 0.25) & (clv >= 0.15) & (rvol >= 1.25), 15.0, 0.0)
    dist_penalty = np.where((result["upper_wick_ratio"] >= 0.30) & (clv <= -0.15) & (rvol >= 1.35), -15.0, 0.0)
    sos_bonus = np.where((close > result["open"]) & (clv >= 0.45) & (rvol >= 1.25) & (spread_ratio >= 1.0), 15.0, 0.0)
    group_effort_vs_result = (effort_base_score + abs_bonus + dist_penalty + sos_bonus).clip(0.0, 100.0)
    is_selling_climax_raw = (rvol >= 2.0) & (spread_ratio >= 1.4) & (clv <= -0.35)
    group_effort_vs_result = pd.Series(np.where(is_selling_climax_raw, np.minimum(group_effort_vs_result, 35.0), group_effort_vs_result), index=result.index)

    # Factor Group 3: Price Acceptance (20%)
    typical_price = (result["high"] + result["low"] + result["close"]) / 3.0
    rolling_vol20 = volume.rolling(20, min_periods=5).sum().replace(0, np.nan)
    rvwap20 = ((typical_price * volume).rolling(20, min_periods=5).sum() / rolling_vol20).fillna(close)
    result["rvwap20"] = rvwap20
    result["vwap20"] = rvwap20  # backward compatibility alias
    rvwap_dist = ((close - rvwap20) / result["atr14"].replace(0, np.nan)).fillna(0.0)
    rvwap_dist_score = _z_to_score_0_100(_rolling_robust_z(rvwap_dist))
    persistence_10 = (close >= rvwap20).astype(float).rolling(10, min_periods=3).mean().fillna(0.5) * 100.0
    rvwap_slope = ((rvwap20 - rvwap20.shift(10)) / (result["atr14"].replace(0, np.nan) * 2.0)).fillna(0.0)
    rvwap_slope_score = _z_to_score_0_100(_rolling_robust_z(rvwap_slope))
    group_price_acceptance = (0.45 * rvwap_dist_score + 0.35 * persistence_10 + 0.20 * rvwap_slope_score).clip(0.0, 100.0)

    # Factor Group 4: Relative Strength & Structure (15%)
    has_valid_benchmark = bool(benchmark is not None and not benchmark.empty and "close" in benchmark and result["rs20"].notna().any())
    if has_valid_benchmark:
        rs_z = _rolling_robust_z(result["rs20"].fillna(0.0))
        rs_score = _z_to_score_0_100(rs_z)
        ema20_slope_10 = ((result["ema20"] - result["ema20"].shift(10)) / (result["atr14"].replace(0, np.nan) * 2.0)).fillna(0.0)
        struct_score = _z_to_score_0_100(_rolling_robust_z(ema20_slope_10))
        group_structure_rs = (0.60 * rs_score + 0.40 * struct_score).clip(0.0, 100.0)
        w1, w2, w3, w4, w5 = 0.30, 0.25, 0.20, 0.15, 0.10
    else:
        group_structure_rs = pd.Series(50.0, index=result.index)
        raw_w = [0.30, 0.25, 0.20, 0.10]
        tot_w = sum(raw_w)
        w1 = 0.30 / tot_w
        w2 = 0.25 / tot_w
        w3 = 0.20 / tot_w
        w4 = 0.0
        w5 = 0.10 / tot_w

    # Factor Group 5: Participation & Persistence (10%)
    price_pct_change = close.pct_change(fill_method=None).fillna(0.0)
    direction = np.sign(close.diff()).fillna(0)
    obv = (volume * direction).cumsum()
    vpt = (volume * price_pct_change).cumsum()
    vpt_slope = vpt.diff(10) / volume.rolling(10, min_periods=10).sum().replace(0, np.nan)
    result["obv"] = obv
    result["vpt"] = vpt
    result["volume_ratio20"] = rvol
    result["clv"] = clv
    mfi_score = result["mfi14"].fillna(50.0)
    vpt_slope_z = _rolling_robust_z(vpt_slope)
    vpt_score = _z_to_score_0_100(vpt_slope_z)
    vol_trend_score = ((vol_trend5_20.clip(0.4, 2.0) - 0.4) / 1.6 * 100.0).clip(0.0, 100.0)
    group_participation = (0.35 * mfi_score + 0.35 * vpt_score + 0.30 * vol_trend_score).clip(0.0, 100.0)

    result["group_directional_flow"] = group_directional_flow.round(2)
    result["group_effort_vs_result"] = group_effort_vs_result.round(2)
    result["group_price_acceptance"] = group_price_acceptance.round(2)
    result["group_structure_rs"] = group_structure_rs.round(2)
    result["group_participation"] = group_participation.round(2)

    # ─── 2. Smart Money Score & Tri-EMA Ribbon ──────────────────────────
    smart_money_score = (
        w1 * group_directional_flow
        + w2 * group_effort_vs_result
        + w3 * group_price_acceptance
        + w4 * group_structure_rs
        + w5 * group_participation
    ).clip(0.0, 100.0)

    pulse = smart_money_score.ewm(span=5, adjust=False).mean()
    flow = smart_money_score.ewm(span=13, adjust=False).mean()
    core = smart_money_score.ewm(span=34, adjust=False).mean()
    center = (pulse + flow + core) / 3.0

    result["smart_money_score"] = smart_money_score.round(2)
    result["pulse_pct"] = pulse.clip(0.0, 100.0).round(2)
    result["flow_pct"] = flow.clip(0.0, 100.0).round(2)
    result["core_pct"] = core.clip(0.0, 100.0).round(2)
    result["center_pct"] = center.clip(0.0, 100.0).round(2)

    # Normalized around 0 for backward-compatible calculations:
    result["pulse"] = ((pulse - 50.0) / 15.0).round(4)
    result["flow"] = ((flow - 50.0) / 15.0).round(4)
    result["core"] = ((core - 50.0) / 15.0).round(4)
    result["center"] = ((center - 50.0) / 15.0).round(4)
    result["money_pressure"] = ((smart_money_score - 50.0) / 15.0).round(4)

    # ─── 3. Weekly Completed Regime & Multi-Timeframe Confirmation ───────
    weekly_trends, weekly_regimes = _compute_completed_weekly_regime(result)
    result["weekly_trend"] = weekly_trends
    result["weekly_regime"] = weekly_regimes

    # ─── 4. Market Structure Detection (Causal 3-Bar Confirmed Pivots) ───
    struct_events, sweeps = _detect_market_structure(result, result["atr14"])
    result["structure_event"] = struct_events
    result["liquidity_sweep"] = sweeps

    # ─── 5. Smart Money Confidence (0-100) ──────────────────────────────
    data_qual_score = np.where(len(result) >= 250, 100.0, (len(result) / 250.0) * 100.0)
    if not has_valid_benchmark:
        data_qual_score = data_qual_score * 0.70
    liq_score = np.where(
        med_val_60 >= 20_000_000_000, 100.0,
        np.where(
            med_val_60 >= 5_000_000_000, 80.0,
            np.where(
                med_val_60 >= 1_000_000_000, 50.0,
                20.0
            )
        )
    )
    liq_score = np.where(zero_vol_ratio > 0.10, liq_score * 0.40, liq_score)
    factor_df = pd.DataFrame({
        "f1": group_directional_flow,
        "f2": group_effort_vs_result,
        "f3": group_price_acceptance,
        "f4": group_structure_rs if has_valid_benchmark else group_directional_flow,
        "f5": group_participation,
    })
    factor_std = factor_df.std(axis=1).fillna(15.0)
    consensus_score = (100.0 - factor_std * 2.5).clip(10.0, 100.0)
    weekly_aligned_mask = (
        ((weekly_trends == "BULLISH") & (result["pulse_pct"] >= result["flow_pct"]))
        | ((weekly_trends == "BEARISH") & (result["pulse_pct"] <= result["flow_pct"]))
        | (weekly_trends == "NEUTRAL")
    )
    weekly_align_score = np.where(weekly_aligned_mask, 100.0, 45.0)
    sm_conf = (
        0.30 * data_qual_score
        + 0.25 * liq_score
        + 0.25 * consensus_score
        + 0.20 * weekly_align_score
    ).clip(0.0, 100.0).round(1)
    result["smart_money_confidence"] = sm_conf

    # ─── 6. Continuous Outflow Pressure (0-100) ──────────────────────────
    outflow_dir = (100.0 - group_directional_flow).clip(0.0, 100.0)
    outflow_ribbon = np.where(pulse < flow, 60.0, 0.0) + np.where(flow < core, 40.0, 0.0)
    outflow_rvwap = np.where(close < rvwap20, 100.0, 0.0)
    outflow_effort = np.where(clv <= 0, ((-clv) * np.sqrt(rvol)).clip(0.0, 2.0) / 2.0 * 100.0, 0.0)
    outflow_structure = np.where(close < result["ema50"], 100.0, np.where(close < result["ema20"], 50.0, 0.0))
    outflow_raw = (
        0.30 * outflow_dir
        + 0.25 * outflow_ribbon
        + 0.20 * outflow_rvwap
        + 0.15 * outflow_effort
        + 0.10 * outflow_structure
    )
    outflow_pressure = pd.Series(outflow_raw, index=result.index).ewm(span=3, adjust=False).mean().clip(0.0, 100.0).round(1)
    result["outflow_pressure"] = outflow_pressure
    result["smart_money_outflow_score"] = outflow_pressure

    # 7. Technical Crowd Emotion v4 (0-100) — Separated from Smart Money
    atr_safe = result["atr14"].replace(0, np.nan)
    dist_ema20 = ((result["close"] - result["ema20"]) / atr_safe).fillna(0.0)
    dist_ema50 = ((result["close"] - result["ema50"]) / atr_safe).fillna(0.0)

    # 7.1 Momentum Score (40%)
    ret5_atr = ((close - close.shift(5)) / atr_safe).fillna(0.0)
    score_ret5 = ((ret5_atr + 3.0) / 6.0).clip(0.0, 1.0)

    ret20_atr = ((close - close.shift(20)) / (atr_safe * 2.0)).fillna(0.0)
    score_ret20 = ((ret20_atr + 3.0) / 6.0).clip(0.0, 1.0)

    score_rsi = ((result["rsi14"] - 20.0) / 60.0).clip(0.0, 1.0)
    score_dist20 = ((dist_ema20 + 2.5) / 5.0).clip(0.0, 1.0)
    score_dist50 = ((dist_ema50 + 3.0) / 6.0).clip(0.0, 1.0)

    cummax252 = close.rolling(252, min_periods=20).max()
    drawdown_252 = ((close - cummax252) / cummax252.replace(0, np.nan)).fillna(0.0)
    score_dd = ((drawdown_252 + 0.35) / 0.40).clip(0.0, 1.0)

    emotion_momentum = (
        0.25 * score_rsi
        + 0.25 * score_ret5
        + 0.20 * score_ret20
        + 0.15 * score_dist20
        + 0.10 * score_dist50
        + 0.05 * score_dd
    ) * 100.0

    # 7.2 Directional Volume Score (25%)
    signed_rvol_clv = ((rvol * clv + 2.0) / 4.0).clip(0.0, 1.0)
    vpt_z = _robust_z(vpt_slope)
    vpt_slope_score = (1.0 / (1.0 + np.exp(-1.5 * vpt_z))).clip(0.0, 1.0)

    close_diff = close.diff().fillna(0.0)
    vol_rank = vol_pct252 / 100.0
    ret_dir_vol = np.where(close_diff > 0, 0.5 + 0.5 * vol_rank, np.where(close_diff < 0, 0.5 - 0.5 * vol_rank, 0.5))
    ret_dir_vol_score = pd.Series(ret_dir_vol, index=result.index).clip(0.0, 1.0)

    emotion_volume = (
        0.45 * signed_rvol_clv
        + 0.30 * vpt_slope_score
        + 0.25 * ret_dir_vol_score
    ) * 100.0

    crowd_attention_score = (rvol / 3.0).clip(0.0, 1.0) * 100.0

    # 7.3 Downside Fear / Calm Score (20%)
    downside_ret = close.pct_change(fill_method=None).fillna(0.0).clip(upper=0.0)
    downside_semivol10 = downside_ret.rolling(10, min_periods=5).std().fillna(0.0)
    score_semivol = (1.0 - (downside_semivol10 / 0.035).clip(0.0, 1.0))

    atr_sma20 = atr_safe.rolling(20, min_periods=10).mean().replace(0, np.nan)
    atr_expansion = (atr_safe / atr_sma20).fillna(1.0).clip(0.5, 2.5)
    down_day = (close < close.shift(1)).astype(float)
    score_atr_down = (1.0 - ((atr_expansion - 0.8) / 1.4).clip(0.0, 1.0) * down_day).clip(0.0, 1.0)

    prev_close_bar = close.shift(1).fillna(close)
    gap_down = ((prev_close_bar - result["open"]).clip(lower=0.0) / atr_safe).fillna(0.0)
    score_gap = (1.0 - (gap_down / 1.5).clip(0.0, 1.0))

    down_count_5 = (close < close.shift(1)).rolling(5, min_periods=3).sum().fillna(2.5)
    score_down_count = (1.0 - (down_count_5 / 5.0).clip(0.0, 1.0))

    roll_low20 = result["low"].rolling(20, min_periods=5).min()
    roll_high20 = result["high"].rolling(20, min_periods=5).max()
    rolling_low_50 = result["low"].rolling(50, min_periods=10).min()
    rolling_high_50 = result["high"].rolling(50, min_periods=10).max()
    ema50_slope_20 = (result["ema50"] - result["ema50"].shift(20)) / result["ema50"].shift(20).replace(0, np.nan)
    ema200_slope_20 = (result["ema200"] - result["ema200"].shift(20)) / result["ema200"].shift(20).replace(0, np.nan)
    ema100_slope_20 = (result["ema100"] - result["ema100"].shift(20)) / result["ema100"].shift(20).replace(0, np.nan)

    roll_low20_prev = roll_low20.shift(1).fillna(result["low"])
    is_20d_breakdown = (result["low"] < roll_low20_prev).astype(float)
    score_breakdown = 1.0 - is_20d_breakdown

    emotion_volatility = (
        0.30 * score_semivol
        + 0.25 * score_atr_down
        + 0.20 * score_gap
        + 0.15 * score_down_count
        + 0.10 * score_breakdown
    ) * 100.0

    # 7.4 EMA Structure Score (15%)
    c_gt_ema20 = (close > result["ema20"]).astype(float) * 20.0
    ema20_gt_ema50 = (result["ema20"] > result["ema50"]).astype(float) * 20.0
    c_gt_ema100 = (close > result["ema100"]).astype(float) * 20.0
    c_gt_ema200 = (close > result["ema200"]).astype(float) * 20.0
    ema50_slope_pos = (ema50_slope_20 > 0).astype(float) * 10.0
    ema200_slope_pos = (ema200_slope_20 > 0).astype(float) * 10.0
    emotion_structure = c_gt_ema20 + ema20_gt_ema50 + c_gt_ema100 + c_gt_ema200 + ema50_slope_pos + ema200_slope_pos

    # 7.5 Composite Raw Technical Crowd Emotion & Asymmetric Smoothing
    raw_technical = (
        0.40 * emotion_momentum.fillna(50.0)
        + 0.25 * emotion_volume.fillna(50.0)
        + 0.20 * emotion_volatility.fillna(50.0)
        + 0.15 * emotion_structure.fillna(50.0)
    ).fillna(50.0)

    smooth_technical = np.zeros(len(result), dtype=float)
    smooth_technical[0] = float(raw_technical.iloc[0]) if not np.isnan(raw_technical.iloc[0]) else 50.0
    ret_in_atr = ((close - close.shift(1)) / atr_safe).fillna(0.0)
    breakdown_consec = ((result["low"] < roll_low20.shift(1)) & (result["low"].shift(1) < roll_low20.shift(2))).astype(bool)

    for i in range(1, len(result)):
        prev_val = smooth_technical[i - 1]
        if np.isnan(prev_val):
            prev_val = 50.0
        curr_raw = float(raw_technical.iloc[i])
        if np.isnan(curr_raw):
            curr_raw = prev_val
        alpha = 0.55 if curr_raw < prev_val else 0.20
        val = alpha * curr_raw + (1.0 - alpha) * prev_val

        # Breakdown shock overrides (hard caps)
        if ret_in_atr.iloc[i] <= -1.5 and rvol.iloc[i] >= 1.5:
            val = min(val, 30.0)
        if breakdown_consec.iloc[i]:
            val = min(val, 25.0)

        smooth_technical[i] = np.clip(val, 5.0, 95.0)

    result["technical_emotion_score"] = pd.Series(smooth_technical, index=result.index).fillna(50.0).round(2)
    result["emotion_momentum"] = emotion_momentum.round(2)
    result["emotion_volume"] = emotion_volume.round(2)
    result["emotion_volatility"] = emotion_volatility.round(2)
    result["emotion_structure"] = emotion_structure.round(2)
    result["emotion_bigboys"] = result["core_pct"].round(2)

    # 8. Multi-Scale Disparity Score (Smart Money vs Crowd Emotion)
    disparity_momentum = ((result["pulse_pct"] - result["emotion_momentum"]) * 0.40).clip(-40, 40)
    disparity_volume = ((result["flow_pct"] - result["emotion_volume"]) * 0.30).clip(-30, 30)
    disparity_regime = ((result["core_pct"] - result["emotion_volatility"]) * 0.30).clip(-30, 30)
    raw_disparity = disparity_momentum + disparity_volume + disparity_regime
    result["disparity_score"] = raw_disparity.ewm(span=3, adjust=False).mean().clip(-100, 100).round(2)

    # Session Loop
    states: list[str] = []
    events: list[bool] = []
    signals: list[Optional[str]] = []
    signal_subtypes: list[Optional[str]] = []
    signal_stages: list[Optional[str]] = []
    pattern_codes: list[Optional[str]] = []
    market_regimes: list[str] = []
    regime_caps: list[float] = []
    market_emotion_scores: list[float] = []
    emotion_states: list[str] = []
    emotion_state_labels: list[str] = []
    emotion_state_colors: list[str] = []
    sm_phases: list[str] = []
    sm_phase_labels: list[str] = []
    sm_phase_colors: list[str] = []
    lifecycle_events: list[Optional[str]] = []
    outflow_events: list[Optional[str]] = []
    action_codes: list[str] = []
    watch_subtypes: list[Optional[str]] = []
    quality_scores: list[int] = []
    score_types: list[str] = []
    candidate_ids: list[Optional[str]] = []
    candidate_dates: list[Optional[str]] = []
    candidate_expires_at_list: list[Optional[str]] = []
    confirmation_dates: list[Optional[str]] = []
    invalidation_prices: list[Optional[float]] = []
    follow_through_conditions: list[Optional[str]] = []
    score_breakdowns: list[dict[str, Any]] = []
    reason_codes_list: list[list[str]] = []
    reason_labels_list: list[list[str]] = []
    veto_codes_list: list[list[str]] = []
    guard_flags_list: list[list[str]] = []
    volume_contexts: list[dict[str, Any]] = []
    divergences: list[Optional[str]] = []
    divergence_pcts: list[Optional[float]] = []
    opportunities: list[int] = []
    risks: list[int] = []
    bottom_confidences: list[int] = []
    conditions: list[list[str]] = []
    trade_setups: list[dict[str, Any]] = []

    last_event_by_state: dict[str, int] = {}
    last_signal_idx: dict[str, int] = {}
    last_subtype_idx: dict[str, int] = {}
    last_bull_div_idx = -999
    last_bear_div_idx = -999
    last_bottom_candidate_idx = -999

    active_bottom_candidate: Optional[dict[str, Any]] = None
    active_top_candidate: Optional[dict[str, Any]] = None
    active_outflow_episode = False

    current_sm_phase = "NEUTRAL"
    pending_sm_phase: Optional[str] = None
    pending_sm_count = 0
    swing_lows: list[dict[str, Any]] = []
    swing_highs: list[dict[str, Any]] = []

    for index in range(len(result)):
        row = result.iloc[index]
        cur_date_str = row["date"].strftime("%Y-%m-%d")
        recent_states_10 = states[max(0, index - 10):index]
        recent_states_20 = states[max(0, index - 20):index]
        overextended_recent = "OVEREXTENDED" in recent_states_10
        bottom_watch_recent = "BOTTOM_WATCH" in recent_states_20
        falling_recent = "FALLING_CONTRACTION" in recent_states_20
        pulse_cross_down = index > 0 and row["pulse"] < row["flow"] and result.iloc[index - 1]["pulse"] >= result.iloc[index - 1]["flow"]
        bullish_order = row["pulse"] > row["flow"] > row["core"]
        flow_core_rising = index >= 5 and row["flow"] > result.iloc[index - 5]["flow"] and row["core"] > result.iloc[index - 5]["core"]
        pulse_improving = index >= 5 and row["pulse"] > result.iloc[index - 5]["pulse"]
        cmf_improving = index >= 5 and _finite(row["cmf20"]) is not None and _finite(result.iloc[index - 5]["cmf20"]) is not None and row["cmf20"] > result.iloc[index - 5]["cmf20"]
        rsi_cross_30 = index > 0 and _finite(row["rsi14"]) is not None and _finite(result.iloc[index - 1]["rsi14"]) is not None and result.iloc[index - 1]["rsi14"] <= 30 < row["rsi14"]

        # High-Quality Confirmed-Pivot Divergence Detection (min 0.2 ATR delta, >=70 strength & confidence)
        div_type: Optional[str] = None
        div_pct: Optional[float] = None

        if index >= 6:
            cur_atr = float(result["atr14"].iloc[index]) if _finite(result["atr14"].iloc[index]) is not None and result["atr14"].iloc[index] > 0 else float(row["close"] * 0.02)
            p_idx = index - 3
            highs_arr = result["high"].values
            lows_arr = result["low"].values
            is_pivot_low = (lows_arr[p_idx] <= np.min(lows_arr[index-6:p_idx])) and (lows_arr[p_idx] <= np.min(lows_arr[p_idx+1:index+1]))
            is_pivot_high = (highs_arr[p_idx] >= np.max(highs_arr[index-6:p_idx])) and (highs_arr[p_idx] >= np.max(highs_arr[p_idx+1:index+1]))

            if is_pivot_low:
                swing_lows.append({
                    "idx": p_idx,
                    "date": result.iloc[p_idx]["date"].strftime("%Y-%m-%d"),
                    "price": float(lows_arr[p_idx]),
                    "pulse": float(result["pulse_pct"].iloc[p_idx]),
                    "rsi": float(result["rsi14"].iloc[p_idx]),
                    "macd_hist": float(result["macd_hist"].iloc[p_idx]),
                })

            if is_pivot_high:
                swing_highs.append({
                    "idx": p_idx,
                    "date": result.iloc[p_idx]["date"].strftime("%Y-%m-%d"),
                    "price": float(highs_arr[p_idx]),
                    "pulse": float(result["pulse_pct"].iloc[p_idx]),
                    "rsi": float(result["rsi14"].iloc[p_idx]),
                    "macd_hist": float(result["macd_hist"].iloc[p_idx]),
                })

            # Check Bullish Divergence on newly confirmed swing low
            if is_pivot_low and len(swing_lows) >= 2:
                curr_sl = swing_lows[-1]
                for prior_sl in reversed(swing_lows[:-1]):
                    bar_dist = curr_sl["idx"] - prior_sl["idx"]
                    if bar_dist < 5:
                        continue
                    if bar_dist > 60:
                        break

                    price_diff = curr_sl["price"] - prior_sl["price"]
                    if price_diff <= 0.2 * cur_atr:
                        pulse_diff = curr_sl["pulse"] - prior_sl["pulse"]
                        rsi_diff = curr_sl["rsi"] - prior_sl["rsi"]
                        macd_diff = curr_sl["macd_hist"] - prior_sl["macd_hist"]

                        has_p_bull = pulse_diff >= 3.0
                        has_r_bull = rsi_diff >= 2.5 and prior_sl["rsi"] <= 48.0
                        has_m_bull = macd_diff >= 0.0002 and prior_sl["macd_hist"] < 0.0

                        dcount = sum([has_p_bull, has_r_bull, has_m_bull])
                        if (dcount >= 2 and has_p_bull) or (has_p_bull and pulse_diff >= 6.0):
                            if index - last_bull_div_idx >= 10:
                                div_type = "TRIPLE_BULLISH" if dcount == 3 else ("DUAL_BULLISH" if has_r_bull else "MACD_RSI_BULLISH")
                                div_pct = _rounded(row["pulse_pct"], 1)
                                last_bull_div_idx = index
                                break

            # Check Bearish Divergence on newly confirmed swing high
            if is_pivot_high and len(swing_highs) >= 2 and not div_type:
                curr_sh = swing_highs[-1]
                for prior_sh in reversed(swing_highs[:-1]):
                    bar_dist = curr_sh["idx"] - prior_sh["idx"]
                    if bar_dist < 5:
                        continue
                    if bar_dist > 60:
                        break

                    price_diff = curr_sh["price"] - prior_sh["price"]
                    if price_diff >= -0.2 * cur_atr:
                        pulse_diff = curr_sh["pulse"] - prior_sh["pulse"]
                        rsi_diff = curr_sh["rsi"] - prior_sh["rsi"]
                        macd_diff = curr_sh["macd_hist"] - prior_sh["macd_hist"]

                        has_p_bear = pulse_diff <= -3.0
                        has_r_bear = rsi_diff <= -2.5 and prior_sh["rsi"] >= 52.0
                        has_m_bear = macd_diff <= -0.0002 and prior_sh["macd_hist"] > 0.0

                        dcount = sum([has_p_bear, has_r_bear, has_m_bear])
                        if (dcount >= 2 and has_p_bear) or (has_p_bear and pulse_diff <= -6.0):
                            if index - last_bear_div_idx >= 10:
                                div_type = "TRIPLE_BEARISH" if dcount == 3 else ("DUAL_BEARISH" if has_r_bear else "MACD_RSI_BEARISH")
                                div_pct = _rounded(row["pulse_pct"], 1)
                                last_bear_div_idx = index
                                break

        composite_divergence = div_type
        divergences.append(composite_divergence)
        divergence_pcts.append(div_pct)
        is_any_bull_div = composite_divergence in ("TRIPLE_BULLISH", "DUAL_BULLISH", "MACD_RSI_BULLISH", "BULLISH", "RSI_BULLISH", "MACD_BULLISH")
        is_any_bear_div = composite_divergence in ("TRIPLE_BEARISH", "DUAL_BEARISH", "MACD_RSI_BEARISH", "BEARISH", "RSI_BEARISH", "MACD_BEARISH")

        # ─── Smart Money 7-Phase State Machine (with 2-bar hysteresis) ────────
        p_val = float(row["pulse_pct"])
        f_val = float(row["flow_pct"])
        c_val = float(row["core_pct"])
        f_slope = f_val - float(result["flow_pct"].iloc[max(0, index - 2)])
        c_slope = c_val - float(result["core_pct"].iloc[max(0, index - 2)])
        is_above_rvwap = bool(row["close"] >= row["rvwap20"])
        cur_conf = float(row["smart_money_confidence"])

        if p_val > f_val > c_val and f_slope > 0 and c_slope >= 0 and is_above_rvwap:
            proposed_sm_phase = "MARKUP"
        elif p_val < f_val < c_val and f_slope < 0 and c_slope <= 0 and not is_above_rvwap:
            proposed_sm_phase = "MARKDOWN"
        elif p_val > f_val and f_slope > 0 and (is_above_rvwap or row["close"] > row["ema20"]) and cur_conf >= 60:
            proposed_sm_phase = "ACCUMULATION_CONFIRMED"
        elif p_val > f_val or p_val > 45 or (index > 0 and p_val > float(result["pulse_pct"].iloc[index - 1]) + 3.0):
            proposed_sm_phase = "ACCUMULATION_WATCH"
        elif p_val < f_val and f_slope < 0 and (not is_above_rvwap or p_val < 40) and cur_conf >= 60:
            proposed_sm_phase = "DISTRIBUTION_CONFIRMED"
        elif p_val < f_val or f_slope < 0:
            proposed_sm_phase = "DISTRIBUTION_WATCH"
        else:
            proposed_sm_phase = "NEUTRAL"

        if proposed_sm_phase == current_sm_phase:
            pending_sm_phase = None
            pending_sm_count = 0
        else:
            if proposed_sm_phase == pending_sm_phase:
                pending_sm_count += 1
                if pending_sm_count >= 2:
                    current_sm_phase = proposed_sm_phase
                    pending_sm_phase = None
                    pending_sm_count = 0
            else:
                pending_sm_phase = proposed_sm_phase
                pending_sm_count = 1

        sm_phases.append(current_sm_phase)
        sm_phase_labels.append(SMART_MONEY_PHASE_LABELS.get(current_sm_phase, current_sm_phase))
        sm_phase_colors.append(SMART_MONEY_PHASE_COLORS.get(current_sm_phase, "#94a3b8"))

        atr_val = row["atr14"] if _finite(row["atr14"]) is not None else row["close"] * 0.02
        distance_atr = (row["close"] - row["ema20"]) / (atr_val if atr_val > 0 else 1.0)

        # Volume Trends & Liquidity Metrics
        prev_low20 = roll_low20.iloc[index - 1] if index > 0 else row["low"]
        prev_high20 = roll_high20.iloc[index - 1] if index > 0 else row["high"]
        low_50_val = rolling_low_50.iloc[index] if index < len(rolling_low_50) else row["close"]
        dist_to_low = (row["close"] - low_50_val) / low_50_val if low_50_val > 0 else 0.0
        cur_ema50_slope20 = ema50_slope_20.iloc[index] if index < len(ema50_slope_20) else 0.0
        cur_ema200_slope20 = ema200_slope_20.iloc[index] if index < len(ema200_slope_20) else 0.0

        cur_clv = float(row["clv"])
        cur_rvol = float(row["volume_ratio20"])
        cur_effort = float(row["effort_result"])
        cur_lower_wick = float(row["lower_wick_ratio"])
        cur_upper_wick = float(row["upper_wick_ratio"])
        cur_body_ratio = float(row["body_ratio"])
        vol_trend_val = float(vol_trend5_20.iloc[index]) if index < len(vol_trend5_20) else 1.0
        vol_pct_val = float(vol_pct252.iloc[index]) if index < len(vol_pct252) else 50.0
        liq_pass_val = bool(liquidity_pass.iloc[index]) if index < len(liquidity_pass) else True
        corp_gap_val = bool(is_corp_action_window.iloc[index]) if index < len(is_corp_action_window) else False

        ema20_prev5 = result["ema20"].iloc[max(0, index - 5)]
        ema20_slope_5 = (row["ema20"] - ema20_prev5) / ema20_prev5 if ema20_prev5 > 0 else 0.0

        is_falling_knife_regime = bool(
            ema20_slope_5 <= -0.012
            and row["close"] < row["ema20"] * 0.97
            and row["close"] < row["ema50"]
            and row["pulse"] < row["flow"]
            and not is_any_bull_div
        )

        is_last_session = (index == len(result) - 1)
        effective_news = news_sentiment if is_last_session else None
        has_positive_news = bool(effective_news and float(effective_news.get("score", 50.0)) >= 65.0 and any(c.get("sentiment") == "POS" for c in effective_news.get("catalysts", [])))

        # ─── 1. Market Regime Gates (Plan v4 Specification) ──────────────────
        is_severe_downtrend = bool(
            row["close"] < row["ema20"] < row["ema50"] < row["ema100"]
            and row["close"] < row["ema200"]
            and cur_ema50_slope20 < 0.0
            and cur_ema200_slope20 < 0.0
        )
        is_downtrend_regime = bool(
            not is_severe_downtrend
            and (
                (row["close"] < row["ema200"] and row["ema20"] < row["ema50"] and cur_ema50_slope20 < 0.0)
                or (row["close"] < row["ema200"] and cur_ema200_slope20 < -0.008)
            )
        )
        is_longterm_bull = bool(
            row["close"] >= row["ema200"]
            and cur_ema50_slope20 >= 0.0
            and cur_ema200_slope20 >= -0.002
            and (row["ema20"] >= row["ema50"] or row["close"] >= prev_high20 * 0.995)
        )

        holds_ema20_3 = index >= 3 and all(result["close"].iloc[index - k] >= result["ema20"].iloc[index - k] for k in range(3))
        is_recovery_regime = bool(
            not is_severe_downtrend
            and not is_downtrend_regime
            and not is_longterm_bull
            and holds_ema20_3
            and ema20_slope_5 > 0.0
        )

        if is_severe_downtrend:
            market_regime = "SEVERE_DOWNTREND"
            regime_cap = 44.0
        elif is_downtrend_regime:
            market_regime = "DOWNTREND"
            regime_cap = 54.0
        elif is_recovery_regime:
            market_regime = "RECOVERY"
            regime_cap = 64.0
        elif is_longterm_bull:
            market_regime = "BULL_TREND"
            regime_cap = 100.0
        else:
            market_regime = "RANGE"
            regime_cap = 70.0

        # Market Emotion Score calculation with Regime Cap & News Adjustment
        tech_emotion = float(row["technical_emotion_score"])
        news_adj = float(effective_news.get("news_adjustment", 0.0)) if effective_news else 0.0
        unbounded_emotion = tech_emotion + (news_adj if is_last_session else 0.0)
        final_emotion_score = float(np.clip(min(unbounded_emotion, regime_cap), 5.0, 95.0))
        em_state, em_label, em_color = _map_emotion_state(final_emotion_score, market_regime)

        regime_caps.append(regime_cap)
        market_emotion_scores.append(round(final_emotion_score, 1))
        emotion_states.append(em_state)
        emotion_state_labels.append(em_label)
        emotion_state_colors.append(em_color)

        # Smart Money Outflow Lifecycle Evaluation
        outflow_sc = float(row["smart_money_outflow_score"])
        session_outflow_event = None

        if active_outflow_episode:
            # Check re-arm: Pulse > Flow for 5 sessions, CMF > 0, close > EMA20
            is_rearmed = (
                index >= 5
                and all(result["pulse"].iloc[index - k] > result["flow"].iloc[index - k] for k in range(5))
                and (_finite(row["cmf20"]) is not None and row["cmf20"] > 0.0)
                and row["close"] > row["ema20"]
            )
            if is_rearmed:
                active_outflow_episode = False
        else:
            if outflow_sc >= 75.0 and (row["close"] < row["ema20"] * 0.99 or row["close"] < row["ema50"] or cur_clv <= -0.25):
                session_outflow_event = "OUTFLOW_CONFIRMED"
                active_outflow_episode = True
            elif outflow_sc >= 60.0:
                session_outflow_event = "OUTFLOW_WATCH"
                active_outflow_episode = True

        outflow_events.append(session_outflow_event)

        # ─── 2. Bottom Candidate Patterns ────────────────────────────────────
        is_stopping_volume = bool(
            index >= 10
            and (falling_recent or row["close"] < row["ema20"] * 0.96 or dist_to_low <= 0.15)
            and cur_rvol >= 1.35
            and cur_lower_wick >= 0.28
            and cur_clv >= 0.18
            and (row["close"] > row["low"] * 1.006)
        )

        is_high_volume_absorption = bool(
            index >= 10
            and cur_rvol >= 1.65
            and cur_effort >= 1.18
            and cur_clv >= 0.05
            and (falling_recent or dist_to_low <= 0.20 or final_emotion_score <= 45)
            and not is_falling_knife_regime
        )

        is_high_volume_shakeout = bool(
            index >= 15
            and row["low"] <= prev_low20 * 0.995
            and cur_rvol >= 1.70
            and cur_lower_wick >= 0.35
            and (row["close"] >= prev_low20 or cur_clv >= 0.30)
        )

        is_low_volume_spring = bool(
            index >= 15
            and row["low"] <= prev_low20 * 1.005
            and row["close"] >= prev_low20
            and cur_rvol <= 0.90
            and (row["close"] >= row["open"] or cur_clv >= 0.25)
            and row["rsi14"] <= 55
        )

        is_wyckoff_spring = bool(
            index >= 15
            and row["low"] <= prev_low20 * 1.005
            and row["close"] >= prev_low20
            and (row["close"] >= row["open"] or cur_clv >= 0.20)
            and row["pulse"] > row["flow"]
            and row["rsi14"] <= 55
        )

        is_three_bar_reversal = False
        if index >= 3:
            bar_a = result.iloc[index - 2]
            bar_b = result.iloc[index - 1]
            bar_c = row
            is_bar_a_drop = bar_a["close"] < bar_a["open"] * 0.985
            is_bar_b_absorbed = bool(bar_b["lower_wick_ratio"] >= 0.25 and (bar_b["volume_ratio20"] >= 1.15 or bar_b["clv"] >= 0.10))
            is_bar_c_confirm = bool(bar_c["close"] > bar_c["open"] and bar_c["close"] >= (bar_a["open"] + bar_a["close"]) / 2.0 and bar_c["pulse"] > bar_c["flow"])
            if is_bar_a_drop and is_bar_b_absorbed and is_bar_c_confirm and (falling_recent or dist_to_low <= 0.25):
                is_three_bar_reversal = True

        is_volume_dryup_div = False
        if index >= 15:
            past_window_bars = result.iloc[max(0, index - 20):index - 3]
            min_past_low = past_window_bars["low"].min()
            min_past_idx = past_window_bars["low"].idxmin()
            if row["low"] <= min_past_low * 1.005 and min_past_idx in result.index:
                past_vol = result.loc[min_past_idx, "volume"]
                if row["volume"] < past_vol * 0.80 and row["close"] >= row["open"]:
                    is_volume_dryup_div = True

        is_capitulation_absorbed = bool(
            index >= 5
            and cur_rvol >= 2.0
            and final_emotion_score <= 35
            and (cur_lower_wick >= 0.28 or cur_clv >= 0.15 or (index > 0 and row["close"] > result.iloc[index - 1]["close"]))
        )

        is_capitulation_continuation = bool(
            cur_rvol >= 1.80
            and row["close"] < row["open"] * 0.97
            and cur_clv <= -0.40
            and row["low"] <= prev_low20 * 0.98
        )

        # ─── 3. Top / Distribution Candidate Patterns ─────────────────────────
        is_wyckoff_sos = bool(
            index >= 15
            and (row["close"] >= prev_high20 * 0.995 or row["close"] >= row["ema20"] * 1.025)
            and cur_rvol >= 1.25
            and cur_clv >= 0.50
            and bullish_order
        )

        has_sustained_uptrend = bool(
            (row["close"] > row["ema50"] * 1.15 or dist_to_low >= 0.30)
            and cur_ema50_slope20 >= 0.010
            and dist_to_low >= 0.20
        )

        is_buying_climax = bool(
            has_sustained_uptrend
            and cur_rvol >= 1.50
            and (cur_upper_wick >= 0.35 or cur_clv <= -0.15)
            and (final_emotion_score >= 70 or row["rsi14"] >= 72 or distance_atr >= 2.0)
        )

        is_upthrust = bool(
            index >= 15
            and row["high"] >= prev_high20 * 1.005
            and row["close"] < prev_high20 * 0.995
            and cur_clv <= -0.25
            and cur_rvol >= 1.25
            and (dist_to_low >= 0.20 or final_emotion_score >= 65)
        )

        is_effort_vs_result_dist = bool(
            has_sustained_uptrend
            and cur_rvol >= 1.60
            and cur_body_ratio <= 0.35
            and row["close"] <= row["open"] * 1.005
            and row["pulse"] < row["flow"]
        )

        is_news_euphoria_distribution = bool(
            has_sustained_uptrend
            and has_positive_news
            and cur_rvol >= 1.50
            and (cur_upper_wick >= 0.35 or cur_clv <= 0.0 or is_upthrust)
            and row["pulse"] < row["flow"]
        )

        is_news_sos = bool(
            has_positive_news
            and cur_rvol >= 1.50
            and row["close"] >= prev_high20 * 0.995
            and cur_clv >= 0.55
            and cur_upper_wick <= 0.22
            and row["pulse"] > row["flow"]
            and (len(states) == 0 or states[-1] not in ("OVEREXTENDED", "DISTRIBUTION_CONTRACTION"))
        )

        # ─── 4. Quality Scoring Calculators ──────────────────────────────────
        b_context = 0
        if dist_to_low <= 0.15: b_context += 10
        elif dist_to_low <= 0.25: b_context += 6
        if index >= 5 and row["close"] >= result["low"].iloc[max(0, index - 5):index + 1].min() * 1.005: b_context += 5
        if _finite(row["rs20"]) is not None and row["rs20"] > 0: b_context += 5
        b_context = min(20, b_context)

        b_candle = 0
        if cur_clv >= 0.30: b_candle += 10
        elif cur_clv >= 0.10: b_candle += 6
        if cur_lower_wick >= 0.28: b_candle += 6
        if cur_body_ratio <= 0.40 or row["close"] >= row["open"]: b_candle += 4
        b_candle = min(20, b_candle)

        b_volume = 0
        if cur_rvol >= 1.35: b_volume += 10
        elif cur_rvol <= 0.90 and is_low_volume_spring: b_volume += 10
        if cur_effort >= 1.15: b_volume += 8
        if vol_pct_val <= 35 or vol_trend_val <= 0.95 or cur_rvol >= 1.50: b_volume += 7
        b_volume = min(25, b_volume)

        b_money = 0
        if bullish_order: b_money += 8
        elif row["pulse"] >= row["flow"]: b_money += 5
        if row["cmf20"] > 0: b_money += 4
        if _finite(row["disparity_score"]) is not None and row["disparity_score"] >= 5.0: b_money += 3
        b_money = min(15, b_money)

        b_div = 10 if composite_divergence in ("TRIPLE_BULLISH", "DUAL_BULLISH", "MACD_RSI_BULLISH") else (7 if is_any_bull_div else (0 if is_any_bear_div else 4))
        b_div = min(10, b_div)

        b_regime = (5 if liq_pass_val else 0) + (5 if _finite(row["rs20"]) is not None and row["rs20"] > 0 else 2)
        b_regime = min(10, b_regime)

        bottom_quality_score = min(100, b_context + b_candle + b_volume + b_money + b_div + b_regime)

        bk_loc = (15 if (row["close"] >= prev_high20 * 0.995 or is_news_sos or is_wyckoff_sos) else 0) + (10 if (row["close"] > row["ema20"] > row["ema50"] * 0.99) else 0)
        bk_candle = (12 if cur_clv >= 0.45 else 6) + (8 if cur_body_ratio >= 0.40 and row["close"] > row["open"] else 0)
        bk_vol = (15 if cur_rvol >= 1.40 else 8) + (10 if vol_trend_val >= 1.15 else 0)
        bk_money = (10 if bullish_order else 5) + (5 if row["cmf20"] > 0.02 else 0)
        bk_regime = (8 if liq_pass_val else 0) + (7 if _finite(row["rs20"]) is not None and row["rs20"] > 0 else 0)
        breakout_quality_score = min(100, bk_loc + bk_candle + bk_vol + bk_money + bk_regime)

        pb_trend = (15 if (row["ema20"] > row["ema50"] and cur_ema50_slope20 > 0) else 0) + (10 if row["close"] >= row["ema50"] else 0)
        pb_loc = (12 if abs(row["close"] - row["ema20"]) / row["ema20"] <= 0.025 else 5) + (8 if row["close"] >= row["ema20"] else 0)
        pb_vol = (15 if cur_rvol <= 1.15 else 5) + (10 if vol_trend_val <= 0.98 else 0)
        pb_money = (10 if row["pulse"] > row["flow"] else 0) + (5 if cur_clv >= 0.20 else 0)
        pb_regime = (8 if liq_pass_val else 0) + (7 if dist_to_low >= 0.10 else 0)
        pullback_quality_score = min(100, pb_trend + pb_loc + pb_vol + pb_money + pb_regime)

        dist_loc = (15 if (dist_to_low >= 0.30 or row["close"] > row["ema50"] * 1.20) else 5) + (10 if (final_emotion_score >= 70 or row["rsi14"] >= 72) else 0)
        dist_candle = (12 if (cur_upper_wick >= 0.35 or cur_clv <= -0.15) else 0) + (8 if row["close"] <= row["open"] else 0)
        dist_vol = (15 if cur_rvol >= 1.50 else 5) + (10 if (cur_effort >= 1.30 or is_effort_vs_result_dist) else 0)
        dist_money = (12 if (row["pulse"] < row["flow"] or row["cmf20"] < 0) else 0) + (8 if (row["pulse"] < row["flow"]) else 0)
        dist_div = (10 if composite_divergence in ("TRIPLE_BEARISH", "DUAL_BEARISH", "MACD_RSI_BEARISH") else (7 if is_any_bear_div else 0))
        distribution_quality_score = min(100, dist_loc + dist_candle + dist_vol + dist_money + dist_div)

        bd_loc = (15 if (row["close"] < row["ema20"] * 0.985 and row["close"] < row["ema50"] * 1.01) else 0) + (10 if row["close"] < prev_low20 else 0)
        bd_candle = (15 if cur_clv <= -0.30 else 5) + (10 if cur_body_ratio >= 0.40 and row["close"] < row["open"] else 0)
        bd_vol = (15 if cur_rvol >= 1.20 else 5) + (10 if vol_trend_val >= 1.05 else 0)
        bd_money = (15 if row["pulse"] < row["flow"] else 0) + (10 if row["cmf20"] < 0.02 else 0)
        breakdown_quality_score = min(100, bd_loc + bd_candle + bd_vol + bd_money)

        # ─── 5. Guard Flags and Veto Codes ───────────────────────────────────
        guard_flags: list[str] = []
        veto_codes: list[str] = []

        hard_anti_bottom = bool(final_emotion_score <= 35 or row["rsi14"] <= 40)
        bottom_evidences = sum([
            is_stopping_volume, is_high_volume_absorption, is_high_volume_shakeout,
            is_low_volume_spring, is_wyckoff_spring, is_three_bar_reversal,
            is_capitulation_absorbed, is_any_bull_div,
        ])
        is_bottom_exhaustion_zone = bool(hard_anti_bottom or (bottom_evidences >= 3))

        if is_bottom_exhaustion_zone:
            guard_flags.append("ANTI_BOTTOM_SELL_ACTIVE")
        if is_news_sos:
            guard_flags.append("NEWS_SOS_ACTIVE")

        if not liq_pass_val:
            veto_codes.append("VETO_LOW_LIQUIDITY")
        if corp_gap_val:
            veto_codes.append("VETO_CORP_ACTION_GAP")
        if is_falling_knife_regime:
            veto_codes.append("VETO_FALLING_KNIFE")
        if is_capitulation_continuation:
            veto_codes.append("VETO_CAPITULATION_CONTINUATION")

        if is_capitulation_continuation and active_bottom_candidate is not None:
            active_bottom_candidate = None

        # ─── 6. State Machine Calculation ────────────────────────────────────
        distribution = bool(overextended_recent and pulse_cross_down and (row["cmf20"] < 0 or row["close"] < row["ema20"]))
        overextended = bool(final_emotion_score >= 80 and (distance_atr >= 2.0 or row["rsi14"] >= 75))
        early = bool(bottom_watch_recent and bullish_order and row["cmf20"] > 0 and _finite(row["rs20"]) is not None and row["rs20"] > 0)
        confirmed = bool(35 <= final_emotion_score <= 85 and flow_core_rising and bullish_order and row["close"] > row["ema20"] and row["close"] > row["ema50"])

        has_bottom_catalyst = bool(
            is_any_bull_div or is_wyckoff_spring or is_low_volume_spring
            or is_stopping_volume or is_high_volume_absorption or is_high_volume_shakeout
            or is_three_bar_reversal or is_volume_dryup_div or is_capitulation_absorbed
            or (pulse_improving and row["pulse"] > row["flow"] and cur_clv >= 0.30 and cur_rvol >= 0.80)
        )
        base_stable = index >= 5 and row["close"] >= result["low"].iloc[max(0, index - 5):index + 1].min() * 1.005

        bottom_watch = bool(
            falling_recent
            and final_emotion_score <= 35
            and has_bottom_catalyst
            and base_stable
            and not is_falling_knife_regime
            and not is_capitulation_continuation
            and bottom_quality_score >= 60
        )
        top_watch = bool(
            has_sustained_uptrend
            and (is_buying_climax or is_upthrust or is_effort_vs_result_dist or is_news_euphoria_distribution)
            and not is_news_sos
            and distribution_quality_score >= 60
        )
        falling = bool(final_emotion_score <= 25 and row["center"] < 0 and index >= 5 and row["center"] < result.iloc[index - 5]["center"] and row["close"] < row["ema50"])

        if distribution:
            state = "DISTRIBUTION_CONTRACTION"
        elif overextended:
            state = "OVEREXTENDED"
        elif top_watch:
            state = "TOP_WATCH"
        elif early:
            state = "EARLY_EXPANSION"
        elif confirmed:
            state = "CONFIRMED_EXPANSION"
        elif bottom_watch:
            state = "BOTTOM_WATCH"
        elif falling:
            state = "FALLING_CONTRACTION"
        else:
            state = "NEUTRAL"

        is_bullish_ema_align = bool(row["close"] > row["ema20"] > row["ema50"] > row["ema100"] > row["ema200"])
        is_bearish_ema_align = bool(row["close"] < row["ema20"] < row["ema50"] < row["ema100"] < row["ema200"])
        is_longterm_bull_regime = bool(row["close"] >= row["ema200"] and cur_ema200_slope20 >= 0.0)
        is_dynamic_pullback = bool(row["ema20"] > row["ema50"] and min(row["ema20"], row["ema50"]) <= row["close"] <= max(row["ema20"], row["ema50"]) * 1.015 and row["pulse"] > row["flow"])

        # ─── 7. Candidate Lifecycle Evaluation ────────────────────────────────
        lifecycle_event: Optional[str] = None
        is_spring_buy = False
        is_climax_sell = False
        candidate_id: Optional[str] = None
        candidate_date: Optional[str] = None
        candidate_expires_at: Optional[str] = None
        candidate_invalidation_px: Optional[float] = None
        confirmation_date: Optional[str] = None
        watch_subtype: Optional[str] = None
        candidate_follow_through: str = "Giữ vững nền đáy candidate, lấy lại EMA20/VWAP với Pulse > Flow và volume cạn khi test."

        # A. Evaluate Active Bottom Candidate
        if active_bottom_candidate is not None:
            candidate_id = active_bottom_candidate["id"]
            candidate_date = active_bottom_candidate["date"]
            candidate_expires_at = active_bottom_candidate.get("expires_date")
            candidate_invalidation_px = active_bottom_candidate["invalidation_price"]
            age = index - active_bottom_candidate["created_idx"]

            if age == 0:
                lifecycle_event = "CREATED"
            elif row["close"] < active_bottom_candidate["invalidation_price"] or ema20_slope_5 <= -0.020:
                lifecycle_event = "INVALIDATED"
                active_bottom_candidate = None
            elif 1 <= age <= 3:
                holds_base = (row["close"] >= active_bottom_candidate["invalidation_price"] and row["low"] >= active_bottom_candidate["low"] * 0.99)
                reclaims_mid = (row["close"] >= active_bottom_candidate["mid"] and (row["close"] >= active_bottom_candidate["high"] * 0.995 or row["close"] >= row["ema20"] or row["close"] >= row["vwap20"]))
                pulse_good = (row["pulse"] >= row["flow"] or is_any_bull_div or is_three_bar_reversal)
                vol_ok = (cur_rvol <= 1.30 or (cur_rvol >= 1.10 and cur_clv >= 0.30))
                not_overextended = final_emotion_score <= 62 and state not in ("OVEREXTENDED", "DISTRIBUTION_CONTRACTION")
                score_ok = (bottom_quality_score >= 70)

                if (holds_base and reclaims_mid and pulse_good and vol_ok and not_overextended and score_ok and not is_falling_knife_regime and not corp_gap_val and liq_pass_val):
                    is_spring_buy = True
                    lifecycle_event = "CONFIRMED"
                    confirmation_date = cur_date_str
                    active_bottom_candidate = None
            else:
                lifecycle_event = "EXPIRED"
                active_bottom_candidate = None

        # B. Evaluate Active Top Candidate
        if active_top_candidate is not None and not is_spring_buy:
            candidate_id = candidate_id or active_top_candidate["id"]
            candidate_date = candidate_date or active_top_candidate["date"]
            candidate_expires_at = candidate_expires_at or active_top_candidate.get("expires_date")
            candidate_invalidation_px = candidate_invalidation_px or active_top_candidate["invalidation_price"]
            age_top = index - active_top_candidate["created_idx"]

            if age_top == 0:
                lifecycle_event = lifecycle_event or "CREATED"
            elif row["close"] > active_top_candidate["invalidation_price"]:
                lifecycle_event = "INVALIDATED"
                active_top_candidate = None
            elif 1 <= age_top <= 3:
                loses_mid = (row["close"] < active_top_candidate["mid"] or row["close"] < active_top_candidate["low"])
                money_out = (row["pulse"] < row["flow"] or row["cmf20"] < 0 or cur_clv <= -0.20)
                failed_high = (row["close"] < active_top_candidate["high"])
                score_ok = (distribution_quality_score >= 70)

                if (loses_mid and money_out and failed_high and score_ok and not is_bottom_exhaustion_zone and not corp_gap_val and liq_pass_val):
                    is_climax_sell = True
                    lifecycle_event = "CONFIRMED"
                    confirmation_date = cur_date_str
                    active_top_candidate = None
            else:
                lifecycle_event = "EXPIRED"
                active_top_candidate = None

        # ─── 8. New Candidate Detection (Decluttered) ────────────────────────
        detected_bottom_pattern: Optional[str] = None
        if is_stopping_volume: detected_bottom_pattern = "STOPPING_VOLUME"
        elif is_high_volume_absorption: detected_bottom_pattern = "HIGH_VOLUME_ABSORPTION"
        elif is_high_volume_shakeout: detected_bottom_pattern = "HIGH_VOLUME_SHAKEOUT"
        elif is_three_bar_reversal: detected_bottom_pattern = "THREE_BAR_REVERSAL"
        elif is_capitulation_absorbed: detected_bottom_pattern = "CAPITULATION_ABSORBED"
        elif is_low_volume_spring or is_wyckoff_spring: detected_bottom_pattern = "LOW_VOLUME_SPRING"
        elif is_volume_dryup_div: detected_bottom_pattern = "VOLUME_DRYUP_DIVERGENCE"

        detected_top_pattern: Optional[str] = None
        if is_news_euphoria_distribution: detected_top_pattern = "NEWS_EUPHORIA_DISTRIBUTION"
        elif is_upthrust: detected_top_pattern = "UPTHRUST"
        elif is_buying_climax: detected_top_pattern = "BUYING_CLIMAX"
        elif is_effort_vs_result_dist: detected_top_pattern = "EFFORT_VS_RESULT_DISTRIBUTION"

        # Register new Bottom Candidate (with minimum 10-bar cooldown to prevent marker spam)
        if detected_bottom_pattern and active_bottom_candidate is None and (index - last_bottom_candidate_idx >= 10) and not is_falling_knife_regime and not is_capitulation_continuation and not corp_gap_val and liq_pass_val and bottom_quality_score >= 60:
            exp_idx = min(len(result) - 1, index + 3)
            exp_date = result.iloc[exp_idx]["date"].strftime("%Y-%m-%d")
            active_bottom_candidate = {
                "id": f"BOT_{index}_{cur_date_str}",
                "pattern": detected_bottom_pattern,
                "date": cur_date_str,
                "expires_date": exp_date,
                "created_idx": index,
                "low": float(row["low"]),
                "high": float(row["high"]),
                "mid": float((row["open"] + row["close"]) / 2.0),
                "score": bottom_quality_score,
                "invalidation_price": float(row["low"] - 0.5 * (row["atr14"] if _finite(row["atr14"]) is not None else row["close"] * 0.02)),
                "expires_at_idx": index + 3,
            }
            last_bottom_candidate_idx = index
            if lifecycle_event is None:
                candidate_id = active_bottom_candidate["id"]
                candidate_date = active_bottom_candidate["date"]
                candidate_expires_at = exp_date
                candidate_invalidation_px = active_bottom_candidate["invalidation_price"]
                lifecycle_event = "CREATED"

        # Register new Top Candidate
        if detected_top_pattern and active_top_candidate is None and has_sustained_uptrend and not is_news_sos and not corp_gap_val and liq_pass_val and distribution_quality_score >= 60:
            exp_idx = min(len(result) - 1, index + 3)
            exp_date = result.iloc[exp_idx]["date"].strftime("%Y-%m-%d")
            active_top_candidate = {
                "id": f"TOP_{index}_{cur_date_str}",
                "pattern": detected_top_pattern,
                "date": cur_date_str,
                "expires_date": exp_date,
                "created_idx": index,
                "high": float(row["high"]),
                "low": float(row["low"]),
                "mid": float((row["open"] + row["close"]) / 2.0),
                "score": distribution_quality_score,
                "invalidation_price": float(row["high"] + 0.5 * (row["atr14"] if _finite(row["atr14"]) is not None else row["close"] * 0.02)),
                "expires_at_idx": index + 3,
            }
            if lifecycle_event is None:
                candidate_id = active_top_candidate["id"]
                candidate_date = active_top_candidate["date"]
                candidate_expires_at = exp_date
                candidate_invalidation_px = active_top_candidate["invalidation_price"]
                lifecycle_event = "CREATED"

        # ─── 9. Breakout Buy (BB2) vs Recovery Breakout Watch ─────────────────
        is_trend_breakout_setup = bool(
            (row["close"] >= prev_high20 * 0.995 or is_news_sos or is_wyckoff_sos)
            and row["close"] > row["ema20"] > row["ema50"] * 0.99
            and cur_rvol >= 1.40
            and cur_clv >= 0.40
            and row["pulse"] > row["flow"]
            and _finite(row["cmf20"]) is not None and row["cmf20"] > 0.0
            and breakout_quality_score >= 70
            and not is_any_bear_div
            and not is_upthrust
            and not corp_gap_val
            and liq_pass_val
            and final_emotion_score <= 75
            and state not in ("OVEREXTENDED", "DISTRIBUTION_CONTRACTION")
        )

        has_trend_structure = bool(market_regime == "BULL_TREND" or (row["close"] >= row["ema200"] and cur_ema200_slope20 >= -0.002))

        if is_trend_breakout_setup:
            if has_trend_structure:
                is_breakout_buy = True
            else:
                is_breakout_buy = False
                watch_subtype = "RECOVERY_BREAKOUT_WATCH"
        else:
            is_breakout_buy = False

        # ─── 10. Pullback Buy (BB3) ───────────────────────────────────────────
        has_recent_sos = any(s == "BB2_SOS_BREAKOUT" for s in signal_subtypes[max(0, index - 60):index]) or is_wyckoff_sos
        is_pullback_buy = bool(
            (has_recent_sos or market_regime == "BULL_TREND")
            and row["ema20"] > row["ema50"]
            and abs(row["close"] - row["ema20"]) / row["ema20"] <= 0.025
            and row["close"] >= row["ema50"]
            and dist_to_low >= 0.10
            and cur_rvol <= 1.15
            and vol_trend_val <= 1.05
            and cur_clv >= 0.20
            and row["pulse"] > row["flow"]
            and pullback_quality_score >= 70
            and not is_any_bear_div
            and not corp_gap_val
            and liq_pass_val
            and final_emotion_score <= 72
            and state in ("EARLY_EXPANSION", "CONFIRMED_EXPANSION")
            and not overextended_recent
        )

        # ─── 11. Breakdown Sell (BS2) ─────────────────────────────────────────
        was_recently_above_ema20 = index >= 10 and (
            (result["ema20"].iloc[max(0, index - 20):index] > result["ema50"].iloc[max(0, index - 20):index]).any()
            or any(s in ("CONFIRMED_EXPANSION", "EARLY_EXPANSION") for s in states[max(0, index - 20):index])
        )
        is_fresh_breakdown_sell = bool(
            was_recently_above_ema20
            and row["close"] < row["ema20"] * 0.985
            and (row["close"] < row["ema50"] * 1.01 or row["pulse"] < row["flow"])
            and cur_rvol >= 1.20
            and cur_clv <= -0.30
            and row["pulse"] < row["flow"]
            and _finite(row["cmf20"]) is not None and row["cmf20"] < 0.02
            and breakdown_quality_score >= 70
            and not is_bottom_exhaustion_zone
            and not corp_gap_val
            and liq_pass_val
            and state not in ("BOTTOM_WATCH", "FALLING_CONTRACTION")
        )

        # ─── 12. Opportunity & Risk ──────────────────────────────────────────
        opportunity_weights = {
            "bullish_order": (25, bullish_order),
            "cmf_positive": (20, bool(row["cmf20"] > 0)),
            "rs_positive": (15, bool(_finite(row["rs20"]) is not None and row["rs20"] > 0)),
            "above_ema20": (15, bool(row["close"] > row["ema20"])),
            "bottom_watch_recent": (15, bottom_watch_recent),
            "catalyst_or_pattern": (10, bool(is_any_bull_div or is_wyckoff_spring or is_wyckoff_sos or is_bullish_ema_align or is_dynamic_pullback or is_stopping_volume or is_high_volume_absorption or is_spring_buy or is_breakout_buy)),
        }
        opportunity = sum(w for w, flag in opportunity_weights.values() if flag)

        risk_weights = {
            "overextended": (25, bool(overextended_recent or overextended)),
            "pulse_below_flow": (20, bool(row["pulse"] < row["flow"])),
            "cmf_negative": (15, bool(row["cmf20"] < 0)),
            "below_ema20": (15, bool(row["close"] < row["ema20"])),
            "technical_exhaustion": (15, bool(row["rsi14"] >= 75 or distance_atr >= 2.0 or is_bearish_ema_align or is_buying_climax or is_upthrust or is_news_euphoria_distribution or is_climax_sell or is_fresh_breakdown_sell or is_severe_downtrend)),
            "bearish_divergence": (10, is_any_bear_div),
        }
        risk = sum(w for w, flag in risk_weights.values() if flag)

        # ─── 13. Signal Assignment & Subtype Cooldown ─────────────────────────
        signal: Optional[str] = None
        signal_subtype: Optional[str] = None
        action_code: str = "WATCH"
        pattern_code: Optional[str] = detected_bottom_pattern or detected_top_pattern or watch_subtype

        if is_spring_buy and (index - last_subtype_idx.get("BB1_SPRING_CONFIRM", -999) >= 20) and len(veto_codes) == 0:
            signal = "BB"
            signal_subtype = "BB1_SPRING_CONFIRM"
            lifecycle_event = "CONFIRMED"
            action_code = "TEST_BUY"
            last_subtype_idx["BB1_SPRING_CONFIRM"] = index
            last_signal_idx["BB"] = index
            pattern_code = pattern_code or "SPRING_CONFIRM"

        elif is_breakout_buy and (index - last_subtype_idx.get("BB2_SOS_BREAKOUT", -999) >= 20) and len(veto_codes) == 0:
            signal = "BB"
            signal_subtype = "BB2_SOS_BREAKOUT"
            lifecycle_event = "CONFIRMED"
            action_code = "ADD_BUY"
            last_subtype_idx["BB2_SOS_BREAKOUT"] = index
            last_signal_idx["BB"] = index
            pattern_code = pattern_code or "SOS_BREAKOUT"

        elif is_pullback_buy and (index - last_subtype_idx.get("BB3_LPS_PULLBACK", -999) >= 20) and len(veto_codes) == 0:
            signal = "BB"
            signal_subtype = "BB3_LPS_PULLBACK"
            lifecycle_event = "CONFIRMED"
            action_code = "ADD_BUY"
            last_subtype_idx["BB3_LPS_PULLBACK"] = index
            last_signal_idx["BB"] = index
            pattern_code = pattern_code or "LPS_PULLBACK"

        elif is_climax_sell and not is_bottom_exhaustion_zone and (index - last_subtype_idx.get("BS1_CLIMAX_DISTRIBUTION", -999) >= 20) and len(veto_codes) == 0:
            signal = "BS"
            signal_subtype = "BS1_CLIMAX_DISTRIBUTION"
            lifecycle_event = "CONFIRMED"
            action_code = "EXIT"
            last_subtype_idx["BS1_CLIMAX_DISTRIBUTION"] = index
            last_signal_idx["BS"] = index
            pattern_code = pattern_code or "CLIMAX_DISTRIBUTION"

        elif is_fresh_breakdown_sell and not is_bottom_exhaustion_zone and (index - last_subtype_idx.get("BS2_SOW_BREAKDOWN", -999) >= 20) and len(veto_codes) == 0:
            signal = "BS"
            signal_subtype = "BS2_SOW_BREAKDOWN"
            lifecycle_event = "CONFIRMED"
            action_code = "EXIT"
            last_subtype_idx["BS2_SOW_BREAKDOWN"] = index
            last_signal_idx["BS"] = index
            pattern_code = pattern_code or "SOW_BREAKDOWN"

        if signal is None:
            if state == "TOP_WATCH" or (is_upthrust or is_buying_climax or is_news_euphoria_distribution):
                action_code = "TRIM"
            elif watch_subtype == "RECOVERY_BREAKOUT_WATCH" or state == "BOTTOM_WATCH" or lifecycle_event == "CREATED":
                action_code = "WATCH"
            elif row["close"] >= row["ema20"] and risk < 60:
                action_code = "HOLD"
            else:
                action_code = "WATCH"

        if signal_subtype == "BB1_SPRING_CONFIRM":
            quality_score = bottom_quality_score
            score_type = "BOTTOM_QUALITY"
        elif signal_subtype == "BB2_SOS_BREAKOUT":
            quality_score = breakout_quality_score
            score_type = "BREAKOUT_QUALITY"
        elif signal_subtype == "BB3_LPS_PULLBACK":
            quality_score = pullback_quality_score
            score_type = "PULLBACK_QUALITY"
        elif signal_subtype == "BS1_CLIMAX_DISTRIBUTION":
            quality_score = distribution_quality_score
            score_type = "DISTRIBUTION_QUALITY"
        elif signal_subtype == "BS2_SOW_BREAKDOWN":
            quality_score = breakdown_quality_score
            score_type = "BREAKDOWN_QUALITY"
        elif state == "TOP_WATCH" or detected_top_pattern:
            quality_score = distribution_quality_score
            score_type = "DISTRIBUTION_QUALITY"
        elif state == "BOTTOM_WATCH" or detected_bottom_pattern:
            quality_score = bottom_quality_score
            score_type = "BOTTOM_QUALITY"
        elif watch_subtype == "RECOVERY_BREAKOUT_WATCH":
            quality_score = breakout_quality_score
            score_type = "BREAKOUT_QUALITY"
        else:
            quality_score = bottom_quality_score
            score_type = "BOTTOM_QUALITY"

        condition_names = []
        for label, flag in (
            ("Pulse > Flow > Core (Dòng tiền tạo lập)", bullish_order),
            ("Đã có tín hiệu Theo dõi đáy", bottom_watch_recent),
            ("Dòng tiền Chaikin CMF dương", row["cmf20"] > 0),
            ("Sức mạnh tương đối RS > 0", _finite(row["rs20"]) is not None and row["rs20"] > 0),
            ("Giá nằm trên EMA20", row["close"] > row["ema20"]),
            ("Cấu trúc Bullish EMA", is_bullish_ema_align),
            ("Regime dài hạn tích cực", is_longterm_bull_regime),
            ("Bật tăng từ vùng hỗ trợ EMA", is_dynamic_pullback),
            ("Cấu trúc Bearish EMA", is_bearish_ema_align),
            ("⚓ Nến Stopping Volume", is_stopping_volume),
            ("🛡️ Hấp thụ cung giá thấp", is_high_volume_absorption),
            ("🔱 Mô hình 3 nến đảo chiều đáy", is_three_bar_reversal),
            ("💧 Phân kỳ cạn cung đáy", is_volume_dryup_div),
            ("⚡ Cao trào bán tháo đã được hấp thụ", is_capitulation_absorbed),
            ("🌱 Wyckoff Spring", is_wyckoff_spring or is_low_volume_spring),
            ("🚀 Wyckoff SOS", is_wyckoff_sos),
            ("📰 Bứt phá tin tốt", is_news_sos),
            ("⚠️ Cao trào mua đuổi đỉnh", is_buying_climax),
            ("🪤 Bẫy giá vượt đỉnh Upthrust", is_upthrust),
            ("📉 Phân phối nỗ lực", is_effort_vs_result_dist),
            ("📰 Rủi ro phân phối tin tốt", is_news_euphoria_distribution),
        ):
            if bool(flag):
                condition_names.append(label)

        changed = index == 0 or state != states[-1]
        cooldown_ok = index - last_event_by_state.get(state, -999) >= 7
        is_event = changed and state != "NEUTRAL" and cooldown_ok
        if is_event:
            last_event_by_state[state] = index

        cur_low20 = roll_low20.iloc[index] if index < len(roll_low20) else row["low"]
        cur_high20 = roll_high20.iloc[index] if index < len(roll_high20) else row["high"]
        cur_vwap = row["vwap20"] if _finite(row["vwap20"]) is not None else row["close"]

        trade_setup = _generate_trade_setup(
            row=row,
            state=state,
            signal=signal,
            div_type=composite_divergence,
            opportunity=opportunity,
            risk=risk,
            bullish_order=bullish_order,
            is_spring=is_spring_buy or is_wyckoff_spring or is_stopping_volume or is_high_volume_absorption or is_three_bar_reversal,
            is_sos=is_breakout_buy or is_wyckoff_sos or is_news_sos,
            roll_low20=cur_low20,
            roll_high20=cur_high20,
            vwap20=cur_vwap,
            disparity_score=row["disparity_score"],
            pattern_name=pattern_code,
            action_code=action_code,
            signal_subtype=signal_subtype,
            signal_stage=lifecycle_event,
            market_regime=market_regime,
            veto_codes=veto_codes,
            watch_subtype=watch_subtype,
        )

        volume_context = {
            "rvol20": _rounded(cur_rvol, 2),
            "vol_pct252": _rounded(vol_pct_val, 1),
            "vol_trend5_20": _rounded(vol_trend_val, 2),
            "clv": _rounded(cur_clv, 2),
            "effort_result": _rounded(cur_effort, 2),
            "lower_wick_ratio": _rounded(cur_lower_wick, 2),
            "upper_wick_ratio": _rounded(cur_upper_wick, 2),
            "body_ratio": _rounded(cur_body_ratio, 2),
            "liquidity_pass": liq_pass_val,
        }

        score_breakdown = {
            "bottom_quality_score": bottom_quality_score,
            "breakout_quality_score": breakout_quality_score,
            "pullback_quality_score": pullback_quality_score,
            "distribution_quality_score": distribution_quality_score,
            "breakdown_quality_score": breakdown_quality_score,
            "composite_score": quality_score,
        }

        states.append(state)
        signals.append(signal)
        signal_subtypes.append(signal_subtype)
        signal_stages.append(lifecycle_event)
        pattern_codes.append(pattern_code)
        market_regimes.append(market_regime)
        lifecycle_events.append(lifecycle_event)
        action_codes.append(action_code)
        watch_subtypes.append(watch_subtype)
        quality_scores.append(quality_score)
        score_types.append(score_type)
        candidate_ids.append(candidate_id)
        candidate_dates.append(candidate_date)
        candidate_expires_at_list.append(candidate_expires_at)
        confirmation_dates.append(confirmation_date)
        invalidation_prices.append(candidate_invalidation_px)
        follow_through_conditions.append(candidate_follow_through)
        score_breakdowns.append(score_breakdown)
        reason_codes_list.append(condition_names)
        reason_labels_list.append(condition_names)
        veto_codes_list.append(veto_codes)
        guard_flags_list.append(guard_flags)
        volume_contexts.append(volume_context)
        opportunities.append(int(opportunity))
        risks.append(int(risk))
        bottom_confidences.append(int(bottom_quality_score))
        conditions.append(condition_names)
        events.append(is_event)
        trade_setups.append(trade_setup)

    result["state"] = states
    result["signal"] = signals
    result["signal_subtype"] = signal_subtypes
    result["signal_stage"] = signal_stages
    result["pattern_code"] = pattern_codes
    result["market_regime"] = market_regimes
    result["regime_cap"] = regime_caps
    result["aperture"] = market_emotion_scores
    result["market_emotion_score"] = market_emotion_scores
    result["emotion_state"] = emotion_states
    result["emotion_state_label"] = emotion_state_labels
    result["emotion_state_color"] = emotion_state_colors
    result["smart_money_phase"] = sm_phases
    result["smart_money_phase_label"] = sm_phase_labels
    result["smart_money_phase_color"] = sm_phase_colors
    result["lifecycle_event"] = lifecycle_events
    result["outflow_event"] = outflow_events
    result["action_code"] = action_codes
    result["watch_subtype"] = watch_subtypes
    result["quality_score"] = quality_scores
    result["score_type"] = score_types
    result["candidate_id"] = candidate_ids
    result["candidate_date"] = candidate_dates
    result["candidate_expires_at"] = candidate_expires_at_list
    result["confirmation_date"] = confirmation_dates
    result["invalidation_price"] = invalidation_prices
    result["follow_through_condition"] = follow_through_conditions
    result["score_breakdown"] = score_breakdowns
    result["reason_codes"] = reason_codes_list
    result["reason_labels"] = reason_labels_list
    result["veto_codes"] = veto_codes_list
    result["guard_flags"] = guard_flags_list
    result["volume_context"] = volume_contexts
    result["divergence"] = divergences
    result["divergence_pct"] = divergence_pcts
    result["opportunity_score"] = opportunities
    result["risk_score"] = risks
    result["bottom_confidence"] = bottom_confidences
    result["conditions"] = conditions
    result["is_event"] = events
    result["trade_setup"] = trade_setups
    return result


def _quality_payload(stock_result: Any, benchmark_available: bool) -> dict[str, Any]:
    status = str(stock_result.quality_status)
    if not benchmark_available and status == "valid":
        status = "partial"
    warnings = []
    if not benchmark_available:
        warnings.append("Không tải được VNINDEX; RS20 không khả dụng và Cơ hội mở sớm bị khóa.")
    if stock_result.freshness_sessions:
        warnings.append(f"Dữ liệu chậm {stock_result.freshness_sessions} phiên giao dịch.")
    return {
        "status": status,
        "source": stock_result.source,
        "source_chain": stock_result.source_chain,
        "served_from_cache": bool(stock_result.served_from_cache),
        "freshness_sessions": int(stock_result.freshness_sessions),
        "last_success_at": stock_result.last_success_at,
        "source_agreement_bps": _rounded(stock_result.source_agreement_bps, 2),
        "data_confidence_score": _rounded(stock_result.data_confidence_score, 1),
        "adjustment_version": stock_result.adjustment_version,
        "corporate_action_status": stock_result.corporate_action_status,
        "benchmark_available": benchmark_available,
        "warnings": warnings,
        "no_synthetic_data": True,
    }


def _series_records(frame: pd.DataFrame, columns: Iterable[str], digits: int = 4) -> list[dict[str, Any]]:
    records = []
    for row in frame.itertuples(index=False):
        item = {
            "date": row.date.strftime("%Y-%m-%d"),
            "timestamp": int(row.date.timestamp() * 1000),
            "divergence": getattr(row, "divergence", None),
            "divergence_pct": _rounded(getattr(row, "divergence_pct", None), 1) if getattr(row, "divergence_pct", None) is not None else None,
        }
        for column in columns:
            val = getattr(row, column, None)
            if isinstance(val, (int, float, np.floating, np.integer)):
                item[column] = _rounded(val, digits)
            else:
                item[column] = val
        records.append(item)
    return records



def _current_summary(row: pd.Series, quality: dict[str, Any], news_sentiment: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    state = str(row["state"])
    invalidation = {
        "FALLING_CONTRACTION": "Pulse ngừng giảm và giá lấy lại EMA20.",
        "BOTTOM_WATCH": "Pulse tạo đáy thấp hơn hoặc giá phá đáy 20 phiên.",
        "TOP_WATCH": "Giá vượt đỉnh candidate + 0.5 ATR hoặc Pulse phục hồi vượt Flow.",
        "EARLY_EXPANSION": "Pulse cắt xuống Flow hoặc giá mất EMA20.",
        "CONFIRMED_EXPANSION": "Flow/Core ngừng tăng hoặc giá mất EMA50.",
        "OVEREXTENDED": "Aperture co mạnh và Pulse cắt xuống Flow.",
        "DISTRIBUTION_CONTRACTION": "Giá lấy lại EMA20, CMF dương và Pulse vượt Flow.",
        "NEUTRAL": "Chưa có cấu trúc đủ mạnh để xác nhận hoặc vô hiệu.",
    }.get(state, "Chưa có cấu trúc đủ mạnh để xác nhận hoặc vô hiệu.")

    pulse_pct = _rounded(row["pulse_pct"], 1)
    flow_pct = _rounded(row["flow_pct"], 1)
    core_pct = _rounded(row["core_pct"], 1)

    market_regime = str(row.get("market_regime", "RANGE") if hasattr(row, "get") else getattr(row, "market_regime", "RANGE"))
    regime_cap = float(row.get("regime_cap", 70.0) if hasattr(row, "get") else getattr(row, "regime_cap", 70.0))
    tech_emotion_val = _finite(row.get("technical_emotion_score") if hasattr(row, "get") else getattr(row, "technical_emotion_score", 50.0))
    tech_emotion = tech_emotion_val if tech_emotion_val is not None else 50.0

    outflow_score_val = _finite(row.get("smart_money_outflow_score") if hasattr(row, "get") else getattr(row, "smart_money_outflow_score", 0.0))
    outflow_score = outflow_score_val if outflow_score_val is not None else 0.0

    news_tone_score = news_sentiment.get("news_tone_score") if news_sentiment else None
    news_attention_score = float(news_sentiment.get("news_attention_score", 0.0)) if news_sentiment else 0.0
    news_adjustment = float(news_sentiment.get("news_adjustment", 0.0)) if news_sentiment else 0.0
    news_label = str(news_sentiment.get("label", "Không có tin tức trực tiếp")) if news_sentiment else "Không có tin tức trực tiếp"
    news_reaction = str(news_sentiment.get("news_reaction", "NO_VALID_DIRECT_NEWS")) if news_sentiment else "NO_VALID_DIRECT_NEWS"
    catalysts = list(news_sentiment.get("catalysts", [])) if news_sentiment else []

    unbounded_emotion = tech_emotion + news_adjustment
    final_emotion_score = round(float(np.clip(min(unbounded_emotion, regime_cap), 5.0, 95.0)), 1)
    if np.isnan(final_emotion_score):
        final_emotion_score = 50.0
    em_state, em_label, em_color = _map_emotion_state(final_emotion_score, market_regime)

    pulse_val = float(row.get("pulse", 0.0) if hasattr(row, "get") else getattr(row, "pulse", 0.0) or 0.0)
    flow_val = float(row.get("flow", 0.0) if hasattr(row, "get") else getattr(row, "flow", 0.0) or 0.0)
    core_val = float(row.get("core", 0.0) if hasattr(row, "get") else getattr(row, "core", 0.0) or 0.0)
    center_val = float(row.get("center_pct", 50.0) if hasattr(row, "get") else getattr(row, "center_pct", 50.0) or 50.0)
    bullish_order = bool(pulse_val > flow_val > core_val)

    disparity_val = _rounded(center_val - final_emotion_score, 1)
    if disparity_val is None:
        disparity_val = 0.0

    if disparity_val >= 20.0:
        insight = "🦊 Dấu hiệu hấp thụ proxy: Đám đông bi quan xả hàng, nhưng dữ liệu OHLCV/Dòng tiền cho thấy lực gom mua đỡ giá tại vùng chiết khấu sâu (proxy định lượng)."
    elif disparity_val <= -20.0:
        insight = "🪤 Cảnh báo phân phối đỉnh: Đám đông đang hưng phấn FOMO đẩy giá, nhưng dữ liệu OHLCV/Pulse cho thấy dấu hiệu chốt lời phân phối (proxy định lượng)."
    elif final_emotion_score <= 25 and pulse_val > flow_val:
        insight = "🦊 Dấu hiệu dò đáy tích lũy: Đám đông sợ hãi cực độ trong khi dòng tiền tạo lập (Pulse) bắt đầu xuất hiện hấp thụ cung."
    elif final_emotion_score >= 75 and pulse_val < flow_val:
        insight = "🪤 Cảnh báo đu đỉnh: Đám đông hưng phấn mua đuổi ở vùng giá cao nhưng xung lực dòng tiền lớn suy yếu."
    elif final_emotion_score <= 30 and pulse_val <= flow_val:
        insight = "⚠️ Thị trường chịu áp lực giảm: Chưa xuất hiện lực cầu đỡ giá rõ nét; thị trường vẫn nằm trong vùng rủi ro, cần tiếp tục quan sát."
    elif final_emotion_score >= 60 and bullish_order:
        insight = "🚀 Dòng tiền dẫn dắt sóng tăng: Cấu trúc dòng tiền (Pulse > Flow > Core) xác nhận xu hướng tăng lành mạnh."
    elif abs(disparity_val) <= 10 and 35 <= final_emotion_score <= 65:
        insight = "🎭 Trạng thái cân bằng: Dòng tiền và tâm lý thị trường đang trong vùng tích lũy cân bằng, chờ tín hiệu xác nhận rõ nét."
    elif disparity_val > 0:
        insight = "🦊 Dòng tiền nhỉnh hơn tâm lý: Dòng tiền lớn có phần tích cực hơn tâm lý chung; khả năng đang xây nền tích lũy."
    else:
        insight = "⚠️ Dòng tiền thận trọng: Xung lực dòng tiền đang co lại trong khi đám đông vẫn giữ kỳ vọng cao; cần ưu tiên quản trị rủi ro."

    mom_val = _rounded(row.get("emotion_momentum", 50.0) if hasattr(row, "get") else getattr(row, "emotion_momentum", 50.0), 1)
    vol_val = _rounded(row.get("emotion_volume", 50.0) if hasattr(row, "get") else getattr(row, "emotion_volume", 50.0), 1)
    vola_val = _rounded(row.get("emotion_volatility", 50.0) if hasattr(row, "get") else getattr(row, "emotion_volatility", 50.0), 1)
    struct_val = _rounded(row.get("emotion_structure", 50.0) if hasattr(row, "get") else getattr(row, "emotion_structure", 50.0), 1)

    emotion_breakdown = {
        "composite_score": final_emotion_score,
        "technical_emotion_score": tech_emotion,
        "market_emotion_score": final_emotion_score,
        "news_tone_score": news_tone_score,
        "news_attention_score": news_attention_score,
        "news_adjustment": news_adjustment,
        "news_reaction": news_reaction,
        "emotion_state": em_state,
        "emotion_state_label": em_label,
        "emotion_state_color": em_color,
        "crowd_sentiment": em_label,
        "market_regime": market_regime,
        "regime_cap": regime_cap,
        "smart_money_outflow_score": outflow_score,
        "momentum_score": mom_val if mom_val is not None else 50.0,
        "directional_volume_score": vol_val if vol_val is not None else 50.0,
        "downside_calm_score": vola_val if vola_val is not None else 50.0,
        "structure_score": struct_val if struct_val is not None else 50.0,
        "price_momentum_score": mom_val if mom_val is not None else 50.0,
        "volume_panic_score": vol_val if vol_val is not None else 50.0,
        "volatility_stretch_score": vola_val if vola_val is not None else 50.0,
        "bigboys_disparity_score": struct_val if struct_val is not None else 50.0,
        "disparity_score": disparity_val,
        "news_sentiment_score": news_tone_score if news_tone_score is not None else 50.0,
        "news_sentiment_label": news_label,
        "catalysts": catalysts,
        "crowd_vs_bigboys_insight": insight,
    }

    divergence = row.get("divergence") if hasattr(row, "get") else getattr(row, "divergence", None)
    trade_setup = row.get("trade_setup") if hasattr(row, "get") else getattr(row, "trade_setup", None)

    sm_phase = str(row.get("smart_money_phase", "NEUTRAL") if hasattr(row, "get") else getattr(row, "smart_money_phase", "NEUTRAL"))
    sm_phase_label = str(row.get("smart_money_phase_label", SMART_MONEY_PHASE_LABELS.get(sm_phase, sm_phase)) if hasattr(row, "get") else getattr(row, "smart_money_phase_label", SMART_MONEY_PHASE_LABELS.get(sm_phase, sm_phase)))
    sm_phase_color = str(row.get("smart_money_phase_color", SMART_MONEY_PHASE_COLORS.get(sm_phase, "#94a3b8")) if hasattr(row, "get") else getattr(row, "smart_money_phase_color", SMART_MONEY_PHASE_COLORS.get(sm_phase, "#94a3b8")))
    sm_score = _rounded(row.get("smart_money_score", 50.0) if hasattr(row, "get") else getattr(row, "smart_money_score", 50.0), 1)
    sm_conf = _rounded(row.get("smart_money_confidence", 50.0) if hasattr(row, "get") else getattr(row, "smart_money_confidence", 50.0), 1)
    weekly_trend = str(row.get("weekly_trend", "NEUTRAL") if hasattr(row, "get") else getattr(row, "weekly_trend", "NEUTRAL"))
    weekly_regime = str(row.get("weekly_regime", "RANGE") if hasattr(row, "get") else getattr(row, "weekly_regime", "RANGE"))
    rvwap_val = _rounded(row.get("rvwap20", row.get("vwap20", row["close"])) if hasattr(row, "get") else getattr(row, "rvwap20", getattr(row, "vwap20", row["close"])), 2)

    smart_money_breakdown = {
        "directional_flow": _rounded(row.get("group_directional_flow", 50.0) if hasattr(row, "get") else getattr(row, "group_directional_flow", 50.0), 1),
        "effort_vs_result": _rounded(row.get("group_effort_vs_result", 50.0) if hasattr(row, "get") else getattr(row, "group_effort_vs_result", 50.0), 1),
        "price_acceptance": _rounded(row.get("group_price_acceptance", 50.0) if hasattr(row, "get") else getattr(row, "group_price_acceptance", 50.0), 1),
        "structure_rs": _rounded(row.get("group_structure_rs", 50.0) if hasattr(row, "get") else getattr(row, "group_structure_rs", 50.0), 1),
        "participation": _rounded(row.get("group_participation", 50.0) if hasattr(row, "get") else getattr(row, "group_participation", 50.0), 1),
        "weekly_trend": weekly_trend,
        "weekly_regime": weekly_regime,
        "rvwap20": rvwap_val,
        "confidence": sm_conf,
    }

    return {
        "date": row["date"].strftime("%Y-%m-%d"),
        "timestamp": int(row["date"].timestamp() * 1000),
        "state": state,
        "label": STATE_LABELS.get(state, state),
        "smart_money_phase": sm_phase,
        "smart_money_phase_label": sm_phase_label,
        "smart_money_phase_color": sm_phase_color,
        "smart_money_score": sm_score if sm_score is not None else 50.0,
        "smart_money_confidence": sm_conf if sm_conf is not None else 50.0,
        "weekly_trend": weekly_trend,
        "weekly_regime": weekly_regime,
        "rvwap20": rvwap_val,
        "market_regime": market_regime,
        "regime_label": REGIME_LABELS.get(market_regime, "Đi ngang / Tích lũy"),
        "regime_cap": regime_cap,
        "lifecycle_event": row.get("lifecycle_event") if hasattr(row, "get") else getattr(row, "lifecycle_event", getattr(row, "signal_stage", None)),
        "outflow_event": row.get("outflow_event") if hasattr(row, "get") else getattr(row, "outflow_event", None),
        "action_code": row.get("action_code") if hasattr(row, "get") else getattr(row, "action_code", "WATCH"),
        "action_label": ACTION_LABELS.get(row.get("action_code") if hasattr(row, "get") else getattr(row, "action_code", "WATCH"), "Quan sát"),
        "watch_subtype": row.get("watch_subtype") if hasattr(row, "get") else getattr(row, "watch_subtype", None),
        "quality_score": int(row.get("quality_score") if hasattr(row, "get") else getattr(row, "quality_score", getattr(row, "bottom_confidence", 50))),
        "score_type": row.get("score_type") if hasattr(row, "get") else getattr(row, "score_type", "BOTTOM_QUALITY"),
        "opportunity_score": int(row["opportunity_score"]),
        "risk_score": int(row["risk_score"]),
        "bottom_confidence": int(row["bottom_confidence"]),
        "aperture": final_emotion_score,
        "market_emotion_score": final_emotion_score,
        "technical_emotion_score": tech_emotion,
        "emotion_state": em_state,
        "emotion_state_label": em_label,
        "emotion_state_color": em_color,
        "smart_money_outflow_score": outflow_score,
        "pulse_pct": pulse_pct,
        "flow_pct": flow_pct,
        "core_pct": core_pct,
        "disparity_score": disparity_val,
        "rsi14": _rounded(row.get("rsi14", 50.0) if hasattr(row, "get") else getattr(row, "rsi14", 50.0), 1),
        "smart_money_label": f"Phase: {sm_phase_label} | Score: {sm_score} | Core: {core_pct} | Flow: {flow_pct} | Pulse: {pulse_pct}",
        "smart_money_breakdown": smart_money_breakdown,
        "crowd_sentiment": em_label,
        "emotion_breakdown": emotion_breakdown,
        "news_sentiment": news_sentiment or {"score": 50.0, "label": "Không có tin tức", "total_articles": 0, "catalysts": []},
        "signal": row.get("signal") if hasattr(row, "get") else getattr(row, "signal", None),
        "signal_subtype": row.get("signal_subtype") if hasattr(row, "get") else getattr(row, "signal_subtype", None),
        "signal_stage": row.get("signal_stage") if hasattr(row, "get") else getattr(row, "signal_stage", None),
        "pattern_code": row.get("pattern_code") if hasattr(row, "get") else getattr(row, "pattern_code", None),
        "candidate_id": row.get("candidate_id") if hasattr(row, "get") else getattr(row, "candidate_id", None),
        "candidate_date": row.get("candidate_date") if hasattr(row, "get") else getattr(row, "candidate_date", None),
        "candidate_expires_at": row.get("candidate_expires_at") if hasattr(row, "get") else getattr(row, "candidate_expires_at", None),
        "confirmation_date": row.get("confirmation_date") if hasattr(row, "get") else getattr(row, "confirmation_date", None),
        "invalidation_price": _rounded(row.get("invalidation_price") if hasattr(row, "get") else getattr(row, "invalidation_price", None), 2),
        "score_breakdown": row.get("score_breakdown") if hasattr(row, "get") else getattr(row, "score_breakdown", None),
        "volume_context": row.get("volume_context") if hasattr(row, "get") else getattr(row, "volume_context", None),
        "reason_codes": list(row.get("reason_codes", []) if hasattr(row, "get") else getattr(row, "reason_codes", [])),
        "veto_codes": list(row.get("veto_codes", []) if hasattr(row, "get") else getattr(row, "veto_codes", [])),
        "guard_flags": list(row.get("guard_flags", []) if hasattr(row, "get") else getattr(row, "guard_flags", [])),
        "divergence": divergence,
        "trade_setup": trade_setup,
        "conditions": list(row["conditions"]),
        "invalidation": invalidation,
        "quality_status": quality["status"],
    }


def _build_analysis_payload(symbol: str, bar_limit: int, frame: pd.DataFrame, quality: dict[str, Any], news_sentiment: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if len(frame) < MIN_BAR_LIMIT:
        return {
            "status": "insufficient_data",
            "metadata": {
                "symbol": symbol,
                "formula_version": FORMULA_VERSION,
                "smart_money_version": SMART_MONEY_VERSION,
                "requested_bars": bar_limit,
                "actual_bars": len(frame),
            },
            "data_quality": quality,
            "bars": [], "series": [], "states": [], "events": [], "divergences": [], "current": None,
            "methodology": _methodology(),
        }
    events = []
    for row in frame.loc[frame["is_event"] | frame["signal"].notna() | (frame["lifecycle_event"] == "CREATED") | frame["outflow_event"].notna()].itertuples(index=False):
        events.append({
            "date": row.date.strftime("%Y-%m-%d"),
            "timestamp": int(row.date.timestamp() * 1000),
            "state": row.state,
            "smart_money_phase": getattr(row, "smart_money_phase", "NEUTRAL"),
            "smart_money_phase_label": getattr(row, "smart_money_phase_label", SMART_MONEY_PHASE_LABELS.get(getattr(row, "smart_money_phase", "NEUTRAL"), "Trung tính")),
            "smart_money_phase_color": getattr(row, "smart_money_phase_color", SMART_MONEY_PHASE_COLORS.get(getattr(row, "smart_money_phase", "NEUTRAL"), "#94a3b8")),
            "smart_money_score": _rounded(getattr(row, "smart_money_score", 50.0), 1),
            "smart_money_confidence": _rounded(getattr(row, "smart_money_confidence", 50.0), 1),
            "weekly_trend": getattr(row, "weekly_trend", "NEUTRAL"),
            "weekly_regime": getattr(row, "weekly_regime", "RANGE"),
            "market_regime": getattr(row, "market_regime", "RANGE"),
            "regime_cap": getattr(row, "regime_cap", 70.0),
            "lifecycle_event": getattr(row, "lifecycle_event", getattr(row, "signal_stage", None)),
            "outflow_event": getattr(row, "outflow_event", None),
            "action_code": getattr(row, "action_code", "WATCH"),
            "watch_subtype": getattr(row, "watch_subtype", None),
            "quality_score": getattr(row, "quality_score", getattr(row, "bottom_confidence", 50)),
            "score_type": getattr(row, "score_type", "BOTTOM_QUALITY"),
            "signal": getattr(row, "signal", None),
            "signal_subtype": getattr(row, "signal_subtype", None),
            "signal_stage": getattr(row, "signal_stage", None),
            "pattern_code": getattr(row, "pattern_code", None),
            "candidate_id": getattr(row, "candidate_id", None),
            "candidate_date": getattr(row, "candidate_date", None),
            "confirmation_date": getattr(row, "confirmation_date", None),
            "invalidation_price": _rounded(getattr(row, "invalidation_price", None), 2),
            "label": STATE_LABELS.get(row.state, row.state),
            "close": _rounded(row.close, 2),
            "market_emotion_score": _rounded(getattr(row, "market_emotion_score", row.aperture), 1),
            "technical_emotion_score": _rounded(getattr(row, "technical_emotion_score", row.aperture), 1),
            "emotion_state": getattr(row, "emotion_state", "NEUTRAL"),
            "smart_money_outflow_score": _rounded(getattr(row, "smart_money_outflow_score", 0.0), 1),
            "opportunity_score": int(row.opportunity_score),
            "risk_score": int(row.risk_score),
            "bottom_confidence": int(row.bottom_confidence),
            "score_breakdown": getattr(row, "score_breakdown", None),
            "volume_context": getattr(row, "volume_context", None),
            "reason_codes": list(getattr(row, "reason_codes", [])),
            "veto_codes": list(getattr(row, "veto_codes", [])),
            "guard_flags": list(getattr(row, "guard_flags", [])),
            "conditions": list(row.conditions),
        })
    divergences = []
    for row in frame.itertuples(index=False):
        div_type = getattr(row, "divergence", None)
        if div_type in (
            "BULLISH", "BEARISH",
            "DUAL_BULLISH", "DUAL_BEARISH",
            "TRIPLE_BULLISH", "TRIPLE_BEARISH",
            "RSI_BULLISH", "RSI_BEARISH",
            "MACD_BULLISH", "MACD_BEARISH",
            "MACD_RSI_BULLISH", "MACD_RSI_BEARISH",
        ):
            divergences.append({
                "date": row.date.strftime("%Y-%m-%d"),
                "timestamp": int(row.date.timestamp() * 1000),
                "type": div_type,
                "y": _rounded(row.pulse_pct, 1),
                "close": _rounded(row.close, 2),
                "pulse_pct": _rounded(row.pulse_pct, 1),
                "flow_pct": _rounded(row.flow_pct, 1),
                "core_pct": _rounded(row.core_pct, 1),
            })
    states = [{
        "date": row.date.strftime("%Y-%m-%d"),
        "timestamp": int(row.date.timestamp() * 1000),
        "state": row.state,
        "label": STATE_LABELS.get(row.state, row.state),
        "smart_money_phase": getattr(row, "smart_money_phase", "NEUTRAL"),
        "smart_money_phase_label": getattr(row, "smart_money_phase_label", SMART_MONEY_PHASE_LABELS.get(getattr(row, "smart_money_phase", "NEUTRAL"), "Trung tính")),
        "smart_money_phase_color": getattr(row, "smart_money_phase_color", SMART_MONEY_PHASE_COLORS.get(getattr(row, "smart_money_phase", "NEUTRAL"), "#94a3b8")),
        "smart_money_score": _rounded(getattr(row, "smart_money_score", 50.0), 1),
        "smart_money_confidence": _rounded(getattr(row, "smart_money_confidence", 50.0), 1),
        "weekly_trend": getattr(row, "weekly_trend", "NEUTRAL"),
        "weekly_regime": getattr(row, "weekly_regime", "RANGE"),
        "rvwap20": _rounded(getattr(row, "rvwap20", getattr(row, "vwap20", row.close)), 2),
        "market_regime": getattr(row, "market_regime", "RANGE"),
        "regime_cap": getattr(row, "regime_cap", 70.0),
        "is_event": bool(row.is_event),
        "signal": getattr(row, "signal", None),
        "signal_subtype": getattr(row, "signal_subtype", None),
        "signal_stage": getattr(row, "signal_stage", None),
        "outflow_event": getattr(row, "outflow_event", None),
        "pattern_code": getattr(row, "pattern_code", None),
        "candidate_id": getattr(row, "candidate_id", None),
        "candidate_date": getattr(row, "candidate_date", None),
        "confirmation_date": getattr(row, "confirmation_date", None),
        "invalidation_price": _rounded(getattr(row, "invalidation_price", None), 2),
        "follow_through_condition": getattr(row, "follow_through_condition", None),
        "score_breakdown": getattr(row, "score_breakdown", None),
        "reason_codes": list(getattr(row, "reason_codes", getattr(row, "conditions", []))),
        "veto_codes": list(getattr(row, "veto_codes", [])),
        "guard_flags": list(getattr(row, "guard_flags", [])),
        "volume_context": getattr(row, "volume_context", None),
        "clv": _rounded(getattr(row, "clv", 0.0), 2),
        "rvol20": _rounded(getattr(row, "volume_ratio20", 1.0), 2),
        "divergence": getattr(row, "divergence", None),
        "opportunity_score": int(row.opportunity_score),
        "risk_score": int(row.risk_score),
        "bottom_confidence": int(row.bottom_confidence),
        "aperture": _rounded(row.aperture, 1),
        "market_emotion_score": _rounded(getattr(row, "market_emotion_score", row.aperture), 1),
        "technical_emotion_score": _rounded(getattr(row, "technical_emotion_score", row.aperture), 1),
        "emotion_state": getattr(row, "emotion_state", "NEUTRAL"),
        "smart_money_outflow_score": _rounded(getattr(row, "smart_money_outflow_score", 0.0), 1),
        "rsi14": _rounded(getattr(row, "rsi14", 50.0), 1),
        "ema20": _rounded(getattr(row, "ema20", 0.0), 2),
        "ema50": _rounded(getattr(row, "ema50", 0.0), 2),
        "ema100": _rounded(getattr(row, "ema100", 0.0), 2),
        "ema200": _rounded(getattr(row, "ema200", 0.0), 2),
        "pulse_pct": _rounded(row.pulse_pct, 1),
        "flow_pct": _rounded(row.flow_pct, 1),
        "core_pct": _rounded(row.core_pct, 1),
        "disparity_score": _rounded(getattr(row, "disparity_score", 0.0), 1),
        "crowd_sentiment": getattr(row, "emotion_state_label", _crowd_sentiment(row.aperture)),
        "emotion_momentum": _rounded(getattr(row, "emotion_momentum", 50.0), 1),
        "emotion_volume": _rounded(getattr(row, "emotion_volume", 50.0), 1),
        "emotion_volatility": _rounded(getattr(row, "emotion_volatility", 50.0), 1),
        "emotion_structure": _rounded(getattr(row, "emotion_structure", 50.0), 1),
        "emotion_bigboys": _rounded(getattr(row, "emotion_bigboys", 50.0), 1),
        "trade_setup": getattr(row, "trade_setup", None),
        "conditions": list(row.conditions),
    } for row in frame.itertuples(index=False)]

    current_summary = _current_summary(frame.iloc[-1], quality, news_sentiment)
    if states:
        states[-1]["aperture"] = current_summary.get("aperture")
        states[-1]["market_emotion_score"] = current_summary.get("market_emotion_score")
        states[-1]["crowd_sentiment"] = current_summary.get("crowd_sentiment")
        states[-1]["disparity_score"] = current_summary.get("disparity_score")
        states[-1]["emotion_state"] = current_summary.get("emotion_state")
        states[-1]["emotion_state_label"] = current_summary.get("emotion_state_label")
        states[-1]["emotion_state_color"] = current_summary.get("emotion_state_color")
        states[-1]["market_regime"] = current_summary.get("market_regime")
        states[-1]["regime_cap"] = current_summary.get("regime_cap")
        states[-1]["smart_money_outflow_score"] = current_summary.get("smart_money_outflow_score")
        states[-1]["smart_money_phase"] = current_summary.get("smart_money_phase")
        states[-1]["smart_money_phase_label"] = current_summary.get("smart_money_phase_label")
        states[-1]["smart_money_phase_color"] = current_summary.get("smart_money_phase_color")
        states[-1]["smart_money_score"] = current_summary.get("smart_money_score")
        states[-1]["smart_money_confidence"] = current_summary.get("smart_money_confidence")
        states[-1]["weekly_trend"] = current_summary.get("weekly_trend")
        states[-1]["weekly_regime"] = current_summary.get("weekly_regime")

    series_cols = (
        "pulse", "flow", "core", "center", "aperture",
        "pulse_pct", "flow_pct", "core_pct", "center_pct",
        "smart_money_score", "smart_money_confidence",
        "smart_money_phase", "smart_money_phase_label", "smart_money_phase_color",
        "weekly_trend", "weekly_regime", "rvwap20",
        "disparity_score", "technical_emotion_score", "market_emotion_score",
        "smart_money_outflow_score", "regime_cap",
        "emotion_momentum", "emotion_volume", "emotion_volatility", "emotion_structure", "emotion_bigboys",
        "market_regime", "emotion_state", "emotion_state_label", "emotion_state_color"
    )
    series_records = _series_records(frame, series_cols, 4)
    if series_records:
        series_records[-1]["aperture"] = current_summary.get("aperture")
        series_records[-1]["market_emotion_score"] = current_summary.get("market_emotion_score")
        series_records[-1]["disparity_score"] = current_summary.get("disparity_score")
        series_records[-1]["emotion_state"] = current_summary.get("emotion_state")
        series_records[-1]["emotion_state_label"] = current_summary.get("emotion_state_label")
        series_records[-1]["emotion_state_color"] = current_summary.get("emotion_state_color")
        series_records[-1]["market_regime"] = current_summary.get("market_regime")
        series_records[-1]["regime_cap"] = current_summary.get("regime_cap")
        series_records[-1]["smart_money_outflow_score"] = current_summary.get("smart_money_outflow_score")
        series_records[-1]["smart_money_phase"] = current_summary.get("smart_money_phase")
        series_records[-1]["smart_money_phase_label"] = current_summary.get("smart_money_phase_label")
        series_records[-1]["smart_money_phase_color"] = current_summary.get("smart_money_phase_color")
        series_records[-1]["smart_money_score"] = current_summary.get("smart_money_score")
        series_records[-1]["smart_money_confidence"] = current_summary.get("smart_money_confidence")
        series_records[-1]["weekly_trend"] = current_summary.get("weekly_trend")
        series_records[-1]["weekly_regime"] = current_summary.get("weekly_regime")

    return {
        "status": "ok",
        "metadata": {
            "symbol": symbol,
            "formula_version": FORMULA_VERSION,
            "smart_money_version": SMART_MONEY_VERSION,
            "requested_bars": bar_limit,
            "actual_bars": len(frame),
            "first_session": frame.iloc[0]["date"].strftime("%Y-%m-%d"),
            "last_session": frame.iloc[-1]["date"].strftime("%Y-%m-%d"),
            "timeframe": "1D",
        },
        "data_quality": quality,
        "bars": _series_records(frame, ("open", "high", "low", "close", "volume", "ema20", "ema50", "ema100", "ema200", "rsi14", "cmf20", "rs20", "rvwap20", "vwap20", "clv", "volume_ratio20", "lower_wick_ratio", "upper_wick_ratio", "effort_result"), 3),
        "series": series_records,
        "states": states,
        "events": events,
        "divergences": divergences,
        "current": current_summary,
        "trade_setup": current_summary.get("trade_setup"),
        "score_breakdown": current_summary.get("score_breakdown") or (states[-1].get("score_breakdown") if states else None),
        "volume_context": current_summary.get("volume_context") or (states[-1].get("volume_context") if states else None),
        "emotion_breakdown": current_summary.get("emotion_breakdown"),
        "news_sentiment": current_summary.get("news_sentiment"),
        "methodology": _methodology(),
    }


def _methodology() -> dict[str, Any]:
    return {
        "title": "Smart Money Start V2 & Market Emotion Index",
        "description": "Mô hình ước lượng dòng tiền lớn (OHLCV Proxy) 5 nhóm nhân tố độc lập (Directional Flow 30%, Effort vs Result 25%, Price Acceptance RVWAP20 20%, Structure & RS 15%, Participation 10%), tổng hợp đa khung thời gian tuần hoàn tất (strictly causal), và máy trạng thái 7 pha với độ trễ 2 phiên; không phải dữ liệu sổ lệnh tick/order-book thực tế của tổ chức.",
        "score_notice": "Điểm số Smart Money Score (0–100) và Market Emotion Index phản ánh mức độ đồng thuận định lượng và vị thế tương đối, không phải cam kết xác suất thắng tuyệt đối.",
        "execution_notice": "Tín hiệu được xác nhận sau khi đóng nến ngày và kiểm định tại phiên tiếp theo.",
        "disclaimer": "Sản phẩm phục vụ nghiên cứu và hỗ trợ ra quyết định đầu tư, không phải hệ thống giao dịch tự động.",
    }


def _load_analysis(symbol: str, bar_limit: int) -> tuple[dict[str, Any], pd.DataFrame]:
    symbol, bar_limit = _validate_request(symbol, bar_limit)
    end = date.today()
    start = end - timedelta(days=max(550, int(bar_limit * 1.8) + 120))
    stock = get_verified_history(symbol, start.isoformat(), end.isoformat(), require_store=False)
    stock_frame = _normalise_frame(stock.frame)
    benchmark_frame: Optional[pd.DataFrame] = None
    try:
        benchmark = get_verified_history("VNINDEX", start.isoformat(), end.isoformat(), require_store=False)
        benchmark_frame = _normalise_frame(benchmark.frame)
    except HistoryUnavailable:
        benchmark_frame = None
    news_sentiment = _analyze_news_sentiment(symbol)
    calculated_full = calculate_indicator(stock_frame, benchmark_frame, news_sentiment)
    calculated = calculated_full.tail(bar_limit).reset_index(drop=True)
    quality = _quality_payload(stock, benchmark_frame is not None and not benchmark_frame.empty)
    return _build_analysis_payload(symbol, bar_limit, calculated, quality, news_sentiment), calculated


def get_bottom_analysis(symbol: str, bar_limit: int = DEFAULT_BAR_LIMIT) -> dict[str, Any]:
    symbol, bar_limit = _validate_request(symbol, bar_limit)
    key = (symbol, bar_limit, False)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[1])
    payload, _ = _load_analysis(symbol, bar_limit)
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


def _event_study(frame: pd.DataFrame) -> dict[str, Any]:
    """Nghiên cứu sự kiện (Event Study) tại các mốc 3, 5, 10, 20, 60 phiên."""
    output: dict[str, Any] = {}
    
    categories = [
        ("BOTTOM_WATCH", frame.index[(frame["state"] == "BOTTOM_WATCH") & frame["is_event"]]),
        ("BB", frame.index[frame["signal"] == "BB"]),
        ("DISTRIBUTION_CONTRACTION", frame.index[(frame["state"] == "DISTRIBUTION_CONTRACTION") & frame["is_event"]]),
        ("BS", frame.index[frame["signal"] == "BS"]),
    ]

    for cat_name, idx_list in categories:
        rows = []
        excluded = 0
        for index in idx_list:
            item: dict[str, Any] = {
                "date": frame.iloc[index]["date"].strftime("%Y-%m-%d"),
                "signal": frame.iloc[index].get("signal"),
                "subtype": frame.iloc[index].get("signal_subtype"),
                "close": float(frame.iloc[index]["close"]),
            }
            complete = True
            for horizon in (3, 5, 10, 20, 60):
                if index + horizon >= len(frame):
                    item[f"return_{horizon}d"] = None
                    complete = False
                else:
                    ret = (frame.iloc[index + horizon]["close"] / frame.iloc[index]["close"] - 1) * 100
                    item[f"return_{horizon}d"] = round(ret, 2)
                    
                    forward_window = frame.iloc[index + 1:index + horizon + 1]
                    mfe = (forward_window["high"].max() / frame.iloc[index]["close"] - 1) * 100
                    mae = (forward_window["low"].min() / frame.iloc[index]["close"] - 1) * 100
                    item[f"mfe_{horizon}d"] = round(mfe, 2)
                    item[f"mae_{horizon}d"] = round(mae, 2)
                    
            if not complete:
                excluded += 1
            rows.append(item)
            
        metrics = {}
        is_bull = cat_name in ("BOTTOM_WATCH", "BB")
        for horizon in (3, 5, 10, 20, 60):
            values = [row[f"return_{horizon}d"] for row in rows if row.get(f"return_{horizon}d") is not None]
            mfes = [row[f"mfe_{horizon}d"] for row in rows if row.get(f"mfe_{horizon}d") is not None]
            maes = [row[f"mae_{horizon}d"] for row in rows if row.get(f"mae_{horizon}d") is not None]
            
            false_rate = None
            if is_bull and horizon >= 5:
                false_count = sum(1 for row in rows if row.get("mae_5d") is not None and row["mae_5d"] <= -3.0 and (row.get("mfe_5d") is None or row["mfe_5d"] < 5.0))
                false_rate = round(false_count / len(values) * 100, 1) if values else None
                
            hit_count = sum(value > 0 for value in values) if is_bull else sum(value < 0 for value in values)
            hit_rate = round(hit_count / len(values) * 100, 1) if values else None

            metrics[f"{horizon}d"] = {
                "median_return_pct": round(float(np.median(values)), 2) if values else None,
                "hit_rate_pct": hit_rate,
                "median_mfe_pct": round(float(np.median(mfes)), 2) if mfes else None,
                "median_mae_pct": round(float(np.median(maes)), 2) if maes else None,
                "false_rate_pct": false_rate,
                "sample_size": len(values),
            }
        output[cat_name] = {"metrics": metrics, "events": rows, "excluded_incomplete_events": excluded}
    return output


def _simulate(frame: pd.DataFrame, initial_capital: float = 100_000_000.0) -> dict[str, Any]:
    commission_rate, sell_tax_rate, slippage_rate = 0.0015, 0.001, 0.001
    cash, shares = float(initial_capital), 0
    entry_index: Optional[int] = None
    entry_price = 0.0
    entry_cost = 0.0
    highest_close = 0.0
    pending_entry = False
    pending_entry_subtype: Optional[str] = None
    pending_exit: Optional[str] = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    skipped = {"no_next_session": 0, "already_in_position": 0, "insufficient_cash": 0, "gap_cancelled": 0}

    for index, row in frame.iterrows():
        # Execute Pending Exit at NEXT Open
        if pending_exit and shares > 0:
            fill = float(row["open"]) * (1 - slippage_rate)
            gross = shares * fill
            exit_cost = gross * (commission_rate + sell_tax_rate)
            cash += gross - exit_cost
            trades.append({
                "entry_date": frame.iloc[entry_index]["date"].strftime("%Y-%m-%d") if entry_index is not None else None,
                "exit_date": row["date"].strftime("%Y-%m-%d"),
                "subtype": pending_entry_subtype or "BB",
                "shares": shares,
                "entry_price": round(entry_price, 2),
                "exit_price": round(fill, 2),
                "pnl": round((gross - exit_cost) - entry_cost, 0),
                "return_pct": round(((gross - exit_cost) / entry_cost - 1) * 100, 3) if entry_cost else None,
                "exit_reason": pending_exit,
                "holding_sessions": index - int(entry_index or index),
            })
            shares, entry_index, entry_price, entry_cost, highest_close = 0, None, 0.0, 0.0, 0.0
            pending_exit = None
            pending_entry_subtype = None

        # Execute Pending Entry at NEXT Open (1% Risk Sizing + Subtype Cap + Even 100-Share Lots)
        if pending_entry and shares == 0:
            prev_row = frame.iloc[index - 1] if index > 0 else row
            prev_close = float(prev_row["close"])
            open_px = float(row["open"])
            
            # Gap Check: If gap up > 3.5% or gap down > 5.0%, cancel entry
            if open_px > prev_close * 1.035 or open_px < prev_close * 0.95:
                skipped["gap_cancelled"] += 1
                pending_entry = False
            else:
                fill = open_px * (1 + slippage_rate)
                unit_cost = fill * (1 + commission_rate)
                
                # Sizing: 1% risk of initial capital
                risk_budget = initial_capital * 0.01
                atr_val = float(row["atr14"]) if _finite(row["atr14"]) is not None and float(row["atr14"]) > 0 else (fill * 0.03)
                stop_dist = max(1.5 * atr_val, fill * 0.03)
                shares_by_risk = int(risk_budget / stop_dist) if stop_dist > 0 else 0
                
                # Subtype max allocation cap
                if pending_entry_subtype == "BB2_SOS_BREAKOUT":
                    max_alloc = 0.40
                elif pending_entry_subtype == "BB3_LPS_PULLBACK":
                    max_alloc = 0.35
                elif pending_entry_subtype == "BB1_SPRING_CONFIRM":
                    max_alloc = 0.25
                else:
                    max_alloc = 0.20
                
                max_cash_for_trade = min(cash, initial_capital * max_alloc)
                shares_by_cap = int(max_cash_for_trade // unit_cost) if unit_cost > 0 else 0
                
                desired_shares = min(shares_by_risk, shares_by_cap) if shares_by_risk > 0 else shares_by_cap
                # Round down to even 100 shares lot
                quantity = (desired_shares // 100) * 100
                if quantity == 0 and cash >= 100 * unit_cost and max_cash_for_trade >= 100 * unit_cost:
                    quantity = 100
                
                if quantity >= 100 and (quantity * unit_cost) <= cash:
                    entry_cost = quantity * unit_cost
                    cash -= entry_cost
                    shares = quantity
                    entry_index = index
                    entry_price = fill
                    highest_close = float(row["close"])
                else:
                    skipped["insufficient_cash"] += 1
                pending_entry = False

        # In-position evaluation
        if shares > 0:
            highest_close = max(highest_close, float(row["close"]))
            held = index - int(entry_index or index)
            trailing_stop = highest_close - 2.5 * float(row["atr14"]) if _finite(row["atr14"]) is not None else -np.inf
            
            if row.get("signal") == "BS":
                pending_exit = f"BS Signal ({row.get('signal_subtype') or 'BS'})"
            elif row["state"] == "DISTRIBUTION_CONTRACTION":
                pending_exit = "distribution_contraction"
            elif int(row["risk_score"]) >= 75:
                pending_exit = "risk_score"
            elif float(row["close"]) <= trailing_stop:
                pending_exit = "atr_trailing_stop"
            elif held >= 60:
                pending_exit = "max_holding"
                
            if row.get("signal") == "BB" or (row["state"] == "EARLY_EXPANSION" and int(row["opportunity_score"]) >= 70):
                skipped["already_in_position"] += 1
        elif (row.get("signal") == "BB") or (row["state"] == "EARLY_EXPANSION" and bool(row["is_event"]) and int(row["opportunity_score"]) >= 70):
            if index + 1 < len(frame):
                pending_entry = True
                pending_entry_subtype = row.get("signal_subtype") or "BB"
            else:
                skipped["no_next_session"] += 1

        equity = cash + shares * float(row["close"])
        benchmark = initial_capital * float(row["close"]) / float(frame.iloc[0]["close"])
        equity_curve.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "equity": round(equity, 0),
            "benchmark": round(benchmark, 0),
            "buy_hold_equity": round(benchmark, 0),
        })

    if shares > 0:
        row = frame.iloc[-1]
        fill = float(row["close"]) * (1 - slippage_rate)
        gross = shares * fill
        exit_cost = gross * (commission_rate + sell_tax_rate)
        cash += gross - exit_cost
        trades.append({
            "entry_date": frame.iloc[entry_index]["date"].strftime("%Y-%m-%d") if entry_index is not None else None,
            "exit_date": row["date"].strftime("%Y-%m-%d"),
            "subtype": pending_entry_subtype or "BB",
            "shares": shares,
            "entry_price": round(entry_price, 2),
            "exit_price": round(fill, 2),
            "pnl": round((gross - exit_cost) - entry_cost, 0),
            "return_pct": round(((gross - exit_cost) / entry_cost - 1) * 100, 3) if entry_cost else None,
            "exit_reason": "forced_end",
            "holding_sessions": len(frame) - 1 - int(entry_index or len(frame) - 1),
        })
        equity_curve[-1]["equity"] = round(cash, 0)

    equity_values = pd.Series([item["equity"] for item in equity_curve], dtype=float)
    returns = equity_values.pct_change(fill_method=None).dropna()
    running_max = equity_values.cummax()
    drawdown = equity_values / running_max - 1
    years = max((frame.iloc[-1]["date"] - frame.iloc[0]["date"]).days / 365.25, 1 / 365.25)
    final_equity = float(equity_values.iloc[-1])
    winning = [trade for trade in trades if trade["pnl"] > 0]
    gross_profit = sum(trade["pnl"] for trade in trades if trade["pnl"] > 0)
    gross_loss = abs(sum(trade["pnl"] for trade in trades if trade["pnl"] < 0))
    summary = {
        "initial_capital": initial_capital, "final_equity": round(final_equity, 0),
        "return_pct": round((final_equity / initial_capital - 1) * 100, 3),
        "buy_hold_return_pct": round((frame.iloc[-1]["close"] / frame.iloc[0]["close"] - 1) * 100, 3),
        "cagr_pct": round(((final_equity / initial_capital) ** (1 / years) - 1) * 100, 3),
        "sharpe": round(float(returns.mean() / returns.std() * np.sqrt(252)), 3) if len(returns) > 1 and returns.std() > 0 else None,
        "max_drawdown_pct": round(float(drawdown.min() * 100), 3),
        "total_trades": len(trades), "win_rate_pct": round(len(winning) / len(trades) * 100, 2) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
    }
    return {"summary": summary, "equity_curve": equity_curve, "trades": list(reversed(trades)), "skipped": skipped}


def get_bottom_backtest(symbol: str, bar_limit: int = DEFAULT_BAR_LIMIT) -> dict[str, Any]:
    symbol, bar_limit = _validate_request(symbol, bar_limit)
    key = (symbol, bar_limit, True)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[1])
    analysis, frame = _load_analysis(symbol, bar_limit)
    if analysis["status"] != "ok":
        payload = {**analysis, "event_study": {}, "summary": None, "equity_curve": [], "trades": [], "execution_audit": {}}
    else:
        simulation = _simulate(frame)
        payload = {
            "status": "ok", "metadata": analysis["metadata"], "data_quality": analysis["data_quality"],
            "parameters": {
                "entry": "BB confirmed (BB1/BB2/BB3) hoặc EARLY_EXPANSION; khớp mở cửa phiên kế tiếp",
                "exit": "BS confirmed (BS1/BS2), Co phân phối, trailing stop 2,5 ATR hoặc 60 phiên",
                "initial_capital": 100_000_000, "commission_pct": 0.15,
                "sell_tax_pct": 0.1, "slippage_pct": 0.1, "long_only": True,
            },
            "event_study": _event_study(frame), "summary": simulation["summary"],
            "equity_curve": simulation["equity_curve"], "trades": simulation["trades"],
            "execution_audit": {
                "events": int(frame["is_event"].sum()),
                "bb_signals": int((frame["signal"] == "BB").sum()),
                "bs_signals": int((frame["signal"] == "BS").sum()),
                "trades_created": len(simulation["trades"]),
                "skipped": simulation["skipped"],
                "causal_execution": True,
                "forced_final_exit": any(trade["exit_reason"] == "forced_end" for trade in simulation["trades"]),
            },
            "methodology": analysis["methodology"],
        }
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


def clear_bottom_indicator_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
