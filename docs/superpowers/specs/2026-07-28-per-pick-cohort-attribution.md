# Per-pick cohort attribution

**Date:** 2026-07-28
**Status:** Approved

## Problem

A cohort's realized return is shown as one opaque number (e.g. 2026-06: -27.2%).
`recent_picks` already computes each pick's forward return and discards everything but the
mean, so the user cannot tell whether one name blew up or all ten did — opposite situations
demanding opposite responses. The same applies to completed rebalance rows.

## Goal

Every realized cohort/rebalance row exposes its per-ticker forward returns; the dashboards
render them sorted by contribution with losers visually flagged.

## Design

- `recent_picks`: each entry gains `pick_returns` — `{ticker: float|None}` over the same
  forward month `return_pct` averages (None where the ticker has no bar). Order = signal order.
- `rebalance_schedule`: completed rows (a following month exists) gain `return_pct` and
  `pick_returns` with identical semantics: the row's picks marked over the following month.
  The newest month-end row and the live row stay without them (nothing realized yet).
  Note: the newest realized month may be partial (month-to-date) — same semantics the
  cohort mean already had.
- API: fields flow through RECENT_COHORTS and REBALANCE unchanged (`None` stays `null`).
- UI: V2 Recent-picks chips get the per-pick % (sorted by return, worst pick emphasized);
  RebalancePanel history rows show a realized-return badge and expand on click to a
  per-pick breakdown (both variants share the panel).

## Testing

- `recent_picks` attribution: mean of non-null `pick_returns` equals `return_pct`, and each
  value equals the underlying monthly return at t+1.
- `rebalance_schedule`: completed rows carry consistent `pick_returns`/`return_pct`; the
  newest row carries neither.

## Non-goals

- Entry/exit price-level attribution or multi-month holding P&L (monthly bars only).
- Per-ticker drill-down drawer (separate roadmap item).
