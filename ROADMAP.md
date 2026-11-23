# Roadmap

**Written:** 2026-07-28
**Head:** `25b3fab` on `main`

## Context

### Shipped 2026-07-28 (the quick-wins batch)

5 specs landed, all Approved, all with tests:

1. [Skip-month momentum via a shared signal builder](docs/superpowers/specs/2026-07-28-skip-month-momentum-signal.md) - collapsed 5 duplicated inline ranking formulas into `momentum_signal(monthly_returns, rank_lookback, skip_months=0)`; added `skipMonths` (0-2) end to end.
2. [Time-aligned event-study alpha](docs/superpowers/specs/2026-07-28-time-aligned-event-study-alpha.md) - alpha per horizon is now the mean of same-month cohort-minus-benchmark spreads with the HAC t-stat on that spread; win rate is share of cohorts beating the benchmark.
3. [Per-pick cohort attribution](docs/superpowers/specs/2026-07-28-per-pick-cohort-attribution.md) - `pick_returns` on every cohort and completed rebalance row, rendered sorted by contribution.
4. [Leaderboard signal-quality columns](docs/superpowers/specs/2026-07-28-leaderboard-signal-quality.md) - vol, 52w-high proximity, max day move, 3m/6m/12m returns, sparkline, streak, all sortable.
5. [Bubbling-under watchlist](docs/superpowers/specs/2026-07-28-bubbling-under-watchlist.md) - ranks N+1..N+10, rank-velocity arrows, NEW badge.

Plus the earlier [current-target alignment design](docs/superpowers/specs/2026-07-05-current-target-run-date-design.md), which is where invariant I1 comes from.

### What this roadmap covers

The 11 specs written and adversarially verified on 2026-07-28, all currently **Status: Proposed**. Every one has a full design spec linked below by relative path; the verifier corrections are already folded into the spec files, so implement from the file and not from any summary of it.

Ordering is by real dependencies and decision-value, not by effort. Two constraints drove it:

- **Same-function collisions.** `momentum_signal`, `momentum_backtest`, `perf_stats`, `top_performers_period`, the `universes.py` label dicts, the per-universe cache JSON keying, `cache.load_prices` and the `bestStrat` selector in both dashboards are each rewritten by more than one spec. Those specs are in different batches on purpose.
- **Value ordering.** A parameter sweep over gross returns nominates the wrong cell. A cross-universe scan is thin with 3 universes. Sizing real dollars into a target is premature while the signal definition is still being decided.

### How much to trust the numbers in here

The figures quoted in the batch sections come from 3 different levels of confidence, so treat them accordingly:

- **Independently re-measured** against the cached 503-name frame (2021-04-28 to 2026-07-27) at the time of writing: the turnover table (1099% / 382% / 212% per year), the gross and net Sharpe pairs, and the regime filter's return and drawdown effect. Re-measuring corrected 3 claims the specs made, which are marked inline where they appear. Note the drag figures first written here (1.51% / 0.51% / 0.29%) charged one side of each trade; the shipped implementation charges both, which is the standard reading of bps per side, giving **2.98% / 1.04% / 0.61%**. The 5x asymmetry between hold1 and hold6 holds either way.
- **Verified against the real code** by the adversarial pass, which applied roughly 130 corrections across the 11 specs, including a wrong weight formula in the turnover spec and an invariant break in the ledger's record endpoint.
- **Taken on trust and flagged in the spec files:** anything requiring a live Wikipedia or Yahoo fetch (Nasdaq-100 and S&P 400 table layouts, the "Selected changes" row counts, the point-in-time membership counts, earnings-endpoint latency) and the vol-adjusted spec's per-ticker examples. Re-confirm these at implementation time rather than quoting them in UI copy.

Where a number drives a sequencing decision, the batch section says which level it came from.

---

## Batch 1 - Honest numbers, and start the out-of-sample clock

| | |
|---|---|
| **Specs** | [Turnover and transaction-cost drag](docs/superpowers/specs/2026-07-28-turnover-transaction-costs.md) (M), then [Out-of-sample paper-trading ledger](docs/superpowers/specs/2026-07-28-oos-paper-trading-ledger.md) (M) |
| **Effort** | M + M |
| **Deps** | None. Both are zero-dependency. |

**Why these go together.** Both attack the same credibility problem from opposite ends: every number on the dashboard is gross of costs and in-sample by construction. Neither touches the ranking signal, so the whole batch stays clear of the `momentum_signal` work in batches 3 and 4. They do share `perf_stats`, `RebalancePanel` and the v1/v2 dashboard bodies, so build turnover first: it owns the `perf_stats` signature and the `bestStrat` switch to net Sharpe that the ledger panel sits underneath.

**Why first.**

- Turnover is a hard dependency of the parameter sweep (batch 7), and not for the net columns: the sweep's entire optimisation is hoisting `momentum_signal` out of the cell loop, which needs the new `momentum_backtest_detail(..., signal=)` seam. Without it the sweep either runs 84s or forks the sleeve loop and breaks I1.
- Turnover is where the sleeve comparison stops being fair. Independently re-measured on the cached 503-name frame at 10 bps one-way: turnover is 1099%/yr at hold1, 382% at hold3, 212% at hold6, so the cost drag is 1.51%/yr, 0.51% and 0.29% respectively, a 5x penalty on the shortest hold. Note this **widens** hold6's existing lead rather than flipping the selection: hold6 already wins on gross Sharpe (1.33 vs 1.18), and it still wins net (1.32 vs 1.15). The CAGR ordering is the part that effectively ties (about 39.8 net for both). 4 later specs read the best-sleeve selection and one of them (regime) needs a server-side copy of the rule, so landing the net definition now means writing that rule once.
- The ledger is the only item whose value accrues with wall-clock time. Every day it is not shipped is a day of out-of-sample track record that does not exist. It is also the spec that lands `cache.atomic_write`, `cache.load_prices` and the `userdata/` convention that 2 later specs reuse.

**What you can newly do.** Read net-of-cost performance with an explicit cost-drag figure and a per-month turnover series. At the shipped default of 10 bps per side the **total-return** ordering inverts (hold1 +497% gross becomes +435% net, below hold6's +458%) while hold6 keeps the best Sharpe both gross and net, so the honest summary is that costs widen hold6's lead rather than changing which sleeve wins on risk-adjusted terms. Click Record on the live target to commit it to an append-only ledger, then watch a real out-of-sample equity curve against the benchmark, with annualized stats deliberately suppressed until 12 completed segments.

**Watch for.** The verifier found an I1 break in the ledger record endpoint (it handed the full shared parquet, benchmark column included, to `current_target`) and an I5 break in both specs (`data.get_universe()` scrapes Wikipedia past its 24h TTL, so the first Record click on an airgapped box would 502). Both are fixed in the spec text as explicit preconditions. The minimum-history refusal in `top_performers_period` is the single most important regression test in this batch.

---

## Batch 2 - Live risk context on the trade list

| | |
|---|---|
| **Specs** | [Regime filter and live momentum-crash banner](docs/superpowers/specs/2026-07-28-regime-filter-crash-banner.md) (M), then [Earnings-date flags](docs/superpowers/specs/2026-07-28-earnings-date-flags.md) (S) |
| **Effort** | M + S |
| **Deps** | Regime depends on per-pick cohort attribution (shipped) for the MTD partial-row plumbing, and in practice on batch 1's net `bestStrat` rule. Earnings depends on nothing. |

**Why these go together.** Both are read-only context layered onto the live rebalance row, both touch `RebalancePanel` and `RebalanceChip`, and neither goes near `momentum_signal`. Doing them in one pass means the trade-list header and chip rendering are opened once.

**Why here.**

- The banner answers the question the tool is living right now: the best-Sharpe sleeve is -14.2% month-to-date against SPY -1.2% and the 2026-06 cohort realized -27.2%, and there is no vocabulary for that anywhere in the UI. That value is available today and decays if deferred.
- The regime spec computes a best-Sharpe sleeve server-side for `REGIME.mtd` and `REGIME.conditional`, which must match the dashboards' client-side selector. Batch 1 moves that selector to net Sharpe and this batch moves it again to exclude filtered rows. Sequential, one batch apart, is the only safe ordering.
- Earnings is the batch's cheap win and the only new outbound network dependency in the roadmap, so it is better isolated in a small batch with its own I5 story (cache-first, split 24h/6h TTLs, `MOMENTUM_OFFLINE=1`, bounded 8s wall clock) than buried inside a large one.

**What you can newly do.** See a data-driven regime state on the header (RISK ON / RISK OFF / FACTOR UNWIND / NO REGIME DATA) with the benchmark's distance from its 200dma, days in state, realized-vol percentile, and a second line that is always conditional evidence with n and a HAC t-stat. Optionally run the strategy with a regime filter to cash or benchmark, as an explicitly labelled experiment. See which buy or keep names report earnings inside the hold window.

**Watch for.** The filter measurably destroys return on this window. Independently re-measured to cash: h6m +470%/Sharpe 1.33 becomes +193%/1.01, and h1m +497%/1.18 becomes +269%/0.96. So the UI must never present filtered as the free lunch. But it is a genuine risk trade, not a pure loss: max drawdown improves from -24.5% to -18.6% at h6m (the spec text saying drawdown is "no shallower" is wrong, corrected here). Present it as return-for-drawdown, off a small sample. Also note the risk-off month-end count is convention-sensitive: a daily 200dma sampled at month-end gives 22 such months on this frame, not the 13 the spec quotes, so pin the convention in code and report n in the banner. The mask ordering is `mask.shift(1).reindex(returns.index)`, not the reverse; the two forms agree on a gapless index, which is why the bug would have shipped.

One more thing the re-measurement surfaced: `SPY`'s last cached bar is NaN while the constituent columns carry 2026-07-27, so the benchmark genuinely lags the trade list. The banner's `as_of` must be computed from the benchmark column's own last valid index, not from `prices.index.max()`, or it will advertise a regime state it did not measure.

---

## Batch 2b - Evidence checks, and restart the ledger

Added 2026-11-18, after batches 1 and 2 shipped. Status: Approved, not built. Suggested position: before batch 3.

| | |
|---|---|
| **Specs** | [Ledger reminder and missed-month backfill](docs/superpowers/specs/2026-11-18-ledger-reminder-backfill.md) (S), then [Equal-weight universe baseline](docs/superpowers/specs/2026-11-18-equal-weight-universe-baseline.md) (S), [Beta, alpha and information ratio](docs/superpowers/specs/2026-11-18-beta-alpha-information-ratio.md) (S), [Rank-bucket returns](docs/superpowers/specs/2026-11-18-rank-bucket-returns.md) (S) |
| **Effort** | S + S + S + S |
| **Deps** | The ledger spec depends on the shipped ledger. The other 3 depend on nothing. |

**Why these go together.** All 4 answer one question: is the measured edge real? The ledger is the out-of-sample answer, and it stopped. The other 3 are in-sample checks the dashboard lacks. None of them edits `momentum_signal`, `momentum_backtest_detail` or `perf_stats`, so the batch stays clear of the signal work in batches 3 and 4. All 4 add response keys only.

**Why before batch 3.**

- The ledger has 1 entry (2026-07-27) and the price cache ends at 2026-08-21. 3 month-ends passed with no record. Under the spec, a gap is fillable only until the current month gets a live entry. So build the ledger spec before the next Record click, or record with the gap and accept it.
- The other 3 are small, and they change how every later result is read. Batch 3 will lower the headline numbers. A baseline, a beta and a bucket chart show which part of the fall is selection and which part was never selection.

**What you can newly do.** See on page open that a target is due. Fill missed month-ends with labeled reconstructed targets, with the live-only total beside the combined one. Compare each sleeve against the equal-weight average of its own universe. Read beta and a regression alpha in place of a CAGR subtraction. See average return across the whole ranking, with the top N as its own bar.

**Watch for.**

- No spec adds a `PERF` row. A new row is eligible for the best-sleeve pick in 3 places (`v1.jsx:349`, `v2.jsx:336`, `server.py:506`).
- 3 of the specs exclude the in-progress month with the same test (`constituents.index.max() < label`). Write it once.
- The regression must use the unfilled benchmark series. `bench_aligned` carries invented 0.0 months for the equity curve.
- Rank buckets become a 6th consumer of `momentum_signal`. Batches 3 and 4 must thread their new arguments through it. The baseline needs the batch 3 member mask, or it stops being comparable.
- All 4 specs bump `?v=`. Built in one pass, that is 1 bump.

**Provenance.** Every figure in these 4 specs was measured on 2026-11-18 against `cache/prices.parquet`. The frame held 497 priced S&P 500 columns from 2011-05-02 to 2026-08-21. The settings were top 10, 1-month rank and 10 bps per side, with the partial August 2026 month excluded. The measurement scripts are not in the repo. All figures use today's member list, so they are upper bounds until batch 3 ships. One claim is not measured: that a real equal-weight fund trailed SPY over the same period.

---

## Batch 3 - Point-in-time membership

| | |
|---|---|
| **Specs** | [Approximate point-in-time S&P 500 membership](docs/superpowers/specs/2026-07-28-point-in-time-membership.md) (L) |
| **Effort** | L |
| **Deps** | None. |

**Why alone.** This is the first of 2 specs that thread a new optional parameter through `momentum_signal` and all 5 ranking consumers. The other is batch 4. They must not be built in parallel, and this batch is the expression of that constraint.

**Why here.** It is the tool's headline methodological flaw and today it is handled with a sentence of prose. It also changes every historical number, so it belongs before the vol-adjusted experiment: comparing raw against vol-adjusted Sharpe is only worth doing once on point-in-time-correct, net-of-cost history, not 3 times as each honesty layer lands. Measured cost of the bias: of today's 503 names, 80 (16%) were not members at 2021-06, 149 were not at 2016-06, 217 were not at 2001-06.

**What you can newly do.** Turn on `pointInTime` for sp500 and get a backtest whose formation-month rankings only see names that were index members at the time, plus a `MEMBERSHIP` payload that reports the bias as numbers (`stripped_at_window_start`, `absent_historic_members`, `reliable_from`, `months_below_top_n`) instead of a caveat sentence.

**Watch for.** The mask goes on the **signal**, never on `monthly_returns`; masking returns would `.dropna()` away exactly the forward losses that removal implies. And it must use `.where`: at the default `rank_lookback=1` `momentum_signal` returns the caller's own frame, so an in-place mask would silently delete realized returns. The change table is "Selected changes": ~21 rows/yr from 2010, ~4/yr for 2000-2010, 10 rows before 2000, so the mask is close to worthless at the 25-year maximum lookback.

---

## Batch 4 - Volatility-adjusted signal family

| | |
|---|---|
| **Specs** | [Volatility-adjusted momentum signal](docs/superpowers/specs/2026-07-28-vol-adjusted-momentum-signal.md) (M) |
| **Effort** | M |
| **Deps** | Skip-month signal (shipped), current-target design (shipped). Serialized behind batch 3. |

**Why alone.** Second writer of `momentum_signal` plus the 5 consumer signatures, and sole writer of `top_performers_period`, `_pick_quality_stats` (factoring out `_ann_vol`), `current_target` and `live_rebalance`. Wide diff across 8 functions and 2 sidebars, with the I2 regression sweep as the only thing keeping the raw path honest. It gets its own isolated diff.

**Why here.** It is a risk-preference switch, not an improvement, and the decision needs a clean side-by-side: on the cached 2021-2026 window vol_adj lowers h6m Sharpe (1.22 vs 1.34) and total return (+167% vs +430%) while cutting vol by a third and max drawdown by 4.3pp. That comparison is only decision-grade after costs (batch 1) and the membership mask (batch 3) are in the numbers.

**What you can newly do.** Rank by trailing return divided by annualized vol instead of raw return, on both the backtest and the live leaderboard, with the family reported once at the top level and per-row `signal_value` / `vol_rank_ann`. Grounded example of what changes: SNDK moves from rank 8 to 211 (180% vol), and the 2026-06 cohort's July return goes from -27.2% to -11.1%.

**Watch for.** The family lives inside `top_performers_period`, which `current_target` already delegates to, so I1 holds structurally. But `return_pct` must stay the raw window return while the ratio goes in `signal_value`, or the live-target card and the leaderboard publish mutually inconsistent percentages for the same picks. `streak` / `prior_rank` / `new_entrant` are raw-return diagnostics and are suppressed to None under vol_adj, which needs the one-line Streak-cell fix (`null > 0` is false, so the cell would print "new" on every row).

---

## Batch 5 - More universes, and a scan across them

| | |
|---|---|
| **Specs** | [Additional universes: Nasdaq-100, S&P MidCap 400, custom list](docs/superpowers/specs/2026-07-28-additional-universes.md) (M), then [Cross-universe scan board](docs/superpowers/specs/2026-07-28-cross-universe-scan.md) (M) |
| **Effort** | M + M |
| **Deps** | The scan is far more valuable after the universes land, and both write `lib/universes.py` and the new `/api/universes` `features` dict. |

**Why these go together.** The scan is thin with 3 universes and rich with 6, and it picks up new keys for free once `UNIVERSE_KEYS` is the single source of truth. They also share `universes.py`, `server.py:universe_info` and the frontend universe tables that move out of `v2.jsx` into `charts.jsx`, so one pass avoids merging the same dicts twice. Strict order: universes first, because the scan's universe checkbox row must be built from the server's key list or it POSTs a value the `UniverseKey` Literal rejects with a 422.

**Why here.** Widening the idea surface is only worth doing once the signal that ranks it is settled (batches 3 and 4) - otherwise the new universes get re-evaluated under a changed ranking definition immediately. This batch also carries a real bug fix that is independent of everything: `v1.jsx:328` renders `TopPerformersPanel` with no `universe` prop, so a V2 global-ETF run leaves V1 showing an S&P 500 leaderboard above a global-ETF rebalance panel that claims a matching signal.

**What you can newly do.** Run the whole tool over Nasdaq-100, S&P MidCap 400, or your own saved ticker list (2 to 250 names), with per-universe default benchmarks (QQQ, MDY, SPY). Then scan several universes in one request and get rotation crosschecks: sector crowding measured against the anchor universe's own composition, leading sectors you own nothing in, and cases where the sector ETF beat the median single-name pick.

**Watch for.** `/api/universes` is called on every page mount, so it must not scrape: iterating `data.get_universe` over 6 keys would serialize 3 Wikipedia timeouts on an airgapped box. A first sp400 run fetches 400 tickers across the **whole span already in the parquet**, not just the requested 5 years. Single-name scan boards must use `_top_performers`' exact `watch_n` expression or the same ticker shows NEW on one surface and not the other, which is an I1 break.

---

## Batch 6 - From symbols to a book

| | |
|---|---|
| **Specs** | [Per-ticker drill-down drawer](docs/superpowers/specs/2026-07-28-ticker-drilldown-drawer.md) (L), then [Portfolio tracker with sized trades](docs/superpowers/specs/2026-07-28-portfolio-tracker-sized-trades.md) (M) |
| **Effort** | L + M |
| **Deps** | Drawer needs `cache.load_prices` (batch 1) and the per-universe metadata helpers (batch 5). Portfolio needs the ledger's `cache.load_prices`, `cache.atomic_write`, `userdata/`, `ledger.params_hash` and `analysis.ledger_marks` (all batch 1). |

**Why these go together.** Both close the gap between a list of symbols and a decision you can act on: one makes every ticker inspectable, the other makes the target sizeable against your actual holdings. Both are self-contained panels with their own fetch, both reuse existing pure functions verbatim rather than adding ranking code, and both sit below `RebalancePanel` in v1 and v2.

**Why here.** The drawer's name/sector lookup must be cache-only and per-universe, which is what batch 5 delivers; writing it before would mean writing it twice. The portfolio tracker is the most instruction-shaped output in the tool, so it should size dollars into a target whose definition has stopped moving: costs (batch 1), regime context (batch 2), membership mask (batch 3) and signal family (batch 4) are all settled by now.

**What you can newly do.** Click any ticker anywhere (leaderboard, buy/keep chips, attribution chips, recent picks) and get a drawer with a rebased price chart against the benchmark, the shaded ranking window, monthly bars, the leaderboard's exact quality stats, and cohort membership dots. Paste a broker holdings list and get equal-weight target sizing, per-position drift, and a self-financing SELL/TRIM/ADD/BUY sequence with share counts, a cash walk, and since-paste performance for your book against the strategy and the benchmark.

**Watch for.** No volume anywhere: `_fetch_prices` keeps adjusted Close only, so the drawer ships without the one column users expect and a broker quote will not match the level. NaN reaching either response is an HTTP 500 (`allow_nan=False`), and `_safe_floats` is the wrong fix - a 0.0 in an equity curve is a wipeout that never happened, and a 0.0 weight is a fabricated position. Holdings never touch `localStorage`, `PROGRESS`, or any log. `BRK.B` must normalize to `BRK-B` on both client and server, since that is what the cache stores and what every broker statement writes.

---

## Batch 7 - Parameter sweep

| | |
|---|---|
| **Specs** | [Parameter-sweep heatmap](docs/superpowers/specs/2026-07-28-parameter-sweep-heatmap.md) (M) |
| **Effort** | M |
| **Deps** | Hard on turnover (batch 1) for `momentum_backtest_detail` and the `signal=` injection seam. |

**Why last.** It is the only spec whose output is actively misleading until every other honesty layer is in place. A grid maximum chosen on gross returns systematically over-nominates high-turnover cells, because the cost penalty scales with how short the hold is: re-measured, the drag is 1.51%/yr at hold1 against 0.29% at hold6, so the shortest holds along the grid's whole `hold` axis are exactly the cells that flatter themselves most. A grid maximum chosen on survivorship-biased history is worse, and the bias grows with `rank_lookback`, which is exactly the axis where the offline run's ridge sits (best cell L=12, N=5, h=1 at 1.99 net Sharpe against a 1.69 neighbour median, on a 5-name portfolio). Running it last means the surface being searched is the one the user would actually trade.

**What you can newly do.** See net Sharpe, CAGR and drawdown across a `rankLookback` x `topN` grid per hold period, with the colour scale anchored on the benchmark rather than the grid max, ineligible short-history cells hatched and excluded from BEST, and a computed neighbour-robustness readout next to a permanently visible cell-count caveat. Click a cell to load its configuration into the sidebar.

**Watch for.** Click-to-load must not auto-run: `setParam` is a state updater and `onRun` closes over the captured `params`, so setting 3 params then calling `onRun()` runs the **previous** configuration while the sidebar shows the new one, which would put the live buy list out of sync with everything around it. Degenerate cells (0 or 1 month) must serialize metrics as `null`, never 0.0, which would assert zero risk where risk is unknown. Cap the grid at 400 cells after dedup.

---

## All 11 specs

| Spec | Effort | Batch | Value in one line |
|---|---|---|---|
| [Turnover and transaction-cost drag](docs/superpowers/specs/2026-07-28-turnover-transaction-costs.md) | M | 1 | Charges for the trading the strategy does; 1.51%/yr drag at hold1 against 0.29% at hold6. |
| [Out-of-sample paper-trading ledger](docs/superpowers/specs/2026-07-28-oos-paper-trading-ledger.md) | M | 1 | The only number in the tool that is not in-sample; starts accruing the day it ships. |
| [Regime filter and live crash banner](docs/superpowers/specs/2026-07-28-regime-filter-crash-banner.md) | M | 2 | Names the market state and the current factor unwind, with n and a HAC t-stat. |
| [Earnings-date flags](docs/superpowers/specs/2026-07-28-earnings-date-flags.md) | S | 2 | Flags picks that report inside the hold window, before you buy them. |
| [Point-in-time S&P 500 membership](docs/superpowers/specs/2026-07-28-point-in-time-membership.md) | L | 3 | Turns the headline survivorship caveat into a measured number and an optional mask. |
| [Volatility-adjusted momentum signal](docs/superpowers/specs/2026-07-28-vol-adjusted-momentum-signal.md) | M | 4 | A second ranking family: same return per unit of risk instead of raw return. |
| [Additional universes](docs/superpowers/specs/2026-07-28-additional-universes.md) | M | 5 | Nasdaq-100, S&P MidCap 400 and a custom watchlist, ending the single-universe monoculture. |
| [Cross-universe scan board](docs/superpowers/specs/2026-07-28-cross-universe-scan.md) | M | 5 | One scan across universes, with sector-crowding and rotation crosschecks. |
| [Per-ticker drill-down drawer](docs/superpowers/specs/2026-07-28-ticker-drilldown-drawer.md) | L | 6 | Makes every symbol in the UI clickable and inspectable, offline. |
| [Portfolio tracker with sized trades](docs/superpowers/specs/2026-07-28-portfolio-tracker-sized-trades.md) | M | 6 | Converts the target into share counts against your own book, with a funded trade sequence. |
| [Parameter-sweep heatmap](docs/superpowers/specs/2026-07-28-parameter-sweep-heatmap.md) | M | 7 | Shows the whole parameter surface, with anti-overfitting machinery as a shipped feature. |

### Added 2026-11-18

| Spec | Effort | Batch | Value in one line |
|---|---|---|---|
| [Ledger reminder and missed-month backfill](docs/superpowers/specs/2026-11-18-ledger-reminder-backfill.md) | S | 2b | Restarts the only out-of-sample record, and keeps a gap from turning into a 4-month hold. |
| [Equal-weight universe baseline](docs/superpowers/specs/2026-11-18-equal-weight-universe-baseline.md) | S | 2b | A yardstick with the same names and weighting as the strategy: 17.4%/yr against 13.9% for SPY. |
| [Beta, alpha and information ratio](docs/superpowers/specs/2026-11-18-beta-alpha-information-ratio.md) | S | 2b | Replaces a CAGR subtraction labeled alpha with a regression. Hold6 beta is 1.35 to 1.50. |
| [Rank-bucket returns](docs/superpowers/specs/2026-11-18-rank-bucket-returns.md) | S | 2b | Shows that 9 of 10 deciles have no ordering and the edge sits in the top 10 names. |

---

## Decisions needed from the user

### Before or during batch 1

Turnover:

1. `costBps` default: one global 10 bps, or per-universe (sp500 10, global_etfs 25, us_sector_etfs 5)? global_etfs also carries roughly 0.50%/yr of expense drag that no bps constant expresses.
2. Does the dashboard headline lead with net (spec's assumption) or stay gross with net secondary?
3. Add `commissionPerTrade` plus an account-size input? At $10k across 10 names, $1/trade is another ~20 bps/month and doubles the drag.
4. Partial (month-to-date) newest month: suppress the cost charge, pro-rate it, or leave it labelled via the existing `fwd_partial` flag?
5. `turnover_ann` sleeve ramp: exclude only month 0 (reports 115%/yr vs 96%/yr post-ramp at h=12), or exclude the first `hold_period` months and accept per-sleeve month counts?

Ledger:

6. Delisted names: freeze at last close in the equal-weight denominator (spec's choice, keeps the denominator honest) or drop them (understates a bankruptcy less, reweights survivors up)?
7. Cap recording to 1 entry per calendar month per params hash? Without it, 2 clicks in a week put a 3-day segment in the chain and the curve depends on click cadence.
8. Keep `userdata/` gitignored, or commit the track record so it is version-controlled?
9. Lock scoring to the recorded benchmark, or allow the read-time override (which permits benchmark shopping after the fact)?
10. `basis_shifted` threshold: 1% will fire on nearly every aged row because auto_adjust re-adjusts dividends too. Raise to a split-shaped ~8%, or split into `basis_readjusted` vs `basis_split_suspect` against `SPLIT_JUMP_LO/HI`?

### Before or during batch 2

11. Given the filter measurably hurts on this window, does the filtered curve show by default when the mode is on, or does the feature ship banner-first with the filter behind an explicit "experiment" label?
12. Ship the optional `^VIX` strip at all, given that `realized_vol_percentile` covers the need with zero new network surface?
13. Wider benchmark price pull for full 200dma coverage from month 1 (needs a `prices_meta.json` coverage fix), or report `warmup_months` and leave those months invested?
14. Benchmark-mode months Yahoo never returned: truncate the filtered series, or show the fabricated flat 0% that `_safe_floats` and `_drawdown` currently produce?
15. Earnings hold-window definition for the warning style: end of the calendar month containing `as_of` (3 days on a run late in the month), flat 30 days, or month-end plus the largest selected hold? This encodes how you actually trade the list.
16. Before-open vs after-close: accept the bare date from `get_calendar`, or pay 5x for `get_earnings_dates`' tz-aware timestamp?
17. At high `topN` the 40-symbol budget truncates: accept unbadged rows past 40, or badge only the first `topN` and drop the watchlist suffix so the boundary is legible?

### Before or during batch 3

18. Should `pointInTime` default to true for sp500 once it exists? I2 forces false for the change itself, but the honest default is arguably true with unmasked as the opt-in upper bound. It changes every number you have been looking at.
19. Block pre-2010 windows outright, or warn via `reliable_from` plus a warnings entry? Row density collapses to ~4/yr before 2010.
20. Parse the Reason column so a ticker change becomes a swap rather than an add plus a remove? Spec chooses naive, because the error direction is conservative.
21. Agree the masked headline before the flag ships. If it lands materially below +470% / Sharpe 1.33, that needs accepting up front, not after.

### Before or during batch 4

22. Does the default family stay `raw`? I2 says yes for the change itself; the answer may differ once both curves have been seen.
23. Vol window: `max(rank_lookback, 6)` as specced, or a fixed 6 months (the literal Barroso and Santa-Clara pairing)? One constant either way.
24. Close the negative-numerator inversion (dividing a negative signal by vol promotes the highest-vol name), by restricting vol_adj to positive-signal names or switching to `R - lambda*sigma`, or leave it documented?
25. Prior-window diagnostics under vol_adj: suppress `streak`/`prior_rank`/`new_entrant` to None (specced, costs the whole rank-velocity column), or recompute them on the ratio (12 extra vol computations per refresh, and silently redefines `streak` from persistence-of-the-move to persistence-of-the-pick)?
26. Daily `min_obs=20` inside a ~128-bar window is looser than the monthly path's full-window requirement, and since the ranking rewards a small denominator it pushes thin-history names up. Add a coverage-fraction requirement, or reuse the 10-day reach-back tolerance?

### Before or during batch 5

27. Custom-list storage: server-side `cache/custom_universe.json` (specced, survives restart) or a per-request `customTickers` field (stateless, 4 call sites to thread)?
28. Should `cache.clear()` wipe the saved custom list? Specced no, because it is user-authored, but the button says "clear cache".
29. Nasdaq-100 sector taxonomy: ICB Industry verbatim (honest label, not comparable to the S&P views, reduces crosscheck coverage) or GICS from the sp500/sp400 metadata for the 88 overlapping names with ICB fallback for 15 (comparable, mongrel label)?
30. The 250-ticker custom cap is arbitrary, chosen to keep a cold custom run under the ~60s an sp500 pull already takes. Confirm or change.
31. Scan anchor when sp500 is unchecked: explicit `anchor` param defaulting to sp500 with crosschecks skipped (specced), or auto-pick the first single-name universe?
32. Is `leadRank=3` of 11 the right leading-sector cutoff? 2 is quieter, 4 risks making `missing_leader` background noise.
33. `crowded` currently fires on almost any 2-pick overlap at `topN` 3 or 4 (2 of 3 is 67%). Move to `count >= max(2, round(0.25 * topN))`, or accept that the flag is only meaningful at `topN >= 8`?
34. Should `crowded` also require a positive ETF return the way `missing_leader` does? Specced no, deliberately, because it would silence the flag in the drawdowns where crowding hurts most.
35. Should the scan auto-run on a warm cache like the leaderboard does? Specced no, at the price of an empty panel that reads as broken until clicked.

### Before or during batch 6

36. Add a `windowDays` override to the drawer? Without it the drawer's window is `round(rank_lookback * 30.4375)`, so a leaderboard at `days=45` cannot be reproduced and the user sees 30-day stats beside a 45-day return with nothing explaining it.
37. Default drawn range: 1 year with the ranking window shaded, or a 1y/3y/max toggle?
38. Ship the drawer without volume, or wait for a `prices.parquet` schema change that also stores it?
39. 13 months of cohort dots from the current run, or add a `MEMBERSHIP` response key for full-history membership (~10-30 KB at topN=10 over 60 months)?
40. Portfolio defaults: `driftTolerance=0.10` and `minTradeUsd=50`. Zero tolerance is the purest reading of the strategy (it rebalances to exactly 1/N monthly) but emits dust trades on all 10 names every month.
41. `portfolioValue` override is treated as total capital, so a $200,000 plan can be sized from a $50,000 paste with only a warning. Reject an override exceeding priced value plus cash instead?
42. `wholeShares=True` is universally executable, but most brokers now do fractional and the strategy is defined on exact equal weights. Flip the default?
43. `persist=False` means a user who never ticks the box keeps no record of their own book. Default to save with easy delete, at the cost of writing financial data to disk unasked?
44. `turnover_pct` denominator on a partly-cash book: `2*V` reports 50% for a first-time deployment of an all-cash account. Divide by `2*holdings_value`, suppress when there are no positions, or label the first diff an initial deployment?
45. Hard warning when `max(price)/target_value > 0.5`? A $900 stock in a $10,000 book is 9% and 1/N is unreachable.

### Before or during batch 7

46. Default grid: 192 cells (13s at 5y, 68s at 25y) or 48 cells with the wide grid opt-in (~4s)?
47. Should the hold axis follow the user's `holds` selection so the sweep matches the sleeves the rest of the dashboard shows, instead of a fixed `[1,3,6,12]`?
48. Keep `/api/sweep` synchronous (saturates the process for up to ~100s; progress polling still answers) or stream cells so the heatmap fills progressively?
49. Third metric: max drawdown (likely near-flat, most cells share the 2022 drawdown), Calmar, or the 5th-percentile month?
50. Benchmark anchoring: one benchmark measured over the longest eligible cell's index (specced, so a 50-month cell is scored against a 61-month benchmark), or recompute per cell window and accept a colour scale whose neutral point moves?
51. Add a per-axis note that long-`rankLookback` cells are the most survivorship-biased? The caveat and neighbour readout do not address that at all today.

### Before or during batch 2b

**All 11 answered by the owner on 2026-11-18.** Each answer is the specced option, so the 4 specs
are Status: Approved. Each spec lists its answers under Decisions.

Ledger reminder and backfill:

52. Gap policy at Record time: ask each time (specced), always backfill, or never?
53. The 12-month cap on one backfill: keep, change, or remove?
54. Do reconstructed segments count toward the 12-segment annualization gate? Specced yes, with the label beside the figure. The strict alternative counts live segments only.
55. Stale prices with no month due: does the reminder fire? Specced no.

Equal-weight baseline:

56. Charge the baseline its own rebalance cost? Specced no. A gross baseline is higher, so the error is against the strategy.
57. Does the headline lead with excess over the baseline, or stay on excess over the benchmark? Specced: benchmark first, baseline second.

Beta and alpha:

58. Add Sortino and Calmar in the same pass? Specced no, because they belong in `perf_stats`, which the spec does not edit.
59. Report beta and alpha against the equal-weight baseline too? Specced no.

Rank buckets:

60. Add a horizon that matches the longest selected hold? Specced: 1 month only.
61. Terciles for universes under 25 names, so the 11 sector ETFs get a chart? Specced no.
62. Medians beside means on the chart? Specced: mean on the bar, median in the tooltip.

---

## Not planned

Carried forward from the 2026-07-28 ideation, deliberately dropped:

| Idea | Why not |
|---|---|
| Run-history compare (diff 2 runs side by side) | Superseded by the parameter sweep and the ledger; a saved-run store is persistent state this tool deliberately does not keep. |
| Sub-period / bootstrap significance panel | The event study already ships HAC t-stats for the overlapping samples; a block bootstrap on ~62 monthly observations promises precision the data cannot support. |
| URL-encoded runs and saved presets | `location.hash` is already spoken for by the V1/V2 switcher, and the sweep's click-to-load covers configuration reuse without a second serialization format to maintain. |
| Monthly idea brief (generated markdown summary) | Duplicates the dashboard in a second surface that then has to be kept in step with every response-contract change, and it is exactly where caveats get dropped. |
| Scheduled refresh (cron or daily prefetch) | Single-user local tool with no process manager, and an airgapped or secured site cannot rely on a background fetch. The cache already refreshes on demand with a 6h tail-freshness window. |
| Universal export (whole-response CSV or XLSX) | Per-panel CSV already exists where the data is actionable; a generic dump exports numbers stripped of the caveats that make them honest. |
| Full OHLCV cache and liquidity screen | `_fetch_prices` keeps adjusted Close only, so this is a `prices.parquet` schema migration plus roughly 5x cache size, for a screen that a large-cap universe barely needs. |
| Market-cap filter | No cap field exists anywhere in the data path; Wikipedia supplies none, and a per-ticker fundamentals fetch is a new network surface (I5) for a weak screen. |
| Real risk-free rate via `^IRX` | `riskFree` is already a parameter. A fetched series puts an index symbol in the shared parquet (the `SPLIT_JUMP_LO/HI` band flags those on nearly every delta fetch) to move Sharpe by hundredths. |
| Dividend / total-return split | `yf.download(auto_adjust=True)` discards dividends at fetch time, so separating them means refetching unadjusted plus actions and maintaining 2 adjustment bases forever. |

---

## Conventions for any implementation here

1. **Dated spec first.** `docs/superpowers/specs/YYYY-MM-DD-<slug>.md`, front matter with Date / Status / Effort / Depends on. Status goes Proposed to Approved when the user signs off the open questions for that batch. Implement from the spec file, not from a summary of it: the verifier corrections live in the file.
2. **Tests against the seeded fixtures.** `tests/test_analysis.py`'s `_monthly_frame` and `_daily_prices(tickers, start, end, seed)` (131 bars, last bar mid-month, which is what exercises the partial-month path), and the injectable `fetcher=` seam plus `_point_cache_at` for anything touching `lib/cache.py`. A `clear()` test must patch all 4 cache paths or it deletes the developer's real cache. `server.py` has no endpoint tests, so push honesty logic down into pure functions where it can be asserted.
3. **The invariants, restated.** I1: one shared ranking path, so the live target equals the leaderboard at matching settings. I2: every new parameter defaults to leaving current output unchanged. I3: no silent lookahead, no fabricated data (None over an invented 0.0 wherever it changes meaning), HAC t-stats on overlapping samples. I4: local single-user, no build step, in-browser JSX, every new component registered in `charts.jsx`'s end-of-file `Object.assign`. I5: any new runtime network dependency is called out explicitly with a cache or offline degradation path.
4. **Serialization discipline.** Starlette dumps with `allow_nan=False`, so a NaN or inf anywhere in a response is a 500. `_safe_floats` is not the fix: 0.0 in an equity curve is a wipeout that never happened. Use None for scalars, in-segment forward fill for curves, and assert `json.dumps(payload, allow_nan=False)` in the test.
5. **No broker connectivity and no trade execution, ever.** The tool emits analysis, numbers and trade lists. Share counts and clipboard exports are the furthest it goes, and the not-advice disclaimer sits above the trade list, not below it.
6. **Frontend housekeeping.** Bump the `?v=` query on all 4 asset tags in `web/index.html` (currently `?v=4`; several specs claim `?v=5`, so whichever lands second takes the next free number). PERF `strategy` labels stay `top{N}_hold{h}m`: both dashboards match an anchored `/^top\d+_hold(\d+m)$/`, and a suffix silently blanks the drawdown chart and monthly heatmap.

---

## Progress

**Batch 1 shipped 2026-07-28** (commits `9792f9b`, `a8bea20`, `4d1e92d`), plus 2 features the
owner asked for outside the roadmap:

- [Turnover and transaction-cost drag](docs/superpowers/specs/2026-07-28-turnover-transaction-costs.md).
  `costBps` defaults to 10 per side, net is the headline series, `costBps=0` reproduces the old
  numbers. Measured: turnover 1099%/yr at hold1 against 218% at hold6, drag 2.98%/yr against 0.61%.
  hold6 keeps the best Sharpe both gross and net; the total-return ordering does invert.
- [Out-of-sample paper-trading ledger](docs/superpowers/specs/2026-07-28-oos-paper-trading-ledger.md).
  Recorded targets persist to gitignored `userdata/`, marked to market from the price cache.
- **Version badge**, bottom right, format `1.1.YYMMDDHH` from the last commit date, with mtime and
  unknown fallbacks.
- **What's new** modal, fed by git log since 2026-07-27.

A 3-dimension adversarial review of that diff confirmed 13 defects, all fixed in `4d1e92d`. The two
worth remembering, because both are shapes that will recur:

1. The benchmark column in `prices.parquet` lags the constituent columns (SPY's last bar is NaN
   while the rest carry the newest one). Any code reading a benchmark at an exact bar silently
   loses the comparison. Resolve to the last valid bar at or before the target, and disclose which
   bar was used.
2. Chaining two series while filtering each independently produces totals over different periods
   whose difference is not an excess return. Chain both legs over the same set and publish the
   coverage counts.

**Batch 2 shipped 2026-07-29** (commits `ab5ba13`, `3146e77`, `4c7b1b8`):

- [Regime filter and live crash banner](docs/superpowers/specs/2026-07-28-regime-filter-crash-banner.md).
  The banner ships always-on; the filter ships behind an explicit experiment label, because on this
  window it gives up a lot of return without reliably improving drawdown. On the current cache the
  badge reads FACTOR UNWIND: the benchmark is 6.5% ABOVE its 200-day average and 2.2% off its high
  while the best sleeve is deeply negative month to date, so the drawdown is momentum-specific
  rather than the market's, which is exactly the distinction the feature exists to draw.
- [Earnings-date flags](docs/superpowers/specs/2026-07-28-earnings-date-flags.md). Badges the live
  buy and keep names that report before the next month-end rebalance. Currently 3 of 10.

Decisions taken, since they differ from the spec defaults:
- Q11: the filter ships experiment-labelled, not on by default.
- Q13: report a warm-up count rather than widening the benchmark price pull.
- Q15: the warning window is "reports on or before the next month-end rebalance", because the
  strategy rebalances monthly, so that is the span over which a report lands inside a position
  taken today. A flat 30 days would warn about reports the next rebalance has already resolved.

The review confirmed 10 defects, fixed in `4c7b1b8`. Two are worth carrying forward as recurring
shapes: (1) any payload that is a dict of SCALARS is not covered by `_safe_floats`, so it needs its
own NaN coercion or it 500s, and `pd.NA` is worse than NaN because it silently encodes as an empty
object that is truthy in JavaScript; (2) a per-ticker network call belongs behind its own endpoint
called after render, never inline in a synchronous handler, or a cold cache silently adds seconds to
a response that advertises 0.3s.

Also settled here: the regime mask convention is now fixed in code (a daily average sampled at
month-end labels by forward fill, giving 13 risk-off month-ends), and under it filtering does NOT
reduce drawdown. That is what the spec originally claimed; the 22-month sampling noted earlier in
this file was the outlier.

Test count went 45 to 156. **Next: batch 3** (point-in-time membership), whose open questions are
numbers 18 to 21 above. Note it will make every headline number worse, which is the point.

**Batch 2b approved 2026-11-18.** A review of the shipped features found the ledger stopped after
1 entry, and measured 3 in-sample checks the dashboard lacks. 4 specs are written. The owner
answered questions 52 to 62 the same day with the specced option in each case, so all 4 are
Status: Approved. Nothing is built yet. Suggested order is batch 2b, then batch 3.

**Ledger reminder and backfill built 2026-11-18** (first of the 4 specs in batch 2b). The other
3 are not built. 22 tests were added, and the suite went from 156 to 177. The flow was checked in
the browser against an offline copy of the cache and a seeded ledger, never the real one.

Decisions taken during the build, since the spec left them open:
- The reminder follows the most recently recorded settings group. An old experiment under other
  settings does not keep the reminder on.
- The reminder sits once above the dashboard body, so it shows with and without a run.
- The reminder counts months on the server date. The Record prompt counts them on the price
  frame. With stale prices the 2 counts differ, and the reminder states where the prices end.

Found during the build:
- Not fixed: 2 tests in `tests/test_earnings.py` fail because their fixtures use fixed dates
  (2026-08-01 and 2026-09-09) that are now in the past.
- Fixed the same day: a partial price fetch left a hole that was never refetched.
  `prices_meta.json` stores one end date for the whole frame. A fetch that returned bars for 4
  new tickers only moved that end to 2026-11-17 for all 497, and the later-range fetch then
  skipped the rest. `cache.get_prices_cached` now finds each requested ticker that stops more than
  5 days before the frame's end and fills it from its own last bar, at most once per 6 hours per
  ticker, with the seam checked for a split. The ledger record path treats such a hole as stale.
  A trial on a copy of the real cache filled all 497 in 35 s. Tests: `tests/test_cache_lag.py`.
- Fixed on 2026-11-23: the gap fill asks Yahoo for bars up to the day after the requested end.
  On 2026-11-18 at 14:03 New York time it stored that day's open session as a final close. Every
  fetch in `cache.get_prices_cached` now drops a bar for today (New York date) until 16:30 New
  York time. The later-range fetch also re-pulls the last 2 cached days, like the tail refetch
  did. Before, it started after the cached end and never replaced a wrong last bar. The refresh
  on the real cache moved the 2026-11-18 close of SPY from 731.69 to 730.20. Tests:
  `tests/test_cache_session.py`.

The same review measured 2 more ideas that have no spec yet: a concentration readout with an
optional sector cap, and a volatility target. It tested a rank buffer and dropped it: on the
1-month signal turnover fell only from 1118% to 956% a year, and on the 12-1 signal Sharpe fell
from 1.37 to 1.23. The hold sleeves already cut turnover better.
