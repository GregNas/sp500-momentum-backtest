// Shared SVG chart primitives. Each chart accepts a `theme` object
// `{ accent, grid, axis, text, bg }` so V1/V2/V3 can restyle without forking.
// All charts are responsive via viewBox + preserveAspectRatio.

const { useMemo, useState, useRef, useEffect } = React;

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

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         style={{ width: '100%', height: '100%', display: 'block', overflow: 'visible' }}
         onMouseMove={(e) => {
           const rect = e.currentTarget.getBoundingClientRect();
           const x = ((e.clientX - rect.left) / rect.width) * W;
           const i = Math.round(((x - padL) / innerW) * (months.length - 1));
           onHover?.(Math.max(0, Math.min(months.length - 1, i)));
         }}
         onMouseLeave={() => onHover?.(null)}
    >
      {/* Y grid + labels */}
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)}
                stroke={theme.grid} strokeWidth="1" strokeDasharray="2 4" />
          <text x={padL - 8} y={yAt(t) + 4} textAnchor="end"
                fontSize="11" fill={theme.axis} fontFamily="JetBrains Mono, monospace">
            {logScale ? `${t}×` : t.toFixed(1)}
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
function EventStudyChart({ data, height = 280, theme, accent, showWinRate = false, benchmarkAvg = 0 }) {
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
function DrawdownChart({ series, months, height = 140, theme, accent }) {
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
                       color: v != null && Math.abs(v) > 0.08 ? '#fff' : theme.text,
                       fontWeight: 500,
                     }}>
                  {v != null ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}` : ''}
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
// TopPerformersPanel — leaderboard of S&P 500 winners over a recent window
// Self-contained: owns its own params + fetch state. Drop into a dashboard
// with a `theme` and `accent` to restyle.
// ============================================================
function TopPerformersPanel({ theme, accent, headingFont, universe = 'sp500' }) {
  const universeDefaultTopN = { sp500: 10, global_etfs: 5, us_sector_etfs: 3 };
  const [topN, setTopN] = useState(universeDefaultTopN[universe] || 10);
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  // Reset state when the parent universe changes.
  useEffect(() => {
    setTopN(universeDefaultTopN[universe] || 10);
    setData(null);
    setError(null);
  }, [universe]);

  const onRefresh = async () => {
    if (running) return;
    setRunning(true);
    setError(null);
    try {
      const result = await fetchTopPerformers({ universe, topN, days });
      setData(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

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
          <div>
            <div style={labelStyle}>Top N</div>
            <input type="number" value={topN} min={1} max={100}
                   onChange={e => setTopN(+e.target.value || 10)}
                   style={inputStyle} />
          </div>
          <div>
            <div style={labelStyle}>Days</div>
            <input type="number" value={days} min={1} max={365}
                   onChange={e => setDays(+e.target.value || 30)}
                   style={inputStyle} />
          </div>
          <button onClick={onRefresh} disabled={running}
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

Object.assign(window, {
  EquityCurveChart, EventStudyChart, DrawdownChart, MonthlyHeatmap, Sparkline,
  TopPerformersPanel, RollingSharpeChart, UnderwaterChart, SectorBarChart,
});
