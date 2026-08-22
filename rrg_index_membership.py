"""Verified, versioned index constituent gateway for LP-RRG.

Index membership is reference data, not application configuration.  This
module fetches it from independent vnstock providers, requires agreement, and
falls back only to the latest verified PostgreSQL snapshot (never a hard-coded
constituent list).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Dict, Iterable, Optional, Tuple

from rrg_data_store import get_rrg_store

INDEX_MEMBERSHIP_TTL_SECONDS = 60 * 60
INDEX_MEMBERSHIP_RULE_VERSION = "index-membership-dual-source-v1"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}$")
_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


class IndexMembershipUnavailable(RuntimeError):
    pass


def normalize_index_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if hasattr(raw, "columns"):
        columns = {str(column).lower(): column for column in raw.columns}
        column = columns.get("symbol") or columns.get("ticker") or columns.get("code")
        values: Iterable[Any] = raw[column].tolist() if column is not None else []
    elif hasattr(raw, "tolist"):
        values = raw.tolist()
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = []
    symbols = {
        str(value).upper().strip() for value in values
        if value is not None and _SYMBOL_RE.fullmatch(str(value).upper().strip())
    }
    symbols.discard("NAN")
    return sorted(symbols)


def _fetch_source(index_code: str, source: str) -> list[str]:
    from vnstock import Listing

    symbols = normalize_index_symbols(
        Listing(source=source, show_log=False).symbols_by_group(index_code)
    )
    expected = 30 if index_code == "VN30" else None
    if expected is not None and len(symbols) != expected:
        raise ValueError(f"{index_code} từ {source} trả về {len(symbols)}/{expected} mã")
    if not symbols:
        raise ValueError(f"{index_code} từ {source} trả về rỗng")
    return symbols


def get_index_membership(
    index_code: str,
    *,
    force_refresh: bool = False,
    store: Optional[Any] = None,
) -> Tuple[list[str], Dict[str, Any]]:
    code = index_code.upper().strip()
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(code)
        if cached and not force_refresh and now - float(cached["fetched_at_epoch"]) < INDEX_MEMBERSHIP_TTL_SECONDS:
            return list(cached["symbols"]), dict(cached["meta"])

        durable_store = store if store is not None else get_rrg_store(required=False)
        results: Dict[str, list[str]] = {}
        errors: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(_fetch_source, code, source): source for source in ("KBS", "VCI")}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    results[source] = future.result()
                except Exception as exc:
                    errors[source] = str(exc)[:500]

        if len(results) == 2 and results["KBS"] == results["VCI"]:
            symbols = results["KBS"]
            fingerprint = hashlib.sha256(
                json.dumps(symbols, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            snapshot_id = f"{code.lower()}-{fingerprint[:20]}"
            meta = {
                "snapshot_id": snapshot_id,
                "index_code": code,
                "as_of_date": date.today().isoformat(),
                "source": "vnstock/KBS+VCI",
                "source_chain": ["vnstock/KBS", "vnstock/VCI"],
                "source_agreement": True,
                "stale": False,
                "fingerprint": fingerprint,
                "rule_version": INDEX_MEMBERSHIP_RULE_VERSION,
                "fetched_at": int(now),
            }
            if durable_store is not None:
                durable_store.save_index_membership_snapshot(code, symbols, meta)
            _CACHE[code] = {"symbols": symbols, "meta": meta, "fetched_at_epoch": now}
            return list(symbols), dict(meta)

        mismatch = len(results) == 2 and results["KBS"] != results["VCI"]
        if durable_store is not None:
            snapshot = durable_store.load_index_membership_snapshot(code)
            if snapshot:
                meta = dict(snapshot["meta"])
                meta.update({
                    "stale": True,
                    "refresh_error": "source_mismatch" if mismatch else "source_unavailable",
                    "provider_errors": errors,
                    "error": "KBS và VCI không khớp" if mismatch else "; ".join(errors.values()),
                })
                symbols = list(snapshot["symbols"])
                _CACHE[code] = {"symbols": symbols, "meta": meta, "fetched_at_epoch": now}
                return symbols, meta

        if cached:
            meta = dict(cached["meta"])
            meta.update({
                "stale": True,
                "refresh_error": "source_mismatch" if mismatch else "source_unavailable",
                "provider_errors": errors,
                "error": "KBS và VCI không khớp" if mismatch else "; ".join(errors.values()),
            })
            return list(cached["symbols"]), meta

        # Single-source fallback when one provider is temporarily down and no store snapshot exists
        if len(results) == 1 and not mismatch:
            single_source, symbols = next(iter(results.items()))
            fingerprint = hashlib.sha256(
                json.dumps(symbols, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            snapshot_id = f"{code.lower()}-{fingerprint[:20]}"
            meta = {
                "snapshot_id": snapshot_id,
                "index_code": code,
                "as_of_date": date.today().isoformat(),
                "source": f"vnstock/{single_source}",
                "source_chain": [f"vnstock/{single_source}"],
                "source_agreement": False,
                "stale": False,
                "provider_errors": errors,
                "fingerprint": fingerprint,
                "rule_version": INDEX_MEMBERSHIP_RULE_VERSION,
                "fetched_at": int(now),
            }
            if durable_store is not None:
                durable_store.save_index_membership_snapshot(code, symbols, meta)
            _CACHE[code] = {"symbols": symbols, "meta": meta, "fetched_at_epoch": now}
            return list(symbols), dict(meta)

        detail = "KBS và VCI trả danh sách khác nhau" if mismatch else "; ".join(
            f"{source}: {error}" for source, error in sorted(errors.items())
        )
        raise IndexMembershipUnavailable(f"Không xác minh được thành phần {code}: {detail}")


def invalidate_index_membership_cache(index_code: Optional[str] = None) -> None:
    with _LOCK:
        if index_code:
            _CACHE.pop(index_code.upper(), None)
        else:
            _CACHE.clear()
