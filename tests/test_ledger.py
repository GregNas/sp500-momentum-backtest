"""Tests for the out-of-sample paper-trading ledger.

Three things here are regression guards rather than unit tests, and are the
reason this file is long:

* the record path must narrow the shared parquet to the requested universe and
  drop the benchmark before ranking (I1) — the parquet holds all 3 universes plus
  every benchmark ever requested, so an unnarrowed frame silently records a
  different cohort than the leaderboard shows;
* every payload must survive ``json.dumps(..., allow_nan=False)``, which is what
  Starlette does when rendering (a stray NaN is an HTTP 500, not a bad number);
* ``cache.clear()`` must not be able to reach the ledger.
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

import server
from lib import analysis, cache, data, ledger


# ---- fixtures -------------------------------------------------------------

def _point_ledger_at(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DATA_DIR", tmp_path / "userdata")
    monkeypatch.setattr(ledger, "LEDGER_JSON", tmp_path / "userdata" / "ledger.json")


def _point_cache_at(tmp_path, monkeypatch):
    """Patch every path ``cache.clear()`` touches, not just the 3 that
    ``test_cache_corrupt._point_cache_at`` patches: ``clear()`` iterates
    ``(PRICES_PARQUET, PRICES_META, TICKERS_JSON, SP500_META_JSON)``, so a test
    that calls it with only the first 3 redirected would unlink the developer's
    real ``cache/tickers.json`` and ``cache/sp500_meta.json`` and force a
    Wikipedia re-scrape on the next run (a hard failure on an airgapped box)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache, "PRICES_PARQUET", cache_dir / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", cache_dir / "prices_meta.json")
    monkeypatch.setattr(cache, "TICKERS_JSON", cache_dir / "tickers.json")
    monkeypatch.setattr(cache, "SP500_META_JSON", cache_dir / "sp500_meta.json")
    return cache_dir


def _ramp_frame(slopes: dict, n: int = 260) -> pd.DataFrame:
    """Deterministic monotonic price ramps ending on the latest business day.

    ``P(u) / P(s) == (1 + slope) ** (u - s)`` in bar counts, so every window
    return and every mark is hand-computable and the ranking never depends on the
    calendar the test happens to run on.
    """
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame(
        {t: 100.0 * (1.0 + s) ** np.arange(n, dtype=float) for t, s in slopes.items()},
        index=idx,
    )


def _entry(as_of, tickers, frame, p_hash="h1", benchmark="BENCH", **extra) -> dict:
    prices = {}
    for t in tickers:
        if t in frame.columns and pd.Timestamp(as_of) in frame.index:
            v = frame[t].loc[pd.Timestamp(as_of)]
            prices[t] = None if pd.isna(v) else float(v)
        else:
            prices[t] = None
    entry = {
        "params_hash": p_hash,
        "signal_version": ledger.SIGNAL_VERSION,
        "params": {"universe": "sp500", "topN": len(tickers),
                   "rankLookback": 1, "skipMonths": 0},
        "benchmark": benchmark,
        "as_of": pd.Timestamp(as_of).strftime("%Y-%m-%d"),
        "tickers": list(tickers),
        "entry_prices": prices,
        "entry_unpriced": [t for t, v in prices.items() if v is None],
        "note": None,
    }
    entry.update(extra)
    return entry


def _mean_ratio(frame, tickers, s_ts, e_ts) -> float:
    return float(np.mean([frame[t].loc[e_ts] / frame[t].loc[s_ts] for t in tickers]))


# ---- params_hash ----------------------------------------------------------

_BASE_PARAMS = {"universe": "sp500", "topN": 10, "rankLookback": 1, "skipMonths": 0}


def test_params_hash_is_stable_and_short():
    h = ledger.params_hash(dict(_BASE_PARAMS))
    assert h == ledger.params_hash(dict(_BASE_PARAMS))
    assert len(h) == 12


@pytest.mark.parametrize("extra", [
    {"benchmark": "ACWI"},
    {"lookback": 25},
    {"holds": [1, 3, 6, 12]},
    {"horizon": 24},
    {"riskFree": 0.05},
    {"runId": "abc"},
])
def test_params_hash_ignores_non_ranking_fields(extra):
    # These change reporting or what the record is compared against, never the
    # recommended cohort. Hashing them would fragment one track record.
    assert ledger.params_hash({**_BASE_PARAMS, **extra}) == ledger.params_hash(_BASE_PARAMS)


@pytest.mark.parametrize("field,value", [
    ("universe", "us_sector_etfs"),
    ("topN", 11),
    ("rankLookback", 2),
    ("skipMonths", 1),
])
def test_params_hash_changes_with_ranking_params(field, value):
    assert ledger.params_hash({**_BASE_PARAMS, field: value}) != ledger.params_hash(_BASE_PARAMS)


def test_params_hash_changes_with_signal_version(monkeypatch):
    before = ledger.params_hash(_BASE_PARAMS)
    monkeypatch.setattr(ledger, "SIGNAL_VERSION", ledger.SIGNAL_VERSION + 1)
    assert ledger.params_hash(_BASE_PARAMS) != before


# ---- append: idempotency, the monthly cap, backdating ---------------------

def test_append_is_idempotent_per_as_of_and_hash(tmp_path, monkeypatch):
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01})
    e = _entry("2026-03-31", ["A"], frame)

    stored, created = ledger.append(e)
    assert created is True
    again, created_again = ledger.append(dict(e))
    assert created_again is False
    assert again["as_of"] == "2026-03-31"
    assert len(ledger.load()) == 1


def test_append_accepts_a_new_month_and_another_hash(tmp_path, monkeypatch):
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01})
    ledger.append(_entry("2026-03-31", ["A"], frame))
    _, created = ledger.append(_entry("2026-04-30", ["A"], frame))
    assert created is True
    _, other_hash = ledger.append(_entry("2026-03-31", ["A"], frame, p_hash="h2"))
    assert other_hash is True
    assert len(ledger.load()) == 3


def test_append_caps_one_entry_per_calendar_month(tmp_path, monkeypatch):
    # A second click in the same month must not put a 3-day segment in the chain.
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01})
    ledger.append(_entry("2026-03-05", ["A"], frame))
    existing, created = ledger.append(_entry("2026-03-27", ["A"], frame))
    assert created is False
    assert existing["as_of"] == "2026-03-05"        # nothing updated
    assert len(ledger.load()) == 1
    # A different hash is a different track record and is not capped by it.
    _, created_other = ledger.append(_entry("2026-03-27", ["A"], frame, p_hash="h2"))
    assert created_other is True


def test_append_refuses_a_backdated_as_of(tmp_path, monkeypatch):
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01})
    ledger.append(_entry("2026-04-30", ["A"], frame))
    before = ledger.LEDGER_JSON.read_bytes()
    with pytest.raises(ledger.LedgerConflict):
        ledger.append(_entry("2026-02-27", ["A"], frame))
    assert ledger.LEDGER_JSON.read_bytes() == before


# ---- durability -----------------------------------------------------------

def test_corrupt_ledger_is_quarantined_not_overwritten(tmp_path, monkeypatch):
    _point_ledger_at(tmp_path, monkeypatch)
    ledger.DATA_DIR.mkdir(parents=True)
    ledger.LEDGER_JSON.write_bytes(b"{not json")

    assert ledger.load() == []
    corrupt = list(ledger.DATA_DIR.glob("ledger.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_bytes() == b"{not json"     # user data moved, never destroyed
    assert not ledger.LEDGER_JSON.exists()

    frame = _ramp_frame({"A": 0.01})
    _, created = ledger.append(_entry("2026-03-31", ["A"], frame))
    assert created is True
    assert len(ledger.load()) == 1


def test_failed_ledger_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01})
    ledger.append(_entry("2026-03-31", ["A"], frame))
    before = ledger.LEDGER_JSON.read_bytes()

    def exploding_write(dest, write):
        raise OSError("disk full")

    monkeypatch.setattr(cache, "atomic_write", exploding_write)
    with pytest.raises(OSError):
        ledger.append(_entry("2026-04-30", ["A"], frame))

    assert ledger.LEDGER_JSON.read_bytes() == before
    assert not list(ledger.DATA_DIR.glob("*.tmp"))
    assert not [p for p in ledger.DATA_DIR.iterdir() if p.name.endswith(".tmp")]


def test_cache_clear_leaves_the_ledger_intact(tmp_path, monkeypatch):
    # Structural guarantee, not luck: the ledger lives outside CACHE_DIR, so no
    # present or future clear() can reach it.
    cache_dir = _point_cache_at(tmp_path, monkeypatch)
    _point_ledger_at(tmp_path, monkeypatch)
    frame = _ramp_frame({"A": 0.01, "B": 0.02})
    cache._save(frame)
    cache.save_tickers(["A", "B"])
    cache.save_metadata({"A": {"name": "A", "sector": "x"}})
    ledger.append(_entry("2026-03-31", ["A"], frame))
    body = ledger.LEDGER_JSON.read_bytes()

    cache.clear()

    assert not cache.PRICES_PARQUET.exists()
    assert not cache.TICKERS_JSON.exists()
    assert not cache.SP500_META_JSON.exists()
    assert ledger.LEDGER_JSON.exists()
    assert ledger.LEDGER_JSON.read_bytes() == body
    assert ledger.DATA_DIR not in cache_dir.parents
    assert cache_dir not in ledger.DATA_DIR.parents


# ---- cache.load_prices ----------------------------------------------------

def test_load_prices_is_disk_only_and_narrows(tmp_path, monkeypatch):
    _point_cache_at(tmp_path, monkeypatch)
    assert cache.load_prices(["A"]) is None            # no cache at all
    frame = _ramp_frame({"A": 0.01, "B": 0.02, "BENCH": 0.005})
    cache._save(frame)

    narrowed = cache.load_prices(["A", "BENCH"])
    assert list(narrowed.columns) == ["A", "BENCH"]
    full = cache.load_prices()
    assert set(full.columns) == {"A", "B", "BENCH"}
    assert cache.load_prices(["NOPE"]).empty


# ---- ledger_marks: arithmetic --------------------------------------------

def test_marks_single_open_segment_matches_hand_computed_mean_of_ratios():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "C": 0.001, "BENCH": 0.0015})
    s_ts = frame.index[-31]
    t_ts = frame.index[-1]
    picks = ["A", "B", "C"]
    out = analysis.ledger_marks([_entry(s_ts, picks, frame)], frame, benchmark="BENCH")

    row = out["rows"][0]
    expected = _mean_ratio(frame, picks, s_ts, t_ts) - 1
    assert row["is_open"] is True
    assert abs(row["return_pct"] - expected) < 1e-12
    assert abs(out["summary"]["total_return"] - expected) < 1e-12
    assert abs(out["equity"]["strategy"][-1] - (1 + expected)) < 1e-12
    assert out["summary"]["equity_covers_all_segments"] is True
    json.dumps(out, allow_nan=False)


def test_marks_two_segments_chain_at_the_shared_bar():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "C": 0.001, "BENCH": 0.0015})
    s0, s1, t1 = frame.index[-61], frame.index[-31], frame.index[-1]
    picks = ["A", "B", "C"]
    entries = [_entry(s0, picks, frame), _entry(s1, picks, frame)]
    out = analysis.ledger_marks(entries, frame, benchmark="BENCH")

    r0 = _mean_ratio(frame, picks, s0, s1) - 1
    r1 = _mean_ratio(frame, picks, s1, t1) - 1
    assert abs(out["summary"]["total_return"] - ((1 + r0) * (1 + r1) - 1)) < 1e-12

    rows = out["rows"]                                   # newest first
    assert [r["as_of"] for r in rows] == [s1.strftime("%Y-%m-%d"), s0.strftime("%Y-%m-%d")]
    assert abs(rows[1]["return_pct"] - r0) < 1e-12
    assert abs(rows[0]["return_pct"] - r1) < 1e-12

    dates = out["equity"]["dates"]
    seam = s1.strftime("%Y-%m-%d")
    assert dates.count(seam) == 1                        # continuous, not duplicated
    assert abs(out["equity"]["strategy"][dates.index(seam)] - (1 + r0)) < 1e-12
    assert abs(out["equity"]["strategy"][-1] - (1 + r0) * (1 + r1)) < 1e-12
    json.dumps(out, allow_nan=False)


def test_marks_frozen_leg_stays_in_the_denominator():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "C": 0.001, "BENCH": 0.0015})
    s_ts, last_c = frame.index[-61], frame.index[-45]
    frame.loc[frame.index > last_c, "C"] = np.nan        # delisted mid-segment
    picks = ["A", "B", "C"]
    out = analysis.ledger_marks([_entry(s_ts, picks, frame)], frame, benchmark="BENCH")

    row = out["rows"][0]
    assert row["frozen_tickers"] == ["C"]
    assert row["frozen_at"]["C"] == last_c.strftime("%Y-%m-%d")
    frozen_r = frame["C"].loc[last_c] / frame["C"].loc[s_ts] - 1
    assert abs(row["pick_returns"]["C"] - frozen_r) < 1e-12
    # Denominator unchanged: dropping the leg would reweight the survivors up,
    # which is the survivorship bias the ledger exists to escape.
    assert row["n_priced"] == 3
    expected = float(np.mean([row["pick_returns"][t] for t in picks]))
    assert abs(row["return_pct"] - expected) < 1e-12
    json.dumps(out, allow_nan=False)


def test_marks_unpriced_leg_is_none_and_excluded_from_the_mean():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "C": 0.001, "BENCH": 0.0015})
    s_ts = frame.index[-31]
    entry = _entry(s_ts, ["A", "B", "C"], frame)
    entry["entry_prices"]["C"] = None                    # never had a bar
    entry["entry_unpriced"] = ["C"]
    out = analysis.ledger_marks([entry], frame.drop(columns=["C"]), benchmark="BENCH")

    row = out["rows"][0]
    assert row["pick_returns"]["C"] is None              # never a fabricated 0
    assert row["unpriced_tickers"] == ["C"]
    assert row["n_priced"] == 2
    expected = _mean_ratio(frame, ["A", "B"], s_ts, frame.index[-1]) - 1
    assert abs(row["return_pct"] - expected) < 1e-12
    json.dumps(out, allow_nan=False)


def test_marks_basis_shift_is_flagged_but_does_not_move_the_return():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "C": 0.001, "BENCH": 0.0015})
    s_ts = frame.index[-31]
    picks = ["A", "B", "C"]
    entry = _entry(s_ts, picks, frame)                    # prices recorded pre-split
    clean = analysis.ledger_marks([entry], frame, benchmark="BENCH")

    halved = frame.copy()
    halved["C"] = halved["C"] / 2.0                      # 2:1 re-adjustment
    shifted = analysis.ledger_marks([dict(entry)], halved, benchmark="BENCH")

    row = shifted["rows"][0]
    assert row["basis_shifted"] is True
    assert abs(row["basis_ratio"]["C"] - 0.5) < 1e-6
    assert row["basis_unverified"] is False              # both endpoints are cached
    assert abs(row["return_pct"] - clean["rows"][0]["return_pct"]) < 1e-12
    json.dumps(shifted, allow_nan=False)


def test_marks_dividend_scale_drift_does_not_trip_the_basis_chip():
    # auto_adjust re-adjusts dividends too, so a ~3%-yield name drifts a few
    # percent a year with no split anywhere. The chip must stay quiet there.
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    s_ts = frame.index[-31]
    entry = _entry(s_ts, ["A", "B"], frame)
    entry["entry_prices"]["A"] = entry["entry_prices"]["A"] * 1.03
    out = analysis.ledger_marks([entry], frame, benchmark="BENCH")
    assert out["rows"][0]["basis_shifted"] is False


def test_marks_survive_a_lagging_benchmark_column():
    # The live cache is exactly this shape: the benchmark's last bars are NaN
    # while the constituents carry the newest one. Reading the exact bar would
    # drop the comparison on every segment ending at the frame's last bar, which
    # includes the open segment the user actually watches.
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    s0, s1 = frame.index[-61], frame.index[-31]
    picks = ["A", "B"]
    lagged = frame.copy()
    lagged.loc[lagged.index[-2:], "BENCH"] = np.nan
    out = analysis.ledger_marks(
        [_entry(s0, picks, frame), _entry(s1, picks, frame)], lagged, benchmark="BENCH")

    open_row = out["rows"][0]
    assert open_row["bench_return_pct"] is not None
    assert open_row["excess_pp"] is not None
    # It reports the bar it actually used, which is earlier than the row's end.
    last_valid = lagged["BENCH"].dropna().index[-1].strftime("%Y-%m-%d")
    assert open_row["bench_to_date"] == last_valid
    assert open_row["bench_to_date"] != open_row["end_date"]
    # Both headline legs chain the same segments, so excess is a real difference.
    s = out["summary"]
    assert s["n_comparable_segments"] == s["n_priced_segments"]
    assert s["comparison_covers_all_segments"] is True
    json.dumps(out, allow_nan=False)


def test_marks_chain_both_legs_over_the_same_segments():
    # A benchmark that only starts partway through: the older segment cannot be
    # compared, so it must be excluded from BOTH totals rather than counted in
    # the strategy leg alone, which would make excess subtract mismatched periods.
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    s0, s1 = frame.index[-61], frame.index[-31]
    picks = ["A", "B"]
    partial = frame.copy()
    partial.loc[partial.index[:-31], "BENCH"] = np.nan   # no bars before s1
    out = analysis.ledger_marks(
        [_entry(s0, picks, frame), _entry(s1, picks, frame)], partial, benchmark="BENCH")

    s = out["summary"]
    assert s["n_priced_segments"] == 2
    assert s["n_comparable_segments"] == 1
    assert s["comparison_covers_all_segments"] is False
    # total_return covers only the comparable segment; the all-segment figure is
    # published separately and differs.
    assert s["total_return"] != s["total_return_all_segments"]
    assert abs(s["excess"] - (s["total_return"] - s["benchmark_total_return"])) < 1e-12
    json.dumps(out, allow_nan=False)


def test_marks_benchmark_is_aligned_bar_for_bar():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    s0, s1, t1 = frame.index[-61], frame.index[-31], frame.index[-1]
    picks = ["A", "B"]
    out = analysis.ledger_marks(
        [_entry(s0, picks, frame), _entry(s1, picks, frame)], frame, benchmark="BENCH")

    older, newer = out["rows"][1], out["rows"][0]
    assert abs(older["bench_return_pct"]
               - (frame["BENCH"].loc[s1] / frame["BENCH"].loc[s0] - 1)) < 1e-12
    assert abs(newer["bench_return_pct"]
               - (frame["BENCH"].loc[t1] / frame["BENCH"].loc[s1] - 1)) < 1e-12
    assert abs(out["equity"]["benchmark"][-1]
               - frame["BENCH"].loc[t1] / frame["BENCH"].loc[s0]) < 1e-12
    assert abs(newer["excess_pp"]
               - (newer["return_pct"] - newer["bench_return_pct"])) < 1e-12


def test_marks_missing_benchmark_column_is_none_not_nan():
    frame = _ramp_frame({"A": 0.004, "B": 0.002})
    s_ts = frame.index[-31]
    out = analysis.ledger_marks([_entry(s_ts, ["A", "B"], frame)], frame,
                                benchmark="BENCH")
    row = out["rows"][0]
    assert row["bench_return_pct"] is None
    assert row["excess_pp"] is None
    assert out["summary"]["benchmark_total_return"] is None
    assert out["summary"]["excess"] is None
    assert out["equity"]["benchmark"] == []
    json.dumps(out, allow_nan=False)               # the assertion that pins the 500


def test_marks_interior_gap_leaves_no_nan_in_the_curve():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    s_ts = frame.index[-31]
    gap = frame.index[-20]
    frame.loc[gap, "A"] = np.nan                   # halt / bad Yahoo day
    frame.loc[gap, "BENCH"] = np.nan
    out = analysis.ledger_marks([_entry(s_ts, ["A", "B"], frame)], frame,
                                benchmark="BENCH")

    strategy = out["equity"]["strategy"]
    assert len(strategy) == len(out["equity"]["dates"])
    assert all(v is not None and np.isfinite(v) for v in strategy)
    assert all(v is not None and np.isfinite(v) for v in out["equity"]["benchmark"])
    assert out["rows"][0]["frozen_tickers"] == []  # a real bar exists at the end
    json.dumps(out, allow_nan=False)


def test_marks_basis_unverified_when_the_cache_no_longer_reaches_as_of():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    picks = ["A", "B"]
    entry = _entry(frame.index[-31], picks, frame)
    trimmed = frame.iloc[-10:]                     # cache no longer covers as_of
    out = analysis.ledger_marks([entry], trimmed, benchmark="BENCH")

    row = out["rows"][0]
    assert row["basis_unverified"] is True
    assert sorted(row["basis_unverified_tickers"]) == picks
    assert row["start_date"] is None
    # The row chain is the honest total; the curve can only be drawn from bars
    # that exist, so it covers less history and the summary says so.
    assert out["summary"]["equity_covers_all_segments"] is False
    assert out["equity"]["dates"] == []
    json.dumps(out, allow_nan=False)


def test_marks_single_bar_entry_is_flat_and_serializable():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    out = analysis.ledger_marks([_entry(frame.index[-1], ["A", "B"], frame)], frame,
                                benchmark="BENCH")
    assert len(out["equity"]["dates"]) == 1        # the panel's months.length < 2 branch
    assert out["summary"]["total_return"] == 0.0
    assert out["rows"][0]["days_held"] == 0
    json.dumps(out, allow_nan=False)


def test_marks_annualization_is_blocked_under_twelve_segments():
    frame = _ramp_frame({"A": 0.004, "B": 0.002, "BENCH": 0.0015})
    entries = [_entry(frame.index[-61], ["A", "B"], frame),
               _entry(frame.index[-31], ["A", "B"], frame)]
    out = analysis.ledger_marks(entries, frame, benchmark="BENCH")
    summary = out["summary"]
    assert summary["annualized"] is None
    assert summary["annualized_blocked_reason"]
    assert summary["n_segments"] == 2
    assert summary["n_completed"] == 1             # the newest segment is still open


def test_marks_rejects_mixed_hashes():
    frame = _ramp_frame({"A": 0.004, "B": 0.002})
    entries = [_entry(frame.index[-61], ["A", "B"], frame, p_hash="h1"),
               _entry(frame.index[-31], ["A", "B"], frame, p_hash="h2")]
    with pytest.raises(ValueError):
        analysis.ledger_marks(entries, frame)


def test_marks_empty_entries_render_an_empty_state():
    out = analysis.ledger_marks([], _ramp_frame({"A": 0.01}))
    assert out["rows"] == []
    assert out["summary"]["n_segments"] == 0
    json.dumps(out, allow_nan=False)


# ---- the record endpoint: I1 preconditions -------------------------------

_UNIVERSE = ["T0", "T1", "T2", "T3", "T4", "T5"]


def _record_setup(tmp_path, monkeypatch, extra_columns=None):
    """A cache shaped like the real one: this universe, the benchmark, and
    another universe's ticker all in one shared parquet."""
    _point_cache_at(tmp_path, monkeypatch)
    _point_ledger_at(tmp_path, monkeypatch)
    slopes = {t: 0.0005 * (i + 1) for i, t in enumerate(_UNIVERSE)}
    # The benchmark and the foreign ticker out-run every constituent, so they
    # would take the top ranks if the frame were not narrowed first.
    slopes.update({"BENCH": 0.01, "XLK": 0.02})
    slopes.update(extra_columns or {})
    frame = _ramp_frame(slopes)
    cache._save(frame)
    monkeypatch.setattr(data, "get_sp500_tickers", lambda: list(_UNIVERSE))
    monkeypatch.setattr(data, "get_prices",
                        lambda tickers, start, end, on_status=None: frame[
                            [t for t in tickers if t in frame.columns]])
    return frame


def test_record_narrows_to_the_universe_and_drops_the_benchmark(tmp_path, monkeypatch):
    # The single most important test here: an unnarrowed frame would rank BENCH
    # and another universe's XLK alongside the constituents, so the recorded
    # entry would silently disagree with the leaderboard (I1).
    frame = _record_setup(tmp_path, monkeypatch)
    out = server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, skipMonths=0, benchmark="BENCH"))

    expected = analysis.current_target(frame[_UNIVERSE], top_n=3, rank_lookback=1)
    assert out["created"] is True
    assert out["entry"]["tickers"] == expected["tickers"]
    assert "BENCH" not in out["entry"]["tickers"]
    assert "XLK" not in out["entry"]["tickers"]
    assert out["target_matches_stored"] is True
    json.dumps(out, allow_nan=False)


def test_record_refuses_a_collapsed_ranking_window(tmp_path, monkeypatch):
    # top_performers_period has no reach-back tolerance, so a short frame would
    # rank on a silently shorter window. Refuse instead of recording that.
    _point_cache_at(tmp_path, monkeypatch)
    _point_ledger_at(tmp_path, monkeypatch)
    slopes = {t: 0.0005 * (i + 1) for i, t in enumerate(_UNIVERSE)}
    slopes["BENCH"] = 0.01
    short = _ramp_frame(slopes, n=20)              # ~27 calendar days
    cache._save(short)
    monkeypatch.setattr(data, "get_sp500_tickers", lambda: list(_UNIVERSE))
    monkeypatch.setattr(data, "get_prices",
                        lambda tickers, start, end, on_status=None: short[
                            [t for t in tickers if t in short.columns]])

    with pytest.raises(server.HTTPException) as exc:
        server._record_ledger_target(server.LedgerRecordParams(
            universe="sp500", topN=3, rankLookback=12, skipMonths=2, benchmark="BENCH"))
    assert exc.value.status_code == 409
    assert "426" in exc.value.detail
    assert ledger.load() == []


def test_record_applies_the_universe_size_guard(tmp_path, monkeypatch):
    _record_setup(tmp_path, monkeypatch)
    with pytest.raises(server.HTTPException) as exc:
        server._record_ledger_target(server.LedgerRecordParams(
            universe="sp500", topN=6, rankLookback=1, benchmark="BENCH"))
    assert exc.value.status_code == 400
    assert "exceeds universe size" in exc.value.detail


def test_record_is_idempotent_and_capped_per_month(tmp_path, monkeypatch):
    _record_setup(tmp_path, monkeypatch)
    params = server.LedgerRecordParams(universe="sp500", topN=3, rankLookback=1,
                                       benchmark="BENCH")
    first = server._record_ledger_target(params)
    second = server._record_ledger_target(params)
    assert first["created"] is True
    assert second["created"] is False
    assert second["reason"] == "already_recorded"
    assert second["target_matches_stored"] is True
    assert len(ledger.load()) == 1


def test_record_falls_back_to_the_stale_ticker_cache_offline(tmp_path, monkeypatch):
    # I5: get_sp500_tickers scrapes Wikipedia past its 24h TTL, so on an
    # airgapped box a Record click a day later would 502 with a usable price
    # cache right there.
    _record_setup(tmp_path, monkeypatch)
    cache.save_tickers(list(_UNIVERSE))

    def no_network():
        raise RuntimeError("airgapped")

    monkeypatch.setattr(data, "get_sp500_tickers", no_network)
    out = server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, benchmark="BENCH"))
    assert out["created"] is True
    assert out["entry"]["universe_source"] == "stale_cache"
    assert out["warnings"]


def test_record_refuses_rather_than_using_price_cache_columns(tmp_path, monkeypatch):
    # prices.parquet is one shared table holding every universe and benchmark
    # ever fetched, so its column list is not an sp500 membership list. Recording
    # off it would rank foreign tickers and break the guarantee that a recorded
    # target equals the leaderboard, so with no membership available at all the
    # only honest outcome is to refuse.
    _record_setup(tmp_path, monkeypatch)

    def no_network():
        raise RuntimeError("airgapped")

    monkeypatch.setattr(data, "get_sp500_tickers", no_network)
    assert not cache.TICKERS_JSON.exists()
    with pytest.raises(server.HTTPException) as exc:
        server._record_ledger_target(server.LedgerRecordParams(
            universe="sp500", topN=3, rankLookback=1, benchmark="BENCH"))
    assert exc.value.status_code == 502
    assert "cached ticker list" in exc.value.detail
    # Nothing was written on the refusal path.
    assert ledger.load() == []


def test_record_still_works_from_a_stale_membership_cache(tmp_path, monkeypatch):
    # The airgap path that IS legitimate: a real membership list on disk, merely
    # past its TTL. It records, and says so on the entry.
    _record_setup(tmp_path, monkeypatch)

    def no_network():
        raise RuntimeError("airgapped")

    monkeypatch.setattr(data, "get_sp500_tickers", no_network)
    cache.save_tickers(list(_UNIVERSE))
    out = server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, benchmark="BENCH"))
    assert out["entry"]["universe_source"] == "stale_cache"
    # Still narrowed: the benchmark and the foreign ticker must not be ranked.
    assert "BENCH" not in out["entry"]["tickers"]
    assert "XLK" not in out["entry"]["tickers"]


def test_record_flags_a_stale_browser_tab(tmp_path, monkeypatch):
    _record_setup(tmp_path, monkeypatch)
    out = server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, benchmark="BENCH",
        displayedAsOf="1999-01-04"))
    assert out["displayed_as_of_matches"] is False


# ---- the read endpoint ---------------------------------------------------

def test_ledger_endpoint_empty_is_200_not_404(tmp_path, monkeypatch):
    _point_cache_at(tmp_path, monkeypatch)
    _point_ledger_at(tmp_path, monkeypatch)
    out = server.ledger_history(params_hash=None, refresh=0)
    assert out["entries_total"] == 0
    assert out["groups"] == []
    json.dumps(out, allow_nan=False)


def test_ledger_endpoint_payload_is_json_clean_and_grouped(tmp_path, monkeypatch):
    frame = _record_setup(tmp_path, monkeypatch)
    server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, benchmark="BENCH"))
    # A second, differently-parameterised record: a separate track record that
    # must never be blended into the first one's curve.
    server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=2, rankLookback=2, benchmark="BENCH"))

    out = server.ledger_history(params_hash=None, refresh=0)
    json.dumps(out, allow_nan=False)               # what Starlette does on render

    assert out["entries_total"] == 2
    assert len(out["groups"]) == 2
    hashes = {g["params_hash"] for g in out["groups"]}
    assert len(hashes) == 2
    for group in out["groups"]:
        assert group["benchmark"] == "BENCH"
        assert group["summary"]["n_segments"] == 1
        assert len(group["rows"]) == 1
        assert len(group["rows"][0]["tickers"]) == group["params"]["topN"]
        assert group["label"].startswith("S&P 500")

    only = server.ledger_history(params_hash=sorted(hashes)[0], refresh=0)
    assert len(only["groups"]) == 1


def test_ledger_endpoint_survives_a_missing_price_cache(tmp_path, monkeypatch):
    frame = _record_setup(tmp_path, monkeypatch)
    server._record_ledger_target(server.LedgerRecordParams(
        universe="sp500", topN=3, rankLookback=1, benchmark="BENCH"))
    os.unlink(cache.PRICES_PARQUET)

    out = server.ledger_history(params_hash=None, refresh=0)
    json.dumps(out, allow_nan=False)
    group = out["groups"][0]
    assert group["summary"]["total_return"] is None
    assert group["equity"]["dates"] == []
    assert out["warnings"]
