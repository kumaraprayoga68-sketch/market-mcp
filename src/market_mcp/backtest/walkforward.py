"""Anchored walk-forward validation.

A single backtest over the whole history tells you what the best parameters
*were*, which is not the same as whether the strategy works. Walk-forward
re-optimises on data up to each fold and scores the fold that follows, so every
reported number comes from bars the optimiser had never seen.

The gap between in-sample and out-of-sample performance is the actual output
here: a strategy that only looks good in-sample is curve-fitted, and the
verdict says so.
"""

from __future__ import annotations

import itertools
from typing import Any

from ..models import Candle, iso
from .engine import run_backtest
from .strategies import Strategy, resolve_params

OBJECTIVES = ("sharpe", "total_return_pct", "calmar", "profit_factor")

MIN_FOLD_BARS = 40


def _grid_combos(strategy: Strategy, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Cartesian product of the strategy's grid, layered over fixed params."""
    keys = [k for k in strategy.grid if k in base]
    if not keys:
        return [dict(base)]
    combos = []
    for values in itertools.product(*(strategy.grid[k] for k in keys)):
        candidate = dict(base)
        candidate.update(dict(zip(keys, values)))
        combos.append(candidate)
    return combos


def _score(metrics: dict[str, Any], objective: str) -> float:
    v = metrics.get(objective)
    if v is None:
        return float("-inf")
    # Strategies that never trade produce a flat, riskless-looking curve; they
    # must not win an optimisation.
    if not metrics.get("trades"):
        return float("-inf")
    return float(v)


def walk_forward(
    candles: list[Candle],
    strategy: Strategy,
    *,
    symbol: str,
    interval: str,
    bars_per_year: float,
    folds: int = 4,
    objective: str = "sharpe",
    param_overrides: dict[str, Any] | None = None,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {list(OBJECTIVES)}")
    folds = max(2, min(int(folds), 10))

    n = len(candles)
    # Fold 0 is reserved as the first training block, so we need folds+1 slices.
    slice_size = n // (folds + 1)
    if slice_size < MIN_FOLD_BARS:
        raise ValueError(
            f"need at least {MIN_FOLD_BARS * (folds + 1)} candles for {folds} folds, got {n}; "
            "request a longer range or fewer folds"
        )

    base = resolve_params(strategy, param_overrides)
    combos = _grid_combos(strategy, base)

    fold_reports: list[dict[str, Any]] = []
    for f in range(1, folds + 1):
        train = candles[: f * slice_size]
        test = candles[f * slice_size : (f + 1) * slice_size]
        if len(test) < MIN_FOLD_BARS:
            break

        best_params: dict[str, Any] | None = None
        best_score = float("-inf")
        best_is: dict[str, Any] = {}

        for params in combos:
            try:
                res = run_backtest(
                    train,
                    strategy.fn(train, **params),
                    symbol=symbol,
                    strategy=strategy.name,
                    params=params,
                    interval=interval,
                    bars_per_year=bars_per_year,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                )
            except ValueError:
                continue
            s = _score(res.metrics, objective)
            if s > best_score:
                best_score, best_params, best_is = s, params, res.metrics

        if best_params is None:
            continue

        oos = run_backtest(
            test,
            strategy.fn(test, **best_params),
            symbol=symbol,
            strategy=strategy.name,
            params=best_params,
            interval=interval,
            bars_per_year=bars_per_year,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )

        fold_reports.append(
            {
                "fold": f,
                "train_bars": len(train),
                "test_bars": len(test),
                "test_start": iso(test[0]["ts"]),
                "test_end": iso(test[-1]["ts"]),
                "chosen_params": best_params,
                "in_sample": {
                    objective: best_is.get(objective),
                    "total_return_pct": best_is.get("total_return_pct"),
                    "trades": best_is.get("trades"),
                },
                "out_of_sample": {
                    objective: oos.metrics.get(objective),
                    "total_return_pct": oos.metrics.get("total_return_pct"),
                    "buy_and_hold_return_pct": oos.metrics.get("buy_and_hold_return_pct"),
                    "max_drawdown_pct": oos.metrics.get("max_drawdown_pct"),
                    "trades": oos.metrics.get("trades"),
                },
            }
        )

    if not fold_reports:
        raise ValueError("no fold produced a usable result; try a longer range or fewer folds")

    return {
        "symbol": symbol,
        "strategy": strategy.name,
        "interval": interval,
        "objective": objective,
        "folds_completed": len(fold_reports),
        "grid_size": len(combos),
        "folds": fold_reports,
        **_summarize(fold_reports, objective),
    }


def _summarize(folds: list[dict[str, Any]], objective: str) -> dict[str, Any]:
    is_vals = [f["in_sample"][objective] for f in folds if f["in_sample"][objective] is not None]
    oos_vals = [f["out_of_sample"][objective] for f in folds if f["out_of_sample"][objective] is not None]
    oos_rets = [f["out_of_sample"]["total_return_pct"] for f in folds]
    oos_bh = [f["out_of_sample"]["buy_and_hold_return_pct"] for f in folds]

    mean_is = sum(is_vals) / len(is_vals) if is_vals else None
    mean_oos = sum(oos_vals) / len(oos_vals) if oos_vals else None

    degradation = None
    if mean_is is not None and mean_oos is not None and abs(mean_is) > 1e-9:
        degradation = (mean_is - mean_oos) / abs(mean_is) * 100

    profitable = sum(1 for r in oos_rets if r is not None and r > 0)
    beat_bh = sum(
        1 for r, b in zip(oos_rets, oos_bh) if r is not None and b is not None and r > b
    )

    return {
        "summary": {
            f"mean_in_sample_{objective}": None if mean_is is None else round(mean_is, 3),
            f"mean_out_of_sample_{objective}": None if mean_oos is None else round(mean_oos, 3),
            "degradation_pct": None if degradation is None else round(degradation, 1),
            "mean_out_of_sample_return_pct": round(sum(r for r in oos_rets if r is not None) / len(oos_rets), 2) if oos_rets else None,
            "profitable_folds": f"{profitable}/{len(folds)}",
            "folds_beating_buy_and_hold": f"{beat_bh}/{len(folds)}",
            "params_stable": len({tuple(sorted(f["chosen_params"].items())) for f in folds}) == 1,
        },
        "verdict": _verdict(mean_oos, degradation, profitable, len(folds)),
    }


def _verdict(
    mean_oos: float | None, degradation: float | None, profitable: int, total: int
) -> dict[str, str]:
    if mean_oos is None:
        return {"label": "inconclusive", "reason": "no out-of-sample scores were produced"}

    if mean_oos <= 0:
        return {
            "label": "overfitted",
            "reason": "out-of-sample performance is non-positive; in-sample results did not carry forward",
        }
    if degradation is not None and degradation > 70:
        return {
            "label": "likely_overfitted",
            "reason": f"out-of-sample score is {degradation:.0f}% below in-sample — the grid search is fitting noise",
        }
    if profitable <= total / 2:
        return {
            "label": "fragile",
            "reason": f"only {profitable} of {total} folds were profitable out-of-sample",
        }
    if degradation is not None and degradation > 40:
        return {
            "label": "acceptable",
            "reason": f"positive out-of-sample, but {degradation:.0f}% weaker than in-sample; size positions conservatively",
        }
    return {
        "label": "robust",
        "reason": "out-of-sample performance held up across folds with limited degradation",
    }
