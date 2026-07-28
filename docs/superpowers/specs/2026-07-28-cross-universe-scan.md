# Cross-universe scan board with rotation crosschecks

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** additional-universes (optional; the scan works with the 3 universes wired today and picks up new ones for free)

## Problem

`us_sector_etfs` (11 names) and `global_etfs` (37 names) already exist in
`lib/universes.py`, and they are the cheapest data in the tool: 48 tickers total, disjoint from
the S&P 500, so after one small delta fetch every subsequent scan is a pure
`cache/prices.parquet` slice. But seeing them requires changing the sidebar universe select,
re-running the whole backtest, reading the leaderboard, and switching back. Nobody does that
mid-session, so sector-leadership and country-rotation information is effectively invisible.

The cost of that blindness shows up directly in the 2026-07-28 run. The live target went 10 in
/ 10 out / 0 kept while the strategy sat at -14.2% MTD vs SPY -1.2%, and the 2026-06 cohort
lost 27.2% on GLW -43.9%, SNDK -43.8%, MRVL -36.5%: three names in the same corner of one
sector. `analysis.cohort_sector_breakdown` exists and feeds `COHORT_GROUPS`, so the dashboard
can already tell you the cohort was concentrated. What it cannot tell you is the thing that
matters: whether the concentration was a stock-picking result or just the sector rising, and
whether the ETF would have been the cleaner, cheaper expression of the same trade.

There is also a whole class of trades the S&P view structurally cannot show. `XLE` or `EWZ`
never appear in a `sp500` leaderboard, so a country or sector rotation idea has no surface in
the tool at all.

## Goal

One button produces trailing-return leaderboards for every wired universe side by side from a
single price pull, plus a short list of thresholded rotation callouts that connect the
single-name picks to the sector and country boards: which sectors the picks are crowded into
while that sector is already leading, which leading sectors the screen missed entirely, and
when the best ETF simply beat the median single-name pick over the same window.

## Design

### Ranking reuse (I1)

No new ranking logic. Every board is `analysis.top_performers_period`, the same function
`/api/top-performers` and `current_target` use, so a scan board and the leaderboard panel are
identical at matching `topN`/`days`. This is the existing alignment guarantee extended to a
third surface rather than a fourth ranking path.

Two calling conventions, chosen for what `streak` and `new_entrant` mean on each board:

- **Single-name boards** (`sp500`, `nasdaq100`, `sp400`, `custom`):
  `top_performers_period(p, top_n=display_n, days=days, watch_n=min(10, max(0, len(u_tickers) - display_n)))`
  where `display_n = max(1, min(topN, len(cols) - 1))`, then only the first `display_n` rows
  are kept. `watch_n` must be `_top_performers`'s exact expression (line 344), **not** 0:
  `new_entrant` is computed against `ref_top_n = max(top_n * 2, top_n + watch_n)`
  (`analysis.py:417`), so `watch_n=0` at `topN=3` would compare against the top 6 while the
  leaderboard compares against the top 13, and the same ticker would render NEW on one
  surface and not the other. With the same `watch_n`, every field on the first `display_n`
  rows is element-wise identical to `/api/top-performers`. The one deliberate divergence:
  `_top_performers` returns 400 when `topN >= len(tickers)` while the scan clamps via
  `display_n`, so "identical at matching settings" means identical whenever the leaderboard
  would have answered at all.
- **ETF boards** (`us_sector_etfs`, `global_etfs`): the crosschecks need a rank for *every*
  sector ETF, not just the top few, so the whole universe must be ranked. Calling with
  `top_n=len(cols)` would rank everything but make `streak` meaningless (every prior-window
  top-N set would be the whole universe, so every streak saturates at the cap of 12). Instead:
  `top_performers_period(p, top_n=leadRank, days=days, watch_n=max(0, len(cols) - leadRank))`
  (the `max(0, ...)` matters: a sector board where only 2 ETFs resolved with `leadRank=3`
  would otherwise pass a negative `watch_n`). This returns every resolved row in return order
  while keeping `streak` relative to the leadership cutoff, so "XLE has been top-3 for 5
  straight windows" is a real statement. Rank is `index + 1`; the denominator `len(cols)` is
  carried in the response and always printed. `new_entrant` is **not** usable on these
  boards: `ref_top_n = max(top_n * 2, top_n + watch_n)` collapses to `len(cols)`, i.e. the
  whole universe, so every row comes back `False`. `ScanBoard` renders the NEW badge on
  single-name boards only.

### New pure function: `analysis.rotation_crosschecks`

```python
def rotation_crosschecks(
    picks: list[str],                     # anchor board tickers, rank order
    pick_returns: dict[str, float],       # ticker -> window return
    sector_of: dict[str, str],            # FULL anchor universe: ticker -> sector label
    sector_board: list[dict],             # top_performers_period rows over us_sector_etfs
    sector_etf_by_group: dict[str, str],  # sector label -> ETF ticker
    country_board: list[dict] | None = None,
    lead_rank: int = 3,
    min_share: float = 0.30,        # absolute floor for a "crowded" flag
    share_multiple: float = 2.5,    # multiple of the group's universe base share
) -> list[dict]
```

Pure, no I/O, no pandas requirement beyond what is already imported. Each returned row:

```python
{"kind": "crowded" | "missing_leader" | "etf_beats_names",
 "severity": "high" | "medium" | "low",
 "group": "Energy", "etf": "XLE", "etf_rank": 2, "etf_n": 11,
 "etf_return_pct": 0.081, "share": 0.40, "count": 4, "base_share": 0.042,
 "tickers": ["APA", "DVN", "HAL", "OXY"],
 "text": "4 of 10 picks are Energy (40% vs 4% of the universe) and XLE ranks #2 of 11 (+8.1%)."}
```

`sector_etf_by_group` is a new dict in `lib/universes.py`, built by inverting
`US_SECTOR_ETFS[*]["group"]` plus exactly one alias. Verified today: 10 of the 11 GICS sector
names on the S&P 500 Wikipedia table match an `US_SECTOR_ETFS` group string exactly; the only
mismatch is GICS `Information Technology` vs XLK's group `Technology`.

```python
SECTOR_ETF_BY_GROUP = {v["group"]: k for k, v in US_SECTOR_ETFS.items()}
SECTOR_ETF_BY_GROUP["Information Technology"] = "XLK"   # GICS name for XLK's "Technology"
```

#### Thresholds (the part that decides whether callouts are signal or noise)

Let `count[g]` = picks in group `g`, `share[g] = count[g] / len(picks)`, and
`base_share[g] = |{t in sector_of : sector_of[t] == g}| / len(sector_of)`, that is the group's
share of the **whole anchor universe**, not of the picks.

1. **`crowded`** fires when all 3 hold:
   - `count[g] >= 2`
   - `share[g] >= max(min_share, 2.5 * base_share[g])`, with `min_share = 0.30`
   - `etf_rank[g] <= lead_rank`

   Severity `high` when `share[g] >= 0.50`, else `medium`.

   Why each term. `count >= 2` blocks a single pick from firing at small `topN` (at `topN=3`
   one pick is already 33%). The `2.5 * base_share` term makes the test universe-relative, so a
   broad sector has to be *more* over-represented than a thin one before it counts. Measured on
   the current S&P 500 list (503 names), base shares by count are Industrials 16.1%,
   Financials 15.1%, Information Technology 14.7%, Health Care 11.7%, Consumer Discretionary
   9.3%, Consumer Staples 6.8%, Utilities 6.2%, Real Estate 6.2%, Materials 5.2%,
   Communication Services 4.6%, Energy 4.2%. The resulting per-sector triggers at `topN=10`:

   | sector | base | threshold | fires at |
   |---|---|---|---|
   | Energy | 4.2% | 30.0% (floor) | 3 of 10 |
   | Communication Services | 4.6% | 30.0% (floor) | 3 of 10 |
   | Information Technology | 14.7% | 36.8% | 4 of 10 |
   | Financials | 15.1% | 37.8% | 4 of 10 |
   | Industrials | 16.1% | 40.3% | 5 of 10 |

   That is the intended shape: 4 of 10 in Energy (9.5x its base rate) is news, 3 of 10 in Tech
   (2.0x) is not. The 0.30 floor stops a thin sector from firing on 2 picks out of 20. And a
   12-name custom watchlist that is half Energy has `base_share = 0.5`, so its threshold is
   1.25 and the callout correctly never fires: a concentrated list holding its own
   concentration is not news.
   `etf_rank <= lead_rank` (default 3 of 11, the top quartile of sectors) is what makes this a
   *crowding* statement rather than a diversification complaint. Picks concentrated in a
   *lagging* sector is a different signal and is deliberately not reported here.

2. **`missing_leader`** fires when `etf_rank[g] <= lead_rank` **and** `count[g] == 0` **and**
   `etf_return_pct > 0`. Severity `medium`. The positive-return condition matters: in a broad
   drawdown the "leading" sector can still be down, and "you own no Utilities and XLU is down
   only 3%" is not a trade idea.

3. **`etf_beats_names`** fires at most once per ETF board when the best-ranked ETF's window
   return is `>= median(pick_returns.values())`. Severity `low`. This is the callout that
   surfaces the trade class the S&P view cannot: if XLE at +8.1% beats the median top-10 pick
   at +6.4%, the basket is the cleaner expression with a fraction of the single-name blowup
   risk that produced the 2026-06 cohort. When `country_board` is given, the same rule is
   applied to it with `group` set to the country/region label, since a country ETF has no
   single-name mapping in the anchor universe and this is the only meaningful crosscheck for
   that board.

#### Emission rules

- Return `[]` when `len(picks) < 3`. Shares are meaningless below that.
- Skip `crowded` / `missing_leader` entirely when `sector_of` is empty: `base_share` divides by
  `len(sector_of)`, so it is a ZeroDivisionError otherwise, and a custom universe whose
  metadata is all blank is a realistic way to reach it. `etf_beats_names` does not read
  `sector_of` and still runs.
- Skip `etf_beats_names` on an empty board or empty `pick_returns`: it reads `sector_board[0]`
  (IndexError) and `statistics.median([])` (StatisticsError). Board `return_pct` values are
  always floats, since `top_performers_period` drops NaN returns before ranking, so there is
  no `None` case to filter here, only the empty one.
- At most 1 row per `group` (a group cannot be both crowded and missing).
- Sort by severity (`high`, `medium`, `low`), then `share` desc, then `etf_rank` asc, with
  defaults, since `etf_beats_names` rows carry no `share`:
  `key=lambda r: (SEV[r["severity"]], -r.get("share", 0.0), r.get("etf_rank", 99))`.
  Sorting on a bare `r["share"]` raises `KeyError` on exactly the row kind that is always
  present.
- Truncate to 5 rows. A 12-item callout list is wallpaper.
- Skip any group whose ETF is absent from `sector_board` (for example XLRE before its 2015-10
  inception on a long-window scan). No fabricated rank.

### `POST /api/scan`

```python
class ScanParams(BaseModel):
    universes: List[UniverseKey] = Field(
        default_factory=lambda: ["sp500", "us_sector_etfs", "global_etfs"])
    anchor: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    days: int = Field(30, ge=1, le=365)
    leadRank: int = Field(3, ge=1, le=11)
    runId: Optional[str] = None
```

`_scan(params)` mirrors `_top_performers` (which it deliberately does not touch):

1. Resolve every requested universe via `data.get_universe`. A failure is recorded as
   `boards[i]["error"]` and the scan continues. A Wikipedia hiccup on `sp400` must not kill the
   sector board.
2. **One** price pull for `sorted(set(union of all tickers))`, `start = today -
   max(days + 5, 375)` (identical to `_top_performers` line 319, so the trailing-return and
   sparkline columns populate and the parquet slice is shared with the leaderboard panel).
3. Per universe: `board_prices = prices[[t for t in u_tickers if t in prices.columns]]`, then
   the appropriate `top_performers_period` call from the conventions above. Rows are enriched
   with `name`/`sector` from that universe's metadata using the exact same `enrich` shape
   `_top_performers` builds (lines 355 to 378), extracted into a shared module-level
   `_enrich_row(row, rank, meta)`. The current closure captures `meta`, so the metadata dict
   becomes an explicit third argument. `_top_performers` is rewritten to call it (a pure move,
   identical output); leaving its private copy in place would defeat the anti-drift point.
4. `analysis.rotation_crosschecks(...)` with the anchor board, the anchor universe's full
   `sector_of` map, the sector board, and the country board.
5. Progress via the existing `PROGRESS` dict keyed on `runId`, popped in a `finally` exactly
   like `/api/backtest` and `/api/top-performers`.

Response:

```json
{"boards": [{"universe_key", "universe_label", "meta_label", "universe_size",
             "display_n", "board_n", "kind": "names" | "etfs",
             "results": [...], "window": {...}, "error": null}],
 "crosschecks": [...],
 "anchor": "sp500",
 "window": {"start_date", "end_date", "days_requested", "days_actual"},
 "lead_rank": 3, "elapsed_s": 0.0, "status_messages": []}
```

Two assembly details `_top_performers` does not have to handle, because it answers with a 400
instead:

- A board can come back with **0 results** (every ticker unresolvable, or a window too short
  for `top_performers_period`'s start-bar rule). `results[0]["start_date"]` then raises
  `IndexError`, so an empty board sets `window: null` and `error: "no data in this window"`
  rather than being indexed. The top-level `window` is taken from the anchor board when it has
  results, else the first board that does, else `null`. Every board is sliced from the one
  union frame, so all non-empty boards share the same `start_date`/`end_date` by construction.
- Every float in `results` is already JSON-safe (`top_performers_period` returns `None`, never
  NaN, for short history), but the crosscheck rows are new floats: `share`, `base_share`,
  `etf_return_pct`. Starlette serializes with `allow_nan=False`, which has already produced a
  500 in this repo, so any of these that can go NaN (a `0/0` share on an empty group) must be
  computed only inside the guards above, not coerced afterwards.

`/api/universes` gains `"features": {"scan": true}` so the client can hide the panel on an
older backend. `additional-universes` and `point-in-time-membership` add their own keys to
the same `features` dict, so add to it rather than assigning over it.

### Frontend

- **`data-loader.js`:** `runScan(params, onProgress)`, a copy of `runBacktest`'s runId +
  `pollProgress` wrapper pointed at `/api/scan`, added to the `Object.assign(window, {...})`.
- **`charts.jsx`:** new `ScanBoard` component, registered in the end-of-file
  `Object.assign(window, {...})` (a component not listed there is invisible to v1/v2). It owns
  its own `topN` / `days` / `leadRank` / universe-checkbox state, exactly like
  `TopPerformersPanel` owns its own controls, and renders:
  - a **Rotation** callout stack at the top: one row per crosscheck, severity-tinted left
    border (`high` #b91c1c, `medium` #92400e, `low` `theme.muted`), the `text` string, and the
    contributing tickers as chips. Empty state: "No rotation flags at these thresholds."
  - a responsive grid of compact boards (`useViewport`, 1 column under 900px, otherwise
    `repeat(auto-fit, minmax(260px, 1fr))`), each with `universe_label`, `#rank TICKER
    +x.x%`, the `leaderboardBadge` already used by `TopPerformersPanel`, and a
    `streak`/`Δ 7d` micro-column. Boards with `error` render the message in place, not a
    blank card.
  - a universe checkbox row built from `Object.keys(universes.universes)` (the
    `/api/universes` payload the panel already receives), **not** a hardcoded list of 6 keys.
    This is what makes the `additional-universes` dependency genuinely optional: on a backend
    where `UniverseKey` is still the 3-value Literal, a hardcoded `nasdaq100` checkbox would
    post a value Pydantic rejects with a 422 the panel has no story for. `sp500` +
    `us_sector_etfs` + `global_etfs` are checked by default, anything else the endpoint
    advertises is unchecked, and `sp400` carries an inline "+400 tickers on a cold cache"
    note.
  - a single Scan button. **No auto-run**, unlike `TopPerformersPanel`'s warm-cache
    auto-load, because an unchecked-by-default `sp400` box is one click away from a 400-ticker
    download.
- **`v2.jsx`:** `<ScanBoard ... anchorUniverse={params.universe || 'sp500'} />` inserted
  directly below `<TopPerformersPanel>` (after line 364), so rotation context sits next to the
  single-name leaderboard it is commenting on.
- **`v1.jsx`:** same insertion after line 329. Note that V1's `TopPerformersPanel` is currently
  called without a `universe` prop; `additional-universes` fixes that, and until it lands
  `ScanBoard` should take `anchorUniverse` from `params.universe` so the scan is at least
  self-consistent.
- **`index.html`:** bump every asset `?v=`.

### Invariants

- **I1:** every board comes from `analysis.top_performers_period`. No new or forked ranking
  path, and a scan board matches the leaderboard panel at matching `topN`/`days`.
- **I2:** additive only. New endpoint, new component, no change to `/api/backtest`,
  `/api/top-performers`, `DEFAULT_PARAMS`, or any analysis function's default behavior. The
  `enrich` extraction is a pure move with identical output.
- **I3:** ETF ranks always print their denominator (`#2 of 11`), never a bare `#2` that could
  read as "2nd of 500". `base_share` ships in the payload so the user can see what the
  threshold compared against. Boards that cannot be computed report `error`, never an empty
  list that looks like "nothing qualified". `etf_beats_names` compares against the **median**
  pick, not the mean, so one blown-up pick cannot manufacture the callout.
- **I4:** no build step, no new dependency, no execution. Callouts are text, the boards are
  lists.
- **I5:** the scan introduces **no new class of network call**. It needs the same per-universe
  Wikipedia constituent scrape (24h disk cache) and the same single `yf.download` delta as a
  normal run. Costs measured against the current universes: with a warm `sp500` cache the
  default 3-universe scan fetches 48 new columns once (the 37 global + 11 sector ETFs are
  disjoint from the S&P 500) and every later scan is a pure parquet slice; adding `nasdaq100`
  costs 15 new tickers (88 of its 103 are already S&P 500 members); `sp400` costs 400 and is
  the only expensive box. One correction to the sizing: a first fetch of missing tickers runs
  over `min(requested_start, cached_start)` to `max(requested_end, cached_end)`
  (`cache.get_prices_cached` branch 1), so those 48 or 400 new columns are pulled across the
  **whole span the parquet already holds**, not the scan's 375-day window. Against a 25-year
  parquet the sp400 box is a 400-ticker, 25-year download. On an airgapped site with a warm parquet and warm ticker caches the
  scan is fully offline; when one universe's ticker cache is cold and unreachable, that board
  reports its error and the rest of the scan still renders.

## Testing

`rotation_crosschecks` is pure, so every threshold is directly assertable. Build
`sector_board` as a list of `{"ticker": "XLE", "return_pct": 0.081}` dicts (the function only
reads `ticker` and `return_pct`, so the fixtures stay small).

- 4 of 10 picks in Energy, `base_share=0.042`, XLE at rank 2: exactly 1 row, `kind="crowded"`,
  `severity="medium"`, `share == 0.4`, `text` contains `"4 of 10"` and `"#2 of 11"`.
- Same shape with 5 of 10: `severity == "high"`.
- Floor boundary for a thin sector: 3 of 10 Energy (`share == 0.30`) fires; 2 of 10
  (`share == 0.20`) does not.
- Relative term binds: 3 of 10 picks in Information Technology with `base_share = 0.147`
  (threshold 0.368) and XLK at rank 1 gives **no** row, while 4 of 10 does. Same frame with
  Industrials at `base_share = 0.161` (threshold 0.403): 4 of 10 gives no row, 5 of 10 does.
- 1 of 3 picks in Energy with XLE at rank 1: no row (`count < 2`).
- A 12-name anchor where Energy is 6 of 12 universe names and 6 of 10 picks
  (`base_share = 0.5`, threshold 1.25): no row.
- 4 of 10 in Energy but XLE at rank 7: no row (`etf_rank > lead_rank`).
- XLI at rank 3 with `return_pct = 0.05` and 0 Industrials picks: exactly 1
  `missing_leader`. Flip the return to `-0.02`: no row.
- Best ETF `+0.081` vs pick returns whose median is `0.064`: exactly 1 `etf_beats_names`.
  Raise the median above `0.081`: none.
- `picks = ["A", "B"]`: returns `[]`.
- 8 qualifying groups: exactly 5 rows returned, with all `high` rows before any `medium`.
- A group whose ETF is missing from `sector_board`: skipped, no `KeyError`, no rank invented.
- ETF board convention, reusing the fixture and assertion style of the existing
  `test_top_performers_watchlist_and_new_entrants`: on an 11-column `_daily_prices` frame,
  `top_performers_period(p, top_n=3, days=30, watch_n=8)` returns 11 rows whose first 3 are
  element-wise equal to `top_performers_period(p, top_n=3, days=30)` **on every key except
  `new_entrant`**, so the scan's ETF board and its leadership cutoff come from one ranking
  pass. Assert separately that the `watch_n=8` rows are all `new_entrant is False` (the
  `ref_top_n` collapse above): that is the reason the badge is suppressed on ETF boards.
- Single-name board convention: on the same fixture,
  `top_performers_period(p, top_n=5, days=30, watch_n=min(10, 11 - 5))[:5]` is element-wise
  equal to the first 5 rows `_top_performers` produces for `topN=5` (same `watch_n`
  expression, `analysis.py:417` reference set unchanged), pinning I1 at the function level.
  Do not write this assertion with `watch_n=0`: it fails on `new_entrant` for any
  `topN` where `min(10, size - topN) > topN`.

## Non-goals

- No backtest, event study, Sharpe or equity curve per universe. The scan is a
  trailing-return snapshot plus crosschecks; a full multi-universe backtest would be N times
  the existing runtime and is what the sidebar universe select is already for.
- No blended or cross-universe portfolio construction, and no "buy XLE instead" suggestion.
  The callouts state facts about ranks and shares; the trade decision stays with the user (I4).
- No new ETF universes (single-country sector, factor, commodity, bond). The 2 existing
  baskets are what is wired.
- No persistence of scan results or callout history between sessions.
- No crowding measured by index *weight*. `base_share` is a name count, because the tool has no
  market-cap or weight data and inventing one would violate I3.
- No lagging-sector callout ("your picks are concentrated in the worst sector"). Plausibly
  useful, deliberately out of scope until the 3 kinds above prove non-noisy.

## Risks / open questions

- **Anchor selection when `sp500` is unchecked.** Chosen: `anchor` is an explicit param
  defaulting to `sp500`, and crosschecks are skipped entirely when the anchor board is missing
  or errored. Alternative: auto-pick the first single-name universe in `universes`. Worth
  confirming which the user expects when they scan only the 2 ETF universes.
- **`leadRank = 3` of 11 is a judgement call.** It makes "leading" mean the top quartile of
  sectors. 2 would be stricter and quieter, 4 would fire on roughly a third of sectors and
  risk making `missing_leader` constant background noise. Exposed as a param so it can be
  tuned, but the default needs the user's eye on live output for a week.
- **The `crowded` and `etf_beats_names` callouts will sometimes disagree with intuition in a
  correlated selloff**, where every sector is down and the "leading" sector is merely least
  bad. The `etf_return_pct > 0` guard covers `missing_leader` but not `crowded`. Adding the
  same guard to `crowded` would silence it in exactly the drawdowns where crowding hurts most,
  which is why it is not there. Open to reversal after live observation.
- **The `count >= 2` floor does not actually quiet small `topN`.** The rationale given for it
  is that at `topN=3` a single pick is already 33%, but 2 of 3 is 67%, which clears every
  sector's threshold including the 0.403 Industrials one. So at `topN` 3 or 4 the `crowded`
  flag fires on almost any 2-pick overlap with a leading sector, which is close to always.
  Either scale the floor (`count >= max(2, round(0.25 * topN))`) or accept that the callout is
  only meaningful at `topN >= 8`. Needs the same week of live output as `leadRank`.
- **`sector_of` for `nasdaq100` is ICB Industry, not GICS** (see `additional-universes`), so
  `SECTOR_ETF_BY_GROUP` will miss on ICB labels like `Basic Materials` and `Technology`.
  Result: crosschecks silently produce fewer rows for that anchor rather than wrong ones.
  Adding ICB aliases is 5 dict entries if the user wants it.
- **No auto-run** means the panel is empty until clicked, which reads as "broken" to a first
  time viewer. Mitigation is explicit empty-state copy naming the cold-cache cost. If the user
  prefers auto-run, gate it on `cacheInfo` being warm **and** only the 3 default universes
  being checked.
- **`/api/scan` has no tests**, consistent with `server.py` having none today. The crosscheck
  logic is pushed into `lib/analysis.py` precisely so the interesting part is covered; the
  endpoint's own assembly (union pull, per-board slice, error capture) stays unverified until
  the repo grows endpoint tests.
