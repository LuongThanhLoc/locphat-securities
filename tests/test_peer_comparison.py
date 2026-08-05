import unittest
from unittest.mock import MagicMock, patch
from peer_comparison_engine import get_peer_comparison, get_single_company_metrics
from peer_accuracy_store import save_financial_snapshot, get_financial_snapshot_by_id, store_summary
from app import get_peers, get_peer_snapshot, get_peer_store_summary

class TestPeerComparison(unittest.TestCase):
    @patch("peer_comparison_engine.get_single_company_metrics")
    def test_default_peers(self, mock_metrics):
        mock_metrics.side_effect = lambda s, refresh=False: {"symbol": s, "metrics": {}, "snapshot_id": 1}
        res = get_peer_comparison("KDH", None)
        self.assertEqual(res["target_symbol"], "KDH")
        self.assertGreater(len(res["peer_symbols"]), 0)

    @patch("peer_comparison_engine.get_single_company_metrics")
    def test_explicit_empty_peers(self, mock_metrics):
        mock_metrics.side_effect = lambda s, refresh=False: {"symbol": s, "metrics": {}, "snapshot_id": 1}
        res = get_peer_comparison("KDH", [])
        self.assertEqual(res["target_symbol"], "KDH")
        self.assertEqual(res["peer_symbols"], [])
        self.assertEqual(len(res["companies"]), 1)
        self.assertEqual(res["companies"][0]["symbol"], "KDH")

    @patch("peer_comparison_engine.get_single_company_metrics")
    def test_custom_peers_dedup_and_max8(self, mock_metrics):
        mock_metrics.side_effect = lambda s, refresh=False: {"symbol": s, "metrics": {}, "snapshot_id": 1}
        input_peers = ["DXG", "NLG", "DIG", "PDR", "SSI", "VND", "HCM", "VCI", "TCB"]
        res = get_peer_comparison("KDH", input_peers)
        self.assertEqual(len(res["peer_symbols"]), 8)
        self.assertEqual(res["peer_symbols"], ["DXG", "NLG", "DIG", "PDR", "SSI", "VND", "HCM", "VCI"])

    @patch("app.get_peer_comparison")
    def test_app_get_peers_endpoint(self, mock_get_peer):
        mock_resp = MagicMock()
        
        # Test peers=None (initial load)
        get_peers("KDH", mock_resp, peers=None)
        mock_get_peer.assert_called_with("KDH", None, force_refresh=False)

        # Test peers="" (all peers removed)
        get_peers("KDH", mock_resp, peers="")
        mock_get_peer.assert_called_with("KDH", [], force_refresh=False)

        # Test peers="DXG,NLG" (custom peer list)
        get_peers("KDH", mock_resp, peers="DXG,NLG")
        mock_get_peer.assert_called_with("KDH", ["DXG", "NLG"], force_refresh=False)

    def test_peer_accuracy_store(self):
        snapshot_id = save_financial_snapshot(
            symbol="TCB",
            period="2026-Q2",
            source="DNSE REST live trade",
            source_url="https://test.com",
            payload={"current_price": 25000, "metrics": {"pe": 7.5}}
        )
        self.assertIsInstance(snapshot_id, int)
        self.assertGreater(snapshot_id, 0)

        snap = get_financial_snapshot_by_id(snapshot_id)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["symbol"], "TCB")
        self.assertEqual(snap["period"], "2026-Q2")
        self.assertEqual(snap["payload"]["current_price"], 25000)

        summary = store_summary()
        self.assertGreaterEqual(summary["financial_snapshots"], 1)

    @patch("peer_comparison_engine.get_dnse_latest_price_snapshot")
    @patch("peer_comparison_engine.analyze_security_stock")
    def test_realtime_price_metrics_recalculation(self, mock_analyze, mock_dnse):
        mock_analyze.return_value = {
            "symbol": "TCB",
            "organ_name": "Ngân hàng Techcombank",
            "current_price": 20000,
            "issue_share_million": 3500.0,
            "latest_quarter": "2026-Q2",
            "archetype": "BANKING",
            "valuation": {"eps": 3000, "bvps": 20000, "pe_ratio": 6.6, "pb_ratio": 1.0},
            "peer_metrics": {"roe": 15.0, "npat_yoy": 20.0},
            "data_quality": {"price_source": "Vietcap snapshot"}
        }
        mock_dnse.return_value = {
            "price_vnd": 24000.0,
            "source": "DNSE REST live trade",
            "exchange_time": "2026-08-05T14:45:00Z"
        }

        res = get_single_company_metrics("TCB", force_refresh=True)
        self.assertEqual(res["symbol"], "TCB")
        self.assertEqual(res["price"], 24000.0)
        # P/E = 24000 / 3000 = 8.0
        self.assertEqual(res["metrics"]["pe"], 8.0)
        # P/B = 24000 / 20000 = 1.2
        self.assertEqual(res["metrics"]["pb"], 1.2)
        # Market cap = 24000 * 3500m * 1e6 / 1e9 = 84000.0 billion
        self.assertEqual(res["metrics"]["market_cap"], 84000.0)
        self.assertIsNotNone(res["snapshot_id"])

if __name__ == "__main__":
    unittest.main()
