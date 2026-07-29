"""Next earnings date for a handful of tickers.

A fresh momentum buy that reports earnings three days later is a coin flip bolted
onto the signal, and the trade list is otherwise silent about it. This is scoped
deliberately narrowly: only the names actually on the live list and the
leaderboard, never the whole universe.

This is the one outbound network dependency in the dashboard that is not
``yf.download``, and it is a slower, flakier, per-ticker surface. So it is treated
as strictly optional decoration (I5):

- Cache first, with a TTL. A warm cache means no network at all.
- A hard wall-clock budget. Whatever has not arrived by then is simply absent.
- Every failure mode, including no network, collapses to "no date known" for that
  ticker. Nothing here can fail the trade list it annotates.
- ``MOMENTUM_OFFLINE=1`` skips the network entirely and serves cache only.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CACHE_DIR = Path("./cache")
EARNINGS_JSON = CACHE_DIR / "earnings_dates.json"

# Earnings calendars move rarely, so a day-old answer is fine and keeps a normal
# session entirely offline.
TTL_HOURS = 24
# Total wall clock for a whole batch. The panel is decoration: it must never be
# the reason a user waits.
BUDGET_S = 8.0
MAX_WORKERS = 8
# Hard ceiling on symbols per call, so a large topN cannot turn into hundreds of
# per-ticker requests.
MAX_SYMBOLS = 40


def _load_raw() -> dict:
    if not EARNINGS_JSON.exists():
        return {}
    try:
        blob = json.loads(EARNINGS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return blob if isinstance(blob, dict) else {}


def _fresh(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - fetched <= timedelta(hours=TTL_HOURS)


def load_cached(tickers) -> dict[str, str | None]:
    """Cached next-earnings dates for the tickers whose entries are still fresh."""
    raw = _load_raw()
    out: dict[str, str | None] = {}
    for t in tickers:
        entry = raw.get(t)
        if isinstance(entry, dict) and _fresh(entry):
            out[t] = entry.get("date")
    return out


def _save(dates: dict[str, str | None]) -> None:
    """Merge into the cache. Writes are best effort: losing the cache costs one
    refetch, so a disk error must not surface to the caller."""
    raw = _load_raw()
    stamp = datetime.now(timezone.utc).isoformat()
    for t, d in dates.items():
        raw[t] = {"date": d, "fetched_at": stamp}
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        EARNINGS_JSON.write_text(json.dumps(raw))
    except OSError:
        pass


def _fetch_one(ticker: str) -> str | None:
    """Next earnings date at or after today as YYYY-MM-DD, or None.

    ``Ticker.calendar`` is a dict whose "Earnings Date" is a list of
    ``datetime.date``; it can also be empty, missing, or raise. All of that means
    the same thing here: no date known.
    """
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception:
        return None
    if not isinstance(cal, dict):
        return None
    raw = cal.get("Earnings Date")
    if not raw:
        return None
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    today = date.today()
    upcoming = []
    for v in raw:
        d = getattr(v, "date", None)
        d = d() if callable(d) else v
        if isinstance(d, date) and d >= today:
            upcoming.append(d)
    return min(upcoming).isoformat() if upcoming else None


def next_earnings(tickers, allow_network: bool = True) -> dict[str, str | None]:
    """{ticker: 'YYYY-MM-DD' or None} for the requested tickers.

    Cache-first. Only the misses go to the network, in a small thread pool under
    a shared wall-clock budget. A ticker still missing when the budget expires is
    simply absent from the result, which the UI renders as no badge.
    """
    wanted = list(dict.fromkeys(t for t in tickers if t))[:MAX_SYMBOLS]
    if not wanted:
        return {}
    out = load_cached(wanted)
    missing = [t for t in wanted if t not in out]
    if not missing or not allow_network or os.environ.get("MOMENTUM_OFFLINE") == "1":
        return out

    fetched: dict[str, str | None] = {}
    deadline = datetime.now(timezone.utc) + timedelta(seconds=BUDGET_S)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in missing}
        for fut in as_completed(futures):
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            try:
                fetched[futures[fut]] = fut.result(timeout=max(0.1, remaining))
            except Exception:
                # Includes the per-future timeout. A missing date is not an error.
                continue
    if fetched:
        _save(fetched)
    out.update(fetched)
    return out


def days_until(iso_date: str | None, as_of: str | None = None) -> int | None:
    """Calendar days from ``as_of`` (default today) to ``iso_date``, or None."""
    if not iso_date:
        return None
    try:
        target = date.fromisoformat(iso_date)
        base = date.fromisoformat(as_of) if as_of else date.today()
    except (TypeError, ValueError):
        return None
    return (target - base).days
