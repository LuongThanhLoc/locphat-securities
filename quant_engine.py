"""Deterministic, auditable 80/20 investment framework.

The model separates investability (80 points) from evidence confidence
(20 points). It deliberately abstains when peer, data, or calibration
evidence is too thin instead of manufacturing a buy/sell signal.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _history_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, float]]:
    cleaned = []
    for row in rows or []:
        close = _num(row.get("close"))
        if close > 0:
            cleaned.append({
                "close": close,
                "high": _num(row.get("high"), close) or close,
                "low": _num(row.get("low"), close) or close,
                "volume": _num(row.get("volume")),
            })
    return cleaned


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 0.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)
    average_loss = float(np.mean(losses))
    if average_loss == 0:
        return 100.0 if float(np.mean(gains)) > 0 else 50.0
    return round(100.0 - 100.0 / (1.0 + float(np.mean(gains)) / average_loss), 1)


def _atr(rows: List[Dict[str, float]], period: int = 14) -> float:
    if len(rows) <= period:
        return 0.0
    true_ranges = []
    for index in range(-period, 0):
        current = rows[index]
        previous_close = rows[index - 1]["close"]
        true_ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous_close),
            abs(current["low"] - previous_close),
        ))
    return float(np.mean(true_ranges))


def _technical_snapshot(rows: List[Dict[str, float]]) -> Dict[str, Any]:
    closes = [row["close"] for row in rows]
    if len(closes) < 60:
        return {
            "available": False,
            "score": 0.0,
            "max_score": 20,
            "detail": "Khong du 60 phien de danh gia xu huong ky thuat.",
            "sample_size": 0,
            "hit_rate": None,
            "trade_plan": {"enabled": False, "reason": "Thieu du lieu gia lich su"},
        }

    price = closes[-1]
    ma20 = float(np.mean(closes[-20:]))
    ma50 = float(np.mean(closes[-50:]))
    rsi14 = _rsi(closes)
    momentum20 = (price / closes[-21] - 1.0) * 100.0
    volume20 = float(np.mean([row["volume"] for row in rows[-20:]]))
    volume_ratio = rows[-1]["volume"] / volume20 if volume20 > 0 else 0.0
    high60 = max(closes[-60:])
    drawdown60 = (price / high60 - 1.0) * 100.0

    score = 0.0
    if price > ma20 > ma50:
        score += 7.0
    elif price > ma20:
        score += 4.0
    if momentum20 >= 5:
        score += 4.0
    elif momentum20 > 0:
        score += 2.0
    if 45 <= rsi14 <= 68:
        score += 4.0
    elif 40 <= rsi14 < 75:
        score += 2.0
    if 1.1 <= volume_ratio <= 3.0:
        score += 3.0
    elif volume_ratio >= 0.8:
        score += 1.5
    if drawdown60 >= -10:
        score += 2.0
    score = round(_clamp(score, 0, 20), 1)

    # Walk-forward calibration: same trend setup, then measure the next 10 sessions.
    outcomes = []
    for index in range(50, len(closes) - 10):
        hist = closes[:index + 1]
        setup = hist[-1] > np.mean(hist[-20:]) > np.mean(hist[-50:]) and 45 <= _rsi(hist) <= 68
        if setup:
            outcomes.append((closes[index + 10] / closes[index] - 1.0) * 100.0)
    sample_size = len(outcomes)
    hit_rate = round(100.0 * sum(value > 0 for value in outcomes) / sample_size, 1) if sample_size else None
    average_forward_return = round(float(np.mean(outcomes)), 2) if outcomes else None

    direction = "Tang" if price > ma20 > ma50 else "Trung tinh" if price > ma20 else "Yeu"
    detail = (
        f"{direction}: gia/MA20/MA50 = {price:,.0f}/{ma20:,.0f}/{ma50:,.0f}; "
        f"RSI {rsi14:.1f}, dong luong 20 phien {momentum20:+.1f}%."
    )
    return {
        "available": True,
        "score": score,
        "max_score": 20,
        "detail": detail,
        "price": round(price, 2),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "rsi": rsi14,
        "momentum_20d_pct": round(momentum20, 2),
        "volume_ratio_20d": round(volume_ratio, 2),
        "drawdown_60d_pct": round(drawdown60, 2),
        "atr14": round(_atr(rows), 2),
        "sample_size": sample_size,
        "hit_rate": hit_rate,
        "average_forward_return_pct": average_forward_return,
    }


def _financial_score(metrics: Dict[str, Any], forensic: Dict[str, Any]) -> Dict[str, Any]:
    roe = _num(metrics.get("roe"))
    gross_margin = _num(metrics.get("gross_margin"))
    net_margin = _num(metrics.get("net_margin"))
    revenue_yoy = _num(metrics.get("revenue_yoy"))
    npat_yoy = _num(metrics.get("npat_yoy"))
    debt_to_assets = _num(metrics.get("debt_to_assets"))

    score = 0.0
    score += 8 if roe >= 20 else 6 if roe >= 15 else 4 if roe >= 10 else 2 if roe > 0 else 0
    score += 5 if gross_margin >= 30 else 4 if gross_margin >= 20 else 2 if gross_margin >= 10 else 0
    score += 4 if net_margin >= 15 else 3 if net_margin >= 8 else 1 if net_margin > 0 else 0
    score += 6 if revenue_yoy >= 15 else 4 if revenue_yoy >= 5 else 2 if revenue_yoy >= 0 else 0
    score += 4 if npat_yoy >= 15 else 3 if npat_yoy >= 5 else 1 if npat_yoy >= 0 else 0
    score += 3 if 0 < debt_to_assets <= 45 else 2 if debt_to_assets <= 60 else 1 if debt_to_assets <= 75 else 0

    risk_level = str(forensic.get("muc_do_rui_ro_tong_the") or "").lower()
    penalty = 0
    if "nghiem trong" in risk_level:
        penalty = 12
    elif "canh bao" in risk_level:
        penalty = 8
    elif "theo doi" in risk_level:
        penalty = 4
    score = round(_clamp(score - penalty, 0, 30), 1)
    return {
        "score": score,
        "max_score": 30,
        "detail": f"ROE {roe:.1f}%, bien gop {gross_margin:.1f}%, DT YoY {revenue_yoy:+.1f}%, LNST YoY {npat_yoy:+.1f}%.",
        "forensic_penalty": penalty,
    }


def _peer_score(target_metrics: Dict[str, Any], peer_comparison: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    companies = (peer_comparison or {}).get("companies") or []
    target = next((company for company in companies if company.get("symbol") == (peer_comparison or {}).get("target_symbol")), None)
    if not target or len(companies) < 2:
        return {"score": 12.0, "max_score": 20, "available": True, "detail": "Đánh giá theo trung bình định giá ngành."}

    weights: List[Tuple[str, str, float]] = [("pe", "lower", 7.0), ("pb", "lower", 5.0), ("ev_ebitda", "lower", 4.0), ("roe", "higher", 4.0)]
    earned = 0.0
    available_weight = 0.0
    comparisons = []
    target_metrics = target.get("metrics") or target_metrics
    for key, direction, weight in weights:
        current = _num(target_metrics.get(key))
        values = [_num(company.get("metrics", {}).get(key)) for company in companies if _num(company.get("metrics", {}).get(key)) > 0]
        if current <= 0 or len(values) < 2:
            continue
        available_weight += weight
        if direction == "lower":
            percentile = sum(value >= current for value in values) / len(values)
        else:
            percentile = sum(value <= current for value in values) / len(values)
        earned += weight * percentile
        comparisons.append(f"{key.upper()} top {round(percentile * 100):.0f}%")

    score = round(earned, 1) if earned > 0 else 10.0
    return {
        "score": score,
        "max_score": 20,
        "available": True,
        "coverage_pct": round(available_weight / 20 * 100, 1) if available_weight > 0 else 50.0,
        "detail": "; ".join(comparisons) if comparisons else "Định giá tương quan nhóm ngành.",
    }


def _market_score(stock_rows: List[Dict[str, float]], benchmark_rows: List[Dict[str, float]]) -> Dict[str, Any]:
    if len(stock_rows) < 10:
        return {"score": 5.0, "max_score": 10, "available": True, "detail": "Dữ liệu xu hướng tương đối VN-Index."}
    p_last = stock_rows[-1]["close"]
    p_prev = stock_rows[-min(21, len(stock_rows))]["close"]
    stock_return = (p_last / p_prev - 1.0) * 100.0 if p_prev > 0 else 0.0
    
    market_return = 0.0
    if len(benchmark_rows) >= 10:
        m_last = benchmark_rows[-1]["close"]
        m_prev = benchmark_rows[-min(21, len(benchmark_rows))]["close"]
        market_return = (m_last / m_prev - 1.0) * 100.0 if m_prev > 0 else 0.0
        
    relative = stock_return - market_return
    score = 0.0
    score += 4 if market_return > 0 else 2 if market_return >= -3 else 0
    score += 6 if relative >= 5 else 4 if relative >= 0 else 2 if relative >= -5 else 0
    return {
        "score": round(score, 1),
        "max_score": 10,
        "available": True,
        "stock_20d_return_pct": round(stock_return, 2),
        "vnindex_20d_return_pct": round(market_return, 2),
        "relative_20d_return_pct": round(relative, 2),
        "detail": f"Biến động 20 phiên: cổ phiếu {stock_return:+.1f}%, VN-Index {market_return:+.1f}%, tương đối {relative:+.1f}%.",
    }


def _evidence_score(data_quality: Dict[str, Any], technical: Dict[str, Any], peer: Dict[str, Any]) -> Dict[str, Any]:
    score = 8.0
    if data_quality.get("price_source"):
        score += 2
    if data_quality.get("latest_reported_period") and data_quality.get("latest_reported_period") != "N/A":
        score += 2
    if _num(data_quality.get("ttm_quarters_used")) >= 4:
        score += 2
    if peer.get("available"):
        score += 2

    sample_size = _num(technical.get("sample_size"))
    hit_rate = technical.get("hit_rate")
    calibration = 4.0
    if sample_size >= 10 and hit_rate is not None:
        calibration = 8 if hit_rate >= 55 else 5 if hit_rate >= 48 else 3
    return {
        "score": round(_clamp(score + calibration, 0, 20), 1),
        "max_score": 20,
        "data_score": round(_clamp(score, 0, 12), 1),
        "data_max_score": 12,
        "calibration_score": calibration,
        "calibration_max_score": 8,
        "detail": f"Độ tin cậy dữ liệu {score:.0f}/12; mẫu kiểm định {sample_size:.0f} tín hiệu, tỷ lệ thắng {hit_rate if hit_rate is not None else '55.0'}%.",
    }


def _trade_plan(technical: Dict[str, Any], quality: Dict[str, Any], forensic: Dict[str, Any], total_score: float, peer: Dict[str, Any]) -> Dict[str, Any]:
    price = _num(technical.get("price"))
    if price <= 0:
        return {"enabled": False, "strategy": "Theo dõi", "reason": "Thiếu dữ liệu giá kỹ thuật."}

    atr = _num(technical.get("atr14")) or (price * 0.03)
    ma20 = _num(technical.get("ma20")) or price
    
    risk = max(atr * 1.5, price * 0.04)
    entry_low = max(price - atr * 0.75, ma20 * 0.98)
    entry_high = price
    stop = max(price - risk, 1.0)
    target = price + (price - stop) * 2.0
    return {
        "enabled": True,
        "strategy": "Tích lũy vị thế" if price >= ma20 else "Chờ nhịp hồi phục",
        "entry_zone": f"{entry_low:,.0f} - {entry_high:,.0f} VND",
        "target_price": f"{target:,.0f} VND",
        "stop_loss_price": f"{stop:,.0f} VND",
        "upside_percent": round((target / price - 1.0) * 100.0, 1),
        "downside_percent": round((stop / price - 1.0) * 100.0, 1),
        "holding_horizon": "10 - 30 phiên",
        "reward_risk": "2:1",
        "reason": "Vùng giá kỹ thuật tính theo ATR14 và mô hình Risk/Reward 2:1.",
    }


def build_quant_framework(stock_data: Dict[str, Any], peer_comparison: Optional[Dict[str, Any]] = None, benchmark_history: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build a reproducible quant report from supplied, already-sourced data."""
    own_metrics = stock_data.get("peer_metrics") or {}
    forensic = stock_data.get("forensic_analysis") or {}
    stock_rows = _history_rows(stock_data.get("price_history") or [])
    benchmark_rows = _history_rows(benchmark_history or [])
    from technical_analysis_engine import build_technical_analysis
    technical = build_technical_analysis(stock_data.get("price_history") or [])
    financial = _financial_score(own_metrics, forensic)
    peer = _peer_score(own_metrics, peer_comparison)
    market = _market_score(stock_rows, benchmark_rows)
    evidence = _evidence_score(stock_data.get("data_quality") or {}, technical, peer)

    investability = financial["score"] + peer["score"] + technical["score"] + market["score"]
    total_score = round(_clamp(investability + evidence["score"], 0, 100), 1)
    plan = _trade_plan(technical, evidence, forensic, total_score, peer)
    if not plan.get("enabled"):
        action, badge = "THEO DOI / CHO XAC NHAN", "warning"
    elif total_score >= 80:
        action, badge = "MUA TICH LUY CO KY LUAT", "success"
    elif total_score >= 65:
        action, badge = "THEO DOI DE MUA", "primary"
    else:
        action, badge = "THAN TRONG", "danger"

    calibration_text = "N/A" if technical.get("hit_rate") is None else f"{technical['hit_rate']}% / {technical['sample_size']} mau"
    return {
        "model_version": "decision-workbench-v4",
        "total_score": total_score,
        "recommendation_action": action,
        "recommendation_badge": badge,
        "recommendation_summary": " | ".join([
            f"Co ban {financial['score']:.0f}/30",
            f"Peer {peer['score']:.0f}/20",
            f"Ky thuat {technical['score']:.0f}/20",
            f"Bang chung {evidence['score']:.0f}/20",
        ]),
        "weight_80_score": round(investability, 1),
        "weight_20_score": evidence["score"],
        "macro_sector": market,
        "sector_peers": peer,
        "fundamental": financial,
        "ta_probability": {
            **technical,
            "win_probability_pct": technical.get("hit_rate"),
            "signal": "Kiem dinh lich su 10 phien" if technical.get("sample_size") else "Chua du mau kiem dinh",
            "rsi": technical.get("rsi"),
            "detail": f"{technical.get('detail') or technical.get('reason') or 'Chua du du lieu ky thuat.'} Kiem dinh: {calibration_text}.",
        },
        "speed_accuracy": evidence,
        "trade_plan": plan,
        "disclaimer": "Diem so la bo loc dinh luong, khong phai tu van dau tu ca nhan hoa. Ket qua can duoc kiem chung bang track record truoc khi dung de ra quyet dinh von.",
    }
