"""Disk cache for prices and tickers.

Single parquet file `cache/prices.parquet` (date index, ticker columns) plus a
sibling `prices_meta.json` recording coverage. On every call we compute the
delta between requested and cached coverage and fetch only what's missing.

Tickers list is cached separately at `cache/tickers.json` with a 24h TTL.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

CACHE_DIR = Path("./cache")
PRICES_PARQUET = CACHE_DIR / "prices.parquet"
PRICES_META = CACHE_DIR / "prices_meta.json"
TICKERS_JSON = CACHE_DIR / "tickers.json"
SP500_META_JSON = CACHE_DIR / "sp500_meta.json"

TICKERS_TTL_HOURS = 24
TAIL_REFETCH_DAYS = 2     # how many days at the tail to re-pull when the cache is stale
TAIL_FRESH_HOURS = 6      # skip the tail refetch when the cache was written this recently

# Split / adjustment-basis discontinuity detection.
# yfinance auto_adjust rebases the WHOLE adjusted-close history to each fetch's
# end date. When a freshly-fetched delta is stitched onto an older cached
# segment across a stock split, the two segments sit on different bases and
# leave a spurious overnight jump. A real one-day move for an index constituent
# essentially never crosses these bounds, but split ratios do (2:1 -> 0.5,
# 3:2 -> 0.67, 1:3 reverse -> ~3.0).
SPLIT_JUMP_HI = 1.40
SPLIT_JUMP_LO = 0.72


# ---------- tickers ----------

def load_tickers() -> list[str] | None:
    if not TICKERS_JSON.exists():
        return None
    try:
        blob = json.loads(TICKERS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    fetched = datetime.fromisoformat(blob["fetched_at"])
    if datetime.now(timezone.utc) - fetched > timedelta(hours=TICKERS_TTL_HOURS):
        return None
    return blob["tickers"]


def save_tickers(tickers: list[str]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    TICKERS_JSON.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "tickers": tickers,
    }))


# ---------- S&P 500 metadata (company name, GICS sector) ----------

def load_metadata() -> dict[str, dict] | None:
    if not SP500_META_JSON.exists():
        return None
    try:
        blob = json.loads(SP500_META_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    fetched = datetime.fromisoformat(blob["fetched_at"])
    if datetime.now(timezone.utc) - fetched > timedelta(hours=TICKERS_TTL_HOURS):
        return None
    return blob["metadata"]


def save_metadata(metadata: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    SP500_META_JSON.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
    }))


# ---------- prices ----------

def _load_meta() -> dict | None:
    if not PRICES_META.exists() or not PRICES_PARQUET.exists():
        return None
    try:
        return json.loads(PRICES_META.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save(prices: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    prices = prices.sort_index()
    prices.to_parquet(PRICES_PARQUET)
    PRICES_META.write_text(json.dumps({
        "start": prices.index.min().strftime("%Y-%m-%d"),
        "end": prices.index.max().strftime("%Y-%m-%d"),
        "tickers": sorted(prices.columns.tolist()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))


def clear() -> None:
    for p in (PRICES_PARQUET, PRICES_META, TICKERS_JSON, SP500_META_JSON):
        if p.exists():
            p.unlink()


def coverage() -> dict | None:
    """Return current cache coverage (start, end, ticker count, freshness) or None if empty."""
    meta = _load_meta()
    if meta is None:
        return None
    out = {
        "start": meta["start"],
        "end": meta["end"],
        "n_tickers": len(meta["tickers"]),
    }
    fetched_at = meta.get("fetched_at")
    if fetched_at:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
        out["fetched_at"] = fetched_at
        out["age_hours"] = round(age.total_seconds() / 3600, 1)
        out["is_fresh"] = age < timedelta(hours=TAIL_FRESH_HOURS)
    return out


def get_prices_cached(
    tickers: list[str],
    start: str,
    end: str,
    fetcher: Callable[[list[str], str, str], pd.DataFrame],
    on_status: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Return prices for `tickers` over `[start, end]`, downloading only the
    delta vs. what's already on disk.

    `fetcher(tickers, start, end) -> DataFrame` is the actual network call.
    """
    def status(msg: str) -> None:
        if on_status is not None:
            on_status(msg)

    requested = pd.Timestamp(start), pd.Timestamp(end)
    requested_set = set(tickers)

    meta = _load_meta()
    if meta is None:
        status(f"Cache miss — downloading {len(tickers)} tickers from {start} to {end}")
        prices = fetcher(tickers, start, end)
        _save(prices)
        return _slice(prices, *requested, tickers)

    cached = pd.read_parquet(PRICES_PARQUET)
    cached_start = pd.Timestamp(meta["start"])
    cached_end = pd.Timestamp(meta["end"])
    cached_tickers = set(meta["tickers"])
    cached_age = datetime.now(timezone.utc) - datetime.fromisoformat(meta["fetched_at"])
    cache_is_fresh = cached_age < timedelta(hours=TAIL_FRESH_HOURS)

    fetches: list[tuple[list[str], str, str]] = []

    # 1. Missing tickers across the whole requested range
    missing_tickers = sorted(requested_set - cached_tickers)
    if missing_tickers:
        full_start = min(requested[0], cached_start)
        full_end = max(requested[1], cached_end)
        fetches.append((missing_tickers, _d(full_start), _d(full_end)))

    # 2. Range extends earlier
    known_tickers = sorted(requested_set & cached_tickers)
    if requested[0] < cached_start and known_tickers:
        fetches.append((known_tickers, _d(requested[0]), _d(cached_start - timedelta(days=1))))

    # 3. Range extends later, OR tail refetch window — both skipped while cache is fresh
    if known_tickers and not cache_is_fresh:
        if requested[1] > cached_end:
            fetches.append((known_tickers,
                            _d(cached_end + timedelta(days=1)), _d(requested[1])))
        else:
            tail_cutoff = cached_end - timedelta(days=TAIL_REFETCH_DAYS)
            if requested[1] > tail_cutoff:
                fetches.append((known_tickers,
                                _d(tail_cutoff + timedelta(days=1)), _d(cached_end)))

    if not fetches:
        status("Cache hit — skipped download")
        return _slice(cached, *requested, tickers)

    merged = cached
    for f_tickers, f_start, f_end in fetches:
        status(f"Fetching {len(f_tickers)} ticker(s), {f_start} to {f_end}")
        delta = fetcher(f_tickers, f_start, f_end)
        merged = _merge(merged, delta)

    # Only known tickers (present in both the old cache and this run) can carry a
    # stitch across their old/new adjustment basis; a split in the fetched gap
    # shows up as a jump at the seam between cached and freshly-pulled data.
    # Boundary = last old-basis date on one side, first fresh-basis date on the
    # other. Extend-earlier prepends fresh bars *before* cached_start, so its
    # seam sits just below cached_start; extend-later / tail append fresh bars
    # after an old-basis date.
    boundaries = set()
    if requested[0] < cached_start:
        boundaries.add(cached_start - timedelta(days=1))
    if not cache_is_fresh:
        boundaries.add(cached_end if requested[1] > cached_end
                       else cached_end - timedelta(days=TAIL_REFETCH_DAYS))
    merged = _heal_split_artifacts(
        merged, sorted(requested_set & cached_tickers), fetcher, status,
        boundaries=boundaries,
    )

    _save(merged)
    return _slice(merged, *requested, tickers)


def _d(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def _merge(base: pd.DataFrame, delta: pd.DataFrame) -> pd.DataFrame:
    """Combine two price frames; delta wins on overlapping (date, ticker) cells.
    Aligns on the union of indices and columns automatically."""
    return delta.combine_first(base).sort_index()


def _slice(prices: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
           tickers: list[str]) -> pd.DataFrame:
    cols = [t for t in tickers if t in prices.columns]
    sliced = prices.loc[start:end, cols].dropna(axis=1, how="all")
    return sliced


# ---------- split / adjustment-basis healing ----------

def _is_split_ratio(r: float) -> bool:
    return r > SPLIT_JUMP_HI or r < SPLIT_JUMP_LO


def _detect_split_artifacts(prices: pd.DataFrame, tickers, boundaries=None) -> list[str]:
    """Tickers whose series has a jump characteristic of a split /
    adjustment-basis mismatch rather than a normal daily move.

    ``boundaries`` (a set of stitch-boundary Timestamps) restricts the check to
    the price step *across* those dates — used by the routine delta path, where
    only a jump at the old/new-basis seam signals an artifact. When omitted the
    whole series is scanned (used by the one-off :func:`repair`).
    """
    flagged: list[str] = []
    for tk in tickers:
        if tk not in prices.columns:
            continue
        s = prices[tk].dropna()
        if len(s) < 2:
            continue
        if boundaries:
            hit = False
            for b in boundaries:
                before = s[s.index <= b]
                after = s[s.index > b]
                if before.empty or after.empty:
                    continue
                if _is_split_ratio(after.iloc[0] / before.iloc[-1]):
                    hit = True
                    break
            if hit:
                flagged.append(tk)
        else:
            ratio = (s / s.shift(1)).iloc[1:]
            if _is_split_ratio(ratio.max()) or _is_split_ratio(ratio.min()):
                flagged.append(tk)
    return flagged


def _heal_split_artifacts(
    merged: pd.DataFrame,
    suspects,
    fetcher: Callable[[list[str], str, str], pd.DataFrame],
    status: Callable[[str], None] | None = None,
    boundaries=None,
) -> pd.DataFrame:
    """Re-fetch full histories for any suspect ticker that shows a split-basis
    discontinuity, so its whole series shares one adjustment basis.

    Best-effort: on a re-fetch failure the (imperfect) stitched data is kept.
    """
    def _status(msg: str) -> None:
        if status is not None:
            status(msg)

    flagged = _detect_split_artifacts(merged, suspects, boundaries)
    if not flagged:
        return merged

    _status(
        f"Healing {len(flagged)} split-adjusted ticker(s) via full re-fetch: "
        f"{', '.join(flagged[:8])}" + ("…" if len(flagged) > 8 else "")
    )
    full_start = _d(merged.index.min())
    full_end = _d(merged.index.max() + timedelta(days=1))
    try:
        clean = fetcher(flagged, full_start, full_end)
    except Exception as exc:  # network is best-effort; keep stitched data
        _status(f"Split-heal re-fetch failed ({exc}); serving stitched data")
        return merged

    for tk in flagged:
        if tk in clean.columns:
            # Prefer fresh (consistent-basis) values, but fall back to the
            # existing cached values for any date the re-fetch didn't return —
            # a partial/short response must never blank out valid prices.
            merged[tk] = clean[tk].reindex(merged.index).combine_first(merged[tk])
    return merged


def repair(
    fetcher: Callable[[list[str], str, str], pd.DataFrame],
    status: Callable[[str], None] | None = None,
) -> list[str]:
    """One-off cache repair: scan the on-disk parquet for split-artifact
    discontinuities left by earlier delta stitches and re-fetch the affected
    tickers on one consistent basis. Returns the list of healed tickers."""
    if _load_meta() is None:
        return []
    merged = pd.read_parquet(PRICES_PARQUET)
    flagged = _detect_split_artifacts(merged, list(merged.columns))
    if not flagged:
        return []
    merged = _heal_split_artifacts(merged, flagged, fetcher, status)
    _save(merged)
    return flagged
