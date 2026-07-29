"""Regime primitives: 200dma state, the filter mask, and conditional evidence.

The tests that matter here are the timing ones. The mask is a decision made AT a
month-end close and must only ever touch the FOLLOWING month, and it has to be
shifted on the monthly index before being reindexed onto the strategy's own
(sparser) index. Both orderings agree on a gapless index, which is exactly why
the wrong one ships easily.
"""

import json

import numpy as np
import pandas as pd

import server
from lib import analysis


def _months(n, start="2020-01-31"):
    return pd.date_range(start, periods=n, freq="ME")


def _daily(n=400, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


# ---- regime_state ----

def test_regime_state_is_na_during_the_moving_average_warmup():
    idx = _daily(60)
    s = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
    st = analysis.regime_state(s, ma_days=50)
    assert st["above"].iloc[:49].isna().all()
    assert st["above"].iloc[49:].notna().all()
    # Nullable boolean, not object: the two dtypes behave oppositely under the
    # natural .where(decision != False) idiom, so the dtype is part of the contract.
    assert str(st["above"].dtype) == "boolean"


def test_regime_state_tracks_crossings():
    idx = _daily(120)
    # Rise then fall hard enough to cross a 20-bar average.
    vals = np.concatenate([np.linspace(100, 140, 60), np.linspace(140, 90, 60)])
    st = analysis.regime_state(pd.Series(vals, index=idx), ma_days=20)
    assert bool(st["above"].iloc[59]) is True
    assert bool(st["above"].iloc[-1]) is False
    assert st["dist"].iloc[-1] < 0


# ---- regime_mask + apply_regime timing ----

def test_apply_regime_shifts_the_decision_by_one_month():
    idx = _months(6)
    r = pd.Series([0.10] * 6, index=idx)
    # Risk-off AT months 2 and 4 (0-based), so months 3 and 5 get replaced.
    mask = pd.Series([True, True, False, True, False, True], index=idx, dtype="boolean")
    out = analysis.apply_regime(r, mask, 0.0)
    assert list(out.values) == [0.10, 0.10, 0.10, 0.0, 0.10, 0.0]


def test_apply_regime_shifts_before_reindexing_onto_a_sparser_index():
    # The strategy index is a strict subset of the monthly index: the backtest
    # starts after the ranking warm-up and skips months with no sleeve. Shifting
    # after reindexing would shift *inside* the subset, reaching two labels back
    # across the hole and leaving the first month unfiltered.
    idx = _months(8)
    mask = pd.Series([pd.NA, pd.NA, True, False, True, False, True, True],
                     index=idx, dtype="boolean")
    kept = [i for i in range(8) if i != 5]          # label 5 absent from returns
    r = pd.Series([0.01 * (i + 1) for i in kept], index=idx[kept])
    out = analysis.apply_regime(r, mask, 0.0)
    # month 4 follows mask[3]==False -> replaced; month 6 follows mask[5]==False
    # -> replaced; month 7 follows mask[6]==True -> kept.
    assert out.loc[idx[4]] == 0.0
    assert out.loc[idx[6]] == 0.0
    assert out.loc[idx[7]] == 0.08
    # The buggy ordering would have zeroed month 6 off mask[3] and kept month 4.
    assert out.loc[idx[3]] == 0.04


def test_apply_regime_leaves_warmup_months_invested():
    idx = _months(4)
    r = pd.Series([0.05] * 4, index=idx)
    mask = pd.Series([pd.NA, pd.NA, True, False], index=idx, dtype="boolean")
    out = analysis.apply_regime(r, mask, 0.0)
    # NA is not "risk-off": an unknown average must not silently zero the sleeve.
    assert list(out.values) == [0.05, 0.05, 0.05, 0.05]


def test_apply_regime_object_dtype_mask_behaves_the_same():
    # Guards the dtype trap: an object mask holding NaN must give the same answer.
    idx = _months(4)
    r = pd.Series([0.05] * 4, index=idx)
    obj = pd.Series([np.nan, True, False, True], index=idx, dtype="object")
    nullable = pd.Series([pd.NA, True, False, True], index=idx, dtype="boolean")
    assert list(analysis.apply_regime(r, obj, 0.0).values) == \
           list(analysis.apply_regime(r, nullable, 0.0).values)


def test_apply_regime_benchmark_mode_substitutes_the_benchmark():
    idx = _months(4)
    r = pd.Series([0.10] * 4, index=idx)
    bench = pd.Series([0.01, 0.02, 0.03, 0.04], index=idx)
    mask = pd.Series([True, False, True, True], index=idx, dtype="boolean")
    out = analysis.apply_regime(r, mask, bench)
    assert out.iloc[2] == 0.03      # month 2 follows a risk-off month 1
    assert out.iloc[1] == 0.10


def test_regime_mask_uses_only_bars_at_or_before_each_label():
    idx = _daily(300)
    s = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    monthly = pd.date_range(idx[0], idx[-1] + pd.Timedelta(days=40), freq="ME")
    mask = analysis.regime_mask(s, monthly, ma_days=50)
    # The newest label is a future month-end; it must resolve to the latest bar
    # that exists rather than becoming NaN or reaching forward.
    assert mask.index[-1] > idx[-1]
    assert not pd.isna(mask.iloc[-1])


# ---- conditional stats ----

def test_conditional_stats_bucket_by_the_prior_month_end_state():
    idx = _months(6)
    r = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05, 0.06], index=idx)
    bench = pd.Series([0.0] * 6, index=idx)
    mask = pd.Series([True, False, True, False, True, True], index=idx, dtype="boolean")
    out = analysis.regime_conditional_stats(r, mask, bench)
    # decision = mask.shift(1) = [NA, True, False, True, False, True]
    assert out["warmup"]["n"] == 1                       # month 0
    assert out["risk_on"]["n"] == 3                      # months 1, 3, 5
    assert out["risk_off"]["n"] == 2                     # months 2, 4
    assert abs(out["risk_off"]["mean"] - np.mean([0.03, 0.05])) < 1e-12


def test_conditional_stats_empty_bucket_reports_n_zero_not_nan():
    idx = _months(4)
    r = pd.Series([0.01] * 4, index=idx)
    bench = pd.Series([0.0] * 4, index=idx)
    mask = pd.Series([True] * 4, index=idx, dtype="boolean")
    out = analysis.regime_conditional_stats(r, mask, bench)
    off = out["risk_off"]
    assert off["n"] == 0
    # None, never NaN: allow_nan=False would 500 the whole response.
    assert off["mean"] is None and off["t_stat"] is None
    json.dumps(out, allow_nan=False)


# ---- snapshot and vol, and the NaN discipline ----

def test_snapshot_reports_a_date_even_with_no_moving_average_window():
    idx = _daily(10)
    s = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    snap = analysis.regime_snapshot(s, ma_days=200)
    assert snap["as_of"] == idx[-1].strftime("%Y-%m-%d")
    assert snap["close"] is not None
    # Everything that needs the average is None, not 0.0.
    assert snap["ma"] is None and snap["dist"] is None and snap["above"] is None
    assert snap["days_in_state"] is None
    json.dumps(snap, allow_nan=False)


def test_snapshot_counts_days_in_the_current_state():
    idx = _daily(120)
    vals = np.concatenate([np.linspace(100, 140, 60), np.linspace(139, 90, 60)])
    snap = analysis.regime_snapshot(pd.Series(vals, index=idx), ma_days=20)
    assert snap["above"] is False
    assert snap["days_in_state"] > 0
    assert snap["state_since"] is not None
    assert snap["drawdown"] < 0


def test_snapshot_and_vol_on_an_empty_series_are_json_clean():
    empty = pd.Series(dtype=float)
    json.dumps(analysis.regime_snapshot(empty), allow_nan=False)
    json.dumps(analysis.realized_vol_percentile(empty), allow_nan=False)


def test_realized_vol_percentile_is_none_on_a_short_series():
    idx = _daily(10)
    out = analysis.realized_vol_percentile(pd.Series(np.linspace(1, 2, 10), index=idx),
                                           window=21)
    assert out["vol_ann"] is None and out["percentile"] is None
    json.dumps(out, allow_nan=False)


def test_realized_vol_percentile_ranks_against_its_own_history():
    rng = np.random.default_rng(3)
    idx = _daily(300)
    # Calm then violent: the latest vol must land at a high percentile.
    steps = np.concatenate([rng.normal(0, 0.002, 250), rng.normal(0, 0.03, 50)])
    s = pd.Series(100 * np.exp(np.cumsum(steps)), index=idx)
    out = analysis.realized_vol_percentile(s, window=21)
    assert out["percentile"] > 0.9
    assert out["vol_ann"] > out["median"]


def test_server_num_coerces_every_nonfinite_to_none():
    for bad in (np.nan, np.inf, -np.inf, None, pd.NA, "x"):
        assert server._num(bad) is None
    assert server._num(np.float64(1.5)) == 1.5
