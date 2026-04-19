export function createOverviewModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    renderAdminDetailCard,
    renderLoadingState,
    renderStructuredError,
    renderEmptyState,
    fmtDate,
    adminUiDeepLinks,
    buildUiJobsUrl,
  } = shared;

  const OVERVIEW_CARD_SPECS = [
    {
      key: 'sources',
      title: 'Source Summary',
      href: '?tab=data-sources',
      actionLabel: 'Open Data Sources',
      helperLabel: 'Source sync inventory',
    },
    {
      key: 'engines',
      title: 'Engine / Binding Summary',
      href: '?tab=execution-engines',
      actionLabel: 'Open Execution Engines',
      helperLabel: 'Routing foundation',
    },
    {
      key: 'semantic',
      title: 'Semantic Summary',
      href: '?tab=semantic-catalog&subtab=entities',
      actionLabel: 'Open Semantic Catalog',
      helperLabel: 'Draft vs published contracts',
    },
    {
      key: 'sessions',
      title: 'Session Operations Summary',
      href: '?tab=analysis-ops',
      actionLabel: 'Open Analysis Ops',
      helperLabel: 'Operator entrypoint',
    },
    {
      key: 'runtime',
      title: 'Runtime / Jobs Summary',
      href: '?tab=runtime-jobs&subtab=jobs',
      actionLabel: 'Open Runtime & Jobs',
      helperLabel: 'Operator-facing runtime truth',
    },
    {
      key: 'governance',
      title: 'Governance / Approvals Summary',
      href: '?tab=governance&subtab=approvals',
      actionLabel: 'Open Governance',
      helperLabel: 'Pending review queue',
    },
    {
      key: 'observability',
      title: 'Observability Summary',
      href: '?tab=observability',
      actionLabel: 'Open Observability',
      helperLabel: 'Health and metrics snapshot',
    },
  ];

  let overviewRenderVersion = 0;

  function countStatuses(items) {
    return (items || []).reduce((acc, item) => {
      const status = String(item?.status || 'unknown').toLowerCase();
      acc[status] = (acc[status] || 0) + 1;
      return acc;
    }, {});
  }

  function pickLatest(items, keys = ['updated_at', 'ended_at', 'created_at']) {
    return (items || []).reduce((latest, item) => {
      const currentValue = keys.map((key) => item?.[key]).find(Boolean);
      if (!currentValue) return latest;
      if (!latest) return item;
      const latestValue = keys.map((key) => latest?.[key]).find(Boolean);
      return String(currentValue) > String(latestValue || '') ? item : latest;
    }, null);
  }

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : '-';
  }

  function extractItems(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
  }

  function renderOverviewKpis(kpis) {
    return `
      <div class="overview-card-kpis">
        ${kpis.map((item) => `
          <div class="overview-kpi">
            <div class="overview-kpi-label">${esc(item.label)}</div>
            <div class="overview-kpi-value">${esc(item.value)}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderOverviewCardLinks(primaryLink, secondaryLink) {
    return `
      <div class="overview-card-links">
        <a class="btn btn-sm" href="${esc(primaryLink.href)}">${esc(primaryLink.label)}</a>
        ${secondaryLink ? `<a class="btn btn-sm" href="${esc(secondaryLink.href)}">${esc(secondaryLink.label)}</a>` : ''}
      </div>
    `;
  }

  function renderOverviewCardState(card, bodyHtml, tone = '') {
    return `
      <section class="overview-summary-card ${tone}" data-overview-card="${esc(card.key)}">
        <div class="overview-card-header">
          <div>
            <h3>${esc(card.title)}</h3>
            <p class="overview-card-copy">${esc(card.helperLabel)}</p>
          </div>
          <span class="shell-chip">overview-card</span>
        </div>
        ${bodyHtml}
      </section>
    `;
  }

  function renderOverviewCardLoading(card) {
    return renderOverviewCardState(
      card,
      `<div class="overview-card-loading">${renderLoadingState(`Loading ${card.title}...`)}</div>`
    );
  }

  function renderOverviewCardError(card, error) {
    return renderOverviewCardState(
      card,
      `
        <div class="overview-card-error">
          ${renderStructuredError(error, `${card.title} unavailable.`)}
        </div>
        ${renderOverviewCardLinks({ href: card.href, label: card.actionLabel }, null)}
      `,
      'is-alert'
    );
  }

  function renderOverviewCardEmpty(card, copy, secondaryLink = null) {
    return renderOverviewCardState(
      card,
      `
        <div class="overview-card-empty">${renderEmptyState(copy)}</div>
        ${renderOverviewCardLinks({ href: card.href, label: card.actionLabel }, secondaryLink)}
      `
    );
  }

  function renderOverviewCardSuccess(card, summary) {
    return renderOverviewCardState(
      card,
      `
        ${renderOverviewKpis(summary.kpis)}
        <div class="overview-card-meta">
          <p class="overview-card-copy">${esc(summary.copy)}</p>
          ${summary.metaHtml || ''}
        </div>
        ${renderOverviewCardLinks(
          { href: card.href, label: card.actionLabel },
          summary.secondaryLink || null
        )}
      `,
      summary.tone || ''
    );
  }

  function summarizeSources(sources) {
    if (!sources.length) {
      return { empty: 'No data sources configured yet.' };
    }
    const statusCounts = countStatuses(sources);
    const activeCount = statusCounts.active || 0;
    const syncEnabledCount = sources.filter((item) => item?.sync_mode && item.sync_mode !== 'none').length;
    const latest = pickLatest(sources);
    return {
      kpis: [
        { label: 'Total Sources', value: String(sources.length) },
        { label: 'Active', value: String(activeCount) },
        { label: 'Sync Enabled', value: String(syncEnabledCount) },
        { label: 'Last Updated', value: formatMaybeDate(latest?.updated_at) },
      ],
      copy: 'Source inventory, sync readiness, and lifecycle entrypoint for catalog onboarding.',
      metaHtml: '<div class="overview-card-contracts"><span class="shell-chip">GET /sources</span></div>',
    };
  }

  function summarizeEngines(engines, bindings) {
    if (!engines.length && !bindings.length) {
      return { empty: 'No execution engines or source-engine bindings configured yet.' };
    }
    const activeBindings = bindings.filter(
      (item) => String(item?.status || '').toLowerCase() === 'active'
    ).length;
    const latestBinding = pickLatest(bindings);
    return {
      kpis: [
        { label: 'Engines', value: String(engines.length) },
        { label: 'Bindings', value: String(bindings.length) },
        { label: 'Active Bindings', value: String(activeBindings) },
        { label: 'Latest Binding', value: latestBinding?.binding_id || '-' },
      ],
      copy: 'Engine inventory and binding coverage for source routing and execution setup.',
      metaHtml:
        '<div class="overview-card-contracts"><span class="shell-chip">GET /engines</span><span class="shell-chip">GET /bindings</span></div>',
    };
  }

  function summarizeSemantic(payloads) {
    const groups = [
      ['Entities', extractItems(payloads.entities)],
      ['Metrics', extractItems(payloads.metrics)],
      ['Process Objects', extractItems(payloads.processObjects)],
      ['Dimensions', extractItems(payloads.dimensions)],
      ['Time', extractItems(payloads.time)],
      ['Enum Sets', extractItems(payloads.enumSets)],
      ['Typed Bindings', extractItems(payloads.typedBindings)],
      ['Compatibility Profiles', extractItems(payloads.compatibilityProfiles)],
    ];
    const allItems = groups.flatMap(([, items]) => items);
    if (!allItems.length) {
      return { empty: 'No semantic contracts, typed bindings, or compatibility profiles published yet.' };
    }
    const statusCounts = countStatuses(allItems);
    const groupSummary = groups
      .filter(([, items]) => items.length > 0)
      .slice(0, 3)
      .map(([label, items]) => `<span class="shell-chip">${esc(label)} ${esc(String(items.length))}</span>`)
      .join('');
    return {
      kpis: [
        { label: 'Contracts', value: String(allItems.length) },
        { label: 'Draft', value: String(statusCounts.draft || 0) },
        { label: 'Published', value: String(statusCounts.published || 0) },
        { label: 'Typed Bindings', value: String(extractItems(payloads.typedBindings).length) },
      ],
      copy: 'Typed semantic lifecycle across contracts, bindings, and compatibility profiles.',
      metaHtml: `<div class="overview-card-contracts">${groupSummary || '<span class="shell-chip">semantic inventory</span>'}</div>`,
      tone: (statusCounts.draft || 0) > 0 ? 'is-warning' : '',
    };
  }

  function summarizeSessions(payload) {
    const sessions = extractItems(payload);
    if (!sessions.length) {
      return { empty: 'No analysis sessions available yet.' };
    }
    const statusCounts = countStatuses(sessions);
    const openCount = statusCounts.open || 0;
    const terminalCount = sessions.length - openCount;
    const latest = pickLatest(sessions);
    const deepLinks = adminUiDeepLinks({ sessionId: latest?.session_id || '' });
    return {
      kpis: [
        { label: 'Open Sessions', value: String(openCount) },
        { label: 'Terminal Sessions', value: String(terminalCount) },
        { label: 'Latest Session', value: latest?.session_id || '-' },
        { label: 'Updated', value: formatMaybeDate(latest?.updated_at) },
      ],
      copy: 'Session operations summary for terminate workflows and canonical drill-ins.',
      secondaryLink: { href: deepLinks.sessions, label: 'Open /ui Sessions' },
      metaHtml: '<div class="overview-card-contracts"><span class="shell-chip">GET /sessions</span></div>',
      tone: openCount > 0 ? 'is-warning' : '',
    };
  }

  function summarizeRuntime(jobs) {
    if (!jobs.length) {
      return { empty: 'No background jobs recorded yet.' };
    }
    const statusCounts = countStatuses(jobs);
    const pending = (statusCounts.submitted || 0) + (statusCounts.running || 0);
    const failed = statusCounts.failed || 0;
    const latest = pickLatest(jobs);
    return {
      kpis: [
        { label: 'Pending Jobs', value: String(pending) },
        { label: 'Failed Jobs', value: String(failed) },
        { label: 'Active Jobs', value: String(statusCounts.running || 0) },
        { label: 'Latest Job', value: latest?.job_id || '-' },
      ],
      copy: 'Jobs are the current runtime signal for operator-facing troubleshooting on the admin homepage.',
      secondaryLink: { href: buildUiJobsUrl('', pending > 0 ? 'running' : ''), label: 'Open /ui Jobs' },
      metaHtml:
        '<div class="overview-card-contracts"><span class="shell-chip">GET /jobs</span><span class="shell-chip">runtime truth stays in /ui</span></div>',
      tone: failed > 0 ? 'is-alert' : pending > 0 ? 'is-warning' : '',
    };
  }

  function summarizeGovernance(approvals, policies, qualityRules) {
    if (!approvals.length && !policies.length && !qualityRules.length) {
      return { empty: 'No approvals, policies, or quality rules configured yet.' };
    }
    const pendingApprovals = approvals.filter(
      (item) => String(item?.status || '').toLowerCase() === 'pending'
    ).length;
    return {
      kpis: [
        { label: 'Pending Approvals', value: String(pendingApprovals) },
        { label: 'Policies', value: String(policies.length) },
        { label: 'Quality Rules', value: String(qualityRules.length) },
        { label: 'Governance Items', value: String(policies.length + qualityRules.length) },
      ],
      copy: 'Governance queue and rule inventory for review workflows and enforcement configuration.',
      metaHtml:
        '<div class="overview-card-contracts"><span class="shell-chip">GET /approvals?status=pending</span><span class="shell-chip">GET /policies</span><span class="shell-chip">GET /quality-rules</span></div>',
      tone: pendingApprovals > 0 ? 'is-alert' : '',
    };
  }

  function summarizeObservability(health, metrics) {
    const requestCount = metrics?.request_count
      ? Object.values(metrics.request_count).reduce((sum, value) => sum + Number(value || 0), 0)
      : 0;
    const errorCount = metrics?.error_count
      ? Object.values(metrics.error_count).reduce((sum, value) => sum + Number(value || 0), 0)
      : 0;
    const activeJobs = metrics?.active_jobs ?? '-';
    const activeSessions = metrics?.active_sessions ?? '-';
    return {
      kpis: [
        { label: 'Health Status', value: String(health?.status || 'unknown') },
        { label: 'Active Sessions', value: String(activeSessions) },
        { label: 'Active Jobs', value: String(activeJobs) },
        { label: 'Error Count', value: String(errorCount) },
      ],
      copy: requestCount > 0
        ? `Metrics snapshot available with ${requestCount} recorded request(s).`
        : 'Health is available. Metrics may be cold, disabled, or not yet populated.',
      metaHtml:
        '<div class="overview-card-contracts"><span class="shell-chip">GET /health</span><span class="shell-chip">GET /metrics</span></div>',
      tone: String(health?.status || '').toLowerCase() === 'ok' ? '' : 'is-alert',
    };
  }

  function renderOverviewOperationsCard() {
    return renderAdminDetailCard({
      title: 'Overview Operating Model',
      statusHtml: '<span class="shell-chip">configuration + operator workflows</span>',
      note: 'Overview is intentionally summary-only. Editing, large tables, and canonical deep reads stay in their dedicated pages.',
      bodyHtml: `
        <div class="overview-mini-list">
          <div class="overview-mini-item">
            <strong>Configuration</strong>
            Sources, engines, semantic contracts, and governance remain primary admin objects.
          </div>
          <div class="overview-mini-item">
            <strong>Operations</strong>
            Session termination, runtime troubleshooting, and job reading stay operator-focused.
          </div>
          <div class="overview-mini-item">
            <strong>Canonical Reads</strong>
            Use adminUiDeepLinks() to jump back into /ui for Sessions, State, Runtime, and Jobs.
          </div>
        </div>
      `,
    });
  }

  function renderOverviewContractCard() {
    return renderAdminDetailCard({
      title: 'Overview Data Contracts',
      statusHtml: '<span class="shell-chip">partial failure tolerant</span>',
      note: 'Each card loads independently and degrades locally when one summary endpoint fails.',
      bodyHtml: `
        <div class="overview-mini-list">
          <div class="overview-mini-item"><strong>Card-level loading</strong>Loading Source Summary..., Loading Semantic Summary..., and other card placeholders render before fetches settle.</div>
          <div class="overview-mini-item"><strong>Card-level empty state</strong>No data sources configured yet. No analysis sessions available yet. No background jobs recorded yet.</div>
          <div class="overview-mini-item"><strong>Card-level errors</strong>Source Summary unavailable. Semantic Summary unavailable. Observability Summary unavailable.</div>
        </div>
      `,
    });
  }

  function render() {
    const cardsHtml = OVERVIEW_CARD_SPECS.map((card) => renderOverviewCardLoading(card)).join('');
    return `
      <div class="overview-page">
        <div class="overview-grid" data-role="overview-grid">
          ${cardsHtml}
        </div>
        <div class="overview-ops-grid">
          ${renderOverviewOperationsCard()}
          ${renderOverviewContractCard()}
        </div>
      </div>
    `;
  }

  function renderOverviewCardInto(panel, cardKey, html) {
    const target = panel.querySelector(`[data-overview-card="${cardKey}"]`);
    if (target) target.outerHTML = html;
  }

  async function hydrate(panel) {
    const renderVersion = ++overviewRenderVersion;
    const cards = Object.fromEntries(OVERVIEW_CARD_SPECS.map((card) => [card.key, card]));

    const safeRender = (cardKey, html) => {
      if (renderVersion !== overviewRenderVersion) return;
      renderOverviewCardInto(panel, cardKey, html);
    };

    const settleCard = async (cardKey, loader, summarize) => {
      try {
        const payload = await loader();
        const summary = summarize(payload);
        if (summary?.empty) {
          safeRender(
            cardKey,
            renderOverviewCardEmpty(cards[cardKey], summary.empty, summary.secondaryLink || null)
          );
          return;
        }
        safeRender(cardKey, renderOverviewCardSuccess(cards[cardKey], summary));
      } catch (error) {
        safeRender(cardKey, renderOverviewCardError(cards[cardKey], error));
      }
    };

    void settleCard('sources', () => ctx.adminApi.listSources(), summarizeSources);
    void settleCard(
      'engines',
      async () => ({
        engines: await ctx.adminApi.listEngines(),
        bindings: await ctx.adminApi.listBindings(),
      }),
      (payload) => summarizeEngines(payload.engines, payload.bindings)
    );
    void settleCard(
      'semantic',
      async () => ({
        entities: await ctx.adminApi.listSemanticEntities(),
        metrics: await ctx.adminApi.listSemanticMetrics(),
        processObjects: await ctx.adminApi.listSemanticProcessObjects(),
        dimensions: await ctx.adminApi.listSemanticDimensions(),
        time: await ctx.adminApi.listSemanticTime(),
        enumSets: await ctx.adminApi.listSemanticEnumSets(),
        typedBindings: await ctx.adminApi.listTypedSemanticBindings(),
        compatibilityProfiles: await ctx.adminApi.listCompatibilityProfiles(),
      }),
      summarizeSemantic
    );
    void settleCard('sessions', () => ctx.adminApi.getJson('/sessions', 'Session Operations Summary unavailable.'), summarizeSessions);
    void settleCard('runtime', () => ctx.adminApi.getJson('/jobs', 'Runtime / Jobs Summary unavailable.'), summarizeRuntime);
    void settleCard(
      'governance',
      async () => ({
        approvals: await ctx.adminApi.getJson('/approvals?status=pending', 'Governance / Approvals Summary unavailable.'),
        policies: await ctx.adminApi.getJson('/policies', 'Governance / Approvals Summary unavailable.'),
        qualityRules: await ctx.adminApi.getJson('/quality-rules', 'Governance / Approvals Summary unavailable.'),
      }),
      (payload) => summarizeGovernance(payload.approvals, payload.policies, payload.qualityRules)
    );
    void settleCard(
      'observability',
      async () => ({
        health: await ctx.adminApi.getJson('/health', 'Observability Summary unavailable.'),
        metrics: await ctx.adminApi.getJson('/metrics', 'Observability Summary unavailable.'),
      }),
      (payload) => summarizeObservability(payload.health, payload.metrics)
    );
  }

  return { render, hydrate };
}
