"""Analysis primitives: event study, momentum backtest, performance stats.

Pure functions over monthly-return DataFrames/Series. No I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def momentum_signal(
    monthly_returns: pd.DataFrame,
    rank_lookback: int,
    skip_months: int = 0,
) -> pd.DataFrame:
    """The ranking signal: trailing ``rank_lookback``-month compounded return,
    optionally ending ``skip_months`` before the formation month.

    Row t holds the compounded return over the window
    ``(t - skip_months - rank_lookback, t - skip_months]``. ``skip_months=0``
    is the plain trailing return; ``skip_months=1`` with ``rank_lookback=11``
    is the classic "12-1" momentum spec, which drops the most recent month to
    sidestep 1-month reversal.

    Every ranking consumer (event study, backtest, cohorts, rebalance schedule,
    sector breakdown) must source its signal here so signal changes propagate
    to all of them consistently.
    """
    if rank_lookback == 1:
        signal = monthly_returns
    else:
        signal = (1 + monthly_returns).rolling(rank_lookback).apply(np.prod) - 1
    if skip_months:
        signal = signal.shift(skip_months)
    return signal


def event_study(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    horizon: int,
    skip_months: int = 0,
    benchmark_monthly: pd.Series | None = None,
) -> pd.DataFrame:
    """For every month t, identify top_n stocks by trailing rank_lookback return,
    then record their average return at t+1, t+2, ..., t+horizon.

    When ``benchmark_monthly`` is given, each row also carries ``bench_return``
    — the benchmark's actual return in that same forward month — so the summary
    can compute a time-aligned alpha instead of comparing against the
    benchmark's full-sample mean.

    Returns a long-form DataFrame: one row per (cohort, h, mean_return, median_return).
    """
    rows = []

    ranking_signal = momentum_signal(monthly_returns, rank_lookback, skip_months)
    bench = (benchmark_monthly.reindex(monthly_returns.index)
             if benchmark_monthly is not None else None)

    for i in range(rank_lookback - 1 + skip_months, len(monthly_returns) - horizon):
        cohort_date = monthly_returns.index[i]
        ranking = ranking_signal.iloc[i].dropna()
        if len(ranking) < top_n:
            continue
        picks = ranking.nlargest(top_n).index

        for h in range(1, horizon + 1):
            future = monthly_returns.iloc[i + h][picks].dropna()
            if len(future) == 0:
                continue
            row = {
                "cohort": cohort_date,
                "h": h,
                "mean_return": future.mean(),
                "median_return": future.median(),
            }
            if bench is not None:
                row["bench_return"] = bench.iloc[i + h]
            rows.append(row)

    return pd.DataFrame(rows)


def _newey_west_tstat(x: np.ndarray, lag: int | None = None) -> float:
    """t-statistic for H0: mean(x) == 0 using a Newey-West (HAC) standard error.

    Event-study cohorts are formed every month and their top-N picks overlap
    heavily from one month to the next, so the per-cohort series is
    autocorrelated. The classic ``mean / (std / sqrt(n))`` t-stat assumes i.i.d.
    observations and therefore overstates significance. A Bartlett-kernel
    Newey-West SE corrects for that autocorrelation.
    """
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 2:
        return np.nan
    mean = x.mean()
    e = x - mean
    if lag is None:
        # Standard automatic bandwidth (Newey-West 1994 plug-in rule).
        lag = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lag = max(0, min(lag, n - 1))
    gamma0 = np.dot(e, e) / n
    var = gamma0
    for k in range(1, lag + 1):
        weight = 1.0 - k / (lag + 1.0)          # Bartlett kernel
        cov = np.dot(e[k:], e[:-k]) / n
        var += 2.0 * weight * cov
    if var <= 0:
        return np.nan
    se_mean = np.sqrt(var / n)
    return mean / se_mean if se_mean > 0 else np.nan


def event_study_summary(events: pd.DataFrame, benchmark_monthly: pd.Series) -> pd.DataFrame:
    """Aggregate event study by horizon, with benchmark comparison.

    When the events carry a ``bench_return`` column (see :func:`event_study`),
    alpha is the mean of the *same-month* cohort-minus-benchmark spread,
    ``win_rate`` is the share of cohorts beating the benchmark at that horizon,
    and the ``t_stat`` tests that aligned spread — so cohorts formed ahead of
    strong market stretches don't look alpha-positive by construction. Without
    the column (legacy callers) the spread falls back to the benchmark's
    full-sample mean and ``win_rate`` counts cohorts above zero.

    The ``t_stat`` uses a Newey-West HAC standard error, so overlapping
    cohorts don't inflate significance. See :func:`_newey_west_tstat`.
    """
    bench_mean = benchmark_monthly.mean()
    aligned = "bench_return" in events.columns
    rows: dict[int, dict] = {}
    for h, grp in events.groupby("h"):
        s = grp["mean_return"]
        use_aligned = aligned
        if aligned:
            valid = grp.dropna(subset=["bench_return"])
            # A benchmark whose history doesn't cover this horizon at all
            # would make the aligned stats NaN — fall back to the legacy
            # full-sample-mean spread instead.
            use_aligned = len(valid) > 0
        if use_aligned:
            spread = valid["mean_return"] - valid["bench_return"]
            win_rate = (spread > 0).mean()
        else:
            spread = s - bench_mean
            win_rate = (s > 0).mean()
        rows[int(h)] = {
            "avg_return":     s.mean(),
            "median_return":  s.median(),
            "win_rate":       win_rate,
            "std":            s.std(),
            "n_cohorts":      int(s.count()),
            "benchmark_avg":  bench_mean,
            "alpha_vs_bench": spread.mean(),
            "t_stat":         _newey_west_tstat(spread.values),
        }
    cols = ["avg_return", "median_return", "win_rate", "std", "n_cohorts",
            "benchmark_avg", "alpha_vs_bench", "t_stat"]
    summary = pd.DataFrame.from_dict(rows, orient="index", columns=cols).sort_index()
    summary.index.name = "h"
    return summary


def momentum_backtest(
    monthly_returns: pd.DataFrame,
    top_n: int,
    hold_period: int,
    rank_lookback: int,
    skip_months: int = 0,
) -> pd.Series:
    """Jegadeesh-Titman: K overlapping equal-weight portfolios, each rebalanced
    every K months. Each month's strategy return = average return of the K
    currently-active portfolios.
    """
    signal = momentum_signal(monthly_returns, rank_lookback, skip_months)

    returns = []
    dates = []
    for t in range(rank_lookback + skip_months, len(monthly_returns)):
        sleeve_returns = []
        for k in range(hold_period):
            select_t = t - 1 - k
            if select_t < rank_lookback - 1 + skip_months:
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
    skip_months: int = 0,
) -> list[dict]:
    """Last `n_cohorts` cohorts that have a realized 1-month forward return.

    Each entry: {month: 'YYYY-MM', picks: [tickers], return_pct: float,
    pick_returns: {ticker: float | None}}. ``pick_returns`` is the per-ticker
    forward return the cohort mean averages over (None where the ticker has no
    bar that month). Newest cohort first.
    """
    ranking_signal = momentum_signal(monthly_returns, rank_lookback, skip_months)

    out: list[dict] = []
    last_realized_idx = len(monthly_returns) - 2  # need t+1 to exist
    for i in range(last_realized_idx, rank_lookback - 2 + skip_months, -1):
        if len(out) >= n_cohorts:
            break
        ranking = ranking_signal.iloc[i].dropna()
        if len(ranking) < top_n:
            continue
        picks = ranking.nlargest(top_n).index.tolist()
        forward = monthly_returns.iloc[i + 1][picks]
        next_returns = forward.dropna()
        if next_returns.empty:
            continue
        out.append({
            "month": monthly_returns.index[i].strftime("%Y-%m"),
            "picks": picks,
            "return_pct": float(next_returns.mean()),
            "pick_returns": {t: (None if pd.isna(forward[t]) else float(forward[t]))
                             for t in picks},
        })
    return out


def rebalance_schedule(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    n_months: int = 13,
    skip_months: int = 0,
) -> list[dict]:
    """Month-by-month trade list for a fully rebalanced top-N portfolio
    (the 1-month-hold sleeve): what enters, what exits, what stays.

    Unlike `recent_picks`, this includes the latest month-end — the cohort
    without a realized forward return yet — because that row IS the
    actionable trade list. Each entry, newest first:
    {month: 'YYYY-MM', buys: [...], sells: [...], holds: [...]}.
    Rows with a following month also carry ``return_pct`` and ``pick_returns``
    ({ticker: float | None}) — the realized forward return of that month's
    picks (month-to-date when the following month is still in progress).
    """
    ranking_signal = momentum_signal(monthly_returns, rank_lookback, skip_months)

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
        row = {
            "month": monthly_returns.index[i].strftime("%Y-%m"),
            "picks": curr,
            "buys": [t for t in curr if t not in prev_set],
            "sells": [t for t in prev if t not in curr_set],
            "holds": [t for t in curr if t in prev_set],
        }
        if i + 1 < len(monthly_returns):
            forward = monthly_returns.iloc[i + 1][curr]
            realized = forward.dropna()
            if len(realized):
                row["return_pct"] = float(realized.mean())
                row["pick_returns"] = {
                    t: (None if pd.isna(forward[t]) else float(forward[t])) for t in curr
                }
        out.append(row)
    return out


def _window_return_top_set(
    prices: pd.DataFrame, end_ts: pd.Timestamp, days: int, top_n: int,
) -> set | None:
    """The top_n ticker set by simple return over the `days` window ending at
    the last bar at-or-before `end_ts`, or None when the window can't be
    formed — including when history stops so short of the target start that
    the "same-length" window would silently collapse to a fraction of `days`
    (same 10-day reach-back tolerance as the trailing-return stats)."""
    upto = prices.loc[prices.index <= end_ts]
    if upto.empty:
        return None
    actual_end = upto.index[-1]
    target_start = actual_end - pd.Timedelta(days=days)
    from_start = prices.loc[prices.index >= target_start]
    if (from_start.empty or from_start.index[0] >= actual_end
            or (from_start.index[0] - target_start).days > 10):
        return None
    r = (upto.iloc[-1] / from_start.iloc[0] - 1).dropna()
    if r.empty:
        return None
    return set(r.nlargest(top_n).index)


def _pick_quality_stats(col: pd.Series, start_ts, end_ts) -> dict:
    """Per-pick context stats from daily closes: volatility, squeeze detector,
    52-week-high proximity, trailing returns, and a weekly sparkline. Fields
    are None when the ticker's history doesn't reach far enough back."""
    end_price = float(col.loc[end_ts])
    # pct_change on the raw (gappy) series: a return across missing bars comes
    # out NaN and is excluded, instead of a multi-day move masquerading as a
    # single-day one (which would inflate vol_ann and fake a max_day_move).
    window = col.loc[start_ts:end_ts]
    daily = window.pct_change(fill_method=None).dropna()
    out: dict = {
        "vol_ann": float(daily.std() * np.sqrt(252)) if len(daily) >= 5 else None,
        "max_day_move": (float(daily.iloc[int(np.argmax(np.abs(daily.values)))])
                         if len(daily) else None),
    }
    year = col.loc[col.index >= end_ts - pd.Timedelta(days=365)].dropna()
    out["below_52w_high"] = (float(end_price / year.max() - 1)
                             if len(year) and year.max() > 0 else None)
    for label, d in (("ret_3m", 91), ("ret_6m", 182), ("ret_12m", 365)):
        target = end_ts - pd.Timedelta(days=d)
        past = col.loc[col.index >= target].dropna()
        if past.empty or (past.index[0] - target).days > 10:
            out[label] = None  # history doesn't reach back that far
        else:
            out[label] = float(end_price / float(past.iloc[0]) - 1)
    weekly = year.resample("W").last().dropna()
    out["spark"] = ([round(float(v), 3) for v in weekly]
                    if len(weekly) >= 8 else None)
    return out


def top_performers_period(
    prices: pd.DataFrame,
    top_n: int = 10,
    days: int = 30,
    watch_n: int = 0,
) -> list[dict]:
    """Rank tickers by simple return over the trailing `days` calendar days.

    Anchored on the last available bar (e.g. yesterday on a weekend) and looks
    back `days` calendar days from there. Picks the first bar at-or-after the
    target start date, so weekend/holiday boundaries are handled naturally.

    With ``watch_n > 0`` the list continues past the leaderboard into the
    "bubbling under" ranks top_n+1..top_n+watch_n (streak/new-entrant stats
    stay relative to top_n).

    Returns the top_n (+ watch_n) entries sorted by return desc, each:
      {ticker, return_pct, start_date, end_date, start_price, end_price,
       vol_ann, max_day_move, below_52w_high, ret_3m, ret_6m, ret_12m,
       spark, streak, prior_rank, new_entrant}
    ``streak`` counts how many consecutive *prior* windows of the same length
    the ticker already ranked in the top_n (capped at 12) — signal persistence.
    ``prior_rank`` is the ticker's rank over the same-length window ending at
    the last bar ≥ 7 calendar days earlier (rank velocity); ``new_entrant`` is
    True when the ticker was absent one full window back from the top
    ``max(2N, N + watch_n)`` — i.e. it wasn't anywhere in the displayed region.
    Context fields are None where the cached history doesn't reach back far
    enough (e.g. a fetch window shorter than a year).
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

    top = returns.nlargest(top_n + max(0, watch_n))

    # Prior-window top-N sets, shared by every pick's streak count.
    prior_sets: list[set | None] = []
    for k in range(1, 13):
        prior_sets.append(
            _window_return_top_set(prices, end_ts - pd.Timedelta(days=k * days),
                                   days=days, top_n=top_n)
        )

    # NEW-entrant reference: one full window back, wide enough to cover the
    # whole displayed region (leaderboard + watchlist) — otherwise a name
    # sitting stationary just past 2N would be flagged NEW on every refresh.
    ref_top_n = max(top_n * 2, top_n + max(0, watch_n))
    prev_top_ref = _window_return_top_set(
        prices, end_ts - pd.Timedelta(days=days), days=days, top_n=ref_top_n,
    )

    # Full-universe ranks over the same-length window ending ≥ 7 days earlier,
    # for the rank-velocity arrows.
    prior_ranks = None
    upto_prev = prices.loc[prices.index <= end_ts - pd.Timedelta(days=7)]
    if not upto_prev.empty:
        prev_end = upto_prev.index[-1]
        prev_from = prices.loc[prices.index >= prev_end - pd.Timedelta(days=days)]
        if not prev_from.empty and prev_from.index[0] < prev_end:
            prev_returns = (upto_prev.iloc[-1] / prev_from.iloc[0] - 1).dropna()
            if not prev_returns.empty:
                prior_ranks = prev_returns.rank(ascending=False, method="min")

    rows: list[dict] = []
    for ticker, ret in top.items():
        streak = 0
        for s in prior_sets:
            if s is None or ticker not in s:
                break
            streak += 1
        rows.append({
            "ticker": ticker,
            "return_pct": float(ret),
            "start_date": start_ts.strftime("%Y-%m-%d"),
            "end_date": end_ts.strftime("%Y-%m-%d"),
            "start_price": float(start_row[ticker]),
            "end_price": float(end_row[ticker]),
            "streak": streak,
            "prior_rank": (int(prior_ranks[ticker])
                           if prior_ranks is not None and ticker in prior_ranks.index
                           else None),
            "new_entrant": ((ticker not in prev_top_ref)
                            if prev_top_ref is not None else None),
            **_pick_quality_stats(prices[ticker], start_ts, end_ts),
        })
    return rows


# Average calendar days per month. A ``rank_lookback`` of N months maps to
# ``round(N * _MONTH_DAYS)`` calendar days, so the live current target uses the
# same trailing window the Top Performers leaderboard does (1 month -> 30 days,
# matching its default).
_MONTH_DAYS = 30.4375


def current_target(
    prices: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    skip_months: int = 0,
) -> dict | None:
    """The momentum portfolio *as of the latest price bar*.

    Ranks by simple return over a trailing window of ``rank_lookback`` months
    (converted to calendar days), anchored on the last available bar — the exact
    same signal :func:`top_performers_period` uses. This is what makes the
    dashboard's "current target" line up with the Top Performers leaderboard at
    matching settings, instead of ranking on a 1-3 trading-day partial calendar
    month right after a month-end.

    With ``skip_months > 0`` the ranking window ends that many months *before*
    the latest bar (the daily-frequency mirror of :func:`momentum_signal`'s
    skip); ``as_of`` still reports the latest bar the target is current as of.

    Returns ``{tickers, as_of, start_date, signal_end, days}`` (tickers ordered
    by return, highest first), or ``None`` when there isn't enough price data.
    """
    if prices.empty:
        return None
    days = round(rank_lookback * _MONTH_DAYS)
    window = prices
    if skip_months:
        cutoff = prices.index.max() - pd.Timedelta(days=round(skip_months * _MONTH_DAYS))
        window = prices.loc[prices.index <= cutoff]
    ranked = top_performers_period(window, top_n=top_n, days=days)
    if not ranked:
        return None
    return {
        "tickers": [r["ticker"] for r in ranked],
        "as_of": prices.index.max().strftime("%Y-%m-%d"),
        "start_date": ranked[0]["start_date"],
        "signal_end": ranked[0]["end_date"],
        "days": days,
    }


def live_rebalance(
    prices: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    n_months: int = 13,
    skip_months: int = 0,
) -> list[dict]:
    """Rebalance trade list for the dashboard: a live "as of today" target row
    on top, followed by the completed month-end rebalance rows.

    The live row (``is_live=True``) ranks by :func:`current_target` — a trailing
    window ending on the latest bar — and diffs against the most recent
    *completed* month's picks (what you'd be holding from the last month-end
    rebalance). The in-progress (partial) month is dropped from the history rows
    so it isn't shown twice. History rows are the untouched month-end sleeve.

    Falls back to the raw :func:`rebalance_schedule` output if there isn't enough
    daily data to form a live target.
    """
    history = rebalance_schedule(
        monthly_returns, top_n=top_n, rank_lookback=rank_lookback, n_months=n_months,
        skip_months=skip_months,
    )
    live = current_target(
        prices, top_n=top_n, rank_lookback=rank_lookback, skip_months=skip_months,
    )
    if live is None:
        return history

    # The newest monthly bar is "in progress" when the latest price bar predates
    # its calendar month-end label (e.g. a mid-month run). Drop that partial row
    # — the live row supersedes it.
    partial_month = None
    if len(monthly_returns.index) and prices.index.max() < monthly_returns.index[-1]:
        partial_month = monthly_returns.index[-1].strftime("%Y-%m")
    completed = [row for row in history if row["month"] != partial_month]

    # The newest completed row's forward month is the in-progress month, so its
    # realized return is month-to-date, not a full month — flag it so the UI
    # and CSV don't present it as final.
    if partial_month and completed and len(monthly_returns.index) >= 2:
        prev_label = monthly_returns.index[-2].strftime("%Y-%m")
        if completed[0]["month"] == prev_label and "return_pct" in completed[0]:
            completed[0]["fwd_partial"] = True
            completed[0]["fwd_as_of"] = prices.index.max().strftime("%Y-%m-%d")

    prev = completed[0]["picks"] if completed else []
    prev_set = set(prev)
    target = live["tickers"]
    target_set = set(target)
    live_row = {
        "month": live["as_of"],
        "as_of": live["as_of"],
        "is_live": True,
        "picks": target,
        "buys": [t for t in target if t not in prev_set],
        "sells": [t for t in prev if t not in target_set],
        "holds": [t for t in target if t in prev_set],
    }
    return ([live_row] + completed)[:n_months]


def rolling_sharpe(
    returns: pd.Series,
    window: int = 12,
    periods_per_year: int = 12,
    risk_free_annual: float = 0.0,
) -> pd.Series:
    """Rolling annualized Sharpe ratio over a trailing window.

    Same definition as `perf_stats`: annualized arithmetic mean excess return
    over annualized volatility.
    """
    r = returns.astype(float)
    mean = r.rolling(window).mean() * periods_per_year
    vol = r.rolling(window).std() * np.sqrt(periods_per_year)
    return ((mean - risk_free_annual) / vol).replace([np.inf, -np.inf], np.nan)


def cohort_sector_breakdown(
    monthly_returns: pd.DataFrame,
    top_n: int,
    rank_lookback: int,
    sector_map: dict[str, str],
    n_cohorts: int = 12,
    skip_months: int = 0,
) -> list[dict]:
    """For the last `n_cohorts` cohorts, count picks per GICS sector.

    Returns newest-first list of {month: 'YYYY-MM', sectors: {sector: count}}.
    """
    ranking_signal = momentum_signal(monthly_returns, rank_lookback, skip_months)

    out: list[dict] = []
    for i in range(len(monthly_returns) - 1, rank_lookback - 2 + skip_months, -1):
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


def perf_stats(
    returns: pd.Series,
    periods_per_year: int = 12,
    risk_free_annual: float = 0.0,
) -> dict:
    """Standard performance stats for a return series.

    Sharpe follows the textbook definition: annualized *arithmetic* mean excess
    return over annualized volatility, with an explicit annual risk-free rate
    (default 0). This matches `rolling_sharpe`; earlier versions divided CAGR by
    vol, which mixed a geometric numerator with an arithmetic denominator and
    silently assumed a zero risk-free rate.
    """
    r = returns.dropna()
    cum = (1 + r).prod() - 1
    years = len(r) / periods_per_year
    cagr = (1 + cum) ** (1 / years) - 1 if years > 0 else 0
    ann_mean = r.mean() * periods_per_year
    vol = r.std() * np.sqrt(periods_per_year)
    sharpe = (ann_mean - risk_free_annual) / vol if vol > 0 else 0
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


def calendar_year_returns(returns: pd.Series) -> list[dict]:
    """Compound a monthly return series within each calendar year.

    Returns an oldest-first list of
    ``{year, return_pct, months, partial}``. ``partial`` is True when the year
    has fewer than 12 monthly observations (a truncated first year or the
    current year-to-date), so the UI can label it.
    """
    r = returns.dropna()
    if r.empty:
        return []
    out: list[dict] = []
    for year, grp in r.groupby(r.index.year):
        comp = float((1 + grp).prod() - 1)
        out.append({
            "year": int(year),
            "return_pct": comp,
            "months": int(len(grp)),
            "partial": bool(len(grp) < 12),
        })
    return out
