# Out-of-sample paper-trading ledger

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** nothing

## Problem

Every performance number on the dashboard is in-sample by construction:

- The universe comes from `data.get_sp500_tickers()`, which scrapes the *current* S&P 500
  membership list. Cohorts formed in 2021 are therefore ranked over 2026's index members, so
  names that dropped out in between never appear. `web/v1.jsx:630` admits this in the caveat
  block, and the KPI tiles then print +470% total vs SPY +89% anyway.
- `lookback`, `topN`, `rankLookback`, `skipMonths` and the hold set were all chosen by
  looking at that same window. The best-Sharpe sleeve (hold6m at 1.33 vs SPY 0.87) and the
  event-study result (7 of 12 horizons at |t| >= 2, strongest h=11 at t=4.34) are selected
  statistics, not forecasts.

Meanwhile the one genuinely out-of-sample observation the tool has produced is discarded on
every run. `analysis.live_rebalance` rebuilds the live row from scratch each time (the
2026-07-28 run reads 10 in / 10 out / 0 kept), so whatever it recommended in June is gone
from the UI. Nothing on disk remembers a recommendation either: `cache/` holds
`prices.parquet`, `prices_meta.json`, `tickers.json`, `sp500_meta.json`, and `cache.clear()`
deletes exactly those 4. July is precisely the month a track record should be forced to
carry (strategy -14.2% month-to-date vs SPY -1.2%, with the 2026-06 cohort at -27.2% on
GLW -43.9%, SNDK -43.8%, MRVL -36.5%), and there is nowhere to put it.

## Goal

After this ships, one click records the live rebalance target to an append-only local file,
and every later run marks the recorded cohorts to market from cached prices. A Ledger panel
shows realized out-of-sample equity for "what the tool told me to buy" against the benchmark
over the same dates, grouped so entries from different settings are never blended, with
per-row picks and realized per-pick returns. The next -14% month lands in the record
permanently instead of being re-fitted away, and the panel is the only place in the tool
whose numbers were not chosen with hindsight.

## Design

### Storage: user data, structurally outside the cache

New directory `./userdata/` (not `./data/`, which would collide conceptually with
`lib/data.py` and become an importable top-level namespace package) holding
`userdata/ledger.json`. This is a deliberate structural answer to the clear-cache question:
`cache.clear()` unlinks 4 named files under `CACHE_DIR`, so a ledger inside `cache/` would
survive today by accident only. Outside `CACHE_DIR` it cannot be reached by `clear()` at all,
now or after any future rewrite that wipes the directory. Both paths are CWD-relative, same
as `CACHE_DIR`, so the server keeps its existing "launch from the repo root" assumption. Add
`userdata/` to `.gitignore` (it is personal, and one entry per run would make noisy diffs);
the file is plain JSON, so a backup is a file copy.

New `lib/ledger.py`, I/O only, mirroring `lib/cache.py`'s JSON-cache pattern:

```python
DATA_DIR = Path("./userdata")
LEDGER_JSON = DATA_DIR / "ledger.json"
SCHEMA_VERSION = 1
SIGNAL_VERSION = 1        # bump when momentum_signal / current_target semantics change

class LedgerConflict(Exception): ...

def params_hash(params: dict) -> str
def load() -> list[dict]
def append(entry: dict) -> tuple[dict, bool]   # (entry, created)
```

`lib/cache._atomic_write` is promoted to public `cache.atomic_write` (with
`_atomic_write = atomic_write` kept as an alias so no existing call site or test moves), and
`ledger.append` writes through it: mkstemp in the same directory plus `os.replace`, so a
crash mid-write leaves the complete old ledger, never a truncated one. `append` must call
`DATA_DIR.mkdir(exist_ok=True)` first, exactly as `cache._save` calls
`CACHE_DIR.mkdir(exist_ok=True)`: `atomic_write` does `tempfile.mkstemp(dir=str(dest.parent))`
and raises `FileNotFoundError` on the very first append if `userdata/` does not exist yet.
`append` takes a module-level `threading.Lock` around load, mutate and write, because FastAPI
runs sync endpoints in a threadpool and two Record clicks could otherwise interleave a
read-modify-write.

File shape: `{"schema_version": 1, "entries": [...]}`, rewritten whole on each append. At
roughly 300 bytes per entry, 1000 entries is 300 KB, so whole-file rewrite is cheaper than
managing a JSONL tail. `load()` never raises: on `JSONDecodeError`/`OSError` it renames the
file to `ledger.corrupt-<utc-iso>.json` and returns `[]`. Append-only means never destroying
user data, so a corrupt file is moved aside, not overwritten, and there is no DELETE
endpoint (hand-editing the JSON is the escape hatch).

### The params hash: what makes an entry reproducible

```python
def params_hash(params: dict) -> str:
    payload = {
        "universe":     params["universe"],
        "topN":         int(params["topN"]),
        "rankLookback": int(params["rankLookback"]),
        "skipMonths":   int(params["skipMonths"]),
        "signal_version": SIGNAL_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
```

Those 4 fields plus `signal_version` are exactly the inputs to `analysis.current_target`
(which delegates to `top_performers_period` on `round(rankLookback * 30.4375)` calendar
days). Deliberate exclusions:

- `lookback` (years of history) does not change the target. `_run_backtest` pulls
  `int(lookback * 365.25) + 90` days, so even the shortest allowed pull (`lookback=1`, 455
  days) covers the widest possible ranking window (`rankLookback=12` plus `skipMonths=2`,
  `round(12 * 30.4375) + round(2 * 30.4375) = 426` days). The top-N set is therefore
  identical across the whole allowed `lookback` range, and including it in the hash would
  fragment one honest track record into several.
- `holds`, `horizon`, `riskFree` affect backtest reporting only, never the live pick set.
- `benchmark` is stored per entry as metadata but is not hashed: it changes what the ledger
  is *compared against*, not what was recommended, and the read path can re-price against any
  benchmark. Every row reports which benchmark it was recorded with.

`signal_version` is the escape valve for I3: if a later spec changes what `current_target`
means, bumping it splits the pre-change and post-change entries into separate groups instead
of silently chaining two different strategies into one equity curve.

### Entry schema

One entry per recorded target:

```jsonc
{
  "params_hash": "9f2c1b40a7d3",
  "signal_version": 1,
  "params": {"universe": "sp500", "topN": 10, "rankLookback": 1, "skipMonths": 0},
  "benchmark": "SPY",
  "as_of": "2026-07-27",          // last price bar the target was computed from
  "signal_end": "2026-07-27",     // end of the ranking window (< as_of when skipMonths > 0)
  "window_start": "2026-06-27",
  "days": 30,
  "recorded_at": "2026-07-28T14:03:11.442Z",
  "tickers": ["LLY", "GEV", ...],                 // signal order, highest first
  "entry_prices": {"LLY": 812.44, "GEV": 611.02},  // adjusted close at as_of, None if no bar
  "entry_unpriced": [],                            // ranked but no bar at as_of (see below)
  "benchmark_entry_price": 648.31,
  "universe_source": "wikipedia",                  // or stale_cache / price_cache_columns
  "note": null                                     // optional, <= 280 chars
}
```

`entry_prices` makes an entry self-contained: after a `cache.clear()` plus a
`lookback=1` re-run, the parquet no longer covers a 3-year-old `as_of` (today's cache spans
2021-04-28 to 2026-07-27 because of the 5-year default), and the stored price is the only way
to mark that segment at all.

### Marking to market

New pure function in `lib/analysis.py`:

```python
def ledger_marks(entries: list[dict], prices: pd.DataFrame,
                 benchmark: str | None = None,
                 risk_free_annual: float = 0.0) -> dict
```

`entries` are the entries of one `params_hash` (the caller groups; the function asserts a
single hash), sorted ascending by `as_of` internally. `prices` is a daily adjusted-close
frame including the benchmark column.

Segments: for entries `e_0 .. e_{m-1}`, segment `k` runs from bar `s_k` (last cached bar at
or before `as_of_k`) to bar `t_k = s_{k+1}` for `k < m-1`, and to the last cached bar `T` for
the open segment. Chaining at a shared bar assumes the old cohort is sold and the new one
bought at the same close, which is the same zero-cost turnover assumption the backtest makes.

Per ticker `i` in segment `k`: `r_ki = P(t_k, i) / P(s_k, i) - 1`. Segment return is the
plain mean `R_k = mean_i r_ki`, which is the correct return for equal dollars at `s_k` held
without intra-segment rebalancing (weights drift, exactly as they do in a real book). Do not
compound monthly means: that would model a rebalance the user never made. (`mean_i(ratio) - 1`
and `mean_i(ratio - 1)` are the same number by algebra, so agreeing to 1e-12 proves nothing.
The choice that matters is mean-of-ratios over the whole segment versus compounding the
segment's monthly equal-weight means, and those differ by exactly the rebalancing the user
did not do: on a 20-day 3-name live-cache slice the buy-and-hold segment return was 0.074755,
and a monthly-rebalanced chain over the same bars is a different number whenever the legs
disperse. The mean of ratios is what both the row-level return and the daily curve use.)

Equity: `E_0 = 1` at `s_0`; for each bar `u` in `(s_k, t_k]`,
`E(u) = E(s_k) * mean_i (P(u, i) / P(s_k, i))`. Benchmark curve
`B(u) = P(u, bench) / P(s_0, bench)`, on the same bar index, so the comparison is
date-for-date rather than against a full-sample mean (same discipline as
`2026-07-28-time-aligned-event-study-alpha`).

Price-basis and missing-data rules, all flagged rather than silent:

- **Both endpoints come from the current cached series**, never a mix of stored entry price
  and cached end price. `yfinance auto_adjust` rebases the whole adjusted-close history at
  each fetch (this is why `cache.SPLIT_JUMP_LO/HI` and `_heal_split_artifacts` exist), so a
  2:1 split between record and mark would otherwise print a spurious -50%.
- **Audit the stored price:** if `|P(s_k, i) / entry_prices[i] - 1| > 0.01`, the row carries
  `basis_shifted: true` and `basis_ratio: {ticker: ratio}`. The return stays the cached-basis
  one; the flag tells the UI to say "prices re-adjusted since recording (split or dividend),
  return recomputed on the current basis".
- **Cache no longer covers `s_k`:** fall back to `entry_prices[i]` and set
  `basis_unverified: true` on the row. This is the one path that *does* mix a stored entry
  price with a cached end price, contradicting the rule above, and that is exactly what the
  flag means: "start price is from the recording, end price is from the current cache, so any
  split or dividend re-adjustment since then is baked into this segment's return". The flag is
  load-bearing and must reach the UI as a per-row warning chip, not just sit in the JSON.
  A row can be `basis_unverified` for some legs and clean for others, so the field is
  `basis_unverified: bool` plus `basis_unverified_tickers: [t, ...]`.
- **Equity curve versus row totals when segments predate the cache.** The daily curve can only
  be drawn from bars that exist, so `equity` starts at the first cached bar at or after `s_0`,
  not at `s_0` itself. When any segment is `basis_unverified` the curve therefore covers less
  history than the row chain, and `equity["strategy"][-1]` will *not* equal
  `1 + summary["total_return"]`. `summary["total_return"]` is always the chain over all rows
  (`prod(1 + R_k) - 1`), which is the honest figure; `summary` additionally carries
  `equity_starts_at` and `equity_covers_all_segments: bool` so the panel can say "chart covers
  4 of 6 segments" instead of quietly showing a shorter curve than the table.
- **Ticker stops having bars mid-segment** (delisting, acquisition, symbol change): mark it
  at its last available bar, list it in `frozen_tickers`, and keep it in the equal-weight
  denominator. Dropping it would silently reweight the survivors upward, which is the exact
  survivorship bias the ledger exists to escape. Freezing approximates holding the
  liquidation proceeds in cash from that date, the closest honest treatment available given
  `_fetch_prices` keeps only adjusted Close and discards corporate actions.
- **No later bar exists at all:** `pick_returns[i] = None`, ticker listed in
  `unpriced_tickers`, `n_priced < len(tickers)` reported on the row. Never a fabricated 0.

Return value:

```python
{
  "rows": [                                # newest first
    {"as_of", "end_date", "is_open", "tickers", "pick_returns": {t: float|None},
     "return_pct", "bench_return_pct": float|None, "excess_pp": float|None,
     "n_priced", "n_frozen", "frozen_tickers", "unpriced_tickers",
     "basis_shifted", "basis_ratio", "basis_unverified", "basis_unverified_tickers",
     "days_held", "note"}
  ],
  "equity": {"dates": [...], "strategy": [...], "benchmark": [...]},
  "summary": {"first_as_of", "priced_through", "n_segments", "n_completed", "days_live",
              "total_return", "benchmark_total_return", "excess",
              "equity_starts_at", "equity_covers_all_segments",
              "annualized": None | {"CAGR", "vol", "Sharpe"},
              "annualized_blocked_reason": str | None}
}
```

`excess` is the simple difference of total returns (percentage points), not a ratio.
`annualized` stays `None` until `days_live >= 365` **and** `n_completed >= 12`, with a reason
string ("3 segments over 61 days: too short to annualize"); when it unblocks it takes exactly
3 keys from `perf_stats(daily_returns, periods_per_year=252, risk_free_annual=...)`, reusing
the existing Sharpe definition. Do not spread `**perf_stats(...)` into `annualized`:
`perf_stats` also returns `months`, which at `periods_per_year=252` holds a trading-day count
and would publish "months: 251". Note also that `perf_stats` returns `Sharpe: 0` (not `None`)
when `vol` is not `> 0`, so a flat curve reads as Sharpe 0. Annualizing 2 segments would
manufacture exactly the false confidence this feature exists to remove (I3).

### NaN must never reach the response

Starlette's `JSONResponse.render` calls `json.dumps(..., allow_nan=False)`, so a single `NaN`
or `inf` anywhere in the payload raises `ValueError: Out of range float values are not JSON
compliant` and the endpoint 500s. This has already bitten this repo, which is why
`server._safe_floats` exists. `ledger_marks` must therefore emit JSON-clean values at the
source, and **must not** be sanitized with `_safe_floats`: mapping NaN to 0.0 in an equity
curve prints a total wipeout that never happened, which is a fabricated number (I3). The rules:

- **Scalars** (`return_pct`, `bench_return_pct`, `excess_pp`, every `summary` number): `None`
  when undefined, never 0.0 and never NaN. `bench_return_pct` is `None` when the benchmark
  column is missing from `prices` or has no bar at `s_k` or `t_k`, and `excess_pp` is then
  `None` too rather than being computed against a hole.
- **`pick_returns` values:** `float` or `None`, as already specified.
- **`equity` arrays:** no `None` and no NaN, because a chart cannot plot a hole. A leg with an
  interior gap (a real bar at `s_k` and at `T` but a missing bar at some `u` in between,
  common around a halt or a bad Yahoo day) is carried forward at its last observed close for
  that bar. This is the freeze rule extended to interior gaps rather than a second policy, and
  it is the reason the curve must be built leg-by-leg with a forward fill inside the segment
  instead of by a bare `mean` over a row that may contain NaN.
- **Final guard:** the endpoint asserts the assembled dict is `json.dumps`-able with
  `allow_nan=False` before returning, so a missed case fails loudly in a test rather than as a
  500 in the browser.

### Reading prices without a network call

New read-only accessor in `lib/cache.py`:

```python
def load_prices(tickers: list[str] | None = None,
                start: str | None = None, end: str | None = None) -> pd.DataFrame | None
```

Pure disk: reads `PRICES_PARQUET` (optionally with `columns=` narrowing), returns `None` when
the cache is missing or unreadable, never writes and never fetches. Measured on the live
504-ticker by 1317-bar cache: 50 ms for a full read, 4 ms for a 12-column read, so the Ledger
panel can render synchronously from disk.

Two details about reusing `cache._slice`, because it cannot be called with the defaults as
written. Its signature is `_slice(prices, start: pd.Timestamp, end: pd.Timestamp, tickers:
list[str])` and it does `[t for t in tickers if t in prices.columns]` then
`prices.loc[start:end, cols]`, so `None` for `tickers` raises `TypeError` and `None` for
`start`/`end` raises on the `.loc` slice. `load_prices` resolves its own defaults first
(`tickers=None` to `list(frame.columns)`, `start=None` to the frame's min index, `end=None` to
its max) and only then delegates. Second, `_slice` ends with `.dropna(axis=1, how="all")`, so a
ticker that is in the parquet but has no bar inside the requested window comes back **absent**,
not all-NaN. Callers must not treat a missing column as proof the ticker was never cached;
`load_prices` therefore also returns nothing about coverage and callers use `cache.coverage()`
for that.

The parquet is a **single shared file across all 3 universes plus every benchmark ever
requested**: today's live cache holds 504 columns, and a `us_sector_etfs` run adds 11 more
alongside them. Any caller that ranks off this frame must narrow the columns to the intended
universe itself. `load_prices` does not know about universes and will happily hand back all of
them.

### Endpoints

`POST /api/ledger/record`

```python
class LedgerRecordParams(BaseModel):
    universe: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    rankLookback: int = Field(1, ge=1, le=12)
    skipMonths: int = Field(0, ge=0, le=2)
    benchmark: str = "SPY"
    note: Optional[str] = Field(None, max_length=280)
    runId: Optional[str] = None
```

The server recomputes the target itself: `cache.load_prices(universe_tickers + [benchmark])`
first, falling back to `data.get_prices(...)` (with `on_status`/`runId` progress) only when the
cache is empty or its `end` is more than 5 days stale, then
`analysis.current_target(constituents, top_n, rank_lookback, skip_months)`. The client never
supplies tickers or prices, so a stale browser tab (or anything else) cannot inject an
arbitrary cohort into the record. Because Record is clicked right after a run, the cache path
is the normal one and the click does not trigger a 500-ticker download.

Three preconditions on that frame, all of which I1 depends on and none of which
`current_target` checks for itself:

1. **Drop the benchmark and narrow to the universe.** `constituents` means
   `frame[[t for t in universe_tickers if t in frame.columns]]`, benchmark column removed.
   `_run_backtest` already does exactly this (`constituents = prices.drop(columns=[benchmark])`)
   before calling `live_rebalance`, and `_top_performers` never fetches the benchmark at all, so
   the leaderboard universe excludes it. Handing the benchmark-bearing frame straight to
   `current_target` would make SPY a ranking candidate that can enter the top N, and handing it
   the unnarrowed cache would rank an 11-name sector-ETF run over 504 S&P names. Either one
   breaks I1 outright, silently, and only on the recorded entry.
2. **Require enough history, and refuse rather than rank short.** `current_target` delegates to
   `top_performers_period`, which sets `start_ts = prices.loc[prices.index >= end_ts -
   days].index[0]` with **no reach-back tolerance** (unlike `_window_return_top_set`, which
   rejects a gap over 10 days). A frame that starts less than the ranking window before its
   last bar therefore yields a silently shorter window and a different top-N set, with no error
   and no flag. Before ranking, assert the frame spans at least
   `round(rankLookback * 30.4375) + round(skipMonths * 30.4375)` days ending at its last bar
   (426 days at the widest settings), and return HTTP 409 "price cache covers N days, this
   signal needs M: run a backtest first" when it does not. Never record a target computed off a
   collapsed window.
3. **Apply the same universe-size guard `/api/backtest` applies.** `_run_backtest` rejects
   `topN >= len(universe_tickers)` with HTTP 400. `LedgerRecordParams` allows `topN` up to 100
   against an 11-name universe, and `top_performers_period` silently returns however many names
   exist, so a `topN=50` record on `us_sector_etfs` would store an 11-ticker cohort under a hash
   that claims 50. Two entries under one hash could then hold different-length cohorts, which is
   precisely what the hash exists to prevent. Reuse the same 400 and the same message.

Response `{"created": bool, "entry": {...}, "displayed_as_of_matches": bool,
"target_matches_stored": bool}`. Idempotency is on `(as_of, params_hash)`: a second click on
the same bar and settings returns the existing entry with `created: false` and HTTP 200,
appending nothing. An `as_of` strictly older than the newest entry for that hash returns HTTP
409 (`LedgerConflict`), because a backdated recommendation cannot exist and would corrupt
segment chaining; the read path also sorts by `as_of` so an out-of-order file (hand-edited)
still marks correctly.

`target_matches_stored` exists because "same bar, same settings" does not guarantee "same
cohort". A `_heal_split_artifacts` re-fetch between the 2 clicks rebases a ticker's whole
adjusted-close history, which can reorder the trailing-return ranking on an unchanged `as_of`
bar. The recomputed target is compared against the stored `tickers` and the flag is false on
any difference, so the UI shows "already recorded for 2026-07-27, and the stored cohort differs
from the one on screen" rather than a bare "Recorded" next to a different list. The stored entry
is still not modified: it is what was recommended at the time, and rewriting it would make the
ledger re-fittable, which is the one thing it must never be.

One entry-price edge case: with `skipMonths > 0` the ranking window ends at `signal_end`, but
`entry_prices` are read at `as_of`, which is later. A name can rank in the top N on the
truncated window and then have no bar at `as_of` (delisted or halted inside the skip gap). Its
`entry_prices` value is `None`, it is listed in `entry_unpriced`, and the entry records that it
was unpriceable at recording time. Never a NaN (which would 500 the response, see above) and
never a copied `signal_end` price passed off as the `as_of` price.

`GET /api/ledger?hash=&benchmark=&refresh=0`

Loads the ledger, groups entries by `params_hash`, and calls `analysis.ledger_marks` per
group. Prices come from `cache.load_prices` on the union of recorded tickers plus each
group's benchmark. `refresh=1` is the only path that touches the network: it calls
`data.get_prices` for that ticker union (tens of names, not 500) and falls back to the
cache-only result with a warning if the fetch raises. Response:

```jsonc
{
  "entries_total": 7,
  "price_source": "cache" | "refreshed" | "cache (refresh failed)",
  "cache_coverage": { ... cache.coverage() ... },
  "groups": [{"params_hash", "params", "signal_version", "label": "S&P 500 · top 10 · 1m rank",
              "benchmark", "rows": [...], "equity": {...}, "summary": {...}}],
  "warnings": ["GLW has no bars after 2026-07-10; frozen at its last close"]
}
```

`GET /api/ledger` on an empty ledger returns `{"entries_total": 0, "groups": []}`, HTTP 200,
never a 404, so the panel can render its own empty state.

### UI

- `web/data-loader.js`: add `recordLedgerTarget(params)` and `fetchLedger(opts)`, both added
  to the existing `Object.assign(window, {...})` block.
- `web/charts.jsx`: new `LedgerPanel({ theme, accent, headingFont, benchmark, reloadKey })`,
  self-contained like `TopPerformersPanel` (owns its fetch, loads on mount, re-fetches when
  `reloadKey` changes). Layout: title "Out-of-sample ledger", a subtitle stating plainly that
  the backtest numbers above are fitted on a survivorship-biased window and this table is
  not; a config chip row when there is more than 1 group (selecting a group, never merging);
  a summary strip (total return, benchmark over the same dates, excess in pp, first `as_of`
  to `priced_through`, segment count); an `EquityCurveChart`; then one row per
  segment reusing the `RebalanceHistoryRow` interaction pattern (collapsed row expands to
  per-pick returns, worst pick emphasized). Frozen/unpriced/basis-shifted/basis-unverified rows
  carry a warning chip with the tooltip text. Register `LedgerPanel` in the end-of-file
  `Object.assign(window, {...})`, otherwise V1/V2 cannot see it.
  (`RebalanceHistoryRow` itself is not in that block, but `LedgerPanel` lives in the same file
  so it can call it directly.)
- The `EquityCurveChart` call has 3 hard requirements from its actual signature
  (`{series, months, height, logScale, filled, theme, accent, hoveredIdx, onHover, onSelect,
  ariaLabel}`):
  `series` is `[{name, values, color, dashed?, isBenchmark?}]`, so pass
  `[{name: 'Recorded targets', values: equity.strategy, color: accent.dark},
  {name: benchmark, values: equity.benchmark, color: theme.muted, isBenchmark: true}]`;
  `months` is a `Date[]`, not ISO strings, because the component calls `d.getFullYear()` for its
  x ticks, so map `equity.dates` through `new Date(...)`;
  and `logScale={false}`, because its log-mode y ticks are hardcoded to `[1..6]` and would
  collapse to a single tick on a curve near 1.0.
  **Guard `months.length < 2` and render the summary strip without the chart.** `xAt(i)` is
  `padL + (i / (months.length - 1)) * innerW`, so a 1-bar curve divides by 0 and every path
  coordinate becomes `NaN`. That is the ledger's day-0 state: the first Record click on the same
  day as the last cached bar produces exactly 1 point. Note also that the x axis labels year
  boundaries only, so a sub-1-year record shows a single x label. Acceptable, but the summary
  strip carries the date range in text so the chart is not the only place the dates appear.
- `RebalancePanel` gains optional props `onRecord`, `recordState`
  (`idle|saving|saved|error|exists`) and `recordedAsOf`. When `onRecord` is absent the panel
  renders byte-identically to today (I2). When present, a "Record this target" button sits
  next to "Download CSV" in the panel header (that is where the CSV button actually is, at
  `charts.jsx:953`, not in the live-row block), disabled unless `latest.is_live`, and shows
  "Recorded" / "Already recorded for 2026-07-27" / the error detail inline. The panel already
  early-returns `null` on an empty `rebalance`, so there is no state where the button renders
  without a target behind it.
- `web/v1.jsx` (after the `RebalancePanel` at line 609, before the caveats block) and
  `web/v2.jsx` (after the `RebalancePanel` at line 739): render `<LedgerPanel ... />` and own
  the `ledgerVersion` counter that `onRecord` bumps on success to force the panel to reload.
- `web/index.html`: `App` stores `ranParams` (a copy of `params` captured on each successful
  run, set alongside `setData(result)` in `onRun`) and adds it to the existing `dashProps`
  object, which is how V1/V2 receive everything else. Both dashboards currently pass the live
  `params` into `RebalancePanel`, so without threading `ranParams` separately the record call
  would pick up sidebar values the user typed and never executed. Bump the `?v=4` query on all
  4 asset tags (`data-loader.js`, `charts.jsx`, `v1.jsx`, `v2.jsx`, lines 30 to 33) to `?v=5`.

### Invariants

- **I1**: no new ranking code. Recording calls `analysis.current_target`, the same function
  `live_rebalance` and the Top Performers alignment guarantee already rest on, so a recorded
  entry is by definition the live target the leaderboard agrees with at matching settings.
- **I2**: 2 new endpoints and 1 new panel. `/api/backtest` and `/api/top-performers`
  responses are untouched; `cache.atomic_write` keeps its `_atomic_write` alias;
  `cache.load_prices` is additive; `RebalancePanel` without `onRecord` renders as today.
- **I3**: the whole point. No fabricated zeros (missing marks are `None`), frozen legs stay in
  the denominator, annualized stats suppressed under 12 completed segments, basis shifts
  surfaced rather than absorbed.
- **I4**: local JSON file, no broker, no orders. The panel reports and the CSV exports; it
  never sends anything anywhere.
- **I5**: `GET /api/ledger` is disk-only (`cache.load_prices`), so the panel renders airgapped
  and offline with no new runtime network dependency. `refresh=1` is opt-in, is scoped to the
  recorded ticker union, and degrades to the cache-only result plus a warning on failure.
  `POST /api/ledger/record` is **not** network-free, and the earlier drafts of this section were
  wrong to imply it was. Recording needs `universe_tickers` to narrow the ranking frame, which
  means `data.get_universe(universe)`. For `sp500` that is `get_sp500_tickers()`, which reads
  `cache.load_tickers()` and **returns `None` past the 24 hour `TICKERS_TTL_HOURS`**, at which
  point it does `requests.get` against Wikipedia. On an airgapped box, the first Record click
  more than 24 hours after the last successful scrape raises and the endpoint 502s, with the
  price cache sitting right there and perfectly usable. Required degradation path: catch the
  universe fetch failure, fall back to the stale `cache/tickers.json` contents (a new
  `cache.load_tickers(stale_ok=True)`, additive and defaulting to today's behaviour), and only
  if that is also absent fall back to the price cache's own column set minus the benchmark.
  Whichever fallback fired is recorded on the entry as `universe_source:
  "wikipedia" | "stale_cache" | "price_cache_columns"` and surfaced as a warning, because a
  universe list that is 3 months stale is a real (if small) drift in what the strategy ranks.
  The 3 curated ETF universes are hardcoded constants in `lib/universes.py` and need no network
  at all. Recording still prefers the price cache and only falls back to a price fetch when the
  cache is empty or over 5 days stale.

## Testing

New `tests/test_ledger.py` (module paths monkeypatched to `tmp_path`, the
`_point_cache_at` pattern from `tests/test_cache_corrupt.py:22`), plus additions to
`tests/test_analysis.py` using its existing `_daily_prices` fixture (line 224):

- `params_hash` equality: dicts differing only in `benchmark`, `lookback`, `holds`,
  `horizon` or `riskFree` hash identically; `topN` 10 to 11 changes the hash; bumping
  `SIGNAL_VERSION` changes it.
- Idempotency: appending the same `(as_of, params_hash)` twice leaves `len(load()) == 1` and
  the second call returns `created is False`; a different `as_of` appends; the same `as_of`
  under a different hash appends.
- Backdating: appending `as_of` earlier than the newest entry for that hash raises
  `LedgerConflict` and leaves the file unchanged byte-for-byte.
- Corrupt file: write `b"{not json"`, assert `load() == []`, a `ledger.corrupt-*.json` exists
  with the original bytes, and `LEDGER_JSON` is then writable.
- Clear-cache safety: write a ledger, call `cache.clear()`, assert `LEDGER_JSON` still
  exists (regression guard against a future `clear()` that wipes a directory).
  **This test must monkeypatch all 4 paths `clear()` touches, not the 3 that
  `test_cache_corrupt._point_cache_at` patches.** That helper sets `CACHE_DIR`,
  `PRICES_PARQUET` and `PRICES_META` only, while `cache.clear()` iterates
  `(PRICES_PARQUET, PRICES_META, TICKERS_JSON, SP500_META_JSON)`. No test in the repo calls
  `clear()` today, so nobody has hit this: borrowing `_point_cache_at` as-is and then calling
  `clear()` would unlink the developer's real `cache/tickers.json` and `cache/sp500_meta.json`,
  silently forcing a Wikipedia re-scrape on the next run and failing outright on an airgapped
  box. Either extend `_point_cache_at` to patch `TICKERS_JSON` and `SP500_META_JSON` (and update
  `test_cache_corrupt.py` with it) or patch them locally in this test.
- Atomic write: monkeypatch the writer to raise, assert the previous ledger bytes are intact
  and no `*.tmp` remains (mirrors `test_cache_corrupt.py`).
- `ledger_marks` single open segment on `_daily_prices([f"T{i}" for i in range(6)], seed=1)`
  (that fixture is 130 business days, 2026-01-01 to 2026-07-02, so a 30-bar-back entry still
  has 30 calendar days of ranking window behind it): build the entry from
  `current_target(prices.loc[:prices.index[-30]], top_n=3, rank_lookback=1)`, mark against the
  full frame, assert `return_pct` equals the hand-computed
  `mean(P_last / P_entry - 1)` within 1e-12 and `equity["strategy"][-1] == 1 + return_pct`.
- JSON-cleanliness: every `ledger_marks` test above additionally asserts
  `json.dumps(result, allow_nan=False)` succeeds, which is the assertion that actually pins the
  Starlette 500. At minimum: a NaN-benchmark case (`prices` without the benchmark column) must
  give `bench_return_pct is None` and still serialize; a mid-segment interior gap (NaN a single
  bar for 1 leg, real bars either side) must leave no NaN in `equity["strategy"]`.
- Single-bar guard: an entry whose `as_of` is the last bar of the frame yields
  `len(equity["dates"]) == 1`, `total_return == 0.0`, and a serializable payload, so the panel's
  `months.length < 2` branch has something to assert against.
- Ranking preconditions: a frame that still contains the benchmark column must not change the
  recorded cohort (record path narrows to `universe_tickers` first), and a frame spanning fewer
  days than `round(rankLookback * 30.4375) + round(skipMonths * 30.4375)` must be rejected rather
  than ranked on a collapsed window. This is the I1 regression guard and is the single most
  important test in this spec.
- Two-segment chaining: entries at bars -60 and -30, assert
  `summary["total_return"] == (1 + R0) * (1 + R1) - 1` with `R0`, `R1` hand-computed as
  mean-of-ratios, and that `equity["strategy"]` is continuous at the seam bar.
- Frozen leg: NaN out one target ticker's bars after the segment start; assert it appears in
  `frozen_tickers`, its `pick_returns` value equals the return to its last real bar, and
  `return_pct` still averages over all 3 legs (denominator unchanged).
- Unpriced leg: remove a recorded ticker's column entirely and give it no `entry_prices` end
  bar; assert `pick_returns[t] is None`, `t in unpriced_tickers`, `n_priced == 2`, and
  `return_pct` is the mean of the 2 priced legs (not a 0-padded 3-leg mean).
- Basis shift: halve one ticker's entire cached series (a 2:1 re-adjustment), assert the row
  reports `basis_shifted is True` with `basis_ratio[t] ~= 0.5` and that `return_pct` is
  unchanged versus the un-halved frame (proof the mark uses one consistent basis).
- Benchmark alignment: `bench_return_pct` equals `P_bench(t_k)/P_bench(s_k) - 1` per segment
  and `equity["benchmark"][-1]` equals `P_bench(T)/P_bench(s_0)`.
- Annualization gate: 2 segments over 61 days yields `summary["annualized"] is None` with a
  non-empty `annualized_blocked_reason`.
- Grouping: 2 hashes in one file produce 2 groups, and no group's rows contain a ticker list
  from the other entry (`ledger_marks` raises on mixed hashes).

## Non-goals

- Transaction costs, slippage, taxes and cash drag. Segments chain at a shared close with 0
  cost, the same assumption the backtest already makes and the caveat block already discloses.
- Sizing the recorded cohort in dollars or shares. That is `portfolio-tracker-sized-trades`;
  the ledger is unit-equity, equal-weight.
- Editing or deleting entries through the API. Append-only by design.
- Recording anything other than the live target (no backfilling synthetic history from the
  in-sample backtest, which would defeat the purpose).
- Significance testing of the OOS record. With 1 to 12 segments there is nothing to test;
  `event_study_summary`'s HAC machinery stays where it is.

## Risks / open questions

- **Freeze vs drop for delisted names.** The spec freezes at the last close and keeps the leg
  in the denominator. The alternative (excluding it, reporting "9 of 10 priced") reweights
  survivors upward. Freezing is the survivorship-honest choice but understates a true
  bankruptcy (real value near 0) and overstates nothing, so the ledger is mildly optimistic
  in a takeover and mildly pessimistic in a wipeout. Needs the user's sign-off; the flags make
  either interpretation visible per row.
- **One-bar chaining.** If the user records twice in the same week, a 3-day segment enters the
  chain and the equity curve gets a kink at each record. That is faithful (they did change
  the book) but makes the curve dependent on click cadence rather than a fixed monthly
  rhythm. Option not taken: only allow 1 entry per calendar month per hash.
- **Two records, one intent.** Recording on 2026-07-27 and again on 2026-07-31 with different
  targets means the July record depends on which day the user happened to click. A monthly
  reminder or an "expected next record date" hint would reduce this; out of scope here.
- **`userdata/` in `.gitignore`** means the ledger is not versioned. If the user wants the
  record under version control (a reasonable ask for a track record) the ignore line should be
  dropped instead. User's call.
- **Benchmark choice at read time** lets the same record be scored against SPY or ACWI. That
  is flexibility, but it also allows benchmark shopping after the fact. The rows always report
  the benchmark recorded with the entry, and the panel labels an override explicitly.
- **`basis_shifted` at a 1% threshold will fire on almost every aged row, so it risks becoming
  noise.** `auto_adjust=True` re-adjusts for dividends as well as splits, so a 3%-yield name
  drifts roughly 3% against its recorded price over a year purely from ex-dates, with no split
  anywhere. Any entry older than about 4 months will therefore light up on several legs. The
  return is still correct (both endpoints are on the current basis), so this is a labelling
  problem, not a math one, but a chip that is always on teaches the user to ignore it. Options
  not taken: raise the threshold to something split-shaped (roughly 8%), or split the flag into
  `basis_readjusted` (informational, expected) and `basis_split_suspect` (crossing
  `cache.SPLIT_JUMP_LO/HI`, worth reading). Needs the user's call on which they want to see.
- **Airgap fallback changes what was ranked.** The `universe_source` fallback chain in I5 keeps
  recording working offline, but a `stale_cache` or `price_cache_columns` universe is not the
  universe the dashboard ranked over the day it was scraped. It is recorded and warned rather
  than hidden, and the drift is small over weeks, but it is a real (if minor) crack in the claim
  that an entry is exactly reproducible from its hash.
- **`params_hash` does not cover the universe *membership*, only its key.** Two entries 6 months
  apart under hash `sp500 / top 10 / 1m` were ranked over 2 different S&P 500 constituent lists,
  because `get_sp500_tickers` always scrapes the current one. That is unavoidable without
  storing a point-in-time membership file, and the ledger is still out-of-sample forward in time
  (the thing the backtest is not), but it means the ledger escapes survivorship bias in its
  *returns* and not in its *selection universe*. The panel copy should not overclaim.
