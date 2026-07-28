# Earnings-date flags on the live trade list

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** S
**Depends on:** nothing

## Problem

Today's live rebalance row (`analysis.live_rebalance` -> `REBALANCE[0]`, rendered by
`RebalancePanel` in `web/charts.jsx`) is **10 in, 10 out, 0 kept**: every position is a
fresh equal-weight buy held to the next month-end close. The tool says nothing about
whether any of those 10 names reports earnings inside that hold window.

That silence is expensive. The 2026-06 cohort came in at -27.2%, driven by GLW -43.9%,
SNDK -43.8% and MRVL -36.5%, and the sleeve is -14.2% MTD against SPY -1.2%. Querying
Yahoo's calendar surface for those exact names today returns: GLW **2026-07-28**, SNDK
**2026-08-05**, MRVL **2026-08-27**. All 3 land inside a hold that starts now. This spec
does not claim earnings caused those drawdowns; the claim is narrower and checkable: the
single largest scheduled event risk each pick carries is published, free, and currently
invisible in both idea surfaces.

The gap is also inconsistent with what the leaderboard already does.
`leaderboardBadge` (`web/charts.jsx`) flags `spike` when one session drove >= 50% of the
window return, i.e. the panel already reasons about single-session risk, but only
retrospectively. A fresh momentum buy that reports in 3 days is the same risk, known in
advance, and unlabelled.

Scope note on cost: this is per-pick, not per-universe. At the default `topN=10` the live
row plus the leaderboard plus the bubbling-under chips is 10 to 30 distinct symbols, never
504. `topN` is settable up to 100, so `results + watchlist` can reach 110; the 40-symbol cap
below is what keeps the worst case bounded.

## Goal

Every BUY/KEEP chip on the live rebalance row and every leaderboard row carries a
"reports in N days" badge, warning-styled when the report lands inside the coming hold
month, sourced from a 24h-TTL `cache/earnings_dates.json` and fetched by a bounded,
best-effort, post-render call that can never delay or fail the trade list. When the
network is unavailable (or the deployment is airgapped) the badges are simply absent and
every existing number is byte-identical to today.

## Design

### Data source: `get_calendar`, not `get_earnings_dates`

Measured on this machine through the repo venv, with `lib.data` imported first so
`_ensure_ssl_trust()` has run:

| call | warm latency | returns |
| --- | --- | --- |
| `yf.Ticker(t).get_calendar()` | 0.20 to 0.25 s | `dict` with `'Earnings Date': [datetime.date(2026, 7, 30)]` |
| `yf.Ticker(t).get_earnings_dates(limit=8)` | 1.4 s | DataFrame indexed by tz-aware timestamps, past **and** future, columns `EPS Estimate`, `Reported EPS`, `Surprise(%)` |

`get_calendar` is 5x cheaper, returns exactly the next date, and needs no future/past
filtering, so it is the primary source. `get_earnings_dates(limit=8)` is the fallback for
the case where `get_calendar()` returns `None` or has no `'Earnings Date'` key: filter the
index to `>= today` and take the minimum. Both return `None` for an unknown symbol
(verified with a junk ticker), and `get_earnings_dates` additionally logs 404 noise through
the `yfinance` logger, so the fetcher raises the logger to `CRITICAL` for the duration of
the batch.

When `'Earnings Date'` holds **2** dates, Yahoo is reporting an estimated window rather
than a confirmed date. Keep the earlier date and stamp `estimated: True`, so the UI can
render `~` instead of implying a confirmed calendar entry (I3: no fabricated precision).

`get_calendar` sometimes returns a date that has **already passed** (Yahoo has not rolled
the entry forward after a report). Only the `get_earnings_dates` fallback filters on
`>= today`, so the primary path needs the same filter: a date strictly before today is
treated as *no date* (`{"next": None}`) and lands on the 6h negative TTL, rather than being
served as a positive that the UI would render as `rpt -3d`.

Two measurements bound the request budget: 8 symbols serially took 3.90 s (first call
2.38 s for session/cookie setup, then ~0.22 s each), and 20 symbols through
`ThreadPoolExecutor(max_workers=8)` took **1.00 s** wall clock.

### `lib/cache.py`: the JSON cache

Clones the `load_tickers`/`save_tickers` pattern, with one deliberate difference. Constants
next to `TICKERS_TTL_HOURS`:

```python
EARNINGS_JSON = CACHE_DIR / "earnings_dates.json"
EARNINGS_TTL_HOURS = 24        # a confirmed/estimated date
EARNINGS_MISS_TTL_HOURS = 6    # "Yahoo has no date for this symbol"
```

On-disk shape, **per symbol** rather than one global stamp:

```json
{"fetched_at": "2026-07-28T09:14:02+00:00",
 "symbols": {"GLW":  {"next": "2026-07-28", "estimated": false, "source": "calendar",
                      "fetched_at": "2026-07-28T09:14:02+00:00"},
             "ZZZZ": {"next": null, "source": "calendar",
                      "fetched_at": "2026-07-28T09:14:02+00:00"}}}
```

Why per-symbol and not the single `fetched_at` that `load_tickers` uses: the symbol set
rotates every run (10 in, 10 out today), so a single stamp would either refetch all 30
whenever one new name appears, or serve a 23h-old entry as fresh alongside a brand-new
name with no entry at all. Negative answers (`"next": null`) are cached too, on the shorter
6h TTL, so a name Yahoo has no date for is not retried on every dashboard refresh but a
newly scheduled date still surfaces the same day.

```python
def load_earnings() -> dict[str, dict]
    # {} on missing/corrupt file (same try/except json.JSONDecodeError, OSError as
    # load_tickers); entries past their TTL (24h for next != None, 6h for None) dropped.

def save_earnings(entries: dict[str, dict]) -> None
    # merge into whatever is on disk (a concurrent request may have added other
    # symbols), then _atomic_write(EARNINGS_JSON, ...).
```

`save_earnings` uses `_atomic_write` rather than `save_tickers`' plain `write_text`: two
dashboard panels can refresh at once and FastAPI runs sync endpoints in a threadpool.
`cache.clear()` gains `EARNINGS_JSON` in its tuple so the existing "Clear cache" button
clears it too.

### `lib/earnings.py`: the fetch, bounded

New module (keeps the per-ticker, flaky yfinance surface out of `lib/data.py`, which owns
only the bulk `yf.download` path). Two things it must do at the top of the file:

- `from __future__ import annotations`, like every other module in `lib/`. The venv is
  **Python 3.9**, where a `dict | None` annotation is a runtime `TypeError` without it.
- `from lib import data  # noqa: F401`, an explicit import for its side effect. `lib/data.py`
  calls `_ensure_ssl_trust()` at module scope, and without it every call here dies with
  `curl_cffi ... CertificateVerifyError: unable to get local issuer certificate` (reproduced
  today by importing `yfinance` on its own). Relying on some other module having imported
  `lib.data` first makes the feature's success depend on import order, which is exactly the
  kind of thing that works in the dashboard and fails in a unit test or a CLI entry point.

```python
EARNINGS_BUDGET_S = 8.0     # wall clock for the whole batch
EARNINGS_MAX_SYMBOLS = 40
EARNINGS_WORKERS = 8

def _fetch_next_earnings(symbol: str) -> dict | None:
    """{'next': 'YYYY-MM-DD'|None, 'estimated': bool, 'source': 'calendar'|'history'}
    or None when the lookup failed (as opposed to succeeded-with-no-date)."""

def next_earnings(
    symbols: list[str],
    fetcher: Callable[[str], dict | None] = _fetch_next_earnings,
    allow_fetch: bool = True,
    budget_s: float = EARNINGS_BUDGET_S,
    max_symbols: int = EARNINGS_MAX_SYMBOLS,
    workers: int = EARNINGS_WORKERS,
    on_status: Callable[[str], None] | None = None,
) -> tuple[dict[str, dict], dict]:
    """({symbol: entry}, meta) where meta = {n_cached, n_fetched, timed_out: [...],
    failed: [...], offline: bool}."""
```

`fetcher` is an injected seam exactly like `cache.get_prices_cached(..., fetcher=...)`, so
no test touches the network.

Failure behavior, in order:

1. Cache first. `load_earnings()` supplies every in-TTL symbol; those cost 0 requests.
2. Misses are fetched through `ThreadPoolExecutor(max_workers=8)`, with `max_symbols=40`
   capping the **fetch list** (cache hits are free and never count against it).
   `concurrent.futures.wait(futures, timeout=budget_s)` bounds the **batch**, not each
   request, because `yfinance`'s own HTTP timeout is `YfData.get(url, params=None,
   timeout=30)` and is not settable per `Ticker` call. On expiry the executor is shut down
   with `shutdown(wait=False, cancel_futures=True)` and whatever completed is used. The
   executor must **not** be used as a `with` block: `Executor.__exit__` calls
   `shutdown(wait=True)`, which would block on the in-flight 30 s request and make
   `budget_s` decorative.
3. A symbol that timed out or raised is **omitted from the result and not written to
   cache** (an abandoned request is not evidence that no date exists), so it retries on the
   next refresh. It renders no badge.
4. Any exception inside `next_earnings` is caught at the endpoint boundary and returned as
   an empty symbol map with a status message. The trade list is already on screen at that
   point (see below), so there is nothing to block.

`yf.set_config(retries=...)` exists but is process-global and would also change `yf.download`
behavior for prices, so it is left alone; the wall-clock budget is the only bound.

### `server.py`: a separate, post-render endpoint

The fetch does **not** go inside `/api/backtest`. A warm-cache backtest returns in well
under a second, and bolting up to 8 s of per-ticker calls onto it would make the whole
dashboard feel broken for a decoration. Instead:

```python
OFFLINE = os.environ.get("MOMENTUM_OFFLINE") == "1"    # needs `import os`; server.py
                                                       # does not import it today

class EarningsParams(BaseModel):
    symbols: List[str] = Field(..., min_length=1, max_length=40)
    allowFetch: bool = True
    runId: Optional[str] = None

@app.post("/api/earnings")
def earnings(params: EarningsParams) -> dict:
    # -> {"symbols": {sym: {"next": "YYYY-MM-DD"|null, "estimated": bool,
    #                       "source": "calendar"|"history"}},
    #     "offline": bool, "n_cached": int, "n_fetched": int,
    #     "timed_out": [...], "failed": [...], "status_messages": [...]}
```

Two placement constraints, both easy to get wrong:

- The handler must be defined **above** `app.mount("/", NoCacheStaticFiles(...))` at the
  bottom of `server.py`. Starlette matches routes in registration order and a `Mount` on
  `"/"` matches every path, so a route registered after it is dead and `/api/earnings`
  would 404 through the static handler.
- `pydantic` in the venv is 2.13, so `min_length`/`max_length` on a `List[str]` is correct
  (these were `min_items`/`max_items` in v1).

Symbols are uppercased and filtered to `^[A-Z0-9.\-]{1,12}$`; anything else is dropped
(they become `yf.Ticker` arguments). Duplicates collapse. `allow_fetch =
params.allowFetch and not OFFLINE`. Never raises for data reasons: an unreachable Yahoo
returns HTTP 200 with an empty `symbols` map and a status message, so the UI's failure mode
is "no badges", not "error banner".

The endpoint always returns 200 for a well-formed request. Pydantic returns 422 for
`symbols` outside 1..40, which is a client bug, not a data condition.

### Frontend (`web/charts.jsx`)

Add to `web/data-loader.js` (and its `Object.assign(window, ...)` list):

```js
async function fetchEarnings(symbols, allowFetch = true)   // POST /api/earnings, resolves
                                                           // to {} on any network error
```

New in `charts.jsx`, both registered in the end-of-file `Object.assign(window, {...})`:

- `useEarningsCalendar(symbols)`: memoizes on the sorted, joined symbol list, fires one
  `fetchEarnings` per distinct set in an effect, returns `{map, loading}`. Never throws,
  never blocks render, returns `{}` until resolved. Slices the set to the first 40 symbols
  in the order given (the server caps at 40 too), so one panel is always exactly one POST;
  symbols past 40 carry no badge. Called **before** any early return in its host component
  (see the `RebalancePanel` note below).
- `EarningsDateBadge({ nextDate, anchorDate, holdEndDate, estimated, theme })`: renders
  nothing when `nextDate` is null, when `days_until < 0` (a stale Yahoo entry that slipped
  past the fetcher's filter), or when it is more than 45 days out. Otherwise a compact
  `rpt 3d` pill, `~rpt 3d` when `estimated`, with a `title` giving the full date.
  `days_until` is computed from `Y/M/D` parsed as **local** dates
  (`new Date(y, m - 1, d)`), not `new Date('2026-07-28')`, which parses as UTC midnight and
  comes out a day off against a local `new Date()` anywhere west of UTC.

Naming hazard, called out because the codebase already has one: the existing
`EarningsBreakdown` component is about **strategy P&L** by year and month, not company
earnings reports. The new surface therefore uses the word "reports" in all visible copy
(`reports 2026-07-28 (in 0d)`), and no shared prefix with `EarningsBreakdown`.

Styling rule, computed client-side, no new server fields:

- Anchor for the rebalance panel is `rebalance[0].as_of`, which exists **only on the live
  row**: `analysis.live_rebalance` falls back to raw `rebalance_schedule` output (rows with
  `month`, no `as_of`, no `is_live`) when there isn't enough daily data to form a live
  target. So the anchor is `latest.is_live ? latest.as_of : null`, and a null anchor means
  no badges at all rather than an anchor of `undefined` yielding `NaN` days.
- The coming hold ends at the **last calendar day of the month containing `as_of`**, because
  the live row is the 1-month-hold sleeve and rebalances at the next month-end close.
- Warning style (`#b45309` on `#fef3c7`, the same pair `leaderboardBadge`'s `spike` uses)
  when `0 <= days_until <= days_to_hold_end`. Neutral style (`theme.muted` on `theme.bg`)
  for a date past the hold end but within 45 days.
- Leaderboard rows have no hold concept, so their anchor is `data.window.end_date` and the
  warning threshold is a flat 14 days.

Placement, 4 call sites:

1. `RebalanceChip` (`charts.jsx`): badge appended inside the chip for `kind === 'buy'` and
   `kind === 'keep'`, not for `'sell'` (a position being sold does not carry the event), and
   only when the chip is handed an `entry` prop. **Only the live row's Buy/Keep columns pass
   one.** `RebalanceChip` is shared with `RebalanceHistoryRow`, so an unconditional
   badge would stamp "reports in 3 days" onto a 2025-03 buy chip, where a forward date is
   meaningless; it would also blow the symbol budget (13 history rows x up to 10 buys) past
   the 40-symbol cap. History chips therefore stay exactly as they are today.
2. `TopPerformersPanel`'s ticker cell, right after the existing `leaderboardBadge` span.
   When both would render, the report badge goes second.
3. The bubbling-under chips in the same panel: a bare `rpt 3d` suffix, no pill, to keep the
   chip row scannable.
4. `RebalancePanel` gains one line of subtitle copy: `report dates from Yahoo, cached 24h;
   absent where unavailable`.

`useEarningsCalendar` is called once in `RebalancePanel` (symbols = the live row's
`buys + holds`, i.e. its `picks`; `[]` when `rebalance[0]` is not live) and once in
`TopPerformersPanel` (symbols = `results` + `watchlist` tickers, capped at 40 in
leaderboard order), so a default view issues 2 POSTs of at most 40 symbols each, most of
which resolve from the JSON cache.

Hook-order trap in `RebalancePanel`: its first statement today is
`if (!rebalance || !rebalance.length) return null;` and the component has **zero** hooks.
Calling `useEarningsCalendar` after that guard makes the hook count vary between renders,
which React rejects the moment `REBALANCE` goes from `[]` to populated without a remount.
The hook call goes above the guard, with `[]` when there is no live row.

`web/index.html`: bump `?v=4` to `?v=5` on all 4 asset tags. The sibling
`ticker-drilldown-drawer` spec claims the same bump, so whichever lands second takes the
next free number instead of re-writing `?v=5`.

### Invariants

- **I1** (live target matches the leaderboard): untouched. No ranking code changes. Both
  surfaces read the same badge map keyed by symbol, so a name showing `rpt 3d` in the
  leaderboard shows the same in the trade list.
- **I2** (defaults leave output byte-identical): no change to any existing endpoint
  response, no new `BacktestParams` field, and every existing number unchanged. With the
  network down or `MOMENTUM_OFFLINE=1` the only visual difference from today is the one
  added `RebalancePanel` subtitle line (which is deliberate: it is what tells the user an
  absent badge means "unknown", not "no earnings"). Nothing else moves.
- **I3** (statistical honesty): the badge is descriptive only. It does **not** filter,
  reweight, or reorder picks. A missing date renders nothing rather than "no earnings",
  because absence of data is not absence of an event; `estimated` dates are visibly
  marked.
- **I4** (local, no-build, no execution): one new backend module, one new endpoint, JSX
  edits only. No broker surface.
- **I5** (airgap): **this is a new runtime network dependency**, on a different yfinance
  surface than `yf.download` (per-symbol `quoteSummary`, slower, flakier, sometimes empty).
  It must be named in `README.md`'s existing networking notes (the block around the
  "First run downloads ~500 tickers" and TLS-inspection bullets), which is therefore a
  required file edit, not a follow-up. Degradation path: cache-first, so a site
  that seeded `cache/earnings_dates.json` before disconnecting keeps badges for 24h and
  then silently loses them; `MOMENTUM_OFFLINE=1` makes the endpoint cache-only with zero
  outbound requests and a `offline: true` flag the panel copy surfaces as
  `report dates unavailable (offline)`. Request budget on a connected site: at most 40
  requests per panel refresh, 8 concurrent, 8 s wall clock, 0 requests on a warm cache.

## Testing

New `tests/test_earnings.py`, using `test_cache_corrupt.py`'s `_point_cache_at(tmp_path,
monkeypatch)` idiom extended with `EARNINGS_JSON`, and the injected `fetcher` seam
throughout (no network in any test):

- `next_earnings(["A","B"], fetcher=fake)` calls `fake` exactly once per symbol and returns
  both entries; a second call with the same symbols calls `fake` **zero** times and reports
  `meta["n_cached"] == 2`.
- TTL split: an entry written with `fetched_at` 25h ago and `next != None` is dropped by
  `load_earnings()`; an entry 7h old with `next: None` is dropped; an entry 3h old with
  `next: None` is served from cache and triggers no fetch.
- A fetcher that raises for one symbol: the other symbol's entry is returned, the raiser
  appears in `meta["failed"]`, and `load_earnings()` afterwards has **no** key for it
  (a failure is never persisted as a negative).
- Budget: a fetcher that sleeps 5 s for one symbol with `budget_s=0.5` returns the fast
  symbols, lists the slow one in `meta["timed_out"]`, and the call itself returns in under
  ~1 s.
- `allow_fetch=False` with an empty cache returns `({}, meta)` with `meta["offline"] is
  True` and the fetcher never invoked.
- `max_symbols=2` with 5 requested symbols fetches exactly the first 2 in the given order.
- `_fetch_next_earnings` date parsing, against synthetic yfinance shapes rather than the
  network: `{'Earnings Date': [date(2026,7,30)]}` -> `{"next": "2026-07-30", "estimated":
  False}`; a 2-element list -> the earlier date with `estimated: True`; `None` ->
  `{"next": None}`; a `get_earnings_dates`-shaped DataFrame whose index mixes past and
  future timestamps -> the earliest future date, `source: "history"`. A `get_calendar`
  date already in the past -> `{"next": None}` (the stale-entry filter), which
  `load_earnings` then ages out on the 6h negative TTL, not the 24h one.
- `cache.clear()` removes `EARNINGS_JSON`.
- Concurrency: two `save_earnings` calls with disjoint symbol maps leave both symbols on
  disk (merge, not overwrite).

No assertions against `tests/test_analysis.py`'s fixtures: this feature touches no pure
analysis function and no price frame, and pinning it there would imply otherwise.

## Non-goals

- Using the report date in the signal (skipping or deferring a pick that reports inside the
  hold). That is a strategy change needing its own backtest, and it cannot be evaluated
  honestly without historical report dates, which Yahoo's calendar surface does not give.
- Historical earnings dates, EPS estimates, surprise history, or post-earnings-drift
  analysis, even though `get_earnings_dates` returns the columns for it.
- Badges for `SELL` chips, cohort-history chips, or the sector panel.
- Universe-wide earnings coverage (504 symbols) or any scheduled/background prefetch.
- Making the rest of the app offline-mode aware. `MOMENTUM_OFFLINE=1` gates only the
  earnings endpoint in this spec; price fetching keeps its current behavior.

## Risks / open questions

- **Yahoo's calendar accuracy.** `'Earnings Date'` is sometimes an estimate ("week of"),
  sometimes stale after a company reschedules, and occasionally wrong. The badge is
  therefore advisory, marked `~` when Yahoo signals a range, and the panel copy names the
  source. There is no second source to cross-check against without a new dependency.
- **Ambiguous timing within the day.** `get_calendar` returns a bare date, so a
  before-open vs after-close report is indistinguishable. `get_earnings_dates` carries a
  tz-aware timestamp (e.g. `16:00:00-04:00`) but costs 5x and is only consulted as a
  fallback. **Judgement call for the user:** is "reports on date D" enough, or is
  before/after the close worth always paying the more expensive call for?
- **Hold-window definition.** The warning uses "end of the calendar month containing
  `as_of`", which on a run late in the month (today is the 28th) warns for a 3-day window
  and stays neutral for a report 5 days out that will in fact land in the *next* hold.
  Alternatives: a flat 30-day window from `as_of`, or `as_of` to next month-end plus the
  selected `holds` maximum. **Needs the user's call**, since it encodes how they actually
  trade the list.
- **Rate limiting.** 40 `quoteSummary` requests per refresh with 8 concurrent workers has
  not been observed to trip Yahoo throttling in testing, but Yahoo has no published limit
  and behavior can change. Mitigations already in the design: cache-first, negative
  caching, hard symbol cap. If throttling appears, drop `workers` to 4 and rely on the
  24h TTL.
- **Blank badges read as "no earnings soon".** A timed-out or unknown symbol renders
  nothing, which is honest but could be misread as reassuring. Mitigation: the panel
  subtitle states that absent badges mean unknown. An explicit `rpt ?` pill for known
  failures is a possible follow-up but adds noise to the common airgap case.
- **The 40-symbol cap silently truncates a large leaderboard.** At `topN >= 40` the rows
  past 40 (and all of the bubbling-under chips) get no badge, which is
  indistinguishable from "unknown" in the UI. Chunking into 3 sequential POSTs would fix
  the coverage but triples the request budget for a decoration, on a surface where the
  10-pick default is the real use case. **Open:** accept truncation at high `topN`, or
  badge only the first `topN` rows and drop the watchlist suffix entirely so the boundary
  is at least legible.
- **The history rows stay unbadged by design**, which means the same symbol can show a
  badge on the live row and nothing on the 2026-06 row two lines below. That is correct
  (the event belongs to the coming hold, not to a closed one) but looks inconsistent.
  Alternative is a per-row anchor, which needs a report date *as of that month* and Yahoo's
  calendar surface has no history.
