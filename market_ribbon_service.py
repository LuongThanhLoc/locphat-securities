"""Compact, source-backed VN30 market ribbon shared by premium pages."""

from __future__ import annotations

import copy
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional


LIVE_REFRESH_SECONDS = 10
CLOSED_REFRESH_SECONDS = 60
SERVER_CACHE_SECONDS = 9


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _direction(change_percent: Optional[float]) -> str:
    if change_percent is None or math.isclose(change_percent, 0.0, abs_tol=0.0001):
        return "REF"
    return "GAIN" if change_percent > 0 else "LOSS"


def _display(symbol: str, value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if symbol in {"VNINDEX", "VN30"}:
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _missing_item(symbol: str, item_type: str, source: str, observed_at: Optional[str]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "type": item_type,
        "last_price": None,
        "reference_price": None,
        "value": None,
        "value_display": None,
        "change": None,
        "change_percent": None,
        "status": "UNAVAILABLE",
        "trend": None,
        "source": source,
        "observed_at": observed_at,
        "as_of": None,
        "stale": True,
    }


class MarketRibbonService:
    """Build a 32-item VNINDEX/VN30 ribbon with single-flight refreshes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Optional[dict[str, Any]] = None
        self._cached_at = 0.0

    @staticmethod
    def _market_session() -> dict[str, Any]:
        from heatmap_engine import get_market_session

        return get_market_session()

    @staticmethod
    def _snapshot() -> dict[str, Any]:
        from heatmap_engine import fetch_market_heatmap_data

        # Heatmap owns the five-second upstream cache. Never bypass it here.
        return fetch_market_heatmap_data(force_refresh=False)

    @staticmethod
    def _membership() -> tuple[list[str], dict[str, Any]]:
        from rrg_index_membership import get_index_membership

        symbols, meta = get_index_membership("VN30")
        normalized = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        if len(normalized) != 30:
            raise RuntimeError(f"Danh sách VN30 chưa được xác minh đủ 30 mã ({len(normalized)}/30).")
        return normalized, meta

    @staticmethod
    def _index_item(symbol: str, session: dict[str, Any]) -> dict[str, Any]:
        from market_data_provider import Quote

        try:
            today = date.fromisoformat(str(session.get("calendar_date")))
        except (TypeError, ValueError):
            today = date.today()
        frame = Quote(symbol, source="VCI").history(
            start=(today - timedelta(days=14)).isoformat(), end=today.isoformat()
        )
        source = str(frame.attrs.get("source") or "unavailable") if frame is not None else "unavailable"
        if frame is None or frame.empty or "close" not in frame.columns:
            return _missing_item(symbol, "index", source, None)
        rows: list[tuple[str, float]] = []
        for _, row in frame.iterrows():
            value = _finite(row.get("close"))
            if value is not None:
                rows.append((str(row.get("time") or "")[:10], value))
        if not rows:
            return _missing_item(symbol, "index", source, None)
        as_of, current = rows[-1]
        previous = rows[-2][1] if len(rows) > 1 else None
        change = current - previous if previous is not None else None
        change_percent = change / previous * 100.0 if change is not None and previous else None
        live = bool(session.get("is_live_matching"))
        stale = bool(live and (as_of != str(session.get("calendar_date") or today.isoformat()) or "vietcap" not in source.lower()))
        status = _direction(change_percent)
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "symbol": symbol,
            "type": "index",
            "last_price": round(current, 4),
            "reference_price": round(previous, 4) if previous is not None else None,
            "value": round(current, 4),
            "value_display": _display(symbol, current),
            "change": round(change, 4) if change is not None else None,
            "change_percent": round(change_percent, 4) if change_percent is not None else None,
            "status": status,
            "trend": "up" if status == "GAIN" else "down" if status == "LOSS" else "flat",
            "source": source,
            "observed_at": observed_at,
            "as_of": as_of,
            "stale": stale,
        }

    @staticmethod
    def _stock_item(
        symbol: str,
        row: Optional[dict[str, Any]],
        source: str,
        observed_at: Optional[str],
        snapshot_stale: bool,
    ) -> dict[str, Any]:
        if not row:
            return _missing_item(symbol, "equity", source, observed_at)
        current = _finite(row.get("match_price")) or _finite(row.get("price_vnd"))
        reference = _finite(row.get("ref_price"))
        if current is None or reference is None:
            return _missing_item(symbol, "equity", source, observed_at)
        change = current - reference
        change_percent = change / reference * 100.0
        status = str(row.get("status") or _direction(change_percent)).upper()
        if status not in {"CEILING", "FLOOR", "GAIN", "LOSS", "REF"}:
            status = _direction(change_percent)
        return {
            "symbol": symbol,
            "type": "equity",
            "last_price": round(current, 4),
            "reference_price": round(reference, 4),
            "value": round(current, 4),
            "value_display": _display(symbol, current),
            "change": round(change, 4),
            "change_percent": round(change_percent, 4),
            "status": status,
            "trend": "up" if change_percent > 0 else "down" if change_percent < 0 else "flat",
            "source": source,
            "observed_at": observed_at,
            "as_of": str(row.get("trading_date") or "")[:10] or None,
            "stale": bool(snapshot_stale),
        }

    def _merge_item_last_known_good(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        previous = {
            item.get("symbol"): item
            for item in (self._cache or {}).get("items", [])
            if item.get("last_price") is not None
        }
        merged: list[dict[str, Any]] = []
        for item in items:
            fallback = previous.get(item["symbol"])
            if item.get("last_price") is None and fallback:
                item = {**copy.deepcopy(fallback), "stale": True, "last_known_good": True}
            merged.append(item)
        return merged

    def _build(self, session: dict[str, Any]) -> dict[str, Any]:
        from market_bubble_engine import dedupe_common_stocks

        symbols, membership = self._membership()
        snapshot = self._snapshot()
        lineage = snapshot.get("data_lineage", {}) or {}
        board_source = str(lineage.get("price_source") or "Vietcap public price board")
        observed_at = lineage.get("fetched_at") or snapshot.get("timestamp")
        rows = {
            str(row.get("symbol") or "").upper(): row
            for row in dedupe_common_stocks(snapshot.get("sectors", []), require_active=False)
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            index_items = list(pool.map(lambda symbol: self._index_item(symbol, session), ("VNINDEX", "VN30")))
        stock_items = [
            self._stock_item(
                symbol, rows.get(symbol), board_source, observed_at,
                bool(snapshot.get("stale") or (snapshot.get("snapshot_frozen") and session.get("is_live_matching"))),
            )
            for symbol in symbols
        ]
        items = self._merge_item_last_known_good(index_items + stock_items)
        generated_at = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": 1,
            "success": True,
            "generated_at": generated_at,
            "market_session": session,
            "refresh_after_seconds": LIVE_REFRESH_SECONDS if session.get("is_live_matching") else CLOSED_REFRESH_SECONDS,
            "source": {
                "equities": board_source,
                "indices": sorted({item.get("source") or "unavailable" for item in index_items}),
                "membership": membership.get("source") or "unavailable",
            },
            "membership": {
                **membership,
                "index_code": "VN30",
                "count": len(symbols),
                "symbols": symbols,
            },
            "items": items,
            "data_quality": {
                "no_synthetic_data": True,
                "expected_items": 32,
                "available_items": sum(item.get("last_price") is not None for item in items),
                "stale_items": [item["symbol"] for item in items if item.get("stale")],
            },
            "stale": any(item.get("stale") for item in items),
            "last_known_good": False,
        }

    @staticmethod
    def _stale_copy(payload: dict[str, Any], *, refreshing: bool = False, error: Optional[str] = None) -> dict[str, Any]:
        result = copy.deepcopy(payload)
        result["stale"] = True
        result["last_known_good"] = True
        result["refreshing"] = refreshing
        if error:
            result["error"] = error[:500]
        for item in result.get("items", []):
            item["stale"] = True
        quality = result.setdefault("data_quality", {})
        quality["stale_items"] = [item.get("symbol") for item in result.get("items", [])]
        return result

    def get(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        session = self._market_session()
        ttl = SERVER_CACHE_SECONDS if session.get("is_live_matching") else CLOSED_REFRESH_SECONDS
        cached_live = bool((self._cache or {}).get("market_session", {}).get("is_live_matching"))
        current_live = bool(session.get("is_live_matching"))
        phase_changed = bool(self._cache) and cached_live != current_live
        if self._cache and not force_refresh and not current_live and not cached_live:
            # Session checks remain cheap, while the close snapshot is immutable.
            result = copy.deepcopy(self._cache)
            result["market_session"] = session
            result["refresh_after_seconds"] = CLOSED_REFRESH_SECONDS
            return result
        if self._cache and not force_refresh and not phase_changed and now - self._cached_at < ttl:
            return copy.deepcopy(self._cache)

        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            if self._cache:
                return self._stale_copy(self._cache, refreshing=True)
            self._lock.acquire()
            acquired = True
        try:
            now = time.monotonic()
            if self._cache and not force_refresh and not phase_changed and now - self._cached_at < ttl:
                return copy.deepcopy(self._cache)
            try:
                payload = self._build(session)
            except Exception as exc:
                if self._cache:
                    return self._stale_copy(self._cache, error=str(exc))
                raise
            self._cache = payload
            self._cached_at = time.monotonic()
            return copy.deepcopy(payload)
        finally:
            if acquired:
                self._lock.release()


_SERVICE = MarketRibbonService()


def get_market_ribbon(*, force_refresh: bool = False) -> dict[str, Any]:
    return _SERVICE.get(force_refresh=force_refresh)
