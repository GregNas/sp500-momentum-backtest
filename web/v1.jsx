// V1 — Modern fintech, calm. Stripe/Linear-style.
// Adapted from the design package: data flows in via props (real backtest API),
// the Run button hits /api/backtest, Clear cache hits /api/cache/clear.

const { useState: useStateV1, useMemo: useMemoV1, useEffect: useEffectV1 } = React;

const v1Theme = {
  bg: '#fafaf9',
  card: '#ffffff',
  border: '#e7e5e4',
  grid: '#e7e5e4',
  axis: '#78716c',
  text: '#1c1917',
  muted: '#78716c',
  benchmark: '#a8a29e',
};
const v1Accent = { color: '#6366f1', dark: '#4338ca', light: '#eef2ff' };

const v1StratColors = {
  h1m: '#6366f1',
  h2m: '#7c3aed',
  h3m: '#8b5cf6',
  h6m: '#0ea5e9',
  h9m: '#06b6d4',
  h12m: '#0284c7',
};

function V1Sidebar({ params, setParam, onRun, running, elapsed, cacheInfo, cacheNotice,
                     universes, compact, onClearCache }) {
  const issues = paramIssues(params, universes);
  const cardStyle = {
    background: v1Theme.card,
    border: `1px solid ${v1Theme.border}`,
    borderRadius: 8,
    padding: '14px 16px',
  };
  const labelStyle = {
    fontSize: 11, fontWeight: 500, color: v1Theme.muted,
    letterSpacing: '0.04em', textTransform: 'uppercase',
    marginBottom: 6,
  };
  const inputBase = {
    width: '100%', padding: '8px 10px', fontSize: 13,
    border: `1px solid ${v1Theme.border}`, borderRadius: 6,
    background: '#fff', fontFamily: 'JetBrains Mono, monospace',
    color: v1Theme.text, outline: 'none',
  };

  return (
    <aside style={{
      width: compact ? 210 : 260, flexShrink: 0, padding: compact ? 14 : 20,
      borderRight: `1px solid ${v1Theme.border}`,
      background: '#f5f5f4',
      display: 'flex', flexDirection: 'column', gap: 16,
      overflowY: 'auto',
    }}>
      <div>
        <div style={{ fontSize: 11, fontWeight: 600, color: v1Accent.dark,
                       letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          Momentum · v1.2
        </div>
        <div style={{ fontSize: 16, fontWeight: 600, color: v1Theme.text, marginTop: 2 }}>
          S&P 500 Backtest
        </div>
      </div>

      <div style={cardStyle}>
        <div style={{ fontSize: 12, fontWeight: 600, color: v1Theme.text, marginBottom: 12 }}>
          Parameters
        </div>

        <NumField id="v1-lookback" label="Lookback (years)" value={params.lookback}
                  min={1} max={25} help={PARAM_HELP.lookback}
                  onChange={v => setParam('lookback', v)}
                  theme={v1Theme} labelStyle={labelStyle} inputStyle={inputBase}
                  wrapStyle={{ marginBottom: 12 }} />

        <NumField id="v1-topn" label="Top N picks / month" value={params.topN}
                  min={1} max={maxTopNFor(universes, params.universe)} help={PARAM_HELP.topN}
                  onChange={v => setParam('topN', v)}
                  theme={v1Theme} labelStyle={labelStyle} inputStyle={inputBase}
                  wrapStyle={{ marginBottom: 12 }} />

        <div style={{ marginBottom: 12 }}>
          <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
            Hold periods (months)
            <HelpTip help={PARAM_HELP.holds} theme={v1Theme} />
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {[1, 2, 3, 6, 9, 12].map(h => {
              const on = params.holds.includes(h);
              return (
                <button key={h}
                  onClick={() => {
                    const next = on ? params.holds.filter(x => x !== h) : [...params.holds, h].sort((a,b)=>a-b);
                    setParam('holds', next.length ? next : [1]);
                  }}
                  style={{
                    padding: '5px 10px', fontSize: 12,
                    fontFamily: 'JetBrains Mono, monospace',
                    border: `1px solid ${on ? v1Accent.color : v1Theme.border}`,
                    background: on ? v1Accent.color : '#fff',
                    color: on ? '#fff' : v1Theme.text,
                    borderRadius: 5, cursor: 'pointer', fontWeight: 500,
                  }}>
                  {h}m
                </button>
              );
            })}
          </div>
        </div>

        <NumField id="v1-ranklb" label="Rank lookback" value={params.rankLookback}
                  min={1} max={12} help={PARAM_HELP.rankLookback}
                  onChange={v => setParam('rankLookback', v)}
                  theme={v1Theme} labelStyle={labelStyle} inputStyle={inputBase}
                  wrapStyle={{ marginBottom: 12 }} />

        <NumField id="v1-horizon" label="Event horizon" value={params.horizon}
                  min={1} max={24} help={PARAM_HELP.horizon}
                  onChange={v => setParam('horizon', v)}
                  theme={v1Theme} labelStyle={labelStyle} inputStyle={inputBase}
                  wrapStyle={{ marginBottom: 12 }} />

        <div>
          <label htmlFor="v1-benchmark"
                 style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
            Benchmark
            <HelpTip help={PARAM_HELP.benchmark} theme={v1Theme} />
          </label>
          <input id="v1-benchmark" type="text" value={params.benchmark}
                 onChange={e => setParam('benchmark', e.target.value.toUpperCase())}
                 style={inputBase} />
        </div>
      </div>

      <button onClick={onRun} disabled={running || issues.length > 0}
              style={{
                padding: '10px 14px', fontSize: 13, fontWeight: 600,
                background: (running || issues.length) ? v1Theme.muted : v1Accent.dark,
                color: '#fff',
                border: 'none', borderRadius: 6,
                cursor: running ? 'wait' : (issues.length ? 'not-allowed' : 'pointer'),
                boxShadow: `0 1px 2px rgba(0,0,0,0.08)`,
              }}>
        {running ? `Running… ${elapsed || 0}s` : 'Run backtest →'}
      </button>
      {issues.length > 0 && !running && (
        <div style={{ fontSize: 11, color: '#b91c1c', marginTop: -8 }}>
          {issues[0]}
        </div>
      )}

      <div style={cardStyle}>
        <div style={{ fontSize: 12, fontWeight: 600, color: v1Theme.text, marginBottom: 8 }}>
          Cache
        </div>
        <div style={{ fontSize: 11, color: v1Theme.muted, fontFamily: 'JetBrains Mono, monospace', lineHeight: 1.6 }}>
          {cacheInfo
            ? <>Range  {cacheInfo.start} → {cacheInfo.end}<br/>Tickers  {cacheInfo.n_tickers}
                {cacheInfo.age_hours != null && <><br/>Updated {fmtAge(cacheInfo.age_hours)} · {cacheInfo.is_fresh
                  ? <span style={{ color: '#047857' }}>fresh</span>
                  : <span title="Next run re-pulls the last 2 days">stale</span>}</>}
              </>
            : <>Empty — first run will cold-fetch.</>}
        </div>
        <button onClick={onClearCache} disabled={cacheNotice === 'Clearing…'} style={{
          marginTop: 10, width: '100%', padding: '6px 10px', fontSize: 11,
          background: '#fff', color: v1Theme.muted,
          border: `1px solid ${v1Theme.border}`, borderRadius: 5,
          cursor: cacheNotice === 'Clearing…' ? 'wait' : 'pointer',
        }}>
          {cacheNotice || 'Clear cache'}
        </button>
      </div>
    </aside>
  );
}

function V1KpiTile({ label, value, sub, positive, mono = true }) {
  return (
    <div style={{
      background: v1Theme.card, border: `1px solid ${v1Theme.border}`,
      borderRadius: 8, padding: '14px 16px', flex: '1 1 150px', minWidth: 150,
    }}>
      <div style={{ fontSize: 10, fontWeight: 500, color: v1Theme.muted,
                     letterSpacing: '0.06em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{
        fontSize: 24, fontWeight: 600, color: v1Theme.text, marginTop: 4,
        fontFamily: mono ? 'JetBrains Mono, monospace' : 'inherit',
        letterSpacing: '-0.02em',
      }}>
        {value}
      </div>
      {sub && (
        <div style={{
          fontSize: 11, marginTop: 4,
          color: positive == null ? v1Theme.muted : (positive ? '#047857' : '#b91c1c'),
          fontFamily: 'JetBrains Mono, monospace',
        }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function V1EmptyState({ onRun, running, error, elapsed, status }) {
  return (
    <div style={{
      flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 40, background: v1Theme.bg,
    }}>
      <div style={{
        background: v1Theme.card, border: `1px solid ${v1Theme.border}`,
        borderRadius: 8, padding: 32, maxWidth: 480, textAlign: 'center',
      }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: v1Theme.text, marginBottom: 8,
                       display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {running && (
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: v1Accent.dark,
                            animation: 'pulse 1.2s ease-in-out infinite' }} />
          )}
          {error ? 'Backtest failed' : (running ? `Running… ${elapsed || 0}s` : 'No backtest yet')}
        </div>
        <div style={{ fontSize: 13, color: v1Theme.muted, lineHeight: 1.6, marginBottom: 20 }}>
          {error || (running && status)
            || 'Pick parameters in the sidebar, then run the backtest. First cold run pulls ~500 tickers from Yahoo (~60 s); subsequent runs hit the cache (sub-second).'}
        </div>
        {!running && (
          <button onClick={onRun} style={{
            padding: '10px 20px', fontSize: 13, fontWeight: 600,
            background: v1Accent.dark, color: '#fff',
            border: 'none', borderRadius: 6, cursor: 'pointer',
          }}>Run backtest →</button>
        )}
      </div>
    </div>
  );
}

function V1Dashboard({ params, setParam, data, status, running, error, elapsed,
                       cacheInfo, cacheNotice, universes, onRun, onClearCache }) {
  const [hoverIdx, setHoverIdx] = useStateV1(null);
  const [pinnedIdx, setPinnedIdx] = useStateV1(null);
  const [logScale, setLogScale] = useStateV1(true);
  const [filled, setFilled] = useStateV1(false);
  const [chartTab, setChartTab] = useStateV1('equity');
  const { isNarrow, isCompact } = useViewport();

  // Reset hover/pin when data changes (different month count)
  useEffectV1(() => { setHoverIdx(null); setPinnedIdx(null); }, [data]);

  // Hover takes precedence; a click pins a month for comparison.
  const activeIdx = hoverIdx != null ? hoverIdx : pinnedIdx;

  const ready = data && data.PERF && data.EQUITY;

  const equitySeries = useMemoV1(() => {
    if (!ready) return [];
    return params.holds.map(h => ({
      name: `top${params.topN}_h${h}m`,
      values: data.EQUITY[`h${h}m`] || data.EQUITY[`h${params.holds[0]}m`],
      color: v1StratColors[`h${h}m`] || v1Accent.color,
    })).filter(s => s.values).concat([{
      name: params.benchmark, values: data.EQUITY.spy,
      color: v1Theme.muted, dashed: true, isBenchmark: true,
    }]);
  }, [ready, data, params.holds, params.topN, params.benchmark]);

  const months = useMemoV1(() => {
    if (!ready) return [];
    return data.EQUITY.months.map(s => new Date(s));
  }, [ready, data]);

  const bestStrat = ready
    ? data.PERF.filter(p => p.strategy !== params.benchmark)
                .sort((a, b) => b.Sharpe - a.Sharpe)[0]
    : null;
  const spy = ready ? data.PERF.find(p => p.strategy === params.benchmark) : null;
  const bestKey = bestStrat
    ? bestStrat.strategy.replace(/^top\d+_hold(\d+m)$/, 'h$1')
    : null;

  // Significant horizon (|t|>=2)
  const sigHorizon = ready && data.EVENT_STUDY
    ? data.EVENT_STUDY.filter(d => Math.abs(d.t_stat) >= 2)
                       .sort((a, b) => Math.abs(b.t_stat) - Math.abs(a.t_stat))[0]
    : null;

  const cohortCount = ready && data.EVENT_STUDY[0] ? data.EVENT_STUDY[0].n : '—';

  const monthRangeStr = ready && months.length
    ? `${fmtMonthYear(months[0])} → ${fmtMonthYear(months[months.length - 1])} · ${months.length} months`
    : '—';

  return (
    <div style={{
      display: 'flex', height: '100vh', width: '100%',
      background: v1Theme.bg, color: v1Theme.text,
      fontFamily: '"Inter", system-ui, sans-serif',
      fontSize: 13, lineHeight: 1.5,
    }}>
      <V1Sidebar params={params} setParam={setParam}
                 onRun={onRun} running={running} elapsed={elapsed}
                 cacheInfo={cacheInfo} cacheNotice={cacheNotice}
                 universes={universes} compact={isCompact}
                 onClearCache={onClearCache} />

      <main style={{ flex: 1, padding: 24, overflow: 'auto', display: 'flex',
                     flexDirection: 'column', gap: 16 }}>
        <TopPerformersPanel theme={v1Theme} accent={v1Accent}
                            cacheInfo={cacheInfo} universes={universes} />

        {!ready ? (
          <V1EmptyState onRun={onRun} running={running} error={error}
                        elapsed={elapsed} status={status} />
        ) : (
          <>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em' }}>
                Momentum portfolio · top {params.topN}
              </div>
              <div style={{ fontSize: 12, color: v1Theme.muted, marginTop: 4,
                             fontFamily: 'JetBrains Mono, monospace' }}>
                {monthRangeStr} · ranked on prior {params.rankLookback}m return · equal-weight
              </div>
            </div>
            <div role="status" aria-live="polite"
                 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11,
                           color: v1Theme.muted }}>
              {status && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '4px 8px', background: v1Accent.light, color: v1Accent.dark,
                  borderRadius: 12, fontWeight: 500,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%',
                                  background: v1Accent.dark,
                                  animation: running ? 'pulse 1.2s ease-in-out infinite' : 'none' }} />
                  {status}
                </span>
              )}
            </div>
          </div>

          {/* KPI row */}
          {bestStrat && spy && (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <V1KpiTile
                label="Best Sharpe"
                value={fmtNum(bestStrat.Sharpe)}
                sub={`${bestStrat.strategy.replace(`top${params.topN}_`, '')} · vs ${fmtNum(spy.Sharpe)} ${params.benchmark}`}
              />
              <V1KpiTile
                label="Total return"
                value={fmtMult(bestStrat.total_return)}
                sub={`${params.benchmark} ${fmtMult(spy.total_return)} · Δ +${((bestStrat.total_return - spy.total_return) * 100).toFixed(0)}pp`}
                positive={bestStrat.total_return >= spy.total_return}
              />
              <V1KpiTile
                label="CAGR"
                value={fmtPct(bestStrat.CAGR, 1, false)}
                sub={`${params.benchmark} ${fmtPct(spy.CAGR, 1, false)} · α +${fmtPct(bestStrat.CAGR - spy.CAGR, 1, false)}`}
                positive={bestStrat.CAGR >= spy.CAGR}
              />
              <V1KpiTile
                label="Max drawdown"
                value={fmtPct(bestStrat.max_drawdown, 1, false)}
                sub={`${params.benchmark} ${fmtPct(spy.max_drawdown, 1, false)}`}
                positive={bestStrat.max_drawdown >= spy.max_drawdown}
              />
              <V1KpiTile
                label="Volatility"
                value={fmtPct(bestStrat.vol, 1, false)}
                sub="annualized"
              />
            </div>
          )}

          {/* Equity card with tabs */}
          <div style={{
            background: v1Theme.card, border: `1px solid ${v1Theme.border}`,
            borderRadius: 8, padding: '14px 16px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center',
                           justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ display: 'flex', gap: 4 }}>
                {[
                  { id: 'equity', label: 'Equity curve' },
                  { id: 'drawdown', label: 'Drawdown' },
                  { id: 'heatmap', label: 'Monthly returns' },
                ].map(t => (
                  <button key={t.id} onClick={() => setChartTab(t.id)}
                    style={{
                      padding: '5px 10px', fontSize: 12, fontWeight: 500,
                      background: chartTab === t.id ? v1Accent.light : 'transparent',
                      color: chartTab === t.id ? v1Accent.dark : v1Theme.muted,
                      border: 'none', borderRadius: 5, cursor: 'pointer',
                    }}>
                    {t.label}
                  </button>
                ))}
              </div>
              {chartTab === 'equity' && (
                <div style={{ display: 'flex', gap: 12, alignItems: 'center',
                               fontSize: 11, color: v1Theme.muted }}>
                  <label style={{ display: 'flex', gap: 4, alignItems: 'center',
                                   cursor: 'pointer' }}>
                    <input type="checkbox" checked={logScale}
                           onChange={e => setLogScale(e.target.checked)} />
                    log scale
                  </label>
                  <label style={{ display: 'flex', gap: 4, alignItems: 'center',
                                   cursor: 'pointer' }}>
                    <input type="checkbox" checked={filled}
                           onChange={e => setFilled(e.target.checked)} />
                    filled
                  </label>
                </div>
              )}
            </div>

            {chartTab === 'equity' && (
              <>
                <div style={{ height: 280 }}>
                  <EquityCurveChart
                    series={equitySeries}
                    months={months}
                    logScale={logScale}
                    filled={filled}
                    theme={v1Theme}
                    accent={v1Accent}
                    hoveredIdx={activeIdx}
                    onHover={setHoverIdx}
                    onSelect={i => setPinnedIdx(p => (p === i ? null : i))}
                    ariaLabel={`Equity curves, ${equitySeries.length} series vs ${params.benchmark}, ${monthRangeStr}`}
                  />
                </div>
                <div style={{ display: 'flex', gap: 16, marginTop: 8, paddingLeft: 56,
                              fontSize: 11, fontFamily: 'JetBrains Mono, monospace',
                              color: v1Theme.muted, flexWrap: 'wrap' }}>
                  {equitySeries.map(s => (
                    <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{
                        width: 14, height: 2, background: s.color,
                        borderTop: s.dashed ? `1px dashed ${s.color}` : 'none',
                      }} />
                      <span style={{ color: v1Theme.text, fontWeight: 500 }}>{s.name}</span>
                      <span>{fmtMult(activeIdx != null ? s.values[activeIdx] : s.values[s.values.length - 1])}</span>
                    </div>
                  ))}
                  {activeIdx != null && months[activeIdx] && (
                    <div style={{ marginLeft: 'auto', color: v1Theme.text,
                                   display: 'flex', alignItems: 'center', gap: 6 }}>
                      {fmtMonthYear(months[activeIdx])}
                      {pinnedIdx != null && (
                        <button onClick={() => setPinnedIdx(null)}
                                aria-label="Clear pinned month"
                                style={{ border: `1px solid ${v1Theme.border}`, background: '#fff',
                                         color: v1Theme.muted, borderRadius: 4, cursor: 'pointer',
                                         fontSize: 10, padding: '1px 5px', lineHeight: 1.4 }}>
                          pinned ✕
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}

            {chartTab === 'drawdown' && bestKey && data.DRAWDOWN[bestKey] && (
              <div style={{ height: 220 }}>
                <DrawdownChart series={data.DRAWDOWN[bestKey]} months={months}
                               theme={v1Theme} accent={v1Accent} height={220}
                               label={bestStrat.strategy} />
              </div>
            )}

            {chartTab === 'heatmap' && bestKey && data.MONTHLY_RETURNS[bestKey] && (
              <MonthlyHeatmap returns={data.MONTHLY_RETURNS[bestKey]} months={months}
                              theme={v1Theme} accent={v1Accent} compact={isCompact} />
            )}
          </div>

          {/* Two-up: event study + perf table */}
          <div style={{ display: 'grid',
                         gridTemplateColumns: isNarrow ? '1fr' : '1.4fr 1fr', gap: 16 }}>
            <div style={{
              background: v1Theme.card, border: `1px solid ${v1Theme.border}`,
              borderRadius: 8, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                             alignItems: 'center', marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Event study</div>
                  <div style={{ fontSize: 11, color: v1Theme.muted, marginTop: 2 }}>
                    Avg forward return of top-{params.topN} cohort · n={cohortCount} cohorts
                  </div>
                </div>
                <div style={{ fontSize: 11, color: v1Theme.muted,
                               fontFamily: 'JetBrains Mono, monospace' }}>
                  <span style={{ color: v1Accent.color, fontWeight: 600 }}>■</span> picks &nbsp;
                  <span style={{ color: v1Accent.dark, fontWeight: 600 }}>■</span> significant (|t| ≥ 2) &nbsp;
                  <span style={{ color: v1Theme.benchmark }}>--</span> {params.benchmark} avg ({fmtPct(data.BENCHMARK_AVG_MONTHLY, 2, false)}/mo)
                </div>
              </div>
              <div style={{ height: 240 }}>
                <EventStudyChart data={data.EVENT_STUDY} benchmarkAvg={data.BENCHMARK_AVG_MONTHLY}
                                 theme={v1Theme} accent={v1Accent} height={240} />
              </div>
              {sigHorizon ? (
                <div style={{
                  marginTop: 10, padding: '8px 10px', borderRadius: 6,
                  background: v1Accent.light, fontSize: 12, color: v1Accent.dark,
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                }}>
                  <span style={{ fontWeight: 600,
                                  fontFamily: 'JetBrains Mono, monospace' }}>
                    h={sigHorizon.h}
                  </span>
                  <span>α = {fmtPct(sigHorizon.alpha, 2)}/mo · t = {sigHorizon.t_stat.toFixed(2)} — only horizon clearing the 5% significance bar.</span>
                </div>
              ) : (
                <div style={{
                  marginTop: 10, padding: '8px 10px', borderRadius: 6,
                  background: '#f5f5f4', fontSize: 12, color: v1Theme.muted,
                }}>
                  No horizon clears the 5% significance bar (|t| ≥ 2). Treat the alpha as noise.
                </div>
              )}
            </div>

            <div style={{
              background: v1Theme.card, border: `1px solid ${v1Theme.border}`,
              borderRadius: 8, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>Performance</div>
              <table style={{ width: '100%', fontSize: 12,
                              fontFamily: 'JetBrains Mono, monospace',
                              borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: v1Theme.muted, fontSize: 10,
                                textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    <th style={{ textAlign: 'left', padding: '6px 4px', fontWeight: 500,
                                  borderBottom: `1px solid ${v1Theme.border}` }}>Strategy</th>
                    <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                                  borderBottom: `1px solid ${v1Theme.border}` }}>Return</th>
                    <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                                  borderBottom: `1px solid ${v1Theme.border}` }}>CAGR</th>
                    <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                                  borderBottom: `1px solid ${v1Theme.border}` }}>Sharpe</th>
                    <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                                  borderBottom: `1px solid ${v1Theme.border}` }}>Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  {data.PERF.map(p => {
                    const isBench = p.strategy === params.benchmark;
                    const isBest = p.strategy === bestStrat?.strategy;
                    return (
                      <tr key={p.strategy} style={{
                        background: isBest ? v1Accent.light : 'transparent',
                        color: isBench ? v1Theme.muted : v1Theme.text,
                      }}>
                        <td style={{ padding: '7px 4px', fontWeight: isBest ? 600 : 400 }}>
                          {isBest && <span style={{ color: v1Accent.dark, marginRight: 4 }}>●</span>}
                          {p.strategy}
                        </td>
                        <td style={{ padding: '7px 4px', textAlign: 'right' }}>{fmtMult(p.total_return)}</td>
                        <td style={{ padding: '7px 4px', textAlign: 'right' }}>{fmtPct(p.CAGR, 1, false)}</td>
                        <td style={{ padding: '7px 4px', textAlign: 'right',
                                      fontWeight: isBest ? 600 : 400 }}>{fmtNum(p.Sharpe)}</td>
                        <td style={{ padding: '7px 4px', textAlign: 'right',
                                      color: '#b91c1c' }}>{fmtPct(p.max_drawdown, 1, false)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Monthly rebalance trade list */}
          <RebalancePanel rebalance={data.REBALANCE} topN={params.topN}
                          rankLookback={params.rankLookback}
                          universe={params.universe || 'sp500'}
                          theme={v1Theme} accent={v1Accent} />

          {/* Caveats */}
          <div style={{
            background: '#fef3c7', border: `1px solid #fcd34d`, borderRadius: 8,
            padding: '12px 16px', display: 'flex', gap: 12,
          }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"
                 style={{ flexShrink: 0, marginTop: 2 }}>
              <path d="M12 3.5 L22 20.5 H2 Z" stroke="#b45309" strokeWidth="2"
                    strokeLinejoin="round" fill="none" />
              <line x1="12" y1="10" x2="12" y2="14.5" stroke="#b45309" strokeWidth="2"
                    strokeLinecap="round" />
              <circle cx="12" cy="17.2" r="1.2" fill="#b45309" />
            </svg>
            <div style={{ fontSize: 12, color: '#78350f', lineHeight: 1.6 }}>
              <span style={{ fontWeight: 600 }}>Read before trusting these numbers.</span>{' '}
              Survivorship bias — uses the <em>current</em> S&P 500 list, so failed names from earlier years are absent. Effect grows with longer lookbacks.
              Look-ahead is clean (ranking on month-end, forward returns start next month).
              Ignores costs, taxes, slippage. Equal-weight, no risk constraints.
            </div>
          </div>
          </>
        )}
      </main>
    </div>
  );
}

window.V1Dashboard = V1Dashboard;
