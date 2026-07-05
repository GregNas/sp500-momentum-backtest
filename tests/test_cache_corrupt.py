"""Regression tests for corrupt-cache recovery and atomic writes.

A partial or concurrently-interleaved parquet write leaves ``prices.parquet``
readable-looking (valid PAR1 magic) but with corrupt page headers, so pyarrow
raises "Couldn't deserialize thrift ... Deserializing page header failed." These
tests pin the two defenses: writes are atomic (a failed write never clobbers the
good cache) and reads self-heal (a corrupt cache re-downloads instead of
bricking every request).
"""

import json
import os
import threading
from datetime import datetime, timezone

import pandas as pd
import pytest

from lib import cache


def _point_cache_at(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PRICES_PARQUET", tmp_path / "prices.parquet")
    monkeypatch.setattr(cache, "PRICES_META", tmp_path / "prices_meta.json")


def test_get_prices_cached_recovers_from_corrupt_parquet(tmp_path, monkeypatch):
    # Corrupt parquet on disk, with an otherwise-valid meta pointing at it —
    # exactly the state that bricked the dashboard.
    _point_cache_at(tmp_path, monkeypatch)
    (tmp_path / "prices.parquet").write_bytes(b"PAR1 this is not a real parquet file PAR1")
    (tmp_path / "prices_meta.json").write_text(json.dumps({
        "start": "2021-01-04",
        "end": "2021-01-08",
        "tickers": ["AAA"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))

    calls = {"n": 0}

    def fetcher(tickers, start, end):
        calls["n"] += 1
        rng = pd.bdate_range(start, end)
        return pd.DataFrame({t: [100.0] * len(rng) for t in tickers}, index=rng)

    out = cache.get_prices_cached(["AAA"], "2021-01-04", "2021-01-08", fetcher)

    assert calls["n"] >= 1                       # corrupt cache forced a re-download
    assert not out.empty
    assert list(out.columns) == ["AAA"]
    pd.read_parquet(tmp_path / "prices.parquet")  # cache is now a readable parquet


def test_save_partial_write_leaves_existing_cache_intact(tmp_path, monkeypatch):
    _point_cache_at(tmp_path, monkeypatch)
    good = pd.DataFrame(
        {"AAA": [1.0, 2.0, 3.0]},
        index=pd.date_range("2021-01-04", periods=3),
    )
    cache._save(good)
    assert pd.read_parquet(cache.PRICES_PARQUET)["AAA"].tolist() == [1.0, 2.0, 3.0]

    # Simulate an interrupted flush: the underlying write truncates the target
    # file it produced and then fails. A non-atomic _save writes straight to the
    # live cache and corrupts it; an atomic _save writes a temp file, so the
    # good cache is untouched.
    real_to_parquet = pd.DataFrame.to_parquet

    def truncating_to_parquet(self, path, *args, **kwargs):
        real_to_parquet(self, path, *args, **kwargs)
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.truncate(size // 2)
        raise OSError("write interrupted")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", truncating_to_parquet)
    doomed = pd.DataFrame({"AAA": [9.0]}, index=pd.date_range("2022-01-04", periods=1))
    with pytest.raises(OSError):
        cache._save(doomed)

    # The previously-good cache must still read back unchanged.
    back = pd.read_parquet(cache.PRICES_PARQUET)
    assert back["AAA"].tolist() == [1.0, 2.0, 3.0]


def test_concurrent_saves_use_distinct_tempfiles(tmp_path, monkeypatch):
    # FastAPI runs sync endpoints in a threadpool, so two requests (e.g. the
    # backtest and the top-performers panel) can call _save in the same process
    # at the same time. If both writers share one temp file their writes
    # interleave and re-corrupt the parquet — the original failure. Each concurrent
    # save must therefore get its own temp path.
    _point_cache_at(tmp_path, monkeypatch)
    got_paths: list[str] = []
    both_writing = threading.Barrier(2, timeout=5)
    real_to_parquet = pd.DataFrame.to_parquet

    def racing_to_parquet(self, path, *args, **kwargs):
        got_paths.append(str(path))
        both_writing.wait()                      # hold until both are mid-write
        real_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", racing_to_parquet)
    idx = pd.date_range("2021-01-04", periods=1)
    df_a = pd.DataFrame({"AAA": [1.0]}, index=idx)
    df_b = pd.DataFrame({"AAA": [2.0]}, index=idx)
    threads = [threading.Thread(target=cache._save, args=(df,)) for df in (df_a, df_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(got_paths) == 2
    assert got_paths[0] != got_paths[1]          # no shared temp -> no interleave
    pd.read_parquet(cache.PRICES_PARQUET)        # final cache is a complete, readable file


def test_save_leaves_no_tempfile(tmp_path, monkeypatch):
    _point_cache_at(tmp_path, monkeypatch)
    df = pd.DataFrame({"AAA": [1.0, 2.0]}, index=pd.date_range("2021-01-04", periods=2))
    cache._save(df)
    entries = sorted(p.name for p in tmp_path.iterdir())
    assert entries == ["prices.parquet", "prices_meta.json"]
