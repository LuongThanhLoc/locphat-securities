"""Market-bubble dataset built on the audited heatmap market universe.

The real-time board remains the source of truth for the current session. Daily
OHLC bars are warmed in the background and persisted locally so switching
between 1W/1M/1Y never fans out hundreds of upstream requests from a browser
request. Long-range performance follows TradingView Screener semantics: the
latest price is compared with the *open* of the selected anchor bar.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

from heatmap_engine import fetch_market_heatmap_data
from market_data_provider import fetch_kbs_history, fetch_vci_history
from corporate_calendar_engine import fetch_price_affecting_actions


SUPPORTED_RANGES = {"1D": 0, "1W": 7, "1M": 30, "1Y": 365}
MAX_REFERENCE_LAG_DAYS = 14
HISTORY_PRICE_BASIS = "SOURCE_REPORTED_OHLC"
SESSION_PRICE_BASIS = "SESSION_REFERENCE"
HISTORY_REFERENCE_FIELD = "open"
SESSION_CHANGE_FORMULA = "((last_price / session_reference_price) - 1) * 100"
HISTORY_CHANGE_FORMULA = "((current_price - anchor_open) / abs(anchor_open)) * 100"
CHANGE_FORMULA = HISTORY_CHANGE_FORMULA
SOURCE_COMPARISON_TOLERANCE_PCT = 0.5
SOURCE_COMPARISON_TOLERANCE_VND = 100.0
RECENT_CLOSE_TOLERANCE_PCT = 1.0
UPSTREAM_REQUESTS_PER_MINUTE = 50
_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_bubbles.db")
_DB_READY = False
_DB_LOCK = threading.Lock()
_WARM_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()
_WARM_THREAD: Optional[threading.Thread] = None
_WARM_STATE: Dict[str, Any] = {
    "running": False, "completed": 0, "total": 0, "error": None,
    "last_started_at": 0, "as_of": None,
}
_ACTION_LOCK = threading.Lock()
_ACTION_THREAD: Optional[threading.Thread] = None
_ACTION_STATE: Dict[str, Any] = {
    "running": False, "completed": 0, "total": 0, "error": None,
    "last_started_at": 0, "as_of": None,
}
_RATE_LOCK = threading.Lock()
_RATE_TOKENS = float(UPSTREAM_REQUESTS_PER_MINUTE)
_RATE_UPDATED_AT = time.monotonic()
_INDEX_MEMBERSHIP_TTL_SECONDS = 6 * 3600
_VN30_CACHE: Dict[str, Any] = {
    "symbols": set(), "fetched_at": 0, "source": None, "stale": True,
}


class HistoryBar(NamedTuple):
    trading_date: str
    open: float
    high: Optional[float]
    low: Optional[float]
    close: float
    source: str
    fetched_at: int
    price_basis: str
    source_endpoint: Optional[str]
    verification_status: str
    comparison_source: Optional[str]
    comparison_open: Optional[float]
    comparison_close: Optional[float]


def init_bubble_cache() -> None:
    global _DB_READY
    if _DB_READY:
        return
    with _DB_LOCK:
        if _DB_READY:
            return
        with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bubble_daily_closes (
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (symbol, trading_date)
                )
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(market_bubble_daily_closes)").fetchall()
            }
            if "price_basis" not in columns:
                conn.execute("ALTER TABLE market_bubble_daily_closes ADD COLUMN price_basis TEXT")
            if "source_endpoint" not in columns:
                conn.execute("ALTER TABLE market_bubble_daily_closes ADD COLUMN source_endpoint TEXT")
            # Schema v3 deliberately uses a new table. The legacy table only
            # contains closes, so it cannot safely produce an anchor open.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bubble_daily_ohlc (
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL,
                    low REAL,
                    close REAL NOT NULL,
                    volume REAL,
                    source TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    price_basis TEXT NOT NULL,
                    source_endpoint TEXT,
                    verification_status TEXT NOT NULL,
                    comparison_source TEXT,
                    comparison_open REAL,
                    comparison_close REAL,
                    PRIMARY KEY (symbol, trading_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bubble_index_members (
                    index_code TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (index_code, symbol)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bubble_corporate_actions (
                    symbol TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (symbol, event_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bubble_action_sync (
                    symbol TEXT PRIMARY KEY,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bubble_closes_date "
                "ON market_bubble_daily_closes(trading_date DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bubble_ohlc_date "
                "ON market_bubble_daily_ohlc(trading_date DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bubble_actions_date "
                "ON market_bubble_corporate_actions(symbol, event_date)"
            )
            conn.commit()
        _DB_READY = True


def normalize_index_symbols(raw: Any) -> List[str]:
    """Normalize vnstock Series/DataFrame/list responses into unique tickers."""
    if raw is None:
        return []
    if hasattr(raw, "columns"):
        columns = {str(column).lower(): column for column in raw.columns}
        column = columns.get("symbol") or columns.get("ticker") or columns.get("code")
        values = raw[column].tolist() if column is not None else []
    elif hasattr(raw, "tolist"):
        values = raw.tolist()
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = []
    symbols = set()
    for value in values:
        text = "" if value is None else str(value).upper().strip()
        if text and text != "NAN" and text.isalnum():
            symbols.add(text)
    return sorted(symbols)


def _read_cached_index_members(index_code: str) -> Tuple[List[str], int, Optional[str]]:
    init_bubble_cache()
    with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
        rows = conn.execute(
            "SELECT symbol, fetched_at, source FROM market_bubble_index_members WHERE index_code = ?",
            (index_code,),
        ).fetchall()
    if not rows:
        return [], 0, None
    return sorted(str(row[0]) for row in rows), max(int(row[1]) for row in rows), str(rows[0][2])


def _save_index_members(index_code: str, symbols: List[str], source: str, fetched_at: int) -> None:
    init_bubble_cache()
    with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
        conn.execute("DELETE FROM market_bubble_index_members WHERE index_code = ?", (index_code,))
        conn.executemany(
            "INSERT INTO market_bubble_index_members(index_code, symbol, source, fetched_at) VALUES (?, ?, ?, ?)",
            [(index_code, symbol, source, fetched_at) for symbol in symbols],
        )
        conn.commit()


def _get_vn30_members_unlocked(force_refresh: bool = False) -> Tuple[set[str], Dict[str, Any]]:
    """Fetch VN30 constituents from vnstock with a durable stale-cache fallback."""
    now = int(datetime.now().timestamp())
    if not _VN30_CACHE["symbols"]:
        symbols, fetched_at, source = _read_cached_index_members("VN30")
        _VN30_CACHE.update({
            "symbols": set(symbols), "fetched_at": fetched_at,
            "source": source, "stale": bool(symbols),
        })
    cache_age = now - int(_VN30_CACHE.get("fetched_at") or 0)
    if _VN30_CACHE["symbols"] and cache_age < _INDEX_MEMBERSHIP_TTL_SECONDS and not force_refresh:
        return set(_VN30_CACHE["symbols"]), {
            "source": _VN30_CACHE["source"], "stale": False,
            "fetched_at": _VN30_CACHE["fetched_at"],
        }

    errors: List[str] = []
    for source in ("KBS", "VCI"):
        try:
            from vnstock import Listing

            symbols = normalize_index_symbols(Listing(source=source, show_log=False).symbols_by_group("VN30"))
            if len(symbols) < 20 or len(symbols) > 40:
                raise ValueError(f"VN30 trả về {len(symbols)} mã")
            _save_index_members("VN30", symbols, f"vnstock/{source}", now)
            _VN30_CACHE.update({
                "symbols": set(symbols), "fetched_at": now,
                "source": f"vnstock/{source}", "stale": False,
            })
            return set(symbols), {"source": f"vnstock/{source}", "stale": False, "fetched_at": now}
        except Exception as exc:
            errors.append(f"{source}: {exc}")

    return set(_VN30_CACHE["symbols"]), {
        "source": _VN30_CACHE.get("source") or "unavailable",
        "stale": True, "fetched_at": _VN30_CACHE.get("fetched_at") or None,
        "error": "; ".join(errors),
    }


def get_vn30_members(force_refresh: bool = False) -> Tuple[set[str], Dict[str, Any]]:
    with _INDEX_LOCK:
        return _get_vn30_members_unlocked(force_refresh=force_refresh)


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized_sector_memberships(
    stock: Dict[str, Any], sector: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Return stable, unique sector memberships for one stock record."""
    sector = sector or {}
    raw_memberships = stock.get("sector_memberships") or []
    if not isinstance(raw_memberships, (list, tuple)):
        raw_memberships = []
    candidates = list(raw_memberships)
    candidates.append({
        "sector": stock.get("sector") or sector.get("name") or "Khác",
        "archetype": stock.get("sector_code") or sector.get("code") or "OTHER",
    })
    memberships: List[Dict[str, str]] = []
    seen = set()
    for raw in candidates:
        if isinstance(raw, str):
            name, archetype = raw.strip(), "OTHER"
        elif isinstance(raw, dict):
            name = str(raw.get("sector") or raw.get("name") or "").strip()
            archetype = str(raw.get("archetype") or raw.get("code") or "OTHER").strip()
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        memberships.append({"sector": name, "archetype": archetype or "OTHER"})
    return memberships or [{"sector": "Khác", "archetype": "OTHER"}]


def dedupe_common_stocks(
    sectors: Iterable[Dict[str, Any]], *, require_active: bool = True,
) -> List[Dict[str, Any]]:
    """Return one common-stock record per symbol, optionally including idle listings."""
    unique: Dict[str, Dict[str, Any]] = {}
    for sector in sectors or []:
        for raw in sector.get("stocks", []) or []:
            symbol = str(raw.get("symbol") or "").upper().strip()
            exchange = str(raw.get("exchange") or "").upper().replace("HSX", "HOSE")
            instrument = str(raw.get("instrument_type") or "STOCK").upper()
            active = (_finite_number(raw.get("volume")) or 0) > 0 or (_finite_number(raw.get("trading_value")) or 0) > 0
            if (
                not symbol
                or exchange not in {"HOSE", "HNX", "UPCOM"}
                or instrument != "STOCK"
                or (require_active and not active)
            ):
                continue
            candidate = dict(raw)
            candidate["exchange"] = exchange
            candidate["is_active"] = active
            candidate["sector_memberships"] = _normalized_sector_memberships(raw, sector)
            candidate["sector"] = str(
                raw.get("sector") or candidate["sector_memberships"][0]["sector"] or "Khác"
            )
            current = unique.get(symbol)
            if current is None:
                unique[symbol] = candidate
                continue
            merged_memberships = _normalized_sector_memberships({
                "sector_memberships": [
                    *(current.get("sector_memberships") or []),
                    *(candidate.get("sector_memberships") or []),
                ],
                "sector": current.get("sector") or candidate.get("sector"),
            })
            current_value = _finite_number(current.get("trading_value")) or 0
            candidate_value = _finite_number(candidate.get("trading_value")) or 0
            chosen = candidate if candidate_value > current_value else current
            chosen = dict(chosen)
            chosen["sector_memberships"] = merged_memberships
            chosen["is_active"] = bool(current.get("is_active") or candidate.get("is_active"))
            unique[symbol] = chosen
    return sorted(unique.values(), key=lambda row: row["symbol"])


def dedupe_active_stocks(sectors: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Backward-compatible active-universe helper used outside pre-open."""
    return dedupe_common_stocks(sectors, require_active=True)


def build_filter_groups(
    items: Iterable[Dict[str, Any]], vn30_members: set[str], vn30_meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build filter metadata from the exact universe returned to the browser."""
    rows = list(items or [])
    sector_counts: Dict[str, Dict[str, int]] = {}
    for item in rows:
        active = bool(item.get("is_active"))
        names = {
            str(membership.get("sector") or membership.get("name") or "").strip()
            for membership in item.get("sector_memberships", [])
            if isinstance(membership, dict)
        }
        if not names:
            names = {str(item.get("sector") or "Khác").strip()}
        for name in names:
            if not name:
                continue
            counts = sector_counts.setdefault(name, {"total_count": 0, "active_count": 0})
            counts["total_count"] += 1
            counts["active_count"] += int(active)

    vn30_rows = [item for item in rows if str(item.get("symbol") or "") in vn30_members]
    groups: List[Dict[str, Any]] = [{
        "key": "ALL", "type": "all", "label": "Tất cả ngành / chỉ số",
        "total_count": len(rows),
        "active_count": sum(int(bool(item.get("is_active"))) for item in rows),
        "enabled": bool(rows),
    }, {
        "key": "INDEX:VN30", "type": "index", "label": "VN30",
        "total_count": len(vn30_rows),
        "active_count": sum(int(bool(item.get("is_active"))) for item in vn30_rows),
        "enabled": bool(vn30_rows),
        "stale": bool(vn30_meta.get("stale")),
        "source": vn30_meta.get("source") or "unavailable",
        "error": vn30_meta.get("error"),
    }]
    groups.extend({
        "key": f"SECTOR:{name}", "type": "sector", "label": name,
        "total_count": counts["total_count"],
        "active_count": counts["active_count"],
        "enabled": counts["total_count"] > 0,
    } for name, counts in sorted(sector_counts.items()))
    return groups


def target_reference_date(as_of: date, range_key: str) -> date:
    if range_key not in SUPPORTED_RANGES or range_key == "1D":
        return as_of
    return as_of - timedelta(days=SUPPORTED_RANGES[range_key])


def calculate_change_pct(current_price: Any, reference_price: Any) -> Optional[float]:
    current = _finite_number(current_price)
    reference = _finite_number(reference_price)
    if current is None or reference is None or current <= 0 or reference <= 0:
        return None
    return round((current / reference - 1.0) * 100.0, 2)


def _load_reference_bars(
    symbols: List[str], target: date, *, price_basis: str = HISTORY_PRICE_BASIS,
) -> Dict[str, HistoryBar]:
    if not symbols:
        return {}
    init_bubble_cache()
    placeholders = ",".join("?" for _ in symbols)
    sql = f"""
        SELECT c.symbol, c.trading_date, c.open, c.high, c.low, c.close,
               c.source, c.fetched_at, c.price_basis, c.source_endpoint,
               c.verification_status, c.comparison_source,
               c.comparison_open, c.comparison_close
        FROM market_bubble_daily_ohlc c
        JOIN (
            SELECT symbol, MAX(trading_date) AS trading_date
            FROM market_bubble_daily_ohlc
            WHERE symbol IN ({placeholders}) AND trading_date <= ? AND price_basis = ?
            GROUP BY symbol
        ) latest ON latest.symbol = c.symbol AND latest.trading_date = c.trading_date
    """
    with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
        rows = conn.execute(sql, (*symbols, target.isoformat(), price_basis)).fetchall()
    return {
        str(row[0]): HistoryBar(
            trading_date=str(row[1]),
            open=float(row[2]),
            high=_finite_number(row[3]),
            low=_finite_number(row[4]),
            close=float(row[5]),
            source=str(row[6]),
            fetched_at=int(row[7]),
            price_basis=str(row[8]),
            source_endpoint=str(row[9]) if row[9] else None,
            verification_status=str(row[10]),
            comparison_source=str(row[11]) if row[11] else None,
            comparison_open=_finite_number(row[12]),
            comparison_close=_finite_number(row[13]),
        )
        for row in rows
    }


# Backward-compatible private alias for integrations that imported the former
# helper. It now returns HistoryBar records from the v3 OHLC cache.
_load_references = _load_reference_bars


def _history_source_endpoint(source: str) -> str:
    if source == "Vietcap":
        return "trading.vietcap.com.vn/chart/OHLCChart/gap-chart"
    if source == "KBS":
        return "kbbuddywts.kbsec.com.vn/iis-server/investment"
    return "unavailable"


def _normalize_history_frame(frame: Any) -> Dict[str, Dict[str, Optional[float]]]:
    if frame is None or frame.empty:
        return {}
    rows: Dict[str, Dict[str, Optional[float]]] = {}
    for raw in frame.to_dict("records"):
        day = str(raw.get("time") or raw.get("date") or "")[:10]
        open_price = _finite_number(raw.get("open"))
        high = _finite_number(raw.get("high"))
        low = _finite_number(raw.get("low"))
        close = _finite_number(raw.get("close"))
        volume = _finite_number(raw.get("volume"))
        if day and open_price and close and open_price > 0 and close > 0:
            rows[day] = {
                "open": open_price, "high": high, "low": low,
                "close": close, "volume": volume,
            }
    return rows


def _acquire_upstream_token() -> None:
    """Shared token bucket for history and corporate-action background work."""
    global _RATE_TOKENS, _RATE_UPDATED_AT
    refill_per_second = UPSTREAM_REQUESTS_PER_MINUTE / 60.0
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            _RATE_TOKENS = min(
                float(UPSTREAM_REQUESTS_PER_MINUTE),
                _RATE_TOKENS + (now - _RATE_UPDATED_AT) * refill_per_second,
            )
            _RATE_UPDATED_AT = now
            if _RATE_TOKENS >= 1.0:
                _RATE_TOKENS -= 1.0
                return
            wait_seconds = (1.0 - _RATE_TOKENS) / refill_per_second
        time.sleep(min(max(wait_seconds, 0.02), 1.2))


def _provider_frames(symbol: str, start: date, end: date) -> Dict[str, Any]:
    frames: Dict[str, Any] = {}
    for source, fetcher in (("Vietcap", fetch_vci_history), ("KBS", fetch_kbs_history)):
        try:
            _acquire_upstream_token()
            frame = fetcher(symbol, start.isoformat(), end.isoformat())
            if frame is not None and not frame.empty:
                frames[source] = frame
        except Exception:
            continue
    return frames


def _values_agree(primary: Any, comparison: Any) -> bool:
    primary_number = _finite_number(primary)
    comparison_number = _finite_number(comparison)
    if not primary_number or not comparison_number:
        return False
    tolerance = max(SOURCE_COMPARISON_TOLERANCE_VND, abs(primary_number) * SOURCE_COMPARISON_TOLERANCE_PCT / 100.0)
    return abs(primary_number - comparison_number) <= tolerance


def _source_difference_pct(primary: Any, comparison: Any) -> Optional[float]:
    primary_number = _finite_number(primary)
    comparison_number = _finite_number(comparison)
    if not primary_number or not comparison_number:
        return None
    return round(abs(primary_number - comparison_number) / abs(primary_number) * 100.0, 4)


def _fetch_symbol_history(
    symbol: str, start: date, end: date,
) -> Tuple[str, List[Dict[str, Any]], str, str, str]:
    """Fetch one coherent provider series and audit it against the fallback.

    Vietcap controls the series whenever available. KBS is only the canonical
    fallback if Vietcap has no usable rows; matching KBS rows are otherwise
    retained solely as independent comparison evidence.
    """
    frames = _provider_frames(symbol, start, end)
    primary_source = "Vietcap" if "Vietcap" in frames else ("KBS" if "KBS" in frames else "unavailable")
    if primary_source == "unavailable":
        return symbol, [], primary_source, HISTORY_PRICE_BASIS, "unavailable"
    comparison_source = "KBS" if primary_source == "Vietcap" and "KBS" in frames else None
    primary_rows = _normalize_history_frame(frames[primary_source])
    comparison_rows = _normalize_history_frame(frames[comparison_source]) if comparison_source else {}
    rows: List[Dict[str, Any]] = []
    for day, primary in sorted(primary_rows.items()):
        comparison = comparison_rows.get(day)
        if comparison:
            verification_status = (
                "CROSS_SOURCE_MATCH"
                if _values_agree(primary["open"], comparison["open"])
                and _values_agree(primary["close"], comparison["close"])
                else "SOURCE_DISAGREEMENT"
            )
        else:
            verification_status = "PRIMARY_ONLY" if primary_source == "Vietcap" else "FALLBACK_ONLY"
        rows.append({
            "trading_date": day,
            **primary,
            "verification_status": verification_status,
            "comparison_source": comparison_source,
            "comparison_open": comparison.get("open") if comparison else None,
            "comparison_close": comparison.get("close") if comparison else None,
        })
    return symbol, rows, primary_source, HISTORY_PRICE_BASIS, _history_source_endpoint(primary_source)


def _save_symbol_history(
    symbol: str, rows: List[Dict[str, Any]], source: str,
    price_basis: str, source_endpoint: str,
) -> None:
    if not rows:
        return
    init_bubble_cache()
    now = int(datetime.now().timestamp())
    with sqlite3.connect(_CACHE_PATH, timeout=20) as conn:
        conn.executemany(
            """
            INSERT INTO market_bubble_daily_ohlc(
                symbol, trading_date, open, high, low, close, volume,
                source, fetched_at, price_basis, source_endpoint,
                verification_status, comparison_source, comparison_open, comparison_close
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trading_date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume,
                source=excluded.source, fetched_at=excluded.fetched_at,
                price_basis=excluded.price_basis, source_endpoint=excluded.source_endpoint,
                verification_status=excluded.verification_status,
                comparison_source=excluded.comparison_source,
                comparison_open=excluded.comparison_open,
                comparison_close=excluded.comparison_close
            """,
            [(
                symbol, row["trading_date"], row["open"], row.get("high"), row.get("low"),
                row["close"], row.get("volume"), source, now, price_basis, source_endpoint,
                row["verification_status"], row.get("comparison_source"),
                row.get("comparison_open"), row.get("comparison_close"),
            ) for row in rows],
        )
        conn.commit()


def _warm_history(symbols: List[str], as_of: date) -> None:
    global _WARM_STATE
    start = as_of - timedelta(days=390)
    _WARM_STATE = {
        "running": True, "completed": 0, "total": len(symbols), "error": None,
        "last_started_at": int(datetime.now().timestamp()), "as_of": as_of.isoformat(),
    }
    try:
        # Each worker performs at most one upstream request at a time. Four
        # workers keep the full-market backfill bounded and provider-friendly.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="lp-bubbles") as pool:
            futures = {pool.submit(_fetch_symbol_history, symbol, start, as_of): symbol for symbol in symbols}
            for future in as_completed(futures):
                try:
                    symbol, rows, source, price_basis, source_endpoint = future.result()
                    _save_symbol_history(symbol, rows, source, price_basis, source_endpoint)
                except Exception as exc:
                    _WARM_STATE["error"] = str(exc)
                _WARM_STATE["completed"] += 1
    finally:
        _WARM_STATE["running"] = False


def start_history_warmup(symbols: List[str], as_of: date) -> bool:
    """Start one guarded, process-local warmup and return whether it is running."""
    global _WARM_THREAD
    with _WARM_LOCK:
        if _WARM_THREAD and _WARM_THREAD.is_alive():
            return True
        if not symbols:
            return False
        # Recently listed symbols may legitimately have no one-year anchor.
        # Do not hammer providers every time the browser polls coverage.
        if (
            _WARM_STATE.get("as_of") == as_of.isoformat()
            and int(datetime.now().timestamp()) - int(_WARM_STATE.get("last_started_at") or 0) < 6 * 3600
        ):
            return False
        _WARM_THREAD = threading.Thread(target=_warm_history, args=(list(symbols), as_of), daemon=True, name="market-bubble-warmup")
        _WARM_THREAD.start()
        return True


def _save_action_audit(
    symbol: str, start: date, end: date, events: List[Dict[str, Any]],
    *, status: str = "OK", error: Optional[str] = None,
) -> None:
    init_bubble_cache()
    now = int(datetime.now().timestamp())
    with sqlite3.connect(_CACHE_PATH, timeout=20) as conn:
        if status == "OK":
            conn.execute(
                "DELETE FROM market_bubble_corporate_actions WHERE symbol = ? AND event_date BETWEEN ? AND ?",
                (symbol, start.isoformat(), end.isoformat()),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO market_bubble_corporate_actions(
                    symbol,event_id,event_date,event_type,title,source,fetched_at,payload
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                [(
                    symbol, str(event.get("id") or f"{symbol}:{index}"),
                    str(event.get("event_date")), str(event.get("type") or "corporate_action"),
                    str(event.get("title") or "Sự kiện doanh nghiệp"),
                    str(event.get("source") or "VCI structured corporate events"), now,
                    json.dumps(event, ensure_ascii=False, default=str),
                ) for index, event in enumerate(events)],
            )
        conn.execute(
            """
            INSERT INTO market_bubble_action_sync(symbol,window_start,window_end,status,fetched_at,error)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                window_start=excluded.window_start, window_end=excluded.window_end,
                status=excluded.status, fetched_at=excluded.fetched_at, error=excluded.error
            """,
            (symbol, start.isoformat(), end.isoformat(), status, now, error),
        )
        conn.commit()


def _fetch_and_save_actions(symbol: str, start: date, end: date) -> None:
    try:
        _acquire_upstream_token()
        events = fetch_price_affecting_actions(symbol, start, end)
        _save_action_audit(symbol, start, end, events)
    except Exception as exc:
        _save_action_audit(symbol, start, end, [], status="ERROR", error=str(exc)[:500])
        raise


def _warm_actions(symbols: List[str], as_of: date) -> None:
    global _ACTION_STATE
    start = as_of - timedelta(days=380)
    _ACTION_STATE = {
        "running": True, "completed": 0, "total": len(symbols), "error": None,
        "last_started_at": int(datetime.now().timestamp()), "as_of": as_of.isoformat(),
    }
    try:
        # One worker plus the shared token bucket makes the 50 req/min ceiling
        # explicit and prevents live price polling from causing a fan-out.
        for symbol in symbols:
            try:
                _fetch_and_save_actions(symbol, start, as_of)
            except Exception as exc:
                _ACTION_STATE["error"] = str(exc)
            _ACTION_STATE["completed"] += 1
    finally:
        _ACTION_STATE["running"] = False


def _load_action_audits(
    symbols: List[str], target: date, as_of: date,
) -> Dict[str, Dict[str, Any]]:
    if not symbols:
        return {}
    init_bubble_cache()
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
        sync_rows = conn.execute(
            f"SELECT symbol,window_start,window_end,status,fetched_at,error FROM market_bubble_action_sync WHERE symbol IN ({placeholders})",
            symbols,
        ).fetchall()
        event_rows = conn.execute(
            f"SELECT symbol,payload FROM market_bubble_corporate_actions WHERE symbol IN ({placeholders}) AND event_date > ? AND event_date <= ?",
            (*symbols, target.isoformat(), as_of.isoformat()),
        ).fetchall()
    audits: Dict[str, Dict[str, Any]] = {}
    for row in sync_rows:
        covered = str(row[1]) <= target.isoformat() and str(row[2]) >= as_of.isoformat()
        audits[str(row[0])] = {
            "status": str(row[3]) if covered else "PENDING",
            "fetched_at": int(row[4]), "error": row[5], "events": [],
        }
    for symbol, payload in event_rows:
        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            continue
        audits.setdefault(str(symbol), {"status": "PENDING", "events": []})["events"].append(event)
    return audits


def _symbols_needing_action_audit(symbols: List[str], as_of: date) -> List[str]:
    audits = _load_action_audits(symbols, as_of - timedelta(days=365), as_of)
    return [symbol for symbol in symbols if audits.get(symbol, {}).get("status") != "OK"]


def start_action_warmup(symbols: List[str], as_of: date) -> bool:
    global _ACTION_THREAD
    with _ACTION_LOCK:
        if _ACTION_THREAD and _ACTION_THREAD.is_alive():
            return True
        if not symbols:
            return False
        if (
            _ACTION_STATE.get("as_of") == as_of.isoformat()
            and int(datetime.now().timestamp()) - int(_ACTION_STATE.get("last_started_at") or 0) < 6 * 3600
        ):
            return False
        _ACTION_THREAD = threading.Thread(
            target=_warm_actions, args=(list(symbols), as_of), daemon=True,
            name="market-bubble-actions",
        )
        _ACTION_THREAD.start()
        return True


def _iso_from_epoch(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _reference_lag_days(target: date, reference_date: Optional[str]) -> Optional[int]:
    if not reference_date:
        return None
    try:
        return (target - datetime.strptime(reference_date[:10], "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None


def _prices_reconcile(expected: Any, actual: Any) -> bool:
    expected_number = _finite_number(expected)
    actual_number = _finite_number(actual)
    if not expected_number or not actual_number:
        return False
    tolerance = max(100.0, expected_number * 0.01)
    return abs(expected_number - actual_number) <= tolerance


def _valid_ohlc(bar: HistoryBar) -> bool:
    values = [bar.open, bar.close]
    if any(value is None or value <= 0 for value in values):
        return False
    if bar.high is not None and bar.high < max(bar.open, bar.close, bar.low or bar.open):
        return False
    if bar.low is not None and bar.low > min(bar.open, bar.close, bar.high or bar.open):
        return False
    return True


def _reconciliation_status(
    stock: Dict[str, Any], latest: Optional[HistoryBar],
    as_of: date, market_session: Dict[str, Any],
) -> str:
    """Cross-check the latest provider close against the equivalent board price."""
    if not latest:
        return "MISSING_RECENT_CLOSE"
    if latest.price_basis != HISTORY_PRICE_BASIS:
        return "UNKNOWN_PRICE_BASIS"
    if not _valid_ohlc(latest):
        return "INVALID_OHLC"
    lag = _reference_lag_days(as_of, latest.trading_date)
    if lag is None or lag < 0:
        return "INVALID_RECENT_CLOSE"
    if lag == 0 and not market_session.get("is_live_matching"):
        return "PASSED" if _prices_reconcile(stock.get("match_price") or stock.get("price_vnd"), latest.close) else "FAILED"
    if 0 < lag <= 4:
        return "PASSED" if _prices_reconcile(stock.get("ref_price"), latest.close) else "FAILED"
    return "NOT_APPLICABLE"


QUALITY_REASONS: Dict[str, Tuple[str, str, bool]] = {
    "VERIFIED": ("VERIFIED", "Đã vượt đầy đủ kiểm định dữ liệu.", False),
    "SESSION_NOT_STARTED": ("VERIFIED", "Phiên mới chưa bắt đầu; biến động, khối lượng và giá trị giao dịch được đặt về 0.", False),
    "MISSING_CURRENT_PRICE": ("UNAVAILABLE", "Không lấy được giá khớp hiện tại hợp lệ.", True),
    "INVALID_SESSION_REFERENCE": ("UNAVAILABLE", "Không lấy được giá tham chiếu cùng snapshot.", True),
    "MISSING_HISTORY": ("UNAVAILABLE", "Không có phiên lịch sử phù hợp cho mốc yêu cầu.", True),
    "REFERENCE_TOO_OLD": ("UNAVAILABLE", "Phiên lịch sử gần nhất trễ quá 14 ngày so với mốc.", False),
    "UNKNOWN_PRICE_BASIS": ("UNVERIFIED", "Chưa xác minh được cơ sở của chuỗi OHLC.", True),
    "INVALID_REFERENCE_DATE": ("UNAVAILABLE", "Ngày phiên mốc không hợp lệ.", True),
    "INVALID_REFERENCE_PRICE": ("UNAVAILABLE", "Giá mở cửa phiên mốc không hợp lệ.", True),
    "INVALID_OHLC": ("UNVERIFIED", "OHLC không thỏa quan hệ giá hợp lệ.", True),
    "SINGLE_SOURCE": ("UNVERIFIED", "Chỉ có một nguồn lịch sử; cần cả Vietcap và KBS.", True),
    "SOURCE_DISAGREEMENT": ("UNVERIFIED", "Open hoặc Close giữa Vietcap và KBS vượt ngưỡng đồng thuận.", True),
    "MISSING_RECENT_CLOSE": ("UNVERIFIED", "Thiếu close gần nhất để đối soát với bảng giá.", True),
    "RECENT_CLOSE_MISMATCH": ("UNVERIFIED", "Close gần nhất lệch quá 1% so với bảng giá hiện tại.", True),
    "RECENT_CLOSE_UNVERIFIED": ("UNVERIFIED", "Chưa đối soát được close gần nhất với bảng giá.", True),
    "HISTORY_CACHE_STALE": ("UNVERIFIED", "Cache OHLC chưa đồng bộ tới phiên giao dịch hiện tại.", True),
    "CORPORATE_ACTION_AUDIT_PENDING": ("UNVERIFIED", "Đang kiểm tra sự kiện doanh nghiệp trong giai đoạn.", True),
    "CORPORATE_ACTION_SOURCE_ERROR": ("UNVERIFIED", "Nguồn sự kiện doanh nghiệp tạm thời không khả dụng.", True),
    "CORPORATE_ACTION_UNVERIFIED": ("UNVERIFIED", "Có sự kiện doanh nghiệp chưa xác minh cơ sở điều chỉnh OHLC.", False),
}


def _quality_result(reason_code: str) -> Dict[str, Any]:
    confidence, message, retryable = QUALITY_REASONS[reason_code]
    return {
        "data_confidence": confidence,
        "reason_code": reason_code,
        "reason_message": message,
        "retryable": retryable,
    }


def _history_quality_decision(
    current_price: Any, bar: Optional[HistoryBar], latest: Optional[HistoryBar],
    target: date, as_of: date, reconciliation_status: str,
    action_audit: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    current = _finite_number(current_price)
    if current is None or current <= 0:
        return _quality_result("MISSING_CURRENT_PRICE")
    if not bar:
        return _quality_result("MISSING_HISTORY")
    if bar.price_basis != HISTORY_PRICE_BASIS:
        return _quality_result("UNKNOWN_PRICE_BASIS")
    if not _valid_ohlc(bar):
        return _quality_result("INVALID_OHLC")
    lag = _reference_lag_days(target, bar.trading_date)
    if lag is None or lag < 0:
        return _quality_result("INVALID_REFERENCE_DATE")
    if lag > MAX_REFERENCE_LAG_DAYS:
        return _quality_result("REFERENCE_TOO_OLD")
    if _finite_number(bar.open) is None or bar.open <= 0:
        return _quality_result("INVALID_REFERENCE_PRICE")
    if bar.verification_status in {"PRIMARY_ONLY", "FALLBACK_ONLY"} or not bar.comparison_source:
        return _quality_result("SINGLE_SOURCE")
    if (
        bar.verification_status != "CROSS_SOURCE_MATCH"
        or not _values_agree(bar.open, bar.comparison_open)
        or not _values_agree(bar.close, bar.comparison_close)
    ):
        return _quality_result("SOURCE_DISAGREEMENT")
    if not latest:
        return _quality_result("MISSING_RECENT_CLOSE")
    latest_lag = _reference_lag_days(as_of, latest.trading_date)
    if latest_lag is None or latest_lag < 0 or latest_lag > 4:
        return _quality_result("HISTORY_CACHE_STALE")
    try:
        fetched_date = datetime.fromtimestamp(int(latest.fetched_at)).date()
    except (TypeError, ValueError, OSError):
        fetched_date = None
    if fetched_date is None or fetched_date < as_of:
        return _quality_result("HISTORY_CACHE_STALE")
    if reconciliation_status == "FAILED":
        return _quality_result("RECENT_CLOSE_MISMATCH")
    if reconciliation_status != "PASSED":
        return _quality_result("RECENT_CLOSE_UNVERIFIED")
    if not action_audit or action_audit.get("status") == "PENDING":
        return _quality_result("CORPORATE_ACTION_AUDIT_PENDING")
    if action_audit.get("status") != "OK":
        return _quality_result("CORPORATE_ACTION_SOURCE_ERROR")
    if action_audit.get("events"):
        return _quality_result("CORPORATE_ACTION_UNVERIFIED")
    return _quality_result("VERIFIED")


def _build_market_bubble_dataset_v3_legacy(range_key: str = "1D", force_refresh: bool = False) -> Dict[str, Any]:
    range_key = str(range_key or "1D").upper().strip()
    if range_key not in SUPPORTED_RANGES:
        raise ValueError("range phải là một trong: 1D, 1W, 1M, 1Y")

    # Share the exact same five-second live snapshot cache as the heatmap.
    # `force_refresh` remains available for explicit operational refreshes.
    snapshot = fetch_market_heatmap_data(force_refresh=force_refresh)
    stocks = dedupe_active_stocks(snapshot.get("sectors", []))
    vn30_members, vn30_meta = get_vn30_members()
    lineage = snapshot.get("data_lineage", {}) or {}
    as_of_text = str(lineage.get("latest_trading_date") or snapshot.get("trading_date") or date.today().isoformat())[:10]
    try:
        as_of = datetime.strptime(as_of_text, "%Y-%m-%d").date()
    except ValueError:
        as_of = date.today()
        as_of_text = as_of.isoformat()

    symbols = [row["symbol"] for row in stocks]
    target_date = target_reference_date(as_of, range_key)
    references = _load_reference_bars(symbols, target_date) if range_key != "1D" else {}
    latest_references = _load_reference_bars(symbols, as_of) if range_key != "1D" else {}

    items: List[Dict[str, Any]] = []
    available = 0
    sources = set()
    status_counts: Dict[str, int] = {}
    market_session = snapshot.get("market_session", {}) or {}
    current_source = str(lineage.get("price_source") or "Vietcap price board")
    current_observed_at = lineage.get("fetched_at") or snapshot.get("timestamp")
    is_session_range = range_key == "1D"
    price_basis = SESSION_PRICE_BASIS if is_session_range else HISTORY_PRICE_BASIS
    formula = SESSION_CHANGE_FORMULA if is_session_range else HISTORY_CHANGE_FORMULA
    metric_definition = "SESSION_CHANGE" if is_session_range else "TRADINGVIEW_SCREENER_PERFORMANCE"
    for stock in stocks:
        symbol = stock["symbol"]
        current_price = _finite_number(stock.get("match_price")) or _finite_number(stock.get("price_vnd"))
        reference_date: Optional[str] = as_of_text
        reference_price: Optional[float] = _finite_number(stock.get("ref_price"))
        reference_source = current_source
        reference_fetched_at = current_observed_at
        reference_endpoint: Optional[str] = None
        reference_lag_days: Optional[int] = 0
        calculation_status = "OK"
        reconciliation_status = "NOT_APPLICABLE"
        anchor_open: Optional[float] = None
        anchor_close: Optional[float] = None
        reference_verification_status = "SESSION_BOARD"
        comparison_source: Optional[str] = None
        comparison_open: Optional[float] = None
        comparison_close: Optional[float] = None
        if not is_session_range:
            cached = references.get(symbol)
            if cached:
                reference_date = cached.trading_date
                reference_price = cached.open
                anchor_open = cached.open
                anchor_close = cached.close
                reference_source = cached.source
                reference_fetched_at = _iso_from_epoch(cached.fetched_at)
                reference_endpoint = cached.source_endpoint
                reference_verification_status = cached.verification_status
                comparison_source = cached.comparison_source
                comparison_open = cached.comparison_open
                comparison_close = cached.comparison_close
                reference_lag_days = _reference_lag_days(target_date, reference_date)
                if cached.price_basis != HISTORY_PRICE_BASIS:
                    calculation_status = "UNKNOWN_PRICE_BASIS"
                elif not _valid_ohlc(cached):
                    calculation_status = "INVALID_OHLC"
                elif reference_lag_days is None or reference_lag_days < 0:
                    calculation_status = "INVALID_REFERENCE_DATE"
                elif reference_lag_days > MAX_REFERENCE_LAG_DAYS:
                    calculation_status = "REFERENCE_TOO_OLD"
                elif cached.verification_status == "SOURCE_DISAGREEMENT":
                    calculation_status = "SOURCE_DISAGREEMENT"
                reconciliation_status = _reconciliation_status(
                    stock, latest_references.get(symbol), as_of, market_session,
                )
                if reconciliation_status == "FAILED":
                    calculation_status = "SOURCE_QUALITY_FAILED"
            else:
                reference_date, reference_price = None, None
                reference_source, reference_fetched_at = "unavailable", None
                reference_lag_days = None
                calculation_status = "MISSING_HISTORY"
                reconciliation_status = _reconciliation_status(
                    stock, latest_references.get(symbol), as_of, market_session,
                )
        if current_price is None or current_price <= 0:
            calculation_status = "MISSING_CURRENT_PRICE"
        elif reference_price is None or reference_price <= 0:
            if calculation_status == "OK":
                calculation_status = "INVALID_REFERENCE_PRICE"
        change = calculate_change_pct(current_price, reference_price) if calculation_status == "OK" else None
        if change is not None:
            available += 1
            sources.add(reference_source)
        status_counts[calculation_status] = status_counts.get(calculation_status, 0) + 1
        items.append({
            "symbol": symbol,
            "name": str(stock.get("name") or symbol),
            "exchange": stock.get("exchange"),
            "sector": str(stock.get("sector") or "Khác"),
            "last_price": current_price,
            "reference_price": reference_price,
            "reference_date": reference_date,
            "change_pct": change,
            "current_source": current_source,
            "current_observed_at": current_observed_at,
            "target_reference_date": target_date.isoformat(),
            "reference_source": reference_source,
            "reference_fetched_at": reference_fetched_at,
            "reference_source_endpoint": reference_endpoint,
            "reference_lag_days": reference_lag_days,
            "reference_price_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
            "anchor_open": anchor_open,
            "anchor_close": anchor_close,
            "reference_verification_status": reference_verification_status,
            "comparison_source": comparison_source,
            "comparison_open": comparison_open,
            "comparison_close": comparison_close,
            "price_basis": price_basis,
            "price_basis_status": "BOARD_DEFINED" if is_session_range else "SOURCE_REPORTED_UNVERIFIED_ADJUSTMENT",
            "metric_definition": metric_definition,
            "calculation_status": calculation_status,
            "reconciliation_status": reconciliation_status,
            "market_cap": _finite_number(stock.get("market_cap")) or 0.0,
            "trading_value": _finite_number(stock.get("trading_value")) or 0.0,
            "status": str(stock.get("status") or "REF"),
            "is_vn30": symbol in vn30_members,
            "logo_url": f"https://cdn.simplize.vn/simplizevn/logo/{symbol}.jpeg",
        })

    missing_symbols = [item["symbol"] for item in items if item["change_pct"] is None]
    # Warm all symbols from the first page visit. Subsequent range changes only
    # read SQLite; the browser request itself never performs the upstream fanout.
    year_anchor = target_reference_date(as_of, "1Y")
    year_references = _load_reference_bars(symbols, year_anchor)
    all_latest_bars = latest_references or _load_reference_bars(symbols, as_of)
    priority_symbols = [
        item["symbol"] for item in sorted(items, key=lambda item: item["market_cap"], reverse=True)
    ]
    symbols_needing_history = [
        symbol for symbol in priority_symbols
        if symbol not in year_references
        or symbol not in all_latest_bars
        or all_latest_bars[symbol].trading_date != as_of_text
    ]
    refreshing = start_history_warmup(symbols_needing_history, as_of)
    total = len(items)
    coverage_pct = round(available / total * 100.0, 1) if total else 0.0
    stale = bool(snapshot.get("snapshot_frozen") and not snapshot.get("market_closed"))
    reference_fetch_times = [
        cached.fetched_at for cached in references.values() if cached.fetched_at
    ]
    newest_history_fetch = max(reference_fetch_times) if reference_fetch_times else None
    now_epoch = int(datetime.now().timestamp())
    return {
        "schema_version": 3,
        "as_of": as_of_text,
        "range": range_key,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "price_basis": price_basis,
        "formula": formula,
        "metric_definition": metric_definition,
        "evaluation_time": current_observed_at,
        "anchor_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
        "target_reference_date": target_date.isoformat(),
        "methodology": {
            "range_definition": "CALENDAR_DAYS",
            "range_days": SUPPORTED_RANGES[range_key],
            "reference_selection": "LATEST_TRADING_BAR_NOT_AFTER_TARGET",
            "max_reference_lag_days": MAX_REFERENCE_LAG_DAYS,
            "metric_definition": metric_definition,
            "anchor_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
            "evaluation_time": current_observed_at,
            "price_basis": price_basis,
            "price_basis_status": "BOARD_DEFINED" if is_session_range else "SOURCE_REPORTED_UNVERIFIED_ADJUSTMENT",
            "price_basis_label": (
                "Giá khớp so với giá tham chiếu cùng phiên"
                if is_session_range else
                "OHLC do nguồn công bố; dùng giá mở cửa phiên mốc. Chưa khẳng định chuỗi điều chỉnh giống TradingView"
            ),
            "formula": formula,
            "no_synthetic_data": True,
            "current_source": current_source,
            "current_observed_at": current_observed_at,
            "history_source_priority": ["Vietcap", "KBS"],
            "source_comparison_tolerance_pct": SOURCE_COMPARISON_TOLERANCE_PCT,
            "tradingview_compatible_definition": not is_session_range,
            "tradingview_data_source": False,
        },
        "history_cache": {
            "price_basis": HISTORY_PRICE_BASIS,
            "newest_fetched_at": _iso_from_epoch(newest_history_fetch),
            "age_seconds": max(0, now_epoch - newest_history_fetch) if newest_history_fetch else None,
            "status": "WARMING" if refreshing else ("READY" if newest_history_fetch else "EMPTY"),
            "stale": False,
            "legacy_unverified_rows_ignored": True,
            "legacy_close_only_rows_ignored": True,
        },
        "market_session": market_session,
        "refresh_interval_seconds": 5 if market_session.get("is_live_matching") else None,
        "indices": {
            "VN30": {
                **vn30_meta,
                "count": len(vn30_members),
                "symbols": sorted(vn30_members),
            }
        },
        "stale": stale,
        "refreshing": refreshing,
        "warmup": dict(_WARM_STATE),
        "sources": sorted(sources) or [str(lineage.get("price_source") or "Vietcap price board")],
        "coverage": {
            "available": available,
            "total": total,
            "missing": total - available,
            "pct": coverage_pct,
            "missing_symbols": missing_symbols,
            "calculation_statuses": status_counts,
        },
        "items": items,
    }


def build_market_bubble_dataset(range_key: str = "1D", force_refresh: bool = False) -> Dict[str, Any]:
    """Build schema-v5 bubbles from the full listed common-stock universe."""
    range_key = str(range_key or "1D").upper().strip()
    if range_key not in SUPPORTED_RANGES:
        raise ValueError("range phải là một trong: 1D, 1W, 1M, 1Y")
    snapshot = fetch_market_heatmap_data(force_refresh=force_refresh)
    market_session = snapshot.get("market_session", {}) or {}
    pre_open = market_session.get("phase") == "PRE_OPEN"
    stocks = dedupe_common_stocks(snapshot.get("sectors", []), require_active=False)
    vn30_members, vn30_meta = get_vn30_members()
    lineage = snapshot.get("data_lineage", {}) or {}
    as_of_text = str(lineage.get("latest_trading_date") or snapshot.get("trading_date") or date.today().isoformat())[:10]
    try:
        as_of = datetime.strptime(as_of_text, "%Y-%m-%d").date()
    except ValueError:
        as_of = date.today()
        as_of_text = as_of.isoformat()

    symbols = [row["symbol"] for row in stocks]
    target_date = target_reference_date(as_of, range_key)
    is_session_range = range_key == "1D"
    references = _load_reference_bars(symbols, target_date) if not is_session_range else {}
    latest_references = _load_reference_bars(symbols, as_of) if not is_session_range else {}
    action_audits = _load_action_audits(symbols, target_date, as_of) if not is_session_range else {}
    current_source = str(lineage.get("price_source") or "Vietcap price board")
    current_observed_at = lineage.get("fetched_at") or snapshot.get("timestamp")
    quality_checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    price_basis = SESSION_PRICE_BASIS if is_session_range else HISTORY_PRICE_BASIS
    formula = SESSION_CHANGE_FORMULA if is_session_range else HISTORY_CHANGE_FORMULA
    metric_definition = "SESSION_CHANGE" if is_session_range else "TRADINGVIEW_SCREENER_PERFORMANCE"
    session_reset_applied = bool(is_session_range and pre_open)
    items: List[Dict[str, Any]] = []
    confidence_counts = {"VERIFIED": 0, "UNVERIFIED": 0, "UNAVAILABLE": 0}
    reason_counts: Dict[str, int] = {}
    verified_sources = set()

    for stock in stocks:
        symbol = stock["symbol"]
        current_price = _finite_number(stock.get("match_price")) or _finite_number(stock.get("price_vnd"))
        cached = references.get(symbol)
        latest = latest_references.get(symbol)
        audit = action_audits.get(symbol)
        reference_price = _finite_number(stock.get("ref_price")) if is_session_range else (cached.open if cached else None)
        reference_date = as_of_text if is_session_range else (cached.trading_date if cached else None)
        reconciliation_status = "PASSED" if is_session_range else _reconciliation_status(stock, latest, as_of, market_session)
        if session_reset_applied:
            quality = _quality_result("SESSION_NOT_STARTED")
        elif is_session_range:
            if current_price is None or current_price <= 0:
                quality = _quality_result("MISSING_CURRENT_PRICE")
            elif reference_price is None or reference_price <= 0:
                quality = _quality_result("INVALID_SESSION_REFERENCE")
            else:
                quality = _quality_result("VERIFIED")
        else:
            quality = _history_quality_decision(
                current_price, cached, latest, target_date, as_of,
                reconciliation_status, audit,
            )
            if quality["reason_code"] == "SINGLE_SOURCE":
                missing_source = "KBS" if cached and cached.source == "Vietcap" else "Vietcap"
                quality = {**quality, "reason_message": f"Thiếu giá mở cửa hoặc đóng cửa từ {missing_source}."}
        verified = quality["data_confidence"] == "VERIFIED"
        change = 0.0 if session_reset_applied else (calculate_change_pct(current_price, reference_price) if verified else None)
        if verified and change is None:
            quality = _quality_result("INVALID_REFERENCE_PRICE")
            verified = False
        confidence_counts[quality["data_confidence"]] += 1
        reason_counts[quality["reason_code"]] = reason_counts.get(quality["reason_code"], 0) + 1
        if verified:
            verified_sources.add(current_source if is_session_range else cached.source)
        differences = [] if not cached else [
            value for value in (
                _source_difference_pct(cached.open, cached.comparison_open),
                _source_difference_pct(cached.close, cached.comparison_close),
            ) if value is not None
        ]
        events = list((audit or {}).get("events") or [])
        reference_source = current_source if is_session_range else (cached.source if cached else "unavailable")
        reference_fetched_at = current_observed_at if is_session_range else (_iso_from_epoch(cached.fetched_at) if cached else None)
        items.append({
            "symbol": symbol,
            "name": str(stock.get("name") or symbol),
            "exchange": stock.get("exchange"),
            "sector": str(stock.get("sector") or "Khác"),
            "sector_memberships": _normalized_sector_memberships(stock),
            "index_memberships": ["VN30"] if symbol in vn30_members else [],
            "is_active": bool(stock.get("is_active")),
            "last_price": current_price,
            "volume": 0.0 if session_reset_applied else (_finite_number(stock.get("volume")) or 0.0),
            "reference_price": reference_price,
            "reference_date": reference_date,
            "change_pct": change,
            "current_source": current_source,
            "current_observed_at": current_observed_at,
            "target_reference_date": target_date.isoformat(),
            "reference_source": reference_source,
            "reference_fetched_at": reference_fetched_at,
            "reference_source_endpoint": cached.source_endpoint if cached else None,
            "reference_lag_days": 0 if is_session_range else _reference_lag_days(target_date, reference_date),
            "reference_price_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
            "anchor_open": cached.open if cached else None,
            "anchor_close": cached.close if cached else None,
            "reference_verification_status": "SESSION_BOARD" if is_session_range else (cached.verification_status if cached else "UNAVAILABLE"),
            "comparison_source": cached.comparison_source if cached else None,
            "comparison_open": cached.comparison_open if cached else None,
            "comparison_close": cached.comparison_close if cached else None,
            "sources_checked": [current_source] if is_session_range else [
                value for value in (cached.source if cached else None, cached.comparison_source if cached else None) if value
            ],
            "source_agreement_pct": max(differences) if differences else None,
            "corporate_actions_detected": [{
                "type": event.get("type"), "event_date": event.get("event_date"),
                "title": event.get("title"), "source": event.get("source"),
            } for event in events],
            "quality_checked_at": quality_checked_at,
            **quality,
            "price_basis": price_basis,
            "price_basis_status": "BOARD_DEFINED" if is_session_range else "SOURCE_REPORTED_UNVERIFIED_ADJUSTMENT",
            "metric_definition": metric_definition,
            "calculation_status": quality["reason_code"] if session_reset_applied else ("OK" if verified else quality["reason_code"]),
            "reconciliation_status": reconciliation_status,
            "market_cap": _finite_number(stock.get("market_cap")) or 0.0,
            "trading_value": 0.0 if session_reset_applied else (_finite_number(stock.get("trading_value")) or 0.0),
            "status": "REF" if session_reset_applied else str(stock.get("status") or "REF"),
            "session_reset": session_reset_applied,
            "is_vn30": symbol in vn30_members,
            "logo_url": f"https://cdn.simplize.vn/simplizevn/logo/{symbol}.jpeg",
        })

    priority_symbols = [item["symbol"] for item in sorted(items, key=lambda item: item["market_cap"], reverse=True)]
    year_references = _load_reference_bars(symbols, target_reference_date(as_of, "1Y"))
    all_latest_bars = latest_references or _load_reference_bars(symbols, as_of)
    history_needed = [symbol for symbol in priority_symbols if symbol not in year_references or symbol not in all_latest_bars]
    history_refreshing = start_history_warmup(history_needed, as_of)
    action_needed = set(_symbols_needing_action_audit(symbols, as_of))
    action_refreshing = start_action_warmup([symbol for symbol in priority_symbols if symbol in action_needed], as_of)
    total = len(items)
    verified_count = confidence_counts["VERIFIED"]
    warning_symbols = [item["symbol"] for item in items if item["data_confidence"] != "VERIFIED"]
    coverage_pct = round(verified_count / total * 100.0, 1) if total else 0.0
    reference_fetch_times = [bar.fetched_at for bar in references.values() if bar.fetched_at]
    newest_history_fetch = max(reference_fetch_times) if reference_fetch_times else None
    now_epoch = int(datetime.now().timestamp())
    stale = bool(snapshot.get("snapshot_frozen") and not snapshot.get("market_closed"))
    filter_groups = build_filter_groups(items, vn30_members, vn30_meta)
    return {
        "schema_version": 5,
        "as_of": as_of_text,
        "range": range_key,
        "generated_at": quality_checked_at,
        "price_basis": price_basis,
        "formula": formula,
        "metric_definition": metric_definition,
        "evaluation_time": current_observed_at,
        "anchor_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
        "target_reference_date": target_date.isoformat(),
        "methodology": {
            "range_definition": "CALENDAR_DAYS",
            "range_days": SUPPORTED_RANGES[range_key],
            "reference_selection": "LATEST_TRADING_BAR_NOT_AFTER_TARGET",
            "max_reference_lag_days": MAX_REFERENCE_LAG_DAYS,
            "metric_definition": metric_definition,
            "anchor_field": "session_reference" if is_session_range else HISTORY_REFERENCE_FIELD,
            "evaluation_time": current_observed_at,
            "price_basis": price_basis,
            "price_basis_status": "BOARD_DEFINED" if is_session_range else "SOURCE_REPORTED_UNVERIFIED_ADJUSTMENT",
            "price_basis_label": "Giá khớp so với giá tham chiếu cùng snapshot" if is_session_range else "OHLC do Vietcap và KBS cùng công bố; dùng Open phiên mốc",
            "formula": formula,
            "no_synthetic_data": True,
            "current_source": current_source,
            "current_observed_at": current_observed_at,
            "history_source_priority": ["Vietcap", "KBS"],
            "source_comparison_tolerance_pct": SOURCE_COMPARISON_TOLERANCE_PCT,
            "source_comparison_tolerance_vnd": SOURCE_COMPARISON_TOLERANCE_VND,
            "recent_close_tolerance_pct": RECENT_CLOSE_TOLERANCE_PCT,
            "verification_policy": "TWO_SOURCES_AND_NO_UNVERIFIED_CORPORATE_ACTIONS",
            "tradingview_compatible_definition": not is_session_range,
            "tradingview_data_source": False,
        },
        "history_cache": {
            "price_basis": HISTORY_PRICE_BASIS,
            "newest_fetched_at": _iso_from_epoch(newest_history_fetch),
            "age_seconds": max(0, now_epoch - newest_history_fetch) if newest_history_fetch else None,
            "status": "WARMING" if history_refreshing else ("READY" if newest_history_fetch else "EMPTY"),
            "stale": False,
            "legacy_unverified_rows_ignored": True,
            "legacy_close_only_rows_ignored": True,
        },
        "corporate_action_cache": {
            "source": "VCI structured corporate events",
            "status": "WARMING" if action_refreshing else ("PARTIAL" if action_needed else "READY"),
            "requests_per_minute_limit": UPSTREAM_REQUESTS_PER_MINUTE,
            "warmup": dict(_ACTION_STATE),
        },
        "market_session": market_session,
        "market_closed": bool(snapshot.get("market_closed")),
        "snapshot_frozen": bool(snapshot.get("snapshot_frozen")),
        "served_from": snapshot.get("served_from"),
        "snapshot_timestamp": snapshot.get("timestamp"),
        "session_date": market_session.get("calendar_date"),
        "session_reset_applied": session_reset_applied,
        "refresh_interval_seconds": 5 if market_session.get("is_live_matching") else None,
        "indices": {"VN30": {**vn30_meta, "count": len(vn30_members), "symbols": sorted(vn30_members)}},
        "filter_groups": filter_groups,
        "stale": stale,
        "refreshing": bool(history_refreshing or action_refreshing),
        "warmup": dict(_WARM_STATE),
        "sources": sorted(verified_sources) or [current_source],
        "coverage": {
            "available": verified_count,
            "total": total,
            "missing": total - verified_count,
            "pct": coverage_pct,
            "verified": verified_count,
            "unverified": confidence_counts["UNVERIFIED"],
            "unavailable": confidence_counts["UNAVAILABLE"],
            "verified_pct": coverage_pct,
            "warning_symbols": warning_symbols,
            "missing_symbols": warning_symbols,
            "confidence_counts": confidence_counts,
            "reason_counts": reason_counts,
            "calculation_statuses": reason_counts,
        },
        "items": items,
    }
