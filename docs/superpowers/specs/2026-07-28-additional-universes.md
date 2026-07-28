# Additional universes: Nasdaq-100, S&P MidCap 400, custom ticker list

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** nothing

## Problem

The tool has exactly one single-name universe. `lib/data.get_universe()` resolves 3 keys, and
2 of them (`global_etfs`, `us_sector_etfs`) are 37 and 11 baskets. Every single-name idea the
dashboard has ever produced comes from the same 503 large-cap S&P names, so:

- The 2026-06 cohort at -27.2% (GLW -43.9%, SNDK -43.8%, MRVL -36.5%) is a single-factor
  blowup in large-cap semis/optical, and the user has no other single-name universe to rank.
  The live target on the 2026-07-28 run went 10 in / 10 out / 0 kept while the strategy sat
  at -14.2% MTD vs SPY -1.2%: maximum churn in one crowded corner of one index.
- The headline evidence (+470% total vs SPY +89%, best-Sharpe sleeve hold6m 1.33 vs SPY 0.87,
  7 of 12 event-study horizons at |t| >= 2 with h=11 at t=4.34) is one universe's worth of
  out-of-sample. Mid-cap momentum is historically the stronger version of the same effect,
  and the tool cannot test it.
- The user's own watchlist cannot be run through any of this machinery at all.

The plumbing is closer than it looks: `cache/prices.parquet` is universe-agnostic and the
missing-ticker delta path in `cache.get_prices_cached` (branch 1, `requested_set -
cached_tickers`) already handles mixed ticker sets. What blocks new universes is that
`cache.load_tickers()/save_tickers()` and `cache.load_metadata()/save_metadata()` are a
single **global** pair of files (`cache/tickers.json`, `cache/sp500_meta.json`) with no
universe key, so a second scraped index would silently overwrite the S&P 500 list.

## Goal

6 universe keys instead of 3: `sp500`, `nasdaq100`, `sp400`, `global_etfs`, `us_sector_etfs`,
`custom`. Each scraped index has its own TTL-cached ticker and metadata files. A custom
comma-separated ticker list runs through the identical backtest, event study, leaderboard and
rebalance code with no scrape at all. Both dashboards can select any of them, and V1 stops
showing an S&P 500 leaderboard above a Global-ETF rebalance panel.

## Design

### Source tables (verified against live Wikipedia, 2026-07-28)

| key | URL | table | symbol col | name col | sector col | rows |
|---|---|---|---|---|---|---|
| `sp500` | `/wiki/List_of_S%26P_500_companies` | 0 | `Symbol` | `Security` | `GICS Sector` | 503 |
| `sp400` | `/wiki/List_of_S%26P_400_companies` | 0 | `Symbol` | `Security` | `GICS Sector` | 400 |
| `nasdaq100` | `/wiki/List_of_NASDAQ-100_companies` | 0 | `Ticker` | `Company` | `ICB Industry[1]` | 103 |

Two corrections to the naive plan, both verified today:

1. **The Nasdaq-100 article has no components table.** `pd.read_html` on
   `/wiki/Nasdaq-100` returns 18 tables and none contain tickers (the string `AAPL` appears 0
   times in the HTML). The list moved to `/wiki/List_of_NASDAQ-100_companies` (note the
   capitalized NASDAQ; `List_of_Nasdaq-100_companies` 404s).
2. **Nasdaq-100 uses ICB, not GICS**, its symbol column is `Ticker` not `Symbol`, and its
   sector column name carries a live footnote suffix (`ICB Industry[1]`) that changes when the
   article's references are renumbered.

So the scraper must not hardcode a table index or an exact column name. Add to `lib/data.py`:

```python
_WIKI_INDEX_SOURCES: dict[str, dict] = {
    "sp500":     {"url": ..., "symbol": "Symbol", "name": "Security", "sector": "GICS Sector"},
    "sp400":     {"url": ..., "symbol": "Symbol", "name": "Security", "sector": "GICS Sector"},
    "nasdaq100": {"url": ..., "symbol": "Ticker", "name": "Company",  "sector": "ICB Industry"},
}

def _base_col(c) -> str | None            # str columns only; strips a trailing "[<digits>]"
def _pick_index_table(tables: list[pd.DataFrame], spec: dict) -> pd.DataFrame
def _scrape_index_table(spec: dict) -> pd.DataFrame   # requests.get + read_html + _pick
def get_index_universe(key: str) -> tuple[list[str], dict[str, dict]]
```

`_pick_index_table` returns the first table whose stripped string column names contain both
`spec["symbol"]` and `spec["name"]`, renamed to canonical `symbol/name/sector`. This is
unambiguous on all 3 pages: the sibling "Selected changes" table has `MultiIndex` tuple
columns, which `_base_col` returns `None` for, and no navbox table carries those pairs.
`_pick_index_table` takes an already-parsed list, so it is unit-testable without network.

Symbol and name are **required** for a match, so a page-layout change raises. `sector` is
**optional** and resolves to `""` when the column is absent, which is exactly today's
tolerance (`str(row.get("GICS Sector", ""))`) and which `cohort_sector_breakdown` already
renders as `Unknown`. A renamed sector column therefore degrades the breakdown panel instead
of killing the universe.

`get_index_universe(key)` is the single entry point: it reads `cache.load_tickers(key)` and
`cache.load_metadata(key)`, returns both on a hit, otherwise scrapes **once** and writes both
caches. Tickers keep Wikipedia row order and the `.` -> `-` Yahoo conversion (sp400 has
exactly 1 dotted symbol, `MOG.A` -> `MOG-A`, verified to resolve on Yahoo). Metadata is built
in the same row order.

`get_sp500_tickers()` and `get_sp500_metadata()` stay as one-line wrappers over
`get_index_universe("sp500")` so `momentum_backtest.py` (the CLI) keeps working unchanged. As
a side effect a cold S&P 500 run drops from 2 requests to the same page down to 1.

### Per-universe JSON caches (the main refactor)

`lib/cache.py` today:

```python
TICKERS_JSON     = CACHE_DIR / "tickers.json"        # module constant
SP500_META_JSON  = CACHE_DIR / "sp500_meta.json"     # module constant
load_tickers() / save_tickers(tickers)
load_metadata() / save_metadata(metadata)
clear()   # unlinks (PRICES_PARQUET, PRICES_META, TICKERS_JSON, SP500_META_JSON)
```

becomes:

```python
JSON_CACHE_KEYS = ("sp500", "nasdaq100", "sp400")
CUSTOM_UNIVERSE_JSON_NAME = "custom_universe.json"

def _tickers_path(universe: str) -> Path:   # computed from CACHE_DIR at call time
    if universe == "sp500":
        return CACHE_DIR / "tickers.json"        # legacy name, no migration
    return CACHE_DIR / f"tickers_{universe}.json"

def _meta_path(universe: str) -> Path:
    if universe == "sp500":
        return CACHE_DIR / "sp500_meta.json"     # legacy name, no migration
    return CACHE_DIR / f"{universe}_meta.json"

def load_tickers(universe: str = "sp500") -> list[str] | None
def save_tickers(tickers: list[str], universe: str = "sp500") -> None
def load_metadata(universe: str = "sp500") -> dict[str, dict] | None
def save_metadata(metadata: dict[str, dict], universe: str = "sp500") -> None
def load_custom_universe() -> dict | None      # {"saved_at", "raw", "tickers"}; NO TTL
def save_custom_universe(raw: str, tickers: list[str]) -> None
```

Three deliberate details:

- **sp500 keeps its exact legacy filenames**, so no migration step and no forced re-scrape on
  first run after the upgrade. The existing on-disk `cache/tickers.json` and
  `cache/sp500_meta.json` stay valid.
- Paths are **functions, not module constants**, computed from `CACHE_DIR` at call time, so a
  test can `monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)` and redirect every JSON cache
  in one line. The 4 existing call sites in `lib/data.py` are the only callers.
- All JSON writes move to the existing `cache._atomic_write` (mkstemp + `os.replace`). The
  current `TICKERS_JSON.write_text(...)` is the same partial-write hazard that produced the
  corrupt-parquet bug in commit 5607480.

`clear()` deletes `PRICES_PARQUET`, `PRICES_META`, and `_tickers_path(k)` / `_meta_path(k)`
for every `k in JSON_CACHE_KEYS`. It deliberately does **not** delete
`custom_universe.json`: clearing the price cache must not destroy the user's watchlist. A
`POST /api/custom-universe` with a blank `raw` is the explicit reset, and that path has to
**bypass** `parse_custom_tickers`, which raises on fewer than 2 tokens and would otherwise
make the reset a 400. So: `raw.strip() == ""` unlinks the file and returns
`{raw: "", tickers: [], count: 0, saved_at: None}`; anything else is parsed and validated.

### Custom universe

Stored server-side rather than passed per request, because `TopPerformersPanel` calls
`fetchTopPerformers({universe, topN, days})` and `/api/universes` needs the size for
client-side validation. Both would otherwise need the raw list threaded through them.

`lib/data.py`:

```python
_CUSTOM_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{0,9}$")
CUSTOM_MAX_TICKERS = 250

def parse_custom_tickers(raw: str) -> list[str]:
    """Split on comma/whitespace/newline, upper, '.'->'-', dedupe preserving order.
    Raises ValueError naming every rejected token, or when the list is <2 or >250."""
    # NOTE: whitespace is a separator, so a pasted company name does NOT arrive as one
    # rejectable token: "MY STOCK" splits into "MY" and "STOCK", both of which match
    # _CUSTOM_TICKER_RE. Only characters outside the class (e.g. "BRK/B") or a >10-char
    # token are rejected here; junk that is syntactically a ticker survives to the
    # absent-ticker status message below. That is the accepted tradeoff for accepting
    # space- and newline-separated pastes.

def get_custom_universe() -> tuple[list[str], dict[str, dict]]:
    """Saved list + best-effort metadata. Raises ValueError when nothing is saved."""
```

Custom metadata is assembled with **zero new network calls**: for each ticker, look it up in
whatever per-universe metadata caches already exist on disk (`sp500`, `sp400`, `nasdaq100`)
and then in the hardcoded `universes.GLOBAL_EQUITY_ETFS` / `US_SECTOR_ETFS` tables, falling
back to `{"name": ticker, "sector": "Unknown"}`. A watchlist of mostly S&P names therefore
still drives a meaningful sector-breakdown panel, and an airgapped box degrades to
`Unknown` labels instead of failing.

Typos cannot corrupt results: `_fetch_prices` drops all-NaN columns and `cache._slice` drops
them again, so an unresolvable symbol simply disappears. That silent disappearance is the
honesty gap (I3), so `_run_backtest` gains 4 lines next to the existing coverage block:

```python
absent = sorted(set(universe_tickers) - set(prices.columns))
if absent:
    on_status(f"{params.universe}: {len(absent)} requested ticker(s) returned no data: "
              f"{', '.join(absent[:8])}" + ("…" if len(absent) > 8 else ""))
```

The identical 4 lines go in `_top_performers` too (it computes `universe_size` from
`prices.shape[1]` and would otherwise shrink just as silently), using its local
`tickers`/`prices` names. Note the I2 consequence: on an sp500 run where Yahoo happens to
return nothing for a current constituent, `status_messages` gains a line it does not have
today. Every number stays identical; `status_messages` is the single field that can differ,
and that is the intended I3 disclosure rather than an accident.

### lib/universes.py

```python
UNIVERSE_KEYS = ("sp500", "nasdaq100", "sp400", "global_etfs", "us_sector_etfs", "custom")
SCRAPED_KEYS  = ("sp500", "nasdaq100", "sp400")
```

plus new entries in the 3 existing dicts:

| key | `UNIVERSE_LABEL` | `META_LABEL` | `DEFAULT_BENCHMARK` |
|---|---|---|---|
| `nasdaq100` | `Nasdaq-100 single names` | `ICB Industry` | `QQQ` |
| `sp400` | `S&P MidCap 400 single names` | `Sector` | `MDY` |
| `custom` | `Custom ticker list` | `Sector` | `SPY` |

`QQQ`, `MDY` and `ACWI` all verified to return bars from `yf.download` today. A module-level
assertion checks that all 3 dicts cover `UNIVERSE_KEYS` exactly, so a future key cannot slip
through and `KeyError` inside `_run_backtest`'s response assembly (lines 290 to 291),
`_top_performers`'s (lines 398 to 399) or `universe_info`'s (line 436), all of which index
those dicts directly rather than via `.get`.

`ICB Industry` is kept verbatim rather than harmonized to GICS, so the Nasdaq-100 sector
breakdown is honest about its taxonomy but **not** comparable to the S&P breakdowns. Flagged
in open questions.

### server.py touchpoints (all of them)

1. Line 23: `UniverseKey = Literal["sp500", "nasdaq100", "sp400", "global_etfs",
   "us_sector_etfs", "custom"]`. `BacktestParams.universe` and
   `TopPerformersParams.universe` pick this up for free.
2. `universe_info()` line 424: the hardcoded `for key in ("sp500", "global_etfs",
   "us_sector_etfs")` becomes `for key in universes.UNIVERSE_KEYS`, but it must **not** call
   `data.get_universe` for the 2 new scraped keys. `/api/universes` is hit on every page
   mount (`index.html`'s `useEffect(() => { fetchUniverses().then(setUniverses); }, [])`), so
   looping `get_universe` over `UNIVERSE_KEYS` would scrape 3 Wikipedia pages on the first
   load even when the user never leaves sp500, and on an unreachable network it would
   serialize 3 `requests.get(..., timeout=20)` failures into that mount-blocking call
   instead of today's 1. So: `sp500` keeps calling `data.get_universe` (unchanged, preserves
   I2); `nasdaq100` and `sp400` read `cache.load_tickers(key)` only and report `size: None`
   when that cache is cold (**no scrape from this endpoint**); the 2 ETF keys read their
   hardcoded tables; `custom` reads `cache.load_custom_universe()` and reports `size: 0`
   when nothing is saved. `max_topN` is `None` whenever `size` is `None` or `0`, which the
   existing `maxTopNFor` already handles by falling back to the hard ceiling of 100. The
   server-side `topN >= len(universe_tickers)` 400 stays the backstop until that universe's
   first real run warms its ticker cache.
3. `universe_info()` gains `"features": {"customUniverse": True}` so the client knows the
   endpoint exists. `cross-universe-scan` and `point-in-time-membership` each add a key to
   this same dict, so whichever lands second must **add to** `out["features"]`, not assign a
   fresh literal over it.
4. New endpoints:
   - `GET  /api/custom-universe` -> `{raw, tickers, count, saved_at}` or `{tickers: [], count: 0}`.
   - `POST /api/custom-universe` body `{raw: str}` -> a blank `raw` resets (see above);
     otherwise parses via `data.parse_custom_tickers` (`ValueError` -> 400 with the rejected
     tokens), saves, returns the same shape.
5. `_run_backtest` line 91 and `_top_performers` line 323 already route through
   `data.get_universe`, which raises `ValueError` -> 400 for an unsaved custom list. No change.
6. `_run_backtest`: the absent-ticker status message above.

`lib/data.get_universe(name)` dispatch: `sp500|nasdaq100|sp400` -> `get_index_universe(name)`,
`global_etfs|us_sector_etfs` -> unchanged, `custom` -> `get_custom_universe()`.

### Frontend touchpoints (all of them)

`UNIVERSE_OPTIONS`, `UNIVERSE_DEFAULTS` and `UNIVERSE_NOUN` **move from `web/v2.jsx` (lines 27
to 43) into `web/charts.jsx`** and are added to the end-of-file `Object.assign(window, {...})`,
so V1, V2 and `TopPerformersPanel` share one table. New rows:

```js
UNIVERSE_DEFAULTS.nasdaq100 = { benchmark: 'QQQ', topN: 10, rankLookback: 1, holds: [1, 3, 6] };
UNIVERSE_DEFAULTS.sp400     = { benchmark: 'MDY', topN: 10, rankLookback: 1, holds: [1, 3, 6] };
UNIVERSE_DEFAULTS.custom    = { benchmark: 'SPY', topN: 5,  rankLookback: 1, holds: [1, 3, 6] };
UNIVERSE_NOUN.nasdaq100 = 'Nasdaq-100 names';
UNIVERSE_NOUN.sp400     = 'S&P 400 names';
UNIVERSE_NOUN.custom    = 'watchlist names';
```

There is no shared universe-change handler today: `onUniverseChange` is a local closure inside
`V2Sidebar` (`v2.jsx` line 46). Rather than clone it into `V1Sidebar`, it moves to `charts.jsx`
as `applyUniverseDefaults(u, setParam, universes)` and is registered in the end-of-file
`Object.assign` alongside the 3 tables. It applies `UNIVERSE_DEFAULTS[u]`, then clamps
`topN` to `Math.min(d.topN, maxTopNFor(universes, u))` so switching to a 12-name custom list
cannot leave `topN: 10` above the ceiling, then sets `universe` last.

- **`charts.jsx` / `TopPerformersPanel` (line 1046):** the local
  `universeDefaultTopN = {sp500: 10, global_etfs: 5, us_sector_etfs: 3}` is replaced by
  `Math.min((UNIVERSE_DEFAULTS[universe] || {}).topN || 10, maxTopNFor(universes, universe))`.
  Keep the `|| 10` fallback of the current code, since `universe` arrives as a prop and an
  unknown key must not throw on `.topN` of `undefined`. Both the `useState` initializer (line
  1047) and the universe-change effect (line 1075) use it. The hardcoded fallback label map
  (lines 1124 to 1127) is replaced by a lookup in `UNIVERSE_OPTIONS`, still behind the
  existing `data?.universe_label ||` so a stale panel keeps rendering.
- **`charts.jsx` / new `CustomUniverseField`** (registered in the `Object.assign`): a textarea
  plus Save button, rendered in both sidebars only when `params.universe === 'custom'`. On
  save it calls a new `saveCustomUniverse(raw)` in `data-loader.js`, then `refreshUniverses()`
  so `maxTopNFor` and `paramIssues` pick up the new size immediately.
- **`index.html`:** `App` gains
  `const refreshUniverses = useCallback(async () => setUniverses(await fetchUniverses()), [])`
  passed down in `dashProps` (the mount effect at `useEffect(() => { fetchUniverses()... })`
  becomes `useEffect(() => { refreshUniverses(); }, [refreshUniverses])`). Every asset query
  bumps `?v=4` -> `?v=5`. `DEFAULT_PARAMS` is **unchanged** (`universe: 'sp500'`), which is
  what keeps I2.
- **`data-loader.js`:** `fetchCustomUniverse()` and `saveCustomUniverse(raw)` added to the
  `Object.assign(window, {...})`.
- **`v2.jsx`:** its 3 local universe tables are deleted (now from `charts.jsx`); the caveats
  block (lines 858 to 893) gains 3 branches:
  - `nasdaq100`: same survivorship warning as sp500 plus "~103 securities, not 100 (multi-class
    share lines); sectors are ICB Industry, not GICS, so this breakdown is not comparable to
    the S&P views; the index is ~60% technology by weight, so a top-10 cohort is close to a
    single-factor bet".
  - `sp400`: same survivorship warning plus "mid-caps are thinner: bid/ask spreads and market
    impact matter far more than for S&P 500 names, and none of that is modelled, so the
    unmodelled-cost gap between this backtest and a real fill is wider here than anywhere else
    in the tool".
  - `custom`: "your list, your survivorship bias: a watchlist assembled today already knows
    which names worked. Sector labels are best-effort from cached index metadata. Nothing about
    the list is validated beyond ticker syntax; symbols Yahoo cannot resolve are dropped and
    reported in the run log".
- **`v1.jsx`:** two fixes.
  1. A `Universe` `<select>` is added to `V1Sidebar` above the existing "Lookback (years)"
     `NumField` (line 72), using the shared `UNIVERSE_OPTIONS` and `onUniverseChange`. V1
     already consumes `params.universe` at line 79 (`maxTopNFor(universes, params.universe)`)
     and line 612 (`RebalancePanel universe=...`) while offering no way to set it.
  2. **The asymmetry bug:** line 328 renders
     `<TopPerformersPanel theme={v1Theme} accent={v1Accent} cacheInfo={cacheInfo}
     universes={universes} />` with **no `universe` prop**, so the panel falls back to its
     `universe = 'sp500'` default. Because `params` is shared App state across the V1/V2 hash
     switch, a V2 `global_etfs` run then leaves V1 showing an S&P 500 leaderboard directly
     above a Global-ETF rebalance panel that claims a matching signal. Fix:
     `universe={params.universe || 'sp500'}`, matching v2.jsx line 363.

### Invariants

- **I1:** no ranking logic is added or forked. `top_performers_period` and `current_target`
  are untouched, so the live-target/leaderboard alignment holds inside every new universe.
  Fixing V1's missing `universe` prop *restores* I1 in a place it was silently broken.
- **I2:** `DEFAULT_PARAMS.universe` stays `sp500`; sp500 keeps its exact legacy cache
  filenames and its Wikipedia row order; all new `cache` params are keyword-defaulted to
  `"sp500"`; `get_index_universe("sp500")` must keep emitting metadata values of exactly
  `{"name": str(...), "sector": str(...)}` (what `server.py:241` and `_top_performers`'s
  `enrich` read). Every number in an sp500 run is byte-identical; the sole exception is
  `status_messages`, which can gain the absent-ticker line above.
- **I3:** requested-but-unresolvable tickers are now reported instead of silently dropped;
  `Unknown` is used for missing sectors rather than an invented one.
- **I4:** no build step, no new dependency (`requests` + `pandas.read_html` + `lxml` are
  already in `requirements.txt`), no broker anything.
- **I5:** the only new runtime network dependency is 2 more Wikipedia GETs, each behind the
  same 24h `TICKERS_TTL_HOURS` disk cache as sp500 today, and each only on first use of that
  universe, which holds **only** because `/api/universes` is barred from scraping the new
  keys (see server touchpoint 2). Offline degradation: a warm `tickers_sp400.json` + warm `prices.parquet` runs the
  sp400 universe fully offline; a cold cache surfaces the existing 502 "Universe fetch failed".
  `custom` needs **no** scrape at all, so a genuinely airgapped site can define its universe by
  hand and still use every panel. Price cost on a warm sp500 cache: `nasdaq100` adds only 15
  new tickers (88 of its 103 are already S&P 500 members), `sp400` adds 400 (disjoint from
  sp500). Union of the 3 single-name universes is 918 tickers.

## Testing

All new tests are network-free. `monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)` redirects
every JSON cache, which works only because the paths are functions (see Design). It does
**not** redirect `PRICES_PARQUET` / `PRICES_META`, which stay module constants, so the
`clear()` test must reuse `test_cache_corrupt.py`'s `_point_cache_at(tmp_path, monkeypatch)`
helper (CACHE_DIR + both price constants) or it will unlink the developer's real
`./cache/prices.parquet`.

- `_base_col`: `"ICB Industry[1]"` -> `"ICB Industry"`, `"Symbol"` -> `"Symbol"`,
  `("Added", "Ticker")` -> `None`.
- `_pick_index_table([changes_like, constituents_like], _WIKI_INDEX_SOURCES["nasdaq100"])`
  returns the constituents frame, where `changes_like` is built with the real MultiIndex
  columns `[("Date","Date"), ("Added","Ticker"), ("Added","Security"), ("Removed","Ticker"),
  ("Removed","Security"), ("Reason","Reason")]` and `constituents_like` has `Ticker`,
  `Company`, `ICB Industry[1]`. Assert the renamed frame exposes `symbol/name/sector`.
- `_pick_index_table` raises when no table matches (a page-layout change must fail loudly,
  not return an empty universe).
- Per-universe cache isolation: `cache.save_tickers(["A","B"], "sp400")` then
  `cache.load_tickers("sp500") is None` and `cache.load_tickers("sp400") == ["A","B"]`.
- Legacy path pinning: `cache.save_tickers(["A"])` writes exactly
  `tmp_path/"tickers.json"`, and `cache.save_metadata({...})` writes exactly
  `tmp_path/"sp500_meta.json"` (no migration, no re-scrape after upgrade).
- TTL still applies per key: hand-write a `tickers_sp400.json` with a `fetched_at` 25h old and
  assert `load_tickers("sp400") is None` (mirrors the existing 24h rule).
- `clear()` removes the keyed ticker/metadata files for all of `JSON_CACHE_KEYS` and leaves
  `custom_universe.json` in place.
- `parse_custom_tickers(" aapl,msft ,, brk.b\nnvda ")` == `["AAPL","MSFT","BRK-B","NVDA"]`;
  `"AAPL,AAPL"` == `["AAPL"]`; `"AAPL, BRK/B"` raises `ValueError` whose message contains
  `BRK/B`, and `"AAPL, TOOLONGTICKER"` raises naming that token; a 1-token list raises;
  251 tokens raises. (Do **not** assert on `"AAPL, MY STOCK"`: whitespace is a separator,
  so that input yields `["AAPL","MY","STOCK"]` and raises nothing.)
- `get_custom_universe()` with a saved `["XLE","ZZZZ"]` returns sector `"Energy"` for `XLE`
  (from the hardcoded `US_SECTOR_ETFS` table, no network) and `"Unknown"` for `ZZZZ`; with
  nothing saved it raises `ValueError`.
- `universes`: assert `set(UNIVERSE_LABEL) == set(META_LABEL) == set(DEFAULT_BENCHMARK) ==
  set(UNIVERSE_KEYS)`.
- Regression: the whole existing `tests/test_analysis.py` (45 tests) is untouched, since
  `lib/analysis.py` is not modified by this spec.

## Non-goals

- Point-in-time membership for any universe, including the 2 new ones (see
  `point-in-time-membership`; both new pages do carry change tables, 618 rows for sp400 and
  227 for nasdaq100, so they are a natural follow-up).
- Russell 2000 / non-US listings / crypto. The Russell constituent list is not on a scrapable
  Wikipedia table and 2000 tickers would blow up the single-parquet cache design.
- Harmonizing Nasdaq-100 ICB industries to GICS.
- Multiple saved custom lists or named watchlists. One list, one file.
- A cross-universe view. That is `cross-universe-scan`, which this spec unblocks.
- Per-universe price-cache partitioning. The shared parquet grows to the union (~966 columns
  with everything wired), which the existing delta path handles; only the sidebar's
  `cacheInfo.n_tickers` copy needs to read "tickers cached (all universes)".

## Risks / open questions

- **Wikipedia layout drift is now a 3-page dependency.** The Nasdaq-100 components table has
  already moved off the main article once, so a second move breaks that universe. The
  column-signature search plus a loud raise (never an empty universe) is the mitigation, but
  the failure mode is a 502 on that universe until the URL is updated.
- **Custom-list storage is a design fork.** Server-side `cache/custom_universe.json` (chosen)
  keeps `TopPerformersParams` and `/api/universes` unchanged and survives a restart; a
  per-request `customTickers` field would be stateless but must be threaded through
  `TopPerformersPanel`, the scan endpoint and `/api/universes`. If the user wants the list to
  be purely session state, this flips.
- **Should `cache.clear()` wipe the custom list?** Chosen: no. It is user-authored content,
  not fetched data. Worth confirming, since the button is labelled "clear cache".
- **Nasdaq-100 sector taxonomy.** Kept as ICB Industry, so the `us_sector_etfs` crosschecks in
  `cross-universe-scan` and the S&P sector breakdown are not directly comparable for this
  universe. Alternative: prefer the GICS sector from the sp500/sp400 metadata caches for the
  88 overlapping names and fall back to ICB for the other 15, labelled "Sector (GICS where
  known)". Cleaner data, uglier label. User's call.
- **250-ticker cap on custom lists** is arbitrary, chosen to keep a cold custom run under the
  ~60s the sp500 pull already takes. Raise it if the user wants to paste a 1000-name list, but
  the cold-fetch time scales roughly linearly.
- **`sp400` cold-run cost** is a real 400-ticker download (~60s, same order as sp500) and it
  permanently adds 400 columns to the shared parquet. No way around it short of partitioning
  the cache. Worse than the ~60s estimate in one case: `cache.get_prices_cached` branch 1
  fetches missing tickers over `min(requested_start, cached_start)` to
  `max(requested_end, cached_end)`, i.e. the **whole span the parquet already covers**, not
  the requested window. So a first sp400 run against a parquet already holding a 25-year
  sp500 window pulls 400 tickers x 25 years, not 400 x 5. Existing behavior, not new, but the
  new universes are the first thing big enough for it to hurt.
- **A custom list containing the benchmark loses that name from the universe.**
  `_run_backtest` does `constituents = prices.drop(columns=[params.benchmark])`, which no
  existing universe trips (SPY/ACWI/QQQ/MDY are not members of any of them) but a watchlist
  that includes `SPY` alongside the default `SPY` benchmark does: the ticker resolves, so the
  absent-ticker check stays quiet, and the name silently vanishes from the ranking universe.
  Cheapest honest guard: in `parse_custom_tickers`, or at the top of `_run_backtest`, detect
  `params.benchmark in universe_tickers` and emit an `on_status` line saying it is being used
  as the benchmark and therefore excluded from the ranking universe.
- **`get_sp500_tickers()` now re-scrapes when only the metadata cache is stale**, because
  `get_index_universe` needs both halves and writes them together. Same 24h ceiling, at most
  1 extra GET, and it makes the 2 caches share a `fetched_at` from here on; noted because it
  is a behavior change for the CLI, which calls `get_sp500_tickers()` alone.
- **V1 gets a universe selector** (chosen) rather than documenting universe as V2-only, because
  the shared-`params` design makes "V2-only" produce actively misleading V1 output. The cost is
  a second control surface to keep in sync; the shared `UNIVERSE_OPTIONS` table in `charts.jsx`
  is what makes that cheap.
