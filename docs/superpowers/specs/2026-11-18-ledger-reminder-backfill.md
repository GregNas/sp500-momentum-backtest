# Ledger reminder and missed-month backfill

**Date:** 2026-11-18
**Status:** Approved
**Effort:** S
**Depends on:** Out-of-sample paper-trading ledger (shipped 2026-07-28)

## Problem

The ledger is the only number in the tool that is not in-sample, and it stopped.

- `userdata/ledger.json` holds 1 entry: `as_of` 2026-07-27, recorded 2026-07-28.
- `cache/prices_meta.json` ends at 2026-08-21. Today is 2026-11-18.
- The month-ends of August, September and October passed with no entry.

Nothing in the tool says so. `LedgerPanel` renders only inside a finished dashboard run
(`web/v1.jsx:731`, `web/v2.jsx:829`). A user who opens the page sees no ledger state at
all. A record depends on a click that nothing asks for.

The gap also damages the record that exists. `analysis.ledger_marks` chains each entry to
the next one's start bar (`ends = [starts[k + 1] ...]`). If the next entry is recorded in
November, the July cohort is scored as held for 4 months. The strategy rebalances monthly,
so that curve describes a portfolio the tool never recommended.

## Goal

1. On open and without a run, the page shows that a target is due and how many month-ends
   were missed.
2. At Record time the user can fill the missed month-ends with reconstructed targets. The
   tool labels each one, and every headline shows the live-only figure beside the combined
   one.

## Design

### Two clocks, kept apart

- **The reminder uses the server date**. Stale prices mean the user stopped opening the
  tool, and that is the case the reminder exists for.
- **The backfill uses the price frame**. A month is reconstructable only from bars on disk.

### Pure functions in `lib/analysis.py`

```python
def ledger_missed_months(newest_as_of: str, upto: str) -> list[str]
```

Returns the `YYYY-MM` labels of the calendar months strictly between the month of
`newest_as_of` and the month of `upto`. Newest 2026-07-27 and `upto` 2026-11-18 give
`["2026-08", "2026-09", "2026-10"]`. A record in consecutive months gives `[]`, so a normal
monthly cadence never triggers the backfill.

```python
def reconstruct_targets(
    prices: pd.DataFrame, months: list[str],
    top_n: int, rank_lookback: int, skip_months: int = 0,
) -> list[dict]
```

For each month label:

1. Find `bar`, the last index value inside that calendar month.
2. Refuse the month unless the frame holds at least 1 bar dated after that month. Without
   this check a frame that ends on 2026-08-21 treats 08-21 as the August month-end.
3. Refuse the month unless `(bar - prices.index.min()).days >= signal_window_days(...)`.
   `top_performers_period` has no reach-back tolerance and ranks on a collapsed window
   instead of refusing. The live record path already applies this guard (`server.py:1017`).
4. Call `current_target(prices.loc[:bar], top_n, rank_lookback, skip_months)`.

Each result is `{"month", "target" | None, "refused_reason" | None}`. Step 4 is the existing
ranking path on a truncated frame, so no ranking code is added (I1).

### `lib/ledger.py`

```python
def append_many(entries: list[dict]) -> list[dict]
```

It takes the lock once. It applies the rules of `append` to the whole list in ascending
`as_of` order: not backdated, and 1 entry per calendar month per hash. It writes the file
once through `cache.atomic_write`. Any violation raises `LedgerConflict` and writes nothing. A backfill is
all or nothing: a half-filled gap is worse than either outcome.

`append` keeps its present behavior and its backdated-entry refusal. The backfill fills
only months after the newest entry of that hash. If the current month already holds a live
entry, the earlier gap is permanent.

A reconstructed entry uses the live schema plus 3 fields:

```jsonc
"reconstructed": true,
"reconstructed_at": "2026-11-18T09:12:44.101Z",
"universe_as_of": "2026-11-18"     // the date the member list was read
```

`recorded_at` is the time of the backfill. `entry_prices` and `benchmark_entry_price` come
from the frame at `bar`, with the same last-valid-bar rule the live path uses for the
benchmark. `SCHEMA_VERSION` stays 1 because the fields are additive. `SIGNAL_VERSION` and
`params_hash` do not change, so reconstructed and live entries share one group.

### When reconstruction is allowed

If the parameters were fixed before a month began, a reconstructed target for that month
is out-of-sample for them. Otherwise it is not. So:

- The backfill needs a prior **live** entry with the same `params_hash`. It never creates
  the first entry of a hash. This rule stops a user who picks new settings today and then
  "backfills" 5 years of them.
- The backfill covers at most 12 months. Older gaps stay as gaps.

### `server.py`

`LedgerRecordParams` gains `gapPolicy: Optional[Literal["backfill", "leave"]] = None`.

After `_record_ledger_target` builds the live target, it computes
`missed = ledger_missed_months(newest_as_of, target["as_of"])`. `newest_as_of` is the newest
entry of that hash, live or reconstructed.

| `missed` | `gapPolicy` | Result |
|---|---|---|
| empty | any | Present behavior, unchanged (I2). |
| not empty | `None` | Nothing is written. Response: `created: false`, `reason: "gap"`, `missed`, and `reconstructable` (per month, with any refusal reason). |
| not empty | `"leave"` | Present behavior. The gap stays. |
| not empty | `"backfill"` | `append_many([reconstructed..., live])` in 1 write. |

Under `"backfill"` the price pull must reach back far enough. The present stale-cache fetch
starts at `today - LEDGER_FETCH_DAYS` (455 days). If
`first_missed_bar - signal_window_days - 10 days` is earlier, start the pull there.

`GET /api/ledger` gains per group:

```jsonc
"gap": {
  "newest_as_of": "2026-07-27",
  "state": "missed",                 // "current" | "due" | "missed"
  "missed_months": ["2026-08", "2026-09", "2026-10"],
  "prices_through": "2026-08-21"
}
```

`due` means the newest entry is in an earlier month than the server date and no full month
was skipped. This endpoint already reads disk only, so the reminder works airgapped (I5).

The group summary gains `n_reconstructed` and a `live_only` block: `total_return`,
`benchmark_total_return`, `excess` and `n_segments` from a second `ledger_marks` call over
the live entries alone. Both figures always appear together. This removes a choice lever. The gap
months are visible before the click, so a single figure lets the user keep whichever record
looks better.

Each row from `_mark_segment` gains `reconstructed: bool`. `equity` gains
`reconstructed_ranges`, a list of `[start, end]` date pairs.

### Frontend

- New `LedgerNudge` in `web/charts.jsx`. It calls `fetchLedger` on mount and renders 1 line
  in the header of both dashboards and in both empty states. `due` is muted. `missed` uses
  the warning color and names the months. It renders nothing for `current` or an empty
  ledger.
- `useLedgerRecorder.onRecord`: on `reason: "gap"` it shows the months and 2 buttons,
  "Backfill, then record" and "Record with the gap". Each resends with the policy.
- `LedgerRow`: a "reconstructed" chip through `ledgerChips`. The tooltip states the 2 limits
  below.
- `LedgerPanel`: the reconstructed stretch of the curve is dashed. The header shows
  "N of M segments reconstructed" and the live-only total beside the combined total.
- Register `LedgerNudge` in the end-of-file `Object.assign`. Bump `?v=13` to the next free
  number on all 4 asset tags (I4).

## What a reconstructed entry is not

The UI text must state both limits, and must read them from the response:

1. **The member list is today's, not the month-end's.** A name that joined the index after
   the month-end can enter a reconstructed cohort. Index additions are often recent
   winners, so the bias is upward. Once point-in-time membership ships (batch 3), the
   backfill must pass that mask.
2. **Nobody saw the recommendation on the day.** It is a recomputation from prices that
   were later re-adjusted. The ranking uses price ratios, so a split does not move it. A
   dividend re-adjustment can move a close call.

## Invariants

- **I1:** reconstruction calls `current_target` on a truncated frame. The record path
  narrows to the universe and drops the benchmark before it ranks, as it does today.
- **I2:** with no gap, the request, the response and the file are unchanged. `"leave"`
  reproduces present behavior under a gap.
- **I3:** no bar after `bar` reaches the ranking. Refused months stay empty. They are never
  filled with a guessed cohort.
- **I5:** the reminder is disk-only. The backfill uses the existing fetch with the existing
  failure path.

## Testing

In `tests/test_ledger.py`, on the existing `_ramp_frame`, `_point_ledger_at` and
`_record_setup` fixtures:

- `ledger_missed_months`: consecutive months give `[]`. July to November gives 3 labels. A
  year boundary works. A same-month pair gives `[]`.
- `reconstruct_targets` equals `current_target` on the frame cut at the month's last bar.
- No lookahead: change every price after `bar`, and the reconstructed cohort is identical.
- A month with no bar after it is refused. A month with a short window is refused.
- `append_many` writes once. A conflict in the last entry leaves the file byte-identical.
- Record with a gap and no policy writes nothing and returns `reason: "gap"`.
- Record with `"backfill"` writes N + 1 entries in ascending order, N flagged.
- Record with `"leave"` matches the present response key for key.
- No live entry for the hash: the backfill is refused.
- `/api/ledger` reports `gap.state` for all 3 states, with the date injected.
- `live_only` equals `ledger_marks` over the live entries.
- `json.dumps(payload, allow_nan=False)` passes on both endpoints.

## Decisions

Taken by the owner on 2026-11-18. Roadmap numbers in brackets.

1. (52) Gap policy at Record time: the tool asks each time.
2. (53) One backfill fills at most 12 months.
3. (54) Reconstructed segments count toward the 12-segment annualization gate. The header
   shows "N of M segments reconstructed" beside the figure.
4. (55) Stale prices with no month due: the reminder stays quiet.

## Non-goals

- A scheduled or background record. The roadmap rejected that, and an airgapped site cannot
  rely on one.
- Backdated inserts between existing entries.
- Any edit or delete of a stored entry.
