export function createRuntimeJobsModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    renderEmptyState,
    renderLoadingState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    buildUiStateUrl,
    buildUiContextUrl,
    buildUiRuntimeUrl,
    buildUiJobsUrl,
    buildUiSessionsUrl,
    statusBadge,
    fmtDate,
  } = shared;

  let runtimeJobsRenderVersion = 0;
  const runtimeJobsUiState = {
    jobFilters: {
      sessionId: '',
      status: '',
    },
  };

  function currentJobFilters(route) {
    return {
      sessionId: runtimeJobsUiState.jobFilters.sessionId || route.sessionId || '',
      status: runtimeJobsUiState.jobFilters.status || '',
    };
  }

  function setJobFilters(nextFilters) {
    runtimeJobsUiState.jobFilters = {
      sessionId: String(nextFilters?.sessionId || '').trim(),
      status: String(nextFilters?.status || '').trim(),
    };
  }

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : '-';
  }

  function renderStructuredValue(value, emptyCopy = 'None') {
    if (value == null || value === '') {
      return `<span class="empty">${esc(emptyCopy)}</span>`;
    }
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return `<span>${esc(String(value))}</span>`;
    }
    return renderJsonPanel('Structured Value', value, emptyCopy);
  }

  function buildRuntimeSummaryCards(items) {
    return `
      <div class="runtime-meta-grid">
        ${items.map(([label, value, type]) => `
          <div class="runtime-summary-card">
            <h4>${esc(label)}</h4>
            ${
              type === 'status'
                ? statusBadge(value || 'unknown')
                : (value == null || value === '' ? '<span class="empty">-</span>' : `<span>${esc(String(value))}</span>`)
            }
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderRuntimeBoundaryCard(copy, scopeLabel) {
    return renderAdminDetailCard({
      title: 'Runtime Boundary',
      statusHtml: '<span class="shell-chip">runtime truth</span>',
      note: 'This page is operator-facing runtime truth, not canonical result.',
      bodyHtml: `
        <p class="panel-note">${esc(copy)}</p>
        <p class="panel-note">Use /ui State and /ui Context when you need externally visible canonical truth.</p>
        <p class="panel-note">No retry, replay, submit, cancel, or publish controls exist on this page.</p>
        <div class="detail-actions">
          ${scopeLabel}
        </div>
      `,
    });
  }

  function renderRuntimeLinks(route, selectedSubtab) {
    const sessionId = route.sessionId || '';
    const propositionId = route.propositionId || '';
    const artifactId = route.artifactId || '';
    return `
      <div class="detail-actions">
        ${sessionId ? `<a class="btn btn-sm" href="${esc(buildUiRuntimeUrl(sessionId, propositionId, artifactId, selectedSubtab === 'session-runtime' ? 'session' : selectedSubtab === 'proposition-runtime' ? 'proposition' : 'artifact'))}">Open in /ui Runtime</a>` : ''}
        ${sessionId ? `<a class="btn btn-sm" href="${esc(buildUiSessionsUrl(sessionId))}">Open linked session in /ui</a>` : ''}
        ${sessionId ? `<a class="btn btn-sm" href="${esc(buildUiStateUrl(sessionId, propositionId))}">Open State in /ui</a>` : ''}
        ${sessionId && propositionId ? `<a class="btn btn-sm" href="${esc(buildUiContextUrl(sessionId, propositionId))}">Open Context in /ui</a>` : ''}
        ${sessionId ? `<a class="btn btn-sm" href="${esc(buildUiJobsUrl(sessionId))}">Open Jobs in /ui</a>` : ''}
      </div>
    `;
  }

  function renderRuntimeQueryCard(route) {
    const selectedSubtab = route.subtab || 'session-runtime';
    const resourceType = selectedSubtab === 'jobs'
      ? 'jobs'
      : selectedSubtab === 'artifact-runtime'
        ? 'artifact'
        : selectedSubtab === 'proposition-runtime'
          ? 'proposition'
          : 'session';
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Runtime Query</h2>
            <div class="results-count">resource type, session_id, proposition_id, and artifact_id drive the runtime locator.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-runtime-query">Reset</button>
          </div>
        </div>
        <p class="panel-note">GET /sessions/{session_id}/runtime-status, GET /sessions/{session_id}/propositions/{proposition_id}/runtime-status, and GET /sessions/{session_id}/artifacts/{artifact_id}/runtime-status are read-only operator surfaces.</p>
        <form class="filters-grid" data-role="runtime-query-form">
          <label>
            <span>resource type</span>
            <select name="resource_type">
              <option value="session" ${resourceType === 'session' ? 'selected' : ''}>Session Runtime</option>
              <option value="proposition" ${resourceType === 'proposition' ? 'selected' : ''}>Proposition Runtime</option>
              <option value="artifact" ${resourceType === 'artifact' ? 'selected' : ''}>Artifact Runtime</option>
              <option value="jobs" ${resourceType === 'jobs' ? 'selected' : ''}>Jobs</option>
            </select>
          </label>
          <label>
            <span>session_id</span>
            <input type="search" name="session_id" value="${esc(route.sessionId || '')}" placeholder="sess_..." />
          </label>
          <label>
            <span>proposition_id</span>
            <input type="search" name="proposition_id" value="${esc(route.propositionId || '')}" placeholder="prop_..." />
          </label>
          <label>
            <span>artifact_id</span>
            <input type="search" name="artifact_id" value="${esc(route.artifactId || '')}" placeholder="art_..." />
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Load Runtime</button>
          </div>
        </form>
      </div>
    `;
  }

  function renderRuntimeResult(route, viewModel) {
    const scope = route.subtab || 'session-runtime';
    if (viewModel.loading) {
      return renderAdminDetailCard({
        title: 'Runtime Status',
        statusHtml: '<span class="shell-chip">loading</span>',
        note: 'Loading operator-facing runtime truth.',
        bodyHtml: renderLoadingState('Loading runtime status...'),
      });
    }
    if (viewModel.error) {
      return renderAdminDetailCard({
        title: 'Runtime Status',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'Runtime lookup failures stay on this page.',
        bodyHtml: `
          ${viewModel.error.status === 404 ? '<p class="panel-note">404 runtime target not found.</p>' : ''}
          ${renderStructuredError(viewModel.error, 'Runtime status unavailable.')}
        `,
      });
    }
    if (!viewModel.result) {
      return renderAdminDetailCard({
        title: 'Runtime Status',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Choose a runtime locator and load the selected operator surface.',
        bodyHtml: renderEmptyState('No runtime status loaded yet.'),
      });
    }

    const result = viewModel.result;
    let summaryHtml = '';
    let detailHtml = '';
    let title = 'Session Runtime';
    if (scope === 'session-runtime') {
      title = 'Session Runtime';
      summaryHtml = buildRuntimeSummaryCards([
        ['overall_status', result.overall_status, 'status'],
        ['last_successful_stage', result.last_successful_stage, 'text'],
        ['blocked_reason', result.blocked_reason, 'text'],
        ['updated_at', formatMaybeDate(result.updated_at), 'text'],
      ]);
      detailHtml = renderJsonPanel('backlog_summary', result.backlog_summary, 'No backlog summary');
    } else if (scope === 'proposition-runtime') {
      title = 'Proposition Runtime';
      summaryHtml = buildRuntimeSummaryCards([
        ['current_stage', result.current_stage, 'status'],
        ['last_successful_stage', result.last_successful_stage, 'text'],
        ['current_assessment_id', result.current_assessment_id, 'text'],
        ['backlog_state', result.backlog_state, 'text'],
      ]);
      detailHtml = `
        ${renderDetailList([
          { label: 'current_attempt', value: result.current_attempt || '-' },
          { label: 'last_failure_reason', value: result.last_failure_reason || '-' },
          { label: 'last_failure_at', value: formatMaybeDate(result.last_failure_at) },
        ])}
      `;
    } else {
      title = 'Artifact Runtime';
      summaryHtml = buildRuntimeSummaryCards([
        ['artifact_stage', result.artifact_stage, 'status'],
        ['correlation_id', result.correlation_id, 'text'],
        ['attempt_id', result.attempt_id, 'text'],
        ['last_failure_reason', result.last_failure_reason, 'text'],
      ]);
      detailHtml = `
        ${renderDetailList([
          { label: 'extractor_key', valueHtml: renderStructuredValue(result.extractor_key, 'No extractor key') },
          { label: 'last_failure_at', value: formatMaybeDate(result.last_failure_at) },
        ])}
      `;
    }

    return `
      ${renderAdminDetailCard({
        title,
        statusHtml: '<span class="shell-chip">operator-facing</span>',
        note: 'This runtime view explains queue, publish, backlog, and failure state without restating canonical conclusions.',
        bodyHtml: `
          ${summaryHtml}
          ${renderRuntimeLinks(route, scope)}
        `,
      })}
      ${renderAdminDetailCard({
        title: `${title} Detail`,
        statusHtml: `<span class="shell-chip">${esc(result.schema_version || 'runtime')}</span>`,
        note: 'Use the detail pane for structured runtime fields only.',
        bodyHtml: `
          ${detailHtml}
          ${renderJsonPanel('Raw Runtime Payload', result, 'No runtime payload.')}
        `,
      })}
      ${renderRuntimeBoundaryCard(
        scope === 'session-runtime'
          ? 'Session Runtime explains backlog and stage progression only.'
          : scope === 'proposition-runtime'
            ? 'Proposition Runtime explains queued, assessment, publish_ready, and externally_visible execution state.'
            : 'Artifact Runtime explains extractor progress and handoff state only.',
        renderRuntimeLinks(route, scope)
      )}
    `;
  }

  function renderJobRows(items, selectedJobId, filters) {
    if (!items.length) {
      const emptyCopy = filters.sessionId || filters.status
        ? 'No jobs match the current filters.'
        : 'No background jobs recorded yet.';
      return `<tr><td colspan="7">${renderEmptyState(emptyCopy)}</td></tr>`;
    }
    return items.map((job) => `
      <tr class="${job.job_id === selectedJobId ? 'is-selected' : ''}">
        <td>
          <button type="button" class="selectable-list-item ${job.job_id === selectedJobId ? 'is-active' : ''}" data-action="select-job" data-job-id="${esc(job.job_id)}">
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(job.job_id)}</span>
              <span class="selectable-list-meta">${esc(job.session_id || '-')}</span>
            </span>
            ${statusBadge(job.status)}
          </button>
        </td>
        <td>${esc(job.job_id || '-')}</td>
        <td>${esc(job.session_id || '-')}</td>
        <td>${esc(job.job_type || '-')}</td>
        <td>${statusBadge(job.status)}</td>
        <td>${esc(formatMaybeDate(job.created_at))}</td>
        <td>${esc(formatMaybeDate(job.updated_at))}</td>
      </tr>
    `).join('');
  }

  function renderJobFilters(route, filters) {
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Jobs Filters</h2>
            <div class="results-count">Filter jobs by linked session_id or status.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-job-filters">Reset</button>
          </div>
        </div>
        <p class="panel-note">GET /jobs and GET /jobs/{job_id} drive this page. Jobs remain read-only troubleshooting surfaces.</p>
        <form class="filters-grid" data-role="jobs-filter-form">
          <label>
            <span>session_id</span>
            <input type="search" name="session_id" value="${esc(filters.sessionId)}" placeholder="sess_..." />
          </label>
          <label>
            <span>status</span>
            <select name="status">
              <option value="" ${filters.status === '' ? 'selected' : ''}>All statuses</option>
              <option value="pending" ${filters.status === 'pending' ? 'selected' : ''}>pending</option>
              <option value="running" ${filters.status === 'running' ? 'selected' : ''}>running</option>
              <option value="completed" ${filters.status === 'completed' ? 'selected' : ''}>completed</option>
              <option value="failed" ${filters.status === 'failed' ? 'selected' : ''}>failed</option>
              <option value="cancelled" ${filters.status === 'cancelled' ? 'selected' : ''}>cancelled</option>
            </select>
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Apply Filters</button>
          </div>
        </form>
        <div class="detail-actions">
          ${route.sessionId ? `<a class="btn btn-sm" href="${esc(buildUiJobsUrl(route.sessionId, filters.status))}">Open in /ui Runtime</a>` : ''}
          ${route.sessionId ? `<a class="btn btn-sm" href="${esc(buildUiSessionsUrl(route.sessionId))}">Open linked session in /ui</a>` : ''}
        </div>
      </div>
    `;
  }

  function buildJobPayloadSummary(payload) {
    if (!payload || typeof payload !== 'object') return '-';
    const parts = [];
    if (payload.step_type) parts.push(`step_type=${payload.step_type}`);
    if (payload.params && typeof payload.params === 'object') {
      if (payload.params.table_name) parts.push(`table_name=${payload.params.table_name}`);
      if (payload.params.metric) parts.push(`metric=${payload.params.metric}`);
    }
    return parts.join(', ') || 'payload summary unavailable';
  }

  function renderJobDetail(selectedJob, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Job Detail',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'Job detail remains secondary to runtime diagnosis.',
        bodyHtml: `
          ${detailError.status === 404 ? '<p class="panel-note">404 job not found. Select another job from the list.</p>' : ''}
          ${renderStructuredError(detailError, 'Job detail unavailable.')}
        `,
      });
    }
    if (!selectedJob) {
      return renderAdminDetailCard({
        title: 'Job Detail',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Select a job to inspect payload summary, error detail, and linked session.',
        bodyHtml: renderEmptyState('Select a job from the list to inspect payload summary, error detail, and linked session.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Job Detail',
      statusHtml: statusBadge(selectedJob.status),
      note: 'Use this detail panel for payload summary, error detail, and linked session only. It is not canonical result.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'job_id', value: selectedJob.job_id },
          { label: 'linked session', value: selectedJob.session_id || '-' },
          { label: 'job_type', value: selectedJob.job_type || '-' },
          { label: 'status', valueHtml: statusBadge(selectedJob.status) },
          { label: 'created_at', value: formatMaybeDate(selectedJob.created_at) },
          { label: 'updated_at', value: formatMaybeDate(selectedJob.updated_at) },
          { label: 'payload summary', value: buildJobPayloadSummary(selectedJob.payload) },
          { label: 'error detail', value: selectedJob.error_detail || selectedJob.error || '-' },
        ])}
        <div class="detail-actions">
          ${selectedJob.session_id ? `<a class="btn btn-sm" href="${esc(buildUiSessionsUrl(selectedJob.session_id))}">Open linked session in /ui</a>` : ''}
          ${selectedJob.session_id ? `<a class="btn btn-sm" href="${esc(buildUiJobsUrl(selectedJob.session_id))}">Open in /ui Runtime</a>` : ''}
        </div>
        ${renderJsonPanel('Raw Job Payload', selectedJob, 'No job payload.')}
      `,
    });
  }

  function renderJobsView(route, viewModel) {
    const filters = viewModel.filters || currentJobFilters(route);
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderJobFilters(route, filters)}
        ${renderAdminTableCard({
          title: 'Jobs Inventory',
          count: viewModel.items.length,
          countLabel: 'job(s)',
          note: 'Jobs are read-only troubleshooting entries. No submit, cancel, or retry controls exist on this page.',
          columns: ['detail', 'job_id', 'session_id', 'job_type', 'status', 'created_at', 'updated_at'],
          actionsHtml: '<div class="detail-actions"><button type="button" class="btn" data-action="refresh-jobs">Refresh</button></div>',
          rowsHtml: renderJobRows(viewModel.items, viewModel.selectedJobId, filters),
          errorHtml: viewModel.listError ? renderStructuredError(viewModel.listError, 'Jobs unavailable.') : '',
        })}
      `,
      secondaryHtml: '',
      detailHtml: `
        ${renderJobDetail(viewModel.selectedJob, viewModel.detailError)}
        ${renderRuntimeBoundaryCard('Jobs help explain background execution and error detail, but they do not replace runtime status diagnosis for blocked work.', `
          <a class="btn btn-sm" href="${esc(buildUiJobsUrl(route.sessionId || '', filters.status || ''))}">Open Jobs in /ui</a>
        `)}
      `,
    });
  }

  function render(route) {
    return `
      <section data-role="runtime-jobs-body">
        ${route.subtab === 'jobs'
          ? renderLoadingState('Loading Jobs...')
          : renderLoadingState('Loading Runtime & Jobs...')}
      </section>
    `;
  }

  function refreshCurrentRuntimeJobs() {
    const panel = document.getElementById('panel-runtime-jobs');
    const route = ctx.getCurrentRoute();
    if (panel && route.tab === 'runtime-jobs') {
      void hydrate(panel, route);
    }
  }

  async function hydrate(panel, route) {
    const renderVersion = ++runtimeJobsRenderVersion;
    const safeRender = (html, binder = null) => {
      if (renderVersion !== runtimeJobsRenderVersion) return;
      const target = panel.querySelector('[data-role="runtime-jobs-body"]');
      if (target) {
        target.innerHTML = html;
        if (binder) binder(target);
      }
    };

    if (route.subtab === 'jobs') {
      const filters = currentJobFilters(route);
      safeRender(renderJobsView(route, {
        filters,
        items: [],
        selectedJobId: route.jobId || '',
        selectedJob: null,
        listError: null,
        detailError: null,
      }), (target) => bindJobsEvents(target, route));
      try {
        const items = await ctx.adminApi.listJobs(filters);
        const selectedJobId = route.jobId || items[0]?.job_id || '';
        if (!route.jobId && selectedJobId) {
          ctx.applyAdminRoute({ ...route, jobId: selectedJobId, sessionId: route.sessionId || items[0]?.session_id || '' }, 'replace');
          return;
        }
        let selectedJob = null;
        let detailError = null;
        if (selectedJobId) {
          try {
            selectedJob = await ctx.adminApi.getJob(selectedJobId);
          } catch (error) {
            detailError = normalizeApiError(error, 'Job detail unavailable.');
          }
        }
        safeRender(renderJobsView(route, {
          filters,
          items,
          selectedJobId,
          selectedJob,
          listError: null,
          detailError,
        }), (target) => bindJobsEvents(target, route));
      } catch (error) {
        safeRender(renderJobsView(route, {
          filters,
          items: [],
          selectedJobId: route.jobId || '',
          selectedJob: null,
          listError: normalizeApiError(error, 'Jobs unavailable.'),
          detailError: null,
        }), (target) => bindJobsEvents(target, route));
      }
      return;
    }

    const loadingView = renderAdminListDetailLayout({
      primaryHtml: renderRuntimeQueryCard(route),
      secondaryHtml: '',
      detailHtml: renderRuntimeResult(route, { loading: true, error: null, result: null }),
    });
    safeRender(loadingView, (target) => bindRuntimeEvents(target, route));

    try {
      let result = null;
      if (route.subtab === 'proposition-runtime') {
        if (route.sessionId && route.propositionId) {
          result = await ctx.adminApi.getPropositionRuntimeStatus(route.sessionId, route.propositionId);
        }
      } else if (route.subtab === 'artifact-runtime') {
        if (route.sessionId && route.artifactId) {
          result = await ctx.adminApi.getArtifactRuntimeStatus(route.sessionId, route.artifactId);
        }
      } else if (route.sessionId) {
        result = await ctx.adminApi.getSessionRuntimeStatus(route.sessionId);
      }
      safeRender(renderAdminListDetailLayout({
        primaryHtml: renderRuntimeQueryCard(route),
        secondaryHtml: '',
        detailHtml: renderRuntimeResult(route, { loading: false, error: null, result }),
      }), (target) => bindRuntimeEvents(target, route));
    } catch (error) {
      safeRender(renderAdminListDetailLayout({
        primaryHtml: renderRuntimeQueryCard(route),
        secondaryHtml: '',
        detailHtml: renderRuntimeResult(route, {
          loading: false,
          error: normalizeApiError(
            error,
            route.subtab === 'proposition-runtime'
              ? 'Proposition Runtime unavailable.'
              : route.subtab === 'artifact-runtime'
                ? 'Artifact Runtime unavailable.'
                : 'Session Runtime unavailable.'
          ),
          result: null,
        }),
      }), (target) => bindRuntimeEvents(target, route));
    }
  }

  function bindRuntimeEvents(target, route) {
    const queryForm = target.querySelector('[data-role="runtime-query-form"]');
    if (queryForm) {
      queryForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(queryForm);
        const resourceType = String(formData.get('resource_type') || 'session');
        const nextSubtab = resourceType === 'proposition'
          ? 'proposition-runtime'
          : resourceType === 'artifact'
            ? 'artifact-runtime'
            : resourceType === 'jobs'
              ? 'jobs'
              : 'session-runtime';
        ctx.applyAdminRoute({
          ...ctx.getCurrentRoute(),
          tab: 'runtime-jobs',
          subtab: nextSubtab,
          sessionId: String(formData.get('session_id') || '').trim(),
          propositionId: String(formData.get('proposition_id') || '').trim(),
          artifactId: String(formData.get('artifact_id') || '').trim(),
          jobId: nextSubtab === 'jobs' ? ctx.getCurrentRoute().jobId || '' : '',
        });
      });
    }
    target.querySelectorAll('[data-action="clear-runtime-query"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute({
          ...ctx.getCurrentRoute(),
          tab: 'runtime-jobs',
          subtab: 'session-runtime',
          sessionId: '',
          propositionId: '',
          artifactId: '',
          jobId: '',
        }, 'replace');
      });
    });
  }

  function bindJobsEvents(target, route) {
    bindRuntimeEvents(target, route);
    target.querySelectorAll('[data-action="refresh-jobs"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentRuntimeJobs());
    });
    target.querySelectorAll('[data-action="select-job"]').forEach((button) => {
      button.addEventListener('click', () => {
        const job = button.dataset.jobId || '';
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: 'runtime-jobs', subtab: 'jobs', jobId: job }, 'push');
      });
    });
    const filterForm = target.querySelector('[data-role="jobs-filter-form"]');
    if (filterForm) {
      filterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(filterForm);
        const nextFilters = {
          sessionId: String(formData.get('session_id') || '').trim(),
          status: String(formData.get('status') || '').trim(),
        };
        setJobFilters(nextFilters);
        ctx.applyAdminRoute({
          ...ctx.getCurrentRoute(),
          tab: 'runtime-jobs',
          subtab: 'jobs',
          sessionId: nextFilters.sessionId,
          jobId: '',
        }, 'replace');
      });
    }
    target.querySelectorAll('[data-action="clear-job-filters"]').forEach((button) => {
      button.addEventListener('click', () => {
        setJobFilters({ sessionId: '', status: '' });
        ctx.applyAdminRoute({
          ...ctx.getCurrentRoute(),
          tab: 'runtime-jobs',
          subtab: 'jobs',
          sessionId: '',
          jobId: '',
        }, 'replace');
      });
    });
  }

  return { render, hydrate };
}
