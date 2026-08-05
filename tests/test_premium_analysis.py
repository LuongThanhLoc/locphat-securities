import unittest

from premium_analysis import build_premium_analysis


class PremiumAnalysisTests(unittest.TestCase):
    def _stock(self):
        return {
            "symbol": "AAA", "sector_name": "Bán lẻ", "current_price": 10000,
            "valuation": {"eps_ttm": 1000, "bvps": 8000},
            "data_quality": {"ttm_quarters_used": 4, "latest_reported_period": "2026-Q2"},
            "forensic_analysis": {"muc_do_rui_ro_tong_the": "Sạch"},
            "decision_framework": {
                "total_score": 75,
                "fundamental": {"score": 25},
                "sector_peers": {"score": 16, "available": True},
                "ta_probability": {"score": 16, "available": True, "sample_size": 24, "ma20": 9800, "ma50": 9000, "atr14": 300, "calibration": {"reliable": True}},
                "speed_accuracy": {"score": 17, "data_score": 12},
            },
        }

    def _peers(self):
        return {"companies": [
            {"symbol": "AAA", "metrics": {"pe": 10, "pb": 1.2}},
            {"symbol": "BBB", "metrics": {"pe": 12, "pb": 1.5}},
            {"symbol": "CCC", "metrics": {"pe": 14, "pb": 1.8}},
            {"symbol": "DDD", "metrics": {"pe": 16, "pb": 2.0}},
        ]}

    def test_numbers_are_deterministic_and_auditable(self):
        first = build_premium_analysis(self._stock(), self._peers())
        second = build_premium_analysis(self._stock(), self._peers())
        self.assertEqual(first, second)
        self.assertEqual(first["model_version"], "lp-decision-workbench-v4")
        self.assertTrue(first["valuation"]["available"])
        self.assertEqual(first["confidence"]["passed"], 6)
        self.assertIn("P/E", first["valuation"]["methodology"])

    def test_abstains_when_peer_valuation_is_missing(self):
        result = build_premium_analysis(self._stock(), {"companies": []})
        self.assertFalse(result["valuation"]["available"])
        self.assertFalse(result["trade_setup"]["enabled"])
        self.assertEqual(result["recommendation"]["portfolio_weight"], "0% vị thế mới")


if __name__ == "__main__":
    unittest.main()
