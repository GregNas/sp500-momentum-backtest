"""Regression tests for split-stitch artifacts in lib/cache.py."""

from __future__ import annotations

import pandas as pd

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
