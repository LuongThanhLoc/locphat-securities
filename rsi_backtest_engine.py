"""RSI Divergence Backtesting Engine v2 for Lộc Phát Securities.

Detects bullish and bearish RSI divergences, simulates trades with configurable
exit strategies, position sizing, multi-timeframe confirmation, and market regime filtering.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _frame(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows or []))
    if frame.empty or "close" not in frame:
        return pd.DataFrame()
    date_col = "date" if "date" in frame.columns else "time" if "time" in frame.columns else None
    if date_col:
        frame["date"] = pd.to_datetime(frame[date_col], errors="coerce")
    else:
        frame["date"] = pd.date_range(end=pd.Timestamp.today(), periods=len(frame), freq="B")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    frame = frame[frame["close"] > 0].reset_index(drop=True)
    return frame


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


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


def _enrich_weekly(weekly_frame: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    result = weekly_frame.copy()
    result["rsi"] = _rsi(result["close"], rsi_period)
    return result


# ------------------------------------------------------------------
# Extrema Detection
# ------------------------------------------------------------------

def _find_local_extrema(series: pd.Series, window: int = 5) -> pd.Series:
    values = series.ffill().values
    half = window // 2
    n = len(values)
    result = np.zeros(n, dtype=bool)
    for i in range(half, n - half):
        center_val = values[i]
        window_vals = values[i - half:i + half + 1]
        if center_val == window_vals.min():
            result[i] = True
    return pd.Series(result, index=series.index)


def _find_local_maxima(series: pd.Series, window: int = 5) -> pd.Series:
    values = series.ffill().values
    half = window // 2
    n = len(values)
    result = np.zeros(n, dtype=bool)
    for i in range(half, n - half):
        center_val = values[i]
        window_vals = values[i - half:i + half + 1]
        if center_val == window_vals.max():
            result[i] = True
    return pd.Series(result, index=series.index)


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
    divergences = []
    price = frame["close"].values
    rsi = frame["rsi"].values
    dates = frame["date"].values
    has_confirm = confirm_timeframe in ("1W", "1M") and weekly_rsi is not None

    for i in range(lookback, len(frame) - 1):
        current_rsi = rsi[i]
        if not np.isfinite(_finite(current_rsi)):
            continue

        # Multi-timeframe confirmation
        if has_confirm:
            weekly_val = _finite(weekly_rsi.iloc[i]) if i < len(weekly_rsi) else 50.0

        window_prices = price[i - lookback:i + 1]
        window_rsi = rsi[i - lookback:i + 1]
        half = lookback // 2

        if len(window_prices[:half]) < 3 or len(window_prices[half:]) < 3:
            continue

        # ---- Bullish divergence ----
        first_half_lows = window_prices[:half]
        second_half_lows = window_prices[half:]
        fl_low_idx = np.argmin(first_half_lows)
        sl_low_idx = np.argmin(second_half_lows) + half

        price_low_1 = price[i - lookback + fl_low_idx]
        price_low_2 = price[i - lookback + sl_low_idx]
        rsi_low_1 = rsi[i - lookback + fl_low_idx]
        rsi_low_2 = rsi[i - lookback + sl_low_idx]

        bullish_ok = (
            price_low_2 < price_low_1
            and rsi_low_2 > rsi_low_1
            and _finite(current_rsi) >= rsi_entry_min
            and _finite(current_rsi) < 55
        )
        if has_confirm:
            bullish_ok = bullish_ok and weekly_val < confirm_rsi_min

        if bullish_ok:
            divergences.append({
                "date": pd.Timestamp(dates[i]).strftime("%Y-%m-%d"),
                "type": "bullish",
                "price_at_signal": round(float(price[i]), 2),
                "rsi_at_signal": round(float(current_rsi), 1),
                "lookback_low_price": round(float(price_low_1), 2),
                "lookback_low_rsi": round(float(rsi_low_1), 1),
                "divergence_low_price": round(float(price_low_2), 2),
                "divergence_low_rsi": round(float(rsi_low_2), 1),
                "signal_bar_index": i,
                "weekly_rsi": round(float(weekly_val), 1) if has_confirm else None,
            })

        # ---- Bearish divergence ----
        first_half_highs = window_prices[:half]
        second_half_highs = window_prices[half:]
        fh_high_idx = np.argmax(first_half_highs)
        sh_high_idx = np.argmax(second_half_highs) + half

        price_high_1 = price[i - lookback + fh_high_idx]
        price_high_2 = price[i - lookback + sh_high_idx]
        rsi_high_1 = rsi[i - lookback + fh_high_idx]
        rsi_high_2 = rsi[i - lookback + sh_high_idx]

        bearish_ok = (
            price_high_2 > price_high_1
            and rsi_high_2 < rsi_high_1
            and _finite(current_rsi) <= rsi_entry_max
            and _finite(current_rsi) > 45
        )
        if has_confirm:
            bearish_ok = bearish_ok and weekly_val > confirm_rsi_max

        if bearish_ok:
            divergences.append({
                "date": pd.Timestamp(dates[i]).strftime("%Y-%m-%d"),
                "type": "bearish",
                "price_at_signal": round(float(price[i]), 2),
                "rsi_at_signal": round(float(current_rsi), 1),
                "lookback_high_price": round(float(price_high_1), 2),
                "lookback_high_rsi": round(float(rsi_high_1), 1),
                "divergence_high_price": round(float(price_high_2), 2),
                "divergence_high_rsi": round(float(rsi_high_2), 1),
                "signal_bar_index": i,
                "weekly_rsi": round(float(weekly_val), 1) if has_confirm else None,
            })

    return divergences


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
        bench_rsi = _rsi(benchmark_frame["close"], 14).values

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
            if idx < len(bench_rsi):
                bench_val = _finite(bench_rsi[idx])
                if div["type"] == "bullish" and bench_val >= 50:
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
    include_short: bool = True,
    max_concurrent_trades: int = 1,
    commission_pct: float = 0.0,
    slippage_pct: float = 0.0,
    position_mode: str = "full",
    position_size_pct: float = 100.0,
    atr_stop_multiple: float = 1.5,
    win_rate_approx: float = 50.0,
    avg_win_approx: float = 5.0,
    avg_loss_approx: float = 3.0,
) -> List[Dict[str, Any]]:
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

    traded_bars: set = set()

    for div in divergences:
        signal_idx = div["signal_bar_index"]
        div_type = div["type"]

        # Skip bearish if shorting is disabled
        if div_type == "bearish" and not include_short:
            continue

        # Skip if bar already has a trade
        if signal_idx in traded_bars or signal_idx + 1 in traded_bars:
            continue

        if signal_idx + 1 >= n:
            continue

        # Apply slippage to entry
        entry_raw = open_prices[signal_idx + 1]
        entry_slippage = entry_raw * (1 + slippage_pct / 100)
        entry_price = round(float(entry_slippage), 2)
        entry_date = pd.Timestamp(dates[signal_idx + 1]).strftime("%Y-%m-%d")

        if entry_price <= 0:
            continue

        # Determine exit
        exit_idx = None
        exit_price = None
        exit_reason = None
        holding = 0

        if exit_strategy == "time":
            exit_idx = min(signal_idx + 1 + holding_days, n - 1)
            exit_reason = "time_exit"

        elif exit_strategy == "rsi":
            max_check = min(signal_idx + 1 + 60, n)
            exit_idx = signal_idx + 1
            for j in range(signal_idx + 1, max_check):
                cur_rsi = rsi[j]
                if div_type == "bullish":
                    if cur_rsi >= 65 or j >= signal_idx + 1 + 60:
                        exit_idx = j
                        exit_reason = "rsi_overbought" if cur_rsi >= 65 else "max_days"
                        break
                else:
                    if cur_rsi <= 35 or j >= signal_idx + 1 + 60:
                        exit_idx = j
                        exit_reason = "rsi_oversold" if cur_rsi <= 35 else "max_days"
                        break
            if exit_idx is None:
                exit_idx = min(signal_idx + 1 + 60, n - 1)
                exit_reason = "max_days"

        elif exit_strategy == "trailing":
            exit_idx = signal_idx + 1
            entry_atr = _finite(atr[signal_idx + 1], entry_price * 0.02)
            trailing_stop = entry_price - atr_stop_multiple * entry_atr
            max_check = min(signal_idx + 1 + 60, n)

            for j in range(signal_idx + 1, max_check):
                cur_low = low_prices[j]
                cur_high = high_prices[j]
                cur_price = price[j]

                if div_type == "bullish":
                    if cur_price > trailing_stop + 0.5 * entry_atr:
                        trailing_stop = cur_price - atr_stop_multiple * entry_atr
                    if cur_low <= trailing_stop:
                        exit_idx = j
                        exit_price = round(float(trailing_stop), 2)
                        exit_reason = "trailing_stop"
                        break
                else:
                    # Short: exit when high exceeds trailing stop (short squeeze)
                    short_stop = entry_price + atr_stop_multiple * entry_atr
                    if cur_price < short_stop - 0.5 * entry_atr:
                        short_stop = cur_price + atr_stop_multiple * entry_atr
                    if cur_high >= short_stop:
                        exit_idx = j
                        exit_price = round(float(short_stop), 2)
                        exit_reason = "trailing_stop"
                        break

            if exit_reason is None:
                exit_idx = min(signal_idx + 1 + 20, n - 1)
                exit_reason = "max_days"

        # Calculate exit price with slippage
        if exit_idx is not None and exit_idx < n:
            exit_date = pd.Timestamp(dates[exit_idx]).strftime("%Y-%m-%d")
            holding = exit_idx - signal_idx - 1

            if exit_price is None:
                exit_raw = close_prices = price[exit_idx]
                exit_price = round(float(exit_raw * (1 - slippage_pct / 100)), 2)
            else:
                exit_price = round(float(exit_price * (1 - slippage_pct / 100)), 2)

            # P&L
            if div_type == "bullish":
                raw_pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                raw_pnl_pct = (entry_price - exit_price) / entry_price * 100

            # Commission
            commission_cost = (entry_price + exit_price) * commission_pct / 100
            net_pnl_pct = raw_pnl_pct - commission_cost

            # Max drawdown / runup
            max_drawdown = 0.0
            max_runup = 0.0

            for k in range(signal_idx + 1, exit_idx + 1):
                k_price = price[k]
                trade_pnl = (k_price - entry_price) / entry_price * 100 if div_type == "bullish" else (entry_price - k_price) / entry_price * 100
                if div_type == "bullish":
                    if trade_pnl < max_drawdown:
                        max_drawdown = trade_pnl
                    if trade_pnl > max_runup:
                        max_runup = trade_pnl
                else:
                    if -trade_pnl < max_drawdown:
                        max_drawdown = -trade_pnl
                    if trade_pnl > max_runup:
                        max_runup = trade_pnl

            # Mark bars
            for k in range(signal_idx + 1, exit_idx + 1):
                traded_bars.add(k)

            trades.append({
                "entry_date": entry_date,
                "exit_date": exit_date,
                "divergence_type": div_type,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(net_pnl_pct, 2),
                "raw_pnl_pct": round(raw_pnl_pct, 2),
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
        "filtered_signals": 0,
        "short_trades": 0,
        "long_trades": 0,
        "avg_commission_pct": 0.0,
        # VND metrics
        "initial_capital": initial_capital,
        "final_balance": initial_capital,
        "total_return_vnd": 0.0,
    }

    if not trades:
        base["total_signals"] = len(divergences)
        base["filtered_signals"] = filtered_signals
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

    # CAGR: compound annual growth rate from equity curve
    # Simplified: use total return over the period
    total_return = sum(pnls) / 100.0
    cagr = ((1 + total_return) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    rsi_values = [d["rsi_at_signal"] for d in divergences]
    avg_rsi = np.mean(rsi_values) if rsi_values else 50.0

    bullish_trades = [t for t in trades if t["divergence_type"] == "bullish"]
    bearish_trades = [t for t in trades if t["divergence_type"] == "bearish"]

    avg_bullish = np.mean([t["pnl_pct"] for t in bullish_trades]) if bullish_trades else 0.0
    avg_bearish = np.mean([t["pnl_pct"] for t in bearish_trades]) if bearish_trades else 0.0

    all_drawdowns = [t["max_drawdown_pct"] for t in trades]
    all_runups = [t["max_runup_pct"] for t in trades]

    # VND balance metrics
    total_return_pct = sum(pnls)
    final_balance = initial_capital * (1 + total_return_pct / 100)
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
    if not trades:
        return []

    equity = initial_capital
    equity_curve = []
    dates = frame["date"].values
    price = frame["close"].values

    # Build trade lookup: date -> trade
    trade_map: Dict[str, Dict] = {}
    for t in trades:
        trade_map[t["entry_date"]] = t

    start_price = frame["close"].iloc[0]
    benchmark = initial_capital

    for i in range(len(frame)):
        current_date = pd.Timestamp(dates[i]).strftime("%Y-%m-%d")
        current_price = price[i]
        benchmark = round((current_price / start_price) * initial_capital, 2)

        # Apply trade P&L when trade starts
        trade = trade_map.get(current_date)
        if trade is not None:
            equity = equity * (1 + trade["pnl_pct"] / 100)

        equity_curve.append({
            "date": current_date,
            "equity": round(max(equity, 0.01), 0),
            "benchmark": benchmark,
        })

    return equity_curve


# ------------------------------------------------------------------
# Weekly Data Resampling
# ------------------------------------------------------------------

def _resample_to_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (Monday close)."""
    if frame.empty:
        return pd.DataFrame()
    df = frame.set_index("date").copy()
    weekly = df[["open", "high", "low", "close", "volume"]].resample("W-MON").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index()
    return weekly


# ------------------------------------------------------------------
# Main API
# ------------------------------------------------------------------

def run_backtest(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    rsi_period: int = 14,
    lookback: int = 20,
    exit_strategy: str = "time",
    holding_days: int = 20,
    rsi_entry_min: float = 40.0,
    rsi_entry_max: float = 60.0,
    # v2 parameters
    include_short: bool = True,
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

    end_date = end or datetime.now().date()
    start_date = start or (end_date - timedelta(days=365 * 3))

    # Fetch daily data
    raw_df = Quote(symbol=symbol).history(
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
    )
    df = _frame(raw_df.to_dict("records"))

    if df.empty:
        return _empty_result(symbol, start_date, end_date, {
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "no_data")

    df = _enrich(df, rsi_period)

    if len(df) < 50:
        return _empty_result(symbol, start_date, end_date, {
            "rsi_period": rsi_period, "lookback": lookback,
            "exit_strategy": exit_strategy, "holding_days": holding_days,
            "include_short": include_short, "position_mode": position_mode,
            "confirm_timeframe": confirm_timeframe, "trend_filter": trend_filter,
        }, "insufficient_data")

    # Weekly data for multi-timeframe confirmation
    weekly_rsi: Optional[pd.Series] = None
    if confirm_timeframe in ("1W", "1M"):
        try:
            weekly_raw = Quote(symbol=symbol).history(
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )
            weekly_df = _frame(weekly_raw.to_dict("records"))
            if not weekly_df.empty:
                weekly_df = _enrich_weekly(weekly_df, rsi_period)
                weekly_rsi = weekly_df["rsi"]
        except Exception:
            weekly_rsi = None

    # Benchmark data for regime filter
    benchmark_frame: Optional[pd.DataFrame] = None
    if trend_filter != "none" and market_index:
        try:
            bench_raw = Quote(symbol=market_index).history(
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )
            benchmark_frame = _frame(bench_raw.to_dict("records"))
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
    )

    # Calculate metrics
    summary = _calculate_metrics(
        trades,
        divergences,
        filtered_count,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
        initial_capital=initial_capital,
    )

    # Equity curve (VND-based)
    equity_curve = _build_equity_curve(trades, df, initial_capital=initial_capital)

    return {
        "symbol": symbol,
        "analysis_period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "parameters": {
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
        "divergences": divergences,
        "trades": trades,
        "summary": summary,
        "equity_curve": equity_curve,
    }


def _empty_result(
    symbol: str,
    start_date: date,
    end_date: date,
    params: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    error_messages = {
        "no_data": f"Không có dữ liệu giá cho mã {symbol} trong khoảng thời gian này.",
        "insufficient_data": f"Cần tối thiểu 50 phiên giao dịch, chỉ có dữ liệu không đủ.",
    }
    return {
        "symbol": symbol,
        "error": error_messages.get(reason, "Lỗi không xác định."),
        "analysis_period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "parameters": params,
        "divergences": [],
        "trades": [],
        "summary": {},
        "equity_curve": [],
    }
