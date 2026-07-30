# market-mcp

An MCP server that gives an AI assistant real market data, technical analysis,
prediction-market odds and an honest strategy backtester — plus a scheduled
scanner that feeds a [live dashboard](https://market-mcp-dashboard.vercel.app)
and Telegram alerts.

Covers four things in one server:

| Domain | Source | No key needed |
|---|---|---|
| Crypto | Binance public spot API | ✅ |
| Global equities / ETFs / indices / FX | Yahoo Finance | ✅ |
| Indonesian equities (IDX) | Yahoo Finance `.JK` + bundled universe | ✅ |
| Prediction markets | Polymarket Gamma + CLOB | ✅ |

No API keys, no accounts, no paid tiers. Runtime dependencies are `mcp` and
`httpx` — indicators and the backtester are pure Python, so there is no
pandas/numpy wheel to fight with.

---

## Install

```bash
git clone https://github.com/kumaraprayoga68-sketch/market-mcp
cd market-mcp
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -e .
```

Then point your MCP client at it. For Claude Desktop, add this to
`claude_desktop_config.json` (use the absolute path to the venv's Python):

```json
{
  "mcpServers": {
    "market": {
      "command": "C:\\path\\to\\market-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "market_mcp.server"]
    }
  }
}
```

For an HTTP transport instead of stdio:

```bash
market-mcp --transport streamable-http --port 8000
```

---

## The 20 tools

**Prices**
- `get_price` — price, daily change, day range, 52-week position
- `get_prices` — many symbols at once; unresolvable ones are reported, not fatal
- `search_symbol` — company name → ticker
- `market_snapshot` — US/Asia indices, VIX, crypto, gold, oil, USD/IDR

**Analysis**
- `technical_analysis` — RSI, MACD, Bollinger, EMA 20/50/200, ATR, ADX, Supertrend, Stochastic + a composite rating
- `multi_timeframe_analysis` — weekly → hourly, and whether they agree
- `candlestick_patterns` — 13 patterns, each tagged with the trend it appeared in

**Screeners**
- `crypto_screener` — Binance spot by volume and 24h change
- `crypto_top_movers` — gainers/losers with a liquidity floor
- `stock_screener` — scan the bundled IDX (277) or US (117) universe
- `technical_scan` — scan for a *setup* (oversold, uptrend, squeeze, volume spike…), not just a price move
- `list_universes`

**Prediction markets**
- `prediction_markets` — most active markets
- `prediction_search` — keyword search, question matches outrank description matches
- `prediction_market_detail` — outcomes, prices, token ids
- `prediction_price_history` — how the market's probability has moved

**Backtesting**
- `list_strategies` — 9 strategies and their parameters
- `backtest_strategy` — one strategy, one instrument, with costs
- `compare_strategies` — rank all 9 against buy-and-hold
- `walk_forward_backtest` — out-of-sample validation with an overfitting verdict

---

## Scheduled scans, alerts and dashboard

Beyond the MCP server, the repo runs itself on a schedule:

```
GitHub Actions (every 4h)
  └─ scripts/run_scan.py
       ├─ snapshots/latest.json  ──►  Vercel dashboard (reads it straight from GitHub)
       └─ Telegram alert          ──►  only for setups that are new since the last run
```

`snapshots/latest.json` is committed back to the repo, so the dashboard picks up
fresh data without a redeploy: the cron owns the data, Vercel only renders it.

### Telegram alerts

Alerts fire only on findings **absent from the previous snapshot**. A scan that
runs every four hours will keep matching the same oversold ticker for days, and
an alert that fires every run is an alert you stop reading.

Set these in the repo under **Settings → Secrets and variables → Actions**:

| Secret | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → your bot → API token |
| `TELEGRAM_CHAT_ID` | message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[].message.chat.id` |

Without them the job still runs and simply skips the alert. Never commit the
token — it is a bearer credential for the whole bot.

### Running the scan by hand

```bash
python scripts/run_scan.py --quick --no-alert
```

`--quick` shrinks the universes and skips backtests; `--no-alert` never sends
Telegram. A full run takes a few minutes and produces roughly 80 KB of JSON.

Every section is independently guarded, so an outage at one venue degrades the
snapshot rather than losing the run. Failures land in `snapshot.errors` and the
dashboard shows them rather than pretending the data is complete.

### Dashboard

**Live: [market-mcp-dashboard.vercel.app](https://market-mcp-dashboard.vercel.app)**

`dashboard/` is a Next.js app with no UI dependencies beyond React.

```bash
cd dashboard && npm install && npm run dev
```

It fetches `snapshots/latest.json` over HTTP with a 5-minute revalidate, so new
scan data appears on its own. Only code changes need a redeploy:

```bash
cd dashboard && vercel deploy --prod
```

To get automatic deploys on every code push, connect the repository under
**Project Settings → Git** and set **Root Directory** to `dashboard`. That last
part matters and cannot be set from the CLI: without it Vercel builds from the
repository root, finds no `package.json`, and the build fails.

Point the dashboard at a different data source with the `SNAPSHOT_URL`
environment variable.

---

## Two things this server is careful about

### The backtester cannot see the future

A strategy sees bar *i* only after it closes, so the position it asks for is
held during bar *i+1*. This is enforced by the engine, not by convention, and
it is asserted in the test suite: an "oracle" strategy that reads the next bar
returns ~5600% on the same data where the identical rule, lagged one bar,
returns ~19%. If look-ahead ever leaked in, both numbers would be huge.

Costs are charged on position *changes*, so a long→short flip pays twice. Every
result reports `buy_and_hold_return_pct` beside it, because beating a flat
market is not an edge.

`walk_forward_backtest` is the tool that matters. It re-optimises parameters on
data up to each fold and scores the fold that follows, then compares in-sample
against out-of-sample and returns a verdict — `robust`, `acceptable`,
`fragile`, `likely_overfitted` or `overfitted`. Most strategies that look great
in a single backtest come back `overfitted`, which is the point.

### The rating explains itself, and knows when oscillators lie

`technical_analysis` returns a composite score, but also every individual vote
that produced it, so the reasoning can be inspected rather than trusted.

RSI pins near 100 for the whole of a real rally. A naive vote counter reads
that as bearish, lets it cancel the trend indicators, and rates an unmistakable
uptrend "neutral". When ADX shows a strong trend, this server drops oscillator
votes that oppose the trend and says so in the vote's reason. ADX itself never
votes on direction — it only scales confidence.

---

## Output shape

Every tool returns the same envelope:

```json
{ "ok": true, "data": { ... }, "error": null }
```

```json
{ "ok": false, "data": null,
  "error": { "code": "rate_limited", "message": "...", "retryable": true } }
```

`retryable` distinguishes "the upstream is having a moment" from "you asked for
something that does not exist", so a client knows whether trying again is
worthwhile. Codes: `bad_input`, `not_found`, `upstream_error`, `rate_limited`,
`timeout`, `internal`.

---

## Layout

```
src/market_mcp/
├── server.py         MCP entry point
├── core/             error envelope, TTL cache, shared HTTP with retry
├── providers/        yahoo, binance, idx, polymarket — plain async functions
├── indicators.py     pure-Python TA
├── patterns.py       candlestick detection
├── analysis.py       indicator snapshot + composite rating
├── scanning.py       signal matching, shared by the MCP tool and the cron job
├── alerting.py       snapshot diffing and Telegram formatting
├── market_data.py    one candle loader across all venues
├── backtest/         engine, strategies, walk-forward
└── tools/            MCP tool definitions
scripts/run_scan.py   the scheduled job
dashboard/            Next.js dashboard for Vercel
snapshots/            latest.json + trimmed history, written by the cron
data/                 idx.txt (277), us.txt (117)
tests/                130 tests, no network required
```

Providers know nothing about MCP; tools know nothing about HTTP. Adding a venue
means writing one provider module that returns the shared candle shape.

---

## Tests

```bash
pytest
```

130 tests, all offline — they cover indicator known-answer cases, engine
invariants (look-ahead, cost accounting, drawdown), symbol normalisation, the
error envelope, cache de-duplication, alert de-duplication, and server wiring.
CI runs them on Python 3.10, 3.12 and 3.14, plus a smoke job that starts the
server and checks it serves all 20 tools.

---

## Adding a strategy

Write a function that maps candles to positions (`+1` long, `0` flat, `-1`
short), then register it:

```python
def my_strategy(candles, period: int = 14) -> list[int]:
    r = ta.rsi(series(candles, "close"), period)
    return [0 if v is None else (1 if v < 25 else 0) for v in r]

Strategy(
    name="my_strategy",
    description="...",
    fn=my_strategy,
    defaults={"period": 14},
    grid={"period": [7, 14, 21]},   # searched by walk-forward
)
```

Warm-up bars must be `0` — a strategy is never allowed to act on an undefined
indicator. `test_every_strategy_produces_valid_signals` enforces the contract
for anything in the registry.

---

## Limits

- Yahoo caps intraday history (7 days of 1-minute bars, 60 days of sub-hourly).
- Polymarket has no server-side text search, so `prediction_search` scans the
  most active markets rather than every market ever created.
- IDX has no free bulk screener, so `stock_screener` quotes a curated universe
  file rather than the whole exchange.
- Backtests model costs as flat fee + slippage. Real fills, funding, borrow and
  liquidity are not modelled.

Not affiliated with TradingView, Binance, Yahoo or Polymarket. Information
only — not financial advice.
