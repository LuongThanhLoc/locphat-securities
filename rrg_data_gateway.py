"""Verified Vietcap -> KBS history gateway for LP-RRG."""

from __future__ import annotations

import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional

import numpy as np
import pandas as pd

from rrg_data_store import RrgStoreUnavailable, get_rrg_store

LOGGER = logging.getLogger("rrg.data")
BENCHMARKS = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOM", "UPCOMINDEX"}
MAX_CONCURRENT_FETCHES = 3
MAX_STALE_SESSIONS = 3
MIN_VALID_PRICE_VND = 100.0
_FETCH_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()
_RAM_CACHE: Dict[str, tuple[float, "HistoryResult"]] = {}
_RAM_CACHE_TTL = 120


class HistoryUnavailable(RuntimeError):
    def __init__(self, symbol: str, reason: str, source_chain: Optional[list] = None):
        super().__init__(reason)
        self.symbol = symbol
        self.reason = reason
        self.source_chain = source_chain or []


class DataQualityError(ValueError):
    pass


class InactiveHistory(RuntimeError):
    def __init__(self, symbol: str, frame: pd.DataFrame, source: str, source_chain: list, freshness: int):
        super().__init__(f"{symbol} không có phiên mới trong {freshness} phiên benchmark")
        self.symbol = symbol
        self.frame = frame
        self.source = source
        self.source_chain = source_chain
        self.freshness = freshness


@dataclass
class HistoryResult:
    frame: pd.DataFrame
    source: str
    source_chain: list[dict[str, Any]] = field(default_factory=list)
    quality_status: str = "valid"
    served_from_cache: bool = False
    freshness_sessions: int = 0
    last_success_at: Optional[str] = None


@dataclass
class _Circuit:
    failures: int = 0
    opened_until: float = 0.0


_CIRCUITS: Dict[str, _Circuit] = {"Vietcap": _Circuit(), "KBS": _Circuit()}


def strict_store_enabled() -> bool:
    value = os.getenv("RRG_DATA_V2_ENABLED")
    if value is None:
        return bool(os.getenv("DATABASE_URL", "").strip())
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


def _fetch_vietcap(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Use the local direct Vietcap adapter; it must not invoke MSN."""
    from market_data_provider import fetch_vci_history
    return fetch_vci_history(symbol, start, end)


def _fetch_kbs(symbol: str, start: str, end: str) -> pd.DataFrame:
    import requests
    kbs_symbol = "UPCOMINDEX" if symbol.upper() == "UPCOM" else symbol.upper()
    kind = "index" if symbol.upper() in BENCHMARKS else "stocks"
    endpoint = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/{kind}/{kbs_symbol}/data_day"
    response = requests.get(
        endpoint,
        params={
            "sdate": datetime.strptime(start, "%Y-%m-%d").strftime("%d-%m-%Y"),
            "edate": datetime.strptime(end, "%Y-%m-%d").strftime("%d-%m-%Y"),
        },
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json().get("data_day", [])
    frame = pd.DataFrame(rows).rename(
        columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    # The raw KBS endpoint already returns Vietnamese equities in canonical
    # VND (e.g. SSI=24,450), while its vnstock wrapper divides them by 1,000.
    return frame


PROVIDERS: tuple[tuple[str, Callable[[str, str, str], pd.DataFrame]], ...] = (
    ("Vietcap", _fetch_vietcap),
    ("KBS", _fetch_kbs),
)


def _normalise_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DataQualityError("response_rỗng")
    data = frame.copy()
    if "time" in data.columns and "date" not in data.columns:
        data = data.rename(columns={"time": "date"})
    required = {"date", "open", "high", "low", "close"}
    missing = required - set(data.columns)
    if missing:
        raise DataQualityError("thiếu_cột:" + ",".join(sorted(missing)))
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    if data["date"].isna().any():
        raise DataQualityError("ngày_không_hợp_lệ")
    if (data["date"].dt.weekday >= 5).any():
        raise DataQualityError("có_dữ_liệu_cuối_tuần_sai_thị_trường")
    data = data.sort_values("date").drop_duplicates("date", keep="last")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in data:
            data[column] = np.nan
        data[column] = pd.to_numeric(data[column], errors="coerce")
    prices = data[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise DataQualityError("giá_nan_vô_cực_hoặc_không_dương")
    if ((data["low"] > data[["open", "close"]].min(axis=1)) |
            (data["high"] < data[["open", "close"]].max(axis=1)) |
            (data["low"] > data["high"])).any():
        raise DataQualityError("ohlc_không_nhất_quán")
    if symbol.upper() not in BENCHMARKS and float(data["close"].median()) < MIN_VALID_PRICE_VND:
        raise DataQualityError("giá_không_phải_đơn_vị_VND_hoặc_sai_thị_trường")
    data["date"] = data["date"].dt.strftime("%Y-%m-%d")
    return data[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def validate_history(frame: pd.DataFrame, symbol: str, cached: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    data = _normalise_frame(frame, symbol)
    if cached is not None and not cached.empty:
        old = _normalise_frame(cached, symbol)
        overlap = old[["date", "close"]].merge(
            data[["date", "close"]], on="date", suffixes=("_old", "_new")
        )
        if not overlap.empty:
            ratios = overlap["close_new"] / overlap["close_old"]
            relative_gap = (ratios - 1.0).abs()
            # A coherent ratio across every overlap can be a legitimate split/
            # adjustment revision. Mixed large gaps indicate corrupt units or
            # a mismatched instrument and must never overwrite the good copy.
            coherent_adjustment = len(ratios) >= 3 and float(ratios.std(ddof=0)) < 0.005
            if float(relative_gap.median()) > 0.05 and not coherent_adjustment:
                raise DataQualityError("nguồn_mới_lệch_trên_5%_so_với_bản_tốt")
    return data


def _freshness_sessions(last_date: str, end: str, calendar: Optional[Iterable[str]]) -> int:
    last = str(last_date)[:10]
    if calendar is not None:
        sessions = sorted({str(value)[:10] for value in calendar})
        return sum(value > last and value <= end for value in sessions)
    start_dt = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)
    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    count = 0
    while start_dt <= end_dt:
        if start_dt.weekday() < 5:
            count += 1
        start_dt += timedelta(days=1)
    return count


def _merge_frames(cached: pd.DataFrame, fresh: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if cached is None or cached.empty:
        return validate_history(fresh, symbol)
    merged = pd.concat([cached, fresh], ignore_index=True)
    return _normalise_frame(merged, symbol)


def _provider_attempts(
    symbol: str, start: str, end: str, cached: pd.DataFrame, store: Any,
    trading_calendar: Optional[Iterable[str]] = None,
) -> tuple[pd.DataFrame, str, list[dict[str, Any]]]:
    chain: list[dict[str, Any]] = []
    stale_candidates: list[tuple[pd.DataFrame, str, int]] = []
    for source, provider in PROVIDERS:
        circuit = _CIRCUITS[source]
        if circuit.opened_until > time.monotonic():
            chain.append({"source": source, "status": "circuit_open"})
            continue
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                with _FETCH_SEMAPHORE:
                    raw = provider(symbol, start, end)
                valid = validate_history(raw, symbol, cached=cached)
                elapsed = round((time.monotonic() - started) * 1000)
                freshness = _freshness_sessions(str(valid["date"].iloc[-1]), end, trading_calendar)
                if freshness > MAX_STALE_SESSIONS:
                    chain.append({
                        "source": source, "status": "inactive_candidate", "attempt": attempt,
                        "freshness_sessions": freshness, "latency_ms": elapsed,
                    })
                    stale_candidates.append((valid, source, freshness))
                    circuit.failures = 0
                    break
                chain.append({"source": source, "status": "ok", "attempt": attempt, "latency_ms": elapsed})
                circuit.failures = 0
                circuit.opened_until = 0.0
                return valid, source, chain
            except Exception as exc:
                reason = str(exc)[:300]
                chain.append({"source": source, "status": "error", "attempt": attempt, "error": reason})
                LOGGER.warning("rrg_source_error symbol=%s source=%s attempt=%s error=%s", symbol, source, attempt, reason)
                if isinstance(exc, DataQualityError) and store is not None:
                    try:
                        store.quarantine(symbol, source, reason, {"start": start, "end": end})
                    except Exception:
                        pass
                if attempt < 3:
                    time.sleep((0.45 * (3 ** (attempt - 1))) + random.uniform(0.0, 0.25))
        circuit.failures += 1
        if circuit.failures >= 5:
            circuit.opened_until = time.monotonic() + 60.0
    if len(stale_candidates) >= 2:
        candidate_dates = {str(candidate[0]["date"].iloc[-1]) for candidate in stale_candidates}
        if len(candidate_dates) == 1:
            frame, source, freshness = stale_candidates[-1]
            raise InactiveHistory(symbol, frame, source, chain, freshness)
    raise HistoryUnavailable(symbol, "Không có nguồn lịch sử hợp lệ", chain)


def get_verified_history(
    symbol: str,
    start: str,
    end: str,
    *,
    trading_calendar: Optional[Iterable[str]] = None,
    store: Any = None,
    require_store: Optional[bool] = None,
) -> HistoryResult:
    symbol = symbol.upper().strip()
    require_store = strict_store_enabled() if require_store is None else require_store
    if store is None:
        store = get_rrg_store(required=require_store)
    cache_key = f"{symbol}|{start}|{end}"
    cached_ram = _RAM_CACHE.get(cache_key)
    if cached_ram and time.monotonic() - cached_ram[0] < _RAM_CACHE_TTL:
        return cached_ram[1]

    with _key_lock(cache_key):
        cached = pd.DataFrame()
        state: Dict[str, Any] = {}
        if store is not None:
            cached = store.load_history(symbol, start, end)
            state = store.state(symbol)

        if not cached.empty:
            cached_freshness = _freshness_sessions(
                str(cached["date"].iloc[-1]), end, trading_calendar
            )
            if cached_freshness == 0:
                result = HistoryResult(
                    frame=cached,
                    source=str(state.get("last_source") or "PostgreSQL"),
                    source_chain=list(state.get("source_chain") or []),
                    quality_status="valid",
                    served_from_cache=True,
                    freshness_sessions=0,
                    last_success_at=str(state.get("last_success_at") or "") or None,
                )
                _RAM_CACHE[cache_key] = (time.monotonic(), result)
                return result

        fetch_start = start
        if not cached.empty:
            last_cached = datetime.strptime(str(cached["date"].iloc[-1])[:10], "%Y-%m-%d").date()
            fetch_start = max(datetime.strptime(start, "%Y-%m-%d").date(), last_cached - timedelta(days=7)).isoformat()

        try:
            fresh, source, chain = _provider_attempts(
                symbol, fetch_start, end, cached, store, trading_calendar=trading_calendar
            )
            merged = _merge_frames(cached, fresh, symbol)
            if store is not None:
                store.upsert_history(symbol, fresh, source, chain)
                # Reload so a backfill assembled across prior incremental writes
                # has exactly the durable canonical rows.
                merged = store.load_history(symbol, start, end)
                state = store.state(symbol)
            result = HistoryResult(
                frame=merged,
                source=source,
                source_chain=chain,
                quality_status="valid",
                served_from_cache=False,
                freshness_sessions=_freshness_sessions(str(merged["date"].iloc[-1]), end, trading_calendar),
                last_success_at=str(state.get("last_success_at") or datetime.now(timezone.utc).isoformat()),
            )
        except InactiveHistory as exc:
            merged = _merge_frames(cached, exc.frame, symbol)
            if store is not None:
                store.upsert_history(symbol, exc.frame, exc.source, exc.source_chain)
                store.record_failure(symbol, str(exc), status="inactive")
            result = HistoryResult(
                frame=merged,
                source=exc.source,
                source_chain=exc.source_chain,
                quality_status="inactive",
                served_from_cache=False,
                freshness_sessions=exc.freshness,
                last_success_at=datetime.now(timezone.utc).isoformat(),
            )
        except HistoryUnavailable as exc:
            if cached.empty:
                if store is not None:
                    store.record_failure(symbol, exc.reason)
                raise
            freshness = _freshness_sessions(str(cached["date"].iloc[-1]), end, trading_calendar)
            if freshness > MAX_STALE_SESSIONS:
                if store is not None:
                    store.record_failure(symbol, f"cache quá cũ: {freshness} phiên")
                raise HistoryUnavailable(symbol, f"Bản tốt gần nhất đã cũ {freshness} phiên", exc.source_chain)
            result = HistoryResult(
                frame=cached,
                source=str(state.get("last_source") or "PostgreSQL"),
                source_chain=exc.source_chain,
                quality_status="stale_valid",
                served_from_cache=True,
                freshness_sessions=freshness,
                last_success_at=str(state.get("last_success_at") or "") or None,
            )
        _RAM_CACHE[cache_key] = (time.monotonic(), result)
        return result


def init_rrg_store() -> bool:
    store = get_rrg_store(required=strict_store_enabled())
    if store is None:
        return False
    store.init_schema()
    return True


def rrg_data_health() -> Dict[str, Any]:
    strict = strict_store_enabled()
    try:
        store = get_rrg_store(required=strict)
        if store is None:
            return {"configured": False, "strict_mode": strict, "status": "disabled"}
        payload = store.health()
        payload.update({"strict_mode": strict, "status": "ok"})
        return payload
    except Exception as exc:
        LOGGER.error("rrg_health_error error=%s", exc)
        return {
            "configured": bool(os.getenv("DATABASE_URL")),
            "strict_mode": strict,
            "status": "error",
            "error_code": "postgres_unavailable",
        }
