# Volatility-adjusted momentum signal

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** 2026-07-28-skip-month-momentum-signal, 2026-07-05-current-target-run-date-design

## Problem

`lib/analysis.py:momentum_signal` knows exactly one thing: trailing compounded return. It
cannot tell a 41% month earned at 71% annualized vol from a 34% month earned at 180%, so
both rank as "winners" and both get bought.

That is not hypothetical. Recomputed on the live cache (`cache/prices.parquet`, 504 tickers,
2021-04-28 to 2026-07-27) the 2026-06-30 formation cohort at `rank_lookback=1`, `top_n=10` is
AMAT, KLAC, MRNA, MRVL, GLW, TECH, LRCX, SNDK, HUM, TER, and its realized July return is
**-27.2%** (GLW -43.9%, SNDK -43.8%, MRVL -36.5%, only TECH positive at +1.4%). Trailing
6-month annualized vol at that formation date: SNDK **180%**, MRVL **94%**, GLW **71%**.
Dividing the same signal by that vol moves SNDK from rank 8 to **rank 211**, MRVL from 4 to
30, GLW from 5 to 10, and the cohort's July return from -27.2% to **-11.1%** (16.1pp of the
loss avoided by never buying the two worst names).

The tool already computes the exact number the ranking would need. `_pick_quality_stats`
returns `vol_ann` and the leaderboard renders it as a sortable "Vol" column
(`web/charts.jsx:TopPerformersPanel`), so the user can *see* that this month's picks are
high-vol and can do nothing about it: sorting a table does not change the buy list. Every
ranking consumer (`event_study`, `momentum_backtest`, `recent_picks`, `rebalance_schedule`,
`cohort_sector_breakdown`) sources from `momentum_signal`, so the seam for fixing this
already exists and is unused.

## Goal

A `signalFamily` parameter (`raw` | `vol_adj`) implemented once inside `momentum_signal` and
once inside its daily-frequency mirror (`top_performers_period`), so that switching it
changes the backtest, the event study, the cohorts, the sector breakdown, the rebalance
schedule, the leaderboard **and** the live target together, and the trade list the user acts
on is the same list the statistics were computed over. Default `raw`, so every current output
stays byte-identical.

## Design

### 1. Monthly builder

```python
VOL_MIN_MONTHS = 6          # never estimate vol from fewer than 6 monthly returns
SIGNAL_FAMILIES = ("raw", "vol_adj")

def momentum_signal(
    monthly_returns: pd.DataFrame,
    rank_lookback: int,
    skip_months: int = 0,
    family: str = "raw",
    vol_min_months: int = VOL_MIN_MONTHS,
) -> pd.DataFrame:
```

Body change, inserted after the existing trailing-return block and **before** the
`skip_months` shift (so numerator and denominator are shifted together):

```python
    if family == "vol_adj":
        vol_months = max(rank_lookback, vol_min_months)
        vol = monthly_returns.rolling(vol_months).std() * np.sqrt(12)   # min_periods == window
        signal = signal / vol.where(vol > 0)
    elif family != "raw":
        raise ValueError(f"unknown signal family: {family!r}")
    if skip_months:
        signal = signal.shift(skip_months)
```

Math: row `t`, ticker `i` holds `R_i(t) / sigma_i(t)` where `R_i` is the existing compounded
return over `(t - skip - L, t - skip]` and `sigma_i` is the annualized standard deviation of
the last `max(L, 6)` monthly returns ending at the same point. The numerator is **not**
annualized: within a formation month every ticker shares the same numerator horizon, so
annualizing it is a positive constant multiplier that leaves `nlargest` ordering untouched.
The quantity is therefore return-per-unit-annual-risk, not a Sharpe ratio, and must not be
labelled as one in the UI.

Decisions baked in here:

- **Vol window `max(rank_lookback, 6)` months.** At `rank_lookback >= 6` the vol window is
  literally the ranking window. Below that it widens to 6 months, because the tool's default
  is `rankLookback=1` and a 1-month rolling `std` with `ddof=1` is **all-NaN** (verified: 0
  non-null rows), i.e. "vol over the same window" is not a computable quantity at the default
  setting. 6 months matches Barroso and Santa-Clara's 126-day estimation window.
- **Minimum observations = the full window** (pandas' default `min_periods == window`). A
  partial-history name gets NaN and is dropped by the `.dropna()` every consumer already
  applies before `nlargest`, exactly like the raw path's incomplete rolling product.
- **Zero vol is excluded, not clamped.** `vol.where(vol > 0)` turns a flat or stale series
  (`sigma = 0`, ratio `inf`) into NaN. A name with zero measured vol over 6 months is a data
  artifact (halted, stale quote, corporate action), and ranking it first would be fabricated
  signal. This is I3, not an edge-case convenience.
- **Plain ratio, no z-score.** A cross-sectional z-score at time `t` is an affine transform
  of the same row (`(x - mean_t) / std_t`, both constants within the row) and therefore
  produces an identical `nlargest` ordering, while adding a cross-sectional dependency that
  makes a single name's signal unreproducible in isolation. Z-scoring only earns its cost
  when signals are being blended, which is a non-goal here.

The five monthly consumers each gain the same trailing keyword and forward it unchanged, so
their existing keyword-only call sites (`server.py`, `momentum_backtest.py`, the tests) keep
working untouched:

```python
event_study(..., skip_months=0, benchmark_monthly=None, family="raw")
momentum_backtest(..., skip_months=0, family="raw")
recent_picks(..., n_cohorts=6, skip_months=0, family="raw")
rebalance_schedule(..., n_months=13, skip_months=0, family="raw")
cohort_sector_breakdown(..., n_cohorts=12, skip_months=0, family="raw")
```

`family` goes **last** in every signature. `momentum_backtest.py` (the CLI entry) calls all of
these with keyword arguments and is deliberately left alone: it has no family constant and its
output stays byte-identical.

New public helper for the API and the status line:

```python
def signal_warmup_months(rank_lookback, skip_months=0, family="raw",
                         vol_min_months=VOL_MIN_MONTHS) -> int:
    """Monthly bars consumed before the first usable ranking row."""
    return (max(rank_lookback, vol_min_months) if family == "vol_adj" else rank_lookback) + skip_months
```

Consumers keep their existing loop bounds. Verified: a `select_t` inside the vol warm-up
yields an all-NaN row, `.dropna()` empties it, `len(ranking) < top_n` skips it. No crash, no
partial cohort. On the cached window the cost is 5 monthly bars (57 usable months instead of
62 at `rank_lookback=1`).

### 2. Daily mirror (this is where I1 lives)

`current_target` delegates to `top_performers_period`, and that shared call is the entire
alignment guarantee from the 2026-07-05 spec. The family therefore has to be implemented
**inside `top_performers_period`**, never in `current_target`.

First, factor the vol computation out of `_pick_quality_stats` so there is one definition,
working on a Series or a whole frame:

```python
# _MONTH_DAYS currently sits at line 463, below top_performers_period. Hoist it (and
# VOL_MIN_MONTHS) into the module header next to the imports, otherwise this line
# raises NameError at import time.
VOL_MIN_DAYS = round(VOL_MIN_MONTHS * _MONTH_DAYS)   # 6 * 30.4375 -> 183
VOL_MIN_OBS  = 20                                    # daily returns needed to rank on vol

def _ann_vol(window, min_obs: int = 5):
    """Annualized vol of daily returns over `window` (Series -> float|None,
    DataFrame -> Series). pct_change on the raw gappy series, so a return across
    missing bars stays NaN and is skipped instead of inflating vol."""
    daily = window.pct_change(fill_method=None)
    vol = daily.std() * np.sqrt(252)
    n = daily.count()
    if isinstance(vol, pd.Series):
        return vol.where(n >= min_obs)
    return float(vol) if n >= min_obs else None
```

`_pick_quality_stats` then calls `_ann_vol(window, min_obs=5)` for its `vol_ann` field.
Verified bit-identical to the current per-column expression to 1e-15 on the seeded fixture
(`std` skips NaN per column and `ddof=1` is unchanged), so the leaderboard's existing Vol
column and `test_top_performers_quality_stats` are untouched.

`top_performers_period(prices, top_n=10, days=30, watch_n=0, family="raw")`:

```python
    returns = (end_row / start_row - 1).dropna()          # unchanged
    vol_rank = None
    if family == "vol_adj":
        vol_days = max(days, VOL_MIN_DAYS)
        v_rows = prices.loc[prices.index >= end_ts - pd.Timedelta(days=vol_days)]
        vol_rank = _ann_vol(v_rows.loc[:end_ts], min_obs=VOL_MIN_OBS)
        vol_rank = vol_rank.where(vol_rank > 0)
        ranked = (returns / vol_rank.reindex(returns.index)).dropna()
        if ranked.empty:
            return []            # nothing has a usable vol window yet
    else:
        ranked = returns
    top = ranked.nlargest(top_n + max(0, watch_n))
```

`return_pct` in each row stays the **plain window return** (that is what the column header
says); the ranked quantity is exposed additively as `signal_value`, with `vol_rank_ann` (the
denominator, `None` under `raw`). Row order becomes signal order, which is already the
contract every caller assumes.

That means the row loop must change too, and this is the one edit that is easy to miss: today
it reads `for ticker, ret in top.items(): ... "return_pct": float(ret)`. Under `vol_adj`
`top`'s *values* are ratios, so leaving that line alone would silently publish the ratio as
`return_pct` and blow up every percentage in the leaderboard, the live-target card and
`current_target`'s "ordered by return" contract. Iterate the index and pull each field from
its own series:

```python
    for ticker in top.index:
        ...
        "return_pct":   float(returns[ticker]),          # plain window return, always
        "signal_value": float(top[ticker]),              # == return_pct under raw
        "vol_rank_ann": (float(vol_rank[ticker]) if vol_rank is not None else None),
```

Both new fields are finite by construction (the `.dropna()` above removed every NaN ratio, and
a surviving ratio implies a finite positive denominator), so nothing NaN-shaped reaches
FastAPI's `allow_nan=False` encoder.

`streak` / `prior_rank` / `new_entrant` are computed from `_window_return_top_set` and
`prev_returns`, both of which rank on **raw** returns. Under `vol_adj` that makes them
incoherent rather than merely conservative, so they are **suppressed** (set to `None`) when
`family == "vol_adj"`:

- `new_entrant` compares against the raw top `max(2N, N + watch_n)` set. A `vol_adj` pick sits
  nowhere near that set by design (the spec's own measurement moves SNDK from raw rank 8 to
  vol_adj rank 211), so essentially every row would carry the NEW badge on every refresh,
  which is exactly the always-on failure the existing `ref_top_n` comment was written to
  prevent.
- `prior_rank` / `rank_delta` would subtract a raw-return rank from a signal rank, so the
  Δ 7d arrows would show large moves that are pure metric mismatch.
- `streak` counts prior *raw* top-N membership, which is a defensible
  persistence-of-the-move reading but is not the persistence of the list the user is being
  told to buy.

`prior_rank: None` already renders as the existing "—" in the Δ 7d cell, and
`new_entrant: None` is falsy so the NEW chip disappears with no component change. The Streak
cell needs one edit: it currently reads `r.streak > 0 ? '×N' : 'new'`, and `null > 0` is
false, so a suppressed streak would render the word **new** on every row. Change that cell to
`r.streak == null ? '—' : (r.streak > 0 ? '×N' : 'new')`, which also closes the same latent
hole for any future row with a missing streak.

Doing this properly instead means teaching `_window_return_top_set` and the prior-rank block
the family, which is a wider diff and is left as an open question below.

`current_target(prices, top_n, rank_lookback, skip_months=0, family="raw")` passes `family`
straight through to `top_performers_period` and adds `family` plus `vol_days` to its returned
dict. `live_rebalance(..., family="raw")` forwards to both `rebalance_schedule` (monthly) and
`current_target` (daily).

Note the two frequencies use different estimators of the same quantity: 183 calendar days of
daily returns scaled by sqrt(252) versus 6 monthly returns scaled by sqrt(12). On the cached
frame at 2026-06-30 they rank the universe's vol with a rank correlation of 0.73 (n=501,
medians 33.7% and 30.1% annualized), so the monthly backtest and the live daily list will
disagree somewhat more than they do under `raw`. That is acceptable and is *not* an I1
regression: the live target and the leaderboard both run the daily path, and the daily-versus-
monthly divergence already exists under `raw` for a separate reason (window anchoring). On
today's cache the overlap between the live 30-day target and the latest monthly cohort is 0
names under `raw` and 0 under `vol_adj`.

### 3. API

`server.py:BacktestParams` gains:

```python
    signalFamily: Literal["raw", "vol_adj"] = "raw"
```

`TopPerformersParams` gains the same field, so the leaderboard can be put in the same mode
and the alignment stays checkable by eye. Both `_run_backtest` and `_top_performers` thread
`family=params.signalFamily` into every analysis call (`event_study`, `momentum_backtest` in
the holds loop, `recent_picks`, `cohort_sector_breakdown`, `live_rebalance`,
`top_performers_period`).

Response additions:

- `/api/backtest`: `"SIGNAL_FAMILY": params.signalFamily`,
  `"SIGNAL_WARMUP_MONTHS": analysis.signal_warmup_months(...)`,
  `"VOL_WINDOW_MONTHS": max(params.rankLookback, analysis.VOL_MIN_MONTHS)` when `vol_adj`
  else `None`.
- `/api/top-performers`: `"signal_family"`, `"vol_window_days"`, and `signal_value` /
  `vol_rank_ann` added to the `enrich()` projection for both `results` and `watchlist`.
- `/api/universes` `ranges` gains `"signalFamily": {"values": ["raw", "vol_adj"]}` so the
  client validator keeps its single source of truth. Note the shape is deliberately different
  from every other `ranges` entry (`{min, max}`), and `paramIssues`' `check()` helper is
  numeric-only (`Number.isFinite(v) || v < min || v > max`), so a string param routed through
  it would report `must be undefined–undefined`. Add a sibling helper instead:
  `checkOneOf('signalFamily', 'Signal family', (ranges.signalFamily || DEFAULT_RANGES.signalFamily).values)`.
  The `||` fallback matters: `ranges` comes from the server whenever `/api/universes`
  succeeded, and reading `ranges.signalFamily.values` unguarded would throw against any
  older server build, exactly the case the existing `ranges.skipMonths || DEFAULT_RANGES.skipMonths`
  line already defends against.
- **PERF `strategy` labels are unchanged** (`top{N}_hold{h}m`). V1/V2 derive `bestKey` with
  `/^top\d+_hold(\d+m)$/`; suffixing the family would break that regex and silently kill the
  drawdown and heatmap panels. The family is reported once, at the top level.

Status message when the family drops a material share of the universe. `_run_backtest` never
builds the signal frame itself (it only calls the consumers), so the counts have to be derived
explicitly rather than borrowed from an existing local:

```python
    if params.signalFamily == "vol_adj":
        vol_months = max(params.rankLookback, analysis.VOL_MIN_MONTHS)
        warmup = analysis.signal_warmup_months(
            params.rankLookback, params.skipMonths, family="vol_adj")
        last_row = analysis.momentum_signal(
            monthly, params.rankLookback, params.skipMonths, family="vol_adj").iloc[-1]
        n_ranked, n_total = int(last_row.notna().sum()), int(monthly.shape[1])
        on_status(f"vol_adj: vol window {vol_months}m, first usable cohort "
                  f"{warmup} months in ({n_ranked}/{n_total} tickers have a full vol window)")
```

That is one extra `momentum_signal` call per run (6 ms of `rolling.std` on top of the 1708 ms
`rolling.apply` the consumers already pay), and it only runs under `vol_adj`, so the `raw` path
gains no work at all.

### 4. UI

- `web/index.html`: `DEFAULT_PARAMS.signalFamily = 'raw'`; bump every asset's `?v=4` to `?v=5`
  (all four tags: `data-loader.js`, `charts.jsx`, `v1.jsx`, `v2.jsx`). The regime-filter spec
  in this same group also bumps to `?v=5`; whichever lands second takes `?v=6`, since a stale
  in-browser Babel transpile of `charts.jsx` against a new server response is the exact failure
  the query is there to prevent.
- `web/charts.jsx`:
  - `PARAM_HELP.signalFamily = 'Raw ranks on trailing return. Vol-adjusted divides that
    return by the name’s trailing annualized volatility (min 6 months), demoting spikes
    earned at extreme risk. This changes the actual buy list.'`
  - `DEFAULT_RANGES.signalFamily = { values: ['raw', 'vol_adj'] }`; `paramIssues` rejects any
    other value.
  - New `SignalFamilyToggle({ value, onChange, theme, accent, labelStyle })`: a 2-button
    segmented control styled like the existing hold-period buttons, labels `raw` and
    `vol-adj`. Registered in the end-of-file `Object.assign(window, {...})`.
  - `TopPerformersPanel`: `family` state **seeded from a new `signalFamily` prop** (default
    `'raw'`) so it starts in whatever mode the backtest is in, the toggle in its control row
    beside Top N / Days, passed to `fetchTopPerformers`; a new sortable "Signal" column
    (`signal_value`, 3 decimals) rendered only when `data.signal_family === 'vol_adj'`, and the
    Vol column tooltip gains "ranking used a {vol_window_days}-day vol window". The panel keeps
    its own state after seeding (it already owns Top N and Days), but the seed matters: the
    panel is a sibling of `RebalancePanel`, so a `vol_adj` live-target row sitting above a
    `raw` leaderboard reads as an I1 violation to the user even though the invariant holds at
    matching settings. The panel subtitle also renders the active family, so "matching
    settings" is visible rather than assumed.
  - `RebalancePanel`: new `family` and `volWindowMonths` props; subtitle says "ranked on
    trailing {rankLookback}m return divided by trailing {volWindowMonths}m volatility" when the
    family is `vol_adj`, and drops the existing ", the same signal as Top performers" clause
    when the leaderboard is not in the same family.
- `web/v1.jsx` / `web/v2.jsx`: the toggle under "Rank lookback" in both sidebars (`v1-family`,
  `v2-family` ids); header subtitle appends `· vol-adjusted` when active; V2's "Read this
  first" caveats block gains one line for `vol_adj` noting the shortened usable window.
- `web/v1.jsx:V1Dashboard` / `web/v2.jsx:V2Dashboard` are the **call sites** and must change
  too, which the touches list previously missed: `<TopPerformersPanel ... signalFamily={params.signalFamily} />`
  (v1.jsx around line 328, v2.jsx around line 361) and `<RebalancePanel ... family={data.SIGNAL_FAMILY}
  volWindowMonths={data.VOL_WINDOW_MONTHS} />` (v1.jsx around line 609, v2.jsx around line 739).
  The header subtitle lives in the dashboards, not the sidebars. Take `RebalancePanel`'s family
  from `data.SIGNAL_FAMILY` rather than `params.signalFamily` so an edited-but-unrun parameter
  cannot mislabel an already-rendered trade list.
- `web/data-loader.js`: no change (params are passed through wholesale).

### 5. Invariants

- **I1** is preserved structurally: the family is implemented in `top_performers_period` and
  reached by `current_target` through the same call, so at matching settings the live target
  is still literally `[r["ticker"] for r in top_performers_period(...)]`. A new test pins
  this under `vol_adj` alongside the existing `raw` one.
- **I2**: `family="raw"` is a pure pass-through on every path (no division, no window
  widening, no reordering), and the `streak` / `prior_rank` / `new_entrant` suppression is
  gated on `vol_adj` only, so the raw leaderboard keeps all three. New response fields are
  additive: `signal_value` equals `return_pct` under `raw`, `vol_rank_ann` and
  `VOL_WINDOW_MONTHS` are `None`. Both are JSON-null or plain floats, never NaN, so nothing
  reaches FastAPI's `allow_nan=False` encoder that could 500 the way a NaN series has before.
- **I3**: no lookahead (the denominator's window ends at the same bar as the numerator's, and
  is shifted by `skip_months` with it); NaN rather than `inf` or 0 at zero or short vol;
  short-history names excluded instead of ranked on 3 observations.
- **I4**: pure pandas inside `lib/analysis.py`, no new dependency, no build step, no
  execution surface. The output is still a list.
- **I5**: zero new network calls. The denominator comes from the same daily frame already in
  `cache/prices.parquet`. Airgapped behaviour is identical to today.

Cost: `rolling(6).std()` is 6 ms at 503 tickers x 300 months against 1708 ms for the existing
`rolling(L).apply(np.prod)`, so the family adds under 0.5% to signal construction. The
daily path adds one vectorized `pct_change().std()` over a 183-day x 503-column slice.

## Testing

Against `tests/test_analysis.py`'s existing fixtures:

1. `momentum_signal(_monthly_frame(), L)` and `momentum_signal(..., family="raw")` are
   `assert_frame_equal`-identical for `L=1` and `L=6` (I2 regression on the default path).
2. `momentum_signal(r, 3, family="vol_adj")` equals
   `momentum_signal(r, 3) / (r.rolling(6).std() * np.sqrt(12))` elementwise, and its first
   non-all-NaN row index is 5 (`max(3, 6) - 1`).
3. `momentum_signal(r, 8, family="vol_adj")` uses an 8-month vol window, not 6: equals
   `raw / (r.rolling(8).std() * np.sqrt(12))`.
4. Skip composes: `momentum_signal(r, 3, skip_months=1, family="vol_adj")` equals
   `momentum_signal(r, 3, family="vol_adj").shift(1)` (mirrors
   `test_momentum_signal_skip_shifts_window`).
5. Zero vol excluded: take `_monthly_frame()` and set `T0` to a constant 0.05, so at
   `rank_lookback=6` T0 has a trailing compounded return of 34.4% and zero vol.
   Verified on the fixture: under `raw`, `rebalance_schedule(r, top_n=3, rank_lookback=6,
   n_months=99)` yields 24 rows and T0 is in the picks of all 24 (it is the single top-ranked
   name in 20 of the 25 valid signal rows, not all of them, so the assertion has to be
   membership in the top 3 rather than rank 1); under `vol_adj`,
   `momentum_signal(r, 6, family="vol_adj").iloc[-1]["T0"]` is NaN and T0 appears in 0 rows.
6. The family changes the buy list: an 8-month, 2-ticker frame where T1 has the larger
   trailing 6-month return but 4x the monthly dispersion of T2. `nlargest(1)` of the raw
   signal is T1, of the vol_adj signal is T2.
7. `_ann_vol` equivalence: for every column of `_daily_prices([...])`,
   `_ann_vol(prices[t].loc[start_ts:end_ts])` equals
   `_pick_quality_stats(prices[t], start_ts, end_ts)["vol_ann"]` within 1e-15, and the
   frame form `_ann_vol(prices.loc[start_ts:end_ts])[t]` equals the same value.
8. Daily ranking: on `_daily_prices([f"T{i}" for i in range(6)], seed=1)`,
   `[r["ticker"] for r in top_performers_period(prices, top_n=3, days=30, family="vol_adj")]`
   equals a hand-computed `nlargest(3)` of
   `(end/start - 1) / (_ann_vol(prices.loc[end - 183d : end], min_obs=20))`, and each row's
   `signal_value` matches that ratio while `return_pct` still equals the plain window return.
   Note the fixture spans 2026-01-01 to 2026-07-02, i.e. 182 calendar days and 131 business
   bars, so `VOL_MIN_DAYS = 183` reaches past its first bar and the vol window is the whole
   fixture. That is fine (131 bars clears `min_obs=20`) but it must be asserted, not assumed:
   pin `len(prices) == 131` in the test so a future widening of `_daily_prices` cannot silently
   change what this test is measuring. `return_pct != signal_value` for at least one row is the
   assertion that catches the `top.items()` regression described in the design.
8b. Row-field separation, stated as its own case because it is the highest-risk edit: under
   `vol_adj`, `sum(abs(r["return_pct"] - r["signal_value"]) for r in rows) > 0` and every
   `return_pct` equals `float(end_row[t] / start_row[t] - 1)` recomputed from the frame. Under
   `raw`, `signal_value == return_pct` exactly for every row and `vol_rank_ann is None`.
8c. Diagnostics suppressed under `vol_adj`: every row has `streak is None`,
   `prior_rank is None`, `new_entrant is None`, while the same call with `family="raw"` returns
   integers/booleans for all three.
9. **I1 under vol_adj** (mirrors `test_current_target_matches_top_performers_signal`):
   `current_target(prices, top_n=3, rank_lookback=1, family="vol_adj")["tickers"]` equals
   `[r["ticker"] for r in top_performers_period(prices, top_n=3, days=30, family="vol_adj")]`,
   and `live_rebalance(..., family="vol_adj")[0]["picks"]` equals the same list.
10. Min-obs guard: a 12-bar daily frame gives every ticker fewer than 20 vol observations, so
    `top_performers_period(..., family="vol_adj")` returns `[]` rather than ranking on a
    3-observation vol, while `family="raw"` still returns rows.
11. Unknown family raises `ValueError` (guards a typo in a future caller rather than silently
    ranking raw).
12. `family="raw"` regression sweep: `event_study`, `momentum_backtest`, `recent_picks`,
    `rebalance_schedule`, `cohort_sector_breakdown`, `top_performers_period`,
    `current_target` and `live_rebalance` each return output equal to the call without the
    argument on the existing fixtures.

## Non-goals

- **Portfolio-level** vol scaling, which is what Barroso and Santa-Clara (2015) actually do:
  they scale the whole winners-minus-losers portfolio's weight by its realized vol, they do
  not re-rank names. Per-name scaling is a related but distinct intervention (closer to
  risk-adjusted or residual momentum). Constant-vol position sizing is a separate feature and
  needs a leverage decision the tool has no place making.
- Residual / idiosyncratic-vol momentum (needs factor returns and a rolling regression),
  beta adjustment, downside-only vol (semi-deviation), and EWMA vol.
- Other signal families the same seam now enables: 52-week-high proximity, absolute
  acceleration, blended composites.
- Changing `streak`, `prior_rank`, `new_entrant` or `leaderboardBadge`, which stay raw-return
  diagnostics.
- Any change to position sizing: the portfolio is still equal-weight.

## Risks / open questions

- **On this window, vol adjustment costs return and Sharpe.** Measured on the cached frame
  (`top_n=10`, `rank_lookback=1`, aligned on the 57 months both families cover):

  | sleeve | family | total | CAGR | vol | Sharpe | max DD |
  |---|---|---|---|---|---|---|
  | h6m | raw | +430% | 42.1% | 29.7% | **1.34** | -23.9% |
  | h6m | vol_adj | +167% | 23.0% | 18.4% | 1.22 | **-19.6%** |
  | h1m | raw | +434% | 42.3% | 35.8% | 1.17 | -27.2% |
  | h1m | vol_adj | +145% | 20.7% | 19.6% | 1.06 | -22.1% |
  | SPY | | +71% | 12.0% | 15.8% | 0.80 | -23.9% |

  So it does what it says (June cohort -11.1% instead of -27.2%, max drawdown 4.3pp
  shallower, vol cut by a third) and it costs 0.12 of Sharpe and most of the upside. This is
  a risk-preference switch, not a free improvement, and the UI copy must say so rather than
  implying vol_adj is the "better" setting. **Open question for the user: should the default
  stay `raw`?** I2 says yes; the answer may be different once they have looked at both.
- **Vol window rule.** `max(rank_lookback, 6)` versus a fixed 6 months regardless of
  lookback (the literal Barroso and Santa-Clara choice, which pairs a 12-1 numerator with a
  126-day denominator). One constant either way. Settled as `max(...)` so the risk measure
  covers the same period the return was earned over, but this is genuinely arguable and is
  the user's call.
- **Negative cross-sections invert the vol preference.** For a negative numerator,
  `-0.05 / 0.10 = -0.50` ranks *below* `-0.05 / 0.40 = -0.125`, so in a month where the whole
  cross-section is down the ratio promotes the *highest*-vol names. Momentum top-N is almost
  always positive-signal (it was in every cohort on the cached window), so this rarely binds,
  but it is a real property, not a bug to be surprised by later. Options if the user wants it
  closed: restrict `vol_adj` ranking to names with a positive numerator, or rank on
  `R - lambda * sigma`. Left open deliberately.
- 5 monthly bars of extra warm-up at `rank_lookback=1` shortens the backtest window and
  therefore the event-study cohort count, which weakens the significance evidence the verdict
  line leans on (currently 7 of 12 horizons clear |t| >= 2, strongest h=11 at t=4.34). Worth
  a UI note when `vol_adj` is active.
- The monthly and daily vol estimators rank the universe at 0.73 rank correlation, so a name
  can sit inside the monthly backtest's cohort and outside the live target's. This is
  disclosed, not fixed; fixing it means computing the monthly signal's denominator from daily
  bars, which would break `momentum_signal`'s "pure function over monthly frames" contract.
- **The two frequencies disagree about what "enough observations" means, and the daily side
  leans the wrong way.** The monthly path requires the *full* window (pandas' `min_periods ==
  window`), so a short-history name is dropped. The daily path requires only 20 returns inside a
  183-day window that normally holds about 128, so a name listed 5 weeks ago is ranked on a
  1-month vol estimate against peers' 6-month estimates. `_slice` only drops all-NaN columns, so
  those names are genuinely in the frame. Small-sample vol estimates are noisier, and the
  `vol_adj` ranking rewards a *small* denominator, so the noise is not symmetric: it
  systematically pushes thin-history names up the list, which is the opposite of what the
  "don't rank on 3 observations" decision was for. Mitigation options, neither taken here: raise
  `VOL_MIN_OBS` to a coverage fraction of the bars actually available in the window (roughly
  `0.8 * len(v_rows)`), or reuse the 10-day reach-back tolerance `_window_return_top_set` and
  `_pick_quality_stats` already apply and require the vol window's first bar to be within 10
  days of its target start. **Open question for the user: which, or accept 20 as-is?**
- **The prior-window diagnostics have no `vol_adj` definition yet.** `streak`, `prior_rank` /
  `rank_delta` and `new_entrant` all come from raw-return rankings of earlier windows, so under
  `vol_adj` they compare two different metrics. This spec suppresses all three to `None` rather
  than shipping a wrong NEW badge on every row, which costs the whole rank-velocity column in
  `vol_adj` mode. The alternative is to thread `family` into `_window_return_top_set` and the
  prior-rank block so the history is ranked the same way as the present, which is correct but
  adds 12 extra vol computations per refresh (one per prior window) on a 183-day x 503-column
  slice. **Open question for the user: suppress (as specced) or pay for family-consistent
  history?** If they pick the latter, `streak`'s meaning changes from persistence-of-the-move
  to persistence-of-the-pick, which is arguably what the column should have measured anyway.
- Two independent family controls exist by construction: `BacktestParams.signalFamily` drives
  the trade list and `TopPerformersParams.signalFamily` drives the leaderboard, because
  `TopPerformersPanel` owns its own fetch and refreshes without a backtest run. The seed prop
  and the visible family label are the mitigation. I1 is stated "at matching settings", and
  this is the one place where a user can put the two panels out of matching settings on purpose.
- I2 is read here as "additive": under `raw` every existing key keeps its exact value, but the
  responses do gain `SIGNAL_FAMILY`, `SIGNAL_WARMUP_MONTHS`, `VOL_WINDOW_MONTHS`,
  `signal_family`, `vol_window_days` and the two per-row fields, so the JSON is not literally
  byte-identical. That matches the precedent set by the quick-wins batch. Flag if the user
  wants the stricter reading, in which case the new keys have to be gated on `vol_adj`.
