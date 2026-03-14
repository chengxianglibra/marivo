/* ============================================================
   OmniDB Shared Utilities
   ============================================================ */

/* --- Toast Notifications --- */
function toast(msg, type = 'info') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.remove(); }, 4000);
}

/* --- Modal --- */
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

/* --- Escaping --- */
function esc(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

/* --- Attribute-safe escaping --- */
function attr(s) {
  return esc(s).replace(/"/g, '&quot;');
}

/* --- Truncate --- */
function truncate(s, n = 60) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '...' : s;
}

/* --- Date Formatting --- */
function fmtDate(s) {
  if (!s) return '-';
  try {
    const d = new Date(s);
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch { return s; }
}

/* --- Loading Row --- */
function loadingRow(cols) {
  return `<tr><td colspan="${cols}" class="empty">Loading...</td></tr>`;
}

/* --- JSON Pretty Print --- */
function jsonPre(obj) {
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  return `<pre class="json-pre">${esc(s)}</pre>`;
}

/* --- Priority Badge --- */
function priorityBadge(p) {
  const map = { p0: 'badge-error', p1: 'badge-warning', p2: 'badge-info', p3: 'badge-success' };
  const cls = map[(p || '').toLowerCase()] || 'badge-gray';
  return `<span class="badge ${cls}">${esc(p || 'N/A')}</span>`;
}

/* --- Status Badge --- */
function statusBadge(status) {
  const map = {
    draft: 'badge-warning', published: 'badge-success', deprecated: 'badge-gray',
    pending: 'badge-warning', approved: 'badge-success', rejected: 'badge-error',
    running: 'badge-info', completed: 'badge-success', failed: 'badge-error',
    cancelled: 'badge-gray', submitted: 'badge-info',
    validated: 'badge-info', executing: 'badge-info',
    ok: 'badge-success',
  };
  const cls = map[(status || '').toLowerCase()] || 'badge-gray';
  return `<span class="badge ${cls}">${esc(status || 'unknown')}</span>`;
}

/* --- Confidence Color --- */
function confidenceColor(v) {
  if (v >= 0.7) return 'green';
  if (v >= 0.4) return 'yellow';
  return 'red';
}

/* --- Donut Chart (conic-gradient) --- */
function donutChart(value, size = 80) {
  const pct = Math.round((value || 0) * 100);
  const color = value >= 0.7 ? 'var(--color-success)' : value >= 0.4 ? 'var(--color-warning)' : 'var(--color-error)';
  return `<div class="donut-chart" style="width:${size}px;height:${size}px;background:conic-gradient(${color} ${pct}%, #e2e8f0 ${pct}%)">
    <div class="donut-label" style="width:${size - 24}px;height:${size - 24}px">${pct}%</div>
  </div>`;
}

/* --- Mini Bar Chart --- */
function miniBarChart(values, height = 40) {
  if (!values || !values.length) return '';
  const max = Math.max(...values, 1);
  const bars = values.map(v => {
    const h = Math.max(2, (v / max) * height);
    return `<div class="bar" style="height:${h}px"></div>`;
  }).join('');
  return `<div class="bar-chart" style="height:${height}px">${bars}</div>`;
}

/* --- Status Pipeline --- */
function statusPipeline(steps, current) {
  let html = '<div class="status-pipeline">';
  steps.forEach((step, i) => {
    const idx = steps.indexOf(current);
    let cls = '';
    if (i < idx) cls = 'done';
    else if (i === idx) cls = 'current';
    if (i > 0) html += `<div class="pipeline-connector ${i <= idx ? 'done' : ''}"></div>`;
    html += `<div class="pipeline-step ${cls}"><div class="step-circle">${i < idx ? '&#10003;' : i + 1}</div><span>${esc(step)}</span></div>`;
  });
  html += '</div>';
  return html;
}

/* --- Sidebar Tab Switching --- */
function initSidebar() {
  const navBtns = document.querySelectorAll('.sidebar-nav button[data-tab]');
  const panels = document.querySelectorAll('.panel[id^="panel-"]');
  const breadcrumbCurrent = document.querySelector('.breadcrumb .current');

  navBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      // Update active nav
      document.querySelectorAll('.sidebar-nav li').forEach(li => li.classList.remove('active'));
      btn.parentElement.classList.add('active');
      // Show panel
      panels.forEach(p => p.classList.remove('active'));
      const panel = document.getElementById('panel-' + tab);
      if (panel) panel.classList.add('active');
      // Update breadcrumb
      if (breadcrumbCurrent) {
        breadcrumbCurrent.textContent = btn.querySelector('.nav-label')?.textContent || tab;
      }
      // Dispatch event for lazy loading
      window.dispatchEvent(new CustomEvent('tab-change', { detail: { tab } }));
    });
  });
}

/* --- SVG Force-Directed Graph (simple) --- */
function renderForceGraph(container, nodes, edges, opts = {}) {
  const width = opts.width || container.clientWidth || 600;
  const height = opts.height || 400;
  const typeColors = {
    entity: '#3b82f6', metric: '#22c55e', asset: '#f59e0b',
    observation: '#3b82f6', claim: '#8b5cf6', recommendation: '#22c55e',
    default: '#94a3b8',
  };

  // Initialize positions
  nodes.forEach((n, i) => {
    n.x = n.x || width / 2 + (Math.random() - 0.5) * width * 0.6;
    n.y = n.y || height / 2 + (Math.random() - 0.5) * height * 0.6;
    n.vx = 0; n.vy = 0;
  });

  const nodeMap = {};
  nodes.forEach(n => nodeMap[n.id] = n);

  // Simple force simulation
  function simulate(iterations) {
    for (let iter = 0; iter < iterations; iter++) {
      // Repulsion between all nodes
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          let dx = nodes[j].x - nodes[i].x;
          let dy = nodes[j].y - nodes[i].y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 1;
          let force = 2000 / (dist * dist);
          let fx = (dx / dist) * force;
          let fy = (dy / dist) * force;
          nodes[i].vx -= fx; nodes[i].vy -= fy;
          nodes[j].vx += fx; nodes[j].vy += fy;
        }
      }
      // Attraction along edges
      edges.forEach(e => {
        const a = nodeMap[e.from || e.source];
        const b = nodeMap[e.to || e.target];
        if (!a || !b) return;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 1;
        let force = (dist - 120) * 0.02;
        let fx = (dx / dist) * force;
        let fy = (dy / dist) * force;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      });
      // Center gravity
      nodes.forEach(n => {
        n.vx += (width / 2 - n.x) * 0.003;
        n.vy += (height / 2 - n.y) * 0.003;
      });
      // Apply velocity with damping
      nodes.forEach(n => {
        n.vx *= 0.85; n.vy *= 0.85;
        n.x += n.vx; n.y += n.vy;
        n.x = Math.max(30, Math.min(width - 30, n.x));
        n.y = Math.max(30, Math.min(height - 30, n.y));
      });
    }
  }

  simulate(80);

  // Render SVG
  let svg = `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg" style="font-family:var(--font-family)">`;
  svg += '<defs><marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="#94a3b8"/></marker></defs>';

  // Edges
  edges.forEach(e => {
    const a = nodeMap[e.from || e.source];
    const b = nodeMap[e.to || e.target];
    if (!a || !b) return;
    const label = e.label || e.type || '';
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    svg += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#cbd5e1" stroke-width="1.5" marker-end="url(#arrowhead)"/>`;
    if (label) svg += `<text x="${mx}" y="${my - 6}" text-anchor="middle" fill="#94a3b8" font-size="10">${esc(label)}</text>`;
  });

  // Nodes
  nodes.forEach(n => {
    const color = typeColors[n.type] || typeColors.default;
    const r = 18;
    svg += `<circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${color}" opacity="0.9"/>`;
    svg += `<text x="${n.x}" y="${n.y + r + 14}" text-anchor="middle" fill="#334155" font-size="11" font-weight="500">${esc(truncate(n.label || n.id, 20))}</text>`;
    // Type initial in circle
    const initial = (n.type || '?')[0].toUpperCase();
    svg += `<text x="${n.x}" y="${n.y + 4}" text-anchor="middle" fill="#fff" font-size="11" font-weight="700">${initial}</text>`;
  });

  svg += '</svg>';
  container.innerHTML = svg;
}

/* --- Init on DOMContentLoaded --- */
document.addEventListener('DOMContentLoaded', initSidebar);
