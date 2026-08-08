import unittest

from datetime import datetime

from heatmap_engine import (
    HEATMAP_MODEL_VERSION,
    HEATMAP_SCHEMA_VERSION,
    _apply_concentration_baseline,
    _concentration_state,
    _detect_market_anomalies,
    _parse_json_object,
    _upgrade_snapshot_to_v4,
    build_quant_snapshot,
    classify_price_status,
    get_market_session,
)


def stock(symbol, change, value, market_cap, sector="NGAN HANG", volume=1_000, instrument_type="STOCK"):
    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": "HOSE",
        "change_pct": change,
        "trading_value": value,
        "market_cap": market_cap,
        "reference_market_cap": market_cap / (1 + change / 100) if change > -100 else market_cap,
        "volume": volume,
        "ref_price": 10_000,
        "price_vnd": 10_000 * (1 + change / 100),
        "sector": sector,
        "instrument_type": instrument_type,
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
        self.assertEqual(result["model_version"], "lp-market-radar-4.0")
        self.assertEqual(len(result["snapshot_id"]), 16)
        self.assertGreater(stocks[0]["flow_score"], stocks[2]["flow_score"])
        self.assertEqual(sectors[0]["advances"], 2)
        self.assertEqual(sectors[0]["declines"], 1)

    def test_heat_is_symmetric_for_broad_advance_and_selloff(self):
        winners = [stock(f"U{i}", 3.0, 1_000, 1_000_000) for i in range(4)]
        losers = [stock(f"D{i}", -3.0, 1_000, 1_000_000) for i in range(4)]
        up = build_quant_snapshot(winners, [])
        down = build_quant_snapshot(losers, [])
        self.assertEqual(up["market_temperature"], 100.0)
        self.assertEqual(down["market_temperature"], 0.0)
        self.assertEqual(up["advance_decline_state"], "NO_DECLINES")
        self.assertIsNone(up["advance_decline_ratio"])

    def test_balanced_tape_is_neutral_and_traded_unchanged_is_separate(self):
        rows = [
            stock("UP", 3.0, 100, 1_000_000),
            stock("DOWN", -3.0, 100, 1_000_000),
            stock("FLAT", 0.0, 100, 1_000_000),
            stock("IDLE", 0.0, 0, 1_000_000, volume=0),
        ]
        for row in rows:
            row["reference_market_cap"] = 1_000_000
        result = build_quant_snapshot(rows, [])
        self.assertEqual(result["market_temperature"], 50.0)
        self.assertEqual(result["breadth_pct"], 50.0)
        self.assertEqual(result["advance_share_active_pct"], 33.3)
        self.assertEqual(result["directional_participation_pct"], 66.7)
        self.assertEqual(result["unchanged_active_count"], 1)
        self.assertEqual(result["inactive_count"], 1)

    def test_inactive_only_has_explicitly_unavailable_breadth_and_concentration(self):
        rows = [stock("IDLE", 0.0, 0, 1_000_000, volume=0)]
        result = build_quant_snapshot(rows, [])
        self.assertFalse(result["breadth_available"])
        self.assertIsNone(result["breadth_pct"])
        self.assertEqual(result["market_temperature"], 50.0)
        self.assertIsNone(result["top10_liquidity_share_pct"])
        self.assertEqual(result["concentration_state"], "KHONG_DU_DU_LIEU")

    def test_funds_are_excluded_from_every_quant_denominator(self):
        equity = stock("AAA", 1.0, 100, 1_000_000)
        etf = stock("E1VFVN30", -3.0, 900, 9_000_000, instrument_type="ETF")
        result = build_quant_snapshot([equity, etf], [])
        self.assertEqual(result["quant_universe_count"], 1)
        self.assertEqual(result["breadth_pct"], 100.0)
        self.assertEqual(result["top10_liquidity_share_pct"], 100.0)
        self.assertEqual(result["matched_trading_value"], 100)

    def test_concentration_pack_is_reconciled(self):
        rows = [
            stock("AAA", 0, 500, 1_000_000),
            stock("BBB", 0, 300, 1_000_000),
            stock("CCC", 0, 200, 1_000_000),
        ]
        result = build_quant_snapshot(rows, [])
        self.assertEqual(result["top5_liquidity_share_pct"], 100.0)
        self.assertEqual(result["top10_liquidity_share_pct"], 100.0)
        self.assertEqual(result["top20_liquidity_share_pct"], 100.0)
        self.assertEqual(result["liquidity_hhi"], 0.38)
        self.assertEqual(result["effective_stock_count"], 2.6)
        self.assertEqual(_concentration_state(25.0, 60.0), "LAN_TOA")
        self.assertEqual(_concentration_state(40.0, 30.0), "CAN_BANG")
        self.assertEqual(_concentration_state(50.0, 20.0), "TAP_TRUNG")
        self.assertEqual(_concentration_state(65.0, 10.0), "RAT_TAP_TRUNG")

    def test_concentration_baseline_requires_ten_compatible_sessions(self):
        quant = {"snapshot_id": "current", "top10_liquidity_share_pct": 50.0}
        history = [{"quant_snapshot": {
            "model_version": HEATMAP_MODEL_VERSION,
            "snapshot_id": f"s{i}",
            "top10_liquidity_share_pct": 40.0 + (i % 3),
        }} for i in range(10)]
        _apply_concentration_baseline(quant, history[:9])
        self.assertFalse(quant["concentration_baseline"]["available"])
        _apply_concentration_baseline(quant, history)
        self.assertTrue(quant["concentration_baseline"]["available"])
        self.assertEqual(quant["concentration_baseline"]["sessions"], 10)

    def test_legacy_full_snapshot_is_upgraded_in_memory(self):
        rows = [stock("AAA", 1.0, 100, 1_000_000), stock("BBB", -1.0, 100, 1_000_000)]
        payload = {
            "schema_version": 7,
            "summary": {},
            "sectors": [{"name": "TEST", "stocks": rows, "total_trading_value": 200, "total_market_cap": 2_000_000, "avg_change_pct": 0}],
            "quant_snapshot": {"model_version": "lp-market-radar-3.3"},
        }
        upgraded = _upgrade_snapshot_to_v4(payload)
        self.assertEqual(upgraded["schema_version"], HEATMAP_SCHEMA_VERSION)
        self.assertEqual(upgraded["quant_snapshot"]["model_version"], HEATMAP_MODEL_VERSION)
        self.assertEqual(upgraded["quant_snapshot"]["source_snapshot_model_version"], "lp-market-radar-3.3")
        self.assertTrue(upgraded["quant_snapshot"]["recomputed_from_legacy"])

    def test_nullable_breadth_does_not_break_historical_anomaly_checks(self):
        current = {
            "quant_snapshot": {
                "breadth_pct": None,
                "market_temperature": 50.0,
                "active_ratio_pct": 0.0,
                "top10_liquidity_share_pct": None,
            },
            "sectors": [{"name": "TEST", "breadth_pct": None, "flow_score": 50}],
        }
        history = [{
            "quant_snapshot": {
                "breadth_pct": None,
                "market_temperature": 50.0,
                "active_ratio_pct": 0.0,
                "top10_liquidity_share_pct": None,
            },
            "sectors": [{"name": "TEST", "breadth_pct": None, "flow_score": 50}],
        }]
        self.assertEqual(_detect_market_anomalies(current, history), [])

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
