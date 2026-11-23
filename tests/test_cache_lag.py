"""Tail holes: a ticker that stops short of the frame's last bar.

The meta stores one end date for the whole frame. A fetch that brings new bars
for a few tickers only moves that end for all of them, so the range-based delta
never asks for the rest again. These tests pin the data-based check that finds
and fills such a hole, and the throttle that keeps a dead ticker from costing a
fetch on every call.
"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from lib import cache


def _point_cache_at(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")


IDX = pd.bdate_range("2026-06-01", "2026-11-17")
GAP_FROM = pd.Timestamp("2026-08-24")


def _full():
    return pd.DataFrame({t: 100.0 * (1 + 0.001 * (i + 1)) ** np.arange(len(IDX))
                         for i, t in enumerate(["AAA", "BBB", "CCC", "NEW"])}, index=IDX)


def _holed():
    """The real failure: NEW arrived with full history, the others stop 08-21."""
    f = _full()
    f.loc[f.index >= GAP_FROM, ["AAA", "BBB", "CCC"]] = np.nan
    return f


class Fetcher:
    def __init__(self, source, fail=False, drop=()):
        self.source, self.fail, self.drop, self.calls = source, fail, set(drop), []

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        if self.fail:
            raise RuntimeError("yahoo down")
        cols = [t for t in tickers if t in self.source.columns and t not in self.drop]
        # yfinance treats end as exclusive.
        return self.source.loc[start:pd.Timestamp(end) - timedelta(days=1), cols]


def _seed(tmp_path, monkeypatch, frame, fetched_hours_ago=0.0, lag_checked=None):
    _point_cache_at(tmp_path, monkeypatch)
    cache._save(frame, lag_checked=lag_checked or {})
    meta = json.loads(cache.PRICES_META.read_text())
    meta["fetched_at"] = (datetime.now(timezone.utc)
                          - timedelta(hours=fetched_hours_ago)).isoformat()
    cache.PRICES_META.write_text(json.dumps(meta))


def test_lagging_tickers_finds_the_hole_and_ignores_a_short_lag():
    f = _holed()
    f.loc[f.index >= pd.Timestamp("2026-11-16"), "NEW"] = np.nan   # 1-bar lag
    lag = cache.lagging_tickers(f)
    assert set(lag) == {"AAA", "BBB", "CCC"}
    assert lag["AAA"] == pd.Timestamp("2026-08-21")
    assert cache.lagging_tickers(f, ["NEW", "ZZZ"]) == {}


def test_a_fresh_holed_cache_is_filled_on_the_next_call(tmp_path, monkeypatch):
    # Fresh on purpose: the hole is made by a fresh write, so freshness must not
    # skip the check.
    _seed(tmp_path, monkeypatch, _holed(), fetched_hours_ago=0.1)
    fetch = Fetcher(_full())
    msgs = []

    out = cache.get_prices_cached(["AAA", "BBB", "CCC", "NEW"], "2026-06-01",
                                  "2026-11-17", fetch, on_status=msgs.append)
    assert fetch.calls == [(["AAA", "BBB", "CCC"], "2026-08-22", "2026-11-18")]
    assert out.notna().all().all()
    pd.testing.assert_frame_equal(out, _full()[out.columns], check_freq=False)
    on_disk = pd.read_parquet(cache.PRICES_PARQUET)
    assert cache.lagging_tickers(on_disk) == {}
    assert any("3 of 3" in m for m in msgs)


def test_only_requested_tickers_are_filled(tmp_path, monkeypatch):
    # The parquet holds every universe ever fetched. A removed member that is
    # not requested must not be re-pulled.
    _seed(tmp_path, monkeypatch, _holed())
    fetch = Fetcher(_full())
    cache.get_prices_cached(["AAA", "NEW"], "2026-06-01", "2026-11-17", fetch)
    assert [c[0] for c in fetch.calls] == [["AAA"]]


def test_a_dead_ticker_is_retried_once_per_window(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _holed())
    fetch = Fetcher(_full(), drop={"CCC"})            # Yahoo has nothing for CCC
    want = ["AAA", "BBB", "CCC", "NEW"]
    msgs = []

    cache.get_prices_cached(want, "2026-06-01", "2026-11-17", fetch, on_status=msgs.append)
    assert len(fetch.calls) == 1
    assert any("still short: CCC" in m for m in msgs)

    cache.get_prices_cached(want, "2026-06-01", "2026-11-17", fetch)
    assert len(fetch.calls) == 1                       # throttled
    meta = json.loads(cache.PRICES_META.read_text())
    assert set(meta["lag_checked"]) == {"AAA", "BBB", "CCC"}

    # Once the window passes it is asked again.
    old = (datetime.now(timezone.utc) - timedelta(hours=cache.LAG_RETRY_HOURS + 1)).isoformat()
    meta["lag_checked"]["CCC"] = old
    cache.PRICES_META.write_text(json.dumps(meta))
    cache.get_prices_cached(want, "2026-06-01", "2026-11-17", fetch)
    assert fetch.calls[-1][0] == ["CCC"]


def test_a_failed_gap_fill_serves_the_cache(tmp_path, monkeypatch):
    holed = _holed()
    _seed(tmp_path, monkeypatch, holed)
    msgs = []
    out = cache.get_prices_cached(["AAA", "NEW"], "2026-06-01", "2026-11-17",
                                  Fetcher(_full(), fail=True), on_status=msgs.append)
    pd.testing.assert_frame_equal(out, holed[["AAA", "NEW"]], check_freq=False)
    assert any("Gap fill failed" in m for m in msgs)
    # The attempt counts, so an outage is not retried on every call.
    assert "AAA" in json.loads(cache.PRICES_META.read_text())["lag_checked"]


def test_a_complete_cache_makes_no_extra_fetch(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _full(), fetched_hours_ago=0.1)
    fetch = Fetcher(_full())
    cache.get_prices_cached(["AAA", "BBB"], "2026-06-01", "2026-11-17", fetch)
    assert fetch.calls == []


def test_a_split_inside_the_gap_is_healed(tmp_path, monkeypatch):
    # The gap fill appends fresh-basis bars after an old-basis bar. A 2:1 split
    # inside the gap leaves a 0.5 jump at that seam unless it is healed.
    holed = _holed()
    rebased = _full()
    rebased["AAA"] = rebased["AAA"] / 2.0     # yfinance rebases the whole history
    _seed(tmp_path, monkeypatch, holed)
    fetch = Fetcher(rebased)

    out = cache.get_prices_cached(["AAA", "NEW"], "2026-06-01", "2026-11-17", fetch)
    ratio = (out["AAA"] / out["AAA"].shift(1)).dropna()
    assert ratio.between(cache.SPLIT_JUMP_LO, cache.SPLIT_JUMP_HI).all()
    assert len(fetch.calls) == 2                       # gap fill, then full heal


def test_other_saves_keep_the_retry_map(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _holed(), lag_checked={"AAA": "2026-11-18T10:00:00+00:00"})
    cache._save(_holed())
    assert json.loads(cache.PRICES_META.read_text())["lag_checked"] == {
        "AAA": "2026-11-18T10:00:00+00:00"}
