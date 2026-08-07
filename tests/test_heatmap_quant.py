import unittest

from datetime import datetime

from heatmap_engine import (
    _parse_json_object,
    build_quant_snapshot,
    classify_price_status,
    get_market_session,
)


def stock(symbol, change, value, market_cap, sector="NGAN HANG", volume=1_000):
    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": "HOSE",
        "change_pct": change,
        "trading_value": value,
        "market_cap": market_cap,
        "volume": volume,
        "ref_price": 10_000,
        "price_vnd": 10_000 * (1 + change / 100),
        "sector": sector,
        "status": "GAIN" if change > 0 else ("LOSS" if change < 0 else "REF"),
    }


class HeatmapQuantTests(unittest.TestCase):
    def test_quant_snapshot_is_deterministic_and_auditable(self):
        stocks = [
            stock("AAA", 2.0, 500_000_000_000, 10_000_000_000_000),
            stock("BBB", 1.0, 300_000_000_000, 8_000_000_000_000),
            stock("CCC", -1.0, 200_000_000_000, 6_000_000_000_000),
        ]
        sectors = [{
            "name": "NGAN HANG",
            "stocks": stocks,
            "avg_change_pct": 0.83,
            "total_trading_value": 1_000_000_000_000,
            "total_market_cap": 24_000_000_000_000,
        }]

        result = build_quant_snapshot(stocks, sectors)

        self.assertEqual(result["breadth_pct"], 66.7)
        self.assertEqual(result["top10_liquidity_share_pct"], 100.0)
        self.assertEqual(result["model_version"], "lp-market-radar-3.4")
        self.assertEqual(len(result["snapshot_id"]), 16)
        self.assertGreater(stocks[0]["flow_score"], stocks[2]["flow_score"])
        self.assertEqual(sectors[0]["advances"], 2)
        self.assertEqual(sectors[0]["declines"], 1)

    def test_untraded_quote_equal_to_floor_is_reference(self):
        result = classify_price_status(0, 100, 120, 100)
        self.assertEqual(result["status"], "REF")
        self.assertEqual(result["match_price"], 100)

    def test_actual_floor_and_ceiling_are_separate_states(self):
        self.assertEqual(classify_price_status(80, 100, 120, 80)["status"], "FLOOR")
        self.assertEqual(classify_price_status(120, 100, 120, 80)["status"], "CEILING")

    def test_market_session_calendar(self):
        self.assertEqual(get_market_session(datetime(2026, 7, 31, 10, 0))["phase"], "MORNING")
        self.assertEqual(get_market_session(datetime(2026, 7, 31, 12, 0))["phase"], "LUNCH_BREAK")
        self.assertEqual(get_market_session(datetime(2026, 7, 31, 14, 35))["phase"], "ATC")
        self.assertEqual(get_market_session(datetime(2026, 7, 31, 15, 1))["phase"], "CLOSED")
        self.assertEqual(get_market_session(datetime(2026, 8, 1, 10, 0))["phase"], "WEEKEND")
        self.assertEqual(get_market_session(datetime(2026, 1, 1, 10, 0))["phase"], "HOLIDAY")
    def test_json_parser_accepts_fenced_object(self):
        parsed = _parse_json_object('```json\n{"headline":"ok"}\n```')
        self.assertEqual(parsed["headline"], "ok")


if __name__ == "__main__":
    unittest.main()
