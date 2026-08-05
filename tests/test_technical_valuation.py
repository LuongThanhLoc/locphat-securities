import unittest
from datetime import date, timedelta

from technical_analysis_engine import build_technical_analysis


class TechnicalDecisionTests(unittest.TestCase):
    def test_requires_minimum_sessions(self):
        rows_short = [{"date": str(date(2025, 1, 1) + timedelta(days=i)), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100} for i in range(15)]
        self.assertFalse(build_technical_analysis(rows_short)["available"])
        rows_valid = [{"date": str(date(2025, 1, 1) + timedelta(days=i)), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100} for i in range(35)]
        self.assertTrue(build_technical_analysis(rows_valid)["available"])

    def test_walk_forward_never_uses_insufficient_future_rows(self):
        rows = []
        start = date(2020, 1, 1)
        for i in range(500):
            price = 20_000 + i * 25
            rows.append({"date": str(start + timedelta(days=i)), "open": price - 20, "high": price + 80, "low": price - 80, "close": price, "volume": 1_000_000 + i})
        result = build_technical_analysis(rows)
        self.assertTrue(result["available"])
        self.assertEqual(result["calibration"]["horizon_sessions"], 20)
        self.assertLessEqual(result["calibration"]["sample_size"], len(rows) - 100)


if __name__ == "__main__":
    unittest.main()
