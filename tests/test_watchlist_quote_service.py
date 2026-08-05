import unittest
from datetime import datetime
from watchlist_quote_service import (
    normalize_symbols,
    quote_from_heatmap_stock,
    quote_from_dnse_payload,
)

class TestWatchlistQuoteService(unittest.TestCase):
    def test_normalize_symbols(self):
        raw = ["fpt", " FPT ", "PNJ", "invalid_123456", "msh", "fpt"]
        cleaned = normalize_symbols(raw)
        self.assertEqual(cleaned, ["FPT", "PNJ", "MSH"])

    def test_quote_from_heatmap_stock(self):
        stock = {
            "symbol": "FPT",
            "name": "Công ty FPT",
            "price_vnd": 105000,
            "ref_price": 100000,
            "ceiling": 107000,
            "floor": 93000,
            "trading_date": "2026-08-04",
        }
        snapshot = {
            "trading_date": "2026-08-04",
            "data_lineage": {"latest_trading_date": "2026-08-04"},
            "data_quality": {"status": "VERIFIED"},
        }
        session = {"phase": "CLOSED"}
        q = quote_from_heatmap_stock(stock, snapshot, session)

        self.assertEqual(q["symbol"], "FPT")
        self.assertEqual(q["price_vnd"], 105000)
        self.assertEqual(q["reference_price_vnd"], 100000)
        self.assertEqual(q["change_vnd"], 5000)
        self.assertEqual(q["change_pct"], 5.0)
        self.assertEqual(q["price_type"], "close_snapshot")

    def test_quote_from_dnse_payload(self):
        trade = {
            "matchPrice": 31.0,
            "boardId": "HOSE",
            "exchange_time": "2026-08-04T14:40:00Z",
        }
        secdef = {
            "basicPrice": 30.0,
            "ceilingPrice": 32.1,
            "floorPrice": 27.9,
        }
        session = {"phase": "AFTERNOON", "calendar_date": "2026-08-04"}
        q = quote_from_dnse_payload("MSH", trade, secdef, session)

        self.assertEqual(q["symbol"], "MSH")
        self.assertEqual(q["price_vnd"], 31000.0)
        self.assertEqual(q["reference_price_vnd"], 30000.0)
        self.assertEqual(q["change_vnd"], 1000.0)
        self.assertAlmostEqual(q["change_pct"], 3.33, places=2)
        self.assertEqual(q["price_type"], "realtime")

if __name__ == "__main__":
    unittest.main()
