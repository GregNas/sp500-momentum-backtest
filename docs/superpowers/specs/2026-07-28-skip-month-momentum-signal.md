# Skip-month momentum via a shared signal builder

**Date:** 2026-07-28
**Status:** Approved

## Problem

The ranking signal — trailing `rank_lookback`-month compounded return — is duplicated
inline in 5 functions (`event_study`, `momentum_backtest`, `recent_picks`,
`rebalance_schedule`, `cohort_sector_breakdown`), so signal variants cannot be added
consistently. And the literature-standard skip-month momentum spec (rank on months
t-12..t-2, skipping the most recent month to dodge 1-month reversal — "12-1") is
inexpressible: the tool always ranks on a window ending at the formation month, exactly
where short-term reversal contaminates the signal (see the Jul 2026 -14% cohort).

## Goal

One `momentum_signal(monthly_returns, rank_lookback, skip_months=0)` builder used by every
ranking consumer, plus a `skipMonths` (0–2, default 0) parameter surfaced through the API
and both dashboards. Default 0 keeps every output byte-identical to today.

## Design

- `momentum_signal` computes the existing trailing compounded return, then `.shift(skip_months)`,
  so row t holds the return over `(t - skip - L, t - skip]`. Classic 12-1 = lookback 11 + skip 1.
- All 5 ranking consumers take `skip_months=0` and source the signal from the builder;
  loop lower bounds move by `skip_months` (NaN rows self-exclude via `.dropna()`, the bound
  change just makes it explicit).
- `current_target` keeps the Top-Performers alignment guarantee at skip=0 (unchanged code
  path). With skip>0 it truncates the daily frame to bars at or before
  `latest_bar - round(skip * 30.4375)` days and ranks the same trailing window there;
  `as_of` stays the latest actual bar.
- `live_rebalance` passes `skip_months` to both the history schedule and the live target.
- `server.py`: `BacktestParams.skipMonths: int = Field(0, ge=0, le=2)`; threaded to all
  analysis calls; `/api/universes` `ranges` gains `skipMonths`.
- Frontend: `DEFAULT_PARAMS.skipMonths = 0`; `PARAM_HELP`/`DEFAULT_RANGES`/`paramIssues`
  entries; a "Skip months" NumField in both sidebars; header copy and RebalancePanel copy
  mention the skip when non-zero.

## Testing

- `momentum_signal` at skip=0 equals the legacy inline formula (L=1 passthrough and L>1 rolling).
- `momentum_signal` skip=S equals the unskipped signal shifted by S.
- `rebalance_schedule` with skip=1: picks at month m equal skip=0 picks at month m-1.
- `current_target` with skip=1 equals `top_performers_period` run on the frame truncated by
  ~1 month; `as_of` remains the latest bar. Existing alignment test pins the skip=0 path.

## Non-goals

- Alternative signal families (vol-adjusted, 52-week-high) — the builder is the seam for
  them, added separately.
- CLI flag surface (defaults keep `momentum_backtest.py` behavior unchanged).
