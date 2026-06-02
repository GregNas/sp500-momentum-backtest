// Data loader: hits the FastAPI backend, exposes formatting helpers globally.
// Replaces the design package's static data.js (which had synthetic numbers).

async function runBacktest(params) {
  const r = await fetch('/api/backtest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
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
function fmtNum(v, digits = 2) {
  if (v == null || isNaN(v)) return '—';
  return v.toFixed(digits);
}

Object.assign(window, {
  runBacktest, fetchTopPerformers, fetchCacheInfo, clearCache,
  fmtMonthShort, fmtMonthYear, fmtPct, fmtMult, fmtNum,
});
