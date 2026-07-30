"""Backtest engine and performance metrics.

Timing convention, which is the part that decides whether results are honest:
a strategy sees bar *i* only after it closes, so the position it asks for is
held during bar *i+1*. Returns are close-to-close. That makes look-ahead bias
structurally impossible rather than something to remember not to do.

Costs are charged on position *changes* (`|Δposition| × cost`), so a flip from
long to short pays twice — as it should.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import Candle, iso


@dataclass
class Trade:
    side: str  # "long" | "short"
    entry_time: str | None
    exit_time: str | None
    entry_price: float
    exit_price: float
    return_pct: float
    bars_held: int


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    params: dict[str, Any]
    interval: str
    bars: int
    period_start: str | None
    period_end: str | None
    metrics: dict[str, Any]
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, include_trades: bool = False, include_equity: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
            "interval": self.interval,
            "bars": self.bars,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "metrics": self.metrics,
            "trade_count": len(self.trades),
        }
        if include_trades:
            out["trades"] = [asdict(t) for t in self.trades]
        if include_equity:
            out["equity_curve"] = self.equity_curve
        return out


def run_backtest(
    candles: list[Candle],
    signals: list[int],
    *,
    symbol: str,
    strategy: str,
    params: dict[str, Any],
    interval: str,
    bars_per_year: float,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Simulate `signals` over `candles`.

    `signals[i]` is the position the strategy wants after bar i closes:
    +1 long, 0 flat, -1 short. Its length must match `candles`.
    """
    n = len(candles)
    if n != len(signals):
        raise ValueError(f"signals length {len(signals)} != candles length {n}")
    if n < 3:
        raise ValueError("need at least 3 candles to backtest")

    closes = [c["close"] for c in candles]
    cost_rate = (fee_bps + slippage_bps) / 10_000.0

    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    prev_pos = 0
    bars_in_market = 0

    bar_returns: list[float] = []
    curve: list[dict[str, Any]] = [
        {"time": iso(candles[0]["ts"]), "equity": round(equity, 2), "position": 0}
    ]
    trades: list[Trade] = []
    open_trade: dict[str, Any] | None = None

    for i in range(1, n):
        pos = signals[i - 1]  # decided on the previous close, held through bar i

        if closes[i - 1] <= 0:
            gross = 0.0
        else:
            gross = (closes[i] - closes[i - 1]) / closes[i - 1] * pos

        turnover = abs(pos - prev_pos)
        net = gross - turnover * cost_rate

        equity *= 1 + net
        bar_returns.append(net)
        if pos != 0:
            bars_in_market += 1

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

        # Trade bookkeeping. Entry/exit fill at the close that produced the
        # signal, matching where the cost was charged.
        if pos != prev_pos:
            if open_trade is not None:
                trades.append(_close_trade(open_trade, candles, i - 1, closes[i - 1], cost_rate))
                open_trade = None
            if pos != 0:
                open_trade = {
                    "side": "long" if pos > 0 else "short",
                    "entry_index": i - 1,
                    "entry_price": closes[i - 1],
                }

        prev_pos = pos
        curve.append({"time": iso(candles[i]["ts"]), "equity": round(equity, 2), "position": pos})

    if open_trade is not None:
        trades.append(_close_trade(open_trade, candles, n - 1, closes[-1], cost_rate))

    elapsed_years = _years(candles)
    buy_hold = (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0.0

    metrics = _metrics(
        equity=equity,
        initial_capital=initial_capital,
        bar_returns=bar_returns,
        max_dd=max_dd,
        bars_per_year=bars_per_year,
        elapsed_years=elapsed_years,
        trades=trades,
        bars_in_market=bars_in_market,
        total_bars=n - 1,
        buy_hold_pct=buy_hold,
    )

    return BacktestResult(
        symbol=symbol,
        strategy=strategy,
        params=params,
        interval=interval,
        bars=n,
        period_start=iso(candles[0]["ts"]),
        period_end=iso(candles[-1]["ts"]),
        metrics=metrics,
        trades=trades,
        equity_curve=curve,
    )


def _close_trade(
    open_trade: dict[str, Any],
    candles: list[Candle],
    exit_index: int,
    exit_price: float,
    cost_rate: float,
) -> Trade:
    entry = open_trade["entry_price"]
    direction = 1 if open_trade["side"] == "long" else -1
    gross = (exit_price - entry) / entry * direction if entry > 0 else 0.0
    return Trade(
        side=open_trade["side"],
        entry_time=iso(candles[open_trade["entry_index"]]["ts"]),
        exit_time=iso(candles[exit_index]["ts"]),
        entry_price=round(entry, 8),
        exit_price=round(exit_price, 8),
        # Round-trip cost: one charge entering, one leaving.
        return_pct=round((gross - 2 * cost_rate) * 100, 4),
        bars_held=exit_index - open_trade["entry_index"],
    )


def _years(candles: list[Candle]) -> float:
    span_ms = candles[-1]["ts"] - candles[0]["ts"]
    return max(span_ms / (365.25 * 24 * 3600 * 1000), 1e-9)


def _metrics(
    *,
    equity: float,
    initial_capital: float,
    bar_returns: list[float],
    max_dd: float,
    bars_per_year: float,
    elapsed_years: float,
    trades: list[Trade],
    bars_in_market: int,
    total_bars: int,
    buy_hold_pct: float,
) -> dict[str, Any]:
    total_return = (equity / initial_capital - 1) * 100

    cagr = None
    if equity > 0 and elapsed_years > 1 / 365:
        cagr = ((equity / initial_capital) ** (1 / elapsed_years) - 1) * 100

    sharpe = sortino = None
    if len(bar_returns) > 1:
        mean = sum(bar_returns) / len(bar_returns)
        var = sum((r - mean) ** 2 for r in bar_returns) / (len(bar_returns) - 1)
        sd = math.sqrt(var)
        if sd > 0:
            sharpe = mean / sd * math.sqrt(bars_per_year)
        # Downside deviation averages the squared losses over *all* bars, not
        # just the losing ones. Dividing by the loss count instead would punish
        # strategies for being flat most of the time and push Sortino below
        # Sharpe, which inverts what the ratio is supposed to say.
        dsd = math.sqrt(sum(min(r, 0.0) ** 2 for r in bar_returns) / len(bar_returns))
        if dsd > 0:
            sortino = mean / dsd * math.sqrt(bars_per_year)

    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    gross_win = sum(t.return_pct for t in wins)
    gross_loss = abs(sum(t.return_pct for t in losses))

    calmar = None
    if cagr is not None and max_dd > 0:
        calmar = cagr / (max_dd * 100)

    return {
        "total_return_pct": round(total_return, 2),
        "buy_and_hold_return_pct": round(buy_hold_pct, 2),
        "excess_vs_buy_hold_pct": round(total_return - buy_hold_pct, 2),
        "cagr_pct": None if cagr is None else round(cagr, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": None if sharpe is None else round(sharpe, 2),
        "sortino": None if sortino is None else round(sortino, 2),
        "calmar": None if calmar is None else round(calmar, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_trade_pct": round(sum(t.return_pct for t in trades) / len(trades), 3) if trades else None,
        "best_trade_pct": round(max((t.return_pct for t in trades), default=0.0), 2) if trades else None,
        "worst_trade_pct": round(min((t.return_pct for t in trades), default=0.0), 2) if trades else None,
        "avg_bars_held": round(sum(t.bars_held for t in trades) / len(trades), 1) if trades else None,
        "exposure_pct": round(bars_in_market / total_bars * 100, 1) if total_bars else 0.0,
        "final_equity": round(equity, 2),
        "years": round(elapsed_years, 2),
    }
