import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import corporate_calendar_engine as calendar


class CorporateCalendarTests(unittest.TestCase):
    def test_disclosures_are_classified_strictly(self):
        self.assertEqual(calendar._classify("FPT: Báo cáo tài chính quý 2/2026"), "financial_report")
        self.assertEqual(calendar._classify("HPG: Kết quả kinh doanh quý 2/2026"), "financial_report")
        self.assertEqual(calendar._classify("MSN: Giải trình LNST BCTC quý 2/2026"), "financial_report")
        self.assertIsNone(calendar._classify("DPM: Ký hợp đồng với đơn vị kiểm toán BCTC năm 2026"))
        self.assertIsNone(calendar._classify("ACB: Thông báo về việc ký Hợp đồng kiểm toán BCTC năm 2026"))
        self.assertIsNone(calendar._classify("BVB: Thông báo ký hợp đồng kiểm toán BCTC 2026"))
        self.assertIsNone(calendar._classify("DPM: Báo cáo thường niên năm 2025"))

    def test_corporate_actions_are_not_misclassified_as_reports(self):
        self.assertEqual(calendar._classify("CET: Tổ chức ĐHĐCĐ thường niên 2026"), "shareholder_meeting")
        self.assertEqual(calendar._classify("PLX: Ngày ĐKCC chi trả cổ tức năm 2025 bằng tiền"), "dividend")
        self.assertEqual(calendar._classify("SSI: Thông báo phát hành CP để tăng vốn"), "capital_action")

    def test_structured_dividend_uses_exright_date(self):
        row = {
            "id": "dpm-dividend-2026",
            "ticker": "DPM",
            "event_code": "DIV",
            "category": "DIVIDEND",
            "event_title_vi": "Trả cổ tức bằng tiền mặt - Cả năm 2025 - 1,500 VND",
            "public_date": "2026-07-22",
            "exright_date": "2026-08-03",
            "record_date": "2026-08-04",
            "payout_date": "2026-09-22T00:00:00",
            "value_per_share": 1500,
        }
        event = calendar._corporate_action_event(row, date(2026, 8, 1), date(2026, 8, 7))
        self.assertEqual(event["event_date"], "2026-08-03")
        self.assertEqual(event["date_role"], "Ngày GDKHQ")
        self.assertEqual(event["record_date"], "2026-08-04")
        self.assertEqual(event["payout_date"], "2026-09-22")
        self.assertEqual(event["ratio_label"], "1,500 VND/cp")

    def test_get_corporate_calendar_structure_and_query_counts(self):
        today = date.today()
        event = {
            "id": "test:event",
            "symbol": "FPT",
            "event_date": today.isoformat(),
            "published_at": today.isoformat(),
            "type": "financial_report",
            "title": "Công bố BCTC quý",
            "status": "published",
            "date_role": "Ngày công bố",
            "source": "test",
        }
        snapshot = {
            "events": [event],
            "coverage": {
                "confirmed_events": 1,
                "issuer_universe": 1,
                "action_sources_ok": 1,
                "disclosure_sources_ok": 1,
                "source_coverage_pct": 100,
            },
            "source": "test",
            "fetched_at": "2026-08-01T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "calendar.db")
            with patch.object(calendar, "DB_PATH", db_path), patch.object(calendar, "_fetch", return_value=snapshot):
                result = calendar.get_corporate_calendar(today, today + timedelta(days=6), force_refresh=True)
        self.assertEqual(result["coverage"]["returned_events"], 1)
        self.assertEqual(result["coverage"]["returned_symbols"], 1)
        self.assertEqual(result["events"][0]["symbol"], "FPT")
        self.assertIn("nearby_events", result)


if __name__ == "__main__":
    unittest.main()
