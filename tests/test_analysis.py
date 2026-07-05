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


def test_rebalance_schedule_exposes_picks():
    prices = _daily_prices([f"T{i}" for i in range(5)], seed=2)
    monthly = datalib.to_monthly_returns(prices)
    rows = analysis.rebalance_schedule(monthly, top_n=2, rank_lookback=1, n_months=6)
    assert rows, "expected at least one rebalance row"
    for row in rows:
        assert len(row["picks"]) == 2
        assert set(row["picks"]) == set(row["buys"]) | set(row["holds"])


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
