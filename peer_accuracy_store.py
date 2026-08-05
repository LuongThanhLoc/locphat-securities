import sqlite3
import json
import hashlib
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer_accuracy.db")

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS financial_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                payload_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                reported_at TEXT,
                payload_json TEXT NOT NULL
            )
        """)
        
        # Verify columns of financial_snapshots
        cursor.execute("PRAGMA table_info(financial_snapshots)")
        cols_fs = {row[1] for row in cursor.fetchall()}
        for col_name, col_type in [
            ("source_url", "TEXT"), ("payload_hash", "TEXT"),
            ("fetched_at", "TEXT"), ("reported_at", "TEXT"), ("payload_json", "TEXT")
        ]:
            if col_name not in cols_fs:
                cursor.execute(f"ALTER TABLE financial_snapshots ADD COLUMN {col_name} {col_type}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS peer_metric_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                raw_inputs_json TEXT NOT NULL,
                calculated_value REAL,
                formula_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Verify columns of peer_metric_snapshots
        cursor.execute("PRAGMA table_info(peer_metric_snapshots)")
        cols_ms = {row[1] for row in cursor.fetchall()}
        if "metric_key" not in cols_ms:
            # Recreate table if schema incompatible
            cursor.execute("DROP TABLE IF EXISTS peer_metric_snapshots")
            cursor.execute("""
                CREATE TABLE peer_metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    metric_key TEXT NOT NULL,
                    raw_inputs_json TEXT NOT NULL,
                    calculated_value REAL,
                    formula_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

        conn.commit()
    finally:
        conn.close()

# Initialize DB on module load
init_db()

def save_financial_snapshot(symbol: str, period: str, source: str, source_url: Optional[str], payload: Dict[str, Any], reported_at: Optional[str] = None) -> int:
    symbol_clean = symbol.upper().strip()
    payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    payload_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
    fetched_at = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO financial_snapshots (symbol, period, source, source_url, payload_hash, fetched_at, reported_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol_clean, period, source, source_url or "", payload_hash, fetched_at, reported_at or "", payload_str))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_financial_snapshot_by_id(snapshot_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM financial_snapshots WHERE id = ?", (snapshot_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "symbol": row["symbol"],
            "period": row["period"],
            "source": row["source"],
            "source_url": row["source_url"],
            "payload_hash": row["payload_hash"],
            "fetched_at": row["fetched_at"],
            "reported_at": row["reported_at"],
            "payload": json.loads(row["payload_json"]) if row["payload_json"] else {}
        }
    finally:
        conn.close()

def save_metric_snapshot(symbol: str, metric_key: str, raw_inputs: Dict[str, Any], calculated_value: Optional[float], formula_name: str) -> int:
    symbol_clean = symbol.upper().strip()
    raw_inputs_str = json.dumps(raw_inputs, sort_keys=True, ensure_ascii=False)
    created_at = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO peer_metric_snapshots (symbol, metric_key, raw_inputs_json, calculated_value, formula_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (symbol_clean, metric_key, raw_inputs_str, calculated_value, formula_name, created_at))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_metric_snapshot(metric_snapshot_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM peer_metric_snapshots WHERE id = ?", (metric_snapshot_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "symbol": row["symbol"],
            "metric_key": row["metric_key"],
            "raw_inputs": json.loads(row["raw_inputs_json"]) if row["raw_inputs_json"] else {},
            "calculated_value": row["calculated_value"],
            "formula_name": row["formula_name"],
            "created_at": row["created_at"]
        }
    finally:
        conn.close()

def store_summary() -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM financial_snapshots")
        fs_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM peer_metric_snapshots")
        ms_count = cursor.fetchone()[0]
        return {
            "financial_snapshots": fs_count,
            "peer_metric_snapshots": ms_count,
            "status": "healthy"
        }
    finally:
        conn.close()
