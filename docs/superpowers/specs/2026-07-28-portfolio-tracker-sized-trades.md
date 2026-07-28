# Portfolio tracker with sized trades

**Date:** 2026-07-28
**Status:** Proposed
**Effort:** M
**Depends on:** oos-paper-trading-ledger (shares `cache.load_prices`, `cache.atomic_write`,
the `userdata/` convention, `analysis.ledger_marks` and `ledger.params_hash`; either spec can
land first, the second reuses what the first added)

## Problem

The rebalance panel hands the user a trade list that is not actionable without a spreadsheet.
The 2026-07-28 live row reads 10 in, 10 out, 0 kept: a complete 100% turnover instruction with
no quantities attached. `RebalancePanel.downloadCsv` (`web/charts.jsx:908`) exports columns
`month, action, ticker, group, fwd_return_pct, fwd_status`, so the answer to "how much" is
absent from the UI and from the export.

Three gaps follow from that:

- **Unequal weights.** The strategy is equal-weight by construction (`momentum_backtest`
  averages `monthly_returns.iloc[t][picks]`, `current_target` returns a bare ticker list). A
  real book is not equal-weight: after the 2026-06 cohort lost 27.2% on GLW -43.9%,
  SNDK -43.8% and MRVL -36.5% while other legs held, the surviving names were carrying far
  more than 1/10 of the book. A KEEP therefore usually still needs a trade.
- **No drift visibility.** Nothing tells the user that a name flagged KEEP is 40% overweight
  its target slice, which is precisely the position that turned a -14.2% month into a worse
  one.
- **No arithmetic.** Converting "BUY LLY" into "BUY 5 shares" needs the last close, the
  portfolio value and the target count, all of which the server already has in
  `cache/prices.parquet` and `analysis.current_target`, and none of which the tool combines.

## Goal

After this ships, the user pastes their actual holdings (ticker, shares, optional cost basis)
plus optional cash, and the server prices the book from the parquet cache, diffs it against
`current_target`'s equal-weight allocation, and returns a sized, ordered trade list
("SELL 12 sh NVDA", "BUY ~$4,300 LLY / 5 sh") with per-position weight, target weight and
drift. Optionally the paste is saved as a local snapshot, after which the panel shows what the
actual book did since that date next to the strategy over the same dates and the benchmark. The
output is arithmetic against a strategy the user chose: still a list, still no execution.

## Design

### Request and validation

`POST /api/portfolio/diff`, new models in `server.py`:

```python
class Holding(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    shares: float = Field(..., gt=0)
    costBasis: Optional[float] = Field(None, gt=0)      # per share

    @field_validator("ticker")
    def _norm(cls, v): ...    # strip(), upper(), "." -> "-"; must match ^[A-Z][A-Z0-9\-]{0,9}$

class PortfolioDiffParams(BaseModel):
    universe: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    rankLookback: int = Field(1, ge=1, le=12)
    skipMonths: int = Field(0, ge=0, le=2)
    benchmark: str = "SPY"
    holdings: List[Holding] = Field(default_factory=list, max_length=200)
    cash: float = Field(0.0, ge=0)
    portfolioValue: Optional[float] = Field(None, gt=0)   # override; default priced + cash
    reserveCash: float = Field(0.0, ge=0)
    wholeShares: bool = True
    driftTolerance: float = Field(0.10, ge=0.0, le=1.0)   # relative
    minTradeUsd: float = Field(50.0, ge=0.0)
    exitOutsideUniverse: bool = False
    persist: bool = False
    baselineId: Optional[str] = None
    runId: Optional[str] = None
```

The `.` to `-` normalization is not cosmetic: `data.get_sp500_tickers()` does
`t.replace(".", "-")` so the cache stores `BRK-B`, while every broker statement writes `BRK.B`.
Accepting the dot form and then failing to match a cache column would send the single most
commonly pasted edge case straight to `unpriced / not_in_cache` and understate the book. Normalize
on the way in and echo the normalized ticker back in `positions` so the user can see what was
matched. `parseHoldings` applies the same rule client-side so the parse feedback agrees with the
server.

Broker CSV parsing stays client-side: `parseHoldings(text)` in `web/charts.jsx` accepts
comma, tab or whitespace-separated `TICKER shares [cost_basis]`, tolerates a header row,
strips `$` and thousands separators, maps common broker headers (`Symbol`, `Quantity`,
`Cost Basis`), and reports unparsed lines back to the user before any request is sent. The
server contract stays strict JSON, so server-side broker-format sniffing (an endless
rabbit hole) never enters the codebase.

### Pricing the book

Prices come from `cache.load_prices(tickers, start, end)` (added by the ledger spec: pure
disk, no fetch, `None` on an empty or unreadable cache). Measured cost on the live cache, 504
tickers by 1317 bars: 50 ms full read, 4 ms for a 12-column read. `as_of` is the last bar of
the sliced frame. A held ticker absent from the cache, or with no bar in the last 10 calendar
days, goes to `unpriced` with a reason (`not_in_cache`, `stale`), is excluded from
`holdings_value`, and raises a warning that the portfolio value is therefore understated.
Never priced at 0 (I3). `refresh` is deliberately not a field here: the diff must work
offline, and pulling arbitrary user-supplied symbols from Yahoo is both a network dependency
and a data-quality risk.

One subtlety inherited from `cache._slice`, which ends in `.dropna(axis=1, how="all")`: a
ticker that is in the parquet but has no bar inside the requested window comes back **absent**
from the frame, not all-NaN. So `not_in_cache` cannot be inferred from a missing column alone
without over-reporting; `_price_book` distinguishes the 2 reasons by reading the full-history
column set once (`cache.load_prices([t])` with no date bounds, or `cache.coverage()["n_tickers"]`
plus a bounded probe) and only calls a ticker `not_in_cache` when it is absent there too.

**The ranking frame is not the pricing frame, and conflating them breaks I1.** The book is
priced over a short recent window on user-supplied symbols; the target must be ranked on a
separate frame that is (a) narrowed to `universe_tickers`, (b) has the benchmark column
dropped, and (c) spans at least `round(rankLookback * 30.4375) + round(skipMonths * 30.4375)`
calendar days ending at its last bar. All 3 matter because the parquet is a single shared file
holding every universe and every benchmark ever requested (504 S&P columns plus 37 global ETFs
plus 11 sector ETFs plus SPY, all in one table), and because `top_performers_period` sets
`start_ts = prices.loc[prices.index >= end_ts - days].index[0]` with **no reach-back
tolerance**. A frame that is too short therefore ranks on a silently collapsed window and
returns a different top-N set from the leaderboard, with no error and no flag. When the cache
is too short for the requested signal, return the "run a backtest first" error rather than a
sized trade list built on a wrong cohort. `_run_backtest` already establishes the correct
pattern: `constituents = prices.drop(columns=[params.benchmark])` before any ranking call.

New helper in `lib/analysis.py`:

```python
def _price_book(prices: pd.DataFrame, holdings: list[dict],
                stale_days: int = 10) -> tuple[list[dict], list[dict], pd.Timestamp | None]
```

returning `(priced_rows, unpriced_rows, as_of)`, where each priced row carries
`{ticker, shares, price, value, cost_basis, unrealized_pct}` and `unrealized_pct` is `None`
without a cost basis.

### The diff and the sizing math

```python
def portfolio_diff(prices: pd.DataFrame, target: dict, holdings: list[dict],
                   universe_tickers: list[str],
                   cash: float = 0.0, portfolio_value: float | None = None,
                   reserve_cash: float = 0.0, whole_shares: bool = True,
                   drift_tolerance: float = 0.10, min_trade_usd: float = 50.0,
                   exit_outside_universe: bool = False) -> dict
```

`target` is `analysis.current_target(...)` verbatim. No new ranking path exists anywhere in
this feature (I1).

Let `N = len(target["tickers"])` (equal to `topN` unless the universe ran short),
`V = portfolio_value or (holdings_value + cash)`, allocatable `A = max(V - reserve_cash, 0)`,
per-name target dollars `T = A / N`, and target weight `w* = T / V`.

Per position:

- `weight = value / V`
- `drift_pp = (weight - w*) * 100` (percentage points of the book)
- `drift_pct = weight / w* - 1` (relative, `None` for a name not in the target)
- `delta_value = T - value` for target names, `-value` for non-target names

Every one of those 3 divisions has a reachable zero denominator, and Starlette renders
responses with `json.dumps(..., allow_nan=False)`, so an `inf` or a `nan` reaching the payload
is not a bad number on screen, it is an HTTP 500. This repo has already been bitten by that,
which is why `server._safe_floats` exists. `_safe_floats` is the wrong fix here (mapping a
weight to 0.0 is a fabricated number, I3), so the guards are explicit:

- **`V == 0`** (no holdings priced, no cash, no override: an empty paste, or a paste where every
  symbol was unpriceable). Return HTTP 400 "nothing to size: no priced holdings and no cash"
  before computing anything. Do not emit a positions table of `weight: null`.
- **`N == 0`** (`current_target` returned `None`, or the cache is too short for the signal per
  the section above). Return the same "run a backtest first" error. `T = A / N` is otherwise a
  `ZeroDivisionError` and a 500 with a stack trace.
- **`w* == 0`**, which happens whenever `reserveCash >= V` so `A == 0`. `drift_pct` is then
  `weight / 0`. Report `drift_pct: None` (relative drift against a zero target is undefined,
  not infinite), keep `drift_pp` (which is well defined and equals `weight * 100`), emit every
  target name as `HOLD` with `reason: "reserve cash leaves nothing to allocate"`, and warn. A
  reserve larger than the book is a user typo, not a rebalance instruction.
- **`price == 0` or a NaN price** for a priced row: cannot happen if `_price_book` did its job,
  but assert it, because `floor(abs(delta_value) / price)` is the share count the user would type
  into a broker.
- As in the ledger spec, the endpoint asserts the assembled dict is `json.dumps`-able with
  `allow_nan=False` before returning, so a missed case fails in a test instead of in the browser.

Actions:

| condition | action |
|---|---|
| in target, not held | `BUY` |
| in target, `abs(drift_pct) <= drift_tolerance` or `abs(delta_value) < min_trade_usd` | `HOLD` |
| in target, underweight beyond tolerance | `ADD` |
| in target, overweight beyond tolerance | `TRIM` |
| held, not in target, in universe | `SELL` |
| held, not in target, not in universe | `REVIEW` (or `SELL` when `exitOutsideUniverse`) |

Share rounding. With `whole_shares=True`, a trade's share count is
`floor(abs(delta_value) / price)` in the trade's direction, so a BUY or ADD never exceeds its
dollar budget and a TRIM never sells more than the drift requires. The one exception is a full
exit: a `SELL` uses the exact held share count (which may be fractional if the user holds
fractional shares), never a floored one, because leaving 0.4 shares behind is not a rebalance.
With `whole_shares=False`, share counts stay floats and the residual is 0 by construction.
Unspent dollars are reported as `residual_cash` and `residual_pct`, so whole-share rounding is
visible rather than absorbed. Any position whose floored share count is 0 becomes `HOLD` with
`reason: "below 1 share"`.

Cash. `cash` is part of `V` by default, so the strategy sizes into it; `reserveCash` holds a
fixed amount out of the allocation. `totals.invested_pct = (V - reserve_cash) / V` is always
reported, because the backtest is fully invested and any cash the user holds is a permanent
gap between their result and the strategy line.

Self-financing order. Trades are emitted `SELL`, then `TRIM`, then `ADD`, then `BUY`, each
carrying a `sequence` index, and the response includes a cumulative-cash walk
`cash_after_sequence`. If `sum(buys) - sum(sells) > cash + 1e-6` (the usual cause is a
`portfolioValue` override larger than the actual assets), a warning names the shortfall
instead of silently emitting unfundable orders.

Out-of-universe holdings default to `REVIEW`, not `SELL`. The strategy has no opinion on a
name it never ranks, and auto-recommending the liquidation of, say, a bond fund or an
employer holding would be the tool overreaching from arithmetic into advice. The warning
reports their count and total weight so the drift numbers are read in context (a book that is
40% out-of-universe is not tracking this strategy at all).

### Response

```jsonc
{
  "as_of": "2026-07-27",
  "target": { ... analysis.current_target(...) verbatim ... },
  "positions": [{"ticker", "name", "sector", "shares", "price", "value", "weight",
                 "target_weight", "target_value", "drift_pp", "drift_pct",
                 "cost_basis", "unrealized_pct", "in_universe", "in_target",
                 "action", "delta_shares", "delta_value", "reason"}],
  "unpriced": [{"ticker", "shares", "reason"}],
  "trades": [{"sequence", "action", "ticker", "shares", "notional",
              "cash_after_sequence", "label": "BUY 5 sh LLY (~$4,300)"}],
  "totals": {"holdings_value", "cash", "portfolio_value", "reserve_cash", "invested_pct",
             "n_positions", "n_in_target", "n_outside_universe", "outside_universe_weight",
             "residual_cash", "residual_pct", "turnover_pct"},
  "since": null | {"baseline_id", "baseline_as_of", "days",
                   "book_return", "strategy_return", "benchmark_return",
                   "book_equity": {"dates", "values"}, "note"},
  "snapshots": [{"id", "saved_at", "as_of", "portfolio_value", "n_positions"}],
  "warnings": [...],
  "disclaimer": "Arithmetic against a strategy you selected. Not investment advice. No orders are placed and this tool has no broker connection."
}
```

`turnover_pct = sum(abs(delta_value)) / (2 * V)` (one-way turnover), which is the number that
makes the 10-in / 10-out live row's real cost obvious.

### Persistence: opt-in, user data, clear-safe

Holdings are personal financial data, so the default is that nothing is written: with
`persist: false` the diff is stateless and the paste exists only in the request body and in
React state. No `localStorage`, no `sessionStorage` (a draft cached there would sit in
plaintext in the browser profile without the user ever asking for it), and holdings are never
passed to `on_status` or into `PROGRESS`, which the UI polls.

With `persist: true` the snapshot is appended to `userdata/portfolio.json`, the same directory
and mechanism the ledger uses: outside `CACHE_DIR`, so `cache.clear()` (which unlinks 4 named
files under `cache/`) cannot touch it now or after any future rewrite; written via
`cache.atomic_write`; a corrupt file is moved aside rather than overwritten; `userdata/` is
gitignored. `lib/portfolio.py` holds the I/O (`load_snapshots()`, `save_snapshot(entry)`), and
a snapshot is:

```jsonc
{"id": "2026-07-28T14:11:02Z-8ac1", "saved_at": "...", "as_of": "2026-07-27",
 "holdings": [{"ticker", "shares", "costBasis"}], "cash": 2500.0,
 "portfolio_value": 118420.55, "prices_at_snapshot": {"LLY": 812.44, ...},
 "params": {"universe", "topN", "rankLookback", "skipMonths"}, "benchmark": "SPY",
 "target_tickers": [...], "note": null}
```

### Performance since paste

With `baselineId` set (or defaulting to the newest snapshot whose `params` match the request),
the `since` block reports 3 numbers over the same date range, from the same cached bars:

- **Book:** buy-and-hold of the pasted positions, no assumed trades, via a new pure helper
  `analysis.book_value_series(prices, holdings, cash=0.0, start_ts=None) -> pd.Series`
  computing `sum_i(shares_i * P(t, i)) + cash` per bar (tickers with no bar at `t` frozen at
  their last close, matching the ledger's freeze rule). `book_return = last / first - 1`.
- **Strategy:** `analysis.ledger_marks([synthetic_entry], prices, benchmark)` called with a
  single entry built from the snapshot's `target_tickers` and `prices_at_snapshot`, so
  "strategy since paste" uses the identical mark-to-market math as the ledger panel rather
  than a second implementation. `ledger_marks` asserts a single `params_hash` and reads a fixed
  key set, so the synthetic entry must carry all of `{params_hash, as_of, tickers, entry_prices,
  benchmark}` (hash computed from the snapshot's own `params` via `ledger.params_hash`, `as_of`
  from `snapshot["as_of"]`, `entry_prices` from `prices_at_snapshot`). `strategy_return` is
  `summary["total_return"]`; `summary["annualized"]` will be `None` under the 12-segment gate and
  is not used here. A snapshot whose `as_of` predates the current cache comes back
  `basis_unverified`, and that flag propagates into the `since` block rather than being dropped.
- **Benchmark:** `P(bench, last) / P(bench, first) - 1`, `None` when the benchmark column is not
  in the cache. Never 0.0.

The block carries an explicit note that the book return diverges from the strategy return
whenever the user did not execute the trades, which is the honest reading of the gap and the
main reason to look at it.

### UI

- `web/data-loader.js`: `postPortfolioDiff(body)` added to the existing
  `Object.assign(window, {...})`.
- `web/charts.jsx`: new `PortfolioPanel({ theme, accent, headingFont, params, benchmark })`,
  self-contained like `TopPerformersPanel`. Contents: a monospace textarea with placeholder
  `LLY 5 812.44`, parse feedback ("14 holdings parsed, 1 line skipped: 'Total'"), numeric
  fields for cash, portfolio value override, reserve cash, drift tolerance and minimum trade
  (all via the existing `NumField` with new `PARAM_HELP` entries). `NumField` renders a bare
  `<input type="number">` with no `step` prop, so the browser defaults to `step=1` and treats a
  fractional value as invalid on the spinner: the drift-tolerance field is therefore labelled
  and edited as **whole percent, 0 to 50**, and the panel divides by 100 before sending
  `driftTolerance`. That keeps `NumField` untouched, which matters for I2, and it reads better
  anyway ("10%" not "0.1"). Also a whole-shares toggle, a
  "Save snapshot" checkbox that maps to `persist` with the plaintext-file caveat inline, and a
  Compute button. Output: the sized trade list first (sequence-ordered, BUY green / SELL red
  reusing `REBAL_COLORS`, dollar and share columns), then a positions table with
  weight / target / drift columns sortable via the same `SortTh` pattern as
  `TopPerformersPanel` (`SortTh` is declared inside that component's render body at
  `charts.jsx:1193`, so it is a pattern to copy, not a symbol to import), then totals and
  warnings, then the `since` strip when a baseline exists. A "Copy trades" button and a CSV export (`sequence, action, ticker, shares,
  notional`) so the list can leave the tool as text, which is the only export path it gets.
  The disclaimer line renders above the trade list, not buried at the bottom. Register
  `PortfolioPanel` in the end-of-file `Object.assign(window, {...})`.
- `web/v1.jsx` and `web/v2.jsx`: render `<PortfolioPanel />` directly below the ledger panel
  (below `RebalancePanel` at `v1.jsx:609` / `v2.jsx:739`), passing the last-run params so the
  target it diffs against matches the displayed rebalance row.
- `web/index.html`: bump the `?v=` query on all 4 asset tags.

### Invariants

- **I1**: the target set comes from `analysis.current_target` only, so the sized list is the
  same cohort the live rebalance row and the Top Performers leaderboard agree on. No sizing
  logic touches ranking.
- **I2**: a new endpoint plus a new panel. `/api/backtest`, `/api/top-performers`,
  `/api/ledger` and every existing response key are unchanged; `RebalancePanel` is not
  modified by this spec.
- **I3**: unpriced holdings are listed, not valued at 0; frozen legs are flagged; whole-share
  residual cash is reported instead of hidden; drift is shown for every position including
  those that produce no trade, so nothing is filtered out of view.
- **I4**: no broker connectivity, no order routing, no API keys, no execution of any kind.
  Output is a table, a clipboard string and a CSV. Stated as an explicit non-goal below.
- **I5**: pricing is disk-only (`cache.load_prices`), so no user-supplied symbol is ever sent to
  Yahoo. On an empty cache the endpoint returns "price cache is empty: run a backtest first",
  never a surprise download. But the endpoint is **not** free of network dependencies, and it is
  worth being exact rather than reassuring: it needs `universe_tickers` for the `in_universe`
  flag and the target, and it needs per-ticker `name` / `sector` for the positions table, both of
  which come from `data.get_universe(universe)`. For `sp500` that is a Wikipedia scrape behind a
  24 hour TTL (`cache.load_tickers()` and `cache.load_metadata()` both return `None` once
  `TICKERS_TTL_HOURS` has passed), so on an airgapped box the panel 502s a day after the last
  successful scrape while a perfectly good price cache sits on disk. This endpoint takes the same
  fallback chain the ledger spec specifies (stale ticker/metadata cache, then the price cache's
  own columns, with the source reported in `warnings`), and `name` / `sector` degrade to empty
  strings rather than blocking the diff. The 3 curated ETF universes are hardcoded constants and
  need no network at all.

## Testing

Additions to `tests/test_analysis.py` (using its `_daily_prices` fixture at line 224 and the
`current_target` helpers already exercised there) plus a new `tests/test_portfolio.py` for the
snapshot I/O, with module paths monkeypatched to `tmp_path` per
`tests/test_cache_corrupt.py:22`:

- **Target identity (I1):** the set of `positions` marked `in_target` equals
  `current_target(prices, top_n=3, rank_lookback=1)["tickers"]`.
- **Already balanced:** holdings constructed as exactly `T / price` shares of each target name
  with `whole_shares=False` produce 0 trades, every action `HOLD`, and `abs(drift_pp) < 1e-9`.
- **Fresh buy sizing:** an all-cash book (`holdings=[]`, `cash=100_000`, no `portfolio_value`
  override) with `N=4` yields 4 BUYs, each `delta_value == 25_000`, and with `whole_shares=True`
  each `shares == floor(25_000 / price)` and `shares * price <= 25_000`. Use `cash`, not the
  `portfolio_value` override: with `holdings=[]`, `cash=0` and `portfolio_value=100_000` the
  spec's own shortfall rule (`sum(buys) - sum(sells) > cash + 1e-6`) also fires, so the override
  form asserts 2 things at once and belongs in the sequencing test below instead.
- **Full-exit exactness:** a holding of 12.4 shares of a non-target name yields
  `action == "SELL"`, `shares == 12.4` exactly (not floored), and
  `notional == 12.4 * price`.
- **Drift tolerance:** a target position at 1.05x its target weight is `HOLD` at
  `drift_tolerance=0.10` and `TRIM` at `0.01`, with `delta_value` closing the gap exactly.
- **Dust suppression:** a $10 gap with `min_trade_usd=50` is `HOLD` with the reason set, while
  its `drift_pp` is still reported.
- **Outside universe:** a holding absent from `universe_tickers` is `REVIEW` by default and
  appears in `warnings` with its weight; with `exit_outside_universe=True` it becomes `SELL`.
- **Unpriced:** a holding whose ticker has no column is in `unpriced` with
  `reason == "not_in_cache"`, contributes 0 to `holdings_value`, produces a warning, and does
  not appear in `positions`.
- **Cash and reserve:** `totals.portfolio_value == holdings_value + cash`; with
  `reserve_cash=10_000` on a $100,000 book and `N=4`, each `target_value == 22_500` and
  `invested_pct == 0.9`.
- **Sequencing:** `trades` are ordered SELL, TRIM, ADD, BUY; walking `cash_after_sequence`
  never goes below 0 when `cash + sells >= buys`; when a `portfolio_value` override makes the
  buys unfundable, the shortfall warning is present.
- **Turnover:** a full 10-out / 10-in swap on an equal-weight book reports
  `turnover_pct == 1.0`.
- **`book_value_series`:** on `_daily_prices([T0..T5], seed=1)` with 2 holdings plus $1,000
  cash, each value equals `shares_0 * P0(t) + shares_1 * P1(t) + 1000` within 1e-9, and
  `book_return` equals `last / first - 1`.
- **Freeze in the book series:** NaN out one holding's tail bars; the series continues using
  that name's last close rather than dropping to the other leg alone.
- **Input purity:** `portfolio_diff` mutates neither the `prices` frame nor the `holdings`
  list (compare with `pd.testing.assert_frame_equal` and a deep copy afterwards).
- **Degenerate denominators:** `V == 0` (empty holdings, no cash) raises the 400 rather than
  producing `weight: NaN`; `N == 0` (empty `target["tickers"]`) raises rather than
  `ZeroDivisionError`; `reserve_cash > V` gives `drift_pct is None` on every position, a
  well-defined `drift_pp`, all-`HOLD` actions and a warning, with no `inf` anywhere.
- **JSON-cleanliness:** every case above additionally asserts
  `json.dumps(result, allow_nan=False)` succeeds. This is the assertion that pins the Starlette
  500, and it is cheap enough to append to all of them.
- **Ticker normalization:** a holding pasted as `BRK.B` prices against the cache's `BRK-B`
  column and is not reported `not_in_cache`.
- **I1 under a short frame:** a `prices` frame spanning fewer days than the requested signal
  window must make the endpoint refuse, not silently rank on a collapsed window. Assert against
  `top_performers_period` on the same short frame to show the 2 top-N sets genuinely differ, so
  the test documents why the guard exists.
- **Benchmark exclusion:** a `prices` frame that still contains the benchmark column produces the
  same `in_target` set as one without it (the endpoint narrows to `universe_tickers` first).
- **Snapshot I/O:** `save_snapshot` then `load_snapshots` round-trips; `cache.clear()` leaves
  `userdata/portfolio.json` on disk; a corrupt file is moved aside and `load_snapshots()`
  returns `[]`; a failed write leaves the previous file byte-identical. The `cache.clear()` test
  must monkeypatch all 4 paths `clear()` unlinks, not the 3 that
  `test_cache_corrupt._point_cache_at` patches, or the test run deletes the developer's real
  `cache/tickers.json` and `cache/sp500_meta.json` (see the same note in the ledger spec).

Endpoint-level tests stay out of scope: there are no `server.py` tests in the repo today, and
adding a `TestClient` harness is its own change. Every rule above is asserted at the `lib`
level where the logic lives.

## Non-goals

- **No broker connectivity and no order execution, ever.** No brokerage API, no OAuth, no API
  keys, no FIX, no "send to broker" button, no order staging file in a broker's import format.
  The deliverable is a table, a clipboard string and a CSV that the user types into their own
  broker themselves. This is invariant I4 and is not negotiable by a later spec.
- **Not personalized investment advice.** The endpoint computes arithmetic (equal weight over
  a ticker list the user's own parameter choices produced) against a strategy the user
  selected. It does not assess suitability, risk tolerance, tax position, concentration limits
  or position sizing beyond `1/N`, and the panel says so above the trade list.
- No tax-lot accounting, wash-sale rules or realized-gain reporting. `costBasis` is used for a
  display-only `unrealized_pct` and nothing else.
- No multi-account or multi-currency support. Single book, USD, matching the price cache.
- No limit prices, order types, market-hours awareness or intraday quotes. Sizing uses the last
  cached daily adjusted close, and the response says so via `as_of`.
- No editing holdings server-side. Each diff is a fresh paste; snapshots are append-only.
- No automatic execution of the ledger's recorded targets. The 2 features share math and
  storage, not actions.

## Risks / open questions

- **Default `driftTolerance = 0.10` and `minTradeUsd = 50` are judgement calls.** 0 tolerance
  is the purest reading of an equal-weight strategy (the backtest rebalances to exactly `1/N`
  every month) but emits a trade on all 10 names every month, most of them dust. The user
  should confirm 10% / $50 or pick their own; drift is displayed for every position either
  way, so nothing is hidden by the choice.
- **Adjusted closes are not tradeable prices.** `_fetch_prices` keeps only `auto_adjust=True`
  adjusted Close, so a name that has paid dividends or split shows a historical adjusted price
  below its actual quote. Share counts derived from the *latest* bar are fine (the latest
  adjusted close equals the actual close on the current basis), but a snapshot's
  `prices_at_snapshot` can be rebased by a later split, which is why the `since` block marks
  through `ledger_marks` and inherits its `basis_shifted` flag. If the user wants sizing off
  live quotes rather than yesterday's close, that is a new fetcher and a new network
  dependency, which I5 makes a separate decision.
- **`portfolioValue` override semantics.** Treating it as total capital (rather than as a
  cross-check on the pasted book) lets the user size a $200,000 plan from a $50,000 paste. The
  shortfall warning catches it, but the alternative (reject the override when it exceeds
  priced value plus cash) may be the safer default. User's call.
- **Whole-share rounding drifts small books.** On a $10,000 book with a $900 stock, 1 share is
  9% of the portfolio and `1/N` sizing cannot be hit at all; `residual_pct` will be large and
  the tracking gap versus the strategy real. Worth a UI note when
  `max(price) / target_value > 0.5`; whether that becomes a hard warning is a judgement call.
- **Fractional-share default.** `wholeShares=True` is the universally executable choice, but
  most retail brokers now support fractional shares and the strategy is defined on exact equal
  weights. Flipping the default to `False` would track the strategy more closely at the cost
  of some brokers rejecting the order.
- **Persistence default.** `persist=False` means the "since paste" comparison only exists once
  the user opts in, and a user who never checks the box gets no track record of their own book.
  An alternative is defaulting to save with an easy delete, at the cost of writing financial
  data to disk without an explicit request. Deliberately left opt-in here.
- **`turnover_pct`'s denominator is ambiguous when the book holds cash.** `sum(abs(delta_value))
  / (2 * V)` with `V` including cash reports 50% for a first-time deployment of an all-cash
  account, because only 1 side of the sum is populated. That is arithmetically what one-way
  turnover means, but "50% turnover" is a strange thing to print next to "you have made no trades
  yet". Options: divide by `2 * holdings_value` instead (undefined on an empty book), suppress
  turnover entirely when `n_positions == 0`, or label the first diff as an initial deployment
  rather than a rebalance. Suppressing it on an empty book is the cheapest, but the choice of
  denominator on a partly-cash book is still a judgement call the user should make.
- **`reserveCash` on a fully invested book reads as a sell instruction.** `reserveCash=10_000`
  against a $100,000 book holding $100,000 of stock and $0 cash is arithmetically consistent
  (targets drop to `90_000 / N`, so every name TRIMs and the trades raise the reserve), but the
  user asked to "hold cash back", not to sell 10% of the book. The behaviour is correct and the
  trade list makes it visible, though a note when `reserveCash > cash` would stop it reading as a
  surprise.
- **Adjusted-close prices are not what the user's broker will fill at**, and the share counts are
  the most instruction-shaped output in the whole tool. `as_of` plus the stale/unpriced flags are
  the only thing standing between "yesterday's adjusted close" and a user typing a wrong quantity.
  Those flags are load-bearing UI, not decoration, and should be reviewed as such.
