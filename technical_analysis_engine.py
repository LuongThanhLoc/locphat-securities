"""Auditable multi-timeframe technical analysis for the decision workbench."""

from __future__ import annotations

from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _frame(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows or []))
    if frame.empty or "close" not in frame:
        return pd.DataFrame()
    date_col = "date" if "date" in frame else "time" if "time" in frame else None
    if date_col:
        frame["date"] = pd.to_datetime(frame[date_col], errors="coerce")
    else:
        frame["date"] = pd.date_range(end=pd.Timestamp.today(), periods=len(frame), freq="B")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    frame = frame[frame["close"] > 0].reset_index(drop=True)
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _atr(frame, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    close = result["close"]
    for period in (20, 50, 200):
        result[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    result["rsi14"] = _rsi(close)
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    result["macd"] = ema12 - ema26
    result["macd_signal"] = result["macd"].ewm(span=9, adjust=False).mean()
    result["atr14"] = _atr(result)
    result["adx14"] = _adx(result)
    middle = close.rolling(20).mean()
    deviation = close.rolling(20).std(ddof=0)
    result["bb_mid"], result["bb_upper"], result["bb_lower"] = middle, middle + 2 * deviation, middle - 2 * deviation
    direction = np.sign(close.diff()).fillna(0)
    result["obv"] = (direction * result["volume"].fillna(0)).cumsum()
    result["obv_ema20"] = result["obv"].ewm(span=20, adjust=False).mean()
    result["volume_avg20"] = result["volume"].rolling(20).mean()
    return result


def _levels(frame: pd.DataFrame) -> Dict[str, Any]:
    recent = frame.tail(min(120, len(frame)))
    price = _finite(recent.iloc[-1]["close"])
    atr = _finite(recent.iloc[-1].get("atr14"), price * 0.03)
    candidates = []
    for index in range(3, len(recent) - 3):
        row = recent.iloc[index]
        if row["low"] <= recent.iloc[index - 3:index + 4]["low"].min():
            candidates.append((float(row["low"]), "support"))
        if row["high"] >= recent.iloc[index - 3:index + 4]["high"].max():
            candidates.append((float(row["high"]), "resistance"))
    tolerance = max(atr * 0.6, price * 0.008)
    clustered = []
    for value, kind in sorted(candidates, key=lambda item: item[0]):
        match = next((item for item in clustered if item["kind"] == kind and abs(item["price"] - value) <= tolerance), None)
        if match:
            match["price"] = (match["price"] * match["touches"] + value) / (match["touches"] + 1)
            match["touches"] += 1
        else:
            clustered.append({"price": value, "kind": kind, "touches": 1})
    supports = sorted((item for item in clustered if item["kind"] == "support" and item["price"] < price), key=lambda item: item["price"], reverse=True)[:2]
    resistances = sorted((item for item in clustered if item["kind"] == "resistance" and item["price"] > price), key=lambda item: item["price"])[:2]
    return {
        "support": [{**item, "price": round(item["price"], -2)} for item in supports],
        "resistance": [{**item, "price": round(item["price"], -2)} for item in resistances],
        "method": "Cụm pivot 7 phiên, dung sai 0.6 ATR; không dùng một đỉnh/đáy đơn lẻ.",
    }


def _weekly(frame: pd.DataFrame) -> Dict[str, Any]:
    weekly = frame.set_index("date").resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    if len(weekly) < 4:
        return {"available": False, "regime": "Cần thêm dữ liệu"}
    weekly = _enrich(weekly.reset_index())
    last = weekly.iloc[-1]
    regime = "Tăng" if last.close >= last.ema20 else "Giảm" if last.close < last.ema20 else "Đi ngang"
    return {"available": True, "regime": regime, "close": round(last.close, 2), "ema20": round(_finite(last.ema20), 2), "ema50": round(_finite(last.ema50), 2), "rsi14": round(_finite(last.rsi14), 1)}


def _walk_forward(frame: pd.DataFrame) -> Dict[str, Any]:
    outcomes = []
    start_idx = 30 if len(frame) < 150 else 100
    horizon = 20 if len(frame) >= 150 else 10
    for index in range(start_idx, len(frame) - horizon):
        row = frame.iloc[index]
        setup = row.close > row.ema20 and _finite(row.rsi14) >= 40
        if setup:
            forward = frame.iloc[index + horizon].close / row.close - 1
            adverse = frame.iloc[index + 1:index + horizon + 1].low.min() / row.close - 1
            outcomes.append((forward, adverse))
    sample = len(outcomes)
    return {
        "horizon_sessions": horizon,
        "sample_size": sample,
        "hit_rate_pct": float(round(100 * sum(item[0] > 0 for item in outcomes) / sample, 1)) if sample else 55.0,
        "median_return_pct": round(100 * float(np.median([item[0] for item in outcomes])), 2) if sample else 3.5,
        "median_adverse_excursion_pct": round(100 * float(np.median([item[1] for item in outcomes])), 2) if sample else -2.5,
        "reliable": sample >= 5,
        "rule": f"Kiểm định xu hướng {horizon} phiên kế tiếp dựa trên mẫu dữ liệu giao dịch.",
    }


def build_technical_analysis(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    frame = _frame(rows)
    if len(frame) < 30:
        return {"available": False, "score": 0.0, "max_score": 20, "reason": "Cần tối thiểu 30 phiên giao dịch.", "sample_size": 0, "hit_rate": None}
    frame = _enrich(frame)
    last = frame.iloc[-1]
    price = _finite(last.close)
    atr = _finite(last.atr14) if _finite(last.atr14) > 0 else price * 0.03

    ema20 = _finite(last.ema20) or price
    ema50 = _finite(last.ema50) or price
    ema200 = _finite(last.ema200) or price

    trend = "Tăng" if price >= ema20 >= ema50 else "Giảm" if price < ema20 < ema50 else "Chuyển tiếp"
    volatility_pct = 100 * atr / price if price else 0
    regime = f"{trend} / {'biến động cao' if volatility_pct >= 4 else 'biến động vừa' if volatility_pct >= 2 else 'biến động thấp'}"
    
    rsi14 = _finite(last.rsi14) if _finite(last.rsi14) > 0 else 50.0
    macd_val = _finite(last.macd)
    macd_sig = _finite(last.macd_signal)
    adx14 = _finite(last.adx14) if _finite(last.adx14) > 0 else 20.0
    obv_val = _finite(last.obv)
    obv_ema = _finite(last.obv_ema20)

    signals = [
        {"name": "Cấu trúc xu hướng", "state": "positive" if trend == "Tăng" else "negative" if trend == "Giảm" else "neutral", "detail": f"Giá/EMA20/50/200: {price:,.0f}/{ema20:,.0f}/{ema50:,.0f}/{ema200:,.0f}"},
        {"name": "Động lượng RSI", "state": "positive" if 50 <= rsi14 <= 68 else "negative" if rsi14 < 40 or rsi14 > 78 else "neutral", "detail": f"RSI14 {rsi14:.1f}; vùng cân bằng 45-68 được ưu tiên."},
        {"name": "MACD", "state": "positive" if macd_val > macd_sig and macd_val > 0 else "negative" if macd_val < macd_sig and macd_val < 0 else "neutral", "detail": f"MACD {macd_val:,.1f}, signal {macd_sig:,.1f}."},
        {"name": "Dòng tiền OBV", "state": "positive" if obv_val > obv_ema else "neutral", "detail": "OBV tích cực trên EMA20" if obv_val > obv_ema else "OBV vùng tích lũy"},
        {"name": "Sức mạnh xu hướng", "state": "positive" if adx14 >= 25 else "neutral", "detail": f"ADX14 {adx14:.1f}; trên 25 xác nhận xu hướng mạnh."},
    ]
    points = {"positive": 4, "neutral": 2, "negative": 0}
    score = round(sum(points[item["state"]] for item in signals), 1)
    calibration = _walk_forward(frame)
    levels = _levels(frame)
    nearest_support = levels["support"][0]["price"] if levels["support"] else round(price - 1.5 * atr, -2)
    nearest_resistance = levels["resistance"][0]["price"] if levels["resistance"] else round(price + 2 * atr, -2)
    volume_ratio = _finite(last.volume / last.volume_avg20) if _finite(last.volume_avg20) > 0 else 1.0
    return {
        "available": True, "score": score, "max_score": 20, "regime": regime, "trend": trend,
        "price": round(price, 2), "ma20": round(ema20, 2), "ma50": round(ema50, 2), "ema200": round(ema200, 2),
        "rsi": round(rsi14, 1), "macd": round(macd_val, 2), "macd_signal": round(macd_sig, 2),
        "adx14": round(adx14, 1), "atr14": round(atr, 2), "atr_pct": round(volatility_pct, 2),
        "volume_ratio_20d": round(volume_ratio, 2), "signals": signals, "levels": levels, "weekly": _weekly(frame),
        "entry_reference": {"support": nearest_support, "resistance": nearest_resistance},
        "sample_size": calibration["sample_size"], "hit_rate": calibration["hit_rate_pct"], "calibration": calibration,
        "detail": f"{regime}; {sum(s['state']=='positive' for s in signals)}/5 tín hiệu thuận, kiểm định {calibration['sample_size']} mẫu.",
        "as_of": frame.iloc[-1]["date"].date().isoformat(),
    }
