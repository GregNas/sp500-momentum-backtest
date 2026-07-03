"""FastAPI backend for the momentum backtest dashboard.

Run:
    .venv/bin/uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lib import analysis, cache, data, universes

UniverseKey = Literal["sp500", "global_etfs", "us_sector_etfs"]

WEB_DIR = Path(__file__).parent / "web"

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
    horizon: int = Field(12, ge=1, le=24)
    benchmark: str = "SPY"
    riskFree: float = Field(0.0, ge=0.0, le=0.2)  # annual risk-free rate for Sharpe
    runId: Optional[str] = None


class TopPerformersParams(BaseModel):
    universe: UniverseKey = "sp500"
    topN: int = Field(10, ge=1, le=100)
    days: int = Field(30, ge=1, le=365)
    runId: Optional[str] = None


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
    )
    summary = analysis.event_study_summary(events, benchmark_monthly)

    # ---- Strategy backtests ----
    strategy_returns: dict[int, pd.Series] = {}
    for h in params.holds:
        r = analysis.momentum_backtest(
            monthly, top_n=params.topN, hold_period=h, rank_lookback=params.rankLookback,
        )
        strategy_returns[h] = r

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
    drawdown = {}
    monthly_returns_out = {}
    for h, r in strategy_returns.items():
        eq = (1 + r.fillna(0)).cumprod()
        equity[f"h{h}m"] = _safe_floats(eq)
        drawdown[f"h{h}m"] = _drawdown(r)
        monthly_returns_out[f"h{h}m"] = _safe_floats(r)

    bench_eq = (1 + bench_aligned).cumprod()
    equity["spy"] = _safe_floats(bench_eq)
    drawdown["spy"] = _drawdown(bench_aligned)
    monthly_returns_out["spy"] = _safe_floats(bench_aligned)

    perf_rows = []
    for h, r in strategy_returns.items():
        s = analysis.perf_stats(r, risk_free_annual=params.riskFree)
        perf_rows.append({
            "strategy": f"top{params.topN}_hold{h}m",
            **{k: float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v
               for k, v in s.items()},
        })
    bench_stats = analysis.perf_stats(bench_aligned, risk_free_annual=params.riskFree)
    perf_rows.append({
        "strategy": params.benchmark,
        **{k: float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v
           for k, v in bench_stats.items()},
    })

    event_study_rows = []
    for h_idx in summary.index:
        row = summary.loc[h_idx]
        event_study_rows.append({
            "h": int(h_idx),
            "avg_return": float(row["avg_return"]),
            "win_rate": float(row["win_rate"]),
            "alpha": float(row["alpha_vs_bench"]),
            "t_stat": float(row["t_stat"]) if not pd.isna(row["t_stat"]) else 0.0,
            "n": int(row["n_cohorts"]),
        })

    cohorts = analysis.recent_picks(
        monthly, top_n=params.topN, rank_lookback=params.rankLookback, n_cohorts=6,
    )

    # ---- Rolling Sharpe (12m) for each strategy + benchmark ----
    rolling_sharpe_out: dict = {"months": months_iso}
    for h, r in strategy_returns.items():
        rs = analysis.rolling_sharpe(
            r.reindex(first_returns.index), risk_free_annual=params.riskFree
        ).fillna(0)
        rolling_sharpe_out[f"h{h}m"] = _safe_floats(rs)
    rolling_sharpe_out["spy"] = _safe_floats(
        analysis.rolling_sharpe(bench_aligned, risk_free_annual=params.riskFree).fillna(0)
    )

    # ---- Cohort group breakdown (sector or country/region per universe) ----
    sector_map = {t: m.get("sector", "") for t, m in universe_meta.items()}
    cohort_groups = analysis.cohort_sector_breakdown(
        monthly,
        top_n=params.topN,
        rank_lookback=params.rankLookback,
        sector_map=sector_map,
        n_cohorts=12,
    )

    # Attach group labels to recent picks for ETF-universe chips.
    for c in cohorts:
        c["picks_meta"] = [
            {"ticker": t, "group": sector_map.get(t, "")} for t in c["picks"]
        ]

    # ---- Monthly rebalance trade list (1-month-hold sleeve) ----
    rebalance = analysis.rebalance_schedule(
        monthly, top_n=params.topN, rank_lookback=params.rankLookback, n_months=13,
    )
    for r in rebalance:
        tickers_in_row = set(r["buys"]) | set(r["sells"]) | set(r["holds"])
        r["meta"] = {t: sector_map.get(t, "") for t in tickers_in_row}

    # ---- Calendar-year (year-over-year) returns per strategy + benchmark ----
    yearly_returns_out: dict = {}
    for h, r in strategy_returns.items():
        yearly_returns_out[f"h{h}m"] = analysis.calendar_year_returns(r)
    yearly_returns_out["spy"] = analysis.calendar_year_returns(bench_aligned)

    return {
        "PERF": perf_rows,
        "EVENT_STUDY": event_study_rows,
        "EQUITY": equity,
        "DRAWDOWN": drawdown,
        "MONTHLY_RETURNS": monthly_returns_out,
        "YEARLY_RETURNS": yearly_returns_out,
        "ROLLING_SHARPE": rolling_sharpe_out,
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
    start = end - timedelta(days=params.days + 5)  # buffer for weekends/holidays
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

    results = analysis.top_performers_period(prices, top_n=params.topN, days=params.days)

    if not results:
        raise HTTPException(
            status_code=400,
            detail="No data available for the requested window. Try a larger `days` value.",
        )

    enriched = []
    for rank, row in enumerate(results, start=1):
        info = meta.get(row["ticker"], {})
        enriched.append({
            "rank": rank,
            "ticker": row["ticker"],
            "return_pct": row["return_pct"],
            "start_price": row["start_price"],
            "end_price": row["end_price"],
            "name": info.get("name", ""),
            "sector": info.get("sector", ""),
        })

    actual_start = pd.Timestamp(results[0]["start_date"])
    actual_end = pd.Timestamp(results[0]["end_date"])

    return {
        "results": enriched,
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


@app.get("/api/cache")
def cache_info() -> Optional[dict]:
    return cache.coverage()


@app.post("/api/cache/clear")
def clear_cache() -> dict:
    cache.clear()
    return {"ok": True}


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
