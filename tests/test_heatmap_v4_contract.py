import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HeatmapV4ContractTests(unittest.TestCase):
    def test_summary_ui_exposes_participation_concentration_and_inactive_counts(self):
        html = (ROOT / "static" / "heatmap.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "heatmap.js").read_text(encoding="utf-8")

        self.assertIn('id="concentrationDetail"', html)
        self.assertIn('id="inactiveCount"', html)
        self.assertIn("advance_share_active_pct", script)
        self.assertIn("directional_participation_pct", script)
        self.assertIn("effective_stock_count", script)
        self.assertIn("advance_decline_state === 'NO_DECLINES'", script)

    def test_timeline_api_keeps_v4_quant_fields(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        for field in (
            "model_version",
            "heat_confidence",
            "advance_share_active_pct",
            "directional_participation_pct",
            "net_breadth_pct",
            "liquidity_hhi",
            "effective_stock_count",
            "concentration_baseline",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}": quant.get("{field}")', app_source)


if __name__ == "__main__":
    unittest.main()
