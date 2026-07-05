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
            {fmtEquityAxis(t)}
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
  check('lookback', 'Lookback', ranges.lookback.min, ranges.lookback.max);
  check('topN', 'Top N', ranges.topN.min, topNMax);
  check('rankLookback', 'Rank lookback', ranges.rankLookback.min, ranges.rankLookback.max);
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
const REBAL_COLORS = {
  buy:  { bg: '#ecfdf5', border: '#a7f3d0', text: '#047857' },
  sell: { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c' },
};

function RebalanceChip({ ticker, group, kind, theme, showGroup }) {
  const c = REBAL_COLORS[kind] || { bg: theme.bg, border: theme.border, text: theme.text };
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
    </span>
  );
}

function RebalancePanel({ rebalance, topN, rankLookback, universe = 'sp500',
                          theme, accent, headingFont }) {
  if (!rebalance || !rebalance.length) return null;
  const latest = rebalance[0];
  const history = rebalance.slice(1);
  const showGroup = universe !== 'sp500';

  const downloadCsv = () => {
    const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
    const rows = [['month', 'action', 'ticker', 'group']];
    rebalance.forEach(r => {
      r.buys.forEach(t => rows.push([r.month, 'BUY', t, r.meta?.[t] || '']));
      r.sells.forEach(t => rows.push([r.month, 'SELL', t, r.meta?.[t] || '']));
      r.holds.forEach(t => rows.push([r.month, 'KEEP', t, r.meta?.[t] || '']));
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
            Hold the top {topN} by trailing {rankLookback}m return; rebalance in full at each
            month-end close (the 1-month-hold sleeve). The top row is the live target as of
            the latest price — ranked over a trailing {rankLookback}-month window ending today,
            the same signal as Top performers. History rows below are the month-end sleeve.
          </div>
        </div>
        <button onClick={downloadCsv} style={{
          padding: '6px 12px', fontSize: 11, fontWeight: 600,
          background: accent.dark, color: '#fff', border: 'none',
          borderRadius: 4, cursor: 'pointer', flexShrink: 0,
        }}>
          Download CSV
        </button>
      </div>

      {/* Latest month — the actionable trades */}
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
        <div style={{ display: 'grid',
                       gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                       gap: 14 }}>
          <div>
            {colTitle('Buy', latest.buys.length, REBAL_COLORS.buy.text)}
            <div style={chipWrap}>
              {latest.buys.map(t => <RebalanceChip key={t} ticker={t} kind="buy"
                group={latest.meta?.[t]} theme={theme} showGroup={showGroup} />)}
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
                group={latest.meta?.[t]} theme={theme} showGroup={showGroup} />)}
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
            <div key={r.month} style={{
              display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap',
              padding: '7px 0', borderTop: `1px solid ${theme.border}`,
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
            </div>
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
function TopPerformersPanel({ theme, accent, headingFont, universe = 'sp500', cacheInfo, universes }) {
  const universeDefaultTopN = { sp500: 10, global_etfs: 5, us_sector_etfs: 3 };
  const [topN, setTopN] = useState(universeDefaultTopN[universe] || 10);
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
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

      {data && data.results && (
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
              <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                            borderBottom: `1px solid ${theme.border}`, width: 32 }}>#</th>
              <th style={{ textAlign: 'left', padding: '6px 4px', fontWeight: 500,
                            borderBottom: `1px solid ${theme.border}` }}>Ticker</th>
              <th style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 500,
                            borderBottom: `1px solid ${theme.border}`,
                            fontFamily: 'inherit' }}>Company</th>
              <th style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 500,
                            borderBottom: `1px solid ${theme.border}`,
                            fontFamily: 'inherit' }}>{data?.meta_label || 'Sector'}</th>
              <th style={{ textAlign: 'right', padding: '6px 4px', fontWeight: 500,
                            borderBottom: `1px solid ${theme.border}` }}>{days}d return</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map(r => (
              <tr key={r.ticker} style={{ borderBottom: `1px solid ${theme.border}` }}>
                <td style={{ padding: '7px 4px', textAlign: 'right',
                              color: theme.muted }}>{r.rank}</td>
                <td style={{ padding: '7px 4px', fontWeight: 600,
                              color: theme.text }}>{r.ticker}</td>
                <td style={{ padding: '7px 8px', fontFamily: 'inherit',
                              color: theme.text }}>{r.name || '—'}</td>
                <td style={{ padding: '7px 8px', fontFamily: 'inherit',
                              color: theme.muted }}>{r.sector || '—'}</td>
                <td style={{ padding: '7px 4px', textAlign: 'right', fontWeight: 600,
                              color: r.return_pct >= 0 ? '#047857' : '#b91c1c' }}>
                  {fmtPct(r.return_pct, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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

Object.assign(window, {
  EquityCurveChart, EventStudyChart, DrawdownChart, MonthlyHeatmap, Sparkline,
  TopPerformersPanel, RollingSharpeChart, UnderwaterChart, SectorBarChart,
  PARAM_HELP, HelpTip, NumField, paramIssues, maxTopNFor, useViewport,
  RebalancePanel, EarningsBreakdown,
});
