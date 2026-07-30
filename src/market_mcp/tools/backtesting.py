"""Backtesting tools."""

from __future__ import annotations

import asyncio
from typing import Any

from ..backtest import REGISTRY, get, resolve_params, run_backtest, walk_forward
from ..core.errors import envelope
from ..market_data import load_candles
from ..models import clean

DISCLAIMER = (
    "Backtests are historical simulations, not predictions. Costs are modelled "
    "as a flat fee plus slippage on every position change; real fills, funding "
    "and liquidity are not modelled."
)


def register(server: Any) -> None:
    @server.tool()
    @envelope
    async def list_strategies() -> dict[str, Any]:
        """List the available backtest strategies with their parameters.

        Call this before backtest_strategy if you are unsure of a strategy name
        or which parameters it accepts.
        """
        return {
            "count": len(REGISTRY),
            "strategies": [
                {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.defaults,
                    "supports_short": s.supports_short,
                }
                for s in REGISTRY.values()
            ],
        }

    @server.tool()
    @envelope
    async def backtest_strategy(
        symbol: str,
        strategy: str,
        market: str = "crypto",
        interval: str = "1d",
        bars: int = 500,
        params: dict[str, Any] | None = None,
        fee_bps: float = 10.0,
        slippage_bps: float = 5.0,
        include_trades: bool = False,
    ) -> dict[str, Any]:
        """Backtest one strategy on one instrument.

        Args:
            symbol: Ticker (BTC, AAPL, BBCA).
            strategy: Strategy name from list_strategies.
            market: "crypto", "stock" or "idx".
            interval: Candle size (15m, 30m, 1h, 4h crypto-only, 1d, 1w).
            bars: Candles to test over (30-1500).
            params: Strategy parameter overrides, e.g. {"period": 21}.
            fee_bps: Round-trip fee in basis points per position change.
            slippage_bps: Assumed slippage in basis points per position change.
            include_trades: Return the full trade log as well as the metrics.

        Signals are acted on at the *next* bar, so results contain no
        look-ahead. Always compare `total_return_pct` against
        `buy_and_hold_return_pct` — beating a flat market is not an edge.
        """
        strat = get(strategy)
        resolved = resolve_params(strat, params)
        candles, meta = await load_candles(symbol, market, interval, bars)

        result = run_backtest(
            candles,
            strat.fn(candles, **resolved),
            symbol=meta["symbol"],
            strategy=strat.name,
            params=resolved,
            interval=meta["interval"],
            bars_per_year=meta["bars_per_year"],
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )

        out = result.to_dict(include_trades=include_trades)
        out["market"] = meta["market"]
        out["costs"] = {"fee_bps": fee_bps, "slippage_bps": slippage_bps}
        out["disclaimer"] = DISCLAIMER
        return clean(out)

    @server.tool()
    @envelope
    async def compare_strategies(
        symbol: str,
        market: str = "crypto",
        interval: str = "1d",
        bars: int = 500,
        fee_bps: float = 10.0,
        slippage_bps: float = 5.0,
        rank_by: str = "sharpe",
    ) -> dict[str, Any]:
        """Run every strategy on one instrument and rank them.

        Args:
            symbol: Ticker.
            market: "crypto", "stock" or "idx".
            interval: Candle size.
            bars: Candles to test over.
            fee_bps: Fee in basis points per position change.
            slippage_bps: Slippage in basis points per position change.
            rank_by: "sharpe", "total_return_pct", "calmar" or "profit_factor".

        A leaderboard on one symbol over one window is a weak signal — the
        winner is partly luck. Confirm anything promising with
        walk_forward_backtest before trusting it.
        """
        if rank_by not in ("sharpe", "total_return_pct", "calmar", "profit_factor"):
            raise ValueError(
                "rank_by must be 'sharpe', 'total_return_pct', 'calmar' or 'profit_factor'"
            )

        candles, meta = await load_candles(symbol, market, interval, bars)

        rows = []
        for strat in REGISTRY.values():
            try:
                res = run_backtest(
                    candles,
                    strat.fn(candles, **strat.defaults),
                    symbol=meta["symbol"],
                    strategy=strat.name,
                    params=strat.defaults,
                    interval=meta["interval"],
                    bars_per_year=meta["bars_per_year"],
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                )
            except ValueError:
                continue
            m = res.metrics
            rows.append(
                {
                    "strategy": strat.name,
                    "total_return_pct": m["total_return_pct"],
                    "sharpe": m["sharpe"],
                    "max_drawdown_pct": m["max_drawdown_pct"],
                    "calmar": m["calmar"],
                    "profit_factor": m["profit_factor"],
                    "win_rate_pct": m["win_rate_pct"],
                    "trades": m["trades"],
                    "exposure_pct": m["exposure_pct"],
                }
            )

        rows.sort(key=lambda r: r[rank_by] if r[rank_by] is not None else -1e9, reverse=True)
        buy_hold = None
        if rows:
            buy_hold = run_backtest(
                candles, [1] * len(candles), symbol=meta["symbol"], strategy="buy_and_hold",
                params={}, interval=meta["interval"], bars_per_year=meta["bars_per_year"],
                fee_bps=fee_bps, slippage_bps=slippage_bps,
            ).metrics

        return clean(
            {
                "symbol": meta["symbol"],
                "market": meta["market"],
                "interval": meta["interval"],
                "bars": meta["bars"],
                "ranked_by": rank_by,
                "ranking": rows,
                "buy_and_hold": {
                    "total_return_pct": buy_hold["total_return_pct"] if buy_hold else None,
                    "sharpe": buy_hold["sharpe"] if buy_hold else None,
                    "max_drawdown_pct": buy_hold["max_drawdown_pct"] if buy_hold else None,
                },
                "disclaimer": DISCLAIMER,
            }
        )

    @server.tool()
    @envelope
    async def walk_forward_backtest(
        symbol: str,
        strategy: str,
        market: str = "crypto",
        interval: str = "1d",
        bars: int = 1000,
        folds: int = 4,
        objective: str = "sharpe",
        fee_bps: float = 10.0,
        slippage_bps: float = 5.0,
    ) -> dict[str, Any]:
        """Validate a strategy out-of-sample and check whether it is curve-fitted.

        Args:
            symbol: Ticker.
            strategy: Strategy name from list_strategies.
            market: "crypto", "stock" or "idx".
            interval: Candle size.
            bars: Candles to use. Needs roughly 40 x (folds + 1) minimum.
            folds: Number of out-of-sample test windows (2-10).
            objective: Metric the parameter search optimises for.
            fee_bps: Fee in basis points per position change.
            slippage_bps: Slippage in basis points per position change.

        Optimises parameters on data up to each fold, then scores the fold that
        follows on bars the optimiser never saw. The `verdict` compares in- and
        out-of-sample results: this is the tool that catches a strategy that only
        looks good in hindsight.
        """
        strat = get(strategy)
        candles, meta = await load_candles(symbol, market, interval, bars)

        # The grid search runs folds x grid backtests, so keep the event loop
        # free while it churns.
        report = await asyncio.to_thread(
            walk_forward,
            candles,
            strat,
            symbol=meta["symbol"],
            interval=meta["interval"],
            bars_per_year=meta["bars_per_year"],
            folds=folds,
            objective=objective,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        report["market"] = meta["market"]
        report["bars"] = meta["bars"]
        report["disclaimer"] = DISCLAIMER
        return clean(report)
