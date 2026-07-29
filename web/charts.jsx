// Shared SVG chart primitives. Each chart accepts a `theme` object
// `{ accent, grid, axis, text, bg }` so V1/V2/V3 can restyle without forking.
// All charts are responsive via viewBox + preserveAspectRatio.

const { useMemo, useState, useRef, useEffect } = React;

// ============================================================
// useViewport — shared breakpoints for laptop-friendly layouts.
// The codebase styles inline, so breakpoints live here instead of media queries.
// ============================================================
function useViewport() {
  const [width, setWidth] = useState(window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return { width, isNarrow: width < 1100, isCompact: width < 900 };
}

// ============================================================
// EquityCurveChart — log-scale line chart of strategies vs benchmark
// ============================================================
function EquityCurveChart({
  series,            // { name, values, color, dashed?, isBenchmark? }[]
  months,            // Date[]
  height = 320,
  logScale = true,
  filled = false,
  theme,
  accent,
  hoveredIdx,
  onHover,
  onSelect,          // click-to-pin: called with the month index under the cursor
  ariaLabel,
}) {
  const W = 1000, H = height;
  const padL = 56, padR = 16, padT = 16, padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const allVals = series.flatMap(s => s.values);
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const yMin = logScale ? Math.log(Math.max(min, 0.01)) : min;
  const yMax = logScale ? Math.log(max) : max;
  const yRange = yMax - yMin || 1;

  const xAt = (i) => padL + (i / (months.length - 1)) * innerW;
  const yAt = (v) => {
    const t = logScale ? Math.log(Math.max(v, 0.01)) : v;
    return padT + innerH - ((t - yMin) / yRange) * innerH;
  };

  // Y ticks
  const yTicks = useMemo(() => {
    if (logScale) {
      const ticks = [1, 2, 3, 4, 5, 6];
      return ticks.filter(t => t >= min * 0.95 && t <= max * 1.05);
    }
    const step = (max - min) / 4;
    return [0, 1, 2, 3, 4].map(i => min + step * i);
  }, [logScale, min, max]);

  // fmtEquityAxis rounds to whole percent, which is right for a multi-hundred-
  // percent backtest curve but collapses several ticks to the same label on a
  // narrow range (a young ledger curve spanning a couple of percent would print
  // "+1%" three times). Add decimals only when the span is small, so every
  // existing chart keeps its current labels.
  const yLabel = (t) => {
    const span = max - min;
    if (logScale || span >= 0.05) return fmtEquityAxis(t);
    const digits = span >= 0.005 ? 1 : 2;
    const pct = (t - 1) * 100;
    return `${pct > 0 ? '+' : ''}${pct.toFixed(digits)}%`;
  };

  // X ticks: year boundaries
  const xTicks = useMemo(() => {
    const out = [];
    let lastYear = -1;
    months.forEach((d, i) => {
      const y = d.getFullYear();
      if (y !== lastYear) {
        out.push({ i, label: y });
        lastYear = y;
      }
    });
    return out;
  }, [months]);

  const idxFromEvent = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((x - padL) / innerW) * (months.length - 1));
    return Math.max(0, Math.min(months.length - 1, i));
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img" aria-label={ariaLabel || 'Equity curve chart'}
         style={{ width: '100%', height: '100%', display: 'block', overflow: 'visible',
                  cursor: onSelect ? 'pointer' : 'default' }}
         onMouseMove={(e) => onHover?.(idxFromEvent(e))}
         onMouseLeave={() => onHover?.(null)}
         onClick={onSelect ? (e) => onSelect(idxFromEvent(e)) : undefined}
    >
      {/* Y grid + labels */}
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {yLabel(t)}
          </text>
        </g>
      ))}

      {/* X tick labels */}
      {xTicks.map(({ i, label }, k) => (
        <text key={k} x={xAt(i)} y={H - padB + 18} textAnchor="middle"
              fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
          {label}
        </text>
      ))}

      {/* Filled areas (if enabled) — strategies only, not benchmark */}
      {filled && series.filter(s => !s.isBenchmark).map((s, k) => {
        const baseY = yAt(yTicks[0]);
        const path = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
        return (
          <path key={`fill-${k}`}
                d={`${path} L${xAt(s.values.length - 1)},${baseY} L${xAt(0)},${baseY} Z`}
                fill={s.color} opacity="0.08" />
        );
      })}

      {/* Lines */}
      {series.map((s, k) => {
        const path = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
        return (
          <path key={k} d={path} fill="none"
                stroke={s.color} strokeWidth={s.isBenchmark ? 1.5 : 2}
                strokeDasharray={s.dashed ? '4 4' : 'none'}
                strokeLinejoin="round" strokeLinecap="round" />
        );
      })}

      {/* Hover crosshair */}
      {hoveredIdx != null && (
        <g>
          <line x1={xAt(hoveredIdx)} x2={xAt(hoveredIdx)}
                y1={padT} y2={H - padB}
                stroke={theme.axis} strokeWidth="1" strokeDasharray="2 3" opacity="0.4" />
          {series.map((s, k) => (
            <circle key={k} cx={xAt(hoveredIdx)} cy={yAt(s.values[hoveredIdx])}
                    r="4" fill={theme.bg} stroke={s.color} strokeWidth="2" />
          ))}
        </g>
      )}
    </svg>
  );
}

// ============================================================
// EventStudyChart — bar chart of avg forward return + significance
// ============================================================
function EventStudyChart({ data, height = 280, theme, accent, showWinRate = false,
                           benchmarkAvg = 0, ariaLabel }) {
  const W = 1000, H = height;
  const padL = 56, padR = 16, padT = 24, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const series = showWinRate ? data.map(d => d.win_rate) : data.map(d => d.avg_return);
  const benchVal = showWinRate ? 0.5 : benchmarkAvg;
  const max = Math.max(...series, benchVal) * 1.1;
  const min = 0;

  const xAt = (i) => padL + (i + 0.5) * (innerW / data.length);
  const yAt = (v) => padT + innerH - ((v - min) / (max - min)) * innerH;
  const barW = (innerW / data.length) * 0.7;

  const yTicks = showWinRate
    ? [0, 0.25, 0.5, 0.75, 1.0]
    : [0, 0.01, 0.02, 0.03, 0.04];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img"
         aria-label={ariaLabel || (showWinRate
           ? 'Event study bar chart of win rate by horizon'
           : 'Event study bar chart of average forward return by horizon')}
         style={{ width: '100%', height: '100%', display: 'block' }}>
      {/* Y grid + labels */}
      {yTicks.filter(t => t <= max).map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {(t * 100).toFixed(showWinRate ? 0 : 1)}%
          </text>
        </g>
      ))}

      {/* Benchmark line */}
      <line x1={padL} x2={W - padR} y1={yAt(benchVal)} y2={yAt(benchVal)}
            stroke={theme.benchmark || '#dc2626'} strokeWidth="1.5" strokeDasharray="6 4" />

      {/* Bars */}
      {data.map((d, i) => {
        const v = showWinRate ? d.win_rate : d.avg_return;
        const significant = !showWinRate && Math.abs(d.t_stat) >= 2;
        return (
          <g key={i}>
            <rect x={xAt(i) - barW / 2} y={yAt(v)}
                  width={barW} height={yAt(0) - yAt(v)}
                  fill={significant ? accent.dark : accent.color}
                  rx="2" />
            {significant && (
              <text x={xAt(i)} y={yAt(v) - 6} textAnchor="middle"
                    fontSize="10" fill={accent.dark}
                    fontFamily="JetBrains Mono, monospace" fontWeight="600">
                t={d.t_stat.toFixed(1)}
              </text>
            )}
          </g>
        );
      })}

      {/* X tick labels */}
      {data.map((d, i) => (
        <text key={i} x={xAt(i)} y={H - padB + 18} textAnchor="middle"
              fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
          {d.h}
        </text>
      ))}
      <text x={W / 2} y={H - 6} textAnchor="middle"
            fontSize="11" fill={theme.axis}>
        Months after selection
      </text>
    </svg>
  );
}

// ============================================================
// DrawdownChart — underwater plot
// ============================================================
function DrawdownChart({ series, months, height = 140, theme, accent, label }) {
  const W = 1000, H = height;
  const padL = 56, padR = 16, padT = 12, padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const min = Math.min(...series);
  const max = 0;

  const xAt = (i) => padL + (i / (series.length - 1)) * innerW;
  const yAt = (v) => padT + ((max - v) / (max - min)) * innerH;

  const path = series.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
  const fillPath = `${path} L${xAt(series.length - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`;

  // Mark max drawdown
  const minIdx = series.indexOf(min);

  const yTicks = [0, min * 0.5, min];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img"
         aria-label={`Drawdown chart${label ? ` for ${label}` : ''}, maximum drawdown ${(min * 100).toFixed(1)}%`}
         style={{ width: '100%', height: '100%', display: 'block' }}>
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="10" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      <path d={fillPath} fill={accent.color} opacity="0.18" />
      <path d={path} fill="none" stroke={accent.color} strokeWidth="1.5" />

      {/* Which series is plotted (only the best sleeve is shown) */}
      {label && (
        <text x={W - padR - 4} y={padT + 10} textAnchor="end"
              fontSize="10" fill={theme.axis}
              fontFamily="JetBrains Mono, monospace">
          drawdown · {label}
        </text>
      )}

      {/* Max DD marker */}
      <circle cx={xAt(minIdx)} cy={yAt(min)} r="3.5"
              fill={accent.dark} stroke={theme.bg} strokeWidth="1.5" />
      <text x={xAt(minIdx)} y={yAt(min) + 16} textAnchor="middle"
            fontSize="10" fill={accent.dark}
            fontFamily="JetBrains Mono, monospace" fontWeight="600">
        max DD {(min * 100).toFixed(1)}%
      </text>
    </svg>
  );
}

// ============================================================
// RollingSharpeChart — multi-series rolling Sharpe with y=0 reference
// ============================================================
function RollingSharpeChart({
  series,            // { name, values, color, isBenchmark? }[]
  months,            // Date[]
  height = 220,
  theme,
  refSharpe,         // optional: SPY full-period Sharpe to draw as reference
}) {
  const W = 1000, H = height;
  const padL = 56, padR = 16, padT = 16, padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  // Skip leading zeros (window warm-up) when computing range
  const allVals = series.flatMap(s => s.values).filter(v => v !== 0 && Number.isFinite(v));
  const min = allVals.length ? Math.min(...allVals, 0) : -1;
  const max = allVals.length ? Math.max(...allVals, 0) : 1;
  const pad = (max - min) * 0.08 || 0.5;
  const yMin = min - pad;
  const yMax = max + pad;
  const yRange = yMax - yMin || 1;

  const xAt = (i) => padL + (i / (months.length - 1)) * innerW;
  const yAt = (v) => padT + innerH - ((v - yMin) / yRange) * innerH;

  // Y ticks: 4 evenly spaced
  const yTicks = useMemo(() => {
    const step = (yMax - yMin) / 4;
    return [0, 1, 2, 3, 4].map(i => yMin + step * i);
  }, [yMin, yMax]);

  const xTicks = useMemo(() => {
    const out = [];
    let lastYear = -1;
    months.forEach((d, i) => {
      const y = d.getFullYear();
      if (y !== lastYear) {
        out.push({ i, label: y });
        lastYear = y;
      }
    });
    return out;
  }, [months]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img"
         aria-label={`Rolling 12-month Sharpe ratio chart, ${series.length} series`}
         style={{ width: '100%', height: '100%', display: 'block' }}>
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {t.toFixed(1)}
          </text>
        </g>
      ))}

      {/* y=0 reference line */}
      {yMin < 0 && yMax > 0 && (
        <line x1={padL} x2={W - padR} y1={yAt(0)} y2={yAt(0)}
              stroke={theme.axis} strokeWidth="1.25" opacity="0.5" />
      )}

      {/* SPY full-period reference (dashed) */}
      {refSharpe != null && refSharpe >= yMin && refSharpe <= yMax && (
        <g>
          <line x1={padL} x2={W - padR} y1={yAt(refSharpe)} y2={yAt(refSharpe)}
                stroke={theme.benchmark || '#dc2626'} strokeWidth="1.2"
                strokeDasharray="6 4" opacity="0.7" />
          <text x={W - padR - 4} y={yAt(refSharpe) - 4} textAnchor="end"
                fontSize="10" fill={theme.benchmark || '#dc2626'}
                fontFamily="JetBrains Mono, monospace">
            SPY full-period {refSharpe.toFixed(2)}
          </text>
        </g>
      )}

      {xTicks.map(({ i, label }, k) => (
        <text key={k} x={xAt(i)} y={H - padB + 18} textAnchor="middle"
              fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
          {label}
        </text>
      ))}

      {/* Lines — skip warm-up zeros by starting where values become non-zero */}
      {series.map((s, k) => {
        const segs = [];
        let cur = '';
        s.values.forEach((v, i) => {
          if (v === 0 || !Number.isFinite(v)) {
            if (cur) { segs.push(cur); cur = ''; }
            return;
          }
          cur += `${cur ? 'L' : 'M'}${xAt(i)},${yAt(v)} `;
        });
        if (cur) segs.push(cur);
        return segs.map((d, j) => (
          <path key={`${k}-${j}`} d={d} fill="none"
                stroke={s.color} strokeWidth={s.isBenchmark ? 1.5 : 2}
                strokeDasharray={s.isBenchmark ? '4 4' : 'none'}
                strokeLinejoin="round" strokeLinecap="round" />
        ));
      })}
    </svg>
  );
}

// ============================================================
// UnderwaterChart — multi-series drawdown overlay
// ============================================================
function UnderwaterChart({
  series,            // { name, values, color, isBenchmark? }[]
  months,
  height = 200,
  theme,
}) {
  const W = 1000, H = height;
  const padL = 56, padR = 16, padT = 12, padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const allVals = series.flatMap(s => s.values);
  const min = Math.min(...allVals, 0);
  const max = 0;

  const xAt = (i) => padL + (i / (months.length - 1)) * innerW;
  const yAt = (v) => padT + ((max - v) / (max - min || 1)) * innerH;

  const yTicks = [0, min * 0.33, min * 0.66, min];

  const xTicks = useMemo(() => {
    const out = [];
    let lastYear = -1;
    months.forEach((d, i) => {
      const y = d.getFullYear();
      if (y !== lastYear) {
        out.push({ i, label: y });
        lastYear = y;
      }
    });
    return out;
  }, [months]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img"
         aria-label={`Underwater drawdown chart, ${series.length} series, deepest ${(min * 100).toFixed(1)}%`}
         style={{ width: '100%', height: '100%', display: 'block' }}>
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="10" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      {xTicks.map(({ i, label }, k) => (
        <text key={k} x={xAt(i)} y={H - padB + 16} textAnchor="middle"
              fontSize="10" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
          {label}
        </text>
      ))}

      {/* Filled benchmark area first, then strategy lines on top */}
      {series.filter(s => s.isBenchmark).map((s, k) => {
        const path = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
        const fill = `${path} L${xAt(s.values.length - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`;
        return (
          <g key={`b-${k}`}>
            <path d={fill} fill={s.color} opacity="0.08" />
            <path d={path} fill="none" stroke={s.color} strokeWidth="1.25"
                  strokeDasharray="4 4" />
          </g>
        );
      })}

      {series.filter(s => !s.isBenchmark).map((s, k) => {
        const path = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
        return (
          <path key={`s-${k}`} d={path} fill="none"
                stroke={s.color} strokeWidth="1.75"
                strokeLinejoin="round" strokeLinecap="round" />
        );
      })}
    </svg>
  );
}

// ============================================================
// SectorBarChart — horizontal bar of pick counts by GICS sector
// ============================================================
function SectorBarChart({ sectors, theme, accent, height = 220 }) {
  // sectors: { "Information Technology": 47, ... }
  const entries = Object.entries(sectors).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    return (
      <div style={{ fontSize: 12, color: theme.muted, fontStyle: 'italic',
                     padding: '20px 0', textAlign: 'center' }}>
        No sector data available.
      </div>
    );
  }
  const max = entries[0][1];
  const total = entries.reduce((acc, [, v]) => acc + v, 0);
  const rowH = Math.min(28, Math.max(18, height / entries.length));
  const labelW = 170;

  return (
    <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
      {entries.map(([name, count]) => {
        const pct = (count / total) * 100;
        const barPct = (count / max) * 100;
        return (
          <div key={name} style={{
            display: 'grid', gridTemplateColumns: `${labelW}px 1fr 64px`,
            alignItems: 'center', gap: 8, height: rowH,
          }}>
            <div style={{
              fontFamily: 'inherit', fontSize: 11, color: theme.text,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }} title={name}>
              {name}
            </div>
            <div style={{ position: 'relative', height: rowH - 8,
                           background: theme.bg, borderRadius: 3,
                           border: `1px solid ${theme.border}` }}>
              <div style={{
                position: 'absolute', left: 0, top: 0, bottom: 0,
                width: `${barPct}%`, background: accent.color,
                borderRadius: 3, opacity: 0.85,
              }} />
            </div>
            <div style={{ textAlign: 'right', fontWeight: 600, color: theme.text }}>
              {count} <span style={{ color: theme.muted, fontWeight: 400 }}>
                ({pct.toFixed(0)}%)
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// MonthlyHeatmap — calendar-style year × month grid
// ============================================================
function MonthlyHeatmap({ returns, months, theme, accent, compact = false }) {
  // Group by year → month
  const byYearMonth = {};
  months.forEach((d, i) => {
    const y = d.getFullYear();
    const m = d.getMonth(); // 0..11
    byYearMonth[y] = byYearMonth[y] || {};
    byYearMonth[y][m] = returns[i];
  });

  const years = Object.keys(byYearMonth).sort();
  const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // Color scale: diverging green/red. Cap at ±15%.
  const colorFor = (v) => {
    if (v == null) return 'transparent';
    const cap = 0.15;
    const t = Math.max(-1, Math.min(1, v / cap));
    if (t >= 0) {
      // green
      const alpha = 0.15 + 0.75 * t;
      return `rgba(16, 185, 129, ${alpha.toFixed(3)})`;
    } else {
      const alpha = 0.15 + 0.75 * (-t);
      return `rgba(239, 68, 68, ${alpha.toFixed(3)})`;
    }
  };

  // Hue-preserving dark text on light cells; white only once the fill is saturated.
  const textColorFor = (v) => {
    if (v == null) return theme.text;
    if (Math.abs(v) > 0.10) return '#fff';
    return v >= 0 ? '#064e3b' : '#7f1d1d';
  };

  const cellH = compact ? 22 : 28;
  const labelW = 44;

  return (
    <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: theme.axis }}>
      {/* Header row */}
      <div style={{ display: 'grid', gridTemplateColumns: `${labelW}px repeat(12, 1fr) 60px`, gap: 2, marginBottom: 4 }}>
        <div></div>
        {monthLabels.map(m => (
          <div key={m} style={{ textAlign: 'center', fontSize: 10 }}>{m}</div>
        ))}
        <div style={{ textAlign: 'right', fontSize: 10, paddingRight: 4 }}>YTD</div>
      </div>
      {years.map(y => {
        const row = byYearMonth[y];
        const ytdReturn = Array.from({ length: 12 }, (_, m) => row[m] || 0)
          .reduce((acc, r) => acc * (1 + r), 1) - 1;
        return (
          <div key={y} style={{
            display: 'grid', gridTemplateColumns: `${labelW}px repeat(12, 1fr) 60px`,
            gap: 2, marginBottom: 2,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', fontSize: 11, color: theme.text, fontWeight: 500 }}>
              {y}
            </div>
            {monthLabels.map((_, m) => {
              const v = row[m];
              return (
                <div key={m} title={v != null ? `${y}-${String(m+1).padStart(2,'0')}: ${(v*100).toFixed(2)}%` : ''}
                     style={{
                       height: cellH,
                       background: colorFor(v),
                       border: v != null ? `1px solid ${theme.grid}` : `1px dashed ${theme.grid}`,
                       borderRadius: 3,
                       display: 'flex', alignItems: 'center', justifyContent: 'center',
                       fontSize: 9.5,
                       color: textColorFor(v),
                       fontWeight: 500,
                     }}>
                  {v != null && !compact ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}` : ''}
                </div>
              );
            })}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
              fontSize: 11, fontWeight: 600,
              color: ytdReturn >= 0 ? '#059669' : '#dc2626',
              paddingRight: 4,
            }}>
              {ytdReturn >= 0 ? '+' : ''}{(ytdReturn * 100).toFixed(1)}%
            </div>
          </div>
        );
      })}
      {/* Color-scale legend */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, fontSize: 10 }}>
        <span>Monthly return</span>
        {[-0.15, -0.075, 0, 0.075, 0.15].map(v => (
          <span key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            <span style={{
              width: 14, height: 10, borderRadius: 2, display: 'inline-block',
              background: colorFor(v), border: `1px solid ${theme.grid}`,
            }} />
            {`${v > 0 ? '+' : ''}${(v * 100).toFixed(0)}%`}
          </span>
        ))}
        <span style={{ color: theme.muted || theme.axis }}>capped at ±15%</span>
      </div>
    </div>
  );
}

// ============================================================
// Sparkline — tiny inline chart for cohort rows
// ============================================================
function Sparkline({ values, color, width = 80, height = 22 }) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const xAt = (i) => (i / (values.length - 1)) * width;
  const yAt = (v) => height - ((v - min) / range) * height;
  const path = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(v)}`).join(' ');
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

// ============================================================
// Parameter help + validated numeric input (shared by V1/V2 sidebars)
// ============================================================
const PARAM_HELP = {
  lookback: 'Years of price history the backtest runs over.',
  topN: 'How many of the highest-momentum names go into the portfolio each month.',
  holds: 'How long each monthly cohort is held, in months. Multiple selections run as parallel sleeves.',
  rankLookback: 'Trailing window (months) of return used to rank momentum each month.',
  skipMonths: 'Months skipped between the ranking window and formation — the window ends this many months before the pick. 1 dodges short-term reversal (classic 12-1 momentum = rank lookback 11 + skip 1).',
  costBps: 'Trading cost in basis points charged on each side of a trade, so replacing the whole book costs twice this. Returns are shown net of it. Set 0 to see gross. Note ETF universes also carry expense-ratio drag that no bps figure captures.',
  regimeMode: 'Experiment: sit out the month after the benchmark closes below its moving average, holding cash or the benchmark instead. On this history it gave up a lot of return without reliably improving drawdown, so the banner above the trade list is the useful part. Compare the curves yourself rather than trusting the label. The state is always shown; this only adds the filtered series.',
  regimeMaDays: 'Length of the benchmark moving average, in trading bars, that defines the risk-on and risk-off state. 200 is the convention. Longer values invite curve-fitting, so the banner reports its sample size.',
  horizon: 'How many months forward the event study tracks each cohort after selection.',
  benchmark: 'Ticker the strategy is measured against.',
  riskFree: 'Annual risk-free rate (%) subtracted from returns when computing the Sharpe ratio.',
  topPerfN: 'How many tickers to show in the leaderboard.',
  topPerfDays: 'Calendar-day window the return is measured over.',
};

const DEFAULT_RANGES = {
  lookback: { min: 1, max: 25 },
  topN: { min: 1, max: 100 },
  rankLookback: { min: 1, max: 12 },
  skipMonths: { min: 0, max: 2 },
  costBps: { min: 0, max: 100 },
  regimeMaDays: { min: 20, max: 400 },
  regimeMode: { values: ['off', 'cash', 'benchmark'] },
  horizon: { min: 1, max: 24 },
};

// Effective topN ceiling for the selected universe (server rejects topN >= size).
function maxTopNFor(universesInfo, universeKey) {
  const ranges = (universesInfo && universesInfo.ranges) || DEFAULT_RANGES;
  const uni = universesInfo?.universes?.[universeKey || 'sp500'];
  return uni?.max_topN != null ? Math.min(uni.max_topN, ranges.topN.max) : ranges.topN.max;
}

// Returns human-readable problems with the current params ([] = all good).
function paramIssues(params, universesInfo) {
  const ranges = (universesInfo && universesInfo.ranges) || DEFAULT_RANGES;
  const topNMax = maxTopNFor(universesInfo, params.universe);
  const issues = [];
  const check = (key, label, min, max) => {
    const v = params[key];
    if (!Number.isFinite(v) || v < min || v > max) issues.push(`${label} must be ${min}–${max}`);
  };
  const skipRange = ranges.skipMonths || DEFAULT_RANGES.skipMonths;
  const costRange = ranges.costBps || DEFAULT_RANGES.costBps;
  check('lookback', 'Lookback', ranges.lookback.min, ranges.lookback.max);
  check('topN', 'Top N', ranges.topN.min, topNMax);
  check('rankLookback', 'Rank lookback', ranges.rankLookback.min, ranges.rankLookback.max);
  check('skipMonths', 'Skip months', skipRange.min, skipRange.max);
  check('costBps', 'Cost (bps)', costRange.min, costRange.max);
  // Read through the DEFAULT_RANGES fallback: paramIssues uses the server's
  // ranges whenever /api/universes succeeded, and a key missing there would throw
  // inside the render path and take the Run button with it.
  const maRange = ranges.regimeMaDays || DEFAULT_RANGES.regimeMaDays;
  check('regimeMaDays', 'Regime MA (bars)', maRange.min, maRange.max);
  // regimeMode is a string, so the Number.isFinite-based check above cannot see it.
  const modeValues = (ranges.regimeMode || DEFAULT_RANGES.regimeMode).values;
  if (params.regimeMode != null && !modeValues.includes(params.regimeMode)) {
    issues.push(`Regime mode must be one of ${modeValues.join(', ')}`);
  }
  check('horizon', 'Event horizon', ranges.horizon.min, ranges.horizon.max);
  if (!params.benchmark || !String(params.benchmark).trim()) issues.push('Benchmark ticker is required');
  return issues;
}

function HelpTip({ help, theme }) {
  const [open, setOpen] = useState(false);
  if (!help) return null;
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}
          onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <span tabIndex={0} title={help}
            onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
            style={{ display: 'inline-flex', cursor: 'help', outline: 'none',
                     color: theme.muted || theme.axis }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
          <line x1="12" y1="11" x2="12" y2="16.5" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" />
          <circle cx="12" cy="7.5" r="1.3" fill="currentColor" />
        </svg>
      </span>
      {open && (
        <span role="tooltip" style={{
          position: 'absolute', bottom: '130%', left: -8, zIndex: 40,
          width: 190, padding: '7px 9px', borderRadius: 5,
          background: theme.text, color: '#fff',
          fontSize: 11, fontWeight: 400, lineHeight: 1.5,
          letterSpacing: 0, textTransform: 'none',
          boxShadow: '0 2px 8px rgba(0,0,0,0.18)', pointerEvents: 'none',
        }}>
          {help}
        </span>
      )}
    </span>
  );
}

// Numeric input that flags out-of-range/empty values inline and clamps on blur.
function NumField({ id, label, value, min, max, help, onChange,
                    theme, labelStyle, inputStyle, wrapStyle }) {
  const invalid = !Number.isFinite(value) || value < min || value > max;
  return (
    <div style={wrapStyle}>
      <label htmlFor={id}
             style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
        {label}
        <HelpTip help={help} theme={theme} />
      </label>
      <input id={id} type="number" min={min} max={max}
             value={Number.isFinite(value) ? value : ''}
             onChange={e => onChange(e.target.value === '' ? NaN : +e.target.value)}
             onBlur={() => {
               if (invalid) onChange(Math.min(max, Math.max(min, Number.isFinite(value) ? value : min)));
             }}
             style={{ ...inputStyle, ...(invalid ? { border: '1px solid #dc2626' } : {}) }} />
      {invalid && (
        <div style={{ fontSize: 10, color: '#b91c1c', marginTop: 3,
                       fontFamily: 'JetBrains Mono, monospace' }}>
          {min}–{max}
        </div>
      )}
    </div>
  );
}

// ============================================================
// RebalancePanel — month-by-month trade list for the top-N portfolio:
// what to buy, what to sell, what to keep. Latest month first (actionable),
// older transitions below as history. CSV export for taking it to a broker.
// ============================================================
// ============================================================
// RegimeBanner — what the market is doing, and what happened last time it did
// this. Evidence only: it never tells the user what to do.
// ============================================================
const REGIME_BADGES = {
  none:   { label: 'NO REGIME DATA', color: '#57534e', bg: '#f5f5f4', border: '#d6d3d1' },
  off:    { label: 'RISK OFF',       color: '#b45309', bg: '#fffbeb', border: '#fcd34d' },
  unwind: { label: 'FACTOR UNWIND',  color: '#b91c1c', bg: '#fef2f2', border: '#fecaca' },
  on:     { label: 'RISK ON',        color: '#047857', bg: '#ecfdf5', border: '#a7f3d0' },
};

function regimeKind(regime) {
  // `above` is null for the whole of any run too short to fill the MA window, and
  // null fails both === false and === true. Without this explicit case the banner
  // would fall through to a green RISK ON asserting a state the data cannot
  // support, which is the same fabrication I3 forbids in the series.
  if (!regime || regime.above == null) return 'none';
  if (regime.above === false) return 'off';
  // Market above its average while the strategy bleeds: the drawdown is
  // momentum-specific, not the market's. mtd is null on a month-end run, so this
  // must be an optional read.
  if (regime.mtd && regime.mtd.spread != null && regime.mtd.spread <= -0.05) return 'unwind';
  return 'on';
}

function RegimeBanner({ regime, benchmark = 'SPY', theme, compact = false }) {
  if (!regime) return null;
  const kind = regimeKind(regime);
  const badge = REGIME_BADGES[kind];
  const pct = (v, d = 1, signed = true) => (v == null ? '—' : fmtPct(v, d, signed));
  // Math.abs(null) is 0, which would print "0.0%" instead of an em-dash, so the
  // null has to survive the abs.
  const absOrNull = (v) => (v == null ? null : Math.abs(v));
  const ordinal = (n) => {
    const r100 = n % 100;
    if (r100 >= 11 && r100 <= 13) return `${n}th`;
    return `${n}${['th', 'st', 'nd', 'rd'][n % 10] || 'th'}`;
  };

  const chip = (
    <span style={{
      padding: compact ? '2px 7px' : '3px 9px', fontSize: compact ? 9.5 : 10,
      fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
      color: badge.color, background: badge.bg,
      border: `1px solid ${badge.border}`, borderRadius: 4, whiteSpace: 'nowrap',
    }}>{badge.label}</span>
  );

  if (compact) {
    return (
      <span title={kind === 'none'
              ? `${benchmark} history is shorter than the ${regime.ma_days}-day average`
              : `${benchmark} ${pct(regime.dist)} vs its ${regime.ma_days}d average, as of ${regime.as_of}`}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {chip}
        {regime.dist != null && (
          <span style={{ fontSize: 10, color: theme.muted,
                          fontFamily: 'JetBrains Mono, monospace' }}>
            {pct(regime.dist)}
          </span>
        )}
      </span>
    );
  }

  const state = kind === 'none'
    ? `${benchmark} history covers ${regime.coverage_months ?? 0} months of the ${regime.ma_days}-day average warm-up, so there is no state yet. Raise Lookback.`
    // The words already carry the direction, so these read unsigned: "+6.5%
    // ABOVE" and "-2.2% off its high" both say the same thing twice, and the
    // second is wrong-looking.
    : (kind === 'off'
        ? `${benchmark} is ${pct(absOrNull(regime.dist), 1, false)} below its ${regime.ma_days}d average, ${regime.days_in_state ?? '—'} days now, and ${pct(absOrNull(regime.drawdown), 1, false)} off its 1-year high.`
        : (kind === 'unwind'
            ? `${benchmark} is ${pct(regime.dist, 1, false)} above its ${regime.ma_days}d average and only ${pct(absOrNull(regime.drawdown), 1, false)} off its high, so this drawdown is momentum-specific rather than the market's.`
            : `${benchmark} is ${pct(regime.dist, 1, false)} above its ${regime.ma_days}d average, ${regime.days_in_state ?? '—'} days now, ${pct(absOrNull(regime.drawdown), 1, false)} off its 1-year high.`));

  // The bucket whose evidence is relevant is the one the CURRENT state implies.
  const cond = regime.conditional || {};
  const bucket = kind === 'off' ? cond.risk_off : cond.risk_on;
  const bucketWord = kind === 'off' ? 'risk-off' : 'risk-on';
  const evidence = (!bucket || !bucket.n || bucket.mean == null)
    ? `No comparable months in this window.`
    : `In the ${bucket.n} prior months that began ${bucketWord}, this sleeve returned ${pct(bucket.mean, 2)}/mo on average against ${pct(bucket.bench_mean, 2)} for ${benchmark} (spread ${pct(bucket.spread_mean, 2)}${bucket.t_stat_spread != null ? `, t=${bucket.t_stat_spread.toFixed(2)}` : ''}).`;

  const v = regime.vol || {};
  return (
    <div style={{
      border: `1px solid ${badge.border}`, background: badge.bg,
      borderRadius: 6, padding: '10px 12px', marginTop: 8,
      display: 'flex', flexDirection: 'column', gap: 5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {chip}
        <span style={{ fontSize: 11.5, color: theme.text, lineHeight: 1.5 }}>{state}</span>
      </div>
      <div style={{ fontSize: 11, color: theme.muted, lineHeight: 1.55 }}>{evidence}</div>
      <div style={{ fontSize: 10.5, color: theme.muted,
                     fontFamily: 'JetBrains Mono, monospace' }}>
        {regime.mtd && (
          <>month to date {pct(regime.mtd.strategy, 1)} vs {benchmark} {pct(regime.mtd.benchmark, 1)}
            {' '}(spread {pct(regime.mtd.spread, 1)}){regime.mtd.is_partial ? ' · partial month' : ''} · </>
        )}
        {v.vol_ann != null
          ? <>{benchmark} {v.window}d vol {pct(v.vol_ann, 1, false)} annualized, {ordinal(Math.round(v.percentile * 100))} percentile of {v.n} bars</>
          : <>vol percentile unavailable</>}
        {regime.as_of && <> · regime as of {regime.as_of}</>}
      </div>
      {/* Only claim filter behaviour while the filter is actually running: with
          the mode off nothing is being counted as invested or otherwise. */}
      {regime.mode && regime.mode !== 'off' && regime.warmup_months > 0 && (
        <div style={{ fontSize: 10, color: theme.muted }}>
          First {regime.warmup_months} month{regime.warmup_months === 1 ? '' : 's'} predate
          {' '}{regime.ma_days}-day average coverage and are held invested rather than filtered.
        </div>
      )}
    </div>
  );
}

const REBAL_COLORS = {
  buy:  { bg: '#ecfdf5', border: '#a7f3d0', text: '#047857' },
  sell: { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c' },
};

function RebalanceChip({ ticker, group, kind, theme, showGroup,
                         earningsDate = null, warnBefore = null }) {
  const c = REBAL_COLORS[kind] || { bg: theme.bg, border: theme.border, text: theme.text };
  // Days to the report, and whether it lands before the next month-end rebalance,
  // i.e. while you would still be holding what you buy today.
  let days = null, urgent = false;
  if (earningsDate) {
    const d = new Date(earningsDate + 'T00:00:00');
    days = Math.round((d - new Date(new Date().toDateString())) / 86400000);
    urgent = warnBefore ? earningsDate <= warnBefore : days <= 30;
  }
  // A date already in the past gets no badge at all rather than an urgent one:
  // the report has happened, so there is nothing to warn about. An unparseable
  // date yields NaN, which fails this guard and also degrades to no badge.
  const badge = earningsDate && days != null && days >= 0 && (
    <span title={`Reports ${earningsDate}${urgent ? ', before the next month-end rebalance' : ''}`}
          style={{
            marginLeft: 1, padding: '0 3px', fontSize: 8.5, fontWeight: 700,
            fontFamily: 'Inter, sans-serif', borderRadius: 2,
            color: urgent ? '#b45309' : theme.muted,
            background: urgent ? '#fef3c7' : 'transparent',
            border: `1px solid ${urgent ? '#fcd34d' : theme.border}`,
          }}>
      {days != null && days >= 0 ? `E${days}d` : 'E'}
    </span>
  );
  return (
    <span title={group || undefined} style={{
      padding: '2px 7px', fontSize: 11,
      fontFamily: 'JetBrains Mono, monospace', fontWeight: 600,
      background: c.bg, border: `1px solid ${c.border}`, color: c.text,
      borderRadius: 4, display: 'inline-flex', gap: 4, alignItems: 'baseline',
    }}>
      {ticker}
      {showGroup && group && (
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 400,
                        fontSize: 10, opacity: 0.75 }}>{group}</span>
      )}
      {badge}
    </span>
  );
}

// One collapsed history row: month, buy/sell chips, kept count, realized-return
// badge — expands on click to the per-pick forward returns (worst flagged).
function RebalanceHistoryRow({ r, theme }) {
  const [open, setOpen] = useState(false);
  const hasAttr = r.pick_returns && Object.keys(r.pick_returns).length > 0;
  const chipWrap = { display: 'flex', flexWrap: 'wrap', gap: 4 };

  const entries = hasAttr
    ? r.picks.map(t => ({ ticker: t, ret: r.pick_returns[t] }))
        .sort((a, b) => ((b.ret ?? -Infinity) - (a.ret ?? -Infinity)))
    : [];
  const worst = entries.reduce(
    (w, e) => (e.ret != null && (w == null || e.ret < w.ret) ? e : w), null);

  return (
    <div style={{ borderTop: `1px solid ${theme.border}` }}>
      <div role={hasAttr ? 'button' : undefined}
           tabIndex={hasAttr ? 0 : undefined}
           aria-expanded={hasAttr ? open : undefined}
           onClick={hasAttr ? () => setOpen(o => !o) : undefined}
           onKeyDown={hasAttr ? (e) => {
             if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(o => !o); }
           } : undefined}
           style={{
             display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap',
             padding: '7px 0', cursor: hasAttr ? 'pointer' : 'default', outline: 'none',
           }}>
        <span style={{ fontSize: 11, fontWeight: 600, width: 56, flexShrink: 0,
                        fontFamily: 'JetBrains Mono, monospace' }}>
          {r.month}
        </span>
        <div style={{ ...chipWrap, flex: 1, minWidth: 220 }}>
          {r.buys.map(t => <RebalanceChip key={`b-${t}`} ticker={t} kind="buy"
            group={r.meta?.[t]} theme={theme} showGroup={false} />)}
          {r.sells.map(t => <RebalanceChip key={`s-${t}`} ticker={t} kind="sell"
            group={r.meta?.[t]} theme={theme} showGroup={false} />)}
        </div>
        <span style={{ fontSize: 10.5, color: theme.muted, flexShrink: 0,
                        fontFamily: 'JetBrains Mono, monospace' }}>
          {r.holds.length} kept
        </span>
        {r.return_pct != null && (
          <span title={r.fwd_partial
                  ? `Month-to-date through ${r.fwd_as_of || 'the latest bar'} — the following month is still in progress`
                  : "Equal-weight return of this month's picks over the following month"}
                style={{ fontSize: 10.5, fontWeight: 600, flexShrink: 0,
                         fontFamily: 'JetBrains Mono, monospace',
                         color: r.return_pct >= 0 ? '#047857' : '#b91c1c' }}>
            → {fmtPct(r.return_pct, 1)}{r.fwd_partial ? ' MTD' : ''}
          </span>
        )}
        {hasAttr && (
          <span aria-hidden="true"
                style={{ fontSize: 10, color: theme.muted, flexShrink: 0 }}>
            {open ? '▾' : '▸'}
          </span>
        )}
      </div>
      {open && hasAttr && (
        <div style={{ padding: '0 0 10px 66px', display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {entries.map(e => {
            const isWorst = worst && e.ticker === worst.ticker && e.ret != null && e.ret < 0;
            return (
              <span key={e.ticker} style={{
                padding: '2px 7px', fontSize: 10.5,
                fontFamily: 'JetBrains Mono, monospace',
                background: isWorst ? '#fef2f2' : theme.bg,
                border: `1px solid ${isWorst ? '#fecaca' : theme.border}`,
                borderRadius: 4, display: 'inline-flex', gap: 5, alignItems: 'baseline',
              }}>
                <span style={{ fontWeight: 600, color: theme.text }}>{e.ticker}</span>
                <span style={{ fontWeight: 600,
                                color: e.ret == null ? theme.muted
                                  : (e.ret >= 0 ? '#047857' : '#b91c1c') }}>
                  {e.ret == null ? 'n/a' : fmtPct(e.ret, 1)}
                </span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

// `onRecord` is optional: without it this panel renders exactly as it did before
// the ledger existed (I2). With it, a Record button sits beside Download CSV and
// `recordState` (idle|saving|saved|error|exists) plus `recordMessage` report back
// inline — the ledger panel below is where the record itself is read.
function RebalancePanel({ rebalance, topN, rankLookback, skipMonths = 0, universe = 'sp500',
                          regime = null, benchmark = 'SPY',
                          theme, accent, headingFont,
                          onRecord, recordState = 'idle', recordMessage, recordedAsOf }) {
  // Earnings dates the server had cached arrive with the backtest; anything it
  // did not are fetched here, after render, so a cold lookup never delays the
  // dashboard. Keyed by the row so a new run discards the previous answers.
  const [lateEarnings, setLateEarnings] = useState({});
  const liveRow = (rebalance && rebalance.length) ? rebalance[0] : null;
  const watchKey = liveRow ? (liveRow.earnings_watch || []).join(',') : '';
  useEffect(() => {
    setLateEarnings({});
    if (!liveRow || !liveRow.is_live) return;
    const watch = liveRow.earnings_watch || [];
    const missing = watch.filter(t => !(liveRow.earnings || {})[t]);
    if (!missing.length || typeof fetchEarnings !== 'function') return;
    let live = true;
    fetchEarnings(missing).then(found => { if (live) setLateEarnings(found || {}); });
    return () => { live = false; };
  }, [watchKey]);

  if (!rebalance || !rebalance.length) return null;
  const latest = rebalance[0];
  const earningsFor = (t) => (latest.earnings || {})[t] || lateEarnings[t] || null;
  const history = rebalance.slice(1);
  const showGroup = universe !== 'sp500';

  const downloadCsv = () => {
    const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
    const fwd = (r, t) => (r.pick_returns?.[t] != null ? (r.pick_returns[t] * 100).toFixed(2) : '');
    const fwdStatus = (r, t) => (r.pick_returns?.[t] != null ? (r.fwd_partial ? 'mtd' : 'final') : '');
    const rows = [['month', 'action', 'ticker', 'group', 'fwd_return_pct', 'fwd_status']];
    rebalance.forEach(r => {
      r.buys.forEach(t => rows.push([r.month, 'BUY', t, r.meta?.[t] || '', fwd(r, t), fwdStatus(r, t)]));
      r.sells.forEach(t => rows.push([r.month, 'SELL', t, r.meta?.[t] || '', '', '']));
      r.holds.forEach(t => rows.push([r.month, 'KEEP', t, r.meta?.[t] || '', fwd(r, t), fwdStatus(r, t)]));
    });
    const csv = rows.map(r => r.map(q).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `rebalance_top${topN}_${latest.month}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const colTitle = (label, count, color) => (
    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                   textTransform: 'uppercase', color, marginBottom: 6 }}>
      {label} ({count})
    </div>
  );
  const chipWrap = { display: 'flex', flexWrap: 'wrap', gap: 4 };

  return (
    <div style={{
      background: theme.card, border: `1px solid ${theme.border}`,
      borderRadius: 6, padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                     alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700,
                         fontFamily: headingFont || 'inherit' }}>
            Monthly rebalance · trade list
          </div>
          <div style={{ fontSize: 11, color: theme.muted, marginTop: 2, lineHeight: 1.5 }}>
            Hold the top {topN} by trailing {rankLookback}m return{skipMonths > 0 ? ` (window ends ${skipMonths}m before formation)` : ''}; rebalance in full at each
            month-end close (the 1-month-hold sleeve). The top row is the live target as of
            the latest price — ranked over a trailing {rankLookback}-month window ending {skipMonths > 0 ? `${skipMonths}m back` : 'today'}{skipMonths > 0 ? '' : ', the same signal as Top performers'}. History rows below are the month-end sleeve.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexShrink: 0 }}>
          {onRecord && (
            <div style={{ display: 'flex', flexDirection: 'column',
                           alignItems: 'flex-end', gap: 3, maxWidth: 260 }}>
              <button onClick={onRecord}
                      disabled={!latest.is_live || recordState === 'saving'}
                      title={latest.is_live
                        ? 'Append this target to the local out-of-sample ledger (one entry per month)'
                        : 'Only a live target can be recorded'}
                      style={{
                        padding: '6px 12px', fontSize: 11, fontWeight: 600,
                        background: recordState === 'saved' ? '#ecfdf5' : '#fff',
                        color: recordState === 'saved' ? '#047857' : accent.dark,
                        border: `1px solid ${recordState === 'saved' ? '#a7f3d0' : accent.color}`,
                        borderRadius: 4, whiteSpace: 'nowrap',
                        cursor: (!latest.is_live || recordState === 'saving')
                          ? 'not-allowed' : 'pointer',
                        opacity: latest.is_live ? 1 : 0.55,
                      }}>
                {recordState === 'saving' ? 'Recording…'
                  : recordState === 'saved' ? 'Recorded' : 'Record this target'}
              </button>
              {recordMessage && (
                <span style={{
                  fontSize: 10, lineHeight: 1.45, textAlign: 'right',
                  color: recordState === 'error' ? '#b91c1c'
                    : (recordState === 'exists' ? '#b45309' : theme.muted),
                }}>
                  {recordMessage}
                </span>
              )}
              {!recordMessage && recordedAsOf && (
                <span style={{ fontSize: 10, color: theme.muted,
                                fontFamily: 'JetBrains Mono, monospace' }}>
                  ledger holds {recordedAsOf}
                </span>
              )}
            </div>
          )}
          <button onClick={downloadCsv} style={{
            padding: '6px 12px', fontSize: 11, fontWeight: 600,
            background: accent.dark, color: '#fff', border: 'none',
            borderRadius: 4, cursor: 'pointer', flexShrink: 0,
          }}>
            Download CSV
          </button>
        </div>
      </div>

      {/* Latest month — the actionable trades */}
      {/* The context the trade list was missing: directly above the buy list, so
          a full-turnover recommendation cannot be read without it. */}
      <RegimeBanner regime={regime} benchmark={benchmark} theme={theme} />

      <div style={{
        border: `1px solid ${theme.border}`, borderRadius: 6,
        padding: '12px 14px', marginTop: 8, background: theme.bg,
      }}>
        <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10,
                       fontFamily: 'JetBrains Mono, monospace' }}>
          {latest.is_live ? 'live' : latest.month}
          <span style={{ fontWeight: 400, color: theme.muted, marginLeft: 8,
                          fontFamily: 'Inter, sans-serif', fontSize: 11 }}>
            {latest.is_live ? `current target · as of ${latest.as_of} — ` : 'current target — '}
            {latest.buys.length} in, {latest.sells.length} out,
            {' '}{latest.holds.length} unchanged
          </span>
        </div>
        {latest.cost_pct != null && latest.cost_pct > 0 && (
          <div style={{ fontSize: 10.5, color: theme.muted, marginTop: -6, marginBottom: 10,
                         fontFamily: 'JetBrains Mono, monospace' }}
               title="One-way turnover of this single rebalance, and what it costs at the configured rate">
            {(latest.turnover * 100).toFixed(0)}% turnover ·{' '}
            {(latest.cost_pct * 100).toFixed(2)}% of capital ·{' '}
            ${(latest.cost_pct * 10000).toFixed(0)} per $10k
          </div>
        )}
        <div style={{ display: 'grid',
                       gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                       gap: 14 }}>
          <div>
            {colTitle('Buy', latest.buys.length, REBAL_COLORS.buy.text)}
            <div style={chipWrap}>
              {latest.buys.map(t => <RebalanceChip key={t} ticker={t} kind="buy"
                group={latest.meta?.[t]} theme={theme} showGroup={showGroup}
                earningsDate={earningsFor(t)}
                warnBefore={latest.earnings_warn_before} />)}
              {!latest.buys.length && <span style={{ fontSize: 11, color: theme.muted, fontStyle: 'italic' }}>nothing to buy</span>}
            </div>
          </div>
          <div>
            {colTitle('Sell', latest.sells.length, REBAL_COLORS.sell.text)}
            <div style={chipWrap}>
              {latest.sells.map(t => <RebalanceChip key={t} ticker={t} kind="sell"
                group={latest.meta?.[t]} theme={theme} showGroup={showGroup} />)}
              {!latest.sells.length && <span style={{ fontSize: 11, color: theme.muted, fontStyle: 'italic' }}>nothing to sell</span>}
            </div>
          </div>
          <div>
            {colTitle('Keep', latest.holds.length, theme.muted)}
            <div style={chipWrap}>
              {latest.holds.map(t => <RebalanceChip key={t} ticker={t} kind="keep"
                group={latest.meta?.[t]} theme={theme} showGroup={showGroup}
                earningsDate={earningsFor(t)}
                warnBefore={latest.earnings_warn_before} />)}
            </div>
          </div>
        </div>
      </div>

      {/* History — previous month-end transitions */}
      {history.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                         textTransform: 'uppercase', color: theme.muted, marginBottom: 6 }}>
            Previous rebalances
          </div>
          {history.map(r => (
            <RebalanceHistoryRow key={r.month} r={r} theme={theme} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// TopPerformersPanel — leaderboard of S&P 500 winners over a recent window
// Self-contained: owns its own params + fetch state. Drop into a dashboard
// with a `theme` and `accent` to restyle.
// ============================================================
// Durable-vs-spike badge: one big session driving the move is a squeeze
// candidate; sitting within 5% of the 52-week high is the classic durable-
// momentum profile.
function leaderboardBadge(r) {
  if (r.max_day_move != null && r.return_pct != null
      && r.max_day_move * r.return_pct > 0   // the big session must drive the move, not oppose it
      && Math.abs(r.max_day_move) >= 0.05
      && Math.abs(r.max_day_move) >= 0.5 * Math.abs(r.return_pct)) {
    return { label: 'spike', color: '#b45309', bg: '#fef3c7',
             title: 'One session drove ≥50% of the window return — squeeze/news candidate' };
  }
  if (r.below_52w_high != null && r.below_52w_high >= -0.05) {
    return { label: 'near high', color: '#047857', bg: '#ecfdf5',
             title: 'Within 5% of its 52-week high — durable-momentum profile' };
  }
  return null;
}

function TopPerformersPanel({ theme, accent, headingFont, universe = 'sp500', cacheInfo, universes }) {
  const universeDefaultTopN = { sp500: 10, global_etfs: 5, us_sector_etfs: 3 };
  const [topN, setTopN] = useState(universeDefaultTopN[universe] || 10);
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [sort, setSort] = useState(null);   // {key, dir} or null = rank order
  const runningRef = useRef(false);

  const refresh = async (n, d) => {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    setError(null);
    try {
      const result = await fetchTopPerformers({ universe, topN: n, days: d });
      setData(result);
    } catch (e) {
      setError(e.message);
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

  // Reset state when the parent universe changes; reload right away if the
  // price cache is warm (a cold cache would mean a surprise 30–60 s fetch).
  const mounted = useRef(false);
  useEffect(() => {
    const n = universeDefaultTopN[universe] || 10;
    setTopN(n);
    setData(null);
    setError(null);
    setSort(null);
    if (mounted.current && cacheInfo) refresh(n, days);
    mounted.current = true;
  }, [universe]);

  // Auto-load once on mount, as soon as we know the cache is warm.
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (cacheInfo && !autoLoaded.current) {
      autoLoaded.current = true;
      if (!data && !runningRef.current) refresh(topN, days);
    }
  }, [cacheInfo]);

  const onRefresh = () => refresh(topN, days);

  const labelStyle = {
    fontSize: 10, fontWeight: 600, color: theme.muted,
    letterSpacing: '0.06em', textTransform: 'uppercase',
    marginBottom: 4,
  };
  const inputStyle = {
    width: 70, padding: '6px 8px', fontSize: 12,
    border: `1px solid ${theme.border}`, borderRadius: 4,
    background: '#fff', fontFamily: 'JetBrains Mono, monospace',
    color: theme.text, outline: 'none',
  };

  return (
    <div style={{
      background: theme.card, border: `1px solid ${theme.border}`,
      borderRadius: 6, padding: '14px 16px',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'flex-end', marginBottom: 12, gap: 12, flexWrap: 'wrap',
      }}>
        <div>
          <div style={{
            fontSize: 13, fontWeight: 700,
            fontFamily: headingFont || 'inherit',
          }}>
            Top performers
          </div>
          <div style={{ fontSize: 11, color: theme.muted, marginTop: 2 }}>
            {(data?.universe_label) || ({
              sp500: 'S&P 500',
              global_etfs: 'Global equity ETFs',
              us_sector_etfs: 'US sector ETFs',
            }[universe])} · last {days} calendar days
            {data?.window && <> · {data.window.start_date} → {data.window.end_date}</>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
          <NumField id={`tp-topn-${universe}`} label="Top N" value={topN}
                    min={1} max={maxTopNFor(universes, universe)}
                    help={PARAM_HELP.topPerfN}
                    onChange={setTopN}
                    theme={theme} labelStyle={labelStyle} inputStyle={inputStyle} />
          <NumField id={`tp-days-${universe}`} label="Days" value={days}
                    min={1} max={365}
                    help={PARAM_HELP.topPerfDays}
                    onChange={setDays}
                    theme={theme} labelStyle={labelStyle} inputStyle={inputStyle} />
          <button onClick={onRefresh}
                  disabled={running || !Number.isFinite(topN) || !Number.isFinite(days)}
                  style={{
                    padding: '7px 14px', fontSize: 12, fontWeight: 600,
                    background: running ? theme.muted : accent.dark, color: '#fff',
                    border: 'none', borderRadius: 4,
                    cursor: running ? 'wait' : 'pointer',
                    letterSpacing: '0.02em',
                  }}>
            {running ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          padding: '10px 12px', fontSize: 12, color: '#b91c1c',
          background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 4,
        }}>
          {error}
        </div>
      )}

      {!data && !error && !running && (
        <div style={{ fontSize: 12, color: theme.muted, fontStyle: 'italic' }}>
          Click Refresh to compute the leaderboard. First run pulls fresh prices (~30–60 s);
          subsequent runs hit the cache.
        </div>
      )}

      {running && !data && (
        <div style={{ fontSize: 12, color: theme.muted, fontStyle: 'italic' }}>
          Pulling prices…
        </div>
      )}

      {data && data.results && (() => {
        const rows = sort
          ? [...data.results].sort((a, b) => {
              const an = a[sort.key] == null ? -Infinity : a[sort.key];
              const bn = b[sort.key] == null ? -Infinity : b[sort.key];
              return sort.dir === 'desc' ? bn - an : an - bn;
            })
          : data.results;
        const clickSort = (key) => setSort(s =>
          (s && s.key === key)
            ? (s.dir === 'desc' ? { key, dir: 'asc' } : null)  // desc → asc → rank order
            : { key, dir: 'desc' });
        const thBase = { padding: '6px 4px', fontWeight: 500,
                         borderBottom: `1px solid ${theme.border}`, whiteSpace: 'nowrap' };
        const SortTh = ({ label, k, title }) => (
          <th title={title || 'Click to sort'} onClick={() => clickSort(k)}
              aria-sort={sort?.key === k ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
              style={{ ...thBase, textAlign: 'right', cursor: 'pointer',
                       color: sort?.key === k ? theme.text : undefined,
                       userSelect: 'none' }}>
            {label}{sort?.key === k ? (sort.dir === 'desc' ? ' ▾' : ' ▴') : ''}
          </th>
        );
        const num = (v, digits = 1, signed = true) => (
          <td style={{ padding: '7px 4px', textAlign: 'right', whiteSpace: 'nowrap',
                        color: v == null ? theme.muted
                          : (signed ? (v >= 0 ? '#047857' : '#b91c1c') : theme.text) }}>
            {v == null ? '—' : fmtPct(v, digits, signed)}
          </td>
        );
        return (
          <div style={{ overflowX: 'auto' }}>
            <table style={{
              width: '100%', fontSize: 12,
              fontFamily: 'JetBrains Mono, monospace',
              borderCollapse: 'collapse',
            }}>
              <thead>
                <tr style={{
                  color: theme.muted, fontSize: 10,
                  textTransform: 'uppercase', letterSpacing: '0.05em',
                }}>
                  <th style={{ ...thBase, textAlign: 'right', width: 32 }}>#</th>
                  <th style={{ ...thBase, textAlign: 'left' }}>Ticker</th>
                  <th style={{ ...thBase, textAlign: 'left', fontFamily: 'inherit' }}>Company</th>
                  <th style={{ ...thBase, textAlign: 'left', fontFamily: 'inherit' }}>
                    {data?.meta_label || 'Sector'}
                  </th>
                  <th style={{ ...thBase, textAlign: 'left' }}
                      title="Trailing year, weekly closes">1y</th>
                  <SortTh label={`${days}d return`} k="return_pct" />
                  <SortTh label="Δ 7d" k="rank_delta"
                          title="Rank change vs the same window a week earlier (▲ = moved up)" />
                  <SortTh label="3m" k="ret_3m" title="Trailing 3-month return" />
                  <SortTh label="12m" k="ret_12m" title="Trailing 12-month return" />
                  <SortTh label="Vol" k="vol_ann"
                          title="Annualized volatility of daily returns over the ranking window" />
                  <SortTh label="vs 52w hi" k="below_52w_high"
                          title="End price vs trailing 52-week high (0% = at the high)" />
                  <SortTh label="Max 1d" k="max_day_move"
                          title="Largest single-day move inside the ranking window" />
                  <SortTh label="Streak" k="streak"
                          title="Consecutive prior windows already ranked in the top N" />
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const badge = leaderboardBadge(r);
                  return (
                    <tr key={r.ticker} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: '7px 4px', textAlign: 'right',
                                    color: theme.muted }}>{r.rank}</td>
                      <td style={{ padding: '7px 4px', fontWeight: 600, whiteSpace: 'nowrap',
                                    color: theme.text }}>
                        {r.ticker}
                        {badge && (
                          <span title={badge.title} style={{
                            marginLeft: 6, padding: '1px 5px', fontSize: 9,
                            fontFamily: 'Inter, sans-serif', fontWeight: 600,
                            textTransform: 'uppercase', letterSpacing: '0.04em',
                            color: badge.color, background: badge.bg,
                            borderRadius: 3, verticalAlign: 'middle',
                          }}>{badge.label}</span>
                        )}
                      </td>
                      <td style={{ padding: '7px 8px', fontFamily: 'inherit',
                                    color: theme.text }}>{r.name || '—'}</td>
                      <td style={{ padding: '7px 8px', fontFamily: 'inherit',
                                    color: theme.muted }}>{r.sector || '—'}</td>
                      <td style={{ padding: '4px 4px' }}>
                        {r.spark && r.spark.length > 1
                          ? <Sparkline values={r.spark} color={accent.color} width={70} height={20} />
                          : <span style={{ color: theme.muted }}>—</span>}
                      </td>
                      <td style={{ padding: '7px 4px', textAlign: 'right', fontWeight: 600,
                                    whiteSpace: 'nowrap',
                                    color: r.return_pct >= 0 ? '#047857' : '#b91c1c' }}>
                        {fmtPct(r.return_pct, 2)}
                      </td>
                      <td style={{ padding: '7px 4px', textAlign: 'right', whiteSpace: 'nowrap',
                                    fontSize: 11,
                                    color: r.rank_delta == null ? theme.muted
                                      : (r.rank_delta > 0 ? '#047857'
                                        : (r.rank_delta < 0 ? '#b91c1c' : theme.muted)) }}>
                        {r.rank_delta == null ? '—'
                          : (r.rank_delta > 0 ? `▲${r.rank_delta}`
                            : (r.rank_delta < 0 ? `▼${-r.rank_delta}` : '·'))}
                      </td>
                      {num(r.ret_3m)}
                      {num(r.ret_12m)}
                      {num(r.vol_ann, 0, false)}
                      {num(r.below_52w_high)}
                      {num(r.max_day_move)}
                      <td style={{ padding: '7px 4px', textAlign: 'right',
                                    color: r.streak > 0 ? theme.text : theme.muted }}>
                        {r.streak > 0 ? `×${r.streak}` : 'new'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })()}

      {data && data.watchlist && data.watchlist.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                         textTransform: 'uppercase', color: theme.muted, marginBottom: 6 }}>
            Bubbling under · ranks {data.results.length + 1}–{data.results.length + data.watchlist.length} by {days}d return
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {data.watchlist.map(r => (
              <span key={r.ticker}
                    title={`${r.name || r.ticker}${r.sector ? ` · ${r.sector}` : ''}${r.prior_rank != null ? ` · was #${r.prior_rank} a week ago` : ''}`}
                    style={{
                      padding: '3px 8px', fontSize: 11,
                      fontFamily: 'JetBrains Mono, monospace',
                      background: theme.bg, border: `1px solid ${theme.border}`,
                      borderRadius: 4, display: 'inline-flex', gap: 6,
                      alignItems: 'baseline',
                    }}>
                <span style={{ color: theme.muted }}>#{r.rank}</span>
                <span style={{ fontWeight: 600, color: theme.text }}>{r.ticker}</span>
                <span style={{ fontWeight: 600,
                                color: r.return_pct >= 0 ? '#047857' : '#b91c1c' }}>
                  {fmtPct(r.return_pct, 1)}
                </span>
                {r.rank_delta != null && r.rank_delta !== 0 && (
                  <span style={{ fontSize: 10,
                                  color: r.rank_delta > 0 ? '#047857' : '#b91c1c' }}>
                    {r.rank_delta > 0 ? `▲${r.rank_delta}` : `▼${-r.rank_delta}`}
                  </span>
                )}
                {r.new_entrant && (
                  <span title="Wasn't anywhere in this list a full window ago" style={{
                    fontSize: 9, fontWeight: 600, fontFamily: 'Inter, sans-serif',
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                    color: '#4338ca', background: '#eef2ff',
                    padding: '1px 5px', borderRadius: 3,
                  }}>new</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {data && data.elapsed_s != null && (
        <div style={{ marginTop: 8, fontSize: 10, color: theme.muted,
                       fontFamily: 'JetBrains Mono, monospace' }}>
          {data.universe_size} tickers · {data.elapsed_s}s
        </div>
      )}
    </div>
  );
}

// ============================================================
// LedgerPanel — realized out-of-sample record of the targets the tool recorded.
// Self-contained like TopPerformersPanel: owns its fetch, loads on mount and
// whenever `reloadKey` changes. The only panel in the tool whose numbers were
// not chosen with hindsight.
// ============================================================

// 'YYYY-MM-DD' -> a local Date. new Date(iso) reads the string as UTC midnight,
// which lands on the previous day anywhere west of GMT (and can therefore print
// the wrong year on the chart's January tick).
function isoToLocalDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso);
}

// Per-row warning chips. `basis` deliberately reads "verify", not "error": the
// return is right (both endpoints sit on the current cached basis), it is the
// price *basis* that moved, and only by a split-shaped amount — dividend
// re-adjustment alone never reaches the threshold.
function ledgerChips(r) {
  const out = [];
  if (r.n_frozen > 0) {
    const at = Object.entries(r.frozen_at || {})
      .map(([t, d]) => `${t} to ${d}`).join(', ');
    out.push({
      label: `${r.n_frozen} frozen`, color: '#b45309', bg: '#fef3c7',
      title: `No bars after the dates shown (delisting, acquisition, symbol change), `
           + `so these legs are marked at their last close and kept in the `
           + `equal-weight denominator: ${at}`,
    });
  }
  if (r.unpriced_tickers && r.unpriced_tickers.length) {
    out.push({
      label: `${r.unpriced_tickers.length} unmarked`, color: '#4338ca', bg: '#eef2ff',
      title: `No price at all for ${r.unpriced_tickers.join(', ')}, so they are left `
           + `out of this row's mean rather than counted as 0%.`,
    });
  }
  if (r.basis_shifted) {
    const at = Object.entries(r.basis_ratio || {})
      .map(([t, v]) => `${t} ×${Number(v).toFixed(3)}`).join(', ');
    out.push({
      label: 'verify basis', color: '#7c2d12', bg: '#fff7ed',
      title: `Prices were re-adjusted by a split-shaped amount since recording `
           + `(${at}). The return is recomputed on the current basis, so it is `
           + `correct; the chip is a prompt to eyeball the name.`,
    });
  }
  if (r.basis_unverified) {
    out.push({
      label: 'stored basis', color: '#b91c1c', bg: '#fef2f2',
      title: `The cache no longer reaches this segment's start, so the start price `
           + `comes from the recording and the end price from the current cache `
           + `(${r.basis_unverified_tickers.join(', ')}). Any split or dividend `
           + `re-adjustment since then is baked into this return.`,
    });
  }
  return out;
}

// One collapsed segment row; expands to the per-pick realized returns.
function LedgerRow({ r, theme, isNewest }) {
  const [open, setOpen] = useState(false);
  const picks = (r.tickers || [])
    .map(t => ({ ticker: t, ret: r.pick_returns ? r.pick_returns[t] : null }))
    .sort((a, b) => ((b.ret == null ? -Infinity : b.ret) - (a.ret == null ? -Infinity : a.ret)));
  const worst = picks.reduce(
    (w, e) => (e.ret != null && (w == null || e.ret < w.ret) ? e : w), null);
  const chips = ledgerChips(r);

  return (
    <div style={{ borderTop: `1px solid ${theme.border}` }}>
      <div role="button" tabIndex={0} aria-expanded={open}
           onClick={() => setOpen(o => !o)}
           onKeyDown={(e) => {
             if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(o => !o); }
           }}
           style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap',
                     padding: '8px 0', cursor: 'pointer', outline: 'none' }}>
        <span style={{ fontSize: 11, fontWeight: 600, width: 96, flexShrink: 0,
                        fontFamily: 'JetBrains Mono, monospace' }}>
          {r.as_of}
        </span>
        <span style={{ fontSize: 10.5, color: theme.muted, width: 132, flexShrink: 0,
                        fontFamily: 'JetBrains Mono, monospace' }}>
          → {r.end_date || 'unmarked'}
          {r.days_held != null ? ` · ${r.days_held}d` : ''}
        </span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, flex: 1, minWidth: 180 }}>
          {(r.tickers || []).slice(0, 12).map(t => (
            <span key={t} style={{
              padding: '1px 6px', fontSize: 10.5, fontWeight: 600,
              fontFamily: 'JetBrains Mono, monospace',
              background: theme.bg, border: `1px solid ${theme.border}`,
              borderRadius: 4, color: theme.text,
            }}>{t}</span>
          ))}
        </div>
        {isNewest && r.is_open && (
          <span title="Still held: this segment is marked to the latest cached bar"
                style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase',
                          letterSpacing: '0.04em', color: '#047857',
                          background: '#ecfdf5', padding: '1px 5px', borderRadius: 3 }}>
            open
          </span>
        )}
        {chips.map(c => (
          <span key={c.label} title={c.title} style={{
            fontSize: 9, fontWeight: 600, textTransform: 'uppercase',
            letterSpacing: '0.04em', color: c.color, background: c.bg,
            padding: '1px 5px', borderRadius: 3, flexShrink: 0, cursor: 'help',
          }}>{c.label}</span>
        ))}
        <span style={{ fontSize: 11, fontWeight: 600, width: 66, textAlign: 'right',
                        flexShrink: 0, fontFamily: 'JetBrains Mono, monospace',
                        color: r.return_pct == null ? theme.muted
                          : (r.return_pct >= 0 ? '#047857' : '#b91c1c') }}>
          {r.return_pct == null ? '—' : fmtPct(r.return_pct, 1)}
        </span>
        {(() => {
          // The benchmark column can lag the constituents, so say which bars the
          // comparison actually used rather than implying it lined up.
          const lagged = r.bench_to_date && r.bench_to_date !== r.end_date;
          return (
            <span title={lagged
                    ? `${r.benchmark || 'benchmark'} ${r.bench_from_date} to ${r.bench_to_date}: its last bar predates this row's ${r.end_date}`
                    : `${r.benchmark || 'benchmark'} over the same dates`}
                  style={{ fontSize: 10.5, width: 62, textAlign: 'right', flexShrink: 0,
                            fontFamily: 'JetBrains Mono, monospace', color: theme.muted }}>
              {r.bench_return_pct == null ? '—' : fmtPct(r.bench_return_pct, 1)}
              {lagged && <span style={{ color: '#b45309' }} aria-label="benchmark bars lag">*</span>}
            </span>
          );
        })()}
        <span title="Excess over the benchmark, in percentage points"
              style={{ fontSize: 10.5, fontWeight: 600, width: 62, textAlign: 'right',
                        flexShrink: 0, fontFamily: 'JetBrains Mono, monospace',
                        color: r.excess_pp == null ? theme.muted
                          : (r.excess_pp >= 0 ? '#047857' : '#b91c1c') }}>
          {r.excess_pp == null ? '—' : fmtPct(r.excess_pp, 1)}
        </span>
        <span aria-hidden="true" style={{ fontSize: 10, color: theme.muted, flexShrink: 0 }}>
          {open ? '▾' : '▸'}
        </span>
      </div>
      {open && (
        <div style={{ padding: '0 0 10px 96px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {picks.map(e => {
              const isWorst = worst && e.ticker === worst.ticker && e.ret != null && e.ret < 0;
              const frozen = (r.frozen_tickers || []).includes(e.ticker);
              return (
                <span key={e.ticker}
                      title={frozen ? `Frozen at its last close, ${r.frozen_at[e.ticker]}`
                                    : undefined}
                      style={{
                        padding: '2px 7px', fontSize: 10.5,
                        fontFamily: 'JetBrains Mono, monospace',
                        background: isWorst ? '#fef2f2' : theme.bg,
                        border: `1px solid ${isWorst ? '#fecaca' : theme.border}`,
                        borderRadius: 4, display: 'inline-flex', gap: 5, alignItems: 'baseline',
                      }}>
                  <span style={{ fontWeight: 600, color: theme.text }}>
                    {e.ticker}{frozen ? '*' : ''}
                  </span>
                  <span style={{ fontWeight: 600,
                                  color: e.ret == null ? theme.muted
                                    : (e.ret >= 0 ? '#047857' : '#b91c1c') }}>
                    {e.ret == null ? 'n/a' : fmtPct(e.ret, 1)}
                  </span>
                </span>
              );
            })}
          </div>
          <div style={{ fontSize: 10, color: theme.muted, marginTop: 6, lineHeight: 1.5 }}>
            {r.n_priced} of {(r.tickers || []).length} legs marked
            {r.n_frozen > 0 ? `, ${r.n_frozen} frozen at a last close (still in the denominator)` : ''}
            {r.start_date ? ` · from the ${r.start_date} close` : ''}
            {r.note ? ` · ${r.note}` : ''}
          </div>
        </div>
      )}
    </div>
  );
}

// Shared Record wiring for both dashboards: owns the request state and the
// version counter LedgerPanel reloads on, so V1 and V2 stay 2 lines each and
// cannot drift apart. `ranParams` must be the params of the last *executed* run,
// not the live sidebar values, or Record would file a cohort under settings the
// user only typed.

// The settings that define a ledger group. Must stay in step with the fields
// server.py hashes into params_hash, or the recorded hint would follow a group
// it does not belong to.
function recordKey(p) {
  if (!p) return null;
  return [p.universe || 'sp500', p.topN, p.rankLookback,
          p.skipMonths || 0, p.benchmark || 'SPY'].join('|');
}

function useLedgerRecorder(ranParams, rebalance) {
  const [recordState, setRecordState] = useState('idle');
  const [recordMessage, setRecordMessage] = useState(null);
  const [ledgerVersion, setLedgerVersion] = useState(0);
  // Tagged with the settings it was recorded under, not just the date: the
  // "ledger holds X" hint must not survive a re-run under a different universe
  // or topN, where it would claim a cohort that group never recorded.
  const [recorded, setRecorded] = useState(null);
  const setRecordedAsOf = (asOf) => setRecorded({ asOf, key: recordKey(ranParams) });

  const latest = rebalance && rebalance.length ? rebalance[0] : null;
  const recordedAsOf = (recorded && recorded.key === recordKey(ranParams))
    ? recorded.asOf : null;

  // A new run invalidates the previous outcome message.
  useEffect(() => { setRecordState('idle'); setRecordMessage(null); }, [rebalance]);

  const onRecord = async () => {
    if (!ranParams || !latest || !latest.is_live || recordState === 'saving') return;
    setRecordState('saving');
    setRecordMessage(null);
    try {
      const res = await recordLedgerTarget({
        universe: ranParams.universe || 'sp500',
        topN: ranParams.topN,
        rankLookback: ranParams.rankLookback,
        skipMonths: ranParams.skipMonths || 0,
        benchmark: ranParams.benchmark || 'SPY',
        displayedAsOf: latest.as_of || null,
      });
      setRecordedAsOf(res.as_of);
      setLedgerVersion(v => v + 1);
      const notes = [];
      if (!res.target_matches_stored) {
        notes.push('the stored cohort differs from the list on screen (prices were '
                 + 're-adjusted since it was recorded, and the record is never rewritten)');
      }
      if (!res.displayed_as_of_matches) {
        notes.push(`the screen shows ${latest.as_of} but the latest bar is ${res.as_of}`);
      }
      (res.warnings || []).forEach(w => notes.push(w));
      const tail = notes.length ? ` — ${notes.join('; ')}` : '';
      if (res.created) {
        setRecordState('saved');
        setRecordMessage(`Recorded for ${res.as_of}${tail}`);
      } else {
        setRecordState('exists');
        setRecordMessage(res.reason === 'already_recorded_this_month'
          ? `Already recorded this month (${res.entry.as_of}); nothing changed${tail}`
          : `Already recorded for ${res.as_of}${tail}`);
      }
    } catch (e) {
      setRecordState('error');
      setRecordMessage(e.message || 'Record failed');
    }
  };

  return { onRecord, recordState, recordMessage, recordedAsOf, ledgerVersion };
}

function LedgerPanel({ theme, accent, headingFont, benchmark, reloadKey }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selHash, setSelHash] = useState(null);
  const [hoverIdx, setHoverIdx] = useState(null);
  const runningRef = useRef(false);

  const load = async (refresh) => {
    if (runningRef.current) return;
    runningRef.current = true;
    setLoading(true);
    setError(null);
    try {
      setData(await fetchLedger({ refresh: refresh ? 1 : 0 }));
    } catch (e) {
      setError(e.message);
    } finally {
      runningRef.current = false;
      setLoading(false);
    }
  };

  useEffect(() => { load(false); }, [reloadKey]);

  const card = {
    background: theme.card, border: `1px solid ${theme.border}`,
    borderRadius: 6, padding: '14px 16px',
  };
  const groups = (data && data.groups) || [];
  const group = groups.find(g => g.params_hash === selHash) || groups[0] || null;
  const s = group ? group.summary : null;
  const dates = group ? (group.equity.dates || []) : [];
  const months = useMemo(() => dates.map(isoToLocalDate), [group, dates.length]);

  const series = [];
  if (group && dates.length > 1) {
    series.push({ name: 'Recorded targets', values: group.equity.strategy,
                  color: accent.dark });
    if ((group.equity.benchmark || []).length === dates.length) {
      series.push({ name: group.benchmark || benchmark || 'benchmark',
                    values: group.equity.benchmark,
                    color: theme.muted, isBenchmark: true });
    }
  }

  const tile = (label, value, sub, color) => (
    <div style={{ minWidth: 128 }}>
      <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.06em',
                     textTransform: 'uppercase', color: theme.muted }}>
        {label}
      </div>
      <div style={{ fontSize: 17, fontWeight: 700, marginTop: 2,
                     fontFamily: 'JetBrains Mono, monospace',
                     color: color || theme.text }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: theme.muted, marginTop: 1 }}>{sub}</div>
      )}
    </div>
  );

  return (
    <div style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                     alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700,
                         fontFamily: headingFont || 'inherit' }}>
            Out-of-sample ledger
          </div>
          <div style={{ fontSize: 11, color: theme.muted, marginTop: 2,
                         lineHeight: 1.55, maxWidth: 720 }}>
            Everything above is fitted: the universe is today's index membership and
            the parameters were chosen by looking at the same window they are scored
            on. This table is not. It holds what the tool recommended on the day it
            was recorded, marked to market since, equal-weight and unit-equity.
            Entries recorded under different settings are separate records and are
            never blended. Selection is still made from today's membership list, so
            the returns escape survivorship bias but the selection universe does not.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          <button onClick={() => load(false)} disabled={loading}
                  title="Re-read the ledger and re-mark it from the local price cache"
                  style={{
                    padding: '6px 11px', fontSize: 11, fontWeight: 600,
                    background: loading ? theme.muted : accent.dark, color: '#fff',
                    border: 'none', borderRadius: 4,
                    cursor: loading ? 'wait' : 'pointer',
                  }}>
            {loading ? 'Loading…' : 'Reload'}
          </button>
          <button onClick={() => load(true)} disabled={loading}
                  title="Pull fresh prices for the recorded tickers only (needs network)"
                  style={{
                    padding: '6px 11px', fontSize: 11, fontWeight: 600,
                    background: 'transparent', color: accent.dark,
                    border: `1px solid ${theme.border}`, borderRadius: 4,
                    cursor: loading ? 'wait' : 'pointer',
                  }}>
            Refresh prices
          </button>
        </div>
      </div>

      {error && (
        <div style={{ marginTop: 10, padding: '10px 12px', fontSize: 12, color: '#b91c1c',
                       background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 4 }}>
          {error}
        </div>
      )}

      {!error && data && data.entries_total === 0 && (
        <div style={{ marginTop: 10, fontSize: 12, color: theme.muted, lineHeight: 1.6 }}>
          Nothing recorded yet. Use <strong>Record this target</strong> on the monthly
          rebalance panel to append the current live target; from the next month on,
          this table marks it to market from the local price cache. One entry per
          calendar month per settings group.
        </div>
      )}

      {!error && group && (
        <>
          {groups.length > 1 && (
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 12 }}>
              {groups.map(g => {
                const on = g.params_hash === group.params_hash;
                return (
                  <button key={g.params_hash} onClick={() => setSelHash(g.params_hash)}
                          title={`${g.rows.length} segment(s) · hash ${g.params_hash}`}
                          style={{
                            padding: '4px 9px', fontSize: 10.5, fontWeight: 600,
                            border: `1px solid ${on ? accent.color : theme.border}`,
                            background: on ? accent.light : '#fff',
                            color: on ? accent.dark : theme.muted,
                            borderRadius: 5, cursor: 'pointer',
                          }}>
                    {g.label}
                  </button>
                );
              })}
            </div>
          )}

          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', marginTop: 12,
                         paddingBottom: 12, borderBottom: `1px solid ${theme.border}` }}>
            {tile('Recorded targets',
                  s.total_return == null ? '—' : fmtPct(s.total_return, 1),
                  `${s.n_segments} segment${s.n_segments === 1 ? '' : 's'} · ${s.n_completed} completed`,
                  s.total_return == null ? theme.muted
                    : (s.total_return >= 0 ? '#047857' : '#b91c1c'))}
            {tile(group.benchmark || 'Benchmark',
                  s.benchmark_total_return == null ? '—' : fmtPct(s.benchmark_total_return, 1),
                  s.comparison_covers_all_segments === false
                    ? `${s.n_comparable_segments} of ${s.n_priced_segments} segments comparable`
                    : 'same dates, bar for bar')}
            {tile('Excess',
                  s.excess == null ? '—' : `${(s.excess * 100).toFixed(1)}pp`,
                  s.comparison_covers_all_segments === false
                    ? 'over comparable segments only'
                    : 'difference of totals',
                  s.excess == null ? theme.muted
                    : (s.excess >= 0 ? '#047857' : '#b91c1c'))}
            {tile('Live since', s.first_as_of || '—',
                  `through ${s.priced_through || '—'} · ${s.days_live}d`)}
            {s.annualized
              ? tile('Sharpe (ann.)', fmtNum(s.annualized.Sharpe),
                     `CAGR ${fmtPct(s.annualized.CAGR, 1, false)} · vol ${fmtPct(s.annualized.vol, 0, false)}`)
              : tile('Annualized', 'held back', s.annualized_blocked_reason)}
          </div>

          {dates.length > 1 ? (
            <div style={{ marginTop: 10 }}>
              <div style={{ height: 240 }}>
                <EquityCurveChart
                  series={series} months={months} height={240}
                  logScale={false} filled={false}
                  theme={theme} accent={accent}
                  hoveredIdx={hoverIdx} onHover={setHoverIdx}
                  ariaLabel={`Realized out-of-sample equity of recorded targets vs `
                            + `${group.benchmark || 'benchmark'}, `
                            + `${s.equity_starts_at} to ${s.priced_through}`}
                />
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 4, paddingLeft: 56,
                             fontSize: 10.5, fontFamily: 'JetBrains Mono, monospace',
                             color: theme.muted, flexWrap: 'wrap' }}>
                {series.map(x => (
                  <span key={x.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 10, height: 2, background: x.color }} />
                    <span style={{ color: theme.text }}>{x.name}</span>
                  </span>
                ))}
                <span>{s.equity_starts_at} → {s.priced_through}</span>
                {!s.equity_covers_all_segments && (
                  <span style={{ color: '#b45309' }}>
                    chart covers {s.equity_segments} of {s.n_segments} segments; the
                    table below is the full chain
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 10, fontSize: 11, color: theme.muted,
                           fontStyle: 'italic' }}>
              {dates.length === 1
                ? `One bar so far (${dates[0]}). The curve starts drawing once a second `
                  + `price bar exists — the totals above are already live.`
                : 'No cached price bars cover these entries yet, so there is nothing to plot.'}
            </div>
          )}

          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'baseline',
                           fontSize: 9.5, fontWeight: 700, letterSpacing: '0.06em',
                           textTransform: 'uppercase', color: theme.muted,
                           paddingBottom: 4 }}>
              <span style={{ width: 96, flexShrink: 0 }}>Recorded</span>
              <span style={{ width: 132, flexShrink: 0 }}>Marked to</span>
              <span style={{ flex: 1, minWidth: 180 }}>Picks</span>
              <span style={{ width: 66, textAlign: 'right', flexShrink: 0 }}>Return</span>
              <span style={{ width: 62, textAlign: 'right', flexShrink: 0 }}>
                {group.benchmark || 'Bench'}
              </span>
              <span style={{ width: 62, textAlign: 'right', flexShrink: 0 }}>+/−</span>
              <span style={{ width: 10, flexShrink: 0 }} />
            </div>
            {group.rows.map((r, i) => (
              <LedgerRow key={`${r.as_of}-${i}`} r={r} theme={theme} isNewest={i === 0} />
            ))}
          </div>

          <div style={{ marginTop: 8, fontSize: 10, color: theme.muted,
                         fontFamily: 'JetBrains Mono, monospace' }}>
            {data.entries_total} entr{data.entries_total === 1 ? 'y' : 'ies'} on file ·
            prices from {data.price_source}
            {(group.universe_sources || []).some(x => x !== 'wikipedia' && x !== 'hardcoded')
              ? ` · universe source ${group.universe_sources.join(', ')}` : ''}
          </div>
        </>
      )}

      {!error && data && (data.warnings || []).length > 0 && (
        <div style={{ marginTop: 10, padding: '9px 11px', fontSize: 11,
                       color: '#78350f', background: '#fef3c7',
                       border: '1px solid #fcd34d', borderRadius: 4, lineHeight: 1.6 }}>
          {data.warnings.map((w, i) => <div key={i}>{w}</div>)}
        </div>
      )}
    </div>
  );
}

// ============================================================
// EarningsBreakdown — year-over-year (%) table + month-by-month earnings
// table, with a sleeve selector. Shared by V1/V2. Percentages throughout
// (no ×-multiples). Reads YEARLY_RETURNS + MONTHLY_RETURNS from the API.
// ============================================================
function EarningsBreakdown({ data, params, bestKey, theme, accent, headingFont }) {
  const { isNarrow } = useViewport();
  const holdKeys = (params.holds || [])
    .map(h => `h${h}m`)
    .filter(k => data.MONTHLY_RETURNS && data.MONTHLY_RETURNS[k]);
  const [selKey, setSelKey] = useState(null);
  if (!holdKeys.length || !data.MONTHLY_RETURNS) return null;
  const effKey = (selKey && holdKeys.includes(selKey)) ? selKey
               : (holdKeys.includes(bestKey) ? bestKey : holdKeys[0]);

  const green = '#047857', red = '#b91c1c';
  const colorOf = (v) => (v == null || isNaN(v) ? theme.muted : (v >= 0 ? green : red));
  const benchLabel = params.benchmark || 'SPY';
  const stratLabel = `top${params.topN}_${effKey}`;

  const months = (data.EQUITY.months || []).map(s => new Date(s));
  const stratRet = data.MONTHLY_RETURNS[effKey] || [];
  const benchRet = data.MONTHLY_RETURNS.spy || [];
  const yearly = data.YEARLY_RETURNS || {};
  const stratYears = yearly[effKey] || [];
  const benchByYear = Object.fromEntries((yearly.spy || []).map(y => [y.year, y]));

  // Monthly rows oldest→newest with running cumulative; shown newest-first.
  const rows = [];
  let cum = 1;
  for (let i = 0; i < stratRet.length; i++) {
    cum *= (1 + (stratRet[i] || 0));
    rows.push({ date: months[i], r: stratRet[i], cum: cum - 1, b: benchRet[i] });
  }
  const rowsNewestFirst = rows.slice().reverse();
  const yearMaxAbs = stratYears.reduce((m, y) => Math.max(m, Math.abs(y.return_pct)), 0.01);

  const card = { background: theme.card, border: `1px solid ${theme.border}`,
                 borderRadius: 6, padding: '14px 16px' };
  const th = { textAlign: 'right', padding: '6px 8px', fontWeight: 500,
               borderBottom: `1px solid ${theme.border}` };
  const td = { padding: '6px 8px', textAlign: 'right',
               fontFamily: 'JetBrains Mono, monospace' };
  const stickyTh = { ...th, position: 'sticky', top: 0, background: theme.card, zIndex: 1 };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                     alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 14, fontWeight: 700, fontFamily: headingFont || 'inherit' }}>
          Earnings breakdown
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 10, color: theme.muted, textTransform: 'uppercase',
                          letterSpacing: '0.05em' }}>Sleeve</span>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {holdKeys.map(k => (
              <button key={k} onClick={() => setSelKey(k)} style={{
                padding: '4px 9px', fontSize: 11, fontWeight: 600,
                fontFamily: 'JetBrains Mono, monospace',
                border: `1px solid ${k === effKey ? accent.color : theme.border}`,
                background: k === effKey ? accent.light : '#fff',
                color: k === effKey ? accent.dark : theme.muted,
                borderRadius: 5, cursor: 'pointer',
              }}>{k}</button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid',
                     gridTemplateColumns: isNarrow ? '1fr' : '1fr 1.25fr', gap: 12 }}>
        {/* Year-over-year */}
        <div style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                         alignItems: 'baseline', marginBottom: 6 }}>
            <div style={{ fontSize: 13, fontWeight: 700, fontFamily: headingFont || 'inherit' }}>
              Year-by-year return
            </div>
            <div style={{ fontSize: 11, color: theme.muted,
                           fontFamily: 'JetBrains Mono, monospace' }}>
              {stratLabel} vs {benchLabel}
            </div>
          </div>
          {stratYears.length ? (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse',
                            fontFamily: 'JetBrains Mono, monospace' }}>
              <thead>
                <tr style={{ color: theme.muted, fontSize: 10, textTransform: 'uppercase',
                              letterSpacing: '0.05em' }}>
                  <th style={{ ...th, textAlign: 'left' }}>Year</th>
                  <th style={th}>Strategy</th>
                  <th style={th}>{benchLabel}</th>
                  <th style={th}>+/−</th>
                </tr>
              </thead>
              <tbody>
                {stratYears.map(y => {
                  const b = benchByYear[y.year];
                  const diff = b ? y.return_pct - b.return_pct : null;
                  const barPct = Math.min(100, Math.abs(y.return_pct) / yearMaxAbs * 100);
                  return (
                    <tr key={y.year} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: '6px 8px', color: theme.text, fontWeight: 600 }}>
                        {y.year}
                        {y.partial && (
                          <span style={{ color: theme.muted, fontWeight: 400, fontSize: 10 }}>
                            {' '}· {y.months}mo
                          </span>
                        )}
                      </td>
                      <td style={{ ...td, position: 'relative', color: colorOf(y.return_pct),
                                    fontWeight: 600 }}>
                        <span style={{ position: 'absolute', right: 2, top: 3, bottom: 3,
                                        width: `${barPct}%`,
                                        background: y.return_pct >= 0 ? green : red,
                                        opacity: 0.12, borderRadius: 2 }} />
                        <span style={{ position: 'relative' }}>{fmtPct(y.return_pct, 1)}</span>
                      </td>
                      <td style={{ ...td, color: colorOf(b ? b.return_pct : null) }}>
                        {b ? fmtPct(b.return_pct, 1) : '—'}
                      </td>
                      <td style={{ ...td, color: colorOf(diff) }}>
                        {diff == null ? '—' : fmtPct(diff, 1)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div style={{ fontSize: 11, color: theme.muted, fontStyle: 'italic' }}>
              Not enough history for a full calendar year.
            </div>
          )}
        </div>

        {/* Month-by-month earnings */}
        <div style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                         alignItems: 'baseline', marginBottom: 6 }}>
            <div style={{ fontSize: 13, fontWeight: 700, fontFamily: headingFont || 'inherit' }}>
              Monthly earnings
            </div>
            <div style={{ fontSize: 11, color: theme.muted,
                           fontFamily: 'JetBrains Mono, monospace' }}>
              {rows.length} months · newest first
            </div>
          </div>
          <div style={{ maxHeight: 340, overflowY: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse',
                            fontFamily: 'JetBrains Mono, monospace' }}>
              <thead>
                <tr style={{ color: theme.muted, fontSize: 10, textTransform: 'uppercase',
                              letterSpacing: '0.05em' }}>
                  <th style={{ ...stickyTh, textAlign: 'left' }}>Month</th>
                  <th style={stickyTh}>Strategy</th>
                  <th style={stickyTh}>Cumulative</th>
                  <th style={stickyTh}>{benchLabel}</th>
                  <th style={stickyTh}>+/−</th>
                </tr>
              </thead>
              <tbody>
                {rowsNewestFirst.map((row, i) => {
                  const diff = (row.r != null && row.b != null) ? row.r - row.b : null;
                  return (
                    <tr key={i} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: '6px 8px', color: theme.text }}>
                        {row.date ? fmtMonthYear(row.date) : '—'}
                      </td>
                      <td style={{ ...td, color: colorOf(row.r), fontWeight: 600 }}>
                        {fmtPct(row.r, 2)}
                      </td>
                      <td style={{ ...td, color: colorOf(row.cum) }}>{fmtPct(row.cum, 1)}</td>
                      <td style={{ ...td, color: colorOf(row.b) }}>{fmtPct(row.b, 2)}</td>
                      <td style={{ ...td, color: colorOf(diff) }}>
                        {diff == null ? '—' : fmtPct(diff, 2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// BuildInfo — bottom-right chrome shared by V1 and V2: the deployed build
// stamp plus a "What's new" modal fed by git log. Anchored bottom-right so it
// never meets the top-right variation switcher; kept small and low-contrast
// with a translucent backdrop because <main> scrolls underneath it.
// ============================================================

// 'YYYY-MM-DD' -> 'Tue, Jul 28, 2026'. Parsed as local parts on purpose:
// new Date(iso) reads the string as UTC midnight and renders the previous
// day anywhere west of GMT.
function fmtIsoDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  if (!m) return iso || '—';
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
  });
}

// Entries arrive newest first; group consecutive same-date runs without resorting.
function groupByDate(entries) {
  const groups = [];
  (entries || []).forEach(e => {
    const last = groups[groups.length - 1];
    if (last && last.date === e.date) last.entries.push(e);
    else groups.push({ date: e.date, entries: [e] });
  });
  return groups;
}

const VERSION_SOURCE_NOTE = {
  git: 'Derived from the last commit date.',
  mtime: 'No git metadata here, so this is the newest source-file date, not a commit.',
  unknown: 'Neither git nor file dates were readable, so the build date is unknown.',
};

function WhatsNewModal({ feed, loading, error, onClose, closeRef, theme, accent, headingFont }) {
  const groups = groupByDate(feed && feed.entries);
  const unavailable = feed && feed.source === 'unavailable';

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
         style={{
           position: 'fixed', inset: 0, zIndex: 200,
           background: 'rgba(28,25,23,0.42)',
           display: 'flex', alignItems: 'center', justifyContent: 'center',
           padding: 20,
         }}>
      <div role="dialog" aria-modal="true" aria-labelledby="whats-new-title"
           style={{
             background: theme.card, color: theme.text,
             border: `1px solid ${theme.border}`, borderRadius: 10,
             boxShadow: '0 18px 50px rgba(0,0,0,0.22)',
             width: 'min(640px, 100%)', maxHeight: '82vh',
             display: 'flex', flexDirection: 'column',
           }}>
        <div style={{
          display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
          gap: 12, padding: '14px 18px', borderBottom: `1px solid ${theme.border}`,
        }}>
          <div>
            <h2 id="whats-new-title"
                style={{ margin: 0, fontSize: 15, fontWeight: 700,
                          fontFamily: headingFont || 'inherit', letterSpacing: '-0.01em' }}>
              What's new
            </h2>
            <div style={{ fontSize: 11, color: theme.muted, marginTop: 3,
                           fontFamily: 'JetBrains Mono, monospace' }}>
              {feed && feed.since ? `changes since ${feed.since}` : 'recent changes'}
              {groups.length ? ` · ${(feed.entries || []).length} commits` : ''}
            </div>
          </div>
          <button ref={closeRef} onClick={onClose} aria-label="Close what's new"
                  style={{
                    flexShrink: 0, padding: '4px 9px', fontSize: 11, fontWeight: 600,
                    color: theme.muted, background: 'transparent',
                    border: `1px solid ${theme.border}`, borderRadius: 5, cursor: 'pointer',
                  }}>
            Close
          </button>
        </div>

        <div style={{ overflowY: 'auto', padding: '14px 18px 18px' }}>
          {loading && (
            <div style={{ fontSize: 12, color: theme.muted }}>Loading changes…</div>
          )}
          {!loading && error && (
            <div style={{ fontSize: 12, color: '#b91c1c' }}>
              Could not load the change feed: {error}
            </div>
          )}
          {!loading && !error && unavailable && (
            <div style={{ fontSize: 12, color: theme.muted, lineHeight: 1.6 }}>
              Change history is unavailable in this copy: it has no git metadata
              (no repository or no git binary on the host). The version badge
              still identifies the build.
            </div>
          )}
          {!loading && !error && !unavailable && !groups.length && (
            <div style={{ fontSize: 12, color: theme.muted }}>
              No changes recorded since {feed && feed.since ? feed.since : 'the cutoff'}.
            </div>
          )}
          {groups.map(g => (
            <div key={g.date} style={{ marginBottom: 18 }}>
              <div style={{
                fontSize: 10, fontWeight: 700, color: accent.dark,
                letterSpacing: '0.1em', textTransform: 'uppercase',
                paddingBottom: 5, marginBottom: 10,
                borderBottom: `1px solid ${theme.border}`,
              }}>
                {fmtIsoDay(g.date)}
              </div>
              {g.entries.map(e => (
                <div key={e.hash} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                    <code style={{ fontSize: 10, color: theme.muted, flexShrink: 0,
                                    fontFamily: 'JetBrains Mono, monospace' }}>
                      {e.hash}
                    </code>
                    <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.45 }}>
                      {e.subject}
                    </div>
                  </div>
                  {e.body && (
                    <div style={{
                      marginTop: 5, marginLeft: 8, paddingLeft: 10,
                      borderLeft: `2px solid ${theme.border}`,
                      fontSize: 12, color: theme.muted, lineHeight: 1.6,
                      whiteSpace: 'pre-wrap',
                    }}>
                      {e.body}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BuildInfo({ theme, accent, headingFont }) {
  const [build, setBuild] = useState(null);
  const [open, setOpen] = useState(false);
  const [feed, setFeed] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const closeRef = useRef(null);

  useEffect(() => {
    let live = true;
    fetchVersion()
      .then(v => { if (live) setBuild(v); })
      .catch(() => { /* the badge is chrome: a failure just leaves it blank */ });
    return () => { live = false; };
  }, []);

  // Escape to dismiss, focus into the dialog, focus restored on unmount. No
  // body scroll lock: <main> is the scroller and body never scrolls, so there
  // is nothing to lock and nothing that can stay stuck.
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    const focusTimer = setTimeout(() => {
      if (closeRef.current) closeRef.current.focus();
    }, 0);
    // aria-modal claims the rest of the page is unreachable, so Tab has to
    // actually cycle inside the dialog. Without this, tabbing walks into the
    // sidebar controls behind the backdrop, which are visually obscured and
    // cannot be clicked. The focusable set is queried live rather than pinned to
    // closeRef so this stays correct if the dialog gains more controls.
    const onKeyDown = e => {
      if (e.key === 'Escape') { setOpen(false); return; }
      if (e.key !== 'Tab') return;
      const dialog = closeRef.current && closeRef.current.closest('[role="dialog"]');
      if (!dialog) return;
      const f = dialog.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      const inside = dialog.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      clearTimeout(focusTimer);
      document.removeEventListener('keydown', onKeyDown);
      if (previous && previous.focus) previous.focus();
    };
  }, [open]);

  // Fetched on first open, then kept: the feed is fixed at server import.
  const onOpen = () => {
    setOpen(true);
    if (feed || loading) return;
    setLoading(true);
    setError(null);
    fetchWhatsNew()
      .then(f => setFeed(f || { entries: [], since: null, source: 'unavailable' }))
      .catch(e => setError(e.message || 'request failed'))
      .finally(() => setLoading(false));
  };

  const source = (build && build.source) || 'unknown';
  const versionText = build ? build.version : '—';

  return (
    <>
      <div style={{
        position: 'fixed', bottom: 10, right: 12, zIndex: 45,
        display: 'flex', alignItems: 'center', gap: 7,
        padding: '3px 5px 3px 9px',
        background: 'rgba(255,255,255,0.82)',
        backdropFilter: 'blur(8px)',
        border: `1px solid ${theme.border}`,
        borderRadius: 7,
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
      }}>
        <span title={`Build ${versionText}. ${VERSION_SOURCE_NOTE[source] || ''}`}
              style={{ fontSize: 10, color: theme.muted, letterSpacing: '0.02em',
                        fontFamily: 'JetBrains Mono, monospace' }}>
          {versionText}
        </span>
        {/* Kept out of the version string so its format stays exactly 1.1.YYMMDDHH. */}
        {build && source !== 'git' && (
          <span style={{ fontSize: 9, color: theme.muted, opacity: 0.75 }}>
            {source === 'mtime' ? '(file date)' : '(no build date)'}
          </span>
        )}
        <button onClick={onOpen} aria-haspopup="dialog" aria-expanded={open}
                style={{
                  padding: '3px 8px', fontSize: 10, fontWeight: 600,
                  color: accent.dark, background: 'transparent',
                  border: `1px solid ${theme.border}`, borderRadius: 5,
                  cursor: 'pointer', letterSpacing: '0.02em',
                }}>
          What's new
        </button>
      </div>

      {open && (
        <WhatsNewModal feed={feed} loading={loading} error={error}
                       onClose={() => setOpen(false)} closeRef={closeRef}
                       theme={theme} accent={accent} headingFont={headingFont} />
      )}
    </>
  );
}

Object.assign(window, {
  EquityCurveChart, EventStudyChart, DrawdownChart, MonthlyHeatmap, Sparkline,
  TopPerformersPanel, RollingSharpeChart, UnderwaterChart, SectorBarChart,
  PARAM_HELP, HelpTip, NumField, paramIssues, maxTopNFor, useViewport,
  RebalancePanel, EarningsBreakdown, BuildInfo, WhatsNewModal, RegimeBanner,
  LedgerPanel, useLedgerRecorder,
});
