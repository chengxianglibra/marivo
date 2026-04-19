export function createObservabilityModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    fmtDate,
    renderEmptyState,
    renderLoadingState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    statusBadge,
  } = shared;

  const AUTO_REFRESH_MS = 15000;

  let observabilityRenderVersion = 0;
  let observabilityAutoRefreshTimer = 0;
  let observabilityRefreshState = {
    lastRefreshedAt: '',
    nextRefreshAt: 0,
  };

  function clearObservabilityAutoRefresh() {
    if (observabilityAutoRefreshTimer) {
      window.clearTimeout(observabilityAutoRefreshTimer);
      observabilityAutoRefreshTimer = 0;
    }
  }

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : '-';
  }

  function formatMetricValue(value) {
    if (value == null || value === '') {
      return '-';
    }
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) {
        return String(value);
      }
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    if (typeof value === 'boolean') {
      return value ? 'true' : 'false';
    }
    return String(value);
  }

  function sumObjectValues(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }
    let total = 0;
    let hasNumeric = false;
    for (const item of Object.values(value)) {
      if (typeof item === 'number' && Number.isFinite(item)) {
        total += item;
        hasNumeric = true;
      }
    }
    return hasNumeric ? total : null;
  }

  function collectPrimaryMetrics(metrics) {
    const requestCount = sumObjectValues(metrics?.request_count) ?? metrics?.counters?.requests_total ?? null;
    const errorCount = sumObjectValues(metrics?.error_count) ?? metrics?.counters?.errors_total ?? null;
    const stepCount = sumObjectValues(metrics?.step_count) ?? metrics?.counters?.steps_executed ?? null;
    const activeSessions = metrics?.active_sessions ?? metrics?.gauges?.active_sessions ?? null;
    const activeJobs = metrics?.active_jobs ?? metrics?.gauges?.pending_jobs ?? metrics?.gauges?.active_jobs ?? null;
    const collectedAt = metrics?.collected_at ?? null;

    return [
      { label: 'Request Count', value: requestCount, tone: 'neutral' },
      { label: 'Error Count', value: errorCount, tone: errorCount > 0 ? 'warning' : 'good' },
      { label: 'Step Count', value: stepCount, tone: 'neutral' },
      { label: 'Active Sessions', value: activeSessions, tone: activeSessions > 0 ? 'neutral' : 'muted' },
      { label: 'Active Jobs', value: activeJobs, tone: activeJobs > 0 ? 'warning' : 'good' },
      { label: 'Collected At', value: collectedAt ? formatMaybeDate(collectedAt) : '-', tone: 'neutral' },
    ];
  }

  function collectKeyValueRows(title, metrics, primaryKeys = []) {
    const rows = [];
    const pushObjectEntries = (groupLabel, value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return;
      }
      for (const [key, item] of Object.entries(value)) {
        rows.push({
          label: `${groupLabel}.${key}`,
          value: formatMetricValue(item),
        });
      }
    };

    pushObjectEntries(title, metrics?.[title]);
    for (const key of primaryKeys) {
      if (rows.length >= 8) break;
      if (metrics && Object.hasOwn(metrics, key)) {
        rows.push({
          label: key,
          value: formatMetricValue(metrics[key]),
        });
      }
    }
    return rows.slice(0, 8);
  }

  function renderHealthSummaryCard(healthResult) {
    if (healthResult.loading) {
      return renderAdminDetailCard({
        title: 'Health Summary',
        statusHtml: '<span class="shell-chip">loading</span>',
        note: 'GET /health is the canonical service health endpoint.',
        bodyHtml: renderLoadingState('Loading health summary...'),
      });
    }
    if (healthResult.error) {
      return renderAdminDetailCard({
        title: 'Health Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'Health endpoint failures degrade this page instead of blocking metrics visibility.',
        bodyHtml: renderStructuredError(healthResult.error, 'Health Summary unavailable.'),
      });
    }

    const health = healthResult.data || {};
    const status = String(health.status || 'unknown');
    return renderAdminDetailCard({
      title: 'Health Summary',
      statusHtml: statusBadge(status),
      note: 'This page remains read-only and does not act as an incident console.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'status', value: status },
          { label: 'db_path', value: health.db_path || '-' },
          { label: 'last_refresh', value: formatMaybeDate(observabilityRefreshState.lastRefreshedAt) },
        ])}
      `,
    });
  }

  function renderMetricsSummaryCard(metricsResult) {
    if (metricsResult.loading) {
      return renderAdminDetailCard({
        title: 'Metrics Summary Cards',
        statusHtml: '<span class="shell-chip">loading</span>',
        note: 'GET /metrics feeds the key operational summary cards.',
        bodyHtml: renderLoadingState('Loading metrics summary cards...'),
      });
    }
    if (metricsResult.error) {
      return renderAdminDetailCard({
        title: 'Metrics Summary Cards',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'Metrics endpoint failures should not hide the rest of the page.',
        bodyHtml: renderStructuredError(metricsResult.error, 'Metrics Summary unavailable.'),
      });
    }

    const cards = collectPrimaryMetrics(metricsResult.data || {});
    if (!cards.some((item) => item.value != null && item.value !== '')) {
      return renderAdminDetailCard({
        title: 'Metrics Summary Cards',
        statusHtml: '<span class="shell-chip">empty</span>',
        note: 'Key metrics cards render only when the backend exposes recognizable signals.',
        bodyHtml: renderEmptyState('No key metrics available yet.'),
      });
    }

    return renderAdminDetailCard({
      title: 'Metrics Summary Cards',
      statusHtml: '<span class="shell-chip">read-only</span>',
      note: 'Auto-refresh keeps summary cards current without adding operator controls.',
      bodyHtml: `
        <div class="observability-summary-grid">
          ${cards.map((item) => `
            <div class="observability-summary-card is-${esc(item.tone)}">
              <h4>${esc(item.label)}</h4>
              <div class="observability-summary-value">${esc(formatMetricValue(item.value))}</div>
            </div>
          `).join('')}
        </div>
      `,
    });
  }

  function renderKeyMetricsCard(metricsResult) {
    if (metricsResult.loading) {
      return renderAdminDetailCard({
        title: 'Key Metric Values',
        statusHtml: '<span class="shell-chip">loading</span>',
        note: 'The card prioritizes request, error, step, and active-workload numbers first.',
        bodyHtml: renderLoadingState('Loading key metric values...'),
      });
    }
    if (metricsResult.error) {
      return renderAdminDetailCard({
        title: 'Key Metric Values',
        statusHtml: '<span class="shell-chip">degraded</span>',
        note: 'Metric parsing errors fall back to structured endpoint errors.',
        bodyHtml: renderStructuredError(metricsResult.error, 'Metrics values unavailable.'),
      });
    }

    const metrics = metricsResult.data || {};
    const requestRows = collectKeyValueRows('request_count', metrics, ['active_sessions', 'active_jobs']);
    const errorRows = collectKeyValueRows('error_count', metrics);
    const stepRows = collectKeyValueRows('step_count', metrics, ['execution_stage_count']);
    const rows = [...requestRows, ...errorRows, ...stepRows].slice(0, 12);

    return renderAdminDetailCard({
      title: 'Key Metric Values',
      statusHtml: '<span class="shell-chip">partial-safe</span>',
      note: 'If some metric groups are missing, the card keeps rendering the groups that did parse.',
      bodyHtml: rows.length
        ? renderDetailList(rows)
        : renderEmptyState('No key metrics available yet.'),
    });
  }

  function renderRawPanels(healthResult, metricsResult, rawMetricsResult) {
    const rawPanelBody = rawMetricsResult.loading
      ? renderLoadingState('Loading metrics raw text...')
      : rawMetricsResult.error
        ? renderStructuredError(rawMetricsResult.error, 'Metrics raw text unavailable.')
        : rawMetricsResult.data
          ? `<pre class="json-pre observability-raw-text">${esc(rawMetricsResult.data)}</pre>`
          : renderEmptyState('No metrics raw text available yet.');

    return `
      ${renderJsonPanel(
        'Health JSON',
        healthResult.error ? null : healthResult.data,
        healthResult.error ? 'Health JSON unavailable.' : 'No health payload.'
      )}
      ${renderJsonPanel(
        'Metrics JSON',
        metricsResult.error ? null : metricsResult.data,
        metricsResult.error ? 'Metrics JSON unavailable.' : 'No metrics payload.'
      )}
      ${renderAdminDetailCard({
        title: 'Metrics Raw Text',
        statusHtml: '<span class="shell-chip">GET /metrics?format=prometheus</span>',
        note: 'Raw text complements JSON views when operators need exporter-ready metric lines.',
        bodyHtml: rawPanelBody,
      })}
    `;
  }

  function renderRefreshCard(loading) {
    const nextRefresh = observabilityRefreshState.nextRefreshAt
      ? formatMaybeDate(new Date(observabilityRefreshState.nextRefreshAt).toISOString())
      : '-';
    return renderAdminDetailCard({
      title: 'Refresh Status',
      statusHtml: `<span class="shell-chip">${loading ? 'refreshing' : 'auto-refresh'}</span>`,
      note: 'Automatic refresh is enabled for this page. Manual Refresh does not change the read-only boundary.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'Auto Refresh', value: 'Every 15 seconds' },
          { label: 'Last Refreshed', value: formatMaybeDate(observabilityRefreshState.lastRefreshedAt) },
          { label: 'Next Refresh', value: nextRefresh },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-primary" data-action="refresh-observability">Manual Refresh</button>
        </div>
      `,
    });
  }

  function renderBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderRefreshCard(viewModel.loading)}
        ${renderHealthSummaryCard(viewModel.health)}
        ${renderMetricsSummaryCard(viewModel.metrics)}
      `,
      secondaryHtml: `
        ${renderKeyMetricsCard(viewModel.metrics)}
      `,
      detailHtml: renderRawPanels(viewModel.health, viewModel.metrics, viewModel.rawMetrics),
    });
  }

  function bindEvents(target) {
    target.querySelector('[data-action="refresh-observability"]')?.addEventListener('click', () => {
      const panel = document.getElementById('panel-observability');
      const route = ctx.getCurrentRoute();
      if (panel && route?.tab === 'observability') {
        void hydrate(panel, route);
      }
    });
  }

  function render() {
    return `
      <div data-role="observability-body">
        ${renderBody({
          loading: true,
          health: { loading: true, error: null, data: null },
          metrics: { loading: true, error: null, data: null },
          rawMetrics: { loading: true, error: null, data: null },
        })}
      </div>
    `;
  }

  async function requestRawMetrics() {
    return ctx.adminApi.getMetricsPrometheus();
  }

  function scheduleAutoRefresh(route) {
    clearObservabilityAutoRefresh();
    observabilityRefreshState.nextRefreshAt = Date.now() + AUTO_REFRESH_MS;
    observabilityAutoRefreshTimer = window.setTimeout(() => {
      const panel = document.getElementById('panel-observability');
      const currentRoute = ctx.getCurrentRoute();
      if (!panel || currentRoute?.tab !== 'observability') {
        clearObservabilityAutoRefresh();
        return;
      }
      void hydrate(panel, route);
    }, AUTO_REFRESH_MS);
  }

  async function hydrate(panel, route) {
    clearObservabilityAutoRefresh();
    const renderVersion = ++observabilityRenderVersion;
    const safeRender = (viewModel) => {
      if (renderVersion !== observabilityRenderVersion) return;
      const target = panel.querySelector('[data-role="observability-body"]');
      if (target) {
        target.innerHTML = renderBody(viewModel);
        bindEvents(target);
      }
    };

    safeRender({
      loading: true,
      health: { loading: true, error: null, data: null },
      metrics: { loading: true, error: null, data: null },
      rawMetrics: { loading: true, error: null, data: null },
    });

    const [healthResult, metricsResult, rawMetricsResult] = await Promise.allSettled([
      ctx.adminApi.getHealth(),
      ctx.adminApi.getMetrics(),
      requestRawMetrics(),
    ]);

    observabilityRefreshState.lastRefreshedAt = new Date().toISOString();
    scheduleAutoRefresh(route);

    safeRender({
      loading: false,
      health: healthResult.status === 'fulfilled'
        ? { loading: false, error: null, data: healthResult.value }
        : { loading: false, error: normalizeApiError(healthResult.reason, 'Health Summary unavailable.'), data: null },
      metrics: metricsResult.status === 'fulfilled'
        ? { loading: false, error: null, data: metricsResult.value }
        : { loading: false, error: normalizeApiError(metricsResult.reason, 'Metrics Summary unavailable.'), data: null },
      rawMetrics: rawMetricsResult.status === 'fulfilled'
        ? { loading: false, error: null, data: rawMetricsResult.value }
        : { loading: false, error: normalizeApiError(rawMetricsResult.reason, 'Metrics raw text unavailable.'), data: null },
    });
  }

  return { render, hydrate };
}
