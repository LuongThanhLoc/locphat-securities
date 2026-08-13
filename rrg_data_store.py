"""Durable PostgreSQL storage for verified LP-RRG daily bars.

The RRG pipeline deliberately has no SQLite/JSON production fallback.  When
``DATABASE_URL`` is configured, every accepted bar is persisted and can be
served during a short upstream outage.  Test code can inject a store object;
production code always uses :class:`PostgresRrgStore`.
"""

from __future__ import annotations

import json
import os
import hashlib
import uuid
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterator, Optional

import pandas as pd


class RrgStoreUnavailable(RuntimeError):
    """Raised when the durable RRG store is required but unavailable."""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rrg_ingestion_batches (
    batch_id UUID PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    range_start DATE NOT NULL,
    range_end DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    response_hash TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error TEXT
);

CREATE TABLE IF NOT EXISTS rrg_raw_observations (
    symbol TEXT NOT NULL,
    trading_date DATE NOT NULL,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION,
    response_hash TEXT NOT NULL,
    batch_id UUID REFERENCES rrg_ingestion_batches(batch_id),
    validation_status TEXT NOT NULL DEFAULT 'valid',
    rule_version TEXT NOT NULL DEFAULT 'rrg-quality-2026-08-11',
    PRIMARY KEY (symbol, trading_date, source, response_hash)
);
CREATE INDEX IF NOT EXISTS idx_rrg_raw_symbol_date
    ON rrg_raw_observations (symbol, trading_date DESC, observed_at DESC);

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
    raw_close DOUBLE PRECISION,
    total_return_close DOUBLE PRECISION,
    adjustment_factor DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    adjustment_version TEXT NOT NULL DEFAULT 'raw-v1',
    corporate_action_status TEXT NOT NULL DEFAULT 'unknown',
    source_agreement_bps DOUBLE PRECISION,
    data_confidence_score DOUBLE PRECISION,
    canonical_fingerprint TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trading_date)
);
CREATE INDEX IF NOT EXISTS idx_rrg_bars_date ON rrg_daily_bars (trading_date DESC);

CREATE TABLE IF NOT EXISTS rrg_security_master (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    sector_code TEXT,
    effective_from DATE NOT NULL,
    effective_to DATE,
    listing_date DATE,
    delisting_date DATE,
    trading_status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, effective_from)
);

CREATE TABLE IF NOT EXISTS rrg_trading_sessions (
    exchange TEXT NOT NULL,
    trading_date DATE NOT NULL,
    session_status TEXT NOT NULL DEFAULT 'open',
    is_final BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (exchange, trading_date)
);

CREATE TABLE IF NOT EXISTS rrg_corporate_actions (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ex_date DATE NOT NULL,
    cash_per_share DOUBLE PRECISION,
    share_ratio DOUBLE PRECISION,
    subscription_price DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, event_type, ex_date, source)
);
CREATE INDEX IF NOT EXISTS idx_rrg_actions_symbol_date
    ON rrg_corporate_actions (symbol, ex_date);

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
    response_hash TEXT,
    rule_version TEXT NOT NULL DEFAULT 'rrg-quality-2026-08-11',
    batch_id UUID,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rrg_quarantine_symbol_time
    ON rrg_quarantine (symbol, quarantined_at DESC);

CREATE TABLE IF NOT EXISTS rrg_market_scores (
    snapshot_id TEXT NOT NULL,
    as_of_session DATE NOT NULL,
    benchmark TEXT NOT NULL,
    period INTEGER NOT NULL DEFAULT 14,
    symbol TEXT NOT NULL,
    rotation_score DOUBLE PRECISION NOT NULL,
    formula_version TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_rrg_scores_lookup
    ON rrg_market_scores (benchmark, period, as_of_session DESC, formula_version, universe_version);

CREATE TABLE IF NOT EXISTS rrg_dataset_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    as_of_session DATE NOT NULL,
    benchmark TEXT NOT NULL,
    group_key TEXT NOT NULL,
    period INTEGER NOT NULL,
    tail_length INTEGER NOT NULL,
    formula_version TEXT NOT NULL,
    adjustment_version TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    completeness_pct DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (as_of_session, benchmark, group_key, period, tail_length,
            formula_version, adjustment_version, universe_version, input_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_rrg_snapshots_lookup
    ON rrg_dataset_snapshots (benchmark, group_key, period, tail_length, as_of_session DESC);

CREATE TABLE IF NOT EXISTS rrg_index_membership_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    index_code TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    members JSONB NOT NULL,
    member_count INTEGER NOT NULL,
    source_chain JSONB NOT NULL,
    source_agreement BOOLEAN NOT NULL,
    fingerprint TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (index_code, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_rrg_index_membership_lookup
    ON rrg_index_membership_snapshots (index_code, observed_at DESC);
"""

MIGRATION_SQL = """
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS raw_close DOUBLE PRECISION;
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS total_return_close DOUBLE PRECISION;
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS adjustment_factor DOUBLE PRECISION NOT NULL DEFAULT 1.0;
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS corporate_action_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS source_agreement_bps DOUBLE PRECISION;
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS data_confidence_score DOUBLE PRECISION;
ALTER TABLE rrg_daily_bars ADD COLUMN IF NOT EXISTS canonical_fingerprint TEXT;
ALTER TABLE rrg_quarantine ADD COLUMN IF NOT EXISTS response_hash TEXT;
ALTER TABLE rrg_quarantine ADD COLUMN IF NOT EXISTS rule_version TEXT NOT NULL DEFAULT 'rrg-quality-2026-08-11';
ALTER TABLE rrg_quarantine ADD COLUMN IF NOT EXISTS batch_id UUID;
ALTER TABLE rrg_market_scores ADD COLUMN IF NOT EXISTS period INTEGER NOT NULL DEFAULT 14;
"""


class PostgresRrgStore:
    advisory_lock_id = 1_947_724_701

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise RrgStoreUnavailable("DATABASE_URL chưa được cấu hình cho kho dữ liệu RRG")
        self._pool = None
        self._pool_lock = threading.Lock()

    def _connect(self):
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RrgStoreUnavailable("Thiếu thư viện psycopg pool cho PostgreSQL RRG") from exc
        try:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        conninfo=self.database_url,
                        min_size=1,
                        max_size=8,
                        kwargs={"connect_timeout": 8},
                        open=True,
                    )
            return self._pool.connection()
        except Exception as exc:
            raise RrgStoreUnavailable(f"Không kết nối được PostgreSQL RRG: {exc}") from exc

    def init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute(MIGRATION_SQL)
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
            SELECT trading_date AS date, open, high, low,
                   COALESCE(total_return_close, close) AS close,
                   COALESCE(raw_close, close) AS raw_close,
                   COALESCE(total_return_close, close) AS total_return_close,
                   volume, source, source AS canonical_source, quality_status,
                   adjustment_version, corporate_action_status,
                   source_agreement_bps, data_confidence_score, fetched_at
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

    def begin_ingestion(self, symbol: str, source: str, start: str, end: str) -> str:
        batch_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO rrg_ingestion_batches
                       (batch_id, symbol, source, range_start, range_end)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (batch_id, symbol.upper(), source, start, end),
                )
            conn.commit()
        return batch_id

    def record_raw_history(
        self, symbol: str, frame: pd.DataFrame, source: str, batch_id: Optional[str] = None
    ) -> str:
        """Append immutable observations and return a deterministic response hash."""
        records = frame[["date", "open", "high", "low", "close", "volume"]].to_dict("records")
        response_hash = hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        observed_at = datetime.now(timezone.utc)
        rows = [(
            symbol.upper(), row["date"], source, observed_at,
            float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
            None if pd.isna(row.get("volume")) else float(row.get("volume")),
            response_hash, batch_id,
        ) for row in records]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rrg_raw_observations
                       (symbol,trading_date,source,observed_at,open,high,low,close,volume,
                        response_hash,batch_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    rows,
                )
                if batch_id:
                    cur.execute(
                        """UPDATE rrg_ingestion_batches SET status='complete', response_hash=%s,
                           row_count=%s, completed_at=NOW() WHERE batch_id=%s""",
                        (response_hash, len(rows), batch_id),
                    )
            conn.commit()
        return response_hash

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
                float(row["low"]), float(row.get("raw_close", row["close"])),
                None if pd.isna(row.get("volume")) else float(row.get("volume")),
                source, fetched_at,
                float(row.get("raw_close", row["close"])),
                float(row.get("total_return_close", row["close"])),
                float(row.get("adjustment_factor", 1.0)),
                str(row.get("adjustment_version") or "raw-v1"),
                str(row.get("corporate_action_status") or "unknown"),
                None if pd.isna(row.get("source_agreement_bps")) else float(row.get("source_agreement_bps")),
                None if pd.isna(row.get("data_confidence_score")) else float(row.get("data_confidence_score")),
                str(row.get("canonical_fingerprint") or "") or None,
            ))
        statement = """
            INSERT INTO rrg_daily_bars
                (symbol, trading_date, open, high, low, close, volume, source, fetched_at,
                 raw_close, total_return_close, adjustment_factor, adjustment_version,
                 corporate_action_status, source_agreement_bps, data_confidence_score,
                 canonical_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_date) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                source = EXCLUDED.source, quality_status = 'valid',
                raw_close = EXCLUDED.raw_close,
                total_return_close = EXCLUDED.total_return_close,
                adjustment_factor = EXCLUDED.adjustment_factor,
                adjustment_version = EXCLUDED.adjustment_version,
                corporate_action_status = EXCLUDED.corporate_action_status,
                source_agreement_bps = EXCLUDED.source_agreement_bps,
                data_confidence_score = EXCLUDED.data_confidence_score,
                canonical_fingerprint = EXCLUDED.canonical_fingerprint,
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
        response_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO rrg_quarantine
                       (symbol, source, reason, payload, response_hash)
                       VALUES (%s,%s,%s,%s::jsonb,%s)""",
                    (symbol.upper(), source, reason[:1000], json.dumps(payload, default=str), response_hash),
                )
            conn.commit()

    def upsert_corporate_actions(self, symbol: str, actions: list[Dict[str, Any]]) -> None:
        if not actions:
            return
        rows = []
        for action in actions:
            event_type = str(action.get("event_type") or action.get("type") or "").lower()
            ex_date = str(action.get("ex_date") or action.get("exright_date") or action.get("event_date") or "")[:10]
            event_id = str(action.get("event_id") or action.get("id") or f"{symbol}:{event_type}:{ex_date}")
            if not event_type or not ex_date:
                continue
            rows.append((
                event_id, symbol.upper(), event_type, ex_date,
                action.get("cash_per_share") or action.get("value_per_share"),
                action.get("share_ratio") or action.get("ratio"),
                action.get("subscription_price") or action.get("issue_price"),
                str(action.get("verification_status") or "confirmed").lower(),
                str(action.get("source") or "VCI"), json.dumps(action, default=str),
            ))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rrg_corporate_actions
                       (event_id,symbol,event_type,ex_date,cash_per_share,share_ratio,
                        subscription_price,verification_status,source,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                       ON CONFLICT (event_id) DO UPDATE SET
                         cash_per_share=EXCLUDED.cash_per_share,
                         share_ratio=EXCLUDED.share_ratio,
                         subscription_price=EXCLUDED.subscription_price,
                         verification_status=EXCLUDED.verification_status,
                         payload=EXCLUDED.payload, observed_at=NOW()""",
                    rows,
                )
                if rows:
                    cur.execute(
                        """UPDATE rrg_daily_bars
                           SET adjustment_version='pending-rebuild',
                               corporate_action_status='pending-rebuild'
                           WHERE symbol=%s""",
                        (symbol.upper(),),
                    )
            conn.commit()

    def upsert_security_master(self, records: list[Dict[str, Any]]) -> None:
        rows = [(
            str(row["symbol"]).upper(), str(row["exchange"]).upper(), row.get("sector_code"),
            row.get("effective_from") or date.today().isoformat(), row.get("effective_to"),
            row.get("listing_date"), row.get("delisting_date"),
            row.get("trading_status") or "active", row.get("source") or "VCI",
        ) for row in records if row.get("symbol") and row.get("exchange")]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rrg_security_master
                       (symbol,exchange,sector_code,effective_from,effective_to,listing_date,
                        delisting_date,trading_status,source)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (symbol,effective_from) DO UPDATE SET
                         exchange=EXCLUDED.exchange, sector_code=EXCLUDED.sector_code,
                         effective_to=EXCLUDED.effective_to, listing_date=EXCLUDED.listing_date,
                         delisting_date=EXCLUDED.delisting_date,
                         trading_status=EXCLUDED.trading_status, source=EXCLUDED.source,
                         observed_at=NOW()""",
                    rows,
                )
            conn.commit()

    def upsert_trading_sessions(self, exchange: str, sessions: list[str], source: str) -> None:
        rows = [(exchange.upper(), session, source) for session in sorted(set(sessions))]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rrg_trading_sessions(exchange,trading_date,source,is_final)
                       VALUES (%s,%s,%s,TRUE)
                       ON CONFLICT (exchange,trading_date) DO UPDATE SET
                         session_status='open', is_final=TRUE, source=EXCLUDED.source,
                         observed_at=NOW()""",
                    rows,
                )
            conn.commit()

    def security_identity(self, symbol: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        target = as_of or date.today().isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT exchange,sector_code,listing_date,delisting_date,trading_status,source
                       FROM rrg_security_master
                       WHERE symbol=%s AND effective_from<=%s
                             AND (effective_to IS NULL OR effective_to>=%s)
                       ORDER BY effective_from DESC LIMIT 1""",
                    (symbol.upper(), target, target),
                )
                row = cur.fetchone()
        if not row:
            return {}
        keys = ["exchange", "sector_code", "listing_date", "delisting_date", "trading_status", "source"]
        return {key: value for key, value in zip(keys, row)}

    def load_corporate_actions(self, symbol: str, start: str, end: str) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT event_id,event_type,ex_date,cash_per_share,share_ratio,
                              subscription_price,verification_status,source
                       FROM rrg_corporate_actions
                       WHERE symbol=%s AND ex_date BETWEEN %s AND %s ORDER BY ex_date""",
                    (symbol.upper(), start, end),
                )
                rows = cur.fetchall()
        keys = ["event_id", "event_type", "ex_date", "cash_per_share", "share_ratio",
                "subscription_price", "verification_status", "source"]
        return [{key: value for key, value in zip(keys, row)} for row in rows]

    def save_market_scores(
        self, snapshot_id: str, as_of_session: str, benchmark: str,
        formula_version: str, universe_version: str, scores: Dict[str, float], period: int = 14,
    ) -> None:
        rows = [(snapshot_id, as_of_session, benchmark, period, symbol, float(score),
                 formula_version, universe_version) for symbol, score in scores.items()]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rrg_market_scores
                       (snapshot_id,as_of_session,benchmark,period,symbol,rotation_score,
                        formula_version,universe_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    rows,
                )
            conn.commit()

    def load_market_scores(
        self, benchmark: str, as_of_session: str, formula_version: str, universe_version: str,
        period: int = 14,
    ) -> Dict[str, float]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT symbol,rotation_score FROM rrg_market_scores
                       WHERE snapshot_id=(
                         SELECT snapshot_id FROM rrg_market_scores
                         WHERE benchmark=%s AND period=%s AND as_of_session<=%s AND formula_version=%s
                               AND universe_version=%s
                         ORDER BY as_of_session DESC, created_at DESC LIMIT 1
                       )""",
                    (benchmark, period, as_of_session, formula_version, universe_version),
                )
                rows = cur.fetchall()
        return {str(symbol): float(score) for symbol, score in rows}

    def save_dataset_snapshot(self, payload: Dict[str, Any]) -> None:
        snapshot = dict(payload)
        fingerprint = str(snapshot["input_fingerprint"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO rrg_dataset_snapshots
                       (snapshot_id,as_of_session,benchmark,group_key,period,tail_length,
                        formula_version,adjustment_version,universe_version,input_fingerprint,
                        completeness_pct,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                       ON CONFLICT DO NOTHING""",
                    (snapshot["snapshot_id"], snapshot["as_of_session"], snapshot["benchmark"],
                     snapshot["group_key"], snapshot["period"], snapshot["tail_length"],
                     snapshot["formula_version"], snapshot["adjustment_version"],
                     snapshot["universe_version"], fingerprint,
                     float(snapshot["completeness_pct"]), json.dumps(snapshot, default=str)),
                )
            conn.commit()

    def load_dataset_snapshot(
        self, benchmark: str, group_key: str, period: int, tail_length: int,
        as_of_session: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cutoff = as_of_session or date.today().isoformat()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT payload FROM rrg_dataset_snapshots
                       WHERE benchmark=%s AND group_key=%s AND period=%s AND tail_length=%s
                             AND as_of_session<=%s AND completeness_pct=100
                       ORDER BY as_of_session DESC, created_at DESC LIMIT 1""",
                    (benchmark, group_key, period, tail_length, cutoff),
                )
                row = cur.fetchone()
        return dict(row[0]) if row else None

    def list_dataset_sessions(self, benchmark: str, group_key: str, limit: int = 60) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT as_of_session FROM rrg_dataset_snapshots
                       WHERE benchmark=%s AND group_key=%s AND completeness_pct=100
                       ORDER BY as_of_session DESC LIMIT %s""",
                    (benchmark, group_key, max(1, min(limit, 250))),
                )
                rows = cur.fetchall()
        return [str(row[0]) for row in rows]

    def save_index_membership_snapshot(
        self, index_code: str, symbols: list[str], meta: Dict[str, Any]
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO rrg_index_membership_snapshots
                       (snapshot_id,index_code,as_of_date,members,member_count,source_chain,
                        source_agreement,fingerprint,rule_version)
                       VALUES (%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s)
                       ON CONFLICT (index_code,fingerprint) DO UPDATE SET
                         as_of_date=EXCLUDED.as_of_date, source_chain=EXCLUDED.source_chain,
                         source_agreement=EXCLUDED.source_agreement,
                         rule_version=EXCLUDED.rule_version, observed_at=NOW()""",
                    (meta["snapshot_id"], index_code.upper(), meta["as_of_date"],
                     json.dumps(symbols), len(symbols), json.dumps(meta["source_chain"]),
                     bool(meta["source_agreement"]), meta["fingerprint"], meta["rule_version"]),
                )
            conn.commit()

    def load_index_membership_snapshot(self, index_code: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT snapshot_id,as_of_date,members,source_chain,source_agreement,
                              fingerprint,rule_version,observed_at
                       FROM rrg_index_membership_snapshots
                       WHERE index_code=%s AND source_agreement=TRUE
                       ORDER BY observed_at DESC LIMIT 1""",
                    (index_code.upper(),),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "symbols": list(row[2]),
            "meta": {
                "snapshot_id": row[0], "index_code": index_code.upper(),
                "as_of_date": str(row[1]), "source": "+".join(row[3]),
                "source_chain": list(row[3]), "source_agreement": bool(row[4]),
                "stale": True, "fingerprint": row[5], "rule_version": row[6],
                "observed_at": row[7].isoformat() if row[7] else None,
            },
        }

    def session_age(self, benchmark: str, as_of_session: str, end: str) -> Optional[int]:
        exchange = {
            "VNINDEX": "HOSE", "VN30": "HOSE",
            "HNXINDEX": "HNX", "HNX30": "HNX", "UPCOM": "UPCOM",
        }.get(benchmark.upper())
        if not exchange:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) FILTER (WHERE trading_date>%s AND trading_date<=%s), COUNT(*)
                       FROM rrg_trading_sessions
                       WHERE exchange=%s AND session_status='open'
                    """,
                    (as_of_session, end, exchange),
                )
                count, total = cur.fetchone()
        return int(count) if int(total) > 0 else None

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

    def coverage(self, symbols: list[str]) -> Dict[str, Any]:
        clean = sorted({symbol.upper() for symbol in symbols})
        if not clean:
            return {"eligible_symbols": 0, "valid_symbols": 0, "completeness_pct": 100.0}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) FILTER (
                                WHERE session_count>=252 AND quality_status='valid'
                              ),
                              COUNT(*) FILTER (WHERE session_count>0 AND session_count<252),
                              COUNT(*) FILTER (WHERE quality_status='inactive')
                       FROM rrg_sync_state WHERE symbol=ANY(%s)""",
                    (clean,),
                )
                valid, insufficient, inactive = map(int, cur.fetchone())
        eligible = len(clean) - insufficient - inactive
        unavailable = max(0, eligible - valid)
        return {
            "eligible_symbols": eligible,
            "valid_symbols": valid,
            "unavailable_symbols": unavailable,
            "inactive_symbols": inactive,
            "insufficient_history_symbols": insufficient,
            "completeness_pct": round(valid / eligible * 100.0, 2) if eligible else 100.0,
        }

    def health(self) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*), COUNT(*) FILTER (WHERE quality_status='valid'),
                              MAX(last_success_at), COUNT(*) FILTER (WHERE last_source='KBS')
                       FROM rrg_sync_state"""
                )
                total, valid, last_success, kbs_count = cur.fetchone()
                cur.execute("SELECT MAX(last_session) FROM rrg_sync_state")
                latest_session = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*), MAX(quarantined_at) FROM rrg_quarantine")
                quarantine_count, last_quarantine = cur.fetchone()
                cur.execute("SELECT COUNT(*) FROM rrg_quarantine WHERE quarantined_at>=NOW()-INTERVAL '24 hours'")
                quarantine_24h = int(cur.fetchone()[0])
                cur.execute(
                    """SELECT GREATEST(COUNT(*) - COUNT(DISTINCT (symbol,trading_date,source)),0)
                       FROM rrg_raw_observations"""
                )
                revision_count = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*), MAX(created_at), MAX(as_of_session) FROM rrg_dataset_snapshots")
                snapshot_count, last_snapshot_at, latest_snapshot_session = cur.fetchone()
                cur.execute(
                    """SELECT COUNT(DISTINCT snapshot_id), MAX(as_of_session)
                       FROM rrg_market_scores
                       WHERE as_of_session=(SELECT MAX(as_of_session) FROM rrg_market_scores)"""
                )
                score_snapshot_count, latest_score_session = cur.fetchone()
                cur.execute(
                    """SELECT COALESCE(AVG(data_confidence_score),0),
                              COALESCE(AVG(source_agreement_bps),0),
                              COUNT(*) FILTER (WHERE corporate_action_status='adjustment_pending')
                       FROM rrg_daily_bars WHERE trading_date=(SELECT MAX(trading_date) FROM rrg_daily_bars)"""
                )
                confidence, agreement_bps, pending_actions = cur.fetchone()
                cur.execute(
                    """SELECT index_code,member_count,source_agreement,observed_at
                       FROM rrg_index_membership_snapshots
                       WHERE observed_at=(SELECT MAX(observed_at) FROM rrg_index_membership_snapshots)"""
                )
                latest_membership = cur.fetchone()
        return {
            "configured": True,
            "symbols_tracked": total,
            "symbols_valid": valid,
            "fallback_symbols": kbs_count,
            "fallback_ratio_pct": round(float(kbs_count or 0) / float(total or 1) * 100.0, 2),
            "last_success_at": last_success.isoformat() if last_success else None,
            "latest_session": str(latest_session) if latest_session else None,
            "quarantine_count": quarantine_count,
            "quarantine_last_24h": quarantine_24h,
            "raw_revision_count": revision_count,
            "last_quarantine_at": last_quarantine.isoformat() if last_quarantine else None,
            "dataset_snapshots": snapshot_count,
            "market_score_snapshots": int(score_snapshot_count or 0),
            "latest_market_score_session": str(latest_score_session) if latest_score_session else None,
            "latest_snapshot_at": last_snapshot_at.isoformat() if last_snapshot_at else None,
            "latest_snapshot_session": str(latest_snapshot_session) if latest_snapshot_session else None,
            "average_data_confidence": round(float(confidence or 0), 2),
            "average_source_agreement_bps": round(float(agreement_bps or 0), 2),
            "adjustment_pending_symbols": int(pending_actions or 0),
            "latest_index_membership": ({
                "index_code": latest_membership[0],
                "member_count": int(latest_membership[1]),
                "source_agreement": bool(latest_membership[2]),
                "observed_at": latest_membership[3].isoformat(),
            } if latest_membership else None),
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
