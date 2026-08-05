import unittest

from ai_advisor_engine import _unwrap_article_url
from data_freshness import periods_compatible, period_year_quarter


class DataFreshnessTests(unittest.TestCase):
    def test_financial_period_compatibility(self):
        self.assertTrue(periods_compatible("FY2025", "2025-Q4"))
        self.assertTrue(periods_compatible("H1-2026", "2026-Q2"))
        self.assertFalse(periods_compatible("FY2025", "2026-Q2"))
        self.assertEqual(period_year_quarter("2026-Q2"), (2026, 2))

    def test_bing_article_link_is_unwrapped(self):
        wrapped = "https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.com%2Farticle&id=1"
        self.assertEqual(_unwrap_article_url(wrapped), "https://example.com/article")


if __name__ == "__main__":
    unittest.main()
