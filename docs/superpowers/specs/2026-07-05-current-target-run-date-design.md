# Align the rebalance "current target" to the run date

**Date:** 2026-07-05
**Status:** Approved

## Problem

Two dashboard panels appear to disagree about which tickers to buy:

- **Top performers** ranks S&P 500 names by simple return over a **trailing 30
  calendar days** ending on the latest available bar (e.g. `2026-06-02 →
  2026-07-02`). Computed from daily prices in `top_performers_period`.
- **Monthly rebalance · current target** (top row of `rebalance_schedule`) ranks
  by the **last monthly bar**. With `rankLookback=1` that bar is the current,
  *partial* calendar month — often only 1–3 trading days of data right after a
  month-end (e.g. `2026-06-30 → 2026-07-02`).

Because a trailing-30-day window and a 2-trading-day month-to-date window measure
completely different things, the two panels show different names. This is not a
data bug — it is two different measurement windows. But it is confusing and the
"current target" is a noisy 2-day coin-flip early in each month.

## Goal

Make the "current target" reflect momentum **as of the run date**, using the same
signal as Top Performers, so the two panels line up at matching settings (the
default view).

Explicitly **out of scope:** merging the two panels' controls into one shared set
("full unify"). The panels keep independent controls; they *coincide* at matching
settings but may diverge if the user sets, e.g., Top Performers to 60 days while
`rankLookback` stays 1.

## Design

### Current-target computation

The top ("current target") row of the rebalance list stops using the partial
calendar-month bar. Instead it is computed from the **daily `prices`** already
fetched in `_run_backtest`, using the same trailing-window logic as
`top_performers_period`:

- **Window:** `rankLookback` months → `round(rankLookback * 30.4375)` calendar
  days. At the default `rankLookback=1` → **30 days**, identical to Top
  Performers' default `days=30`.
- **N:** the momentum "Top N picks/month" (`topN`). Default 4 = Top Performers'
  default.

Anchored on the last available bar and picks the first bar at-or-after the target
start date (same weekend/holiday handling as `top_performers_period`).

### Diff semantics (BUY / SELL / KEEP)

The current-target row's diff compares the live target set against **the most
recent completed calendar month's picks** (what you'd be holding from the last
month-end rebalance):

- `buys`  = live target names not in last-completed-month picks
- `sells` = last-completed-month picks not in live target
- `holds` = intersection

So `buys ⊆` Top-Performers names (a name already held shows as KEEP, not BUY) —
correct rebalance behavior.

### History rows unchanged

All rows below the top row remain exactly as today: month-end monthly bars,
diffed against the prior month. They represent the backtest and are not touched.

### Labeling

The top row is relabeled from a `YYYY-MM` month to a live marker, e.g.
`live · as of 2026-07-02` (the latest bar date). Panel helper text in
`web/charts.jsx` (`RebalancePanel`) updated to describe the top row as an
"as of today" trailing-window snapshot rather than a calendar month.

## Implementation surface

- `lib/analysis.py`
  - New helper (or extended `rebalance_schedule`) that produces the live
    current-target row from daily prices. Preferred: a small standalone function
    `current_target(prices, top_n, rank_lookback)` returning the pick list +
    `as_of` date, reusing the window math from `top_performers_period`, so it is
    independently testable.
- `server.py`
  - In `_run_backtest`, build the live current-target row from `prices` (daily,
    pre-monthly) and splice it as the first element of `REBALANCE`, computing the
    diff against the most recent completed-month picks. Attach `meta` sector
    labels as done for other rows.
- `web/charts.jsx` (`RebalancePanel`)
  - Render the live row's `as_of` label and update helper/subtitle text.

## Testing

- Unit: `current_target(prices, top_n, rank_lookback)` returns exactly
  `top_performers_period(prices, top_n, days=round(rank_lookback*30.4375))`'s
  tickers, in the same order, with the correct `as_of` date.
- Unit: the spliced `REBALANCE[0]` buys/sells/holds equal the set-diff of the
  live target vs the most recent completed month's picks.
- Regression: `REBALANCE[1:]` (history) is unchanged vs the current behavior.

## Non-goals / follow-ups

- Full control unification (single shared lookback + top-N driving both panels).
- Revisiting whether the *historical* rebalance rows should rank by the prior
  month (`t-1`) to match `momentum_backtest`'s convention — a separate, deeper
  question flagged but not addressed here.
