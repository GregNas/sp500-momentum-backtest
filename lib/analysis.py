"""Analysis primitives: event study, momentum backtest, performance stats.

Pure functions over monthly-return DataFrames/Series. No I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def event_study(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    horizon: int,
) -> pd.DataFrame:
    """For every month t, identify top_n stocks by trailing rank_lookback return,
    then record their average return at t+1, t+2, ..., t+horizon.

    Returns a long-form DataFrame: one row per (cohort, h, mean_return, median_return).
    """
    rows = []

    if rank_lookback == 1:
        ranking_signal = monthly_returns
    else:
        ranking_signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1

    for i in range(rank_lookback - 1, len(monthly_returns) - horizon):
        cohort_date = monthly_returns.index[i]
        ranking = ranking_signal.iloc[i].dropna()
        if len(ranking) < top_n:
            continue
        picks = ranking.nlargest(top_n).index

        for h in range(1, horizon + 1):
            future = monthly_returns.iloc[i + h][picks].dropna()
            if len(future) == 0:
                continue
            rows.append({
                "cohort": cohort_date,
                "h": h,
                "mean_return": future.mean(),
                "median_return": future.median(),
            })

    return pd.DataFrame(rows)


def event_study_summary(events: pd.DataFrame, benchmark_monthly: pd.Series) -> pd.DataFrame:
    """Aggregate event study by horizon, with benchmark comparison."""
    g = events.groupby("h")
    summary = pd.DataFrame({
        "avg_return":      g["mean_return"].mean(),
        "median_return":   g["mean_return"].median(),
        "win_rate":        g["mean_return"].apply(lambda s: (s > 0).mean()),
        "std":             g["mean_return"].std(),
        "n_cohorts":       g["mean_return"].count(),
    })
    bench_mean = benchmark_monthly.mean()
    summary["benchmark_avg"] = bench_mean
    summary["alpha_vs_bench"] = summary["avg_return"] - bench_mean
    summary["t_stat"] = summary["alpha_vs_bench"] / (summary["std"] / np.sqrt(summary["n_cohorts"]))
    return summary


def momentum_backtest(
    monthly_returns: pd.DataFrame,
    top_n: int,
    hold_period: int,
    rank_lookback: int,
) -> pd.Series:
    """Jegadeesh-Titman: K overlapping equal-weight portfolios, each rebalanced
    every K months. Each month's strategy return = average return of the K
    currently-active portfolios.
    """
    if rank_lookback == 1:
        signal = monthly_returns
    else:
        signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1

    returns = []
    dates = []
    for t in range(rank_lookback, len(monthly_returns)):
        sleeve_returns = []
        for k in range(hold_period):
            select_t = t - 1 - k
            if select_t < rank_lookback - 1:
                continue
            ranking = signal.iloc[select_t].dropna()
            if len(ranking) < top_n:
                continue
            picks = ranking.nlargest(top_n).index
            r = monthly_returns.iloc[t][picks].dropna()
            if len(r) > 0:
                sleeve_returns.append(r.mean())
        if sleeve_returns:
            returns.append(np.mean(sleeve_returns))
            dates.append(monthly_returns.index[t])

    return pd.Series(
        returns,
        index=pd.DatetimeIndex(dates),
        name=f"momentum_top{top_n}_hold{hold_period}",
    )


def recent_picks(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    n_cohorts: int = 6,
) -> list[dict]:
    """Last `n_cohorts` cohorts that have a realized 1-month forward return.

    Each entry: {month: 'YYYY-MM', picks: [tickers], return_pct: float}.
    Newest cohort first.
    """
    if rank_lookback == 1:
        ranking_signal = monthly_returns
    else:
        ranking_signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1

    out: list[dict] = []
    last_realized_idx = len(monthly_returns) - 2  # need t+1 to exist
    for i in range(last_realized_idx, rank_lookback - 2, -1):
        if len(out) >= n_cohorts:
            break
        ranking = ranking_signal.iloc[i].dropna()
        if len(ranking) < top_n:
            continue
        picks = ranking.nlargest(top_n).index.tolist()
        next_returns = monthly_returns.iloc[i + 1][picks].dropna()
        if next_returns.empty:
            continue
        out.append({
            "month": monthly_returns.index[i].strftime("%Y-%m"),
            "picks": picks,
            "return_pct": float(next_returns.mean()),
        })
    return out


def rebalance_schedule(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    n_months: int = 13,
) -> list[dict]:
    """Month-by-month trade list for a fully rebalanced top-N portfolio
    (the 1-month-hold sleeve): what enters, what exits, what stays.

    Unlike `recent_picks`, this includes the latest month-end — the cohort
    without a realized forward return yet — because that row IS the
    actionable trade list. Each entry, newest first:
    {month: 'YYYY-MM', buys: [...], sells: [...], holds: [...]}.
    """
    if rank_lookback == 1:
        ranking_signal = monthly_returns
    else:
        ranking_signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1

    picks_cache: dict[int, list | None] = {}

    def picks_at(i: int) -> list | None:
        if i not in picks_cache:
            ranking = ranking_signal.iloc[i].dropna()
            picks_cache[i] = (
                ranking.nlargest(top_n).index.tolist() if len(ranking) >= top_n else None
            )
        return picks_cache[i]

    out: list[dict] = []
    for i in range(len(monthly_returns) - 1, 0, -1):
        if len(out) >= n_months:
            break
        curr, prev = picks_at(i), picks_at(i - 1)
        if curr is None or prev is None:
            continue
        prev_set = set(prev)
        curr_set = set(curr)
        out.append({
            "month": monthly_returns.index[i].strftime("%Y-%m"),
            "buys": [t for t in curr if t not in prev_set],
            "sells": [t for t in prev if t not in curr_set],
            "holds": [t for t in curr if t in prev_set],
        })
    return out


def top_performers_period(
    prices: pd.DataFrame,
    top_n: int = 10,
    days: int = 30,
) -> list[dict]:
    """Rank tickers by simple return over the trailing `days` calendar days.

    Anchored on the last available bar (e.g. yesterday on a weekend) and looks
    back `days` calendar days from there. Picks the first bar at-or-after the
    target start date, so weekend/holiday boundaries are handled naturally.

    Returns the top_n entries sorted by return desc, each:
      {ticker, return_pct, start_date, end_date, start_price, end_price}
    """
    if prices.empty:
        return []

    end_ts = prices.index.max()
    target_start = end_ts - pd.Timedelta(days=days)
    valid_start_rows = prices.loc[prices.index >= target_start]
    if valid_start_rows.empty:
        return []
    start_ts = valid_start_rows.index[0]

    start_row = prices.loc[start_ts]
    end_row = prices.loc[end_ts]

    returns = (end_row / start_row - 1).dropna()
    if returns.empty:
        return []

    top = returns.nlargest(top_n)
    rows: list[dict] = []
    for ticker, ret in top.items():
        rows.append({
            "ticker": ticker,
            "return_pct": float(ret),
            "start_date": start_ts.strftime("%Y-%m-%d"),
            "end_date": end_ts.strftime("%Y-%m-%d"),
            "start_price": float(start_row[ticker]),
            "end_price": float(end_row[ticker]),
        })
    return rows


def rolling_sharpe(
    returns: pd.Series,
    window: int = 12,
    periods_per_year: int = 12,
) -> pd.Series:
    """Rolling annualized Sharpe ratio over a trailing window."""
    r = returns.astype(float)
    mean = r.rolling(window).mean() * periods_per_year
    vol = r.rolling(window).std() * np.sqrt(periods_per_year)
    return (mean / vol).replace([np.inf, -np.inf], np.nan)


def cohort_sector_breakdown(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    sector_map: dict[str, str],
    n_cohorts: int = 12,
) -> list[dict]:
    """For the last `n_cohorts` cohorts, count picks per GICS sector.

    Returns newest-first list of {month: 'YYYY-MM', sectors: {sector: count}}.
    """
    if rank_lookback == 1:
        ranking_signal = monthly_returns
    else:
        ranking_signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1

    out: list[dict] = []
    for i in range(len(monthly_returns) - 1, rank_lookback - 2, -1):
        if len(out) >= n_cohorts:
            break
        ranking = ranking_signal.iloc[i].dropna()
        if len(ranking) < top_n:
            continue
        picks = ranking.nlargest(top_n).index.tolist()
        sectors: dict[str, int] = {}
        for t in picks:
            s = sector_map.get(t) or "Unknown"
            sectors[s] = sectors.get(s, 0) + 1
        out.append({
            "month": monthly_returns.index[i].strftime("%Y-%m"),
            "sectors": sectors,
        })
    return out


def perf_stats(returns: pd.Series, periods_per_year: int = 12) -> dict:
    """Standard performance stats for a return series."""
    r = returns.dropna()
    cum = (1 + r).prod() - 1
    years = len(r) / periods_per_year
    cagr = (1 + cum) ** (1 / years) - 1 if years > 0 else 0
    vol = r.std() * np.sqrt(periods_per_year)
    sharpe = cagr / vol if vol > 0 else 0
    equity = (1 + r).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()
    return {
        "total_return": cum,
        "CAGR": cagr,
        "vol": vol,
        "Sharpe": sharpe,
        "max_drawdown": drawdown,
        "months": len(r),
    }
