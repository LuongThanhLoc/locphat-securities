import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import corporate_calendar_engine as calendar


class CorporateCalendarTests(unittest.TestCase):
    def test_disclosures_are_classified_strictly(self):
        self.assertEqual(calendar._classify("FPT: Báo cáo tài chính quý 2/2026"), "financial_report")
        self.assertEqual(calendar._classify("HPG: Kết quả kinh doanh quý 2/2026"), "earnings_release")
        self.assertEqual(calendar._classify("MSN: Giải trình LNST BCTC quý 2/2026"), "financial_report")
        negatives = (
            "DPM: Ký hợp đồng với đơn vị kiểm toán BCTC năm 2026",
            "ACB: Thông báo về việc ký Hợp đồng kiểm toán BCTC năm 2026",
            "DPM: Báo cáo thường niên năm 2025",
            "FPT: Nghị quyết Hội đồng quản trị",
            "MSB: Thông báo thay đổi nhân sự",
            "PVS: Báo cáo quản trị công ty bán niên 2026",
            "PNJ: Báo cáo sở hữu của nhóm nhà đầu tư nước ngoài",
        )
        for title in negatives:
            with self.subTest(title=title):
                self.assertIsNone(calendar._classify(title))

    def test_unknown_disclosure_is_not_coerced_to_financial_report(self):
        item = {
            "id": "news-1",
            "newsTitle": "FPT: Nghị quyết Hội đồng quản trị",
            "publicDate": "2026-08-03T16:00:00",
        }
        self.assertIsNone(calendar._disclosure_event(item, "FPT", date(2026, 8, 1), date(2026, 8, 7)))

    def test_media_story_without_explicit_ticker_prefix_is_rejected(self):
        item = {
            "id": "news-2",
            "newsTitle": "Hòa Phát công bố kết quả kinh doanh quý 2/2026 tăng mạnh",
            "publicDate": "2026-08-03T09:00:00",
        }
        self.assertIsNone(calendar._disclosure_event(item, "HPG", date(2026, 8, 1), date(2026, 8, 7)))

    def test_structured_dividend_emits_each_observed_milestone(self):
        row = {
            "id": "dpm-dividend-2026", "ticker": "DPM", "event_code": "DIV",
            "category": "DIVIDEND", "event_title_vi": "Trả cổ tức bằng tiền mặt - Cả năm 2025 - 1,500 VND",
            "public_date": "2026-07-22", "exright_date": "2026-08-03",
            "record_date": "2026-08-04", "payout_date": "2026-09-22T00:00:00",
            "value_per_share": 1500,
        }
        events = calendar._corporate_action_occurrences(row, date(2026, 8, 1), date(2026, 9, 30))
        self.assertEqual({event["date_role_code"] for event in events}, {"ex_right", "record", "payment"})
        ex_event = next(event for event in events if event["date_role_code"] == "ex_right")
        self.assertEqual(ex_event["event_date"], "2026-08-03")
        self.assertEqual(ex_event["date_role"], "Ngày GDKHQ")
        self.assertEqual(ex_event["ratio_label"], "1,500 VND/cp")
        self.assertIsNone(ex_event["event_time"])

    def test_display_date_is_never_relabelled_as_ex_right_date(self):
        row = {
            "id": "sdv-dividend-2026", "ticker": "SDV", "event_code": "DIV",
            "event_title_vi": "Trả cổ tức bằng tiền mặt - Cả năm 2025 - 2,500 VND",
            "display_date1": "2026-08-07", "public_date": "2026-08-07", "value_per_share": 2500,
        }
        event = calendar._corporate_action_event(row, date(2026, 8, 1), date(2026, 8, 9))
        self.assertEqual(event["date_role_code"], "provider_display")
        self.assertEqual(event["date_role"], "Ngày theo nguồn")
        self.assertIsNone(event["exright_date"])
        self.assertFalse(event["source_verified"])

    def test_financial_disclosure_keeps_observed_publication_time_and_evidence(self):
        item = {
            "id": "acv-q2", "newsTitle": "ACV: Báo cáo tài chính quý 2/2026 (công ty mẹ)",
            "publicDate": "2026-08-03T17:31:00", "newsSourceLink": None,
        }
        event = calendar._disclosure_event(item, "ACV", date(2026, 8, 1), date(2026, 8, 7))
        self.assertEqual(event["event_time"], "17:31")
        self.assertEqual(event["details"]["report_period"], "2026-Q2")
        self.assertEqual(event["details"]["report_scope"], "parent")
        self.assertEqual(event["verification"]["status"], "provider_only")
        self.assertFalse(event["source_verified"])

    def test_deduplicate_keeps_distinct_occurrences_of_same_event(self):
        base = {
            "canonical_event_id": "vci:x", "symbol": "FPT", "type": "cash_dividend",
            "title": "Trả cổ tức", "priority": 2,
        }
        rows = [
            {**base, "id": "vci:x:ex_right:2026-08-03", "event_date": "2026-08-03", "date_role_code": "ex_right"},
            {**base, "id": "vci:x:record:2026-08-04", "event_date": "2026-08-04", "date_role_code": "record"},
            {**base, "id": "vci:x:record:2026-08-04", "event_date": "2026-08-04", "date_role_code": "record"},
        ]
        self.assertEqual(len(calendar._deduplicate(rows)), 2)

    def test_promoted_snapshot_returns_v2_quality_and_measured_coverage(self):
        event = {
            "id": "test:event:publication:2026-08-09", "canonical_event_id": "test:event",
            "symbol": "FPT", "exchange": "HOSE", "event_date": "2026-08-09", "event_time": None,
            "date_role": "Ngày công bố", "date_role_code": "publication", "date_role_label": "Ngày công bố",
            "type": "financial_report", "title": "Báo cáo tài chính quý 2/2026", "status": "published",
            "verification": {"status": "provider_only", "sources": [], "conflict_fields": [], "stale": False},
        }
        snapshot = {
            "schema_version": 2, "events": [event],
            "coverage": {"universe_total": 700, "universe_scanned": 700, "action_pages_fetched": 2,
                         "action_pages_total": 2, "action_records_received": 100, "accepted_events": 1,
                         "rejected_items": 99, "conflicts": 0, "partial": False},
            "data_quality": {"no_synthetic_data": True, "stale": False, "partial": False},
            "source": "test", "fetched_at": "2026-08-09T00:00:00+00:00",
            "window_start": "2026-08-01", "window_end": "2026-08-31",
        }
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "calendar.db")
            snapshot_path = os.path.join(directory, "calendar.json")
            with patch.object(calendar, "DB_PATH", db_path), patch.object(calendar, "SNAPSHOT_PATH", snapshot_path):
                calendar._promote_snapshot(snapshot)
                result = calendar.get_corporate_calendar(date(2026, 8, 9), date(2026, 8, 9))
        self.assertEqual(result["schema_version"], 2)
        self.assertTrue(result["no_synthetic_data"])
        self.assertEqual(result["coverage"]["returned_events"], 1)
        self.assertEqual(result["coverage"]["universe_scanned"], 700)
        self.assertEqual(result["coverage"]["rejected_items"], 99)

    def test_partial_snapshot_cannot_replace_last_known_good(self):
        snapshot = {
            "events": [{"id": "x"}], "coverage": {"action_pages_fetched": 1, "action_pages_total": 2},
            "window_start": "2026-08-01", "window_end": "2026-08-31",
        }
        with self.assertRaisesRegex(RuntimeError, "chưa tải đủ trang"):
            calendar._promote_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
