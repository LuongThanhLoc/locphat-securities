"""Watchlist Quote Service — Batch quote provider for user personal watchlist.

Responsible for:
- Normalizing ticker symbols
- Checking current Vietnam market session phase via `get_market_session()`
- Selecting the proper data source per trading session phase:
  - MORNING / LUNCH_BREAK / AFTERNOON / ATC / POST_CLOSE_TRADING: DNSE REST trade + secdef
  - CLOSED (15:00 - 15:10): DNSE REST trade (POST_CLOSE_PENDING)
  - CLOSED (>= 15:10) / PRE_OPEN / WEEKEND / HOLIDAY: Frozen Heatmap snapshot (sectors[*].stocks), with DNSE fallback for missing tickers
- Formatting standardized JSON items with clear price labels, sources, and data quality indicators.
- NO DeepSeek or LLM calls.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dnse_realtime import (
    _normalize_vnd_price,
    _rest_latest_trade,
    _rest_security_definition,
    _settings,
)
from heatmap_engine import (
    build_snapshot_symbol_index,
    ensure_latest_frozen_snapshot,
    get_latest_snapshot,
    get_market_session,
    get_vn_now,
    validate_frozen_snapshot,
)


def normalize_symbols(symbols: List[str]) -> List[str]:
    """Normalize symbol list: uppercase, trim, regex validate, deduplicate, max 100."""
    seen = set()
    cleaned = []
    for item in symbols:
        if not item:
            continue
        sym = str(item).upper().strip()
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", sym) and sym not in seen:
            seen.add(sym)
            cleaned.append(sym)
            if len(cleaned) >= 100:
                break
    return cleaned


def quote_from_heatmap_stock(
    stock: Dict[str, Any],
    snapshot: Dict[str, Any],
    session: Dict[str, Any],
) -> Dict[str, Any]:
    """Format quote item from a Heatmap snapshot stock entry."""
    symbol = str(stock.get("symbol") or "").upper().strip()
    match_price = float(stock.get("match_price") or stock.get("price_vnd") or 0)
    ref_price = float(stock.get("ref_price") or stock.get("reference_price") or 0)
    ceiling = float(stock.get("ceiling") or stock.get("ceiling_price") or 0)
    floor = float(stock.get("floor") or stock.get("floor_price") or 0)

    price_vnd = match_price if match_price > 0 else None
    ref_vnd = ref_price if ref_price > 0 else None
    ceiling_vnd = ceiling if ceiling > 0 else None
    floor_vnd = floor if floor > 0 else None

    change_vnd = None
    change_pct = None

    if price_vnd is not None and ref_vnd is not None and ref_vnd > 0:
        change_vnd = round(price_vnd - ref_vnd, 2)
        change_pct = round((change_vnd / ref_vnd) * 100.0, 2)
    elif stock.get("change_pct") is not None:
        try:
            change_pct = round(float(stock["change_pct"]), 2)
        except (TypeError, ValueError):
            pass

    lineage = snapshot.get("data_lineage") or {}
    trade_date = lineage.get("latest_trading_date") or stock.get("trading_date") or snapshot.get("trading_date")

    phase = session.get("phase", "CLOSED")
    if phase in ("PRE_OPEN", "WEEKEND", "HOLIDAY"):
        label = f"Giá phiên gần nhất ({trade_date or 'đã đóng cửa'})"
    else:
        label = f"Giá cuối phiên ({trade_date or 'đã chốt'})"

    quality_status = (snapshot.get("data_quality") or {}).get("status", "VERIFIED")

    return {
        "symbol": symbol,
        "company_name": stock.get("name") or stock.get("company_name") or "",
        "exchange": stock.get("exchange") or "HOSE",
        "price_vnd": price_vnd,
        "reference_price_vnd": ref_vnd,
        "ceiling_price_vnd": ceiling_vnd,
        "floor_price_vnd": floor_vnd,
        "change_vnd": change_vnd,
        "change_pct": change_pct,
        "price_type": "close_snapshot",
        "price_label": label,
        "price_source": "Snapshot bảng giá / Vietcap ICB",
        "trading_date": trade_date,
        "exchange_time": stock.get("received_time") or stock.get("exchange_time"),
        "fetched_at": lineage.get("fetched_at") or get_vn_now().isoformat(),
        "updated_at": get_vn_now().isoformat(),
        "data_quality": quality_status,
        "stale": False,
    }


def quote_from_dnse_payload(
    symbol: str,
    trade_payload: Optional[Dict[str, Any]],
    secdef_payload: Optional[Dict[str, Any]],
    session: Dict[str, Any],
    snapshot_stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Format quote item from DNSE REST trade + secdef payloads, with snapshot fallback."""
    trade = trade_payload or {}
    secdef = secdef_payload or {}
    snap = snapshot_stock or {}

    match_price = trade.get("price_vnd")
    if match_price is None and "matchPrice" in trade:
        match_price = _normalize_vnd_price(trade.get("matchPrice"))

    ref_price = _normalize_vnd_price(
        secdef.get("basicPrice") or secdef.get("referencePrice") or trade.get("basicPrice")
    )
    if ref_price is None or ref_price <= 0:
        try:
            ref_price = float(snap.get("ref_price") or snap.get("reference_price") or 0) or None
        except (TypeError, ValueError):
            ref_price = None

    ceiling = _normalize_vnd_price(secdef.get("ceilingPrice") or trade.get("ceilingPrice"))
    if ceiling is None or ceiling <= 0:
        try:
            ceiling = float(snap.get("ceiling") or snap.get("ceiling_price") or 0) or None
        except (TypeError, ValueError):
            ceiling = None

    floor = _normalize_vnd_price(secdef.get("floorPrice") or trade.get("floorPrice"))
    if floor is None or floor <= 0:
        try:
            floor = float(snap.get("floor") or snap.get("floor_price") or 0) or None
        except (TypeError, ValueError):
            floor = None

    change_vnd = None
    change_pct = None

    if match_price is not None and ref_price is not None and ref_price > 0:
        change_vnd = round(match_price - ref_price, 2)
        change_pct = round((change_vnd / ref_price) * 100.0, 2)

    phase = session.get("phase", "MORNING")
    if session.get("is_finalization_pending"):
        price_type = "post_close_pending"
        label = "Đang hoàn tất dữ liệu cuối phiên"
    elif phase in ("MORNING", "AFTERNOON", "ATC"):
        price_type = "realtime"
        label = "Giá khớp gần nhất"
    elif phase == "LUNCH_BREAK":
        price_type = "lunch_break"
        label = "Giá chốt phiên sáng"
    elif phase == "POST_CLOSE_TRADING":
        price_type = "post_close"
        label = "Dữ liệu sau ATC"
    else:
        price_type = "latest_trade"
        label = "Giá giao dịch mới nhất"

    exchange_time = trade.get("exchange_time") or trade.get("time")

    company_name = snap.get("name") or snap.get("company_name") or ""
    exchange_name = snap.get("exchange") or trade.get("boardId") or "HOSE"

    return {
        "symbol": symbol,
        "company_name": company_name,
        "exchange": exchange_name,
        "price_vnd": match_price,
        "reference_price_vnd": ref_price,
        "ceiling_price_vnd": ceiling,
        "floor_price_vnd": floor,
        "change_vnd": change_vnd,
        "change_pct": change_pct,
        "price_type": price_type,
        "price_label": label,
        "price_source": "DNSE REST latest trade",
        "trading_date": session.get("calendar_date"),
        "exchange_time": exchange_time,
        "fetched_at": get_vn_now().isoformat(),
        "updated_at": get_vn_now().isoformat(),
        "data_quality": "VERIFIED" if match_price is not None else "DEGRADED",
        "stale": False,
    }


async def get_watchlist_quotes(symbols: List[str]) -> Dict[str, Any]:
    """Batch quote service method for user personal watchlist."""
    clean_symbols = normalize_symbols(symbols)
    if not clean_symbols:
        return {
            "market_session": get_market_session(),
            "quote_mode": "EMPTY",
            "snapshot_date": None,
            "snapshot_frozen": False,
            "items": {},
            "errors": {},
        }

    session = get_market_session()
    phase = session.get("phase")
    is_closed = session.get("is_closed")
    is_final_snapshot = session.get("is_final_snapshot_time")

    use_snapshot = (
        is_final_snapshot or
        phase in ("PRE_OPEN", "WEEKEND", "HOLIDAY")
    )

    items: Dict[str, Any] = {}
    errors: Dict[str, Any] = {}
    quote_mode = "DNSE_REALTIME"
    snapshot_date = None
    snapshot_frozen = False

    if use_snapshot:
        snapshot = ensure_latest_frozen_snapshot() or get_latest_snapshot()
        val = validate_frozen_snapshot(snapshot) if snapshot else {"valid": False}
        if val.get("valid") and snapshot:
            quote_mode = "HEATMAP_CLOSE_SNAPSHOT"
            snapshot_date = val.get("trade_date")
            snapshot_frozen = True
            symbol_index = build_snapshot_symbol_index(snapshot)
            missing_symbols = []

            for sym in clean_symbols:
                stock_entry = symbol_index.get(sym)
                if stock_entry:
                    items[sym] = quote_from_heatmap_stock(stock_entry, snapshot, session)
                else:
                    missing_symbols.append(sym)

            # Fallback ONLY missing symbols to DNSE
            if missing_symbols:
                try:
                    dnse_settings = _settings()
                    for sym in missing_symbols:
                        try:
                            trade = _rest_latest_trade(dnse_settings, sym)
                            secdef = _rest_security_definition(dnse_settings, sym)
                            if trade and "error" not in trade:
                                q = quote_from_dnse_payload(sym, trade, secdef, session)
                                q["price_source"] = "DNSE REST fallback (missing from snapshot)"
                                items[sym] = q
                            else:
                                errors[sym] = "Không tìm thấy giá từ snapshot hoặc DNSE"
                        except Exception as exc:
                            errors[sym] = str(exc)
                except Exception as exc:
                    for sym in missing_symbols:
                        errors[sym] = f"DNSE fallback config error: {exc}"
            return {
                "market_session": session,
                "quote_mode": quote_mode,
                "snapshot_date": snapshot_date,
                "snapshot_frozen": snapshot_frozen,
                "items": items,
                "errors": errors,
            }

    # In-session or 15:00-15:10 pending finalization: fetch DNSE REST for all clean symbols
    if session.get("is_finalization_pending"):
        quote_mode = "POST_CLOSE_PENDING"
    else:
        quote_mode = "DNSE_REALTIME_TRADE"

    snapshot = ensure_latest_frozen_snapshot() or get_latest_snapshot()
    if not snapshot:
        try:
            from heatmap_engine import fetch_market_heatmap_data
            snapshot = fetch_market_heatmap_data()
        except Exception:
            snapshot = None
    symbol_index = build_snapshot_symbol_index(snapshot) if snapshot else {}

    try:
        dnse_settings = _settings()
        for sym in clean_symbols:
            snap_stock = symbol_index.get(sym)
            try:
                trade = _rest_latest_trade(dnse_settings, sym)
                secdef = _rest_security_definition(dnse_settings, sym)
                if trade and "error" not in trade:
                    items[sym] = quote_from_dnse_payload(sym, trade, secdef, session, snap_stock)
                elif snap_stock:
                    q = quote_from_heatmap_stock(snap_stock, snapshot, session)
                    q["price_source"] = "Snapshot bảng giá (DNSE REST offline)"
                    items[sym] = q
                else:
                    errors[sym] = "Không tải được giá từ DNSE hoặc snapshot"
            except Exception:
                if snap_stock:
                    q = quote_from_heatmap_stock(snap_stock, snapshot, session)
                    items[sym] = q
                else:
                    errors[sym] = "Không tải được giá từ DNSE hoặc snapshot"
    except Exception:
        for sym in clean_symbols:
            snap_stock = symbol_index.get(sym)
            if snap_stock:
                q = quote_from_heatmap_stock(snap_stock, snapshot, session)
                items[sym] = q
            else:
                errors[sym] = "Không cấu hình được kết nối giá"

    return {
        "market_session": session,
        "quote_mode": quote_mode,
        "snapshot_date": None,
        "snapshot_frozen": False,
        "items": items,
        "errors": errors,
    }
