"""Append-only local record of the live targets the tool recommended.

I/O only, mirroring :mod:`lib.cache`'s JSON pattern. The math lives in
``analysis.ledger_marks``.

The file sits in ``./userdata``, not under ``cache.CACHE_DIR``: ``cache.clear()``
unlinks 4 named files there today, so a ledger inside the cache directory would
survive by accident only. Outside it, no present or future ``clear()`` can reach
the one thing in this project that cannot be recomputed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from lib import cache

DATA_DIR = Path("./userdata")
LEDGER_JSON = DATA_DIR / "ledger.json"
SCHEMA_VERSION = 1

# Bump when momentum_signal / current_target semantics change. It is part of
# params_hash, so a bump splits pre-change and post-change entries into separate
# groups instead of chaining two different strategies into one equity curve.
SIGNAL_VERSION = 1

# FastAPI runs sync endpoints in a threadpool, so two Record clicks can reach
# append() at once and interleave a read-modify-write.
_LOCK = threading.Lock()


class LedgerConflict(Exception):
    """An append that cannot be made without corrupting the segment chain."""


def params_hash(params: dict) -> str:
    """Stable short hash of the inputs that determine the recorded cohort.

    These 4 fields plus ``SIGNAL_VERSION`` are exactly what
    ``analysis.current_target`` consumes. Deliberately excluded: ``lookback``
    (the widest ranking window fits inside even the shortest allowed price pull,
    so the top-N set does not move with it, and hashing it would fragment one
    honest track record), ``holds``/``horizon``/``riskFree`` (backtest reporting
    only), and ``benchmark`` (changes what the record is compared against, not
    what was recommended).
    """
    payload = {
        "universe":       params["universe"],
        "topN":           int(params["topN"]),
        "rankLookback":   int(params["rankLookback"]),
        "skipMonths":     int(params["skipMonths"]),
        "signal_version": SIGNAL_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _quarantine() -> Path | None:
    """Move an unreadable ledger aside and return where it went.

    Append-only means never destroying user data, so a corrupt file is renamed,
    never overwritten. The stamp is ISO basic format (no colons) so the name is
    also legal on Windows.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = LEDGER_JSON.with_name(f"ledger.corrupt-{stamp}.json")
    n = 1
    while target.exists():
        target = LEDGER_JSON.with_name(f"ledger.corrupt-{stamp}-{n}.json")
        n += 1
    try:
        LEDGER_JSON.rename(target)
    except OSError:
        return None
    return target


def load() -> list[dict]:
    """Every recorded entry, or ``[]``. Never raises.

    A file that is missing, unreadable or not shaped like a ledger yields ``[]``;
    in the unreadable case it is quarantined first (see :func:`_quarantine`) so
    the next append starts from a clean file instead of failing forever.
    """
    if not LEDGER_JSON.exists():
        return []
    try:
        blob = json.loads(LEDGER_JSON.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _quarantine()
        return []
    if not isinstance(blob, dict) or not isinstance(blob.get("entries"), list):
        _quarantine()
        return []
    return [e for e in blob["entries"] if isinstance(e, dict)]


def _for_hash(entries: list[dict], p_hash: str) -> list[dict]:
    return [e for e in entries if e.get("params_hash") == p_hash]


def append(entry: dict) -> tuple[dict, bool]:
    """Add ``entry`` unless an equivalent one is already recorded.

    Returns ``(entry, created)``. ``created is False`` means nothing was written
    and the returned entry is the one already on file, in 2 cases:

    * the same ``(as_of, params_hash)`` — a double click on the same bar;
    * a different ``as_of`` in the same calendar month under the same hash — the
      one-per-month cap, which keeps a second click in the same month from
      putting a 3-day segment in the chain.

    Raises :class:`LedgerConflict` for an ``as_of`` older than the newest entry
    of that hash: a backdated recommendation cannot exist and would corrupt
    segment chaining.
    """
    p_hash = entry["params_hash"]
    as_of = entry["as_of"]

    with _LOCK:
        entries = load()
        same_hash = _for_hash(entries, p_hash)

        for existing in same_hash:
            if existing.get("as_of") == as_of:
                return existing, False

        if same_hash:
            newest = max(same_hash, key=lambda e: e.get("as_of") or "")
            if as_of < (newest.get("as_of") or ""):
                raise LedgerConflict(
                    f"as_of {as_of} predates the newest entry for these settings "
                    f"({newest.get('as_of')}); a backdated target cannot be recorded."
                )
            in_month = [e for e in same_hash
                        if (e.get("as_of") or "")[:7] == as_of[:7]]
            if in_month:
                return max(in_month, key=lambda e: e.get("as_of") or ""), False

        entries.append(entry)
        _write(entries)
        return entry, True


def _write(entries: list[dict]) -> None:
    # allow_nan=False so a NaN in an entry fails here, loudly, instead of
    # writing a file that json.loads will later reject as corrupt.
    blob = json.dumps(
        {"schema_version": SCHEMA_VERSION, "entries": entries},
        indent=2, allow_nan=False,
    )
    DATA_DIR.mkdir(exist_ok=True)
    cache.atomic_write(LEDGER_JSON, lambda p: p.write_text(blob))


def append_many(new_entries: list[dict]) -> list[dict]:
    """Add several entries in one write, or none of them.

    Used by the backfill, where a half-filled gap is worse than either outcome.
    Entries are applied in ascending ``as_of`` order and each must pass, against
    the file plus the entries staged before it:

    * its ``as_of`` is later than the newest entry of its hash;
    * its calendar month holds no entry of its hash;
    * if it is ``reconstructed``, an earlier *live* entry of its hash exists. A
      reconstructed month is out-of-sample only for settings that were already
      on record, so a backfill can never start a track record.

    Any failure raises :class:`LedgerConflict` and leaves the file untouched.
    Unlike :func:`append`, a duplicate is a conflict here, not a no-op: the
    caller asked for an exact set of entries to exist together.
    """
    ordered = sorted(new_entries, key=lambda e: e["as_of"])
    if not ordered:
        return []

    with _LOCK:
        staged = load()
        for entry in ordered:
            as_of = entry["as_of"]
            same_hash = _for_hash(staged, entry["params_hash"])
            if same_hash:
                newest = max((e.get("as_of") or "") for e in same_hash)
                if as_of <= newest:
                    raise LedgerConflict(
                        f"as_of {as_of} is not later than the newest entry for these "
                        f"settings ({newest}); nothing was written."
                    )
                if any((e.get("as_of") or "")[:7] == as_of[:7] for e in same_hash):
                    raise LedgerConflict(
                        f"{as_of[:7]} already has an entry for these settings; "
                        f"nothing was written."
                    )
            if entry.get("reconstructed") and not any(
                    not e.get("reconstructed") for e in same_hash):
                raise LedgerConflict(
                    "A reconstructed entry needs an earlier live entry with the same "
                    "settings; nothing was written."
                )
            staged.append(entry)
        _write(staged)
        return ordered
