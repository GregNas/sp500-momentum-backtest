"""Unit tests for the analysis methodology fixes and new helpers."""

import numpy as np
import pandas as pd

from lib import analysis


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
