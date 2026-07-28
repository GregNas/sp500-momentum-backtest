# Per-ticker drill-down drawer

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** L
**Depends on:** nothing (composes with `2026-07-28-leaderboard-signal-quality` and
`2026-07-28-per-pick-cohort-attribution`, both already shipped)

## Problem

Every ticker in the dashboard is dead text. Today's live rebalance row is 10 in, 10 out, 0
kept, so the user is looking at 10 unfamiliar symbols and 10 exits. The 2026-06 cohort came
in at -27.2% on GLW -43.9%, SNDK -43.8% and MRVL -36.5%, and the sleeve is -14.2% MTD vs
SPY -1.2%. The reasonable next question for each of those names, "what does this actually
look like, and why did it rank", cannot be answered inside the tool. It forces a Yahoo
Finance tab per symbol.

The irony is that the answer is already computed and already local:

- `_pick_quality_stats` (`lib/analysis.py`) computes `vol_ann`, `max_day_move`,
  `below_52w_high`, `ret_3m`/`ret_6m`/`ret_12m` and a weekly `spark` for every leaderboard
  row, then the panel renders 6 of those 7 fields as 60px-wide numbers in a 13-column
  table.
- `cache/prices.parquet` currently holds 504 tickers of adjusted daily closes from
  2021-04-28 to 2026-07-27 (1317 bars, 4.84 MB). GLW has 250 bars in the trailing year.
- `REBALANCE` already carries `picks` for 13 months and `RECENT_COHORTS` for 6, so
  "which months was this name picked" is sitting in the response the browser already has.

The cost is behavioural: at 1 external round-trip per name, the user evaluates 3 candidates
per session instead of 10, and the tool's credibility anchor (7 of 12 event-study horizons
clearing |t| >= 2, strongest h=11 at t=4.34, +470% total vs SPY +89%, best-Sharpe sleeve
hold6m 1.33 vs SPY 0.87) never gets connected to the individual names it is telling the
user to buy.

## Goal

Clicking any ticker anywhere in either dashboard opens one shared drawer showing that
name's trailing-year adjusted price rebased against the benchmark with the ranking window
shaded, its monthly returns, which of the recent rebalance months it was picked in, its
sector, and the window stats the leaderboard already computes. Served entirely from
`cache/prices.parquet` through one new pure analysis function and one new read-only
endpoint: no new data source, no network call, no change to any existing number.

## Design

### 1. `lib/analysis.py`: the shared signal window

The drawer's shaded band must be the exact window the live target ranks on, or it is
decoration. `current_target` computes that window inline (lines 490 to 497 plus
`top_performers_period`'s anchoring). Extract it, additively:

```python
def signal_window(prices: pd.DataFrame, rank_lookback: int, skip_months: int = 0) -> dict | None:
    """The daily window the live ranking signal is measured over.

    ``{days, start_date, signal_end, as_of}`` or None when the window can't be
    formed. ``days = round(rank_lookback * _MONTH_DAYS)``; ``signal_end`` is the
    last bar at or before ``as_of - round(skip_months * _MONTH_DAYS)`` days;
    ``start_date`` is the first bar at or after ``signal_end - days``; ``as_of``
    is always the latest bar. Identical arithmetic to :func:`current_target`,
    which keeps its own code path unchanged.
    """
```

`current_target` is **not** refactored to call it. It keeps deriving `start_date`/
`signal_end` from `ranked[0]`, so there is zero chance of a value shift (I2), and a test
pins the two against each other on the existing fixture.

One implementation rule that is load-bearing for I1: the window comes off the **frame's**
row index, never off the symbol's own non-null bars. `top_performers_period` computes
`start_ts = prices.loc[prices.index >= target_start].index[0]` across the whole frame and
hands that same `start_ts` to `_pick_quality_stats` for every ticker. A `dropna()` on the
column before measuring the window would shift `start_ts` for any symbol with a hole at the
boundary and quietly produce different `vol_ann`/`max_day_move` than the leaderboard shows.
A 2-column `read_parquet(columns=[...])` keeps the full row index (verified: the projected
index is `.equals()` the full one), so this is free as long as nothing drops rows.

### 2. `lib/analysis.py`: `ticker_profile`

```python
def ticker_profile(
    prices: pd.DataFrame,          # daily closes; must contain `symbol`, may contain `benchmark`
    symbol: str,
    benchmark: str | None = None,
    rank_lookback: int = 1,
    skip_months: int = 0,
    days: int = 365,
) -> dict:
```

Pure, no I/O, same house style as the rest of the module. Returns:

```python
{
  "symbol": "GLW",
  "available": True,
  "as_of": "2026-07-27",
  "coverage": {"first_bar": "2021-04-28", "last_bar": "2026-07-27", "n_bars": 1317},
  "benchmark": "SPY",                     # or None when absent from the frame
  "bars": {                               # the drawn range: last `days` calendar days
    "dates":         ["2025-07-28", ...],           # 251 entries today
    "close":         [12.3401, None, ...],          # adjusted close, null on a missing bar
    "rebased":       [1.0, None, ...],              # close / first valid close in range
    "bench_rebased": [1.0, ...] | None,             # benchmark, same rebasing
  },
  "window": {"days": 30, "start_date": "2026-06-27", "signal_end": "2026-07-27",
             "return_pct": -0.4102},      # the ranking window, for the shaded band
  "monthly": [{"month": "2025-08", "return_pct": 0.0412}, ...],   # oldest first
  "stats": {"vol_ann": ..., "max_day_move": ..., "below_52w_high": ...,
            "ret_3m": ..., "ret_6m": ..., "ret_12m": ..., "spark": [...]},
  "stats_as_of": "2026-07-27",
  "stale_bars": 0,
}
```

Rules that carry weight:

- **`stats` is `_pick_quality_stats(prices[symbol], start_ts, end_ts)`, verbatim**, with
  `start_ts`/`end_ts` from `signal_window`. Not a reimplementation. A drawer opened at a
  window of the same length as the leaderboard's therefore shows the leaderboard's exact
  numbers, including its `None`s (I1). The lengths are **not** freely matchable: the
  leaderboard's window is its own `days` control (1 to 365, default 30) while the drawer's
  is `round(rank_lookback * _MONTH_DAYS)`, so only 30, 61, 91, ... are reachable and a
  leaderboard sitting at `days=45` shows numbers the drawer cannot reproduce. See the open
  question on `windowDays` below.
- **`days` slices only `bars` and `monthly`.** The column handed to `_pick_quality_stats`
  must be the full cached history: `below_52w_high` and `ret_12m` reach back 365 days from
  `end_ts` and return `None` when the first bar at-or-after the target is more than 10 days
  late, so pre-slicing the frame to the drawn range would fabricate `None`s the leaderboard
  does not have. Same for the frame `signal_window` sees.
- **Stale-tail guard.** `_pick_quality_stats` does `float(col.loc[end_ts])`, which yields
  NaN for a symbol with no bar on `signal_end` (halted, delisted, late listing) and would
  quietly poison `below_52w_high` and the trailing returns. `ticker_profile` therefore
  anchors on `end_ts = col.loc[:signal_end].last_valid_index()`, reports it as
  `stats_as_of`, and sets `stale_bars` to the number of index positions between it and
  `signal_end`. `last_valid_index() is None` returns
  `{"symbol": ..., "available": False, "reason": "no_bars"}`.
- **`window.return_pct`** is `col.loc[stats_as_of] / col.loc[start_date] - 1`, the same
  `end_row / start_row - 1` the leaderboard computes, anchored on the same
  `stats_as_of` the stats use. `None` when either endpoint is missing. The leaderboard never
  hits that case, because `(end_row / start_row - 1).dropna()` drops such tickers before
  ranking, but the drawer can be opened on any cached symbol.
- **`monthly`** is `col.resample("ME").last().pct_change(fill_method=None)` restricted to
  the drawn range, i.e. the same construction as `data.to_monthly_returns`, so the drawer
  and the cohort tables cannot disagree. A month with no bar is absent, never 0%.
- **Nulls stay null.** `close`/`rebased` emit `None` for a missing bar. This must not be
  routed through `server._safe_floats`, which maps NaN to 0.0 and would draw a data gap as
  a crash to zero (I3). Floats are rounded to 4 decimals; 251 bars times 3 arrays is about
  12 KB of JSON.
- **But no NaN may survive to serialization anywhere in the payload.** Starlette's
  `JSONResponse` dumps with `allow_nan=False`, so a single NaN in `window.return_pct`,
  any `stats` field, `monthly[].return_pct`, or `spark` is a 500 rather than a `null`, the
  failure mode this repo has already hit once. `ticker_profile` therefore converts every
  float through one `_none_if_nan` helper on the way out: NaN and +/-inf become `None`,
  never `0.0`. That is the I3-correct direction and it is also what keeps the endpoint from
  crashing on a halted symbol.
- **Rebasing** divides by each series' own first valid value in the drawn range, so both
  lines start at 1.0 and the chart reads as relative performance.
- Absent symbol column returns `{"symbol": ..., "available": False, "reason":
  "not_in_cache"}`. Never raises for a data condition.

### 3. `lib/cache.py`: a read-only accessor

```python
def load_prices(columns: list[str] | None = None) -> pd.DataFrame | None:
    """Read-only view of the cached parquet: no network, no delta logic, no save.

    ``columns`` is pushed down into ``pd.read_parquet`` so a 2-column drill-down
    read costs ~4 ms against the ~8 ms full-frame read, both warm, on today's
    504-ticker, 1317-bar, 4.84 MB cache. (The 49 ms figure from the first
    measurement was a cold page cache; the projection is still worth doing, but
    the honest saving is roughly half a read, not 12x.) Returns None when the
    cache is missing or unreadable, with the same corrupt-parquet tolerance as
    :func:`get_prices_cached`.
    """
```

Callers must intersect their requested columns with `_load_meta()["tickers"]` first:
pyarrow raises on an unknown column (verified: `ArrowInvalid: No match for
FieldRef.Name(NOPE)`), and "not in the cache" is a 200-with-explanation, not a 500.

### 4. `server.py`: `GET /api/ticker/{symbol}`

Needs two imports `server.py` does not have yet: `import re`, and `Query` added to
`from fastapi import FastAPI, HTTPException`.

```python
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")

def _clean_symbol(raw: str) -> str | None:
    """Uppercase and validate a client-supplied symbol, or None."""

@app.get("/api/ticker/{symbol}")
def ticker_detail(
    symbol: str,
    universe: UniverseKey = "sp500",
    benchmark: str = "SPY",
    rankLookback: int = Query(1, ge=1, le=12),
    skipMonths: int = Query(0, ge=0, le=2),
    days: int = Query(365, ge=30, le=1825),
) -> dict:
```

Flow:

1. `_clean_symbol` on both `symbol` and `benchmark`; a reject is a 400. They become parquet
   column selectors and dict keys, so validate rather than trust.
2. `cache._load_meta()` is None -> 200 `{"symbol": sym, "available": False, "reason":
   "cache_empty"}`.
3. `sym not in meta["tickers"]` -> 200 `{"available": False, "reason": "not_in_cache"}`.
   This is a live path today, not a theoretical one: the cache holds SPY but **not** ACWI
   or XLK, so drilling into a global-ETF pick before rerunning that universe lands here.
4. `cols = [sym] + ([bench] if bench in meta["tickers"] else [])`, then
   `cache.load_prices(columns=cols)`. A benchmark missing from the cache yields
   `benchmark: None` and `bench_rebased: None` rather than failing the whole drawer.
5. `analysis.ticker_profile(...)`, then attach `name` and `sector` from a **cache-only**
   metadata lookup, falling back to empty strings. This must *not* be
   `data.get_universe(universe)`: for `sp500` that calls `get_sp500_tickers()` and
   `get_sp500_metadata()`, both of which hit **Wikipedia** whenever the 24h JSON cache has
   expired, which would hand this endpoint a live network dependency and break the I5 claim
   below. Use `cache.load_metadata() or {}` for `sp500` (already TTL-guarded, returns None
   when stale, and a stale-empty lookup just renders no company name), and the hardcoded
   `universes.get_global_etfs()` / `get_us_sector_etfs()` tables for the two ETF universes,
   which do no I/O at all.

Route placement: the handler must be defined **above**
`app.mount("/", NoCacheStaticFiles(...))` at the bottom of `server.py`. Starlette matches
routes in registration order and a `Mount` on `"/"` matches every path, so a route added
after it never runs and `/api/ticker/GLW` would fall through to the static handler as a 404.

`GET` with query params deviates from the POST-plus-pydantic shape of the other data
endpoints. Deliberate: this is an idempotent, path-addressable, ~5 ms read with no body,
and a bare `fetch()` keeps the drawer instant. Symbols are public tickers, so nothing
sensitive lands in a URL. Dots survive the path segment fine (`{symbol}` matches `[^/]+`,
so `BF.B` needs no escaping).

`web/data-loader.js` gains `fetchTickerProfile(symbol, opts)` and registers it in the
existing `Object.assign(window, {...})`.

### 5. `web/charts.jsx`: one drawer, mounted twice

Five additions, all registered in the end-of-file
`Object.assign(window, {...})` (a component missing from that list is invisible to v1/v2):

```
TickerDrawerProvider, useTickerOpener, TickerDrawer, TickerChip, TickerPriceChart
```

Hook availability, because this file does not import React hooks the usual way:
`charts.jsx` line 5 is `const { useMemo, useState, useRef, useEffect } = React;`, with no
`useCallback`, no `useContext`, no `createContext`. The new components must reach for those
as `React.useCallback` / `React.useContext` / `React.createContext` rather than extending
that destructure, because top-level `const` in these classic scripts share one global
lexical scope and `index.html`'s inline block already declares
`const { useState, useEffect, useCallback } = React;`. That collision is exactly why
`v1.jsx` and `v2.jsx` alias theirs (`useStateV1`, `useStateV2`).

**Context, so chips need no prop drilling:**

```jsx
const TickerDrawerContext = React.createContext(null);

function TickerDrawerProvider({ children, theme, accent, headingFont,
                                params, rebalance, cohorts }) {
  const [symbol, setSymbol] = useState(null);
  const opener = useRef(null);
  const open  = React.useCallback((sym, el) => { opener.current = el || document.activeElement;
                                                 setSymbol(sym); }, []);
  const close = React.useCallback(() => { setSymbol(null);
                                          opener.current && opener.current.focus?.(); }, []);
  const membership = useMemo(() => buildMembership(rebalance, cohorts), [rebalance, cohorts]);
  return (
    <TickerDrawerContext.Provider value={{ open }}>
      {children}
      {symbol && <TickerDrawer symbol={symbol} onClose={close} membership={membership}
                               params={params} theme={theme} accent={accent}
                               headingFont={headingFont} />}
    </TickerDrawerContext.Provider>
  );
}

function useTickerOpener() {
  const ctx = React.useContext(TickerDrawerContext);
  return ctx ? ctx.open : null;      // null = no provider = render exactly as today
}
```

**How chips get handlers without rewriting every call site.** Every ticker in the app is
already funnelled through 4 render sites, and 2 of them collapse into one new component:

| site | file:line today | change |
| --- | --- | --- |
| live Buy/Sell/Keep chips + history buy/sell chips | `charts.jsx` `RebalanceChip` (~798) | one component, activation props added |
| per-pick attribution chips | `charts.jsx` `RebalanceHistoryRow` (~879, inline `<span>`) | replaced by `TickerChip` |
| V2 recent-picks chips | `v2.jsx` (~696, inline `<span>`) | replaced by `TickerChip` |
| leaderboard ticker cell + bubbling-under chips | `charts.jsx` (~1251, ~1313) | activation props added |

`TickerChip({ ticker, right, tone, group, theme })` absorbs the 2 near-identical inline
chip implementations (mono ticker, optional right-hand percentage, optional muted group
label, optional `#fef2f2`/`#fecaca` "worst" tone), so the attribution row and V2's recent
picks stop drifting apart.

"Near-identical" is not identical, and the 3 differences have to be a deliberate choice
rather than an accident: the attribution chip is `padding: '2px 7px'`, `borderRadius: 4`,
and renders a muted `n/a` when `pick_returns[t]` is null; V2's is `padding: '2px 6px'`,
`borderRadius: 3`, renders **nothing** for a null return, and adds the group label on
non-sp500 universes. Unifying them therefore moves pixels on at least one surface. Ship it
as an accepted 1px cosmetic convergence (padding 7, radius 4, `n/a` preserved as the honest
"no bar that month" marker, which is strictly better than V2's silence), and say so in the
commit. Do not claim these chips are unchanged.

A shared helper keeps activation uniform:

```jsx
function tickerActivation(open, symbol) {
  if (!open) return { props: {}, cursor: undefined };
  const fire = (e) => { e.stopPropagation(); open(symbol, e.currentTarget); };
  return {
    cursor: 'pointer',
    props: {
      role: 'button', tabIndex: 0, 'aria-label': `Open ${symbol} details`,
      onClick: fire,
      onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(e); } },
    },
  };
}
```

`stopPropagation` is load-bearing: the attribution chips sit inside
`RebalanceHistoryRow`'s clickable header. That header is currently `role="button"
tabIndex={0}`, and nesting an interactive element inside it is invalid ARIA. Fix as part of
this work: the header div keeps its `onClick` for mouse convenience but drops
`role`/`tabIndex`/`onKeyDown`, and the existing caret `<span>{open ? '▾' : '▸'}</span>`
becomes a real `<button aria-expanded={open} aria-label="Show per-pick returns">`. Same
behavior, valid tree, chips focusable.

**Drawer contents**, top to bottom:

1. Header: symbol (mono, 18px), company name, sector, and a close `<button aria-label="Close
   details">`. `id="ticker-drawer-title"` for `aria-labelledby`.
2. `TickerPriceChart({ bars, window, benchmark, theme, accent, height: 220 })`: rebased
   symbol line (`accent.color`, 2px) and benchmark line (`theme.muted`, 1.5px, `4 4`
   dashed), matching `EquityCurveChart`'s conventions. The ranking window is a `<rect>`
   spanning the `window.start_date`..`window.signal_end` indices, `fill={accent.color}`
   `opacity="0.10"`, labelled `ranking window · 30d`. Paths are built with
   `RollingSharpeChart`'s segment loop so a `null` bar breaks the line instead of
   interpolating across it. Hover crosshair reports both rebased values and the date.
3. Window strip from `stats`: `30d -41.0% · vol 62% · off 52w high -8.2% · max 1d +12.1% ·
   3m -22% · 6m +14% · 12m —`, via `fmtPct`, with `—` for `null` (never 0).
4. Monthly bars: signed bars from `monthly`, `#047857`/`#b91c1c`, month labels, value in
   the tooltip. Real-data sanity check: GLW's 2026-07 bar is -43.88% from the current
   cache, which is the number this must reproduce.
5. Cohort dots: one dot per month in `membership.months` (oldest left), filled
   `accent.color` when this symbol is in that month's picks, hollow `theme.border`
   otherwise, `title` = `2026-06 · picked` / `· not picked`. Caption:
   `picked in 4 of the last 13 rebalance months`.
6. Footer: `adjusted closes from the local cache · 2021-04-28 → 2026-07-27 · 1317 bars ·
   no network calls`.

**Cohort membership source.** Client-side only, from data the browser already holds:

```jsx
function buildMembership(rebalance, cohorts) {
  const months = [], byTicker = {};
  (rebalance || []).slice().reverse().forEach(r => {          // oldest -> newest
    const label = r.is_live ? 'live' : r.month;
    months.push(label);
    (r.picks || []).forEach(t => (byTicker[t] = byTicker[t] || new Set()).add(label));
  });
  (cohorts || []).forEach(c => (c.picks || []).forEach(t =>
    (byTicker[t] = byTicker[t] || new Set()).add(c.month)));
  return { months, byTicker };
}
```

`REBALANCE` (13 rows: 1 live + 12 completed month-ends) and `RECENT_COHORTS` (6 rows) both
derive their picks from `momentum_signal` at the same settings, so their overlap is
consistent and the union is safe. With today's counts the cohort loop adds nothing (the 6
cohort months are a subset of the 12 completed rebalance months); it is there so the map
stays correct if either count changes. The drawer caption states the 13-month limit
explicitly rather than implying full history.

Two edge cases in the caption and the dot row:

- The cohort loop writes into `byTicker` but never into `months`, so if the counts ever
  invert, `byTicker` can hold a month with no dot. The `picked in N of the last M` count
  must therefore be `months.filter(m => set.has(m)).length`, not `set.size`.
- `TopPerformersPanel` sits above the `ready` guard and the drawer opens from it before any
  backtest has run, so `rebalance`/`cohorts` arrive `undefined` and `months` is empty. The
  cohort-dots block is then **omitted entirely**, not rendered as "picked in 0 of the last
  0 rebalance months".

**Keyboard, focus, escape:**

- `role="dialog" aria-modal="true" aria-labelledby="ticker-drawer-title"`.
- On open, focus the close button in an effect; the invoking element arrives as
  `open(sym, e.currentTarget)` and gets focus back on close.
- Escape closes, via a `window` `keydown` listener registered in the drawer's effect (so it
  works even if focus escaped the panel) and removed on unmount.
- Tab trap: `keydown` on the panel queries
  `button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])` and wraps at
  both ends.
- Backdrop `position: fixed; inset: 0; background: rgba(0,0,0,0.28)`, click closes. Panel
  `position: fixed; top/bottom: 0; right: 0; width: min(560px, 100vw)`, own
  `overflow-y: auto`. Under `useViewport().isCompact` (<900px) it becomes a full-width
  bottom sheet at `max-height: 85vh`.
- A 160 ms `transform` slide-in, gated on
  `window.matchMedia('(prefers-reduced-motion: reduce)')`. `index.html`'s existing
  reduced-motion rule only zeroes `animation-*`, not `transition`, so the check has to be
  in the component.
- One provider-owned instance: opening a second ticker swaps content instead of stacking
  drawers.

**Not-available and error states** (the drawer always renders its header, never a blank
panel):

| condition | copy |
| --- | --- |
| `reason: "not_in_cache"` | `GLW is not in the local price cache. Run a backtest or leaderboard for a universe that contains it.` |
| `reason: "cache_empty"` | `No local price cache yet. Run a backtest first.` |
| `reason: "no_bars"` | `No price bars for GLW in the cached range.` |
| benchmark absent | chart renders the single line plus `benchmark ACWI not in cache` |
| fetch threw / non-200 | the error text, plus a retry button |
| in flight | skeleton with the symbol title (locally ~5 ms, but a cold page or `days=1825` needs it) |

**Mount points**, 2 edits: wrap the root `<div style={{display:'flex', height:'100vh', ...}}>`
returned by `V1Dashboard` (`web/v1.jsx` ~314) and `V2Dashboard` (`web/v2.jsx` ~347) in

```jsx
<TickerDrawerProvider theme={v1Theme} accent={v1Accent} params={params}
                      rebalance={data?.REBALANCE} cohorts={data?.RECENT_COHORTS}>
```

so `TopPerformersPanel` (`v1.jsx` 328 / `v2.jsx` 361, above the `ready` guard, works before
any backtest has run) is inside the provider too. `v1Theme`/`v1Accent` are module-level
consts in `v1.jsx`, not props; V2 passes its own `v2Theme`/`v2Accent` plus
`headingFont='"Source Serif Pro", Georgia, serif'`.

`web/index.html`: bump `?v=4` to `?v=5` on all 4 asset tags, and add
`window.TickerDrawerProvider` to `tryMount`'s guard alongside `window.EquityCurveChart`. The
sibling `earnings-date-flags` spec claims the same bump, so whichever lands second takes the
next free number instead of re-writing `?v=5`.

### Invariants

- **I1**: no ranking logic is added or forked. The shaded band comes from `signal_window`,
  the arithmetic already inside `current_target`, pinned equal by test. The window stats
  come from `_pick_quality_stats`, the same call the leaderboard makes. A drawer opened from
  the live trade list matches it exactly, because the live target's window *is*
  `rank_lookback` months. Against the leaderboard the guarantee is narrower and honest: the
  numbers are identical **when the leaderboard's `days` equals
  `round(rank_lookback * 30.4375)`** (the default 30-day / 1-month case), and legitimately
  different otherwise, since the drawer has no way to express `days=45` today.
- **I2**: every new parameter has a default (`rank_lookback=1`, `skip_months=0`,
  `days=365`, `benchmark=None`) and no existing response key or value changes.
  `useTickerOpener()` returns `null` without a provider, so the un-refactored chips
  (`RebalanceChip`, the leaderboard cell, the bubbling-under chips) render byte-identically;
  `current_target` is untouched. The 2 chips that collapse into `TickerChip` change by ~1px
  of padding and radius, called out above and in the commit.
- **I3**: gaps are `null`, not 0.0 (explicitly bypassing `_safe_floats` for price arrays);
  NaN and inf anywhere in the payload become `null`, never 0.0; `None` stats stay `—`;
  monthly returns use `fill_method=None`; the stale-tail guard stops a NaN end price from
  producing a plausible-looking wrong `below_52w_high`; membership claims are scoped to
  "the last 13 rebalance months" rather than implied as full history.
- **I4**: no build step (one more in-browser JSX component), no broker surface, no
  execution, read-only endpoint.
- **I5**: **zero new runtime network dependency**, but only because of the cache-only
  metadata rule in step 5. Price bytes come from `cache/prices.parquet` via
  `cache.load_prices`; name/sector come from `cache.load_metadata()` or the hardcoded
  `universes` tables. Routing that lookup through `data.get_universe` instead would put a
  Wikipedia fetch on the drawer's critical path every time the 24h metadata cache expires,
  which is the one way this feature can break the invariant. On an airgapped site with a
  seeded cache the drawer is fully functional, including the benchmark comparison whenever
  the benchmark was fetched with the universe. This is the deliberate counterweight to the
  earnings-badge spec, which does add one.

## Testing

Against `tests/test_analysis.py`'s existing fixtures (`_daily_prices` for the 6-ticker
business-day walk ending mid-month at 2026-07-02, and the gappy/short-history frames):

1. `signal_window(prices, 1, 0)` returns `days == 30` and `start_date`/`signal_end`/`as_of`
   equal to `current_target(prices, top_n=3, rank_lookback=1)`'s corresponding keys, to the
   character. Repeat at `skip_months=1` against
   `test_current_target_skip_ranks_on_earlier_window`'s truncation expectation.
2. `ticker_profile(prices, "T0", rank_lookback=1)["stats"]` equals
   `_pick_quality_stats(prices["T0"], start_ts, end_ts)` field for field, and `ret_12m is
   None` on this 6-month fixture (same assertion `test_top_performers_quality_stats`
   already makes).
3. Leaderboard agreement: for the top-ranked row of
   `top_performers_period(prices, top_n=3, days=30)`, the profile's `window.return_pct`
   matches that row's `return_pct` and `stats.vol_ann` matches its `vol_ann`, both within
   1e-12.
4. `bars.rebased[0] == 1.0` and `bars.rebased[i] == round(close[i]/close[0], 4)` for every
   non-null bar; with a benchmark column present, `bench_rebased[0] == 1.0` and its length
   equals `len(bars.dates)`.
5. Gap handling on `test_pick_quality_stats_ignore_gap_crossing_returns`'s `GAPPY` frame:
   `bars.close` holds `None` (not `0.0`) at the 5 masked days, `len(close) ==
   len(dates) == len(rebased)`, and `max_day_move` still comes out ~0 (the existing
   assertion, now reached through `ticker_profile`).
6. `monthly` equals `datalib.to_monthly_returns(prices)["T0"].dropna()` restricted to the
   drawn range, entry for entry, with no zero-filled months. The `dropna()` matters:
   `to_monthly_returns` only drops rows where *every* column is NaN, so a per-symbol NaN
   month survives in the frame while the payload omits it.
7. Stale tail: blank T0's last 5 bars. `stats_as_of` is the last valid bar, `stale_bars ==
   5`, and no `stats` field is NaN (they are floats or `None`).
7b. Serialization: `json.dumps(profile, allow_nan=False)` succeeds for T0, for the stale-tail
   frame, for the `GAPPY` frame, and for an all-NaN column. This is the actual regression
   being guarded: Starlette dumps with `allow_nan=False`, so any NaN that reaches the
   response is a 500, and a plain `pytest` assertion on field types would not catch a NaN
   hiding inside `spark` or `monthly`.
8. `ticker_profile(prices, "NOPE")` returns `{"available": False, "reason":
   "not_in_cache"}` and raises nothing; an all-NaN column returns `reason == "no_bars"`.
9. `days=30` returns roughly 21 business-day bars and `days=365` returns the whole fixture,
   with `window` **and** `stats` identical in both. Neither the shaded band nor the quality
   stats may depend on the drawn range; this is the test that catches an implementation that
   slices the frame before calling `_pick_quality_stats`.
10. `cache.load_prices(columns=["T0"])` against a `tmp_path` cache written by `cache._save`
    (the `_point_cache_at` idiom from `tests/test_cache_corrupt.py`) returns exactly that
    column; `load_prices()` over a deliberately corrupted parquet returns `None` instead of
    raising, mirroring `test_get_prices_cached_recovers_from_corrupt_parquet`.
11. `server._clean_symbol`: `"glw" -> "GLW"`, `"BRK-B"` and `"BF.B"` pass, `"../etc/passwd"`
    and a 20-character string return `None`. Unit-testable without an HTTP client, which is
    why the validation lives in a helper rather than inline in the route.

No frontend test harness exists, so the drawer's behavior is verified manually against the
checklist: Escape closes, focus returns to the invoking chip, Tab cycles inside the panel,
a chip inside an expanded history row opens the drawer without collapsing the row, and a
`us_sector_etfs` pick opened against today's cache shows the `not_in_cache` message rather
than an error.

## Non-goals

- Volume, fundamentals, news, analyst data, or anything else absent from the parquet cache.
  `data._fetch_prices` keeps only adjusted Close.
- Multi-ticker overlay or compare mode. One symbol per drawer.
- Chart zoom, pan, or brushing; the range is the `days` query parameter only.
- Full-history cohort membership. Scope is the current run's 13 rebalance rows plus 6
  cohorts, and the UI says so.
- Deep links (`#ticker=GLW`), drawer state persistence, or restoring the last opened symbol.
- Drilling in from the sector bar chart, the monthly heatmap, or the event-study chart.
- Any change to ranking, backtest, or event-study output. The drawer is read-only
  inspection.

## Risks / open questions

- **No volume, which is the one thing users expect in a price drawer.** Adding it means
  changing what `_fetch_prices` keeps and migrating the single-frame `prices.parquet`
  schema (today: date index, ticker columns, adjusted close only). That is a separate,
  larger piece of work. **User's call** on whether the drawer ships without it.
- **Adjusted closes are not broker quotes.** yfinance's `auto_adjust=True` rebases the
  whole history to each fetch's end date (the reason `_heal_split_artifacts` exists), so
  absolute levels in the drawer will not match a brokerage screen. Mitigation is copy
  ("adjusted"), and the rebased-to-1.0 presentation makes levels secondary. Still a likely
  source of "these prices are wrong" confusion.
- **13 months of membership dots may be too shallow** to show the persistence the
  event-study result claims. Full membership over the whole backtest window is cheap
  server-side (one `momentum_signal` pass, already computed in `_run_backtest`) but adds a
  response key, e.g. `MEMBERSHIP: {ticker: ["2024-03", ...]}`, weighing maybe 10 to 30 KB
  at topN=10 over 60 months. **Needs a decision:** ship 13 months now, or add the key.
- **Default range.** 1 year (251 bars, ~12 KB) against a 5.2-year cache (1317 bars,
  ~60 KB). A 1y/3y/max toggle is easy but adds state and re-fetches. **User's call.**
- **The drawer cannot express the leaderboard's window.** `TopPerformersPanel` ranks over
  its own `days` control (1 to 365, default 30); the drawer's window is
  `round(rank_lookback * 30.4375)`, so it can only ever show 30, 61, 91, ... A user sitting
  at `days=45` and clicking a ticker gets a 30-day window and 30-day `vol_ann`/`max_day_move`
  next to a 45-day return in the row above, with nothing on screen explaining the mismatch.
  The fix is small: an optional `windowDays` query parameter that overrides the
  `rank_lookback` conversion, `None` by default (so I2 holds), with the leaderboard's chips
  passing their own `days` and the rebalance chips passing nothing. **Needs a decision:**
  add `windowDays` now, or ship rank_lookback-only and label the strip
  `ranking window · 30d` loudly enough that the difference is visible rather than silent.
- **`GET` breaks the POST-plus-pydantic convention** every other data endpoint follows.
  Justified above, but worth confirming rather than silently diverging.
- **Compact layout is unverified.** The bottom-sheet variant below 900px has to coexist
  with `TopPerformersPanel`'s horizontally scrolling 13-column table; the sheet may need to
  become a full-screen overlay instead.
- **ARIA change to `RebalanceHistoryRow`.** Moving the expand affordance from the row div
  onto a real caret button is correct but changes an existing keyboard interaction (today
  Enter/Space anywhere on the focused row toggles it). Low risk, but it is a behavior change
  to shipped UI and should be called out in the commit rather than buried. Note the caret is
  currently `aria-hidden="true"` and the row carries `aria-expanded`, so the change also
  moves which element announces the state.
- **Cache-only metadata means the company name can be blank.** `cache.load_metadata()`
  returns `None` past its 24h TTL, and this endpoint deliberately will not refresh it (that
  would be a network call). So a drawer opened on a machine whose metadata cache went stale
  overnight shows the symbol and sector as empty until the next backtest re-seeds them. That
  is the right trade for I5, but it is a visible degradation with no on-screen explanation.
- **`prices.parquet` is universe-agnostic while the drawer's window is not.** The parquet
  index is the union of bar dates across all 504 cached tickers from every universe run,
  whereas `current_target` sees only the current universe's constituents (the benchmark
  column is dropped before `live_rebalance`). If a future universe ever brings a trading
  calendar the S&P names do not share, `as_of` and `start_date` could differ between the two
  frames and the drawer's band would drift off the live target's window. Harmless with
  today's all-US-listed universes; worth a comment at `signal_window` so it is not a silent
  assumption.
