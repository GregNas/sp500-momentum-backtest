"""Regression tests for the cache split/adjustment-basis healing."""

import numpy as np
import pandas as pd

from lib import cache


def test_detect_reverse_split_jump():
    idx = pd.date_range("2021-01-01", periods=10, freq="B")
    normal = pd.Series(np.linspace(100, 104, 10), index=idx)
    reverse = pd.Series([47] * 5 + [141] * 5, index=idx)   # 1:3 reverse -> +200%
    df = pd.DataFrame({"OK": normal, "DD": reverse})
    assert cache._detect_split_artifacts(df, ["OK", "DD"]) == ["DD"]


def test_detect_forward_split_jump():
    idx = pd.date_range("2021-01-01", periods=6, freq="B")
    fwd = pd.Series([400, 400, 400, 100, 100, 100], index=idx)  # 4:1 -> -75%
    df = pd.DataFrame({"KLAC": fwd})
    assert cache._detect_split_artifacts(df, ["KLAC"]) == ["KLAC"]


def test_normal_moves_not_flagged():
    idx = pd.date_range("2021-01-01", periods=8, freq="B")
    # a chunky but realistic +20% earnings pop is NOT a split artifact
    v = pd.Series([100, 101, 99, 120, 118, 121, 119, 122], index=idx)
    assert cache._detect_split_artifacts(pd.DataFrame({"X": v}), ["X"]) == []


def test_heal_replaces_with_continuous_series():
    idx = pd.date_range("2021-01-01", periods=6, freq="B")
    stitched = pd.DataFrame({"DD": [47, 47, 47, 141, 141, 141]}, index=idx)

    def fetcher(tickers, start, end):
        assert tickers == ["DD"]
        return pd.DataFrame({"DD": [141] * 6}, index=idx)

    healed = cache._heal_split_artifacts(stitched.copy(), ["DD"], fetcher)
    ratio = (healed["DD"] / healed["DD"].shift(1)).dropna()
    assert ratio.between(cache.SPLIT_JUMP_LO, cache.SPLIT_JUMP_HI).all()


def test_heal_partial_refetch_preserves_existing_rows():
    # Re-fetch returns FEWER rows than the cache spans; the heal must keep the
    # cached values for the missing dates instead of blanking them to NaN.
    idx = pd.date_range("2021-01-01", periods=6, freq="B")
    stitched = pd.DataFrame({"DD": [100, 100, 100, 50, 50, 50]}, index=idx)  # split seam

    def partial_fetcher(tickers, start, end):
        # only the last 3 dates come back (e.g. rate-limited / short history)
        return pd.DataFrame({"DD": [50, 50, 50]}, index=idx[3:])

    out = cache._heal_split_artifacts(stitched.copy(), ["DD"], partial_fetcher)
    assert out["DD"].notna().all()                 # no dates blanked out
    assert list(out["DD"]) == [100, 100, 100, 50, 50, 50]  # cached rows preserved


def test_heal_is_graceful_on_fetch_failure():
    idx = pd.date_range("2021-01-01", periods=4, freq="B")
    stitched = pd.DataFrame({"DD": [47, 47, 141, 141]}, index=idx)

    def bad(tickers, start, end):
        raise RuntimeError("429 Too Many Requests")

    out = cache._heal_split_artifacts(stitched.copy(), ["DD"], bad)
    assert list(out["DD"]) == [47, 47, 141, 141]        # unchanged, no crash


def test_get_prices_cached_heals_split_across_stitch(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")
    monkeypatch.setattr(cache, "TAIL_FRESH_HOURS", -1)   # always treat cache as stale

    dates = pd.bdate_range("2021-01-04", periods=8)
    calls = {"n": 0}

    def fetcher(tickers, start, end):
        # First fetch = pre-split basis (~47); every later fetch (delta + heal
        # re-fetch) is on the post-split basis (~141).
        calls["n"] += 1
        level = 47.0 if calls["n"] == 1 else 141.0
        rng = pd.bdate_range(start, end)
        return pd.DataFrame({t: [level] * len(rng) for t in tickers}, index=rng)

    d = lambda ts: ts.strftime("%Y-%m-%d")
    cache.get_prices_cached(["DD"], d(dates[0]), d(dates[4]), fetcher)      # cold
    out = cache.get_prices_cached(["DD"], d(dates[0]), d(dates[7]), fetcher)  # delta+heal

    ratio = (out["DD"] / out["DD"].shift(1)).dropna()
    assert ratio.between(cache.SPLIT_JUMP_LO, cache.SPLIT_JUMP_HI).all()    # no jump
    assert (out["DD"] == 141.0).all()                                       # one basis
    assert calls["n"] >= 3   # cold + delta + heal re-fetch


def test_get_prices_cached_heals_split_on_extend_earlier(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")
    # Leave the cache "fresh" so ONLY the extend-earlier branch fetches.

    dates = pd.bdate_range("2021-01-04", periods=8)
    calls = {"n": 0}

    def fetcher(tickers, start, end):
        calls["n"] += 1
        level = 47.0 if calls["n"] == 1 else 141.0
        rng = pd.bdate_range(start, end)
        return pd.DataFrame({t: [level] * len(rng) for t in tickers}, index=rng)

    d = lambda ts: ts.strftime("%Y-%m-%d")
    cache.get_prices_cached(["DD"], d(dates[3]), d(dates[7]), fetcher)        # cold, later half
    out = cache.get_prices_cached(["DD"], d(dates[0]), d(dates[7]), fetcher)  # extend earlier + heal

    ratio = (out["DD"] / out["DD"].shift(1)).dropna()
    assert ratio.between(cache.SPLIT_JUMP_LO, cache.SPLIT_JUMP_HI).all()      # seam healed
    assert (out["DD"] == 141.0).all()
