import math
import unittest
from datetime import date
from unittest.mock import patch

import market_bubble_engine as bubbles


def stock(symbol, *, trading_value=1_000_000, volume=100, instrument="STOCK", sector="NGÂN HÀNG"):
    return {
        "symbol": symbol,
        "name": f"Công ty {symbol}",
        "exchange": "HOSE",
        "instrument_type": instrument,
        "volume": volume,
        "trading_value": trading_value,
        "market_cap": 20_000_000,
        "match_price": 110,
        "ref_price": 100,
        "change_pct": 10,
        "status": "GAIN",
        "sector": sector,
    }


class MarketBubbleEngineTests(unittest.TestCase):
    def test_reference_dates_and_change_are_deterministic(self):
        as_of = date(2026, 8, 7)
        self.assertEqual(bubbles.target_reference_date(as_of, "1W"), date(2026, 7, 31))
        self.assertEqual(bubbles.target_reference_date(as_of, "1M"), date(2026, 7, 8))
        self.assertEqual(bubbles.target_reference_date(as_of, "1Y"), date(2025, 8, 7))
        self.assertEqual(bubbles.calculate_change_pct(110, 100), 10.0)
        self.assertIsNone(bubbles.calculate_change_pct(110, 0))
        self.assertIsNone(bubbles.calculate_change_pct(float("nan"), 100))
        self.assertEqual(bubbles._reference_lag_days(date(2026, 7, 31), "2026-07-17"), 14)
        self.assertEqual(bubbles._reference_lag_days(date(2026, 7, 31), "2026-07-16"), 15)

    def test_universe_is_active_common_stock_and_deduplicated(self):
        duplicate_low_value = stock("VCB", trading_value=10)
        duplicate_high_value = stock("VCB", trading_value=50)
        sectors = [
            {"name": "A", "stocks": [duplicate_low_value, stock("ETF1", instrument="ETF")]},
            {"name": "B", "stocks": [duplicate_high_value, stock("ZERO", trading_value=0, volume=0)]},
        ]
        result = bubbles.dedupe_active_stocks(sectors)
        self.assertEqual([row["symbol"] for row in result], ["VCB"])
        self.assertEqual(result[0]["trading_value"], 50)

    def test_dataset_keeps_missing_history_as_null_without_non_finite_numbers(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB"), stock("BID")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CLOSED"},
            "market_closed": True,
            "snapshot_frozen": True,
        }
        references = {
            "VCB": ("2026-07-08", 100.0, "test-history", 1_786_000_000, bubbles.HISTORY_PRICE_BASIS, "test/history")
        }
        latest = {
            "VCB": ("2026-08-07", 110.0, "test-history", 1_786_000_000, bubbles.HISTORY_PRICE_BASIS, "test/history")
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_references", side_effect=[references, latest, references]), \
             patch.object(bubbles, "get_vn30_members", return_value=({"VCB"}, {"source": "test-index", "stale": False, "fetched_at": 1})), \
             patch.object(bubbles, "start_history_warmup", return_value=True), \
             patch.object(bubbles, "Quote") as quote:
            payload = bubbles.build_market_bubble_dataset("1M")

        self.assertEqual(payload["range"], "1M")
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["price_basis"], bubbles.HISTORY_PRICE_BASIS)
        self.assertTrue(payload["methodology"]["no_synthetic_data"])
        self.assertEqual(payload["methodology"]["max_reference_lag_days"], 14)
        self.assertEqual(payload["coverage"]["available"], 1)
        self.assertEqual(payload["coverage"]["missing"], 1)
        rows = {row["symbol"]: row for row in payload["items"]}
        self.assertEqual(rows["VCB"]["change_pct"], 10.0)
        self.assertEqual(rows["VCB"]["calculation_status"], "OK")
        self.assertEqual(rows["VCB"]["reference_source"], "test-history")
        self.assertEqual(rows["VCB"]["reference_lag_days"], 0)
        self.assertIsNotNone(rows["VCB"]["reference_fetched_at"])
        self.assertTrue(rows["VCB"]["is_vn30"])
        self.assertFalse(rows["BID"]["is_vn30"])
        self.assertIsNone(rows["BID"]["change_pct"])
        self.assertEqual(rows["BID"]["calculation_status"], "MISSING_HISTORY")
        self.assertEqual(payload["indices"]["VN30"]["count"], 1)
        quote.assert_not_called()
        for row in payload["items"]:
            for value in row.values():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value))

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValueError):
            bubbles.build_market_bubble_dataset("1H")

    def test_reference_quality_reconciliation_rejects_large_mismatch(self):
        recent = ("2026-08-07", 400.0, "test-history", 1, bubbles.HISTORY_PRICE_BASIS, "test/history")
        self.assertEqual(
            bubbles._reconciliation_status(stock("VCB"), recent, date(2026, 8, 7), {"is_live_matching": False}),
            "FAILED",
        )
        self.assertFalse(bubbles._prices_reconcile(100, 400))
        self.assertEqual(bubbles.HISTORY_PRICE_BASIS, "ADJUSTED_CLOSE")
        self.assertEqual(bubbles.MAX_REFERENCE_LAG_DAYS, 14)

    def test_reference_older_than_fourteen_days_is_returned_as_missing_change(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CLOSED", "is_live_matching": False},
            "market_closed": True,
        }
        too_old = {
            "VCB": ("2026-06-20", 100.0, "test-history", 1_786_000_000, bubbles.HISTORY_PRICE_BASIS, "test/history")
        }
        latest = {
            "VCB": ("2026-08-07", 110.0, "test-history", 1_786_000_000, bubbles.HISTORY_PRICE_BASIS, "test/history")
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_references", side_effect=[too_old, latest, too_old]), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test", "stale": False})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1M")

        row = payload["items"][0]
        self.assertEqual(row["reference_lag_days"], 18)
        self.assertEqual(row["calculation_status"], "REFERENCE_TOO_OLD")
        self.assertIsNone(row["change_pct"])
        self.assertEqual(payload["coverage"]["calculation_statuses"]["REFERENCE_TOO_OLD"], 1)

    def test_realtime_refresh_uses_the_shared_heatmap_snapshot_cache(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CONTINUOUS", "is_live_matching": True},
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot) as fetch, \
             patch.object(bubbles, "_load_references", return_value={"VCB": ("2025-08-07", 90.0, "test-history")}), \
             patch.object(bubbles, "get_vn30_members", return_value=({"VCB"}, {"source": "test-index", "stale": False, "fetched_at": 1})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1D", force_refresh=True)

        fetch.assert_called_once_with(force_refresh=True)
        self.assertTrue(payload["market_session"]["is_live_matching"])
        self.assertEqual(payload["refresh_interval_seconds"], 5)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["items"][0]["current_source"], "test-board")
        self.assertEqual(payload["items"][0]["calculation_status"], "OK")

    def test_vn30_api_payload_normalization(self):
        self.assertEqual(
            bubbles.normalize_index_symbols([" vcb ", "FPT", "VCB", None, ""]),
            ["FPT", "VCB"],
        )


if __name__ == "__main__":
    unittest.main()
