"""Alert diffing and formatting.

The anti-spam behaviour is the point of this module, so it is pinned down here:
a repeated finding must not produce a second alert.
"""

import pytest

from market_mcp import alerting


def snapshot(scans, generated_at="2026-07-31T02:00:00"):
    return {"generated_at": generated_at, "scans": scans, "market": {"biggest_movers": []}}


def scan_block(**signals):
    return {
        sig: {"criterion": "x", "matched": len(rows), "results": rows}
        for sig, rows in signals.items()
    }


def hit(symbol, score=0.5, rsi=25.0, price=100.0):
    return {
        "symbol": symbol, "price": price, "rsi_14": rsi,
        "rating": "buy", "rating_score": score, "trend_strength": "moderate",
    }


def test_first_run_reports_everything_as_new():
    current = snapshot({"crypto": scan_block(oversold=[hit("BTCUSDT"), hit("ETHUSDT")])})
    findings = alerting.new_findings(current, None)
    assert {f["symbol"] for f in findings} == {"BTCUSDT", "ETHUSDT"}


def test_repeated_findings_are_not_realerted():
    scans = {"crypto": scan_block(oversold=[hit("BTCUSDT")])}
    previous = snapshot(scans)
    current = snapshot(scans, generated_at="2026-07-31T06:00:00")
    assert alerting.new_findings(current, previous) == []


def test_only_the_genuinely_new_symbol_is_reported():
    previous = snapshot({"crypto": scan_block(oversold=[hit("BTCUSDT")])})
    current = snapshot({"crypto": scan_block(oversold=[hit("BTCUSDT"), hit("SOLUSDT")])})
    findings = alerting.new_findings(current, previous)
    assert [f["symbol"] for f in findings] == ["SOLUSDT"]


def test_same_symbol_under_a_different_signal_is_new():
    """BTC oversold yesterday and BTC squeezing today are different events."""
    previous = snapshot({"crypto": scan_block(oversold=[hit("BTCUSDT")])})
    current = snapshot({"crypto": scan_block(squeeze=[hit("BTCUSDT")])})
    findings = alerting.new_findings(current, previous)
    assert len(findings) == 1
    assert findings[0]["signal"] == "squeeze"


def test_same_symbol_in_a_different_market_is_new():
    previous = snapshot({"crypto": scan_block(oversold=[hit("AAA")])})
    current = snapshot({
        "crypto": scan_block(oversold=[hit("AAA")]),
        "idx": scan_block(oversold=[hit("AAA")]),
    })
    findings = alerting.new_findings(current, previous)
    assert [f["market"] for f in findings] == ["idx"]


def test_findings_are_ranked_by_conviction():
    current = snapshot({
        "crypto": scan_block(oversold=[hit("WEAK", score=0.1), hit("STRONG", score=0.9)])
    })
    findings = alerting.new_findings(current, None)
    assert [f["symbol"] for f in findings] == ["STRONG", "WEAK"]


def test_missing_scan_section_is_handled():
    assert alerting.new_findings({"generated_at": "x"}, None) == []
    assert alerting.new_findings(snapshot({"crypto": {}}), None) == []


def test_message_says_so_when_nothing_is_new():
    text = alerting.format_message([], snapshot({}))
    assert "No new setups" in text


def test_message_groups_by_signal_and_lists_symbols():
    current = snapshot({
        "crypto": scan_block(
            oversold=[hit("BTCUSDT", rsi=22.0, price=64000.0)],
            squeeze=[hit("ETHUSDT", rsi=48.0, price=1900.0)],
        )
    })
    findings = alerting.new_findings(current, None)
    text = alerting.format_message(findings, current)

    assert "oversold" in text and "squeeze" in text
    assert "BTCUSDT" in text and "ETHUSDT" in text
    assert "64,000" in text  # thousands separator for readability
    assert "RSI 22" in text


def test_message_stays_within_telegram_limit():
    many = [hit(f"SYM{i}USDT", score=i / 500) for i in range(500)]
    current = snapshot({"crypto": scan_block(oversold=many)})
    findings = alerting.new_findings(current, None)
    text = alerting.format_message(findings, current, limit=400)
    assert len(text) <= alerting.MAX_MESSAGE + 40


def test_message_escapes_html():
    """Symbol names flow into an HTML-parse-mode message unescaped otherwise."""
    current = snapshot({"crypto": scan_block(oversold=[hit("<b>x</b>")])})
    text = alerting.format_message(alerting.new_findings(current, None), current)
    assert "<b>x</b>" not in text.replace("<b>market-mcp scan</b>", "")
    assert "&lt;b&gt;x&lt;/b&gt;" in text


def test_overview_line_shows_biggest_movers():
    snap = {
        "generated_at": "2026-07-31T02:00:00",
        "scans": {},
        "market": {"biggest_movers": [{"name": "VIX", "change_pct": -4.2}]},
    }
    assert "VIX" in alerting.format_message([], snap)


@pytest.mark.parametrize(
    "value,expected",
    [(64000.0, "64,000"), (12.3456, "12.35"), (0.00012345, "0.000123"), (None, "?")],
)
def test_price_formatting_adapts_to_magnitude(value, expected):
    assert alerting._fmt_price(value) == expected


def test_credentials_are_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert alerting.telegram_credentials() is None

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert alerting.telegram_credentials() == ("t", "c")


def test_partial_credentials_are_treated_as_unconfigured(monkeypatch):
    """Half-configured secrets must not produce a broken API call."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert alerting.telegram_credentials() is None
