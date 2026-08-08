"""Durable PostgreSQL storage for verified LP-RRG daily bars.

The RRG pipeline deliberately has no SQLite/JSON production fallback.  When
``DATABASE_URL`` is configured, every accepted bar is persisted and can be
served during a short upstream outage.  Test code can inject a store object;
production code always uses :class:`PostgresRrgStore`.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

import pandas as pd


class RrgStoreUnavailable(RuntimeError):
    """Raised when the durable RRG store is required but unavailable."""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rrg_daily_bars (
    symbol TEXT NOT NULL,
    trading_date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION,
    source TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'valid',
    adjustment_version TEXT NOT NULL DEFAULT 'raw-v1',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trading_date)
);
CREATE INDEX IF NOT EXISTS idx_rrg_bars_date ON rrg_daily_bars (trading_date DESC);

CREATE TABLE IF NOT EXISTS rrg_sync_state (
    symbol TEXT PRIMARY KEY,
    first_session DATE,
    last_session DATE,
    session_count INTEGER NOT NULL DEFAULT 0,
    last_source TEXT,
    source_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    quality_status TEXT NOT NULL DEFAULT 'unknown',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rrg_quarantine (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rrg_quarantine_symbol_time
    ON rrg_quarantine (symbol, quarantined_at DESC);
"""


class PostgresRrgStore:
    advisory_lock_id = 1_947_724_701

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise RrgStoreUnavailable("DATABASE_URL chưa được cấu hình cho kho dữ liệu RRG")

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RrgStoreUnavailable("Thiếu thư viện psycopg cho PostgreSQL RRG") from exc
        try:
            return psycopg.connect(self.database_url, connect_timeout=8)
        except Exception as exc:
            raise RrgStoreUnavailable(f"Không kết nối được PostgreSQL RRG: {exc}") from exc

    def init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def sync_lock(self) -> Iterator[bool]:
        """Acquire a cross-instance advisory lock for a refresh/backfill."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (self.advisory_lock_id,))
                acquired = bool(cur.fetchone()[0])
                try:
                    yield acquired
                finally:
                    if acquired:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (self.advisory_lock_id,))

    def load_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        query = """
            SELECT trading_date AS date, open, high, low, close, volume,
                   source, quality_status, fetched_at
            FROM rrg_daily_bars
            WHERE symbol = %s AND trading_date BETWEEN %s AND %s
                  AND quality_status = 'valid'
            ORDER BY trading_date
        """
        with self._connect() as conn:
            frame = pd.read_sql_query(query, conn, params=(symbol.upper(), start, end))
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
        return frame

    def upsert_history(
        self,
        symbol: str,
        frame: pd.DataFrame,
        source: str,
        source_chain: list[dict[str, Any]],
    ) -> None:
        if frame.empty:
            return
        rows = []
        fetched_at = datetime.now(timezone.utc)
        for row in frame.to_dict("records"):
            rows.append((
                symbol.upper(), row["date"], float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]),
                None if pd.isna(row.get("volume")) else float(row.get("volume")),
                source, fetched_at,
            ))
        statement = """
            INSERT INTO rrg_daily_bars
                (symbol, trading_date, open, high, low, close, volume, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_date) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                source = EXCLUDED.source, quality_status = 'valid',
                fetched_at = EXCLUDED.fetched_at
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(statement, rows)
                cur.execute(
                    """
                    INSERT INTO rrg_sync_state
                        (symbol, first_session, last_session, session_count, last_source,
                         source_chain, last_success_at, last_error, quality_status)
                    SELECT %s, MIN(trading_date), MAX(trading_date), COUNT(*), %s,
                           %s::jsonb, NOW(), NULL, 'valid'
                    FROM rrg_daily_bars WHERE symbol = %s AND quality_status = 'valid'
                    ON CONFLICT (symbol) DO UPDATE SET
                        first_session = EXCLUDED.first_session,
                        last_session = EXCLUDED.last_session,
                        session_count = EXCLUDED.session_count,
                        last_source = EXCLUDED.last_source,
                        source_chain = EXCLUDED.source_chain,
                        last_success_at = EXCLUDED.last_success_at,
                        last_error = NULL, quality_status = 'valid', updated_at = NOW()
                    """,
                    (symbol.upper(), source, json.dumps(source_chain), symbol.upper()),
                )
            conn.commit()

    def record_failure(self, symbol: str, error: str, status: str = "source_unavailable") -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rrg_sync_state
                        (symbol, last_error_at, last_error, quality_status)
                    VALUES (%s, NOW(), %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        last_error_at = NOW(), last_error = EXCLUDED.last_error,
                        quality_status = EXCLUDED.quality_status, updated_at = NOW()
                    """,
                    (symbol.upper(), error[:1000], status),
                )
            conn.commit()

    def quarantine(self, symbol: str, source: str, reason: str, payload: Dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO rrg_quarantine (symbol, source, reason, payload) VALUES (%s,%s,%s,%s::jsonb)",
                    (symbol.upper(), source, reason[:1000], json.dumps(payload, default=str)),
                )
            conn.commit()

    def state(self, symbol: str) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT first_session, last_session, session_count, last_source,
                              source_chain, last_success_at, last_error_at, last_error,
                              quality_status
                       FROM rrg_sync_state WHERE symbol = %s""",
                    (symbol.upper(),),
                )
                row = cur.fetchone()
        if not row:
            return {}
        keys = ["first_session", "last_session", "session_count", "last_source",
                "source_chain", "last_success_at", "last_error_at", "last_error", "quality_status"]
        return {key: value for key, value in zip(keys, row)}

    def health(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*), COUNT(*) FILTER (WHERE quality_status='valid'),
                              MAX(last_success_at), COUNT(*) FILTER (WHERE last_source='KBS')
                       FROM rrg_sync_state"""
                )
                total, valid, last_success, kbs_count = cur.fetchone()
        return {
            "configured": True,
            "symbols_tracked": total,
            "symbols_valid": valid,
            "fallback_symbols": kbs_count,
            "last_success_at": last_success.isoformat() if last_success else None,
        }


_STORE: Optional[PostgresRrgStore] = None


def get_rrg_store(required: bool = True) -> Optional[PostgresRrgStore]:
    global _STORE
    if _STORE is not None:
        return _STORE
    if not os.getenv("DATABASE_URL", "").strip():
        if required:
            raise RrgStoreUnavailable("DATABASE_URL chưa được cấu hình cho RRG")
        return None
    _STORE = PostgresRrgStore()
    return _STORE
