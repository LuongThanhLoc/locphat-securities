import math
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException, Response

import rrg_engine
from app import get_rrg_data_api


class LpRrgFormulaTests(unittest.TestCase):
    def setUp(self):
        self.index = pd.date_range("2024-01-01", periods=340, freq="B").strftime("%Y-%m-%d")
        t = np.arange(len(self.index), dtype=float)
        self.benchmark = pd.Series(100.0 * np.exp(0.0005 * t), index=self.index)

    def test_flat_relative_strength_is_neutral(self):
        ratio, momentum = rrg_engine.compute_rs_ratio_momentum(self.benchmark * 1.5, self.benchmark, 14)
        self.assertFalse(ratio.empty)
        self.assertAlmostEqual(float(ratio.iloc[-1]), 100.0, places=8)
        self.assertAlmostEqual(float(momentum.iloc[-1]), 100.0, places=8)

    def test_accelerating_outperformance_is_north_east(self):
        t = np.arange(len(self.index), dtype=float)
        stock = self.benchmark * np.exp(0.000025 * t**2)
        ratio, momentum = rrg_engine.compute_rs_ratio_momentum(stock, self.benchmark, 14)
        self.assertGreater(float(ratio.iloc[-1]), 100.0)
        self.assertGreater(float(momentum.iloc[-1]), 100.0)

    def test_short_history_returns_empty_series(self):
        ratio, momentum = rrg_engine.compute_rs_ratio_momentum(
            self.benchmark.iloc[:200], self.benchmark.iloc[:200], 14
        )
        self.assertTrue(ratio.empty)
        self.assertTrue(momentum.empty)

    def test_tail_length_does_not_change_latest_coordinates(self):
        t = np.arange(len(self.index), dtype=float)
        stock = self.benchmark * np.exp(0.00002 * t**2 + 0.01 * np.sin(t / 9))
        raw = pd.DataFrame({
            "date": self.index,
            "close": stock.values,
            "volume": np.full(len(self.index), 1_000_000),
        })
        with patch("rrg_engine._close_series", return_value=stock), patch(
            "rrg_engine._fetch_history", return_value=raw
        ):
            points = [
                rrg_engine._build_item("FPT", self.benchmark, 14, tail, "2024-01-01", "2025-12-31")
                for tail in (5, 10, 15, 20)
            ]
        self.assertEqual({point["rs_ratio"] for point in points}, {points[0]["rs_ratio"]})
        self.assertEqual({point["rs_momentum"] for point in points}, {points[0]["rs_momentum"]})
        self.assertEqual([len(point["tail"]) for point in points], [5, 10, 15, 20])


class RotationRadarTests(unittest.TestCase):
    def _item(self, symbol, quadrant, ratio, momentum, dr, dm, streak=1, persistence=0.0):
        item = rrg_engine._empty_item(symbol, {"sector": "Test", "archetype": "TEST"})
        item.update({
            "rs_ratio": ratio,
            "rs_momentum": momentum,
            "delta_ratio_5d": dr,
            "delta_momentum_5d": dm,
            "positive_persistence_5d": persistence,
            "quadrant": rrg_engine.get_quadrant(ratio, momentum),
            "quadrant_streak": streak,
            "tail_quadrants": [quadrant] * max(streak, 2),
        })
        return item

    def test_scores_are_finite_and_bounded(self):
        items = [
            self._item("AAA", "LEADING", 108, 106, 2, 3, 6, 1.0),
            self._item("BBB", "IMPROVING", 98, 104, 1, 2, 2, 1.0),
            self._item("CCC", "LAGGING", 94, 92, -2, -3, 4, 0.0),
        ]
        rrg_engine._assign_rotation_scores(items)
        for item in items:
            self.assertTrue(math.isfinite(item["rotation_score"]))
            self.assertGreaterEqual(item["rotation_score"], 0)
            self.assertLessEqual(item["rotation_score"], 100)
        self.assertGreater(items[0]["rotation_score"], items[2]["rotation_score"])

    def test_radar_classification(self):
        accelerating = self._item("AAA", "IMPROVING", 98, 104, 2, 2)
        leader = self._item("BBB", "LEADING", 106, 103, 1, 1, 6, 1.0)
        warning = self._item("CCC", "LAGGING", 95, 94, -2, -1, 3, 0.0)
        items = [accelerating, leader, warning]
        rrg_engine._assign_rotation_scores(items)
        radar = rrg_engine._build_rotation_radar(items)
        self.assertIn("AAA", {item["symbol"] for item in radar["ACCELERATING"]})
        self.assertIn("BBB", {item["symbol"] for item in radar["SUSTAINED_LEADER"]})
        self.assertIn("CCC", {item["symbol"] for item in radar["WEAKENING_ALERT"]})


class RrgApiValidationTests(unittest.TestCase):
    def test_vn30_group_uses_latest_effective_constituents(self):
        returned = [f"S{i:02d}" for i in range(30)]
        with patch("rrg_index_membership.get_index_membership", return_value=(returned, {
            "snapshot_id": "vn30-live", "as_of_date": "2026-08-11",
            "source": "vnstock/KBS+VCI", "source_agreement": True, "stale": False,
        })):
            symbols, key, name = rrg_engine._resolve_group("VN30", None)
            groups = {group["key"]: group for group in rrg_engine._preset_groups_listing()}
        self.assertEqual(key, "VN30")
        self.assertEqual(name, "VN30")
        self.assertEqual(len(symbols), 30)
        self.assertEqual(len(set(symbols)), 30)
        self.assertEqual(symbols, returned)
        self.assertEqual(groups["VN30"]["count"], 30)
        self.assertEqual(groups["VN30"]["snapshot_id"], "vn30-live")
        self.assertTrue(groups["VN30"]["source_agreement"])

    def test_invalid_parameters_return_422(self):
        cases = [
            {"benchmark": "INVALID"},
            {"group": "INVALID"},
            {"period": 13},
            {"tail_length": 9},
            {"group": "CUSTOM", "symbols": ",".join(f"A{i}" for i in range(31))},
        ]
        for overrides in cases:
            kwargs = dict(group="SMC_TOP", symbols=None, benchmark="VNINDEX", tail_length=15, period=14)
            kwargs.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(HTTPException) as ctx:
                get_rrg_data_api(Response(), **kwargs)
            self.assertEqual(ctx.exception.status_code, 422)

    def test_incomplete_dataset_returns_503(self):
        error = rrg_engine.RrgDataIncomplete("data_incomplete", ["SSI"])
        with patch("rrg_engine.generate_rrg_dataset", side_effect=error), self.assertRaises(HTTPException) as ctx:
            get_rrg_data_api(
                Response(), group="SMC_TOP", symbols=None, benchmark="VNINDEX",
                tail_length=15, period=14,
            )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["missing_symbols"], ["SSI"])

    def test_unverified_index_membership_returns_503(self):
        from rrg_index_membership import IndexMembershipUnavailable

        with patch(
            "rrg_engine.generate_rrg_dataset",
            side_effect=IndexMembershipUnavailable("KBS và VCI không khớp"),
        ), self.assertRaises(HTTPException) as ctx:
            get_rrg_data_api(
                Response(), group="VN30", symbols=None, benchmark="VNINDEX",
                tail_length=15, period=14,
            )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["code"], "index_membership_unavailable")


if __name__ == "__main__":
    unittest.main()
