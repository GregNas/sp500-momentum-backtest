// V2 — Editorial-data hybrid. Denser, chart-led, emerald accent.
// Adapted from the design package: data via props, real backtest API.

const { useState: useStateV2, useMemo: useMemoV2, useEffect: useEffectV2 } = React;

const v2Theme = {
  bg: '#fbfaf7',
  card: '#ffffff',
  border: '#e8e3da',
  grid: '#ece7dd',
  axis: '#6b6557',
  text: '#1f1d18',
  muted: '#6b6557',
  benchmark: '#a39c8c',
};
const v2Accent = { color: '#059669', dark: '#065f46', light: '#d1fae5' };

const v2StratColors = {
  h1m: '#059669',
  h2m: '#0d9488',
  h3m: '#0891b2',
  h6m: '#7c3aed',
  h9m: '#6d28d9',
  h12m: '#5b21b6',
};

const UNIVERSE_OPTIONS = [
  { key: 'sp500',          label: 'S&P 500 single names' },
  { key: 'global_etfs',    label: 'Global equity ETFs' },
  { key: 'us_sector_etfs', label: 'US sector ETFs' },
];

const UNIVERSE_DEFAULTS = {
  sp500:          { benchmark: 'SPY',  topN: 10, rankLookback: 1, holds: [1, 3, 6] },
  global_etfs:    { benchmark: 'ACWI', topN: 5,  rankLookback: 6, holds: [3, 6, 12] },
  us_sector_etfs: { benchmark: 'SPY',  topN: 3,  rankLookback: 3, holds: [1, 3, 6] },
};

const UNIVERSE_NOUN = {
  sp500:          'S&P 500 names',
  global_etfs:    'country/region ETFs',
  us_sector_etfs: 'US sector ETFs',
};

function V2Sidebar({ params, setParam, onRun, running, elapsed, cacheInfo, cacheNotice,
                     universes, compact, onClearCache }) {
  const issues = paramIssues(params, universes);
  const onUniverseChange = (u) => {
    const d = UNIVERSE_DEFAULTS[u];
    if (d) Object.entries(d).forEach(([k, v]) => setParam(k, v));
    setParam('universe', u);
  };
  const inputBase = {
    width: '100%', padding: '6px 8px', fontSize: 12,
    border: `1px solid ${v2Theme.border}`, borderRadius: 4,
    background: '#fff', fontFamily: 'JetBrains Mono, monospace',
    color: v2Theme.text, outline: 'none',
  };
  const labelStyle = {
    fontSize: 10, fontWeight: 600, color: v2Theme.muted,
    letterSpacing: '0.06em', textTransform: 'uppercase',
    marginBottom: 4,
  };

  return (
    <aside style={{
      width: compact ? 210 : 240, flexShrink: 0, padding: compact ? '16px 12px' : '20px 18px',
      borderRight: `1px solid ${v2Theme.border}`,
      background: '#f6f3eb',
      display: 'flex', flexDirection: 'column', gap: 14,
      overflowY: 'auto', fontSize: 12,
    }}>
      <div style={{ borderBottom: `2px solid ${v2Theme.text}`, paddingBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 700,
                       fontFamily: '"Source Serif Pro", "Georgia", serif',
                       color: v2Theme.text, letterSpacing: '-0.01em' }}>
          Momentum
        </div>
        <div style={{ fontSize: 10, color: v2Theme.muted, marginTop: 2,
                       letterSpacing: '0.1em', textTransform: 'uppercase', fontWeight: 600 }}>
          Backtest Console
        </div>
      </div>

      <div>
        <label htmlFor="v2-universe" style={{ ...labelStyle, display: 'block' }}>Universe</label>
        <select id="v2-universe" value={params.universe || 'sp500'}
                onChange={e => onUniverseChange(e.target.value)}
                style={{ ...inputBase, fontFamily: 'inherit' }}>
          {UNIVERSE_OPTIONS.map(o => (
            <option key={o.key} value={o.key}>{o.label}</option>
          ))}
        </select>
      </div>

      <NumField id="v2-lookback" label="History" value={params.lookback}
                min={1} max={25} help={PARAM_HELP.lookback}
                onChange={v => setParam('lookback', v)}
                theme={v2Theme} labelStyle={labelStyle} inputStyle={inputBase} />

      <NumField id="v2-topn" label="Top N" value={params.topN}
                min={1} max={maxTopNFor(universes, params.universe)} help={PARAM_HELP.topN}
                onChange={v => setParam('topN', v)}
                theme={v2Theme} labelStyle={labelStyle} inputStyle={inputBase} />

      <div>
        <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
          Hold periods
          <HelpTip help={PARAM_HELP.holds} theme={v2Theme} />
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {[1, 2, 3, 6, 9, 12].map(h => {
            const on = params.holds.includes(h);
            return (
              <button key={h}
                onClick={() => {
                  const next = on ? params.holds.filter(x => x !== h) : [...params.holds, h].sort((a,b)=>a-b);
                  setParam('holds', next.length ? next : [1]);
                }}
                style={{
                  padding: '4px 8px', fontSize: 11,
                  fontFamily: 'JetBrains Mono, monospace',
                  border: `1px solid ${on ? v2Accent.dark : v2Theme.border}`,
                  background: on ? v2Accent.dark : '#fff',
                  color: on ? '#fff' : v2Theme.text,
                  borderRadius: 3, cursor: 'pointer',
                }}>
                {h}m
              </button>
            );
          })}
        </div>
      </div>

      <NumField id="v2-ranklb" label="Rank lookback" value={params.rankLookback}
                min={1} max={12} help={PARAM_HELP.rankLookback}
                onChange={v => setParam('rankLookback', v)}
                theme={v2Theme} labelStyle={labelStyle} inputStyle={inputBase} />

      <NumField id="v2-horizon" label="Event horizon" value={params.horizon}
                min={1} max={24} help={PARAM_HELP.horizon}
                onChange={v => setParam('horizon', v)}
                theme={v2Theme} labelStyle={labelStyle} inputStyle={inputBase} />

      <div>
        <label htmlFor="v2-benchmark"
               style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
          Benchmark
          <HelpTip help={PARAM_HELP.benchmark} theme={v2Theme} />
        </label>
        <input id="v2-benchmark" type="text" value={params.benchmark}
               onChange={e => setParam('benchmark', e.target.value.toUpperCase())}
               style={inputBase} />
      </div>

      <NumField id="v2-riskfree" label="Risk-free (annual %)"
                value={+(((params.riskFree || 0) * 100).toFixed(2))}
                min={0} max={20} help={PARAM_HELP.riskFree}
                onChange={v => setParam('riskFree', Number.isFinite(v) ? v / 100 : 0)}
                theme={v2Theme} labelStyle={labelStyle} inputStyle={inputBase} />

      <button onClick={onRun} disabled={running || issues.length > 0}
              style={{
                padding: '8px 12px', fontSize: 12, fontWeight: 600,
                background: (running || issues.length) ? v2Theme.muted : v2Theme.text,
                color: '#fff',
                border: 'none', borderRadius: 4,
                cursor: running ? 'wait' : (issues.length ? 'not-allowed' : 'pointer'),
                letterSpacing: '0.04em',
              }}>
        {running ? `RUNNING… ${elapsed || 0}s` : 'RUN BACKTEST'}
      </button>
      {issues.length > 0 && !running && (
        <div style={{ fontSize: 10.5, color: '#b91c1c', marginTop: -6 }}>
          {issues[0]}
        </div>
      )}

      <div style={{ paddingTop: 8 }}>
        <div style={labelStyle}>Cache</div>
        <div style={{ fontSize: 10.5, color: v2Theme.muted,
                       fontFamily: 'JetBrains Mono, monospace', lineHeight: 1.7 }}>
          {cacheInfo
            ? <>{cacheInfo.start} → {cacheInfo.end}<br/>{cacheInfo.n_tickers} tickers
                {cacheInfo.age_hours != null && <><br/>updated {fmtAge(cacheInfo.age_hours)} · {cacheInfo.is_fresh
                  ? <span style={{ color: '#047857' }}>fresh</span>
                  : <span title="Next run re-pulls the last 2 days">stale</span>}</>}
              </>
            : <>Empty — first run cold-fetches.</>}
        </div>
        <button onClick={onClearCache} disabled={cacheNotice === 'Clearing…'} style={{
          marginTop: 6, width: '100%', padding: '4px 6px', fontSize: 10.5,
          background: '#fff', color: v2Theme.muted,
          border: `1px solid ${v2Theme.border}`, borderRadius: 3,
          cursor: cacheNotice === 'Clearing…' ? 'wait' : 'pointer',
          fontFamily: 'JetBrains Mono, monospace',
        }}>
          {cacheNotice ? cacheNotice.toLowerCase() : 'clear'}
        </button>
      </div>
    </aside>
  );
}

function V2EmptyState({ onRun, running, error, elapsed, status }) {
  return (
    <div style={{
      flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 40, background: v2Theme.bg,
    }}>
      <div style={{
        background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
        borderRadius: 6, padding: 32, maxWidth: 480, textAlign: 'center',
      }}>
        <div style={{ fontSize: 22, fontWeight: 700,
                       fontFamily: '"Source Serif Pro", Georgia, serif',
                       color: v2Theme.text, marginBottom: 8,
                       display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {running && (
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: v2Accent.dark,
                            animation: 'pulse 1.2s ease-in-out infinite' }} />
          )}
          {error ? 'Backtest failed' : (running ? `Running… ${elapsed || 0}s` : 'No backtest yet')}
        </div>
        <div style={{ fontSize: 13, color: v2Theme.muted, lineHeight: 1.6, marginBottom: 20 }}>
          {error || (running && status)
            || 'Pick parameters in the sidebar, then run the backtest. Cold runs pull ~500 tickers (~60 s); cache hits return in under a second.'}
        </div>
        {!running && (
          <button onClick={onRun} style={{
            padding: '10px 20px', fontSize: 13, fontWeight: 600,
            background: v2Theme.text, color: '#fff',
            border: 'none', borderRadius: 4, cursor: 'pointer',
            letterSpacing: '0.04em',
          }}>RUN BACKTEST</button>
        )}
      </div>
    </div>
  );
}

function V2Dashboard({ params, setParam, data, status, running, error, elapsed,
                       cacheInfo, cacheNotice, universes, onRun, onClearCache }) {
  const [hoverIdx, setHoverIdx] = useStateV2(null);
  const [pinnedIdx, setPinnedIdx] = useStateV2(null);
  const [logScale, setLogScale] = useStateV2(true);
  const [filled, setFilled] = useStateV2(true);
  const [esView, setEsView] = useStateV2('alpha');
  const { isNarrow, isCompact } = useViewport();

  useEffectV2(() => { setHoverIdx(null); setPinnedIdx(null); }, [data]);

  // Hover takes precedence; a click pins a month for comparison.
  const activeIdx = hoverIdx != null ? hoverIdx : pinnedIdx;

  const ready = data && data.PERF && data.EQUITY;

  const equitySeries = useMemoV2(() => {
    if (!ready) return [];
    return params.holds.map(h => ({
      name: `top${params.topN}_h${h}m`,
      values: data.EQUITY[`h${h}m`] || data.EQUITY[`h${params.holds[0]}m`],
      color: v2StratColors[`h${h}m`] || v2Accent.color,
    })).filter(s => s.values).concat([{
      name: params.benchmark, values: data.EQUITY.spy,
      color: v2Theme.muted, dashed: true, isBenchmark: true,
    }]);
  }, [ready, data, params.holds, params.topN, params.benchmark]);

  const months = useMemoV2(() => {
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

  const cohortCount = ready && data.EVENT_STUDY[0] ? data.EVENT_STUDY[0].n : 0;
  const universeSize = ready ? (data.UNIVERSE_SIZE || '—') : '—';

  const monthRange = months.length
    ? `${fmtMonthYear(months[0])}–${fmtMonthYear(months[months.length - 1])}`
    : '—';

  // Find a significant horizon for the verdict callout
  const sigHorizon = ready
    ? data.EVENT_STUDY.filter(d => Math.abs(d.t_stat) >= 2)
                       .sort((a, b) => Math.abs(b.t_stat) - Math.abs(a.t_stat))[0]
    : null;

  const heatmapValues = bestKey && data?.MONTHLY_RETURNS?.[bestKey];
  const bestMonth = heatmapValues ? Math.max(...heatmapValues) : null;
  const worstMonth = heatmapValues ? Math.min(...heatmapValues) : null;

  const rollingSharpeSeries = useMemoV2(() => {
    if (!ready || !data.ROLLING_SHARPE) return [];
    return params.holds.map(h => ({
      name: `top${params.topN}_h${h}m`,
      values: data.ROLLING_SHARPE[`h${h}m`] || [],
      color: v2StratColors[`h${h}m`] || v2Accent.color,
    })).filter(s => s.values.length).concat([{
      name: params.benchmark, values: data.ROLLING_SHARPE.spy || [],
      color: v2Theme.muted, isBenchmark: true,
    }]);
  }, [ready, data, params.holds, params.topN, params.benchmark]);

  const underwaterSeries = useMemoV2(() => {
    if (!ready) return [];
    return params.holds.map(h => ({
      name: `top${params.topN}_h${h}m`,
      values: data.DRAWDOWN[`h${h}m`] || [],
      color: v2StratColors[`h${h}m`] || v2Accent.color,
    })).filter(s => s.values.length).concat([{
      name: params.benchmark, values: data.DRAWDOWN.spy || [],
      color: v2Theme.muted, isBenchmark: true,
    }]);
  }, [ready, data, params.holds, params.topN, params.benchmark]);

  const cohortGroups = ready ? (data.COHORT_GROUPS || data.COHORT_SECTORS || []) : [];
  const metaLabel = ready ? (data.META_LABEL || 'Sector') : 'Sector';

  const aggregatedSectors = useMemoV2(() => {
    const agg = {};
    cohortGroups.forEach(c => {
      Object.entries(c.sectors).forEach(([s, n]) => {
        agg[s] = (agg[s] || 0) + n;
      });
    });
    return agg;
  }, [cohortGroups]);

  const sectorCohortCount = cohortGroups.length;

  return (
    <div style={{
      display: 'flex', height: '100vh', width: '100%',
      background: v2Theme.bg, color: v2Theme.text,
      fontFamily: '"Inter", system-ui, sans-serif',
      fontSize: 12, lineHeight: 1.5,
    }}>
      <V2Sidebar params={params} setParam={setParam}
                 onRun={onRun} running={running} elapsed={elapsed}
                 cacheInfo={cacheInfo} cacheNotice={cacheNotice}
                 universes={universes} compact={isCompact}
                 onClearCache={onClearCache} />

      <main style={{ flex: 1, padding: 22, overflow: 'auto', display: 'flex',
                     flexDirection: 'column', gap: 16 }}>
        <TopPerformersPanel theme={v2Theme} accent={v2Accent}
                            headingFont='"Source Serif Pro", Georgia, serif'
                            universe={params.universe || 'sp500'}
                            cacheInfo={cacheInfo} universes={universes} />

        {!ready ? (
          <V2EmptyState onRun={onRun} running={running} error={error}
                        elapsed={elapsed} status={status} />
        ) : (
          <>
          {/* Editorial header bar */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
            borderBottom: `1px solid ${v2Theme.border}`, paddingBottom: 12,
            flexWrap: 'wrap', gap: 12,
          }}>
            <div>
              <div role="status" aria-live="polite"
                   style={{ fontSize: 10, fontWeight: 700, color: v2Accent.dark,
                             letterSpacing: '0.15em', textTransform: 'uppercase',
                             display: 'flex', alignItems: 'center', gap: 6 }}>
                {running && (
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: v2Accent.dark,
                                  animation: 'pulse 1.2s ease-in-out infinite' }} />
                )}
                {status ? `Run · ${status}` : 'Backtest console'}
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, marginTop: 2,
                             fontFamily: '"Source Serif Pro", Georgia, serif',
                             letterSpacing: '-0.015em' }}>
                Do winners keep winning?
              </div>
              <div style={{ fontSize: 13, color: v2Theme.muted, marginTop: 2 }}>
                Top {params.topN} {UNIVERSE_NOUN[params.universe] || 'names'} by prior {params.rankLookback}-month return,
                equal-weighted, held {params.holds.join('/')} months.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 24, fontFamily: 'JetBrains Mono, monospace' }}>
              <div>
                <div style={{ fontSize: 10, color: v2Theme.muted, letterSpacing: '0.06em',
                               textTransform: 'uppercase' }}>{months.length} mo</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{monthRange}</div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: v2Theme.muted, letterSpacing: '0.06em',
                               textTransform: 'uppercase' }}>Cohorts</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{cohortCount}</div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: v2Theme.muted, letterSpacing: '0.06em',
                               textTransform: 'uppercase' }}>Universe</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{universeSize}</div>
              </div>
            </div>
          </div>

          {/* Hero: equity curve with side rail */}
          <div style={{ display: 'grid',
                         gridTemplateColumns: isNarrow ? '1fr' : '1fr 220px', gap: 16 }}>
            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                             alignItems: 'center', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                  <span style={{ fontSize: 13, fontWeight: 700,
                                  fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                    Equity curves
                  </span>
                  <span style={{ fontSize: 11, color: v2Theme.muted }}>$1 starting capital</span>
                </div>
                <div style={{ display: 'flex', gap: 14, fontSize: 10.5, color: v2Theme.muted,
                               fontFamily: 'JetBrains Mono, monospace' }}>
                  <label style={{ display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
                    <input type="checkbox" checked={logScale}
                           onChange={e => setLogScale(e.target.checked)} />log
                  </label>
                  <label style={{ display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
                    <input type="checkbox" checked={filled}
                           onChange={e => setFilled(e.target.checked)} />fill
                  </label>
                </div>
              </div>
              <div style={{ height: 260 }}>
                <EquityCurveChart
                  series={equitySeries} months={months}
                  logScale={logScale} filled={filled}
                  theme={v2Theme} accent={v2Accent}
                  hoveredIdx={activeIdx} onHover={setHoverIdx}
                  onSelect={i => setPinnedIdx(p => (p === i ? null : i))}
                  ariaLabel={`Equity curves, ${equitySeries.length} series vs ${params.benchmark}, ${monthRange}`}
                />
              </div>
              {bestKey && data.DRAWDOWN[bestKey] && (
                <div style={{ height: 90, marginTop: 4,
                               borderTop: `1px dashed ${v2Theme.border}`, paddingTop: 4 }}>
                  <DrawdownChart series={data.DRAWDOWN[bestKey]} months={months}
                                 theme={v2Theme} accent={v2Accent} height={90}
                                 label={bestStrat.strategy} />
                </div>
              )}
              <div style={{ display: 'flex', gap: 14, marginTop: 6, paddingLeft: 56,
                            fontSize: 11, fontFamily: 'JetBrains Mono, monospace',
                            color: v2Theme.muted, flexWrap: 'wrap' }}>
                {equitySeries.map(s => (
                  <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 10, height: 2, background: s.color }} />
                    <span style={{ color: v2Theme.text, fontWeight: 500 }}>{s.name}</span>
                    <span>{fmtGrowthPct(activeIdx != null ? s.values[activeIdx] : s.values[s.values.length - 1])}</span>
                  </div>
                ))}
                {activeIdx != null && months[activeIdx] && (
                  <div style={{ marginLeft: 'auto', color: v2Theme.text, fontWeight: 600,
                                 display: 'flex', alignItems: 'center', gap: 6 }}>
                    {fmtMonthYear(months[activeIdx])}
                    {pinnedIdx != null && (
                      <button onClick={() => setPinnedIdx(null)}
                              aria-label="Clear pinned month"
                              style={{ border: `1px solid ${v2Theme.border}`, background: '#fff',
                                       color: v2Theme.muted, borderRadius: 3, cursor: 'pointer',
                                       fontSize: 10, padding: '1px 5px', lineHeight: 1.4,
                                       fontWeight: 400 }}>
                        pinned ✕
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Side rail — stacks beside the chart, wraps beneath it when narrow */}
            <div style={{ display: 'flex',
                           flexDirection: isNarrow ? 'row' : 'column',
                           flexWrap: isNarrow ? 'wrap' : 'nowrap', gap: 10 }}>
              {bestStrat && (
                <div style={{
                  background: v2Accent.dark, color: '#fff',
                  borderRadius: 6, padding: '14px 16px',
                  flex: isNarrow ? '1 1 200px' : 'none',
                }}>
                  <div style={{ fontSize: 10, opacity: 0.75, letterSpacing: '0.06em',
                                 textTransform: 'uppercase', fontWeight: 600 }}>
                    Best sleeve
                  </div>
                  <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4,
                                 fontFamily: 'JetBrains Mono, monospace' }}>
                    {bestStrat.strategy.replace(/^top\d+_/, '')}
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.85, marginTop: 4, lineHeight: 1.6 }}>
                    Sharpe <strong style={{ fontFamily: 'JetBrains Mono, monospace' }}>{fmtNum(bestStrat.Sharpe)}</strong>
                    {' '}· {fmtPct(bestStrat.total_return, 0)} return
                    {' '}· {fmtPct(bestStrat.CAGR, 1, false)} CAGR
                  </div>
                </div>
              )}

              {bestStrat && spy && (
                <div style={{
                  background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
                  borderRadius: 6, padding: '12px 14px',
                  flex: isNarrow ? '1 1 200px' : 'none',
                }}>
                  <div style={{ fontSize: 10, color: v2Theme.muted, letterSpacing: '0.06em',
                                 textTransform: 'uppercase', fontWeight: 600 }}>
                    vs {params.benchmark}
                  </div>
                  <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                                 marginTop: 6, lineHeight: 1.8 }}>
                    {[
                      ['α (CAGR)', (bestStrat.CAGR - spy.CAGR) * 100, 'pp', true],
                      ['Δ Sharpe', bestStrat.Sharpe - spy.Sharpe, '', true],
                      ['Δ vol', (bestStrat.vol - spy.vol) * 100, 'pp', false],
                      ['Δ max DD', (bestStrat.max_drawdown - spy.max_drawdown) * 100, 'pp', null],
                    ].map(([label, v, suffix, betterIfPositive]) => {
                      const positive = v >= 0;
                      // For drawdown, "less negative" = better, leave neutral
                      const goodColor = '#047857', badColor = '#b91c1c';
                      const color = betterIfPositive == null
                        ? (positive ? goodColor : badColor)
                        : (positive === betterIfPositive ? goodColor : badColor);
                      const sign = v >= 0 ? '+' : '';
                      const value = suffix === ''
                        ? `${sign}${v.toFixed(2)}`
                        : `${sign}${v.toFixed(1)}${suffix}`;
                      return (
                        <div key={label}
                             style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: v2Theme.muted }}>{label}</span>
                          <span style={{ color, fontWeight: 600 }}>{value}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div style={{
                background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
                borderRadius: 6, padding: '12px 14px',
                flex: isNarrow ? '1 1 200px' : 'none',
              }}>
                <div style={{ fontSize: 10, color: v2Theme.muted, letterSpacing: '0.06em',
                               textTransform: 'uppercase', fontWeight: 600 }}>
                  All sleeves
                </div>
                <table style={{ width: '100%', fontSize: 11,
                                fontFamily: 'JetBrains Mono, monospace',
                                borderCollapse: 'collapse', marginTop: 6 }}>
                  <tbody>
                    {data.PERF.map(p => (
                      <tr key={p.strategy} style={{
                        color: p.strategy === params.benchmark ? v2Theme.muted : v2Theme.text,
                        borderBottom: `1px solid ${v2Theme.border}`,
                      }}>
                        <td style={{ padding: '4px 0' }}>
                          {p.strategy.replace(/^top\d+_hold/, 'h')}
                        </td>
                        <td style={{ padding: '4px 0', textAlign: 'right',
                                      fontWeight: 600 }}>{fmtNum(p.Sharpe)}</td>
                        <td style={{ padding: '4px 0', textAlign: 'right',
                                      color: v2Theme.muted }}>{fmtPct(p.CAGR, 0, false)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Two-up: event study + recent picks */}
          <div style={{ display: 'grid',
                         gridTemplateColumns: isNarrow ? '1fr' : '1.4fr 1fr', gap: 16 }}>
            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                             alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 700,
                                fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                  Event study
                </span>
                <div style={{ display: 'flex', border: `1px solid ${v2Theme.border}`,
                               borderRadius: 4, overflow: 'hidden' }}>
                  {[
                    { id: 'alpha', label: 'Avg return' },
                    { id: 'win', label: 'Win rate' },
                  ].map(t => (
                    <button key={t.id} onClick={() => setEsView(t.id)}
                      style={{
                        padding: '4px 10px', fontSize: 11, fontWeight: 500,
                        background: esView === t.id ? v2Theme.text : 'transparent',
                        color: esView === t.id ? '#fff' : v2Theme.muted,
                        border: 'none', cursor: 'pointer',
                      }}>
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ fontSize: 11, color: v2Theme.muted, marginBottom: 8 }}>
                Forward return at horizon h after a top-{params.topN} cohort is selected.
                n={cohortCount} cohorts. Dark bars clear |t| ≥ 2 (5% significance).
              </div>
              <div style={{ height: 220 }}>
                <EventStudyChart
                  data={data.EVENT_STUDY} theme={v2Theme} accent={v2Accent}
                  benchmarkAvg={data.BENCHMARK_AVG_MONTHLY}
                  height={220} showWinRate={esView === 'win'}
                />
              </div>
              <div style={{
                marginTop: 8, fontSize: 11, color: v2Theme.text,
                borderLeft: `3px solid ${v2Accent.color}`, paddingLeft: 10, lineHeight: 1.6,
              }}>
                <strong>Verdict:</strong>{' '}
                {sigHorizon
                  ? <>winners keep winning at <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 600 }}>h={sigHorizon.h}</span> (t={sigHorizon.t_stat.toFixed(2)}, α={fmtPct(sigHorizon.alpha, 2)}/mo). Other horizons are noise — be skeptical.</>
                  : <>no horizon clears 5% significance (|t| ≥ 2). On this run, the alpha is indistinguishable from noise.</>
                }
              </div>
            </div>

            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 13, fontWeight: 700,
                             fontFamily: '"Source Serif Pro", Georgia, serif',
                             marginBottom: 4 }}>
                Recent picks
              </div>
              <div style={{ fontSize: 11, color: v2Theme.muted, marginBottom: 10 }}>
                Last {data.RECENT_COHORTS?.length || 0} monthly cohorts · top {params.topN} by prior {params.rankLookback}m
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8,
                             maxHeight: 320, overflowY: 'auto' }}>
                {(data.RECENT_COHORTS || []).map(c => (
                  <div key={c.month} style={{
                    borderBottom: `1px solid ${v2Theme.border}`, paddingBottom: 8,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between',
                                   alignItems: 'baseline' }}>
                      <span style={{ fontSize: 12, fontWeight: 600,
                                      fontFamily: 'JetBrains Mono, monospace' }}>
                        {c.month}
                      </span>
                      <span style={{
                        fontSize: 11, fontWeight: 600,
                        fontFamily: 'JetBrains Mono, monospace',
                        color: c.return_pct >= 0 ? '#047857' : '#b91c1c',
                      }}>
                        {fmtPct(c.return_pct, 1)}
                      </span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                      {(c.picks_meta || c.picks.map(t => ({ ticker: t, group: '' }))).map(p => (
                        <span key={p.ticker} style={{
                          padding: '2px 6px', fontSize: 10.5,
                          fontFamily: 'JetBrains Mono, monospace',
                          background: v2Theme.bg, border: `1px solid ${v2Theme.border}`,
                          borderRadius: 3, color: v2Theme.text,
                          display: 'inline-flex', gap: 4, alignItems: 'baseline',
                        }}>
                          <span style={{ fontWeight: 600 }}>{p.ticker}</span>
                          {params.universe !== 'sp500' && p.group && (
                            <span style={{ color: v2Theme.muted, fontFamily: 'inherit',
                                            fontWeight: 400, fontSize: 10 }}>
                              {p.group}
                            </span>
                          )}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
                {!data.RECENT_COHORTS?.length && (
                  <div style={{ fontSize: 11, color: v2Theme.muted, fontStyle: 'italic' }}>
                    Not enough history to surface cohort picks.
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Year-over-year + monthly earnings tables */}
          <EarningsBreakdown data={data} params={params} bestKey={bestKey}
                             theme={v2Theme} accent={v2Accent}
                             headingFont='"Source Serif Pro", Georgia, serif' />

          {/* Monthly rebalance trade list */}
          <RebalancePanel rebalance={data.REBALANCE} topN={params.topN}
                          rankLookback={params.rankLookback}
                          universe={params.universe || 'sp500'}
                          theme={v2Theme} accent={v2Accent}
                          headingFont='"Source Serif Pro", Georgia, serif' />

          {/* Rolling Sharpe + Underwater (two-up) */}
          <div style={{ display: 'grid',
                         gridTemplateColumns: isNarrow ? '1fr' : '1fr 1fr', gap: 16 }}>
            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 13, fontWeight: 700,
                             fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                Rolling Sharpe (12m)
              </div>
              <div style={{ fontSize: 11, color: v2Theme.muted, marginBottom: 8 }}>
                Trailing 12-month annualized Sharpe — does the edge persist or decay?
              </div>
              <div style={{ height: 220 }}>
                <RollingSharpeChart
                  series={rollingSharpeSeries} months={months}
                  theme={v2Theme} refSharpe={spy?.Sharpe} height={220}
                />
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 6, paddingLeft: 56,
                            fontSize: 11, fontFamily: 'JetBrains Mono, monospace',
                            color: v2Theme.muted, flexWrap: 'wrap' }}>
                {rollingSharpeSeries.map(s => (
                  <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 10, height: 2, background: s.color }} />
                    <span style={{ color: v2Theme.text, fontWeight: 500 }}>{s.name}</span>
                  </div>
                ))}
              </div>
            </div>

            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 13, fontWeight: 700,
                             fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                Underwater (drawdown)
              </div>
              <div style={{ fontSize: 11, color: v2Theme.muted, marginBottom: 8 }}>
                Time spent below prior peak — depth and recovery duration vs {params.benchmark}.
              </div>
              <div style={{ height: 220 }}>
                <UnderwaterChart
                  series={underwaterSeries} months={months}
                  theme={v2Theme} height={220}
                />
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 6, paddingLeft: 56,
                            fontSize: 11, fontFamily: 'JetBrains Mono, monospace',
                            color: v2Theme.muted, flexWrap: 'wrap' }}>
                {underwaterSeries.map(s => (
                  <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 10, height: 2, background: s.color,
                                    borderTop: s.isBenchmark ? `2px dashed ${s.color}` : 'none' }} />
                    <span style={{ color: v2Theme.text, fontWeight: 500 }}>{s.name}</span>
                    <span>{fmtPct(Math.min(...s.values), 1)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Sector breakdown */}
          {sectorCohortCount > 0 && (
            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                             alignItems: 'baseline', marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 700,
                                fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                  {metaLabel === 'Sector' ? 'Sector' : metaLabel} concentration of recent picks
                </span>
                <span style={{ fontSize: 11, color: v2Theme.muted,
                                fontFamily: 'JetBrains Mono, monospace' }}>
                  last {sectorCohortCount} cohorts · {params.topN}/cohort
                </span>
              </div>
              <div style={{ fontSize: 11, color: v2Theme.muted, marginBottom: 12 }}>
                Where momentum is currently concentrated — a single dominant sector means
                the strategy is effectively a sector bet.
              </div>
              <SectorBarChart sectors={aggregatedSectors}
                              theme={v2Theme} accent={v2Accent} />
            </div>
          )}

          {/* Monthly heatmap */}
          {bestKey && heatmapValues && (
            <div style={{
              background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
              borderRadius: 6, padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                             alignItems: 'baseline', marginBottom: 12 }}>
                <span style={{ fontSize: 13, fontWeight: 700,
                                fontFamily: '"Source Serif Pro", Georgia, serif' }}>
                  Monthly returns · {bestStrat.strategy}
                </span>
                <span style={{ fontSize: 11, color: v2Theme.muted,
                                fontFamily: 'JetBrains Mono, monospace' }}>
                  best {fmtPct(bestMonth, 1)} · worst {fmtPct(worstMonth, 1)}
                </span>
              </div>
              <MonthlyHeatmap returns={heatmapValues} months={months}
                              theme={v2Theme} accent={v2Accent} compact={isCompact} />
            </div>
          )}

          {/* Caveats */}
          <div style={{
            background: v2Theme.card, border: `1px solid ${v2Theme.border}`,
            borderRadius: 6, padding: '14px 18px',
            display: 'grid', gridTemplateColumns: '120px 1fr', gap: 18,
          }}>
            <div style={{
              fontSize: 11, fontWeight: 700, color: '#92400e',
              letterSpacing: '0.1em', textTransform: 'uppercase',
              borderRight: `1px solid ${v2Theme.border}`,
            }}>
              Read this first
            </div>
            <div style={{ fontSize: 12, color: v2Theme.text, lineHeight: 1.7 }}>
              {params.universe === 'sp500' && (<>
                <strong>Survivorship bias.</strong> Universe is the <em>current</em> S&P 500.
                Names that fell out are silently absent — making the strategy look better the
                further back the window goes.{' '}
                <strong>Look-ahead is clean</strong> (rank on month-end close, hold starts next month).{' '}
                <strong>No costs modelled</strong> — no transaction fees, slippage, or taxes.
                Equal-weight, no risk constraints. Treat results as upper-bound, not what you'd actually clear.
              </>)}
              {params.universe === 'global_etfs' && (<>
                <strong>USD-denominated, US-listed ETFs only</strong> — embedded FX exposure varies by fund.
                <strong> Tracking error and expense-ratio drag</strong> (~0.50 %/yr typical for single-country iShares)
                are not modelled. Universe ranges from ~11 (pre-2010) to ~35 names — small-N momentum is noisier.{' '}
                <strong>Look-ahead is clean</strong>; <strong>no transaction costs</strong>; equal-weight.
              </>)}
              {params.universe === 'us_sector_etfs' && (<>
                <strong>11-member universe</strong> (10 before XLRE inception 2015-10, 9 before XLC inception 2018-06).
                <strong> Sectors are highly correlated</strong> by construction — a top-3 cohort is closer to a tilt
                than a diversified portfolio. Expense ratio (~0.10 %) and tracking error not modelled.{' '}
                <strong>Look-ahead is clean</strong>; <strong>no transaction costs</strong>; equal-weight.
              </>)}
            </div>
          </div>
          </>
        )}
      </main>
    </div>
  );
}

window.V2Dashboard = V2Dashboard;
