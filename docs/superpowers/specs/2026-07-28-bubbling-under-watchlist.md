# Bubbling-under watchlist: near-misses, new entrants, rank velocity

**Date:** 2026-07-28
**Status:** Approved

## Problem

The leaderboard only surfaces names *after* their move is fully priced into the trailing
return. Names about to enter the top N — the actual buy-early ideas — are invisible, and
there is no way to tell an accelerating candidate from a decelerating incumbent.

## Goal

Show ranks N+1..N+10 ("just outside"), a rank-change arrow vs one week earlier, and a NEW
badge for names absent from the previous window's top 2N — all from the already-ranked
universe frame.

## Design

- `top_performers_period` gains `watch_n=0`: ranks `top_n + watch_n` names (default
  unchanged, so `current_target` and existing tests are unaffected). Every row also gains:
  - `prior_rank`: the ticker's rank over the same-length window ending at the last bar ≥ 7
    calendar days earlier (None when that window can't be formed);
  - `new_entrant`: True when the ticker was absent from the top-2N set one full window
    back (reuses `_window_return_top_set`); None when that set can't be formed.
- `server._top_performers`: `watch_n = min(10, universe_size - topN - 1)`; splits the
  ranked rows into `results` (existing contract) and a new `watchlist` response field, both
  enriched with name/sector and `rank_delta = prior_rank - rank` (positive = moved up).
- `TopPerformersPanel`: sortable "Δ 7d" column on the main table (▲ up / ▼ down); a
  "Bubbling under" chip row below the table — `#rank TICKER +x% ▲n NEW` with a
  name·sector·prior-rank tooltip.

## Testing

- `watch_n=k` returns `top_n + k` rows whose first `top_n` match the plain call.
- A ticker that only explodes in the final 2 weeks: `new_entrant` is True; a steady
  compounder already in the prior top-2N: False.
- `prior_rank` equals a hand-computed rank over the window ending ≥ 7 days back.

## Non-goals

- Cross-universe scanning and richer accumulation logic (roadmap items).
- Persisting watchlists between sessions.
