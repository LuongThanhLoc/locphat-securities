"""LP Relative Rotation Graph calculation engine.

Computes transparent LP RS-Ratio and LP RS-Momentum values against a benchmark
(VNINDEX / VN30 / HNXINDEX) using verified Vietcap/KBS close prices, then
constructs historical rotation tail curves across the four RRG quadrants:

1. Leading    (Dẫn dắt)  : RS-Ratio >= 100, RS-Momentum >= 100   (Green)
2. Weakening  (Suy yếu)   : RS-Ratio >= 100, RS-Momentum <  100   (Orange)
3. Lagging    (Tụt hậu)   : RS-Ratio <  100, RS-Momentum <  100   (Red)
4. Improving  (Hồi phục)  : RS-Ratio <  100, RS-Momentum >= 100   (Blue)

Industry group membership is sourced from `sector_mapping.SECTOR_DEFINITIONS`
so the same 25 ICB sectors that feed the heatmap (and the heatmap's
`sectorFilter` dropdown) drive the RRG group selector — guaranteeing visual
parity between the two pages.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from rrg_data_gateway import HistoryUnavailable, RrgStoreUnavailable, get_verified_history

NORMALIZATION_WINDOW = 63
MOMENTUM_LAG = 5
MIN_CALCULATION_SESSIONS = 252
SCORE_WEIGHTS = {
    "rs_ratio": 0.25,
    "rs_momentum": 0.30,
    "delta_ratio_5d": 0.20,
    "delta_momentum_5d": 0.20,
    "positive_persistence_5d": 0.05,
}

# ---------------------------------------------------------------------------
# Sector / Industry Group definitions — single source of truth.
# ---------------------------------------------------------------------------
# NOTE: importing sector_mapping at module load keeps the RRG groups in lock-
# step with the heatmap (which uses the same dict). The fallback list is used
# only if the import fails (e.g. running tests in isolation).
try:
    from sector_mapping import SECTOR_DEFINITIONS, get_sector_info  # type: ignore
except Exception:  # pragma: no cover - defensive fallback
    SECTOR_DEFINITIONS = {}
    def get_sector_info(symbol: str) -> Dict[str, str]:  # type: ignore
        return {"sector": "Khác", "archetype": "MANUFACTURING_GENERAL"}


# Manual fallback for the "cổ phiếu tiêu điểm" bucket. Used when we can't
# compute a real turnover ranking (e.g. vnstock offline). The list is the
# Vietnamese large-/mid-cap liquid names Lộc Phát traders watch by default.
SMC_TOP_FALLBACK = [
    "TCB", "VCB", "SSI", "VND", "HPG", "FPT", "MWG", "VHM",
    "MSN", "VNM", "STB", "MBB", "DGC", "VRE", "CTG",
]

# Map user-facing benchmark labels -> the ticker symbol vnstock accepts.
# The benchmark dropdown on the RRG page lets the user pick VN-Index, VN30,
# or HNX-Index — vnstock exposes each as a normal equity symbol.
BENCHMARK_SYMBOLS = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "HNXINDEX": "HNXINDEX",
    "HNX30": "HNX30",
    "UPCOM": "UPCOM",
}


# ---------------------------------------------------------------------------
# Quadrant helpers.
# ---------------------------------------------------------------------------
def get_quadrant(ratio: float, momentum: float) -> Dict[str, str]:
    """Return quadrant id/display/colors for an (rs_ratio, rs_momentum) point."""
    if ratio >= 100 and momentum >= 100:
        return {
            "id": "LEADING",
            "name": "Dẫn dắt",
            "color": "#10b981",
            "bg": "rgba(16, 185, 129, 0.15)",
        }
    if ratio >= 100 and momentum < 100:
        return {
            "id": "WEAKENING",
            "name": "Suy yếu",
            "color": "#f59e0b",
            "bg": "rgba(245, 158, 11, 0.15)",
        }
    if ratio < 100 and momentum < 100:
        return {
            "id": "LAGGING",
            "name": "Tụt hậu",
            "color": "#ef4444",
            "bg": "rgba(239, 68, 68, 0.15)",
        }
    return {
        "id": "IMPROVING",
        "name": "Hồi phục",
        "color": "#3b82f6",
        "bg": "rgba(59, 130, 246, 0.15)",
    }


# ---------------------------------------------------------------------------
# Price cache — short-lived (5-min TTL) above the durable PostgreSQL store.
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 300  # 5 minutes
_PRICE_CACHE: Dict[str, Tuple[float, pd.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(symbol: str, start: str, end: str) -> str:
    return f"{symbol.upper().strip()}|{start}|{end}"


def _cache_get(key: str) -> Optional[pd.DataFrame]:
    with _CACHE_LOCK:
        entry = _PRICE_CACHE.get(key)
        if not entry:
            return None
        ts, df = entry
        if (datetime.now() - datetime.fromtimestamp(ts)).total_seconds() > _CACHE_TTL_SECONDS:
            # Expired — drop and fetch fresh.
            _PRICE_CACHE.pop(key, None)
            return None
        return df


def _cache_put(key: str, df: pd.DataFrame) -> None:
    with _CACHE_LOCK:
        # Drop the entry if it's empty — caching "no data" forever would
        # permanently kill a symbol until process restart. A short retry
        # next time around gives intermittent vnstock failures a chance.
        if df is None or df.empty:
            _PRICE_CACHE.pop(key, None)
            return
        # Bounded cache: when full, drop the oldest 25% of entries.
        if len(_PRICE_CACHE) >= 1024:
            sorted_entries = sorted(_PRICE_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _ in sorted_entries[: len(sorted_entries) // 4]:
                _PRICE_CACHE.pop(k, None)
        _PRICE_CACHE[key] = (datetime.now().timestamp(), df)


# ---------------------------------------------------------------------------
# Data access — `Quote` adapter (same path as track_record/rsi_backtest).
# ---------------------------------------------------------------------------
def _fetch_history(
    symbol: str, start: str, end: str, trading_calendar: Optional[List[str]] = None
) -> pd.DataFrame:
    """Fetch one verified daily series through Vietcap -> KBS -> PostgreSQL."""
    key = _cache_key(symbol, start, end)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        result = get_verified_history(symbol, start, end, trading_calendar=trading_calendar)
        df = result.frame.copy()
        df.attrs.update({
            "data_source": result.source,
            "source_chain": result.source_chain,
            "quality_status": result.quality_status,
            "served_from_cache": result.served_from_cache,
            "freshness_sessions": result.freshness_sessions,
            "last_success_at": result.last_success_at,
        })
    except (HistoryUnavailable, RrgStoreUnavailable) as exc:
        df = pd.DataFrame()
        df.attrs.update({
            "quality_status": "source_unavailable",
            "error": str(exc),
            "source_chain": getattr(exc, "source_chain", []),
        })
        return df
    _cache_put(key, df)
    return df


def _close_series(symbol: str, start: str, end: str) -> pd.Series:
    """Return a daily close series indexed by ISO date string. Sorted ASC."""
    df = _fetch_history(symbol, start, end)
    if df.empty or "close" not in df.columns:
        return pd.Series(dtype=float)
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if "date" in df.columns:
        idx = df.loc[closes.index, "date"].astype(str)
    else:
        idx = [str(d)[:10] for d in closes.index]
    closes.index = idx
    closes = closes[~closes.index.duplicated(keep="last")]
    return closes.sort_index()


# ---------------------------------------------------------------------------
# LP RS-Ratio / RS-Momentum math.
# ---------------------------------------------------------------------------
def compute_rs_ratio_momentum(
    stock_closes: pd.Series,
    bench_closes: pd.Series,
    period: int = 14,
) -> Tuple[pd.Series, pd.Series]:
    """Compute the transparent LP-RRG series.

    The calculation uses log relative strength, an EMA sensitivity controlled
    by ``period``, and rolling 63-session z-scores.  It deliberately does not
    claim compatibility with the proprietary JdK implementation.
    """
    if stock_closes.empty or bench_closes.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    # Align on intersection of trading dates; both should be 1-D Series.
    aligned = pd.concat(
        [stock_closes.rename("stock"), bench_closes.rename("bench")],
        axis=1,
        join="inner",
    ).dropna()

    minimum_rows = max(MIN_CALCULATION_SESSIONS, NORMALIZATION_WINDOW + period + MOMENTUM_LAG)
    if len(aligned) < minimum_rows:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    s = aligned["stock"].astype(float)
    b = aligned["bench"].astype(float)

    valid_prices = (s > 0) & (b > 0)
    relative_log = np.log(s[valid_prices] / b[valid_prices])
    smoothed = relative_log.ewm(span=period, adjust=False, min_periods=period).mean()

    def rolling_lp_index(values: pd.Series) -> pd.Series:
        mean = values.rolling(NORMALIZATION_WINDOW, min_periods=NORMALIZATION_WINDOW).mean()
        std = values.rolling(NORMALIZATION_WINDOW, min_periods=NORMALIZATION_WINDOW).std(ddof=0)
        zscore = (values - mean) / std.replace(0.0, np.nan)
        # A flat relative-strength series is neutral, not missing.
        neutral = std.eq(0.0) & mean.notna()
        zscore = zscore.mask(neutral, 0.0).clip(-4.0, 4.0)
        return 100.0 + 5.0 * zscore

    rs_ratio = rolling_lp_index(smoothed)
    momentum_raw = smoothed.diff(MOMENTUM_LAG)
    rs_mom = rolling_lp_index(momentum_raw)
    pair = pd.concat([rs_ratio.rename("ratio"), rs_mom.rename("momentum")], axis=1).dropna()
    return pair["ratio"], pair["momentum"]


def _empty_item(symbol: str, sector_info: Dict[str, str], status: str = "no_data") -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "rs_ratio": None,
        "rs_momentum": None,
        "close": None,
        "change_5d_pct": None,
        "volume": None,
        "quadrant": None,
        "tail": [],
        "tail_quadrants": [],
        "sector": sector_info.get("sector", "Khác"),
        "sector_code": sector_info.get("archetype", "MANUFACTURING_GENERAL"),
        "data_status": status,
        "data_source": None,
        "source_chain": [],
        "quality_status": status,
        "history_sessions": 0,
        "required_sessions": MIN_CALCULATION_SESSIONS,
        "freshness_sessions": None,
        "last_success_at": None,
        "served_from_cache": False,
        "last_date": None,
        "delta_ratio_5d": None,
        "delta_momentum_5d": None,
        "heading_degrees": None,
        "heading_label": None,
        "velocity_5d": None,
        "distance_from_center": None,
        "quadrant_streak": 0,
        "positive_persistence_5d": 0.0,
        "rotation_score": None,
    }


def _heading_label(degrees: float) -> str:
    labels = ["Đông", "Đông Bắc", "Bắc", "Tây Bắc", "Tây", "Tây Nam", "Nam", "Đông Nam"]
    return labels[int(((degrees + 22.5) % 360) // 45)]


# ---------------------------------------------------------------------------
# Per-symbol processing.
# ---------------------------------------------------------------------------
def _build_item(
    symbol: str,
    bench_closes: pd.Series,
    period: int,
    tail_length: int,
    start: str,
    end: str,
) -> Dict[str, Any]:
    """Build one RRG entry for a single symbol.

    Always returns a dict (even when no data is available) so the frontend
    never breaks on a single bad ticker.
    """
    sym = symbol.upper().strip()
    raw_df = _fetch_history(sym, start, end, trading_calendar=list(bench_closes.index))
    if raw_df.empty or "close" not in raw_df.columns:
        item = _empty_item(sym, get_sector_info(sym), raw_df.attrs.get("quality_status", "source_unavailable"))
        item.update({"source_chain": raw_df.attrs.get("source_chain", [])})
        return item
    closes = pd.to_numeric(raw_df["close"], errors="coerce").dropna()
    closes.index = raw_df.loc[closes.index, "date"].astype(str)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    sector_info = get_sector_info(sym)

    aligned_sessions = len(pd.concat([closes.rename("stock"), bench_closes.rename("bench")], axis=1, join="inner").dropna())

    if raw_df.attrs.get("quality_status") == "inactive":
        item = _empty_item(sym, sector_info, "inactive")
        item.update({
            "close": float(closes.iloc[-1]),
            "data_source": raw_df.attrs.get("data_source"),
            "source_chain": raw_df.attrs.get("source_chain", []),
            "quality_status": "inactive",
            "history_sessions": aligned_sessions,
            "freshness_sessions": raw_df.attrs.get("freshness_sessions"),
            "last_success_at": raw_df.attrs.get("last_success_at"),
            "last_date": str(closes.index[-1])[:10],
        })
        return item

    rs_ratio, rs_mom = compute_rs_ratio_momentum(closes, bench_closes, period=period)

    data_status = "stale_valid" if raw_df.attrs.get("quality_status") == "stale_valid" else "ok"
    if rs_ratio.empty or rs_mom.empty:
        latest_close = float(closes.iloc[-1])
        item = _empty_item(sym, sector_info, "insufficient_history")
        item.update({
            "close": latest_close,
            "data_source": raw_df.attrs.get("data_source"),
            "source_chain": raw_df.attrs.get("source_chain", []),
            "quality_status": "insufficient_history",
            "history_sessions": aligned_sessions,
            "freshness_sessions": raw_df.attrs.get("freshness_sessions"),
            "last_success_at": raw_df.attrs.get("last_success_at"),
            "served_from_cache": bool(raw_df.attrs.get("served_from_cache")),
            "last_date": str(closes.index[-1])[:10],
        })
        return item

    # Align by date and take the most-recent `tail_length` points.
    full_pair = pd.concat([rs_ratio.rename("r"), rs_mom.rename("m")], axis=1).dropna()
    pair = full_pair.tail(tail_length)

    # Walk every tail row and pull matching close + volume from the raw feed.
    close_lookup = closes.to_dict()
    volume_lookup: Dict[str, float] = {}
    if not raw_df.empty and "date" in raw_df.columns and "volume" in raw_df.columns:
        for _, row in raw_df.iterrows():
            d = str(row["date"])[:10]
            v = pd.to_numeric(pd.Series([row.get("volume")]), errors="coerce").iloc[0]
            if not np.isnan(v):
                volume_lookup[d] = float(v)

    tail: List[Dict[str, Any]] = []
    for date_str, row in pair.iterrows():
        d = str(date_str)[:10]
        tail.append({
            "date": d,
            "rs_ratio": round(float(row["r"]), 2),
            "rs_momentum": round(float(row["m"]), 2),
            "close": float(close_lookup.get(d, np.nan)) if d in close_lookup else None,
            "volume": volume_lookup.get(d),
        })

    latest = tail[-1]
    quadrants: List[Optional[str]] = []
    for pt in tail:
        q = get_quadrant(pt["rs_ratio"], pt["rs_momentum"])
        # quant quadrant id only — frontend already has the colour map.
        quadrants.append(q["id"])

    metric_lookback = min(MOMENTUM_LAG, len(full_pair) - 1)
    prior = full_pair.iloc[-1 - metric_lookback]
    latest_full = full_pair.iloc[-1]
    delta_ratio = float(latest_full["r"] - prior["r"])
    delta_momentum = float(latest_full["m"] - prior["m"])
    heading_degrees = float(np.degrees(np.arctan2(delta_momentum, delta_ratio)) % 360.0)
    velocity = float(np.hypot(delta_ratio, delta_momentum) / max(metric_lookback, 1))
    full_quadrants = [get_quadrant(float(row["r"]), float(row["m"]))["id"] for _, row in full_pair.iterrows()]
    current_quadrant = full_quadrants[-1]
    streak = 0
    for quadrant_id in reversed(full_quadrants):
        if quadrant_id != current_quadrant:
            break
        streak += 1
    recent_quadrants = full_quadrants[-MOMENTUM_LAG:]
    positive_persistence = sum(q in {"LEADING", "IMPROVING"} for q in recent_quadrants) / len(recent_quadrants)

    # 5-day % change from the real close series.
    if len(closes) >= 6:
        last_close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-6])
        chg_5d = ((last_close - prev_close) / prev_close * 100.0) if prev_close > 0 else None
    else:
        chg_5d = None

    # Volume on the latest trading day.
    latest_volume = None
    if not raw_df.empty and "volume" in raw_df.columns and "date" in raw_df.columns:
        last_date = str(raw_df["date"].iloc[-1])[:10]
        latest_volume = volume_lookup.get(last_date)

    return {
        "symbol": sym,
        "rs_ratio": latest["rs_ratio"],
        "rs_momentum": latest["rs_momentum"],
        "close": latest["close"],
        "change_5d_pct": round(chg_5d, 2) if chg_5d is not None else None,
        "volume": int(latest_volume) if latest_volume is not None else None,
        "quadrant": get_quadrant(latest["rs_ratio"], latest["rs_momentum"]),
        "tail": tail,
        "tail_quadrants": quadrants,
        "sector": sector_info.get("sector", "Khác"),
        "sector_code": sector_info.get("archetype", "MANUFACTURING_GENERAL"),
        "data_status": data_status,
        "data_source": raw_df.attrs.get("data_source"),
        "source_chain": raw_df.attrs.get("source_chain", []),
        "quality_status": raw_df.attrs.get("quality_status", "valid"),
        "history_sessions": aligned_sessions,
        "required_sessions": MIN_CALCULATION_SESSIONS,
        "freshness_sessions": raw_df.attrs.get("freshness_sessions", 0),
        "last_success_at": raw_df.attrs.get("last_success_at"),
        "served_from_cache": bool(raw_df.attrs.get("served_from_cache")),
        "last_date": latest["date"],
        "delta_ratio_5d": round(delta_ratio, 2),
        "delta_momentum_5d": round(delta_momentum, 2),
        "heading_degrees": round(heading_degrees, 1),
        "heading_label": _heading_label(heading_degrees),
        "velocity_5d": round(velocity, 2),
        "distance_from_center": round(float(np.hypot(latest["rs_ratio"] - 100, latest["rs_momentum"] - 100)), 2),
        "quadrant_streak": streak,
        "positive_persistence_5d": round(positive_persistence, 2),
        "rotation_score": None,
    }


# ---------------------------------------------------------------------------
# Group resolution.
# ---------------------------------------------------------------------------
def _resolve_group(
    group_key: str,
    custom_symbols: Optional[List[str]],
) -> Tuple[List[str], str, str]:
    """Return (symbols, group_key, display_name) for a given group_key."""

    if custom_symbols:
        clean = list(dict.fromkeys(s.strip().upper() for s in custom_symbols if s and s.strip()))[:30]
        return clean, "CUSTOM", "Danh mục tùy chỉnh"

    if group_key == "SMC_TOP":
        # The original fallback list is curated; in a future iteration we can
        # rank by 20-day average turnover when that becomes available.
        return list(SMC_TOP_FALLBACK), "SMC_TOP", "Cổ phiếu Tiêu Điểm (Top Liquid)"

    if group_key in SECTOR_DEFINITIONS:
        data = SECTOR_DEFINITIONS[group_key]
        symbols = list(data.get("symbols", []))
        return symbols, group_key, data.get("sector", group_key)

    raise ValueError(f"Nhóm RRG không hợp lệ: {group_key}")


def _preset_groups_listing() -> List[Dict[str, Any]]:
    """Build the dropdown dataset for the frontend.

    Order: "SMC_TOP" first, then every ICB sector alphabetically, with
    "CUSTOM" appended last (handled by the frontend).
    """
    out: List[Dict[str, Any]] = [
        {
            "key": "SMC_TOP",
            "name": "Cổ phiếu Tiêu Điểm (Top Liquid)",
            "count": len(SMC_TOP_FALLBACK),
        }
    ]
    for arch, data in SECTOR_DEFINITIONS.items():
        symbols = data.get("symbols", []) or []
        if not symbols:
            continue
        out.append({
            "key": arch,
            "name": data.get("sector", arch),
            "count": len(symbols),
        })
    out.sort(key=lambda g: (g["key"] != "SMC_TOP", g["name"]))
    return out


def _assign_rotation_scores(items: List[Dict[str, Any]]) -> None:
    """Attach a transparent 0-100 cross-sectional rotation score."""
    valid = [item for item in items if item.get("rs_ratio") is not None]
    if not valid:
        return
    frame = pd.DataFrame(
        [
            {
                "symbol": item["symbol"],
                **{key: item.get(key) for key in SCORE_WEIGHTS},
            }
            for item in valid
        ]
    ).set_index("symbol")
    percentiles = frame.rank(pct=True, method="average", na_option="bottom")
    for item in valid:
        score = sum(float(percentiles.loc[item["symbol"], key]) * weight for key, weight in SCORE_WEIGHTS.items())
        item["rotation_score"] = round(max(0.0, min(100.0, score * 100.0)), 1)


def _build_rotation_radar(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    def summary(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "symbol", "sector", "quadrant", "rotation_score", "rs_ratio",
                "rs_momentum", "delta_ratio_5d", "delta_momentum_5d",
                "heading_label", "velocity_5d", "quadrant_streak",
            )
        }

    valid = [item for item in items if item.get("quadrant")]
    accelerating = [
        item for item in valid
        if item["quadrant"]["id"] in {"LEADING", "IMPROVING"}
        and (item.get("delta_ratio_5d") or 0) > 0
        and (item.get("delta_momentum_5d") or 0) > 0
    ]
    sustained = [
        item for item in valid
        if item["quadrant"]["id"] == "LEADING" and item.get("quadrant_streak", 0) >= 5
    ]
    weakening = []
    for item in valid:
        quadrants = item.get("tail_quadrants") or []
        just_left_leading = len(quadrants) >= 2 and quadrants[-2] == "LEADING" and quadrants[-1] != "LEADING"
        both_negative = (
            item["quadrant"]["id"] in {"WEAKENING", "LAGGING"}
            and (item.get("delta_ratio_5d") or 0) < 0
            and (item.get("delta_momentum_5d") or 0) < 0
        )
        if just_left_leading or both_negative:
            weakening.append(item)

    rank = lambda values: sorted(values, key=lambda item: (-(item.get("rotation_score") or -1), item["symbol"]))[:5]
    # Warnings prioritize the weakest score first.
    warning_rank = sorted(weakening, key=lambda item: (item.get("rotation_score") is None, item.get("rotation_score") or 0, item["symbol"]))[:5]
    return {
        "ACCELERATING": [summary(item) for item in rank(accelerating)],
        "SUSTAINED_LEADER": [summary(item) for item in rank(sustained)],
        "WEAKENING_ALERT": [summary(item) for item in warning_rank],
    }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------
def generate_rrg_dataset(
    group_key: str = "SMC_TOP",
    custom_symbols: Optional[List[str]] = None,
    benchmark_symbol: str = "VNINDEX",
    tail_length: int = 15,
    period: int = 14,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """Compute a full RRG dataset for the requested group/benchmark.

    Returns a dict with the same JSON shape the frontend already consumes:

        {
            "benchmark": "VNINDEX",
            "group_key": "BANKING",
            "group_name": "Ngân hàng",
            "total_symbols": 15,
            "tail_length": 15,
            "period": 14,
            "updated_at": "2026-08-08 09:30:00",
            "quadrant_counts": {...},
            "preset_groups": [...],
            "data": [
                {
                    "symbol": ...,
                    "rs_ratio": ...,
                    "rs_momentum": ...,
                    "close": ...,
                    "change_5d_pct": ...,
                    "volume": ...,
                    "quadrant": {...},
                    "tail": [...],
                    "sector": "Ngân hàng",
                    "sector_code": "BANKING",
                    "data_status": "ok" | "no_data" | "insufficient_history",
                }, ...
            ]
        }
    """
    benchmark_key = benchmark_symbol.upper().strip()
    if benchmark_key not in BENCHMARK_SYMBOLS:
        raise ValueError(f"Chỉ số tham chiếu không hợp lệ: {benchmark_symbol}")
    if period not in {10, 14, 20}:
        raise ValueError("period chỉ nhận 10, 14 hoặc 20")
    if tail_length not in {5, 10, 15, 20}:
        raise ValueError("tail_length chỉ nhận 5, 10, 15 hoặc 20")

    symbols, resolved_key, resolved_name = _resolve_group(group_key, custom_symbols)
    if not symbols:
        return _empty_dataset(resolved_key, resolved_name, benchmark_symbol, tail_length, period)

    # Fixed history: changing the visible tail must never change today's point.
    today = datetime.now().date()
    end_dt = today
    lookback_days = 620  # ~425 trading sessions: durable backfill target >= 400.
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    benchmark_sym = BENCHMARK_SYMBOLS[benchmark_key]
    bench_closes = _close_series(benchmark_sym, start_str, end_str)
    if bench_closes.empty:
        raise RrgDataIncomplete("benchmark_unavailable", [benchmark_sym])

    # Parallel compute with a gateway-level global fetch semaphore (max 3).
    items: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 16))) as ex:
        future_map = {
            ex.submit(
                _build_item,
                sym,
                bench_closes,
                period,
                tail_length,
                start_str,
                end_str,
            ): sym
            for sym in symbols
        }
        for fut in as_completed(future_map):
            try:
                items.append(fut.result())
            except Exception as exc:  # pragma: no cover - defensive
                sym = future_map[fut]
                print(f"[RRG] Unexpected failure for {sym}: {exc}")
                items.append(_empty_item(sym, get_sector_info(sym), "error"))

    _assign_rotation_scores(items)

    valid_items = [item for item in items if item.get("quadrant") is not None and item.get("data_status") in {"ok", "stale_valid"}]
    ineligible_items = [item for item in items if item.get("data_status") in {"insufficient_history", "inactive"}]
    failed_items = [item for item in items if item not in valid_items and item not in ineligible_items]
    eligible_symbols = len(valid_items) + len(failed_items)
    valid_symbols = len(valid_items)
    completeness_pct = round(valid_symbols / eligible_symbols * 100.0, 2) if eligible_symbols else 100.0
    if failed_items:
        raise RrgDataIncomplete("data_incomplete", [item["symbol"] for item in failed_items])

    # Default API order mirrors the table default: score desc, symbol asc.
    items.sort(
        key=lambda x: (
            x.get("rotation_score") is None,
            -(x.get("rotation_score") or 0.0),
            x["symbol"],
        )
    )

    # Quadrant distro counts (only over symbols with valid data).
    counts = {"LEADING": 0, "WEAKENING": 0, "LAGGING": 0, "IMPROVING": 0}
    for item in items:
        q = item.get("quadrant")
        if q and q.get("id") in counts:
            counts[q["id"]] += 1

    return {
        "benchmark": benchmark_sym,
        "group_key": resolved_key,
        "group_name": resolved_name,
        "total_symbols": len(items),
        "eligible_symbols": eligible_symbols,
        "valid_symbols": valid_symbols,
        "completeness_pct": completeness_pct,
        "coverage_status": "complete",
        "served_from_cache": any(item.get("served_from_cache") for item in items),
        "has_stale_data": any(item.get("data_status") == "stale_valid" for item in items),
        "data_as_of": max((item.get("last_date") or "" for item in valid_items), default=None),
        "tail_length": tail_length,
        "period": period,
        "method": "LP_RRG_V1",
        "normalization_window": NORMALIZATION_WINDOW,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quadrant_counts": counts,
        "preset_groups": _preset_groups_listing(),
        "rotation_radar": _build_rotation_radar(items),
        "data": items,
    }


class RrgDataIncomplete(RuntimeError):
    """Dataset-level completeness gate used by the HTTP layer."""

    def __init__(self, reason: str, missing_symbols: List[str]):
        super().__init__(reason)
        self.reason = reason
        self.missing_symbols = missing_symbols


def _empty_dataset(
    group_key: str,
    group_name: str,
    benchmark_symbol: str,
    tail_length: int,
    period: int,
    reason: str = "empty",
) -> Dict[str, Any]:
    """Return a well-formed empty payload so the frontend can render the
    empty-state UI without crashing on nulls."""
    return {
        "benchmark": benchmark_symbol,
        "group_key": group_key,
        "group_name": group_name,
        "total_symbols": 0,
        "eligible_symbols": 0,
        "valid_symbols": 0,
        "completeness_pct": 100.0,
        "coverage_status": "complete",
        "served_from_cache": False,
        "has_stale_data": False,
        "data_as_of": None,
        "tail_length": tail_length,
        "period": period,
        "method": "LP_RRG_V1",
        "normalization_window": NORMALIZATION_WINDOW,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quadrant_counts": {"LEADING": 0, "WEAKENING": 0, "LAGGING": 0, "IMPROVING": 0},
        "preset_groups": _preset_groups_listing(),
        "rotation_radar": {"ACCELERATING": [], "SUSTAINED_LEADER": [], "WEAKENING_ALERT": []},
        "data": [],
        "reason": reason,
    }
