# Approximate point-in-time S&P 500 membership

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** L
**Depends on:** nothing (lands cleanly before or after `additional-universes`; if that ships first, clone its per-universe JSON-cache helpers instead of the legacy `load_metadata`/`save_metadata` pair)

## Problem

This is the tool's headline methodological flaw, and it is currently handled with a sentence of
prose in `web/v2.jsx`'s caveats block ("Universe is the *current* S&P 500. Names that fell out
are silently absent") rather than with a number.

`lib/data.get_sp500_tickers()` returns today's 503 constituents. `_run_backtest` pulls prices
for exactly those names over the whole window, and every ranking consumer in
`lib/analysis.py` ranks across all of them at every formation month. So a name promoted into
the index in 2025 because it tripled is in the eligible set for the 2021 formation months too,
and momentum ranking buys precisely the names that tripled. The bias is not incidental to the
strategy, it is aligned with it.

Reconstructing membership from the "Selected changes to the list of S&P 500 components" table
on the same Wikipedia page `get_sp500_metadata()` already fetches (verified today: table index
1, 406 rows, dates parsing from 1976-07-01 through 2026-06-30) puts real numbers on it. Walking
that table backward from today's list gives, for today's 503 names:

| formation month | of today's 503, were members | today's names not yet members | then-members absent from price data |
|---|---|---|---|
| 2026-06 | 503 | 0 | 0 |
| 2024-06 | 468 | 35 | 36 |
| 2021-06 | 423 | **80 (16%)** | 83 |
| 2019-06 | 394 | 109 | 112 |
| 2016-06 | 354 | 149 | 152 |
| 2011-06 | 301 | 202 | 203 |
| 2001-06 | 286 | 217 | 225 |

At the **default 5-year lookback**, roughly 16% of the ranking universe in the oldest formation
months consists of names that were not in the index yet. Every headline number on the
2026-07-28 run is computed on that enriched universe: +470% total vs SPY +89%, best-Sharpe
sleeve hold6m 1.33 vs SPY 0.87, 7 of 12 event-study horizons at |t| >= 2 with h=11 at t=4.34.
The user has no way to know how much of that survives.

## Goal

An opt-in `pointInTime` flag that masks each formation month's ranking universe to
approximate members-as-of-that-date, so the user can run the identical backtest twice and read
the difference. Plus a diagnostics payload that states, in numbers, exactly how much of the
bias the mask removes and how much it provably does not.

## Design

### 1. Scrape and cache the change table

`lib/data.py`:

```python
def get_sp500_changes() -> list[dict]:
    """Newest-first [{date: 'YYYY-MM-DD', added: str|None, removed: str|None, reason: str}].
    24h disk cache. Raises on a fetch/parse failure; callers decide the fallback."""
```

Parsing, all verified against the live page today:

- Select the table whose columns are a `MultiIndex` containing both `("Added", "Ticker")` and
  `("Removed", "Ticker")`. Do **not** hardcode index 1. The first column label differs by page
  (`("Effective Date", "Effective Date")` on the S&P 500 page, `("Date", "Date")` on the S&P
  400 page), so take it positionally after the Added/Removed tuples confirm the table.
- `pd.to_datetime(col, errors="coerce", format="mixed")` parses all 406 rows ("June 30, 2026"
  style). Rows that fail to parse are dropped and counted.
- Ticker cells: strip, `"." -> "-"` (same Yahoo convention as the constituent list), and map
  `nan` / `""` / `"—"` / `"-"` to `None`. Verified counts: 19 rows have no `Added`, 23 have no
  `Removed` (spin-offs, mergers, share-class changes). Rows where both are `None` are dropped.
- One GET. There is **no** shared URL constant today: the string
  `"https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"` is duplicated as a local in
  `get_sp500_tickers()` (line 107) and `get_sp500_metadata()` (line 127), so this spec either
  lifts it to a module constant or, if `additional-universes` has landed, reads
  `_WIKI_INDEX_SOURCES["sp500"]["url"]`. If that spec has landed, prefer having
  `get_index_universe("sp500")` hand its already-parsed `read_html` table list to
  `_normalize_changes_table` and cache all 3 blobs from one response, which keeps a cold
  sp500 run at the 1 GET `additional-universes` won, instead of putting the second one back.

New JSON cache, cloning the `load_metadata` / `save_metadata` pattern in `lib/cache.py` exactly
(24h `TICKERS_TTL_HOURS`, `fetched_at` ISO timestamp, corrupt-JSON returns `None`), with one
improvement: write through `cache._atomic_write` rather than bare `write_text`.

```python
SP500_CHANGES_JSON = CACHE_DIR / "sp500_changes.json"   # or _changes_path() per additional-universes
def load_changes() -> list[dict] | None
def save_changes(changes: list[dict]) -> None
```

`cache.clear()` gains it to its unlink list.

### 2. Reconstruct snapshots (pure)

`lib/analysis.py`:

```python
def membership_snapshots(
    current: list[str],
    changes: list[dict],
    index: pd.DatetimeIndex,
) -> dict[pd.Timestamp, frozenset]:
    """Approximate index membership at each date in `index`, walking `changes`
    backward from today's `current` list."""
```

Algorithm: sort `index` descending and `changes` descending by date. Walk the changes; before
reversing a change effective at `D`, freeze the snapshot for every not-yet-frozen index date
`>= D`; then reverse it (`members.discard(added)`, `members.add(removed)`). After the loop,
freeze whatever index dates remain with the final set.

The `>= D` boundary is the load-bearing detail: a change effective 2026-06-22 is already
reflected in the 2026-06-30 monthly bar, so the month-end snapshot for M is today's set with
all changes effective **after** M reversed. Verified: this yields 503 members at 2026-06-30,
matching today's list, with the 2026-06-30 CAG removal correctly already applied.

```python
def membership_mask(
    index: pd.DatetimeIndex,
    columns,
    snapshots: dict[pd.Timestamp, frozenset],
) -> pd.DataFrame:
    """Bool frame, index x columns. Rows whose date has no snapshot are all True
    (fail open: an unreconstructable month must never silently empty the universe)."""
```

### 3. Mask the signal, not the returns

This is the most important decision in the spec.

```python
def momentum_signal(
    monthly_returns: pd.DataFrame,
    rank_lookback: int,
    skip_months: int = 0,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
```

The mask is applied last, after the rolling product and after the `.shift(skip_months)`:

```python
if membership is not None:
    signal = signal.where(membership.reindex(index=signal.index, columns=signal.columns,
                                            fill_value=True))
```

It **must** be `.where` (which returns a new frame), never an in-place
`signal[~mask] = np.nan`. At `rank_lookback == 1` the current code does `signal =
monthly_returns` (`analysis.py:31`) and returns that very object. Verified: `momentum_signal(r, 1)
is r` is `True`. In-place masking would therefore NaN the caller's `monthly_returns` and delete
realized forward returns inside `_run_backtest` for every consumer downstream, which is the
exact failure this whole section exists to prevent, only worse because it would only appear at
`rank_lookback == 1` (the default). `reindex(..., fill_value=True)` keeps the frame `bool`
dtype, so `.where` gets a clean boolean cond with no NaN third state.

Because all 5 ranking consumers (`event_study`, `momentum_backtest`, `recent_picks`,
`rebalance_schedule`, `cohort_sector_breakdown`) already source their signal from
`momentum_signal` and already call `.dropna()` on each formation row, a NaN'd signal cell makes
that name un-rankable in that month with **zero changes to any ranking loop**. One optional
parameter threaded through 5 call sites (plus `live_rebalance`, which forwards it to
`rebalance_schedule` only) reaches everything.

**Why not mask `monthly_returns` instead**, which looks simpler: it would also NaN out the
*forward* return of a name that left the index mid-hold. `monthly_returns.iloc[t][picks]
.dropna()` would then drop that name from the cohort mean, quietly deleting exactly the losses
that removal-from-the-index implies. That is a **new** bias in the favorable direction, worse
than the one being fixed, and it would be invisible. Masking the signal keeps every realized
forward return intact: a pick that gets dropped from the index next month still contributes its
actual (usually bad) return. Applying the mask after the `.shift` also means it is keyed on the
formation month, which is what "members as of the formation date" means. And it avoids a second
trap: NaN'ing a return cell would poison the whole `rolling(rank_lookback).apply(np.prod)`
window for that name, so a newly-promoted name would stay un-rankable for `rank_lookback` extra
months for reasons unrelated to membership.

`current_target` and `top_performers_period` are **deliberately unmasked**. They rank on daily
prices for today's constituent list, and today's list *is* the point-in-time correct membership
set for today. So `live_rebalance`'s live row still equals the Top Performers leaderboard at
matching settings even with `pointInTime=true`: only the history rows beneath it change. I1
holds without any special handling.

### 4. Diagnostics (pure)

```python
def membership_coverage(mask: pd.DataFrame, top_n: int) -> dict:
    """-> {months, first_month, min_members, mean_members,
           members_first_month, stripped_first_month,
           months_below_top_n: ['YYYY-MM', ...]}"""
```

`_run_backtest` maps `first_month`/`members_first_month`/`stripped_first_month` onto the
`window_start`/`members_at_window_start`/`stripped_at_window_start` keys of the `MEMBERSHIP`
payload below; the names differ deliberately (the function knows about a mask, not about a
request window) and the mapping is the sort of thing that rots, so keep it in one place.

One honesty note on those counts: `mask` is indexed by `monthly.columns`, i.e. the
**priced** subset of today's list after `_fetch_prices` / `_slice` drop all-NaN columns, not
the raw 503 names the table in Problem was computed from. So `members_at_window_start` +
`stripped_at_window_start` sums to `len(monthly.columns)`, which is at most 503 and usually a
few less. Report the pair, not a hardcoded 503.

`months_below_top_n` is the `top_n >= available-members` edge case made visible. Today every
ranking loop silently `continue`s when `len(ranking) < top_n`, so a masked month with too few
eligible names would just drop out of the ranking with no trace (see the per-ranker breakdown
below for what "drop out" costs in each case). On the real sp500
reconstruction this cannot bite at `topN <= 100` (the minimum eligible-and-priced count is 286
at 2001-06), but it can on a short custom window or a future 100-name universe mask, so the
guard ships regardless: `_run_backtest` emits an `on_status` message naming up to 6 skipped
months and the full list rides in the response. No exception is raised; a skipped month is an
honest outcome, silence is not.

`months_below_top_n` lists **formation** months, and the 3 rankers react to a thin month
differently, so the label matters:

- `momentum_backtest`'s `len(ranking) < top_n` check sits inside the per-sleeve loop
  (`analysis.py:190`), not the per-month loop. A thin formation month therefore does not remove
  a month from the equity curve: it removes 1 of the `K` sleeves from `np.mean(sleeve_returns)`
  for each of the next `K` months, so those months quietly become `(K-1)`-sleeve averages. Only
  when every active sleeve's formation month is thin does the month itself disappear. At
  `hold_period=6` one thin formation month perturbs 6 subsequent returns.
- `event_study` drops the whole cohort, which shows up honestly as a lower `n` per horizon.
- `rebalance_schedule` `continue`s, so the month vanishes from the trade list with no gap
  marker between the rows above and below it.

So the `warnings` copy must say "formation months excluded from ranking", never "months
missing from the curve", and the UI should not try to draw a gap in the equity curve from this
list.

### 5. server.py

- `BacktestParams.pointInTime: bool = False`.
- In `_run_backtest`, after `monthly` is built and before the event study:

```python
membership = None
membership_info = {"available": False, "reason": "not requested"}
if params.pointInTime:
    if params.universe != "sp500":
        membership_info = {"available": False,
                           "reason": f"point-in-time membership is only reconstructable "
                                     f"for sp500, not {params.universe!r}"}
        on_status(membership_info["reason"])
    else:
        try:
            changes = data.get_sp500_changes()
            snaps = analysis.membership_snapshots(universe_tickers, changes, monthly.index)
            membership = analysis.membership_mask(monthly.index, monthly.columns, snaps)
            membership_info = {...}          # see payload below
        except Exception as exc:
            membership_info = {"available": False, "reason": f"change table unavailable: {exc}"}
            on_status(f"Point-in-time membership unavailable ({exc}); running on the "
                      f"current constituent list.")
```

  then `membership=membership` on the calls to `event_study`, `momentum_backtest` (per hold),
  `recent_picks`, `cohort_sector_breakdown` and `live_rebalance`.

- New response key `MEMBERSHIP`:

```json
{"available": true,
 "source": "wikipedia: List of S&P 500 companies, Selected changes table",
 "fetched_at": "2026-07-28T...", "n_changes": 406,
 "earliest_change": "1976-07-01", "reliable_from": "2010-01",
 "window_start": "2021-04", "window_start_reliable": true,
 "members_at_window_start": 423, "stripped_at_window_start": 80,
 "min_members": 421, "months_below_top_n": [],
 "absent_historic_members": 83,
 "warnings": ["…"]}
```

  `absent_historic_members` is `len(snapshot_at_window_start - set(constituents.columns))`: the
  count of then-members the price frame cannot supply. Use `constituents`, not `prices`:
  `prices` still carries the benchmark column, so a benchmark that happens to be a former
  constituent would be miscounted as covered. That is the residual bias reported as a number
  instead of a hand-wave (83 at 2021-06, 225 at 2001-06).

- **Fallback when coverage is unavailable** (scrape fails, cache cold and offline, page layout
  changed): run **unmasked** with `MEMBERSHIP.available = false` and a populated `reason`, plus
  the `on_status` line. Chosen over a 502 to match the existing fail-soft precedents in this
  codebase (`_run_backtest`'s missing-benchmark-months warning at lines 168 to 173,
  `cache._heal_split_artifacts`'s "re-fetch failed; serving stitched data"). The requirement
  this creates is that the UI must never show a PIT badge on an unmasked run, which the
  `available` flag makes mechanical.

- `/api/universes` gains `"features": {"pointInTime": ["sp500"]}` so the client knows which
  universes support the toggle rather than hardcoding it. `additional-universes`
  (`customUniverse`) and `cross-universe-scan` (`scan`) add keys to the same dict, so whichever
  lands second adds to `out["features"]` instead of assigning a fresh literal over it.

- `momentum_backtest.py` (CLI) is left alone: its `momentum_signal` calls keep the default
  `membership=None`.

### 6. Frontend

- `PARAM_HELP.pointInTime`: "Mask each month's ranking universe to the S&P 500 members as of
  that month, reconstructed from Wikipedia's index-change table. Approximate: it removes
  names that were not members yet, but delisted names are absent from price data entirely and
  cannot be added back."
- New `ToggleField` component in `charts.jsx`, registered in the end-of-file
  `Object.assign(window, {...})` (a component missing from that list is invisible to v1/v2).
  There is no reusable checkbox today: the only checkboxes are 4 raw inline `<input
  type="checkbox">` in the chart toolbars (`v1.jsx` 428 and 435, `v2.jsx` 436 and 441) with no
  label/help plumbing, so `ToggleField` mirrors `NumField`'s
  `{id, label, help, value, onChange, theme, labelStyle}` contract and renders the same
  `HelpTip`, which is what makes `PARAM_HELP.pointInTime` show up like every other field.
- Rendered in both sidebars below the Skip-months field (`v1.jsx` line 119, `v2.jsx` line 140),
  only when `params.universe === 'sp500'` (gated on the `features.pointInTime` list).
- A `PIT` badge next to the PERF table heading and in the equity-chart legend, rendered **only**
  when `data.MEMBERSHIP?.available`. When `pointInTime` was requested but
  `available === false`, the badge reads `PIT unavailable` in muted/amber with the `reason` as
  its `title`.
- `v2.jsx` caveats block (line 858), `sp500` branch: when `MEMBERSHIP.available`, replace today's
  "Survivorship bias. Universe is the *current* S&P 500…" text with the quantified version
  built from the payload: "**Survivorship bias partly masked.** 80 of today's 503 names were
  not yet index members at the start of this window and are excluded from those months'
  rankings. 83 names that *were* members then are absent from price data entirely, so the mask
  removes the promoted-winner half of the bias, not the delisted-loser half. Reconstruction is
  approximate and thins out before 2010." When unavailable, today's text stays verbatim.
- `index.html`: `DEFAULT_PARAMS.pointInTime = false` (I2), asset `?v=` bumped.
- `paramIssues` needs no entry (a boolean has no range).

### Invariants

- **I1:** `current_target` and `top_performers_period` are untouched, so the live target still
  equals the leaderboard at matching settings with the flag on or off. The mask enters through
  the single shared `momentum_signal` builder, so all 5 ranking consumers get the identical
  eligible set. No per-panel ranking logic is forked.
- **I2:** `membership=None` is the default everywhere and `pointInTime` defaults to `false`.
  `momentum_signal` with `membership=None` is `assert_frame_equal`-identical to today, pinned
  by the existing `test_momentum_signal_skip_zero_matches_legacy_formula`.
- **I3:** this spec exists to serve I3. Specifically: the mask goes on the signal so no realized
  loss is deleted; unreconstructable months fail open rather than silently emptying;
  `months_below_top_n` surfaces months the rankers would otherwise skip in silence; the
  residual bias ships as a count (`absent_historic_members`) not an adjective; and a requested
  mask that could not be built is labelled `PIT unavailable` rather than quietly rendering as a
  normal run.
- **I4:** no build step, no new dependency, no execution. One more `requests.get` to a page the
  tool already fetches.
- **I5:** the change table is a new runtime network dependency: 1 additional GET to the same
  Wikipedia URL `get_sp500_metadata()` already hits, behind the same 24h disk cache. Offline
  degradation: a warm `cache/sp500_changes.json` runs masked fully offline; a cold cache on an
  airgapped site falls back to an unmasked run with `MEMBERSHIP.available = false` and a visible
  label. Nothing hangs and nothing errors. The file is small (406 rows, well under 100 KB) so a
  site can also seed it by hand from a machine with connectivity.

### Honest limits (stated in the spec, mirrored in the UI copy)

1. **The table is "Selected changes", not a complete corporate-actions history.** Row counts by
   era: 353 of the 406 rows are 2010 or later, about 21/year, which matches the real index
   turnover of roughly 20 to 25 changes/year. The 2000 to 2010 decade carries only 43 rows
   (about 4/year), and only 10 rows predate 2000. So the mask is materially useful at lookbacks
   up to about 16 years and close to worthless at the 25-year maximum. `reliable_from` is
   hardcoded to `2010-01` on that evidence, and a warning is emitted when the window starts
   earlier.
2. **Delisted names are absent from price data entirely.** The reconstruction names 855 distinct
   tickers across 1996 to 2026, of which 352 are not current members and none are in the price
   frame (the price pull is driven by today's list, and yfinance does not serve most delisted
   symbols). The mask can therefore only *remove* today's names from historic months. It fixes
   the promoted-winner component of survivorship bias and does nothing at all about the
   delisted-loser component.
3. **Renames and mergers are ambiguous.** A ticker change recorded only as a removal would make
   a still-present name look like a non-member. Crucially, that error has a **known direction**:
   because the mask can only ever remove names, every parsing error makes the eligible set
   *smaller* than the truth, which lowers measured performance. So the masked result is a
   **floor** and the unmasked result is a **ceiling**, and the truth is between them. That
   framing is the spec's core claim and belongs in the UI copy.
4. **Set-size drift.** Because 19 rows have no `Added` and 23 have no `Removed`, the
   reconstructed set size wanders. Measured band over 1996 to 2026: 503 to 512 members, against
   a true 500 companies / roughly 503 to 506 share lines. Small and reported (`min_members`),
   not silently clamped.

## Testing

All assertions are network-free and pure. Reuse the existing `_months(n)` and
`_monthly_frame(n_months, n_tickers, seed)` helpers in `tests/test_analysis.py`.

- **Boundary rule:** a 6-month index and one change effective inside month 3. Assert the month-3
  snapshot already reflects it and the month-2 snapshot does not (`>=` semantics), with the
  exact expected member sets for all 6 months.
- **Removal-only row** (`added is None`): walking back re-adds the removed ticker to the earlier
  snapshots and leaves the later ones unchanged.
- **Addition-only row** (`removed is None`): the added ticker is absent from every snapshot
  before its effective date and present at and after it.
- **Fail open:** with a `changes` list whose oldest date is after the start of `index`, the
  earliest index dates get the fully-reversed set, and `membership_mask` on an index date with
  no snapshot returns an all-True row rather than all-False.
- **I2 pin:** `momentum_signal(r, 6, membership=None)` is `assert_frame_equal`-identical to
  `momentum_signal(r, 6)` and to the legacy `(1 + r).rolling(6).apply(np.prod) - 1`, extending
  `test_momentum_signal_skip_zero_matches_legacy_formula`.
- **Mask placement:** on `_monthly_frame()`, a mask that is False only at `("2000-06-30", "T0")`
  makes exactly that one signal cell NaN and leaves every other cell equal to the unmasked
  signal.
- **Mask applies after the shift:** with `skip_months=1` and a mask False at row `m` for `T0`,
  the NaN lands at row `m` (the formation month), not at `m - 1`.
- **Signal masked, returns not** (the I3 regression): on `_monthly_frame(n_months=18,
  n_tickers=8)`, mask out the 3 highest-signal tickers for one formation month and assert
  `rebalance_schedule(..., top_n=3, membership=mask)` picks that month equal the top 3 of the
  remaining 5, **and** that the row's `return_pct` equals the mean of those picks' unmasked
  forward returns from the original frame.
- **Mid-hold removal keeps the loss** (the bias-direction test): build a frame where `T0` is a
  pick at month `m`, its membership ends at `m + 1`, and its month `m + 1` return is `-0.5`.
  Assert `recent_picks(..., membership=mask)` still reports `pick_returns["T0"] == -0.5` and
  that `return_pct` includes it. This is the assertion that fails if anyone ever "simplifies"
  the design by masking `monthly_returns`.
- **Coverage:** a mask leaving 2 eligible names in one month with `top_n=3` puts exactly that
  month in `months_below_top_n`, and `min_members == 2`.
- **Every masked pick is eligible:** across a full `rebalance_schedule` and `recent_picks` run
  on a random mask, assert no returned pick is ever outside its formation month's eligible set.
  Cheap, and it is the property that actually matters. It cannot be written directly against
  `momentum_backtest`, which returns only a return `Series` and never exposes its picks; the
  equivalent there is a **dominant-masked-ticker** test: give `T0` a +500% return every month,
  mask it in every row, and assert
  `momentum_backtest(r, ..., membership=mask)` is `assert_series_equal`-identical to
  `momentum_backtest(r.drop(columns=["T0"]), ...)`. If the mask leaked, `T0` would win every
  sleeve and the 2 series could not match.
- **Index alignment:** `membership_mask` given `columns` that include a ticker absent from every
  snapshot returns False for it in every row with a snapshot, and the ranking functions handle
  an entirely-masked column without raising.
- **Parser:** `get_sp500_changes`'s pure normalization step (extracted as
  `_normalize_changes_table(df)` so it is testable without network) given a hand-built frame
  with the real MultiIndex columns `[("Effective Date","Effective Date"), ("Added","Ticker"),
  ("Added","Security"), ("Removed","Ticker"), ("Removed","Security"), ("Reason","Reason")]`
  and rows containing `NaN`, `"—"`, `"June 30, 2026"` and `"MOG.A"` yields the expected dicts
  with `None` for blanks, `"MOG-A"` for the dotted ticker, and both-blank rows dropped.

## Non-goals

- **Point-in-time membership for `nasdaq100` or `sp400`.** Both pages carry the same change
  table shape (618 rows on the S&P 400 page, 227 on the Nasdaq-100 list page), so this is a
  natural follow-up once the sp500 path proves out, but the reconstruction quality has not been
  measured for either.
- **Resurrecting delisted price history.** That needs a paid point-in-time dataset (CRSP,
  Norgate, Sharadar). It also conflicts with I5: none of them mirror cleanly into an airgapped
  site, and it would replace a 100 KB JSON cache with a licensed database.
- **Running masked and unmasked in one request** to show a side-by-side delta. That doubles
  every backtest's runtime; the user toggles and compares two runs instead. The
  `stripped_at_window_start` / `absent_historic_members` numbers give the magnitude without the
  second run.
- **Masking the daily leaderboard, `top_performers_period` or `current_target`.** Today's list
  is correct for today, by construction.
- **Index weights.** The strategy is equal-weight; membership is a set, not a weighting.
- **Parsing the `Reason` column for corporate-action semantics** (spin-off vs merger vs ticker
  change). See open questions.

## Risks / open questions

- **Should `pointInTime` default to `true` for sp500 once it exists?** I2 forces `false` for
  this change, but the honest default is arguably `true`, with the unmasked run as the opt-in
  "upper bound" view. This is a product call and it changes every number the user has been
  looking at, so it is explicitly the user's.
- **Should a pre-2010 window be blocked rather than warned?** Chosen: warn (`reliable_from`
  plus a `warnings` entry plus UI copy), because a partial mask is still strictly better than
  none and blocking would make the 25-year lookback silently uncomparable to the 5-year one.
  A hard error at, say, `window_start < 2005-01` is defensible if the user would rather not see
  a number they cannot trust at all.
- **`Reason`-column parsing.** A row whose reason is a ticker change should be a swap, not an
  add plus a remove. Naive add/remove is chosen because the parsing error direction is
  conservative (see Honest limits 3). Parsing `Reason` for `"ticker change"` / `"name change"`
  and pairing those rows would tighten the mask, at the cost of regex-matching free text that
  Wikipedia editors write inconsistently. Worth it only if the user finds the floor/ceiling gap
  implausibly wide.
- **The floor/ceiling framing is the spec's main claim and it is only *mostly* true.** It holds
  for missing or mis-parsed *removals*. A spurious `Added` row for a name that was in fact
  already a member would also over-remove (still conservative), but a *missing* `Added` row for
  a name promoted mid-window leaves it eligible too early, which is optimistic. Given 19
  blank-`Added` rows out of 406, that channel exists and is small. The spec should not claim a
  strict bound, only a strong tendency.
- **The masked result may be uncomfortable.** If the +470% / Sharpe 1.33 headline drops
  substantially, the honest reading is that the unmasked figure was never the real number. Worth
  agreeing on that before the flag ships, rather than after.
- **The live rebalance row is unmasked while the row it diffs against is masked.**
  `live_rebalance` keeps `current_target` unmasked (correct: today's list is today's
  membership) but forwards `membership` to `rebalance_schedule`, so `buys`/`sells`/`holds` on
  the live row compare an unmasked target against `completed[0]["picks"]`, which was ranked on a
  masked universe. In practice the masked set for the most recent month-end is today's list
  minus the handful of changes effective in the past month, so the diff is all but identical;
  it is only ever wrong about a name added in the last month or 2. Recorded because it is the
  one place where 2 differently-masked sets meet, and because the alternative (masking the live
  row) would break I1.
- **Wikipedia layout drift** breaks the parse. Mitigated by selecting the table on its
  `("Added","Ticker")` / `("Removed","Ticker")` column signature and by the fail-soft path, so
  the failure mode is a labelled unmasked run, not a broken dashboard.
- **`reliable_from = 2010-01` is a judgement call** derived from row density (353 of 406 rows
  since 2010, about 21/year vs a true 20 to 25/year). It is not validated against an
  authoritative membership history, because the tool has none.
