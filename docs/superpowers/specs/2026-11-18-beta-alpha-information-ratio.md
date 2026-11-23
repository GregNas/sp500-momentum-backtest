# Beta, alpha and information ratio

**Date:** 2026-11-18
**Status:** Approved
**Effort:** S
**Depends on:** nothing

## Problem

`analysis.perf_stats` returns 6 figures: total return, CAGR, volatility, Sharpe, maximum
drawdown and the month count. None of them relates the strategy to the benchmark month by
month. The dashboard fills that hole with a subtraction and calls it alpha:

- `web/v1.jsx:442` prints `α +` followed by `bestStrat.CAGR - spy.CAGR`.
- `web/v2.jsx:603` labels the same difference `α (CAGR)`.

If beta is far from 1, a CAGR difference is not alpha. Here beta is far from 1. Measured on the
cache (see Provenance), the default hold6 sleeve has a beta of 1.35 over 15 years and 1.50
over 5. A portfolio with beta 1.5 beats a rising market with no skill.

Both v1 lines also hardcode the plus sign: `Δ +${...}` at `v1.jsx:436` and `α +${...}` at
`v1.jsx:442`. A sleeve that trails the benchmark prints `+-12pp`.

## Goal

Each sleeve reports beta, annualized alpha with a HAC t-stat, tracking error and
information ratio, computed by regression on paired monthly returns. The 2 mislabeled lines
show what they are.

## Design

### `lib/analysis.py`

```python
RELATIVE_MIN_OBS = 12

def relative_stats(returns: pd.Series, benchmark: pd.Series,
                   periods_per_year: int = 12,
                   risk_free_annual: float = 0.0) -> dict
```

Steps:

1. Pair the 2 series on months where both have a value. Do not fill.
2. Subtract the monthly risk-free rate from both.
3. Fit `r = alpha + beta * b` by ordinary least squares, with numpy.
4. Take the coefficient covariance from the Newey-West sandwich, with the same Bartlett
   kernel and automatic bandwidth as `_newey_west_tstat`:
   `V = (X'X)^-1 * S * (X'X)^-1`, where `S` is the weighted sum of the lagged
   autocovariances of `X_t * u_t`.
5. `active = r - b` on the paired months, before step 2.

| Key | Meaning |
|---|---|
| `beta` | slope |
| `alpha_ann` | intercept times `periods_per_year` |
| `alpha_t` | intercept over its HAC standard error |
| `tracking_error` | `std(active) * sqrt(periods_per_year)` |
| `information_ratio` | `mean(active) * periods_per_year / tracking_error` |
| `rel_n` | paired months |

Every value is `None` in 3 cases:

- `rel_n < RELATIVE_MIN_OBS`
- the benchmark has zero variance
- `X'X` is singular

`tracking_error == 0` gives `information_ratio: None`. All floats pass through `_jsonf`.

**Why a new function and not new keys in `perf_stats`.** `perf_stats` takes 1 series. Its
keys are spread into every `PERF` row and the CLI table, and `ledger_marks` calls it on
daily data. A benchmark argument there changes 3 callers.

**Why not `_newey_west_tstat` on `r - beta * b`.** That treats beta as known. The sandwich
form carries the estimation error in beta. On this data the 2 differ by up to 0.25: 3.41
against 3.66 for hold6 over 15 years. The shortcut is not conservative in every case.

**No new dependency.** `statsmodels` is not in `requirements.txt`. The sandwich is about 15
lines of numpy, and an airgapped mirror stays the same size.

### `server.py`, in `_run_backtest`

The regression must not use `bench_aligned`. That series has missing months filled with
0.0 for the equity curve (`server.py:398`). A regression on invented zeros biases beta
toward 0.

- Use `benchmark_monthly.reindex(r.index)`, unfilled.
- Drop the in-progress month from both series (`constituents.index.max() < label`). The
  benchmark column can lag the constituents, so in a partial month the 2 returns can end on
  different days.
- Add the 6 keys to each strategy row in `PERF`, filtered rows included. The benchmark row
  carries `None` for all 6.

No row is added and no existing key changes, so the 3 best-sleeve selectors are untouched.

### Frontend

- Performance table (both dashboards): columns Beta, Alpha, t, IR. A `None` renders as the
  existing dash.
- `v1.jsx:442`: the tile sub-line becomes `Δ CAGR` with a real sign, and gains
  `alpha 11.7%/yr, t 3.66, beta 1.35`.
- `v1.jsx:436`: remove the hardcoded plus sign.
- `v2.jsx:603`: rename `α (CAGR)` to `Δ CAGR`, and add `alpha (regression)` and `beta`
  lines to the same block.
- One fixed sentence under the table: alpha is the return left after the benchmark's
  moves are scaled by beta and removed. It is in-sample.
- Bump `?v=` on all 4 asset tags (I4).

### CLI

`momentum_backtest.py:99` builds the table from `perf_stats` only. Add the `relative_stats`
keys to the strategy rows there, so the 2 surfaces stay in step.

## Invariants

- **I1:** no ranking code.
- **I2:** no new parameter. `perf_stats` is not edited. Every existing `PERF` key keeps its
  value.
- **I3:** no filled months in the regression. HAC errors. `None` under 12 observations. The
  in-progress month is excluded.
- **I5:** no new data and no new package.

## Testing

In `tests/test_analysis.py`:

- `r = 0.01 + 2 * b` exactly: `beta` is 2, `alpha_ann` is 0.12, `tracking_error` matches the
  hand value.
- `r = b`: `beta` 1, `alpha_ann` 0, `information_ratio` is `None` (zero tracking error).
- Autocorrelated residuals: `alpha_t` is smaller than the plain OLS t-stat, in the manner
  of `test_newey_west_shrinks_tstat_under_autocorrelation`.
- 11 paired months: every key is `None`.
- A benchmark with 3 missing months: `rel_n` drops by 3, and the result equals the fit on
  the hand-paired months.
- A constant benchmark: every key is `None`, and nothing raises.
- A risk-free rate changes `alpha_ann` by `rf * (1 - beta)` and leaves `beta` unchanged.
- `json.dumps` of a `PERF` row with `allow_nan=False` passes for each case above.

## Provenance

Measured 2026-11-18 on `cache/prices.parquet`: 497 priced S&P 500 columns, 2011-05-02 to
2026-08-21, top 10, 1-month rank, 10 bps per side, against SPY, the partial August 2026
month excluded. The t-stats are the sandwich form above.

| Window | Sleeve | Beta | Alpha | t | Tracking error | IR |
|---|---|---|---|---|---|---|
| 15 years | hold1 | 1.11 | 11.4%/yr | 2.08 | 19.9% | 0.66 |
| 15 years | hold6 | 1.35 | 11.7%/yr | 3.66 | 13.9% | 1.21 |
| 5 years | hold1 | 1.33 | 23.2%/yr | 2.02 | 28.9% | 0.95 |
| 5 years | hold6 | 1.50 | 20.3%/yr | 2.67 | 18.8% | 1.42 |

These figures use today's member list. They are an upper bound until batch 3 ships.

## Decisions

Taken by the owner on 2026-11-18. Roadmap numbers in brackets.

1. (58) No Sortino or Calmar in this pass. `perf_stats` stays untouched.
2. (59) Beta and alpha are reported against the benchmark only.

## Non-goals

- Multi-factor attribution (size, value, a momentum factor). It needs factor return series
  the tool does not hold, which is a new network dependency.
- Rolling beta. One number per sleeve is enough to correct the reading of the headline.
