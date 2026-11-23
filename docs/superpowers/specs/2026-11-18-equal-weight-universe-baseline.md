# Equal-weight universe baseline

**Date:** 2026-11-18
**Status:** Approved
**Effort:** S
**Depends on:** nothing

## Problem

Every comparison in the dashboard is against a cap-weighted ETF (`SPY` by default). The
strategy differs from that benchmark in 3 ways at once:

1. It selects names (the thing under test).
2. It equal-weights them.
3. It draws them from today's member list (survivorship).

The benchmark shares none of these, so the headline gap credits all 3 to selection.

Measured on the cache (see Provenance): the equal-weight average of every current S&P 500
member returned 17.4% a year over 15 years against 13.9% for SPY, with no selection at all.
Most of that 3.5 points is items 2 and 3. A comparison against it removes them.

## Goal

A second baseline on every run: the equal-weight return of the whole universe, from the
same names, the same months and the same weighting rule as the strategy. Each sleeve reports
its excess over that baseline with a HAC t-stat. The baseline line appears on the equity
chart.

## Design

### `lib/analysis.py`

```python
def equal_weight_universe(monthly_returns: pd.DataFrame) -> pd.Series:
    return monthly_returns.mean(axis=1, skipna=True)

def excess_stats(returns: pd.Series, baseline: pd.Series,
                 periods_per_year: int = 12) -> dict
```

`equal_weight_universe` averages the names that have a return in the month. That is the
same rule the sleeve mean applies to its picks (`month[picks].dropna().mean()`), so the 2
series treat a missing bar alike. A month with no returns stays NaN. It is never 0.0.

`excess_stats` pairs the 2 series on the months where both exist and returns:

| Key | Meaning |
|---|---|
| `excess_ann` | mean of `r - baseline`, times 12 |
| `excess_t` | `_newey_west_tstat(r - baseline)`, because overlapping sleeves are autocorrelated |
| `n` | paired months |

All 3 are `None` under 12 paired months.

### `server.py`, in `_run_backtest`

`constituents` already has the benchmark column removed, and `monthly` is built from it, so
`ew = analysis.equal_weight_universe(monthly)` needs no further narrowing.

- Reindex `ew` to `first_returns.index`, the index every other series uses.
- Exclude the in-progress month from `excess_stats`. The test is the one `fwd_partial`
  uses: `constituents.index.max() < label`.
- New top-level key:

```jsonc
"BASELINE": {
  "key": "ew",
  "label": "Equal-weight S&P 500",          // from UNIVERSE_LABEL
  "perf": { ...perf_stats(ew) },
  "names_median": 471,
  "costs_charged": false,
  "shares_survivorship": true
}
```

- `EQUITY["ew"]`, `DRAWDOWN["ew"]`, `MONTHLY_RETURNS["ew"]`, `YEARLY_RETURNS["ew"]`. Both
  dashboards read `EQUITY` by explicit key (`h{n}m`, `h{n}m_filt`, `spy`). A new key is inert
  until the UI asks for it.
- Each strategy row in `PERF`, filtered rows included, gains `excess_ew_ann`,
  `excess_ew_t` and `excess_ew_n`. The benchmark row carries `None` for all 3.

**The baseline is not a `PERF` row**. Both dashboards and `server.py:506` pick the best
sleeve from `PERF`. The pick is the highest Sharpe among rows that are not the benchmark and
not filtered. A baseline row becomes eligible for that pick in 3 places.

### Costs

The sleeves are net of `costBps`. The baseline is gross. That makes the baseline higher and
the reported excess lower, so the error is against the strategy. `costs_charged: false`
goes in the payload and the UI text reads it from there.

### Frontend

- `v1.jsx:324` and `v2.jsx:311`: add a dotted third line to the equity series, named from
  `BASELINE.label`.
- KPI area (v1) and the "vs benchmark" rail (v2): one line for the best sleeve,
  "vs equal-weight universe +13.6%/yr, t 3.64".
- Performance table: 1 extra row drawn from `BASELINE.perf` below the benchmark row, and an
  "Excess vs EW" column on the strategy rows.
- A short caveat built from the payload flags. The baseline shares the member list. So the
  excess removes the equal-weight and survivorship effects that every name shares. It does
  not remove the part of survivorship that selection itself picks up.
- Bump `?v=` on all 4 asset tags (I4).

## Invariants

- **I1:** no ranking code. The baseline never touches `momentum_signal`.
- **I2:** no new parameter. Every existing response key keeps its value. `PERF` keeps its
  row count.
- **I3:** a missing month is `None`. The t-stat is HAC. The in-progress month is excluded
  from the statistics.
- **I5:** no new data. The series comes from frames already in memory.

## Interaction with batch 3

Under point-in-time membership the mask goes on the signal, not on `monthly_returns`. The
baseline averages `monthly_returns`, so it keeps today's member list while the
strategy stops using it. Batch 3 must give the baseline the same member mask, or the 2
series stop being comparable. When batch 3 starts, record this in its spec.

## Testing

In `tests/test_analysis.py`, on `_monthly_frame`:

- `equal_weight_universe` equals the hand-computed row mean with a NaN cell skipped.
- An all-NaN month yields NaN from the function and `None` in the payload.
- `excess_stats` equals `_newey_west_tstat` on the hand-built difference.
- Under 12 paired months every key is `None`.
- A baseline month missing on one side drops the pair. It does not zero-fill it.
- With the feature present, every pre-existing `PERF` key equals its prior value on the
  seeded frame, and the row count is unchanged.
- `json.dumps(payload, allow_nan=False)` passes.

## Provenance

Measured 2026-11-18 on `cache/prices.parquet`: 497 priced S&P 500 columns, 2011-05-02 to
2026-08-21, top 10, 1-month rank, 10 bps per side, the partial August 2026 month excluded.

| Window | Baseline CAGR | SPY CAGR | hold1 excess | hold6 excess |
|---|---|---|---|---|
| 15 years | 17.4% | 13.9% | 9.9%/yr, t 1.75 | 13.6%/yr, t 3.64 |
| 5 years | 14.4% | 12.6% | 26.0%/yr, t 2.05 | 25.3%/yr, t 2.88 |

Not measured: the return of a real equal-weight fund over the same period. The claim that
most of the baseline's lead over SPY is survivorship rests on that comparison. Do not put
the claim in UI text until it is measured.

## Decisions

Taken by the owner on 2026-11-18. Roadmap numbers in brackets.

1. (56) The baseline stays gross. The payload carries `costs_charged: false`.
2. (57) The headline leads with excess over the benchmark. Excess over the baseline is the
   second line.

## Non-goals

- A fetched equal-weight ETF (`RSP`) as a second benchmark. It is a new symbol in the
  shared parquet. It also does not share the survivorship of the strategy, which is the
  point of this baseline.
- A cap-weighted universe baseline. No market-cap field exists in the data path.
