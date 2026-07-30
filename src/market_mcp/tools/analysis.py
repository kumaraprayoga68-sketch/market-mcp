"""Technical analysis tools."""

from __future__ import annotations

import asyncio
from typing import Any

from .. import analysis, patterns
from ..core.errors import envelope
from ..market_data import load_candles, normalize_market
from ..models import clean

# Ordered slowest -> fastest so alignment reads top-down like a trader would.
MTF_INTERVALS = {
    "crypto": ["1w", "1d", "4h", "1h"],
    "stock": ["1w", "1d", "1h"],
    "idx": ["1w", "1d", "1h"],
}


def register(server: Any) -> None:
    @server.tool()
    @envelope
    async def technical_analysis(
        symbol: str, market: str = "stock", interval: str = "1d", bars: int = 300
    ) -> dict[str, Any]:
        """Run a full technical analysis on one instrument.

        Args:
            symbol: Ticker (BTC, AAPL, BBCA).
            market: "crypto", "stock" or "idx".
            interval: 15m, 30m, 1h, 4h (crypto only), 1d or 1w.
            bars: How many candles to analyse. More bars means slower indicators
                like the 200-EMA actually warm up.

        Returns RSI, MACD, Bollinger, EMA20/50/200, ATR, ADX, Supertrend and
        Stochastic, plus support/resistance and a composite rating. The rating
        lists every individual vote, so you can explain *why* rather than just
        quoting a label.
        """
        candles, meta = await load_candles(symbol, market, interval, bars)
        out = analysis.summarize(candles, symbol=meta["symbol"], interval=meta["interval"])
        out["market"] = meta["market"]
        return clean(out)

    @server.tool()
    @envelope
    async def multi_timeframe_analysis(
        symbol: str, market: str = "stock", bars: int = 300
    ) -> dict[str, Any]:
        """Check whether timeframes agree on direction.

        Args:
            symbol: Ticker.
            market: "crypto", "stock" or "idx".
            bars: Candles per timeframe.

        Runs the composite rating on weekly through hourly candles. Aligned
        timeframes are the setups worth acting on; conflict between them is the
        usual reason a signal on one chart fails.
        """
        intervals = MTF_INTERVALS[normalize_market(market)]

        async def one(iv: str) -> dict[str, Any] | None:
            try:
                candles, meta = await load_candles(symbol, market, iv, bars)
                s = analysis.summarize(candles, symbol=meta["symbol"], interval=iv)
                return {
                    "symbol": meta["symbol"],
                    "interval": iv,
                    "price": s["indicators"]["price"],
                    "rating": s["rating"]["label"],
                    "score": s["rating"]["score"],
                    "trend_strength": s["rating"]["trend_strength"],
                    "rsi_14": s["indicators"]["rsi_14"],
                    "supertrend_direction": s["indicators"]["supertrend_direction"],
                }
            except Exception:  # noqa: BLE001 - one missing timeframe must not kill the rest
                return None

        rows = [r for r in await asyncio.gather(*(one(iv) for iv in intervals)) if r]
        if not rows:
            raise ValueError(f"no timeframe returned usable data for '{symbol}'")

        scores = [r["score"] for r in rows]
        bullish = sum(1 for s in scores if s > 0.15)
        bearish = sum(1 for s in scores if s < -0.15)

        if bullish == len(rows):
            alignment, note = "fully_bullish", "every timeframe agrees on upside"
        elif bearish == len(rows):
            alignment, note = "fully_bearish", "every timeframe agrees on downside"
        elif bullish and bearish:
            alignment, note = "conflicted", "timeframes disagree — the higher timeframe usually wins"
        else:
            alignment, note = "mixed", "no clear agreement across timeframes"

        resolved = rows[0].pop("symbol")
        for r in rows[1:]:
            r.pop("symbol", None)

        return clean(
            {
                "symbol": resolved,
                "market": market,
                "timeframes": rows,
                "alignment": alignment,
                "note": note,
                "average_score": round(sum(scores) / len(scores), 3),
            }
        )

    @server.tool()
    @envelope
    async def candlestick_patterns(
        symbol: str, market: str = "stock", interval: str = "1d", lookback: int = 5
    ) -> dict[str, Any]:
        """Detect candlestick patterns in the most recent bars.

        Args:
            symbol: Ticker.
            market: "crypto", "stock" or "idx".
            interval: Candle size.
            lookback: How many recent bars to scan (1-20).

        Each hit reports the trend it appeared in and whether that makes it a
        genuine reversal — a hammer inside an uptrend is not a bottom signal.
        """
        lookback = max(1, min(int(lookback), 20))
        candles, meta = await load_candles(symbol, market, interval, max(60, lookback + 40))
        found = patterns.detect(candles, lookback)
        return clean(
            {
                "symbol": meta["symbol"],
                "market": meta["market"],
                "interval": meta["interval"],
                "bars_scanned": lookback,
                "count": len(found),
                "patterns": found,
                "confirmed_reversals": [p for p in found if p["confirms_reversal"]],
            }
        )
