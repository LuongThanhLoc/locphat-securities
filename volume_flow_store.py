"""PostgreSQL repository for the 20-session volume-flow module.

Production deliberately has no JSON/SQLite fallback: normalized EOD rows are
committed first and API responses are always read back from PostgreSQL.
"""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator, Optional


class VolumeFlowStoreUnavailable(RuntimeError):
    """Raised when the durable volume-flow repository cannot be used."""


def _configured_database_url(database_url: Optional[str] = None) -> str:
    """Resolve the module store without changing other PostgreSQL consumers."""
    return (
        database_url
        or os.getenv("VOLUME_FLOW_DATABASE_URL", "")
        or os.getenv("DATABASE_URL", "")
    ).strip()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS volume_flow_daily (
    symbol TEXT NOT NULL,
    trading_date DATE NOT NULL,
    open_price NUMERIC(20, 4) NOT NULL,
    high_price NUMERIC(20, 4) NOT NULL,
    low_price NUMERIC(20, 4) NOT NULL,
    close_price NUMERIC(20, 4) NOT NULL,
    market_volume BIGINT NOT NULL DEFAULT 0,
    market_value NUMERIC(24, 0) NOT NULL DEFAULT 0,
    foreign_buy_volume BIGINT NOT NULL DEFAULT 0,
    foreign_sell_volume BIGINT NOT NULL DEFAULT 0,
    foreign_net_volume BIGINT NOT NULL DEFAULT 0,
    foreign_buy_value NUMERIC(24, 0) NOT NULL DEFAULT 0,
    foreign_sell_value NUMERIC(24, 0) NOT NULL DEFAULT 0,
    foreign_net_value NUMERIC(24, 0) NOT NULL DEFAULT 0,
    foreign_ytd_net_volume BIGINT,
    foreign_ytd_net_value NUMERIC(24, 0),
    proprietary_buy_volume BIGINT,
    proprietary_sell_volume BIGINT,
    proprietary_net_volume BIGINT,
    proprietary_buy_value NUMERIC(24, 0),
    proprietary_sell_value NUMERIC(24, 0),
    proprietary_net_value NUMERIC(24, 0),
    proprietary_source_record BOOLEAN NOT NULL DEFAULT FALSE,
    is_final BOOLEAN NOT NULL DEFAULT TRUE,
    source TEXT NOT NULL,
    response_hash TEXT NOT NULL,
    source_updated_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trading_date)
);
CREATE INDEX IF NOT EXISTS idx_volume_flow_daily_date
    ON volume_flow_daily (trading_date DESC);

CREATE TABLE IF NOT EXISTS volume_flow_sync_state (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    exchange TEXT,
    final_cutoff_date DATE,
    first_session DATE,
    last_session DATE,
    session_count INTEGER NOT NULL DEFAULT 0,
    last_source TEXT,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    quality_status TEXT NOT NULL DEFAULT 'unknown',
    quality_version TEXT,
    foreign_ytd_start_date DATE,
    foreign_ytd_session_count INTEGER,
    foreign_ytd_complete BOOLEAN NOT NULL DEFAULT FALSE,
    foreign_ytd_calculation TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE volume_flow_daily
    ADD COLUMN IF NOT EXISTS foreign_ytd_net_volume BIGINT,
    ADD COLUMN IF NOT EXISTS foreign_ytd_net_value NUMERIC(24, 0);
ALTER TABLE volume_flow_daily
    ALTER COLUMN proprietary_buy_volume DROP NOT NULL,
    ALTER COLUMN proprietary_buy_volume DROP DEFAULT,
    ALTER COLUMN proprietary_sell_volume DROP NOT NULL,
    ALTER COLUMN proprietary_sell_volume DROP DEFAULT,
    ALTER COLUMN proprietary_net_volume DROP NOT NULL,
    ALTER COLUMN proprietary_net_volume DROP DEFAULT,
    ALTER COLUMN proprietary_buy_value DROP NOT NULL,
    ALTER COLUMN proprietary_buy_value DROP DEFAULT,
    ALTER COLUMN proprietary_sell_value DROP NOT NULL,
    ALTER COLUMN proprietary_sell_value DROP DEFAULT,
    ALTER COLUMN proprietary_net_value DROP NOT NULL,
    ALTER COLUMN proprietary_net_value DROP DEFAULT;
UPDATE volume_flow_daily SET
    proprietary_buy_volume = NULL,
    proprietary_sell_volume = NULL,
    proprietary_net_volume = NULL,
    proprietary_buy_value = NULL,
    proprietary_sell_value = NULL,
    proprietary_net_value = NULL
WHERE proprietary_source_record = FALSE;

ALTER TABLE volume_flow_sync_state
    ADD COLUMN IF NOT EXISTS quality_version TEXT,
    ADD COLUMN IF NOT EXISTS foreign_ytd_start_date DATE,
    ADD COLUMN IF NOT EXISTS foreign_ytd_session_count INTEGER,
    ADD COLUMN IF NOT EXISTS foreign_ytd_complete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS foreign_ytd_calculation TEXT;

CREATE TABLE IF NOT EXISTS price_chart_daily (
    symbol TEXT NOT NULL,
    trading_date DATE NOT NULL,
    open_price NUMERIC(20, 4) NOT NULL,
    high_price NUMERIC(20, 4) NOT NULL,
    low_price NUMERIC(20, 4) NOT NULL,
    close_price NUMERIC(20, 4) NOT NULL,
    volume BIGINT NOT NULL,
    price_basis TEXT NOT NULL DEFAULT 'unadjusted',
    source TEXT NOT NULL,
    response_hash TEXT NOT NULL,
    source_updated_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trading_date),
    CHECK (price_basis = 'unadjusted')
);
CREATE INDEX IF NOT EXISTS idx_price_chart_daily_date
    ON price_chart_daily (trading_date DESC);

CREATE TABLE IF NOT EXISTS price_chart_sync_state (
    symbol TEXT PRIMARY KEY,
    exchange TEXT,
    final_cutoff_date DATE,
    retention_start_date DATE,
    first_session DATE,
    last_session DATE,
    session_count INTEGER NOT NULL DEFAULT 0,
    last_source TEXT,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    quality_status TEXT NOT NULL DEFAULT 'unknown',
    quality_version TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


SESSION_COLUMNS = (
    "symbol", "trading_date", "open_price", "high_price", "low_price", "close_price",
    "market_volume", "market_value", "foreign_buy_volume", "foreign_sell_volume",
    "foreign_net_volume", "foreign_buy_value", "foreign_sell_value", "foreign_net_value",
    "foreign_ytd_net_volume", "foreign_ytd_net_value",
    "proprietary_buy_volume", "proprietary_sell_volume", "proprietary_net_volume",
    "proprietary_buy_value", "proprietary_sell_value", "proprietary_net_value",
    "proprietary_source_record", "is_final", "source", "response_hash",
    "source_updated_at", "fetched_at",
)

PRICE_CHART_COLUMNS = (
    "symbol", "trading_date", "open_price", "high_price", "low_price",
    "close_price", "volume", "price_basis", "source", "response_hash",
    "source_updated_at", "fetched_at",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


class PostgresVolumeFlowStore:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = _configured_database_url(database_url)
        if not self.database_url:
            raise VolumeFlowStoreUnavailable(
                "VOLUME_FLOW_DATABASE_URL hoặc DATABASE_URL chưa được cấu hình "
                "cho kho Tổng quan KLGD"
            )
        self._pool = None
        self._pool_lock = threading.Lock()

    def _connect(self):
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise VolumeFlowStoreUnavailable("Thiếu psycopg pool cho Tổng quan KLGD") from exc
        try:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        conninfo=self.database_url,
                        min_size=1,
                        max_size=6,
                        kwargs={"connect_timeout": 8},
                        open=True,
                    )
            return self._pool.connection()
        except Exception as exc:
            raise VolumeFlowStoreUnavailable(
                f"Không kết nối được PostgreSQL Tổng quan KLGD: {exc}"
            ) from exc

    @contextmanager
    def _connection(self, operation: str):
        """Normalize connection and SQL failures to the endpoint's 503 contract."""
        try:
            with self._connect() as conn:
                yield conn
        except VolumeFlowStoreUnavailable:
            raise
        except Exception as exc:
            raise VolumeFlowStoreUnavailable(
                f"PostgreSQL lỗi khi {operation} dữ liệu Tổng quan KLGD: {exc}"
            ) from exc

    def init_schema(self) -> None:
        with self._connection("khởi tạo") as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    @staticmethod
    def _advisory_lock_id(symbol: str) -> int:
        raw = hashlib.sha256(f"volume-flow:{symbol.upper()}".encode()).digest()[:8]
        return int.from_bytes(raw, "big", signed=True)

    @contextmanager
    def sync_lock(self, symbol: str) -> Iterator[bool]:
        """Try a per-symbol lock shared by all application instances."""
        lock_id = self._advisory_lock_id(symbol)
        with self._connection("khóa đồng bộ") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
                acquired = bool(cur.fetchone()[0])
                try:
                    yield acquired
                finally:
                    if acquired:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

    def load_sessions(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        query = f"""
            SELECT {', '.join(SESSION_COLUMNS)}
            FROM volume_flow_daily
            WHERE symbol = %s AND is_final = TRUE
            ORDER BY trading_date DESC
            LIMIT %s
        """
        with self._connection("đọc lịch sử") as conn:
            with conn.cursor() as cur:
                cur.execute(query, (symbol.upper(), max(1, min(int(limit), 20))))
                rows = cur.fetchall()
        return [
            {name: _json_value(value) for name, value in zip(SESSION_COLUMNS, row)}
            for row in reversed(rows)
        ]

    def load_state(self, symbol: str) -> Optional[dict[str, Any]]:
        columns = (
            "symbol", "company_name", "exchange", "final_cutoff_date", "first_session",
            "last_session", "session_count", "last_source", "last_success_at", "last_error_at", "last_error",
            "quality_status", "quality_version", "foreign_ytd_start_date",
            "foreign_ytd_session_count", "foreign_ytd_complete",
            "foreign_ytd_calculation", "updated_at",
        )
        with self._connection("đọc trạng thái") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(columns)} FROM volume_flow_sync_state WHERE symbol = %s",
                    (symbol.upper(),),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {name: _json_value(value) for name, value in zip(columns, row)}

    def load_price_chart(self, symbol: str, limit: int = 900) -> list[dict[str, Any]]:
        query = f"""
            SELECT {', '.join(PRICE_CHART_COLUMNS)}
            FROM price_chart_daily
            WHERE symbol = %s AND price_basis = 'unadjusted'
            ORDER BY trading_date DESC
            LIMIT %s
        """
        with self._connection("đọc lịch sử giá chart") as conn:
            with conn.cursor() as cur:
                cur.execute(query, (symbol.upper(), max(1, min(int(limit), 1000))))
                rows = cur.fetchall()
        return [
            {name: _json_value(value) for name, value in zip(PRICE_CHART_COLUMNS, row)}
            for row in reversed(rows)
        ]

    def load_price_chart_state(self, symbol: str) -> Optional[dict[str, Any]]:
        columns = (
            "symbol", "exchange", "final_cutoff_date", "retention_start_date",
            "first_session", "last_session", "session_count", "last_source",
            "last_success_at", "last_error_at", "last_error", "quality_status",
            "quality_version", "updated_at",
        )
        with self._connection("đọc trạng thái chart") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(columns)} FROM price_chart_sync_state WHERE symbol = %s",
                    (symbol.upper(),),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {name: _json_value(value) for name, value in zip(columns, row)}

    def upsert_price_chart(
        self,
        symbol: str,
        sessions: list[dict[str, Any]],
        *,
        final_cutoff_date: str,
        retention_start_date: str,
        exchange: str,
        source: str,
        quality_version: str,
    ) -> None:
        if not sessions:
            return
        rows = [tuple(row.get(column) for column in PRICE_CHART_COLUMNS[:-1]) for row in sessions]
        placeholders = ", ".join(["%s"] * (len(PRICE_CHART_COLUMNS) - 1))
        statement = f"""
            INSERT INTO price_chart_daily ({', '.join(PRICE_CHART_COLUMNS[:-1])})
            VALUES ({placeholders})
            ON CONFLICT (symbol, trading_date) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume,
                price_basis = EXCLUDED.price_basis,
                source = EXCLUDED.source,
                response_hash = EXCLUDED.response_hash,
                source_updated_at = EXCLUDED.source_updated_at,
                fetched_at = NOW()
        """
        clean_symbol = symbol.upper()
        with self._connection("ghi lịch sử giá chart") as conn:
            with conn.cursor() as cur:
                cur.executemany(statement, rows)
                cur.execute(
                    "DELETE FROM price_chart_daily WHERE symbol = %s AND trading_date < %s",
                    (clean_symbol, retention_start_date),
                )
                cur.execute(
                    """
                    INSERT INTO price_chart_sync_state
                        (symbol, exchange, final_cutoff_date, retention_start_date,
                         first_session, last_session, session_count, last_source,
                         last_success_at, last_error, quality_status, quality_version)
                    SELECT %s, %s, %s, %s, MIN(trading_date), MAX(trading_date), COUNT(*),
                           %s, NOW(), NULL, 'valid', %s
                    FROM price_chart_daily WHERE symbol = %s AND price_basis = 'unadjusted'
                    ON CONFLICT (symbol) DO UPDATE SET
                        exchange = EXCLUDED.exchange,
                        final_cutoff_date = EXCLUDED.final_cutoff_date,
                        retention_start_date = EXCLUDED.retention_start_date,
                        first_session = EXCLUDED.first_session,
                        last_session = EXCLUDED.last_session,
                        session_count = EXCLUDED.session_count,
                        last_source = EXCLUDED.last_source,
                        last_success_at = EXCLUDED.last_success_at,
                        last_error = NULL,
                        quality_status = 'valid',
                        quality_version = EXCLUDED.quality_version,
                        updated_at = NOW()
                    """,
                    (
                        clean_symbol, exchange, final_cutoff_date, retention_start_date,
                        source, quality_version, clean_symbol,
                    ),
                )
            conn.commit()

    def record_price_chart_failure(self, symbol: str, error: str) -> None:
        with self._connection("ghi lỗi chart") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_chart_sync_state
                        (symbol, last_error_at, last_error, quality_status)
                    VALUES (%s, NOW(), %s, 'stale')
                    ON CONFLICT (symbol) DO UPDATE SET
                        last_error_at = NOW(), last_error = EXCLUDED.last_error,
                        quality_status = 'stale', updated_at = NOW()
                    """,
                    (symbol.upper(), str(error)[:1000]),
                )
            conn.commit()

    def upsert_sessions(
        self,
        symbol: str,
        sessions: list[dict[str, Any]],
        *,
        final_cutoff_date: str,
        source: str,
        company_name: str,
        exchange: str,
        quality_version: str,
        foreign_ytd_start_date: Optional[str],
        foreign_ytd_session_count: int,
        foreign_ytd_complete: bool,
        foreign_ytd_calculation: str,
    ) -> None:
        if not sessions:
            return
        rows = [tuple(row.get(column) for column in SESSION_COLUMNS[:-1]) for row in sessions]
        placeholders = ", ".join(["%s"] * (len(SESSION_COLUMNS) - 1))
        statement = f"""
            INSERT INTO volume_flow_daily ({', '.join(SESSION_COLUMNS[:-1])})
            VALUES ({placeholders})
            ON CONFLICT (symbol, trading_date) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                market_volume = EXCLUDED.market_volume,
                market_value = EXCLUDED.market_value,
                foreign_buy_volume = EXCLUDED.foreign_buy_volume,
                foreign_sell_volume = EXCLUDED.foreign_sell_volume,
                foreign_net_volume = EXCLUDED.foreign_net_volume,
                foreign_buy_value = EXCLUDED.foreign_buy_value,
                foreign_sell_value = EXCLUDED.foreign_sell_value,
                foreign_net_value = EXCLUDED.foreign_net_value,
                foreign_ytd_net_volume = EXCLUDED.foreign_ytd_net_volume,
                foreign_ytd_net_value = EXCLUDED.foreign_ytd_net_value,
                proprietary_buy_volume = EXCLUDED.proprietary_buy_volume,
                proprietary_sell_volume = EXCLUDED.proprietary_sell_volume,
                proprietary_net_volume = EXCLUDED.proprietary_net_volume,
                proprietary_buy_value = EXCLUDED.proprietary_buy_value,
                proprietary_sell_value = EXCLUDED.proprietary_sell_value,
                proprietary_net_value = EXCLUDED.proprietary_net_value,
                proprietary_source_record = EXCLUDED.proprietary_source_record,
                is_final = EXCLUDED.is_final,
                source = EXCLUDED.source,
                response_hash = EXCLUDED.response_hash,
                source_updated_at = EXCLUDED.source_updated_at,
                fetched_at = NOW()
        """
        clean_symbol = symbol.upper()
        with self._connection("ghi") as conn:
            with conn.cursor() as cur:
                cur.executemany(statement, rows)
                cur.execute(
                    """
                    DELETE FROM volume_flow_daily
                    WHERE symbol = %s AND trading_date NOT IN (
                        SELECT trading_date FROM volume_flow_daily
                        WHERE symbol = %s AND is_final = TRUE
                        ORDER BY trading_date DESC LIMIT 20
                    )
                    """,
                    (clean_symbol, clean_symbol),
                )
                cur.execute(
                    """
                    INSERT INTO volume_flow_sync_state
                        (symbol, company_name, exchange, final_cutoff_date,
                         first_session, last_session, session_count,
                         last_source, last_success_at, last_error, quality_status,
                         quality_version, foreign_ytd_start_date,
                         foreign_ytd_session_count, foreign_ytd_complete,
                         foreign_ytd_calculation)
                    SELECT %s, %s, %s, %s, MIN(trading_date), MAX(trading_date), COUNT(*),
                           %s, NOW(), NULL, 'valid', %s, %s, %s, %s, %s
                    FROM volume_flow_daily WHERE symbol = %s AND is_final = TRUE
                    ON CONFLICT (symbol) DO UPDATE SET
                        company_name = EXCLUDED.company_name,
                        exchange = EXCLUDED.exchange,
                        final_cutoff_date = EXCLUDED.final_cutoff_date,
                        first_session = EXCLUDED.first_session,
                        last_session = EXCLUDED.last_session,
                        session_count = EXCLUDED.session_count,
                        last_source = EXCLUDED.last_source,
                        last_success_at = EXCLUDED.last_success_at,
                        last_error = NULL,
                        quality_status = 'valid',
                        quality_version = EXCLUDED.quality_version,
                        foreign_ytd_start_date = EXCLUDED.foreign_ytd_start_date,
                        foreign_ytd_session_count = EXCLUDED.foreign_ytd_session_count,
                        foreign_ytd_complete = EXCLUDED.foreign_ytd_complete,
                        foreign_ytd_calculation = EXCLUDED.foreign_ytd_calculation,
                        updated_at = NOW()
                    """,
                    (
                        clean_symbol, company_name, exchange, final_cutoff_date,
                        source, quality_version, foreign_ytd_start_date,
                        foreign_ytd_session_count, foreign_ytd_complete,
                        foreign_ytd_calculation, clean_symbol,
                    ),
                )
            conn.commit()

    def record_failure(self, symbol: str, error: str) -> None:
        with self._connection("ghi lỗi") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO volume_flow_sync_state
                        (symbol, last_error_at, last_error, quality_status)
                    VALUES (%s, NOW(), %s, 'stale')
                    ON CONFLICT (symbol) DO UPDATE SET
                        last_error_at = NOW(), last_error = EXCLUDED.last_error,
                        quality_status = 'stale', updated_at = NOW()
                    """,
                    (symbol.upper(), str(error)[:1000]),
                )
            conn.commit()


_STORE: Optional[PostgresVolumeFlowStore] = None
_STORE_LOCK = threading.Lock()


def get_volume_flow_store(required: bool = True) -> Optional[PostgresVolumeFlowStore]:
    global _STORE
    if _STORE is not None:
        return _STORE
    if not _configured_database_url():
        if required:
            raise VolumeFlowStoreUnavailable(
                "VOLUME_FLOW_DATABASE_URL hoặc DATABASE_URL chưa được cấu hình "
                "cho Tổng quan KLGD"
            )
        return None
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = PostgresVolumeFlowStore()
            _STORE.init_schema()
    return _STORE
