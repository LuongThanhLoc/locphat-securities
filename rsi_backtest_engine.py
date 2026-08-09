"""RSI Divergence Backtesting Engine v3 for Lộc Phát Securities.

Detects bullish and bearish RSI divergences, simulates trades with configurable
exit strategies, position sizing, multi-timeframe confirmation, and market regime filtering.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

SUPPORTED_TIMEFRAMES = {"1H", "2H", "4H", "1D", "1W", "1M"}
INTRADAY_TIMEFRAMES = {"1H", "2H", "4H"}
DEFAULT_BAR_LIMIT = 748


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _frame(
    rows: Iterable[Dict[str, Any]],
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """Normalize provider bars without inventing dates or prices.

    A row is usable only when it contains a provider-supplied session date and
    a coherent positive OHLC bar.  When volume is available, zero-volume rows
    are removed because no transaction actually occurred for the instrument.
    """
    frame = pd.DataFrame(list(rows or []))
    if frame.empty or "close" not in frame:
        return pd.DataFrame()
    date_col = "date" if "date" in frame.columns else "time" if "time" in frame.columns else None
    if not date_col:
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame[date_col], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame = frame[frame["date"].dt.weekday < 5]
    frame = frame[
        (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
    ]
    if frame["volume"].notna().any() and (frame["volume"] > 0).any():
        frame = frame[frame["volume"] > 0]
    if start is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((loss == 0) & (gain > 0), 100.0)
    result = result.mask((loss == 0) & (gain == 0), 50.0)
    return result


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _enrich(frame: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    result = frame.copy()
    result["rsi"] = _rsi(result["close"], rsi_period)
    result["atr"] = _atr(result)
    result["ma50"] = _ma(result["close"], 50)
    result["ma200"] = _ma(result["close"], 200)
    return result


def _resample_ohlc(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if frame.empty or timeframe not in ("1W", "1M"):
        return frame.copy()

    period_freq = "W-FRI" if timeframe == "1W" else "M"
    work = frame.copy()
    work["period_key"] = work["date"].dt.to_period(period_freq)
    rows = []
    for _, group in work.groupby("period_key", sort=True):
        group = group.sort_values("date")
        rows.append({
            "date": group["date"].iloc[-1],
            "open": group["open"].iloc[0],
            "high": group["high"].max(),
            "low": group["low"].min(),
            "close": group["close"].iloc[-1],
            "volume": group["volume"].sum() if "volume" in group else np.nan,
        })
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _history_start_for_bar_limit(end_date: date, timeframe: str, bar_limit: int) -> date:
    bars = max(int(bar_limit or DEFAULT_BAR_LIMIT), 50)
    days_per_bar = {"1D": 3, "1W": 10, "1M": 35}.get(timeframe, 3)
    return end_date - timedelta(days=bars * days_per_bar + 90)


def _aligned_higher_timeframe_rsi(
    frame: pd.DataFrame,
    timeframe: str,
    rsi_period: int,
) -> pd.Series:
    """Return the last *completed* weekly/monthly RSI for every daily bar.

    Shifting one complete period is deliberate: a Monday signal must never use
    the close of the following Friday, and a mid-month signal must never use
    the eventual month-end close.
    """
    if frame.empty or timeframe not in ("1W", "1M"):
        return pd.Series(np.nan, index=frame.index, dtype=float)
    period_freq = "W-FRI" if timeframe == "1W" else "M"
    period_key = frame["date"].dt.to_period(period_freq)
    period_close = frame.groupby(period_key, sort=True)["close"].last()
    completed_rsi = _rsi(period_close, rsi_period).shift(1)
    return period_key.map(completed_rsi).set_axis(frame.index).astype(float)


# ------------------------------------------------------------------
# Divergence Detection
# ------------------------------------------------------------------

def _detect_divergences(
    frame: pd.DataFrame,
    lookback: int = 20,
    rsi_entry_min: float = 40.0,
    rsi_entry_max: float = 60.0,
    weekly_rsi: Optional[pd.Series] = None,
    confirm_timeframe: str = "",
    confirm_rsi_min: float = 50.0,
    confirm_rsi_max: float = 50.0,
) -> List[Dict[str, Any]]:
    """Detect causal, confirmed swing divergences exactly once per pivot.

    The former rolling-half implementation could emit the same divergence on
    several consecutive sessions.  Here a pivot is confirmed only after
    ``pivot_right`` real bars have traded, and that confirmation date is the
    signal date used by the backtest.
    """
    divergences: List[Dict[str, Any]] = []
    close = frame["close"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    rsi = frame["rsi"].values
    dates = frame["date"].values
    has_confirm = confirm_timeframe in ("1W", "1M") and weekly_rsi is not None
    pivot_right = max(2, min(5, lookback // 4))
    pivot_width = pivot_right * 2 + 1
    low_pivots: List[int] = []
    high_pivots: List[int] = []

    for signal_idx in range(pivot_width - 1, len(frame) - 1):
        pivot_idx = signal_idx - pivot_right
        left = pivot_idx - pivot_right
        right = pivot_idx + pivot_right + 1
        current_rsi = rsi[signal_idx]
        pivot_rsi = rsi[pivot_idx]
        if not np.isfinite(current_rsi) or not np.isfinite(pivot_rsi):
            continue
        weekly_val = float(weekly_rsi.iloc[signal_idx]) if has_confirm else np.nan
        confirm_available = not has_confirm or np.isfinite(weekly_val)

        low_window = low[left:right]
        is_low = low[pivot_idx] == np.min(low_window) and np.argmin(low_window) == pivot_right
        if is_low:
            previous = low_pivots[-1] if low_pivots else None
            if previous is not None and pivot_idx - previous <= lookback:
                bullish_ok = (
                    low[pivot_idx] < low[previous]
                    and rsi[pivot_idx] > rsi[previous]
                    and current_rsi >= rsi_entry_min
                    and current_rsi < 55
                    and confirm_available
                )
                if has_confirm:
                    bullish_ok = bullish_ok and weekly_val < confirm_rsi_min
                if bullish_ok:
                    divergences.append({
                        "date": pd.Timestamp(dates[signal_idx]).strftime("%Y-%m-%d"),
                        "pivot_date": pd.Timestamp(dates[pivot_idx]).strftime("%Y-%m-%d"),
                        "previous_pivot_date": pd.Timestamp(dates[previous]).strftime("%Y-%m-%d"),
                        "type": "bullish",
                        "price_at_signal": round(float(close[signal_idx]), 2),
                        "rsi_at_signal": round(float(current_rsi), 1),
                        "lookback_low_price": round(float(low[previous]), 2),
                        "lookback_low_rsi": round(float(rsi[previous]), 1),
                        "divergence_low_price": round(float(low[pivot_idx]), 2),
                        "divergence_low_rsi": round(float(rsi[pivot_idx]), 1),
                        "signal_bar_index": signal_idx,
                        "weekly_rsi": round(weekly_val, 1) if has_confirm else None,
                    })
            low_pivots.append(pivot_idx)

        high_window = high[left:right]
        is_high = high[pivot_idx] == np.max(high_window) and np.argmax(high_window) == pivot_right
        if is_high:
            previous = high_pivots[-1] if high_pivots else None
            if previous is not None and pivot_idx - previous <= lookback:
                bearish_ok = (
                    high[pivot_idx] > high[previous]
                    and rsi[pivot_idx] < rsi[previous]
                    and current_rsi <= rsi_entry_max
                    and current_rsi > 45
                    and confirm_available
                )
                if has_confirm:
                    bearish_ok = bearish_ok and weekly_val > confirm_rsi_max
                if bearish_ok:
                    divergences.append({
                        "date": pd.Timestamp(dates[signal_idx]).strftime("%Y-%m-%d"),
                        "pivot_date": pd.Timestamp(dates[pivot_idx]).strftime("%Y-%m-%d"),
                        "previous_pivot_date": pd.Timestamp(dates[previous]).strftime("%Y-%m-%d"),
                        "type": "bearish",
                        "price_at_signal": round(float(close[signal_idx]), 2),
                        "rsi_at_signal": round(float(current_rsi), 1),
                        "lookback_high_price": round(float(high[previous]), 2),
                        "lookback_high_rsi": round(float(rsi[previous]), 1),
                        "divergence_high_price": round(float(high[pivot_idx]), 2),
                        "divergence_high_rsi": round(float(rsi[pivot_idx]), 1),
                        "signal_bar_index": signal_idx,
                        "weekly_rsi": round(weekly_val, 1) if has_confirm else None,
                    })
            high_pivots.append(pivot_idx)

    return sorted(divergences, key=lambda item: (item["signal_bar_index"], item["type"]))


# ------------------------------------------------------------------
# Market Regime Filter
# ------------------------------------------------------------------

def _filter_by_regime(
    divergences: List[Dict[str, Any]],
    frame: pd.DataFrame,
    trend_filter: str = "none",
    market_index: str = "^VNINDEX",
    benchmark_frame: Optional[pd.DataFrame] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Filter divergences by market regime. Returns (filtered_divs, filtered_count)."""
    if trend_filter == "none" or not divergences:
        return divergences, 0

    filtered_count = 0
    filtered = []

    # Pre-compute regime signals
    price = frame["close"].values
    ma50 = frame["ma50"].values if "ma50" in frame.columns else None
    ma200 = frame["ma200"].values if "ma200" in frame.columns else None

    # Benchmark RSI if provided
    bench_rsi = None
    if benchmark_frame is not None and len(benchmark_frame) > 14:
        benchmark = benchmark_frame[["date", "close"]].copy()
        benchmark["benchmark_rsi"] = _rsi(benchmark["close"], 14)
        aligned = pd.merge_asof(
            frame[["date"]].sort_values("date"),
            benchmark[["date", "benchmark_rsi"]].sort_values("date"),
            on="date",
            direction="backward",
            tolerance=pd.Timedelta(days=7),
        )
        bench_rsi = aligned["benchmark_rsi"].to_numpy(dtype=float)

    for div in divergences:
        idx = div["signal_bar_index"]

        # Trend filter: MA-based
        if trend_filter in ("ma50", "ma200"):
            if trend_filter == "ma50" and ma50 is not None:
                if idx < len(price) and _finite(ma50[idx]) > 0 and price[idx] < ma50[idx]:
                    filtered_count += 1
                    continue
            elif trend_filter == "ma200" and ma200 is not None:
                if idx < len(price) and _finite(ma200[idx]) > 0 and price[idx] < ma200[idx]:
                    filtered_count += 1
                    continue

        # Benchmark RSI filter
        if trend_filter == "rsi_bench" and bench_rsi is not None:
            if idx < len(bench_rsi) and np.isfinite(bench_rsi[idx]):
                bench_val = float(bench_rsi[idx])
                if div["type"] == "bullish" and bench_val >= 50:
                    filtered_count += 1
                    continue
                if div["type"] == "bearish" and bench_val <= 50:
                    filtered_count += 1
                    continue

        filtered.append(div)

    return filtered, filtered_count


# ------------------------------------------------------------------
# Position Sizing
# ------------------------------------------------------------------

def _compute_position_size(
    capital: float,
    position_size_pct: float,
    mode: str,
    risk_amount: float,
    atr: float,
    entry_price: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """Compute position size in currency units based on sizing mode."""
    if mode == "full":
        return capital * (position_size_pct / 100.0)

    if mode == "fixed":
        return min(position_size_pct, capital)

    if mode == "fixed_fractional":
        # Risk amount = position_size_pct% of capital
        risk_per_trade = capital * (position_size_pct / 100.0)
        # Stop-loss distance = atr * atr_multiplier
        if atr > 0 and entry_price > 0:
            shares = risk_per_trade / (atr * 1.5)  # 1.5 ATR stop
            return shares * entry_price
        return capital * (position_size_pct / 100.0)

    if mode == "kelly":
        # Kelly criterion: f = (bp - q) / b
        # where b = avg_win/avg_loss ratio, p = win_rate, q = 1-p
        if avg_loss == 0 or win_rate <= 0:
            return capital * (position_size_pct / 100.0)
        b = avg_win / avg_loss
        p = win_rate / 100.0
        q = 1 - p
        kelly = max(0, (b * p - q) / b)
        # Use fractional Kelly (half or quarter)
        kelly_frac = kelly * 0.25
        return capital * kelly_frac

    return capital * (position_size_pct / 100.0)


# ------------------------------------------------------------------
# Trade Simulation
# ------------------------------------------------------------------

def _simulate_trades(
    frame: pd.DataFrame,
    divergences: List[Dict[str, Any]],
    exit_strategy: str = "time",
    holding_days: int = 20,
    include_short: bool = False,
    max_concurrent_trades: int = 1,
    commission_pct: float = 0.0,
    slippage_pct: float = 0.0,
    position_mode: str = "full",
    position_size_pct: float = 100.0,
    atr_stop_multiple: float = 1.5,
    win_rate_approx: float = 50.0,
    avg_win_approx: float = 5.0,
    avg_loss_approx: float = 3.0,
    initial_capital: float = 100_000_000.0,
    execution_audit: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    audit = execution_audit if execution_audit is not None else {}
    for reason in (
        "short_disabled",
        "concurrency_limit",
        "no_next_session",
        "incomplete_exit_window",
        "invalid_entry_price",
    ):
        audit.setdefault(reason, 0)

    if not divergences:
        return []

    trades = []
    price = frame["close"].values
    open_prices = frame["open"].values
    high_prices = frame["high"].values
    low_prices = frame["low"].values
    rsi = frame["rsi"].values
    atr = frame["atr"].values
    dates = frame["date"].values
    n = len(frame)

    active_exit_indices: List[int] = []

    for div in divergences:
        signal_idx = div["signal_bar_index"]
        div_type = div["type"]

        # Skip bearish if shorting is disabled
        if div_type == "bearish" and not include_short:
            audit["short_disabled"] += 1
            continue

        if signal_idx + 1 >= n:
            audit["no_next_session"] += 1
            continue

        entry_idx = signal_idx + 1
        active_exit_indices = [idx for idx in active_exit_indices if idx >= entry_idx]
        if len(active_exit_indices) >= max(1, max_concurrent_trades):
            audit["concurrency_limit"] += 1
            continue

        # Apply adverse slippage in the correct direction.
        entry_raw = open_prices[entry_idx]
        entry_factor = 1 + slippage_pct / 100 if div_type == "bullish" else 1 - slippage_pct / 100
        entry_slippage = entry_raw * entry_factor
        entry_price = round(float(entry_slippage), 2)
        entry_date = pd.Timestamp(dates[entry_idx]).strftime("%Y-%m-%d")

        if entry_price <= 0:
            audit["invalid_entry_price"] += 1
            continue

        # Determine exit
        exit_idx = None
        exit_price = None
        exit_reason = None
        holding = 0

        if exit_strategy == "time":
            target_idx = entry_idx + holding_days
            if target_idx >= n:
                audit["incomplete_exit_window"] += 1
                continue  # Do not turn a still-open position into a completed trade.
            exit_idx = target_idx
            exit_reason = "time_exit"

        elif exit_strategy == "rsi":
            max_exit_idx = entry_idx + 60
            max_check = min(max_exit_idx, n - 1)
            for j in range(entry_idx, max_check + 1):
                cur_rsi = rsi[j]
                if div_type == "bullish":
                    if np.isfinite(cur_rsi) and cur_rsi >= 65:
                        exit_idx = j
                        exit_reason = "rsi_overbought"
                        break
                else:
                    if np.isfinite(cur_rsi) and cur_rsi <= 35:
                        exit_idx = j
                        exit_reason = "rsi_oversold"
                        break
            if exit_idx is None:
                if max_exit_idx >= n:
                    audit["incomplete_exit_window"] += 1
                    continue
                exit_idx = max_exit_idx
                exit_reason = "max_days"

        elif exit_strategy == "trailing":
            entry_atr = _finite(atr[entry_idx], entry_price * 0.02)
            trailing_stop = entry_price - atr_stop_multiple * entry_atr if div_type == "bullish" else entry_price + atr_stop_multiple * entry_atr
            max_exit_idx = entry_idx + 60
            max_check = min(max_exit_idx, n - 1)

            for j in range(entry_idx, max_check + 1):
                cur_low = low_prices[j]
                cur_high = high_prices[j]
                cur_price = price[j]

                if div_type == "bullish":
                    if cur_low <= trailing_stop:
                        exit_idx = j
                        exit_price = round(float(trailing_stop), 2)
                        exit_reason = "trailing_stop"
                        break
                    # Do not assume whether today's high or low happened first.
                    # A close-based stop update becomes active next session.
                    if cur_price > trailing_stop + 0.5 * entry_atr:
                        trailing_stop = max(trailing_stop, cur_price - atr_stop_multiple * entry_atr)
                else:
                    if cur_high >= trailing_stop:
                        exit_idx = j
                        exit_price = round(float(trailing_stop), 2)
                        exit_reason = "trailing_stop"
                        break
                    if cur_price < trailing_stop - 0.5 * entry_atr:
                        trailing_stop = min(trailing_stop, cur_price + atr_stop_multiple * entry_atr)

            if exit_reason is None:
                if max_exit_idx >= n:
                    audit["incomplete_exit_window"] += 1
                    continue
                exit_idx = max_exit_idx
                exit_reason = "max_days"

        # Calculate exit price with slippage
        if exit_idx is not None and exit_idx < n:
            exit_date = pd.Timestamp(dates[exit_idx]).strftime("%Y-%m-%d")
            holding = exit_idx - entry_idx

            if exit_price is None:
                exit_raw = price[exit_idx]
                exit_factor = 1 - slippage_pct / 100 if div_type == "bullish" else 1 + slippage_pct / 100
                exit_price = round(float(exit_raw * exit_factor), 2)
            else:
                exit_factor = 1 - slippage_pct / 100 if div_type == "bullish" else 1 + slippage_pct / 100
                exit_price = round(float(exit_price * exit_factor), 2)

            # P&L
            if div_type == "bullish":
                raw_pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                raw_pnl_pct = (entry_price - exit_price) / entry_price * 100

            # Commission
            commission_cost_pct = commission_pct * (entry_price + exit_price) / entry_price
            net_pnl_pct = raw_pnl_pct - commission_cost_pct

            # Max drawdown / runup
            max_drawdown = 0.0
            max_runup = 0.0

            for k in range(entry_idx, exit_idx + 1):
                if div_type == "bullish":
                    adverse = (low_prices[k] - entry_price) / entry_price * 100
                    favorable = (high_prices[k] - entry_price) / entry_price * 100
                else:
                    adverse = (entry_price - high_prices[k]) / entry_price * 100
                    favorable = (entry_price - low_prices[k]) / entry_price * 100
                max_drawdown = min(max_drawdown, adverse)
                max_runup = max(max_runup, favorable)

            position_value = _compute_position_size(
                initial_capital, position_size_pct, position_mode, 0.0,
                _finite(atr[entry_idx]), entry_price,
                win_rate_approx, avg_win_approx, avg_loss_approx,
            )
            position_weight = min(max(position_value / initial_capital, 0.0), 1.0) if initial_capital > 0 else 0.0
            capital_return_pct = net_pnl_pct * position_weight
            active_exit_indices.append(exit_idx)

            trades.append({
                "entry_date": entry_date,
                "exit_date": exit_date,
                "divergence_type": div_type,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(net_pnl_pct, 2),
                "raw_pnl_pct": round(raw_pnl_pct, 2),
                "commission_pct": round(commission_cost_pct, 4),
                "position_weight_pct": round(position_weight * 100, 2),
                "capital_return_pct": round(capital_return_pct, 4),
                "holding_days": holding,
                "exit_reason": exit_reason,
                "max_drawdown_pct": round(max_drawdown, 2),
                "max_runup_pct": round(max_runup, 2),
                "signal_date": div["date"],
                "rsi_at_signal": div["rsi_at_signal"],
                "weekly_rsi": div.get("weekly_rsi"),
            })

    return trades


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------

def _calculate_metrics(
    trades: List[Dict[str, Any]],
    divergences: List[Dict[str, Any]],
    filtered_signals: int,
    start_date: str,
    end_date: str,
    initial_capital: float = 100_000_000.0,
) -> Dict[str, Any]:
    base = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "avg_pnl_pct": 0.0,
        "avg_holding_days": 0.0,
        "best_trade_pct": 0.0,
        "worst_trade_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "max_runup_pct": 0.0,
        "sharpe_ratio": 0.0,
        "cagr": 0.0,
        "profit_factor": 0.0,
        "divergences_per_year": 0.0,
        "avg_rsi_at_signal": 0.0,
        "bullish_trades": 0,
        "bearish_trades": 0,
        "avg_bullish_pnl": 0.0,
        "avg_bearish_pnl": 0.0,
        "total_signals": 0,
        "bullish_signals": 0,
        "bearish_signals": 0,
        "filtered_signals": 0,
        "short_trades": 0,
        "long_trades": 0,
        "avg_commission_pct": 0.0,
        # VND metrics
        "initial_capital": initial_capital,
        "final_balance": initial_capital,
        "total_return_vnd": 0.0,
    }

    base["total_signals"] = len(divergences)
    base["bullish_signals"] = sum(1 for item in divergences if item.get("type") == "bullish")
    base["bearish_signals"] = sum(1 for item in divergences if item.get("type") == "bearish")
    base["filtered_signals"] = filtered_signals
    rsi_values = [d["rsi_at_signal"] for d in divergences if d.get("rsi_at_signal") is not None]
    base["avg_rsi_at_signal"] = round(float(np.mean(rsi_values)), 1) if rsi_values else 0.0

    if not trades:
        return base

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls) if len(pnls) > 1 else 1.0
    sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0.0

    total_profit = sum(wins) if wins else 0.0
    total_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = float("inf") if (total_profit > 0 and total_loss == 0) else (total_profit / total_loss if total_loss > 0 else 0.0)

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        years = max((end_dt - start_dt).days / 365.0, 0.1)
        divs_per_year = len(divergences) / years
    except (ValueError, TypeError):
        years = 1.0
        divs_per_year = 0.0

    capital_returns = [t.get("capital_return_pct", t["pnl_pct"]) for t in trades]
    growth_factor = float(np.prod([1 + value / 100.0 for value in capital_returns]))
    total_return = growth_factor - 1
    cagr = ((max(growth_factor, 0.0) ** (1 / years)) - 1) * 100 if years > 0 else 0.0

    avg_rsi = np.mean(rsi_values) if rsi_values else 50.0

    bullish_trades = [t for t in trades if t["divergence_type"] == "bullish"]
    bearish_trades = [t for t in trades if t["divergence_type"] == "bearish"]

    avg_bullish = np.mean([t["pnl_pct"] for t in bullish_trades]) if bullish_trades else 0.0
    avg_bearish = np.mean([t["pnl_pct"] for t in bearish_trades]) if bearish_trades else 0.0

    all_drawdowns = [t["max_drawdown_pct"] for t in trades]
    all_runups = [t["max_runup_pct"] for t in trades]

    # VND balance metrics
    final_balance = initial_capital * growth_factor
    total_return_vnd = final_balance - initial_capital

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "avg_pnl_pct": round(avg_pnl, 2),
        "avg_holding_days": round(np.mean([t["holding_days"] for t in trades]), 1) if trades else 0.0,
        "best_trade_pct": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade_pct": round(min(pnls), 2) if pnls else 0.0,
        "max_drawdown_pct": round(min(all_drawdowns), 2) if all_drawdowns else 0.0,
        "max_runup_pct": round(max(all_runups), 2) if all_runups else 0.0,
        "sharpe_ratio": round(sharpe, 2),
        "cagr": round(cagr, 2),
        "profit_factor": round(min(profit_factor, 999.99), 2),
        "divergences_per_year": round(divs_per_year, 1),
        "avg_rsi_at_signal": round(avg_rsi, 1),
        "bullish_trades": len(bullish_trades),
        "bearish_trades": len(bearish_trades),
        "avg_bullish_pnl": round(avg_bullish, 2),
        "avg_bearish_pnl": round(avg_bearish, 2),
        "total_signals": len(divergences),
        "bullish_signals": sum(1 for item in divergences if item.get("type") == "bullish"),
        "bearish_signals": sum(1 for item in divergences if item.get("type") == "bearish"),
        "filtered_signals": filtered_signals,
        "long_trades": len(bullish_trades),
        "short_trades": len(bearish_trades),
        "avg_commission_pct": round(np.mean([abs(t["raw_pnl_pct"] - t["pnl_pct"]) for t in trades]), 2),
        # VND metrics
        "initial_capital": initial_capital,
        "final_balance": round(final_balance, 0),
        "total_return_vnd": round(total_return_vnd, 0),
    }


# ------------------------------------------------------------------
# Equity Curve
# ------------------------------------------------------------------

def _build_equity_curve(
    trades: List[Dict[str, Any]],
    frame: pd.DataFrame,
    initial_capital: float = 100_000_000.0,
) -> List[Dict[str, Any]]:
    """Build equity curve using bar-by-bar compounding, in VND units."""
    if frame.empty:
        return []

    equity = initial_capital
    equity_curve = []
    dates = frame["date"].values
    price = frame["close"].values

    # Realise results only on the actual exit session; applying final P&L on
    # entry day would leak future information into the equity curve.
    trade_map: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        trade_map.setdefault(t["exit_date"], []).append(t)

    start_price = frame["close"].iloc[0]
    benchmark = initial_capital

    for i in range(len(frame)):
        current_date = pd.Timestamp(dates[i]).strftime("%Y-%m-%d")
        current_price = price[i]
        benchmark = round((current_price / start_price) * initial_capital, 2)

        # Apply trade P&L only when the position exits. With no trades this
        # stays as a truthful flat cash line while the benchmark uses real closes.
        for trade in trade_map.get(current_date, []):
            equity = equity * (1 + trade.get("capital_return_pct", trade["pnl_pct"]) / 100)

        equity_curve.append({
            "date": current_date,
            "equity": round(max(equity, 0.01), 0),
            "benchmark": benchmark,
        })

    return equity_curve


# ------------------------------------------------------------------
# Main API
# ------------------------------------------------------------------

def run_backtest(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    timeframe: str = "1D",
    bar_limit: Optional[int] = DEFAULT_BAR_LIMIT,
    range_mode: str = "bars",
    rsi_period: int = 14,
    lookback: int = 20,
    exit_strategy: str = "time",
    holding_days: int = 20,
    rsi_entry_min: float = 40.0,
    rsi_entry_max: float = 60.0,
    # v2 parameters
    include_short: bool = False,
    max_concurrent_trades: int = 1,
    commission_pct: float = 0.0,
    slippage_pct: float = 0.0,
    position_mode: str = "full",
    position_size_pct: float = 100.0,
    confirm_timeframe: str = "",
    confirm_rsi_min: float = 50.0,
    confirm_rsi_max: float = 50.0,
    trend_filter: str = "none",
    market_index: str = "^VNINDEX",
    initial_capital: float = 100_000_000.0,
) -> Dict[str, Any]:
    from market_data_provider import Quote

    timeframe = str(timeframe or "1D").upper().strip()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _empty_result(symbol, start or datetime.now().date(), end or datetime.now().date(), {
            "timeframe": timeframe, "bar_limit": bar_limit, "range_mode": range_mode,
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "unsupported_timeframe", {
            "source": "Không xác định",
            "timeframe": timeframe,
            "requested_bar_limit": bar_limit,
            "actual_bars": 0,
            "verified_trading_sessions": 0,
            "timeframe_supported": False,
            "unsupported_reason": f"Khung {timeframe} chưa nằm trong whitelist kiểm định.",
            "first_bar": None,
            "last_bar": None,
        })

    end_date = end or datetime.now().date()
    use_bar_limit = range_mode != "dates" and bar_limit is not None
    requested_bar_limit = max(50, min(int(bar_limit or DEFAULT_BAR_LIMIT), 5000))
    start_date = start or _history_start_for_bar_limit(end_date, timeframe, requested_bar_limit)

    if timeframe in INTRADAY_TIMEFRAMES:
        return _empty_result(symbol, start_date, end_date, {
            "timeframe": timeframe, "bar_limit": requested_bar_limit, "range_mode": range_mode,
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "unsupported_timeframe", {
            "source": "Vietcap/KBS daily OHLC",
            "timeframe": timeframe,
            "requested_bar_limit": requested_bar_limit,
            "actual_bars": 0,
            "verified_trading_sessions": 0,
            "timeframe_supported": False,
            "unsupported_reason": f"Chưa có nguồn OHLC intraday thật đã xác minh cho khung {timeframe}; hệ thống không dựng bar giả từ dữ liệu ngày.",
            "first_bar": None,
            "last_bar": None,
        })

    # Fetch verified daily bars. Weekly/monthly bars are aggregated from these
    # real sessions and explicitly marked in data_quality.
    raw_df = Quote(symbol=symbol).history(
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        interval="1D",
    )
    provider_rows = len(raw_df)
    provider_source = str(raw_df.attrs.get("source") or "Không xác định")
    df = _frame(raw_df.to_dict("records"), start=start_date, end=end_date)
    source_transform = "native"
    if timeframe in ("1W", "1M") and not df.empty:
        df = _resample_ohlc(df, timeframe)
        source_transform = "resampled_from_daily"
    if use_bar_limit and not df.empty:
        df = df.tail(requested_bar_limit).reset_index(drop=True)

    if df.empty:
        return _empty_result(symbol, start_date, end_date, {
            "timeframe": timeframe, "bar_limit": requested_bar_limit, "range_mode": range_mode,
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "no_data", {
            "source": provider_source,
            "timeframe": timeframe,
            "requested_bar_limit": requested_bar_limit,
            "actual_bars": 0,
            "provider_rows": provider_rows,
            "verified_trading_sessions": 0,
            "excluded_rows": provider_rows,
            "first_session": None,
            "last_session": None,
            "first_bar": None,
            "last_bar": None,
            "timeframe_supported": True,
            "unsupported_reason": None,
            "source_transform": source_transform,
        })

    df = _enrich(df, rsi_period)
    actual_start_date = pd.Timestamp(df["date"].iloc[0]).date()
    actual_end_date = pd.Timestamp(df["date"].iloc[-1]).date()

    if len(df) < 50:
        return _empty_result(symbol, actual_start_date, actual_end_date, {
            "timeframe": timeframe, "bar_limit": requested_bar_limit, "range_mode": range_mode,
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "insufficient_data", {
            "source": provider_source,
            "timeframe": timeframe,
            "requested_bar_limit": requested_bar_limit,
            "actual_bars": len(df),
            "provider_rows": provider_rows,
            "verified_trading_sessions": len(df),
            "excluded_rows": max(provider_rows - len(df), 0),
            "first_session": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_session": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "first_bar": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_bar": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "timeframe_supported": True,
            "unsupported_reason": None,
            "source_transform": source_transform,
        })

    # Completed higher-timeframe data aligned causally to each daily session.
    weekly_rsi: Optional[pd.Series] = None
    if confirm_timeframe in ("1W", "1M"):
        weekly_rsi = _aligned_higher_timeframe_rsi(df, confirm_timeframe, rsi_period)

    # Benchmark data for regime filter
    benchmark_frame: Optional[pd.DataFrame] = None
    if trend_filter != "none" and market_index:
        try:
            bench_raw = Quote(symbol=market_index).history(
                start=actual_start_date.strftime("%Y-%m-%d"),
                end=actual_end_date.strftime("%Y-%m-%d"),
            )
            benchmark_frame = _frame(bench_raw.to_dict("records"), start=actual_start_date, end=actual_end_date)
            if timeframe in ("1W", "1M") and not benchmark_frame.empty:
                benchmark_frame = _resample_ohlc(benchmark_frame, timeframe)
            if not benchmark_frame.empty:
                benchmark_frame = _enrich(benchmark_frame, rsi_period)
        except Exception:
            benchmark_frame = None

    # Detect divergences
    divergences = _detect_divergences(
        df,
        lookback=lookback,
        rsi_entry_min=rsi_entry_min,
        rsi_entry_max=rsi_entry_max,
        weekly_rsi=weekly_rsi,
        confirm_timeframe=confirm_timeframe,
        confirm_rsi_min=confirm_rsi_min,
        confirm_rsi_max=confirm_rsi_max,
    )

    # Filter by market regime
    filtered_divs, filtered_count = _filter_by_regime(
        divergences,
        df,
        trend_filter=trend_filter,
        market_index=market_index,
        benchmark_frame=benchmark_frame,
    )

    # Simulate trades
    simulation_skips: Dict[str, int] = {}
    trades = _simulate_trades(
        df,
        filtered_divs,
        exit_strategy=exit_strategy,
        holding_days=holding_days,
        include_short=include_short,
        max_concurrent_trades=max_concurrent_trades,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        position_mode=position_mode,
        position_size_pct=position_size_pct,
        initial_capital=initial_capital,
        execution_audit=simulation_skips,
    )

    execution_audit = {
        "total_detected_signals": len(divergences),
        "eligible_after_regime": len(filtered_divs),
        "trades_created": len(trades),
        "skipped_signals": filtered_count + sum(simulation_skips.values()),
        "skipped": {
            "market_regime_filter": filtered_count,
            **simulation_skips,
        },
    }

    # Calculate metrics
    summary = _calculate_metrics(
        trades,
        divergences,
        filtered_count,
        actual_start_date.strftime("%Y-%m-%d"),
        actual_end_date.strftime("%Y-%m-%d"),
        initial_capital=initial_capital,
    )

    # Equity curve (VND-based)
    equity_curve = _build_equity_curve(trades, df, initial_capital=initial_capital)

    return {
        "symbol": symbol,
        "analysis_period": {
            "start": actual_start_date.isoformat(),
            "end": actual_end_date.isoformat(),
        },
        "parameters": {
            "timeframe": timeframe,
            "bar_limit": requested_bar_limit,
            "range_mode": range_mode,
            "rsi_period": rsi_period,
            "lookback": lookback,
            "exit_strategy": exit_strategy,
            "holding_days": holding_days,
            "rsi_entry_min": rsi_entry_min,
            "rsi_entry_max": rsi_entry_max,
            "include_short": include_short,
            "max_concurrent_trades": max_concurrent_trades,
            "commission_pct": commission_pct,
            "slippage_pct": slippage_pct,
            "position_mode": position_mode,
            "position_size_pct": position_size_pct,
            "confirm_timeframe": confirm_timeframe,
            "confirm_rsi_min": confirm_rsi_min,
            "confirm_rsi_max": confirm_rsi_max,
            "trend_filter": trend_filter,
            "market_index": market_index,
            "initial_capital": initial_capital,
        },
        "data_quality": {
            "source": provider_source,
            "timeframe": timeframe,
            "requested_bar_limit": requested_bar_limit,
            "actual_bars": len(df),
            "provider_rows": provider_rows,
            "verified_trading_sessions": len(df),
            "excluded_rows": max(provider_rows - len(df), 0),
            "first_session": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_session": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "first_bar": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_bar": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "timeframe_supported": True,
            "unsupported_reason": None,
            "source_transform": source_transform,
            "rules": [
                "Không tạo ngày thay thế khi nguồn thiếu ngày",
                "Các dòng ngoài khoảng kiểm định không được đưa vào kết quả",
                "Chỉ dùng OHLC dương, nhất quán và ngày thứ Hai-thứ Sáu",
                "Loại phiên khối lượng 0 khi nguồn có dữ liệu khối lượng",
                "Tín hiệu xác nhận sau pivot; lệnh vào ở giá mở cửa phiên kế tiếp",
                "Intraday chỉ chạy khi có OHLC thật đã xác minh; không dựng bar giờ từ dữ liệu ngày",
            ],
        },
        "divergences": sorted(divergences, key=lambda item: item["date"], reverse=True),
        "trades": sorted(trades, key=lambda item: item["entry_date"], reverse=True),
        "summary": summary,
        "equity_curve": equity_curve,
        "execution_audit": execution_audit,
    }


def _empty_result(
    symbol: str,
    start_date: date,
    end_date: date,
    params: Dict[str, Any],
    reason: str,
    data_quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    quality = data_quality or {}
    source = quality.get("source") or "Không xác định"
    sessions = int(quality.get("verified_trading_sessions") or 0)
    error_messages = {
        "no_data": (
            f"Nguồn {source} không trả phiên OHLC hợp lệ cho mã {symbol} "
            "trong khoảng thời gian đã chọn."
        ),
        "insufficient_data": (
            f"Cần tối thiểu 50 phiên giao dịch thực để kiểm định; "
            f"nguồn {source} chỉ trả {sessions} phiên hợp lệ trong khoảng đã chọn."
        ),
        "unsupported_timeframe": (
            quality.get("unsupported_reason")
            or f"Khung thời gian {params.get('timeframe') or ''} chưa được hỗ trợ bằng dữ liệu OHLC thật."
        ),
    }
    return {
        "symbol": symbol,
        "error": error_messages.get(reason, "Lỗi không xác định."),
        "analysis_period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "parameters": params,
        "data_quality": quality,
        "divergences": [],
        "trades": [],
        "summary": {},
        "equity_curve": [],
        "execution_audit": {
            "total_detected_signals": 0,
            "eligible_after_regime": 0,
            "trades_created": 0,
            "skipped_signals": 0,
            "skipped": {
                "market_regime_filter": 0,
                "short_disabled": 0,
                "concurrency_limit": 0,
                "no_next_session": 0,
                "incomplete_exit_window": 0,
                "invalid_entry_price": 0,
            },
        },
    }
