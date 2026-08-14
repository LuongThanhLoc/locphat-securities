"""Durable v2 repository for verified macro data."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator, Optional


DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "macro_calendar_v2.db")
ADVISORY_LOCK_ID = 724_202_608


class MacroRepository:
    def __init__(self, database_url: Optional[str] = None, sqlite_path: Optional[str] = None):
        configured_url = database_url if database_url is not None else os.getenv("DATABASE_URL", "").strip()
        self.database_url = configured_url
        self.sqlite_path = sqlite_path or os.getenv("MACRO_SQLITE_PATH", DEFAULT_SQLITE_PATH)
        self.backend = "postgres" if configured_url else "sqlite"
        self.init_schema()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.backend == "postgres" else statement

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.backend == "postgres":
            import psycopg

            conn = psycopg.connect(self.database_url)
        else:
            conn = sqlite3.connect(self.sqlite_path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        identity = "BIGSERIAL PRIMARY KEY" if self.backend == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        with self.connection() as conn:
            cursor = conn.cursor()
            statements = [
                """CREATE TABLE IF NOT EXISTS macro_events_v2 (
                    id TEXT PRIMARY KEY,
                    event_key TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    scheduled_at TEXT,
                    payload TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_macro_events_v2_date ON macro_events_v2(event_date)",
                "CREATE INDEX IF NOT EXISTS idx_macro_events_v2_key ON macro_events_v2(event_key)",
                """CREATE TABLE IF NOT EXISTS macro_observations_v2 (
                    series_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(series_id, period)
                )""",
                """CREATE TABLE IF NOT EXISTS macro_tickers_v2 (
                    symbol TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                )""",
                f"""CREATE TABLE IF NOT EXISTS macro_sync_runs_v2 (
                    id {identity},
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    official INTEGER NOT NULL DEFAULT 0,
                    aggregator INTEGER NOT NULL DEFAULT 0,
                    rejected INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    meta TEXT NOT NULL DEFAULT '{{}}'
                )""",
                "CREATE TABLE IF NOT EXISTS macro_meta_v2 (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            ]
            for statement in statements:
                cursor.execute(statement)
            conn.commit()

    @contextmanager
    def advisory_sync_lock(self) -> Iterator[bool]:
        """Hold a PostgreSQL session lock for the full sync transaction."""
        if self.backend != "postgres":
            yield True
            return
        with self.connection() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,)).fetchone()
            acquired = bool(row and row[0])
            try:
                yield acquired
            finally:
                if acquired:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))

    def event_count(self, start: Optional[str] = None, end: Optional[str] = None) -> int:
        statement = "SELECT COUNT(*) FROM macro_events_v2"
        params: list[Any] = []
        active_at = self.latest_meta().get("fetched_at")
        clauses: list[str] = []
        if active_at:
            clauses.append("last_seen_at = ?")
            params.append(active_at)
        if start and end:
            clauses.append("event_date BETWEEN ? AND ?")
            params.extend([start, end])
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        with self.connection() as conn:
            return int(conn.execute(self._sql(statement), params).fetchone()[0])

    def promote(
        self,
        *,
        events: list[dict[str, Any]],
        observations: dict[str, list[dict[str, Any]]],
        tickers: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> None:
        now = str(meta["fetched_at"])
        with self.connection() as conn:
            cursor = conn.cursor()
            for event in events:
                previous = cursor.execute(
                    self._sql("SELECT payload, first_seen_at FROM macro_events_v2 WHERE id = ?"),
                    (event["id"],),
                ).fetchone()
                revision = 0
                first_seen = now
                if previous:
                    old = json.loads(previous[0])
                    first_seen = previous[1]
                    tracked = ("event_date", "event_time", "scheduled_at_utc", "source_url", "actual", "previous")
                    revision = int(old.get("revision") or 0) + int(any(old.get(key) != event.get(key) for key in tracked))
                event["revision"] = revision
                cursor.execute(self._sql("""INSERT INTO macro_events_v2
                    (id,event_key,event_date,scheduled_at,payload,first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    event_key=excluded.event_key,event_date=excluded.event_date,scheduled_at=excluded.scheduled_at,
                    payload=excluded.payload,last_seen_at=excluded.last_seen_at"""), (
                    event["id"], event["event_key"], event["event_date"], event.get("scheduled_at_utc"),
                    json.dumps(event, ensure_ascii=False), first_seen, now,
                ))
            for series_key, rows in observations.items():
                for row in rows:
                    series_id = str(row.get("series_id") or series_key)
                    cursor.execute(self._sql("""INSERT INTO macro_observations_v2
                        (series_id,period,payload,observed_at) VALUES (?,?,?,?)
                        ON CONFLICT(series_id,period) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at"""), (
                        series_id, row["period"], json.dumps(row, ensure_ascii=False), now,
                    ))
            for ticker in tickers:
                cursor.execute(self._sql("""INSERT INTO macro_tickers_v2(symbol,payload,observed_at) VALUES (?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at"""), (
                    ticker["symbol"], json.dumps(ticker, ensure_ascii=False), now,
                ))
            sync_meta = {key: value for key, value in meta.items() if key not in {"error"}}
            cursor.execute(self._sql("""INSERT INTO macro_sync_runs_v2
                (status,started_at,finished_at,accepted,official,aggregator,rejected,error,meta)
                VALUES (?,?,?,?,?,?,?,?,?)"""), (
                "success", meta["started_at"], now, len(events), meta.get("official", 0),
                meta.get("aggregator", 0), meta.get("rejected", 0), None,
                json.dumps(sync_meta, ensure_ascii=False),
            ))
            cursor.execute(self._sql("""INSERT INTO macro_meta_v2(key,value) VALUES ('active_sync',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value"""), (json.dumps(sync_meta, ensure_ascii=False),))
            conn.commit()

    def record_failure(self, started_at: str, finished_at: str, error: str) -> None:
        with self.connection() as conn:
            conn.execute(self._sql("""INSERT INTO macro_sync_runs_v2
                (status,started_at,finished_at,error,meta) VALUES (?,?,?,?,?)"""),
                ("error", started_at, finished_at, error[:1000], "{}"))
            conn.commit()

    def list_events(self, start: str, end: str) -> list[dict[str, Any]]:
        active_at = self.latest_meta().get("fetched_at")
        active_sql = " AND last_seen_at = ?" if active_at else ""
        params: tuple[Any, ...] = (start, end, active_at) if active_at else (start, end)
        with self.connection() as conn:
            rows = conn.execute(
                self._sql(f"SELECT payload FROM macro_events_v2 WHERE event_date BETWEEN ? AND ?{active_sql} ORDER BY event_date, scheduled_at"),
                params,
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(self._sql("SELECT payload FROM macro_events_v2 WHERE id = ?"), (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, series_id: str, limit: int = 24) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(self._sql("""SELECT payload FROM macro_observations_v2
                WHERE series_id = ? ORDER BY period DESC LIMIT ?"""), (series_id, limit)).fetchall()
        return list(reversed([json.loads(row[0]) for row in rows]))

    def tickers(self) -> list[dict[str, Any]]:
        active_at = self.latest_meta().get("fetched_at")
        with self.connection() as conn:
            if active_at:
                rows = conn.execute(self._sql("SELECT payload FROM macro_tickers_v2 WHERE observed_at = ? ORDER BY symbol"), (active_at,)).fetchall()
            else:
                rows = conn.execute("SELECT payload FROM macro_tickers_v2 ORDER BY symbol").fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_meta(self) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM macro_meta_v2 WHERE key='active_sync'").fetchone()
        return json.loads(row[0]) if row else {}

    def audit(self) -> dict[str, Any]:
        active_at = self.latest_meta().get("fetched_at")
        with self.connection() as conn:
            if active_at:
                event_rows = conn.execute(self._sql("SELECT payload FROM macro_events_v2 WHERE last_seen_at = ?"), (active_at,)).fetchall()
                ticker_rows = conn.execute(self._sql("SELECT payload FROM macro_tickers_v2 WHERE observed_at = ?"), (active_at,)).fetchall()
            else:
                event_rows = conn.execute("SELECT payload FROM macro_events_v2").fetchall()
                ticker_rows = conn.execute("SELECT payload FROM macro_tickers_v2").fetchall()
            last_run = conn.execute("SELECT status,finished_at,error FROM macro_sync_runs_v2 ORDER BY id DESC LIMIT 1").fetchone()
        events = [json.loads(row[0]) for row in event_rows]
        tickers = [json.loads(row[0]) for row in ticker_rows]
        return {
            "events": len(events),
            "official_events": sum(event.get("verification") == "official" for event in events),
            "aggregator_events": sum(event.get("verification") == "aggregator" for event in events),
            "events_with_actual": sum(event.get("actual") is not None for event in events),
            "events_with_actual_without_official_evidence": sum(
                event.get("actual") is not None and not any(
                    evidence.get("source_tier") == "official" for evidence in event.get("evidence") or []
                ) for event in events
            ),
            "stale_tickers": [ticker.get("symbol") for ticker in tickers if ticker.get("stale")],
            "last_run": dict(last_run) if last_run and hasattr(last_run, "keys") else list(last_run) if last_run else None,
        }
