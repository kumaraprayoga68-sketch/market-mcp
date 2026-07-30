"""Turn snapshot diffs into Telegram alerts.

The rule that makes this usable: alert on findings that are *new* since the
previous snapshot. A scan that runs every few hours will keep matching the same
oversold ticker for days, and an alert that fires every run is an alert you
stop reading.
"""

from __future__ import annotations

import html
import os
from typing import Any

from .core.http import fetch_json

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram rejects messages over 4096 characters.
MAX_MESSAGE = 3800

SIGNAL_EMOJI = {
    "oversold": "🟢",
    "overbought": "🔴",
    "uptrend": "📈",
    "downtrend": "📉",
    "bullish": "🐂",
    "bearish": "🐻",
    "volume_spike": "🔊",
    "squeeze": "🎯",
}


def finding_key(market: str, signal: str, symbol: str) -> str:
    return f"{market}:{signal}:{symbol}"


def _keys_in(snapshot: dict[str, Any] | None) -> set[str]:
    """Every (market, signal, symbol) present in a snapshot's scans."""
    if not snapshot:
        return set()
    keys = set()
    for market, signals in (snapshot.get("scans") or {}).items():
        for signal, payload in (signals or {}).items():
            for entry in (payload or {}).get("results", []):
                keys.add(finding_key(market, signal, entry["symbol"]))
    return keys


def new_findings(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Scan hits present now but absent from the previous snapshot.

    With no previous snapshot every hit is new, which is correct for a first
    run but would make it noisy — callers can cap what they send.
    """
    seen = _keys_in(previous)
    out: list[dict[str, Any]] = []

    for market, signals in (current.get("scans") or {}).items():
        for signal, payload in (signals or {}).items():
            for entry in (payload or {}).get("results", []):
                key = finding_key(market, signal, entry["symbol"])
                if key in seen:
                    continue
                out.append({**entry, "market": market, "signal": signal, "key": key})

    # Strongest convictions first, so a truncated message keeps what matters.
    out.sort(key=lambda f: abs(f.get("rating_score") or 0), reverse=True)
    return out


def format_message(
    findings: list[dict[str, Any]], snapshot: dict[str, Any], *, limit: int = 25
) -> str:
    """Build the Telegram message body (HTML parse mode)."""
    generated = snapshot.get("generated_at", "")
    lines = [f"<b>market-mcp scan</b>  <i>{html.escape(generated[:16])}Z</i>"]

    overview = _overview_line(snapshot)
    if overview:
        lines.append(overview)

    if not findings:
        lines.append("\nNo new setups since the last run.")
        return "\n".join(lines)

    by_signal: dict[str, list[dict[str, Any]]] = {}
    for f in findings[:limit]:
        by_signal.setdefault(f["signal"], []).append(f)

    for signal, group in by_signal.items():
        emoji = SIGNAL_EMOJI.get(signal, "•")
        lines.append(f"\n{emoji} <b>{html.escape(signal)}</b> ({len(group)})")
        for f in group:
            symbol = html.escape(str(f["symbol"]))
            price = _fmt_price(f.get("price"))
            bits = [f"  <code>{symbol}</code> {price}"]
            if f.get("rsi_14") is not None:
                bits.append(f"RSI {f['rsi_14']:.0f}")
            if f.get("rating"):
                bits.append(html.escape(str(f["rating"])))
            lines.append(" · ".join(bits))

    hidden = len(findings) - limit
    if hidden > 0:
        lines.append(f"\n<i>+{hidden} more not shown</i>")

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE].rsplit("\n", 1)[0] + "\n<i>…truncated</i>"
    return message


def _overview_line(snapshot: dict[str, Any]) -> str | None:
    movers = ((snapshot.get("market") or {}).get("biggest_movers")) or []
    if not movers:
        return None
    parts = []
    for m in movers[:3]:
        pct = m.get("change_pct")
        if pct is None:
            continue
        arrow = "▲" if pct >= 0 else "▼"
        parts.append(f"{html.escape(str(m['name']))} {arrow}{abs(pct):.2f}%")
    return "  ".join(parts) if parts else None


def _fmt_price(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def telegram_credentials() -> tuple[str, str] | None:
    """Read bot credentials from the environment, or None when unconfigured.

    Never hard-code these: the token is a bearer credential for the whole bot.
    In CI they belong in repository secrets.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


async def send_telegram(token: str, chat_id: str, text: str) -> dict[str, Any]:
    """Post a message. Raises ToolError on transport failure."""
    return await fetch_json(
        TELEGRAM_API.format(token=token),
        params={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        source="Telegram",
    )
