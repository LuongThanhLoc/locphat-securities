"""Backfill, audit and background refresh commands for the LP-RRG store."""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable

from rrg_data_gateway import get_verified_history, init_rrg_store
from rrg_data_store import get_rrg_store

LOGGER = logging.getLogger("rrg.sync")
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()


def rrg_universe(force_index_refresh: bool = False) -> list[str]:
    from rrg_engine import BENCHMARK_SYMBOLS, SMC_TOP_FALLBACK
    from rrg_index_membership import get_index_membership
    from sector_mapping import SECTOR_DEFINITIONS
    vn30_symbols, _ = get_index_membership("VN30", force_refresh=force_index_refresh)
    symbols = set(SMC_TOP_FALLBACK) | set(vn30_symbols) | set(BENCHMARK_SYMBOLS.values())
    for definition in SECTOR_DEFINITIONS.values():
        symbols.update(str(value).upper() for value in definition.get("symbols", []) if value)
    return sorted(symbols)


def sync_universe(symbols: Iterable[str] | None = None) -> Dict[str, Any]:
    init_rrg_store()
    store = get_rrg_store(required=True)
    universe = list(dict.fromkeys(symbols or rrg_universe(force_index_refresh=True)))
    end = datetime.now().date().isoformat()
    # Five calendar years support point-in-time replay/backtests; the online
    # engine still requires only 252 aligned sessions.
    start = (datetime.now().date() - timedelta(days=5 * 366)).isoformat()
    results: Dict[str, str] = {}
    with store.sync_lock() as acquired:
        if not acquired:
            return {"status": "already_running", "total": len(universe), "results": {}}
        action_status = sync_reference_data(store, start, end)
        benchmark_symbols = [symbol for symbol in ("VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOM") if symbol in universe]
        for benchmark in benchmark_symbols:
            try:
                result = get_verified_history(
                    benchmark, start, end, store=store, require_store=True,
                )
                results[benchmark] = f"{result.quality_status}:{len(result.frame)}"
            except Exception as exc:
                results[benchmark] = f"error:{str(exc)[:180]}"
                LOGGER.error("rrg_sync_error symbol=%s error=%s", benchmark, exc)
        calendars = {}
        for benchmark in ("VNINDEX", "HNXINDEX", "UPCOM"):
            frame = store.load_history(benchmark, start, end)
            calendars[benchmark] = frame["date"].astype(str).tolist() if not frame.empty else None

        equity_universe = [symbol for symbol in universe if symbol not in benchmark_symbols]

        def sync_symbol(symbol: str):
            identity = store.security_identity(symbol, end)
            calendar_key = {"HNX": "HNXINDEX", "UPCOM": "UPCOM"}.get(
                str(identity.get("exchange") or "").upper(), "VNINDEX"
            )
            return get_verified_history(
                symbol, start, end, trading_calendar=calendars.get(calendar_key),
                store=store, require_store=True,
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(sync_symbol, symbol): symbol
                for symbol in equity_universe
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result = future.result()
                    results[symbol] = f"{result.quality_status}:{len(result.frame)}"
                except Exception as exc:
                    results[symbol] = f"error:{str(exc)[:180]}"
                    LOGGER.error("rrg_sync_error symbol=%s error=%s", symbol, exc)
    failures = sorted(symbol for symbol, status in results.items() if status.startswith("error:"))
    for exchange, benchmark in (("HOSE", "VNINDEX"), ("HNX", "HNXINDEX"), ("UPCOM", "UPCOM")):
        try:
            frame = store.load_history(benchmark, start, end)
            if not frame.empty:
                store.upsert_trading_sessions(exchange, frame["date"].astype(str).tolist(), f"{benchmark} canonical")
        except Exception as exc:
            LOGGER.error("rrg_calendar_sync_error exchange=%s error=%s", exchange, exc)
    score_snapshots = []
    if not failures:
        from rrg_engine import build_market_score_snapshot
        for benchmark in ("VNINDEX", "VN30", "HNXINDEX"):
            for period in (10, 14, 20):
                try:
                    score_snapshots.append(build_market_score_snapshot(universe, benchmark, period))
                except Exception as exc:
                    failures.append(f"SCORE:{benchmark}:{period}")
                    LOGGER.error("rrg_score_snapshot_error benchmark=%s period=%s error=%s", benchmark, period, exc)
    return {
        "status": "complete" if not failures else "incomplete",
        "total": len(universe),
        "synced": len(universe) - len(failures),
        "failed": failures,
        "results": results,
        "reference_data": action_status,
        "score_snapshots": score_snapshots,
    }


def sync_reference_data(store: Any, start: str, end: str) -> Dict[str, Any]:
    """Sync listed identity, benchmark calendars and price-affecting actions."""
    status: Dict[str, Any] = {"security_master": "error", "corporate_actions": "error"}
    try:
        from corporate_calendar_engine import _listed_universe
        symbols, exchanges = _listed_universe()
        store.upsert_security_master([
            {"symbol": symbol, "exchange": exchanges[symbol], "effective_from": start,
             "trading_status": "active", "source": "VCI listed universe"}
            for symbol in symbols
        ])
        status["security_master"] = f"ok:{len(symbols)}"
    except Exception as exc:
        status["security_master"] = f"error:{str(exc)[:160]}"

    try:
        from corporate_calendar_engine import _fetch_global_actions
        events, meta = _fetch_global_actions(
            datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
        )
        grouped: Dict[str, list[Dict[str, Any]]] = {}
        for event in events:
            if event.get("date_role_code") != "ex_right":
                continue
            details = event.get("details") or {}
            event_type = str(event.get("type") or "")
            if event_type == "capital_action":
                event_type = "rights_issue"
            grouped.setdefault(str(event.get("symbol") or "").upper(), []).append({
                "event_id": event.get("canonical_event_id") or event.get("id"),
                "event_type": event_type,
                "ex_date": event.get("event_date"),
                "cash_per_share": details.get("cash_per_share"),
                "share_ratio": details.get("exercise_ratio"),
                "subscription_price": details.get("issue_price"),
                "verification_status": "confirmed",
                "source": event.get("source") or "Vietcap public REST",
                "payload": event,
            })
        for symbol, rows in grouped.items():
            store.upsert_corporate_actions(symbol, rows)
        from rrg_data_gateway import invalidate_rrg_cache
        invalidate_rrg_cache(grouped)
        from rrg_engine import invalidate_engine_cache
        invalidate_engine_cache(list(grouped))
        status["corporate_actions"] = f"ok:{sum(map(len, grouped.values()))}"
        status["corporate_action_meta"] = meta
    except Exception as exc:
        status["corporate_actions"] = f"error:{str(exc)[:160]}"
    return status


def audit_universe(symbols: Iterable[str] | None = None) -> Dict[str, Any]:
    store = get_rrg_store(required=True)
    universe = list(dict.fromkeys(symbols or rrg_universe()))
    valid, insufficient, unavailable, stale, inactive, adjustment_pending = [], [], [], [], [], []
    benchmark_latest: Dict[str, Any] = {}
    for benchmark in ("VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOM"):
        state = store.state(benchmark)
        if state.get("last_session"):
            benchmark_latest[benchmark] = state.get("last_session")
    for symbol in universe:
        state = store.state(symbol)
        count = int(state.get("session_count") or 0)
        quality = state.get("quality_status")
        if quality == "adjustment_pending":
            adjustment_pending.append(symbol)
        if quality == "inactive":
            inactive.append(symbol)
        elif count >= 252 and quality == "valid":
            identity = store.security_identity(symbol)
            calendar = {"HNX": "HNXINDEX", "UPCOM": "UPCOM"}.get(
                str(identity.get("exchange") or "").upper(), "VNINDEX"
            )
            age = store.session_age(calendar, str(state.get("last_session")), datetime.now().date().isoformat())
            if age is not None and age > 3:
                stale.append({"symbol": symbol, "freshness_sessions": age})
                unavailable.append(symbol)
            else:
                valid.append(symbol)
        elif 0 < count < 252:
            insufficient.append({"symbol": symbol, "sessions": count})
        elif quality != "inactive":
            unavailable.append(symbol)
    eligible = len(valid) + len(unavailable)
    completeness = round(len(valid) / eligible * 100.0, 2) if eligible else 100.0
    store_health = store.health()
    score_snapshots_ready = int(store_health.get("market_score_snapshots") or 0) >= 9
    from rrg_engine import SMC_TOP_FALLBACK
    from rrg_index_membership import get_index_membership
    from sector_mapping import SECTOR_DEFINITIONS
    vn30_symbols, vn30_meta = get_index_membership("VN30")
    group_coverage = {
        "SMC_TOP": store.coverage(SMC_TOP_FALLBACK),
        "VN30": {**store.coverage(vn30_symbols), "membership": vn30_meta},
    }
    for key, definition in SECTOR_DEFINITIONS.items():
        group_coverage[key] = store.coverage(list(definition.get("symbols") or []))
    groups_complete = all(
        float(coverage.get("completeness_pct") or 0) == 100.0
        for coverage in group_coverage.values()
    )
    return {
        "coverage_status": "complete" if not unavailable else "incomplete",
        "completeness_pct": completeness,
        "valid_symbols": len(valid),
        "eligible_symbols": eligible,
        "insufficient_history": insufficient,
        "unavailable": unavailable,
        "stale": stale,
        "inactive": inactive,
        "adjustment_pending": adjustment_pending,
        "benchmark_latest": benchmark_latest,
        "market_score_snapshots": store_health.get("market_score_snapshots", 0),
        "coverage_by_group": group_coverage,
        "audit_passed": not unavailable and not adjustment_pending and score_snapshots_ready and groups_complete,
    }


def _worker_loop() -> None:
    # Cold starts/deploys always verify gaps. PostgreSQL ensures this is an
    # incremental operation after the first backfill.
    last_run_date = None
    while True:
        now = datetime.now()
        should_run = last_run_date is None or (
            now.weekday() < 5 and (now.hour, now.minute) >= (15, 20) and last_run_date != now.date()
        )
        if should_run:
            try:
                payload = sync_universe()
                if payload.get("status") in {"complete", "already_running"}:
                    last_run_date = now.date()
            except Exception as exc:
                LOGGER.error("rrg_background_sync_error error=%s", exc)
        time.sleep(15 * 60)


def start_background_sync() -> bool:
    global _WORKER
    # Production should run `python rrg_sync.py backfill` from a dedicated cron
    # or worker.  In-process scheduling is opt-in for local/single-instance use.
    enabled = os.getenv("RRG_BACKGROUND_SYNC", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return True
        _WORKER = threading.Thread(target=_worker_loop, name="rrg-data-sync", daemon=True)
        _WORKER.start()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LP-RRG PostgreSQL data maintenance")
    parser.add_argument("command", choices=("backfill", "audit"))
    parser.add_argument("symbols", nargs="*")
    args = parser.parse_args()
    payload = sync_universe(args.symbols or None) if args.command == "backfill" else audit_universe(args.symbols or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
