# Top Performers leaderboard: signal-quality columns

**Date:** 2026-07-28
**Status:** Approved

## Problem

The leaderboard — the tool's primary idea surface — shows only trailing return and sector,
so a one-day short squeeze is indistinguishable from a trend consolidating near its
52-week high. Every "should I actually buy this" question forces a trip to an external
site.

## Goal

Each leaderboard row carries enough context, computed from the daily closes already in the
parquet cache, to triage durable momentum from junk spikes at a glance: volatility, 52-week
-high proximity, largest single-day move, 3m/6m/12m trailing returns, a 1-year weekly
sparkline, and a top-N persistence streak. Columns sortable; a compact durable-vs-spike
badge.

## Design

- `analysis.top_performers_period` rows gain: `vol_ann` (annualized std of daily returns in
  the ranking window), `max_day_move` (signed largest |1-day| move in the window),
  `below_52w_high` (end price vs trailing-365d max, ≤ 0), `ret_3m`/`ret_6m`/`ret_12m`
  (trailing returns; None when history doesn't reach back within 10 days of the target
  start), `spark` (trailing-year weekly closes, None when under 8 weeks), and `streak`
  (consecutive *prior* windows of the same length the ticker already ranked top-N, capped
  at 12; prior top-N sets are computed once and shared across picks).
- All fields are additive — `current_target` and existing tests are unaffected.
- `server._top_performers` widens the price pull to ≥ 370 days so the 52w/12m/spark fields
  are populated, and passes the new fields through `results`.
- `TopPerformersPanel`: sortable numeric columns (window return, 3m, 12m, vol, off-high,
  max 1d, streak) via clickable headers; 1y `Sparkline` (previously dead code); badge:
  `spike` when one session ≥ 5% drove ≥ 50% of the window return, `near high` when within
  5% of the 52-week high. Table scrolls horizontally on narrow viewports.

## Testing

- On the seeded daily fixture: `vol_ann` equals the hand-computed window std × √252;
  `below_52w_high` equals end/max−1 over the trailing year; `ret_3m` equals the price ratio
  at the 91-day boundary; `max_day_move` is the signed largest absolute daily move;
  short-history frames yield None for `ret_12m` instead of a fabricated number.
- `streak`: a ticker made to dominate the last k windows reports streak ≥ k−1... asserted
  via a constructed frame where one ticker strictly outperforms in every window (streak
  hits the cap) and a shuffled frame where streaks stay small.

## Non-goals

- Fundamentals / liquidity data (needs new fetchers — separate roadmap items).
- The bubbling-under watchlist section (next feature, same seam).
