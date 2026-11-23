# Rank-bucket returns (decile chart)

**Date:** 2026-11-18
**Status:** Approved
**Effort:** S
**Depends on:** Skip-month momentum via a shared signal builder (shipped)

## Problem

The tool tests one slice of the ranking: the top N. It never shows what the rest of the
ranking did. That hides the most useful check on a ranking signal: if rank predicts return,
average return rises from the worst bucket to the best. If only the extreme top differs,
the result rests on a few names.

Measured on the cache (see Provenance), with the default 1-month signal over 15 years, the
average next-month return by decile, losers first:

```
1.55  1.56  1.44  1.45  1.36  1.31  1.45  1.20  1.56  1.72   (% per month)
```

- 9 of the 10 buckets show no ordering. The best decile beats the universe average (1.45%)
  with t 1.09, and beats the worst decile with t 0.48.
- The top 10 names return 2.47% a month, with t 2.16 against the universe.

So the measured edge sits in about 2% of the universe. A broad momentum effect does not
look like this. A few very large winners do, and so does survivorship bias, because the
current member list keeps exactly those winners. The dashboard shows none of this.

## Goal

- A chart of average next-month return by rank bucket, with the user's top N as a separate
  bar.
- A top-minus-bottom spread with a HAC t-stat.
- One computed sentence that says whether the edge is broad or concentrated.

## Design

### `lib/analysis.py`

```python
BUCKET_MIN_NAMES = {10: 100, 5: 25}

def rank_bucket_returns(
    monthly_returns: pd.DataFrame,
    rank_lookback: int,
    skip_months: int = 0,
    top_n: int | None = None,
    signal: pd.DataFrame | None = None,
) -> dict
```

- The signal comes from `momentum_signal`. If the caller already holds it, the `signal=`
  argument takes it instead. `_run_backtest` already computes it once at `server.py:349` and passes
  it to the sleeve loop. This function reads it and never mutates it.
- **Bucket count**. If the median count of ranked names is at least 100, the count is 10.
  If it is at least 25, the count is 5. Otherwise the function returns `rows: []` with a `reason`. The S&P 500 gets
  deciles, the 37 global ETFs get quintiles, and the 11 sector ETFs get the reason text.
- **Loop**. For each formation month `i` in the range `event_study` uses
  (`rank_lookback - 1 + skip_months` to the second-last row):
  1. `rank = signal.iloc[i].dropna()`. If the month holds fewer names than the
     threshold, skip it.
  2. Assign buckets with `pd.qcut(rank.rank(method="first"), n, labels=False)`. Bucket 1
     holds the losers. `method="first"` makes ties deterministic.
  3. For each bucket, take the mean and the median of `monthly_returns.iloc[i + 1]` over
     its names, with NaN dropped.
  4. Take the same mean over all ranked names (the universe average). If `top_n` is given,
     take it over `rank.nlargest(top_n)` too.
- **Output**:

```jsonc
{
  "n_buckets": 10, "bucket_label": "decile",
  "horizon_months": 1, "gross": true, "months": 181,
  "universe_avg": 0.0145,
  "rows": [{"bucket": 1, "avg_return": 0.0155, "median_return": 0.0131,
            "excess": 0.0010, "t_stat": 0.4, "n_months": 181, "avg_names": 47.1}, ...],
  "top_n": {"n": 10, "avg_return": 0.0247, "excess": 0.0102, "t_stat": 2.16},
  "spread": {"avg": 0.0016, "t_stat": 0.48},        // best bucket minus worst
  "rank_correlation": 0.12,                          // Spearman, bucket vs avg_return
  "reason": null
}
```

`median_return` is the median across the names in the bucket each month, averaged over
the months. It feeds the tooltip. `excess` is the bucket minus the universe average in
the same month. Every `t_stat` is
`_newey_west_tstat` on the monthly difference series. The numbers shown for `median_return` and `t_stat` on
the first row and for `rank_correlation` are placeholders. Every float passes through `_jsonf`.

### `server.py`, in `_run_backtest`

- Pass the `signal` already in hand and `top_n=params.topN`.
- If `constituents.index.max() < monthly.index[-1]`, the newest month is in progress. Drop
  it before the call: `monthly.iloc[:-1]` and the matching signal rows. A month-to-date return is not
  a monthly observation.
- New top-level key `RANK_BUCKETS`. No existing key changes.

### Costs

The buckets are gross. A bucket is not a tradable portfolio, and a cost needs a turnover
rule the bucket does not have. The payload says `gross: true`. The top-N bar is therefore
slightly above the net hold1 sleeve in the performance table, and the chart caption says
so from the flag.

### Frontend

- New `RankBucketChart` in `web/charts.jsx`: 1 bar per bucket, a dashed line at
  `universe_avg`, and the top-N bar set apart on the right in the accent color. The tooltip
  shows the excess and the t-stat.
- Placement: beside the event-study chart in both dashboards.
- One sentence, chosen from the numbers:
  - `spread.t_stat >= 2`: "Return rises with rank across the universe."
  - `spread.t_stat < 2` and `top_n.t_stat >= 2`: "The edge sits in the top N names only.
    The broad ranking shows no ordering."
  - neither: "No bucket differs reliably from the universe average."
- A fixed caveat: survivorship inflates the lowest buckets most. A current member that fell
  hard is a name that later recovered enough to stay in the index.
- With `rows: []` the panel shows the `reason` text.
- Register `RankBucketChart` in the end-of-file `Object.assign`. Bump `?v=` on all 4 asset
  tags (I4).

## Invariants

- **I1:** the buckets rank on `momentum_signal`. The top-N bar must equal the gross hold1
  sleeve month for month, and a test asserts it. This function becomes a 6th consumer of
  the signal, so batch 3 and batch 4 must thread their new arguments through it too.
- **I2:** no new parameter. The response gains 1 key.
- **I3:** formation uses row `i` and returns use row `i + 1`. The in-progress month is
  excluded. Overlap-safe HAC t-stats. A universe that is too small gets a reason, not a
  chart of 1-name buckets.
- **I5:** no new data.

## Testing

In `tests/test_analysis.py`, on `_monthly_frame` widened to 120 tickers:

- A frame built so that return next month rises with rank this month: bucket averages
  increase strictly, `rank_correlation` is 1.0, `spread.t_stat` is large.
- A shuffled frame: `abs(spread.t_stat) < 2`.
- The top-N series equals `momentum_backtest(..., hold_period=1)` on the matching months.
- `signal=None` and a passed-in signal give identical output, and the passed frame is
  unchanged afterward.
- 37 tickers give 5 buckets. 11 tickers give `rows: []` and a `reason`.
- Ties: 2 tickers with equal signal land in a stable bucket across 2 calls.
- No lookahead: change row `i + 2` onward, and the bucket assigned at `i` and its return do
  not move.
- `skip_months=1` shifts the first formation month by 1.
- `json.dumps(payload, allow_nan=False)` passes, with a month where 1 bucket has no return.

## Provenance

Measured 2026-11-18 on `cache/prices.parquet`: 497 priced S&P 500 columns, 2011-05-02 to
2026-08-21, gross, complete months only.

| Window | Signal | Worst decile | Universe | Best decile | Top 10 | t (best decile vs universe) | t (top 10 vs universe) |
|---|---|---|---|---|---|---|---|
| 15 years, n 181 | 1-month | 1.53% | 1.45% | 1.69% | 2.47% | 1.09 | 2.16 |
| 5 years, n 59 | 1-month | 0.83% | 1.18% | 2.13% | 3.52% | 1.96 | 2.22 |

The 12-1 signal over 15 years gives a U shape: 1.84% for the worst decile and 2.13% for the
best, with every bucket between them near 1.4%. The high worst-decile figure is the
survivorship pattern the caveat describes. That row came from an earlier run that included
the partial month, so re-measure it before any use in UI text.

## Decisions

Taken by the owner on 2026-11-18. Roadmap numbers in brackets.

1. (60) The horizon is 1 month only.
2. (61) A universe under 25 names gets the reason text. No terciles.
3. (62) The bar shows the mean. The tooltip shows the median.

## Non-goals

- A long-short backtest of best minus worst bucket. The tool is long-only, and the spread
  figure already answers the validation question.
- Bucket transition tables or bucket-level turnover.
