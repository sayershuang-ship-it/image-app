/* ── Shared helpers for all app pages ─────────────────── */
/* eslint no-undef: off */

/* ── DOM shorthand ────────────────────────────────────── */
const $ = (sel) => document.querySelector(sel);

/* ── HTML escaping ─────────────────────────────────────  */
function escHtml(s) {
  const el = document.createElement('span');
  el.textContent = s || '';
  return el.innerHTML;
}
function escAttr(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── UTC timestamp parsing ─────────────────────────────  */
function parseTs(s) {
  if (!s) return new Date();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) return new Date(s.replace(' ', 'T') + 'Z');
  return new Date(s);
}

/* ── Toast notifications ───────────────────────────────  */
function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  $('#toast-container').appendChild(el);
  setTimeout(() => { el.remove(); }, 4000);
}

/* ── Utilities ─────────────────────────────────────────  */
function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/* ── Async job polling ─────────────────────────────────  */
async function pollJob(jobId, startTime, onTick) {
  const INTERVAL = 1500;
  const TIMEOUT  = 180_000;
  while (Date.now() - startTime < TIMEOUT) {
    await sleep(INTERVAL);
    if (onTick) onTick(Math.floor((Date.now() - startTime) / 1000));
    const r = await fetch(`/job-status/${jobId}`);
    if (!r.ok) throw new Error('Lost contact with server');
    const d = await r.json();
    if (d.status === 'done')   return d.results;
    if (d.status === 'failed') throw new Error(d.error || 'Generation failed');
  }
  throw new Error('Generation timed out (3 min)');
}

/* ── ZIP export ────────────────────────────────────────  */
async function exportZip(ids) {
  const r = await fetch('/export-zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids })
  });
  if (!r.ok) throw new Error('Export failed');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `image-studio-export-${Date.now()}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
  return ids.length;
}
