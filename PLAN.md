# PLAN: Local web app for the S&P 500 momentum backtest

## Context

`momentum_backtest.py` is a clean, single-file script that (a) runs an event study on monthly top-N S&P 500 cohorts and (b) backtests a Jegadeesh-Titman overlapping-sleeves strategy, then dumps PNGs and CSVs to `./momentum_output/`. Every run re-downloads ~500 tickers from Yahoo, which is the dominant latency and the main reason iterating on parameters from the CLI is painful.

We want to wrap it in a local single-user web app: a form to set params, a "Run" button, inline charts and tables, and a disk cache so the second run with the same date range is essentially instant.

---

## 1. Tech stack decision

**Streamlit.**

- Native widgets for every form field we need (`st.number_input`, `st.multiselect`, `st.text_input`), native rendering for matplotlib Figures (`st.pyplot`) and DataFrames (`st.dataframe`), and built-in status UI (`st.spinner`, `st.status`).
- Run command is one line (`streamlit run app.py`) and it ships its own dev server.
- Versus **Gradio**: Gradio is event/component-driven and tuned for ML demos; arranging two charts + two stats tables + a side-by-side compare panel is more awkward than Streamlit's top-down layout.
- Versus **FastAPI + HTMX**: gives more control but requires hand-rolling templates, JSON ↔ form glue, and chart embedding. Wrong tradeoff for a single-user local tool where you specifically asked for minimal frontend code.

---

## 2. Architecture

```
┌─────────────────────────────────────────────┐
│  Browser  (localhost:8501)                  │
│  - param form, charts, tables               │
└──────────────────┬──────────────────────────┘
                   │ HTTP (Streamlit)
┌──────────────────▼──────────────────────────┐
│  app.py            UI layer                 │
│  - form widgets                             │
│  - orchestration + status                   │
│  - st.session_state (presets, last run)     │
└──────────────────┬──────────────────────────┘
                   │ pure Python calls
┌──────────────────▼──────────────────────────┐
│  lib/  Backtest engine (pure functions)     │
│  ┌─────────────────────────────────────┐    │
│  │ analysis.py  event_study,           │    │
│  │              momentum_backtest,     │    │
│  │              perf_stats             │    │
│  ├─────────────────────────────────────┤    │
│  │ plotting.py  return Figure objects  │    │
│  ├─────────────────────────────────────┤    │
│  │ data.py      get_prices(...)  ──┐   │    │
│  └─────────────────────────────────┼───┘    │
└────────────────────────────────────┼────────┘
                                     │
┌────────────────────────────────────▼────────┐
│  lib/cache.py  Cache layer                  │
│  - read parquet, compute delta, write back  │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Disk         cache/ (parquet + meta.json)  │
│  Network      yfinance (delta only)         │
└─────────────────────────────────────────────┘
```

---

## 3. Module / file structure

```
sp500-momentum-backtest/
├── app.py                  # Streamlit UI: form, orchestration, rendering
├── momentum_backtest.py    # CLI entry — slimmed to import from lib/
├── lib/
│   ├── __init__.py
│   ├── data.py             # get_sp500_tickers, get_prices (cache-aware), to_monthly_returns
│   ├── analysis.py         # event_study, event_study_summary, momentum_backtest, perf_stats
│   ├── plotting.py         # plot_event_study, plot_equity_curves -> Figure
│   └── cache.py            # parquet I/O, coverage check, delta fetch
├── cache/                  # gitignored — prices.parquet, prices_meta.json, tickers.json
├── presets/                # gitignored — saved-parameter JSON files
├── requirements.txt
└── PLAN.md
```

---

## 4. Refactor plan for `momentum_backtest.py`

Most of the script is already pure functions returning DataFrames/Series — the refactor is mostly *moves*, with two functional changes (plotting returns Figures, downloads go through the cache).

### Move into `lib/analysis.py` (signatures unchanged)
- `event_study(monthly_returns, top_n, rank_lookback, horizon) -> pd.DataFrame`
- `event_study_summary(events, benchmark_monthly) -> pd.DataFrame`
- `momentum_backtest(monthly_returns, top_n, hold_period, rank_lookback) -> pd.Series`
- `perf_stats(returns, periods_per_year=12) -> dict`

### Move into `lib/data.py`
- `get_sp500_tickers() -> list[str]`  *(unchanged; cached separately with a 24h TTL)*
- `to_monthly_returns(prices) -> pd.DataFrame`  *(unchanged)*
- **NEW** `get_prices(tickers, start, end) -> pd.DataFrame` — cache-aware. Internally delegates the actual `yf.download` call to a private `_fetch_prices` helper that holds the existing yfinance logic from [momentum_backtest.py:74-86](momentum_backtest.py:74).

### Change in `lib/plotting.py` — UI-friendly signatures
Old:
```python
def plot_event_study(summary, out_path: Path): ...   # writes PNG, returns None
def plot_equity_curves(strategies, benchmark, out_path: Path): ...
```
New:
```python
def plot_event_study(summary) -> matplotlib.figure.Figure: ...
def plot_equity_curves(strategies, benchmark, benchmark_label="SPY") -> Figure: ...
```
Body change: drop `plt.savefig` / `plt.close`, return `fig`. Caller (CLI or Streamlit) decides what to do with it. Hard-coded `BENCHMARK` reference in [momentum_backtest.py:251](momentum_backtest.py:251) becomes a parameter.

### Slim `momentum_backtest.py` down to glue
Keeps the parameter constants ([lines 53-59](momentum_backtest.py:53)) and a `main()` that:
1. Imports from `lib`
2. Calls `lib.data.get_prices(...)` (now cache-aware)
3. Runs analysis
4. Calls plotting funcs, then `fig.savefig(...)` itself for CLI mode
5. Writes CSVs as today

### Delete
- Inline `plt.close()` calls (now in plotting funcs)
- `OUT_DIR.mkdir(...)` moves into `main()` only — the library never touches disk except via the cache layer

---

## 5. Data flow (request lifecycle)

```
form submit
   │
   ▼
app.py reads params from widgets
   │
   ▼
app.py → lib.data.get_prices(tickers, start, end)
   │
   ├── lib.cache.load_meta()
   │     ├── HIT  range ⊆ cached range
   │     │       └── slice prices.parquet → return
   │     │
   │     ├── PARTIAL  range extends cached range
   │     │       ├── lib.data._fetch_prices(delta_start, delta_end)
   │     │       ├── concat with cached frame, dedupe by date
   │     │       ├── persist parquet + meta
   │     │       └── return slice
   │     │
   │     └── MISS  no cache or ticker set changed
   │             ├── _fetch_prices(start, end)
   │             ├── persist
   │             └── return
   ▼
lib.data.to_monthly_returns(prices)
   ▼
lib.analysis.event_study(...)              ──► event_study_summary(...)
lib.analysis.momentum_backtest(...) ×N      ──► perf_stats(...) ×N
   ▼
lib.plotting.plot_event_study(summary)     → Figure
lib.plotting.plot_equity_curves(strats…)   → Figure
   ▼
st.pyplot(fig)   st.dataframe(table)       (rendered in browser)
```

`st.spinner("Downloading ...")` / `st.spinner("Backtesting ...")` wrap the slow steps so the user sees progress.

---

## 6. Caching strategy

**Format:** single `cache/prices.parquet` (adjusted close, daily, columns = tickers, index = date) + `cache/prices_meta.json`. Parquet is columnar, native to pandas (`to_parquet` / `read_parquet`), and ~3-5× smaller than CSV for this shape. Tickers list cached separately at `cache/tickers.json` with a `fetched_at` timestamp.

**Key scheme:**
- `prices.parquet` is *the* cache — there's only one, keyed implicitly by its column set + date index.
- `prices_meta.json`:
  ```json
  {"start": "2020-01-02", "end": "2026-04-28",
   "tickers": ["AAPL","MSFT",...,"SPY"],
   "fetched_at": "2026-04-29T11:30:00Z"}
  ```

**Coverage logic** (in `lib/cache.py`):
1. Compute requested `(start, end, ticker_set)`.
2. If `ticker_set ⊄ cached_tickers`: fetch the missing tickers for the full requested range, merge as new columns.
3. If `start < cached.start`: fetch `[start, cached.start)`, prepend rows.
4. If `end > cached.end`: fetch `(cached.end, end]`, append rows.
5. Slice the merged frame to `[start, end]` and return.

**Invalidation:**
- Most-recent date is always considered stale within trading hours — if `cached.end == today` and we're between 09:30-16:00 ET, re-fetch the last 2 trading days. (Simple fudge: if `end > cached.end - 2 days`, refetch the tail two days.)
- Manual "Clear cache" button in the sidebar deletes `cache/*` and forces a cold fetch.
- S&P 500 tickers list TTL: 24 hours.

**Expected size:** 500 tickers × ~252 trading days/yr × 5 yrs × float64 ≈ 5 MB parquet (compresses well; floats are mostly redundant scale). Negligible.

---

## 7. UI wireframe

Single page, top-down (Streamlit's natural mode):

```
┌──────────────────────────────────────────────────────────────────────┐
│ S&P 500 Momentum Backtest                                            │
├─────── sidebar ──────────────┬───── main panel ─────────────────────┤
│ PARAMETERS                   │  STATUS                              │
│  Lookback years   [  5  ▼]   │   ▸ "Cache hit — skipped download"   │
│  Top N            [ 10  ▼]   │   ▸ "Backtest complete (3.2s)"       │
│  Hold periods    [×1 ×3 ×6]  │                                      │
│  Rank lookback    [  1  ▼]   │  ── Equity curves ───────────────    │
│  Event horizon    [ 12  ▼]   │  [chart: log-scale equity, SPY +     │
│  Benchmark        [SPY    ]  │   one line per hold period]          │
│                              │                                      │
│  [   Run backtest   ]        │  ── Performance ─────────────────    │
│                              │  ┌─────────┬─────┬─────┬──────┐      │
│  ── Presets ─────────        │  │strategy │CAGR │Sharp│MaxDD │      │
│  Save current as: [____ ▢]   │  │top10_h1 │ ... │ ... │ ...  │      │
│  Load: [▼ presets list]      │  │top10_h3 │ ... │ ... │ ...  │      │
│  Compare: [▼ second preset]  │  │SPY      │ ... │ ... │ ...  │      │
│                              │  └─────────┴─────┴─────┴──────┘      │
│  ── Cache ───────────────    │                                      │
│  Cached: 2020-01 → 2026-04   │  ── Event study ─────────────────    │
│  [ Clear cache ]             │  [chart: avg fwd return + win rate   │
│                              │   bars at h=1..12, benchmark line]   │
│                              │                                      │
│                              │  ── Event-study summary ─────────    │
│                              │  [table: h, avg_return, win_rate,    │
│                              │   alpha_vs_bench, t_stat]            │
│                              │                                      │
│                              │  ── Errors ──────────────────────    │
│                              │  (only if any) red callout per       │
│                              │   missing ticker / yfinance failure  │
└──────────────────────────────┴──────────────────────────────────────┘
```

**Compare mode (M5):** when "Compare" preset is set, equity-curve chart overlays both runs (different line styles / legend prefix), and the perf table gains a `preset` column. Event-study chart stays single-run (the focused preset).

---

## 8. Milestones

Ordered for early dogfooding — you can use the tool from M3 onward.

| # | Phase | What's done | Verification |
|---|-------|-------------|--------------|
| **M1** | Refactor to library | `lib/` modules created; `momentum_backtest.py` reduced to glue; CLI behavior identical | `python momentum_backtest.py` produces same PNGs / CSVs as before; diff old vs new outputs |
| **M2** | Caching layer | `lib/cache.py` + `lib/data.get_prices` cache-aware; CLI now uses it transparently | First CLI run downloads, second run completes in <2s; manually corrupt parquet → graceful re-fetch |
| **M3** | Streamlit MVP | `app.py` with form + Run button, both charts, both tables, spinners, error display | `streamlit run app.py`, change params, see results refresh; throw `ZZZZ` ticker in benchmark to verify error handling |
| **M4** | Polish | Persistent presets (save/load JSON), "Clear cache" button, refined error surfaces | Save preset, restart server, reload preset — params restored |
| **M5** | Side-by-side compare *(stretch)* | Compare two presets on equity-curve chart | Pick preset A & B, charts overlay, perf table shows both |

---

## 9. Open questions

1. **Charts: matplotlib or Plotly?** Matplotlib is the path of least resistance (existing code, `st.pyplot` works directly). Plotly gives hover/zoom but means rewriting both plot functions. **Default: matplotlib.** Override?
2. **Cache invalidation aggressiveness.** Proposed: refetch trailing 2 days whenever requested `end` is within 2 days of `cached.end`. OK, or do you want a strict "1 fetch per calendar day per range" policy?
3. **CLI mode.** Should the script's `./momentum_output/` PNG+CSV outputs stay supported, or is the UI the only entry point going forward and we delete `main()`? **Default: keep CLI working** — useful for cron / scripting.
4. **Preset storage.** JSON files in `./presets/` (lives across server restarts) versus `st.session_state` only (lost on restart). **Default: JSON files**, which also gives you a diff-able audit trail of param sets you've tried.
5. **Survivorship bias.** Out of scope per your spec, but worth noting: as `LOOKBACK_YEARS` grows past ~5 the bias gets material. Want a banner in the UI when `LOOKBACK_YEARS > 5`? **Default: no banner.**
6. **Tickers list refresh.** Wikipedia membership changes a few times a year. 24h TTL OK, or refresh only manually (button)?

---

## Verification plan (end-to-end)

1. `pip install -r requirements.txt` from a clean venv — confirm one-shot install.
2. `python momentum_backtest.py` — confirm CLI still produces the same `event_study.png`, `equity_curves.png`, `event_study_summary.csv`, `backtest_perf.csv` (visual + numeric diff vs pre-refactor outputs).
3. `streamlit run app.py`, default params, click Run — confirm both charts and both tables render.
4. Re-run with same params — confirm no Yahoo call (status panel says "cache hit"), total time <2s.
5. Bump `LOOKBACK_YEARS` from 5 → 6 — confirm only the delta year is downloaded (status panel shows it).
6. Set `BENCHMARK="ZZZZ"` (invalid) — confirm a red error callout appears, no traceback dumped to the page.
7. Clear cache, re-run — confirm cold fetch path works.
8. Save preset "winners-tight" with TOP_N=5, HOLD=[1], reload server, load preset — confirm fields restored.
