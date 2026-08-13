"""Corporate-action aware price preparation for LP-RRG V2.

The module intentionally refuses to infer missing rights/split terms.  It
builds a back-adjusted total-return series while preserving the quoted close
used by the rest of the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


ADJUSTMENT_VERSION = "total-return-v2"
ADJUSTMENT_RULE_VERSION = "rrg-actions-2026-08-11"
ACTION_TYPES = {"cash_dividend", "stock_dividend", "bonus_share", "split", "rights_issue"}


class AdjustmentPending(ValueError):
    """Raised when a price-affecting action cannot be calculated safely."""


@dataclass(frozen=True)
class AdjustmentResult:
    frame: pd.DataFrame
    status: str
    applied_actions: int
    pending_actions: tuple[str, ...]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _normalise_action(action: dict[str, Any]) -> dict[str, Any]:
    event_type = str(action.get("event_type") or action.get("type") or "").strip().lower()
    ex_date = str(
        action.get("ex_date") or action.get("exright_date") or action.get("event_date") or ""
    )[:10]
    return {
        "event_id": str(action.get("event_id") or action.get("id") or f"{event_type}:{ex_date}"),
        "event_type": event_type,
        "ex_date": ex_date,
        "cash_per_share": _number(action.get("cash_per_share") or action.get("value_per_share")),
        "share_ratio": _number(action.get("share_ratio") or action.get("ratio")),
        "subscription_price": _number(action.get("subscription_price") or action.get("issue_price")),
        "verification_status": str(action.get("verification_status") or "confirmed").lower(),
    }


def build_total_return_series(
    bars: pd.DataFrame,
    actions: Iterable[dict[str, Any]] = (),
    *,
    strict: bool = True,
) -> AdjustmentResult:
    """Return raw OHLC plus a latest-price-anchored total-return close.

    ``share_ratio`` is the number of new/bonus shares per existing share
    (10% => 0.10).  Rights issues additionally require a subscription price.
    Missing terms are never guessed.
    """
    if bars is None or bars.empty:
        return AdjustmentResult(pd.DataFrame(), "ok", 0, ())

    data = bars.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    if data["date"].isna().any() or data["close"].isna().any() or (data["close"] <= 0).any():
        raise ValueError("Không thể điều chỉnh chuỗi giá không hợp lệ")
    data = data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    data["raw_close"] = data["close"].astype(float)

    by_date: dict[str, list[dict[str, Any]]] = {}
    pending: list[str] = []
    for raw_action in actions:
        action = _normalise_action(dict(raw_action))
        if action["event_type"] not in ACTION_TYPES or not action["ex_date"]:
            continue
        if action["verification_status"] not in {"confirmed", "verified", "ok"}:
            pending.append(action["event_id"])
            continue
        by_date.setdefault(action["ex_date"], []).append(action)

    tri = np.empty(len(data), dtype=float)
    tri[0] = float(data.loc[0, "raw_close"])
    applied = 0
    dates = data["date"].dt.strftime("%Y-%m-%d")
    for index in range(1, len(data)):
        previous = float(data.loc[index - 1, "raw_close"])
        current = float(data.loc[index, "raw_close"])
        cash_total = 0.0
        share_multiplier = 1.0
        rights_cost = 0.0
        gross_denominator = previous
        for action in by_date.get(dates.iloc[index], []):
            kind = action["event_type"]
            if kind == "cash_dividend":
                cash = action["cash_per_share"]
                if cash is None or cash < 0:
                    pending.append(action["event_id"])
                    continue
                cash_total += cash
            elif kind in {"stock_dividend", "bonus_share", "split"}:
                ratio = action["share_ratio"]
                if ratio is None or ratio <= 0:
                    pending.append(action["event_id"])
                    continue
                share_multiplier *= 1.0 + ratio
            elif kind == "rights_issue":
                ratio = action["share_ratio"]
                subscription = action["subscription_price"]
                if ratio is None or ratio <= 0 or subscription is None or subscription < 0:
                    pending.append(action["event_id"])
                    continue
                share_multiplier *= 1.0 + ratio
                rights_cost += ratio * subscription
            applied += 1
        gross_numerator = current * share_multiplier + cash_total - rights_cost
        if gross_numerator <= 0 or gross_denominator <= 0:
            raise ValueError("Hệ số total-return không dương")
        tri[index] = tri[index - 1] * gross_numerator / gross_denominator

    if pending and strict:
        raise AdjustmentPending("Thiếu dữ kiện corporate action: " + ", ".join(sorted(set(pending))))

    # Anchor the transformed series to the latest quoted close.  This retains
    # familiar current-price units while removing artificial historical gaps.
    scale = float(data["raw_close"].iloc[-1]) / float(tri[-1])
    data["total_return_close"] = tri * scale
    data["adjustment_factor"] = data["total_return_close"] / data["raw_close"]
    data["adjustment_version"] = ADJUSTMENT_VERSION
    data["corporate_action_status"] = "adjustment_pending" if pending else "ok"
    data["date"] = dates
    return AdjustmentResult(data, data["corporate_action_status"].iloc[-1], applied, tuple(sorted(set(pending))))
