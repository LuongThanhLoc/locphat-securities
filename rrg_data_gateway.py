"""Verified Vietcap -> KBS history gateway for LP-RRG."""

from __future__ import annotations

import logging
import math
import os
import random
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional

import numpy as np
import pandas as pd

from rrg_data_store import RrgStoreUnavailable, get_rrg_store
from rrg_adjustment import ADJUSTMENT_VERSION, AdjustmentPending, build_total_return_series

LOGGER = logging.getLogger("rrg.data")
BENCHMARKS = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOM", "UPCOMINDEX"}
MAX_CONCURRENT_FETCHES = 3
MAX_STALE_SESSIONS = 3
MIN_VALID_PRICE_VND = 100.0
SOURCE_AGREEMENT_WARN_BPS = 50.0
SOURCE_AGREEMENT_REJECT_BPS = 300.0
QUALITY_RULE_VERSION = "rrg-quality-2026-08-11"
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
    canonical_source: Optional[str] = None
    source_agreement_bps: Optional[float] = None
    data_confidence_score: Optional[float] = None
    adjustment_version: str = "raw-v1"
    corporate_action_status: str = "unknown"


@dataclass
class _Circuit:
    failures: int = 0
    opened_until: float = 0.0


_CIRCUITS: Dict[str, _Circuit] = {"Vietcap": _Circuit(), "KBS": _Circuit()}


def invalidate_rrg_cache(symbols: Optional[Iterable[str]] = None) -> None:
    wanted = {str(symbol).upper() for symbol in symbols or []}
    if not wanted:
        _RAM_CACHE.clear()
        return
    for key in list(_RAM_CACHE):
        if key.split("|", 1)[0] in wanted:
            _RAM_CACHE.pop(key, None)


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


def _normalise_frame(
    frame: pd.DataFrame, symbol: str, *, exchange: Optional[str] = None,
    trading_calendar: Optional[Iterable[str]] = None,
    corporate_action_dates: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
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
    weekend_mask = data["date"].dt.weekday >= 5
    if weekend_mask.any():
        num_weekend = int(weekend_mask.sum())
        max_allowed_weekend = max(2, int(len(data) * 0.005))
        if num_weekend <= max_allowed_weekend and not weekend_mask.iloc[-1]:
            data = data.loc[~weekend_mask].copy()
        else:
            raise DataQualityError("có_dữ_liệu_cuối_tuần_sai_thị_trường")
    data = data.sort_values("date").drop_duplicates("date", keep="last")
    if trading_calendar is not None and symbol.upper() not in BENCHMARKS:
        official = {str(value)[:10] for value in trading_calendar}
        unexpected = set(data["date"].dt.strftime("%Y-%m-%d")) - official
        if unexpected:
            max_allowed_unexpected = max(3, int(len(data) * 0.02))
            latest_date_str = data["date"].dt.strftime("%Y-%m-%d").iloc[-1]
            if len(unexpected) <= max_allowed_unexpected and latest_date_str in official:
                data = data.loc[data["date"].dt.strftime("%Y-%m-%d").isin(official)].copy()
            else:
                raise DataQualityError("phiên_không_thuộc_lịch_benchmark:" + sorted(unexpected)[0])
    for column in ("open", "high", "low", "close", "volume"):
        if column not in data:
            data[column] = np.nan
        data[column] = pd.to_numeric(data[column], errors="coerce")
    prices = data[["open", "high", "low", "close"]]
    invalid_prices = (~np.isfinite(prices.to_numpy())).any(axis=1) | (prices <= 0).any(axis=1)
    if invalid_prices.any():
        num_invalid = int(invalid_prices.sum())
        max_allowed = max(2, int(len(data) * 0.005))
        if num_invalid <= max_allowed and not invalid_prices.iloc[-1]:
            data = data.loc[~invalid_prices].copy()
        else:
            raise DataQualityError("giá_nan_vô_cực_hoặc_không_dương")
    
    if (data["low"] > data["high"]).any():
        raise DataQualityError("ohlc_không_nhất_quán")

    inconsistent_mask = (
        (data["low"] > data[["open", "close"]].min(axis=1)) |
        (data["high"] < data[["open", "close"]].max(axis=1))
    )
    if inconsistent_mask.any():
        num_inconsistent = int(inconsistent_mask.sum())
        max_allowed = max(2, int(len(data) * 0.005)) if len(data) >= 100 else 0
        if num_inconsistent <= max_allowed and not inconsistent_mask.iloc[-1]:
            data = data.loc[~inconsistent_mask].copy()
        else:
            raise DataQualityError("ohlc_không_nhất_quán")
    non_null_volume = data["volume"].dropna()
    if (non_null_volume < 0).any():
        raise DataQualityError("khối_lượng_âm")
    if not non_null_volume.empty and not np.allclose(non_null_volume, np.round(non_null_volume), atol=1e-6):
        raise DataQualityError("khối_lượng_không_nguyên")
    if symbol.upper() not in BENCHMARKS and float(data["close"].median()) < MIN_VALID_PRICE_VND:
        raise DataQualityError("giá_không_phải_đơn_vị_VND_hoặc_sai_thị_trường")
    exchange = str(exchange or "").upper()
    if exchange in {"HOSE", "HNX", "UPCOM"}:
        closes = data["close"].astype(float)
        if exchange == "HOSE":
            ticks = np.where(closes < 10_000, 10.0, np.where(closes < 50_000, 50.0, 100.0))
        else:
            ticks = np.full(len(closes), 100.0)
        tick_error = np.abs(closes / ticks - np.round(closes / ticks))
        if (tick_error > 1e-6).any():
            raise DataQualityError("giá_không_đúng_bước_giá_theo_sàn")
        limit = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}[exchange]
        action_dates = {str(value)[:10] for value in corporate_action_dates or []}
        date_strings = data["date"].dt.strftime("%Y-%m-%d")
        returns = closes.pct_change().abs()
        abnormal = (returns > limit + 0.015) & ~date_strings.isin(action_dates)
        if abnormal.any():
            raise DataQualityError("biến_động_vượt_biên_độ_không_có_corporate_action")
    data["date"] = data["date"].dt.strftime("%Y-%m-%d")
    return data[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    payload = frame[["date", "open", "high", "low", "close", "volume"]].to_dict("records")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _source_agreement(primary: pd.DataFrame, secondary: pd.DataFrame) -> tuple[float | None, int]:
    overlap = primary[["date", "close", "volume"]].merge(
        secondary[["date", "close", "volume"]], on="date", suffixes=("_primary", "_secondary")
    ).tail(20)
    if overlap.empty:
        return None, 0
    denominator = overlap["close_primary"].abs().replace(0, np.nan)
    bps = ((overlap["close_primary"] - overlap["close_secondary"]).abs() / denominator * 10_000).dropna()
    return (float(bps.median()) if not bps.empty else None), len(overlap)


def _confidence_score(
    *, freshness: int, agreement_bps: float | None, history_sessions: int,
    adjustment_pending: bool, source_count: int,
) -> float:
    score = 100.0
    score -= min(max(freshness, 0) * 12.0, 36.0)
    if source_count < 2:
        score -= 15.0
    if agreement_bps is None:
        score -= 5.0
    else:
        score -= min(20.0, agreement_bps / 15.0)
    if history_sessions < 400:
        score -= min(15.0, (400 - history_sessions) / 10.0)
    if adjustment_pending:
        score -= 40.0
    return round(max(0.0, min(100.0, score)), 1)


def validate_history(
    frame: pd.DataFrame, symbol: str, cached: Optional[pd.DataFrame] = None, *,
    exchange: Optional[str] = None, trading_calendar: Optional[Iterable[str]] = None,
    corporate_action_dates: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    data = _normalise_frame(
        frame, symbol, exchange=exchange, trading_calendar=trading_calendar,
        corporate_action_dates=corporate_action_dates,
    )
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


def _raw_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    raw = frame.copy()
    if "raw_close" in raw:
        raw["close"] = raw["raw_close"]
    return raw[[column for column in ("date", "open", "high", "low", "close", "volume") if column in raw]]


def _prepare_canonical(
    symbol: str, raw_frame: pd.DataFrame, store: Any, start: str, end: str,
    *, agreement_bps: float | None, source_count: int, freshness: int,
) -> pd.DataFrame:
    if store is None:
        canonical = raw_frame.copy()
        canonical["raw_close"] = canonical["close"].astype(float)
        canonical["total_return_close"] = canonical["close"].astype(float)
        canonical["adjustment_factor"] = 1.0
        canonical["adjustment_version"] = "raw-v1"
        canonical["corporate_action_status"] = "source_unavailable"
        canonical["source_agreement_bps"] = agreement_bps
        canonical["data_confidence_score"] = min(60.0, _confidence_score(
            freshness=freshness, agreement_bps=agreement_bps,
            history_sessions=len(canonical), adjustment_pending=True,
            source_count=source_count,
        ))
        canonical["canonical_fingerprint"] = _frame_fingerprint(_raw_view(canonical))
        return canonical
    actions: list[dict[str, Any]] = []
    if store is not None and hasattr(store, "load_corporate_actions"):
        actions = store.load_corporate_actions(symbol, start, end)
    try:
        adjusted = build_total_return_series(raw_frame, actions, strict=store is not None)
    except AdjustmentPending as exc:
        if store is not None:
            store.record_failure(symbol, str(exc), status="adjustment_pending")
        raise HistoryUnavailable(symbol, str(exc)) from exc
    canonical = adjusted.frame
    canonical["source_agreement_bps"] = agreement_bps
    canonical["data_confidence_score"] = _confidence_score(
        freshness=freshness,
        agreement_bps=agreement_bps,
        history_sessions=len(canonical),
        adjustment_pending=adjusted.status == "adjustment_pending",
        source_count=source_count,
    )
    canonical["canonical_fingerprint"] = _frame_fingerprint(_raw_view(canonical))
    return canonical


def _provider_attempts(
    symbol: str, start: str, end: str, cached: pd.DataFrame, store: Any,
    trading_calendar: Optional[Iterable[str]] = None,
) -> tuple[pd.DataFrame, str, list[dict[str, Any]]]:
    """Fetch every available provider, retain raw evidence, then reconcile."""
    chain: list[dict[str, Any]] = []
    chain_lock = threading.Lock()
    identity = store.security_identity(symbol, end) if store is not None and hasattr(store, "security_identity") else {}
    action_rows = store.load_corporate_actions(symbol, start, end) if store is not None and hasattr(store, "load_corporate_actions") else []
    action_dates = [str(action.get("ex_date"))[:10] for action in action_rows]

    def attempt_source(source: str, provider: Callable[[str, str, str], pd.DataFrame]):
        circuit = _CIRCUITS[source]
        if circuit.opened_until > time.monotonic():
            with chain_lock:
                chain.append({"source": source, "status": "circuit_open"})
            return None
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                with _FETCH_SEMAPHORE:
                    raw = provider(symbol, start, end)
                valid = validate_history(
                    raw, symbol, cached=cached,
                    exchange=identity.get("exchange"),
                    trading_calendar=trading_calendar,
                    corporate_action_dates=action_dates,
                )
                elapsed = round((time.monotonic() - started) * 1000)
                freshness = _freshness_sessions(str(valid["date"].iloc[-1]), end, trading_calendar)
                status = "inactive_candidate" if freshness > MAX_STALE_SESSIONS else "ok"
                with chain_lock:
                    chain.append({
                        "source": source, "status": status, "attempt": attempt,
                        "freshness_sessions": freshness, "latency_ms": elapsed,
                        "response_hash": _frame_fingerprint(valid),
                    })
                if store is not None and hasattr(store, "record_raw_history"):
                    batch_id = None
                    if hasattr(store, "begin_ingestion"):
                        batch_id = store.begin_ingestion(symbol, source, start, end)
                    store.record_raw_history(symbol, valid, source, batch_id)
                circuit.failures = 0
                circuit.opened_until = 0.0
                return valid, source, freshness
            except Exception as exc:
                reason = str(exc)[:300]
                with chain_lock:
                    chain.append({"source": source, "status": "error", "attempt": attempt, "error": reason})
                LOGGER.warning("rrg_source_error symbol=%s source=%s attempt=%s error=%s", symbol, source, attempt, reason)
                if isinstance(exc, DataQualityError) and store is not None:
                    try:
                        sample = []
                        if "raw" in locals() and isinstance(raw, pd.DataFrame):
                            sample = raw.head(5).to_dict("records")
                        store.quarantine(symbol, source, reason, {
                            "start": start, "end": end, "sample": sample,
                            "rule_version": QUALITY_RULE_VERSION,
                        })
                    except Exception:
                        pass
                if attempt < 3:
                    time.sleep((0.45 * (3 ** (attempt - 1))) + random.uniform(0.0, 0.25))
        circuit.failures += 1
        if circuit.failures >= 5:
            circuit.opened_until = time.monotonic() + 60.0
        return None

    candidates: list[tuple[pd.DataFrame, str, int]] = []
    with ThreadPoolExecutor(max_workers=min(len(PROVIDERS), MAX_CONCURRENT_FETCHES)) as executor:
        futures = [executor.submit(attempt_source, source, provider) for source, provider in PROVIDERS]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                candidates.append(result)

    fresh_candidates = [item for item in candidates if item[2] <= MAX_STALE_SESSIONS]
    if fresh_candidates:
        # Provider priority remains deterministic regardless of response order.
        priority = {name: index for index, (name, _) in enumerate(PROVIDERS)}
        fresh_candidates.sort(key=lambda item: priority.get(item[1], 999))
        canonical, source, _ = fresh_candidates[0]
        agreement_bps = None
        if len(fresh_candidates) >= 2:
            agreement_bps, overlap_count = _source_agreement(canonical, fresh_candidates[1][0])
            chain.append({
                "source": "reconciliation", "status": "ok" if agreement_bps is not None and agreement_bps <= SOURCE_AGREEMENT_WARN_BPS else "warning",
                "agreement_bps": None if agreement_bps is None else round(agreement_bps, 2),
                "overlap_sessions": overlap_count,
            })
            if agreement_bps is not None and agreement_bps > SOURCE_AGREEMENT_REJECT_BPS:
                payload = {
                    "sources": [fresh_candidates[0][1], fresh_candidates[1][1]],
                    "agreement_bps": agreement_bps,
                    "primary_hash": _frame_fingerprint(canonical),
                    "comparison_hash": _frame_fingerprint(fresh_candidates[1][0]),
                }
                if store is not None:
                    store.quarantine(symbol, "reconciliation", "nguồn_bất_đồng_vượt_ngưỡng", payload)
                raise HistoryUnavailable(symbol, "Hai nguồn dữ liệu bất đồng vượt ngưỡng", chain)
        canonical.attrs["source_agreement_bps"] = agreement_bps
        canonical.attrs["source_count"] = len(fresh_candidates)
        return canonical, source, chain

    stale_candidates = candidates
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
        store = get_rrg_store(required=True) if require_store else None
    cache_key = f"{symbol}|{start}|{end}|{ADJUSTMENT_VERSION}|{QUALITY_RULE_VERSION}"
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
            cached_adjustment = str(cached["adjustment_version"].iloc[-1]) if "adjustment_version" in cached else "raw-v1"
            v2_store = store is not None and hasattr(store, "load_corporate_actions")
            if cached_freshness == 0 and cached_adjustment != ADJUSTMENT_VERSION and v2_store:
                rebuilt = _prepare_canonical(
                    symbol, _raw_view(cached), store, start, end,
                    agreement_bps=(float(cached["source_agreement_bps"].dropna().iloc[-1]) if "source_agreement_bps" in cached and not cached["source_agreement_bps"].dropna().empty else None),
                    source_count=1, freshness=0,
                )
                store.upsert_history(
                    symbol, rebuilt, str(state.get("last_source") or "PostgreSQL"),
                    list(state.get("source_chain") or []),
                )
                cached = store.load_history(symbol, start, end)
                cached_adjustment = ADJUSTMENT_VERSION
            if cached_freshness == 0 and (cached_adjustment == ADJUSTMENT_VERSION or not v2_store):
                result = HistoryResult(
                    frame=cached,
                    source=str(state.get("last_source") or "PostgreSQL"),
                    source_chain=list(state.get("source_chain") or []),
                    quality_status="valid",
                    served_from_cache=True,
                    freshness_sessions=0,
                    last_success_at=str(state.get("last_success_at") or "") or None,
                    canonical_source=str(state.get("last_source") or "PostgreSQL"),
                    source_agreement_bps=(float(cached["source_agreement_bps"].dropna().iloc[-1]) if "source_agreement_bps" in cached and not cached["source_agreement_bps"].dropna().empty else None),
                    data_confidence_score=(float(cached["data_confidence_score"].dropna().iloc[-1]) if "data_confidence_score" in cached and not cached["data_confidence_score"].dropna().empty else None),
                    adjustment_version=(str(cached["adjustment_version"].iloc[-1]) if "adjustment_version" in cached else "raw-v1"),
                    corporate_action_status=(str(cached["corporate_action_status"].iloc[-1]) if "corporate_action_status" in cached else "unknown"),
                )
                _RAM_CACHE[cache_key] = (time.monotonic(), result)
                return result

        fetch_start = start
        if not cached.empty:
            last_cached = datetime.strptime(str(cached["date"].iloc[-1])[:10], "%Y-%m-%d").date()
            fetch_start = max(datetime.strptime(start, "%Y-%m-%d").date(), last_cached - timedelta(days=7)).isoformat()

        try:
            cached_raw = _raw_view(cached)
            fresh, source, chain = _provider_attempts(
                symbol, fetch_start, end, cached_raw, store, trading_calendar=trading_calendar
            )
            agreement_bps = fresh.attrs.get("source_agreement_bps")
            source_count = int(fresh.attrs.get("source_count") or 1)
            merged_raw = _merge_frames(cached_raw, fresh, symbol)
            freshness = _freshness_sessions(str(merged_raw["date"].iloc[-1]), end, trading_calendar)
            merged = _prepare_canonical(
                symbol, merged_raw, store, start, end,
                agreement_bps=agreement_bps, source_count=source_count, freshness=freshness,
            )
            canonical_metadata = {
                "data_confidence_score": float(merged["data_confidence_score"].iloc[-1]),
                "adjustment_version": str(merged["adjustment_version"].iloc[-1]),
                "corporate_action_status": str(merged["corporate_action_status"].iloc[-1]),
            }
            if store is not None:
                store.upsert_history(symbol, merged, source, chain)
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
                canonical_source=source,
                source_agreement_bps=agreement_bps,
                data_confidence_score=canonical_metadata["data_confidence_score"],
                adjustment_version=canonical_metadata["adjustment_version"],
                corporate_action_status=canonical_metadata["corporate_action_status"],
            )
        except InactiveHistory as exc:
            merged_raw = _merge_frames(_raw_view(cached), exc.frame, symbol)
            merged = _prepare_canonical(
                symbol, merged_raw, store, start, end,
                agreement_bps=None, source_count=2, freshness=exc.freshness,
            )
            if store is not None:
                store.upsert_history(symbol, merged, exc.source, exc.source_chain)
                store.record_failure(symbol, str(exc), status="inactive")
            result = HistoryResult(
                frame=merged,
                source=exc.source,
                source_chain=exc.source_chain,
                quality_status="inactive",
                served_from_cache=False,
                freshness_sessions=exc.freshness,
                last_success_at=datetime.now(timezone.utc).isoformat(),
                canonical_source=exc.source,
                data_confidence_score=float(merged["data_confidence_score"].iloc[-1]),
                adjustment_version=str(merged["adjustment_version"].iloc[-1]),
                corporate_action_status=str(merged["corporate_action_status"].iloc[-1]),
            )
        except HistoryUnavailable as exc:
            if cached.empty:
                if store is not None:
                    store.record_failure(symbol, exc.reason)
                raise
            freshness = _freshness_sessions(str(cached["date"].iloc[-1]), end, trading_calendar)
            cached_adjustment = str(cached["adjustment_version"].iloc[-1]) if "adjustment_version" in cached else "raw-v1"
            if store is not None and hasattr(store, "load_corporate_actions") and cached_adjustment != ADJUSTMENT_VERSION:
                store.record_failure(symbol, "Bản cache chưa được điều chỉnh total-return", status="adjustment_pending")
                raise HistoryUnavailable(symbol, "Bản cache chưa được điều chỉnh total-return", exc.source_chain)
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
                canonical_source=str(state.get("last_source") or "PostgreSQL"),
                source_agreement_bps=(float(cached["source_agreement_bps"].dropna().iloc[-1]) if "source_agreement_bps" in cached and not cached["source_agreement_bps"].dropna().empty else None),
                data_confidence_score=(float(cached["data_confidence_score"].dropna().iloc[-1]) if "data_confidence_score" in cached and not cached["data_confidence_score"].dropna().empty else None),
                adjustment_version=(str(cached["adjustment_version"].iloc[-1]) if "adjustment_version" in cached else "raw-v1"),
                corporate_action_status=(str(cached["corporate_action_status"].iloc[-1]) if "corporate_action_status" in cached else "unknown"),
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
        coverage_by_group: Dict[str, Any] = {}
        try:
            from rrg_engine import SMC_TOP_FALLBACK
            from sector_mapping import SECTOR_DEFINITIONS
            coverage_by_group["SMC_TOP"] = store.coverage(SMC_TOP_FALLBACK)
            for key, definition in SECTOR_DEFINITIONS.items():
                symbols = list(definition.get("symbols") or [])
                if symbols:
                    coverage_by_group[key] = store.coverage(symbols)
        except Exception as exc:
            LOGGER.warning("rrg_health_coverage_error error=%s", exc)
        now = time.monotonic()
        payload.update({
            "strict_mode": strict,
            "status": "ok",
            "coverage_by_group": coverage_by_group,
            "ram_cache_entries": len(_RAM_CACHE),
            "provider_circuits": {
                source: {
                    "failures": circuit.failures,
                    "open": circuit.opened_until > now,
                    "retry_limit": 3,
                }
                for source, circuit in _CIRCUITS.items()
            },
            "quality_rule_version": QUALITY_RULE_VERSION,
            "adjustment_version": ADJUSTMENT_VERSION,
        })
        return payload
    except Exception as exc:
        LOGGER.error("rrg_health_error error=%s", exc)
        return {
            "configured": bool(os.getenv("DATABASE_URL")),
            "strict_mode": strict,
            "status": "error",
            "error_code": "postgres_unavailable",
        }
