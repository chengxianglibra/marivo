export function createExecutionEnginesModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    closeModal,
    openModal,
    toast,
    renderEmptyState,
    renderErrorState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    formatKeyValueSummary,
    statusBadge,
    openDangerConfirm,
  } = shared;

  let executionEnginesRenderVersion = 0;
  const executionEnginesUiState = {
    sourceEngineErrors: {},
  };

  function summarizeConnection(config) {
    return formatKeyValueSummary(config);
  }

  function buildEngineBindingsByEngine(bindings) {
    return (bindings || []).reduce((acc, binding) => {
      const engineId = String(binding?.engine_id || '');
      if (!engineId) return acc;
      if (!acc[engineId]) acc[engineId] = [];
      acc[engineId].push(binding);
      return acc;
    }, {});
  }

  function buildEngineListRows(engines, selectedEngineId) {
    if (!engines.length) {
      return `
        <tr>
          <td colspan="4">${renderEmptyState('No execution engines configured yet.', '<button type="button" class="btn btn-primary" data-action="create-engine">Create Engine</button>')}</td>
        </tr>
      `;
    }
    return engines.map((engine) => `
      <tr class="${engine.engine_id === selectedEngineId ? 'is-selected' : ''}">
        <td>
          <button type="button" class="selectable-list-item ${engine.engine_id === selectedEngineId ? 'is-active' : ''}" data-action="select-engine" data-engine-id="${esc(engine.engine_id)}">
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(engine.engine_id)}</span>
              <span class="selectable-list-meta">${esc(engine.display_name || 'Unnamed Engine')}</span>
            </span>
            ${statusBadge(engine.status)}
          </button>
        </td>
        <td>${esc(engine.display_name || '-')}</td>
        <td>${esc(engine.engine_type || '-')}</td>
        <td>${statusBadge(engine.status)}</td>
      </tr>
    `).join('');
  }

  function buildBindingListRows(bindings, selectedBindingId) {
    if (!bindings.length) {
      return `
        <tr>
          <td colspan="5">${renderEmptyState('No source-engine bindings configured yet.', '<button type="button" class="btn btn-primary" data-action="create-binding">Create Binding</button>')}</td>
        </tr>
      `;
    }
    return bindings.map((binding) => `
      <tr class="${binding.binding_id === selectedBindingId ? 'is-selected' : ''}">
        <td>
          <button type="button" class="selectable-list-item ${binding.binding_id === selectedBindingId ? 'is-active' : ''}" data-action="select-binding" data-binding-id="${esc(binding.binding_id)}">
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(binding.binding_id)}</span>
              <span class="selectable-list-meta">${esc(binding.source_id)} → ${esc(binding.engine_id)}</span>
            </span>
            ${statusBadge(binding.status)}
          </button>
        </td>
        <td>${esc(binding.source_id || '-')}</td>
        <td>${esc(binding.engine_id || '-')}</td>
        <td>${esc(String(binding.priority ?? 0))}</td>
        <td>${statusBadge(binding.status)}</td>
      </tr>
    `).join('');
  }

  function renderEngineListCard(viewModel) {
    return renderAdminTableCard({
      title: 'Engine Inventory',
      count: viewModel.engines.length,
      countLabel: 'engine(s)',
      note: 'GET /engines lists execution engines only. Create Engine registers the execution backend and does not create any semantic typed binding.',
      columns: ['engine_id', 'display_name', 'engine_type', 'status'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn btn-primary" data-action="create-engine">Create Engine</button>
          <button type="button" class="btn" data-action="refresh-execution-engines">Refresh</button>
        </div>
      `,
      rowsHtml: buildEngineListRows(viewModel.engines, viewModel.selectedEngineId),
      errorHtml: viewModel.listError ? renderStructuredError(viewModel.listError, 'Execution Engines unavailable.') : '',
    });
  }

  function renderBindingListCard(viewModel) {
    return renderAdminTableCard({
      title: 'Binding Inventory',
      count: viewModel.bindings.length,
      countLabel: 'binding(s)',
      note: 'GET /bindings manages execution engine bindings only. Semantic typed bindings live in Semantic Catalog and must not be mixed into this list.',
      columns: ['binding_id', 'source_id', 'engine_id', 'priority', 'status'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn btn-primary" data-action="create-binding">Create Binding</button>
          <button type="button" class="btn" data-action="refresh-execution-engines">Refresh</button>
        </div>
      `,
      rowsHtml: buildBindingListRows(viewModel.bindings, viewModel.selectedBindingId),
      errorHtml: '',
    });
  }

  function renderEngineSummaryCard(engine, engineBindings, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Engine Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /engines/{engine_id} is the canonical engine detail endpoint.',
        bodyHtml: renderStructuredError(detailError, 'Engine detail unavailable.'),
      });
    }
    if (!engine) {
      return renderAdminDetailCard({
        title: 'Engine Summary',
        statusHtml: '<span class="shell-chip">no engine selected</span>',
        note: 'Select an engine from Engine Inventory to inspect connection, capabilities, and binding coverage.',
        bodyHtml: renderEmptyState('Select an engine to inspect execution backend configuration and binding coverage.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Engine Summary',
      statusHtml: statusBadge(engine.status),
      note: 'GET /engines/{engine_id} provides the canonical detail for execution engine inventory.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'engine_id', value: engine.engine_id },
          { label: 'display_name', value: engine.display_name || '-' },
          { label: 'engine_type', value: engine.engine_type || '-' },
          { label: 'status', valueHtml: statusBadge(engine.status) },
          { label: 'active bindings', value: String((engineBindings || []).length) },
          { label: 'bound sources', value: String(new Set((engineBindings || []).map((item) => item.source_id)).size) },
          { label: 'connection', value: summarizeConnection(engine.connection) },
          { label: 'capabilities', value: summarizeConnection(engine.capabilities) },
        ])}
        ${renderJsonPanel('Engine JSON', engine, 'No engine payload.')}
      `,
    });
  }

  function renderBindingSummaryCard(binding, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Binding Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /bindings/{binding_id} returns the canonical execution binding payload.',
        bodyHtml: renderStructuredError(detailError, 'Binding detail unavailable.'),
      });
    }
    if (!binding) {
      return renderAdminDetailCard({
        title: 'Binding Summary',
        statusHtml: '<span class="shell-chip">no binding selected</span>',
        note: 'Select a binding to inspect source-engine routing priority and deletion controls.',
        bodyHtml: renderEmptyState('Select a binding to inspect source_id, engine_id, priority, and namespace.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Binding Summary',
      statusHtml: statusBadge(binding.status),
      note: 'Execution engine bindings connect a source to an execution backend. Semantic typed bindings are managed in Semantic Catalog.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'binding_id', value: binding.binding_id },
          { label: 'source_id', value: binding.source_id || '-' },
          { label: 'engine_id', value: binding.engine_id || '-' },
          { label: 'priority', value: String(binding.priority ?? 0) },
          { label: 'status', valueHtml: statusBadge(binding.status) },
          { label: 'namespace', value: summarizeConnection(binding.namespace) },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-danger" data-action="delete-binding" data-binding-id="${esc(binding.binding_id)}">Delete Binding</button>
        </div>
        ${renderJsonPanel('Binding JSON', binding, 'No binding payload.')}
      `,
    });
  }

  function renderSourceEngineRelationshipsCard(binding, sourceEngines, sourceEngineError) {
    if (!binding) {
      return renderAdminDetailCard({
        title: 'Source-engine Relationship',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Select a binding to inspect GET /sources/{source_id}/engines relationship coverage.',
        bodyHtml: renderEmptyState('No binding selected.'),
      });
    }
    if (sourceEngineError) {
      return renderAdminDetailCard({
        title: 'Source-engine Relationship',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /sources/{source_id}/engines shows all active execution backends for the selected source.',
        bodyHtml: renderStructuredError(sourceEngineError, 'Source-engine relationship unavailable.'),
      });
    }
    const relationshipHtml = (sourceEngines || []).length
      ? `
        <div class="compact-list">
          ${(sourceEngines || []).map((item) => `
            <div class="compact-list-item">
              <div class="compact-list-copy">
                <strong>${esc(item.engine_id)}</strong>
                <span>${esc(item.display_name || 'Unnamed Engine')} · ${esc(item.engine_type || '-')}</span>
              </div>
              <span class="shell-chip">priority ${esc(String(item.priority ?? 0))}</span>
            </div>
          `).join('')}
        </div>
      `
      : renderEmptyState('No active execution engines are bound to this source.');
    return renderAdminDetailCard({
      title: 'Source-engine Relationship',
      statusHtml: `<span class="shell-chip">${esc(binding.source_id)}</span>`,
      note: 'GET /sources/{source_id}/engines returns the active execution engines for this source. Use it to verify routing coverage without confusing it with semantic typed bindings.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'source_id', value: binding.source_id || '-' },
          { label: 'selected binding', value: binding.binding_id || '-' },
          { label: 'selected engine', value: binding.engine_id || '-' },
          { label: 'active engines on source', value: String((sourceEngines || []).length) },
        ])}
        ${relationshipHtml}
      `,
    });
  }

  function renderExecutionBindingContractCard() {
    return renderAdminDetailCard({
      title: 'Execution Binding Contract',
      statusHtml: '<span class="shell-chip">execution only</span>',
      note: 'This page manages execution engine bindings only and must not be used as a semantic typed binding editor.',
      bodyHtml: `
        <div class="overview-mini-list">
          <div class="overview-mini-item">
            <strong>HTTP contracts</strong>
            GET /engines, GET /engines/{engine_id}, GET /bindings, GET /bindings/{binding_id}, DELETE /bindings/{binding_id}, GET /sources/{source_id}/engines
          </div>
          <div class="overview-mini-item">
            <strong>Execution engine binding</strong>
            Connects a source to an execution engine so routing can resolve where physical data is executed.
          </div>
          <div class="overview-mini-item">
            <strong>Not a typed binding</strong>
            Semantic typed bindings belong to Semantic Catalog and should not share list semantics or deletion flows with execution bindings.
          </div>
        </div>
      `,
    });
  }

  function renderBody(viewModel) {
    return `
      <div class="data-sources-page">
        ${renderAdminListDetailLayout({
          primaryHtml: renderEngineListCard(viewModel),
          secondaryHtml: viewModel.listError ? '' : renderBindingListCard(viewModel),
          detailHtml: `
            <div class="data-sources-detail-stack">
              ${renderEngineSummaryCard(
                viewModel.selectedEngine,
                viewModel.engineBindingsByEngine[viewModel.selectedEngine?.engine_id || ''] || [],
                viewModel.engineDetailError
              )}
              ${renderBindingSummaryCard(viewModel.selectedBinding, viewModel.bindingDetailError)}
              ${renderSourceEngineRelationshipsCard(
                viewModel.selectedBinding,
                viewModel.selectedSourceEngines,
                viewModel.sourceEngineError
              )}
              ${renderExecutionBindingContractCard()}
            </div>
          `,
        })}
      </div>
    `;
  }

  function render() {
    return `<div data-role="execution-engines-body">${renderBody({
      engines: [],
      bindings: [],
      sources: [],
      selectedEngineId: '',
      selectedBindingId: '',
      selectedEngine: null,
      selectedBinding: null,
      selectedSourceEngines: [],
      sourceEngineError: null,
      listError: null,
      engineDetailError: null,
      bindingDetailError: null,
      engineBindingsByEngine: {},
    })}</div>`;
  }

  function ensureEngineFormModal() {
    let overlay = document.getElementById('engine-form-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'engine-form-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="engine-form-title">
        <div class="modal-card-stack">
          <div class="shell-card-title">
            <h3 id="engine-form-title">Create Engine</h3>
            <span class="shell-chip">execution backend</span>
          </div>
          <p class="panel-note">POST /engines registers a new execution engine. This does not create any source-engine binding or semantic typed binding.</p>
          <form class="source-form-grid" data-role="form">
            <label>
              Engine Type
              <input name="engine_type" type="text" placeholder="duckdb" />
            </label>
            <label>
              Display Name
              <input name="display_name" type="text" placeholder="Local Demo Engine" />
            </label>
            <label>
              Connection JSON
              <textarea name="connection_json" placeholder="{&#10;  &quot;path&quot;: &quot;/tmp/demo-engine.duckdb&quot;&#10;}"></textarea>
            </label>
            <label>
              Capabilities JSON
              <textarea name="capabilities_json" placeholder="{&#10;  &quot;supports_federation&quot;: true&#10;}"></textarea>
            </label>
            <div class="detail-error" data-role="error" style="display:none;"></div>
            <div class="detail-actions">
              <button type="button" class="btn" data-role="cancel">Cancel</button>
              <button type="submit" class="btn btn-primary" data-role="submit">Create Engine</button>
            </div>
          </form>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target?.dataset?.role === 'cancel') {
        closeModal('engine-form-modal');
      }
    });
    return overlay;
  }

  function ensureBindingFormModal() {
    let overlay = document.getElementById('binding-form-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'binding-form-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="binding-form-title">
        <div class="modal-card-stack">
          <div class="shell-card-title">
            <h3 id="binding-form-title">Create Binding</h3>
            <span class="shell-chip">execution engine binding</span>
          </div>
          <p class="panel-note" data-role="copy">Create Binding connects a source to an execution engine. This flow manages execution engine bindings only; semantic typed bindings stay in Semantic Catalog.</p>
          <form class="source-form-grid" data-role="form">
            <label>
              Source
              <select name="source_id"></select>
            </label>
            <label>
              Engine
              <select name="engine_id"></select>
            </label>
            <label>
              Priority
              <input name="priority" type="number" min="0" step="1" value="0" />
            </label>
            <details>
              <summary>Advanced Namespace JSON</summary>
              <textarea name="namespace_json" placeholder="{&#10;  &quot;catalog&quot;: &quot;hive&quot;,&#10;  &quot;schema&quot;: &quot;prod&quot;&#10;}"></textarea>
            </details>
            <div class="detail-error" data-role="error" style="display:none;"></div>
            <div class="detail-actions">
              <button type="button" class="btn" data-role="cancel">Cancel</button>
              <button type="submit" class="btn btn-primary" data-role="submit">Create Binding</button>
            </div>
          </form>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target?.dataset?.role === 'cancel') {
        closeModal('binding-form-modal');
      }
    });
    return overlay;
  }

  function refreshCurrentExecutionEngines() {
    const panel = document.getElementById('panel-execution-engines');
    const route = ctx.getCurrentRoute();
    if (panel && route.tab === 'execution-engines') {
      void hydrate(panel, route);
    }
  }

  async function openEngineFormModal() {
    const overlay = ensureEngineFormModal();
    const form = overlay.querySelector('[data-role="form"]');
    const errorBox = overlay.querySelector('[data-role="error"]');
    const engineTypeInput = form.querySelector('[name="engine_type"]');
    const displayNameInput = form.querySelector('[name="display_name"]');
    const connectionInput = form.querySelector('[name="connection_json"]');
    const capabilitiesInput = form.querySelector('[name="capabilities_json"]');

    engineTypeInput.value = 'duckdb';
    displayNameInput.value = '';
    connectionInput.value = JSON.stringify({ path: '/tmp/demo-engine.duckdb' }, null, 2);
    capabilitiesInput.value = JSON.stringify({}, null, 2);
    if (errorBox) {
      errorBox.style.display = 'none';
      errorBox.innerHTML = '';
    }

    form.onsubmit = async (event) => {
      event.preventDefault();
      let connection = {};
      let capabilities = {};
      try {
        connection = JSON.parse(connectionInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Connection JSON is invalid.');
        return;
      }
      try {
        capabilities = JSON.parse(capabilitiesInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Capabilities JSON is invalid.');
        return;
      }
      try {
        const created = await ctx.adminApi.createEngine({
          engine_type: engineTypeInput.value.trim(),
          display_name: displayNameInput.value.trim(),
          connection,
          capabilities,
        });
        toast('Engine created.', 'success');
        closeModal('engine-form-modal');
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: created.engine_id, bindingId: '' },
          'replace'
        );
      } catch (error) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderStructuredError(error, 'Create Engine failed.');
      }
    };

    openModal('engine-form-modal');
  }

  async function openBindingFormModal(sources, engines) {
    const overlay = ensureBindingFormModal();
    const form = overlay.querySelector('[data-role="form"]');
    const errorBox = overlay.querySelector('[data-role="error"]');
    const copy = overlay.querySelector('[data-role="copy"]');
    const submit = overlay.querySelector('[data-role="submit"]');
    const sourceSelect = form.querySelector('[name="source_id"]');
    const engineSelect = form.querySelector('[name="engine_id"]');
    const priorityInput = form.querySelector('[name="priority"]');
    const namespaceInput = form.querySelector('[name="namespace_json"]');

    sourceSelect.innerHTML = (sources || []).map((source) => `
      <option value="${esc(source.source_id)}">${esc(source.display_name || source.source_id)} · ${esc(source.source_id)}</option>
    `).join('');
    engineSelect.innerHTML = (engines || []).map((engine) => `
      <option value="${esc(engine.engine_id)}">${esc(engine.display_name || engine.engine_id)} · ${esc(engine.engine_type || '-')}</option>
    `).join('');
    priorityInput.value = '0';
    namespaceInput.value = JSON.stringify({}, null, 2);
    if (errorBox) {
      errorBox.style.display = 'none';
      errorBox.innerHTML = '';
    }

    const hasSources = Array.isArray(sources) && sources.length > 0;
    const hasEngines = Array.isArray(engines) && engines.length > 0;
    if (!hasSources || !hasEngines) {
      submit.disabled = true;
      copy.textContent = !hasSources
        ? 'Create at least one data source before creating an execution binding.'
        : 'Create at least one execution engine before creating an execution binding.';
      if (errorBox) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderEmptyState(copy.textContent);
      }
    } else {
      submit.disabled = false;
      copy.textContent = 'Create Binding connects a source to an execution engine. This flow manages execution engine bindings only; semantic typed bindings stay in Semantic Catalog.';
    }

    form.onsubmit = async (event) => {
      event.preventDefault();
      let namespace = {};
      try {
        namespace = JSON.parse(namespaceInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Namespace JSON is invalid.');
        return;
      }
      try {
        const created = await ctx.adminApi.createBinding({
          source_id: sourceSelect.value,
          engine_id: engineSelect.value,
          priority: Number(priorityInput.value || 0),
          namespace,
        });
        toast('Binding created.', 'success');
        closeModal('binding-form-modal');
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: '', bindingId: created.binding_id },
          'replace'
        );
      } catch (error) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderStructuredError(error, 'Create Binding failed.');
      }
    };

    openModal('binding-form-modal');
  }

  function handleDeleteBinding(binding) {
    openDangerConfirm({
      title: 'Delete Binding',
      objectLabel: binding.binding_id,
      impactScope: 'Removes the source-engine routing association for this binding and may change engine resolution for the source.',
      reversible: 'No',
      confirmLabel: 'Delete Binding',
      detailsHtml: renderDetailList([
        { label: 'source_id', value: binding.source_id || '-' },
        { label: 'engine_id', value: binding.engine_id || '-' },
        { label: 'priority', value: String(binding.priority ?? 0) },
        { label: 'warning', value: 'This deletes an execution engine binding only. Semantic typed bindings are unaffected.' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.deleteBinding(binding.binding_id);
          toast('Binding deleted.', 'success');
          ctx.applyAdminRoute(
            { ...ctx.getCurrentRoute(), tab: 'execution-engines', bindingId: '', engineId: '' },
            'replace'
          );
        } catch (error) {
          toast(normalizeApiError(error, 'Delete Binding failed.').message, 'error');
          refreshCurrentExecutionEngines();
        }
      },
    });
  }

  async function hydrate(panel, route) {
    const renderVersion = ++executionEnginesRenderVersion;
    const safeRender = (viewModel) => {
      if (renderVersion !== executionEnginesRenderVersion) return;
      const target = panel.querySelector('[data-role="execution-engines-body"]');
      if (target) {
        target.innerHTML = renderBody(viewModel);
        bindEvents(panel, viewModel);
      }
    };

    safeRender({
      engines: [],
      bindings: [],
      sources: [],
      selectedEngineId: route.engineId || '',
      selectedBindingId: route.bindingId || '',
      selectedEngine: null,
      selectedBinding: null,
      selectedSourceEngines: [],
      sourceEngineError: null,
      listError: null,
      engineDetailError: null,
      bindingDetailError: null,
      engineBindingsByEngine: {},
    });

    try {
      const [engines, bindings, sources] = await Promise.all([
        ctx.adminApi.listEngines(),
        ctx.adminApi.listBindings(),
        ctx.adminApi.listSources(),
      ]);

      let selectedEngineId = engines.some((item) => item.engine_id === route.engineId) ? route.engineId : '';
      let selectedBindingId = bindings.some((item) => item.binding_id === route.bindingId) ? route.bindingId : '';
      if (!selectedEngineId && !selectedBindingId) {
        selectedEngineId = engines[0]?.engine_id || '';
        if (!selectedEngineId) {
          selectedBindingId = bindings[0]?.binding_id || '';
        }
      }

      if (route.engineId !== selectedEngineId || route.bindingId !== selectedBindingId) {
        ctx.applyAdminRoute({ ...route, engineId: selectedEngineId, bindingId: selectedBindingId }, 'replace');
        return;
      }

      let selectedEngine = null;
      let selectedBinding = null;
      let engineDetailError = null;
      let bindingDetailError = null;
      let selectedSourceEngines = [];
      let sourceEngineError = null;

      if (selectedEngineId) {
        try {
          selectedEngine = await ctx.adminApi.getEngine(selectedEngineId);
        } catch (error) {
          engineDetailError = normalizeApiError(error, 'Engine detail unavailable.');
        }
      }

      if (selectedBindingId) {
        try {
          selectedBinding = await ctx.adminApi.getBinding(selectedBindingId);
        } catch (error) {
          bindingDetailError = normalizeApiError(error, 'Binding detail unavailable.');
        }
      }

      if (selectedBinding?.source_id) {
        try {
          selectedSourceEngines = await ctx.adminApi.listSourceEngines(selectedBinding.source_id);
          delete executionEnginesUiState.sourceEngineErrors[selectedBinding.source_id];
        } catch (error) {
          sourceEngineError = normalizeApiError(error, 'Source-engine relationship unavailable.');
          executionEnginesUiState.sourceEngineErrors[selectedBinding.source_id] = sourceEngineError;
        }
      }

      safeRender({
        engines,
        bindings,
        sources,
        selectedEngineId,
        selectedBindingId,
        selectedEngine,
        selectedBinding,
        selectedSourceEngines,
        sourceEngineError,
        listError: null,
        engineDetailError,
        bindingDetailError,
        engineBindingsByEngine: buildEngineBindingsByEngine(bindings),
      });
    } catch (error) {
      safeRender({
        engines: [],
        bindings: [],
        sources: [],
        selectedEngineId: '',
        selectedBindingId: '',
        selectedEngine: null,
        selectedBinding: null,
        selectedSourceEngines: [],
        sourceEngineError: null,
        listError: normalizeApiError(error, 'Execution Engines unavailable.'),
        engineDetailError: null,
        bindingDetailError: null,
        engineBindingsByEngine: {},
      });
    }
  }

  function bindEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="refresh-execution-engines"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentExecutionEngines());
    });
    panel.querySelectorAll('[data-action="create-engine"]').forEach((button) => {
      button.addEventListener('click', () => {
        void openEngineFormModal();
      });
    });
    panel.querySelectorAll('[data-action="create-binding"]').forEach((button) => {
      button.addEventListener('click', () => {
        void openBindingFormModal(viewModel.sources, viewModel.engines);
      });
    });
    panel.querySelectorAll('[data-action="select-engine"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: button.dataset.engineId || '', bindingId: '' },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="select-binding"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: '', bindingId: button.dataset.bindingId || '' },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="delete-binding"]').forEach((button) => {
      button.addEventListener('click', () => {
        if (viewModel.selectedBinding) {
          handleDeleteBinding(viewModel.selectedBinding);
        }
      });
    });
  }

  return { render, hydrate };
}
