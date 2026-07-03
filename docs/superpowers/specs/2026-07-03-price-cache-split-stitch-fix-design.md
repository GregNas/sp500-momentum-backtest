# Price-cache split-stitch artifact fix

Date: 2026-07-03
Component: `lib/cache.py` (with a small change to `requirements.txt` and a new `tests/`)

## Problem

`lib/cache.py:get_prices_cached` stitches a previously-cached price segment with a
freshly-fetched delta segment using `delta.combine_first(base)`. Because
`lib/data.py:_fetch_prices` calls yfinance with `auto_adjust=True`, **every** fetch
re-bases the entire adjusted-close history to that fetch's end date.

When a corporate action (stock split, reverse split, spin-off, or large special
dividend) occurs *after* the initial cache was written, the cached segment and the
delta segment end up on different adjustment bases. `combine_first` then fabricates a
spurious price jump at the stitch boundary.

Confirmed evidence (cache dated 2026-06-10, re-run 2026-07-03): DD showed a fake
+187.7% overnight jump (1:3 reverse split 2026-06-24), KLAC −89.9% (10:1 split
2026-06-12), CRWD −75.4% (4:1 split 2026-07-02). A clean full re-fetch of each name is
continuous (max daily move ~4–13%), proving these are stitching artifacts, not market
events. Impact: the Top Performers panel showed DD as the #1 S&P 500 performer at
+187.53% (pure artifact); a booked (traded) fake +200% in a momentum month could
distort returns far more than the ~1.4% CAGR shift seen in this particular cache.

### Which code paths are affected

The delta is computed in `get_prices_cached`. Three fetch cases stitch onto existing
cached data for **known** tickers and can therefore mismatch bases:

- **Later extension** (`requested_end > cached_end`, `cache.py:173`): fetches
  `[cached_end+1 … requested_end]` — currently **no overlap** with the cache. This is
  the path the reported 2026-06-10 → 2026-07-03 scenario actually hits.
- **Earlier extension** (`requested_start < cached_start`, `cache.py:168`): fetches
  `[requested_start … cached_start-1]` — also **no overlap**.
- **Tail refetch** (`cache.py:177`): fetches the last `TAIL_REFETCH_DAYS` — already
  overlaps the cache.

The **missing-tickers** case (`cache.py:161`) fetches full history for those tickers
on a single basis, so it never mismatches.

## Approach (chosen: A — overlap ratio check)

Make every stitch seam carry a few overlapping trading days, then detect a
basis change by comparing the two fetches on those shared days. The overlap compares
the **same calendar day fetched twice**, so the ratio isolates the adjustment-basis
change and is immune to real market moves. When a ticker's basis has shifted, re-fetch
that ticker's **full** history (one consistent basis) and replace its column entirely —
healing all of history, not just the seam.

Rejected alternatives:
- **B — seam jump detection:** threshold-fragile; a real ±25% earnings move is
  indistinguishable from a small split.
- **C — store raw prices + actions, re-adjust on read (`auto_adjust=False`):** most
  correct long-term but a substantial cache/adjustment-math rewrite; YAGNI here.

## Design

### New constants (`lib/cache.py`)

```python
SEAM_OVERLAP_DAYS = 7     # calendar days of overlap requested at each stitch seam
                          # (guarantees >= 3 trading days even across a holiday weekend)
SEAM_SHIFT_TOL    = 0.02  # flag a ticker when |median(delta/cached) - 1| exceeds this
```

Tolerance rationale: the overlap ratio is ~0.1 for a 10:1 split and ~3.0 for a 1:3
reverse split (far outside the band), ~0.97 for a 3% special dividend (flagged), and
~0.995 for a normal ~0.5% quarterly dividend (ignored — its seam artifact is negligible
for momentum, and flagging it would re-fetch most dividend-paying names on every stale
run, defeating the cache).

### 1. Guarantee overlap at extension seams

- Later extension: fetch `[cached_end − SEAM_OVERLAP_DAYS … requested_end]`.
- Earlier extension: fetch `[requested_start … cached_start + SEAM_OVERLAP_DAYS]`.
- Tail refetch: unchanged (already overlaps).

### 2. `_detect_basis_shifts(cached, delta, tol) -> list[str]`

Pure helper. For each ticker present in **both** frames:
- Align on the days where both frames are non-NaN (the overlap).
- If there is no such day, skip the ticker (documented limitation — cannot detect
  without an overlap).
- Compute `ratio = median(delta[overlap] / cached[overlap])`. Guard against a zero/NaN
  cached price.
- Flag the ticker when `abs(ratio - 1) > tol`.

Returns the sorted list of flagged tickers.

### 3. Heal inside the merge loop of `get_prices_cached`

```python
merged, shifted = cached, set()
for f_tickers, f_start, f_end in fetches:
    status(...)
    delta = fetcher(f_tickers, f_start, f_end)
    shifted |= set(_detect_basis_shifts(cached, delta, SEAM_SHIFT_TOL))
    merged = _merge(merged, delta)

if shifted:
    status(f"Re-adjusted {len(shifted)} ticker(s) after detecting corporate "
           f"actions: {', '.join(sorted(shifted))}")
    full_start = min(requested[0], cached_start)
    full_end   = max(requested[1], cached_end)
    fresh = fetcher(sorted(shifted), _d(full_start), _d(full_end))
    merged = _merge(fresh, merged.drop(columns=list(shifted), errors="ignore"))

_save(merged)
return _slice(merged, *requested, tickers)
```

Notes:
- Detection compares each delta against the **original** on-disk `cached` (the old
  basis), not the progressively-merged frame.
- Healing replaces the entire column for each shifted ticker via the existing `_merge`
  idiom (`fresh` carries only the shifted columns; the rest are dropped from `merged`
  before the combine).
- `fresh` spans `[full_start, full_end]`, which covers every date already in `merged`,
  so no dates are lost for the healed tickers.

### 4. Status/notification

The only new user-visible surface is the `on_status` message emitted when healing
occurs. No persistent stale-cache banner — the artifact is fixed, not merely flagged.

## Testing

`tests/test_cache_splits.py` (pytest). Add `pytest>=8.0` to `requirements.txt`.
No network — a stateful mock fetcher supplies old-/new-basis segments, and cache paths
are redirected to `tmp_path` via monkeypatch (`CACHE_DIR`, `PRICES_PARQUET`,
`PRICES_META`).

1. **Unit — `_detect_basis_shifts`:** flags a 10:1 split (ratio 0.1) and a 1:3 reverse
   split (ratio ~3.0); ignores a 0.5% dividend (ratio 0.995); returns nothing when the
   two frames share no non-NaN overlap day.
2. **Integration — the artifact:** seed the cache via a first `get_prices_cached` call
   (old basis). Then a stateful mock returns the new basis once the split date is in
   range. A second call over the extended window must:
   - return a series with **no seam jump** (equal to a clean full new-basis fetch for
     the split ticker), and
   - have triggered a **full re-fetch** for the split ticker (asserted via the mock's
     recorded calls).
3. **Negative — dividend:** a re-run whose only change is a ~0.5% dividend re-base does
   **not** trigger a full re-fetch (assert the mock's full-refetch call count is 0).

## Out of scope

- No change to `_fetch_prices` / `auto_adjust` (that is Approach C).
- No persistent stale-cache warning banner in the dashboard/CLI.
- No storage of raw prices or corporate-action records.
```
