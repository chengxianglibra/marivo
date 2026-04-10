export function createGovernanceModule(ctx) {
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
    openDangerConfirm,
    statusBadge,
    fmtDate,
  } = shared;

  let governanceRenderVersion = 0;
  const governanceUiState = {
    policyFilters: {
      enabled: 'all',
    },
    qualityRuleFilters: {
      tableQuery: '',
    },
    approvalsFilters: {
      status: 'pending',
      sessionId: '',
    },
    policyErrors: {
      create: null,
      update: {},
    },
    qualityRuleErrors: {
      create: null,
      delete: {},
    },
    approvalsErrors: {
      decision: {},
      autoFlag: null,
    },
    helperState: {
      autoFlagResult: null,
      governanceCheckResult: null,
      governanceCheckError: null,
      routingResolveResult: null,
      routingResolveError: null,
      governanceCheckDefaults: {
        sessionId: '',
        stepType: 'metric_query',
        paramsJson: '{\n  "table_name": "analytics.watch_events",\n  "limit": 500\n}',
      },
      routingResolveDefaults: {
        tableNames: 'analytics.watch_events',
        routingIntentJson: '',
      },
    },
  };

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : '-';
  }

  function localError(message, detail = null) {
    return {
      message,
      detail: detail || message,
      transport: 'client',
    };
  }

  function parseJsonInput(value, emptyFallback) {
    const text = String(value || '').trim();
    if (!text) {
      return emptyFallback;
    }
    return JSON.parse(text);
  }

  function parseTableNamesInput(value) {
    return String(value || '')
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function currentPolicyFilters() {
    return {
      enabled: governanceUiState.policyFilters.enabled || 'all',
    };
  }

  function setPolicyFilters(nextFilters) {
    governanceUiState.policyFilters = {
      enabled: String(nextFilters?.enabled || 'all').trim() || 'all',
    };
  }

  function currentQualityRuleFilters() {
    return {
      tableQuery: governanceUiState.qualityRuleFilters.tableQuery || '',
    };
  }

  function setQualityRuleFilters(nextFilters) {
    governanceUiState.qualityRuleFilters = {
      tableQuery: String(nextFilters?.tableQuery || '').trim(),
    };
  }

  function currentApprovalFilters(route) {
    return {
      status: governanceUiState.approvalsFilters.status || 'pending',
      sessionId: governanceUiState.approvalsFilters.sessionId || route.sessionId || '',
    };
  }

  function setApprovalFilters(nextFilters) {
    governanceUiState.approvalsFilters = {
      status: String(nextFilters?.status || 'pending').trim() || 'pending',
      sessionId: String(nextFilters?.sessionId || '').trim(),
    };
  }

  function renderPolicyRows(policies, selectedPolicyId) {
    if (!policies.length) {
      return `
        <tr>
          <td colspan="6">${renderEmptyState('No policies match the current filters.', '<button type="button" class="btn btn-primary" data-action="open-create-policy">Create Policy</button>')}</td>
        </tr>
      `;
    }
    return policies
      .map(
        (policy) => `
      <tr class="${policy.policy_id === selectedPolicyId ? 'is-selected' : ''}">
        <td>
          <button
            type="button"
            class="selectable-list-item ${policy.policy_id === selectedPolicyId ? 'is-active' : ''}"
            data-action="select-policy"
            data-policy-id="${esc(policy.policy_id)}"
          >
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(policy.policy_id)}</span>
              <span class="selectable-list-meta">${esc(policy.name)}</span>
            </span>
            ${statusBadge(policy.enabled ? 'approved' : 'rejected')}
          </button>
        </td>
        <td>${esc(policy.name)}</td>
        <td>${esc(policy.policy_type)}</td>
        <td>${policy.enabled ? '<span class="shell-chip">enabled</span>' : '<span class="shell-chip">disabled</span>'}</td>
        <td>${esc(formatMaybeDate(policy.created_at))}</td>
        <td>${esc(formatMaybeDate(policy.updated_at))}</td>
      </tr>
    `
      )
      .join('');
  }

  function renderPolicyFilters(viewModel) {
    const filters = viewModel.filters || currentPolicyFilters();
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Policy Filters</h2>
            <div class="results-count">Filter policies by enabled status.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-policy-filters">Reset</button>
          </div>
        </div>
        <form class="filters-grid" data-role="policy-filters-form">
          <label>
            <span>Enabled Status</span>
            <select name="enabled">
              <option value="all" ${filters.enabled === 'all' ? 'selected' : ''}>All policies</option>
              <option value="enabled" ${filters.enabled === 'enabled' ? 'selected' : ''}>Enabled only</option>
              <option value="disabled" ${filters.enabled === 'disabled' ? 'selected' : ''}>Disabled only</option>
            </select>
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Apply Filters</button>
          </div>
        </form>
      </div>
    `;
  }

  function renderPolicyInventory(viewModel) {
    return renderAdminTableCard({
      title: 'Policy Inventory',
      count: viewModel.policies.length,
      countLabel: 'policy item(s)',
      note: 'GET /policies lists all policies. Create uses POST /policies. Edit Definition and enable/disable use PUT /policies/{policy_id}. Delete uses DELETE /policies/{policy_id}.',
      columns: ['policy_id', 'name', 'policy_type', 'enabled', 'created_at', 'updated_at'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn" data-action="refresh-governance">Refresh</button>
          <button type="button" class="btn btn-primary" data-action="open-create-policy">Create Policy</button>
        </div>
      `,
      rowsHtml: renderPolicyRows(viewModel.policies, viewModel.selectedPolicyId),
      errorHtml: viewModel.listError
        ? renderStructuredError(viewModel.listError, 'Policies unavailable.')
        : '',
    });
  }

  function renderPolicySummaryCard(policy, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Policy Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /policies/{policy_id} is the canonical policy detail endpoint.',
        bodyHtml: `
          ${detailError.status === 404 ? '<p class="panel-note">404 policy not found.</p>' : ''}
          ${renderStructuredError(detailError, 'Policy detail unavailable.')}
        `,
      });
    }
    if (!policy) {
      return renderAdminDetailCard({
        title: 'Policy Summary',
        statusHtml: '<span class="shell-chip">no policy selected</span>',
        note: 'Select a policy to inspect scope, definition, and lifecycle metadata.',
        bodyHtml: renderEmptyState('No policies configured yet. Create Policy to start governance enforcement.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Policy Summary',
      statusHtml: policy.enabled ? '<span class="shell-chip">enabled</span>' : '<span class="shell-chip">disabled</span>',
      note: 'T10 keeps policy editing aligned to the current HTTP contract: enable/disable and Edit Definition only.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'policy_id', value: policy.policy_id },
          { label: 'name', value: policy.name },
          { label: 'policy_type', value: policy.policy_type },
          { label: 'enabled', value: policy.enabled ? 'true' : 'false' },
          { label: 'created_at', value: formatMaybeDate(policy.created_at) },
          { label: 'updated_at', value: formatMaybeDate(policy.updated_at) },
        ])}
        ${renderJsonPanel('Scope JSON', policy.scope, 'No scope configured.')}
        ${renderJsonPanel('Definition JSON', policy.definition, 'No definition configured.')}
        ${renderJsonPanel('Raw JSON Panel', policy, 'No policy payload.')}
      `,
    });
  }

  function renderPolicyEditorCard(policy, updateError, createError) {
    const definitionText = policy ? JSON.stringify(policy.definition || {}, null, 2) : '{\n  "min_group_size": 100\n}';
    return renderAdminDetailCard({
      title: 'Policy Editor',
      statusHtml: '<span class="shell-chip">typed controls only</span>',
      note: 'Create Policy accepts name, policy_type, Definition JSON, and Scope JSON. Edit Definition updates the selected policy definition only; name, policy_type, and scope remain immutable in /admin because the backend does not support changing them.',
      bodyHtml: `
        <div class="card">
          <h3>Create Policy</h3>
          <p class="panel-note">POST /policies</p>
          ${createError ? renderStructuredError(createError, 'Create Policy failed.') : ''}
          <form data-role="create-policy-form">
            <label>
              <span>name</span>
              <input type="text" name="name" placeholder="no_raw_pii" required />
            </label>
            <label>
              <span>policy_type</span>
              <select name="policy_type">
                <option value="aggregate_only">aggregate_only</option>
                <option value="field_mask">field_mask</option>
                <option value="row_filter">row_filter</option>
                <option value="max_rows">max_rows</option>
              </select>
            </label>
            <label>
              <span>Scope JSON</span>
              <textarea name="scope_json" rows="6" spellcheck="false">{}</textarea>
            </label>
            <label>
              <span>Definition JSON</span>
              <textarea name="definition_json" rows="8" spellcheck="false">{}</textarea>
            </label>
            <div class="detail-actions">
              <button type="submit" class="btn btn-primary">Create Policy</button>
            </div>
          </form>
        </div>
        <div class="card">
          <h3>Edit Definition</h3>
          <p class="panel-note">PUT /policies/{policy_id}</p>
          ${
            policy
              ? `
                ${updateError ? renderStructuredError(updateError, 'Update Policy failed.') : ''}
                <form data-role="update-policy-form" data-policy-id="${esc(policy.policy_id)}">
                  <label>
                    <span>Definition JSON</span>
                    <textarea name="definition_json" rows="10" spellcheck="false">${esc(definitionText)}</textarea>
                  </label>
                  <div class="detail-actions">
                    <button type="submit" class="btn btn-primary">Edit Definition</button>
                    <button type="button" class="btn" data-action="toggle-policy" data-policy-id="${esc(policy.policy_id)}" data-enabled="${policy.enabled ? 'true' : 'false'}">${policy.enabled ? 'Disable Policy' : 'Enable Policy'}</button>
                    <button type="button" class="btn btn-danger" data-action="delete-policy" data-policy-id="${esc(policy.policy_id)}">Delete Policy</button>
                  </div>
                </form>
              `
              : renderEmptyState('Select a policy before using Edit Definition or delete controls.')
          }
        </div>
      `,
    });
  }

  function renderPoliciesBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderPolicyFilters(viewModel)}
        ${renderPolicyInventory(viewModel)}
      `,
      secondaryHtml: renderPolicySummaryCard(viewModel.selectedPolicy, viewModel.detailError),
      detailHtml: renderPolicyEditorCard(
        viewModel.selectedPolicy,
        viewModel.selectedPolicyId ? governanceUiState.policyErrors.update[viewModel.selectedPolicyId] || null : null,
        governanceUiState.policyErrors.create
      ),
    });
  }

  function renderQualityRuleRows(rules, selectedRuleId) {
    if (!rules.length) {
      return `
        <tr>
          <td colspan="6">${renderEmptyState('No quality rules match the current filters.', '<button type="button" class="btn btn-primary" data-action="open-create-quality-rule">Create Quality Rule</button>')}</td>
        </tr>
      `;
    }
    return rules
      .map(
        (rule) => `
      <tr class="${rule.rule_id === selectedRuleId ? 'is-selected' : ''}">
        <td>
          <button
            type="button"
            class="selectable-list-item ${rule.rule_id === selectedRuleId ? 'is-active' : ''}"
            data-action="select-quality-rule"
            data-rule-id="${esc(rule.rule_id)}"
          >
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(rule.rule_id)}</span>
              <span class="selectable-list-meta">${esc(rule.name)}</span>
            </span>
            ${statusBadge(rule.severity === 'block' ? 'rejected' : 'approved')}
          </button>
        </td>
        <td>${esc(rule.name)}</td>
        <td>${esc(rule.rule_type)}</td>
        <td>${esc(rule.table_name)}</td>
        <td>${esc(rule.severity)}</td>
        <td>${esc(formatMaybeDate(rule.created_at))}</td>
      </tr>
    `
      )
      .join('');
  }

  function renderQualityRuleFilters(viewModel) {
    const filters = viewModel.filters || currentQualityRuleFilters();
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Quality Rule Filters</h2>
            <div class="results-count">Filter GET /quality-rules by table name.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-quality-rule-filters">Reset</button>
          </div>
        </div>
        <form class="filters-grid" data-role="quality-rule-filters-form">
          <label>
            <span>table_name</span>
            <input type="search" name="table_query" value="${esc(filters.tableQuery)}" placeholder="analytics.watch_events" />
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Apply Filters</button>
          </div>
        </form>
      </div>
    `;
  }

  function renderQualityRuleInventory(viewModel) {
    return renderAdminTableCard({
      title: 'Quality Rule Inventory',
      count: viewModel.rules.length,
      countLabel: 'quality rule(s)',
      note: 'GET /quality-rules lists enabled rules. Create uses POST /quality-rules. Delete uses DELETE /quality-rules/{rule_id}. There is no update endpoint, so T10 does not fake edit controls.',
      columns: ['rule_id', 'name', 'rule_type', 'table_name', 'severity', 'created_at'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn" data-action="refresh-governance">Refresh</button>
          <button type="button" class="btn btn-primary" data-action="open-create-quality-rule">Create Quality Rule</button>
        </div>
      `,
      rowsHtml: renderQualityRuleRows(viewModel.rules, viewModel.selectedRuleId),
      errorHtml: viewModel.listError
        ? renderStructuredError(viewModel.listError, 'Quality Rules unavailable.')
        : '',
    });
  }

  function renderQualityRuleSummaryCard(rule) {
    if (!rule) {
      return renderAdminDetailCard({
        title: 'Quality Rule Summary',
        statusHtml: '<span class="shell-chip">no rule selected</span>',
        note: 'Select a quality rule to inspect threshold and severity.',
        bodyHtml: renderEmptyState('No quality rules configured yet. Create Quality Rule to define freshness or completeness expectations.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Quality Rule Summary',
      statusHtml: `<span class="shell-chip">${esc(rule.severity || 'warn')}</span>`,
      note: 'Quality rule detail is list-backed because the current API does not expose GET /quality-rules/{rule_id}.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'rule_id', value: rule.rule_id },
          { label: 'name', value: rule.name },
          { label: 'rule_type', value: rule.rule_type },
          { label: 'table_name', value: rule.table_name },
          { label: 'severity', value: rule.severity },
          { label: 'enabled', value: rule.enabled ? 'true' : 'false' },
          { label: 'created_at', value: formatMaybeDate(rule.created_at) },
        ])}
        ${renderJsonPanel('Threshold JSON', rule.threshold, 'No threshold configured.')}
        ${renderJsonPanel('Raw JSON Panel', rule, 'No quality rule payload.')}
      `,
    });
  }

  function renderQualityRuleCreateCard(selectedRule) {
    const deleteError = selectedRule
      ? governanceUiState.qualityRuleErrors.delete[selectedRule.rule_id] || null
      : null;
    return renderAdminDetailCard({
      title: 'Quality Rule Create',
      statusHtml: '<span class="shell-chip">create and delete only</span>',
      note: 'POST /quality-rules creates new rules. DELETE /quality-rules/{rule_id} removes them. There is no update endpoint, so /admin does not expose edit actions.',
      bodyHtml: `
        <div class="card">
          <h3>Create Quality Rule</h3>
          <p class="panel-note">POST /quality-rules</p>
          ${governanceUiState.qualityRuleErrors.create ? renderStructuredError(governanceUiState.qualityRuleErrors.create, 'Create Quality Rule failed.') : ''}
          <form data-role="create-quality-rule-form">
            <label>
              <span>name</span>
              <input type="text" name="name" placeholder="watch_events_freshness" required />
            </label>
            <label>
              <span>rule_type</span>
              <select name="rule_type">
                <option value="freshness">freshness</option>
                <option value="null_rate">null_rate</option>
                <option value="row_count_min">row_count_min</option>
              </select>
            </label>
            <label>
              <span>table_name</span>
              <input type="text" name="table_name" placeholder="analytics.watch_events" required />
            </label>
            <label>
              <span>severity</span>
              <select name="severity">
                <option value="warn">warn</option>
                <option value="block">block</option>
              </select>
            </label>
            <label>
              <span>Threshold JSON</span>
              <textarea name="threshold_json" rows="8" spellcheck="false">{\n  "max_age_hours": 24\n}</textarea>
            </label>
            <div class="detail-actions">
              <button type="submit" class="btn btn-primary">Create Quality Rule</button>
            </div>
          </form>
        </div>
        <div class="card">
          <h3>Delete Quality Rule</h3>
          <p class="panel-note">DELETE /quality-rules/{rule_id}</p>
          ${deleteError ? renderStructuredError(deleteError, 'Delete Quality Rule failed.') : ''}
          ${
            selectedRule
              ? `
                <div class="detail-actions">
                  <button type="button" class="btn btn-danger" data-action="delete-quality-rule" data-rule-id="${esc(selectedRule.rule_id)}">Delete Quality Rule</button>
                </div>
              `
              : renderEmptyState('Select a quality rule before using delete controls.')
          }
        </div>
      `,
    });
  }

  function renderQualityRulesBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderQualityRuleFilters(viewModel)}
        ${renderQualityRuleInventory(viewModel)}
      `,
      secondaryHtml: renderQualityRuleSummaryCard(viewModel.selectedRule),
      detailHtml: renderQualityRuleCreateCard(viewModel.selectedRule),
    });
  }

  function renderApprovalFilters(viewModel) {
    const filters = viewModel.filters || currentApprovalFilters(ctx.getCurrentRoute());
    return `
      <div class="card">
        <div class="list-meta">
          <div>
            <h2>Approval Filters</h2>
            <div class="results-count">Approvals default to pending. Filter by status or session_id.</div>
          </div>
          <div class="detail-actions">
            <button type="button" class="btn" data-action="clear-approval-filters">Reset</button>
          </div>
        </div>
        <form class="filters-grid" data-role="approval-filters-form">
          <label>
            <span>Status</span>
            <select name="status">
              <option value="pending" ${filters.status === 'pending' ? 'selected' : ''}>Pending</option>
              <option value="approved" ${filters.status === 'approved' ? 'selected' : ''}>Approved</option>
              <option value="rejected" ${filters.status === 'rejected' ? 'selected' : ''}>Rejected</option>
              <option value="all" ${filters.status === 'all' ? 'selected' : ''}>All statuses</option>
            </select>
          </label>
          <label>
            <span>session_id</span>
            <input type="search" name="session_id" value="${esc(filters.sessionId)}" placeholder="sess_..." />
          </label>
          <div class="detail-actions">
            <button type="submit" class="btn btn-primary">Apply Filters</button>
          </div>
        </form>
      </div>
    `;
  }

  function renderApprovalRows(approvals, selectedRequestId) {
    if (!approvals.length) {
      return `
        <tr>
          <td colspan="7">${renderEmptyState('No approval requests match the current filters. The default queue only shows pending requests.')}</td>
        </tr>
      `;
    }
    return approvals
      .map(
        (approval) => `
      <tr class="${approval.request_id === selectedRequestId ? 'is-selected' : ''}">
        <td>
          <button
            type="button"
            class="selectable-list-item ${approval.request_id === selectedRequestId ? 'is-active' : ''}"
            data-action="select-approval"
            data-request-id="${esc(approval.request_id)}"
            data-session-id="${esc(approval.session_id)}"
          >
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(approval.request_id)}</span>
              <span class="selectable-list-meta">${esc(approval.rec_id)}</span>
            </span>
            ${statusBadge(approval.status)}
          </button>
        </td>
        <td>${esc(approval.session_id)}</td>
        <td>${esc(approval.rec_id)}</td>
        <td>${statusBadge(approval.status)}</td>
        <td>${esc(approval.reviewer || '-')}</td>
        <td>${esc(formatMaybeDate(approval.submitted_at))}</td>
        <td>${esc(formatMaybeDate(approval.decided_at))}</td>
      </tr>
    `
      )
      .join('');
  }

  function renderApprovalInventory(viewModel) {
    const pendingCount = viewModel.approvals.filter((item) => item.status === 'pending').length;
    return renderAdminTableCard({
      title: 'Approval Queue',
      count: viewModel.approvals.length,
      countLabel: 'approval request(s)',
      note: 'GET /approvals and GET /approvals/{request_id} drive the queue. Approve and Reject operate only on pending requests. The global approvals badge remains on the governance nav item.',
      columns: ['request_id', 'session_id', 'rec_id', 'status', 'reviewer', 'submitted_at', 'decided_at'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn" data-action="refresh-governance">Refresh</button>
          <span class="shell-chip">pending approvals: ${esc(String(pendingCount))}</span>
        </div>
      `,
      rowsHtml: renderApprovalRows(viewModel.approvals, viewModel.selectedRequestId),
      errorHtml: viewModel.listError
        ? renderStructuredError(viewModel.listError, 'Approvals unavailable.')
        : '',
    });
  }

  function renderApprovalSummaryCard(approval, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Approval Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /approvals/{request_id} is the canonical approval detail endpoint.',
        bodyHtml: `
          ${detailError.status === 404 ? '<p class="panel-note">404 approval request not found.</p>' : ''}
          ${renderStructuredError(detailError, 'Approval detail unavailable.')}
        `,
      });
    }
    if (!approval) {
      return renderAdminDetailCard({
        title: 'Approval Summary',
        statusHtml: '<span class="shell-chip">no approval selected</span>',
        note: 'Select an approval request to inspect rec_id, session, and review history.',
        bodyHtml: renderEmptyState('No approval request selected. Pending approvals remain the default governance queue.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Approval Summary',
      statusHtml: statusBadge(approval.status),
      note: 'Approvals are session-linked governance objects. Use Open linked session in /ui for canonical read surfaces.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'request_id', value: approval.request_id },
          { label: 'session_id', value: approval.session_id },
          { label: 'rec_id', value: approval.rec_id },
          { label: 'status', valueHtml: statusBadge(approval.status) },
          { label: 'reviewer', value: approval.reviewer || '-' },
          { label: 'submitted_at', value: formatMaybeDate(approval.submitted_at) },
          { label: 'decided_at', value: formatMaybeDate(approval.decided_at) },
          { label: 'reason', value: approval.reason || '-' },
        ])}
        <div class="detail-actions">
          <a class="btn btn-sm" href="${esc(buildUiSessionsUrl(approval.session_id))}">Open linked session in /ui</a>
        </div>
        ${renderJsonPanel('Raw JSON Panel', approval, 'No approval payload.')}
      `,
    });
  }

  function renderApprovalActionsCard(approval) {
    const decisionError = approval
      ? governanceUiState.approvalsErrors.decision[approval.request_id] || null
      : null;
    return renderAdminDetailCard({
      title: 'Approval Actions',
      statusHtml: '<span class="shell-chip">pending only</span>',
      note: 'POST /approvals/{request_id}/approve and POST /approvals/{request_id}/reject require reviewer and optional reason. POST /sessions/{session_id}/approvals/auto-flag helps seed requests from recommendation risk.',
      bodyHtml: `
        <div class="card">
          <h3>Approve / Reject</h3>
          ${decisionError ? renderStructuredError(decisionError, 'Approval action failed.') : ''}
          ${
            approval && approval.status === 'pending'
              ? `
                <form data-role="approval-decision-form" data-request-id="${esc(approval.request_id)}">
                  <label>
                    <span>reviewer</span>
                    <input type="text" name="reviewer" placeholder="ops_user" required />
                  </label>
                  <label>
                    <span>reason</span>
                    <textarea name="reason" rows="5" spellcheck="false" placeholder="Decision note for audit trail"></textarea>
                  </label>
                  <div class="detail-actions">
                    <button type="submit" class="btn btn-primary" data-decision="approve">Approve</button>
                    <button type="submit" class="btn btn-danger" data-decision="reject">Reject</button>
                  </div>
                </form>
              `
              : approval
                ? renderEmptyState(`Request is already ${approval.status}. Approve and Reject are disabled.`)
                : renderEmptyState('Select a pending approval request before using approval actions.')
          }
        </div>
        <div class="card">
          <h3>Auto-flag Approvals</h3>
          <p class="panel-note">POST /sessions/{session_id}/approvals/auto-flag</p>
          ${governanceUiState.approvalsErrors.autoFlag ? renderStructuredError(governanceUiState.approvalsErrors.autoFlag, 'Auto-flag approvals failed.') : ''}
          <form data-role="auto-flag-form">
            <label>
              <span>session_id</span>
              <input type="text" name="session_id" value="${esc(approval?.session_id || ctx.getCurrentRoute().sessionId || '')}" placeholder="sess_..." required />
            </label>
            <label>
              <span>risk_threshold</span>
              <select name="risk_threshold">
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="P3">P3</option>
              </select>
            </label>
            <div class="detail-actions">
              <button type="submit" class="btn">Auto-flag</button>
            </div>
          </form>
          ${
            governanceUiState.helperState.autoFlagResult
              ? renderJsonPanel('Auto-flag Result', governanceUiState.helperState.autoFlagResult, 'No auto-flag result.')
              : ''
          }
        </div>
      `,
    });
  }

  function renderApprovalsBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderApprovalFilters(viewModel)}
        ${renderApprovalInventory(viewModel)}
      `,
      secondaryHtml: renderApprovalSummaryCard(viewModel.selectedApproval, viewModel.detailError),
      detailHtml: renderApprovalActionsCard(viewModel.selectedApproval),
    });
  }

  function renderGovernanceHelpersBody() {
    const helperState = governanceUiState.helperState;
    const governanceDefaults = helperState.governanceCheckDefaults;
    const routingDefaults = helperState.routingResolveDefaults;
    return renderAdminListDetailLayout({
      primaryHtml: `
        ${renderAdminDetailCard({
          title: 'Governance Helpers',
          statusHtml: '<span class="shell-chip">diagnostic only</span>',
          note: 'Governance Helpers stay auxiliary. They support pre-flight diagnostics and routing checks, not the primary policy/rule/approval workflow.',
          bodyHtml: `
            <p class="panel-note">POST /governance/check checks a proposed step against active policies.</p>
            <p class="panel-note">POST /routing/resolve shows how table_names map onto an execution engine. Do not move approvals or runtime troubleshooting into this page.</p>
          `,
        })}
        <div class="card">
          <div class="list-meta">
            <div>
              <h2>Governance Check</h2>
              <div class="results-count">POST /governance/check</div>
            </div>
          </div>
          ${helperState.governanceCheckError ? renderStructuredError(helperState.governanceCheckError, 'Governance Check failed.') : ''}
          <form data-role="governance-check-form">
            <label>
              <span>session_id</span>
              <input type="text" name="session_id" value="${esc(governanceDefaults.sessionId)}" placeholder="sess_..." required />
            </label>
            <label>
              <span>step_type</span>
              <input type="text" name="step_type" value="${esc(governanceDefaults.stepType)}" placeholder="metric_query" required />
            </label>
            <label>
              <span>params JSON</span>
              <textarea name="params_json" rows="10" spellcheck="false">${esc(governanceDefaults.paramsJson)}</textarea>
            </label>
            <div class="detail-actions">
              <button type="submit" class="btn btn-primary">Run Governance Check</button>
            </div>
          </form>
        </div>
      `,
      secondaryHtml: `
        <div class="card">
          <div class="list-meta">
            <div>
              <h2>Routing Resolve</h2>
              <div class="results-count">POST /routing/resolve</div>
            </div>
          </div>
          ${helperState.routingResolveError ? renderStructuredError(helperState.routingResolveError, 'Routing Resolve failed.') : ''}
          <form data-role="routing-resolve-form">
            <label>
              <span>table_names</span>
              <textarea name="table_names" rows="5" spellcheck="false">${esc(routingDefaults.tableNames)}</textarea>
            </label>
            <label>
              <span>routing_intent JSON</span>
              <textarea name="routing_intent_json" rows="8" spellcheck="false" placeholder="{\n  \"required_capabilities\": [\"aggregate_only\"]\n}">${esc(routingDefaults.routingIntentJson)}</textarea>
            </label>
            <div class="detail-actions">
              <button type="submit" class="btn btn-primary">Run Routing Resolve</button>
            </div>
          </form>
        </div>
      `,
      detailHtml: `
        ${renderAdminDetailCard({
          title: 'Helper Results',
          statusHtml: '<span class="shell-chip">JSON + summary</span>',
          note: 'Helpers remain read-oriented. They do not create policies, rules, sessions, or approvals directly.',
          bodyHtml: `
            ${helperState.governanceCheckResult ? renderJsonPanel('Governance Check Result', helperState.governanceCheckResult, 'No governance check result yet.') : renderEmptyState('Run Governance Check or Routing Resolve to inspect diagnostic output.')}
            ${helperState.routingResolveResult ? renderJsonPanel('Routing Resolve Result', helperState.routingResolveResult, 'No routing resolve result yet.') : ''}
          `,
        })}
      `,
    });
  }

  function render(route) {
    const copy = route.subtab === 'policies'
      ? 'Loading Policies...'
      : route.subtab === 'quality-rules'
        ? 'Loading Quality Rules...'
        : route.subtab === 'approvals'
          ? 'Loading Approvals...'
          : 'Loading Governance Helpers...';
    return renderLoadingState(copy);
  }

  function refreshCurrentGovernance() {
    const panel = document.getElementById('panel-governance');
    if (panel) {
      void hydrate(panel, ctx.getCurrentRoute());
    }
  }

  async function handleCreatePolicy(form) {
    governanceUiState.policyErrors.create = null;
    const formData = new FormData(form);
    try {
      const created = await ctx.adminApi.createPolicy({
        name: String(formData.get('name') || '').trim(),
        policy_type: String(formData.get('policy_type') || '').trim(),
        scope: parseJsonInput(formData.get('scope_json'), {}),
        definition: parseJsonInput(formData.get('definition_json'), {}),
      });
      toast('Policy created.', 'success');
      ctx.applyAdminRoute(
        {
          ...ctx.getCurrentRoute(),
          tab: 'governance',
          subtab: 'policies',
          policyId: created.policy_id,
          ruleId: '',
          requestId: '',
        },
        'push'
      );
    } catch (error) {
      governanceUiState.policyErrors.create = normalizeApiError(error, 'Create Policy failed.');
      toast(governanceUiState.policyErrors.create.message, 'error');
      refreshCurrentGovernance();
    }
  }

  async function handleUpdatePolicy(form, policyId) {
    governanceUiState.policyErrors.update[policyId] = null;
    const formData = new FormData(form);
    try {
      await ctx.adminApi.updatePolicy(policyId, {
        definition: parseJsonInput(formData.get('definition_json'), {}),
      });
      toast('Policy definition updated.', 'success');
      refreshCurrentGovernance();
    } catch (error) {
      governanceUiState.policyErrors.update[policyId] = normalizeApiError(
        error,
        'Update Policy failed.'
      );
      toast(governanceUiState.policyErrors.update[policyId].message, 'error');
      refreshCurrentGovernance();
    }
  }

  function handleTogglePolicy(policy) {
    openDangerConfirm({
      title: policy.enabled ? 'Disable Policy' : 'Enable Policy',
      objectLabel: policy.policy_id,
      impactScope: 'Toggles whether the policy participates in governance enforcement.',
      reversible: 'Yes',
      confirmLabel: policy.enabled ? 'Disable Policy' : 'Enable Policy',
      detailsHtml: renderDetailList([
        { label: 'name', value: policy.name },
        { label: 'policy_type', value: policy.policy_type },
        { label: 'current enabled', value: policy.enabled ? 'true' : 'false' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.updatePolicy(policy.policy_id, { enabled: !policy.enabled });
          toast(policy.enabled ? 'Policy disabled.' : 'Policy enabled.', 'success');
          refreshCurrentGovernance();
        } catch (error) {
          governanceUiState.policyErrors.update[policy.policy_id] = normalizeApiError(
            error,
            'Update Policy failed.'
          );
          toast(governanceUiState.policyErrors.update[policy.policy_id].message, 'error');
          refreshCurrentGovernance();
        }
      },
    });
  }

  function handleDeletePolicy(policy) {
    openDangerConfirm({
      title: 'Delete Policy',
      objectLabel: policy.policy_id,
      impactScope: 'Removes the policy and stops all future enforcement for this policy_id.',
      reversible: 'No',
      confirmLabel: 'Delete Policy',
      detailsHtml: renderDetailList([
        { label: 'name', value: policy.name },
        { label: 'policy_type', value: policy.policy_type },
        { label: 'warning', value: 'Delete Policy permanently removes the governance object.' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.deletePolicy(policy.policy_id);
          toast('Policy deleted.', 'success');
          const currentRoute = ctx.getCurrentRoute();
          ctx.applyAdminRoute(
            { ...currentRoute, policyId: '', requestId: '', ruleId: '' },
            'replace'
          );
        } catch (error) {
          governanceUiState.policyErrors.update[policy.policy_id] = normalizeApiError(
            error,
            'Delete Policy failed.'
          );
          toast(governanceUiState.policyErrors.update[policy.policy_id].message, 'error');
          refreshCurrentGovernance();
        }
      },
    });
  }

  async function handleCreateQualityRule(form) {
    governanceUiState.qualityRuleErrors.create = null;
    const formData = new FormData(form);
    try {
      const created = await ctx.adminApi.createQualityRule({
        name: String(formData.get('name') || '').trim(),
        rule_type: String(formData.get('rule_type') || '').trim(),
        table_name: String(formData.get('table_name') || '').trim(),
        severity: String(formData.get('severity') || 'warn').trim(),
        threshold: parseJsonInput(formData.get('threshold_json'), {}),
      });
      toast('Quality rule created.', 'success');
      ctx.applyAdminRoute(
        {
          ...ctx.getCurrentRoute(),
          tab: 'governance',
          subtab: 'quality-rules',
          ruleId: created.rule_id,
          policyId: '',
          requestId: '',
        },
        'push'
      );
    } catch (error) {
      governanceUiState.qualityRuleErrors.create = normalizeApiError(
        error,
        'Create Quality Rule failed.'
      );
      toast(governanceUiState.qualityRuleErrors.create.message, 'error');
      refreshCurrentGovernance();
    }
  }

  function handleDeleteQualityRule(rule) {
    openDangerConfirm({
      title: 'Delete Quality Rule',
      objectLabel: rule.rule_id,
      impactScope: 'Removes the quality rule from future sync and quality checks.',
      reversible: 'No',
      confirmLabel: 'Delete Quality Rule',
      detailsHtml: renderDetailList([
        { label: 'name', value: rule.name },
        { label: 'rule_type', value: rule.rule_type },
        { label: 'table_name', value: rule.table_name },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.deleteQualityRule(rule.rule_id);
          toast('Quality rule deleted.', 'success');
          const currentRoute = ctx.getCurrentRoute();
          ctx.applyAdminRoute(
            { ...currentRoute, ruleId: '', policyId: '', requestId: '' },
            'replace'
          );
        } catch (error) {
          governanceUiState.qualityRuleErrors.delete[rule.rule_id] = normalizeApiError(
            error,
            'Delete Quality Rule failed.'
          );
          toast(governanceUiState.qualityRuleErrors.delete[rule.rule_id].message, 'error');
          refreshCurrentGovernance();
        }
      },
    });
  }

  function handleApprovalDecision(approval, payload, decision) {
    openDangerConfirm({
      title: decision === 'approve' ? 'Approve Request' : 'Reject Request',
      objectLabel: approval.request_id,
      impactScope: 'Writes a review decision and closes the pending approval request.',
      reversible: 'No',
      confirmLabel: decision === 'approve' ? 'Approve' : 'Reject',
      detailsHtml: renderDetailList([
        { label: 'session_id', value: approval.session_id },
        { label: 'rec_id', value: approval.rec_id },
        { label: 'reviewer', value: payload.reviewer },
      ]),
      onConfirm: async () => {
        try {
          governanceUiState.approvalsErrors.decision[approval.request_id] = null;
          if (decision === 'approve') {
            await ctx.adminApi.approveApproval(approval.request_id, payload);
            toast('Approval request approved.', 'success');
          } else {
            await ctx.adminApi.rejectApproval(approval.request_id, payload);
            toast('Approval request rejected.', 'success');
          }
          refreshCurrentGovernance();
        } catch (error) {
          governanceUiState.approvalsErrors.decision[approval.request_id] = normalizeApiError(
            error,
            'Approval action failed.'
          );
          toast(governanceUiState.approvalsErrors.decision[approval.request_id].message, 'error');
          refreshCurrentGovernance();
        }
      },
    });
  }

  async function handleAutoFlag(form) {
    const formData = new FormData(form);
    governanceUiState.approvalsErrors.autoFlag = null;
    try {
      const sessionId = String(formData.get('session_id') || '').trim();
      const result = await ctx.adminApi.autoFlagApprovals(sessionId, {
        risk_threshold: String(formData.get('risk_threshold') || 'P0').trim() || 'P0',
      });
      governanceUiState.helperState.autoFlagResult = result;
      toast('Auto-flag completed.', 'success');
      ctx.applyAdminRoute(
        { ...ctx.getCurrentRoute(), tab: 'governance', subtab: 'approvals', sessionId },
        'replace'
      );
    } catch (error) {
      governanceUiState.approvalsErrors.autoFlag = normalizeApiError(
        error,
        'Auto-flag approvals failed.'
      );
      toast(governanceUiState.approvalsErrors.autoFlag.message, 'error');
      refreshCurrentGovernance();
    }
  }

  async function handleGovernanceCheck(form) {
    const formData = new FormData(form);
    try {
      const payload = {
        session_id: String(formData.get('session_id') || '').trim(),
        step_type: String(formData.get('step_type') || '').trim(),
        params: parseJsonInput(formData.get('params_json'), {}),
      };
      governanceUiState.helperState.governanceCheckDefaults = {
        sessionId: payload.session_id,
        stepType: payload.step_type,
        paramsJson: JSON.stringify(payload.params, null, 2),
      };
      governanceUiState.helperState.governanceCheckError = null;
      governanceUiState.helperState.governanceCheckResult = await ctx.adminApi.governanceCheck(
        payload
      );
      toast('Governance Check completed.', 'success');
      refreshCurrentGovernance();
    } catch (error) {
      governanceUiState.helperState.governanceCheckError = normalizeApiError(
        error,
        'Governance Check failed.'
      );
      toast(governanceUiState.helperState.governanceCheckError.message, 'error');
      refreshCurrentGovernance();
    }
  }

  async function handleRoutingResolve(form) {
    const formData = new FormData(form);
    try {
      const tableNames = parseTableNamesInput(formData.get('table_names'));
      if (!tableNames.length) {
        throw localError('Routing Resolve requires at least one table name.');
      }
      const routingIntentJson = String(formData.get('routing_intent_json') || '').trim();
      const payload = {
        table_names: tableNames,
        routing_intent: routingIntentJson ? parseJsonInput(routingIntentJson, null) : null,
      };
      governanceUiState.helperState.routingResolveDefaults = {
        tableNames: tableNames.join('\n'),
        routingIntentJson: routingIntentJson,
      };
      governanceUiState.helperState.routingResolveError = null;
      governanceUiState.helperState.routingResolveResult = await ctx.adminApi.routingResolve(
        payload
      );
      toast('Routing Resolve completed.', 'success');
      refreshCurrentGovernance();
    } catch (error) {
      governanceUiState.helperState.routingResolveError = normalizeApiError(
        error,
        'Routing Resolve failed.'
      );
      toast(governanceUiState.helperState.routingResolveError.message, 'error');
      refreshCurrentGovernance();
    }
  }

  async function hydrate(panel, route) {
    governanceRenderVersion += 1;
    const renderVersion = governanceRenderVersion;

    const safeRender = (html, bindFn = null) => {
      if (renderVersion !== governanceRenderVersion) return;
      panel.innerHTML = html;
      if (typeof bindFn === 'function') bindFn(panel);
    };

    if (route.subtab === 'governance-helpers') {
      safeRender(renderGovernanceHelpersBody(), bindHelpersEvents);
      return;
    }

    if (route.subtab === 'policies') {
      safeRender(renderLoadingState('Loading Policies...'));
      const filters = currentPolicyFilters();
      try {
        const allPolicies = await ctx.adminApi.listPolicies();
        const policies = allPolicies.filter((policy) => {
          if (filters.enabled === 'enabled') return policy.enabled;
          if (filters.enabled === 'disabled') return !policy.enabled;
          return true;
        });
        let selectedPolicyId = route.policyId || '';
        if (!selectedPolicyId && policies.length) {
          selectedPolicyId = policies[0].policy_id;
        }
        if (selectedPolicyId && route.policyId !== selectedPolicyId) {
          ctx.applyAdminRoute({ ...route, tab: 'governance', subtab: 'policies', policyId: selectedPolicyId }, 'replace');
          return;
        }
        let selectedPolicy = null;
        let detailError = null;
        if (selectedPolicyId) {
          try {
            selectedPolicy = await ctx.adminApi.getPolicy(selectedPolicyId);
          } catch (error) {
            detailError = normalizeApiError(error, 'Policy detail unavailable.');
          }
        }
        safeRender(
          renderPoliciesBody({
            filters,
            policies,
            selectedPolicyId,
            selectedPolicy,
            listError: null,
            detailError,
          }),
          (target) => bindPoliciesEvents(target, { policies, selectedPolicy })
        );
      } catch (error) {
        safeRender(
          renderPoliciesBody({
            filters,
            policies: [],
            selectedPolicyId: route.policyId || '',
            selectedPolicy: null,
            listError: normalizeApiError(error, 'Policies unavailable.'),
            detailError: null,
          }),
          (target) => bindPoliciesEvents(target, { policies: [], selectedPolicy: null })
        );
      }
      return;
    }

    if (route.subtab === 'quality-rules') {
      safeRender(renderLoadingState('Loading Quality Rules...'));
      const filters = currentQualityRuleFilters();
      try {
        const rules = await ctx.adminApi.listQualityRules(
          filters.tableQuery ? { table: filters.tableQuery } : {}
        );
        let selectedRuleId = route.ruleId || '';
        if (!selectedRuleId && rules.length) {
          selectedRuleId = rules[0].rule_id;
        }
        if (selectedRuleId && route.ruleId !== selectedRuleId) {
          ctx.applyAdminRoute(
            { ...route, tab: 'governance', subtab: 'quality-rules', ruleId: selectedRuleId },
            'replace'
          );
          return;
        }
        const selectedRule = rules.find((item) => item.rule_id === selectedRuleId) || null;
        safeRender(
          renderQualityRulesBody({
            filters,
            rules,
            selectedRuleId,
            selectedRule,
            listError: null,
          }),
          (target) => bindQualityRuleEvents(target, { rules, selectedRule })
        );
      } catch (error) {
        safeRender(
          renderQualityRulesBody({
            filters,
            rules: [],
            selectedRuleId: route.ruleId || '',
            selectedRule: null,
            listError: normalizeApiError(error, 'Quality Rules unavailable.'),
          }),
          (target) => bindQualityRuleEvents(target, { rules: [], selectedRule: null })
        );
      }
      return;
    }

    safeRender(renderLoadingState('Loading Approvals...'));
    const filters = currentApprovalFilters(route);
    try {
      const params = {};
      if (filters.status && filters.status !== 'all') params.status = filters.status;
      if (filters.sessionId) params.session_id = filters.sessionId;
      const approvals = await ctx.adminApi.listApprovals(params);
      let selectedRequestId = route.requestId || '';
      if (!selectedRequestId && approvals.length) {
        selectedRequestId = approvals[0].request_id;
      }
      if (selectedRequestId && route.requestId !== selectedRequestId) {
        const selectedFromList =
          approvals.find((item) => item.request_id === selectedRequestId) || approvals[0] || null;
        ctx.applyAdminRoute(
          {
            ...route,
            tab: 'governance',
            subtab: 'approvals',
            requestId: selectedRequestId,
            sessionId: selectedFromList?.session_id || route.sessionId || filters.sessionId || '',
          },
          'replace'
        );
        return;
      }
      let selectedApproval = null;
      let detailError = null;
      if (selectedRequestId) {
        try {
          selectedApproval = await ctx.adminApi.getApproval(selectedRequestId);
        } catch (error) {
          detailError = normalizeApiError(error, 'Approval detail unavailable.');
        }
      }
      safeRender(
        renderApprovalsBody({
          filters,
          approvals,
          selectedRequestId,
          selectedApproval,
          listError: null,
          detailError,
        }),
        (target) => bindApprovalsEvents(target, { approvals, selectedApproval })
      );
    } catch (error) {
      safeRender(
        renderApprovalsBody({
          filters,
          approvals: [],
          selectedRequestId: route.requestId || '',
          selectedApproval: null,
          listError: normalizeApiError(error, 'Approvals unavailable.'),
          detailError: null,
        }),
        (target) => bindApprovalsEvents(target, { approvals: [], selectedApproval: null })
      );
    }
  }

  function bindPoliciesEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="refresh-governance"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentGovernance());
    });
    panel.querySelectorAll('[data-action="select-policy"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'policies',
            policyId: button.dataset.policyId || '',
          },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="open-create-policy"]').forEach((button) => {
      button.addEventListener('click', () => {
        const form = panel.querySelector('[data-role="create-policy-form"] input[name="name"]');
        if (form) form.focus();
      });
    });
    const policyFilterForm = panel.querySelector('[data-role="policy-filters-form"]');
    if (policyFilterForm) {
      policyFilterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(policyFilterForm);
        setPolicyFilters({ enabled: String(formData.get('enabled') || 'all') });
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'policies',
            policyId: '',
          },
          'replace'
        );
      });
    }
    panel.querySelectorAll('[data-action="clear-policy-filters"]').forEach((button) => {
      button.addEventListener('click', () => {
        setPolicyFilters({ enabled: 'all' });
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'policies',
            policyId: '',
          },
          'replace'
        );
      });
    });
    const createPolicyForm = panel.querySelector('[data-role="create-policy-form"]');
    if (createPolicyForm) {
      createPolicyForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleCreatePolicy(createPolicyForm);
      });
    }
    const updatePolicyForm = panel.querySelector('[data-role="update-policy-form"]');
    if (updatePolicyForm) {
      updatePolicyForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const policyId = updatePolicyForm.dataset.policyId || '';
        if (policyId) void handleUpdatePolicy(updatePolicyForm, policyId);
      });
    }
    panel.querySelectorAll('[data-action="toggle-policy"]').forEach((button) => {
      button.addEventListener('click', () => {
        const policy = viewModel.policies.find((item) => item.policy_id === button.dataset.policyId);
        if (policy) handleTogglePolicy(policy);
      });
    });
    panel.querySelectorAll('[data-action="delete-policy"]').forEach((button) => {
      button.addEventListener('click', () => {
        const policy = viewModel.policies.find((item) => item.policy_id === button.dataset.policyId);
        if (policy) handleDeletePolicy(policy);
      });
    });
  }

  function bindQualityRuleEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="refresh-governance"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentGovernance());
    });
    panel.querySelectorAll('[data-action="select-quality-rule"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'quality-rules',
            ruleId: button.dataset.ruleId || '',
          },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="open-create-quality-rule"]').forEach((button) => {
      button.addEventListener('click', () => {
        const form = panel.querySelector('[data-role="create-quality-rule-form"] input[name="name"]');
        if (form) form.focus();
      });
    });
    const filterForm = panel.querySelector('[data-role="quality-rule-filters-form"]');
    if (filterForm) {
      filterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(filterForm);
        setQualityRuleFilters({ tableQuery: String(formData.get('table_query') || '') });
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'quality-rules',
            ruleId: '',
          },
          'replace'
        );
      });
    }
    panel.querySelectorAll('[data-action="clear-quality-rule-filters"]').forEach((button) => {
      button.addEventListener('click', () => {
        setQualityRuleFilters({ tableQuery: '' });
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'quality-rules',
            ruleId: '',
          },
          'replace'
        );
      });
    });
    const createForm = panel.querySelector('[data-role="create-quality-rule-form"]');
    if (createForm) {
      createForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleCreateQualityRule(createForm);
      });
    }
    panel.querySelectorAll('[data-action="delete-quality-rule"]').forEach((button) => {
      button.addEventListener('click', () => {
        const rule = viewModel.rules.find((item) => item.rule_id === button.dataset.ruleId);
        if (rule) handleDeleteQualityRule(rule);
      });
    });
  }

  function bindApprovalsEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="refresh-governance"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentGovernance());
    });
    panel.querySelectorAll('[data-action="select-approval"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'approvals',
            requestId: button.dataset.requestId || '',
            sessionId: button.dataset.sessionId || '',
          },
          'push'
        );
      });
    });
    const filterForm = panel.querySelector('[data-role="approval-filters-form"]');
    if (filterForm) {
      filterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const formData = new FormData(filterForm);
        const nextFilters = {
          status: String(formData.get('status') || 'pending'),
          sessionId: String(formData.get('session_id') || ''),
        };
        setApprovalFilters(nextFilters);
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'approvals',
            requestId: '',
            sessionId: nextFilters.sessionId,
          },
          'replace'
        );
      });
    }
    panel.querySelectorAll('[data-action="clear-approval-filters"]').forEach((button) => {
      button.addEventListener('click', () => {
        setApprovalFilters({ status: 'pending', sessionId: '' });
        ctx.applyAdminRoute(
          {
            ...ctx.getCurrentRoute(),
            tab: 'governance',
            subtab: 'approvals',
            requestId: '',
            sessionId: '',
          },
          'replace'
        );
      });
    });
    const decisionForm = panel.querySelector('[data-role="approval-decision-form"]');
    if (decisionForm) {
      decisionForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const submitter = event.submitter;
        const decision = submitter?.dataset?.decision || 'approve';
        if (!viewModel.selectedApproval) return;
        const formData = new FormData(decisionForm);
        handleApprovalDecision(
          viewModel.selectedApproval,
          {
            reviewer: String(formData.get('reviewer') || '').trim(),
            reason: String(formData.get('reason') || '').trim(),
          },
          decision
        );
      });
    }
    const autoFlagForm = panel.querySelector('[data-role="auto-flag-form"]');
    if (autoFlagForm) {
      autoFlagForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleAutoFlag(autoFlagForm);
      });
    }
  }

  function bindHelpersEvents(panel) {
    const governanceCheckForm = panel.querySelector('[data-role="governance-check-form"]');
    if (governanceCheckForm) {
      governanceCheckForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleGovernanceCheck(governanceCheckForm);
      });
    }
    const routingResolveForm = panel.querySelector('[data-role="routing-resolve-form"]');
    if (routingResolveForm) {
      routingResolveForm.addEventListener('submit', (event) => {
        event.preventDefault();
        void handleRoutingResolve(routingResolveForm);
      });
    }
  }

  return { render, hydrate };
}
