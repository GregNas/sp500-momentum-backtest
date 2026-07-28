# Regime filter and live momentum-crash banner

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** 2026-07-28-per-pick-cohort-attribution (for the MTD partial-row plumbing)

## Problem

The tool is living the exact scenario it has no vocabulary for. On the 2026-07-28 run the
best-Sharpe sleeve (h6m, Sharpe 1.33 versus SPY 0.87) is **-14.2% month-to-date** against SPY
**-1.2%**, the 2026-06 cohort has realized -27.2%, and
`web/charts.jsx:RebalancePanel` presents the live row as "10 in, 10 out, 0 unchanged": a 100%
turnover instruction, sitting directly under a +470% equity curve, with no indication that the
strategy is mid-unwind.

Worse, the tool cannot tell the user *what kind* of drawdown this is. Recomputed from the
cached daily benchmark series (`cache/prices.parquet`, SPY column, last valid bar 2026-07-23):
SPY closes at 738.18 against a 200-day moving average of 695.06, i.e. **+6.2% above** its
200dma, it has been above it for 106 days (since 2026-04-08), and its trailing-252-day
drawdown is only **-2.6%**. The market is fine. The strategy is down 13.1pp against it in
three weeks. This is a momentum-factor unwind, not a market regime event, and those two
situations call for opposite responses. Nothing in `server.py:_run_backtest`'s response says
which one is happening, and nothing in `PERF` shows what a regime-conditioned version of the
strategy would have done.

`benchmark_prices = prices[params.benchmark]` (server.py:122) is already the daily series a
200dma needs, and it is currently used for exactly one thing: `resample("ME").last()`.

## Goal

Two deliverables from one piece of plumbing. (1) An opt-in backtest option: when the
benchmark closes below its 200-day moving average at a month-end, the following month holds
cash or the benchmark instead of the momentum sleeve, with filtered and unfiltered equity
curves plotted together and both reported in `PERF`. (2) A live banner above the trade list
that states the benchmark-versus-200dma state, the benchmark's own trailing drawdown, the
strategy-minus-benchmark month-to-date spread, and **what actually happened next in
historically matching conditions**, with n and a HAC t-stat, so the badge is evidence rather
than a mood. After this ships, a 100%-turnover trade list can no longer be read without the
context that explains it.

## Design

### 1. Regime primitives in `lib/analysis.py`

```python
REGIME_MA_DAYS = 200

def regime_state(benchmark_prices: pd.Series, ma_days: int = REGIME_MA_DAYS) -> pd.DataFrame:
    """Daily frame: ``ma`` (trailing ``ma_days``-bar mean of the close), ``above``
    (close >= ma), ``dist`` (close / ma - 1). NaN/NA before the MA has a full window."""
    s = benchmark_prices.dropna()
    ma = s.rolling(ma_days).mean()
    above = (s >= ma).astype("boolean").where(ma.notna())   # nullable, NA in warm-up
    return pd.DataFrame({"close": s, "ma": ma, "dist": s / ma - 1, "above": above})
```

The explicit `"boolean"` dtype matters. `(s >= ma).where(ma.notna())` alone silently produces
`object` dtype holding `np.nan`, and the two dtypes behave *oppositely* under the natural
`.where(decision != False, ...)` idiom (verified on pandas 2.3.3: object keeps the warm-up
month invested, nullable boolean zeroes it). `apply_regime` below is written so both are safe,
but the dtype is pinned here so a future refactor cannot flip the warm-up semantics by accident.

`ma_days` counts **bars, not calendar days**: the series is daily closes with weekends and
holidays absent, so a 200-bar mean is the conventional 200-day MA.

```python
def regime_mask(benchmark_prices, monthly_index, ma_days: int = REGIME_MA_DAYS) -> pd.Series:
    """Risk-on flag AT each month-end label in ``monthly_index``.

    Value at label t = whether the last benchmark bar at or before t closed at or
    above its ``ma_days``-bar moving average, using only bars <= t. pd.NA while
    the MA has no full window. This is a decision made ON t; applying it to
    month t's own return would be lookahead, so every consumer shifts it.
    """
    st = regime_state(benchmark_prices, ma_days)
    return st["above"].reindex(monthly_index, method="ffill")
```

`method="ffill"` picks the last bar at or before each label, which also handles the
in-progress month correctly: the newest monthly label is a future month-end, so the mask uses
the latest bar that exists. No lookahead by construction.

```python
def apply_regime(returns: pd.Series, mask: pd.Series,
                 replacement: float | pd.Series = 0.0) -> pd.Series:
    """Month t's exposure is decided by the regime state at month t-1's close.

    The month is replaced (0.0 for cash, the benchmark's return for 'benchmark'
    mode) only where the prior month-end was *explicitly* risk-off. NA (MA
    warm-up) leaves the sleeve invested and is counted in ``warmup_months``,
    never silently zeroed.
    """
    decision = mask.shift(1).reindex(returns.index)         # shift on the MONTHLY index
    off = decision.eq(False).fillna(False).astype(bool)     # NA is not "off"
    repl = (replacement.reindex(returns.index) if isinstance(replacement, pd.Series)
            else pd.Series(replacement, index=returns.index))
    return returns.mask(off, repl)
```

`decision.eq(False).fillna(False)` is the load-bearing line, and it is written this way because
it is the only formulation that is correct for both dtypes: `~decision` propagates NA, and
`.where(decision != False, repl)` gives opposite answers for `object` and `boolean` masks
(verified). Building the "off" mask positively, then defaulting NA to "not off", makes
"warm-up means invested" explicit instead of dtype-dependent.

Timing, stated once so nobody has to re-derive it: `momentum_backtest`'s value at monthly
label `M` is the return realized **during** `M`, from a signal formed at `M-1`. The regime
decision for `M` must therefore be the 200dma state at `M-1`'s close, which is exactly
`mask.shift(1)[M]`. Nothing later than `M-1`'s last bar touches month `M`.

**Shift before reindex, not after**, and this is the second load-bearing detail. `mask` is
indexed on `monthly.index`, while `returns.index` is a strict subset of it: `momentum_backtest`
starts at `rank_lookback + skip_months` and appends a label only when at least one sleeve
produced a return. Shifting after reindexing shifts *inside the subset*, which is wrong twice
over. The first strategy month always lands on NA and therefore always stays invested even
though the mask at the preceding monthly label is perfectly well known, and any month the
strategy skipped becomes a hole that makes the shift reach two labels back. Verified on pandas
2.3.3 with mask `[NA, NA, True, False, True, False, True, True]`, returns 0.01 to 0.08 and
label 5 absent from `returns.index`: reindex-then-shift gives
`[0.01, 0.02, 0.03, 0.04, 0.0, 0.0, 0.08]`, zeroing month 6 off `mask[3]`, while
shift-then-reindex gives `[0.01, 0.02, 0.03, 0.04, 0.06, 0.0, 0.08]`, which is the intended
`decision[M] == mask[M-1]`. The two orderings agree on a gapless index, which is exactly why
this is easy to ship broken.

```python
def regime_conditional_stats(strategy_returns, mask, benchmark_monthly) -> dict:
    """What happened NEXT, split by the regime state that was observable then.

    {"risk_on"|"risk_off"|"warmup": {n, mean, median, bench_mean, spread_mean,
     t_stat, t_stat_spread}} where t_stat is the Newey-West HAC t-stat from
    :func:`_newey_west_tstat` (overlapping sleeves are autocorrelated, so the
    plain t-stat overstates significance) and ``spread`` is strategy minus the
    same-month benchmark, matching the event study's time-aligned convention.

    Buckets are formed from ``mask.shift(1).reindex(returns.index)``, the exact
    same decision series :func:`apply_regime` uses, so the banner's evidence and
    the filtered curve can never disagree about which months were risk-off.
    """
```

```python
def regime_snapshot(benchmark_prices, ma_days=REGIME_MA_DAYS,
                    dd_window_days: int = 365) -> dict:
    """Live regime state: {as_of, close, ma, dist, above, days_in_state,
    state_since, drawdown, ma_days}. ``days_in_state`` counts calendar days
    since the first bar of the current above/below run; ``drawdown`` is
    close / trailing-``dd_window_days`` max - 1. Fields are None (never 0.0)
    when the MA has no full window yet.

    ``as_of`` and ``close`` are the exception: they are the last *valid*
    benchmark bar and are always reported, MA window or not, because the banner
    needs a date even when it has no state to show."""
```

**No NaN may escape any of these functions.** This is not a style preference. `REGIME` is a
dict of scalars, `_safe_floats` only handles series, and FastAPI serializes with
`allow_nan=False`, so one `float("nan")` raises
`ValueError: Out of range float values are not JSON compliant` and 500s the entire backtest.
This repo has already been bitten by that exact failure. The NaN sources here are concrete, not
hypothetical:

- `_newey_west_tstat` returns `np.nan` whenever `n < 2` or `var <= 0`. The `warmup` bucket is at
  n=0 or n=1 on most runs, and a short `lookback` leaves `risk_off` at n=0 too.
- `mean` / `median` / `bench_mean` / `spread_mean` of an empty bucket are NaN.
- `regime_snapshot`'s `ma`, `dist` and `drawdown` are NaN while the MA has no full window, which
  is the default state of any run shorter than about 10 months.
- `realized_vol_percentile`'s `vol_ann` / `percentile` / `median` / `p90` are NaN on a series
  shorter than `window + 1` bars.

So every scalar leaves these functions through one shared helper,
`_finite_or_none(v) -> float | None` (`None` for `None`, NaN and +/-inf), and an n=0 bucket is
emitted as `{"n": 0}` with `None` for every statistic rather than omitted, so `RegimeBanner` can
render "no comparable months in this window" instead of tripping over a missing key. A test
asserts `json.dumps(response["REGIME"], allow_nan=False)` succeeds on a fixture short enough
that all four NaN sources are live at once.

Numpy and pandas scalars need the same discipline, and the failure modes differ in a way that
matters. Verified against this repo's FastAPI on pandas 2.3.3:

| value | result |
|---|---|
| `np.float64(1.5)` | fine, encodes as `1.5` |
| `float("nan")` | `ValueError: Out of range float values are not JSON compliant` |
| `np.int64(3)` | `ValueError` out of `jsonable_encoder` |
| `np.bool_(True)` | `ValueError` out of `jsonable_encoder` |
| `pd.NA` | **silently encodes as `{}`** |

The last row is the dangerous one: `above: pd.NA` does not crash, it reaches the browser as an
empty object, which is *truthy* in JavaScript while failing both `above === true` and
`above == null`, so the banner falls straight through to a green RISK ON badge on a run with no
MA coverage at all. So: every count (`n`, `warmup_months`, `coverage_months`, `days_in_state`)
is wrapped in `int()`, every boolean in `None if pd.isna(v) else bool(v)`, and every float in
`_finite_or_none`. This is the same coercion discipline `_run_backtest`'s existing
`int(constituents.shape[1])` and `float(v) if isinstance(v, (int, float, np.floating, np.integer))`
lines already apply, and `REGIME` is the first payload in this repo that is a dict of scalars
rather than a series, so none of that existing machinery covers it.

```python
def realized_vol_percentile(benchmark_prices, window: int = 21) -> dict:
    """{vol_ann, percentile, n, median, p90} for the benchmark's trailing
    ``window``-bar realized vol against its own cached history."""
```

This is the airgap-safe substitute for a VIX strip: it needs no new ticker at all. On the
current cache it reads SPY 21-bar realized vol 11.4% annualized, **32nd percentile** of 1294
bars (median 13.4%, 90th 24.8%), which is the quantitative form of "the market is calm and
this is your problem, not the market's".

### 2. Params

`server.py:BacktestParams` gains:

```python
    regimeMode: Literal["off", "cash", "benchmark"] = "off"
    regimeMaDays: int = Field(200, ge=20, le=400)
```

Default `"off"` means no filtered series, no `_filt` keys, no PERF rows, byte-identical
response (I2). `REGIME` (the banner payload) is computed **always**, because it is pure
diagnostics over data already in hand and costs one `rolling(200).mean()` on a single column;
it is additive, so it does not violate I2 either.

`server.py:universe_info` must gain `"regimeMaDays": {"min": 20, "max": 400}` in its `ranges`
dict, and this is a hard requirement rather than tidiness. `paramIssues` reads
`ranges = universesInfo.ranges` whenever `/api/universes` succeeded and then dereferences
`ranges.<key>.min`; with `regimeMaDays` absent from the server payload,
`check('regimeMaDays', ..., ranges.regimeMaDays.min, ...)` throws a TypeError inside the render
path and takes the Run button with it. Belt and braces, mirror the existing defence on the
neighbouring line: `const maRange = ranges.regimeMaDays || DEFAULT_RANGES.regimeMaDays;`.
`regimeMode` is a string, so it needs the same non-numeric membership check the
signal-family spec adds (`check()` is `Number.isFinite`-based), plus a
`"regimeMode": {"values": ["off", "cash", "benchmark"]}` entry in `ranges`. `server.py:universe_info`
is therefore in the touches list.

No confirmation-band parameter. Evidence: the raw 200dma crossing whipsaws badly intraday
(2025-03-10 below, 03-24 above, 03-26 below, 05-12 above, 4 flips in 63 days), but sampling it
only at month-ends already collapses that to 13 risk-off month-ends in 6 distinct episodes
over the 54 covered months. Month-end sampling *is* the confirmation filter, so a band would
add a knob without adding information.

### 3. Wiring in `_run_backtest`

```python
    regime_mask = analysis.regime_mask(benchmark_prices, monthly.index,
                                       ma_days=params.regimeMaDays)
    ...
    filtered_returns: dict[int, pd.Series] = {}
    if params.regimeMode != "off":
        repl = 0.0 if params.regimeMode == "cash" else benchmark_monthly
        for h, r in strategy_returns.items():
            filtered_returns[h] = analysis.apply_regime(r, regime_mask, repl)
```

- `EQUITY` / `DRAWDOWN` / `MONTHLY_RETURNS` / `ROLLING_SHARPE` / `YEARLY_RETURNS` gain
  `f"h{h}m_filt"` keys, built by the same loops (`_safe_floats`, `_drawdown`,
  `rolling_sharpe`, `calendar_year_returns`) so no new coercion logic appears.
- Reusing those loops has one consequence that must be decided, not inherited. In `benchmark`
  mode the replacement is the **unfilled** `benchmark_monthly` (see the risks section), so a
  month Yahoo never returned stays NaN. `perf_stats` does `returns.dropna()` and drops it, but
  `_safe_floats` maps NaN to 0.0 and `_drawdown` does `.fillna(0)`, so the same month appears
  as a fabricated flat 0% in `MONTHLY_RETURNS`, `EQUITY` and the heatmap. Total return happens
  to agree either way (multiplying by 1 and skipping are the same thing) but `months`, `vol` and
  `Sharpe` in the PERF row are then computed over a different sample than the chart draws, and
  the heatmap shows a grey-zero cell for a month that has no data. Settled as: keep the shared
  loops, and have `_run_backtest` emit a status message naming those months
  (`f"Benchmark {params.benchmark}: {n} month(s) shown as flat in the filtered series"`) reusing
  the `n_missing` count it already computes at line 167. Flag if the user would rather see the
  filtered series truncated at the first missing benchmark month.
- `PERF` gains one row per filtered sleeve, `{"strategy": f"top{N}_hold{h}m", "filtered":
  True, **<the same float-coerced perf_stats spread the existing rows use>}`. **The `strategy`
  label is not suffixed**, and every existing row gets `"filtered": False`. Reason: V1 and V2
  both compute `bestKey = bestStrat.strategy.replace(/^top\d+_hold(\d+m)$/, 'h$1')`, and a
  `_filt` suffix would fail that anchored regex, leaving `bestKey` as the raw label and silently
  blanking the drawdown chart (`data.DRAWDOWN[bestKey]` undefined) and the monthly heatmap. Both
  dashboards instead change their best-sleeve selector to
  `data.PERF.filter(p => p.strategy !== params.benchmark && !p.filtered)`.
- Not suffixing the label has a cost that has to be paid explicitly, in **three** places, not
  one:
  1. `web/v1.jsx`'s PERF table (around line 578) and `web/v2.jsx`'s "All sleeves" table (around
     line 571) both render `<tr key={p.strategy}>`. Two rows now share a `strategy`, so React
     gets duplicate keys and may reconcile the wrong row. Change both to
     `key={p.strategy + (p.filtered ? '_filt' : '')}`.
  2. V1's `isBest = p.strategy === bestStrat?.strategy` would highlight the filtered row as
     the best sleeve as well, since it matches on the label alone. Compare both fields:
     `p.strategy === bestStrat?.strategy && !p.filtered`.
  3. V2's "All sleeves" table renders `p.strategy.replace(/^top\d+_hold/, 'h')` with no other
     distinguishing text, so it would show two rows both reading `h6m` with different Sharpes
     and no explanation. V1 groups filtered rows under a "regime-filtered" subheading; V2 needs
     the equivalent, at minimum a `· filt` suffix on the rendered label. `web/v2.jsx`'s PERF
     table is therefore in the touches list alongside its `bestStrat` selector.
- New top-level `REGIME` key:

```python
"REGIME": {
    "mode": params.regimeMode,
    "ma_days": params.regimeMaDays,
    "as_of": "2026-07-23",              # last VALID benchmark bar, not prices.index.max()
    "close": 738.18, "ma": 695.06, "dist": 0.0620, "above": True,
    "days_in_state": 106, "state_since": "2026-04-08",
    "drawdown": -0.0257,
    "vol": {"vol_ann": 0.114, "percentile": 0.32, "n": 1294,
            "median": 0.134, "p90": 0.248},
    "warmup_months": 9, "coverage_months": 54,
    # month-END labels at which the signal read risk-off, i.e. DECISION dates.
    # "2022-02" here means the 2022-02 close was below the MA and therefore
    # 2022-03 is the month that gets replaced. Named _month_ends, not _months,
    # because "risk_off_months" reads like "the months that were skipped" and
    # the off-by-one between the two readings is the whole timing bug this
    # design is trying not to ship.
    "risk_off_month_ends": ["2022-02", "2022-04", ...],
    "mtd": {"sleeve": "h6m", "strategy": -0.1421, "benchmark": -0.0115,
            "spread": -0.1306, "as_of": "2026-07-27", "is_partial": True},
    "conditional": { "risk_on": {"n": 40, "mean": 0.0305, "bench_mean": 0.0069,
                                 "spread_mean": 0.0236, "t_stat_spread": 2.47}, ... },
},
```

  `mtd` reuses what already exists rather than recomputing prices: the strategy MTD is the
  best-Sharpe sleeve's last monthly value when `constituents.index.max() < monthly.index[-1]`
  (the same partial-month test `_run_backtest` already applies to `cohorts[0]["fwd_partial"]`),
  and the benchmark MTD is `benchmark_monthly` at that same label. `is_partial=False` and the
  last completed month are used when the run happens to land on a month-end.

  "Best-Sharpe sleeve" does not exist server-side today: `bestStrat` is a client-side
  computation in V1/V2 over `data.PERF`. Both `mtd.sleeve` and `conditional` need it, so
  `_run_backtest` has to pick it explicitly, and it has to pick it by the **same rule the
  dashboards use** or the banner will attribute numbers to a sleeve the header is not showing.
  Reuse the `perf_rows` already built a few lines above rather than recomputing:

```python
    unfiltered = [p for p in perf_rows
                  if p["strategy"] != params.benchmark and not p["filtered"]]
    best = max(unfiltered, key=lambda p: p["Sharpe"], default=None)
    best_h = (int(best["strategy"].rsplit("_hold", 1)[1][:-1]) if best else None)
```

  That mirrors V1/V2's `filter(p => p.strategy !== params.benchmark && !p.filtered).sort((a,b) => b.Sharpe - a.Sharpe)[0]`.
  When `params.holds` has one entry the two agree trivially; with 3 sleeves they must be kept in
  step, so a comment on both sides is warranted. If `unfiltered` is empty (impossible today,
  since `_run_backtest` 400s on an empty backtest) `mtd` and `conditional` are `None`.

  Note the `as_of` asymmetry, which is real on the current cache and must not be papered over:
  SPY's last valid bar is 2026-07-23 while the constituent frame reaches 2026-07-27 (2026-07-24
  has 1 non-null constituent), so the regime `as_of` can trail the leaderboard's `as_of`. Both
  dates are reported; the banner shows the regime one.

All numeric fields are `None` when unavailable (MA warm-up, no partial month), never `0.0`.
`_safe_floats` is for series and is not applied here (I3: a missing 200dma is not a flat one),
which is exactly why `REGIME` needs its own scalar coercion, see section 1.

### 4. UI

New `RegimeBanner({ regime, benchmark, theme, accent, headingFont, compact })` in
`web/charts.jsx`, added to the end-of-file `Object.assign(window, {...})` (a component absent
from that list is invisible to v1 and v2).

Three-state badge, driven by data, not by a threshold on vibes:

| condition | badge | copy |
|---|---|---|
| `above == null` | `NO REGIME DATA` (grey) | `{benchmark} history covers {coverage_months} of {ma_days}d MA warm-up: no state yet. Raise Lookback.` |
| `above === false` | `RISK OFF` (amber) | `{benchmark} {dist} below its {ma_days}d MA, {days_in_state}d, benchmark drawdown {drawdown}` |
| `above === true` and `mtd?.spread <= -0.05` | `FACTOR UNWIND` (red) | `{benchmark} is {dist} ABOVE its MA and only {drawdown} off its high: the drawdown is momentum-specific, not the market` |
| otherwise | `RISK ON` (green) | state line only |

The first row is not padding. `above` is NA for the whole of any run shorter than about 10
months, and `above === false` / `above === true` are both false against `null`, so without an
explicit null state the banner falls through to a green `RISK ON` badge asserting a state the
data cannot support. That is the same class of fabrication I3 forbids in the series. For the
same reason the `FACTOR UNWIND` test must use `mtd?.spread`, since `mtd` is `null` on a run that
lands exactly on a month-end.

`above` also needs a type coercion on the way out: `regime_snapshot` reads it off a
`dtype="boolean"` column, and `pd.NA` is not JSON-serializable (and `bool(pd.NA)` raises), so it
must leave Python as a real `bool` or `None`, e.g. `None if pd.isna(v) else bool(v)`.

Second line is always the evidence, never advice: `In the {n} prior months that began
{risk-on|risk-off}, this sleeve returned {mean}/mo on average versus {bench_mean} for
{benchmark} (spread {spread_mean}, t={t_stat_spread}).` With `n === 0` or a `null` `t_stat` it
reads `No comparable months in this window` instead. Third line is the vol percentile
strip. When `warmup_months > 0` the banner appends `first {warmup_months} months predate
{ma_days}dma coverage and are counted as invested`.

Placement:

- `RebalancePanel` gains a `regime` prop and renders `<RegimeBanner compact={false} .../>`
  immediately **above** the live-target card (inside the panel, above the "live / current
  target, N in, N out" block). This is the one placement that matters: it is the context the
  trade list is currently missing. The prop has to be threaded at both call sites,
  `<RebalancePanel ... regime={data.REGIME} />` in `V1Dashboard` (around line 609) and
  `V2Dashboard` (around line 739); the component takes no `data` object today.
- V1 header: a compact banner beside the status pill (`web/v1.jsx`, the `role="status"` block
  around line 347). V2: same, in the editorial header bar's right-hand stat cluster
  (`web/v2.jsx` around line 398).
- `EquityCurveChart` needs no change. V1/V2 `equitySeries` builders append
  `{ name: 'h{h}m · filtered', values: data.EQUITY['h{h}m_filt'], color: <same hue>,
  dashed: true }` when the key exists, plus a "filtered" checkbox next to the existing
  log / fill controls so the chart does not double in line count unasked.
- `PARAM_HELP.regimeMode` / `.regimeMaDays`, `DEFAULT_RANGES.regimeMaDays = {min: 20, max: 400}`
  and `DEFAULT_RANGES.regimeMode = {values: [...]}`, `paramIssues` validates both (numeric
  `check` for the MA length, a membership check for the mode, both reading through the
  `ranges.X || DEFAULT_RANGES.X` fallback); `DEFAULT_PARAMS` in `web/index.html` gains
  `regimeMode: 'off', regimeMaDays: 200`; all four asset tags bumped from `?v=4` to `?v=5`.
  The vol-adjusted-signal spec in this same group also bumps to `?v=5`; whichever lands second
  takes `?v=6`.
- Sidebar control: a 3-way segmented control (`off` / `cash` / `bench`) under "Skip months",
  plus a `NumField` for the MA length shown only when the mode is not `off`.

### 5. Optional VIX strip

Deliberately **optional and second**. `realized_vol_percentile` covers the same need with zero
new data, so VIX is an upgrade, not a dependency.

If added: `vixStrip: bool = False` on `BacktestParams`, and `REGIME["vix"] = {level, percentile,
as_of}` when the column exists, omitted with a status message when it does not (delisted symbol,
Yahoo refusal, airgapped cache miss). Three things must be handled first:

- **`^VIX` must NOT simply be appended to `_run_backtest`'s ticker list.** Traced through the
  real code: `tickers = universe_tickers + [params.benchmark]` goes to `data.get_prices`, which
  goes to `cache.get_prices_cached`. A ticker absent from `prices_meta.json["tickers"]` lands in
  `missing_tickers` and forces `fetcher(["^VIX"], full_start, full_end)`;
  `data._fetch_prices` calls `yf.download` and propagates any exception; `_run_backtest` wraps
  the whole pull in `except Exception -> HTTPException(502, "Data fetch failed")`. So on an
  airgapped or cache-only box, ticking one optional decorative strip **502s the entire
  backtest**, which is precisely the I5 failure this feature claims not to have. The only
  correct shape is a second, separately guarded call:

```python
    vix = None
    if params.vixStrip:
        try:
            vix = data.get_prices(["^VIX"], start_str, end_str, on_status=on_status)["^VIX"]
        except Exception as exc:
            on_status(f"VIX strip unavailable ({exc}); regime banner continues without it")
```

  The good news, verified in `cache.get_prices_cached`: a missing ticker triggers a fetch for
  *only* that ticker over the union range, not a full 504-ticker re-download, and toggling the
  flag back off is a plain cache hit because `_save` keeps the column. So the cache cost is one
  small fetch, once.
- Two more things:

- `lib/cache.py:_detect_split_artifacts` flags any bar-to-bar ratio above
  `SPLIT_JUMP_HI = 1.40` or below `SPLIT_JUMP_LO = 0.72` as a split-basis artifact and triggers
  a full-history re-fetch through `_heal_split_artifacts`. VIX routinely clears both bounds in
  one session, so every delta fetch would "heal" it: a wasted full re-download plus a
  misleading `Healing 1 split-adjusted ticker(s)` status message on the dashboard. Fix:
  `NO_SPLIT_HEAL = frozenset({"^VIX"})` plus a `tk.startswith("^")` guard (index symbols do not
  split), skipped at the top of the per-ticker loop in `_detect_split_artifacts`, and covered
  by a test in `tests/test_cache_heal.py` using the existing injectable `fetcher` seam.
- VIX is an index level, not a price. `_fetch_prices` uses `auto_adjust=True` and keeps only
  `Close`, which is harmless for an index, but the column must never reach
  `to_monthly_returns` or the ranking universe. It is fetched alongside the benchmark and
  dropped from `constituents` the same way `params.benchmark` is.

### 6. Invariants

- **I1**: untouched. No ranking logic changes; the regime mask is applied to realized sleeve
  returns after selection, and the live target still comes from `current_target` through
  `top_performers_period`. The banner is presentation over the same numbers.
- **I2**: `regimeMode="off"` produces no `_filt` keys, no extra `PERF` rows and unchanged
  values. `REGIME` and `PERF[*].filtered` are additive.
- **I3**: the mask is computed only from bars at or before each month-end and applied via
  `.shift(1)` to the following month, so no lookahead. Warm-up months are `pd.NA` and stay
  invested rather than being zeroed. Conditional stats use `_newey_west_tstat` on the
  time-aligned spread, consistent with `event_study_summary`. Missing values are `None`.
- **I4**: no broker, no orders. The banner adds context to a list; it never sizes, times or
  places anything. Still local, still no build step.
- **I5**: the 200dma, the drawdown, the vol percentile and the MTD spread all come from
  `cache/prices.parquet` data already fetched for the benchmark. **Zero new runtime network
  dependency.** The optional `^VIX` strip is the only network-touching part: it is off by
  default and, in the guarded form specified in section 5, degrades to an omitted field plus a
  status message when the symbol is absent, so an airgapped or cache-only deployment loses one
  strip and nothing else. Riding the existing `yf.download` call instead would fail the whole
  request with a 502 on an airgapped box, which is why that shortcut is explicitly rejected.

Cost: one `rolling(200).mean()` on a single ~1300-bar column, plus one `apply_regime` pass per
sleeve. Immeasurable next to the 400 ms signal build.

## Testing

1. **No lookahead, pinned exactly.** Build a benchmark series that crosses below its MA on a
   known date, e.g. `bdate_range("2023-01-01", "2026-07-02")` rising linearly then falling.
   Assert `regime_mask(bench, monthly.index)[label]` equals
   `bench.loc[:label].iloc[-1] >= bench.loc[:label].iloc[-ma_days:].mean()` **for every label
   with at least `ma_days` bars at or before it**, and `pd.isna(...)` for the ones before that.
   The unconditional form fails as written: at `ma_days=200` on a series starting 2023-01-01 the
   first 9 monthly labels are inside the warm-up, where the mask is `pd.NA` and the right-hand
   side evaluates to `False` (a `>=` against a NaN mean), so `NA != False` trips the assertion on
   month 1. Then assert `apply_regime(r, mask, 0.0)` zeroes month `M` when `mask[M-1]` is False
   while leaving `M` untouched when only `mask[M]` is False. A deliberately mutated variant that
   uses `mask` unshifted must fail this test.
1b. **Shift order.** Drop one label from `returns.index` (simulating a month where no sleeve
   produced a return) and assert the month *after* the hole is decided by its own predecessor's
   mask value, not by the label two back. This is the case that separates
   `mask.shift(1).reindex(...)` from `mask.reindex(...).shift(1)`; both pass on a gapless index,
   so without the hole the test proves nothing. Also assert the **first** month of
   `returns.index` is decided by `mask` at the preceding monthly label rather than defaulting to
   invested.
2. **Warm-up stays invested, for both mask dtypes.** With `ma_days` longer than the fixture's
   history the mask is all-NA and `apply_regime(r, mask)` returns a series equal to `r`
   (`pd.testing.assert_series_equal`), not a series of zeros. Repeat the assertion with the
   mask cast to `object` and to `"boolean"`, since those two dtypes diverge under the naive
   `.where(decision != False, ...)` idiom and this test is what stops a refactor from silently
   zeroing 9 months of the curve.
3. **Cash versus benchmark replacement.** With a hand-built 6-month mask
   `[True, False, True, False, True, True]`, `apply_regime(r, mask, 0.0)` has 0.0 at exactly
   the two months following a False, and `apply_regime(r, mask, bench)` has `bench`'s value at
   those months.
4. **Mask reads the last bar at or before the label.** On the existing `_daily_prices` fixture
   (last bar 2026-07-02, mid-month), the newest monthly label's mask equals the state at
   2026-07-02, and `regime_snapshot(...)["as_of"]` is 2026-07-02. Use `ma_days=20` here, not the
   200 default: that fixture is 131 business bars, so at `ma_days=200` every value is `pd.NA` and
   the test would pass vacuously without ever exercising the ffill. `as_of` is asserted at both
   MA lengths, since it is the last valid bar regardless of MA coverage.
5. **`regime_state` boundary.** `above` is True when close equals the MA exactly (the `>=`
   convention is pinned so a future refactor cannot flip it), and NA for the first
   `ma_days - 1` bars.
6. **Conditional stats.** On a constructed pair of series where risk-off months are exactly
   `[0.10, 0.10]` and risk-on months exactly `[0.01] * 10`,
   `regime_conditional_stats` reports `n` 2 and 10, means 0.10 and 0.01, and a `t_stat` equal
   to `_newey_west_tstat` of each subset, with `spread_mean` equal to the mean of
   strategy-minus-benchmark over the same months.
7. **`regime_snapshot` fields.** On a series that flips above the MA at a known bar,
   `state_since` is that bar's date, `days_in_state` is the calendar-day difference to the
   last bar, and `drawdown` equals `close / trailing-365d max - 1`.
8. **`realized_vol_percentile`** on a series with a known vol path: `percentile` equals
   `(hist <= current).mean()` and `n` equals the count of non-NaN rolling values.
9. **I2 regression.** `perf_stats` / `EQUITY` construction for `regimeMode="off"` is unchanged:
   assert `apply_regime` is never called and no `_filt` key appears in a response built with
   the default params (an endpoint-level assertion, so this is the point where a first
   `tests/test_server.py` earns its keep, since there is none today). There is no injectable
   seam at the server layer the way `cache.get_prices_cached` has `fetcher`, so this test cannot
   be written against the existing fixtures as-is: `_run_backtest` calls `data.get_universe` and
   `data.get_prices` directly and both hit the network. The test therefore has to
   `monkeypatch.setattr(server.data, "get_prices", ...)` returning a synthetic frame built from
   `_daily_prices` plus a benchmark column, and `server.data.get_universe` returning
   `(cols, {})`, then call `server._run_backtest(BacktestParams(...))` in-process (no
   `TestClient` needed, which also avoids adding an httpx dependency). Note the synthetic frame
   must span enough bars for `momentum_backtest` to produce a non-empty series or
   `_run_backtest` raises its own 400.
9b. **NaN cannot reach the encoder.** Same monkeypatched harness, a frame short enough that the
   MA never completes, `realized_vol_percentile` has too few bars and the `risk_off` and `warmup`
   buckets are at n=0 or n=1: assert `json.dumps(resp["REGIME"], allow_nan=False)` succeeds and
   that `resp["REGIME"]["above"] is None` (not `pd.NA`). Without `_finite_or_none` this is the
   test that fails, and in production it is a 500 on the whole dashboard rather than a missing
   badge.
10. **Cache exclusion** (only if the VIX strip ships): a synthetic frame with a `^VIX` column
    containing a 2x overnight jump plus a normal ticker with a genuine 0.5x split artifact.
    `_detect_split_artifacts` flags the ticker and not `^VIX`; `_heal_split_artifacts` re-fetches
    only the ticker via the injectable `fetcher` (assert the fetcher's recorded call list).

## Non-goals

- Any other regime definition: no VIX threshold rule, no yield-curve or breadth signal, no
  drawdown-triggered de-risking, no cross-sectional dispersion filter. One benchmark-versus-MA
  rule, one MA length knob.
- Partial de-risking, volatility targeting, or scaling exposure by the distance from the MA.
  The filter is binary: invested or not.
- Applying the regime state to the *ranking* (for example switching to low-vol names in
  risk-off). That is a signal-family question and belongs to
  2026-07-28-vol-adjusted-momentum-signal.
- Intramonth exits, stop losses, or any timing finer than the month-end close the whole tool is
  built on. The banner reports a live MTD spread; it does not act on one.
- Notifications, alerts, or scheduled evaluation. The banner exists only inside a run.

## Risks / open questions

- **On this window the filter destroys value, and the banner will say so.** Measured on the
  cache (`top_n=10`, `rank_lookback=1`, SPY, 200dma, 62 months):

  | sleeve | variant | total | Sharpe | max DD |
  |---|---|---|---|---|
  | h6m | unfiltered | +470.3% | **1.33** | -24.5% |
  | h6m | filtered to cash | +190.0% | 0.98 | -25.0% |
  | h6m | filtered to SPY | +305.5% | 1.18 | -25.3% |
  | h3m | unfiltered | +379.6% | 1.17 | -25.7% |
  | h3m | filtered to cash | +194.6% | 0.93 | **-21.4%** |

  The conditional stats explain why: next-month h6m returns averaged **+5.81%** after the 13
  risk-off month-ends versus **+3.05%** after the 40 risk-on ones (benchmark +2.82% and +0.69%;
  spreads +2.99pp at t=2.31 and +2.36pp at t=2.47). On 2021 to 2026 the below-200dma months
  were the 2022 bear-market rallies and the 2025 tariff-crash rebound, which is when this
  strategy earned the most. This is the strongest argument for shipping the banner and treating
  the filter as an investigation tool rather than a recommendation. It also means the UI must
  never present "filtered" as a free lunch. **Open question for the user: given that, is the
  filtered equity curve worth showing by default when the mode is on, or should the feature ship
  banner-first with the filter behind an "experiment" label?**
- **The drawdown effect is convention-dependent, so pin the convention in code.** The table above
  reports that filtering does not reduce h6m max drawdown (-25.0% versus -24.5%). An independent
  re-measurement on the same cache using a different and equally defensible mask convention (a
  *daily* 200dma sampled at month-end via `resample("ME").last()`, then `.shift(1)`) counts **22**
  risk-off month-ends rather than 13, and on that convention filtering **does** reduce h6m max
  drawdown materially, from -24.5% to **-18.6%**, while landing in the same place on return
  (+193% versus the table's +190%) and Sharpe (1.01 versus 0.98). So the return verdict is robust
  to the convention but the drawdown verdict is not. Implementation must fix one convention
  explicitly, assert `n_risk_off` in a test so it cannot drift silently, and report n in the
  banner. Until that is settled, describe the filter as trading return for drawdown, not as
  pure value destruction.
- **13 risk-off month-ends and 6 episodes is a small sample over 4.5 years.** Every conditional
  number in the banner is estimated from n=13 on one side. The t-stats are HAC-corrected but
  the sample cannot support strong claims, and the banner copy must carry n visibly. A longer
  `lookback` improves this and should probably be suggested in the banner when
  `coverage_months` is under about 60.
- **200dma warm-up eats the first 9 monthly labels** of a default 5-year run (first valid MA
  bar on the cache is 2022-02-09, so 54 of 63 labels are covered). The alternative is widening
  the price pull by roughly 300 days when the mode is on, which is rejected here: a
  benchmark-only wider fetch would extend `prices_meta.json`'s `start` to a date only SPY
  actually covers, and `coverage()` would then advertise history the constituents do not have.
  Widening the pull for *all* tickers instead would change the monthly frame and therefore the
  unfiltered curve, breaking the like-for-like comparison. Settled as: report
  `warmup_months`, treat those months as invested, and let the user raise `lookback`. Flag if
  the user prefers the wider fetch with a cache-meta fix.
- **`regimeMaDays` bounds 20 to 400 invite curve-fitting.** The user can and will try 50, 100,
  150 and keep the best. There is no correction for that in the design; the honest mitigation
  is that the banner reports n and the t-stat for whatever length is selected, so an
  overfit choice usually shows up as a small n. Worth a one-line UI warning.
- The benchmark column can lag the constituent frame (2026-07-23 versus 2026-07-27 today), so
  the banner's `as_of` and the trade list's `as_of` will sometimes differ by a couple of
  sessions. Reported, not hidden. If the user finds it confusing, the alternative is to
  suppress the banner when the gap exceeds about 5 sessions.
- `benchmark_monthly` is `.dropna()`'d in `_run_backtest` before this code runs, and
  `bench_aligned` fills missing months with 0.0 with a status message. In `benchmark` mode the
  replacement therefore inherits that 0% fill for any month Yahoo did not return, which would
  quietly mean "held a flat benchmark". Use the unfilled `benchmark_monthly` for the
  replacement and leave such months as NaN so `perf_stats` drops them rather than counting a
  fabricated flat month. This does not fully close the hole: the response builders
  (`_safe_floats`, `_drawdown`) still map that NaN to 0.0, so the chart and the heatmap show a
  flat month the PERF row excluded. Handled with a status message rather than a truncation, see
  section 3. **Open question: truncate the filtered series at the first missing benchmark month
  instead?**
- **The warm-up state has no badge in the source design and now has one.** With `above` NA the
  original three-state table fell through to a green RISK ON, which asserts a market state from
  no data. That is the same fabrication I3 rules out for series, and it is the *default*
  presentation for any run under about 10 months, so it would have been the first thing most
  users saw. Fixed with an explicit `NO REGIME DATA` state; worth re-reading the copy once the
  banner is real, because a grey "no data" badge above a 100%-turnover trade list is its own
  kind of message.
- **`regimeMode` and `regimeMaDays` widen the params surface that has no server-side test.**
  Both new fields flow into `paramIssues`, which dereferences `ranges.<key>.min` off the
  `/api/universes` payload and throws a render-killing TypeError on any key the server does not
  advertise. The `ranges` additions in section 2 are load-bearing, not documentation, and this is
  the second time this shape of coupling has appeared (see the existing
  `ranges.skipMonths || DEFAULT_RANGES.skipMonths` guard). A `/api/universes` contract test would
  retire the whole class; out of scope here, worth flagging.
