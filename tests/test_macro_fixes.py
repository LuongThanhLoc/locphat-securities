"""Unit tests specifically verifying Macro v2 fixes for N/A elimination and data integrity."""

import os
import tempfile
import unittest
from datetime import datetime
from macro.providers import make_event
from macro.registry import find_indicator, INDICATORS
from macro.service import MacroService
from macro.repository import MacroRepository


class TestMacroV2Fixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = MacroRepository(database_url="", sqlite_path=self.tmp.name)
        self.repo.init_schema()
        self.service = MacroService(repository=self.repo)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except Exception:
                pass

    def test_indicators_registry_expanded(self):
        self.assertIn("ppi", INDICATORS)
        self.assertIn("core_ppi", INDICATORS)
        self.assertIn("michigan_sentiment", INDICATORS)
        self.assertIn("michigan_inflation", INDICATORS)
        self.assertIn("building_permits", INDICATORS)
        self.assertIn("trade_balance", INDICATORS)

        spec = find_indicator("Producer Price Index (PPI m/m)")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.key, "ppi")
        self.assertEqual(spec.category, "inflation")

        spec2 = find_indicator("Core PPI m/m")
        self.assertIsNotNone(spec2)
        self.assertEqual(spec2.key, "core_ppi")
        self.assertEqual(spec2.category, "inflation")

    def test_make_event_forwards_metrics(self):
        ev = make_event(
            publisher="FairEconomy / ForexFactory",
            source_url="https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            title="CPI m/m",
            scheduled=datetime(2026, 8, 12, 12, 30),
            verification="aggregator",
            impact="high",
            forecast="0.2%",
            previous="-0.1%",
            actual="0.2%",
        )
        self.assertEqual(ev["forecast"], "0.2%")
        self.assertEqual(ev["previous"], "-0.1%")
        self.assertEqual(ev["actual"], "0.2%")
        self.assertEqual(ev["indicator_key"], "cpi")
        self.assertEqual(ev["category"], "inflation")
        self.assertEqual(ev["impact_stars"], 3)

    def test_event_merging_deduplication(self):
        ev1 = make_event(
            publisher="U.S. Bureau of Labor Statistics",
            source_url="https://www.bls.gov/news.release/cpi.htm",
            title="Consumer Price Index (CPI)",
            scheduled=datetime(2026, 8, 12, 12, 30),
            verification="official",
            impact="high",
        )
        ev2 = make_event(
            publisher="FairEconomy / ForexFactory",
            source_url="https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            title="CPI m/m",
            scheduled=datetime(2026, 8, 12, 12, 30),
            verification="aggregator",
            impact="high",
            forecast="0.2%",
            previous="-0.1%",
            actual="0.2%",
        )
        merged = MacroService._merge_events([[ev1], [ev2]])
        self.assertEqual(len(merged), 1)
        res = merged[0]
        self.assertEqual(res["verification"], "official")
        self.assertEqual(res["forecast"], "0.2%")
        self.assertEqual(res["previous"], "-0.1%")
        self.assertEqual(res["actual"], "0.2%")

    def test_sync_and_get_calendar_has_no_na(self):
        self.service.sync()
        cal = self.service.get_calendar("2026-08-10", "2026-08-16", country="USD")
        self.assertGreater(len(cal["events"]), 10)
        for ev in cal["events"]:
            self.assertNotEqual(ev.get("actual"), "N/A")
            self.assertNotEqual(ev.get("forecast"), "N/A")
            self.assertNotEqual(ev.get("previous"), "N/A")
            self.assertNotEqual(ev.get("title_vi"), "N/A")


if __name__ == "__main__":
    unittest.main()
