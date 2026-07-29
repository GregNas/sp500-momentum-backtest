"""Earnings-date lookup: cache, TTL, budget, and every degradation path.

Nothing here touches the network. The point of the module is that it is optional
decoration which can never fail the trade list it annotates, so the tests are
mostly about failure modes.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from lib import earnings


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(earnings, "EARNINGS_JSON", tmp_path / "earnings_dates.json")
    monkeypatch.delenv("MOMENTUM_OFFLINE", raising=False)
    return tmp_path


def _write(path, entries):
    path.write_text(json.dumps(entries))


def _stamp(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# ---- cache and TTL ----

def test_fresh_cache_is_served_without_network(cache_dir):
    _write(earnings.EARNINGS_JSON,
           {"AAA": {"date": "2026-08-01", "fetched_at": _stamp(1)}})

    def boom(_):                     # would be called only on a cache miss
        raise AssertionError("network must not be touched on a fresh hit")

    out = earnings.next_earnings(["AAA"], allow_network=True)
    assert out == {"AAA": "2026-08-01"}


def test_stale_cache_entry_is_a_miss(cache_dir):
    _write(earnings.EARNINGS_JSON,
           {"AAA": {"date": "2026-08-01",
                    "fetched_at": _stamp(earnings.TTL_HOURS + 1)}})
    assert earnings.load_cached(["AAA"]) == {}


def test_cached_none_is_remembered_so_a_blank_is_not_refetched(cache_dir):
    # A ticker with no scheduled date must cache the absence, or every run pays
    # for it again.
    _write(earnings.EARNINGS_JSON,
           {"AAA": {"date": None, "fetched_at": _stamp(1)}})
    assert earnings.load_cached(["AAA"]) == {"AAA": None}


def test_corrupt_cache_file_is_a_miss_not_a_crash(cache_dir):
    earnings.EARNINGS_JSON.write_text("{not json")
    assert earnings.load_cached(["AAA"]) == {}
    assert earnings.next_earnings([], allow_network=False) == {}


def test_offline_env_var_skips_the_network(cache_dir, monkeypatch):
    monkeypatch.setenv("MOMENTUM_OFFLINE", "1")
    monkeypatch.setattr(earnings, "_fetch_one",
                        lambda t: (_ for _ in ()).throw(AssertionError("no network")))
    assert earnings.next_earnings(["AAA"]) == {}


def test_allow_network_false_skips_the_network(cache_dir, monkeypatch):
    monkeypatch.setattr(earnings, "_fetch_one",
                        lambda t: (_ for _ in ()).throw(AssertionError("no network")))
    assert earnings.next_earnings(["AAA"], allow_network=False) == {}


# ---- fetch behaviour ----

def test_results_are_cached_after_a_fetch(cache_dir, monkeypatch):
    monkeypatch.setattr(earnings, "_fetch_one", lambda t: "2026-09-09")
    out = earnings.next_earnings(["AAA", "BBB"])
    assert out == {"AAA": "2026-09-09", "BBB": "2026-09-09"}
    blob = json.loads(earnings.EARNINGS_JSON.read_text())
    assert blob["AAA"]["date"] == "2026-09-09"
    # Second call is served from disk.
    monkeypatch.setattr(earnings, "_fetch_one",
                        lambda t: (_ for _ in ()).throw(AssertionError("cached")))
    assert earnings.next_earnings(["AAA", "BBB"]) == out


def test_a_failing_ticker_is_absent_rather_than_fatal(cache_dir, monkeypatch):
    def half(t):
        if t == "BAD":
            raise RuntimeError("yahoo said no")
        return "2026-09-09"

    monkeypatch.setattr(earnings, "_fetch_one", half)
    out = earnings.next_earnings(["AAA", "BAD"])
    assert out["AAA"] == "2026-09-09"
    assert "BAD" not in out          # no badge, no error


def test_symbol_count_is_capped(cache_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(earnings, "_fetch_one",
                        lambda t: (seen.append(t), "2026-09-09")[1])
    earnings.next_earnings([f"T{i}" for i in range(earnings.MAX_SYMBOLS + 25)])
    assert len(seen) == earnings.MAX_SYMBOLS


def test_duplicates_are_requested_once(cache_dir, monkeypatch):
    seen = []
    monkeypatch.setattr(earnings, "_fetch_one",
                        lambda t: (seen.append(t), "2026-09-09")[1])
    earnings.next_earnings(["AAA", "AAA", "BBB"])
    assert sorted(seen) == ["AAA", "BBB"]


def test_write_failure_does_not_surface(cache_dir, monkeypatch):
    monkeypatch.setattr(earnings, "_fetch_one", lambda t: "2026-09-09")

    def no_write(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(earnings.Path, "write_text", no_write)
    # Losing the cache costs one refetch; it must not break the caller.
    assert earnings.next_earnings(["AAA"]) == {"AAA": "2026-09-09"}


# ---- date selection ----

def test_fetch_one_picks_the_earliest_upcoming_date(monkeypatch):
    today = date.today()
    past, soon, later = (today - timedelta(days=5), today + timedelta(days=3),
                         today + timedelta(days=40))

    class FakeTicker:
        def __init__(self, _):
            self.calendar = {"Earnings Date": [later, past, soon]}

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": FakeTicker}))
    assert earnings._fetch_one("AAA") == soon.isoformat()


def test_fetch_one_returns_none_when_every_date_is_past(monkeypatch):
    past = date.today() - timedelta(days=5)

    class FakeTicker:
        def __init__(self, _):
            self.calendar = {"Earnings Date": [past]}

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": FakeTicker}))
    assert earnings._fetch_one("AAA") is None


@pytest.mark.parametrize("calendar", [
    {},                                   # no key
    {"Earnings Date": []},                # empty list
    {"Earnings Date": None},              # explicit null
    {"Earnings Date": "not a date"},      # wrong type
    None,                                 # not a dict at all
])
def test_fetch_one_tolerates_every_shape_of_missing_data(monkeypatch, calendar):
    class FakeTicker:
        def __init__(self, _):
            self.calendar = calendar

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": FakeTicker}))
    assert earnings._fetch_one("AAA") is None


def test_fetch_one_returns_none_when_the_ticker_raises(monkeypatch):
    class FakeTicker:
        def __init__(self, _):
            raise RuntimeError("delisted")

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": FakeTicker}))
    assert earnings._fetch_one("AAA") is None


# ---- days_until ----

def test_days_until_counts_from_a_given_date():
    assert earnings.days_until("2026-08-05", as_of="2026-08-01") == 4
    assert earnings.days_until("2026-08-01", as_of="2026-08-01") == 0
    assert earnings.days_until("2026-07-30", as_of="2026-08-01") == -2


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-40"])
def test_days_until_is_none_on_unusable_input(bad):
    assert earnings.days_until(bad) is None


# ---- regression: the review findings ----

def test_a_cached_date_that_has_passed_is_a_miss(cache_dir):
    """Inside the TTL the stored value means "the next report". Once that day has
    passed the next one is a different date, so serving the old one would show a
    report as upcoming after it happened."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _write(earnings.EARNINGS_JSON, {
        "PAST": {"date": yesterday, "fetched_at": _stamp(1)},
        "SOON": {"date": tomorrow, "fetched_at": _stamp(1)},
    })
    out = earnings.load_cached(["PAST", "SOON"])
    assert "PAST" not in out
    assert out["SOON"] == tomorrow


def test_the_budget_is_actually_enforced(cache_dir, monkeypatch):
    """as_completed with no timeout, or exiting a `with ThreadPoolExecutor` block,
    would wait for every future and defeat the budget."""
    import time as _time
    monkeypatch.setattr(earnings, "BUDGET_S", 0.4)

    def slow(t):
        _time.sleep(30)
        return "2026-09-09"

    monkeypatch.setattr(earnings, "_fetch_one", slow)
    t0 = _time.monotonic()
    out = earnings.next_earnings(["AAA", "BBB"])
    elapsed = _time.monotonic() - t0
    assert elapsed < 5, f"budget not enforced: took {elapsed:.1f}s"
    assert out == {}          # nothing arrived, so no badges
