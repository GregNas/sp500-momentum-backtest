"""Plotting: matplotlib Figures (no I/O). Caller decides what to do with them."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure


def plot_event_study(summary: pd.DataFrame) -> Figure:
    """Two-panel event study: avg forward return + win rate at each horizon."""
    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    bench_avg = summary["benchmark_avg"].iloc[0]
    ax[0].bar(summary.index, summary["avg_return"] * 100, alpha=0.7,
              label="Avg return of top-N picks")
    ax[0].axhline(bench_avg * 100, color="red", ls="--",
                  label=f"Benchmark avg ({bench_avg * 100:.2f}%/mo)")
    ax[0].set_ylabel("Avg return (%)")
    ax[0].set_title("Event study: avg forward return of top-N cohort")
    ax[0].legend()
    ax[0].grid(True, alpha=0.3)

    ax[1].bar(summary.index, summary["win_rate"] * 100, color="seagreen", alpha=0.7)
    ax[1].axhline(50, color="black", ls=":")
    ax[1].set_xlabel("Months after selection")
    ax[1].set_ylabel("Win rate (%)")
    ax[1].set_title("% of cohorts where the picks had positive avg return")
    ax[1].grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_equity_curves(
    strategies: dict[str, pd.Series],
    benchmark: pd.Series,
    benchmark_label: str = "SPY",
) -> Figure:
    """Log-scale equity curves for each strategy plus the benchmark."""
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, r in strategies.items():
        equity = (1 + r).cumprod()
        ax.plot(equity.index, equity.values, label=name, lw=1.5)

    first_strategy = next(iter(strategies.values()))
    bench_equity = (1 + benchmark.reindex(first_strategy.index).fillna(0)).cumprod()
    ax.plot(bench_equity.index, bench_equity.values,
            label=benchmark_label, color="black", lw=1.5, ls="--")

    ax.set_yscale("log")
    ax.set_title("Equity curves (log scale, $1 starting capital)")
    ax.set_ylabel("Equity")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig
