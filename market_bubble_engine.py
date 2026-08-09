"""Market-bubble dataset built on the audited heatmap market universe.

The real-time board remains the source of truth for the current session.  Daily
closes are warmed in the background and persisted locally so switching between
1W/1M/1Y never fans out hundreds of upstream requests from a browser request.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from heatmap_engine import fetch_market_heatmap_data
from market_data_provider import Quote


SUPPORTED_RANGES = {"1D": 0, "1W": 7, "1M": 30, "1Y": 365}
MAX_REFERENCE_LAG_DAYS = 14
HISTORY_PRICE_BASIS = "ADJUSTED_CLOSE"
SESSION_PRICE_BASIS = "SESSION_REFERENCE"
CHANGE_FORMULA = "((last_price / reference_price) - 1) * 100"
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
_INDEX_MEMBERSHIP_TTL_SECONDS = 6 * 3600
_VN30_CACHE: Dict[str, Any] = {
    "symbols": set(), "fetched_at": 0, "source": None, "stale": True,
}


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
                "CREATE INDEX IF NOT EXISTS idx_bubble_closes_date "
                "ON market_bubble_daily_closes(trading_date DESC)"
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


def dedupe_active_stocks(sectors: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return one currently traded common-stock record per symbol."""
    unique: Dict[str, Dict[str, Any]] = {}
    for sector in sectors or []:
        for raw in sector.get("stocks", []) or []:
            symbol = str(raw.get("symbol") or "").upper().strip()
            exchange = str(raw.get("exchange") or "").upper().replace("HSX", "HOSE")
            instrument = str(raw.get("instrument_type") or "STOCK").upper()
            active = (_finite_number(raw.get("volume")) or 0) > 0 or (_finite_number(raw.get("trading_value")) or 0) > 0
            if not symbol or exchange not in {"HOSE", "HNX", "UPCOM"} or instrument != "STOCK" or not active:
                continue
            candidate = dict(raw)
            candidate["exchange"] = exchange
            candidate["sector"] = str(raw.get("sector") or sector.get("name") or "Khác")
            current = unique.get(symbol)
            if current is None or (_finite_number(candidate.get("trading_value")) or 0) > (_finite_number(current.get("trading_value")) or 0):
                unique[symbol] = candidate
    return sorted(unique.values(), key=lambda row: row["symbol"])


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


def _load_references(
    symbols: List[str], target: date, *, price_basis: str = HISTORY_PRICE_BASIS,
) -> Dict[str, Tuple[str, float, str, int, str, Optional[str]]]:
    if not symbols:
        return {}
    init_bubble_cache()
    placeholders = ",".join("?" for _ in symbols)
    sql = f"""
        SELECT c.symbol, c.trading_date, c.close, c.source,
               c.fetched_at, c.price_basis, c.source_endpoint
        FROM market_bubble_daily_closes c
        JOIN (
            SELECT symbol, MAX(trading_date) AS trading_date
            FROM market_bubble_daily_closes
            WHERE symbol IN ({placeholders}) AND trading_date <= ? AND price_basis = ?
            GROUP BY symbol
        ) latest ON latest.symbol = c.symbol AND latest.trading_date = c.trading_date
    """
    with sqlite3.connect(_CACHE_PATH, timeout=10) as conn:
        rows = conn.execute(sql, (*symbols, target.isoformat(), price_basis)).fetchall()
    return {
        str(symbol): (
            str(trading_date), float(close), str(source), int(fetched_at),
            str(row_basis), str(source_endpoint) if source_endpoint else None,
        )
        for symbol, trading_date, close, source, fetched_at, row_basis, source_endpoint in rows
    }


def _history_source_endpoint(source: str) -> str:
    if source == "Vietcap":
        return "trading.vietcap.com.vn/chart/OHLCChart/gap-chart"
    if source == "KBS":
        return "kbbuddywts.kbsec.com.vn/iis-server/investment"
    return "unavailable"


def _fetch_symbol_history(
    symbol: str, start: date, end: date,
) -> Tuple[str, List[Tuple[str, float]], str, str, str]:
    frame = Quote(symbol=symbol, source="VCI").history(start=start.isoformat(), end=end.isoformat())
    if frame is None or frame.empty:
        return symbol, [], "unavailable", HISTORY_PRICE_BASIS, "unavailable"
    source = str(frame.attrs.get("source") or "VCI/KBS")
    rows: List[Tuple[str, float]] = []
    for raw in frame.to_dict("records"):
        day = str(raw.get("time") or raw.get("date") or "")[:10]
        close = _finite_number(raw.get("close"))
        if day and close is not None and close > 0:
            rows.append((day, close))
    return symbol, rows, source, HISTORY_PRICE_BASIS, _history_source_endpoint(source)


def _save_symbol_history(
    symbol: str, rows: List[Tuple[str, float]], source: str,
    price_basis: str, source_endpoint: str,
) -> None:
    if not rows:
        return
    init_bubble_cache()
    now = int(datetime.now().timestamp())
    with sqlite3.connect(_CACHE_PATH, timeout=20) as conn:
        conn.executemany(
            """
            INSERT INTO market_bubble_daily_closes(
                symbol, trading_date, close, source, fetched_at, price_basis, source_endpoint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trading_date) DO UPDATE SET
                close=excluded.close, source=excluded.source, fetched_at=excluded.fetched_at,
                price_basis=excluded.price_basis, source_endpoint=excluded.source_endpoint
            """,
            [(symbol, day, close, source, now, price_basis, source_endpoint) for day, close in rows],
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
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="lp-bubbles") as pool:
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


def _reconciliation_status(
    stock: Dict[str, Any], latest: Optional[Tuple[str, float, str, int, str, Optional[str]]],
    as_of: date, market_session: Dict[str, Any],
) -> str:
    """Cross-check recent adjusted history when an equivalent board price exists."""
    if not latest:
        return "MISSING_RECENT_CLOSE"
    if len(latest) >= 6:
        latest_date, latest_close, _source, _fetched_at, basis, _endpoint = latest[:6]
    else:
        latest_date, latest_close, _source = latest[:3]
        basis = HISTORY_PRICE_BASIS
    if basis != HISTORY_PRICE_BASIS:
        return "UNKNOWN_PRICE_BASIS"
    lag = _reference_lag_days(as_of, latest_date)
    if lag is None or lag < 0:
        return "INVALID_RECENT_CLOSE"
    if lag == 0 and not market_session.get("is_live_matching"):
        return "PASSED" if _prices_reconcile(stock.get("match_price") or stock.get("price_vnd"), latest_close) else "FAILED"
    if 0 < lag <= 4:
        return "PASSED" if _prices_reconcile(stock.get("ref_price"), latest_close) else "FAILED"
    return "NOT_APPLICABLE"


def build_market_bubble_dataset(range_key: str = "1D", force_refresh: bool = False) -> Dict[str, Any]:
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
    references: Dict[str, Tuple[str, float, str, int, str, Optional[str]]] = {}
    if range_key != "1D":
        references = _load_references(symbols, target_date)
    latest_references = _load_references(symbols, as_of) if range_key != "1D" else {}

    items: List[Dict[str, Any]] = []
    available = 0
    sources = set()
    status_counts: Dict[str, int] = {}
    market_session = snapshot.get("market_session", {}) or {}
    current_source = str(lineage.get("price_source") or "Vietcap price board")
    current_observed_at = lineage.get("fetched_at") or snapshot.get("timestamp")
    price_basis = SESSION_PRICE_BASIS if range_key == "1D" else HISTORY_PRICE_BASIS
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
        if range_key != "1D":
            cached = references.get(symbol)
            if cached:
                if len(cached) >= 6:
                    (
                        reference_date, reference_price, reference_source,
                        fetched_at, cached_basis, reference_endpoint,
                    ) = cached[:6]
                    reference_fetched_at = _iso_from_epoch(fetched_at)
                else:
                    reference_date, reference_price, reference_source = cached[:3]
                    cached_basis = HISTORY_PRICE_BASIS
                reference_lag_days = _reference_lag_days(target_date, reference_date)
                if cached_basis != HISTORY_PRICE_BASIS:
                    calculation_status = "UNKNOWN_PRICE_BASIS"
                elif reference_lag_days is None or reference_lag_days < 0:
                    calculation_status = "INVALID_REFERENCE_DATE"
                elif reference_lag_days > MAX_REFERENCE_LAG_DAYS:
                    calculation_status = "REFERENCE_TOO_OLD"
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
            "price_basis": price_basis,
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
    year_references = _load_references(symbols, year_anchor)
    symbols_needing_history = [symbol for symbol in symbols if symbol not in year_references]
    refreshing = start_history_warmup(symbols_needing_history, as_of)
    total = len(items)
    coverage_pct = round(available / total * 100.0, 1) if total else 0.0
    stale = bool(snapshot.get("snapshot_frozen") and not snapshot.get("market_closed"))
    reference_fetch_times = [
        cached[3] for cached in references.values() if len(cached) > 3 and cached[3]
    ]
    newest_history_fetch = max(reference_fetch_times) if reference_fetch_times else None
    now_epoch = int(datetime.now().timestamp())
    return {
        "schema_version": 2,
        "as_of": as_of_text,
        "range": range_key,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "price_basis": price_basis,
        "formula": CHANGE_FORMULA,
        "target_reference_date": target_date.isoformat(),
        "methodology": {
            "range_definition": "CALENDAR_DAYS",
            "range_days": SUPPORTED_RANGES[range_key],
            "reference_selection": "LATEST_TRADING_DATE_NOT_AFTER_TARGET",
            "max_reference_lag_days": MAX_REFERENCE_LAG_DAYS,
            "price_basis": price_basis,
            "price_basis_label": (
                "Giá khớp so với giá tham chiếu cùng phiên"
                if range_key == "1D" else
                "Giá đóng cửa điều chỉnh theo dữ liệu nguồn; không gắn nhãn total return"
            ),
            "formula": CHANGE_FORMULA,
            "no_synthetic_data": True,
            "current_source": current_source,
            "current_observed_at": current_observed_at,
            "history_source_priority": ["Vietcap", "KBS"],
        },
        "history_cache": {
            "price_basis": HISTORY_PRICE_BASIS,
            "newest_fetched_at": _iso_from_epoch(newest_history_fetch),
            "age_seconds": max(0, now_epoch - newest_history_fetch) if newest_history_fetch else None,
            "status": "WARMING" if refreshing else ("READY" if newest_history_fetch else "EMPTY"),
            "stale": False,
            "legacy_unverified_rows_ignored": True,
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
