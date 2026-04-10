export function createAnalysisOpsModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    toast,
    renderEmptyState,
    renderLoadingState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    buildUiSessionsUrl,
    buildFactumUiUrl,
    buildUiStateUrl,
    buildUiRuntimeUrl,
    buildUiJobsUrl,
    openDangerConfirm,
    statusBadge,
    fmtDate,
  } = shared;

  let analysisOpsRenderVersion = 0;
  const analysisOpsUiState = {
    filters: {
      status: '',
      sessionQuery: '',
    },
    terminateErrors: {},
  };

  function extractItems(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
  }

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : '-';
  }

  function getGoalQuestion(session) {
    return session?.goal?.question || '-';
  }

  function getLifecycle(session) {
    return session?.lifecycle || {};
  }

  function getScope(session) {
    return session?.scope || {};
  }

  function getGovernance(session) {
    return session?.governance || {};
  }

  function currentFilters() {
    return {
      status: analysisOpsUiState.filters.status || '',
      sessionQuery: analysisOpsUiState.filters.sessionQuery || '',
    };
  }

  function setFilters(nextFilters) {
    analysisOpsUiState.filters = {
      status: String(nextFilters?.status || '').trim(),
      sessionQuery: String(nextFilters?.sessionQuery || '').trim(),
    };
  }

  function buildSessionListRows(sessions, selectedSessionId, filters) {
    if (!sessions.length) {
      const emptyCopy = filters.status || filters.sessionQuery
        ? 'No sessions match the current filters.'
        : 'No analysis sessions available yet.';
      return `
        <tr>
          <td colspan="5">${renderEmptyState(emptyCopy)}</td>
        </tr>
      `;
    }
    return sessions.map((session) => `
      <tr class="${session.session_id === selectedSessionId ? 'is-selected' : ''}">
        <td>
          <button
            type="button"
            class="selectable-list-item ${session.session_id === selectedSessionId ? 'is-active' : ''}"
            data-action="select-session"
            data-session-id="${esc(session.session_id)}"
          >
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(session.session_id)}</span>
              <span class="selectable-list-meta">${esc(getGoalQuestion(session))}</span>
            </span>
            ${statusBadge(getLifecycle(session).status)}
          </button>
        </td>
        <td>${esc(getGoalQuestion(session))}</td>
        <td>${statusBadge(getLifecycle(session).status)}</td>
        <td>${esc(formatMaybeDate(session.created_at))}</td>
        <td>${esc(formatMaybeDate(session.updated_at))}</td>
      </tr>
    `).join('');
  }

  function renderFilters(viewModel) {
    const filters = viewModel.filters || currentFilters();
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Session Filters</h2>
            <div class="results-count">Filter session operations by lifecycle status or Search by session_id.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-session-filters">Reset</button>
          </div>
        </div>
        <p class="panel-note">GET /sessions supports status filtering and session_id prefix search. T8 intentionally does not add page_token controls.</p>
        <form class="filters-grid" data-role="session-filters">
          <label>
            <span>Status</span>
            <select name="status">
              <option value="" ${filters.status === '' ? 'selected' : ''}>All statuses</option>
              <option value="open" ${filters.status === 'open' ? 'selected' : ''}>Open</option>
              <option value="closed" ${filters.status === 'closed' ? 'selected' : ''}>Closed</option>
            </select>
          </label>
          <label>
            <span>Search by session_id</span>
            <input type="search" name="session_query" value="${esc(filters.sessionQuery)}" placeholder="sess_..." />
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Apply Filters</button>
          </div>
        </form>
      </div>
    `;
  }

  function renderSessionListCard(viewModel) {
    const filters = viewModel.filters || currentFilters();
    return renderAdminTableCard({
      title: 'Session Inventory',
      count: viewModel.sessions.length,
      countLabel: 'session(s)',
      note: 'GET /sessions returns canonical analysis_session.v1 items. /admin lists session operations only and does not expose create-session, intent, step, or plan-management controls.',
      columns: ['session_id', 'goal', 'status', 'created_at', 'updated_at'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn" data-action="refresh-analysis-ops">Refresh</button>
          <a class="btn btn-sm" href="${esc(buildUiSessionsUrl('', filters.status, filters.sessionQuery))}">Open in /ui Sessions</a>
        </div>
      `,
      rowsHtml: buildSessionListRows(viewModel.sessions, viewModel.selectedSessionId, filters),
      errorHtml: viewModel.listError
        ? renderStructuredError(viewModel.listError, 'Analysis Ops unavailable.')
        : '',
    });
  }

  function renderSessionSummaryCard(session, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Session Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /sessions/{session_id} is the canonical session root detail endpoint.',
        bodyHtml: `
          ${detailError.status === 404 ? '<p class="panel-note">404 session not found.</p>' : ''}
          ${renderStructuredError(detailError, 'Session detail unavailable.')}
        `,
      });
    }
    if (!session) {
      return renderAdminDetailCard({
        title: 'Session Summary',
        statusHtml: '<span class="shell-chip">no session selected</span>',
        note: 'Select a session from Session Inventory to inspect goal, governance, and lifecycle metadata.',
        bodyHtml: renderEmptyState('Select a session to inspect goal, constraints, budget, policy, terminal reason, and rollover lineage.'),
      });
    }

    const lifecycle = getLifecycle(session);
    const scope = getScope(session);
    const governance = getGovernance(session);
    return renderAdminDetailCard({
      title: 'Session Summary',
      statusHtml: statusBadge(lifecycle.status),
      note: 'Canonical deep reads stay in /ui. /admin only summarizes the session root and operator-safe controls.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'session_id', value: session.session_id },
          { label: 'goal', value: getGoalQuestion(session) },
          { label: 'status', valueHtml: statusBadge(lifecycle.status) },
          { label: 'created_at', value: formatMaybeDate(session.created_at) },
          { label: 'updated_at', value: formatMaybeDate(session.updated_at) },
          { label: 'terminal_reason', value: lifecycle.terminal_reason || '-' },
          { label: 'rollover_from_session_id', value: lifecycle.rollover_from_session_id || '-' },
        ])}
        ${renderJsonPanel('Constraints JSON', scope.constraints, 'No constraints configured.')}
        ${renderJsonPanel('Budget JSON', governance.budget, 'No budget configured.')}
        ${renderJsonPanel('Policy JSON', governance.policy, 'No policy configured.')}
      `,
    });
  }

  function renderSessionOpsCard(session, terminateError) {
    if (!session) {
      return renderAdminDetailCard({
        title: 'Session Operations',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Terminate Session is the only session write action exposed in /admin.',
        bodyHtml: renderEmptyState('No session selected.'),
      });
    }

    const lifecycle = getLifecycle(session);
    const isOpen = lifecycle.status === 'open';
    return renderAdminDetailCard({
      title: 'Session Operations',
      statusHtml: isOpen ? '<span class="shell-chip">operator action</span>' : '<span class="shell-chip">read only</span>',
      note: 'Terminate Session is the only suggested session mutation here. Canonical State, Runtime, and Jobs remain in /ui.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'allowed write action', value: 'Terminate Session' },
          { label: 'intent writes after terminate', value: 'Blocked' },
          { label: 'reversible', value: 'No' },
          { label: 'runtime drill-ins', value: 'View State in /ui, View Runtime in /ui, View Jobs in /ui' },
        ])}
        <div class="detail-actions">
          ${isOpen ? '<button type="button" class="btn btn-danger" data-action="terminate-session">Terminate Session</button>' : '<span class="shell-chip">Session already closed</span>'}
          <a class="btn" href="${esc(buildFactumUiUrl({ tab: 'sessions', sessionId: session.session_id }))}">Open in /ui Sessions</a>
          <a class="btn" href="${esc(buildUiStateUrl(session.session_id))}">View State in /ui</a>
          <a class="btn" href="${esc(buildUiRuntimeUrl(session.session_id))}">View Runtime in /ui</a>
          <a class="btn" href="${esc(buildUiJobsUrl(session.session_id))}">View Jobs in /ui</a>
        </div>
        <p class="panel-note">Terminate Session must warn that 终止后将阻止新的 intent 写入.</p>
        ${terminateError ? renderStructuredError(terminateError, 'Terminate Session failed.') : ''}
      `,
    });
  }

  function renderBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderFilters(viewModel)}
        ${renderSessionListCard(viewModel)}
      `,
      secondaryHtml: '',
      detailHtml: `
        ${renderSessionSummaryCard(viewModel.selectedSession, viewModel.detailError)}
        ${renderSessionOpsCard(viewModel.selectedSession, viewModel.terminateError)}
      `,
    });
  }

  function render() {
    return `
      <section data-role="analysis-ops-body">
        ${renderLoadingState('Loading Session Inventory...')}
      </section>
    `;
  }

  function refreshCurrentAnalysisOps() {
    const panel = document.getElementById('panel-analysis-ops');
    const route = ctx.getCurrentRoute();
    if (panel && route.tab === 'analysis-ops') {
      void hydrate(panel, route);
    }
  }

  function handleTerminateSession(session) {
    openDangerConfirm({
      title: 'Terminate Session',
      objectLabel: session.session_id,
      impactScope: 'Closes the selected analysis session and blocks new intent writes.',
      reversible: 'No',
      confirmLabel: 'Terminate Session',
      detailsHtml: renderDetailList([
        { label: 'goal', value: getGoalQuestion(session) },
        { label: 'status', value: getLifecycle(session).status || '-' },
        { label: 'warning', value: '终止后将阻止新的 intent 写入' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.terminateSession(session.session_id, {});
          delete analysisOpsUiState.terminateErrors[session.session_id];
          toast('Session terminated.', 'success');
          refreshCurrentAnalysisOps();
        } catch (error) {
          analysisOpsUiState.terminateErrors[session.session_id] = normalizeApiError(
            error,
            'Terminate Session failed.'
          );
          toast(analysisOpsUiState.terminateErrors[session.session_id].message, 'error');
          refreshCurrentAnalysisOps();
        }
      },
    });
  }

  async function hydrate(panel, route) {
    const renderVersion = ++analysisOpsRenderVersion;
    const filters = currentFilters();
    const safeRender = (viewModel) => {
      if (renderVersion !== analysisOpsRenderVersion) return;
      const target = panel.querySelector('[data-role="analysis-ops-body"]');
      if (target) {
        target.innerHTML = renderBody(viewModel);
        bindEvents(panel, viewModel);
      }
    };

    safeRender({
      filters,
      sessions: [],
      selectedSessionId: route.sessionId || '',
      selectedSession: null,
      listError: null,
      detailError: null,
      terminateError: route.sessionId ? analysisOpsUiState.terminateErrors[route.sessionId] || null : null,
    });

    try {
      const payload = await ctx.adminApi.listSessions({
        status: filters.status,
        sessionId: filters.sessionQuery,
      });
      const sessions = extractItems(payload);
      const selectedSessionId = route.sessionId || sessions[0]?.session_id || '';

      if (!route.sessionId && selectedSessionId) {
        ctx.applyAdminRoute({ ...route, sessionId: selectedSessionId }, 'replace');
        return;
      }

      let selectedSession = null;
      let detailError = null;
      if (selectedSessionId) {
        try {
          selectedSession = await ctx.adminApi.getSession(selectedSessionId);
        } catch (error) {
          detailError = normalizeApiError(error, 'Session detail unavailable.');
        }
      }

      safeRender({
        filters,
        sessions,
        selectedSessionId,
        selectedSession,
        listError: null,
        detailError,
        terminateError: selectedSessionId
          ? analysisOpsUiState.terminateErrors[selectedSessionId] || null
          : null,
      });
    } catch (error) {
      safeRender({
        filters,
        sessions: [],
        selectedSessionId: route.sessionId || '',
        selectedSession: null,
        listError: normalizeApiError(error, 'Analysis Ops unavailable.'),
        detailError: null,
        terminateError: route.sessionId ? analysisOpsUiState.terminateErrors[route.sessionId] || null : null,
      });
    }
  }

  function bindEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="refresh-analysis-ops"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentAnalysisOps());
    });
    panel.querySelectorAll('[data-action="select-session"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'analysis-ops', sessionId: button.dataset.sessionId || '' },
          'push'
        );
      });
    });

    const filterForm = panel.querySelector('[data-role="session-filters"]');
    if (filterForm) {
      filterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(filterForm);
        setFilters({
          status: String(formData.get('status') || ''),
          sessionQuery: String(formData.get('session_query') || ''),
        });
        const currentRoute = ctx.getCurrentRoute();
        ctx.applyAdminRoute({ ...currentRoute, tab: 'analysis-ops', sessionId: '' }, 'replace');
      });
    }

    panel.querySelectorAll('[data-action="clear-session-filters"]').forEach((button) => {
      button.addEventListener('click', () => {
        setFilters({ status: '', sessionQuery: '' });
        const currentRoute = ctx.getCurrentRoute();
        ctx.applyAdminRoute({ ...currentRoute, tab: 'analysis-ops', sessionId: '' }, 'replace');
      });
    });

    panel.querySelectorAll('[data-action="terminate-session"]').forEach((button) => {
      button.addEventListener('click', () => {
        if (viewModel.selectedSession) {
          handleTerminateSession(viewModel.selectedSession);
        }
      });
    });
  }

  return { render, hydrate };
}
