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


def rrg_universe() -> list[str]:
    from rrg_engine import BENCHMARK_SYMBOLS, SMC_TOP_FALLBACK
    from sector_mapping import SECTOR_DEFINITIONS
    symbols = set(SMC_TOP_FALLBACK) | set(BENCHMARK_SYMBOLS.values())
    for definition in SECTOR_DEFINITIONS.values():
        symbols.update(str(value).upper() for value in definition.get("symbols", []) if value)
    return sorted(symbols)


def sync_universe(symbols: Iterable[str] | None = None) -> Dict[str, Any]:
    init_rrg_store()
    store = get_rrg_store(required=True)
    universe = list(dict.fromkeys(symbols or rrg_universe()))
    end = datetime.now().date().isoformat()
    start = (datetime.now().date() - timedelta(days=620)).isoformat()
    results: Dict[str, str] = {}
    with store.sync_lock() as acquired:
        if not acquired:
            return {"status": "already_running", "total": len(universe), "results": {}}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    get_verified_history, symbol, start, end,
                    store=store, require_store=True,
                ): symbol
                for symbol in universe
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
    return {
        "status": "complete" if not failures else "incomplete",
        "total": len(universe),
        "synced": len(universe) - len(failures),
        "failed": failures,
        "results": results,
    }


def audit_universe(symbols: Iterable[str] | None = None) -> Dict[str, Any]:
    store = get_rrg_store(required=True)
    universe = list(dict.fromkeys(symbols or rrg_universe()))
    valid, insufficient, unavailable = [], [], []
    for symbol in universe:
        state = store.state(symbol)
        count = int(state.get("session_count") or 0)
        if count >= 252 and state.get("quality_status") == "valid":
            valid.append(symbol)
        elif 0 < count < 252:
            insufficient.append({"symbol": symbol, "sessions": count})
        else:
            unavailable.append(symbol)
    eligible = len(valid) + len(unavailable)
    completeness = round(len(valid) / eligible * 100.0, 2) if eligible else 100.0
    return {
        "coverage_status": "complete" if not unavailable else "incomplete",
        "completeness_pct": completeness,
        "valid_symbols": len(valid),
        "eligible_symbols": eligible,
        "insufficient_history": insufficient,
        "unavailable": unavailable,
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
    enabled = os.getenv("RRG_BACKGROUND_SYNC", "true").strip().lower() in {"1", "true", "yes", "on"}
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
