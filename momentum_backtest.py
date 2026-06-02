"""
S&P 500 Monthly Momentum — Thesis Test & Backtest (CLI entry)
=============================================================

Question: If you take the top N performers of the S&P 500 each month,
do they keep outperforming in subsequent months?

Two analyses:

1. EVENT STUDY — for every monthly cohort of "top N" stocks, measure the
   average forward return at horizons of 1, 2, ..., 12 months after
   selection. Tests "do winners keep winning?".

2. STRATEGY BACKTEST — Jegadeesh-Titman overlapping sleeves. Each month
   buy top N equal-weighted, hold for K months across K rolling
   sleeves. Compare to SPY.

Caveats:
- Survivorship bias: uses *current* S&P 500. Worse for longer lookbacks.
- Look-ahead bias: ranking uses month-end data only; forward returns
  start the next month. Clean.
- Ignores transaction costs, taxes, slippage.
- Equal weight, no sector / risk constraints.

Setup:
    pip install -r requirements.txt

Run:
    python momentum_backtest.py            # CLI mode (PNG + CSV outputs)
    streamlit run app.py                   # interactive web UI
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from lib import analysis, data, plotting

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# PARAMETERS  --  tweak freely
# ============================================================

LOOKBACK_YEARS  = 5          # how far back to pull data
TOP_N           = 10         # picks per month
HOLD_PERIODS    = [1, 3, 6]  # backtest holding horizons (months)
EVENT_HORIZON   = 12         # event study: months to track post-selection
RANK_LOOKBACK   = 1          # months of return used for ranking
BENCHMARK       = "SPY"
OUT_DIR         = Path("./momentum_output")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    end = datetime.today()
    start = end - timedelta(days=int(LOOKBACK_YEARS * 365.25) + 90)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    tickers = data.get_sp500_tickers() + [BENCHMARK]
    prices = data.get_prices(tickers, start_str, end_str, on_status=print)
    print(f"Got {prices.shape[1]} usable tickers, {prices.shape[0]} days.")

    if BENCHMARK not in prices.columns:
        raise SystemExit(f"Benchmark {BENCHMARK!r} returned no data from Yahoo.")

    benchmark_prices = prices[BENCHMARK]
    constituents = prices.drop(columns=[BENCHMARK])

    monthly = data.to_monthly_returns(constituents)
    benchmark_monthly = benchmark_prices.resample("ME").last().pct_change().dropna()

    # ---- 1. Event study ----
    print("\n--- EVENT STUDY ---")
    events = analysis.event_study(
        monthly, top_n=TOP_N, rank_lookback=RANK_LOOKBACK, horizon=EVENT_HORIZON,
    )
    summary = analysis.event_study_summary(events, benchmark_monthly)
    print(summary.round(4).to_string())
    summary.to_csv(OUT_DIR / "event_study_summary.csv")
    fig = plotting.plot_event_study(summary)
    fig.savefig(OUT_DIR / "event_study.png", dpi=120)
    print(f"Saved {OUT_DIR / 'event_study.png'}")

    # ---- 2. Strategy backtest ----
    print("\n--- STRATEGY BACKTEST ---")
    strategies = {}
    rows = []
    for hold in HOLD_PERIODS:
        r = analysis.momentum_backtest(
            monthly, top_n=TOP_N, hold_period=hold, rank_lookback=RANK_LOOKBACK,
        )
        strategies[f"top{TOP_N}_hold{hold}m"] = r
        stats = analysis.perf_stats(r)
        stats["strategy"] = f"top{TOP_N}_hold{hold}m"
        rows.append(stats)

    bench_aligned = benchmark_monthly.reindex(next(iter(strategies.values())).index).dropna()
    bstats = analysis.perf_stats(bench_aligned)
    bstats["strategy"] = BENCHMARK
    rows.append(bstats)

    perf = pd.DataFrame(rows).set_index("strategy")
    print(perf.round(4).to_string())
    perf.to_csv(OUT_DIR / "backtest_perf.csv")
    fig = plotting.plot_equity_curves(strategies, benchmark_monthly, benchmark_label=BENCHMARK)
    fig.savefig(OUT_DIR / "equity_curves.png", dpi=120)
    print(f"Saved {OUT_DIR / 'equity_curves.png'}")

    print(f"\nAll outputs in {OUT_DIR.resolve()}/")


if __name__ == "__main__":
    main()
