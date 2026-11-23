"""Analysis primitives: event study, momentum backtest, performance stats.

Pure functions over monthly-return DataFrames/Series. No I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Split-shaped bounds for the ledger's recorded-vs-cached basis audit. Imported
# rather than redefined so the "verify basis" chip fires on exactly the
# magnitudes _heal_split_artifacts treats as a re-adjustment: auto_adjust
# re-adjusts dividends too, so a percent-scale threshold would light up on every
# aged row. (Constants only — this module still does no I/O.)
from lib.cache import SPLIT_JUMP_HI, SPLIT_JUMP_LO


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


_DETAIL_COLUMNS = ["gross", "trade", "turnover", "cost", "net"]


def momentum_backtest_detail(
    monthly_returns: pd.DataFrame,
    top_n: int,
    hold_period: int,
    rank_lookback: int,
    skip_months: int = 0,
    cost_bps: float = 0.0,
    signal: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Jegadeesh-Titman sleeves plus the turnover and trading cost they imply.

    K = ``hold_period`` overlapping equal-weight portfolios, each rebalanced
    every K months; a month's gross return is the average of the sleeves active
    that month. Columns, indexed by month:

    ``gross``    what :func:`momentum_backtest` returns.
    ``trade``    sum of absolute portfolio-weight changes, 0 to 2.
    ``turnover`` one-way turnover, ``trade / 2`` (1.0 = the book fully replaced).
    ``cost``     ``cost_bps / 10000 * trade``, charged in the month it happens.
    ``net``      ``gross - cost``.

    The implied weight of ticker j in month t follows from the sleeve mean:
    each active sleeve carries 1/S_t of capital and equal-weights the picks that
    actually have a bar that month, so
    ``w_t(j) = (1 / S_t) * SUM over sleeves of [j in members] / len(members)``.
    ``members`` is the post-dropna set, not the raw picks — those are the names
    ``.mean()`` averages over, and pairing raw picks with a dropna-reduced
    denominator makes the weights sum above 1 whenever a pick lacks a bar, which
    bills notional that was never held. S_t is the number of sleeves that
    produced a return, not ``hold_period``, so the weights sum to 1 during the
    ramp too.

    ``trade`` compares against the previous *appended* month's weights grown by
    that month's returns: the book held entering month t is last month's book
    after price drift, and the sleeve mean re-equal-weights it every month,
    which is a real trade. A month where every sleeve is empty appends nothing,
    so the comparison is against the last month that produced a return rather
    than against ``t-1``. Summing absolute weight changes charges both the sell
    and the buy side at ``cost_bps``, and bills the opening funding buy
    (``trade == 1.0``) one side only.

    ``signal`` lets a caller that already holds :func:`momentum_signal`'s output
    pass it in instead of paying for it again; it is read, never mutated.
    ``signal=None`` reproduces the internal call exactly.
    """
    if signal is None:
        signal = momentum_signal(monthly_returns, rank_lookback, skip_months)

    picks_cache: dict[int, pd.Index | None] = {}

    def picks_at(i: int) -> pd.Index | None:
        if i not in picks_cache:
            ranking = signal.iloc[i].dropna()
            picks_cache[i] = (ranking.nlargest(top_n).index
                              if len(ranking) >= top_n else None)
        return picks_cache[i]

    rate = cost_bps / 10000.0
    rows: list[list[float]] = []
    dates = []
    drifted: pd.Series | None = None      # last month's weights, grown by returns
    for t in range(rank_lookback + skip_months, len(monthly_returns)):
        month = monthly_returns.iloc[t]
        sleeve_returns = []
        members: list[pd.Index] = []
        for k in range(hold_period):
            select_t = t - 1 - k
            if select_t < rank_lookback - 1 + skip_months:
                continue
            picks = picks_at(select_t)
            if picks is None:
                continue
            r = month[picks].dropna()
            if len(r) > 0:
                sleeve_returns.append(r.mean())
                members.append(r.index)
        if not sleeve_returns:
            continue

        gross = float(np.mean(sleeve_returns))
        n_sleeves = len(sleeve_returns)
        weights: dict = {}
        for idx in members:
            share = 1.0 / (n_sleeves * len(idx))
            for j in idx:
                weights[j] = weights.get(j, 0.0) + share
        w = pd.Series(weights, dtype=float)

        if drifted is None:
            trade = float(w.abs().sum())          # funding buy, one side
        else:
            trade = float(w.subtract(drifted, fill_value=0.0).abs().sum())
        cost = rate * trade
        rows.append([gross, trade, trade / 2.0, cost, gross - cost])
        dates.append(monthly_returns.index[t])

        grown = w * (1.0 + month[w.index])
        total = float(grown.sum())
        # A total of 0 needs every holding to lose exactly 100% in one month.
        # Fall back to the undrifted book rather than emit NaN weights.
        drifted = (grown / total) if total > 0 else w

    return pd.DataFrame(rows, index=pd.DatetimeIndex(dates),
                        columns=_DETAIL_COLUMNS, dtype=float)


def momentum_backtest(
    monthly_returns: pd.DataFrame,
    top_n: int,
    hold_period: int,
    rank_lookback: int,
    skip_months: int = 0,
) -> pd.Series:
    """Gross monthly return series of the overlapping-sleeve momentum strategy.

    Thin wrapper over :func:`momentum_backtest_detail` — the cost columns
    default to zero, so this returns exactly what it always has.
    """
    detail = momentum_backtest_detail(
        monthly_returns, top_n=top_n, hold_period=hold_period,
        rank_lookback=rank_lookback, skip_months=skip_months,
    )
    return detail["gross"].rename(f"momentum_top{top_n}_hold{hold_period}")


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


def signal_window_days(rank_lookback: int, skip_months: int = 0) -> int:
    """Calendar days of history :func:`current_target` reads, end to end.

    ``top_performers_period`` takes the first bar at-or-after ``end - days`` with
    no reach-back tolerance, so a frame shorter than this silently ranks on a
    collapsed window instead of refusing. Callers that must not record a target
    off a short window check the frame's span against this first.
    """
    return round(rank_lookback * _MONTH_DAYS) + round(skip_months * _MONTH_DAYS)


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
    turnover: pd.Series | None = None,
    cost: pd.Series | None = None,
) -> dict:
    """Standard performance stats for a return series.

    Sharpe follows the textbook definition: annualized *arithmetic* mean excess
    return over annualized volatility, with an explicit annual risk-free rate
    (default 0). This matches `rolling_sharpe`; earlier versions divided CAGR by
    vol, which mixed a geometric numerator with an arithmetic denominator and
    silently assumed a zero risk-free rate.

    Passing the ``turnover`` and ``cost`` columns of
    :func:`momentum_backtest_detail` adds ``turnover_ann`` and ``cost_ann``.
    Both drop the first observation, which is the initial funding buy rather than
    a rebalance: including it would report the one-off cost of opening the book
    as a recurring rate. Residual bias remains for long holds, where the first
    ``hold_period`` months are still ramping sleeves up (roughly 6% of the
    reported rate at h=6, 20% at h=12).

    Keys are spread directly into the API's PERF rows and the CLI table, so a new
    key here appears in both surfaces.
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
    out = {
        "total_return": cum,
        "CAGR": cagr,
        "vol": vol,
        "Sharpe": sharpe,
        "max_drawdown": drawdown,
        "months": len(r),
    }
    if turnover is not None:
        rebalances = turnover.dropna().iloc[1:]
        out["turnover_ann"] = (float(rebalances.mean() * periods_per_year)
                               if len(rebalances) else None)
    if cost is not None:
        charged = cost.dropna().iloc[1:]
        out["cost_ann"] = (float(charged.mean() * periods_per_year)
                           if len(charged) else None)
    return out


# ---------- out-of-sample ledger: marking recorded targets to market ----------

# Annualized stats stay suppressed below both bars. Annualizing 2 segments would
# manufacture exactly the false confidence the ledger exists to remove.
LEDGER_ANNUALIZE_MIN_DAYS = 365
LEDGER_ANNUALIZE_MIN_SEGMENTS = 12


def _jsonf(v) -> float | None:
    """float, or None for anything not finite.

    Starlette renders with ``allow_nan=False``, so a NaN or inf anywhere in a
    response is an HTTP 500. None is the only honest substitute: 0.0 in an equity
    curve is a wipeout that never happened.
    """
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _bar_at_or_before(index: pd.DatetimeIndex, ts: pd.Timestamp):
    prior = index[index <= ts]
    return prior[-1] if len(prior) else None


def _recorded_price(entry: dict, ticker: str) -> float | None:
    v = _jsonf((entry.get("entry_prices") or {}).get(ticker))
    return v if v is not None and v > 0 else None


LEDGER_BACKFILL_MAX_MONTHS = 12


def ledger_missed_months(newest_as_of: str, upto: str) -> list[str]:
    """``YYYY-MM`` labels of the calendar months strictly between two dates.

    A record in consecutive months gives ``[]``, so a normal monthly cadence
    never reports a gap.
    """
    first = pd.Period(pd.Timestamp(newest_as_of), "M") + 1
    last = pd.Period(pd.Timestamp(upto), "M") - 1
    if last < first:
        return []
    return [str(p) for p in pd.period_range(first, last, freq="M")]


def ledger_gap(newest_as_of: str, today: str) -> dict:
    """Where a record group stands against the calendar.

    ``current``: the newest entry is in today's month. ``due``: it is in the
    month before. ``missed``: at least one whole month has no entry.
    """
    missed = ledger_missed_months(newest_as_of, today)
    same_month = (pd.Period(pd.Timestamp(newest_as_of), "M")
                  >= pd.Period(pd.Timestamp(today), "M"))
    state = "current" if same_month else ("missed" if missed else "due")
    return {"newest_as_of": newest_as_of, "state": state, "missed_months": missed}


def reconstruct_targets(
    prices: pd.DataFrame,
    months: list[str],
    top_n: int,
    rank_lookback: int,
    skip_months: int = 0,
) -> list[dict]:
    """The target :func:`current_target` gives at each past month-end.

    Each month is ranked on the frame cut at that month's last bar, so no later
    bar can reach the ranking and no ranking code is added. A month is refused,
    never guessed, when the frame cannot prove the month-end or cannot cover the
    ranking window. Returns one ``{month, as_of, target, refused_reason}`` per
    label, in the order given.
    """
    idx = prices.index
    need_days = signal_window_days(rank_lookback, skip_months)
    out: list[dict] = []
    for label in months:
        period = pd.Period(label, "M")
        item = {"month": label, "as_of": None, "target": None, "refused_reason": None}
        out.append(item)
        in_month = idx[(idx >= period.start_time) & (idx <= period.end_time)]
        if not len(in_month):
            item["refused_reason"] = "no price bars in that month"
            continue
        bar = in_month[-1]
        item["as_of"] = bar.strftime("%Y-%m-%d")
        # Without a later bar the frame may simply stop mid-month, and its last
        # bar would be passed off as the month-end.
        if not (idx > period.end_time).any():
            item["refused_reason"] = "no price bar after that month to confirm its last bar"
            continue
        span = int((bar - idx.min()).days)
        if span < need_days:
            item["refused_reason"] = (
                f"prices cover {span} days before that month-end, the signal needs {need_days}")
            continue
        target = current_target(prices.loc[:bar], top_n=top_n,
                                rank_lookback=rank_lookback, skip_months=skip_months)
        if target is None:
            item["refused_reason"] = "not enough price history to form a target"
            continue
        if len(target["tickers"]) < top_n:
            item["refused_reason"] = (
                f"only {len(target['tickers'])} of {top_n} names have prices in the window")
            continue
        item["target"] = target
    return out


def _empty_ledger_marks(reason: str) -> dict:
    return {
        "rows": [],
        "equity": {"dates": [], "strategy": [], "benchmark": [],
                   "reconstructed_ranges": []},
        "summary": {
            "first_as_of": None, "priced_through": None,
            "n_segments": 0, "n_completed": 0, "days_live": 0,
            "n_reconstructed": 0, "live_only": None,
            "total_return": None, "benchmark_total_return": None, "excess": None,
            "equity_starts_at": None, "equity_segments": 0,
            "equity_covers_all_segments": False,
            "annualized": None, "annualized_blocked_reason": reason,
        },
    }


def _mark_segment(entry: dict, s_ts, end_ts, frame: pd.DataFrame,
                  idx: pd.DatetimeIndex, bench: pd.Series | None,
                  is_open: bool) -> dict:
    """One recorded cohort marked from bar ``s_ts`` to bar ``end_ts``."""
    tickers = list(entry.get("tickers") or [])
    # With no cached bar at or before as_of, the segment still has to start
    # somewhere: the earliest bar the cache does hold, paired with the stored
    # entry price as the basis.
    window_start = s_ts if s_ts is not None else (idx[0] if len(idx) else None)

    pick_returns: dict[str, float | None] = {}
    priced: list[float] = []
    frozen: list[str] = []
    frozen_at: dict[str, str] = {}
    unpriced: list[str] = []
    unverified: list[str] = []
    basis_ratio: dict[str, float] = {}
    basis_shifted = False

    for t in tickers:
        col = frame[t] if t in frame.columns else None

        start_val: float | None = None
        from_cache = False
        if col is not None and s_ts is not None and s_ts in col.index:
            v = _jsonf(col.loc[s_ts])
            if v is not None and v > 0:
                start_val, from_cache = v, True
        if start_val is None:
            start_val = _recorded_price(entry, t)

        # Audit the stored price against the current cached basis. The return
        # itself always uses one consistent basis; this only labels the row.
        if from_cache:
            recorded = _recorded_price(entry, t)
            if recorded is not None:
                ratio = start_val / recorded
                if ratio > SPLIT_JUMP_HI or ratio < SPLIT_JUMP_LO:
                    basis_shifted = True
                    basis_ratio[t] = round(ratio, 6)

        end_val: float | None = None
        at_ts = None
        if col is not None and end_ts is not None and window_start is not None:
            seg = col.loc[window_start:end_ts].dropna()
            if len(seg):
                end_val, at_ts = _jsonf(seg.iloc[-1]), seg.index[-1]

        r = None
        if start_val is not None and end_val is not None and start_val > 0:
            r = _jsonf(end_val / start_val - 1)
        if r is None:
            pick_returns[t] = None
            unpriced.append(t)
            continue

        if not from_cache:
            unverified.append(t)
        if at_ts is not None and end_ts is not None and at_ts < end_ts:
            frozen.append(t)
            frozen_at[t] = at_ts.strftime("%Y-%m-%d")
        pick_returns[t] = r
        priced.append(r)

    # Equal dollars at s_k held without intra-segment rebalancing: the plain mean
    # of the legs' ratios. Frozen legs stay in the denominator (dropping them
    # would reweight the survivors up, which is the survivorship bias the ledger
    # exists to escape); legs with no mark at all are excluded rather than
    # counted as 0.
    return_pct = _jsonf(np.mean(priced)) if priced else None

    # Both benchmark endpoints resolve to the last valid bar at or before the
    # target date, the same rule the constituent legs above and the record
    # endpoint already use. Reading the exact bar instead would drop the
    # comparison entirely whenever the benchmark column lags the constituents,
    # which is the normal cache state (one late column among hundreds), and it
    # is the open segment — the one the user watches — that always ends on the
    # frame's last bar. The bars actually used are reported so a short benchmark
    # window is disclosed rather than passed off as aligned.
    bench_return = None
    bench_from_date = None
    bench_to_date = None
    if bench is not None and s_ts is not None and end_ts is not None:
        b_start = bench.loc[:s_ts].dropna()
        b_end = bench.loc[:end_ts].dropna()
        if len(b_start) and len(b_end):
            b0, b1 = _jsonf(b_start.iloc[-1]), _jsonf(b_end.iloc[-1])
            if b0 is not None and b1 is not None and b0 > 0:
                bench_return = _jsonf(b1 / b0 - 1)
                bench_from_date = b_start.index[-1].strftime("%Y-%m-%d")
                bench_to_date = b_end.index[-1].strftime("%Y-%m-%d")

    excess = (None if return_pct is None or bench_return is None
              else _jsonf(return_pct - bench_return))

    days_held = None
    if end_ts is not None:
        from_ts = s_ts if s_ts is not None else pd.Timestamp(entry["as_of"])
        days_held = int((end_ts - from_ts).days)

    return {
        "as_of": entry["as_of"],
        "end_date": end_ts.strftime("%Y-%m-%d") if end_ts is not None else None,
        "start_date": s_ts.strftime("%Y-%m-%d") if s_ts is not None else None,
        "is_open": is_open,
        "benchmark": entry.get("benchmark"),
        "tickers": tickers,
        "pick_returns": pick_returns,
        "return_pct": return_pct,
        "bench_return_pct": bench_return,
        # The bars the benchmark legs came from. They differ from start_date /
        # end_date when the benchmark column lags, which the UI discloses.
        "bench_from_date": bench_from_date,
        "bench_to_date": bench_to_date,
        # Difference of the two returns in the same fractional units; the UI
        # renders it as percentage points.
        "excess_pp": excess,
        "n_priced": len(priced),
        "n_frozen": len(frozen),
        "frozen_tickers": frozen,
        "frozen_at": frozen_at,
        "unpriced_tickers": unpriced,
        "basis_shifted": basis_shifted,
        "basis_ratio": basis_ratio,
        "basis_unverified": bool(unverified),
        "basis_unverified_tickers": unverified,
        "days_held": days_held,
        "note": entry.get("note"),
        "reconstructed": bool(entry.get("reconstructed")),
        "universe_as_of": entry.get("universe_as_of"),
    }


def _ledger_equity(ordered: list[dict], starts: list, ends: list,
                   frame: pd.DataFrame) -> tuple[list, list[float], int]:
    """Daily unit-equity curve chained across segments.

    Built leg by leg with a forward fill inside each segment: a leg with an
    interior gap (halt, bad Yahoo day) is carried at its last observed close, so
    the array has no NaN and no hole. A chart cannot plot a hole, and NaN would
    500 the response.

    Only segments with a real cached start bar can be drawn, so the curve may
    start later than the first entry and stop at the first hole. The row chain,
    not this curve, is the authoritative total return.
    """
    dates: list = []
    vals: list[float] = []
    level = 1.0
    covered = 0

    for k, entry in enumerate(ordered):
        s_ts, end_ts = starts[k], ends[k]
        if s_ts is None or end_ts is None:
            if dates:
                break            # a hole after the curve started: stop there
            continue             # entries older than the cache: nothing to draw
        legs: dict[str, float] = {}
        for t in (entry.get("tickers") or []):
            if t in frame.columns and s_ts in frame.index:
                v = _jsonf(frame[t].loc[s_ts])
                if v is not None and v > 0:
                    legs[t] = v
        if not legs:
            if dates:
                break
            continue

        block = frame.loc[s_ts:end_ts, list(legs.keys())].ffill()
        curve = level * (block / pd.Series(legs)).mean(axis=1)
        if not dates:
            dates.append(block.index[0])
            vals.append(level)
        for ts, v in zip(block.index[1:], curve.iloc[1:]):
            f = _jsonf(v)
            if f is None:
                break
            dates.append(ts)
            vals.append(f)
        level = vals[-1]
        covered += 1

    return dates, vals, covered


def ledger_marks(
    entries: list[dict],
    prices: pd.DataFrame,
    benchmark: str | None = None,
    risk_free_annual: float = 0.0,
) -> dict:
    """Mark one params_hash group of recorded targets to market.

    ``entries`` must all share a ``params_hash`` (the caller groups; mixing two
    hashes would chain two different strategies into one curve). They are sorted
    by ``as_of`` here, so a hand-edited out-of-order file still marks correctly.
    ``prices`` is a daily adjusted-close frame including the benchmark column.

    Segment ``k`` runs from the last cached bar at or before ``as_of_k`` to the
    next entry's start bar, or to the last cached bar for the open segment.
    Chaining at a shared close is the same zero-cost turnover assumption the
    backtest makes.

    Both endpoints come from the current cached series wherever possible, never a
    mix of stored entry price and cached end price: ``auto_adjust`` rebases the
    whole adjusted-close history on each fetch, so a split between recording and
    marking would otherwise print a spurious -50%. Where the cache no longer
    reaches ``as_of_k`` the stored price is the only basis available, and the row
    says so via ``basis_unverified``.
    """
    if not entries:
        return _empty_ledger_marks("no entries recorded yet")

    hashes = {e.get("params_hash") for e in entries}
    if len(hashes) > 1:
        raise ValueError(
            "ledger_marks expects entries from one params_hash, got "
            f"{sorted(str(h) for h in hashes)}"
        )

    ordered = sorted(entries, key=lambda e: e.get("as_of") or "")
    m = len(ordered)

    frame = prices if prices is not None else pd.DataFrame()
    if len(frame.index):
        frame = frame.sort_index()
    idx = frame.index if len(frame.index) else pd.DatetimeIndex([])
    last_bar = idx[-1] if len(idx) else None
    bench = frame[benchmark] if benchmark and benchmark in frame.columns else None

    starts = [_bar_at_or_before(idx, pd.Timestamp(e["as_of"])) for e in ordered]
    ends = [(starts[k + 1] if k + 1 < m else last_bar) for k in range(m)]

    rows = [_mark_segment(ordered[k], starts[k], ends[k], frame, idx, bench,
                          is_open=(k == m - 1))
            for k in range(m)]

    dates, vals, covered = _ledger_equity(ordered, starts, ends, frame)

    bench_vals: list[float] = []
    if bench is not None and dates:
        b = bench.reindex(pd.DatetimeIndex(dates)).ffill()
        b0 = _jsonf(b.iloc[0])
        if b0 is not None and b0 > 0:
            scaled = [_jsonf(v / b0) for v in b]
            if all(v is not None for v in scaled):
                bench_vals = scaled

    def chain(key: str, over: list[dict]) -> float | None:
        legs = [r[key] for r in over if r[key] is not None]
        if not legs:
            return None
        acc = 1.0
        for r in legs:
            acc *= (1.0 + r)
        return _jsonf(acc - 1.0)

    # The two headline totals must compound over the SAME segments, or their
    # difference is not an excess return: it would subtract a benchmark measured
    # over a shorter period from a strategy measured over a longer one. Segments
    # missing either leg are excluded from both, and the counts are published so
    # a spliced chain can never be presented as a full-period total.
    total_all = chain("return_pct", rows)
    comparable = [r for r in rows
                  if r["return_pct"] is not None and r["bench_return_pct"] is not None]
    total = chain("return_pct", comparable)
    bench_total = chain("bench_return_pct", comparable)
    n_priced_segments = sum(1 for r in rows if r["return_pct"] is not None)
    n_completed = sum(1 for r in rows if not r["is_open"] and r["return_pct"] is not None)
    first_as_of = ordered[0]["as_of"]
    days_live = 0
    if last_bar is not None:
        days_live = max(0, int((last_bar - pd.Timestamp(first_as_of)).days))

    annualized = None
    blocked: str | None = None
    if days_live < LEDGER_ANNUALIZE_MIN_DAYS or n_completed < LEDGER_ANNUALIZE_MIN_SEGMENTS:
        blocked = (
            f"{n_completed} completed segment(s) over {days_live} days: "
            f"needs {LEDGER_ANNUALIZE_MIN_SEGMENTS} completed segments and "
            f"{LEDGER_ANNUALIZE_MIN_DAYS} days to annualize"
        )
    elif len(vals) < 3:
        blocked = "equity curve covers too few bars to annualize"
    else:
        daily = pd.Series(vals, index=pd.DatetimeIndex(dates)).pct_change().dropna()
        stats = perf_stats(daily, periods_per_year=252, risk_free_annual=risk_free_annual)
        # Take 3 keys by hand: perf_stats also returns "months", which at
        # periods_per_year=252 holds a trading-day count and would publish
        # "months: 251".
        annualized = {
            "CAGR":   _jsonf(stats["CAGR"]),
            "vol":    _jsonf(stats["vol"]),
            "Sharpe": _jsonf(stats["Sharpe"]),
        }

    # A reconstructed month was recomputed after the fact, so the total that
    # includes it is published beside the total over live entries alone (where
    # the last live cohort is held through the gap). Showing both removes the
    # choice of whichever record looks better.
    n_reconstructed = sum(1 for r in rows if r["reconstructed"])
    live_only = None
    if n_reconstructed:
        live = [e for e in ordered if not e.get("reconstructed")]
        if live:
            ls = ledger_marks(live, prices, benchmark=benchmark,
                              risk_free_annual=risk_free_annual)["summary"]
            live_only = {
                "total_return": ls["total_return"],
                "benchmark_total_return": ls["benchmark_total_return"],
                "excess": ls["excess"],
                "n_segments": ls["n_segments"],
            }

    recon_ranges: list[list[str]] = []
    if dates:
        for k, e in enumerate(ordered):
            # Only the part of a reconstructed segment the curve actually drew.
            if (not e.get("reconstructed") or starts[k] is None or ends[k] is None
                    or starts[k] < dates[0] or starts[k] >= dates[-1]):
                continue
            pair = [starts[k].strftime("%Y-%m-%d"),
                    min(ends[k], dates[-1]).strftime("%Y-%m-%d")]
            if recon_ranges and recon_ranges[-1][1] == pair[0]:
                recon_ranges[-1][1] = pair[1]
            else:
                recon_ranges.append(pair)

    return {
        "rows": rows[::-1],                       # newest first for the UI
        "equity": {
            "dates": [d.strftime("%Y-%m-%d") for d in dates],
            "strategy": vals,
            "benchmark": bench_vals,
            "reconstructed_ranges": recon_ranges,
        },
        "summary": {
            "first_as_of": first_as_of,
            "priced_through": (last_bar.strftime("%Y-%m-%d")
                               if last_bar is not None else None),
            "n_segments": m,
            "n_completed": n_completed,
            "days_live": days_live,
            "n_reconstructed": n_reconstructed,
            "live_only": live_only,
            # total_return and benchmark_total_return chain the same segments, so
            # excess is a real difference. total_return_all_segments compounds
            # every priced segment including those the benchmark could not mark,
            # and equals total_return whenever the comparison is complete.
            "total_return": total,
            "benchmark_total_return": bench_total,
            "total_return_all_segments": total_all,
            "n_comparable_segments": len(comparable),
            "n_priced_segments": n_priced_segments,
            "comparison_covers_all_segments": len(comparable) == n_priced_segments,
            "excess": (None if total is None or bench_total is None
                       else _jsonf(total - bench_total)),
            "equity_starts_at": (dates[0].strftime("%Y-%m-%d") if dates else None),
            "equity_segments": covered,
            "equity_covers_all_segments": covered == m,
            "annualized": annualized,
            "annualized_blocked_reason": blocked,
        },
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


# ---------- market regime: 200dma state, filtering, and conditional evidence ----------

REGIME_MA_DAYS = 200


def regime_state(benchmark_prices: pd.Series,
                 ma_days: int = REGIME_MA_DAYS) -> pd.DataFrame:
    """Daily frame of the benchmark's trend state.

    Columns: ``close``, ``ma`` (trailing ``ma_days``-bar mean), ``dist``
    (close / ma - 1) and ``above`` (close >= ma). ``ma_days`` counts BARS, not
    calendar days: the series is daily closes with weekends and holidays absent,
    so a 200-bar mean is the conventional 200-day average.

    ``above`` is pinned to the nullable ``"boolean"`` dtype and is NA until the
    average has a full window. The dtype matters: ``(s >= ma).where(ma.notna())``
    alone yields ``object`` holding NaN, and object and nullable-boolean masks
    behave *oppositely* under the natural ``.where(decision != False, ...)``
    idiom, so leaving it implicit would let a later refactor silently flip what
    the warm-up months do. :func:`apply_regime` is written to be safe either way,
    but the dtype is fixed here as well.
    """
    s = benchmark_prices.dropna()
    ma = s.rolling(ma_days).mean()
    above = (s >= ma).astype("boolean").where(ma.notna())
    return pd.DataFrame({"close": s, "ma": ma, "dist": s / ma - 1, "above": above})


def regime_mask(benchmark_prices: pd.Series, monthly_index,
                ma_days: int = REGIME_MA_DAYS) -> pd.Series:
    """Risk-on flag AT each month-end label, using only bars at or before it.

    The value at label t is whether the last benchmark bar at or before t closed
    at or above its moving average. pd.NA while the average has no full window.

    This is a decision made ON t, so applying it to month t's own return would be
    lookahead; every consumer shifts it by one month. ``method="ffill"`` picks the
    last bar at or before each label, which also makes the in-progress month
    correct: its label is a future month-end, so the mask uses the latest bar that
    actually exists.
    """
    st = regime_state(benchmark_prices, ma_days)
    return st["above"].reindex(monthly_index, method="ffill")


def apply_regime(returns: pd.Series, mask: pd.Series,
                 replacement: float | pd.Series = 0.0) -> pd.Series:
    """Replace the months the regime said to sit out.

    Month t's exposure is decided by the state at month t-1's close, so the mask
    is shifted one month. A month is replaced only where the prior month-end was
    *explicitly* risk-off: NA (the moving-average warm-up) leaves the sleeve
    invested rather than silently zeroing it.

    Two details here are load-bearing. First, ``decision.eq(False).fillna(False)``
    builds the "sit out" mask positively, because ``~decision`` propagates NA and
    ``.where(decision != False, ...)`` disagrees between object and
    nullable-boolean dtypes. Second, the shift happens on the MONTHLY index and
    only then is reindexed onto ``returns.index``, which is a strict subset of it
    (the backtest starts after the ranking warm-up and appends a label only when
    a sleeve produced a return). Reindexing first would shift inside the subset,
    which both leaves the first strategy month unfiltered when its predecessor is
    perfectly well known, and makes any skipped month reach two labels back. The
    orderings agree on a gapless index, which is why the wrong one ships easily.
    """
    decision = mask.shift(1).reindex(returns.index)
    off = decision.eq(False).fillna(False).astype(bool)
    repl = (replacement.reindex(returns.index) if isinstance(replacement, pd.Series)
            else pd.Series(replacement, index=returns.index))
    return returns.mask(off, repl)


def _bucket_stats(vals: pd.Series, bench: pd.Series) -> dict:
    """Mean/median/t-stat for one regime bucket, with no NaN escaping.

    An empty or single-observation bucket reports ``n`` with None statistics
    rather than being omitted, so the UI can say "no comparable months" instead
    of tripping over a missing key.
    """
    n = int(len(vals))
    spread = (vals - bench).dropna()
    return {
        "n": n,
        "mean": _jsonf(vals.mean()) if n else None,
        "median": _jsonf(vals.median()) if n else None,
        "bench_mean": _jsonf(bench.mean()) if len(bench.dropna()) else None,
        "spread_mean": _jsonf(spread.mean()) if len(spread) else None,
        "t_stat": _jsonf(_newey_west_tstat(vals.values)) if n else None,
        "t_stat_spread": _jsonf(_newey_west_tstat(spread.values)) if len(spread) else None,
    }


def regime_conditional_stats(strategy_returns: pd.Series, mask: pd.Series,
                             benchmark_monthly: pd.Series) -> dict:
    """What happened NEXT, bucketed by the regime state observable beforehand.

    Returns ``{"risk_on"|"risk_off"|"warmup": {...}}``. The buckets come from the
    same ``mask.shift(1)`` decision series :func:`apply_regime` uses, so the
    banner's evidence and the filtered curve can never disagree about which
    months were risk-off. ``spread`` is strategy minus the same-month benchmark,
    matching the event study's time-aligned convention, and the t-stats are
    Newey-West because overlapping sleeves are autocorrelated.
    """
    r = strategy_returns.dropna()
    decision = mask.shift(1).reindex(r.index)
    bench = benchmark_monthly.reindex(r.index)
    is_on = decision.eq(True).fillna(False).astype(bool)
    is_off = decision.eq(False).fillna(False).astype(bool)
    warm = ~(is_on | is_off)
    return {
        "risk_on": _bucket_stats(r[is_on], bench[is_on]),
        "risk_off": _bucket_stats(r[is_off], bench[is_off]),
        "warmup": _bucket_stats(r[warm], bench[warm]),
    }


def regime_snapshot(benchmark_prices: pd.Series, ma_days: int = REGIME_MA_DAYS,
                    dd_window_days: int = 365) -> dict:
    """Live regime state for the banner.

    ``as_of`` and ``close`` are the last *valid* benchmark bar and are always
    reported, because the banner needs a date even with no state to show. The
    benchmark column routinely lags the constituents, so this date can trail the
    trade list's own as_of; both are surfaced rather than reconciled.

    Everything else is None (never 0.0) while the moving average has no full
    window: a missing 200-day average is not a flat one.
    """
    s = benchmark_prices.dropna()
    if s.empty:
        return {"as_of": None, "close": None, "ma": None, "dist": None,
                "above": None, "days_in_state": None, "state_since": None,
                "drawdown": None, "ma_days": int(ma_days)}
    st = regime_state(s, ma_days)
    last = st.index[-1]
    above = st["above"].iloc[-1]
    above_out = None if pd.isna(above) else bool(above)

    days_in_state = None
    state_since = None
    if above_out is not None:
        run = st["above"].dropna()
        same = run == run.iloc[-1]
        # Walk back while the state is unchanged; the run starts at the first bar
        # of the current above/below stretch.
        start = run.index[-1]
        for ts in reversed(run.index):
            if not bool(same.loc[ts]):
                break
            start = ts
        state_since = start.strftime("%Y-%m-%d")
        days_in_state = int((last - start).days)

    window = s.loc[s.index >= last - pd.Timedelta(days=dd_window_days)]
    peak = window.max() if len(window) else None
    drawdown = (_jsonf(float(s.iloc[-1]) / float(peak) - 1)
                if peak is not None and peak > 0 else None)

    return {
        "as_of": last.strftime("%Y-%m-%d"),
        "close": _jsonf(s.iloc[-1]),
        "ma": _jsonf(st["ma"].iloc[-1]),
        "dist": _jsonf(st["dist"].iloc[-1]),
        "above": above_out,
        "days_in_state": days_in_state,
        "state_since": state_since,
        "drawdown": drawdown,
        "ma_days": int(ma_days),
    }


def realized_vol_percentile(benchmark_prices: pd.Series, window: int = 21) -> dict:
    """Trailing realized vol against the benchmark's own cached history.

    The airgap-safe substitute for a VIX strip: it needs no extra ticker. All
    fields are None on a series shorter than ``window + 1`` bars.
    """
    s = benchmark_prices.dropna()
    daily = s.pct_change(fill_method=None).dropna()
    if len(daily) <= window:
        return {"vol_ann": None, "percentile": None, "n": int(len(daily)),
                "median": None, "p90": None, "window": int(window)}
    hist = daily.rolling(window).std().dropna() * np.sqrt(252)
    current = hist.iloc[-1]
    return {
        "vol_ann": _jsonf(current),
        "percentile": _jsonf((hist <= current).mean()),
        "n": int(len(hist)),
        "median": _jsonf(hist.median()),
        "p90": _jsonf(hist.quantile(0.90)),
        "window": int(window),
    }
