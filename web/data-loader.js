// Data loader: hits the FastAPI backend, exposes formatting helpers globally.
// Replaces the design package's static data.js (which had synthetic numbers).

// Poll /api/progress/{runId} once a second, forwarding the latest server-side
// status message ("Cache hit…", "Fetching 503 tickers…") to onProgress.
function pollProgress(runId, onProgress) {
  let seen = 0;
  const timer = setInterval(async () => {
    try {
      const r = await fetch(`/api/progress/${runId}`);
      if (!r.ok) return;
      const { messages } = await r.json();
      if (messages.length > seen) {
        seen = messages.length;
        onProgress(messages[messages.length - 1]);
      }
    } catch (e) { /* polling is best-effort */ }
  }, 1000);
  return () => clearInterval(timer);
}

function newRunId() {
  return (crypto.randomUUID && crypto.randomUUID()) || `run-${performance.now()}`;
}

async function runBacktest(params, onProgress) {
  const runId = newRunId();
  const stopPolling = onProgress ? pollProgress(runId, onProgress) : null;
  try {
    const r = await fetch('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...params, runId }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || 'Backtest failed');
    }
    const body = await r.json();
    // Convert ISO date strings into Date objects on the EQUITY.months axis
    if (body.EQUITY && body.EQUITY.months) {
      body.EQUITY.months = body.EQUITY.months.map(s => new Date(s));
    }
    return body;
  } finally {
    if (stopPolling) stopPolling();
  }
}

async function fetchTopPerformers(params) {
  const r = await fetch('/api/top-performers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || 'Top-performers fetch failed');
  }
  return await r.json();
}

async function fetchCacheInfo() {
  const r = await fetch('/api/cache');
  if (!r.ok) return null;
  return await r.json();
}

async function fetchUniverses() {
  const r = await fetch('/api/universes');
  if (!r.ok) return null;
  return await r.json();
}

async function clearCache() {
  await fetch('/api/cache/clear', { method: 'POST' });
}

// ---- formatters ----
function fmtMonthShort(d) {
  return d.toLocaleDateString('en-US', { month: 'short' });
}
function fmtMonthYear(d) {
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}
function fmtPct(v, digits = 2, signed = true) {
  if (v == null || isNaN(v)) return '—';
  const sign = signed && v > 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(digits)}%`;
}
function fmtMult(v) {
  if (v == null || isNaN(v)) return '—';
  return `${v.toFixed(2)}×`;
}
// Growth-of-$1 multiple (e.g. 5.79) rendered as a cumulative % return (+479%).
function fmtGrowthPct(mult, digits = 0) {
  if (mult == null || isNaN(mult)) return '—';
  return fmtPct(mult - 1, digits);
}
// Compact equity-axis tick label: multiple 1 -> "0%", 5 -> "+400%".
function fmtEquityAxis(mult) {
  if (mult == null || isNaN(mult)) return '';
  const r = Math.round((mult - 1) * 100);
  return `${r > 0 ? '+' : ''}${r}%`;
}
function fmtNum(v, digits = 2) {
  if (v == null || isNaN(v)) return '—';
  return v.toFixed(digits);
}
function fmtAge(hours) {
  if (hours == null || isNaN(hours)) return '—';
  if (hours < 1) return '<1 h ago';
  if (hours < 48) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

Object.assign(window, {
  runBacktest, fetchTopPerformers, fetchCacheInfo, fetchUniverses, clearCache,
  fmtMonthShort, fmtMonthYear, fmtPct, fmtMult, fmtGrowthPct, fmtEquityAxis,
  fmtNum, fmtAge,
});
