"""Shared as-of rules for market, filings, disclosures and news."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_vn_iso() -> str:
    return datetime.now(VN_TZ).isoformat()


def period_year_quarter(period: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    text = str(period or "").strip().upper()
    match = re.fullmatch(r"(\d{4})-Q([1-4])", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"FY(\d{4})", text)
    if match:
        return int(match.group(1)), 4
    match = re.fullmatch(r"(?:H1|6M)[- ]?(\d{4})", text)
    if match:
        return int(match.group(1)), 2
    match = re.fullmatch(r"9M[- ]?(\d{4})", text)
    if match:
        return int(match.group(1)), 3
    return None, None


def periods_compatible(disclosure_period: Optional[str], latest_reported_period: Optional[str]) -> bool:
    return period_year_quarter(disclosure_period) == period_year_quarter(latest_reported_period)


def build_as_of_contract(
    latest_reported_period: str,
    statement_reported_at: Optional[str],
    price_source: str,
    price_as_of: Optional[str],
    ttm_quarters: list[str],
) -> dict:
    return {
        "generated_at": now_vn_iso(),
        "market": {
            "mode": "realtime_or_latest_trade",
            "source": price_source,
            "as_of": price_as_of,
        },
        "financials": {
            "mode": "latest_reported",
            "period": latest_reported_period,
            "reported_at": statement_reported_at,
        },
        "ttm": {
            "ending_period": latest_reported_period,
            "quarters": ttm_quarters,
            "complete": len(ttm_quarters) == 4,
        },
        "policy": "Không dùng dữ liệu kỳ cũ làm kết quả hiện tại khi module yêu cầu khớp kỳ.",
    }
