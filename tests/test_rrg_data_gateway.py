import os
import unittest
from unittest.mock import patch

import pandas as pd

import rrg_data_gateway as gateway
from rrg_data_store import PostgresRrgStore, RrgStoreUnavailable, SCHEMA_SQL


def bars(periods=300, start="2025-01-01", price=25000.0):
    dates = pd.date_range(start, periods=periods, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": price,
        "high": price * 1.01,
        "low": price * 0.99,
        "close": price,
        "volume": 1_000_000,
    })


class MemoryStore:
    def __init__(self, frame=None, state=None):
        self.frame = frame if frame is not None else pd.DataFrame()
        self._state = state or {}
        self.failures = []
        self.quarantined = []

    def load_history(self, symbol, start, end):
        return self.frame.copy()

    def state(self, symbol):
        return dict(self._state)

    def upsert_history(self, symbol, frame, source, chain):
        self.frame = gateway._merge_frames(self.frame, frame, symbol)
        self._state.update({"last_source": source, "last_success_at": "2026-08-08T00:00:00Z"})

    def record_failure(self, symbol, error, status="source_unavailable"):
        self.failures.append((symbol, error, status))

    def quarantine(self, symbol, source, reason, payload):
        self.quarantined.append((symbol, source, reason))


class QualityValidationTests(unittest.TestCase):
    def setUp(self):
        gateway._RAM_CACHE.clear()
        for circuit in gateway._CIRCUITS.values():
            circuit.failures = 0
            circuit.opened_until = 0

    def test_rejects_ssi_philippines_collision(self):
        wrong = bars(periods=10, price=2.05)
        wrong.loc[0, "date"] = "2026-08-02"  # Sunday, as seen in the bad MSN series.
        with self.assertRaises(gateway.DataQualityError):
            gateway.validate_history(wrong, "SSI")

    def test_rejects_inconsistent_ohlc_and_non_finite_values(self):
        bad = bars(periods=3)
        bad.loc[1, "low"] = bad.loc[1, "high"] + 1
        with self.assertRaises(gateway.DataQualityError):
            gateway.validate_history(bad, "FPT")

    def test_vietcap_failure_falls_back_to_kbs(self):
        valid = bars(periods=300)
        valid["date"] = pd.bdate_range(end="2026-08-07", periods=300).strftime("%Y-%m-%d")
        providers = (
            ("Vietcap", lambda *_: (_ for _ in ()).throw(TimeoutError("timeout"))),
            ("KBS", lambda *_: valid),
        )
        with patch.object(gateway, "PROVIDERS", providers), patch("rrg_data_gateway.time.sleep"):
            frame, source, chain = gateway._provider_attempts(
                "SSI", "2025-01-01", "2026-08-08", pd.DataFrame(), MemoryStore()
            )
        self.assertEqual(source, "KBS")
        self.assertEqual(len(frame), 300)
        self.assertTrue(any(item["source"] == "Vietcap" and item["status"] == "error" for item in chain))

    def test_both_sources_fail_uses_recent_verified_cache(self):
        cached = bars(periods=300, start="2025-06-16")
        # One benchmark/weekday session behind: try upstream, then serve stale-good.
        cached.loc[cached.index[-1], "date"] = "2026-08-06"
        store = MemoryStore(cached, {"last_source": "Vietcap", "last_success_at": "2026-08-07T09:00:00Z"})
        failing = (("Vietcap", lambda *_: pd.DataFrame()), ("KBS", lambda *_: pd.DataFrame()))
        with patch.object(gateway, "PROVIDERS", failing), patch("rrg_data_gateway.time.sleep"):
            result = gateway.get_verified_history(
                "SSI", "2025-01-01", "2026-08-08", store=store, require_store=True
            )
        self.assertEqual(result.quality_status, "stale_valid")
        self.assertTrue(result.served_from_cache)

    def test_cache_older_than_three_sessions_fails_closed(self):
        cached = bars(periods=260, start="2025-01-01")
        cached.loc[cached.index[-1], "date"] = "2026-07-31"
        store = MemoryStore(cached)
        failing = (("Vietcap", lambda *_: pd.DataFrame()), ("KBS", lambda *_: pd.DataFrame()))
        with patch.object(gateway, "PROVIDERS", failing), patch("rrg_data_gateway.time.sleep"):
            with self.assertRaises(gateway.HistoryUnavailable):
                gateway.get_verified_history(
                    "SSI", "2025-01-01", "2026-08-08", store=store, require_store=True
                )

    def test_two_sources_confirm_inactive_symbol(self):
        old = bars(periods=260, start="2025-01-01")
        old.loc[old.index[-1], "date"] = "2026-07-31"
        store = MemoryStore()
        providers = (("Vietcap", lambda *_: old), ("KBS", lambda *_: old))
        with patch.object(gateway, "PROVIDERS", providers), patch("rrg_data_gateway.time.sleep"):
            result = gateway.get_verified_history(
                "OLD", "2025-01-01", "2026-08-08", store=store, require_store=True
            )
        self.assertEqual(result.quality_status, "inactive")
        self.assertGreater(result.freshness_sessions, 3)


class StoreContractTests(unittest.TestCase):
    def test_schema_has_durable_keys_and_quarantine(self):
        self.assertIn("PRIMARY KEY (symbol, trading_date)", SCHEMA_SQL)
        self.assertIn("rrg_sync_state", SCHEMA_SQL)
        self.assertIn("rrg_quarantine", SCHEMA_SQL)

    def test_database_url_is_required(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RrgStoreUnavailable):
            PostgresRrgStore()


if __name__ == "__main__":
    unittest.main()
