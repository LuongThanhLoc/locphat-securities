import math
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_bubble_engine as bubbles


def stock(
    symbol, *, trading_value=1_000_000, volume=100, instrument="STOCK",
    sector="NGÂN HÀNG", exchange="HOSE", sector_memberships=None,
):
    return {
        "symbol": symbol,
        "name": f"Công ty {symbol}",
        "exchange": exchange,
        "instrument_type": instrument,
        "volume": volume,
        "trading_value": trading_value,
        "market_cap": 20_000_000,
        "match_price": 110,
        "ref_price": 100,
        "change_pct": 10,
        "status": "GAIN",
        "sector": sector,
        "sector_memberships": sector_memberships or [{"sector": sector, "archetype": "TEST"}],
    }


def bar(
    day="2026-07-08", open_price=100.0, close=101.0, *,
    verification="CROSS_SOURCE_MATCH", source="Vietcap",
):
    return bubbles.HistoryBar(
        day, open_price, max(open_price, close), min(open_price, close), close,
        source, 1_786_251_600, bubbles.HISTORY_PRICE_BASIS, "test/history",
        verification, "KBS", open_price, close,
    )


class MarketBubbleEngineTests(unittest.TestCase):
    def setUp(self):
        bubbles._WARM_STATE.update({
            "running": False, "completed": 0, "total": 0, "error": None,
            "last_started_at": 0, "as_of": None,
        })
        bubbles._ACTION_STATE.update({
            "running": False, "completed": 0, "total": 0, "error": None,
            "last_started_at": 0, "as_of": None,
        })
        self.action_needed = patch.object(bubbles, "_symbols_needing_action_audit", return_value=[])
        self.action_start = patch.object(bubbles, "start_action_warmup", return_value=False)
        self.action_needed.start()
        self.action_start.start()
        self.addCleanup(self.action_needed.stop)
        self.addCleanup(self.action_start.stop)

    def test_reference_dates_and_change_are_deterministic(self):
        as_of = date(2026, 8, 7)
        self.assertEqual(bubbles.target_reference_date(as_of, "1W"), date(2026, 7, 31))
        self.assertEqual(bubbles.target_reference_date(as_of, "1M"), date(2026, 7, 8))
        self.assertEqual(bubbles.target_reference_date(as_of, "1Y"), date(2025, 8, 7))
        self.assertEqual(bubbles.calculate_change_pct(110, 100), 10.0)
        self.assertIsNone(bubbles.calculate_change_pct(110, 0))
        self.assertIsNone(bubbles.calculate_change_pct(float("nan"), 100))
        self.assertEqual(bubbles._reference_lag_days(date(2026, 7, 31), "2026-07-17"), 14)
        self.assertEqual(bubbles._reference_lag_days(date(2026, 7, 31), "2026-07-16"), 15)

    def test_vn30_membership_delegates_to_shared_verified_gateway(self):
        meta = {"snapshot_id": "shared-vn30", "source_agreement": True, "stale": False}
        with patch.object(bubbles, "get_index_membership", return_value=(["FPT", "VCB"], meta)) as gateway:
            symbols, returned_meta = bubbles.get_vn30_members(force_refresh=True)
        gateway.assert_called_once_with("VN30", force_refresh=True)
        self.assertEqual(symbols, {"FPT", "VCB"})
        self.assertEqual(returned_meta["snapshot_id"], "shared-vn30")

    def test_tradingview_golden_examples_use_anchor_open_not_close(self):
        self.assertEqual(bubbles.calculate_change_pct(41_500, 65_000), -36.15)
        self.assertEqual(bubbles.calculate_change_pct(215_000, 59_250), 262.87)
        self.assertNotEqual(bubbles.calculate_change_pct(41_500, 67_100), -36.15)
        self.assertNotEqual(bubbles.calculate_change_pct(215_000, 57_500), 262.87)

    def test_universe_is_active_common_stock_and_deduplicated(self):
        sectors = [
            {"name": "A", "stocks": [stock("VCB", trading_value=10), stock("ETF1", instrument="ETF")]},
            {"name": "B", "stocks": [stock("VCB", trading_value=50), stock("ZERO", trading_value=0, volume=0)]},
        ]
        result = bubbles.dedupe_active_stocks(sectors)
        self.assertEqual([row["symbol"] for row in result], ["VCB"])
        self.assertEqual(result[0]["trading_value"], 50)

    def test_full_universe_keeps_idle_stocks_and_merges_all_sector_memberships(self):
        sectors = [
            {"name": "Ngân hàng", "stocks": [stock(
                "VCB", trading_value=0, volume=0,
                sector="Ngân hàng",
                sector_memberships=[{"sector": "Ngân hàng", "archetype": "BANK"}],
            )]},
            {"name": "VN30", "stocks": [stock(
                "VCB", trading_value=0, volume=0,
                sector="Ngân hàng",
                sector_memberships=[{"sector": "Bluechip", "archetype": "LARGE_CAP"}],
            )]},
        ]
        result = bubbles.dedupe_common_stocks(sectors, require_active=False)
        self.assertEqual([row["symbol"] for row in result], ["VCB"])
        self.assertFalse(result[0]["is_active"])
        self.assertEqual(
            {membership["sector"] for membership in result[0]["sector_memberships"]},
            {"Ngân hàng", "Bluechip"},
        )

    def test_every_market_phase_keeps_idle_vn30_and_filter_counts_reconcile(self):
        phases = ("PRE_OPEN", "ATO", "CONTINUOUS", "LUNCH_BREAK", "ATC", "CLOSED")
        for phase in phases:
            with self.subTest(phase=phase):
                snapshot = {
                    "sectors": [{"name": "Ngân hàng", "stocks": [
                        stock("VCB", trading_value=0, volume=0),
                        stock("SHS", trading_value=500, volume=5, exchange="HNX"),
                    ]}],
                    "data_lineage": {
                        "latest_trading_date": "2026-08-10", "price_source": "test-board",
                        "fetched_at": "2026-08-10T09:10:00+07:00",
                    },
                    "market_session": {
                        "phase": phase, "calendar_date": "2026-08-10",
                        "is_live_matching": phase in {"ATO", "CONTINUOUS", "ATC"},
                    },
                    "market_closed": phase == "CLOSED",
                }
                with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
                     patch.object(bubbles, "_load_reference_bars", return_value={}), \
                     patch.object(bubbles, "get_vn30_members", return_value=({"VCB"}, {"source": "test", "stale": False})), \
                     patch.object(bubbles, "start_history_warmup", return_value=False):
                    payload = bubbles.build_market_bubble_dataset("1D")

                self.assertEqual(payload["schema_version"], 5)
                self.assertEqual({item["symbol"] for item in payload["items"]}, {"VCB", "SHS"})
                rows = {item["symbol"]: item for item in payload["items"]}
                self.assertFalse(rows["VCB"]["is_active"])
                self.assertEqual(rows["VCB"]["index_memberships"], ["VN30"])
                self.assertEqual(rows["VCB"]["sector_memberships"][0]["sector"], "NGÂN HÀNG")
                groups = {group["key"]: group for group in payload["filter_groups"]}
                self.assertEqual(groups["INDEX:VN30"]["total_count"], 1)
                self.assertEqual(groups["INDEX:VN30"]["active_count"], 0)
                self.assertEqual(groups["SECTOR:NGÂN HÀNG"]["total_count"], 2)
                self.assertEqual(groups["SECTOR:NGÂN HÀNG"]["active_count"], 1)

    def test_pre_open_1d_keeps_idle_stocks_and_resets_session_metrics(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [
                stock("VCB", trading_value=0, volume=0),
                stock("BID", trading_value=0, volume=0),
            ]}],
            "data_lineage": {
                "latest_trading_date": "2026-08-07", "price_source": "test-board",
                "fetched_at": "2026-08-10T08:45:00+07:00",
            },
            "market_session": {
                "phase": "PRE_OPEN", "calendar_date": "2026-08-10",
                "is_live_matching": False,
            },
            "market_closed": False,
            "snapshot_frozen": True,
            "served_from": "SQLITE_CLOSE_SNAPSHOT",
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", return_value={}), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1D")

        self.assertTrue(payload["session_reset_applied"])
        self.assertEqual(payload["session_date"], "2026-08-10")
        self.assertEqual(payload["coverage"]["total"], 2)
        self.assertEqual({item["symbol"] for item in payload["items"]}, {"VCB", "BID"})
        for item in payload["items"]:
            self.assertEqual(item["change_pct"], 0.0)
            self.assertEqual(item["volume"], 0.0)
            self.assertEqual(item["trading_value"], 0.0)
            self.assertEqual(item["status"], "REF")
            self.assertEqual(item["calculation_status"], "SESSION_NOT_STARTED")
            self.assertEqual(item["market_cap"], 20_000_000)

    def test_pre_open_historical_range_keeps_historical_performance(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB", trading_value=0, volume=0)]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "PRE_OPEN", "calendar_date": "2026-08-10", "is_live_matching": False},
        }
        anchors = {"VCB": bar()}
        latest = {"VCB": bar("2026-08-07", 109.0, 110.0)}
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", side_effect=[anchors, latest, anchors]), \
             patch.object(bubbles, "_load_action_audits", return_value={"VCB": {"status": "OK", "events": []}}), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1M")

        self.assertFalse(payload["session_reset_applied"])
        self.assertEqual(payload["items"][0]["change_pct"], 10.0)

    def test_dataset_v5_only_exposes_verified_change(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB"), stock("BID")]}],
            "data_lineage": {
                "latest_trading_date": "2026-08-07", "price_source": "test-board",
                "fetched_at": "2026-08-07T15:00:00+07:00",
            },
            "market_session": {"phase": "CLOSED", "is_live_matching": False},
            "market_closed": True,
        }
        references = {"VCB": bar()}
        latest = {"VCB": bar("2026-08-07", 109.0, 110.0)}
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", side_effect=[references, latest, references]), \
             patch.object(bubbles, "_load_action_audits", return_value={"VCB": {"status": "OK", "events": []}}), \
             patch.object(bubbles, "get_vn30_members", return_value=({"VCB"}, {"source": "test-index", "stale": False})), \
             patch.object(bubbles, "start_history_warmup", return_value=True):
            payload = bubbles.build_market_bubble_dataset("1M")

        self.assertEqual(payload["schema_version"], 5)
        self.assertEqual(payload["metric_definition"], "TRADINGVIEW_SCREENER_PERFORMANCE")
        self.assertEqual(payload["anchor_field"], "open")
        self.assertEqual(payload["formula"], bubbles.HISTORY_CHANGE_FORMULA)
        rows = {row["symbol"]: row for row in payload["items"]}
        self.assertEqual(rows["VCB"]["reference_price"], 100.0)
        self.assertEqual(rows["VCB"]["anchor_open"], 100.0)
        self.assertEqual(rows["VCB"]["anchor_close"], 101.0)
        self.assertEqual(rows["VCB"]["reference_price_field"], "open")
        self.assertEqual(rows["VCB"]["change_pct"], 10.0)
        self.assertEqual(rows["VCB"]["calculation_status"], "OK")
        self.assertEqual(rows["VCB"]["data_confidence"], "VERIFIED")
        self.assertIsNone(rows["BID"]["change_pct"])
        self.assertEqual(rows["BID"]["calculation_status"], "MISSING_HISTORY")
        self.assertEqual(rows["BID"]["data_confidence"], "UNAVAILABLE")
        self.assertEqual(payload["coverage"]["available"], 1)
        self.assertEqual(payload["coverage"]["missing"], 1)
        for row in payload["items"]:
            for value in row.values():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value))

    def test_source_disagreement_is_gray_not_synthetic(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CLOSED", "is_live_matching": False},
            "market_closed": True,
        }
        disagreeing = {"VCB": bar(verification="SOURCE_DISAGREEMENT")}
        latest = {"VCB": bar("2026-08-07", 109.0, 110.0)}
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", side_effect=[disagreeing, latest, disagreeing]), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1M")
        row = payload["items"][0]
        self.assertEqual(row["calculation_status"], "SOURCE_DISAGREEMENT")
        self.assertIsNone(row["change_pct"])

    def test_reference_older_than_fourteen_days_is_rejected(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CLOSED", "is_live_matching": False},
            "market_closed": True,
        }
        too_old = {"VCB": bar("2026-06-20")}
        latest = {"VCB": bar("2026-08-07", 109.0, 110.0)}
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", side_effect=[too_old, latest, too_old]), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1M")
        self.assertEqual(payload["items"][0]["calculation_status"], "REFERENCE_TOO_OLD")
        self.assertIsNone(payload["items"][0]["change_pct"])

    def test_cache_v3_ignores_legacy_close_only_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = str(Path(temp_dir) / "bubbles.db")
            with patch.object(bubbles, "_CACHE_PATH", cache_path), patch.object(bubbles, "_DB_READY", False):
                bubbles.init_bubble_cache()
                with sqlite3.connect(cache_path) as conn:
                    conn.execute(
                        "INSERT INTO market_bubble_daily_closes(symbol,trading_date,close,source,fetched_at,price_basis) VALUES(?,?,?,?,?,?)",
                        ("VCB", "2025-08-07", 90, "legacy", 1, "ADJUSTED_CLOSE"),
                    )
                    conn.commit()
                self.assertEqual(bubbles._load_reference_bars(["VCB"], date(2025, 8, 7)), {})

    def test_fetch_history_keeps_one_primary_series_and_records_comparison(self):
        primary = pd.DataFrame([{"time": "2025-08-07", "open": 65_000, "high": 68_000, "low": 64_000, "close": 67_100, "volume": 1}])
        comparison = pd.DataFrame([{"time": "2025-08-07", "open": 65_000, "high": 68_000, "low": 64_000, "close": 67_100, "volume": 1}])
        with patch.object(bubbles, "fetch_vci_history", return_value=primary), \
             patch.object(bubbles, "fetch_kbs_history", return_value=comparison):
            symbol, rows, source, basis, _endpoint = bubbles._fetch_symbol_history(
                "ACV", date(2025, 8, 1), date(2026, 8, 7),
            )
        self.assertEqual((symbol, source, basis), ("ACV", "Vietcap", bubbles.HISTORY_PRICE_BASIS))
        self.assertEqual(rows[0]["open"], 65_000)
        self.assertEqual(rows[0]["verification_status"], "CROSS_SOURCE_MATCH")
        self.assertEqual(rows[0]["comparison_source"], "KBS")

    def test_realtime_refresh_uses_shared_heatmap_snapshot_cache(self):
        snapshot = {
            "sectors": [{"name": "Ngân hàng", "stocks": [stock("VCB")]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CONTINUOUS", "is_live_matching": True},
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot) as fetch, \
             patch.object(bubbles, "_load_reference_bars", return_value={}), \
             patch.object(bubbles, "get_vn30_members", return_value=({"VCB"}, {"source": "test-index"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1D", force_refresh=True)
        fetch.assert_called_once_with(force_refresh=True)
        self.assertEqual(payload["refresh_interval_seconds"], 5)
        self.assertEqual(payload["schema_version"], 5)
        self.assertEqual(payload["metric_definition"], "SESSION_CHANGE")
        self.assertEqual(payload["items"][0]["calculation_status"], "OK")
        self.assertEqual(payload["items"][0]["data_confidence"], "VERIFIED")

    def test_single_source_and_corporate_action_never_expose_candidate(self):
        target = date(2025, 8, 7)
        as_of = date(2026, 8, 7)
        latest = bar("2026-08-07", 109, 110)
        single = bar("2025-08-07", verification="PRIMARY_ONLY")._replace(
            comparison_source=None, comparison_open=None, comparison_close=None,
        )
        decision = bubbles._history_quality_decision(
            110, single, latest, target, as_of, "PASSED", {"status": "OK", "events": []},
        )
        self.assertEqual(decision["reason_code"], "SINGLE_SOURCE")
        event_decision = bubbles._history_quality_decision(
            41_500, bar("2025-08-07", 65_000, 67_100), latest,
            target, as_of, "PASSED",
            {"status": "OK", "events": [{"type": "cash_dividend", "event_date": "2026-01-01"}]},
        )
        self.assertEqual(event_decision["reason_code"], "CORPORATE_ACTION_UNVERIFIED")
        self.assertEqual(event_decision["data_confidence"], "UNVERIFIED")

    def test_cross_source_threshold_checks_both_open_and_close(self):
        self.assertTrue(bubbles._values_agree(20_000, 20_100))  # 100 đồng
        self.assertFalse(bubbles._values_agree(20_000, 20_101))
        self.assertTrue(bubbles._values_agree(100_000, 100_500))  # 0,5%
        self.assertFalse(bubbles._values_agree(100_000, 100_501))

    def test_acv_golden_verified_but_vic_event_is_hidden(self):
        snapshot = {
            "sectors": [{"name": "Hạ tầng", "stocks": [
                {**stock("ACV"), "match_price": 41_500, "ref_price": 41_000},
                {**stock("VIC"), "match_price": 215_000, "ref_price": 210_000},
            ]}],
            "data_lineage": {"latest_trading_date": "2026-08-07", "price_source": "test-board"},
            "market_session": {"phase": "CLOSED", "is_live_matching": False},
            "market_closed": True,
        }
        anchors = {
            "ACV": bar("2025-08-07", 65_000, 67_100),
            "VIC": bar("2025-08-07", 59_250, 57_500),
        }
        latest = {
            "ACV": bar("2026-08-07", 41_000, 41_500),
            "VIC": bar("2026-08-07", 210_000, 215_000),
        }
        audits = {
            "ACV": {"status": "OK", "events": []},
            "VIC": {"status": "OK", "events": [{
                "type": "stock_dividend", "event_date": "2025-12-05", "title": "Thưởng cổ phiếu 100%",
            }]},
        }
        with patch.object(bubbles, "fetch_market_heatmap_data", return_value=snapshot), \
             patch.object(bubbles, "_load_reference_bars", side_effect=[anchors, latest, anchors]), \
             patch.object(bubbles, "_load_action_audits", return_value=audits), \
             patch.object(bubbles, "get_vn30_members", return_value=(set(), {"source": "test"})), \
             patch.object(bubbles, "start_history_warmup", return_value=False):
            payload = bubbles.build_market_bubble_dataset("1Y")
        rows = {row["symbol"]: row for row in payload["items"]}
        self.assertEqual(rows["ACV"]["change_pct"], -36.15)
        self.assertEqual(rows["ACV"]["data_confidence"], "VERIFIED")
        self.assertIsNone(rows["VIC"]["change_pct"])
        self.assertEqual(rows["VIC"]["reason_code"], "CORPORATE_ACTION_UNVERIFIED")
        self.assertEqual(payload["coverage"]["verified"], 1)
        self.assertEqual(payload["coverage"]["unverified"], 1)

    def test_invalid_range_and_vn30_normalization(self):
        with self.assertRaises(ValueError):
            bubbles.build_market_bubble_dataset("1H")
        self.assertEqual(bubbles.normalize_index_symbols([" vcb ", "FPT", "VCB", None, ""]), ["FPT", "VCB"])


if __name__ == "__main__":
    unittest.main()
