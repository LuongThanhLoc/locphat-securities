import os
import re
import time
import threading
import json
import hashlib
import math
import sqlite3
import statistics
import urllib.request
import requests
import logging
from datetime import datetime, timedelta, time as dtime
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

from sector_mapping import get_sector_info, get_sector_memberships, SECTOR_DEFINITIONS

HEATMAP_SCHEMA_VERSION = 9
HEATMAP_MODEL_VERSION = "lp-market-radar-4.0"

# Weekly reporting and quant baselining intentionally use different windows:
# five sessions for the weekly read, twenty close snapshots for robust history.
WEEKLY_ANALYSIS_DAYS = 5
SNAPSHOT_RETENTION_DAYS = 20

# ---------- SQLite Snapshot Storage (Tasks 2 & 3) ----------
_SNAPSHOT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heatmap_snapshots.db")
_SNAPSHOT_DB_INITIALIZED = False

def _get_snapshot_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_SNAPSHOT_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_snapshot() -> None:
    """Create the close-of-session snapshot store AND the intraday timeline store."""
    global _SNAPSHOT_DB_INITIALIZED
    if _SNAPSHOT_DB_INITIALIZED:
        return
    try:
        with _get_snapshot_db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS heatmap_snapshots (
                    trade_date       TEXT PRIMARY KEY,  -- format: YYYY-MM-DD
                    snapshot_json    TEXT NOT NULL,
                    created_at       INTEGER NOT NULL,
                    is_frozen_15h10  INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Intraday timeline snapshots — one row per captured checkpoint during a session.
            # `snapshot_time` is ISO-8601 with +07:00 offset (Asia/Ho_Chi_Minh).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS heatmap_intraday_snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_time   TEXT NOT NULL,
                    session_phase   TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    created_at      INTEGER NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_intraday_time
                ON heatmap_intraday_snapshots(snapshot_time)
            """)
            conn.commit()
        _SNAPSHOT_DB_INITIALIZED = True
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: init snapshot DB failed: {db_err}")


# ---------- Intraday Timeline Storage (Task 1) ----------
# These power the bottom-of-page scrubber on /heatmap. A daemon poller
# (IntradaySnapshotPoller) writes a fresh row every 1m (ATO/ATC) or 5m
# (continuous matching) during a live trading day; the front-end reads the
# table via /api/heatmap/timeline and renders the slider.
INTRADAY_MAX_PER_DAY = 80  # cap so a busy session can't blow up the SQLite row count

def save_intraday_snapshot(snapshot_time: str, session_phase: str, payload: Dict[str, Any]) -> None:
    """Persist one intraday timeline checkpoint.

    `payload` follows the same shape as a regular heatmap snapshot
    (sectors + quant_snapshot + summary + data_lineage). Schema version
    bump from 6 → 7 introduced the timeline feature; readers gate on
    schema_version >= 7.
    """
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            conn.execute(
                """
                INSERT INTO heatmap_intraday_snapshots (snapshot_time, session_phase, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot_time,
                    session_phase,
                    json.dumps(payload, ensure_ascii=False),
                    int(time.time()),
                ),
            )
            conn.commit()
        _trim_intraday_for_date(snapshot_time[:10])
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: save intraday snapshot failed: {db_err}")


def _trim_intraday_for_date(trade_date: str) -> None:
    """Keep at most INTRADAY_MAX_PER_DAY rows for the given trading date.

    Invariant: the row closest to ATC close (14:45 UTC+7) and the row
    closest to 15:10 (frozen) must be retained so the scrubber always
    has anchor points when opened outside trading hours.
    """
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            # Anchor times within a session — keep these even when over cap.
            anchor_prefixes = (f"{trade_date}T14:4", f"{trade_date}T14:45", f"{trade_date}T15:10")
            anchor_clause = " OR ".join(["snapshot_time LIKE ?"] * len(anchor_prefixes))
            anchor_params: tuple = tuple(anchor_prefixes)

            total = conn.execute(
                "SELECT COUNT(*) AS n FROM heatmap_intraday_snapshots WHERE snapshot_time LIKE ?",
                (f"{trade_date}%",),
            ).fetchone()["n"]
            if total <= INTRADAY_MAX_PER_DAY:
                return

            # Find IDs of the rows we want to protect (anchors).
            anchor_rows = conn.execute(
                f"""
                SELECT id FROM heatmap_intraday_snapshots
                WHERE snapshot_time LIKE ?
                  AND ({anchor_clause})
                """,
                (f"{trade_date}%",) + anchor_params,
            ).fetchall()
            anchor_ids = {row["id"] for row in anchor_rows}

            # Pick IDs to delete: every row that is NOT an anchor, ordered
            # oldest-first, until total - INTRADAY_MAX_PER_DAY rows are removed.
            excess = total - INTRADAY_MAX_PER_DAY
            if excess <= 0:
                return
            non_anchor_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM heatmap_intraday_snapshots
                    WHERE snapshot_time LIKE ?
                    ORDER BY snapshot_time ASC
                    """,
                    (f"{trade_date}%",),
                ).fetchall()
                if row["id"] not in anchor_ids
            ]
            if not non_anchor_ids:
                return
            victims = non_anchor_ids[:excess]
            placeholders = ",".join(["?"] * len(victims))
            conn.execute(
                f"DELETE FROM heatmap_intraday_snapshots WHERE id IN ({placeholders})",
                victims,
            )
            conn.commit()
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: trim intraday failed: {db_err}")


def purge_intraday_before(trade_date: str) -> int:
    """Delete every intraday checkpoint whose date is strictly before `trade_date`.

    Retention policy: the scrubber is only meant to replay the current
    trading session. Anything older than `trade_date` (YYYY-MM-DD, VN time)
    is purged so the SQLite row count stays bounded — otherwise a long-running
    process would accumulate several hundred KB per day in `payload_json`.

    The cutoff is the start of `trade_date` at 00:00 UTC+7. Because
    `snapshot_time` is stored as ISO-8601 with the `+07:00` offset, a
    lexicographic comparison on the prefix matches chronological order.

    The function is idempotent: calling it on the same date twice is a no-op.
    Rows belonging to `trade_date` itself are NEVER touched.

    Returns the number of rows deleted (0 if nothing matched).
    """
    init_db_snapshot()
    cutoff_iso = f"{trade_date}T00:00:00+07:00"
    try:
        with _get_snapshot_db_conn() as conn:
            cur = conn.execute(
                """
                DELETE FROM heatmap_intraday_snapshots
                WHERE snapshot_time < ?
                """,
                (cutoff_iso,),
            )
            conn.commit()
            deleted = cur.rowcount or 0
            if deleted:
                print(f"[Heatmap DB] Purged {deleted} intraday rows older than {trade_date}.")
            return deleted
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: purge intraday failed: {db_err}")
        return 0


def get_intraday_snapshots(trade_date: str) -> List[Dict[str, Any]]:
    """Return all intraday checkpoints for `trade_date` (YYYY-MM-DD), oldest first.

    Each entry carries `snapshot_time`, `session_phase`, and `payload`.
    Schema version 7+ is required; older rows are filtered out.
    """
    init_db_snapshot()
    snapshots: List[Dict[str, Any]] = []
    try:
        with _get_snapshot_db_conn() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_time, session_phase, payload_json
                FROM heatmap_intraday_snapshots
                WHERE snapshot_time LIKE ?
                ORDER BY snapshot_time ASC
                """,
                (f"{trade_date}%",),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("schema_version", 0) < 7:
                continue
            payload = _upgrade_snapshot_to_v4(payload)
            snapshots.append({
                "snapshot_time": row["snapshot_time"],
                "session_phase": row["session_phase"],
                "payload": payload,
            })
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read intraday failed: {db_err}")
    return snapshots


def get_latest_intraday_snapshot() -> Optional[Dict[str, Any]]:
    """Return the most recent intraday checkpoint (any trade date)."""
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            row = conn.execute(
                """
                SELECT snapshot_time, session_phase, payload_json
                FROM heatmap_intraday_snapshots
                ORDER BY snapshot_time DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        if payload.get("schema_version", 0) < 7:
            return None
        payload = _upgrade_snapshot_to_v4(payload)
        return {
            "snapshot_time": row["snapshot_time"],
            "session_phase": row["session_phase"],
            "payload": payload,
        }
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read latest intraday failed: {db_err}")
        return None


def get_snapshot_for_date(trade_date: str) -> Optional[Dict[str, Any]]:
    """Read stored snapshot from DB for a given date. Returns dict or None."""
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            row = conn.execute(
                "SELECT snapshot_json, is_frozen_15h10 FROM heatmap_snapshots WHERE trade_date = ?",
                (trade_date,)
            ).fetchone()
            if not row:
                return None
            payload = json.loads(row["snapshot_json"])
            # Chấp nhận schema version >= 5 để đọc được cả snapshot cũ
            if payload.get("schema_version", 0) < 5:
                return None
            payload = _upgrade_snapshot_to_v4(payload)
            payload["snapshot_frozen"] = bool(row["is_frozen_15h10"])
            return payload
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read snapshot failed: {db_err}")
        return None


def get_latest_snapshot() -> Optional[Dict[str, Any]]:
    """Return the newest compatible session snapshot without calling a market API."""
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            row = conn.execute(
                """
                SELECT snapshot_json, is_frozen_15h10
                FROM heatmap_snapshots
                ORDER BY trade_date DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            payload = json.loads(row["snapshot_json"])
            # Chấp nhận schema version >= 5
            if payload.get("schema_version", 0) < 5:
                return None
            payload = _upgrade_snapshot_to_v4(payload)
            payload["snapshot_frozen"] = bool(row["is_frozen_15h10"])
            return payload
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read latest snapshot failed: {db_err}")
        return None


def get_recent_snapshots(days: int = WEEKLY_ANALYSIS_DAYS) -> List[Dict[str, Any]]:
    """Return the most recent N frozen snapshots for historical comparison."""
    init_db_snapshot()
    snapshots = []
    try:
        with _get_snapshot_db_conn() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_json, trade_date, is_frozen_15h10
                FROM heatmap_snapshots
                WHERE is_frozen_15h10 = 1
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (days,)
            ).fetchall()
            for row in rows:
                payload = json.loads(row["snapshot_json"])
                # Chấp nhận schema version >= 5 để đọc được cả snapshot cũ
                if payload.get("schema_version", 0) >= 5:
                    payload = _upgrade_snapshot_to_v4(payload)
                    payload["snapshot_frozen"] = bool(row["is_frozen_15h10"])
                    payload["trade_date"] = row["trade_date"]
                    snapshots.append(payload)
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read recent snapshots failed: {db_err}")
    return snapshots

def save_snapshot_for_date(trade_date: str, payload: Dict[str, Any], frozen: bool = False) -> None:
    """Upsert snapshot to DB. frozen=True means 15:10 close-of-day snapshot (final).
    Safeguard: Never overwrite an existing frozen snapshot with an unfrozen payload.
    """
    init_db_snapshot()
    try:
        store_payload = {k: v for k, v in payload.items() if k not in ("snapshot_frozen", "market_closed")}
        with _get_snapshot_db_conn() as conn:
            upsert_cursor = conn.execute(
                """
                INSERT INTO heatmap_snapshots (trade_date, snapshot_json, created_at, is_frozen_15h10)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    snapshot_json   = CASE WHEN heatmap_snapshots.is_frozen_15h10 = 1 AND excluded.is_frozen_15h10 = 0 THEN heatmap_snapshots.snapshot_json ELSE excluded.snapshot_json END,
                    created_at      = CASE WHEN heatmap_snapshots.is_frozen_15h10 = 1 AND excluded.is_frozen_15h10 = 0 THEN heatmap_snapshots.created_at ELSE excluded.created_at END,
                    is_frozen_15h10 = CASE WHEN excluded.is_frozen_15h10 = 1 THEN 1 ELSE heatmap_snapshots.is_frozen_15h10 END
                """,
                (
                    trade_date,
                    json.dumps(store_payload, ensure_ascii=False),
                    int(time.time()),
                    1 if frozen else 0,
                ),
            )
            # `rowcount` here is the SQLite `ON CONFLICT` semantics:
            #   1 = new row inserted
            #   2 = existing row updated (columns changed)
            #   0 = update was a no-op (e.g. frozen row, frozen-safe CASE branch)
            # When the upsert was a no-op we skip the DELETE — retention hasn't
            # actually changed, so re-running it would just be wasted work and a
            # tiny race window where another writer's pending INSERT could be
            # dropped by our DELETE.
            if upsert_cursor.rowcount > 0:
                # Keep a longer quant baseline while weekly reporting remains a 5-session view.
                conn.execute(
                    """
                    DELETE FROM heatmap_snapshots
                    WHERE trade_date NOT IN (
                        SELECT trade_date FROM heatmap_snapshots
                        ORDER BY trade_date DESC LIMIT ?
                    )
                    """,
                    (SNAPSHOT_RETENTION_DAYS,),
                )
            conn.commit()
        # Ops visibility: log progress toward WEEKLY_ANALYSIS_DAYS so Render
        # logs show whether the weekly report will succeed or hit "not enough data".
        try:
            stats = get_snapshot_stats()
            print(
                f"[Heatmap DB] Snapshot store: {stats['total']} total, "
                f"{stats['frozen']} frozen (weekly target: {WEEKLY_ANALYSIS_DAYS}; "
                f"quant retention: {SNAPSHOT_RETENTION_DAYS})."
            )
        except Exception:
            pass
        print(f"[Heatmap DB] {'[FROZEN 15h10] ' if frozen else ''}Saved snapshot for {trade_date}")
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: save snapshot failed: {db_err}")


def get_snapshot_stats() -> Dict[str, int]:
    """Return counts of total and frozen snapshots in the store.

    Cheap diagnostic for ops dashboards and weekly-readiness checks. Reads
    a single row with `COUNT(...) FILTER (...)`, so it does not scan the table.
    """
    init_db_snapshot()
    try:
        with _get_snapshot_db_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_frozen_15h10 = 1 THEN 1 ELSE 0 END) AS frozen
                FROM heatmap_snapshots
                """
            ).fetchone()
        return {"total": int(row["total"] or 0), "frozen": int(row["frozen"] or 0)}
    except Exception as db_err:
        print(f"[Heatmap DB] Warning: read snapshot stats failed: {db_err}")
        return {"total": 0, "frozen": 0}


def validate_frozen_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate if a snapshot is a valid frozen close-of-day snapshot."""
    if not isinstance(snapshot, dict):
        return {"valid": False, "reason": "Not a dict", "symbol_count": 0}
    # Chấp nhận schema version >= 5
    if snapshot.get("schema_version", 0) < 5:
        return {"valid": False, "reason": "Schema version mismatch", "symbol_count": 0}
    if not snapshot.get("snapshot_frozen"):
        return {"valid": False, "reason": "Not frozen", "symbol_count": 0}
    sectors = snapshot.get("sectors")
    if not isinstance(sectors, list) or not sectors:
        return {"valid": False, "reason": "Sectors empty", "symbol_count": 0}
    lineage = snapshot.get("data_lineage") or {}
    trade_date = lineage.get("latest_trading_date") or snapshot.get("trading_date")
    if not trade_date:
        return {"valid": False, "reason": "Missing trade date", "symbol_count": 0}
    today_str = get_vn_now().strftime("%Y-%m-%d")
    if trade_date > today_str:
        return {"valid": False, "reason": "Future trade date", "symbol_count": 0}

    total_priced = 0
    for sector in sectors:
        if isinstance(sector, dict):
            for s in sector.get("stocks", []):
                if isinstance(s, dict) and float(s.get("price_vnd", 0) or s.get("match_price", 0) or 0) > 0:
                    total_priced += 1
    if total_priced == 0:
        return {"valid": False, "reason": "No priced stocks", "symbol_count": 0}

    quality_status = (snapshot.get("data_quality") or {}).get("status", "VERIFIED")
    return {
        "valid": True,
        "reason": None,
        "trade_date": trade_date,
        "data_quality": quality_status,
        "symbol_count": total_priced,
    }


def choose_better_snapshot_record(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Choose the record with valid price and newer trading date/time."""
    a_price = float(a.get("price_vnd", 0) or a.get("match_price", 0) or 0)
    b_price = float(b.get("price_vnd", 0) or b.get("match_price", 0) or 0)
    if a_price > 0 and b_price <= 0:
        return a
    if b_price > 0 and a_price <= 0:
        return b
    a_date = str(a.get("trading_date") or "")
    b_date = str(b.get("trading_date") or "")
    if a_date > b_date:
        return a
    if b_date > a_date:
        return b
    a_time = str(a.get("received_time") or a.get("exchange_time") or "")
    b_time = str(b.get("received_time") or b.get("exchange_time") or "")
    if a_time >= b_time:
        return a
    return b


def build_snapshot_symbol_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build a symbol -> stock index from snapshot sectors[*].stocks."""
    index: Dict[str, Dict[str, Any]] = {}
    for sector in snapshot.get("sectors", []):
        if not isinstance(sector, dict):
            continue
        stocks = sector.get("stocks", [])
        if not isinstance(stocks, list):
            continue
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            symbol = str(stock.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            current = index.get(symbol)
            if current is None:
                index[symbol] = stock
            else:
                index[symbol] = choose_better_snapshot_record(current, stock)
    return index


def _is_finite_metric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# ---------- Nâng cấp AI: Historical Context & Trend Detection ----------
def _detect_sector_momentum_trend(sector_history: List[Dict[str, Any]], sector_name: str) -> Dict[str, Any]:
    """Detect momentum trend for a sector across multiple days."""
    if len(sector_history) < 2:
        return {"trend": "KHONG_CO_DU_LIEU", "days_in_trend": 0, "change_summary": "Chưa đủ 2 ngày để phân tích xu hướng"}

    flow_scores = [s.get("flow_score", 50) for s in sector_history if isinstance(s, dict)]
    breadths = [
        float(s.get("breadth_pct")) for s in sector_history
        if isinstance(s, dict) and _is_finite_metric(s.get("breadth_pct"))
    ]

    if len(flow_scores) < 2:
        return {"trend": "KHONG_CO_DU_LIEU", "days_in_trend": 0, "change_summary": "Không có dữ liệu so sánh"}

    current = flow_scores[0] if flow_scores else 50
    prev_avg = sum(flow_scores[1:]) / len(flow_scores[1:]) if len(flow_scores) > 1 else 50

    diff = current - prev_avg
    change_pct = ((current - prev_avg) / max(prev_avg, 1)) * 100 if prev_avg > 0 else 0

    if diff > 8:
        trend = "TANG_NHANH"
        label = "tăng mạnh"
    elif diff > 3:
        trend = "TANG_CHAM"
        label = "tăng dần"
    elif diff < -8:
        trend = "GIAM_NHANH"
        label = "giảm mạnh"
    elif diff < -3:
        trend = "GIAM_CHAM"
        label = "giảm dần"
    else:
        trend = "ON_DINH"
        label = "ổn định"

    if len(breadths) >= 2:
        breadth_trend = "TANG" if breadths[0] > sum(breadths[1:]) / len(breadths[1:]) else "GIAM"
    else:
        breadth_trend = "KHONG_XAC_DINH"

    return {
        "trend": trend,
        "trend_label": label,
        "days_in_trend": len(flow_scores),
        "current_flow_score": round(current, 1),
        "avg_flow_score": round(prev_avg, 1),
        "change_summary": f"{'+' if change_pct >= 0 else ''}{change_pct:.1f}% so voi avg {len(flow_scores)-1} ngay truoc",
        "breadth_trend": breadth_trend,
        "momentum_signal": "XAC_NHAN" if abs(diff) > 5 else "YEU",
    }


def _detect_market_anomalies(current_snapshot: Dict[str, Any], recent_snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect anomalies by comparing current snapshot with historical data."""
    anomalies = []
    if not recent_snapshots:
        return anomalies

    current_quant = current_snapshot.get("quant_snapshot", {})
    current_sectors = {s["name"]: s for s in current_snapshot.get("sectors", [])}

    current_breadth = current_quant.get("breadth_pct")
    current_temp = current_quant.get("market_temperature")
    current_active = current_quant.get("active_ratio_pct")
    current_top10_liq = current_quant.get("top10_liquidity_share_pct")

    historical_breadths = []
    historical_temps = []
    historical_actives = []
    historical_top10_liqs = []

    for snap in recent_snapshots:
        quant = snap.get("quant_snapshot", {}) or {}
        for target, value in (
            (historical_breadths, quant.get("breadth_pct")),
            (historical_temps, quant.get("market_temperature")),
            (historical_actives, quant.get("active_ratio_pct")),
            (historical_top10_liqs, quant.get("top10_liquidity_share_pct")),
        ):
            if _is_finite_metric(value):
                target.append(float(value))

    avg_breadth = sum(historical_breadths) / len(historical_breadths) if historical_breadths else 50
    avg_temp = sum(historical_temps) / len(historical_temps) if historical_temps else 50
    avg_active = sum(historical_actives) / len(historical_actives) if historical_actives else 50
    avg_top10 = sum(historical_top10_liqs) / len(historical_top10_liqs) if historical_top10_liqs else 30

    threshold_breadth = 20
    threshold_temp = 15
    threshold_active = 15
    threshold_top10 = 15

    if _is_finite_metric(current_breadth) and abs(current_breadth - avg_breadth) > threshold_breadth:
        direction = "tang" if current_breadth > avg_breadth else "giam"
        anomalies.append({
            "type": "BREADTH_SPIKE",
            "severity": "HIGH" if abs(current_breadth - avg_breadth) > 30 else "MEDIUM",
            "title": f"Do rong thi truong {direction} bat thuong",
            "detail": f"Day {current_breadth:.1f}% ({'+' if direction == 'tang' else ''}{current_breadth - avg_breadth:.1f}% so voi avg {avg_breadth:.1f}% trong 5 ngay)",
            "metric": {"current": current_breadth, "avg_5d": round(avg_breadth, 1), "diff": round(current_breadth - avg_breadth, 1)}
        })

    if _is_finite_metric(current_temp) and abs(current_temp - avg_temp) > threshold_temp:
        direction = "tang" if current_temp > avg_temp else "giam"
        anomalies.append({
            "type": "TEMPERATURE_SHIFT",
            "severity": "HIGH" if abs(current_temp - avg_temp) > 20 else "MEDIUM",
            "title": f"Nhiet thi truong {direction} manh",
            "detail": f"{current_temp:.1f}/100 ({'+' if direction == 'tang' else ''}{current_temp - avg_temp:.1f} diem so voi avg {avg_temp:.1f})",
            "metric": {"current": current_temp, "avg_5d": round(avg_temp, 1), "diff": round(current_temp - avg_temp, 1)}
        })

    if _is_finite_metric(current_active) and abs(current_active - avg_active) > threshold_active:
        direction = "tang" if current_active > avg_active else "giam"
        anomalies.append({
            "type": "ACTIVE_RATIO_CHANGE",
            "severity": "MEDIUM",
            "title": f"Ti le ma co giao dich {direction} bat thuong",
            "detail": f"{current_active:.1f}% ({'+' if direction == 'tang' else ''}{current_active - avg_active:.1f}% so voi avg {avg_active:.1f}%)",
            "metric": {"current": current_active, "avg_5d": round(avg_active, 1), "diff": round(current_active - avg_active, 1)}
        })

    if _is_finite_metric(current_top10_liq) and abs(current_top10_liq - avg_top10) > threshold_top10:
        direction = "tang" if current_top10_liq > avg_top10 else "giam"
        anomalies.append({
            "type": "LIQUIDITY_CONCENTRATION",
            "severity": "HIGH" if current_top10_liq > avg_top10 + 20 else "MEDIUM",
            "title": f"Tien tot {direction} tap trung hoac lan toa",
            "detail": f"Top 10 chiếm {current_top10_liq:.1f}% GTGD ({'+' if direction == 'tang' else ''}{current_top10_liq - avg_top10:.1f}% so voi avg {avg_top10:.1f}%)",
            "metric": {"current": current_top10_liq, "avg_5d": round(avg_top10, 1), "diff": round(current_top10_liq - avg_top10, 1)}
        })

    for sector_name, sector in current_sectors.items():
        sector_breadth = sector.get("breadth_pct")

        hist_sector_breadths = []
        for snap in recent_snapshots:
            for s in snap.get("sectors", []):
                if s.get("name") == sector_name:
                    if _is_finite_metric(s.get("breadth_pct")):
                        hist_sector_breadths.append(float(s.get("breadth_pct")))
                    break

        if _is_finite_metric(sector_breadth) and hist_sector_breadths:
            avg_sec_breadth = sum(hist_sector_breadths) / len(hist_sector_breadths)
            if abs(sector_breadth - avg_sec_breadth) > 25:
                direction = "tang" if sector_breadth > avg_sec_breadth else "giam"
                anomalies.append({
                    "type": "SECTOR_BREADTH_SPIKE",
                    "severity": "MEDIUM",
                    "title": f"Nganh {sector_name} co do rong {direction} bat thuong",
                    "detail": f"{sector_breadth:.1f}% ({'+' if direction == 'tang' else ''}{sector_breadth - avg_sec_breadth:.1f}% so voi avg {avg_sec_breadth:.1f}%)",
                    "sector": sector_name,
                    "metric": {"current": sector_breadth, "avg_5d": round(avg_sec_breadth, 1), "diff": round(sector_breadth - avg_sec_breadth, 1)}
                })

    return anomalies[:10]


def _build_historical_context(current_snapshot: Dict[str, Any], recent_snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build historical context for AI analysis."""
    if not recent_snapshots:
        return {
            "available": False,
            "message": "Chưa có dữ liệu lịch sử để so sánh"
        }

    current_quant = current_snapshot.get("quant_snapshot", {})
    current_sectors = {s["name"]: s for s in current_snapshot.get("sectors", [])}

    temps = [
        float(s.get("quant_snapshot", {}).get("market_temperature")) for s in recent_snapshots
        if _is_finite_metric(s.get("quant_snapshot", {}).get("market_temperature"))
    ]
    breadths = [
        float(s.get("quant_snapshot", {}).get("breadth_pct")) for s in recent_snapshots
        if _is_finite_metric(s.get("quant_snapshot", {}).get("breadth_pct"))
    ]
    temps.reverse()
    breadths.reverse()

    current_temp = float(current_quant.get("market_temperature")) if _is_finite_metric(current_quant.get("market_temperature")) else 50.0
    current_breadth = float(current_quant.get("breadth_pct")) if _is_finite_metric(current_quant.get("breadth_pct")) else 50.0
    avg_temp = sum(temps) / len(temps) if temps else 50
    avg_breadth = sum(breadths) / len(breadths) if breadths else 50

    temp_change = current_temp - avg_temp
    breadth_change = current_breadth - avg_breadth

    sector_trends = {}
    for sector_name, sector in current_sectors.items():
        sector_history = []
        for snap in recent_snapshots:
            for s in snap.get("sectors", []):
                if s.get("name") == sector_name:
                    sector_history.append(s)
                    break
        if sector_history:
            sector_history.insert(0, sector)
            trend_info = _detect_sector_momentum_trend(sector_history, sector_name)
            sector_trends[sector_name] = {
                "current_flow_score": sector.get("flow_score", 50),
                "avg_flow_score_5d": trend_info.get("avg_flow_score", 50),
                "trend": trend_info.get("trend", "KHONG_CO_DU_LIEU"),
                "trend_label": trend_info.get("trend_label", ""),
                "change_summary": trend_info.get("change_summary", ""),
                "momentum_signal": trend_info.get("momentum_signal", "YEU"),
            }

    sorted_by_flow = sorted(sector_trends.items(), key=lambda x: x[1].get("current_flow_score", 0), reverse=True)
    top_momentum = [
        {"sector": name, "flow_score": data["current_flow_score"], "trend": data["trend"], "trend_label": data["trend_label"]}
        for name, data in sorted_by_flow[:3]
    ]
    weak_momentum = [
        {"sector": name, "flow_score": data["current_flow_score"], "trend": data["trend"], "trend_label": data["trend_label"]}
        for name, data in sorted_by_flow[-2:]
    ]

    return {
        "available": True,
        "days_of_history": len(recent_snapshots),
        "market_summary": {
            "temperature_current": round(current_temp, 1),
            "temperature_avg_5d": round(avg_temp, 1),
            "temperature_change": round(temp_change, 1),
            "temperature_trend": "TANG" if temp_change > 3 else "GIAM" if temp_change < -3 else "ON_DINH",
            "breadth_current": round(current_breadth, 1),
            "breadth_avg_5d": round(avg_breadth, 1),
            "breadth_change": round(breadth_change, 1),
            "breadth_trend": "TANG" if breadth_change > 5 else "GIAM" if breadth_change < -5 else "ON_DINH",
        },
        "sector_trends": sector_trends,
        "top_momentum_sectors": top_momentum,
        "weak_momentum_sectors": weak_momentum,
        "insight": _generate_trend_insight(temp_change, breadth_change, top_momentum, weak_momentum),
    }


def _generate_trend_insight(temp_change: float, breadth_change: float, top_sectors: List, weak_sectors: List) -> str:
    """Generate a brief text insight about current trend."""
    insights = []

    if temp_change > 5:
        insights.append(f"Nhiệt thị trường tăng mạnh (+{temp_change:.1f} điểm so với avg 5 ngày)")
    elif temp_change < -5:
        insights.append(f"Nhiệt thị trường giảm rõ rệt ({temp_change:.1f} điểm so với avg 5 ngày)")
    else:
        insights.append("Nhiệt thị trường ổn định")

    if breadth_change > 8:
        insights.append("Độ rộng cải thiện đáng kể, dòng tiền đang lan tỏa")
    elif breadth_change < -8:
        insights.append("Độ rộng thu hẹp, thị trường có xu hướng phân hóa")

    if top_sectors:
        top_names = [s["sector"] for s in top_sectors[:2]]
        insights.append(f"Nhóm dẫn dắt: {', '.join(top_names)}")

    return ". ".join(insights) if insights else "Thị trường ổn định, chưa có xu hướng rõ ràng"


def build_historical_snapshot_context() -> Dict[str, Any]:
    """Build comprehensive historical context for AI analysis from recent snapshots."""
    current = get_latest_snapshot()
    if not current:
        return {"available": False, "message": "Không có snapshot hiện tại"}

    current_quant = current.get("quant_snapshot") or {}
    if current_quant.get("model_version") != HEATMAP_MODEL_VERSION:
        return {"available": False, "message": "Snapshot hiện tại chưa đủ dữ liệu để chuẩn hóa Quant v4"}

    current_id = current_quant.get("snapshot_id")
    recent = [
        snapshot for snapshot in get_recent_snapshots(days=WEEKLY_ANALYSIS_DAYS + 1)
        if (snapshot.get("quant_snapshot") or {}).get("model_version") == HEATMAP_MODEL_VERSION
        and (snapshot.get("quant_snapshot") or {}).get("snapshot_id") != current_id
    ][:WEEKLY_ANALYSIS_DAYS]
    if not recent:
        return {"available": False, "message": "Chưa có snapshot Quant v4 tương thích để so sánh"}
    anomalies = _detect_market_anomalies(current, recent)
    historical = _build_historical_context(current, recent)

    return {
        "available": True,
        "historical_context": historical,
        "anomalies": anomalies,
        "generated_at": get_vn_now().strftime("%Y-%m-%d %H:%M:%S"),
    }


_FINAL_SNAPSHOT_LOCK = threading.Lock()

def ensure_latest_frozen_snapshot() -> Optional[Dict[str, Any]]:
    """Ensure latest frozen snapshot is built once per day after 15:10, thread-locked."""
    snap = get_latest_snapshot()
    today_str = get_vn_now().strftime("%Y-%m-%d")
    val = validate_frozen_snapshot(snap)
    if val["valid"] and val["trade_date"] == today_str:
        return snap

    with _FINAL_SNAPSHOT_LOCK:
        snap = get_latest_snapshot()
        val = validate_frozen_snapshot(snap)
        if val["valid"] and val["trade_date"] == today_str:
            return snap
        session = get_market_session()
        if session["is_trading_day"] and session["is_final_snapshot_time"]:
            try:
                built = fetch_market_heatmap_data(force_refresh=True)
                if isinstance(built, dict) and built.get("snapshot_frozen"):
                    return built
            except Exception as e:
                print(f"[Heatmap Engine] Locked snapshot build error: {e}")
        return snap

# Global in-memory cache
_HEATMAP_CACHE = {
    "data": None,
    "timestamp": 0,
    "snapshot_date": None,
    "snapshot_frozen": False
}

_AI_INSIGHT_CACHE = {
    "report": None,
    "timestamp": 0,
    "snapshot_id": None,
}

# #region debug-point runtime-logger
def _debug_report(hypothesis_id: str, location: str, msg: str, data: Optional[Dict[str, Any]] = None) -> None:
    try:
        debug_url = "http://127.0.0.1:7777/event"
        session_id = "heatmap-slow-load"
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dbg", "heatmap-slow-load.env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
            for line in content.splitlines():
                if line.startswith("DEBUG_SERVER_URL="):
                    debug_url = line.split("=", 1)[1].strip()
                elif line.startswith("DEBUG_SESSION_ID="):
                    session_id = line.split("=", 1)[1].strip()
        payload = {
            "sessionId": session_id,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data or {},
            "ts": int(time.time() * 1000),
        }
        req = urllib.request.Request(
            debug_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        pass
import threading
from zoneinfo import ZoneInfo

MARKET_MORNING_OPEN = dtime(9, 0)
MARKET_ATO_END = dtime(9, 15)
MARKET_MORNING_CLOSE = dtime(11, 30)
MARKET_AFTERNOON_OPEN = dtime(13, 0)
MARKET_ATC_START = dtime(14, 30)
MARKET_MATCHING_CLOSE = dtime(14, 45)
MARKET_POST_CLOSE_END = dtime(15, 0)
HEATMAP_FINAL_SNAPSHOT_TIME = dtime(15, 10)
MARKET_SCHEDULE_VERSION = "VN_CASH_MARKET_2026_08"

# --------- Timezone Helper (CRITICAL): Force Ho Chi Minh (UTC+7) for market hour checks ---------
def get_vn_now() -> datetime:
    """Return current date & time IN VIETNAM TIMEZONE (UTC+7). Works regardless of system timezone."""
    try:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
        return datetime.now(tz).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() + timedelta(hours=7)


def vn_datetime_from_timestamp(timestamp: float) -> datetime:
    """Convert epoch timestamp float to naive datetime in Asia/Ho_Chi_Minh (UTC+7)."""
    try:
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)
    except Exception:
        return datetime.utcfromtimestamp(timestamp) + timedelta(hours=7)


def _configured_market_holidays() -> set:
    raw = os.environ.get("VN_MARKET_HOLIDAYS", "")
    return {item.strip() for item in raw.split(",") if re.match(r"^\d{4}-\d{2}-\d{2}$", item.strip())}


def _is_vietnam_public_holiday(current: datetime) -> bool:
    date_key = current.strftime("%Y-%m-%d")
    if date_key in _configured_market_holidays():
        return True
    try:
        import holidays
        return current.date() in holidays.country_holidays(
            "VN", years=[current.year], observed=True, language="vi"
        )
    except Exception:
        return current.strftime("%m-%d") in {"01-01", "04-30", "05-01", "09-02"}


def _exchange_session_state(exchange: str, current_time: dtime, is_trading_day: bool) -> Dict[str, Any]:
    """Return the exchange-specific cash-equity phase for the supplied VN time."""
    exchange = str(exchange or "").upper()
    if not is_trading_day:
        return {"phase": "CLOSED", "label": "Đóng cửa", "order_types": [], "is_matching": False}
    if current_time < MARKET_MORNING_OPEN:
        return {"phase": "PRE_OPEN", "label": "Chờ mở cửa", "order_types": [], "is_matching": False}
    if current_time < MARKET_ATO_END:
        if exchange == "HOSE":
            return {"phase": "ATO", "label": "Khớp lệnh mở cửa", "order_types": ["ATO", "LO"], "is_matching": True}
        order_types = ["LO", "MTL", "MOK", "MAK"] if exchange == "HNX" else ["LO"]
        return {"phase": "CONTINUOUS", "label": "Khớp lệnh liên tục", "order_types": order_types, "is_matching": True}
    if current_time < MARKET_MORNING_CLOSE:
        order_types = ["LO", "MTL", "MOK", "MAK"] if exchange == "HNX" else ["LO"]
        return {"phase": "CONTINUOUS", "label": "Khớp lệnh liên tục", "order_types": order_types, "is_matching": True}
    if current_time < MARKET_AFTERNOON_OPEN:
        return {"phase": "LUNCH_BREAK", "label": "Nghỉ trưa", "order_types": [], "is_matching": False}
    if current_time < MARKET_ATC_START:
        order_types = ["LO", "MTL", "MOK", "MAK"] if exchange == "HNX" else ["LO"]
        return {"phase": "CONTINUOUS", "label": "Khớp lệnh liên tục", "order_types": order_types, "is_matching": True}
    if current_time < MARKET_MATCHING_CLOSE:
        if exchange in {"HOSE", "HNX"}:
            return {"phase": "ATC", "label": "Khớp lệnh đóng cửa", "order_types": ["ATC", "LO"], "is_matching": True}
        return {"phase": "CONTINUOUS", "label": "Khớp lệnh liên tục", "order_types": ["LO"], "is_matching": True}
    if current_time < MARKET_POST_CLOSE_END:
        if exchange == "HOSE":
            return {"phase": "AGREEMENT_ONLY", "label": "Chỉ giao dịch thỏa thuận", "order_types": [], "is_matching": False}
        if exchange == "HNX":
            return {"phase": "PLO", "label": "Khớp lệnh sau giờ", "order_types": ["PLO"], "is_matching": True}
        return {"phase": "CONTINUOUS", "label": "Khớp lệnh liên tục", "order_types": ["LO"], "is_matching": True}
    return {"phase": "CLOSED", "label": "Đóng cửa", "order_types": [], "is_matching": False}


def get_market_session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Describe the cash-market session using Vietnam local time."""
    current = now or get_vn_now()
    date_key = current.strftime("%Y-%m-%d")
    current_time = current.time()
    is_weekend = current.weekday() >= 5
    is_holiday = _is_vietnam_public_holiday(current)
    is_trading_day = not is_weekend and not is_holiday

    if not is_trading_day:
        phase = "WEEKEND" if is_weekend else "HOLIDAY"
    elif current_time < MARKET_MORNING_OPEN:
        phase = "PRE_OPEN"
    elif current_time < MARKET_ATO_END:
        phase = "ATO"
    elif current_time < MARKET_MORNING_CLOSE:
        phase = "CONTINUOUS"
    elif current_time < MARKET_AFTERNOON_OPEN:
        phase = "LUNCH_BREAK"
    elif current_time < MARKET_ATC_START:
        phase = "CONTINUOUS"
    elif current_time < MARKET_MATCHING_CLOSE:
        phase = "ATC"
    elif current_time < MARKET_POST_CLOSE_END:
        phase = "POST_CLOSE_TRADING"
    else:
        phase = "CLOSED"

    is_final_snapshot_time = is_trading_day and current_time >= HEATMAP_FINAL_SNAPSHOT_TIME
    is_finalization_pending = is_trading_day and (MARKET_POST_CLOSE_END <= current_time < HEATMAP_FINAL_SNAPSHOT_TIME)
    exchange_sessions = {
        exchange: _exchange_session_state(exchange, current_time, is_trading_day)
        for exchange in ("HOSE", "HNX", "UPCOM")
    }
    is_live_matching = any(item["is_matching"] for item in exchange_sessions.values())
    detail_label = " · ".join(
        f"{exchange} {item['label']}" for exchange, item in exchange_sessions.items()
    )

    return {
        "local_time": current.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        "calendar_date": date_key,
        "phase": phase,
        "schedule_version": MARKET_SCHEDULE_VERSION,
        "is_trading_day": is_trading_day,
        "is_live_matching": is_live_matching,
        "is_closed": phase in {"WEEKEND", "HOLIDAY", "CLOSED"},
        "can_poll": phase in {"ATO", "CONTINUOUS", "LUNCH_BREAK", "ATC", "POST_CLOSE_TRADING"},
        "is_final_snapshot_time": is_final_snapshot_time,
        "is_finalization_pending": is_finalization_pending,
        "exchange_sessions": exchange_sessions,
        "detail_label": detail_label,
    }

def is_market_open_time() -> bool:
    return bool(get_market_session()["is_live_matching"])

def is_past_close_or_weekend() -> bool:
    return bool(get_market_session()["is_closed"])

def should_reset_15h10(last_timestamp: float) -> bool:
    if last_timestamp <= 0:
        return True
    now = get_vn_now()
    last_dt = vn_datetime_from_timestamp(last_timestamp)
    if now.date() > last_dt.date():
        return True
    reset_target = datetime.combine(now.date(), HEATMAP_FINAL_SNAPSHOT_TIME)
    if now >= reset_target and last_dt < reset_target:
        return True
    return False

DEFAULT_HEATMAP_SYMBOLS = None  # Legacy placeholder — REPLACED BY DYNAMIC FULL MARKET FETCHER BELOW

# -------- DYNAMIC FULL-MARKET STOCK LISTING (PART 1 of spec) --------
# Use the direct listing adapter for real-time ICB-based market coverage.
# Auto-refresh once per day (ICB industry mappings change < 1 per month, so 24h cache is safe).
_ALL_STOCK_CACHE: Dict[str, Any] = {
    "symbols": [],
    "last_refresh_date": None,   # YYYY-MM-DD string, once per day refresh
    "icb_text_by_symbol": {},
    "sector_info_by_symbol": {},
    "security_type_by_symbol": {},
}

def calculate_sector_change_percent(stocks_in_sector: List[Dict[str, Any]]) -> float:
    """Standard market price-board sector change: Simple average % change of all listed stocks in sector."""
    if not stocks_in_sector:
        return 0.0
    valid_changes = [float(s.get("change_pct", 0.0)) for s in stocks_in_sector if "change_pct" in s]
    if not valid_changes:
        return 0.0
    return round(sum(valid_changes) / len(valid_changes), 2)


def _fetch_market_total_liquidity(matched_val: float) -> float:
    """Fetch total market trading value (including matched + put-through / thỏa thuận) from index board."""
    try:
        url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
        now_ts = int(time.time())
        payload = {"timeFrame": "ONE_DAY", "symbols": ["VNINDEX"], "to": now_ts, "countBack": 2}
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and "accumulatedValue" in data[0]:
                acc_vals = data[0]["accumulatedValue"]
                if acc_vals:
                    total_vnindex_val = float(acc_vals[-1]) * 1_000_000
                    if total_vnindex_val > matched_val:
                        return total_vnindex_val
    except Exception as e:
        print(f"[Heatmap Liquidity] Fallback to matched value: {e}")
    return matched_val

def _build_curated_sector_map() -> Dict[str, Dict[str, str]]:
    curated = {}
    for sec_data in SECTOR_DEFINITIONS.values():
        for symbol in sec_data.get("symbols", []):
            curated[str(symbol).upper()] = {
                "sector": sec_data.get("sector", "SẢN XUẤT CÔNG NGHIỆP"),
                "archetype": sec_data.get("archetype", "MANUFACTURING_GENERAL"),
            }
    return curated

_CURATED_SECTOR_MAP = _build_curated_sector_map()

def _infer_sector_from_icb_text(symbol: str, icb_text: str) -> Dict[str, str]:
    """Fast local sector resolver for heatmap; avoids a second Listing() call via get_sector_info()."""
    symbol = str(symbol).upper().strip()
    if symbol in _CURATED_SECTOR_MAP:
        return _CURATED_SECTOR_MAP[symbol]

    text = str(icb_text or "").lower()
    leaf = text.split("|")[-1].strip()
    if any(k in text for k in ["bank", "ngân hàng"]):
        return {"sector": "NGÂN HÀNG", "archetype": "BANKING"}
    if any(k in text for k in ["securities", "brokerage", "investment services", "môi giới chứng khoán", "công ty chứng khoán"]):
        return {"sector": "CHỨNG KHOÁN", "archetype": "SECURITIES"}
    if any(k in text for k in ["insurance", "bảo hiểm"]):
        return {"sector": "BẢO HIỂM", "archetype": "INSURANCE"}
    if any(k in text for k in ["industrial park", "khu công nghiệp", "industrial properties"]):
        return {"sector": "BĐS KHU CÔNG NGHIỆP", "archetype": "INDUSTRIAL_PARK"}
    if any(k in text for k in ["real estate", "bất động sản", "real estate investment", "real estate services"]):
        return {"sector": "BẤT ĐỘNG SẢN", "archetype": "REAL_ESTATE"}
    if any(k in text for k in ["steel", "thép"]):
        return {"sector": "THÉP", "archetype": "STEEL"}
    if any(k in leaf for k in ["building materials", "vật liệu xây dựng", "xi măng", "cement", "gạch", "nội thất"]):
        return {"sector": "VẬT LIỆU XÂY DỰNG", "archetype": "BUILDING_MATERIALS"}
    if any(k in leaf for k in ["construction", "xây dựng", "civil engineering"]):
        return {"sector": "XÂY DỰNG - ĐẦU TƯ CÔNG", "archetype": "CONSTRUCTION"}
    if any(k in text for k in ["chemicals", "fertilizer", "phân bón", "hóa chất"]):
        return {"sector": "HÓA CHẤT - PHÂN BÓN", "archetype": "CHEMICALS_FERTILIZERS"}
    if any(k in text for k in ["rubber", "cao su"]):
        return {"sector": "CAO SU", "archetype": "RUBBER"}
    if any(k in text for k in ["oil", "gas", "petroleum", "dầu khí", "energy equipment"]):
        return {"sector": "DẦU KHÍ", "archetype": "OIL_GAS"}
    if any(k in text for k in ["electricity", "power", "utility", "water utility", "điện", "năng lượng", "cấp nước"]):
        return {"sector": "ĐIỆN - NĂNG LƯỢNG", "archetype": "POWER_ENERGY"}
    if any(k in text for k in ["mining", "khoáng sản", "coal", "than"]):
        return {"sector": "KHOÁNG SẢN", "archetype": "MINING"}
    if any(k in text for k in ["automobiles", "auto", "ô tô", "phụ tùng"]):
        return {"sector": "Ô TÔ - PHỤ TÙNG", "archetype": "AUTOMOTIVE"}
    if any(k in text for k in ["textiles", "garments", "dệt may", "apparel"]):
        return {"sector": "DỆT MAY", "archetype": "TEXTILE"}
    if any(k in text for k in ["seafood", "aquaculture", "thủy sản"]):
        return {"sector": "THỦY SẢN", "archetype": "SEAFOOD"}
    if any(k in text for k in ["food", "beverage", "đồ uống", "thực phẩm", "dairy", "brewery"]):
        return {"sector": "THỰC PHẨM & ĐỒ UỐNG", "archetype": "FOOD_BEVERAGE"}
    if any(k in text for k in ["retail", "personal goods", "household goods", "bán lẻ", "trang sức"]):
        return {"sector": "BÁN LẺ", "archetype": "RETAIL"}
    if any(k in text for k in ["pharmaceutical", "health care", "y tế", "dược"]):
        return {"sector": "DƯỢC - Y TẾ", "archetype": "PHARMA_HEALTHCARE"}
    if any(k in text for k in ["software", "technology", "telecommunications", "viễn thông", "công nghệ"]):
        return {"sector": "CÔNG NGHỆ - TRUYỀN THÔNG", "archetype": "TECH_TELECOM"}
    if any(k in text for k in ["airlines", "airport", "travel", "leisure", "hàng không", "du lịch", "khách sạn"]):
        return {"sector": "HÀNG KHÔNG - DU LỊCH", "archetype": "AVIATION_TOURISM"}
    if any(k in text for k in ["marine transportation", "industrial transportation", "logistics", "cảng biển", "vận tải", "shipping", "port"]):
        return {"sector": "CẢNG BIỂN - VẬN TẢI", "archetype": "PORTS_LOGISTICS"}
    if any(k in text for k in ["paper", "wood", "forest", "sugar", "gỗ", "giấy", "mía đường"]):
        return {"sector": "ĐƯỜNG - GỖ - GIẤY", "archetype": "SUGAR_WOOD_PAPER"}
    return {"sector": "SẢN XUẤT CÔNG NGHIỆP", "archetype": "MANUFACTURING_GENERAL"}

def fetch_all_listed_symbols(force_refresh: bool = False) -> List[str]:
    """
    PART 1 Core engine: fetch EVERY common stock listed on HOSE / HNX / UPCOM
    via the direct listing adapter, filter out derivatives/CWs/bonds.
    Cache lifetime = 1 calendar day.
    Returns: list of symbol strings ["SSI", "FPT", ..., "VIC"]
    """
    global _ALL_STOCK_CACHE
    today = get_vn_now().strftime("%Y-%m-%d")
    started_at = time.time()

    if not force_refresh \
        and _ALL_STOCK_CACHE["symbols"] \
        and _ALL_STOCK_CACHE["last_refresh_date"] == today:
        _debug_report("C", "heatmap_engine.py:193", "All-stock listing cache hit", {
            "symbol_count": len(_ALL_STOCK_CACHE["symbols"]),
            "trade_date": today,
        })
        return list(_ALL_STOCK_CACHE["symbols"])  # Cache hit today

    try:
        from market_data_provider import Listing
        df = Listing(source='VCI').symbols_by_industries()
        if df is None or df.empty:
            raise ValueError("Listing.symbols_by_industries returned empty")

        valid_exchanges = {"HOSE", "HNX", "UPCoM", "UPCOM", "HSX", "HNX"}
        seen_symbols = set()
        symbols = []
        icb_text_by_symbol: Dict[str, str] = {}
        sector_info_by_symbol: Dict[str, Dict[str, str]] = {}
        security_type_by_symbol: Dict[str, str] = {}

        # Normalize column lookup case-insensitive across provider responses.
        cols = {c.lower(): c for c in df.columns}
        sym_col = cols.get("symbol", cols.get("ticker", "symbol"))
        exch_col = cols.get("exchange", cols.get("san", "exchange"))

        for _, row in df.iterrows():
            try:
                sym_raw = str(row.get(sym_col, "") or "").strip().upper()
                if not sym_raw:
                    continue

                # The provider has already filtered security types. Allow
                # alphanumeric UPCoM tickers and longer listed-fund symbols.
                if not re.match(r"^[A-Z][A-Z0-9]{1,11}$", sym_raw):
                    continue

                exch_raw = str(row.get(exch_col, "") or "").strip().upper()
                if exch_raw and exch_raw not in valid_exchanges:
                    continue  # Filter to the 3 main common stock boards

                security_type = str(row.get(cols.get("com_type_code", "com_type_code"), "") or "").strip().upper()
                icb_parts = []
                for col_name in df.columns:
                    lower_name = str(col_name).lower()
                    if lower_name.startswith("icb_name"):
                        val = str(row.get(col_name, "") or "").strip()
                        if val:
                            icb_parts.append(val)
                icb_text = " | ".join(icb_parts)
                lower_icb_text = icb_text.lower()
                if security_type != "QU" and any(k in lower_icb_text for k in ["bond", "trái phiếu", "etf", "fund", "quỹ", "warrant", "chứng quyền", "cw", "certificate"]):
                    continue

                if sym_raw in seen_symbols:
                    continue  # Dedupe
                seen_symbols.add(sym_raw)
                symbols.append(sym_raw)
                icb_text_by_symbol[sym_raw] = icb_text
                primary_sector = (
                    {"sector": "QUỸ ETF", "archetype": "LISTED_FUND"}
                    if security_type == "QU"
                    else _infer_sector_from_icb_text(sym_raw, icb_text)
                )
                sector_info_by_symbol[sym_raw] = primary_sector
                # Multi-membership is resolved via the global sieucophieu multimap;
                # ICB-based inference only contributes the primary sector.
                security_type_by_symbol[sym_raw] = security_type
            except Exception:
                continue

        # Safety floor: must have >= 300 tickers otherwise data is suspicious (e.g., API partial outage).
        # If < 300 fall back to SECTOR_DEFINITIONS (manually curated ~500 tickers hardcodes).
        if len(symbols) < 300:
            print(f"[Full Market Listing] Warning: only {len(symbols)} symbols (<300). Fallback to SECTOR_DEFINITIONS tickers.")
            symbols = []
            from sector_mapping import SECTOR_DEFINITIONS
            for _, sec_data in SECTOR_DEFINITIONS.items():
                for s in sec_data.get("symbols", []):
                    s = str(s).upper()
                    if s not in seen_symbols:
                        seen_symbols.add(s)
                        symbols.append(s)

        symbols.sort()
        _ALL_STOCK_CACHE["symbols"] = symbols
        _ALL_STOCK_CACHE["last_refresh_date"] = today
        _ALL_STOCK_CACHE["icb_text_by_symbol"] = icb_text_by_symbol
        _ALL_STOCK_CACHE["sector_info_by_symbol"] = sector_info_by_symbol
        # Multi-membership cache is populated lazily on first snapshot call,
        # since get_sector_memberships() is just a dict lookup against the
        # global SIEUCOPHIEU_MULTIMAP (no API calls required).
        _ALL_STOCK_CACHE["sector_memberships_by_symbol"] = {}
        _ALL_STOCK_CACHE["security_type_by_symbol"] = security_type_by_symbol
        _debug_report("A", "heatmap_engine.py:239", "Loaded symbols_by_industries", {
            "symbol_count": len(symbols),
            "elapsed_ms": round((time.time() - started_at) * 1000, 2),
            "columns": list(df.columns),
        })
        print(f"[Full Market Listing] OK: {len(symbols)} valid common stocks cached for session {today}")
        return list(symbols)

    except Exception as exc:
        _debug_report("A", "heatmap_engine.py:246", "Listing fetch failed; fallback to manual sectors", {
            "error": str(exc),
            "elapsed_ms": round((time.time() - started_at) * 1000, 2),
        })
        print(f"[Full Market Listing] Error: {exc}. Fallback to SECTOR_DEFINITIONS manual tickers.")
        # --- Safety fallback: use manually curated sector tickers ---
        from sector_mapping import SECTOR_DEFINITIONS
        manual = []
        seen = set()
        for _, sec_data in SECTOR_DEFINITIONS.items():
            for s in sec_data.get("symbols", []):
                s = str(s).upper()
                if s not in seen:
                    seen.add(s)
                    manual.append(s)
        manual.sort()
        _ALL_STOCK_CACHE["symbols"] = manual
        _ALL_STOCK_CACHE["last_refresh_date"] = today
        _ALL_STOCK_CACHE["icb_text_by_symbol"] = {}
        _ALL_STOCK_CACHE["sector_info_by_symbol"] = {}
        _ALL_STOCK_CACHE["sector_memberships_by_symbol"] = {}
        return list(manual)


def get_env_api_key(key_name: str) -> str:
    key = os.environ.get(key_name, "").strip()
    if key:
        return key
    for env_path in [os.path.join(os.path.dirname(__file__), ".env"), "/etc/secrets/.env"]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith(key_name):
                            parts = line.strip().split("=", 1)
                            if len(parts) == 2:
                                return parts[1].strip().strip('"\'')
            except Exception:
                pass
    return ""



def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _calc_volume_price_alignment(stock: Dict[str, Any], sector_avg_value: float) -> float:
    """
    Volume-Price Alignment Signal: Xác nhận dòng tiền từ volume.
    +1.0 = Volume confirm price direction (up + high vol, down + low vol)
    -1.0 = Divergence (up + low vol, down + high vol)
    """
    price_change = float(stock.get("change_pct", 0))
    stock_vol = float(stock.get("trading_value", 0))
    
    if sector_avg_value <= 0 or stock_vol <= 0:
        return 0.0
    
    vol_ratio = stock_vol / sector_avg_value
    
    if price_change > 0:
        if vol_ratio >= 1.5:
            return 1.0   # Strong: up + high volume = real buying
        elif vol_ratio >= 0.8:
            return 0.4   # Moderate: up + normal volume
        else:
            return -0.3  # Weak: up + low volume = suspicious
    elif price_change < 0:
        if vol_ratio >= 1.5:
            return -0.5  # Down + high volume = real selling
        elif vol_ratio >= 0.8:
            return -0.2  # Moderate: down + normal volume
        else:
            return 0.3   # Weak: down + low volume = lack of conviction
    return 0.0


def _calc_position_in_range(stock: Dict[str, Any]) -> float:
    """
    Money Flow Position: Vị trí giá trong range floor-ceiling.
    >0.6 = Accumulation zone (người mua kiểm soát)
    <0.4 = Distribution zone (người bán kiểm soát)
    """
    price = float(stock.get("match_price", 0))
    floor = float(stock.get("floor", 0))
    ceiling = float(stock.get("ceiling", 0))
    
    price_range = ceiling - floor
    if price_range <= 0 or price <= 0:
        return 0.5
    
    position = (price - floor) / price_range
    return _clamp(position, 0.0, 1.0)


def _is_quant_stock(stock: Dict[str, Any]) -> bool:
    """Return whether a board row belongs to the v4 common-stock universe."""
    instrument_type = str(stock.get("instrument_type") or "STOCK").upper()
    exchange = str(stock.get("exchange") or "").upper()
    return (
        instrument_type == "STOCK"
        and exchange in {"HOSE", "HNX", "UPCOM"}
        and float(stock.get("ref_price", 0) or 0) > 0
    )


def _is_active_stock(stock: Dict[str, Any]) -> bool:
    return float(stock.get("volume", 0) or 0) > 0 or float(stock.get("trading_value", 0) or 0) > 0


def _normalized_heatmap_memberships(
    stock: Dict[str, Any], sector: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Return stable, unique sector memberships for one heatmap symbol."""
    sector = sector or {}
    raw_memberships = stock.get("sector_memberships") or []
    if not isinstance(raw_memberships, (list, tuple)):
        raw_memberships = []
    candidates = [
        *raw_memberships,
        {
            "sector": stock.get("sector") or sector.get("name") or "Khác",
            "archetype": stock.get("sector_code") or sector.get("code") or "OTHER",
        },
    ]
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


def _load_heatmap_vn30_contract() -> Tuple[set, Dict[str, Any]]:
    """Load the same dual-source membership snapshot used by RRG and Bubbles."""
    try:
        from rrg_index_membership import get_index_membership

        symbols, meta = get_index_membership("VN30")
        return set(symbols), meta
    except Exception as exc:
        logger.warning("Heatmap VN30 membership unavailable: %s", exc)
        return set(), {
            "source": "unavailable",
            "stale": True,
            "fetched_at": None,
            "error": str(exc),
        }


def _apply_heatmap_universe_contract(
    payload: Dict[str, Any],
    vn30_members: Optional[set] = None,
    vn30_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize a live or legacy payload to the schema-v9 visual universe.

    Sector arrays deliberately retain full memberships for sector analytics.
    The browser is given enough metadata to draw each symbol once in the
    all-market view while still finding it through every secondary sector.
    """
    sectors = payload.get("sectors") if isinstance(payload.get("sectors"), list) else []
    membership_union: Dict[str, List[Dict[str, str]]] = {}
    membership_seen: Dict[str, set] = {}

    for sector in sectors:
        for stock in sector.get("stocks", []) or []:
            if not _is_quant_stock(stock):
                continue
            symbol = str(stock.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            for membership in _normalized_heatmap_memberships(stock, sector):
                name = membership["sector"]
                if name in membership_seen.setdefault(symbol, set()):
                    continue
                membership_seen[symbol].add(name)
                membership_union.setdefault(symbol, []).append(membership)

    normalized_sectors: List[Dict[str, Any]] = []
    unique: Dict[str, Dict[str, Any]] = {}
    placements = 0
    for raw_sector in sectors:
        sector = dict(raw_sector)
        stocks: List[Dict[str, Any]] = []
        for raw_stock in raw_sector.get("stocks", []) or []:
            if not _is_quant_stock(raw_stock):
                continue
            symbol = str(raw_stock.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            stock = dict(raw_stock)
            stock["symbol"] = symbol
            stock["sector_memberships"] = membership_union.get(symbol) or _normalized_heatmap_memberships(stock, raw_sector)
            stock["is_active"] = _is_active_stock(stock)
            stock["index_memberships"] = []
            stocks.append(stock)
            placements += 1

            current = unique.get(symbol)
            if current is None or float(stock.get("trading_value", 0) or 0) > float(current.get("trading_value", 0) or 0):
                unique[symbol] = stock

        if not stocks:
            continue
        stocks.sort(key=lambda row: float(row.get("market_cap", 0) or 0), reverse=True)
        sector["stocks"] = stocks
        sector["total_market_cap"] = sum(max(float(row.get("market_cap", 0) or 0), 0.0) for row in stocks)
        sector["total_trading_value"] = sum(max(float(row.get("trading_value", 0) or 0), 0.0) for row in stocks)
        sector["avg_change_pct"] = calculate_sector_change_percent(stocks)
        normalized_sectors.append(sector)

    if vn30_members is None or vn30_meta is None:
        loaded_members, loaded_meta = _load_heatmap_vn30_contract()
        vn30_members = loaded_members if vn30_members is None else vn30_members
        vn30_meta = loaded_meta if vn30_meta is None else vn30_meta
    vn30_members = {str(symbol).upper().strip() for symbol in (vn30_members or set()) if str(symbol).strip()}
    vn30_meta = vn30_meta or {}

    for sector in normalized_sectors:
        for stock in sector["stocks"]:
            memberships = ["VN30"] if stock["symbol"] in vn30_members else []
            stock["index_memberships"] = memberships
            stock["is_vn30"] = bool(memberships)
    for symbol, stock in unique.items():
        memberships = ["VN30"] if symbol in vn30_members else []
        stock["index_memberships"] = memberships
        stock["is_vn30"] = bool(memberships)

    # Keep the filter contract byte-for-byte compatible with Market Bubbles.
    try:
        from market_bubble_engine import build_filter_groups

        filter_groups = build_filter_groups(unique.values(), vn30_members, vn30_meta)
    except Exception as exc:
        logger.warning("Heatmap filter groups degraded: %s", exc)
        filter_groups = [{
            "key": "ALL", "type": "all", "label": "Tất cả ngành / chỉ số",
            "total_count": len(unique),
            "active_count": sum(int(bool(stock.get("is_active"))) for stock in unique.values()),
            "enabled": bool(unique),
        }]

    rows = list(unique.values())
    active_rows = [stock for stock in rows if stock.get("is_active")]
    summary = payload.setdefault("summary", {})
    summary.update({
        "total_stocks": len(rows),
        "advances": sum(stock.get("status") == "GAIN" for stock in active_rows),
        "declines": sum(stock.get("status") == "LOSS" for stock in active_rows),
        "unchanged": sum(stock.get("status") == "REF" for stock in active_rows),
        "inactive_count": len(rows) - len(active_rows),
        "ceilings": sum(stock.get("status") == "CEILING" for stock in active_rows),
        "floors": sum(stock.get("status") == "FLOOR" for stock in active_rows),
        "total_market_cap": sum(max(float(stock.get("market_cap", 0) or 0), 0.0) for stock in rows),
        "matched_trading_value": sum(max(float(stock.get("trading_value", 0) or 0), 0.0) for stock in rows),
    })

    vn30_rows = [stock for symbol, stock in unique.items() if symbol in vn30_members]
    payload["schema_version"] = HEATMAP_SCHEMA_VERSION
    payload["sectors"] = sorted(normalized_sectors, key=lambda row: float(row.get("total_market_cap", 0) or 0), reverse=True)
    payload["filter_groups"] = filter_groups
    payload["indices"] = {
        "VN30": {
            **vn30_meta,
            "symbols": sorted(stock["symbol"] for stock in vn30_rows),
            "total_count": len(vn30_rows),
            "active_count": sum(int(bool(stock.get("is_active"))) for stock in vn30_rows),
            "available": bool(vn30_rows),
            "stale": bool(vn30_meta.get("stale")),
            "source": vn30_meta.get("source") or "unavailable",
        }
    }
    lineage = payload.setdefault("data_lineage", {})
    lineage["sector_count"] = len(normalized_sectors)
    lineage["visual_universe"] = {
        "policy": "UNIQUE_COMMON_STOCK_PRIMARY_SECTOR",
        "unique_symbols": len(unique),
        "sector_membership_placements": placements,
        "multi_sector_extra_placements": max(placements - len(unique), 0),
        "zero_trading_value_count": sum(float(stock.get("trading_value", 0) or 0) <= 0 for stock in rows),
    }
    lineage["quant_universe"] = {
        "instrument_type": "STOCK",
        "exchanges": ["HOSE", "HNX", "UPCOM"],
        "count": len(unique),
        "excluded_funds": (lineage.get("quant_universe") or {}).get("excluded_funds", 0),
    }
    return payload


def _reference_market_cap(stock: Dict[str, Any]) -> float:
    explicit = float(stock.get("reference_market_cap", 0) or 0)
    if explicit > 0:
        return explicit
    current_cap = max(float(stock.get("market_cap", 0) or 0), 0.0)
    change = float(stock.get("change_pct", 0) or 0) / 100.0
    divisor = 1.0 + change
    return current_cap / divisor if current_cap > 0 and divisor > 0 else current_cap


def _concentration_state(top10_share: Optional[float], effective_count: Optional[float]) -> str:
    if top10_share is None or effective_count is None:
        return "KHONG_DU_DU_LIEU"
    if top10_share < 30.0 and effective_count >= 50.0:
        return "LAN_TOA"
    if top10_share < 45.0 and effective_count >= 25.0:
        return "CAN_BANG"
    if top10_share < 60.0 and effective_count >= 12.0:
        return "TAP_TRUNG"
    return "RAT_TAP_TRUNG"


def _heat_confidence(valid_count: int, active_ratio: float, directional_ratio: float) -> str:
    if valid_count >= 500 and active_ratio >= 0.55 and directional_ratio >= 0.50:
        return "CAO"
    if valid_count >= 200 and active_ratio >= 0.35 and directional_ratio >= 0.25:
        return "VUA"
    return "THAP"


def _score_label(score: float) -> str:
    if score >= 72:
        return "DAN_DAT"
    if score >= 58:
        return "TICH_CUC"
    if score >= 42:
        return "PHAN_HOA"
    if score >= 28:
        return "THAN_TRONG"
    return "SUY_YEU"


def _market_regime(score: float, breadth: float) -> str:
    if score >= 72 and breadth >= 0.62:
        return "LAN_TOA_TICH_CUC"
    if score >= 58:
        return "NGHIENG_TICH_CUC"
    if score >= 42:
        return "PHAN_HOA"
    if score >= 28:
        return "NGHIENG_THAN_TRONG"
    return "RUI_RO_CAO"


def classify_price_status(raw_match_price: float, ref_price: float,
                          ceiling: float, floor: float) -> Dict[str, Any]:
    """Classify the five board states without treating an untraded quote as floor/ceiling."""
    has_actual_match = raw_match_price > 0
    match_price = raw_match_price if has_actual_match else ref_price
    change_pct = round(((match_price - ref_price) / ref_price) * 100.0, 2) if ref_price > 0 else 0.0
    if has_actual_match and ceiling > 0 and match_price >= ceiling:
        status = "CEILING"
    elif has_actual_match and floor > 0 and match_price <= floor:
        status = "FLOOR"
    elif change_pct > 0:
        status = "GAIN"
    elif change_pct < 0:
        status = "LOSS"
    else:
        status = "REF"
    return {"match_price": match_price, "change_pct": change_pct, "status": status}


def build_quant_snapshot(stock_records: List[Dict[str, Any]], sectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the deterministic v4 common-stock market radar.

    Direction (heat) is deliberately separate from participation quality and
    liquidity concentration. This prevents a busy flat tape or a decisive
    selloff from receiving an artificial bullish bonus.
    """
    valid = [s for s in stock_records if _is_quant_stock(s)]
    active = [s for s in valid if _is_active_stock(s)]
    advances = [s for s in active if float(s.get("change_pct", 0) or 0) > 0]
    declines = [s for s in active if float(s.get("change_pct", 0) or 0) < 0]
    unchanged = [s for s in active if float(s.get("change_pct", 0) or 0) == 0]
    inactive = [s for s in valid if not _is_active_stock(s)]
    directional_count = len(advances) + len(declines)
    breadth = len(advances) / directional_count if directional_count else None
    active_ratio = len(active) / len(valid) if valid else 0.0
    directional_ratio = directional_count / len(active) if active else 0.0
    advance_share_active = len(advances) / len(active) if active else None
    net_breadth = (len(advances) - len(declines)) / len(active) if active else 0.0
    total_market_cap = sum(_reference_market_cap(s) for s in valid)
    total_trading_value = sum(max(float(s.get("trading_value", 0)), 0.0) for s in valid)

    weighted_change = 0.0
    if total_market_cap > 0:
        weighted_change = sum(
            float(s.get("change_pct", 0) or 0) * _reference_market_cap(s)
            for s in valid
        ) / total_market_cap

    median_change = statistics.median(float(s.get("change_pct", 0) or 0) for s in active) if active else 0.0
    cap_return_signal = _clamp(weighted_change / 3.0, -1.0, 1.0)
    median_return_signal = _clamp(median_change / 3.0, -1.0, 1.0)
    temperature = round(_clamp(
        50.0 + 50.0 * (
            0.50 * net_breadth
            + 0.30 * cap_return_signal
            + 0.20 * median_return_signal
        )
    ), 1)

    liquidity_sorted = sorted(valid, key=lambda s: float(s.get("trading_value", 0)), reverse=True)
    liquidity_shares = [
        max(float(s.get("trading_value", 0) or 0), 0.0) / total_trading_value
        for s in liquidity_sorted
    ] if total_trading_value > 0 else []
    top5_share = sum(liquidity_shares[:5]) * 100.0 if liquidity_shares else None
    top10_share = sum(liquidity_shares[:10]) * 100.0 if liquidity_shares else None
    top20_share = sum(liquidity_shares[:20]) * 100.0 if liquidity_shares else None
    liquidity_hhi = sum(share * share for share in liquidity_shares) if liquidity_shares else None
    effective_count = (1.0 / liquidity_hhi) if liquidity_hhi and liquidity_hhi > 0 else None

    liquidity_rank = {s["symbol"]: rank for rank, s in enumerate(liquidity_sorted, start=1)}
    population = max(len(liquidity_sorted) - 1, 1)
    
    # Pre-calculate sector average trading values for volume alignment
    sector_avg_values: Dict[str, float] = {}
    for sector in sectors:
        sec_stocks = sector.get("stocks", [])
        if sec_stocks:
            total_sec_vol = sum(float(s.get("trading_value", 0)) for s in sec_stocks)
            sector_avg_values[sector.get("name", "")] = total_sec_vol / len(sec_stocks)
    
    for stock in valid:
        rank = liquidity_rank.get(stock["symbol"], len(liquidity_sorted))
        liquidity_percentile = 1.0 - ((rank - 1) / population)
        
        # Core signals
        price_signal = _clamp(float(stock.get("change_pct", 0)) / 7.0, -1.0, 1.0)
        
        # NEW: Volume-price alignment (sector-relative)
        sector_name = stock.get("sector", "")
        sector_avg = sector_avg_values.get(sector_name, 0)
        vol_align = _calc_volume_price_alignment(stock, sector_avg)
        
        # NEW: Position in floor-ceiling range (accumulation/distribution proxy)
        position = _calc_position_in_range(stock)
        position_signal = _clamp((position - 0.5) * 2.0, -1.0, 1.0)
        
        # Enhanced flow_score: 50 + weighted signals
        stock["liquidity_rank"] = rank
        stock["market_liquidity_share_pct"] = round(
            float(stock.get("trading_value", 0)) / total_trading_value * 100.0, 3
        ) if total_trading_value else 0.0
        
        # NEW formula: 50 + 24*price + 14*liquidity + 8*vol_align + 6*position
        stock["flow_score"] = round(_clamp(
            50 
            + 24 * price_signal 
            + 14 * (liquidity_percentile - 0.5)
            + 8 * vol_align
            + 6 * position_signal
        ), 1)
        
        # Store intermediate signals for debugging/analysis
        stock["_signals"] = {
            "price_signal": round(price_signal, 3),
            "vol_align": round(vol_align, 3),
            "position_signal": round(position_signal, 3),
        }

    for sector in sectors:
        stocks = [s for s in sector.get("stocks", []) if _is_quant_stock(s)]
        sector_active = [s for s in stocks if _is_active_stock(s)]
        sector_up = sum(1 for s in sector_active if float(s.get("change_pct", 0) or 0) > 0)
        sector_down = sum(1 for s in sector_active if float(s.get("change_pct", 0) or 0) < 0)
        sector_unchanged = sum(1 for s in sector_active if float(s.get("change_pct", 0) or 0) == 0)
        sector_inactive = len(stocks) - len(sector_active)
        sector_directional = sector_up + sector_down
        sector_breadth = sector_up / sector_directional if sector_directional else None
        sector_net_breadth = (sector_up - sector_down) / len(sector_active) if sector_active else 0.0
        sector_active_ratio = len(sector_active) / len(stocks) if stocks else 0.0
        sector_value = sum(max(float(s.get("trading_value", 0) or 0), 0.0) for s in stocks)
        sector_ref_cap = sum(_reference_market_cap(s) for s in stocks)
        liquidity_share = sector_value / total_trading_value if total_trading_value else 0.0
        sector_weighted_change = (
            sum(float(s.get("change_pct", 0) or 0) * _reference_market_cap(s) for s in stocks) / sector_ref_cap
            if sector_ref_cap > 0 else 0.0
        )
        sector_median_change = statistics.median(
            float(s.get("change_pct", 0) or 0) for s in sector_active
        ) if sector_active else 0.0
        market_turnover = total_trading_value / total_market_cap if total_market_cap > 0 else 0.0
        sector_turnover = sector_value / sector_ref_cap if sector_ref_cap > 0 else 0.0
        relative_turnover = sector_turnover / market_turnover if market_turnover > 0 else 1.0
        turnover_signal = _clamp(math.log2(max(relative_turnover, 0.25)) / 2.0, -1.0, 1.0)

        sec_shares = [
            max(float(s.get("trading_value", 0) or 0), 0.0) / sector_value
            for s in stocks
        ] if sector_value > 0 else []
        sec_hhi = sum(share * share for share in sec_shares) if sec_shares else None
        sec_effective = 1.0 / sec_hhi if sec_hhi and sec_hhi > 0 else None

        score = _clamp(50.0 + 50.0 * (
            0.45 * sector_net_breadth
            + 0.30 * _clamp(sector_weighted_change / 3.0, -1.0, 1.0)
            + 0.15 * _clamp(sector_median_change / 3.0, -1.0, 1.0)
            + 0.10 * turnover_signal
        ))
        
        sector.update({
            "advances": sector_up,
            "declines": sector_down,
            "unchanged": sector_unchanged,
            "inactive_count": sector_inactive,
            "breadth_pct": round(sector_breadth * 100.0, 1) if sector_breadth is not None else None,
            "net_breadth_pct": round(sector_net_breadth * 100.0, 1),
            "directional_participation_pct": round(
                sector_directional / len(sector_active) * 100.0, 1
            ) if sector_active else 0.0,
            "active_ratio_pct": round(sector_active_ratio * 100.0, 1),
            "liquidity_share_pct": round(liquidity_share * 100.0, 2),
            "flow_score": round(score, 1),
            "flow_status": _score_label(score),
            "confidence": (
                "CAO" if len(sector_active) >= 12 and sector_active_ratio >= 0.5
                else ("VUA" if len(sector_active) >= 5 else "THAP")
            ),
            "_sector_signals": {
                "reference_cap_weighted_change_pct": round(sector_weighted_change, 3),
                "median_change_pct": round(sector_median_change, 3),
                "relative_turnover": round(relative_turnover, 3),
                "liquidity_hhi": round(sec_hhi, 6) if sec_hhi is not None else None,
                "effective_stock_count": round(sec_effective, 1) if sec_effective is not None else None,
            }
        })

    watchlist = sorted(
        [s for s in active if s.get("change_pct", 0) > 0],
        key=lambda s: (float(s.get("flow_score", 0)), float(s.get("trading_value", 0))),
        reverse=True,
    )[:8]
    sector_leaders = sorted(sectors, key=lambda s: (float(s.get("flow_score", 0)), float(s.get("total_trading_value", 0))), reverse=True)
    source_fingerprint = HEATMAP_MODEL_VERSION + "|" + "|".join(
        f"{s['symbol']}:{s.get('match_price', 0)}:{s.get('volume', 0)}" for s in sorted(valid, key=lambda x: x["symbol"])
    )
    snapshot_id = hashlib.sha256(source_fingerprint.encode("utf-8")).hexdigest()[:16]

    return {
        "model_version": HEATMAP_MODEL_VERSION,
        "snapshot_id": snapshot_id,
        "market_temperature": temperature,
        "market_regime": _market_regime(temperature, breadth if breadth is not None else 0.5),
        "heat_confidence": _heat_confidence(len(valid), active_ratio, directional_ratio),
        "breadth_pct": round(breadth * 100.0, 1) if breadth is not None else None,
        "breadth_available": breadth is not None,
        "breadth_state": "AVAILABLE" if breadth is not None else "INSUFFICIENT_DIRECTIONAL_DATA",
        "breadth_sample_size": directional_count,
        "advance_share_active_pct": round(advance_share_active * 100.0, 1) if advance_share_active is not None else None,
        "directional_participation_pct": round(directional_ratio * 100.0, 1),
        "net_breadth_pct": round(net_breadth * 100.0, 1),
        "advance_decline_ratio": round(len(advances) / len(declines), 2) if declines else None,
        "advance_decline_state": (
            "AVAILABLE" if declines else ("NO_DECLINES" if advances else "NO_DIRECTIONAL_ISSUES")
        ),
        "active_ratio_pct": round(active_ratio * 100.0, 1),
        "active_count": len(active),
        "inactive_count": len(inactive),
        "unchanged_active_count": len(unchanged),
        "quant_universe_count": len(valid),
        "market_cap_weighted_change_pct": round(weighted_change, 2),
        "median_change_pct": round(median_change, 2),
        "matched_trading_value": round(total_trading_value, 0),
        "top5_liquidity_share_pct": round(top5_share, 1) if top5_share is not None else None,
        "top10_liquidity_share_pct": round(top10_share, 1) if top10_share is not None else None,
        "top20_liquidity_share_pct": round(top20_share, 1) if top20_share is not None else None,
        "liquidity_hhi": round(liquidity_hhi, 6) if liquidity_hhi is not None else None,
        "effective_stock_count": round(effective_count, 1) if effective_count is not None else None,
        "concentration_state": _concentration_state(top10_share, effective_count),
        "concentration_baseline": {"available": False, "sessions": 0, "reason": "INSUFFICIENT_HISTORY"},
        "breadth_stability_pct": None,
        "deprecated_fields": ["breadth_stability_pct"],
        "sector_leaders": [
            {
                "sector": s["name"],
                "flow_score": s["flow_score"],
                "change_pct": s["avg_change_pct"],
                "breadth_pct": s["breadth_pct"],
                "liquidity_share_pct": s["liquidity_share_pct"],
                "confidence": s["confidence"],
            }
            for s in sector_leaders[:6]
        ],
        "watchlist": [
            {
                "symbol": s["symbol"],
                "sector": s["sector"],
                "price_vnd": s["price_vnd"],
                "change_pct": s["change_pct"],
                "trading_value": s["trading_value"],
                "liquidity_rank": s["liquidity_rank"],
                "flow_score": s["flow_score"],
                "signal": "THEO_DOI_DONG_TIEN",
            }
            for s in watchlist
        ],
        "methodology": {
            "scope": "Anh chup mot phien; khong phai du bao gia va khong suy dien giao dich to chuc.",
            "quant_universe": "Co phieu thuong HOSE/HNX/UPCOM co gia tham chieu hop le; loai ETF va chung chi quy.",
            "temperature": "50 + 50 x (50% net breadth + 30% loi suat von hoa tham chieu + 20% trung vi loi suat); loi suat chuan hoa trong +/-3%.",
            "breadth": "A/(A+D); ma tran/san duoc tinh theo huong, ma dung tham chieu co giao dich va ma khong giao dich duoc tach rieng.",
            "concentration": "Top 5/10/20 ty trong GTGD khop lenh + HHI + so ma hieu dung 1/HHI; khong tham gia huong cua diem nhiet.",
            "flow_score": "24% bien dong gia + 14% thanh khoan + 8% khop khoi luong-gia + 6% vi tri trong range.",
            "flow_score_sector": "45% net breadth + 30% loi suat von hoa tham chieu + 15% trung vi loi suat + 10% cuong do vong quay tuong doi.",
        },
    }


def _apply_concentration_baseline(
    quant_snapshot: Dict[str, Any], recent_snapshots: List[Dict[str, Any]]
) -> None:
    """Attach a robust trailing Top-10 baseline when enough v4 sessions exist."""
    current_id = quant_snapshot.get("snapshot_id")
    values: List[float] = []
    for snapshot in recent_snapshots:
        quant = snapshot.get("quant_snapshot") or {}
        if quant.get("model_version") != HEATMAP_MODEL_VERSION:
            continue
        if current_id and quant.get("snapshot_id") == current_id:
            continue
        value = quant.get("top10_liquidity_share_pct")
        if isinstance(value, (int, float)):
            values.append(float(value))
    values = values[:SNAPSHOT_RETENTION_DAYS]
    current = quant_snapshot.get("top10_liquidity_share_pct")
    if not isinstance(current, (int, float)) or len(values) < 10:
        quant_snapshot["concentration_baseline"] = {
            "available": False,
            "sessions": len(values),
            "reason": "INSUFFICIENT_HISTORY",
        }
        return

    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    robust_scale = 1.4826 * mad
    robust_z = (float(current) - median) / robust_scale if robust_scale > 0 else 0.0
    if robust_z >= 2.0:
        relative_state = "CAO_BAT_THUONG"
    elif robust_z <= -2.0:
        relative_state = "LAN_TOA_BAT_THUONG"
    else:
        relative_state = "BINH_THUONG"
    quant_snapshot["concentration_baseline"] = {
        "available": True,
        "sessions": len(values),
        "median_top10_pct": round(median, 1),
        "mad_pct": round(mad, 2),
        "delta_pct_points": round(float(current) - median, 1),
        "robust_zscore": round(robust_z, 2),
        "relative_state": relative_state,
    }


def _get_recent_v4_quant_snapshots() -> List[Dict[str, Any]]:
    """Read only compact v4 quant objects for concentration baselining."""
    init_db_snapshot()
    snapshots: List[Dict[str, Any]] = []
    try:
        with _get_snapshot_db_conn() as conn:
            rows = conn.execute(
                """
                SELECT json_extract(snapshot_json, '$.quant_snapshot') AS quant_json
                FROM heatmap_snapshots
                WHERE is_frozen_15h10 = 1
                  AND json_extract(snapshot_json, '$.quant_snapshot.model_version') = ?
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (HEATMAP_MODEL_VERSION, SNAPSHOT_RETENTION_DAYS),
            ).fetchall()
        for row in rows:
            if row["quant_json"]:
                snapshots.append({"quant_snapshot": json.loads(row["quant_json"])})
    except Exception as db_err:
        logger.warning("Heatmap concentration baseline unavailable: %s", db_err)
    return snapshots


def _upgrade_snapshot_to_v4(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute legacy full snapshots and upgrade them to schema v9 in memory."""
    quant = payload.get("quant_snapshot") or {}
    if payload.get("schema_version", 0) >= HEATMAP_SCHEMA_VERSION and quant.get("model_version") == HEATMAP_MODEL_VERSION:
        return payload

    symbol_index = build_snapshot_symbol_index(payload)
    if not symbol_index:
        payload.setdefault("data_quality", {}).setdefault("warnings", []).append(
            "LEGACY_MODEL: snapshot khong co du dong co phieu de tai tinh Quant v4."
        )
        payload["schema_version"] = HEATMAP_SCHEMA_VERSION
        payload.setdefault("filter_groups", [{
            "key": "ALL", "type": "all", "label": "Tất cả ngành / chỉ số",
            "total_count": 0, "active_count": 0, "enabled": False,
        }])
        return payload

    if quant.get("model_version") != HEATMAP_MODEL_VERSION:
        source_model_version = quant.get("model_version") or "unknown"
        stocks = list(symbol_index.values())
        sectors = payload.get("sectors") if isinstance(payload.get("sectors"), list) else []
        new_quant = build_quant_snapshot(stocks, sectors)
        new_quant["source_snapshot_model_version"] = source_model_version
        new_quant["recomputed_from_legacy"] = True
        payload["quant_snapshot"] = new_quant
    return _apply_heatmap_universe_contract(payload)


# ---------- Intraday Snapshot Poller (Task 2) ----------
# Captures one heatmap payload per phase-aware interval and persists it to
# `heatmap_intraday_snapshots` so the front-end scrubber can replay the day.
# Phase -> interval mapping lives in `_phase_snapshot_interval_seconds` so
# both runtime and unit tests can interrogate it.
INTRADAY_PHASE_INTERVALS: Dict[str, int] = {
    # ATO is the 9:00–9:15 opening call — high churn, capture every minute.
    "ATO": 60,
    # Continuous matching during morning + afternoon — coarser cadence.
    "CONTINUOUS": 300,
    # Lunch break: price board is frozen; we still sample every 15 minutes
    # so the scrubber timeline doesn't have a visible gap.
    "LUNCH_BREAK": 900,
    # ATC closing call — same density as ATO.
    "ATC": 60,
    # Post-close window: data stops updating but we keep a checkpoint so the
    # scrubber knows where the day ended.
    "POST_CLOSE_TRADING": 300,
}


def _classify_intraday_phase(current_time: dtime) -> str:
    """Translate a Vietnam-local clock time into the intraday phase label.

    Mirrors `get_market_session` but collapses the live/closed distinction
    into a single label per phase so the poller and unit tests share logic.
    """
    if current_time < MARKET_MORNING_OPEN:
        return "PRE_OPEN"
    if current_time < MARKET_MORNING_CLOSE:
        # 9:00–9:15 is ATO; everything else inside the morning window is
        # continuous matching.
        if current_time < MARKET_ATO_END:
            return "ATO"
        return "CONTINUOUS"
    if current_time < MARKET_AFTERNOON_OPEN:
        return "LUNCH_BREAK"
    if current_time < MARKET_ATC_START:
        return "CONTINUOUS"
    if current_time < MARKET_MATCHING_CLOSE:
        return "ATC"
    if current_time < MARKET_POST_CLOSE_END:
        return "POST_CLOSE_TRADING"
    return "CLOSED"


def _phase_snapshot_interval_seconds(phase: str) -> int:
    """Polling interval for a given phase. Returns 0 to skip capture."""
    if phase == "PRE_OPEN" or phase == "CLOSED":
        return 0
    return INTRADAY_PHASE_INTERVALS.get(phase, 300)


def _next_snapshot_target(now_dt: datetime, last_snapshot_iso: Optional[str]) -> datetime:
    """Compute the wall-clock time of the next snapshot this poller should take.

    Always returns the next *future* target ≥ now, rounded down to the
    phase's bucket boundary so concurrent checkpoints from multiple workers
    can't drift apart. If we already have a snapshot for this bucket we
    step forward by one phase interval.
    """
    phase = _classify_intraday_phase(now_dt.time())
    interval_seconds = _phase_snapshot_interval_seconds(phase)
    if interval_seconds <= 0:
        # Outside trading hours — next target is the next market open.
        # Caller treats the 0 interval as "skip"; we still bump by 60s so
        # the loop doesn't spin at full speed while we wait for 9:00.
        return now_dt + timedelta(seconds=60)

    if last_snapshot_iso:
        try:
            last_dt = datetime.fromisoformat(last_snapshot_iso)
            candidate = last_dt + timedelta(seconds=interval_seconds)
            if candidate > now_dt:
                return candidate
        except (TypeError, ValueError):
            pass

    # No prior snapshot for today — round down to the current bucket edge
    # so ATO captures at 09:00, 09:01, 09:02 … fall on clean minute marks.
    base = now_dt.replace(second=0, microsecond=0)
    elapsed = (now_dt - base).total_seconds()
    bucket_index = int(elapsed // interval_seconds)
    candidate = base + timedelta(seconds=bucket_index * interval_seconds)
    if candidate < now_dt:
        candidate += timedelta(seconds=interval_seconds)
    return candidate


_INTRADAY_POLLER_STARTED = False
_INTRADAY_POLLER_LOCK = threading.Lock()
_INTRADAY_POLLER_THREAD: Optional[threading.Thread] = None


def _intraday_poll_loop() -> None:
    """Background worker: capture heatmap snapshots on a phase-aware cadence."""
    global _INTRADAY_POLLER_STARTED
    _INTRADAY_POLLER_STARTED = True
    print("[Intraday Poller] Started.")
    while True:
        try:
            now_dt = get_vn_now()
            session = get_market_session(now_dt)
            if not session.get("can_poll"):
                # Outside trading hours: sleep 60s and re-check. The scrubber
                # is fed from the SQLite store, so no harm in idling.
                time.sleep(60)
                continue

            # Find the most recent intraday snapshot for today (any phase).
            today_str = now_dt.strftime("%Y-%m-%d")
            latest = get_latest_intraday_snapshot()
            last_iso = latest["snapshot_time"] if (latest and latest["snapshot_time"].startswith(today_str)) else None

            # Retention policy: scrubber is "today only". When the poller
            # detects a fresh trading day (latest row belongs to yesterday
            # or the DB is empty on a pollable session), drop every older
            # checkpoint before we capture the first ATO tick. Idempotent.
            if latest and not last_iso:
                purge_intraday_before(today_str)

            target = _next_snapshot_target(now_dt, last_iso)
            sleep_seconds = max(1.0, (target - now_dt).total_seconds())
            # Cap at 30s so we react quickly when the clock crosses a phase
            # boundary (e.g. 9:15 ATO → CONTINUOUS).
            sleep_seconds = min(sleep_seconds, 30.0)
            time.sleep(sleep_seconds)

            # Re-evaluate after the sleep — phase may have changed.
            now_dt = get_vn_now()
            phase = _classify_intraday_phase(now_dt.time())
            if _phase_snapshot_interval_seconds(phase) <= 0:
                continue
            if not is_market_open_time() and phase not in {"ATO", "ATC", "POST_CLOSE_TRADING"}:
                # Only sample during live matching + the two call auctions.
                continue

            stock_records, board_source, board_fetched_at, requested_symbols = _collect_market_board()
            visual_stock_records = [stock for stock in stock_records if _is_quant_stock(stock)]
            sectors_list = _group_stocks_by_sector(visual_stock_records)
            session_now = get_market_session(now_dt)
            payload = _assemble_heatmap_payload(
                stock_records,
                sectors_list,
                board_source,
                board_fetched_at,
                session_now,
                now_dt,
                requested_symbols=requested_symbols,
            )
            payload = _apply_heatmap_universe_contract(payload)
            snapshot_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S+07:00")
            save_intraday_snapshot(snapshot_iso, phase, payload)
            print(f"[Intraday Poller] Saved {phase} snapshot @ {snapshot_iso} ({len(stock_records)} mã).")
        except Exception as exc:
            # Never crash the thread — Vietcap rate-limits and DNS blips must
            # not take down the scrubber data pipeline.
            print(f"[Intraday Poller] Warning: loop iteration failed: {exc}")
            time.sleep(30)


def start_intraday_poller() -> None:
    """Idempotently launch the background intraday poller."""
    global _INTRADAY_POLLER_THREAD
    with _INTRADAY_POLLER_LOCK:
        if _INTRADAY_POLLER_THREAD and _INTRADAY_POLLER_THREAD.is_alive():
            return
        thread = threading.Thread(target=_intraday_poll_loop, name="intraday-snapshot-poller", daemon=True)
        thread.start()
        _INTRADAY_POLLER_THREAD = thread


def _build_intraday_heatmap_data() -> Optional[Dict[str, Any]]:
    """One-shot snapshot for callers that want the current state without going
    through the in-memory cache (e.g. on-demand timeline backfill)."""
    init_db_snapshot()
    now_dt = get_vn_now()
    session = get_market_session(now_dt)
    if not session.get("can_poll"):
        return None
    try:
        stock_records, board_source, board_fetched_at, requested_symbols = _collect_market_board()
        visual_stock_records = [stock for stock in stock_records if _is_quant_stock(stock)]
        sectors_list = _group_stocks_by_sector(visual_stock_records)
        payload = _assemble_heatmap_payload(
            stock_records,
            sectors_list,
            board_source,
            board_fetched_at,
            session,
            now_dt,
            requested_symbols=requested_symbols,
        )
        payload = _apply_heatmap_universe_contract(payload)
        phase = _classify_intraday_phase(now_dt.time())
        snapshot_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S+07:00")
        save_intraday_snapshot(snapshot_iso, phase, payload)
        return {
            "snapshot_time": snapshot_iso,
            "session_phase": phase,
            "payload": payload,
        }
    except Exception as exc:
        print(f"[Heatmap] _build_intraday_heatmap_data failed: {exc}")
        return None


def _collect_market_board() -> Tuple[List[Dict[str, Any]], str, str, int]:
    """Fetch the full-market price board from Vietcap and parse rows.

    Returns (stock_records, board_source, board_fetched_at, requested_symbols).

    This is the I/O-heavy half of the heatmap build; the analytical half
    (sector grouping + quant snapshot + payload assembly) lives in
    `_assemble_heatmap_payload` so the intraday poller can reuse it without
    re-querying the price board for every checkpoint.
    """
    from market_data_provider import Trading  # local import mirrors existing pattern

    board_started_at = time.time()
    all_tickers = fetch_all_listed_symbols()
    requested_symbols = len(all_tickers)
    df_board = Trading(source='VCI').price_board(all_tickers)
    board_source = str(df_board.attrs.get("source") or "Vietcap public price board")
    board_fetched_at = str(df_board.attrs.get("fetched_at") or datetime.utcnow().isoformat())
    _debug_report("A", "heatmap_engine.py:_collect_market_board", "price_board completed", {
        "requested_tickers": len(all_tickers),
        "row_count": 0 if df_board is None else len(df_board),
        "elapsed_ms": round((time.time() - board_started_at) * 1000, 2),
    })
    if df_board.empty:
        raise ValueError("Nguồn bảng giá trả về dữ liệu rỗng")

    stock_records: List[Dict[str, Any]] = []
    parse_started_at = time.time()
    skipped_count = 0
    included_count = 0

    for idx, row in df_board.iterrows():
        try:
            def get_val(grp, col, default=0.0):
                try:
                    if (grp, col) in row.index:
                        v = row[(grp, col)]
                    elif col in row.index:
                        v = row[col]
                    else:
                        v = default
                    if pd.isna(v):
                        return default
                    return v
                except Exception:
                    return default

            symbol = str(get_val('listing', 'symbol', '')).strip().upper()
            if not symbol:
                continue

            organ_name = str(get_val('listing', 'organ_name', symbol)).strip()
            exchange = str(get_val('listing', 'exchange', 'HOSE')).strip().upper()
            if exchange == "HSX":
                exchange = "HOSE"
            stock_type = str(get_val('listing', 'stock_type', 'STOCK')).strip().upper()
            is_delisted = int(float(get_val('listing', 'is_delisted', 0) or 0))
            trading_date = str(get_val('listing', 'trading_date', '') or '').strip()
            received_time = str(get_val('listing', 'received_time', '') or '').strip()
            listed_shares = float(get_val('listing', 'listed_share', 0))

            if exchange not in {"HOSE", "HNX", "UPCOM"} or stock_type not in {"STOCK", "ETF", "UNIT_TRUST"} or is_delisted:
                skipped_count += 1
                continue

            ref_price = float(get_val('listing', 'ref_price', 0.0))
            ceiling = float(get_val('listing', 'ceiling', 0.0))
            floor = float(get_val('listing', 'floor', 0.0))

            raw_match_price = float(get_val('match', 'match_price', 0.0))
            price_state = classify_price_status(raw_match_price, ref_price, ceiling, floor)
            match_price = float(price_state["match_price"])

            if ref_price <= 0 or match_price <= 0 or listed_shares <= 0:
                skipped_count += 1
                continue

            accumulated_val = float(get_val('match', 'accumulated_value', 0.0))
            accumulated_vol = float(get_val('match', 'accumulated_volume', 0.0))

            # Debug log: xem giá trị raw trước khi convert
            if included_count < 3:  # Chỉ log 3 mã đầu để không spam
                print(f"[DEBUG] {symbol}: raw_accumulated_val={accumulated_val}")

            # Vietcap's accumulatedValue is expressed in million VND for every board.
            accumulated_val = max(accumulated_val, 0.0) * 1_000_000

            if ref_price > 0:
                change_pct = float(price_state["change_pct"])
                change_amt = round(match_price - ref_price, 0)
            else:
                change_pct = 0.0
                change_amt = 0.0

            # Direct price-board values are already VND, including penny stocks below 1,000.
            price_vnd = match_price
            market_cap = listed_shares * price_vnd

            status = str(price_state["status"])

            sector_memberships = _ALL_STOCK_CACHE.get("sector_memberships_by_symbol", {}).get(symbol)
            if not sector_memberships:
                sector_memberships = get_sector_memberships(symbol)
            primary_membership = sector_memberships[0]
            sector_name = primary_membership.get("sector", "Sản xuất công nghiệp") if isinstance(primary_membership, dict) else "Sản xuất công nghiệp"
            archetype = primary_membership.get("archetype", "MANUFACTURING_GENERAL") if isinstance(primary_membership, dict) else "MANUFACTURING_GENERAL"

            stock_records.append({
                "symbol": symbol,
                "name": organ_name,
                "exchange": exchange,
                "match_price": match_price,
                "price_vnd": price_vnd,
                "ref_price": ref_price,
                "ceiling": ceiling,
                "floor": floor,
                "change_amt": change_amt,
                "change_pct": change_pct,
                "volume": int(accumulated_vol),
                "trading_value": float(accumulated_val),
                "market_cap": float(market_cap),
                "reference_market_cap": float(listed_shares * ref_price),
                "status": status,
                "instrument_type": stock_type,
                "sector": sector_name,
                "sector_code": archetype,
                "sector_memberships": [
                    {"sector": m["sector"], "archetype": m["archetype"]}
                    for m in sector_memberships
                ],
                "trading_date": trading_date,
                "received_time": received_time,
            })
            included_count += 1
        except Exception as row_err:
            skipped_count += 1
            if skipped_count <= 3:
                print(f"Error parsing row {idx} for heatmap: {row_err}")
            continue

    _debug_report("B", "heatmap_engine.py:_collect_market_board", "Board rows parsed", {
        "stock_records": len(stock_records),
        "included": included_count,
        "skipped": skipped_count,
        "elapsed_ms": round((time.time() - parse_started_at) * 1000, 2),
    })
    return stock_records, board_source, board_fetched_at, requested_symbols


def _group_stocks_by_sector(stock_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group parsed stock records into ICB sector buckets (multi-membership aware)."""
    sector_started_at = time.time()
    sectors_dict: Dict[str, Dict[str, Any]] = {}
    for s in stock_records:
        memberships = s.get("sector_memberships") or [
            {"sector": s.get("sector"), "archetype": s.get("sector_code")}
        ]
        for mem in memberships:
            sec_name = mem["sector"]
            if sec_name not in sectors_dict:
                sectors_dict[sec_name] = {
                    "name": sec_name,
                    "code": mem["archetype"],
                    "total_market_cap": 0.0,
                    "total_trading_value": 0.0,
                    "stocks": []
                }
            sectors_dict[sec_name]["stocks"].append(s)
            sectors_dict[sec_name]["total_market_cap"] += s["market_cap"]
            sectors_dict[sec_name]["total_trading_value"] += s["trading_value"]

    sectors_list = []
    for sec_name, sec_data in sectors_dict.items():
        sec_data["stocks"].sort(key=lambda x: x["market_cap"], reverse=True)
        sec_data["avg_change_pct"] = calculate_sector_change_percent(sec_data["stocks"])
        _mcap_tot = sum(float(s["market_cap"]) for s in sec_data["stocks"] if float(s["market_cap"]) > 0)
        print(f"[Heatmap Calc] {sec_name:22s}: {len(sec_data['stocks']):3d} mã | "
              f"ΣMCAP ≈ {(_mcap_tot/1e9):.0f} tỷ VNĐ | ΣGTGD ≈ {sec_data['total_trading_value']/1e9:.1f} tỷ | TB có trọng số: {sec_data['avg_change_pct']:+.2f}%")
        sectors_list.append(sec_data)

    sectors_list.sort(key=lambda x: x["total_market_cap"], reverse=True)
    _debug_report("D", "heatmap_engine.py:_group_stocks_by_sector", "Sector grouping completed", {
        "sector_count": len(sectors_list),
        "elapsed_ms": round((time.time() - sector_started_at) * 1000, 2),
    })
    return sectors_list


def _assemble_heatmap_payload(
    stock_records: List[Dict[str, Any]],
    sectors_list: List[Dict[str, Any]],
    board_source: str,
    board_fetched_at: str,
    market_session: Dict[str, Any],
    now_dt: datetime,
    requested_symbols: int = 0,
) -> Dict[str, Any]:
    """Build the full heatmap payload (sectors + quant + summary + lineage).

    Pure analytical work — no I/O. Caller passes in the result of
    `_collect_market_board()` (or an equivalent stock_records list). The
    intraday poller invokes this with a fresh `market_session` snapshot.
    `requested_symbols` is the pre-parsed ticker count, used to populate
    `data_lineage.coverage.requested_symbols` accurately; if unknown we
    fall back to the unique-symbols count (a safe under-estimate).
    """
    quant_snapshot = build_quant_snapshot(stock_records, sectors_list)
    _apply_concentration_baseline(quant_snapshot, _get_recent_v4_quant_snapshots())
    sectors_list.sort(key=lambda x: x["total_market_cap"], reverse=True)

    quant_stocks = [stock for stock in stock_records if _is_quant_stock(stock)]
    active_quant_stocks = [stock for stock in quant_stocks if _is_active_stock(stock)]
    total_stocks = len(quant_stocks)
    advances = sum(1 for s in active_quant_stocks if s["status"] == "GAIN")
    declines = sum(1 for s in active_quant_stocks if s["status"] == "LOSS")
    unchanged = sum(1 for s in active_quant_stocks if s["status"] == "REF")
    inactive_count = total_stocks - len(active_quant_stocks)
    ceilings = sum(1 for s in active_quant_stocks if s["status"] == "CEILING")
    floors = sum(1 for s in active_quant_stocks if s["status"] == "FLOOR")
    total_mcap = sum(s["market_cap"] for s in quant_stocks)
    matched_val = sum(s["trading_value"] for s in quant_stocks)
    total_val = _fetch_market_total_liquidity(matched_val)

    print(f"[DEBUG] Tổng thanh khoản thị trường: {total_val/1e9:.2f} tỷ VNĐ (Khớp lệnh: {matched_val/1e9:.2f} tỷ)")

    now_str = get_vn_now().strftime("%d/%m/%Y %H:%M:%S")

    requested_count = requested_symbols or len({s["symbol"] for s in stock_records})

    return {
        "schema_version": HEATMAP_SCHEMA_VERSION,
        "timestamp": now_str,
        "is_market_open": is_market_open_time(),
        "market_closed": is_past_close_or_weekend(),
        "market_session": market_session,
        "snapshot_frozen": False,
        "served_from": "LIVE_MARKET_ADAPTER",
        "summary": {
            "total_stocks": total_stocks,
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "inactive_count": inactive_count,
            "ceilings": ceilings,
            "floors": floors,
            "total_market_cap": total_mcap,
            "total_trading_value": total_val,
            "matched_trading_value": matched_val,
        },
        "sectors": sectors_list,
        "quant_snapshot": quant_snapshot,
        "data_lineage": {
            "price_source": board_source,
            "classification_source": "Vietcap ICB level 1-4 + curated Vietnamese sector aliases",
            "classification_reference": "https://sieucophieu.vn/bang-dien",
            "sector_count": len(sectors_list),
            "fetched_at": board_fetched_at,
            "latest_trading_date": max((s.get("trading_date") or "" for s in stock_records), default=""),
            "coverage": {
                "requested_symbols": requested_count,
                "accepted_listings": total_stocks,
                "accepted_active_listings": len(active_quant_stocks),
                "excluded_or_unpriced": max(requested_count - total_stocks, 0),
            },
            "quant_universe": {
                "instrument_type": "STOCK",
                "exchanges": ["HOSE", "HNX", "UPCOM"],
                "count": total_stocks,
                "excluded_funds": sum(
                    str(stock.get("instrument_type") or "STOCK").upper() != "STOCK"
                    for stock in stock_records
                ),
            },
        },
        "data_quality": {
            "status": "VERIFIED" if total_stocks >= 500 else "DEGRADED",
            "warnings": [
                "Du lieu heatmap la anh chup bang gia gan nhat; khong dai dien truc tiep cho giao dich khoi ngoai, tu doanh hoac to chuc."
            ],
        },
    }


def fetch_market_heatmap_data(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Fetches market board quote data for target symbols from the direct adapter,
    organizes into ICB sector hierarchy, computes market cap & trading value,
    and returns treemap payload.

    [Task 2 - Snapshot 15h10]:
      - If past 15:10 on a weekday OR weekend AND DB already has FROZEN snapshot for today -> RETURN DB snapshot (NO API call)
      - After 15:10 if we DO run the API -> persist result to DB as frozen (saves money/API quota for all later users today)
      - Automatic cleanup retains 20 close snapshots for robust quant baselines;
        weekly reporting still consumes only the latest 5 compatible sessions.

    [Task 3 - Market Closed]: adds `market_closed` + `snapshot_frozen` flags so UI can switch display.
    """
    global _HEATMAP_CACHE

    # Init DB (once per process)
    init_db_snapshot()

    current_time = time.time()
    now_dt = get_vn_now()
    today_str = now_dt.strftime("%Y-%m-%d")
    market_session = get_market_session(now_dt)
    overall_started_at = time.time()
    _debug_report("E", "heatmap_engine.py:325", "Heatmap fetch entered", {
        "force_refresh": force_refresh,
        "today": today_str,
        "weekend_mode": now_dt.weekday() >= 5,
        "market_open": is_market_open_time(),
    })

    # A frozen close snapshot is immutable. Refresh requests are deliberately
    # ignored outside a live trading day to protect market-data quota.
    read_only_phase = market_session["phase"] in {"WEEKEND", "HOLIDAY", "PRE_OPEN", "CLOSED"}
    if read_only_phase and not force_refresh:
        db_snap = get_latest_snapshot()
        if db_snap is not None and db_snap.get("snapshot_frozen"):
            snap_date = (db_snap.get("data_lineage", {}) or {}).get("latest_trading_date") or db_snap.get("trading_date")
            is_today_snapshot = (snap_date == today_str)
            is_valid_weekend_snapshot = (now_dt.weekday() >= 5 and snap_date)
            if is_today_snapshot or is_valid_weekend_snapshot:
                _debug_report("C", "heatmap_engine.py:351", "Serving heatmap from DB snapshot", {
                    "today": today_str,
                    "market_phase": market_session["phase"],
                    "sector_count": len(db_snap.get("sectors", [])),
                })
                _HEATMAP_CACHE["data"] = db_snap
                _HEATMAP_CACHE["timestamp"] = current_time
                _HEATMAP_CACHE["snapshot_date"] = snap_date
                _HEATMAP_CACHE["snapshot_frozen"] = True
                db_snap["market_closed"] = True
                db_snap["is_market_open"] = False
                db_snap["snapshot_frozen"] = True
                db_snap["market_session"] = market_session
                db_snap["served_from"] = "SQLITE_CLOSE_SNAPSHOT"
                return db_snap

    # ---- Standard in-memory cache logic (5s real-time reset) ----
    cache_ttl = 5

    if _HEATMAP_CACHE["data"] and not should_reset_15h10(_HEATMAP_CACHE["timestamp"]) and not force_refresh:
        if (current_time - _HEATMAP_CACHE["timestamp"]) < cache_ttl:
            data = _HEATMAP_CACHE["data"]
            data["market_closed"] = is_past_close_or_weekend()
            data["snapshot_frozen"] = _HEATMAP_CACHE.get("snapshot_frozen", False)
            _debug_report("C", "heatmap_engine.py:366", "Serving heatmap from in-memory cache", {
                "cache_ttl": cache_ttl,
                "sector_count": len(data.get("sectors", [])),
            })
            return data

    try:
        # PART 1 — Real-time full market coverage (500-800 tickers, HOSE/HNX/UPCOM)
        stock_records, board_source, board_fetched_at, requested_symbols = _collect_market_board()

        # Group by Sector — supports multi-membership: a stock is placed in
        # every sector listed in its `sector_memberships` array (per
        # sieucophieu.vn/bang-dien grouping). Each placement contributes to
        # that sector's market-cap and trading-value totals so the sector
        # % change matches sieucophieu's market-cap-weighted calculation.
        visual_stock_records = [stock for stock in stock_records if _is_quant_stock(stock)]
        sectors_list = _group_stocks_by_sector(visual_stock_records)

        payload = _assemble_heatmap_payload(
            stock_records,
            sectors_list,
            board_source,
            board_fetched_at,
            market_session,
            now_dt,
            requested_symbols=requested_symbols,
        )
        payload = _apply_heatmap_universe_contract(payload)

        # Outside a live trading date, one final fetch is persisted under the
        # source trading date. All later users receive SQLite only.
        latest_trade_date = payload["data_lineage"].get("latest_trading_date") or today_str
        should_freeze = read_only_phase or (
            market_session["is_trading_day"] and now_dt.time() >= HEATMAP_FINAL_SNAPSHOT_TIME
        )
        payload["snapshot_frozen"] = False
        # Invariant: the existing frozen row for `latest_trade_date` is never
        # overwritten by an unfrozen upsert. `save_snapshot_for_date`'s
        # ON CONFLICT clause preserves frozen=1 rows when the incoming payload
        # is frozen=0, so repeated holiday/weekend traffic is safe and cannot
        # bump the `created_at` timestamp of an already-frozen snapshot.
        if should_freeze:
            try:
                save_snapshot_for_date(latest_trade_date, payload, frozen=True)
                payload["snapshot_frozen"] = True
                payload["served_from"] = "LIVE_MARKET_ADAPTER_THEN_FROZEN"
            except Exception:
                pass

        _HEATMAP_CACHE["data"] = payload
        _HEATMAP_CACHE["timestamp"] = current_time
        _HEATMAP_CACHE["snapshot_date"] = today_str
        _HEATMAP_CACHE["snapshot_frozen"] = payload["snapshot_frozen"]

        return payload

    except Exception as e:
        _debug_report("A", "heatmap_engine.py:539", "Heatmap build failed", {
            "error": str(e),
            "total_elapsed_ms": round((time.time() - overall_started_at) * 1000, 2),
        })
        print(f"Error building heatmap data: {e}")
        # --- Fallback: try DB snapshot when API is down today
        db_snap = get_latest_snapshot()
        if db_snap is not None:
            _HEATMAP_CACHE["data"] = db_snap
            _HEATMAP_CACHE["timestamp"] = current_time
            _HEATMAP_CACHE["snapshot_date"] = today_str
            _HEATMAP_CACHE["snapshot_frozen"] = True
            db_snap["market_closed"] = is_past_close_or_weekend()
            db_snap["snapshot_frozen"] = True
            return db_snap
        if _HEATMAP_CACHE["data"]:
            return _HEATMAP_CACHE["data"]
        raise e

def _parse_json_object(content: str) -> Dict[str, Any]:
    cleaned = str(content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        cleaned_substr = cleaned[start:end + 1]
        try:
            value = json.loads(cleaned_substr)
        except json.JSONDecodeError:
            # Repair common LLM JSON syntax errors:
            # 1. Trailing commas before closing brackets/braces
            repaired = re.sub(r",\s*([}\]])", r"\1", cleaned_substr)
            # 2. Missing quotes on property key names like "scenarios: -> "scenarios":
            repaired = re.sub(r'("[\w_]+)\s*:', r'\1":', repaired)
            # 3. Control characters inside strings
            repaired = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", repaired)
            try:
                value = json.loads(repaired)
            except json.JSONDecodeError:
                raise
    if not isinstance(value, dict):
        raise ValueError("DeepSeek response is not a JSON object")
    return value


def _allocation_guardrail(temperature: float) -> Dict[str, Any]:
    if temperature >= 72:
        equity_band = "60-75%"
    elif temperature >= 58:
        equity_band = "45-60%"
    elif temperature >= 42:
        equity_band = "30-45%"
    else:
        equity_band = "15-30%"
    return {
        "reference_equity_band": equity_band,
        "position_rule": "Mỗi vị thế tối đa 5-8%; chỉ tăng khi độ rộng và thanh khoản tiếp tục xác nhận.",
        "scope": "Khung tham khảo quản trị rủi ro, không phải phân bổ cá nhân hóa.",
        "checklist_1": "Đọc kỹ báo cáo và hiểu rõ ngành bạn đang quan tâm",
        "checklist_2": "Kiểm tra thanh khoản: khối lượng giao dịch cao hơn trung bình 20 phiên",
        "checklist_3": "Xác nhận xu hướng: giá đang trên MA20 hoặc đang pullback về hỗ trợ",
        "checklist_4": "Không all-in: chia vốn tối đa 20-30% cho một vị thế",
        "checklist_5": "Đặt stop-loss ngay từ đầu, không hold hy vọng",
    }


def _build_quant_only_heatmap_insight(heatmap_data: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Return a transparent deterministic brief when the optional LLM is unavailable."""
    quant = heatmap_data.get("quant_snapshot") or {}
    snapshot_id = quant.get("snapshot_id") or "unknown"
    sectors = heatmap_data.get("sectors", [])
    sector_matrix = []
    for item in quant.get("sector_leaders", []):
        sector_name = item.get("sector") or "Không xác định"
        sector_matrix.append({
            **item,
            "status": _score_label(float(item.get("flow_score", 0))),
            "ai_note": (
                f"{sector_name}: điểm dòng tiền {float(item.get('flow_score', 0)):.1f}, "
                f"độ rộng {float(item.get('breadth_pct', 0)):.1f}%; cần xác nhận thêm bằng giá và thanh khoản."
            ),
        })
    radar_watchlist = []
    for item in quant.get("watchlist", []):
        radar_watchlist.append({
            **item,
            "ai_note": (
                f"{item.get('symbol')}: biến động {float(item.get('change_pct', 0)):+.2f}% "
                f"và điểm dòng tiền {float(item.get('flow_score', 0)):.1f}; đây là radar, không phải khuyến nghị mua."
            ),
            "validation_rule": "Chỉ giữ trong radar nếu giá còn trên tham chiếu và thanh khoản không suy giảm rõ rệt.",
        })
    summary = heatmap_data.get("summary") or {}
    breadth = float(quant.get("breadth_pct") or 0)
    temperature = float(quant.get("market_temperature") or 0)
    regime = str(quant.get("market_regime") or "PHAN_HOA")
    report = {
        "report_version": "lp-quant-market-radar-fallback-2.0",
        "snapshot_id": snapshot_id,
        "generated_at": get_vn_now().strftime("%d/%m/%Y %H:%M:%S"),
        "market_temperature": temperature,
        "market_regime": regime,
        "headline": f"Quant snapshot: thị trường {regime.replace('_', ' ').lower()}, nhiệt độ {temperature:.1f}/100.",
        "market_read": (
            f"Dữ liệu phiên cho thấy {summary.get('advances', 0)} mã tăng, {summary.get('declines', 0)} mã giảm "
            f"và độ rộng tăng {breadth:.1f}%. Đây là diễn giải định lượng tự động."
        ),
        "money_flow_matrix": {
            "liquidity_concentration": (
                f"Top 10 mã chiếm {float(quant.get('top10_liquidity_share_pct') or 0):.1f}% GTGD khớp lệnh; "
                f"quy mô hiệu dụng {float(quant.get('effective_stock_count') or 0):.1f} mã, trạng thái {quant.get('concentration_state', 'KHONG_DU_DU_LIEU')}."
            ),
            "market_breadth_eval": (
                f"A/D {'∞' if quant.get('advance_decline_state') == 'NO_DECLINES' else format(float(quant.get('advance_decline_ratio') or 0), '.2f')}; "
                f"{float(quant.get('advance_share_active_pct') or 0):.1f}% mã có giao dịch tăng và "
                f"{float(quant.get('directional_participation_pct') or 0):.1f}% có hướng."
            ),
            "participation_quality": f"Độ tin cậy nhiệt: {quant.get('heat_confidence', 'THAP')}; tỷ lệ mã có giao dịch {float(quant.get('active_ratio_pct') or 0):.1f}%.",
            "scope_warning": "Chỉ là proxy giá-thanh khoản; không có dữ liệu khối ngoại, tự doanh hay lệnh của tổ chức.",
        },
        "evidence": {
            "breadth_pct": quant.get("breadth_pct"),
            "advance_decline_ratio": quant.get("advance_decline_ratio"),
            "active_ratio_pct": quant.get("active_ratio_pct"),
            "advance_share_active_pct": quant.get("advance_share_active_pct"),
            "directional_participation_pct": quant.get("directional_participation_pct"),
            "net_breadth_pct": quant.get("net_breadth_pct"),
            "market_cap_weighted_change_pct": quant.get("market_cap_weighted_change_pct"),
            "top10_liquidity_share_pct": quant.get("top10_liquidity_share_pct"),
            "liquidity_hhi": quant.get("liquidity_hhi"),
            "effective_stock_count": quant.get("effective_stock_count"),
            "concentration_state": quant.get("concentration_state"),
            "data_lineage": heatmap_data.get("data_lineage", {}),
        },
        "sector_momentum_matrix": sector_matrix,
        "radar_watchlist": radar_watchlist,
        "risk_radar": [
            "Độ rộng thấp hoặc A/D suy yếu sẽ làm giảm độ tin cậy của tín hiệu dòng tiền.",
            "Thanh khoản tập trung vào ít mã có thể làm chỉ số cải thiện nhưng thị trường chung chưa lan tỏa.",
            "Không suy luận dòng tiền tổ chức khi snapshot không có dữ liệu lệnh hoặc giao dịch nhà đầu tư theo nhóm.",
        ],
        "scenarios": {
            "positive_confirmation": "Độ rộng tăng và thanh khoản duy trì hoặc mở rộng trong các phiên kế tiếp.",
            "positive_action": "Có thể tăng tỷ trọng cổ phiếu lên 60-70%, ưu tiên ngành dẫn dắt.",
            "base_case": "Thị trường tiếp tục phân hóa; ưu tiên theo dõi các mã có giá và thanh khoản cùng xác nhận.",
            "base_action": "Giữ tỷ trọng 40-50%, chờ tín hiệu rõ hơn từ thị trường.",
            "risk_trigger": "A/D giảm dưới 1 và thanh khoản suy yếu đồng thời với nhóm dẫn dắt.",
            "risk_action": "Giảm tỷ trọng xuống 20-30%, bảo toàn vốn và chờ thị trường ổn định.",
        },
        "capital_allocation_guardrail": _allocation_guardrail(temperature),
        "ai_engine_source": "LP Quant snapshot (fallback định lượng)",
        "token_usage": {},
        "disclaimer": "Hệ thống AI chưa được kích hoạt nên báo cáo hiện tại sử dụng phân tích định lượng chuẩn. Đây là công cụ hỗ trợ phân tích, không phải tư vấn đầu tư cá nhân hóa.",
        "configuration_notice": reason,
    }
    return report


def generate_deepseek_heatmap_insight(heatmap_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ask DeepSeek to explain the deterministic snapshot with historical context and anomaly detection."""
    global _AI_INSIGHT_CACHE

    quant = heatmap_data.get("quant_snapshot") or {}
    snapshot_id = quant.get("snapshot_id")
    if not snapshot_id:
        raise RuntimeError("Heatmap chưa có Quant snapshot hợp lệ.")
    if heatmap_data.get("data_quality", {}).get("status") == "DEGRADED":
        raise RuntimeError("Độ phủ dữ liệu chưa đạt ngưỡng để tạo báo cáo AI.")

    current_time = time.time()
    if (
        _AI_INSIGHT_CACHE.get("report")
        and _AI_INSIGHT_CACHE.get("snapshot_id") == snapshot_id
        and current_time - float(_AI_INSIGHT_CACHE.get("timestamp") or 0) < 600
    ):
        return _AI_INSIGHT_CACHE["report"]

    deepseek_key = get_env_api_key("DEEPSEEK_API_KEY")

    # Lấy historical context và anomalies
    hist_context = build_historical_snapshot_context()
    anomalies = hist_context.get("anomalies", []) if hist_context.get("available") else []
    historical = hist_context.get("historical_context", {}) if hist_context.get("available") else {}

    if not deepseek_key:
        return _build_quant_only_heatmap_insight_v2(
            heatmap_data,
            "Hệ thống AI chưa được cấu hình; hãy kiểm tra API key trong Environment của Render để bật tính năng phân tích AI.",
            historical,
            anomalies,
        )

    sectors_by_name = {s.get("name"): s for s in heatmap_data.get("sectors", [])}
    sector_evidence = []
    for leader in quant.get("sector_leaders", []):
        sector_name = leader.get("sector", "")
        sector = sectors_by_name.get(sector_name, {})
        key_tickers = sorted(
            sector.get("stocks", []),
            key=lambda stock: (float(stock.get("flow_score", 0)), float(stock.get("trading_value", 0))),
            reverse=True,
        )[:3]

        # Lấy trend info từ historical context
        sector_trend = historical.get("sector_trends", {}).get(sector_name, {})

        sector_evidence.append({
            **leader,
            "key_tickers": [stock.get("symbol") for stock in key_tickers],
            "momentum_trend": sector_trend.get("trend", "KHONG_CO_DU_LIEU"),
            "trend_label": sector_trend.get("trend_label", ""),
            "change_summary": sector_trend.get("change_summary", ""),
        })

    # Xây dựng LLM input với historical context
    llm_input = {
        "as_of": heatmap_data.get("data_lineage", {}).get("latest_trading_date") or heatmap_data.get("timestamp"),
        "summary": heatmap_data.get("summary", {}),
        "quant": {
            key: quant.get(key) for key in [
                "market_temperature", "market_regime", "breadth_pct", "advance_decline_ratio",
                "active_ratio_pct", "market_cap_weighted_change_pct", "top10_liquidity_share_pct",
                "advance_share_active_pct", "directional_participation_pct", "net_breadth_pct",
                "heat_confidence", "liquidity_hhi", "effective_stock_count", "concentration_state",
            ]
        },
        "historical_context": historical if historical.get("available") else None,
        "sector_evidence": sector_evidence,
        "watchlist": quant.get("watchlist", []),
        "anomalies": anomalies if anomalies else None,
    }

    prompt = f"""Bạn là chuyên gia phân tích thị trường Việt Nam của Lộc Phát Securities.

NHIỆM VỤ: Diễn giải dữ liệu heatmap cho nhà đầu tư cá nhân một cách rõ ràng, có căn cứ số liệu. VIẾT GẦN GŨI, DỄ HIỂU, CÓ HÀNH ĐỘNG CỤ THỂ.

INPUT CÓ:
- Snapshot hiện tại (nhiệt độ, độ rộng, thanh khoản)
- Dữ liệu lịch sử 5 ngày gần nhất (xu hướng)
- Danh sách ngành với điểm dòng tiền và momentum trend
- Danh sách anomalies (bất thường thanh khoản/độ rộng)
- Radar mã (top tín hiệu với flow_score và change_pct)

QUY TẮC BẮT BUỘC:
1. SO SÁNH với ngày hôm qua và 5 ngày avg khi có dữ liệu lịch sử
2. XÁC ĐỊNH sector nào đang "breakout" hoặc "fade"
3. CẢNH BÁO nếu có anomaly (bất thường thanh khoản, độ rộng spike)
4. ĐƯA RA 3 KỊCH BẢN với HÀNH ĐỘNG CỤ THỂ CHO NHÀ ĐẦU TƯ CÁ NHÂN
5. VỚI MÃ TRONG RADAR: đưa ra "entry_zone" (vùng mua tiềm năng), "signal_type" (buy/sell/neutral), "signal_label" (nhãn ngắn)
6. Mỗi nhận định phải CHỈ RA SỐ LIỆU CỤ THỂ từ input
7. KHÔNG khẳng định giao dịch khối ngoại, tự doanh, tổ chức
8. KHÔNG bịa stop-loss, giá mục tiêu cụ thể - chỉ gợi ý zone hoặc nguyên tắc

YÊU CẦU VIẾT:
- Tiếng Việt NGẮN, RÕ, GẦN GŨI - như đang nói chuyện với nhà đầu tư cá nhân
- Ngành: gợi ý ngành nào ĐÁNG CHÚ Ý tuần này, tại sao
- Radar: gợi ý mã nào CÓ THỂ theo dõi, điểm dòng tiền bao nhiêu, xu hướng thế nào
- Kịch bản: NÊU RÕ HÀNH ĐỘNG cụ thể cho nhà đầu tư
- Checklist: gợi ý 5 điều nhà đầu tư nên kiểm tra trước khi vào lệnh

Trả JSON hợp lệ, không markdown, theo đúng schema:
{{
  "headline": "Tiêu đề ngắn gọn, có số liệu chính (dưới 80 ký tự)",
  "market_read": "Mô tả tình hình thị trường hôm nay bằng ngôn ngữ gần gũi (dưới 300 ký tự)",
  "trend_read": "Xu hướng so với 5 ngày qua - ngắn gọn (dưới 200 ký tự)",
  "liquidity_read": "Thanh khoản tập trung hay phân tán - dễ hiểu (dưới 200 ký tự)",
  "breadth_read": "Độ rộng thị trường - dễ hiểu (dưới 200 ký tự)",
  "anomaly_alerts": ["Cảnh báo 1 ngắn gọn", "Cảnh báo 2"],
  "sector_notes": {{"TÊN NGÀNH": "Ghi chú ngắn gọn về ngành này hôm nay, có số liệu cụ thể"}},
  "watchlist_notes": {{
    "MÃ_CP_1": {{
      "signal_type": "buy|sell|neutral",
      "signal_label": "Nhãn ngắn: Theo dõi mua|Xác nhận tăng|Thận trọng|Theo dõi",
      "note": "Ghi chú ngắn về mã này, điểm dòng tiền bao nhiêu, vì sao có trong radar"
    }}
  }},
  "risk_radar": ["Cảnh báo rủi ro 1 ngắn gọn", "Cảnh báo rủi ro 2", "Cảnh báo rủi ro 3"],
  "scenarios": {{
    "positive_confirmation": "Kịch bản lạc quan: điều kiện nào xảy ra?",
    "positive_action": "Hành động cụ thể: ví dụ 'Có thể tăng tỷ trọng cổ phiếu lên 60-70%, ưu tiên ngành X'",
    "base_case": "Kịch bản cơ sở: thị trường đi sideways hay như thế nào?",
    "base_action": "Hành động: ví dụ 'Giữ tỷ trọng 40-50%, chờ tín hiệu rõ hơn'",
    "risk_trigger": "Kịch bản thận trọng: tín hiệu nào cảnh báo rủi ro?",
    "risk_action": "Hành động: ví dụ 'Giảm tỷ trọng xuống 20-30%, chờ thị trường ổn định'"
  }},
  "checklist": [
    "Điều nhà đầu tư nên kiểm tra 1",
    "Điều nhà đầu tư nên kiểm tra 2",
    "Điều nhà đầu tư nên kiểm tra 3",
    "Điều nhà đầu tư nên kiểm tra 4",
    "Điều nhà đầu tư nên kiểm tra 5"
  ]
}}

INPUT:
{json.dumps(llm_input, ensure_ascii=False, separators=(',', ':'))}
"""

    try:
        from deepseek_client import call_deepseek_json
        narrative = call_deepseek_json(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Bạn diễn giải dữ liệu định lượng có historical context. So sánh với avg 5 ngày khi có. Tuyệt đối không thêm dữ kiện ngoài input.",
            temperature=0.15,
            max_tokens=2500,
            enable_thinking=False,
            timeout=40.0,
        )
    except Exception as err:
        logger.warning(f"Lỗi gọi/parse DeepSeek AI insight: {err}")
        return _build_quant_only_heatmap_insight_v2(
            heatmap_data,
            f"Không thể phản hồi từ DeepSeek AI ({str(err)}). Hiển thị chế độ dữ liệu định lượng thuần.",
            historical,
            anomalies,
        )
    sector_notes = narrative.get("sector_notes") if isinstance(narrative.get("sector_notes"), dict) else {}
    watchlist_notes = narrative.get("watchlist_notes") if isinstance(narrative.get("watchlist_notes"), dict) else {}
    risk_radar = narrative.get("risk_radar") if isinstance(narrative.get("risk_radar"), list) else []
    scenarios = narrative.get("scenarios") if isinstance(narrative.get("scenarios"), dict) else {}
    anomaly_alerts = narrative.get("anomaly_alerts") if isinstance(narrative.get("anomaly_alerts"), list) else []
    checklist = narrative.get("checklist") if isinstance(narrative.get("checklist"), list) else []

    sector_matrix = []
    for item in sector_evidence:
        sector_matrix.append({
            **item,
            "status": _score_label(float(item.get("flow_score", 0))),
            "ai_note": str(sector_notes.get(item.get("sector"), ""))[:500],
        })

    radar_watchlist = []
    for item in quant.get("watchlist", []):
        symbol = item.get("symbol", "")
        stock_note = watchlist_notes.get(symbol, {}) if isinstance(watchlist_notes, dict) else {}
        signal_type = stock_note.get("signal_type", "neutral") if isinstance(stock_note, dict) else "neutral"
        signal_label = stock_note.get("signal_label", "Theo dõi") if isinstance(stock_note, dict) else "Theo dõi"
        note = stock_note.get("note", "") if isinstance(stock_note, dict) else str(stock_note)

        radar_watchlist.append({
            **item,
            "signal_type": signal_type,
            "signal_label": signal_label,
            "ai_note": str(note)[:500] or str(watchlist_notes.get(symbol, ""))[:500],
            "validation_rule": "Chỉ radar, không phải khuyến nghị. Nhà đầu tư tự nghiên cứu trước khi quyết định.",
        })

    report = {
        "report_version": "lp-ai-market-radar-4.0",
        "snapshot_id": snapshot_id,
        "generated_at": get_vn_now().strftime("%d/%m/%Y %H:%M:%S"),
        "market_temperature": quant.get("market_temperature"),
        "market_regime": quant.get("market_regime"),
        "headline": str(narrative.get("headline") or "Phan tich anh chup dong tien thi truong")[:300],
        "market_read": str(narrative.get("market_read") or "")[:1200],
        "trend_read": str(narrative.get("trend_read") or "")[:800],
        "money_flow_matrix": {
            "liquidity_concentration": str(narrative.get("liquidity_read") or "")[:1000],
            "market_breadth_eval": str(narrative.get("breadth_read") or "")[:1000],
            "scope_warning": "Chỉ là proxy giá-thanh khoản; không có dữ liệu khối ngoại, tự doanh hay lệnh của tổ chức.",
        },
        "evidence": {
            "breadth_pct": quant.get("breadth_pct"),
            "advance_decline_ratio": quant.get("advance_decline_ratio"),
            "active_ratio_pct": quant.get("active_ratio_pct"),
            "advance_share_active_pct": quant.get("advance_share_active_pct"),
            "directional_participation_pct": quant.get("directional_participation_pct"),
            "net_breadth_pct": quant.get("net_breadth_pct"),
            "market_cap_weighted_change_pct": quant.get("market_cap_weighted_change_pct"),
            "top10_liquidity_share_pct": quant.get("top10_liquidity_share_pct"),
            "liquidity_hhi": quant.get("liquidity_hhi"),
            "effective_stock_count": quant.get("effective_stock_count"),
            "concentration_state": quant.get("concentration_state"),
            "data_lineage": heatmap_data.get("data_lineage", {}),
        },
        "historical_context": {
            "available": historical.get("available", False),
            "market_summary": historical.get("market_summary", {}) if historical else {},
            "top_momentum_sectors": historical.get("top_momentum_sectors", []) if historical else [],
            "weak_momentum_sectors": historical.get("weak_momentum_sectors", []) if historical else [],
            "insight": historical.get("insight", "") if historical else "",
        } if historical else {"available": False},
        "anomalies": anomalies[:5],
        "ai_anomaly_notes": anomaly_alerts[:3],
        "sector_momentum_matrix": sector_matrix,
        "radar_watchlist": radar_watchlist,
        "risk_radar": [str(item)[:500] for item in risk_radar[:4]],
        "scenarios": {
            "positive_confirmation": str(scenarios.get("positive_confirmation") or "Thị trường tiếp tục tăng với thanh khoản mở rộng.")[:700],
            "positive_action": str(scenarios.get("positive_action") or "Có thể tăng tỷ trọng cổ phiếu, ưu tiên ngành dẫn dắt.")[:300],
            "base_case": str(scenarios.get("base_case") or "Thị trường đi ngang hoặc phân hóa.")[:700],
            "base_action": str(scenarios.get("base_action") or "Giữ tỷ trọng hiện tại, chờ tín hiệu rõ hơn.")[:300],
            "risk_trigger": str(scenarios.get("risk_trigger") or "Thanh khoản suy giảm đồng thời với nhóm dẫn dắt.")[:700],
            "risk_action": str(scenarios.get("risk_action") or "Giảm tỷ trọng, bảo toàn vốn.")[:300],
        },
        "capital_allocation_guardrail": {
            **_allocation_guardrail(float(quant.get("market_temperature") or 0)),
            "checklist_1": str(checklist[0] if len(checklist) > 0 else "Đọc kỹ báo cáo và hiểu rõ ngành bạn đang quan tâm")[:200],
            "checklist_2": str(checklist[1] if len(checklist) > 1 else "Kiểm tra thanh khoản: khối lượng giao dịch cao hơn trung bình 20 phiên")[:200],
            "checklist_3": str(checklist[2] if len(checklist) > 2 else "Xác nhận xu hướng: giá đang trên MA20 hoặc đang pullback về hỗ trợ")[:200],
            "checklist_4": str(checklist[3] if len(checklist) > 3 else "Không all-in: chia vốn tối đa 20-30% cho một vị thế")[:200],
            "checklist_5": str(checklist[4] if len(checklist) > 4 else "Đặt stop-loss ngay từ đầu, không hold hy vọng")[:200],
        },
        "ai_engine_source": "Lộc Phát AI Engine v4.0 (DeepSeek-V4), grounded on LP Quant snapshot + compatible 5-day history",
        "token_usage": narrative.get("_deepseek_meta", {}),
        "disclaimer": "Đây là công cụ hỗ trợ phân tích dựa trên dữ liệu và mô hình AI, không phải tư vấn đầu tư cá nhân hóa. Nhà đầu tư tự chịu trách nhiệm với quyết định của mình.",
    }
    _AI_INSIGHT_CACHE.update({"report": report, "timestamp": current_time, "snapshot_id": snapshot_id})
    return report


def _build_quant_only_heatmap_insight_v2(
    heatmap_data: Dict[str, Any],
    reason: str,
    historical: Dict[str, Any],
    anomalies: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a deterministic brief with historical context when LLM is unavailable."""
    quant = heatmap_data.get("quant_snapshot") or {}
    snapshot_id = quant.get("snapshot_id") or "unknown"
    sectors = heatmap_data.get("sectors", [])

    sector_matrix = []
    for item in quant.get("sector_leaders", []):
        sector_name = item.get("sector") or "Không xác định"
        sector_trend = historical.get("sector_trends", {}).get(sector_name, {}) if historical else {}

        trend_label = sector_trend.get("trend_label", "")
        trend_note = f" [{trend_label}]" if trend_label else ""

        sector_matrix.append({
            **item,
            "momentum_trend": sector_trend.get("trend", "KHONG_CO_DU_LIEU"),
            "trend_label": trend_label,
            "status": _score_label(float(item.get("flow_score", 0))),
            "ai_note": (
                f"{sector_name}: điểm dòng tiền {float(item.get('flow_score', 0)):.1f}, "
                f"độ rộng {float(item.get('breadth_pct', 0)):.1f}%.{trend_note}"
            ),
        })

    radar_watchlist = []
    for item in quant.get("watchlist", []):
        radar_watchlist.append({
            **item,
            "ai_note": (
                f"{item.get('symbol')}: biến động {float(item.get('change_pct', 0)):+.2f}% "
                f"và điểm dòng tiền {float(item.get('flow_score', 0)):.1f}; đây là radar, không phải khuyến nghị mua."
            ),
            "validation_rule": "Chỉ giữ trong radar nếu giá còn trên tham chiếu và thanh khoản không suy giảm rõ rệt.",
        })

    summary = heatmap_data.get("summary") or {}
    breadth = float(quant.get("breadth_pct") or 0)
    temperature = float(quant.get("market_temperature") or 0)
    regime = str(quant.get("market_regime") or "PHAN_HOA")

    hist_note = ""
    if historical and historical.get("available"):
        mkt = historical.get("market_summary", {})
        temp_trend = mkt.get("temperature_trend", "")
        breadth_trend = mkt.get("breadth_trend", "")
        hist_note = f" | Xu hướng 5 ngày: nhiệt {temp_trend}, độ rộng {breadth_trend}"

    report = {
        "report_version": "lp-quant-market-radar-fallback-3.0",
        "snapshot_id": snapshot_id,
        "generated_at": get_vn_now().strftime("%d/%m/%Y %H:%M:%S"),
        "market_temperature": temperature,
        "market_regime": regime,
        "headline": f"Quant snapshot: thị trường {regime.replace('_', ' ').lower()}, nhiệt {temperature:.1f}/100.{hist_note}",
        "market_read": (
            f"Dữ liệu phiên cho thấy {summary.get('advances', 0)} mã tăng, {summary.get('declines', 0)} mã giảm "
            f"và độ rộng tăng {breadth:.1f}%. Đây là diễn giải định lượng tự động."
        ),
        "trend_read": historical.get("insight", "Chưa có dữ liệu lịch sử để phân tích xu hướng.") if historical else "Chưa có dữ liệu lịch sử.",
        "money_flow_matrix": {
            "liquidity_concentration": (
                f"Top 10 mã chiếm {float(quant.get('top10_liquidity_share_pct') or 0):.1f}% GTGD khớp lệnh; "
                f"quy mô hiệu dụng {float(quant.get('effective_stock_count') or 0):.1f} mã."
            ),
            "market_breadth_eval": (
                f"A/D {'∞' if quant.get('advance_decline_state') == 'NO_DECLINES' else format(float(quant.get('advance_decline_ratio') or 0), '.2f')}; "
                f"{float(quant.get('advance_share_active_pct') or 0):.1f}% mã có giao dịch tăng, "
                f"mức tham gia có hướng {float(quant.get('directional_participation_pct') or 0):.1f}%."
            ),
            "participation_quality": f"Độ tin cậy nhiệt: {quant.get('heat_confidence', 'THAP')}; hoạt động {float(quant.get('active_ratio_pct') or 0):.1f}%.",
            "scope_warning": "Chỉ là proxy giá-thanh khoản; không có dữ liệu khối ngoại, tự doanh hay lệnh của tổ chức.",
        },
        "evidence": {
            "breadth_pct": quant.get("breadth_pct"),
            "advance_decline_ratio": quant.get("advance_decline_ratio"),
            "active_ratio_pct": quant.get("active_ratio_pct"),
            "advance_share_active_pct": quant.get("advance_share_active_pct"),
            "directional_participation_pct": quant.get("directional_participation_pct"),
            "net_breadth_pct": quant.get("net_breadth_pct"),
            "market_cap_weighted_change_pct": quant.get("market_cap_weighted_change_pct"),
            "top10_liquidity_share_pct": quant.get("top10_liquidity_share_pct"),
            "liquidity_hhi": quant.get("liquidity_hhi"),
            "effective_stock_count": quant.get("effective_stock_count"),
            "concentration_state": quant.get("concentration_state"),
            "data_lineage": heatmap_data.get("data_lineage", {}),
        },
        "historical_context": {
            "available": historical.get("available", False) if historical else False,
            "market_summary": historical.get("market_summary", {}) if historical else {},
            "top_momentum_sectors": historical.get("top_momentum_sectors", []) if historical else [],
            "weak_momentum_sectors": historical.get("weak_momentum_sectors", []) if historical else [],
        } if historical else {"available": False},
        "anomalies": anomalies[:5],
        "ai_anomaly_notes": [],
        "sector_momentum_matrix": sector_matrix,
        "radar_watchlist": radar_watchlist,
        "risk_radar": [
            "Độ rộng thấp hoặc A/D suy yếu sẽ làm giảm độ tin cậy của tín hiệu dòng tiền.",
            "Thanh khoản tập trung vào ít mã có thể làm chỉ số cải thiện nhưng thị trường chung chưa lan tỏa.",
            "Không suy luận dòng tiền tổ chức khi snapshot không có dữ liệu lệnh hoặc giao dịch nhà đầu tư theo nhóm.",
        ],
        "scenarios": {
            "positive_confirmation": "Độ rộng tăng và thanh khoản duy trì hoặc mở rộng trong các phiên kế tiếp.",
            "positive_action": "Có thể tăng tỷ trọng cổ phiếu lên 60-70%, ưu tiên ngành dẫn dắt.",
            "base_case": "Thị trường tiếp tục phân hóa; ưu tiên theo dõi các mã có giá và thanh khoản cùng xác nhận.",
            "base_action": "Giữ tỷ trọng 40-50%, chờ tín hiệu rõ hơn từ thị trường.",
            "risk_trigger": "A/D giảm dưới 1 và thanh khoản suy yếu đồng thời với nhóm dẫn dắt.",
            "risk_action": "Giảm tỷ trọng xuống 20-30%, bảo toàn vốn và chờ thị trường ổn định.",
        },
        "capital_allocation_guardrail": _allocation_guardrail(temperature),
        "ai_engine_source": "LP Quant snapshot v4.0 (fallback định lượng + compatible historical context)",
        "token_usage": {},
        "disclaimer": "Hệ thống AI chưa được kích hoạt nên báo cáo hiện tại sử dụng phân tích định lượng chuẩn. Đây là công cụ hỗ trợ phân tích, không phải tư vấn đầu tư cá nhân hóa.",
        "configuration_notice": reason,
    }
    return report


def generate_weekly_analysis() -> Dict[str, Any]:
    """
    Generate weekly trading analysis from the last 5 frozen snapshots.
    Called after market close on Friday (15:00+).
    """
    snapshots = [
        snapshot for snapshot in get_recent_snapshots(days=SNAPSHOT_RETENTION_DAYS)
        if (snapshot.get("quant_snapshot") or {}).get("model_version") == HEATMAP_MODEL_VERSION
    ][:WEEKLY_ANALYSIS_DAYS]

    if len(snapshots) < 5:
        raise RuntimeError(
            f"Chưa có đủ 5 ngày snapshot (hiện có {len(snapshots)} ngày). "
            "Hãy đảm bảo hệ thống chạy đều đặn trong tuần để lưu snapshot 15h10 mỗi ngày."
        )

    # Calculate week range
    dates = [s.get("trade_date") or s.get("data_lineage", {}).get("latest_trading_date") for s in snapshots]
    dates.sort()
    week_range = f"{dates[0]} → {dates[-1]}"

    # Aggregate data from all 5 days
    total_advances = sum(s.get("summary", {}).get("advances", 0) for s in snapshots)
    total_declines = sum(s.get("summary", {}).get("declines", 0) for s in snapshots)
    breadth_values = [
        float(s.get("quant_snapshot", {}).get("breadth_pct")) for s in snapshots
        if _is_finite_metric(s.get("quant_snapshot", {}).get("breadth_pct"))
    ]
    if len(breadth_values) != WEEKLY_ANALYSIS_DAYS:
        raise RuntimeError("Có snapshot không đủ mẫu tăng/giảm để tính độ rộng tuần.")
    temperature_values = [float(s.get("quant_snapshot", {}).get("market_temperature", 50)) for s in snapshots]
    avg_breadth = sum(breadth_values) / WEEKLY_ANALYSIS_DAYS
    avg_temperature = sum(temperature_values) / WEEKLY_ANALYSIS_DAYS

    # Sector performance across the week
    sector_changes = {}
    for snap in snapshots:
        for sector in snap.get("sectors", []):
            name = sector.get("name", "Unknown")
            if name not in sector_changes:
                sector_changes[name] = []
            flow = float(sector.get("flow_score", 50))
            sector_changes[name].append(flow)

    # Calculate average flow score change per sector
    sector_performance = []
    for name, flows in sector_changes.items():
        if len(flows) >= 2:
            change = flows[-1] - flows[0]
            sector_performance.append({
                "sector": name,
                "change_pct": change,
                "avg_flow": sum(flows) / len(flows),
                "final_flow": flows[-1]
            })

    sector_performance.sort(key=lambda x: x["change_pct"], reverse=True)

    # Daily breadth for each day
    day_names = ["T2", "T3", "T4", "T5", "T6"]
    daily_breadth = []
    for i, snap in enumerate(snapshots):
        date = snap.get("trade_date") or dates[i] if i < len(dates) else f"Ngày {i+1}"
        day_name = day_names[i] if i < len(day_names) else f"Ngày {i+1}"
        breadth = snap.get("quant_snapshot", {}).get("breadth_pct", 50)
        daily_breadth.append({
            "day": day_name,
            "date": date,
            "breadth_pct": breadth
        })

    # Market change calculation (first vs last day temperature)
    first_temp = snapshots[0].get("quant_snapshot", {}).get("market_temperature", 50)
    last_temp = snapshots[-1].get("quant_snapshot", {}).get("market_temperature", 50)
    market_change = last_temp - first_temp

    # DeepSeek analysis
    deepseek_key = get_env_api_key("DEEPSEEK_API_KEY")

    if not deepseek_key:
        return _build_weekly_quant_only_report(
            week_range, market_change, avg_breadth, avg_temperature,
            daily_breadth, sector_performance, snapshots
        )

    # Build context for DeepSeek
    context = {
        "week_range": week_range,
        "total_advances": total_advances,
        "total_declines": total_declines,
        "avg_breadth": avg_breadth,
        "market_change": market_change,
        "daily_breadth": daily_breadth,
        "top_sectors": sector_performance[:5],
        "weak_sectors": sector_performance[-3:] if len(sector_performance) >= 3 else [],
    }

    prompt = f"""Phân tích tuần giao dịch Việt Nam dựa trên dữ liệu định lượng:

**Khoảng thời gian:** {week_range}

**Tổng quan:**
- Mã tăng trong tuần: {total_advances}
- Mã giảm trong tuần: {total_declines}
- Nhiệt thị trường trung bình: {avg_temperature:.1f}/100
- Thay đổi nhiệt (T2→T6): {market_change:+.1f} điểm

**Độ rộng theo ngày:**
{chr(10).join(f"- {d['day']}: {d['breadth_pct']:.1f}%" for d in daily_breadth)}

**Top sectors (thay đổi điểm dòng tiền):**
{chr(10).join(f"- {s['sector']}: {s['change_pct']:+.1f} điểm" for s in sector_performance[:5])}

**Yếu sectors:**
{chr(10).join(f"- {s['sector']}: {s['change_pct']:+.1f} điểm" for s in sector_performance[-3:])}

Hãy trả về JSON với các trường:
- "headline": tiêu đề báo cáo tuần (dưới 100 ký tự)
- "summary": tóm tắt 2-3 câu về tuần giao dịch
- "money_flow_trend": mô tả xu hướng dòng tiền tuần
- "weekly_verdict": nhận định tổng thể (tích cực/trung lập/tiêu cực)
- "opportunities": cơ hội tuần sau (dưới 300 ký tự)
- "risks": rủi ro cần lưu ý (dưới 300 ký tự)
- "sector_rotation_note": ghi chú về sự xoay vòng ngành trong tuần

Response format: JSON object"""

    try:
        from deepseek_client import call_deepseek_json
        narrative = call_deepseek_json(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Bạn là chuyên gia phân tích thị trường chứng khoán Việt Nam. Phân tích ngắn gọn, thực tế, có căn cứ dữ liệu.",
            temperature=0.2,
            max_tokens=2000,
            enable_thinking=False,
            timeout=40.0,
        )

        return {
            "report_version": "lp-weekly-radar-1.0",
            "week_range": week_range,
            "market_change_pct": market_change,
            "avg_breadth": avg_breadth,
            "avg_temperature": avg_temperature,
            "headline": str(narrative.get("headline", "Phân tích tuần"))[:100],
            "summary": str(narrative.get("summary", ""))[:500],
            "daily_breadth": daily_breadth,
            "top_sectors": [
                {
                    "sector": s["sector"],
                    "change_pct": s["change_pct"],
                    "note": f"Điểm dòng tiền {s['change_pct']:+.1f} điểm trong tuần"
                }
                for s in sector_performance[:5]
            ],
            "money_flow_trend": str(narrative.get("money_flow_trend", "Không có dữ liệu"))[:500],
            "weekly_verdict": str(narrative.get("weekly_verdict", ""))[:300],
            "opportunities": str(narrative.get("opportunities", "Không có"))[:300],
            "risks": str(narrative.get("risks", "Không có"))[:300],
            "sector_rotation_note": str(narrative.get("sector_rotation_note", ""))[:500],
            "ai_engine_source": "Lộc Phát AI Engine v4.0 - Weekly Analysis",
            "token_usage": body.get("usage", {}),
            "disclaimer": "Đây là công cụ hỗ trợ phân tích dựa trên dữ liệu và mô hình AI, không phải tư vấn đầu tư cá nhân hóa.",
        }
    except Exception as e:
        return _build_weekly_quant_only_report(
            week_range, market_change, avg_breadth, avg_temperature,
            daily_breadth, sector_performance, snapshots
        )


def _build_weekly_quant_only_report(
    week_range: str,
    market_change: float,
    avg_breadth: float,
    avg_temperature: float,
    daily_breadth: List[Dict],
    sector_performance: List[Dict],
    snapshots: List[Dict]
) -> Dict[str, Any]:
    """Fallback report when DeepSeek is unavailable."""
    change_sign = "tăng" if market_change > 0 else "giảm" if market_change < 0 else "không đổi"

    # Guard: sector_performance may be empty if every snapshot in the week
    # had no sectors populated (e.g., API outage on a single trading day).
    # Render a neutral weekly report instead of crashing with IndexError.
    if sector_performance:
        top_sector = sector_performance[0]
        top_sector_name = top_sector.get("sector", "N/A")
        top_sector_change = top_sector.get("change_pct", 0.0)
        sector_rotation_note = (
            f"Top sector: {top_sector_name} với {top_sector_change:+.1f} điểm."
        )
        opportunities = (
            f"Sector rotation: top sectors đạt {top_sector_change:+.1f} điểm "
            f"nếu độ rộng duy trì trên 50%."
        )
    else:
        top_sector_name = "N/A"
        top_sector_change = 0.0
        sector_rotation_note = "Không đủ dữ liệu sector để xác định rotation tuần này."
        opportunities = (
            "Chưa đủ dữ liệu sector trong tuần để đánh giá cơ hội rotation."
        )

    return {
        "report_version": "lp-weekly-quant-1.0",
        "week_range": week_range,
        "market_change_pct": market_change,
        "avg_breadth": avg_breadth,
        "avg_temperature": avg_temperature,
        "headline": f"Tuần giao dịch: thị trường {change_sign} {abs(market_change):.1f} điểm",
        "summary": f"Nhiệt thị trường trung bình {avg_temperature:.1f}/100, độ rộng trung bình {avg_breadth:.1f}%.",
        "daily_breadth": daily_breadth,
        "top_sectors": [
            {
                "sector": s["sector"],
                "change_pct": s["change_pct"],
                "note": f"Điểm dòng tiền {s['change_pct']:+.1f} điểm"
            }
            for s in sector_performance[:5]
        ],
        "money_flow_trend": f"Thị trường {'tăng' if market_change > 0 else 'giảm' if market_change < 0 else 'đi ngang'} nhiệt {abs(market_change):.1f} điểm trong tuần.",
        "weekly_verdict": "Tích cực" if market_change > 5 else "Trung lập" if market_change > -5 else "Tiêu cực",
        "opportunities": opportunities,
        "risks": "Độ rộng dưới 45% kéo dài có thể làm suy yếu xu hướng hiện tại.",
        "sector_rotation_note": sector_rotation_note,
        "ai_engine_source": "Lộc Phát Quant v4.0 - Weekly Analysis (fallback)",
        "token_usage": {},
        "disclaimer": "Đây là công cụ hỗ trợ phân tích dựa trên dữ liệu định lượng, không phải tư vấn đầu tư.",
    }
