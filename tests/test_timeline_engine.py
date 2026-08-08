"""Tests for the intraday timeline scrubber (Market Radar 4.0).

The poller persists intraday heatmap snapshots to
`heatmap_intraday_snapshots`. These tests exercise the storage helpers and
the poller's phase-decision logic without ever calling the live Vietcap
adapter. They also gate the schema-version bump from 6 → 7 to keep
forward-compat guarantees in lockstep with the storage layer.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, time as dtime, timedelta

# Make the project root importable when running from the tests/ directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import heatmap_engine  # noqa: E402
from heatmap_engine import (  # noqa: E402
    HEATMAP_SCHEMA_VERSION,
    INTRADAY_MAX_PER_DAY,
    INTRADAY_PHASE_INTERVALS,
    SNAPSHOT_RETENTION_DAYS,
    WEEKLY_ANALYSIS_DAYS,
    _classify_intraday_phase,
    _phase_snapshot_interval_seconds,
    _trim_intraday_for_date,
    get_intraday_snapshots,
    get_latest_intraday_snapshot,
    get_recent_snapshots,
    purge_intraday_before,
    save_intraday_snapshot,
    save_snapshot_for_date,
)


class IntradayTimelineTestBase(unittest.TestCase):
    """Redirect the heatmap DB to a temp file so tests don't touch the
    production `heatmap_snapshots.db` (which is committed to the repo)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = heatmap_engine._SNAPSHOT_DB_PATH
        heatmap_engine._SNAPSHOT_DB_PATH = os.path.join(self._tmpdir.name, "heatmap_test.db")
        heatmap_engine._SNAPSHOT_DB_INITIALIZED = False
        heatmap_engine.init_db_snapshot()

    def tearDown(self):
        heatmap_engine._SNAPSHOT_DB_PATH = self._orig_path
        heatmap_engine._SNAPSHOT_DB_INITIALIZED = False
        self._tmpdir.cleanup()

    def _make_payload(self, *, schema_version=8, extra=None):
        payload = {
            "schema_version": schema_version,
            "timestamp": "07/08/2026 10:00:00",
            "is_market_open": True,
            "market_closed": False,
            "snapshot_frozen": False,
            "served_from": "TEST",
            "summary": {
                "total_stocks": 10,
                "advances": 6,
                "declines": 3,
                "unchanged": 1,
                "ceilings": 0,
                "floors": 0,
                "total_market_cap": 1_000_000_000_000,
                "total_trading_value": 5_000_000_000,
            },
            "sectors": [
                {
                    "name": "Ngân hàng",
                    "code": "BANKING",
                    "avg_change_pct": 0.5,
                    "flow_score": 60.0,
                    "breadth_pct": 65.0,
                    "liquidity_share_pct": 25.0,
                    "stocks": [],
                }
            ],
            "quant_snapshot": {
                "market_temperature": 55.0,
                "market_regime": "PHAN_HOA",
                "breadth_pct": 60.0,
                "advance_decline_ratio": 1.5,
                "active_ratio_pct": 80.0,
                "snapshot_id": "TEST-SNAP-1",
            },
        }
        if extra:
            payload.update(extra)
        return payload


class IntradaySnapshotRoundTripTests(IntradayTimelineTestBase):
    def test_save_and_read_back_round_trip(self):
        trade_date = "2026-08-07"
        snapshot_time = f"{trade_date}T10:00:00+07:00"
        payload = self._make_payload()
        save_intraday_snapshot(snapshot_time, "CONTINUOUS", payload)

        items = get_intraday_snapshots(trade_date)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["snapshot_time"], snapshot_time)
        self.assertEqual(items[0]["session_phase"], "CONTINUOUS")
        self.assertEqual(items[0]["payload"]["quant_snapshot"]["snapshot_id"], "TEST-SNAP-1")
        self.assertEqual(items[0]["payload"]["summary"]["total_stocks"], 10)

    def test_old_schema_version_is_filtered_on_read(self):
        trade_date = "2026-08-07"
        # Insert directly with a v6 payload (below the v7 floor).
        with sqlite3.connect(heatmap_engine._SNAPSHOT_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO heatmap_intraday_snapshots
                    (snapshot_time, session_phase, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    f"{trade_date}T09:00:00+07:00",
                    "ATO",
                    '{"schema_version": 6, "summary": {}}',
                    0,
                ),
            )
            conn.commit()
        items = get_intraday_snapshots(trade_date)
        self.assertEqual(items, [], "v6 snapshots must be hidden from the timeline reader")

    def test_get_latest_intraday_snapshot_returns_most_recent(self):
        trade_date = "2026-08-07"
        save_intraday_snapshot(f"{trade_date}T09:01:00+07:00", "ATO", self._make_payload(extra={"summary": {"total_stocks": 1}}))
        save_intraday_snapshot(f"{trade_date}T09:30:00+07:00", "CONTINUOUS", self._make_payload(extra={"summary": {"total_stocks": 2}}))
        save_intraday_snapshot(f"{trade_date}T10:05:00+07:00", "CONTINUOUS", self._make_payload(extra={"summary": {"total_stocks": 3}}))

        latest = get_latest_intraday_snapshot()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["snapshot_time"], f"{trade_date}T10:05:00+07:00")
        self.assertEqual(latest["payload"]["summary"]["total_stocks"], 3)


class IntradayTrimTests(IntradayTimelineTestBase):
    def test_trim_caps_a_day_to_max(self):
        trade_date = "2026-08-07"
        # Insert INTRADAY_MAX_PER_DAY + 20 rows. The trim should keep at
        # most INTRADAY_MAX_PER_DAY non-anchor rows, never touching the
        # 14:45 / 15:10 anchors if they exist.
        for i in range(INTRADAY_MAX_PER_DAY + 20):
            minute = i % 60
            hour = 9 + (i // 60)
            if hour >= 15:
                hour = 14
                minute = min(minute, 44)
            time_label = f"{hour:02d}:{minute:02d}"
            save_intraday_snapshot(
                f"{trade_date}T{time_label}:00+07:00",
                "CONTINUOUS",
                self._make_payload(extra={"summary": {"total_stocks": i}}),
            )
        items = get_intraday_snapshots(trade_date)
        self.assertLessEqual(len(items), INTRADAY_MAX_PER_DAY)
        # Anchor at 14:45 must survive even if it's "old" by timestamp.
        anchor = next((it for it in items if it["snapshot_time"].endswith("T14:45:00+07:00")), None)
        # Anchor insertion order: the test doesn't place one explicitly, so
        # we just assert the count cap rather than anchor presence here.
        self.assertLessEqual(len(items), INTRADAY_MAX_PER_DAY)

    def test_trim_preserves_atc_and_frozen_anchors(self):
        trade_date = "2026-08-07"
        # Insert 100 generic rows, then add the ATC anchor late (so it has
        # the highest id and would normally be the last to delete) plus a
        # frozen 15:10 anchor. The trim should keep both.
        for i in range(100):
            save_intraday_snapshot(
                f"{trade_date}T09:{i % 60:02d}:00+07:00",
                "CONTINUOUS",
                self._make_payload(extra={"summary": {"total_stocks": i}}),
            )
        save_intraday_snapshot(
            f"{trade_date}T14:45:00+07:00",
            "ATC",
            self._make_payload(extra={"summary": {"total_stocks": 999}}),
        )
        save_intraday_snapshot(
            f"{trade_date}T15:10:00+07:00",
            "POST_CLOSE_TRADING",
            self._make_payload(extra={"summary": {"total_stocks": 1000}}),
        )
        items = get_intraday_snapshots(trade_date)
        snapshot_times = {it["snapshot_time"] for it in items}
        self.assertIn(f"{trade_date}T14:45:00+07:00", snapshot_times)
        self.assertIn(f"{trade_date}T15:10:00+07:00", snapshot_times)


class PollerDecisionTests(unittest.TestCase):
    """Cover `_classify_intraday_phase` and `_phase_snapshot_interval_seconds`
    across the full trading day. No DB / I/O involved."""

    def test_classify_phase_returns_canonical_label(self):
        cases = {
            dtime(8, 59): "PRE_OPEN",
            dtime(9, 0): "ATO",
            dtime(9, 14): "ATO",
            dtime(9, 15): "CONTINUOUS",
            dtime(11, 29): "CONTINUOUS",
            dtime(11, 30): "LUNCH_BREAK",
            dtime(12, 59): "LUNCH_BREAK",
            dtime(13, 0): "CONTINUOUS",
            dtime(14, 29): "CONTINUOUS",
            dtime(14, 30): "ATC",
            dtime(14, 44): "ATC",
            dtime(14, 45): "POST_CLOSE_TRADING",
            dtime(14, 59): "POST_CLOSE_TRADING",
            dtime(15, 0): "CLOSED",
            dtime(23, 59): "CLOSED",
        }
        for clock, expected in cases.items():
            with self.subTest(clock=clock):
                self.assertEqual(_classify_intraday_phase(clock), expected)

    def test_interval_per_phase_matches_documented_table(self):
        self.assertEqual(_phase_snapshot_interval_seconds("ATO"), INTRADAY_PHASE_INTERVALS["ATO"])
        self.assertEqual(_phase_snapshot_interval_seconds("CONTINUOUS"), INTRADAY_PHASE_INTERVALS["CONTINUOUS"])
        self.assertEqual(_phase_snapshot_interval_seconds("LUNCH_BREAK"), INTRADAY_PHASE_INTERVALS["LUNCH_BREAK"])
        self.assertEqual(_phase_snapshot_interval_seconds("ATC"), INTRADAY_PHASE_INTERVALS["ATC"])
        # Out-of-session phases should skip captures.
        self.assertEqual(_phase_snapshot_interval_seconds("PRE_OPEN"), 0)
        self.assertEqual(_phase_snapshot_interval_seconds("CLOSED"), 0)
        # Cadences must satisfy the documented ratios: ATO = ATC = 60s,
        # continuous = 300s, lunch = 900s.
        self.assertEqual(_phase_snapshot_interval_seconds("ATO"), 60)
        self.assertEqual(_phase_snapshot_interval_seconds("ATC"), 60)
        self.assertEqual(_phase_snapshot_interval_seconds("CONTINUOUS"), 300)
        self.assertEqual(_phase_snapshot_interval_seconds("LUNCH_BREAK"), 900)

    def test_next_target_progresses_through_phase_boundary(self):
        """When the poller already captured at 09:14 (ATO), the next
        target must fall inside ATO (still 1-minute cadence)."""
        from heatmap_engine import _next_snapshot_target
        now_dt = datetime(2026, 8, 7, 9, 14, 30)  # 09:14:30 local
        last_iso = "2026-08-07T09:14:00+07:00"
        target = _next_snapshot_target(now_dt, last_iso)
        self.assertEqual(target.time().minute, 15)  # next ATO bucket edge
        # After 09:15 the cadence switches to 5 minutes.
        now_dt2 = datetime(2026, 8, 7, 9, 15, 30)
        last_iso2 = "2026-08-07T09:15:00+07:00"
        target2 = _next_snapshot_target(now_dt2, last_iso2)
        self.assertEqual(target2.minute, 20)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_bumped_to_eight(self):
        """Quant v4 publishes schema 8 while legacy persisted readers remain supported."""
        self.assertGreaterEqual(HEATMAP_SCHEMA_VERSION, 8)
        # Existing v6 readers (heatmap_snapshots table) must still pass the
        # `>= 5` gate used by `get_snapshot_for_date` and friends.
        self.assertGreaterEqual(HEATMAP_SCHEMA_VERSION, 5)


class DailySnapshotRetentionTests(IntradayTimelineTestBase):
    def test_retains_twenty_sessions_while_weekly_window_stays_five(self):
        self.assertEqual(SNAPSHOT_RETENTION_DAYS, 20)
        self.assertEqual(WEEKLY_ANALYSIS_DAYS, 5)
        for day in range(1, 23):
            save_snapshot_for_date(
                f"2026-07-{day:02d}",
                self._make_payload(extra={"data_lineage": {"latest_trading_date": f"2026-07-{day:02d}"}}),
                frozen=True,
            )
        self.assertEqual(len(get_recent_snapshots(days=100)), SNAPSHOT_RETENTION_DAYS)


class IntradayRetentionTests(IntradayTimelineTestBase):
    """Verify the today-only retention policy enforced by
    `purge_intraday_before`. The poller calls this on every iteration
    where the latest stored checkpoint belongs to a previous date, so the
    DB must stay bounded to the current trading session."""

    def _seed_multi_day(self):
        yesterday = "2026-08-06"
        today = "2026-08-07"
        # Yesterday: ATO + continuous + ATC + post-close.
        for hh, mm in [(9, 1), (9, 14), (10, 30), (11, 30), (13, 0), (14, 45), (15, 10)]:
            save_intraday_snapshot(
                f"{yesterday}T{hh:02d}:{mm:02d}:00+07:00",
                "CONTINUOUS",
                self._make_payload(),
            )
        # Today: just one ATO row so we can prove it's left intact.
        save_intraday_snapshot(
            f"{today}T09:00:30+07:00",
            "ATO",
            self._make_payload(),
        )

    def test_purge_removes_everything_before_today(self):
        self._seed_multi_day()
        deleted = purge_intraday_before("2026-08-07")
        self.assertEqual(deleted, 7, "All 7 yesterday rows must be deleted")

        remaining_dates = {it["snapshot_time"][:10] for it in get_intraday_snapshots("2026-08-07")}
        self.assertEqual(remaining_dates, {"2026-08-07"})
        # Today's row is untouched.
        today_items = get_intraday_snapshots("2026-08-07")
        self.assertEqual(len(today_items), 1)
        self.assertEqual(today_items[0]["snapshot_time"], "2026-08-07T09:00:30+07:00")

    def test_purge_is_idempotent_on_same_day(self):
        self._seed_multi_day()
        first = purge_intraday_before("2026-08-07")
        second = purge_intraday_before("2026-08-07")
        self.assertEqual(first, 7)
        self.assertEqual(second, 0, "Re-running purge on the same date is a no-op")

    def test_purge_does_not_touch_today_or_future(self):
        self._seed_multi_day()
        purge_intraday_before("2026-08-07")
        # Yesterday's reader still returns nothing (already purged).
        self.assertEqual(get_intraday_snapshots("2026-08-06"), [])
        # Today's row count is unchanged.
        self.assertEqual(len(get_intraday_snapshots("2026-08-07")), 1)

    def test_purge_handles_empty_database(self):
        # DB starts empty (setUp initialises schema only).
        deleted = purge_intraday_before("2026-08-07")
        self.assertEqual(deleted, 0)

    def test_purge_cutoff_uses_vietnam_midnight(self):
        """A snapshot at 00:00:00+07:00 on the cutoff date must NOT be
        deleted (strict `<` comparison) so the first tick of the new day
        survives if it ever lands exactly on midnight."""
        yesterday = "2026-08-06"
        save_intraday_snapshot(
            f"{yesterday}T23:59:30+07:00",
            "CONTINUOUS",
            self._make_payload(),
        )
        save_intraday_snapshot(
            "2026-08-07T00:00:00+07:00",
            "PRE_OPEN",
            self._make_payload(),
        )
        deleted = purge_intraday_before("2026-08-07")
        self.assertEqual(deleted, 1, "Only the 23:59:30 row should be deleted")
        survivors = get_intraday_snapshots("2026-08-07")
        self.assertEqual([it["snapshot_time"] for it in survivors], ["2026-08-07T00:00:00+07:00"])

    def test_purge_does_not_affect_daily_snapshot_table(self):
        """Retention targets `heatmap_intraday_snapshots` only — the main
        `heatmap_snapshots` table (daily cache) must remain untouched."""
        # Insert a fake daily snapshot directly. purge_intraday_before should
        # leave it alone.
        with sqlite3.connect(heatmap_engine._SNAPSHOT_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS heatmap_snapshots (
                    trade_date       TEXT PRIMARY KEY,
                    snapshot_json    TEXT NOT NULL,
                    created_at       INTEGER NOT NULL,
                    is_frozen_15h10  INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "INSERT INTO heatmap_snapshots (trade_date, snapshot_json, created_at, is_frozen_15h10) VALUES (?, ?, ?, ?)",
                ("2026-08-06", '{"schema_version": 7, "summary": {}}', 0, 0),
            )
            conn.commit()
        purge_intraday_before("2026-08-07")
        with sqlite3.connect(heatmap_engine._SNAPSHOT_DB_PATH) as conn:
            row = conn.execute("SELECT trade_date FROM heatmap_snapshots").fetchone()
        self.assertEqual(row[0], "2026-08-06")


if __name__ == "__main__":
    unittest.main()
