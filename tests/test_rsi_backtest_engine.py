import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from rsi_backtest_engine import (
    _aligned_higher_timeframe_rsi,
    _build_equity_curve,
    _calculate_metrics,
    _detect_divergences,
    _frame,
    _rsi,
    _simulate_trades,
)


class RsiDataIntegrityTests(unittest.TestCase):
    def test_rsi_handles_one_way_and_flat_markets_without_fake_missing_values(self):
        rising = _rsi(pd.Series(np.arange(1.0, 25.0)), 14)
        flat = _rsi(pd.Series(np.full(24, 10.0)), 14)
        self.assertEqual(rising.dropna().iloc[-1], 100.0)
        self.assertEqual(flat.dropna().iloc[-1], 50.0)

    def test_frame_never_fabricates_dates_and_keeps_only_real_traded_bars(self):
        self.assertTrue(_frame([{"close": 10}]).empty)

        rows = [
            {"time": "2026-08-07", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"time": "2026-08-08", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},  # Saturday
            {"time": "2026-08-10", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0},
            {"time": "2026-08-11", "open": 10, "high": 9, "low": 8, "close": 10, "volume": 100},
            {"time": "2026-08-12", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        ]
        result = _frame(rows, start=date(2026, 8, 7), end=date(2026, 8, 11))
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-08-07"])

    def test_higher_timeframe_rsi_uses_only_previous_completed_period(self):
        dates = pd.bdate_range("2026-01-05", periods=30)
        frame = pd.DataFrame({"date": dates, "close": np.arange(1.0, 31.0)})
        before = _aligned_higher_timeframe_rsi(frame, "1W", 2)
        changed = frame.copy()
        current_period = changed["date"].dt.to_period("W-FRI").iloc[-1]
        changed.loc[changed["date"].dt.to_period("W-FRI") == current_period, "close"] = 10_000
        after = _aligned_higher_timeframe_rsi(changed, "1W", 2)
        mask = frame["date"].dt.to_period("W-FRI") == current_period
        pd.testing.assert_series_equal(before[mask], after[mask])


class RsiSignalAndExecutionTests(unittest.TestCase):
    def test_confirmed_pivot_emits_one_causal_signal(self):
        dates = pd.bdate_range("2026-01-05", periods=18)
        lows = np.array([10, 9, 8, 7, 5, 7, 8, 9, 10, 9, 8, 4, 8, 9, 10, 11, 12, 13], dtype=float)
        rsi = np.full(18, 50.0)
        rsi[4], rsi[11], rsi[13] = 20.0, 30.0, 45.0
        frame = pd.DataFrame({
            "date": dates,
            "open": lows + 1,
            "high": np.full(18, 20.0),
            "low": lows,
            "close": lows + 1,
            "volume": np.full(18, 100),
            "rsi": rsi,
        })
        signals = _detect_divergences(frame, lookback=8, rsi_entry_min=40, rsi_entry_max=60)
        bullish = [item for item in signals if item["type"] == "bullish"]
        self.assertEqual(len(bullish), 1)
        self.assertEqual(bullish[0]["pivot_date"], dates[11].strftime("%Y-%m-%d"))
        self.assertEqual(bullish[0]["date"], dates[13].strftime("%Y-%m-%d"))

    def test_execution_costs_use_percent_units_and_equity_realises_on_exit(self):
        dates = pd.bdate_range("2026-01-05", periods=5)
        frame = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 5,
            "high": [101.0, 102.0, 111.0, 112.0, 113.0],
            "low": [99.0, 98.0, 89.0, 88.0, 87.0],
            "close": [100.0, 100.0, 110.0, 110.0, 110.0],
            "rsi": [50.0] * 5,
            "atr": [2.0] * 5,
        })
        divergence = [{"signal_bar_index": 0, "type": "bullish", "date": "2026-01-05", "rsi_at_signal": 50.0}]
        trades = _simulate_trades(frame, divergence, holding_days=1, commission_pct=0.1)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["commission_pct"], 0.21, places=4)
        self.assertAlmostEqual(trades[0]["pnl_pct"], 9.79, places=2)

        curve = _build_equity_curve(trades, frame, 100_000_000)
        by_date = {item["date"]: item["equity"] for item in curve}
        self.assertEqual(by_date[trades[0]["entry_date"]], 100_000_000)
        self.assertGreater(by_date[trades[0]["exit_date"]], 100_000_000)

    def test_zero_trades_keep_real_sessions_cash_and_benchmark_curve(self):
        frame = pd.DataFrame({
            "date": pd.bdate_range("2026-01-05", periods=3),
            "close": [100.0, 105.0, 90.0],
        })
        curve = _build_equity_curve([], frame, 100_000_000)
        self.assertEqual(len(curve), len(frame))
        self.assertEqual([point["equity"] for point in curve], [100_000_000] * 3)
        self.assertEqual(curve[0]["benchmark"], 100_000_000)
        self.assertEqual(curve[-1]["benchmark"], 90_000_000)

    def test_short_disabled_is_audited_without_creating_fake_trade(self):
        dates = pd.bdate_range("2026-01-05", periods=4)
        frame = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 4,
            "high": [101.0] * 4,
            "low": [99.0] * 4,
            "close": [100.0] * 4,
            "rsi": [50.0] * 4,
            "atr": [2.0] * 4,
        })
        bearish = [{
            "signal_bar_index": 0,
            "type": "bearish",
            "date": "2026-01-05",
            "rsi_at_signal": 60.0,
        }]
        audit = {}
        trades = _simulate_trades(frame, bearish, include_short=False, execution_audit=audit)
        self.assertEqual(trades, [])
        self.assertEqual(audit["short_disabled"], 1)
        self.assertEqual(sum(audit.values()), 1)

        summary = _calculate_metrics(
            trades, bearish, 0, "2026-01-05", "2026-01-09", 100_000_000
        )
        self.assertEqual(summary["total_signals"], 1)
        self.assertEqual(summary["bullish_signals"], 0)
        self.assertEqual(summary["bearish_signals"], 1)


class RsiTableContractTests(unittest.TestCase):
    def test_divergence_table_defaults_to_newest_first_and_is_sortable(self):
        script = Path("static/backtest.js").read_text(encoding="utf-8")
        html = Path("static/backtest.html").read_text(encoding="utf-8")
        self.assertIn("let divergenceSortColumn = 'date'", script)
        self.assertIn("let divergenceSortDirection = 'desc'", script)
        self.assertIn("function sortDivergences(column)", script)
        self.assertIn('data-sort-column="date" aria-sort="descending"', html)
        self.assertIn("Mô phỏng Short (giả định)", html)
        self.assertIn('id="includeShortToggle" class="toggle-track"', html)
        self.assertIn("execution_audit", script)
        self.assertIn("Có dữ liệu giá nhưng không phát hiện phân kỳ", script)
        self.assertIn("Bật mô phỏng Short (giả định) và chạy lại", script)


if __name__ == "__main__":
    unittest.main()
