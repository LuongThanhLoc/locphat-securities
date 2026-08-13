import unittest

import numpy as np
import pandas as pd

import rrg_engine
import rrg_data_gateway as gateway
from rrg_adjustment import ADJUSTMENT_VERSION, AdjustmentPending, build_total_return_series
from rrg_data_store import SCHEMA_SQL
from rrg_backtest import evaluate_point_in_time


def price_bars(closes, start="2026-01-05"):
    dates = pd.bdate_range(start, periods=len(closes))
    values = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": values,
        "high": values * 1.01,
        "low": values * 0.99,
        "close": values,
        "volume": np.full(len(values), 1_000_000),
    })


class TotalReturnAdjustmentTests(unittest.TestCase):
    def test_cash_dividend_does_not_create_false_loss(self):
        bars = price_bars([100.0, 95.0, 96.0])
        action = {
            "event_id": "AAA:cash", "event_type": "cash_dividend",
            "ex_date": bars.loc[1, "date"], "cash_per_share": 5.0,
            "verification_status": "confirmed",
        }
        result = build_total_return_series(bars, [action])
        adjusted_return = result.frame.loc[1, "total_return_close"] / result.frame.loc[0, "total_return_close"]
        self.assertAlmostEqual(adjusted_return, 1.0, places=8)
        self.assertEqual(result.frame.loc[2, "adjustment_version"], ADJUSTMENT_VERSION)
        self.assertEqual(float(result.frame.loc[2, "raw_close"]), 96.0)

    def test_stock_dividend_does_not_create_false_loss(self):
        bars = price_bars([100.0, 50.0, 51.0])
        action = {
            "event_id": "AAA:stock", "event_type": "stock_dividend",
            "ex_date": bars.loc[1, "date"], "share_ratio": 1.0,
            "verification_status": "confirmed",
        }
        result = build_total_return_series(bars, [action])
        adjusted_return = result.frame.loc[1, "total_return_close"] / result.frame.loc[0, "total_return_close"]
        self.assertAlmostEqual(adjusted_return, 1.0, places=8)

    def test_rights_issue_requires_complete_terms(self):
        bars = price_bars([100.0, 90.0])
        action = {
            "event_id": "AAA:rights", "event_type": "rights_issue",
            "ex_date": bars.loc[1, "date"], "share_ratio": 0.2,
            "verification_status": "confirmed",
        }
        with self.assertRaises(AdjustmentPending):
            build_total_return_series(bars, [action], strict=True)

    def test_revision_produces_different_adjustment_fingerprint_input(self):
        bars = price_bars([100.0, 95.0, 96.0])
        base = {
            "event_id": "AAA:cash", "event_type": "cash_dividend",
            "ex_date": bars.loc[1, "date"], "verification_status": "confirmed",
        }
        first = build_total_return_series(bars, [{**base, "cash_per_share": 4.0}]).frame
        revised = build_total_return_series(bars, [{**base, "cash_per_share": 5.0}]).frame
        self.assertFalse(np.allclose(first["total_return_close"], revised["total_return_close"]))


class MarketScoreTests(unittest.TestCase):
    def _item(self, symbol, value):
        item = rrg_engine._empty_item(symbol, {"sector": "Test", "archetype": "TEST"})
        item.update({
            "rs_ratio": 100 + value,
            "rs_momentum": 100 + value,
            "delta_ratio_5d": value,
            "delta_momentum_5d": value,
            "positive_persistence_5d": 1.0 if value > 0 else 0.0,
        })
        return item

    def test_market_score_is_invariant_when_visible_group_changes(self):
        market = [self._item("AAA", 3), self._item("BBB", 1), self._item("CCC", -2)]
        group_one = [dict(market[0]), dict(market[1])]
        group_two = [dict(market[0]), dict(market[2])]
        rrg_engine._assign_rotation_scores(group_one, reference_items=market)
        rrg_engine._assign_rotation_scores(group_two, reference_items=market)
        self.assertEqual(group_one[0]["rotation_score"], group_two[0]["rotation_score"])
        self.assertNotEqual(group_one[0]["group_rank"], None)


class V2SchemaTests(unittest.TestCase):
    def test_schema_contains_immutable_raw_actions_and_snapshots(self):
        for table in (
            "rrg_raw_observations", "rrg_ingestion_batches", "rrg_security_master",
            "rrg_trading_sessions", "rrg_corporate_actions", "rrg_market_scores",
            "rrg_dataset_snapshots", "rrg_index_membership_snapshots",
        ):
            self.assertIn(table, SCHEMA_SQL)
        self.assertIn("PRIMARY KEY (symbol, trading_date, source, response_hash)", SCHEMA_SQL)


class ExchangeRuleTests(unittest.TestCase):
    def test_rejects_session_outside_official_calendar(self):
        bars = price_bars([25_000, 25_050])
        with self.assertRaises(gateway.DataQualityError):
            gateway.validate_history(
                bars, "AAA", exchange="HOSE",
                trading_calendar=[bars.loc[0, "date"]],
            )

    def test_corporate_action_exempts_price_limit_gap_but_not_tick_size(self):
        bars = price_bars([25_000, 20_000])
        validated = gateway.validate_history(
            bars, "AAA", exchange="HOSE",
            trading_calendar=bars["date"], corporate_action_dates=[bars.loc[1, "date"]],
        )
        self.assertEqual(len(validated), 2)
        bad_tick = bars.copy()
        bad_tick.loc[1, "close"] = 20_025
        with self.assertRaises(gateway.DataQualityError):
            gateway.validate_history(
                bad_tick, "AAA", exchange="HOSE",
                trading_calendar=bad_tick["date"], corporate_action_dates=[bad_tick.loc[1, "date"]],
            )


class PointInTimeBacktestTests(unittest.TestCase):
    def test_forward_test_uses_versioned_snapshot_rows(self):
        sessions = pd.bdate_range("2026-01-05", periods=8).strftime("%Y-%m-%d")
        symbols = ["AAA", "BBB", "CCC"]
        scores = pd.DataFrame([
            {"session": sessions[0], "symbol": symbol, "rotation_score": score,
             "snapshot_id": "snap-1", "universe_version": "u1"}
            for symbol, score in zip(symbols, [90, 50, 10])
        ])
        prices = pd.DataFrame([
            {"session": session, "symbol": symbol, "total_return_close": 100 + index * drift}
            for symbol, drift in zip(symbols, [3, 1, -1])
            for index, session in enumerate(sessions)
        ])
        benchmark = pd.Series([100 + index for index in range(len(sessions))], index=sessions)
        result = evaluate_point_in_time(scores, prices, benchmark, horizons=(5,))
        self.assertEqual(result["snapshot_count"], 1)
        self.assertEqual(result["horizons"]["5"]["observations"], 3)
        self.assertGreater(result["horizons"]["5"]["information_coefficient"], 0)


if __name__ == "__main__":
    unittest.main()
