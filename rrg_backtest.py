"""Point-in-time diagnostics for LP-RRG market scores.

The evaluator accepts already-versioned snapshot rows.  It never joins a
score to a revised/future universe, which keeps the calculation free of
look-ahead and survivorship leakage.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def evaluate_point_in_time(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark_prices: pd.Series,
    horizons: Iterable[int] = (5, 10, 20),
) -> dict:
    required_scores = {"session", "symbol", "rotation_score", "snapshot_id", "universe_version"}
    required_prices = {"session", "symbol", "total_return_close"}
    if not required_scores.issubset(scores.columns) or not required_prices.issubset(prices.columns):
        raise ValueError("Thiếu cột point-in-time bắt buộc")
    score_data = scores.copy()
    price_data = prices.copy()
    score_data["session"] = score_data["session"].astype(str)
    price_data["session"] = price_data["session"].astype(str)
    price_data = price_data.sort_values(["symbol", "session"])
    benchmark = pd.to_numeric(benchmark_prices, errors="coerce").dropna().sort_index()
    benchmark.index = benchmark.index.astype(str)

    output = {"horizons": {}, "snapshot_count": int(score_data["snapshot_id"].nunique())}
    for horizon in horizons:
        if int(horizon) <= 0:
            raise ValueError("Horizon phải dương")
        future = price_data.copy()
        future["future_close"] = future.groupby("symbol")["total_return_close"].shift(-int(horizon))
        future["stock_return"] = future["future_close"] / future["total_return_close"] - 1.0
        benchmark_return = benchmark.shift(-int(horizon)) / benchmark - 1.0
        future["benchmark_return"] = future["session"].map(benchmark_return)
        future["relative_return"] = future["stock_return"] - future["benchmark_return"]
        joined = score_data.merge(
            future[["session", "symbol", "relative_return"]], on=["session", "symbol"], how="inner"
        ).dropna(subset=["rotation_score", "relative_return"])

        daily_ic = pd.Series({
            session: frame["rotation_score"].rank().corr(frame["relative_return"].rank())
            if len(frame) >= 3 else np.nan
            for session, frame in joined.groupby("session")
        }, dtype=float).dropna()
        top = joined[joined.groupby("session")["rotation_score"].rank(pct=True) >= 0.8]
        top_sets = [set(frame["symbol"]) for _, frame in top.groupby("session")]
        turnovers = []
        for previous, current in zip(top_sets, top_sets[1:]):
            union = previous | current
            turnovers.append(1.0 - len(previous & current) / len(union) if union else 0.0)
        output["horizons"][str(horizon)] = {
            "observations": int(len(joined)),
            "sessions": int(joined["session"].nunique()),
            "information_coefficient": None if daily_ic.empty else round(float(daily_ic.mean()), 4),
            "positive_relative_hit_rate_pct": None if top.empty else round(float((top["relative_return"] > 0).mean() * 100), 2),
            "average_relative_return_pct": None if top.empty else round(float(top["relative_return"].mean() * 100), 4),
            "top_bucket_turnover_pct": None if not turnovers else round(float(np.mean(turnovers) * 100), 2),
        }
    return output
