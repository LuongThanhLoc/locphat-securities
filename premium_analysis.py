"""Auditable premium decision packet for the stock Deep Analysis screen.

All prices, scores and sizing rules are calculated here. The language model is
only allowed to explain this packet and never owns an investment number.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, Iterable, Optional


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def _fmt_price(value: float) -> str:
    return f"{round(value / 100) * 100:,.0f} VND"


def _positive(values: Iterable[Any]) -> list[float]:
    return [number for value in values if (number := _num(value)) > 0]


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * ratio
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _valuation(stock_data: Dict[str, Any], peer_data: Dict[str, Any]) -> Dict[str, Any]:
    valuation = stock_data.get("valuation") or {}
    current = _num(stock_data.get("current_price"))
    eps = _num(valuation.get("eps_ttm"))
    bvps = _num(valuation.get("bvps"))
    sector = str(stock_data.get("sector_name") or "").lower()
    companies = [c for c in peer_data.get("companies", []) if c.get("symbol") != stock_data.get("symbol")]
    pe_values = _positive((c.get("metrics") or {}).get("pe") for c in companies)
    pb_values = _positive((c.get("metrics") or {}).get("pb") for c in companies)
    is_financial = any(term in sector for term in ("ngân hàng", "chứng khoán", "bảo hiểm", "tài chính"))

    methods = []
    scenarios: Dict[str, float] = {}
    if not is_financial and eps > 0 and len(pe_values) >= 2:
        q25, q50, q75 = (_percentile(pe_values, q) for q in (0.25, 0.5, 0.75))
        methods.append({"method": "P/E peer TTM", "multiple": round(q50, 2), "fair_value": eps * q50})
        scenarios["bear"] = eps * q25
        scenarios["base"] = eps * q50
        scenarios["bull"] = eps * q75
    if bvps > 0 and len(pb_values) >= 2:
        q25, q50, q75 = (_percentile(pb_values, q) for q in (0.25, 0.5, 0.75))
        methods.append({"method": "P/B peer", "multiple": round(q50, 2), "fair_value": bvps * q50})
        candidate = {"bear": bvps * q25, "base": bvps * q50, "bull": bvps * q75}
        if scenarios and not is_financial:
            scenarios = {key: (scenarios[key] * 0.65 + candidate[key] * 0.35) for key in scenarios}
        else:
            scenarios = candidate

    fair_value = scenarios.get("base", 0.0)
    margin = (fair_value / current - 1) * 100 if current > 0 and fair_value > 0 else None
    return {
        "available": bool(fair_value),
        "methodology": "Blend 65% P/E TTM + 35% P/B peer" if len(methods) == 2 else (methods[0]["method"] if methods else "Khong du du lieu"),
        "peer_count": len(companies),
        "methods": [{**m, "fair_value": round(m["fair_value"], -2)} for m in methods],
        "fair_value": round(fair_value, -2) if fair_value else None,
        "fair_value_range": [round(scenarios.get("bear", 0), -2), round(scenarios.get("bull", 0), -2)] if fair_value else [],
        "margin_of_safety_pct": round(margin, 1) if margin is not None else None,
        "scenarios": {
            key: {"fair_value": round(value, -2), "upside_pct": round((value / current - 1) * 100, 1) if current else None}
            for key, value in scenarios.items()
        },
        "warning": "Dinh gia tuong doi theo peer, khong phai gia muc tieu bao dam." if fair_value else "Khong cong bo fair value khi EPS/BVPS hoac peer khong du dieu kien.",
    }


def build_premium_analysis(stock_data: Dict[str, Any], peer_data: Dict[str, Any]) -> Dict[str, Any]:
    framework = stock_data.get("decision_framework") or {}
    technical = framework.get("ta_probability") or {}
    evidence = framework.get("speed_accuracy") or {}
    fundamental = framework.get("fundamental") or {}
    forensic = stock_data.get("forensic_analysis") or {}
    valuation = _valuation(stock_data, peer_data)
    current = _num(stock_data.get("current_price"))
    score = _num(framework.get("total_score"))
    data_score = _num(evidence.get("data_score"))
    sample_size = int(_num(technical.get("sample_size")))
    peer_ready = bool((framework.get("sector_peers") or {}).get("available"))
    calibration = technical.get("calibration") or {}
    technical_ready = bool(technical.get("available")) and bool(calibration.get("reliable"))
    report_period = (stock_data.get("data_quality") or {}).get("latest_reported_period") or "N/A"

    gates = {
        "current_price": current > 0,
        "four_quarter_ttm": _num((stock_data.get("data_quality") or {}).get("ttm_quarters_used")) >= 4,
        "peer_coverage": peer_ready,
        "technical_sample": technical_ready,
        "valuation": valuation["available"],
        "forensic_clear": "nghiêm trọng" not in str(forensic.get("muc_do_rui_ro_tong_the") or "").lower(),
    }
    passed = sum(gates.values())
    confidence_score = round(100 * passed / len(gates) * 0.65 + min(data_score / 12, 1) * 35, 1)
    confidence_grade = "A" if confidence_score >= 85 else "B" if confidence_score >= 70 else "C" if confidence_score >= 55 else "D"

    mos = valuation.get("margin_of_safety_pct")
    trend_positive = current > _num(technical.get("ma20")) > _num(technical.get("ma50")) > 0
    if confidence_score < 55 or score < 45:
        action, max_weight = "TRÁNH / CHỜ DỮ LIỆU", 0
    elif score >= 72 and mos is not None and mos >= 15 and trend_positive:
        action, max_weight = "MUA TÍCH LŨY CÓ ĐIỀU KIỆN", 10
    elif score >= 60 and mos is not None and mos >= 5:
        action, max_weight = "THEO DÕI ĐIỂM MUA", 5
    elif mos is not None and mos < -15:
        action, max_weight = "GIẢM TỶ TRỌNG / KHÔNG MUA ĐUỔI", 0
    else:
        action, max_weight = "THEO DÕI / CHỜ XÁC NHẬN", 0

    atr = _num(technical.get("atr14"))
    ma20 = _num(technical.get("ma20"))
    references = technical.get("entry_reference") or {}
    support = _num(references.get("support"), ma20 or current)
    resistance = _num(references.get("resistance"))
    entry_mid = min(current, max(support, ma20)) if support > 0 and ma20 > 0 else min(current, support or ma20 or current)
    entry_low = max(0, entry_mid - 0.25 * atr) if atr else entry_mid * 0.98
    entry_high = entry_mid + 0.25 * atr if atr else entry_mid * 1.01
    stop = max(0, min(support, entry_low) - 1.0 * atr) if atr else entry_low * 0.94
    fair_value = _num(valuation.get("fair_value"))
    target_candidates = [value for value in (fair_value, resistance) if value > entry_high]
    target = min(target_candidates) if target_candidates else 0.0
    executable = max_weight > 0 and all((current > 0, target > entry_high, stop < entry_low))
    reward_risk = (target - entry_mid) / (entry_mid - stop) if executable and entry_mid > stop else 0
    if reward_risk < 1.5:
        executable = False
        max_weight = 0
        if action.startswith("MUA"):
            action = "THEO DÕI / TỶ LỆ LỢI NHUẬN-RỦI RO CHƯA ĐẠT"
        elif action == "THEO DÕI ĐIỂM MUA":
            action = "THEO DÕI / CHỜ XÁC NHẬN KỸ THUẬT"

    trade_setup = {
        "enabled": executable,
        "entry_zone": f"{_fmt_price(entry_low)} - {_fmt_price(entry_high)}" if executable else "N/A",
        "target_price": _fmt_price(target) if executable else "N/A",
        "upside_percent": round((target / entry_mid - 1) * 100, 1) if executable else None,
        "stop_loss_price": _fmt_price(stop) if executable else "N/A",
        "downside_risk_percent": round((stop / entry_mid - 1) * 100, 1) if executable else None,
        "reward_risk": round(reward_risk, 2) if executable else None,
        "holding_horizon": "10-30 phiên; đánh giá lại khi chạm kháng cự hoặc có BCTC mới" if executable else "Chờ điều kiện mở vị thế",
        "reason": "Đạt gate dữ liệu, định giá, xu hướng; mục tiêu lấy mốc gần hơn giữa kháng cự và fair value." if executable else "Chưa đồng thời đạt gate dữ liệu, định giá, xu hướng và R:R >= 1.5.",
    }

    valuation_metric = f"fair value {_fmt_price(_num(valuation.get('fair_value')))}" if valuation["available"] else "chưa đủ dữ liệu định giá"
    invalidation = (
        f"Đóng cửa dưới {_fmt_price(stop)} hoặc kỳ BCTC sau {report_period} cho thấy ROE giảm dưới 10%/LNST TTM chuyển âm."
        if executable else
        f"Chỉ mở luận điểm mới khi score >= 60, confidence >= 70, {valuation_metric} cao hơn giá và xu hướng vượt MA20/MA50."
    )
    return {
        "model_version": "lp-decision-workbench-v4",
        "as_of": stock_data.get("as_of") or {},
        "recommendation": {
            "action": action,
            "portfolio_weight": f"0-{max_weight}% NAV" if max_weight else "0% vị thế mới",
            "risk_level": "THẤP" if confidence_score >= 85 and score >= 70 else "TRUNG BÌNH" if confidence_score >= 70 else "CAO",
        },
        "confidence": {"score": confidence_score, "grade": confidence_grade, "gates": gates, "passed": passed, "total": len(gates)},
        "scorecard": {
            "total": score,
            "fundamental": fundamental.get("score"),
            "peer": (framework.get("sector_peers") or {}).get("score"),
            "technical": technical.get("score"),
            "evidence": evidence.get("score"),
        },
        "valuation": valuation,
        "technical_analysis": technical,
        "trade_setup": trade_setup,
        "invalidation_trigger": invalidation,
        "capital_allocation_strategy": (
            f"Tối đa {max_weight}% NAV; giải ngân 40%/30%/30% trong vùng mua, dừng mua khi vi phạm invalidation."
            if executable else "Không mở vị thế mới; chỉ đưa vào watchlist và đánh giá lại khi gate thay đổi."
        ),
        "disclaimer": "Công cụ hỗ trợ nghiên cứu, không phải tư vấn đầu tư cá nhân hóa hay cam kết lợi nhuận.",
    }
