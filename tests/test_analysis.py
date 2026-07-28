"""Unit tests for the analysis methodology fixes and new helpers."""

import numpy as np
import pandas as pd

from lib import analysis
from lib import data as datalib


def _months(n, start="2000-01-31"):
    return pd.date_range(start, periods=n, freq="ME")


# ---- Sharpe: standardized (arithmetic mean excess / vol) ----

def test_perf_stats_sharpe_zero_when_flat():
    r = pd.Series([0.01] * 24, index=_months(24))   # zero vol
    assert analysis.perf_stats(r)["Sharpe"] == 0


def test_perf_stats_sharpe_is_arithmetic_mean_over_vol():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.01, 0.04, 60), index=_months(60))
    s = analysis.perf_stats(r, risk_free_annual=0.0)
    expected = (r.mean() * 12) / (r.std() * np.sqrt(12))
    assert abs(s["Sharpe"] - expected) < 1e-12


def test_risk_free_rate_reduces_sharpe():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.02, 0.05, 60), index=_months(60))
    assert (analysis.perf_stats(r, risk_free_annual=0.04)["Sharpe"]
            < analysis.perf_stats(r, risk_free_annual=0.0)["Sharpe"])


def test_perf_and_rolling_sharpe_use_same_formula():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0.01, 0.03, 36), index=_months(36))
    perf = analysis.perf_stats(r)["Sharpe"]
    rolling_last = analysis.rolling_sharpe(r, window=36).iloc[-1]
    assert abs(perf - rolling_last) < 1e-12


# ---- Newey-West t-stat ----

def test_newey_west_equals_simple_tstat_at_lag_zero():
    rng = np.random.default_rng(3)
    x = rng.normal(0.5, 1.0, 200)
    t_simple = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert abs(analysis._newey_west_tstat(x, lag=0) - t_simple) < 1e-12


def test_newey_west_shrinks_tstat_under_autocorrelation():
    rng = np.random.default_rng(4)
    n = 400
    e = rng.normal(0, 1, n)
    x = np.zeros(n)
    x[0] = e[0]
    for i in range(1, n):
        x[i] = 0.8 * x[i - 1] + e[i]      # strong positive autocorrelation
    x += 0.5                               # nonzero mean
    t_simple = x.mean() / (np.std(x, ddof=0) / np.sqrt(n))
    t_nw = analysis._newey_west_tstat(x)   # automatic lag
    assert abs(t_nw) < abs(t_simple)


def test_newey_west_handles_degenerate_input():
    assert np.isnan(analysis._newey_west_tstat(np.array([1.0])))


# ---- event_study_summary structure + Newey-West column ----

def test_event_study_summary_structure():
    rng = np.random.default_rng(5)
    rows = []
    for cohort in range(30):
        for h in (1, 2, 3):
            rows.append({"cohort": cohort, "h": h,
                         "mean_return": rng.normal(0.02, 0.05),
                         "median_return": 0.0})
    events = pd.DataFrame(rows)
    bench = pd.Series(rng.normal(0.008, 0.04, 60))
    summ = analysis.event_study_summary(events, bench)
    assert list(summ.index) == [1, 2, 3]
    for col in ["avg_return", "median_return", "win_rate", "std", "n_cohorts",
                "benchmark_avg", "alpha_vs_bench", "t_stat"]:
        assert col in summ.columns
    assert (summ["n_cohorts"] == 30).all()
    # alpha == avg_return - benchmark mean
    assert abs(summ.loc[1, "alpha_vs_bench"]
               - (summ.loc[1, "avg_return"] - bench.mean())) < 1e-12


# ---- time-aligned event-study alpha ----

def test_event_study_summary_aligned_alpha_and_win_rate():
    rng = np.random.default_rng(6)
    rows = []
    for cohort in range(40):
        for h in (1, 2):
            rows.append({"cohort": cohort, "h": h,
                         "mean_return": rng.normal(0.02, 0.05),
                         "median_return": 0.0,
                         "bench_return": rng.normal(0.01, 0.04)})
    events = pd.DataFrame(rows)
    bench = pd.Series(rng.normal(0.008, 0.04, 60))
    summ = analysis.event_study_summary(events, bench)

    grp = events[events["h"] == 1]
    spread = grp["mean_return"] - grp["bench_return"]
    assert abs(summ.loc[1, "alpha_vs_bench"] - spread.mean()) < 1e-12
    assert abs(summ.loc[1, "win_rate"] - (spread > 0).mean()) < 1e-12
    assert abs(summ.loc[1, "t_stat"]
               - analysis._newey_west_tstat(spread.values)) < 1e-12
    # benchmark_avg stays the full-sample mean (chart reference line)
    assert abs(summ.loc[1, "benchmark_avg"] - bench.mean()) < 1e-12


def test_event_study_summary_aligned_drops_missing_benchmark_months():
    rows = [
        {"cohort": 0, "h": 1, "mean_return": 0.05, "median_return": 0.0, "bench_return": 0.01},
        {"cohort": 1, "h": 1, "mean_return": 0.03, "median_return": 0.0, "bench_return": np.nan},
        {"cohort": 2, "h": 1, "mean_return": -0.02, "median_return": 0.0, "bench_return": 0.01},
    ]
    summ = analysis.event_study_summary(pd.DataFrame(rows), pd.Series([0.01] * 12))
    # NaN benchmark row excluded from the spread stats but not from n_cohorts.
    assert abs(summ.loc[1, "alpha_vs_bench"] - np.mean([0.04, -0.03])) < 1e-12
    assert abs(summ.loc[1, "win_rate"] - 0.5) < 1e-12
    assert summ.loc[1, "n_cohorts"] == 3


def test_event_study_stamps_time_aligned_bench_return():
    n = 10
    idx = _months(n)
    # 4 tickers, deterministic returns; benchmark = distinct known series
    r = pd.DataFrame({f"T{i}": np.linspace(0.01 * (i + 1), 0.02 * (i + 1), n)
                      for i in range(4)}, index=idx)
    bench = pd.Series(np.arange(n) / 100.0, index=idx)
    events = analysis.event_study(r, top_n=2, rank_lookback=1, horizon=2,
                                  benchmark_monthly=bench)
    for _, row in events.iterrows():
        i = idx.get_loc(row["cohort"])
        assert abs(row["bench_return"] - bench.iloc[i + row["h"]]) < 1e-12


# ---- calendar_year_returns ----

def test_calendar_year_returns_compounds_within_year():
    idx = pd.to_datetime(["2020-11-30", "2020-12-31", "2021-01-31", "2021-02-28"])
    r = pd.Series([0.10, 0.10, 0.05, 0.05], index=idx)
    out = analysis.calendar_year_returns(r)
    assert out[0]["year"] == 2020
    assert abs(out[0]["return_pct"] - (1.1 * 1.1 - 1)) < 1e-12
    assert out[0]["partial"] is True and out[0]["months"] == 2
    assert out[1]["year"] == 2021
    assert abs(out[1]["return_pct"] - (1.05 * 1.05 - 1)) < 1e-12


def test_calendar_year_returns_full_year_not_partial():
    idx = pd.date_range("2022-01-31", periods=12, freq="ME")
    r = pd.Series([0.01] * 12, index=idx)
    out = analysis.calendar_year_returns(r)
    assert out[0]["months"] == 12 and out[0]["partial"] is False


def test_calendar_year_returns_empty():
    assert analysis.calendar_year_returns(pd.Series(dtype=float)) == []


# ---- momentum_signal: shared ranking-signal builder + skip-month ----

def _monthly_frame(n_months=30, n_tickers=8, seed=7):
    idx = _months(n_months)
    rng = np.random.default_rng(seed)
    return pd.DataFrame(rng.normal(0.01, 0.06, (n_months, n_tickers)),
                        index=idx, columns=[f"T{i}" for i in range(n_tickers)])


def test_momentum_signal_skip_zero_matches_legacy_formula():
    r = _monthly_frame()
    pd.testing.assert_frame_equal(analysis.momentum_signal(r, 1), r)
    legacy = (1 + r).rolling(6).apply(np.prod) - 1
    pd.testing.assert_frame_equal(analysis.momentum_signal(r, 6), legacy)


def test_momentum_signal_skip_shifts_window():
    r = _monthly_frame()
    base = analysis.momentum_signal(r, 3, skip_months=0)
    skipped = analysis.momentum_signal(r, 3, skip_months=1)
    pd.testing.assert_frame_equal(skipped, base.shift(1))


def test_rebalance_schedule_skip_shifts_picks_one_month():
    r = _monthly_frame(n_months=18)
    by_month_0 = {row["month"]: row["picks"]
                  for row in analysis.rebalance_schedule(r, top_n=3, rank_lookback=2, n_months=99)}
    by_month_1 = {row["month"]: row["picks"]
                  for row in analysis.rebalance_schedule(r, top_n=3, rank_lookback=2, n_months=99,
                                                         skip_months=1)}
    labels = [d.strftime("%Y-%m") for d in r.index]
    checked = 0
    for i in range(1, len(labels)):
        m, prev = labels[i], labels[i - 1]
        if m in by_month_1 and prev in by_month_0:
            assert by_month_1[m] == by_month_0[prev]
            checked += 1
    assert checked > 5


def test_momentum_backtest_skip_zero_unchanged_and_skip_runs():
    r = _monthly_frame(n_months=24)
    base = analysis.momentum_backtest(r, top_n=3, hold_period=1, rank_lookback=2)
    explicit = analysis.momentum_backtest(r, top_n=3, hold_period=1, rank_lookback=2,
                                          skip_months=0)
    pd.testing.assert_series_equal(base, explicit)
    skipped = analysis.momentum_backtest(r, top_n=3, hold_period=1, rank_lookback=2,
                                         skip_months=1)
    assert len(skipped) == len(base) - 1  # one fewer month of valid signal
    assert skipped.index[0] == base.index[1]


# ---- live current target anchored to the run date ----

def _daily_prices(tickers, start="2026-01-01", end="2026-07-02", seed=0):
    """Deterministic daily (business-day) prices as a geometric random walk.

    The last bar (2026-07-02) sits mid-month, so the newest monthly bar is a
    partial 'in-progress' month — the case the live target replaces.
    """
    idx = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    cols = {t: 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, len(idx))))
            for t in tickers}
    return pd.DataFrame(cols, index=idx)


_MONTH_DAYS = 30.4375  # avg calendar days per month; 1 month -> 30 days (matches Top Performers default)


def test_current_target_matches_top_performers_signal():
    prices = _daily_prices([f"T{i}" for i in range(6)], seed=1)
    ct = analysis.current_target(prices, top_n=3, rank_lookback=1)
    days = round(1 * _MONTH_DAYS)
    tp = analysis.top_performers_period(prices, top_n=3, days=days)
    assert ct["tickers"] == [r["ticker"] for r in tp]
    assert ct["as_of"] == prices.index.max().strftime("%Y-%m-%d")
    assert ct["days"] == days


def test_current_target_none_when_empty():
    assert analysis.current_target(pd.DataFrame(), top_n=3, rank_lookback=1) is None


def test_current_target_skip_ranks_on_earlier_window():
    prices = _daily_prices([f"T{i}" for i in range(6)], seed=5)
    ct = analysis.current_target(prices, top_n=3, rank_lookback=1, skip_months=1)
    cutoff = prices.index.max() - pd.Timedelta(days=round(1 * _MONTH_DAYS))
    truncated = prices.loc[prices.index <= cutoff]
    tp = analysis.top_performers_period(truncated, top_n=3, days=round(1 * _MONTH_DAYS))
    assert ct["tickers"] == [r["ticker"] for r in tp]
    # The target is still "as of" the latest bar; only the signal window moved back.
    assert ct["as_of"] == prices.index.max().strftime("%Y-%m-%d")
    assert ct["signal_end"] == truncated.index.max().strftime("%Y-%m-%d")


def test_rebalance_schedule_exposes_picks():
    prices = _daily_prices([f"T{i}" for i in range(5)], seed=2)
    monthly = datalib.to_monthly_returns(prices)
    rows = analysis.rebalance_schedule(monthly, top_n=2, rank_lookback=1, n_months=6)
    assert rows, "expected at least one rebalance row"
    for row in rows:
        assert len(row["picks"]) == 2
        assert set(row["picks"]) == set(row["buys"]) | set(row["holds"])


def test_recent_picks_attribution_sums_to_cohort_mean():
    prices = _daily_prices([f"T{i}" for i in range(6)], seed=4)
    monthly = datalib.to_monthly_returns(prices)
    cohorts = analysis.recent_picks(monthly, top_n=3, rank_lookback=1)
    assert cohorts
    labels = [d.strftime("%Y-%m") for d in monthly.index]
    for c in cohorts:
        vals = [v for v in c["pick_returns"].values() if v is not None]
        assert set(c["pick_returns"]) == set(c["picks"])
        assert abs(np.mean(vals) - c["return_pct"]) < 1e-12
        # Each value is the pick's actual return in the following month.
        i = labels.index(c["month"])
        for t, v in c["pick_returns"].items():
            expected = monthly.iloc[i + 1][t]
            if v is None:
                assert pd.isna(expected)
            else:
                assert abs(v - expected) < 1e-12


def test_rebalance_schedule_attribution_on_completed_rows_only():
    prices = _daily_prices([f"T{i}" for i in range(5)], seed=2)
    monthly = datalib.to_monthly_returns(prices)
    rows = analysis.rebalance_schedule(monthly, top_n=2, rank_lookback=1, n_months=6)
    assert rows
    newest = rows[0]  # newest month-end: nothing realized yet
    assert "return_pct" not in newest and "pick_returns" not in newest
    realized = [r for r in rows[1:] if "return_pct" in r]
    assert realized, "expected at least one realized rebalance row"
    for r in realized:
        vals = [v for v in r["pick_returns"].values() if v is not None]
        assert abs(np.mean(vals) - r["return_pct"]) < 1e-12


def test_live_rebalance_prepends_live_row_diffed_vs_last_completed_month():
    prices = _daily_prices([f"T{i}" for i in range(6)], seed=3)
    monthly = datalib.to_monthly_returns(prices)
    reb = analysis.live_rebalance(prices, monthly, top_n=3, rank_lookback=1)

    # Top row is the live snapshot, anchored on the latest bar.
    assert reb[0]["is_live"] is True
    assert reb[0]["as_of"] == prices.index.max().strftime("%Y-%m-%d")

    # Its target set equals Top Performers over the same trailing window.
    tp = analysis.top_performers_period(prices, top_n=3, days=round(_MONTH_DAYS))
    assert reb[0]["picks"] == [r["ticker"] for r in tp]

    # The in-progress (partial) month is dropped from the history rows.
    partial = monthly.index[-1].strftime("%Y-%m")
    assert all(row.get("month") != partial for row in reb[1:])

    # Buys/sells/holds are the set-diff of the live target vs the newest
    # completed month's picks (what you'd be holding from the last rebalance).
    prev = reb[1]["picks"]
    tgt = reb[0]["picks"]
    assert reb[0]["buys"] == [t for t in tgt if t not in set(prev)]
    assert reb[0]["sells"] == [t for t in prev if t not in set(tgt)]
    assert reb[0]["holds"] == [t for t in tgt if t in set(prev)]
