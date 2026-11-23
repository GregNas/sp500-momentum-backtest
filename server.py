"""FastAPI backend for the momentum backtest dashboard.

Run:
    .venv/bin/uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lib import analysis, cache, data, earnings, ledger, universes

UniverseKey = Literal["sp500", "global_etfs", "us_sector_etfs"]

ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"

app = FastAPI(title="S&P 500 Momentum Backtest")

# In-flight status messages keyed by client-generated runId, polled via
# GET /api/progress/{run_id}. In-process only — fine for single-worker uvicorn,
# not shared across multiple workers.
PROGRESS: dict[str, list[str]] = {}


class BacktestParams(BaseModel):
    universe: UniverseKey = "sp500"
    lookback: int = Field(5, ge=1, le=25)
    topN: int = Field(10, ge=1, le=100)
    holds: List[int] = Field(default_factory=lambda: [1, 3, 6])
    rankLookback: int = Field(1, ge=1, le=12)
    skipMonths: int = Field(0, ge=0, le=2)  # gap between ranking window and formation
    horizon: int = Field(12, ge=1, le=24)
    benchmark: str = "SPY"
    riskFree: float = Field(0.0, ge=0.0, le=0.2)  # annual risk-free rate for Sharpe
    # Trading cost in basis points charged on each side of a trade. Replacing the
    # whole book bills 2x this. costBps=0 reproduces the pre-cost numbers exactly.
    costBps: float = Field(10.0, ge=0.0, le=100.0)
    # Regime filter. "off" adds no filtered series and no PERF rows, so the
    # response is unchanged (I2). The REGIME banner payload is computed either
    # way: it is pure diagnostics over data already in hand.
    regimeMode: Literal["off", "cash", "benchmark"] = "off"
    regimeMaDays: int = Field(200, ge=20, le=400)
    runId: Optional[str] = None


class TopPerformersParams(BaseModel):
    universe: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    days: int = Field(30, ge=1, le=365)
    runId: Optional[str] = None


class LedgerRecordParams(BaseModel):
    universe: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    rankLookback: int = Field(1, ge=1, le=12)
    skipMonths: int = Field(0, ge=0, le=2)
    benchmark: str = "SPY"
    note: Optional[str] = Field(None, max_length=280)
    # What the browser had on screen, checked against the recomputed bar so a
    # stale tab can be told it recorded a different day than it displayed. The
    # cohort itself is never taken from the client.
    displayedAsOf: Optional[str] = None
    # What to do when earlier month-ends have no entry. None asks: nothing is
    # written and the response lists the months. "leave" records with the gap.
    # "backfill" first fills the months with reconstructed targets. With no gap
    # all 3 behave the same.
    gapPolicy: Optional[Literal["backfill", "leave"]] = None
    runId: Optional[str] = None


# ---- build info (version badge + "What's new" feed) ----

# Bump by hand on a release. The trailing YYMMDDHH is derived from the code,
# never hand-edited.
APP_VERSION = "1.1"

# Date the "What's new" feed starts from, inclusive. Fixed on purpose: a rolling
# "last 24h" window would silently drop entries as time passes. There are no
# commits on 2026-07-27 itself, so this captures the 2026-07-28 work and
# excludes the 2026-07-05 and earlier history.
WHATS_NEW_SINCE = "2026-07-27"
WHATS_NEW_MAX_ENTRIES = 100

GIT_TIMEOUT_S = 3.0

# ASCII unit/record separators. Commit bodies contain blank lines and trailers,
# so newline- or blank-line-delimited git output cannot be parsed unambiguously;
# these two bytes cannot occur in a commit message.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

# Files whose mtime stands in for the commit date when git is unavailable.
_SOURCE_GLOBS = ("server.py", "lib/*.py", "web/*")

_COAUTHOR_RE = re.compile(r"^[ \t]*Co-Authored-By:.*(?:\n|$)", re.IGNORECASE | re.MULTILINE)


def _git(args: List[str]) -> Optional[str]:
    """stdout of a git command run in the project root, or None on any failure.

    A deployed copy may have no .git directory and no git binary at all, so
    every failure mode (missing binary, not a repo, non-zero exit, timeout)
    collapses to None rather than an exception.

    ``git -C DIR`` searches *upward* for a repository, so this first requires
    ROOT_DIR to be the repo root itself. Without that check, a copy unpacked
    inside some unrelated working tree would report that repo's commit date as
    this app's version and render its commit messages as this app's change feed.
    ``.git`` is tested as a path rather than a directory so worktrees and
    submodules, where it is a file, still work.
    """
    if not (ROOT_DIR / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT_DIR)] + list(args),
            capture_output=True, timeout=GIT_TIMEOUT_S, check=False,
            # Decode explicitly: bare text=True uses the host's preferred
            # encoding, so on a non-UTF-8 locale one accented name in an author
            # trailer would drop the whole feed instead of one character.
            encoding="utf-8", errors="replace",
        )
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _mtime_stamp() -> Optional[str]:
    """Newest source-file mtime as YYMMDDHH, or None if nothing is readable."""
    newest = None
    for pattern in _SOURCE_GLOBS:
        for path in ROOT_DIR.glob(pattern):
            try:
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    if newest is None:
        return None
    return datetime.fromtimestamp(newest).strftime("%y%m%d%H")


def _build_stamp() -> Tuple[Optional[str], str]:
    """(YYMMDDHH, source) identifying the deployed code. Never raises.

    Prefers the last commit date so the stamp tracks the code, not a file touch;
    falls back to mtimes, then gives up rather than blocking startup.
    """
    out = _git(["log", "-1", "--format=%cd", "--date=format:%y%m%d%H"])
    if out:
        stamp = out.strip()
        if re.fullmatch(r"\d{8}", stamp):
            return stamp, "git"
    try:
        stamp = _mtime_stamp()
    except Exception:
        stamp = None
    if stamp:
        return stamp, "mtime"
    return None, "unknown"


def strip_coauthor_trailers(body: str) -> str:
    """Drop Co-Authored-By trailers: plumbing, not user-facing news."""
    return _COAUTHOR_RE.sub("", body).strip()


def parse_git_log(raw: str, max_entries: int = WHATS_NEW_MAX_ENTRIES) -> List[dict]:
    """Parse `git log` output formatted with _FIELD_SEP/_RECORD_SEP into entries.

    Bodies keep their internal blank lines; only the Co-Authored-By trailers and
    surrounding whitespace are removed.
    """
    entries: List[dict] = []
    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        fields = record.split(_FIELD_SEP)
        if len(fields) < 4:
            continue
        commit_hash, date, subject = (f.strip() for f in fields[:3])
        if not commit_hash:
            continue
        entries.append({
            "date": date,
            "hash": commit_hash,
            "subject": subject,
            "body": strip_coauthor_trailers(fields[3]),
        })
        if len(entries) >= max_entries:
            break
    return entries


def _whats_new_entries() -> Tuple[List[dict], str]:
    """(entries, source) newest first. source is "git" or "unavailable"."""
    fmt = _FIELD_SEP.join(["%h", "%cd", "%s", "%b"]) + _RECORD_SEP
    raw = _git([
        "log",
        "--since=" + WHATS_NEW_SINCE,
        "--max-count=" + str(WHATS_NEW_MAX_ENTRIES),
        "--date=format:%Y-%m-%d",
        "--format=" + fmt,
    ])
    if raw is None:
        return [], "unavailable"
    try:
        return parse_git_log(raw), "git"
    except Exception:
        return [], "unavailable"


# Computed once at import: the version identifies the deployed code, so it only
# changes on restart anyway, and no request should pay for a subprocess.
_BUILD_STAMP, VERSION_SOURCE = _build_stamp()
VERSION = "{}.{}".format(APP_VERSION, _BUILD_STAMP or "unknown")
WHATS_NEW, WHATS_NEW_SOURCE = _whats_new_entries()


def _num(v) -> Optional[float]:
    """float, or None for anything not finite.

    REGIME is a dict of scalars, so it is not covered by _safe_floats (series
    only), and Starlette renders with allow_nan=False: one NaN 500s the whole
    backtest. None is also the honest answer here, since a missing moving average
    is not a flat one.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f


def _safe_floats(series: pd.Series) -> list:
    """Convert a pandas Series to a JSON-safe list (NaN → 0.0)."""
    return [0.0 if pd.isna(v) else float(v) for v in series]


def _drawdown(returns: pd.Series) -> list:
    eq = (1 + returns.fillna(0)).cumprod()
    dd = eq / eq.cummax() - 1
    return _safe_floats(dd)


@app.post("/api/backtest")
def run_backtest(params: BacktestParams) -> dict:
    try:
        return _run_backtest(params)
    finally:
        if params.runId:
            PROGRESS.pop(params.runId, None)


def _run_backtest(params: BacktestParams) -> dict:
    if not params.holds:
        raise HTTPException(status_code=400, detail="At least one hold period is required.")

    status_messages: list[str] = []
    def on_status(msg: str) -> None:
        status_messages.append(msg)
        if params.runId:
            PROGRESS.setdefault(params.runId, []).append(msg)

    t_start = time.time()
    end = datetime.today()
    start = end - timedelta(days=int(params.lookback * 365.25) + 90)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    try:
        universe_tickers, universe_meta = data.get_universe(params.universe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Universe fetch failed: {exc}")

    if params.topN >= len(universe_tickers):
        raise HTTPException(
            status_code=400,
            detail=(
                f"topN={params.topN} exceeds universe size ({len(universe_tickers)}) "
                f"for {params.universe!r}. Try topN ≤ {len(universe_tickers) - 1}."
            ),
        )

    try:
        tickers = universe_tickers + [params.benchmark]
        prices = data.get_prices(tickers, start_str, end_str, on_status=on_status)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Data fetch failed: {exc}")

    if params.benchmark not in prices.columns:
        suggestion = universes.DEFAULT_BENCHMARK.get(params.universe, "SPY")
        raise HTTPException(
            status_code=400,
            detail=(
                f"Benchmark {params.benchmark!r} returned no data from Yahoo. "
                f"Try {suggestion} or another widely-listed ETF."
            ),
        )

    benchmark_prices = prices[params.benchmark]
    constituents = prices.drop(columns=[params.benchmark])
    monthly = data.to_monthly_returns(constituents)
    benchmark_monthly = benchmark_prices.resample("ME").last().pct_change(fill_method=None).dropna()

    # Coverage status: how many tickers have a full-window history?
    if len(constituents.columns):
        first_bars = constituents.apply(lambda c: c.first_valid_index())
        full_history = first_bars.dropna() <= constituents.index[0] + pd.Timedelta(days=14)
        n_full = int(full_history.sum())
        n_total = int(len(first_bars.dropna()))
        if n_total and n_full < n_total:
            late_starters = sorted(first_bars[~full_history].dropna().index.tolist())[:6]
            on_status(
                f"{params.universe}: {n_full}/{n_total} tickers cover the full window; "
                f"late starters: {', '.join(late_starters)}"
                + ("…" if len(late_starters) == 6 else "")
            )

    # ---- Event study ----
    events = analysis.event_study(
        monthly, top_n=params.topN, rank_lookback=params.rankLookback, horizon=params.horizon,
        skip_months=params.skipMonths, benchmark_monthly=benchmark_monthly,
    )
    summary = analysis.event_study_summary(events, benchmark_monthly)

    # ---- Strategy backtests ----
    # Net of trading costs is the headline series, because that is what the
    # strategy would actually have cleared; gross is carried alongside so the
    # cost can be seen rather than assumed. costBps=0 makes the two identical.
    detail: dict[int, pd.DataFrame] = {}
    strategy_returns: dict[int, pd.Series] = {}
    gross_returns: dict[int, pd.Series] = {}
    signal = analysis.momentum_signal(monthly, params.rankLookback, params.skipMonths)
    for h in params.holds:
        d = analysis.momentum_backtest_detail(
            monthly, top_n=params.topN, hold_period=h, rank_lookback=params.rankLookback,
            skip_months=params.skipMonths, cost_bps=params.costBps, signal=signal,
        )
        detail[h] = d
        strategy_returns[h] = d["net"]
        gross_returns[h] = d["gross"]

    # Regime: the mask is a decision made AT each month-end, so apply_regime
    # shifts it before touching the following month's return. Computed even when
    # the filter is off, because the banner is pure diagnostics.
    regime_mask = analysis.regime_mask(benchmark_prices, monthly.index,
                                       ma_days=params.regimeMaDays)
    filtered_returns: dict[int, pd.Series] = {}
    if params.regimeMode != "off":
        # A month the benchmark cannot price must not become a NaN hole in the
        # filtered sleeve. perf_stats drops NaN, so a sleeve reduced to one
        # observation gets NaN vol and Sharpe, and NaN in the response is a 500.
        # Holding cash through an unpriceable month is also the honest reading:
        # "no benchmark data" is not "the benchmark returned something unknown".
        repl = (0.0 if params.regimeMode == "cash"
                else benchmark_monthly.reindex(monthly.index).fillna(0.0))
        for h, r in strategy_returns.items():
            filtered_returns[h] = analysis.apply_regime(r, regime_mask, repl)

    if not strategy_returns or all(s.empty for s in strategy_returns.values()):
        raise HTTPException(
            status_code=400,
            detail="Backtest produced no data. Try a longer lookback or fewer rank_lookback months.",
        )

    # Align benchmark to strategy index. Don't silently fabricate 0% months —
    # if the benchmark genuinely lacks a month the strategy has, surface it.
    first_returns = next(iter(strategy_returns.values()))
    bench_aligned = benchmark_monthly.reindex(first_returns.index)
    n_missing = int(bench_aligned.isna().sum())
    if n_missing:
        on_status(
            f"Benchmark {params.benchmark}: {n_missing} month(s) missing in the "
            f"strategy window — treated as flat (0%) for alignment."
        )
    bench_aligned = bench_aligned.fillna(0.0)

    # ---- Build response ----
    months_iso = [d.strftime("%Y-%m-%d") for d in first_returns.index]

    equity = {"months": months_iso}
    equity_gross = {"months": months_iso}
    drawdown = {}
    monthly_returns_out = {}
    turnover_out = {"months": months_iso}
    for h, r in strategy_returns.items():
        eq = (1 + r.fillna(0)).cumprod()
        equity[f"h{h}m"] = _safe_floats(eq)
        equity_gross[f"h{h}m"] = _safe_floats((1 + gross_returns[h].fillna(0)).cumprod())
        drawdown[f"h{h}m"] = _drawdown(r)
        monthly_returns_out[f"h{h}m"] = _safe_floats(r)
        turnover_out[f"h{h}m"] = _safe_floats(detail[h]["turnover"])
    for h, fr in filtered_returns.items():
        fr = fr.reindex(first_returns.index)
        equity[f"h{h}m_filt"] = _safe_floats((1 + fr.fillna(0)).cumprod())
        drawdown[f"h{h}m_filt"] = _drawdown(fr)
        monthly_returns_out[f"h{h}m_filt"] = _safe_floats(fr)

    bench_eq = (1 + bench_aligned).cumprod()
    equity["spy"] = _safe_floats(bench_eq)
    equity_gross["spy"] = _safe_floats(bench_eq)   # no trading, so gross == net
    drawdown["spy"] = _drawdown(bench_aligned)
    monthly_returns_out["spy"] = _safe_floats(bench_aligned)

    perf_rows = []
    for h, r in strategy_returns.items():
        d = detail[h]
        s = analysis.perf_stats(
            r, risk_free_annual=params.riskFree,
            turnover=d["turnover"], cost=d["cost"],
        )
        g = analysis.perf_stats(gross_returns[h], risk_free_annual=params.riskFree)
        perf_rows.append({
            "strategy": f"top{params.topN}_hold{h}m",
            # The label is deliberately NOT suffixed for filtered rows: both
            # dashboards derive bestKey from an anchored /^top\d+_hold(\d+m)$/,
            # and a suffix would silently blank the drawdown chart and heatmap.
            # They distinguish the rows by this flag instead.
            "filtered": False,
            **{k: float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v
               for k, v in s.items()},
            # Gross counterparts, so the UI can show what the costs took.
            "gross_total_return": float(g["total_return"]),
            "gross_CAGR": float(g["CAGR"]),
            "gross_Sharpe": float(g["Sharpe"]),
            "gross_max_drawdown": float(g["max_drawdown"]),
            "cost_drag": float(g["CAGR"] - s["CAGR"]),
        })
    for h, fr in filtered_returns.items():
        s = analysis.perf_stats(fr, risk_free_annual=params.riskFree)
        perf_rows.append({
            "strategy": f"top{params.topN}_hold{h}m",
            "filtered": True,
            # Through _num, not a bare float(): a filtered sleeve can be reduced
            # to a single observation, whose vol and Sharpe are NaN, and NaN
            # anywhere in the response is a 500.
            **{k: (_num(v) if isinstance(v, (float, np.floating)) else v)
               for k, v in s.items()},
        })
    bench_stats = analysis.perf_stats(bench_aligned, risk_free_annual=params.riskFree)
    perf_rows.append({
        "strategy": params.benchmark,
        "filtered": False,
        **{k: float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v
           for k, v in bench_stats.items()},
    })

    event_study_rows = []
    for h_idx in summary.index:
        row = summary.loc[h_idx]
        event_study_rows.append({
            "h": int(h_idx),
            "avg_return": float(row["avg_return"]),
            "win_rate": float(row["win_rate"]) if not pd.isna(row["win_rate"]) else 0.0,
            "alpha": float(row["alpha_vs_bench"]) if not pd.isna(row["alpha_vs_bench"]) else 0.0,
            "t_stat": float(row["t_stat"]) if not pd.isna(row["t_stat"]) else 0.0,
            "n": int(row["n_cohorts"]),
        })

    cohorts = analysis.recent_picks(
        monthly, top_n=params.topN, rank_lookback=params.rankLookback, n_cohorts=6,
        skip_months=params.skipMonths,
    )
    # The newest cohort's forward month may still be in progress — its
    # return_pct/pick_returns are then month-to-date, so flag it for the UI.
    if (len(monthly.index) >= 2 and constituents.index.max() < monthly.index[-1]
            and cohorts and cohorts[0]["month"] == monthly.index[-2].strftime("%Y-%m")):
        cohorts[0]["fwd_partial"] = True

    # ---- Rolling Sharpe (12m) for each strategy + benchmark ----
    rolling_sharpe_out: dict = {"months": months_iso}
    for h, r in strategy_returns.items():
        rs = analysis.rolling_sharpe(
            r.reindex(first_returns.index), risk_free_annual=params.riskFree
        ).fillna(0)
        rolling_sharpe_out[f"h{h}m"] = _safe_floats(rs)
    for h, fr in filtered_returns.items():
        rolling_sharpe_out[f"h{h}m_filt"] = _safe_floats(analysis.rolling_sharpe(
            fr.reindex(first_returns.index), risk_free_annual=params.riskFree).fillna(0))
    rolling_sharpe_out["spy"] = _safe_floats(
        analysis.rolling_sharpe(bench_aligned, risk_free_annual=params.riskFree).fillna(0)
    )

    # ---- Regime banner payload ----
    # Picked by the SAME rule both dashboards use for bestStrat (highest Sharpe
    # among unfiltered non-benchmark rows). If the two ever drift, the banner
    # would attribute its month-to-date numbers to a sleeve the header is not
    # showing, so the client-side selectors carry a matching comment.
    unfiltered = [p for p in perf_rows
                  if p["strategy"] != params.benchmark and not p["filtered"]]
    best_row = max(unfiltered, key=lambda p: p["Sharpe"], default=None)
    best_h = int(best_row["strategy"].rsplit("_hold", 1)[1][:-1]) if best_row else None

    regime = analysis.regime_snapshot(benchmark_prices, ma_days=params.regimeMaDays)
    regime["mode"] = params.regimeMode
    regime["vol"] = analysis.realized_vol_percentile(benchmark_prices)
    # Warm-up months that actually reach a sleeve, on the strategy's own index.
    # Counting NA across the whole monthly index would include the ranking
    # warm-up months the backtest never traded, so the banner would claim
    # unfiltered exposure the filtered curve does not have and contradict
    # conditional.warmup.n in the same payload.
    regime["warmup_months"] = int(
        regime_mask.shift(1).reindex(first_returns.index).isna().sum())
    regime["coverage_months"] = int(regime_mask.notna().sum())
    # Month-END labels where the signal read risk-off, i.e. DECISION dates: an
    # entry of "2022-02" means 2022-02 closed below the average, so 2022-03 is
    # the month that gets replaced. Named _month_ends because "risk_off_months"
    # reads like "the months skipped", and that off-by-one is the whole timing
    # bug this design avoids.
    regime["risk_off_month_ends"] = [
        d.strftime("%Y-%m") for d in regime_mask.index[regime_mask.eq(False).fillna(False)]
    ]
    regime["mtd"] = None
    regime["conditional"] = None
    if best_h is not None:
        sleeve = strategy_returns[best_h].dropna()
        # The in-progress month is reported separately as mtd. Folding it into the
        # historical buckets would count the same partial return as both the
        # evidence and the event it is evidence about, and it is usually the
        # extreme observation that put the banner in that state to begin with.
        history = (sleeve.iloc[:-1]
                   if len(sleeve) and constituents.index.max() < sleeve.index[-1]
                   else sleeve)
        regime["conditional"] = analysis.regime_conditional_stats(
            history, regime_mask, benchmark_monthly)
        if len(sleeve):
            label = sleeve.index[-1]
            regime["mtd"] = {
                "sleeve": f"h{best_h}m",
                "strategy": _num(sleeve.iloc[-1]),
                "benchmark": _num(benchmark_monthly.get(label)),
                "spread": _num(sleeve.iloc[-1] - benchmark_monthly.get(label, np.nan)),
                "as_of": constituents.index.max().strftime("%Y-%m-%d"),
                # True when the newest monthly bar is still in progress, the same
                # test the cohort rows already use for fwd_partial.
                "is_partial": bool(constituents.index.max() < label),
            }

    # ---- Cohort group breakdown (sector or country/region per universe) ----
    sector_map = {t: m.get("sector", "") for t, m in universe_meta.items()}
    cohort_groups = analysis.cohort_sector_breakdown(
        monthly,
        top_n=params.topN,
        rank_lookback=params.rankLookback,
        sector_map=sector_map,
        n_cohorts=12,
        skip_months=params.skipMonths,
    )

    # Attach group labels to recent picks for ETF-universe chips.
    for c in cohorts:
        c["picks_meta"] = [
            {"ticker": t, "group": sector_map.get(t, "")} for t in c["picks"]
        ]

    # ---- Monthly rebalance trade list (1-month-hold sleeve) ----
    # The top row is a live target ranked on a trailing window ending on the
    # latest bar (same signal as Top Performers), not the partial calendar month;
    # history rows below are the month-end sleeve, unchanged.
    rebalance = analysis.live_rebalance(
        constituents, monthly, top_n=params.topN,
        rank_lookback=params.rankLookback, n_months=13,
        skip_months=params.skipMonths,
    )
    for r in rebalance:
        tickers_in_row = set(r["buys"]) | set(r["sells"]) | set(r["holds"])
        r["meta"] = {t: sector_map.get(t, "") for t in tickers_in_row}
        # What this single rebalance costs. In an equal-weight top-N book each
        # name carries 1/N, so selling S and buying B trades (B+S)/N of notional
        # and is billed costBps on each side of it.
        if params.topN > 0:
            traded = (len(r["buys"]) + len(r["sells"])) / params.topN
            r["turnover"] = traded / 2.0
            r["cost_pct"] = params.costBps / 10000.0 * traded

    # ---- Earnings dates for the live trade list ----
    # Scoped to the names on the live row only, never the universe, and never the
    # history rows (a forward report date is meaningless on a past month). Optional
    # decoration: any failure, including no network at all, just means no badge.
    live_row = rebalance[0] if rebalance else None
    if live_row is not None and live_row.get("is_live"):
        watch = list(live_row.get("buys", [])) + list(live_row.get("holds", []))
        # Cache only here. This runs inline in a synchronous handler, so a cold
        # fetch would add seconds to a response that otherwise reports 0.3s, for
        # decoration. The client calls POST /api/earnings after it has rendered,
        # which is the path allowed to touch the network.
        try:
            dates = earnings.next_earnings(watch, allow_network=False)
        except Exception as exc:
            dates = {}
            on_status(f"Earnings dates unavailable ({exc}); badges omitted.")
        live_row["earnings"] = {t: d for t, d in dates.items() if d}
        live_row["earnings_watch"] = watch
        # The rebalance is monthly, so the decision-relevant question is whether a
        # name reports before the next month-end, while you would still be holding
        # what you buy today. The UI styles those as warnings.
        as_of = pd.Timestamp(live_row.get("as_of") or constituents.index.max())
        live_row["earnings_warn_before"] = (
            (as_of + pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d"))

    # ---- Calendar-year (year-over-year) returns per strategy + benchmark ----
    yearly_returns_out: dict = {}
    for h, r in strategy_returns.items():
        yearly_returns_out[f"h{h}m"] = analysis.calendar_year_returns(r)
    for h, fr in filtered_returns.items():
        yearly_returns_out[f"h{h}m_filt"] = analysis.calendar_year_returns(fr)
    yearly_returns_out["spy"] = analysis.calendar_year_returns(bench_aligned)

    return {
        "PERF": perf_rows,
        "EVENT_STUDY": event_study_rows,
        "EQUITY": equity,
        "EQUITY_GROSS": equity_gross,
        "TURNOVER": turnover_out,
        "COST_BPS": float(params.costBps),
        "DRAWDOWN": drawdown,
        "MONTHLY_RETURNS": monthly_returns_out,
        "YEARLY_RETURNS": yearly_returns_out,
        "ROLLING_SHARPE": rolling_sharpe_out,
        "REGIME": regime,
        "COHORT_GROUPS": cohort_groups,
        "RECENT_COHORTS": cohorts,
        "REBALANCE": rebalance,
        "BENCHMARK_AVG_MONTHLY": float(benchmark_monthly.mean()),
        "UNIVERSE_SIZE": int(constituents.shape[1]),
        "UNIVERSE_KEY": params.universe,
        "UNIVERSE_LABEL": universes.UNIVERSE_LABEL[params.universe],
        "META_LABEL": universes.META_LABEL[params.universe],
        "elapsed_s": round(time.time() - t_start, 2),
        "status_messages": status_messages,
    }


@app.post("/api/top-performers")
def top_performers(params: TopPerformersParams) -> dict:
    try:
        return _top_performers(params)
    finally:
        if params.runId:
            PROGRESS.pop(params.runId, None)


def _top_performers(params: TopPerformersParams) -> dict:
    """Top S&P 500 tickers by simple return over the last N calendar days."""
    status_messages: list[str] = []
    def on_status(msg: str) -> None:
        status_messages.append(msg)
        if params.runId:
            PROGRESS.setdefault(params.runId, []).append(msg)

    t_start = time.time()
    end = datetime.today()
    # Pull at least ~1 year so the 52w-high / trailing-return / sparkline /
    # streak context columns are populated (the parquet cache usually already
    # covers this from a backtest run, so it's a slice, not a re-download).
    start = end - timedelta(days=max(params.days + 5, 375))
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    try:
        tickers, meta = data.get_universe(params.universe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Universe fetch failed: {exc}")

    if params.topN >= len(tickers):
        raise HTTPException(
            status_code=400,
            detail=(
                f"topN={params.topN} exceeds universe size ({len(tickers)}) "
                f"for {params.universe!r}. Try topN ≤ {len(tickers) - 1}."
            ),
        )

    try:
        prices = data.get_prices(tickers, start_str, end_str, on_status=on_status)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Data fetch failed: {exc}")

    # "Bubbling under": the next ~10 ranks past the leaderboard cutoff.
    watch_n = min(10, max(0, len(tickers) - params.topN))
    results = analysis.top_performers_period(
        prices, top_n=params.topN, days=params.days, watch_n=watch_n,
    )

    if not results:
        raise HTTPException(
            status_code=400,
            detail="No data available for the requested window. Try a larger `days` value.",
        )

    def enrich(row: dict, rank: int) -> dict:
        info = meta.get(row["ticker"], {})
        prior = row.get("prior_rank")
        return {
            "rank": rank,
            "ticker": row["ticker"],
            "return_pct": row["return_pct"],
            "start_price": row["start_price"],
            "end_price": row["end_price"],
            "name": info.get("name", ""),
            "sector": info.get("sector", ""),
            # signal-quality context (None where history is too short)
            "vol_ann": row.get("vol_ann"),
            "max_day_move": row.get("max_day_move"),
            "below_52w_high": row.get("below_52w_high"),
            "ret_3m": row.get("ret_3m"),
            "ret_6m": row.get("ret_6m"),
            "ret_12m": row.get("ret_12m"),
            "spark": row.get("spark"),
            "streak": row.get("streak"),
            "prior_rank": prior,
            "rank_delta": (prior - rank) if prior is not None else None,  # + = moved up
            "new_entrant": row.get("new_entrant"),
        }

    enriched = [enrich(row, rank) for rank, row in enumerate(results[:params.topN], start=1)]
    watchlist = [enrich(row, rank) for rank, row
                 in enumerate(results[params.topN:], start=params.topN + 1)]

    actual_start = pd.Timestamp(results[0]["start_date"])
    actual_end = pd.Timestamp(results[0]["end_date"])

    return {
        "results": enriched,
        "watchlist": watchlist,
        "window": {
            "start_date": results[0]["start_date"],
            "end_date": results[0]["end_date"],
            "days_requested": params.days,
            "days_actual": (actual_end - actual_start).days,
        },
        "universe_size": int(prices.shape[1]),
        "universe_key": params.universe,
        "universe_label": universes.UNIVERSE_LABEL[params.universe],
        "meta_label": universes.META_LABEL[params.universe],
        "elapsed_s": round(time.time() - t_start, 2),
        "status_messages": status_messages,
    }


@app.get("/api/progress/{run_id}")
def progress(run_id: str) -> dict:
    """Status messages collected so far for an in-flight run (polled by the UI)."""
    return {"messages": PROGRESS.get(run_id, [])}


@app.get("/api/universes")
def universe_info() -> dict:
    """Universe sizes + parameter ranges — single source of truth for client-side validation."""
    out: dict = {
        "ranges": {
            "lookback": {"min": 1, "max": 25},
            "topN": {"min": 1, "max": 100},
            "rankLookback": {"min": 1, "max": 12},
            "skipMonths": {"min": 0, "max": 2},
            "costBps": {"min": 0, "max": 100},
            "regimeMaDays": {"min": 20, "max": 400},
            "regimeMode": {"values": ["off", "cash", "benchmark"]},
            "horizon": {"min": 1, "max": 24},
        },
        "universes": {},
    }
    for key in ("sp500", "global_etfs", "us_sector_etfs"):
        try:
            tickers, _ = data.get_universe(key)
            size = len(tickers)
        except Exception:
            # Wikipedia hiccup on a cold ticker cache must never block UI validation.
            size = None
        # Cap at both the universe size (server rejects topN >= size) and the
        # hard parameter limit the /api/backtest endpoint enforces (topN <= 100),
        # so the advertised ceiling matches what will actually be accepted.
        topn_hard_max = out["ranges"]["topN"]["max"]
        out["universes"][key] = {
            "label": universes.UNIVERSE_LABEL[key],
            "size": size,
            "max_topN": (min(size - 1, topn_hard_max) if size else None),
            "default_benchmark": universes.DEFAULT_BENCHMARK.get(key, "SPY"),
        }
    return out


@app.get("/api/version")
def version_info() -> dict:
    """Deployed build stamp. `source` lets the UI be honest about provenance."""
    return {"version": VERSION, "source": VERSION_SOURCE}


@app.get("/api/whats-new")
def whats_new() -> dict:
    """Commits from WHATS_NEW_SINCE onward, newest first."""
    return {"entries": WHATS_NEW, "since": WHATS_NEW_SINCE, "source": WHATS_NEW_SOURCE}


class EarningsParams(BaseModel):
    # Capped well below the module's own MAX_SYMBOLS so a crafted request cannot
    # turn into hundreds of per-ticker calls.
    tickers: List[str] = Field(default_factory=list, max_length=40)


@app.post("/api/earnings")
def earnings_dates(params: EarningsParams) -> dict:
    """Next earnings date per ticker. The one path allowed to hit the network.

    Called by the client after the dashboard has rendered, so a cold fetch never
    delays the backtest itself. Failures are reported as an empty mapping rather
    than an error status: the badges are decoration and must never look broken.
    """
    tickers = [t.strip().upper() for t in params.tickers if t and t.strip()]
    if not tickers:
        return {"earnings": {}, "source": "empty"}
    try:
        dates = earnings.next_earnings(tickers)
    except Exception as exc:
        return {"earnings": {}, "source": "unavailable", "detail": str(exc)}
    return {"earnings": {t: d for t, d in dates.items() if d}, "source": "ok"}


@app.get("/api/cache")
def cache_info() -> Optional[dict]:
    return cache.coverage()


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    cache.clear()
    return {"ok": True}


# ---- out-of-sample ledger ----

# Recording prefers the price cache; it only fetches when the cache is missing or
# its last bar is older than this. Record is clicked right after a run, so the
# cache path is the normal one and the click never triggers a 500-ticker download.
LEDGER_CACHE_STALE_DAYS = 5

# Matches the shortest allowed backtest pull (lookback=1), so a record-triggered
# fetch warms exactly the cache a backtest would and still covers the widest
# ranking window (426 days at rankLookback=12 plus skipMonths=2).
LEDGER_FETCH_DAYS = 455

LEDGER_MAX_WARNINGS = 12

_UNIVERSE_SOURCE_WARNING = {
    "stale_cache": (
        "Universe list came from the stale on-disk cache (the membership scrape "
        "failed), so it may be weeks out of date."
    ),
}


def _assert_json_clean(payload: dict, what: str) -> dict:
    """Fail loudly here rather than as an opaque 500 from Starlette's renderer,
    which dumps with allow_nan=False and rejects any NaN or inf in the tree."""
    try:
        json.dumps(payload, allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=500,
                            detail=f"{what} payload is not JSON-clean: {exc}")
    return payload


def _record_universe(key: str, benchmark: str) -> Tuple[List[str], str]:
    """(tickers, source) for the record path, without letting a scrape outage win.

    ``get_sp500_tickers`` returns the disk cache only inside its 24h TTL and
    scrapes Wikipedia after that, so on an airgapped box the first Record click a
    day after the last successful scrape would 502 with a perfectly usable price
    cache sitting right there. The stale-cache fallback is recorded on the entry,
    because a 3-month-stale membership list is a real (if small) drift in what
    was ranked.
    """
    if key != "sp500":
        try:
            tickers, _meta = data.get_universe(key)   # module constants, no network
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return tickers, "hardcoded"

    try:
        return data.get_sp500_tickers(), "wikipedia"
    except Exception:
        pass
    stale = cache.load_tickers(stale_ok=True)
    if stale:
        return stale, "stale_cache"
    # There is deliberately no fall back to the price cache's own columns.
    # cache/prices.parquet is ONE shared table holding every universe and every
    # benchmark ever fetched, so handing its column list back as "sp500" would
    # rank global and sector ETFs alongside S&P names and break the guarantee
    # that a recorded target equals the leaderboard (I1). Refusing is the honest
    # outcome: a wrong universe silently recorded is worse than a clear failure.
    raise HTTPException(
        status_code=502,
        detail=("Universe unavailable: the membership scrape failed and there is no "
                "cached ticker list to fall back on. Run a backtest first to "
                "populate the membership cache, then record."),
    )


def _today() -> date:
    """The server date. A seam, so tests can pin the calendar."""
    return datetime.today().date()


def _entries_for_hash(p_hash: str) -> List[dict]:
    """Recorded entries of one settings group whose as_of parses as a date."""
    out = []
    for e in ledger.load():
        if e.get("params_hash") != p_hash:
            continue
        try:
            pd.Timestamp(e.get("as_of"))
        except Exception:
            continue
        if e.get("as_of"):
            out.append(e)
    return out


def _ledger_entry(target: dict, constituents: pd.DataFrame, frame: pd.DataFrame,
                  benchmark: str, entry_params: dict, universe_source: str,
                  note: Optional[str], extra: Optional[dict] = None) -> dict:
    """A ledger entry for ``target``, priced at the target's own as_of bar."""
    as_of_ts = pd.Timestamp(target["as_of"])
    entry_prices: dict[str, float | None] = {}
    entry_unpriced: list[str] = []
    at_as_of = (constituents.loc[as_of_ts]
                if as_of_ts in constituents.index else None)
    for t in target["tickers"]:
        v = None if at_as_of is None else at_as_of.get(t)
        # None, never NaN: a NaN here would 500 the response, and copying the
        # signal_end price would pass off an older bar as the as_of price.
        price = None if v is None or pd.isna(v) else float(v)
        entry_prices[t] = price
        if price is None:
            entry_unpriced.append(t)

    # The benchmark column can lag the constituents (a NaN on the as_of bar is
    # normal), and scoring is locked to the benchmark recorded here, so taking
    # only the as_of bar would leave the entry permanently unscoreable. Fall back
    # to the benchmark's own last valid close at or before as_of and record which
    # date it came from, rather than silently passing it off as the as_of price.
    bench_price = None
    bench_price_date = None
    if benchmark in frame.columns:
        bench_col = frame[benchmark].loc[:as_of_ts].dropna()
        if len(bench_col):
            bench_price = float(bench_col.iloc[-1])
            bench_price_date = bench_col.index[-1].strftime("%Y-%m-%d")

    entry = {
        "params_hash": ledger.params_hash(entry_params),
        "signal_version": ledger.SIGNAL_VERSION,
        "params": entry_params,
        "benchmark": benchmark,
        "as_of": target["as_of"],
        "signal_end": target["signal_end"],
        "window_start": target["start_date"],
        "days": target["days"],
        "recorded_at": (datetime.now(timezone.utc)
                        .isoformat(timespec="milliseconds").replace("+00:00", "Z")),
        "tickers": target["tickers"],
        "entry_prices": entry_prices,
        "entry_unpriced": entry_unpriced,
        "benchmark_entry_price": bench_price,
        "benchmark_entry_date": bench_price_date,
        "universe_source": universe_source,
        "note": note,
    }
    entry.update(extra or {})
    return entry


def _ledger_label(entry: dict) -> str:
    p = entry.get("params") or {}
    parts = [
        universes.UNIVERSE_LABEL.get(p.get("universe"), str(p.get("universe"))),
        f"top {p.get('topN')}",
        f"{p.get('rankLookback')}m rank",
    ]
    if p.get("skipMonths"):
        parts.append(f"skip {p['skipMonths']}m")
    return " · ".join(parts)


@app.post("/api/ledger/record")
def record_ledger_target(params: LedgerRecordParams) -> dict:
    try:
        return _record_ledger_target(params)
    finally:
        if params.runId:
            PROGRESS.pop(params.runId, None)


def _record_ledger_target(params: LedgerRecordParams) -> dict:
    """Recompute the live target server-side and append it to the ledger.

    The client supplies settings only, never tickers or prices, so a stale tab
    cannot inject a cohort into the record.
    """
    status_messages: list[str] = []
    def on_status(msg: str) -> None:
        status_messages.append(msg)
        if params.runId:
            PROGRESS.setdefault(params.runId, []).append(msg)

    warnings: list[str] = []

    universe_tickers, universe_source = _record_universe(
        params.universe, params.benchmark)
    if universe_source in _UNIVERSE_SOURCE_WARNING:
        warnings.append(_UNIVERSE_SOURCE_WARNING[universe_source])
        on_status(_UNIVERSE_SOURCE_WARNING[universe_source])

    # Same guard /api/backtest applies: without it a topN=50 record against an
    # 11-name universe would store an 11-ticker cohort under a hash claiming 50,
    # and two entries under one hash could hold different-length cohorts.
    if params.topN >= len(universe_tickers):
        raise HTTPException(
            status_code=400,
            detail=(
                f"topN={params.topN} exceeds universe size ({len(universe_tickers)}) "
                f"for {params.universe!r}. Try topN ≤ {len(universe_tickers) - 1}."
            ),
        )

    entry_params = {
        "universe": params.universe,
        "topN": params.topN,
        "rankLookback": params.rankLookback,
        "skipMonths": params.skipMonths,
    }
    recorded = _entries_for_hash(ledger.params_hash(entry_params))
    newest_as_of = max((e["as_of"] for e in recorded), default=None)

    # A backfill ranks each missed month on the window that ends at that month,
    # so a refresh must reach back past the oldest month it may fill, not just
    # past today's window.
    fetch_days = LEDGER_FETCH_DAYS
    if newest_as_of and params.gapPolicy != "leave":
        pending = analysis.ledger_missed_months(
            newest_as_of, _today().isoformat())[-analysis.LEDGER_BACKFILL_MAX_MONTHS:]
        if pending:
            first = pd.Period(pending[0], "M").start_time
            reach = ((pd.Timestamp(_today()) - first).days + 10
                     + analysis.signal_window_days(params.rankLookback, params.skipMonths))
            fetch_days = max(fetch_days, int(reach))

    # Dedupe: a benchmark that is itself a universe member (SPY under a custom or
    # ETF universe) would otherwise appear twice, and a duplicated label makes
    # frame[col] return a DataFrame instead of a Series, which raises deep inside
    # the ranking rather than here.
    wanted = list(dict.fromkeys(universe_tickers + [params.benchmark]))
    frame = cache.load_prices(wanted)
    price_source = "cache"
    stale = True
    if frame is not None and not frame.empty:
        age = (pd.Timestamp(datetime.today().date()) - frame.index.max()).days
        # The frame's last bar can come from a few tickers only, so also check
        # whether the universe itself reaches it. get_prices fills such a gap.
        lagging = cache.lagging_tickers(
            frame, [t for t in universe_tickers if t != params.benchmark])
        stale = age > LEDGER_CACHE_STALE_DAYS or bool(lagging)
    if frame is None or frame.empty or stale:
        end = datetime.today()
        start = end - timedelta(days=fetch_days)
        try:
            frame = data.get_prices(wanted, start.strftime("%Y-%m-%d"),
                                    end.strftime("%Y-%m-%d"), on_status=on_status)
            price_source = "fetched"
        except Exception as exc:
            if frame is None or frame.empty:
                raise HTTPException(status_code=502,
                                    detail=f"Price fetch failed and the cache is empty: {exc}")
            warnings.append(
                f"Price refresh failed ({exc}); recorded off the cached prices "
                f"through {frame.index.max():%Y-%m-%d}."
            )
            price_source = "cache (refresh failed)"

    # I1: the parquet is one shared table across all 3 universes plus every
    # benchmark ever requested, so narrow to this universe and drop the benchmark
    # before ranking — exactly what _run_backtest does before live_rebalance.
    # Handing the raw frame to current_target would let SPY into the top N and
    # rank an 11-name sector run over 504 S&P names.
    cols = [t for t in universe_tickers
            if t in frame.columns and t != params.benchmark]
    if not cols:
        raise HTTPException(
            status_code=409,
            detail=(f"No prices on hand for the {params.universe!r} universe. "
                    f"Run a backtest first."),
        )
    constituents = frame[cols]

    # top_performers_period has no reach-back tolerance: a frame shorter than the
    # ranking window silently collapses to a shorter one and a different top N.
    need_days = analysis.signal_window_days(params.rankLookback, params.skipMonths)
    span = int((constituents.index.max() - constituents.index.min()).days)
    if span < need_days:
        raise HTTPException(
            status_code=409,
            detail=(f"Price cache covers {span} days, this signal needs {need_days}: "
                    f"run a backtest first."),
        )

    target = analysis.current_target(
        constituents, top_n=params.topN,
        rank_lookback=params.rankLookback, skip_months=params.skipMonths,
    )
    if target is None:
        raise HTTPException(
            status_code=409,
            detail="Not enough price history to form a target: run a backtest first.",
        )
    if len(target["tickers"]) < params.topN:
        raise HTTPException(
            status_code=409,
            detail=(f"Only {len(target['tickers'])} of {params.topN} names have prices "
                    f"in this window; recording a short cohort would break the hash. "
                    f"Run a backtest first."),
        )

    # ---- missed month-ends ----
    # Measured against the target's own bar, not the wall clock: a month is only
    # "missed" once the price frame has moved past it.
    missed = (analysis.ledger_missed_months(newest_as_of, target["as_of"])
              if newest_as_of else [])
    backfill_entries: list[dict] = []
    if missed and params.gapPolicy != "leave":
        fillable = missed[-analysis.LEDGER_BACKFILL_MAX_MONTHS:]
        beyond_cap = missed[:len(missed) - len(fillable)]
        if any(not e.get("reconstructed") for e in recorded):
            recon = analysis.reconstruct_targets(
                constituents, fillable, top_n=params.topN,
                rank_lookback=params.rankLookback, skip_months=params.skipMonths)
        else:
            recon = [{"month": m, "as_of": None, "target": None,
                      "refused_reason": "no live entry exists for these settings"}
                     for m in fillable]

        if params.gapPolicy is None:
            return _assert_json_clean({
                "created": False,
                "entry": None,
                "reason": "gap",
                "as_of": target["as_of"],
                "newest_as_of": newest_as_of,
                "missed": missed,
                "beyond_cap": beyond_cap,
                "reconstructable": [
                    {"month": r["month"], "as_of": r["as_of"],
                     "ok": r["target"] is not None, "reason": r["refused_reason"]}
                    for r in recon],
                "target_matches_stored": True,
                "displayed_as_of_matches":
                    params.displayedAsOf is None or params.displayedAsOf == target["as_of"],
                "universe_source": universe_source,
                "price_source": price_source,
                "warnings": warnings,
                "status_messages": status_messages,
            }, "ledger record")

        usable = [r for r in recon if r["target"] is not None]
        if not usable:
            reasons = "; ".join(f"{r['month']}: {r['refused_reason']}" for r in recon)
            raise HTTPException(
                status_code=409,
                detail=(f"None of the missed months can be reconstructed ({reasons}). "
                        f"Nothing was written. Record with the gap instead."),
            )
        for r in recon:
            if r["target"] is None:
                warnings.append(f"{r['month']} was not reconstructed: {r['refused_reason']}.")
        if beyond_cap:
            warnings.append(
                f"{len(beyond_cap)} older month(s) are past the "
                f"{analysis.LEDGER_BACKFILL_MAX_MONTHS}-month backfill limit and stay as a gap.")
        stamp = (datetime.now(timezone.utc)
                 .isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        backfill_entries = [
            _ledger_entry(r["target"], constituents, frame, params.benchmark,
                          entry_params, universe_source, note=None,
                          extra={"reconstructed": True, "reconstructed_at": stamp,
                                 "universe_as_of": _today().isoformat()})
            for r in usable]

    entry = _ledger_entry(target, constituents, frame, params.benchmark,
                          entry_params, universe_source, note=params.note)

    try:
        if backfill_entries:
            # One write for the whole set: a half-filled gap is worse than either
            # outcome.
            ledger.append_many(backfill_entries + [entry])
            stored, created = entry, True
        else:
            stored, created = ledger.append(entry)
    except ledger.LedgerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write the ledger: {exc}")

    reason = None
    if not created:
        reason = ("already_recorded" if stored.get("as_of") == target["as_of"]
                  else "already_recorded_this_month")

    return _assert_json_clean({
        "created": created,
        "entry": stored,
        "reason": reason,
        "as_of": target["as_of"],
        # "Same bar, same settings" does not guarantee "same cohort": a split heal
        # between two clicks rebases a whole adjusted-close history and can
        # reorder the ranking on an unchanged bar. The stored entry is never
        # rewritten — it is what was recommended at the time — so the UI says the
        # stored cohort differs rather than showing a bare "Recorded".
        "target_matches_stored":
            list(stored.get("tickers") or []) == target["tickers"],
        "displayed_as_of_matches":
            params.displayedAsOf is None or params.displayedAsOf == target["as_of"],
        "universe_source": universe_source,
        "price_source": price_source,
        "warnings": warnings,
        "status_messages": status_messages,
        **({"backfilled": [e["as_of"] for e in backfill_entries]}
           if backfill_entries else {}),
    }, "ledger record")


@app.get("/api/ledger")
def ledger_history(
    params_hash: Optional[str] = Query(None, alias="hash"),
    refresh: int = Query(0, ge=0, le=1),
) -> dict:
    """Recorded targets marked to market, grouped by params_hash.

    Disk-only by default (``cache.load_prices``), so the panel renders airgapped.
    ``refresh=1`` is the one path that touches the network and is scoped to the
    recorded ticker union (tens of names, not 500); a failure degrades to the
    cache-only result plus a warning.

    Scoring is locked to the benchmark recorded with each group. There is no
    read-time override: re-scoring an existing record against a different
    benchmark would allow benchmark shopping after the fact.
    """
    entries = ledger.load()
    if not entries:
        return {"entries_total": 0, "groups": [], "price_source": "cache",
                "cache_coverage": cache.coverage(), "warnings": []}

    # ledger.json is a plain file a user may hand-edit, and every downstream date
    # comparison parses as_of. Skip unusable rows at READ time rather than in
    # ledger.load(), so one malformed row cannot take down the whole panel and is
    # also not silently erased the next time append() rewrites the file.
    unusable = 0
    usable: list[dict] = []
    for e in entries:
        try:
            pd.Timestamp(e.get("as_of"))
        except Exception:
            unusable += 1
            continue
        usable.append(e)
    entries = usable

    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e.get("params_hash") or "unknown", []).append(e)
    if params_hash:
        grouped = {k: v for k, v in grouped.items() if k == params_hash}

    wanted: list[str] = []
    for group in grouped.values():
        for e in group:
            for t in list(e.get("tickers") or []) + [e.get("benchmark")]:
                if t and t not in wanted:
                    wanted.append(t)

    warnings: list[str] = []
    if unusable:
        warnings.append(
            f"{unusable} ledger entr(y/ies) have an unreadable as_of date and were "
            f"skipped. The file was left untouched: fix or remove those rows in "
            f"userdata/ledger.json."
        )
    prices = cache.load_prices(wanted)
    price_source = "cache"
    if refresh and wanted:
        first = min((e.get("as_of") or "9999-12-31")
                    for group in grouped.values() for e in group)
        start = min(pd.Timestamp(first) - pd.Timedelta(days=10),
                    pd.Timestamp(datetime.today().date())
                    - pd.Timedelta(days=LEDGER_FETCH_DAYS))
        try:
            prices = data.get_prices(wanted, start.strftime("%Y-%m-%d"),
                                     datetime.today().strftime("%Y-%m-%d"))
            price_source = "refreshed"
        except Exception as exc:
            warnings.append(f"Price refresh failed ({exc}); showing cached prices.")
            price_source = "cache (refresh failed)"
    if prices is None:
        prices = pd.DataFrame()
        warnings.append("No price cache on disk, so nothing can be marked to "
                        "market yet. Run a backtest first.")

    groups: list[dict] = []
    frozen_seen: set[tuple] = set()
    for h, group in grouped.items():
        ordered = sorted(group, key=lambda e: e.get("as_of") or "")
        newest = ordered[-1]
        bench = newest.get("benchmark")
        recorded_benchmarks = sorted({e.get("benchmark") for e in ordered
                                      if e.get("benchmark")})
        if len(recorded_benchmarks) > 1:
            warnings.append(
                f"{_ledger_label(newest)}: entries were recorded against "
                f"{', '.join(recorded_benchmarks)}; the curve is scored against "
                f"{bench} (each row reports its own)."
            )
        marks = analysis.ledger_marks(ordered, prices, benchmark=bench)
        # The reminder runs on the server date, not the price frame: stale prices
        # are exactly the state it has to report.
        gap = analysis.ledger_gap(newest["as_of"], _today().isoformat())
        gap["prices_through"] = marks["summary"]["priced_through"]
        for row in marks["rows"]:
            for t in row["frozen_tickers"]:
                key = (t, row["frozen_at"].get(t))
                if key in frozen_seen:
                    continue
                frozen_seen.add(key)
                warnings.append(f"{t} has no bars after {key[1]}; frozen at its "
                                f"last close in the equal-weight denominator.")
        groups.append({
            "params_hash": h,
            "params": newest.get("params") or {},
            "signal_version": newest.get("signal_version"),
            "label": _ledger_label(newest),
            "benchmark": bench,
            "universe_sources": sorted({e.get("universe_source") for e in ordered
                                        if e.get("universe_source")}),
            "gap": gap,
            **marks,
        })

    groups.sort(key=lambda g: g["summary"]["first_as_of"] or "", reverse=True)
    if len(warnings) > LEDGER_MAX_WARNINGS:
        extra = len(warnings) - LEDGER_MAX_WARNINGS
        warnings = warnings[:LEDGER_MAX_WARNINGS] + [f"…and {extra} more."]

    return _assert_json_clean({
        "entries_total": len(entries),
        "price_source": price_source,
        "cache_coverage": cache.coverage(),
        "groups": groups,
        "warnings": warnings,
    }, "ledger")


# ---- static frontend ----

# The frontend has no build step (JSX is transpiled in-browser), so files change
# in place. Tell the browser to always revalidate via ETag — it gets fresh code
# the moment a file changes and a cheap 304 otherwise, instead of silently
# serving a stale cached copy after an edit.
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.get("/")
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", NoCacheStaticFiles(directory=WEB_DIR), name="web")
