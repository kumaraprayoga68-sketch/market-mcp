"""Scheduled market scan: build a snapshot, alert on new findings.

Run by .github/workflows/scan.yml on a cron. Writes snapshots/latest.json for
the dashboard to read, keeps a trimmed history, and sends a Telegram message
listing only setups that were not in the previous snapshot.

Every section is independently guarded: an outage at one venue degrades the
snapshot rather than losing the whole run.

    python scripts/run_scan.py            # full run
    python scripts/run_scan.py --quick    # small universes, for testing
    python scripts/run_scan.py --no-alert # never send Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from market_mcp import analysis, alerting  # noqa: E402
from market_mcp.backtest import REGISTRY, get, run_backtest, walk_forward  # noqa: E402
from market_mcp.core.http import close_client  # noqa: E402
from market_mcp.market_data import load_candles  # noqa: E402
from market_mcp.models import clean  # noqa: E402
from market_mcp.providers import binance, idx, polymarket, yahoo  # noqa: E402
from market_mcp.scanning import SIGNALS, evaluate_symbols, matches, row  # noqa: E402

SNAPSHOT_DIR = REPO_ROOT / "snapshots"
HISTORY_DIR = SNAPSHOT_DIR / "history"
HISTORY_KEEP = 200

# What gets scanned. Sized so a full run stays inside a few minutes.
SCAN_SIGNALS = ["oversold", "overbought", "uptrend", "downtrend", "volume_spike", "squeeze"]
CRYPTO_SCAN_SIZE = 40
IDX_SCAN_SIZE = 40

WATCHLIST: list[tuple[str, str]] = [
    ("BTC", "crypto"), ("ETH", "crypto"), ("SOL", "crypto"),
    ("^JKSE", "stock"), ("BBCA", "idx"), ("BBRI", "idx"), ("TLKM", "idx"),
    ("AAPL", "stock"), ("NVDA", "stock"),
]

BACKTEST_TARGETS: list[tuple[str, str]] = [("BTC", "crypto"), ("ETH", "crypto")]
WALK_FORWARD_TARGETS: list[tuple[str, str, str]] = [("BTC", "crypto", "rsi_pullback")]

PREDICTION_TOPICS = ["fed", "bitcoin", "election"]

T = TypeVar("T")


class Recorder:
    """Runs each section, recording failures instead of propagating them."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []

    async def run(self, name: str, coro_fn: Callable[[], Awaitable[T]], default: T) -> T:
        started = time.monotonic()
        try:
            result = await coro_fn()
        except Exception as e:  # noqa: BLE001 - a failed section must not lose the run
            self.errors.append({"section": name, "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(limit=2, file=sys.stderr)
            return default
        print(f"  [ok]   {name} ({time.monotonic() - started:.1f}s)")
        return result


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------


async def build_market_overview() -> dict[str, Any]:
    pairs = [
        (sym, label, group)
        for group, items in yahoo.SNAPSHOT_SYMBOLS.items()
        for sym, label in items
    ]
    quotes = await yahoo.quotes_bulk([p[0] for p in pairs], concurrency=8)
    by_symbol = {q["symbol"]: q for q in quotes}

    groups: dict[str, list[dict[str, Any]]] = {}
    for sym, label, group in pairs:
        q = by_symbol.get(sym)
        if q is None:
            continue
        groups.setdefault(group, []).append(
            {
                "name": label,
                "symbol": sym,
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "currency": q.get("currency"),
            }
        )

    movers = [r for rows in groups.values() for r in rows if r.get("change_pct") is not None]
    movers.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
    return {"groups": groups, "biggest_movers": movers[:5]}


async def build_crypto(limit: int) -> dict[str, Any]:
    pairs = await binance.universe("USDT", 10_000_000)
    ranked = [p for p in pairs if p.get("change_pct") is not None]
    ranked.sort(key=lambda r: r["change_pct"], reverse=True)
    return {
        "universe_size": len(pairs),
        "by_volume": pairs[:limit],
        "gainers": ranked[:10],
        "losers": list(reversed(ranked[-10:])),
    }


async def build_idx(limit: int) -> dict[str, Any]:
    rows = await idx.scan("idx", max_symbols=limit)
    trimmed = [
        {
            "symbol": r["symbol"],
            "name": r.get("name"),
            "price": r.get("price"),
            "change_pct": r.get("change_pct"),
            "volume": r.get("volume"),
        }
        for r in rows
    ]
    return {
        "scanned": limit,
        "gainers": trimmed[:10],
        "losers": list(reversed(trimmed[-10:])),
    }


async def build_scans(market: str, symbols: list[str]) -> dict[str, Any]:
    """Analyse each symbol once, then test every signal against the result."""
    analysed, skipped = await evaluate_symbols(symbols, market, "1d", concurrency=6)

    out: dict[str, Any] = {}
    for signal in SCAN_SIGNALS:
        rows = [
            row(summary)
            for summary, candles in analysed.values()
            if matches(summary, candles, signal)
        ]
        rows.sort(key=lambda r: abs(r["rating_score"]), reverse=True)
        out[signal] = {
            "criterion": SIGNALS[signal],
            "matched": len(rows),
            "results": rows[:20],
        }

    out["_meta"] = {
        "requested": len(symbols),
        "evaluated": len(analysed),
        "skipped": len(skipped),
    }
    return out


async def build_watchlist() -> list[dict[str, Any]]:
    async def one(symbol: str, market: str) -> dict[str, Any] | None:
        try:
            candles, meta = await load_candles(symbol, market, "1d", 300)
            summary = analysis.summarize(candles, symbol=meta["symbol"], interval="1d")
        except Exception as e:  # noqa: BLE001 - one dead symbol must not drop the list
            return {"symbol": symbol, "market": market, "error": str(e)[:120]}

        ind = summary["indicators"]
        return {
            "symbol": meta["symbol"],
            "market": market,
            "price": ind["price"],
            "rating": summary["rating"]["label"],
            "rating_score": summary["rating"]["score"],
            "trend_strength": summary["rating"]["trend_strength"],
            "rsi_14": ind.get("rsi_14"),
            "adx_14": ind.get("adx_14"),
            "supertrend_direction": ind.get("supertrend_direction"),
            "ema_200": ind.get("ema_200"),
            "atr_pct_of_price": ind.get("atr_pct_of_price"),
            "bullish_votes": summary["rating"]["bullish_votes"],
            "bearish_votes": summary["rating"]["bearish_votes"],
            "support_20bar": summary["levels"]["support_20bar"],
            "resistance_20bar": summary["levels"]["resistance_20bar"],
        }

    results = await asyncio.gather(*(one(s, m) for s, m in WATCHLIST))
    return [r for r in results if r]


async def build_prediction() -> dict[str, Any]:
    top = await polymarket.list_markets(12)
    topics: dict[str, Any] = {}
    for topic in PREDICTION_TOPICS:
        try:
            topics[topic] = await polymarket.search_markets(topic, 5)
        except Exception as e:  # noqa: BLE001
            topics[topic] = {"error": str(e)[:120]}
    return {"top_by_volume": top, "topics": topics}


async def build_backtests() -> dict[str, Any]:
    out: dict[str, Any] = {"comparisons": [], "walk_forward": []}

    for symbol, market in BACKTEST_TARGETS:
        candles, meta = await load_candles(symbol, market, "1d", 700)
        rows = []
        for strat in REGISTRY.values():
            try:
                res = run_backtest(
                    candles,
                    strat.fn(candles, **strat.defaults),
                    symbol=meta["symbol"],
                    strategy=strat.name,
                    params=strat.defaults,
                    interval="1d",
                    bars_per_year=meta["bars_per_year"],
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
                    "win_rate_pct": m["win_rate_pct"],
                    "trades": m["trades"],
                }
            )
        rows.sort(key=lambda r: r["sharpe"] if r["sharpe"] is not None else -1e9, reverse=True)

        bh = run_backtest(
            candles, [1] * len(candles), symbol=meta["symbol"], strategy="buy_and_hold",
            params={}, interval="1d", bars_per_year=meta["bars_per_year"],
        ).metrics
        out["comparisons"].append(
            {
                "symbol": meta["symbol"],
                "market": market,
                "bars": meta["bars"],
                "ranking": rows,
                "buy_and_hold_return_pct": bh["total_return_pct"],
            }
        )

    for symbol, market, strategy in WALK_FORWARD_TARGETS:
        candles, meta = await load_candles(symbol, market, "1d", 900)
        report = await asyncio.to_thread(
            walk_forward, candles, get(strategy),
            symbol=meta["symbol"], interval="1d",
            bars_per_year=meta["bars_per_year"], folds=4,
        )
        out["walk_forward"].append(
            {
                "symbol": meta["symbol"],
                "strategy": strategy,
                "verdict": report["verdict"],
                "summary": report["summary"],
            }
        )

    return out


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


async def build_snapshot(quick: bool) -> dict[str, Any]:
    started = time.monotonic()
    rec = Recorder()

    crypto_size = 10 if quick else CRYPTO_SCAN_SIZE
    idx_size = 10 if quick else IDX_SCAN_SIZE

    print("building snapshot...")
    market = await rec.run("market_overview", build_market_overview, {})
    crypto = await rec.run("crypto", lambda: build_crypto(25), {})
    idx_data = await rec.run("idx", lambda: build_idx(idx_size), {})

    crypto_symbols = [p["symbol"] for p in (crypto.get("by_volume") or [])[:crypto_size]]
    if not crypto_symbols:
        # The universe call failed; fall back to majors so scans still produce
        # something rather than silently returning an empty section.
        crypto_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

    scans = {
        "crypto": await rec.run(
            "scans.crypto", lambda: build_scans("crypto", crypto_symbols), {}
        ),
        "idx": await rec.run(
            "scans.idx",
            lambda: build_scans("idx", list(idx.load_universe("idx"))[:idx_size]),
            {},
        ),
    }

    watchlist = await rec.run("watchlist", build_watchlist, [])
    prediction = await rec.run("prediction", build_prediction, {})
    backtests = await rec.run("backtests", build_backtests, {}) if not quick else {}

    return clean(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", ""),
            "duration_seconds": round(time.monotonic() - started, 1),
            "quick_mode": quick,
            "market": market,
            "crypto": crypto,
            "idx": idx_data,
            "scans": scans,
            "watchlist": watchlist,
            "prediction": prediction,
            "backtests": backtests,
            "errors": rec.errors,
        }
    )


def read_previous() -> dict[str, Any] | None:
    path = SNAPSHOT_DIR / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_snapshot(snapshot: dict[str, Any]) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(snapshot, indent=2, ensure_ascii=False)
    latest = SNAPSHOT_DIR / "latest.json"
    latest.write_text(payload, encoding="utf-8")

    stamp = snapshot["generated_at"].replace(":", "").replace("-", "")
    (HISTORY_DIR / f"{stamp}.json").write_text(payload, encoding="utf-8")

    # Keep history bounded; a repo that grows without limit becomes slow to clone.
    files = sorted(HISTORY_DIR.glob("*.json"))
    for stale in files[:-HISTORY_KEEP]:
        stale.unlink(missing_ok=True)

    return latest


async def main() -> int:
    parser = argparse.ArgumentParser(description="Build a market snapshot")
    parser.add_argument("--quick", action="store_true", help="small universes, skip backtests")
    parser.add_argument("--no-alert", action="store_true", help="never send Telegram")
    args = parser.parse_args()

    previous = read_previous()
    snapshot = await build_snapshot(args.quick)
    path = write_snapshot(snapshot)

    findings = alerting.new_findings(snapshot, previous)
    snapshot_summary = (
        f"{len(findings)} new findings, {len(snapshot['errors'])} section errors, "
        f"{snapshot['duration_seconds']}s"
    )
    print(f"wrote {path.relative_to(REPO_ROOT)} :: {snapshot_summary}")

    if args.no_alert:
        print("alerting disabled by --no-alert")
    else:
        creds = alerting.telegram_credentials()
        if creds is None:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping alert")
        elif not findings and previous is not None:
            print("no new findings; staying quiet")
        else:
            token, chat_id = creds
            message = alerting.format_message(findings, snapshot)
            try:
                await alerting.send_telegram(token, chat_id, message)
                print(f"telegram alert sent ({len(findings)} findings)")
            except Exception as e:  # noqa: BLE001 - a failed alert must not fail the run
                print(f"telegram send failed: {type(e).__name__}: {e}", file=sys.stderr)

    await close_client()
    # A snapshot with every section broken means something systemic; fail loudly.
    return 1 if len(snapshot["errors"]) >= 5 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
