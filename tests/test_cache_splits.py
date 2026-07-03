"""Regression tests for split-stitch artifacts in lib/cache.py."""

from __future__ import annotations

import pandas as pd
import pytest

from lib import cache


def _frame(dates: list[str], **cols: list[float]) -> pd.DataFrame:
    return pd.DataFrame(cols, index=pd.to_datetime(dates))


def test_detect_flags_forward_split():
    dates = ["2026-06-08", "2026-06-09", "2026-06-10"]
    cached = _frame(dates, KLAC=[500.0, 510.0, 505.0])
    delta = _frame(dates, KLAC=[50.0, 51.0, 50.5])       # 10:1 split -> ratio 0.1
    assert cache._detect_basis_shifts(cached, delta) == ["KLAC"]


def test_detect_flags_reverse_split():
    dates = ["2026-06-08", "2026-06-09", "2026-06-10"]
    cached = _frame(dates, DD=[20.0, 21.0, 20.5])
    delta = _frame(dates, DD=[60.0, 63.0, 61.5])         # 1:3 reverse -> ratio ~3.0
    assert cache._detect_basis_shifts(cached, delta) == ["DD"]


def test_detect_ignores_small_dividend():
    dates = ["2026-06-08", "2026-06-09", "2026-06-10"]
    cached = _frame(dates, AAPL=[200.0, 201.0, 202.0])
    delta = _frame(dates, AAPL=[199.0, 199.995, 200.99]) # ~0.5% re-base -> ratio 0.995
    assert cache._detect_basis_shifts(cached, delta) == []


def test_detect_skips_when_no_overlap():
    cached = _frame(["2026-06-08", "2026-06-09"], KLAC=[500.0, 510.0])
    delta = _frame(["2026-06-11", "2026-06-12"], KLAC=[50.0, 51.0])  # disjoint dates
    assert cache._detect_basis_shifts(cached, delta) == []


class MockFetcher:
    """Offline stand-in for lib.data._fetch_prices.

    `new_series[t]` is the clean, new-basis adjusted series over the full range.
    `splits[t] = (event_date, factor)` models an auto_adjust re-base: a fetch whose
    window ends BEFORE event_date does not know about the action, so it returns the
    OLD basis (`new_series * factor`); a fetch ending on/after event_date returns the
    new basis. Every call is recorded in `.calls` as (tuple(sorted(tickers)), start, end).
    """

    def __init__(self, new_series: dict[str, pd.Series],
                 splits: dict[str, tuple[pd.Timestamp, float]] | None = None):
        self.new = new_series
        self.splits = splits or {}
        self.calls: list[tuple[tuple[str, ...], str, str]] = []

    def __call__(self, tickers: list[str], start: str, end: str) -> pd.DataFrame:
        self.calls.append((tuple(sorted(tickers)), start, end))
        end_ts = pd.Timestamp(end)
        idx = pd.bdate_range(start, end)
        data: dict[str, pd.Series] = {}
        for t in tickers:
            s = self.new[t].reindex(idx)
            if t in self.splits:
                event_date, factor = self.splits[t]
                if end_ts < event_date:
                    s = s * factor          # fetch predates the action -> old basis
            data[t] = s
        return pd.DataFrame(data, index=idx)


def _redirect_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")
    monkeypatch.setattr(cache, "TAIL_FRESH_HOURS", 0)   # never treat the cache as fresh


def _ramp(idx: pd.DatetimeIndex, base: float, step: float) -> pd.Series:
    return pd.Series([base + step * i for i in range(len(idx))], index=idx)


def test_split_after_cache_is_healed(tmp_path, monkeypatch):
    _redirect_cache(tmp_path, monkeypatch)
    full_idx = pd.bdate_range("2026-06-01", "2026-07-03")
    fetcher = MockFetcher(
        new_series={"KLAC": _ramp(full_idx, 50.0, 0.1),
                    "AAPL": _ramp(full_idx, 200.0, 0.2)},
        splits={"KLAC": (pd.Timestamp("2026-06-12"), 10.0)},   # 10:1 split after cache
    )

    # 1. Seed the cache BEFORE the split -> KLAC stored on the old (~500) basis.
    cache.get_prices_cached(["KLAC", "AAPL"], "2026-06-01", "2026-06-10", fetcher)
    cached = pd.read_parquet(cache.PRICES_PARQUET)
    assert cached.loc["2026-06-10", "KLAC"] > 400            # old basis confirmed

    fetcher.calls.clear()

    # 2. Re-run over an extended window that now spans the split.
    out = cache.get_prices_cached(["KLAC", "AAPL"], "2026-06-01", "2026-07-03", fetcher)

    # KLAC healed end-to-end on the new basis: no fake 10x seam jump ...
    klac = out["KLAC"].dropna()
    assert klac.pct_change().dropna().abs().max() < 0.05
    # ... and pre-split history was re-based (~50, not ~500).
    assert out.loc["2026-06-08", "KLAC"] < 60
    # AAPL never shifted -> also continuous.
    assert out["AAPL"].pct_change().dropna().abs().max() < 0.05

    # A full re-fetch was triggered for the split ticker (single-ticker call spanning
    # the full start), distinct from the multi-ticker delta fetch.
    heal_calls = [c for c in fetcher.calls if c[0] == ("KLAC",) and c[1] == "2026-06-01"]
    assert heal_calls, "expected a full re-fetch for the split ticker"


def test_dividend_rerun_does_not_refetch(tmp_path, monkeypatch):
    _redirect_cache(tmp_path, monkeypatch)
    full_idx = pd.bdate_range("2026-06-01", "2026-07-03")
    fetcher = MockFetcher(
        new_series={"KLAC": _ramp(full_idx, 50.0, 0.1)},
        splits={"KLAC": (pd.Timestamp("2026-06-12"), 1.005)},  # ~0.5% dividend re-base
    )

    cache.get_prices_cached(["KLAC"], "2026-06-01", "2026-06-10", fetcher)
    fetcher.calls.clear()
    cache.get_prices_cached(["KLAC"], "2026-06-01", "2026-07-03", fetcher)

    # 0.5% re-base is below SEAM_SHIFT_TOL -> no full-history re-fetch was issued.
    heal_calls = [c for c in fetcher.calls if c[0] == ("KLAC",) and c[1] == "2026-06-01"]
    assert not heal_calls
