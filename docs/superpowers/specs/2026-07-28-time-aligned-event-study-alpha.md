# Time-aligned event-study alpha and benchmark-relative win rate

**Date:** 2026-07-28
**Status:** Approved

## Problem

`event_study_summary` computes alpha as the cohort mean minus the benchmark's **full-sample**
mean monthly return, and win rate as the share of cohorts with a return above **zero**.
Cohorts formed ahead of strong market stretches therefore look alpha-positive by
construction, and win rate is trivially high in bull markets. The dashboard's verdict line
("winners keep winning at h=11, t=3.2") — the tool's credibility anchor for trusting the
live trade list — is built on that miscalibrated statistic.

## Goal

Alpha per horizon = mean of **same-month** cohort-minus-benchmark spreads, with the
Newey-West t-stat computed on that aligned spread; win rate = share of cohorts **beating
the benchmark** at that horizon. The full-sample benchmark mean stays as the chart's
reference line.

## Design

- `event_study` gains an optional `benchmark_monthly` param. When given, the benchmark is
  reindexed to the constituents' monthly index and each `(cohort, h)` row carries
  `bench_return` = the benchmark's actual return at `t+h`.
- `event_study_summary` detects the `bench_return` column: spread = `mean_return -
  bench_return` (rows with a missing benchmark month dropped from the spread stats),
  `alpha_vs_bench` = spread mean, `t_stat` = NW HAC t-stat of the spread, `win_rate` =
  `(spread > 0).mean()`. Without the column (legacy callers) the old constant-mean behavior
  is preserved bit-for-bit.
- `server.py` and the CLI (`momentum_backtest.py`) pass `benchmark_monthly` in, so both
  entry points report aligned stats.
- UI copy: V1 significance callout and V2 verdict say the alpha is vs same-month benchmark;
  V2's win-rate view keeps its 0.5 reference line, which now reads "coin flip vs benchmark".

## Testing

- Synthetic events with a `bench_return` column: alpha equals the hand-computed spread mean,
  win rate equals the share of rows beating the benchmark, t-stat equals
  `_newey_west_tstat(spread)`.
- `event_study(..., benchmark_monthly=...)` stamps `bench_return` equal to the benchmark's
  return at exactly `t+h` on a deterministic frame.
- Legacy path: the existing structure test (events without `bench_return`) still passes
  unchanged.

## Non-goals

- Beta-adjusted (regression) alpha and bootstrap CIs — heavier inference, separate feature.
- Changing `benchmark_avg` (the chart reference line) or the bar values themselves.
