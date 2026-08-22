import unittest
from unittest.mock import patch

import rrg_index_membership as membership


class FakeStore:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.saved = []

    def save_index_membership_snapshot(self, code, symbols, meta):
        self.saved.append((code, symbols, meta))

    def load_index_membership_snapshot(self, code):
        return self.snapshot


class IndexMembershipTests(unittest.TestCase):
    def setUp(self):
        membership.invalidate_index_membership_cache()
        self.symbols = [f"S{i:02d}" for i in range(30)]

    def test_dual_source_agreement_is_saved_and_returned(self):
        store = FakeStore()
        with patch.object(membership, "_fetch_source", return_value=self.symbols):
            symbols, meta = membership.get_index_membership("VN30", store=store)
        self.assertEqual(symbols, self.symbols)
        self.assertTrue(meta["source_agreement"])
        self.assertFalse(meta["stale"])
        self.assertEqual(len(store.saved), 1)

    def test_source_mismatch_uses_verified_snapshot_not_either_provider(self):
        old = [f"O{i:02d}" for i in range(30)]
        store = FakeStore({"symbols": old, "meta": {
            "snapshot_id": "old", "source_agreement": True, "source_chain": ["KBS", "VCI"]
        }})
        def fetch(_, source):
            return self.symbols if source == "KBS" else [f"V{i:02d}" for i in range(30)]
        with patch.object(membership, "_fetch_source", side_effect=fetch):
            symbols, meta = membership.get_index_membership("VN30", store=store)
        self.assertEqual(symbols, old)
        self.assertTrue(meta["stale"])
        self.assertEqual(meta["refresh_error"], "source_mismatch")

    def test_single_source_fallback_when_other_provider_errors_and_no_store(self):
        store = FakeStore()
        def fetch(_, source):
            if source == "KBS":
                return self.symbols
            raise RuntimeError("VCI temporary error")
        with patch.object(membership, "_fetch_source", side_effect=fetch):
            symbols, meta = membership.get_index_membership("VN30", store=store)
        self.assertEqual(symbols, self.symbols)
        self.assertFalse(meta["source_agreement"])
        self.assertFalse(meta["stale"])
        self.assertEqual(meta["source"], "vnstock/KBS")
        self.assertEqual(len(store.saved), 1)

    def test_no_source_and_no_snapshot_fails_closed(self):
        with patch.object(membership, "_fetch_source", side_effect=RuntimeError("down")):
            with self.assertRaises(membership.IndexMembershipUnavailable):
                membership.get_index_membership("VN30", store=FakeStore())



if __name__ == "__main__":
    unittest.main()
