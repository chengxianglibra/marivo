export const ADMIN_TABS = [
  'overview',
  'data-sources',
  'execution-engines',
  'semantic-catalog',
  'analysis-ops',
  'runtime-jobs',
  'governance',
  'observability',
];

export const DEFAULT_ADMIN_ROUTE = {
  tab: 'overview',
  subtab: '',
  sourceId: '',
  engineId: '',
  mappingId: '',
  objectId: '',
  sessionId: '',
  policyId: '',
  ruleId: '',
  requestId: '',
  propositionId: '',
  artifactId: '',
  jobId: '',
};

export const ADMIN_TAB_META = {
  overview: {
    label: 'Overview',
    subtitle: 'Unified entrypoint for configuration and operator workflows.',
    objectLabel: '',
    subtabConfig: [],
    summary: [
      'Use this page as the default landing shell for the redesigned admin console.',
      'Overview will later host summary cards for sources, engines, semantic lifecycle, sessions, governance, and health.',
      'This shell intentionally avoids authoring, intent submission, or plan execution controls.',
    ],
    routeFocus: ['tab'],
    uiLinks: [{ label: 'Open Sessions in /ui', route: { tab: 'sessions' } }],
  },
  'data-sources': {
    label: 'Data Sources',
    subtitle: 'Configuration area for source lifecycle, catalog selections, and sync entrypoints.',
    objectLabel: 'source_id',
    subtabConfig: [],
    summary: [
      'T1 only establishes the page shell and source-oriented URL state.',
      'Future tasks will attach source lists, details, catalog browsing, and sync controls here.',
      'Semantic contract authoring remains outside this page.',
    ],
    routeFocus: ['tab', 'source_id'],
    uiLinks: [],
  },
  'execution-engines': {
    label: 'Execution Engines',
    subtitle: 'Manage execution engine inventory and source-engine routing mappings.',
    objectLabel: 'engine_id',
    subtabConfig: [],
    summary: [
      'Manage execution engine inventory and source-engine mappings from one operator-facing page.',
      'Source-to-engine mappings stay distinct from semantic typed bindings, which remain in Semantic Catalog.',
      'The URL contract keeps engine_id and mapping_id shareable for stable drill-ins and refresh-safe detail panes.',
    ],
    routeFocus: ['tab', 'engine_id', 'mapping_id'],
    uiLinks: [],
  },
  'semantic-catalog': {
    label: 'Semantic Catalog',
    subtitle: 'Centralized shell for typed semantic objects, bindings, and compatibility profiles.',
    objectLabel: 'object_id',
    subtabConfig: [
      { value: 'entities', label: 'Entities' },
      { value: 'metrics', label: 'Metrics' },
      { value: 'process-objects', label: 'Process Objects' },
      { value: 'dimensions', label: 'Dimensions' },
      { value: 'time', label: 'Time' },
      { value: 'enum-sets', label: 'Enum Sets' },
      { value: 'typed-bindings', label: 'Typed Bindings' },
      { value: 'compatibility-profiles', label: 'Compatibility Profiles' },
    ],
    summary: [
      'Semantic Catalog now provides a shared shell for typed semantic objects, typed bindings, and compatibility profiles.',
      'Typed Bindings and Compatibility Profiles remain first-class subtabs instead of helper-only areas.',
      'T6 standardizes list/detail/form/publish primitives here so T7 can focus on object-specific fields and workflows.',
    ],
    routeFocus: ['tab', 'subtab', 'object_id'],
    uiLinks: [],
  },
  'analysis-ops': {
    label: 'Analysis Ops',
    subtitle: 'Session operations shell for operator actions and jumps into canonical read surfaces.',
    objectLabel: 'session_id',
    subtabConfig: [],
    summary: [
      'This page is reserved for session list, filters, and terminate controls.',
      'Canonical session reading stays in /ui instead of being reimplemented in /admin.',
      'The route model already supports session_id-based deep links.',
    ],
    routeFocus: ['tab', 'session_id'],
    uiLinks: [
      { label: 'Open Sessions in /ui', route: { tab: 'sessions' } },
      { label: 'Open State in /ui', route: { tab: 'state' } },
      { label: 'Open Runtime in /ui', route: { tab: 'runtime' } },
      { label: 'Open Jobs in /ui', route: { tab: 'jobs' } },
    ],
  },
  'runtime-jobs': {
    label: 'Runtime & Jobs',
    subtitle: 'Read-only operator shell for runtime truth and job troubleshooting.',
    objectLabel: 'job_id',
    subtabConfig: [
      { value: 'session-runtime', label: 'Session Runtime' },
      { value: 'proposition-runtime', label: 'Proposition Runtime' },
      { value: 'artifact-runtime', label: 'Artifact Runtime' },
      { value: 'jobs', label: 'Jobs' },
    ],
    summary: [
      'This shell is explicitly for runtime truth, not canonical result reading.',
      'The page reserves subtab routing for runtime scopes and jobs before data widgets land.',
      'Future implementation will map runtime and job IDs into structured detail panes.',
    ],
    routeFocus: ['tab', 'subtab', 'session_id', 'proposition_id', 'artifact_id', 'job_id'],
    uiLinks: [
      { label: 'Open Runtime in /ui', route: { tab: 'runtime' } },
      { label: 'Open Jobs in /ui', route: { tab: 'jobs' } },
      { label: 'Open State in /ui', route: { tab: 'state' } },
    ],
  },
  governance: {
    label: 'Governance',
    subtitle: 'Shell for policy, quality, approval, and helper workflows.',
    objectLabel: 'request_id',
    subtabConfig: [
      { value: 'policies', label: 'Policies' },
      { value: 'quality-rules', label: 'Quality Rules' },
      { value: 'approvals', label: 'Approvals' },
      { value: 'governance-helpers', label: 'Governance Helpers' },
    ],
    summary: [
      'T1 preserves a single governance entrypoint while moving old approvals navigation into subtabs.',
      'The global approvals badge remains attached to the governance nav item.',
      'Governance Helpers stay explicitly auxiliary instead of becoming the default task flow.',
    ],
    routeFocus: ['tab', 'subtab', 'session_id', 'policy_id', 'rule_id', 'request_id'],
    uiLinks: [],
  },
  observability: {
    label: 'Observability',
    subtitle: 'Read-only shell for health and metrics summaries.',
    objectLabel: '',
    subtabConfig: [],
    summary: [
      'This page remains intentionally read-only and operational.',
      'Auto-refresh, JSON panes, and degraded metric rendering will be layered in later tasks.',
      'T1 only establishes the page shell and route stability.',
    ],
    routeFocus: ['tab'],
    uiLinks: [],
  },
};

export function createAdminShell(ctx) {
  const { shared, modules } = ctx;
  const {
    esc,
    renderDetailList,
    renderJsonPanel,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    buildMarivoUiUrl,
    buildUiSessionsUrl,
    adminUiDeepLinks,
    initSidebar,
    setActiveTab,
  } = shared;

  let currentAdminRoute = { ...DEFAULT_ADMIN_ROUTE };
  let suppressAdminTabSync = false;

  function normalizeAdminRoute(nextRoute) {
    const normalized = {
      tab: ADMIN_TABS.includes(nextRoute.tab) ? nextRoute.tab : DEFAULT_ADMIN_ROUTE.tab,
      subtab: String(nextRoute.subtab || '').trim(),
      sourceId: String(nextRoute.sourceId || '').trim(),
      engineId: String(nextRoute.engineId || '').trim(),
      mappingId: String(nextRoute.mappingId || '').trim(),
      objectId: String(nextRoute.objectId || '').trim(),
      sessionId: String(nextRoute.sessionId || '').trim(),
      policyId: String(nextRoute.policyId || '').trim(),
      ruleId: String(nextRoute.ruleId || '').trim(),
      requestId: String(nextRoute.requestId || '').trim(),
      propositionId: String(nextRoute.propositionId || '').trim(),
      artifactId: String(nextRoute.artifactId || '').trim(),
      jobId: String(nextRoute.jobId || '').trim(),
    };

    const tabMeta = ADMIN_TAB_META[normalized.tab];
    const validSubtabs = tabMeta.subtabConfig.map((item) => item.value);
    if (!validSubtabs.includes(normalized.subtab)) {
      normalized.subtab = validSubtabs[0] || '';
    }

    if (normalized.tab !== 'data-sources') normalized.sourceId = '';
    if (normalized.tab !== 'execution-engines') normalized.engineId = '';
    if (normalized.tab !== 'execution-engines') normalized.mappingId = '';
    if (normalized.tab !== 'semantic-catalog') normalized.objectId = '';
    if (!['analysis-ops', 'runtime-jobs', 'governance'].includes(normalized.tab)) normalized.sessionId = '';
    if (normalized.tab !== 'governance') normalized.policyId = '';
    if (normalized.tab !== 'governance') normalized.ruleId = '';
    if (normalized.tab !== 'governance') normalized.requestId = '';
    if (normalized.tab !== 'runtime-jobs') normalized.propositionId = '';
    if (normalized.tab !== 'runtime-jobs') normalized.artifactId = '';
    if (normalized.tab !== 'runtime-jobs') normalized.jobId = '';

    if (normalized.tab === 'governance') {
      if (normalized.subtab === 'policies') {
        normalized.ruleId = '';
        normalized.requestId = '';
      } else if (normalized.subtab === 'quality-rules') {
        normalized.policyId = '';
        normalized.requestId = '';
      } else if (normalized.subtab === 'approvals') {
        normalized.policyId = '';
        normalized.ruleId = '';
      } else {
        normalized.policyId = '';
        normalized.ruleId = '';
        normalized.requestId = '';
      }
    }

    if (normalized.tab === 'runtime-jobs') {
      if (normalized.subtab === 'session-runtime') {
        normalized.propositionId = '';
        normalized.artifactId = '';
        normalized.jobId = '';
      } else if (normalized.subtab === 'proposition-runtime') {
        normalized.artifactId = '';
        normalized.jobId = '';
      } else if (normalized.subtab === 'artifact-runtime') {
        normalized.propositionId = '';
        normalized.jobId = '';
      } else if (normalized.subtab === 'jobs') {
        normalized.propositionId = '';
        normalized.artifactId = '';
      }
    }

    return normalized;
  }

  function adminRouteFromLocation() {
    const params = new URLSearchParams(window.location.search);
    return normalizeAdminRoute({
      tab: params.get('tab') || DEFAULT_ADMIN_ROUTE.tab,
      subtab: params.get('subtab') || '',
      sourceId: params.get('source_id') || '',
      engineId: params.get('engine_id') || '',
      mappingId: params.get('mapping_id') || '',
      objectId: params.get('object_id') || '',
      sessionId: params.get('session_id') || '',
      policyId: params.get('policy_id') || '',
      ruleId: params.get('rule_id') || '',
      requestId: params.get('request_id') || '',
      propositionId: params.get('proposition_id') || '',
      artifactId: params.get('artifact_id') || '',
      jobId: params.get('job_id') || '',
    });
  }

  function writeAdminRoute(route, historyMode = 'push') {
    const params = new URLSearchParams();
    if (route.tab && route.tab !== DEFAULT_ADMIN_ROUTE.tab) params.set('tab', route.tab);
    if (route.subtab) params.set('subtab', route.subtab);
    if (route.sourceId) params.set('source_id', route.sourceId);
    if (route.engineId) params.set('engine_id', route.engineId);
    if (route.mappingId) params.set('mapping_id', route.mappingId);
    if (route.objectId) params.set('object_id', route.objectId);
    if (route.sessionId) params.set('session_id', route.sessionId);
    if (route.policyId) params.set('policy_id', route.policyId);
    if (route.ruleId) params.set('rule_id', route.ruleId);
    if (route.requestId) params.set('request_id', route.requestId);
    if (route.propositionId) params.set('proposition_id', route.propositionId);
    if (route.artifactId) params.set('artifact_id', route.artifactId);
    if (route.jobId) params.set('job_id', route.jobId);
    const nextUrl = params.toString() ? `?${params.toString()}` : window.location.pathname;
    const method = historyMode === 'replace' ? 'replaceState' : 'pushState';
    window.history[method](null, '', nextUrl);
  }

  function adminObjectLocator(route) {
    if (route.tab === 'data-sources') return route.sourceId;
    if (route.tab === 'execution-engines') return route.engineId || route.mappingId;
    if (route.tab === 'semantic-catalog') return route.objectId;
    if (route.tab === 'analysis-ops') return route.sessionId;
    if (route.tab === 'runtime-jobs') {
      if (route.subtab === 'jobs') return route.jobId || route.sessionId;
      if (route.subtab === 'artifact-runtime') return route.artifactId || route.sessionId;
      if (route.subtab === 'proposition-runtime') return route.propositionId || route.sessionId;
      return route.sessionId;
    }
    if (route.tab === 'governance') return route.requestId || route.policyId || route.ruleId || route.sessionId;
    return '';
  }

  function updateAdminBreadcrumb(route) {
    const breadcrumb = document.querySelector('.breadcrumb');
    if (!breadcrumb) return;
    const tabMeta = ADMIN_TAB_META[route.tab];
    const segments = ['Admin', tabMeta.label];
    if (route.subtab) {
      const subtabMeta = tabMeta.subtabConfig.find((item) => item.value === route.subtab);
      if (subtabMeta) segments.push(subtabMeta.label);
    }
    const locator = adminObjectLocator(route);
    if (locator) segments.push(locator);
    breadcrumb.innerHTML = segments.map((segment, index) => {
      if (index === segments.length - 1) {
        return `<span class="current">${esc(segment)}</span>`;
      }
      return `<span>${esc(segment)}</span><span class="sep">/</span>`;
    }).join('');
  }

  function renderAdminSubnav(route, tabMeta) {
    if (!tabMeta.subtabConfig.length) return '';
    return `
      <div class="admin-subnav">
        ${tabMeta.subtabConfig.map((item) => `
          <button
            class="btn btn-sm admin-subnav-btn ${route.subtab === item.value ? 'is-active' : ''}"
            type="button"
            data-subtab="${item.value}"
          >${esc(item.label)}</button>
        `).join('')}
      </div>
    `;
  }

  function renderRouteSummary(route, tabMeta) {
    const locator = adminObjectLocator(route);
    const deepLinks = adminUiDeepLinks({
      sessionId: route.sessionId,
      propositionId: route.propositionId,
      artifactId: route.artifactId,
      runtimeScope: route.runtimeScope,
      status: route.status,
      sessionQuery: route.sessionQuery,
    });
    const linkMap = {
      'Open Sessions in /ui': deepLinks.sessions,
      'Open State in /ui': deepLinks.state,
      'Open Runtime in /ui': deepLinks.runtime,
      'Open Jobs in /ui': deepLinks.jobs,
    };
    const uiLinks = tabMeta.uiLinks.map((link) => {
      const href = linkMap[link.label] || buildMarivoUiUrl(link.route || {});
      return `<a class="btn btn-sm" href="${esc(href)}">${esc(link.label)}</a>`;
    }).join('');

    return renderAdminDetailCard({
      title: 'Shared Route Summary',
      statusHtml: '<span class="shell-chip">shareable-link</span>',
      note: 'Shared deep-link and error helpers are centralized in shared.js so later admin pages do not inline query-string assembly.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'tab', value: route.tab },
          { label: 'subtab', value: route.subtab || '-' },
          { label: 'source_id', value: route.sourceId || '-' },
          { label: 'engine_id', value: route.engineId || '-' },
          { label: 'mapping_id', value: route.mappingId || '-' },
          { label: 'object_id', value: route.objectId || '-' },
          { label: 'session_id', value: route.sessionId || '-' },
          { label: 'policy_id', value: route.policyId || '-' },
          { label: 'rule_id', value: route.ruleId || '-' },
          { label: 'request_id', value: route.requestId || '-' },
          { label: 'proposition_id', value: route.propositionId || '-' },
          { label: 'artifact_id', value: route.artifactId || '-' },
          { label: 'job_id', value: route.jobId || '-' },
        ])}
        <div class="detail-actions">
          ${uiLinks || '<span class="shell-chip">No /ui deep links for this page yet.</span>'}
        </div>
        ${renderJsonPanel(
          'Route JSON',
          {
            tab: route.tab,
            subtab: route.subtab || null,
            source_id: route.sourceId || null,
            engine_id: route.engineId || null,
            mapping_id: route.mappingId || null,
            object_id: route.objectId || null,
            session_id: route.sessionId || null,
            policy_id: route.policyId || null,
            rule_id: route.ruleId || null,
            request_id: route.requestId || null,
            proposition_id: route.propositionId || null,
            artifact_id: route.artifactId || null,
            job_id: route.jobId || null,
            locator: locator || null,
            deep_links: deepLinks,
          },
          'No route state.'
        )}
      `,
    });
  }

  async function updateApprovalsBadge() {
    try {
      const approvals = await ctx.adminApi.listApprovals({ status: 'pending' });
      const badge = document.querySelector('#approvals-badge');
      if (!badge) return;
      if (Array.isArray(approvals) && approvals.length > 0) {
        badge.textContent = String(approvals.length);
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    } catch {
      const badge = document.querySelector('#approvals-badge');
      if (badge) badge.style.display = 'none';
    }
  }

  function renderAdminPanel(route) {
    const panel = document.getElementById(`panel-${route.tab}`);
    if (!panel) return;
    const tabMeta = ADMIN_TAB_META[route.tab];
    const routeFocus = tabMeta.routeFocus.map((item) => `<span class="shell-chip"><strong>${esc(item)}</strong></span>`).join('');
    const isOverview = route.tab === 'overview';
    const isDataSources = route.tab === 'data-sources';
    const isExecutionEngines = route.tab === 'execution-engines';
    const isSemanticCatalog = route.tab === 'semantic-catalog';
    const isAnalysisOps = route.tab === 'analysis-ops';
    const isRuntimeJobs = route.tab === 'runtime-jobs';
    const isGovernance = route.tab === 'governance';
    const isObservability = route.tab === 'observability';
    const mainContent = isOverview
      ? modules.overview.render(route)
      : isDataSources
        ? modules.dataSources.render(route)
        : isExecutionEngines
          ? modules.executionEngines.render(route)
          : isSemanticCatalog
          ? modules.semanticCatalog.render(route)
          : isAnalysisOps
            ? modules.analysisOps.render(route)
            : isRuntimeJobs
              ? modules.runtimeJobs.render(route)
              : isGovernance
                ? modules.governance.render(route)
                : isObservability
                  ? modules.observability.render(route)
            : renderAdminListDetailLayout({
                primaryHtml: `
                  <div class="admin-shell-card">
                    <h3>Page Scope</h3>
                    <ul>
                      ${tabMeta.summary.map((item) => `<li>${esc(item)}</li>`).join('')}
                    </ul>
                  </div>
                  <div class="admin-shell-card">
                    <h3>Implementation Boundary</h3>
                    <p>T2 provides shared list/detail, JSON, error, and danger-confirm primitives. Later tasks attach concrete HTTP-backed workflows here without reintroducing page-local scaffolding.</p>
                  </div>
                `,
                secondaryHtml: renderAdminTableCard({
                  title: 'Upcoming Page Integration',
                  count: tabMeta.routeFocus.length,
                  countLabel: 'route signal(s)',
                  note: 'Later tasks will plug this page into real list queries and detail panes without re-implementing table shells or `/ui` deep links.',
                  columns: ['Signal', 'Why it matters'],
                  rowsHtml: tabMeta.routeFocus.map((item) => `
                    <tr>
                      <td><span class="inline-code">${esc(item)}</span></td>
                      <td class="is-quiet">Shared renderers keep ${esc(item)} in the URL contract and out of ad-hoc client state.</td>
                    </tr>
                  `).join(''),
                }),
                detailHtml: renderRouteSummary(route, tabMeta),
              });

    panel.innerHTML = `
      <div class="admin-shell-header">
        <div class="admin-shell-copy">
          <h1>${esc(tabMeta.label)}</h1>
          <p>${esc(tabMeta.subtitle)}</p>
          <div class="shell-chip-group">
            ${routeFocus}
            <span class="shell-chip"><strong>refresh-safe</strong></span>
            <span class="shell-chip"><strong>shareable-link</strong></span>
          </div>
        </div>
        <div class="admin-shell-meta">
          <a class="btn btn-sm" href="/admin">Reset to Overview</a>
          <a class="btn btn-sm" href="${esc(buildUiSessionsUrl(route.sessionId || ''))}">Open /ui</a>
        </div>
      </div>
      ${renderAdminSubnav(route, tabMeta)}
      ${mainContent}
    `;

    if (isOverview) void modules.overview.hydrate(panel, route);
    if (isDataSources) void modules.dataSources.hydrate(panel, route);
    if (isExecutionEngines) void modules.executionEngines.hydrate(panel, route);
    if (isSemanticCatalog) void modules.semanticCatalog.hydrate(panel, route);
    if (isAnalysisOps) void modules.analysisOps.hydrate(panel, route);
    if (isRuntimeJobs) void modules.runtimeJobs.hydrate(panel, route);
    if (isGovernance) void modules.governance.hydrate(panel, route);
    if (isObservability) void modules.observability.hydrate(panel, route);

    panel.querySelectorAll('[data-subtab]').forEach((button) => {
      button.addEventListener('click', () => {
        applyAdminRoute({ ...currentAdminRoute, tab: route.tab, subtab: button.dataset.subtab || '' });
      });
    });
  }

  function applyAdminRoute(nextRoute, historyMode = 'push') {
    currentAdminRoute = normalizeAdminRoute(nextRoute);
    writeAdminRoute(currentAdminRoute, historyMode);
    updateAdminBreadcrumb(currentAdminRoute);
    suppressAdminTabSync = true;
    setActiveTab(currentAdminRoute.tab);
    suppressAdminTabSync = false;
    renderAdminPanel(currentAdminRoute);
  }

  ctx.getCurrentRoute = () => currentAdminRoute;
  ctx.applyAdminRoute = applyAdminRoute;
  ctx.renderCurrentRoute = () => renderAdminPanel(currentAdminRoute);

  function start() {
    initSidebar();
    currentAdminRoute = adminRouteFromLocation();
    window.addEventListener('tab-change', (event) => {
      if (suppressAdminTabSync) return;
      applyAdminRoute({ ...currentAdminRoute, tab: event.detail?.tab || DEFAULT_ADMIN_ROUTE.tab });
    });
    window.addEventListener('popstate', () => {
      currentAdminRoute = adminRouteFromLocation();
      updateAdminBreadcrumb(currentAdminRoute);
      suppressAdminTabSync = true;
      setActiveTab(currentAdminRoute.tab);
      suppressAdminTabSync = false;
      renderAdminPanel(currentAdminRoute);
    });
    updateAdminBreadcrumb(currentAdminRoute);
    suppressAdminTabSync = true;
    setActiveTab(currentAdminRoute.tab);
    suppressAdminTabSync = false;
    renderAdminPanel(currentAdminRoute);
    void updateApprovalsBadge();
  }

  return {
    start,
    normalizeAdminRoute,
    adminRouteFromLocation,
    writeAdminRoute,
    applyAdminRoute,
    renderAdminPanel,
    updateApprovalsBadge,
  };
}
