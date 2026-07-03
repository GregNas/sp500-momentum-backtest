# Price-cache split-stitch fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `get_prices_cached` from fabricating a spurious price jump when a stock split (or other corporate action) lands after the cache was written, by detecting the adjustment-basis change at the stitch seam and re-fetching affected tickers' full history.

**Architecture:** Every delta fetch that stitches onto existing cached data is made to overlap the cache by a few trading days. After each fetch, a pure helper compares the two frames on their shared days — the ratio isolates the adjustment-basis change (it compares the same calendar day fetched twice, so real market moves cancel). Tickers whose ratio deviates beyond a tolerance get their whole column re-fetched on one consistent basis and replaced.

**Tech Stack:** Python 3, pandas, pyarrow (parquet), yfinance (production only — tests use a mock fetcher), pytest.

## Global Constraints

- Add `pytest>=8.0` to `requirements.txt`.
- New constants in `lib/cache.py`: `SEAM_OVERLAP_DAYS = 7`, `SEAM_SHIFT_TOL = 0.02`.
- Deviation rule: flag a ticker when `abs(median(delta/cached) - 1) > SEAM_SHIFT_TOL`.
- Do NOT change `lib/data.py:_fetch_prices` or its `auto_adjust=True` (that is the rejected Approach C).
- Do NOT add a persistent stale-cache warning banner. The only new user-visible surface is an `on_status(...)` message emitted when healing occurs.
- All tests run offline via a stateful mock fetcher; cache file paths are redirected to `tmp_path` with `monkeypatch`.

## File Structure

- `lib/cache.py` (modify) — add the two constants, the `_detect_basis_shifts` helper, the overlap widening in fetch cases 2 & 3, and the healing block in the merge loop of `get_prices_cached`.
- `tests/test_cache_splits.py` (create) — unit tests for the detector, plus integration + negative tests for the healing path.
- `requirements.txt` (modify) — add `pytest>=8.0`.

---

### Task 1: Detection helper + constants

**Files:**
- Modify: `lib/cache.py` (add constants near line 25; add `_detect_basis_shifts` helper next to `_merge` around line 200)
- Modify: `requirements.txt` (append `pytest>=8.0`)
- Test: `tests/test_cache_splits.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `cache.SEAM_OVERLAP_DAYS: int` (= 7)
  - `cache.SEAM_SHIFT_TOL: float` (= 0.02)
  - `cache._detect_basis_shifts(cached: pd.DataFrame, delta: pd.DataFrame, tol: float = SEAM_SHIFT_TOL) -> list[str]` — sorted list of tickers present in both frames whose median overlap ratio deviates from 1 by more than `tol`; tickers with no shared non-NaN day are omitted.

- [ ] **Step 1: Add pytest to requirements and install test deps**

Append one line to `requirements.txt`:

```
pytest>=8.0
```

Then install (pandas/pyarrow are already required by the project):

Run: `pip install -r requirements.txt`
Expected: pytest installs successfully; no errors.

- [ ] **Step 2: Write the failing unit tests**

Create `tests/test_cache_splits.py`:

```python
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
```

- [ ] **Step 3: Run the unit tests to verify they fail**

Run: `pytest tests/test_cache_splits.py -v`
Expected: FAIL — `AttributeError: module 'lib.cache' has no attribute '_detect_basis_shifts'`.

- [ ] **Step 4: Add the constants and the detector to `lib/cache.py`**

Add the two constants right after the existing `TAIL_FRESH_HOURS = 6` line (around line 27):

```python
SEAM_OVERLAP_DAYS = 7     # calendar days of overlap requested at each stitch seam
                          # (guarantees >= 3 trading days even across a holiday weekend)
SEAM_SHIFT_TOL = 0.02     # flag a ticker when |median(delta/cached) - 1| exceeds this
```

Add the helper immediately after `_merge` (after its `return` around line 203):

```python
def _detect_basis_shifts(base: pd.DataFrame, delta: pd.DataFrame,
                         tol: float = SEAM_SHIFT_TOL) -> list[str]:
    """Tickers whose adjustment basis differs between `base` (on-disk cache) and a
    freshly-fetched `delta`.

    Compares the two frames on the calendar days they share (the seam overlap).
    Because the same day is fetched twice, the ratio isolates the adjustment-basis
    change (a split / large-dividend re-base) and is immune to real market moves.
    Tickers with no shared non-NaN day cannot be checked and are omitted.
    """
    common_tickers = [t for t in delta.columns if t in base.columns]
    common_dates = base.index.intersection(delta.index)
    if common_dates.empty or not common_tickers:
        return []

    b = base.loc[common_dates, common_tickers]
    d = delta.loc[common_dates, common_tickers]
    shifted: list[str] = []
    for t in common_tickers:
        pair = pd.concat([b[t], d[t]], axis=1).dropna()
        pair = pair[pair.iloc[:, 0] != 0]
        if pair.empty:
            continue
        ratio = (pair.iloc[:, 1] / pair.iloc[:, 0]).median()
        if pd.notna(ratio) and abs(ratio - 1.0) > tol:
            shifted.append(t)
    return sorted(shifted)
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `pytest tests/test_cache_splits.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 6: Commit**

```bash
git add lib/cache.py requirements.txt tests/test_cache_splits.py
git commit -m "Add basis-shift detector for price-cache seam

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Overlap seams + healing in `get_prices_cached`

**Files:**
- Modify: `lib/cache.py:166-193` (fetch cases 2 & 3, and the merge loop of `get_prices_cached`)
- Test: `tests/test_cache_splits.py` (append integration + negative tests and their fixtures)

**Interfaces:**
- Consumes: `cache._detect_basis_shifts`, `cache.SEAM_OVERLAP_DAYS`, `cache.SEAM_SHIFT_TOL`, `cache._merge`, `cache._slice`, `cache._d` (all from Task 1 / existing).
- Produces: healed behavior in `get_prices_cached` — no new public symbols.

- [ ] **Step 1: Append the failing integration + negative tests**

Add to the top of `tests/test_cache_splits.py`, after the existing imports:

```python
import pytest
```

Append these fixtures and tests to the end of `tests/test_cache_splits.py`:

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_cache_splits.py::test_split_after_cache_is_healed tests/test_cache_splits.py::test_dividend_rerun_does_not_refetch -v`
Expected: `test_split_after_cache_is_healed` FAILS (the seam jump assertion `< 0.05` fails, and/or the heal-call assertion fails) because healing is not wired yet. (`test_dividend_rerun_does_not_refetch` may already pass, since no heal path exists — that is fine.)

- [ ] **Step 3: Widen the extension fetch windows to overlap the cache**

In `lib/cache.py`, replace fetch case 2 (currently `cache.py:167-169`):

```python
    # 2. Range extends earlier
    known_tickers = sorted(requested_set & cached_tickers)
    if requested[0] < cached_start and known_tickers:
        fetches.append((known_tickers, _d(requested[0]), _d(cached_start - timedelta(days=1))))
```

with (overlap forward into the cache by `SEAM_OVERLAP_DAYS`):

```python
    # 2. Range extends earlier (overlap into the cache so a basis shift is detectable)
    known_tickers = sorted(requested_set & cached_tickers)
    if requested[0] < cached_start and known_tickers:
        fetches.append((known_tickers, _d(requested[0]),
                        _d(cached_start + timedelta(days=SEAM_OVERLAP_DAYS))))
```

Then replace the later-extension branch of case 3 (currently `cache.py:173-175`):

```python
        if requested[1] > cached_end:
            fetches.append((known_tickers,
                            _d(cached_end + timedelta(days=1)), _d(requested[1])))
```

with (overlap back into the cache by `SEAM_OVERLAP_DAYS`):

```python
        if requested[1] > cached_end:
            fetches.append((known_tickers,
                            _d(cached_end - timedelta(days=SEAM_OVERLAP_DAYS)), _d(requested[1])))
```

Leave the tail-refetch `else` branch (`cache.py:176-180`) unchanged — it already overlaps the cache.

- [ ] **Step 4: Add the healing block to the merge loop**

In `lib/cache.py`, replace the merge loop and return (currently `cache.py:186-193`):

```python
    merged = cached
    for f_tickers, f_start, f_end in fetches:
        status(f"Fetching {len(f_tickers)} ticker(s), {f_start} to {f_end}")
        delta = fetcher(f_tickers, f_start, f_end)
        merged = _merge(merged, delta)

    _save(merged)
    return _slice(merged, *requested, tickers)
```

with:

```python
    merged = cached
    shifted: set[str] = set()
    for f_tickers, f_start, f_end in fetches:
        status(f"Fetching {len(f_tickers)} ticker(s), {f_start} to {f_end}")
        delta = fetcher(f_tickers, f_start, f_end)
        shifted.update(_detect_basis_shifts(cached, delta))
        merged = _merge(merged, delta)

    if shifted:
        names = ", ".join(sorted(shifted))
        status(f"Re-adjusted {len(shifted)} ticker(s) after detecting corporate "
               f"actions: {names}")
        full_start = min(requested[0], cached_start)
        full_end = max(requested[1], cached_end)
        fresh = fetcher(sorted(shifted), _d(full_start), _d(full_end))
        merged = _merge(fresh, merged.drop(columns=list(shifted), errors="ignore"))

    _save(merged)
    return _slice(merged, *requested, tickers)
```

- [ ] **Step 5: Run the full test file to verify everything passes**

Run: `pytest tests/test_cache_splits.py -v`
Expected: PASS — all six tests green (4 unit from Task 1 + 2 from Task 2).

- [ ] **Step 6: Commit**

```bash
git add lib/cache.py tests/test_cache_splits.py
git commit -m "Heal split-stitch artifacts by re-fetching re-based tickers

Overlap each stitch seam, detect adjustment-basis shifts at the seam,
and re-fetch the full history of affected tickers on one basis.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Two constants (`SEAM_OVERLAP_DAYS`, `SEAM_SHIFT_TOL`) → Task 1, Step 4. ✓
- `_detect_basis_shifts` (median overlap ratio, tol, no-overlap skip) → Task 1, Step 4; unit-tested Step 2. ✓
- Overlap at later- and earlier-extension seams; tail unchanged → Task 2, Step 3. ✓
- Healing block (detect vs original `cached`, `on_status` message, full re-fetch, whole-column replace via `_merge`) → Task 2, Step 4. ✓
- Missing-tickers case never falsely flagged (fetches full history, no overlap with cache) → unchanged behavior; not modified. ✓
- Tests: detector unit (split/reverse/dividend/no-overlap) → Task 1; integration artifact-healed + full-refetch-triggered, and negative dividend-no-refetch → Task 2. ✓
- `pytest>=8.0` in requirements → Task 1, Step 1. ✓
- No change to `_fetch_prices`/`auto_adjust`; no persistent banner → nothing in plan touches them. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows full code. ✓

**3. Type consistency:** `_detect_basis_shifts(base, delta, tol=SEAM_SHIFT_TOL) -> list[str]` defined in Task 1 and called with two positional args (defaulting `tol`) in Task 2. Constants referenced by the exact names defined. `_merge`/`_slice`/`_d` used with their existing signatures. `MockFetcher.calls` entries are `(tuple(sorted(tickers)), start, end)` and the assertions index `c[0]`/`c[1]` accordingly. ✓
