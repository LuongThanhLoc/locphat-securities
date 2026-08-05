"""Build period-correct financial trends from the three primary statements."""

from __future__ import annotations

import math
import re
from typing import Callable, Optional

from industry_indicator_profiles import METRICS, canonical_archetype, get_trend_schema


NPAT_ALIASES = [
    "Lợi nhuận sau thuế",
    "Lợi nhuận của Cổ đông của Công ty mẹ",
    "Cổ đông của Công ty mẹ",
    "Lãi/(lỗ) thuần sau thuế",
    "LỢI NHUẬN KẾ TOÁN SAU THUẾ",
    "LỢI NHUẬN SAU THUẾ TNDN",
    "net_profit_loss_after_tax",
    "attributable_to_parent_company",
    "LNST",
]
REV_ALIASES = ["Doanh thu thuần", "Doanh thu bán hàng và cung cấp dịch vụ", "DOANH THU HOẠT ĐỘNG"]
GROSS_ALIASES = ["LỢI NHUẬN GỘP", "Lợi nhuận gộp", "LỢI NHUẬN GỘP VỀ BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ"]
CFO_ALIASES = ["Dòng tiền thuần từ hoạt động kinh doanh", "Lưu chuyển tiền thuần từ hoạt động kinh doanh"]


def _number(value) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _display(value) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number / 1e9:,.1f}"


def _metric(key: str, label: str, statement: str, nature: str) -> dict:
    return {"key": key, "label": label, "statement": statement, "nature": nature, "unit": "ty_vnd"}


def get_quarterly_table_schema(archetype: str) -> dict:
    return get_trend_schema(archetype)


def _comparison_period(period: str, frequency: str) -> Optional[str]:
    if frequency == "year":
        match = re.fullmatch(r"(\d{4})", str(period))
        return str(int(match.group(1)) - 1) if match else None
    match = re.fullmatch(r"(\d{4})-Q([1-4])", str(period))
    return f"{int(match.group(1)) - 1}-Q{match.group(2)}" if match else None


def _growth_badge(current, previous, frequency: str) -> Optional[dict]:
    current_number = _number(current)
    previous_number = _number(previous)
    if current_number is None or previous_number in (None, 0):
        return None
    growth = round((current_number - previous_number) / abs(previous_number) * 100, 1)
    return {"pct": growth, "class": "green" if growth >= 0 else "red", "basis": "YoY"}


def build_trend_data(
    archetype: str,
    periods: list,
    get_bs_item: Callable,
    get_is_item: Callable,
    get_cf_item: Optional[Callable] = None,
    frequency: str = "quarter",
) -> list:
    """Return statement-grounded rows; missing values remain missing, never zero-filled."""
    get_cf_item = get_cf_item or (lambda _names, _period=None: None)
    schema = get_trend_schema(archetype)
    metric_columns = [column for column in schema["columns"] if column["key"] != "period"]
    rows = []
    for period in periods:
        npat_aliases = METRICS["npat"]["aliases"]
        npat = get_is_item(npat_aliases, period)
        previous_period = _comparison_period(str(period), frequency)
        previous_npat = get_is_item(NPAT_ALIASES, previous_period) if previous_period else None
        row = {
            "period": period,
            "npat": _display(npat),
            "yoy_badge": _growth_badge(npat, previous_npat, frequency),
        }
        for column in metric_columns:
            key = column["key"]
            if key == "npat":
                continue
            aliases = column.get("aliases", [])
            statement = column.get("statement")
            if statement == "balance_sheet":
                value = get_bs_item(aliases, period)
            elif statement == "cash_flow":
                value = get_cf_item(aliases, period)
            else:
                value = get_is_item(aliases, period)
            row[key] = _display(value)
        rows.append(row)
    return rows
