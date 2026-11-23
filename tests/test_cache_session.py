"""Unfinished sessions: Yahoo serves the bar of a session that is still open.

Its close keeps moving until the session ends, so it must not reach the cache.
The tail refetch must also replace a last bar that an older version stored
mid-session, or that bar stays wrong for good.
"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from lib import cache

NY = cache.SESSION_TZ
MID_SESSION = pd.Timestamp("2026-11-18 14:03", tz=NY)
AFTER_CLOSE = pd.Timestamp("2026-11-18 17:00", tz=NY)
IDX = pd.bdate_range("2026-10-01", "2026-11-18")


def _frame(level=100.0):
    return pd.DataFrame({t: level * (1 + 0.001 * (i + 1)) ** np.arange(len(IDX))
                         for i, t in enumerate(["AAA", "BBB"])}, index=IDX)


class Fetcher:
    def __init__(self, source):
        self.source, self.calls = source, []

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        cols = [t for t in tickers if t in self.source.columns]
        return self.source.loc[start:pd.Timestamp(end) - timedelta(days=1), cols]


def _at(monkeypatch, tmp_path, now):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")
    monkeypatch.setattr(cache, "_now_session", lambda: now)


def _seed(frame, fetched_hours_ago):
    cache._save(frame, lag_checked={})
    meta = json.loads(cache.PRICES_META.read_text())
    meta["fetched_at"] = (datetime.now(timezone.utc)
                          - timedelta(hours=fetched_hours_ago)).isoformat()
    cache.PRICES_META.write_text(json.dumps(meta))


def test_today_is_unsettled_until_the_session_settles():
    assert cache.first_unsettled_day(MID_SESSION) == pd.Timestamp("2026-11-18")
    assert cache.first_unsettled_day(
        pd.Timestamp("2026-11-18 16:29", tz=NY)) == pd.Timestamp("2026-11-18")
    assert cache.first_unsettled_day(
        pd.Timestamp("2026-11-18 16:30", tz=NY)) == pd.Timestamp("2026-11-19")
    # 01:00 in Israel on 11-19 is still 11-18 in New York.
    il = pd.Timestamp("2026-11-19 01:00", tz="Asia/Jerusalem").tz_convert(NY)
    assert cache.first_unsettled_day(il) == pd.Timestamp("2026-11-19")


def test_a_cold_fetch_mid_session_keeps_no_bar_for_today(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, MID_SESSION)
    out = cache.get_prices_cached(["AAA", "BBB"], "2026-10-01", "2026-11-19",
                                  Fetcher(_frame()))
    assert out.index.max() == pd.Timestamp("2026-11-17")
    assert json.loads(cache.PRICES_META.read_text())["end"] == "2026-11-17"


def test_a_gap_fill_mid_session_keeps_no_bar_for_today(tmp_path, monkeypatch):
    # The gap fill asks up to the day after the requested end, so it is the
    # path that pulled the open session on 2026-11-18.
    _at(monkeypatch, tmp_path, MID_SESSION)
    holed = _frame().loc[:"2026-11-17"].copy()
    holed.loc[holed.index >= pd.Timestamp("2026-11-02"), "BBB"] = np.nan
    _seed(holed, fetched_hours_ago=0.1)
    fetch = Fetcher(_frame())
    out = cache.get_prices_cached(["AAA", "BBB"], "2026-10-01", "2026-11-18", fetch)
    assert fetch.calls[0][2] == "2026-11-19"
    assert out.index.max() == pd.Timestamp("2026-11-17")
    assert out["BBB"].notna().all()


def test_after_the_close_today_is_kept(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, AFTER_CLOSE)
    out = cache.get_prices_cached(["AAA", "BBB"], "2026-10-01", "2026-11-19",
                                  Fetcher(_frame()))
    assert out.index.max() == pd.Timestamp("2026-11-18")


def test_extending_later_replaces_a_mid_session_last_bar(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, pd.Timestamp("2026-11-23 10:00", tz=NY))
    full = pd.DataFrame(
        {t: 100.0 * (1 + 0.001 * (i + 1)) ** np.arange(37)
         for i, t in enumerate(["AAA", "BBB"])},
        index=pd.bdate_range("2026-10-01", periods=37))
    stored = full.loc[:"2026-11-18"].copy()
    stored.loc["2026-11-18"] *= 0.98          # the close as it stood at 14:03
    _seed(stored, fetched_hours_ago=24 * 5)
    fetch = Fetcher(full)

    out = cache.get_prices_cached(["AAA", "BBB"], "2026-10-01", "2026-11-23", fetch)
    assert fetch.calls[0][1] == "2026-11-17"
    pd.testing.assert_frame_equal(out.loc[:"2026-11-20"], full.loc[:"2026-11-20"],
                                  check_freq=False)
    assert out.index.max() == pd.Timestamp("2026-11-20")
