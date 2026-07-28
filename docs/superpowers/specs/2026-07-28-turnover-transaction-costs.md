# Turnover and transaction-cost drag

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** nothing

## Problem

`lib/analysis.py:momentum_backtest` returns a gross monthly return series and nothing anywhere
charges for the trading that produces it. `perf_stats` reports total return, CAGR, vol, Sharpe
and max drawdown off that series, and both caveat blocks (`web/v1.jsx` bottom card,
`web/v2.jsx` "Read this first") say only "no costs modelled" without a number, so the user
cannot tell whether the gap to realizable returns is 20 bps or 200.

The gap is not small, because the default configuration replaces the entire portfolio every
month. Today's live rebalance row (`analysis.live_rebalance`, rendered by
`charts.jsx:RebalancePanel`) reads 10 in, 10 out, 0 kept at `topN=10`, with no cost shown
anywhere on the panel.

Measured on the cached 5-year S&P frame (503 constituents, 62 strategy months, `topN=10`,
`rankLookback=1`), at 10 bps one-way:

| hold | one-way turnover /yr | cost drag /yr | Sharpe gross to net | CAGR gross to net | total gross to net |
|------|---------------------|---------------|---------------------|-------------------|--------------------|
| 1    | 1098%               | 2.20%         | 1.179 to 1.116      | 41.31% to 38.33%  | 496.8% to 434.6%   |
| 3    | 388%                | 0.79%         | 1.175 to 1.148      | 35.45% to 34.41%  | 379.6% to 360.9%   |
| 6    | 218%                | 0.45%         | 1.327 to 1.312      | 40.07% to 39.46%  | 470.3% to 457.6%   |
| 12   | 138%                | 0.29%         | 1.329 to 1.318      | 39.19% to 38.79%  | 452.0% to 443.9%   |

Two consequences the tool currently hides:

1. **The sleeve ranking inverts.** Gross, hold1 beats hold6 on CAGR (41.31% vs 40.07%) and on
   total return (496.8% vs 470.3%). Net, hold6 wins both (39.46% vs 38.33%, 457.6% vs 434.6%).
   The sleeve the user actually trades, and therefore the monthly buy list, is decided by a
   term the code does not compute. The dashboard already prefers hold6 on Sharpe (1.33 vs SPY
   0.87); costs widen that lead from 0.15 to 0.20 and add the return-based agreement it lacks.
2. **Cost sensitivity is a property of the hold period, and it is invisible.** Breakeven
   one-way cost at which net Sharpe falls to SPY's 0.866 on the same 62 months: 50 bps for
   hold1, 295 bps for hold6. So at 10 bps the strategy-vs-SPY verdict does *not* flip here (be
   honest about that), but hold1 is 6x closer to the cliff than hold6 and nothing on screen
   says so.

Framing for the user: this month's -14.2% MTD (vs SPY -1.2%) is uncertain and mean-reverting.
The 20 bps round trip is certain and compounds every single month.

## Goal

Every backtest reports a net-of-cost return series alongside the gross one, driven by turnover
computed from the actual sleeve composition, and the dashboards lead with the net numbers while
keeping gross visible for comparison. Turnover %/yr and cost drag %/yr become first-class perf
stats, so "which hold period" and "does this still beat SPY" are answered on realizable
numbers. The live rebalance row states, in one line, what this month's trade costs.

## Design

### Turnover in the overlapping-sleeve structure

`momentum_backtest` holds `K = hold_period` overlapping sleeves. At month `t`, sleeve `k` holds
the top-N picks formed at `t-1-k`, and the sleeve's return is `r.mean()` over the picks that
have a bar in month `t`. That `.mean()` is a monthly re-equal-weighting, so the implied
portfolio weight of ticker `j` during month `t` is

```
w_t(j) = (1 / S_t) * SUM over active k of  [ j in members(t,k) ] / n_(t,k)

members(t,k) = { j in picks(t-1-k) : monthly_returns.iloc[t][j] is not NaN }
n_(t,k)      = len(members(t,k))            # == len(r) after .dropna()
```

`members`, not `picks`, is load-bearing. The indicator has to be restricted to the picks that
actually have a bar in month `t`, because that is the set `.mean()` averages over. Using
`[ j in picks(t-1-k) ]` with the dropna-reduced denominator makes the weights sum to more than
1 whenever any pick is missing a bar: verified on `_monthly_frame(n_months=36)` with three NaNs
punched in, `sum_j w_t(j)` ranges 1.000 to 1.500 with the naive indicator and is exactly 1.000
with the `members` restriction. A weight vector summing to 1.5 silently bills 50% of extra
notional as a trade and breaks the `trade <= 2.0` bound test 5 pins.

`S_t` is the number of sleeves that produced a return that month (the length of the list
`np.mean` averages, not `hold_period`). Weights then sum to 1 by construction.

Carry the weights forward with price drift, since the portfolio the user holds entering month
`t` is the previous month's weights grown by that month's returns. Write `p` for the previous
month the backtest actually produced a return for (see the skipped-month note below; `p == t-1`
on every real frame):

```
w~_p(j)     = w_p(j) * (1 + r_p(j)) / SUM_j w_p(j) * (1 + r_p(j))

trade_t     = SUM_j | w_t(j) - w~_p(j) |            with w~ = 0 before the first month
turnover_t  = trade_t / 2                           (one-way, 100% = full replacement)
cost_t      = (cost_bps / 10000) * trade_t
net_t       = gross_t - cost_t
```

`trade_t` (sum of absolute weight changes) is the right quantity to bill because it charges
both the sell side and the buy side at `cost_bps` each, and it handles the first month
correctly on its own: the initial funding buy has `trade_0 = 1.0`, so it is charged one side
only (10 bps), not two.

**Why not the naive pick-set diff.** Diffing consecutive top-N pick sets gives 91.5% one-way
churn per month on the cached frame, *independent of `hold_period`*. That is correct at K=1 (it
matches the measured 1098%/yr) and overstates K=6 by roughly 5x (measured 218%/yr, not
1098%/yr), because only one cohort of the K rotates each month: 1/K of capital leaves and 1/K
enters, and the entering and leaving cohorts partly overlap. A whole-portfolio diff would
therefore price hold6 as if it traded like hold1, destroying the exact comparison this feature
exists to make.

**Drift is included deliberately.** Holding weights fixed instead of drifting them gives hold6
195.7%/yr instead of 217.5%/yr (11% lower) and 0.41% instead of 0.45% drag. The drifted number
is the correct one for *this* implementation, because `.mean()` per sleeve per month means the
strategy really does rebalance back to equal weight monthly, and those trades cost money.

**The sleeve ramp is not steady state.** `momentum_backtest` skips sleeve `k` while
`t - 1 - k < rank_lookback - 1 + skip_months`, so `S_t` climbs 1, 2, ... K over the first K
months and each step reallocates a fresh 1/S_t of capital. Turnover in those months is
structurally elevated for a reason that is not rebalancing, and `turnover_ann` only excludes
month 0. At hold=12 on a 62-month window that leaves 11 ramp months (18% of the sample) in the
average. Measured on a 62-month, 20-ticker frame at `top_n=3`: excluding month 0 only gives
1020%/yr at h=1, 198%/yr at h=6 and 115%/yr at h=12; excluding the full ramp gives 1020%, 187%
and 96%. So the bias is zero at h=1, 6% at h=6, and 20% at h=12, and it always *overstates* the
long holds. That direction is conservative for this spec's headline claim (long holds cost less),
but it is still a 20% error in a number printed to the user. Options: exclude the first
`hold_period` months from `turnover_ann`, or keep the single-month exclusion and label the ramp.
This spec keeps the single-month exclusion for simplicity and records the choice here; the raw
`TURNOVER` series exposes every month so a reader can see the ramp.

**Skipped months break the `t-1` adjacency.** `momentum_backtest` appends nothing for a month
where every sleeve is empty, so `w~` is drifted from the previous *appended* month, which may
not be the previous calendar month. On the cached frames this never fires (every month has at
least one sleeve), but the implementation must drift from the previous appended row and not
assume `t-1`, or `cost` lands on the wrong month.

### New and changed functions in `lib/analysis.py`

```python
momentum_backtest_detail(monthly_returns, top_n, hold_period, rank_lookback,
                         skip_months=0, cost_bps=0.0, signal=None) -> pd.DataFrame
    # DatetimeIndex, columns: gross, trade, turnover, cost, net
    # signal=None -> momentum_signal(monthly_returns, rank_lookback, skip_months) internally.
    # A caller that already holds that frame passes it in; the function must not
    # re-derive or mutate it.

momentum_backtest(monthly_returns, top_n, hold_period, rank_lookback, skip_months=0) -> pd.Series
    # becomes a thin wrapper:
    #   momentum_backtest_detail(...)["gross"].rename(f"momentum_top{top_n}_hold{hold_period}")

perf_stats(returns, periods_per_year=12, risk_free_annual=0.0,
           turnover=None, cost=None) -> dict
```

The `signal=` seam exists for one reason: `parameter-sweep-heatmap` hoists `momentum_signal` out
of its cell loop (12 calls instead of 192, which is that spec's entire optimisation) and must do
so *through* this function rather than by re-implementing the sleeve loop. Without the seam the
sweep forks the ranking/sleeve logic, which is what I1 forbids and what would make that spec's
"every cell equals a single backtest to 1e-12" assertion a coincidence rather than a
consequence. `signal=None` keeps every current caller byte-identical.

`perf_stats` additions, both opt-in:

- `turnover` given: `turnover_ann = turnover.iloc[1:].mean() * periods_per_year`. The first
  month is excluded because it is the funding trade, not a rebalance (excluded, not silently
  dropped: the raw series still carries it and the API exposes it).
- `cost` given: `cost_drag_ann = (1 + cost).prod() ** (periods_per_year / len(cost)) - 1`,
  geometrically annualized so it composes with CAGR rather than being an arithmetic sum.
- Neither given: the dict has exactly today's 6 keys.

**Both new keys must be NaN-free and division-safe, or they 500 the API.** `perf_stats` today is
carefully written so every returned value is finite for a non-degenerate series (`Sharpe` falls
back to `0` when `vol <= 0`, `cagr` to `0` when `years == 0`), and FastAPI serializes with
`allow_nan=False`, so a single NaN in a PERF row is a 500 rather than a `null`. The new keys
break that on inputs that are trivially reachable, verified against the real functions:

- `turnover.iloc[1:].mean()` on a 1-row series is `NaN`. A 1-month strategy series happens
  whenever `rank_lookback` almost exhausts the window (`_monthly_frame(n_months=13)` with
  `rank_lookback=12` gives exactly 1 strategy month).
- `(1 + cost).prod() ** (periods_per_year / len(cost))` raises `ZeroDivisionError` on an empty
  `cost` series, which is what a 0-month strategy series produces.

So: `turnover_ann = 0.0` when `len(turnover) < 2`, `cost_drag_ann = 0.0` when `cost` is empty, and
both NaN-coerced to 0.0 on the way out. 0.0 is defensible for these two specifically ("no rebalance
was observed in the sample") in a way it would not be for `vol` or `Sharpe`, where 0.0 would assert
zero risk; `parameter-sweep-heatmap` uses `null` for those, on purpose. Worth
noting while in there: `perf_stats` already returns `vol=NaN` and `max_drawdown=NaN` for a
0-month series and `vol=NaN` for a 1-month series (verified: `json.dumps(..., allow_nan=False)`
raises on both), so `_run_backtest` can already 500 on a degenerate hold period today. Fixing
that pre-existing hole is out of scope here, but do not widen it.

**`cost_drag_ann` is the annualized cost rate, not the CAGR impact, and must be labelled as
such.** The spec's own table proves the gap: hold1 reports 2.20%/yr of drag while CAGR falls
41.31% to 38.33%, a 2.98pp hit; hold6 reports 0.45% while CAGR falls 0.61pp. The ratio is
roughly `1 + CAGR`, because the cost is deducted from each month's return and the remainder then
compounds at the gross growth rate. Shipping a "Cost drag 0.45%/yr" tile next to a CAGR that
moved 0.61pp is the same class of quiet misstatement this spec exists to remove. PERF already
carries both `CAGR` and `CAGR_net`, so the UI computes the honest number itself
(`cost_drag_cagr = CAGR - CAGR_net`) and the tile leads with that; `cost_drag_ann` stays in the
payload as the annualized cost rate, labelled "cost rate", not "drag".

**Plumbing consequence, stated explicitly.** `server._run_backtest` spreads the `perf_stats`
dict straight into PERF rows (`**{k: float(v) ... for k, v in s.items()}`) and
`momentum_backtest.py` builds its printed table from the same dict via
`pd.DataFrame(rows).set_index("strategy")`. So `turnover_ann` and `cost_drag_ann` appear in the
API PERF rows and as two extra columns in the CLI's `perf.round(4).to_string()` automatically,
with no per-key plumbing, and conversely there is no way to add a `perf_stats` key without both
surfaces changing. Because the keys only appear when the caller passes `turnover`/`cost`, the
CLI output stays byte-identical until `momentum_backtest.py` opts in.

`live_rebalance(prices, monthly_returns, top_n, rank_lookback, n_months=13, skip_months=0,
cost_bps=0.0)`. Every row gains `turnover_pct = (len(picks) - len(holds)) / len(picks)` (one-way,
for the full-replacement 1-month sleeve the panel shows). The live row additionally gains
`cost_bps_round_trip = cost_bps * 2 * turnover_pct` and
`cost_per_10k = round(10000 * (cost_bps / 10000) * 2 * turnover_pct, 2)`. Today's row: turnover
100%, 20 bps, $20.00.

Three details the implementation has to get right here:

- `turnover_pct` is the pick-set diff, deliberately, because the panel *is* the K=1 sleeve where
  that diff is exact. It is therefore a different quantity from the `TURNOVER` series' `h1m`
  value for the same month, which includes the drift-back-to-equal-weight trades. The two agree
  at full replacement (everything is sold either way) and diverge whenever anything is kept. Two
  numbers on one screen both called "turnover" that disagree is a support question waiting to
  happen: the panel line says "of the 10 names" and the perf tile says "portfolio weight
  turnover", and neither claims to be the other.
- `live_rebalance` returns the raw `rebalance_schedule` rows unchanged when `current_target`
  yields `None`, so `turnover_pct` has to be stamped on the fallback path too, otherwise the CSV
  and the history badge lose the column exactly when daily data is thin.
- When there is no completed history at all (`completed == []`) the live row's `holds` is empty,
  so `turnover_pct` is 1.0 and the round trip is billed at 2x. That is the funding buy, which the
  analysis layer is careful to bill one side only (`trade_0 = 1.0`). Either bill it one-sided
  here as well or accept the inconsistency knowingly; do not let the panel and the series
  disagree by accident.

### Parameters

- `server.py`: `BacktestParams.costBps: float = Field(10.0, ge=0.0, le=200.0)`, threaded into
  `momentum_backtest_detail`, `perf_stats` and `live_rebalance`.
- `GET /api/universes` `ranges` gains `costBps: {"min": 0, "max": 200}`.
- `web/index.html`: `DEFAULT_PARAMS.costBps = 10`. `charts.jsx`: `PARAM_HELP.costBps`
  ("One-way trading cost in basis points, charged on both the buy and the sell side. 10 bps is
  a liquid-large-cap retail assumption: spread plus slippage, no commission."),
  `DEFAULT_RANGES.costBps`, a `paramIssues` check, and a `NumField` in both sidebars next to
  "Risk-free rate".
- The `paramIssues` check must read the range defensively:
  `const costRange = ranges.costBps || DEFAULT_RANGES.costBps;`. This is not optional polish.
  `paramIssues` runs against whatever `/api/universes` returned, and it already carries exactly
  this guard for `skipMonths` (`const skipRange = ranges.skipMonths || DEFAULT_RANGES.skipMonths`)
  because that parameter was added the same way. `check('costBps', 'Cost', ranges.costBps.min,
  ...)` against a server response that predates the field throws a TypeError inside a render, and
  the whole dashboard goes blank rather than showing one validation message. Note also that
  `DEFAULT_RANGES` is module-private in `charts.jsx` and is *not* in the end-of-file
  `Object.assign(window, {...})`, so it needs no export change; only `PARAM_HELP`, `paramIssues`
  and `NumField` cross the window boundary and all three are already there.

### Response keys (all additive, every existing key keeps its gross value)

- `EQUITY`, `DRAWDOWN`, `MONTHLY_RETURNS`, `ROLLING_SHARPE`: gain `h{K}m_net` alongside each
  existing `h{K}m`.
- New `TURNOVER: {months: [...], h{K}m: [one-way turnover per month]}`, built through
  `_safe_floats` like every other series block, so a NaN can never reach the encoder.
- `PERF` rows gain `total_return_net`, `CAGR_net`, `Sharpe_net`, `max_drawdown_net`,
  `turnover_ann`, `cost_drag_ann`. The benchmark row is built with zero turnover/cost series so
  every row has the same key set: net equals gross for a buy-and-hold benchmark in this model,
  which is a modelling choice worth one line of UI copy, not a claim that indexing is free.
- `REBALANCE` rows gain `turnover_pct`; the live row also `cost_bps_round_trip`, `cost_per_10k`.
- Top-level `COST_BPS` echo so the UI can label its own numbers.

### UI

- `charts.jsx:RebalancePanel`, inside the "current target" box directly under the
  `{buys} in, {sells} out, {holds} unchanged` line: `turnover 100% (10 of 10 replaced) - 20 bps
  round trip - $20 per $10,000 at 10 bps one-way`, with a `HelpTip` noting the panel is the
  1-month sleeve, so a user running hold6 rotates only 1/K of capital per month. History rows
  show `Δ {turnover_pct}` next to the existing "N kept".
- `RebalancePanel` CSV export gains `turnover_pct` and `cost_bps_round_trip` columns, repeated on
  every row of that month. Not "on the first row of each month": the CSV is one row per
  ticker-action (`month, action, ticker, group, fwd_return_pct, fwd_status`, emitted buys then
  sells then holds), so "first row" is whichever bucket happens to be non-empty and shifts to a
  SELL line on a month with no buys. Repeating the value per row matches how `fwd_status` already
  behaves and survives sorting the CSV in a spreadsheet. `cost_bps_round_trip` exists only on the
  live row, so history rows carry an empty cell there, same convention as the existing empty
  `fwd_return_pct` on SELL lines.
- V1 KPI tiles and V2 "Best sleeve" / "vs SPY" rail lead with net and show gross in the
  sub-label (`Sharpe 1.31 net - 1.33 gross`), plus a new "Cost drag" tile
  (`0.45%/yr - 218% turnover`).
- `bestStrat` selection in both dashboards changes from `b.Sharpe - a.Sharpe` to
  `(b.Sharpe_net ?? b.Sharpe) - (a.Sharpe_net ?? a.Sharpe)`, so the highlighted sleeve, the
  drawdown/heatmap subject and `EarningsBreakdown`'s default sleeve all follow the net winner.
  (`??` is already used in both files, so in-browser Babel handles it.) Note that `bestKey` is
  derived by `bestStrat.strategy.replace(/^top\d+_hold(\d+m)$/, 'h$1')`, so the drawdown and
  heatmap tabs keep reading `data.DRAWDOWN[bestKey]` / `data.MONTHLY_RETURNS[bestKey]`, which are
  the *gross* arrays. Either point those two tabs at the `_net` arrays too or label them "gross";
  leading the tiles with net while the chart beneath them is gross and unlabelled is the
  inconsistency I3 is about.
- `EquityCurveChart` gets one extra dashed series per sleeve for `h{K}m_net`, off by default
  behind a "net" checkbox next to the existing log/fill toggles, so the default view is not
  8 lines.
- The caveat copy replaces "no costs modelled" with the live numbers: costs modelled at
  `COST_BPS` bps one-way, `CAGR - CAGR_net` per year, taxes still not modelled and short-term at
  this turnover. There are **four** sites, not two, and leaving any of them saying "no transaction
  costs" while the headline is net is a factual error in the UI: `v1.jsx`'s bottom caveat card
  ("Ignores costs, taxes, slippage"), `v2.jsx`'s "Read this first" block ("**No costs modelled**
  ... no transaction fees, slippage, or taxes"), and the two per-universe caveat lines in `v2.jsx`
  that each end "**no transaction costs**; equal-weight". Grep `no transaction cost` and
  `Ignores costs` before calling this done.
- Bump `?v=4` to `?v=5` on all 4 asset tags in `web/index.html`.

### Invariants

- **I1**: turnover is derived from the picks the sleeves already hold, and cost is a scalar
  overlay subtracted from returns, never an input to ranking. `current_target` and
  `top_performers_period` are untouched, so the live target keeps matching the leaderboard at
  matching settings.
- **I2**: analysis-layer defaults are `cost_bps=0.0` and `turnover=None`, so
  `momentum_backtest` returns the identical Series (verified: max abs diff 0.0 vs today for
  (N,h,L) of (10,1,1), (10,6,1) and (10,3,6) on the real cached frame) and `perf_stats` returns
  today's exact 6 keys. The CLI is byte-identical. At the API level the default is 10 bps, but
  every pre-existing response key still carries the gross value and net arrives under new
  `_net` keys, so no existing number changes. **Justification for 10 rather than 0**: a default
  of 0 would leave the dashboard headline exactly as misleading as today, which is the entire
  problem this spec exists to fix, and the honest number is knowable to 2 decimal places from
  data already in memory. Defaulting to 10 with gross preserved side by side satisfies both the
  letter (no existing output changes) and the spirit (the tool stops implying free trading).
  The one visible behavior change is which sleeve the dashboards highlight, which is a UI
  selection over unchanged data and is the intended editorial outcome.
- **I3**: no lookahead: `trade_t` uses weights at `t` and the previous appended month, plus that
  month's returns, and cost is charged in the month the trade happens. A pick with no bar in a
  month leaves both the `members` numerator and the `n_(t,k)` denominator, exactly as the existing
  `.mean()` does, so weights stay consistent with the return being billed and nothing is invented
  to fill the hole. The funding month is reported and excluded from the turnover average rather
  than quietly averaged in. `turnover_ann` and `cost_drag_ann` return 0.0 on degenerate inputs
  rather than NaN, which is a JSON-safety fallback and not a claim that a 1-month sample has zero
  turnover: the raw `TURNOVER` series and `months` are on screen next to it.
- **I4**: pure computation in `lib/analysis.py`. No broker, no execution, no order routing. The
  cost line is a number printed next to a list.
- **I5**: no new network dependency. Turnover comes from the monthly frame already in memory,
  and `costBps` is a user-entered constant rather than a fetched fee schedule, so the feature
  works unchanged airgapped.

## Testing

New tests in `tests/test_analysis.py`. Most reuse the existing fixtures, but note their real
signatures: `_monthly_frame(n_months=30, n_tickers=8, seed=7)` and
`_daily_prices(tickers, start="2026-01-01", end="2026-07-02", seed=0)` where **`tickers` is a
required positional list** (every existing caller passes `[f"T{i}" for i in range(6)]`). Tests 4
and 7 below cannot use either fixture: they need small hand-built frames with prescribed picks,
so they get local helpers.

1. `momentum_backtest_detail(r, 3, h, 2)["gross"]` equals
   `momentum_backtest(r, top_n=3, hold_period=h, rank_lookback=2)` under
   `assert_series_equal` (after `rename`) for h in (1, 3, 6) on `_monthly_frame(n_months=24)`.
2. `cost_bps=0`: `cost` is all zeros and `net` is `assert_series_equal`-identical to `gross`.
3. `cost == cost_bps / 1e4 * trade` elementwise and `net == gross - cost` elementwise at
   `cost_bps=10`.
4. Full replacement: on a locally built frame whose top-3 set is disjoint month over month
   (needs at least 6 tickers cycling; the shared `_monthly_frame` will not do it),
   `trade.iloc[0] == 1.0` (funding buy) and `trade.iloc[1:] == 2.0`, `turnover.iloc[1:] == 1.0`.
5. Fully invested bound: `sum_j w_t(j) == 1.0` to 1e-12 for every month, and hence
   `0 <= trade <= 2.0 + 1e-12`, for every `(top_n, hold)` combination tried. Run this variant on
   a frame with NaNs punched into held names (`r.iloc[10, 0] = np.nan` and similar), because that
   is the case the naive `[j in picks]` indicator gets wrong: measured `sum_j w_t(j)` reaches
   1.500 there, and the bound test is what catches it. Assert the weight sum directly, not only
   the `trade` bound: with 8 tickers and `top_n=3` the trade bound alone stays under 2.0 even when
   the weights are broken, so it is not a sufficient guard on its own.
6. Sleeve scaling: on `_monthly_frame(n_months=36)` with `top_n=3, rank_lookback=2`, the mean of
   `turnover.iloc[1:]` is strictly decreasing in `hold_period` over (1, 2, 3, 6), with h=1 above
   0.35 and h=6 below 0.20. Measured on that exact fixture (seed=7, 8 tickers): 0.4383, 0.3194,
   0.2148, 0.1141. Do **not** assert "h=1 above 0.80" as an earlier draft of this spec did: 0.80
   is the number the real 503-name universe produces, and with 8 tickers two consecutive random
   top-3 sets overlap by 1.1 names on average, so the fixture's ceiling is around 0.44 and the
   assertion fails. Pin `rank_lookback` explicitly, since L=1 on the same fixture gives 0.6275
   and would move every threshold.
   This is the assertion that would fail if anyone reintroduced a whole-portfolio pick diff.
7. Drift trades are real: on a locally built frame where one held name doubles and the rest are
   flat while the pick set is unchanged, `trade` at hold=1 is nonzero and equals the hand-computed
   `sum |w_target - w_drifted|`. Verified constructible: 4 tickers over 6 months with A/B/C always
   ranked above D and A returning 100% in one month yields `trade` of 0.3333 in the month after the
   double and 0.0556 in the quiet months, with the pick set never changing. Note the quiet-month
   values are nonzero too, so a "drift is off" regression shows up as `trade == 0` everywhere, not
   just at the double.
8. `perf_stats` key surface: `set(perf_stats(r)) == {"total_return", "CAGR", "vol", "Sharpe",
   "max_drawdown", "months"}` (this is the I2 guard for the API PERF contract and the CLI
   table). With `turnover`/`cost` passed the dict gains exactly `turnover_ann` and
   `cost_drag_ann`; `turnover_ann == turnover.iloc[1:].mean() * 12`; `cost_drag_ann` equals
   `(1 + cost).prod() ** (12 / len(cost)) - 1`.
9. Degenerate-series guard (the 500 test): `perf_stats(r, turnover=t, cost=c)` where `t`/`c` have
   1 row returns `turnover_ann == 0.0`, and where they are empty returns
   `cost_drag_ann == 0.0` with no `ZeroDivisionError`. Then assert the whole dict survives
   `json.dumps(stats, allow_nan=False)` for the new keys, which is the encoder FastAPI actually
   uses. Build the 1-row case from `momentum_backtest_detail(_monthly_frame(n_months=13), 3, 1,
   12)`, which yields exactly one strategy month (verified).
10. `live_rebalance(prices, monthly, top_n=3, rank_lookback=1, cost_bps=10)` on
    `_daily_prices([f"T{i}" for i in range(6)], seed=3)` (the fixture takes `tickers` as a
    required positional; `_daily_prices(seed=3)` is a TypeError): the live row's
    `turnover_pct == (3 - len(holds)) / 3`, `cost_bps_round_trip == 10 * 2 * turnover_pct`,
    `cost_per_10k == round(2 * turnover_pct * 10, 2)`; at `cost_bps=0` the fields are present and
    0.0 (never absent, so the UI needs no null branch). Also assert `turnover_pct` is present on
    every history row, including on the `current_target is None` fallback path.
11. Regression: `test_momentum_backtest_skip_zero_unchanged_and_skip_runs` and
    `test_live_rebalance_prepends_live_row_diffed_vs_last_completed_month` pass unchanged.
12. Seam neutrality: `momentum_backtest_detail(r, 3, h, 2, signal=momentum_signal(r, 2))` is
    `assert_frame_equal`-identical to `momentum_backtest_detail(r, 3, h, 2)`. This is the
    contract `parameter-sweep-heatmap` depends on.

## Non-goals

- Per-name spread or market-impact modelling. One bps constant, not a liquidity model.
- Fixed per-ticket commissions, borrow cost, short-side cost, currency conversion.
- Taxes. At ~1100%/yr turnover essentially every gain is short-term, which likely dwarfs the
  cost drag for a taxable account. The caveat block says so; the tool does not model it.
- Costing the event study. It is a signal test over cohorts, not a tradeable portfolio.
- A cost-aware signal: no-trade buffer bands, rank hysteresis, or a turnover-penalized
  objective. That is the obvious follow-up once the drag is measurable.
- Changing the rebalance panel from the 1-month sleeve to the actual K-sleeve trade list.

## Risks / open questions

- **10 bps is a judgement call.** For liquid S&P names at retail size (commission-free, 1 to 3
  bps spread plus slippage) 5 bps is defensible; for `global_etfs` (single-country iShares,
  wider spreads) 20 to 30 bps is closer, and that universe's caveat already flags ~0.50%/yr of
  expense drag on top. Should the default be per-universe (sp500 10, global_etfs 25,
  us_sector_etfs 5) rather than one global number?
- **Should the dashboard headline be net or gross?** This spec assumes net leads and gross sits
  in the sub-label. The inverse (gross leads, net as a footnote) is a defensible editorial
  choice and is a one-line change either way.
- **Per-trade commission and account size.** At a $10k account holding 10 names, a $1 per trade
  commission is another ~20 bps per month, doubling the drag. That needs a
  `commissionPerTrade` param plus an account-size input to express, and the "$Z per $10k" line
  is where it would surface. In or out?
- **The panel-versus-sleeve mismatch.** The rebalance panel is the hold1 sleeve while the cost
  analysis may nominate hold6. A user could read "20 bps" as applying to what they actually
  trade when it applies to full monthly replacement. Copy mitigates it; only a K-sleeve trade
  list fixes it.
- **The newest month can be partial.** When the latest price bar is mid-month, the newest
  monthly return is month-to-date but the rebalance cost charged against it is a full one, so
  the newest month's drag is slightly overstated. Options: suppress cost on a partial month,
  pro-rate it, or leave it and label it (the `fwd_partial` flag already exists to hang a label
  on).
- **Fill assumption.** Trades are assumed to execute at the same month-end close the returns
  use. Real fills are next-open or intraday, and that slippage is folded into the bps constant
  rather than modelled.
- **How many ramp months to exclude from `turnover_ann`.** `S_t` climbs from 1 to K over the
  first K months, so those months carry allocation trades that are not rebalances. This spec
  excludes only month 0. Excluding the first `hold_period` months instead would report a cleaner
  steady-state number but would drop 12 of 62 months at hold12 and make the reported month count
  differ per sleeve. Open.
- **Two numbers both called turnover.** `REBALANCE[].turnover_pct` is a pick-set diff on the K=1
  sleeve; `TURNOVER.h1m` is the weight-based, drift-inclusive figure. They match at full
  replacement and diverge whenever anything is kept. Distinct labels are the plan; a single
  definition everywhere would mean either putting drift into the panel (which then stops matching
  the trade list the user executes) or dropping drift from the series (which understates hold6 by
  11%). Neither is obviously right.
- **`cost_drag_ann` is a cost rate, not a CAGR delta.** Resolved in Design (the tile leads with
  `CAGR - CAGR_net`), but it is worth restating as a live trap: the two differ by roughly
  `1 + CAGR`, which is 2.20% vs 2.98pp at hold1. Any future surface that prints `cost_drag_ann`
  next to a CAGR pair has to say which one it is.
