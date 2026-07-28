# Parameter-sweep heatmap

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** turnover-transaction-costs (hard, see below). Only the *net metric columns* are
soft; the compute path is not.

The earlier "soft dependency" framing was wrong and would have produced a fork. The whole
optimisation here is hoisting `momentum_signal` out of the cell loop, and today's
`momentum_backtest` computes the signal internally from `(monthly_returns, rank_lookback,
skip_months)` with no way to inject it. So a sweep built on today's API either calls
`momentum_signal` 192 times (84s at 5 years, the thing this spec exists to avoid) or
re-implements the sleeve loop in `parameter_sweep`, which forks the ranking logic I1 requires to
stay shared and reduces "every cell equals a single backtest" from a consequence to a
coincidence. `turnover-transaction-costs` now specifies the seam
(`momentum_backtest_detail(..., signal=None)`) precisely so this spec can consume it. If that
spec slips, this one either slips with it or lands the seam itself; it does not work around it.

## Problem

The dashboard evaluates exactly one point in parameter space. `DEFAULT_PARAMS` in
`web/index.html` fixes `topN=10`, `rankLookback=1`, `skipMonths=0`, `holds=[1,3,6]`, and
`analysis.live_rebalance` turns that single cell into the actual monthly buy list. Nothing tells
the user whether that cell sits on a broad plateau or on an isolated spike, which is the
question that decides whether the live list is worth acting on at all.

The current run gives several reasons to doubt it is a plateau:

- Two adjacent hold periods differ by 0.15 of Sharpe (hold6 1.33 vs hold1 1.18, against SPY
  0.87). A single parameter step moves the headline metric by 17%.
- 7 of 12 event-study horizons clear |t| >= 2, and the strongest is h=11 at t=4.34. The
  significance is concentrated at horizons the default 1-month-hold sleeve never harvests,
  which is exactly the pattern you would expect if the short-horizon configuration were the
  weak part of the surface.
- The 2026-06 cohort returned -27.2% (GLW -43.9%, SNDK -43.8%, MRVL -36.5%) and the strategy is
  -14.2% MTD against SPY -1.2%. Single-cell noise of that size makes a single-cell metric a
  poor basis for trusting anything.

Answering "is top10 / rank1 robust" today means editing the sidebar and pressing Run once per
cell. A single 5-year backtest takes ~13s of compute on the warm cache, so a 192-cell scan by
hand is roughly 40 minutes of clicking.

Running that grid offline once (cached 5-year S&P frame, 10 bps costs) shows the sweep would
change the user's view immediately. The default cell (L=1, N=10, h=1) scores 1.12 net Sharpe.
The grid maximum is L=12, N=5, h=1 at 1.99, and the 8 strongest cells are all N=5 with L in
{5, 7, 10, 11, 12}: a ridge along "few names, long lookback", not an isolated spike. The
default configuration is not on it. That is a conclusion the tool cannot currently reach, and
it is also exactly the result that most needs an overfitting caveat attached to it.

## Goal

One request returns Sharpe, CAGR and max drawdown (gross and net) for every cell of a
`rankLookback` x `topN` x `hold` grid, computed off the monthly matrix already derived from the
cached parquet, and both dashboards render it as a heatmap with the current configuration
marked, click-to-load on any cell, and a permanent multiple-testing caveat plus a
neighbour-robustness readout so a plateau is visually distinguishable from a lucky spike.

## Design

### Endpoint

```python
class SweepParams(BaseModel):
    universe: UniverseKey = "sp500"
    lookback: int = Field(5, ge=1, le=25)
    rankLookbacks: List[int] = Field(default_factory=lambda: list(range(1, 13)))
    topNs: List[int]         = Field(default_factory=lambda: [5, 10, 20, 50])
    holds: List[int]         = Field(default_factory=lambda: [1, 3, 6, 12])
    skipMonths: int = Field(0, ge=0, le=2)
    benchmark: str = "SPY"
    riskFree: float = Field(0.0, ge=0.0, le=0.2)
    costBps: float = Field(10.0, ge=0.0, le=200.0)
    runId: Optional[str] = None
```

`POST /api/sweep`, rejecting bad input with the same `HTTPException(400, detail=...)` pattern
`_run_backtest` uses for `topN >= universe size`, naming the requested cell count and the cap.
At the measured cost below, 400 cells bounds the endpoint at roughly 18s on a 5-year window and
roughly 100s on a 25-year one.

Be precise about what is new here, because "validated the same way `_run_backtest` validates" is
not true of list contents. `BacktestParams.holds` is a bare `List[int]` with **no** per-entry
bound today, and `_run_backtest`'s only list check is `if not params.holds: raise
HTTPException(400, "At least one hold period is required.")`. So the per-entry bounds below are
new validation, hand-rolled in the endpoint body or expressed as
`List[Annotated[int, Field(ge=1, le=12)]]`, and the axis bounds are borrowed from the *scalar*
fields (`rankLookback` 1 to 12, `topN` 1 to 100) plus `horizon`'s 1 to 24 as the precedent for a
hold ceiling that does not otherwise exist:

- each `rankLookbacks` entry 1 to 12, each `topNs` entry 1 to 100, each `holds` entry 1 to 24
- none of the three lists empty, mirroring the `holds` message above, because an empty axis
  yields 0 cells and then `BEST` has nothing to report
- each list de-duplicated and sorted before use, because `topNs=[10, 10]` silently doubles a
  grid column and puts two cells in one heatmap square
- `len(rankLookbacks) * len(topNs) * len(holds) <= 400` computed **after** dedup and after the
  universe-size drop below, so the cap counts cells that will actually run

`topNs` entries at or above the universe size are dropped, not fatal: `us_sector_etfs` has 11
names so 20 and 50 are impossible, `global_etfs` has 37 so 50 is. Dropped values come back in
`SKIPPED` with a reason instead of 400-ing an otherwise valid sweep.

The pure function applies the same `>=` rule against `monthly_returns.shape[1]`, which is the
number of constituents that actually returned data (503, not `len(universe_tickers)`), because
that is all it can see. Two consequences: the `SKIPPED` reason string is phrased against the
frame ("exceeds 37 tickers with data"), and the server may additionally drop a value the frame
would have allowed, since `_run_backtest`'s check is against the universe list. Keep both drops
and merge their reasons into one `SKIPPED` list rather than letting the two rules disagree
silently.

`GET /api/universes` `ranges` gains nothing new: the sweep reuses `rankLookback` and `topN`
bounds already published there.

### Compute

```python
analysis.parameter_sweep(monthly_returns, rank_lookbacks, top_ns, holds,
                         skip_months=0, cost_bps=0.0, risk_free_annual=0.0,
                         on_progress=None) -> tuple[list[dict], list[dict]]
    # -> (cells, skipped)

analysis.sweep_best(cells, metric="sharpe_net") -> dict | None
    # -> {by, rank_lookback, top_n, hold, value, neighbour_median} over eligible cells only
```

Loop order is `rank_lookback` outer, then `top_n`, then `hold`, which is what makes the grid
cheap:

- `momentum_signal(monthly_returns, L, skip_months)` is computed once per distinct `L` and passed
  down through the `signal=` seam, so 12 calls instead of 192. This is the dominant saving: the
  signal is `(1 + r).rolling(L).apply(np.prod) - 1`, measured at 0.35 to 0.39s per call on the
  503-column frame, so calling it per cell costs 67s of the 84s a naive 192-cell loop takes.
  (At `L == 1` `momentum_signal` returns the input frame itself rather than a copy, so the
  hoisted value must be treated as read-only.)
- Per `(L, top_n)` the top-N pick index is cached for every formation month (0.5s for all 48
  combinations at 5 years), so the `hold` axis is nearly free. The cache must reproduce
  `momentum_backtest`'s selection exactly, including the skip: `ranking = signal.iloc[i].dropna()`,
  then `None` when `len(ranking) < top_n`, else `ranking.nlargest(top_n).index`. A cache that
  returns a short pick list instead of `None` for a thin month changes which sleeves contribute
  and breaks the per-cell agreement test.
- Each cell then calls `momentum_backtest_detail(..., signal=hoisted)` (not a private copy of its
  sleeve loop) and `perf_stats` twice (gross, net).

**Guard the degenerate cells or the endpoint 500s.** Verified against the real `perf_stats`: a
0-month strategy series returns `vol=NaN` and `max_drawdown=NaN`, and a 1-month series returns
`vol=NaN`; `json.dumps(..., allow_nan=False)` raises on both, which is exactly the encoder FastAPI
uses and exactly the failure mode this repo has already hit once. A sweep walks
`rank_lookbacks` up to 12 on whatever window the user picked, so `lookback=1` (about 12 monthly
rows) puts several cells at 0 or 1 months on the *default* grid. Therefore:

- cells with `months == 0` go into `skipped` with `reason="no strategy months at rank_lookback=L"`
  and never appear in `cells`
- every remaining numeric field is coerced NaN to `None` before it leaves `parameter_sweep`
  (`None` serializes as `null`, unlike the `_safe_floats` NaN-to-0.0 rule, because a 1-month
  cell's volatility genuinely is unknown and 0.0 would be a fabricated number, which I3 forbids)
- a cell with any `None` metric is marked `eligible=False` alongside the short-history hatching,
  so it is never ranked
- the UI renders a `null` metric as an empty hatched square, not as a 0.0-coloured one

Measured wall clock on this machine, real cached S&P frame (503 constituents):

| window | monthly rows | signals | 192 cells (incl. turnover, net, 2x perf_stats) | total |
|--------|--------------|---------|------------------------------------------------|-------|
| 5y (default) | 63  | 4.3s  | 8.7s (45 ms/cell)   | 13.1s |
| 25y (synthetic 500-col frame) | 303 | 21.7s | 46.5s (242 ms/cell) | 68.2s |

For comparison, 192 calls to today's `momentum_backtest` unchanged: 84s at 5 years, ~347s
extrapolated at 25 years. Hoisting the signal is the whole optimisation and it is enough.

**Deliberately not vectorizing `momentum_signal`.** A log-space form
(`np.expm1(np.log1p(r).rolling(L).sum())`) is 0.0035s vs 0.39s, a 110x speedup, and it would cut
the 25-year signal term from 21.7s to under 0.1s. It also deviates from the current
`np.prod` result by up to 1.4e-14, which breaks
`test_momentum_signal_skip_zero_matches_legacy_formula`'s exact `assert_frame_equal` and would
move the last digits of every existing number in the app (I2). It gets its own spec if 25-year
sweeps become routine; this spec does not touch the signal's math.

Progress: `on_progress` is called once per completed `rank_lookback` slice with
`"sweep: rankLookback 4/12 (64/192 cells)"`, wired into the same `on_status` closure
`_run_backtest` uses, so `PROGRESS[runId]` and the existing `pollProgress` in
`web/data-loader.js` surface it with no new client polling machinery.

Data: the endpoint calls `data.get_prices` over the same window `_run_backtest` uses
(`end - (lookback * 365.25 + 90) days`) with the same `universe_tickers + [benchmark]` list, then
drops the benchmark column before `to_monthly_returns`, exactly as `_run_backtest` does. It also
needs `_run_backtest`'s "benchmark returned no data from Yahoo" 400 guard, since `BENCHMARK`
anchors the colour scale and a missing column would otherwise be a KeyError 500. The result is a
cache slice whenever the user has already run a backtest (the normal case, since the sweep is
reached from a rendered dashboard). A cold cache means the same 30 to 60s download the backtest
already does, reported through the same status messages. No new data source, no new fetch
pattern.

### Cell shape and response

Real values from the offline run, so the contract is not illustrative:

```json
{
  "CELLS": [{"rank_lookback": 1, "top_n": 10, "hold": 6, "months": 62, "eligible": true,
             "sharpe": 1.3272, "cagr": 0.4007, "max_dd": -0.2454,
             "total_return": 4.7029, "vol": 0.2879,
             "turnover_ann": 2.1751, "cost_drag_ann": 0.0045,
             "sharpe_net": 1.3116, "cagr_net": 0.3946, "max_dd_net": -0.2475}],
  "GRID": {"rankLookbacks": [1, ...], "topNs": [5, 10, 20, 50], "holds": [1, 3, 6, 12]},
  "BENCHMARK": {"strategy": "SPY", "sharpe": 0.8655, "cagr": 0.1307, "max_dd": -0.2393,
                "months": 61},
  "SKIPPED": [{"top_n": 50, "reason": "exceeds 37 tickers with data"},
              {"rank_lookback": 12, "reason": "no strategy months at rank_lookback=12"}],
  "BEST": {"by": "sharpe_net", "rank_lookback": 12, "top_n": 5, "hold": 1,
           "value": 1.991, "neighbour_median": 1.685, "neighbour_count": 3},
  "COST_BPS": 10.0,
  "UNIVERSE_SIZE": 503, "UNIVERSE_KEY": "sp500", "UNIVERSE_LABEL": "...",
  "elapsed_s": 13.1, "status_messages": ["Cache hit - skipped download", "..."]
}
```

`CELLS` is ordered by `(rank_lookback, top_n, hold)`. The `_net` fields are omitted (and the UI
toggle hidden) when `costBps` is 0. `months` is carried per cell because a large `rankLookback` on
a short window leaves fewer strategy months, and a cell computed on 30 months is not comparable to
one computed on 62.

`BEST` and the neighbour median come from `analysis.sweep_best(cells, metric)`, not from ad-hoc
code in `server.sweep`. That placement is not cosmetic: the eligibility rule, the hatching
threshold and the neighbour median are the honesty machinery of this feature, the repo has **no
server tests at all**, and `tests/test_analysis.py` can only assert on what the pure layer
returns. `server.sweep` composes `parameter_sweep` + `sweep_best` + the benchmark row and does no
selection of its own.

**Pin the benchmark window.** `BENCHMARK` anchors the entire colour scale, and cells have
different month counts, so "SPY's Sharpe" is ambiguous until the alignment is stated. Definition:
`perf_stats(benchmark_monthly.reindex(idx).fillna(0.0), risk_free_annual=riskFree)` where `idx` is
the index of the longest eligible cell, plus a `BENCHMARK.months` field so the payload says which
window it was measured over. The consequence has to be admitted rather than hidden: a cell
computed on 50 months is being colour-coded against a benchmark measured over 61, so "beats SPY"
near the high-`rankLookback` edge of the grid is an approximate claim. Reuse `_run_backtest`'s
existing "N month(s) missing, treated as flat (0%)" status message rather than inventing a second
alignment convention.

### UI

New `SweepHeatmap({ cells, grid, benchmark, metric, net, current, onSelect, theme, accent })` in
`web/charts.jsx`, added to the end-of-file `Object.assign(window, {...})` (without that line
neither v1 nor v2 can see it).

- Layout: inline-div grid in the style of `MonthlyHeatmap`, x axis `rankLookback` (12 columns),
  y axis `topN` (rows), one small-multiple panel per `hold` value, so the default sweep renders
  4 panels. Wrapped in an `overflow-x: auto` container like `TopPerformersPanel`'s table.
- Color scale is anchored on the **benchmark** value, not the grid max: neutral at
  `BENCHMARK.sharpe`, saturating at 2x, so the eye reads "beats SPY" rather than "best of
  grid". Matches `MonthlyHeatmap`'s visual treatment (diverging green/red, hue-preserving dark text
  on light fills) but does not call its functions: `colorFor` and `textColorFor` are declared inside
  the `MonthlyHeatmap` function body and close over `theme`, so they are not reachable from a
  sibling component. Write `SweepHeatmap`'s own pair (it needs a different domain anyway, anchored
  on a benchmark value rather than capped at plus or minus 15%) and leave `MonthlyHeatmap`
  untouched, rather than hoisting shared helpers to module scope and risking a pixel change in an
  existing chart for no functional gain (I2).
- Cells with `months` below 60% of the grid maximum render hatched and are excluded from `BEST`.
  Note this rule almost never fires on the default grid: at `lookback=5` (about 62 monthly rows)
  L=1 gives 61 months and L=12 gives 50, and 50/61 is 82%. It starts firing around
  `lookback <= 2`, which is precisely where it is needed.
- Controls: a metric strip (Sharpe / CAGR / max DD) and a gross/net toggle, styled like the
  `chartTab` button strip in `v1.jsx`. That strip is inline JSX inside `V1Dashboard`, not an
  extracted component, so this is a style match and a small amount of duplicated markup, not a
  reuse.
- Current configuration gets a 2px accent outline and a bullet marker. Every cell is a
  `<button>`; click calls `onSelect({ rankLookback, topN, hold })`, which the dashboard maps to
  three `setParam` calls. It **loads the configuration into the sidebar and does not auto-run**.
- That is a correctness constraint, not just the editorial preference recorded in the open
  questions below. `setParam` is `useCallback((key, value) => setParams(p => ({...p, [key]: value})),
  [])` in `index.html`, and `onRun` is `useCallback(..., [params, running, refreshCache])` calling
  `runBacktest(params, ...)` over the `params` captured when that callback was built. So
  `setParam('rankLookback', 12); setParam('topN', 5); setParam('holds', [1]); onRun();` inside one
  click handler batches the three state updates and then runs the backtest with the **pre-click**
  parameters. The user would see the sidebar showing L=12 N=5 while the dashboard, and the live buy
  list under it, still described the old cell. Auto-run would need either an effect that fires after
  the params commit (`useEffect(() => { if (pendingRun) onRun(); }, [params])`) or a
  `runWith(overrideParams)` variant, and neither is worth the risk in exchange for a click saved.
  If a later change does add auto-run, that plumbing is mandatory.
- Tooltip per cell: `L=3 N=10 h=6 - Sharpe 1.31 net (1.33 gross) - CAGR 39.5% - maxDD -24.1% -
  turnover 218%/yr - 51 mo`.
- **Overfitting caveat, rendered permanently under the panel, not hidden in a tooltip**: the
  number of cells scanned, the statement that the maximum of N noisy draws sits above the true
  mean by construction, that the event study's |t| bar is a single-cell statistic and is not
  corrected for this search, and the instruction to prefer a broad plateau over an isolated
  peak.
- **Neighbour robustness readout** next to the caveat, which is the computable version of that
  advice: for `BEST`, the median metric of its up-to-8 adjacent cells at the same `hold`. A
  plateau shows a neighbour median close to the peak; a spike shows a large gap. On the offline
  run this renders as `best 1.99 (L=12 N=5 h=1) - neighbours 1.69 of 3 (gap 0.31)`. Print the
  neighbour count, not just the median: that particular best cell sits in a grid corner, so its
  "up to 8" neighbours are the 3 cells (11,5), (11,10) and (12,10), and a bare median hides the
  difference between a 3-cell corner claim and an 8-cell interior one.
- Placement: V1 as a 4th entry in the existing chart-card tab strip (`equity | drawdown |
  heatmap | sweep`), fetched lazily the first time the tab is opened. V2 as its own card between
  the sector-concentration card and the monthly-heatmap card, with a Run-sweep button rather
  than auto-fetch (a 13 to 68s call should be user-initiated).
- `web/data-loader.js` gains `runSweep(params, onProgress)`, modelled on `runBacktest`
  (`newRunId` + `pollProgress` + the same error unwrapping), exported through the existing
  `Object.assign(window, {...})`.
- Bump the `?v=` query on all 4 asset tags in `web/index.html` (`data-loader.js`, `charts.jsx`,
  `v1.jsx`, `v2.jsx`). It is `?v=4` on `main` today, and `turnover-transaction-costs` also bumps
  it, so this spec lands `?v=6` if that one went first and `?v=5` if it did not. Read the file,
  do not assume the number.

### Invariants

- **I1**: the sweep produces metrics only, never a pick list or a trade list, and it calls the
  same `momentum_signal` and sleeve logic every other consumer uses, so a cell's numbers equal a
  single backtest at those parameters (asserted below). `current_target`,
  `top_performers_period` and `live_rebalance` are untouched, so the live-target /
  leaderboard identity is unaffected. Click-to-load changes the sidebar parameters and stops
  there, so the live list is only ever regenerated by the existing `onRun` path on parameters the
  sidebar is actually displaying. Auto-running on click would break that (see the stale-`params`
  closure in the UI section): the buy list on screen would belong to the previous cell while the
  sidebar advertised the new one.
- **I2**: a new endpoint, a new UI section and a new pure function. No existing response key,
  default, or number changes. `parameter_sweep` at `cost_bps=0` reports `sharpe` identical to
  `perf_stats(momentum_backtest(...))["Sharpe"]`.
- **I3**: cells short on history are reported with their `months` count, hatched and excluded
  from `BEST` rather than silently ranked against longer cells. Dropped `topN` values appear in
  `SKIPPED` rather than vanishing. Metrics that are genuinely undefined (a 1-month cell has no
  volatility, hence no Sharpe) serialize as `null` and render as an empty square, never as 0.0,
  and 0-month cells are moved to `SKIPPED` with a reason rather than shipped as a row of zeros.
  The benchmark's own measurement window is published so a reader can see it does not match every
  cell. The multiple-testing caveat is part of the deliverable, not optional copy: presenting a
  grid maximum as an achievable Sharpe is precisely the dishonesty I3 exists to prevent.
- **I4**: no build step (plain JSX plus inline-styled divs, no new library), no new dependency,
  local single-user. Consequence to accept explicitly: a 13 to 68s sweep saturates the process for
  its whole duration, so the dashboards must disable Run and the sweep button while one is in
  flight (the same `running` guard the panels already use). `PROGRESS` keeps its single-worker
  assumption. Be accurate about the mechanism, since a wrong premise here would send someone
  "fixing" progress polling: `/api/sweep` is a plain `def` endpoint, so FastAPI runs it in the
  anyio threadpool and the event loop stays free, which is why `GET /api/progress/{run_id}`
  continues to answer during a sweep exactly as it does during a backtest. What degrades is
  throughput, not availability: the sweep is CPU-bound pandas holding the GIL, so concurrent polls
  are served late and a concurrent `/api/backtest` would roughly double both wall clocks.
- **I5**: compute-only over the cached parquet slice. No new runtime network dependency; on a
  warm cache the sweep makes zero network calls, and on a cold cache it degrades to exactly the
  fetch the backtest already performs, with the same status messages.

## Testing

New tests in `tests/test_analysis.py` against the existing
`_monthly_frame(n_months=30, n_tickers=8, seed=7)` fixture. Every assertion below is on the pure
layer, because the repo has no server tests and this spec does not add the harness for any.

1. Cell agreement: on `_monthly_frame(n_months=36)`, for the grid
   `rank_lookbacks=[1,2,3] x top_ns=[2,3] x holds=[1,3]`, every cell's `sharpe` equals
   `perf_stats(momentum_backtest(r, top_n=N, hold_period=h, rank_lookback=L))["Sharpe"]` to
   1e-12, and `months` equals `len(momentum_backtest(...))`.
2. Signal hoisting is not observable: `parameter_sweep(r, [1, 6], [3], [1])` returns the same
   two cells as two separate single-cell sweeps.
3. `skip_months` threads through: cells from `parameter_sweep(..., skip_months=1)` match
   `momentum_backtest(..., skip_months=1)` per cell, and differ from the `skip_months=0` cells.
4. Ordering and count: `len(cells)` equals the product of the clamped grid sizes, and the list
   is sorted by `(rank_lookback, top_n, hold)`.
5. Universe clamping on the pure function: an 8-ticker frame with `top_ns=[3, 9]` yields cells
   only for 3 and exactly one `skipped` entry naming 9.
6. Progress callback: with a list-appending stub, `on_progress` is called exactly
   `len(rank_lookbacks)` times and the reported completed-cell counts are strictly increasing
   and end at `len(cells)`.
7. Cost coupling: at `cost_bps=0`, `sharpe_net == sharpe` for every cell; at 10 bps,
   `sharpe_net < sharpe` for every cell whose `turnover_ann > 0`, and `cost_drag_ann` is strictly
   decreasing in `hold` at fixed `(L, N)`. Restrict this one to eligible cells: at `months == 1`
   both Sharpes are `None` and the comparison is vacuous.
8. Short-history handling: on `_monthly_frame(n_months=14)` with `rank_lookbacks=[1, 12]`, the
   L=12 cells carry `months == 2` (verified) and `eligible is False`, the L=1 cells carry
   `months == 13`, and `analysis.sweep_best(cells)` returns an L=1 cell. Asserting "absent from
   `BEST`" only works because `BEST` is computed by `sweep_best` in the pure layer; against a
   `parameter_sweep` that returns only `(cells, skipped)` there would be nothing to assert on
   from `tests/test_analysis.py`.
9. Degenerate cells never carry a NaN, which is the JSON-encoder guard: on
   `_monthly_frame(n_months=13)` with `rank_lookbacks=[12]` every cell has `months == 1` and
   `vol is None` and `sharpe is None` (verified that `perf_stats` yields `vol=NaN` there), and on
   `_monthly_frame(n_months=12)` with `rank_lookbacks=[12]` `cells == []` with one `skipped` entry
   naming the 0-month cause (verified that `perf_stats` yields both `vol=NaN` and
   `max_drawdown=NaN` there). Finish with `json.dumps({"CELLS": cells, "SKIPPED": skipped},
   allow_nan=False)` succeeding, since that is literally what FastAPI does and NaN there is a 500,
   not a `null`.
10. Runtime budget, documented rather than asserted (timing tests are flaky): 45 ms/cell at 63
    monthly rows and 242 ms/cell at 303 on this machine, which is what the 400-cell cap is sized
    against.
11. Input hygiene: `top_ns=[10, 10]` and `rank_lookbacks=[3, 3]` collapse to one column and one
    row (dedup), and an empty axis raises rather than returning `cells == []` with no explanation.

## Non-goals

- Walk-forward or out-of-sample parameter selection, cross-validation, and bootstrap or White
  reality-check p-values. Those are the real fix for the multiple-testing problem; the caveat
  plus the neighbour readout are an honest stopgap and this spec says so on its face.
- Sweeping `skipMonths`, `benchmark`, `riskFree`, `lookback` or the universe. The grid stays
  3-dimensional.
- Persisting or caching sweep results across sessions, and any background job queue.
- Vectorizing `momentum_signal` (measured 110x, changes the last digits, separate spec).
- Auto-applying the best cell, or nudging the user toward it in copy.
- Sweeping the event study or turnover-optimal parameters.

## Risks / open questions

- **Default grid size.** 12 x 4 x 4 = 192 cells at 13s is fine on the default 5-year window and
  becomes 68s at a 25-year lookback. Alternative default: 12 x 1 x 4 = 48 cells (`topN` fixed at
  the user's current value, ~4s) with the wide grid opt-in behind a "full grid" checkbox. Which
  default does the user want?
- **Should the hold axis follow the user's `holds` selection** (so the sweep matches the sleeves
  the rest of the dashboard shows) instead of a fixed `[1, 3, 6, 12]`?
- **Blocking the single worker for up to ~100s.** Acceptable for a local single-user tool, or
  should the sweep stream cells (chunked response or SSE) or run in a background thread keyed by
  `runId` with a separate results endpoint? Streaming would also let the heatmap fill in
  progressively, which is a materially better experience for a 68s sweep.
- **Click-to-load changes the live buy list.** One click can move the actionable trade list to
  the grid maximum, which is exactly the overfit trap the caveat warns about. Resolved in the UI
  section as load-without-auto-run, which the stale-`params` closure forces anyway. The residual
  question is whether even loading the configuration deserves a confirm step, given that the next
  Run press then produces a real trade list off a cell the user picked because it was the maximum
  of 192 draws.
- **Max drawdown may render flat.** Across the cached window most cells share the same 2022
  drawdown, so the max-DD panel could be visually uninformative. Calmar (CAGR over max DD) or
  the 5th-percentile month may be the better third metric.
- **The grid is survivorship-biased in every cell**, so a plateau is evidence of parameter
  robustness, not of the strategy being real. Long-`rankLookback` cells are the worst affected
  because the current-membership bias grows with window length, and nothing in the heatmap
  communicates that a plateau at L=12 is built on the most biased data. Worth a per-axis note?
- **The neighbour-robustness readout is weakest exactly where it is quoted.** The offline example
  best cell (L=12, N=5, h=1) sits in a grid corner, so "up to 8 neighbours" is actually 3:
  (11,5), (11,10), (12,10). A median of 3 cells is a thin robustness claim, and a corner peak is
  the shape most likely to be an artifact. Either report the neighbour count next to the median
  ("neighbours 1.69 of 3") or refuse the readout below some neighbour count. Reporting a bare
  median that is silently a 3-cell one at the corner and an 8-cell one in the middle undercuts the
  honesty this readout exists to provide.
- **"Adjacent" is not uniform on the topN axis.** The default `topNs` are [5, 10, 20, 50], so a
  one-step move in that direction is a 2x to 2.5x change in portfolio size while a one-step move
  on `rankLookback` is one month. A "plateau" that is flat along `topN` is therefore a much
  stronger claim than one flat along `rankLookback`, and the heatmap draws them as the same
  distance. Either space the axis by label only (accepting the distortion, with a note) or restrict
  the neighbour median to same-`topN` neighbours.
- **`months` differs across the grid but the benchmark does not.** The colour scale is anchored on
  one benchmark Sharpe measured over the longest eligible cell's window. High-`rankLookback` cells
  are scored on 50 months against a 61-month benchmark. Recomputing the benchmark per cell window
  would fix it and cost nothing (it is a reindex plus a `perf_stats`), at the price of a colour
  scale whose neutral point shifts per cell. Which is less misleading?
