import unittest

from revenue_structure_engine import build_revenue_structure
from trend_table_engine import build_trend_data, get_quarterly_table_schema
from sector_mapping import SECTOR_DEFINITIONS, get_ui_badge
from industry_indicator_profiles import get_industry_profile


class RevenueStructureTests(unittest.TestCase):
    def test_pnj_uses_issuer_disclosure_and_reconciles(self):
        result = build_revenue_structure(
            "PNJ", "RETAIL", lambda *_: 0, lambda *_: 0,
            latest_reported_period="2025-Q4",
        )
        self.assertEqual(result["classification"], "issuer_business_disclosure")
        self.assertEqual(result["period"], "FY2025")
        self.assertAlmostEqual(sum(item["percentage"] for item in result["segments"]), 100.0)
        self.assertTrue(result["reconciliation"]["passed"])

    def test_pnj_historical_mix_is_labelled_as_fallback_for_newer_quarter(self):
        result = build_revenue_structure(
            "PNJ", "RETAIL", lambda *_: 0, lambda *_: 0,
            latest_reported_period="2026-Q2",
        )
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["period"], "FY2025")

    def test_generic_company_uses_reported_accounting_income_sources(self):
        values = {
            ("Doanh thu thuần", "2026-Q2"): 160_000_000_000,
            ("Doanh thu hoạt động tài chính", "2026-Q2"): 906_000_000_000,
        }

        def income(names, period):
            return next((values[(name, period)] for name in names if (name, period) in values), 0)

        result = build_revenue_structure(
            "KDH", "REAL_ESTATE", lambda *_: 0, lambda *_: 0,
            latest_reported_period="2026-Q2",
            get_is_period_item=income,
            reported_periods=["2026-Q2", "2026-Q1"],
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["period"], "2026-Q2")
        self.assertFalse(result["fallback_used"])
        self.assertEqual(len(result["segments"]), 2)

    def test_generic_company_falls_back_to_nearest_reported_quarter(self):
        def income(names, period):
            if period == "2026-Q1" and "Doanh thu thuần" in names:
                return 281_000_000_000
            return 0

        result = build_revenue_structure(
            "KDH", "REAL_ESTATE", lambda *_: 0, lambda *_: 0,
            latest_reported_period="2026-Q2",
            get_is_period_item=income,
            reported_periods=["2026-Q2", "2026-Q1"],
        )
        self.assertEqual(result["period"], "2026-Q1")
        self.assertTrue(result["fallback_used"])

    def test_negative_income_is_not_flipped_positive(self):
        def income(names):
            text = " ".join(names)
            if "ngoại hối" in text:
                return -2_000_000_000
            if "lãi thuần" in text.lower():
                return 10_000_000_000
            return 0

        result = build_revenue_structure("VCB", "BANKING", income, lambda *_: 0)
        self.assertTrue(any(item["amount_billion"] < 0 for item in result["negative_components"]))
        self.assertTrue(all(item["amount_billion"] > 0 for item in result["segments"]))

    def test_real_estate_quality_uses_ttm_cash_flow_and_balance_sheet(self):
        period_values = {
            ("Doanh thu thuần", "2026-Q2"): 90_000_000_000,
            ("Doanh thu hoạt động tài chính", "2026-Q2"): 10_000_000_000,
        }

        def period_income(names, period):
            return next((period_values[(name, period)] for name in names if (name, period) in period_values), 0)

        def ttm_income(names):
            text = " ".join(names).lower()
            if "doanh thu thuần" in text:
                return 400_000_000_000
            if "lợi nhuận sau thuế" in text:
                return 80_000_000_000
            if "lợi nhuận gộp" in text:
                return 160_000_000_000
            return 0

        def balance(names):
            text = " ".join(names).lower()
            if "hàng tồn kho" in text:
                return 600_000_000_000
            if "người mua trả tiền trước" in text:
                return 200_000_000_000
            return 0

        result = build_revenue_structure(
            "VHM", "REAL_ESTATE", ttm_income, balance,
            latest_reported_period="2026-Q2",
            get_is_period_item=period_income,
            reported_periods=["2026-Q2"],
            get_cf_item=lambda _names: -20_000_000_000,
        )
        quality = result["quality_assessment"]
        values = {item["key"]: item["value"] for item in quality["metrics"]}
        self.assertEqual(result["industry_profile"]["sector_name"], "Bất động sản")
        self.assertEqual(values["core_income_share"], 90.0)
        self.assertEqual(values["prepayments_to_revenue"], 50.0)
        self.assertEqual(values["cfo_to_npat"], -0.2)
        self.assertTrue(any("trái dấu" in item for item in quality["warnings"]))


class TrendTests(unittest.TestCase):
    def test_cfo_comes_from_cash_flow_and_yoy_requires_year_ago(self):
        values = {
            "2024-Q1": 50_000_000_000,
            "2025-Q1": 75_000_000_000,
            "2025-Q2": 80_000_000_000,
        }

        def income(names, period):
            if any("nhuận" in name.lower() or name == "LNST" for name in names):
                return values.get(period)
            return 100_000_000_000

        def cash_flow(_names, period):
            return {"2025-Q1": -7_000_000_000, "2025-Q2": 9_000_000_000}.get(period)

        rows = build_trend_data(
            "TECH_TELECOM", ["2025-Q1", "2025-Q2"],
            lambda *_: None, income, cash_flow, frequency="quarter"
        )
        self.assertEqual(rows[0]["cfo"], "-7.0")
        self.assertEqual(rows[0]["yoy_badge"]["pct"], 50.0)
        self.assertIsNone(rows[1]["yoy_badge"])

    def test_annual_comparison_uses_previous_year(self):
        def income(names, period):
            if any("nhuận" in name.lower() or name == "LNST" for name in names):
                return {"2024": 100, "2025": 120}.get(period)
            return None

        rows = build_trend_data("RETAIL", ["2025"], lambda *_: None, income, frequency="year")
        self.assertEqual(rows[0]["yoy_badge"]["pct"], 20.0)

    def test_each_heatmap_sector_has_its_own_analysis_profile(self):
        for archetype, definition in SECTOR_DEFINITIONS.items():
            with self.subTest(archetype=archetype):
                profile = get_industry_profile(archetype)
                schema = get_quarterly_table_schema(archetype)
                badge = get_ui_badge(archetype)
                self.assertEqual(profile["archetype"], archetype)
                self.assertEqual(badge["badge_code"], archetype)
                self.assertEqual(badge["badge"], definition["sector"])
                self.assertEqual(len(schema["columns"]), 5)

    def test_real_estate_trend_uses_project_indicators(self):
        schema = get_quarterly_table_schema("REAL_ESTATE")
        self.assertEqual(
            [column["key"] for column in schema["columns"]],
            ["period", "rev", "project_inventory", "prepayments", "npat"],
        )

    def test_construction_trend_reads_cfo_from_cash_flow(self):
        rows = build_trend_data(
            "CONSTRUCTION", ["2026-Q2"],
            lambda _names, _period: 25_000_000_000,
            lambda names, _period: 10_000_000_000 if any("nhuận" in name.lower() for name in names) else 100_000_000_000,
            lambda _names, _period: -12_000_000_000,
        )
        self.assertEqual(rows[0]["cfo"], "-12.0")


if __name__ == "__main__":
    unittest.main()
