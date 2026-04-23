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

  function summarizeConnection(config) {
    return formatKeyValueSummary(config);
  }

  function extractItems(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
  }

  function buildEngineMappingsByEngine(mappings) {
    return (mappings || []).reduce((acc, mapping) => {
      const engineId = String(mapping?.engine_id || '');
      if (!engineId) return acc;
      if (!acc[engineId]) acc[engineId] = [];
      acc[engineId].push(mapping);
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

  function buildMappingListRows(mappings, selectedMappingId) {
    if (!mappings.length) {
      return `
        <tr>
          <td colspan="5">${renderEmptyState('No source-engine mappings configured yet.', '<button type="button" class="btn btn-primary" data-action="create-mapping">Create Mapping</button>')}</td>
        </tr>
      `;
    }
    return mappings.map((mapping) => `
      <tr class="${mapping.mapping_id === selectedMappingId ? 'is-selected' : ''}">
        <td>
          <button type="button" class="selectable-list-item ${mapping.mapping_id === selectedMappingId ? 'is-active' : ''}" data-action="select-mapping" data-mapping-id="${esc(mapping.mapping_id)}">
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(mapping.mapping_id)}</span>
              <span class="selectable-list-meta">${esc(mapping.source_id)} → ${esc(mapping.engine_id)}</span>
            </span>
            ${statusBadge(mapping.status)}
          </button>
        </td>
        <td>${esc(mapping.source_id || '-')}</td>
        <td>${esc(mapping.engine_id || '-')}</td>
        <td>${esc(String(mapping.priority ?? 0))}</td>
        <td>${statusBadge(mapping.status)}</td>
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

  function renderMappingListCard(viewModel) {
    return renderAdminTableCard({
      title: 'Mapping Inventory',
      count: viewModel.mappings.length,
      countLabel: 'mapping(s)',
      note: 'GET /mappings manages source-to-engine routing mappings only. Semantic typed bindings live in Semantic Catalog and must not be mixed into this list.',
      columns: ['mapping_id', 'source_id', 'engine_id', 'priority', 'status'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn btn-primary" data-action="create-mapping">Create Mapping</button>
          <button type="button" class="btn" data-action="refresh-execution-engines">Refresh</button>
        </div>
      `,
      rowsHtml: buildMappingListRows(viewModel.mappings, viewModel.selectedMappingId),
      errorHtml: '',
    });
  }

  function renderEngineSummaryCard(engine, engineMappings, detailError) {
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
        note: 'Select an engine from Engine Inventory to inspect connection, capabilities, and mapping coverage.',
        bodyHtml: renderEmptyState('Select an engine to inspect execution backend configuration and mapping coverage.'),
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
          { label: 'active mappings', value: String((engineMappings || []).length) },
          { label: 'mapped sources', value: String(new Set((engineMappings || []).map((item) => item.source_id)).size) },
          { label: 'connection', value: summarizeConnection(engine.connection) },
          { label: 'default_namespace', value: summarizeConnection(engine.default_namespace) },
          { label: 'intrinsic_capabilities', value: summarizeConnection(engine.intrinsic_capabilities) },
          { label: 'deployment_capabilities', value: summarizeConnection(engine.deployment_capabilities) },
          { label: 'policy', value: summarizeConnection(engine.policy) },
        ])}
        ${renderJsonPanel('Engine JSON', engine, 'No engine payload.')}
      `,
    });
  }

  function renderMappingSummaryCard(mapping, detailError) {
    if (detailError) {
      return renderAdminDetailCard({
        title: 'Mapping Summary',
        statusHtml: '<span class="shell-chip">error</span>',
        note: 'GET /mappings/{mapping_id} returns the canonical mapping payload.',
        bodyHtml: renderStructuredError(detailError, 'Mapping detail unavailable.'),
      });
    }
    if (!mapping) {
      return renderAdminDetailCard({
        title: 'Mapping Summary',
        statusHtml: '<span class="shell-chip">no mapping selected</span>',
        note: 'Select a mapping to inspect source-engine routing priority, catalog projection, and deletion controls.',
        bodyHtml: renderEmptyState('Select a mapping to inspect source_id, engine_id, priority, and catalog_mappings.'),
      });
    }
    return renderAdminDetailCard({
      title: 'Mapping Summary',
      statusHtml: statusBadge(mapping.status),
      note: 'Source-to-engine mappings connect authority catalogs to execution catalogs. Semantic typed bindings are managed in Semantic Catalog.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'mapping_id', value: mapping.mapping_id },
          { label: 'source_id', value: mapping.source_id || '-' },
          { label: 'engine_id', value: mapping.engine_id || '-' },
          { label: 'priority', value: String(mapping.priority ?? 0) },
          { label: 'status', valueHtml: statusBadge(mapping.status) },
          { label: 'readiness_status', value: mapping.readiness_status || '-' },
          { label: 'failure_code', value: mapping.failure_code || '-' },
          { label: 'catalog_mappings', value: summarizeConnection(mapping.catalog_mappings) },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-danger" data-action="delete-mapping" data-mapping-id="${esc(mapping.mapping_id)}">Delete Mapping</button>
        </div>
        ${renderJsonPanel('Mapping JSON', mapping, 'No mapping payload.')}
      `,
    });
  }

  function renderRoutingContractCard(viewModel) {
    const mapping = viewModel.selectedMapping;
    return renderAdminDetailCard({
      title: 'Routing & Mapping Contract',
      statusHtml: '<span class="shell-chip">mapping only</span>',
      note: 'This page manages source-to-engine mappings only. Typed semantic bindings remain in Semantic Catalog.',
      bodyHtml: `
        ${renderDetailList([
          { label: 'selected mapping', value: mapping?.mapping_id || '-' },
          { label: 'selected source', value: mapping?.source_id || '-' },
          { label: 'selected engine', value: mapping?.engine_id || '-' },
          { label: 'routing detail', value: 'Use POST /routing/resolve when you need a concrete selection_reason and routing_detail for a table set.' },
        ])}
        <div class="overview-mini-list">
          <div class="overview-mini-item">
            <strong>HTTP contracts</strong>
            GET /engines, GET /engines/{engine_id}, GET /mappings, GET /mappings/{mapping_id}, POST /mappings, PUT /mappings/{mapping_id}, DELETE /mappings/{mapping_id}, POST /routing/resolve
          </div>
          <div class="overview-mini-item">
            <strong>Source-to-engine mapping</strong>
            Connects authority catalogs to execution catalogs so routing and compile can resolve execution locators without guessing.
          </div>
          <div class="overview-mini-item">
            <strong>Not a typed binding</strong>
            Semantic typed bindings belong to Semantic Catalog and should not share list semantics or deletion flows with source-to-engine mappings.
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
          secondaryHtml: viewModel.listError ? '' : renderMappingListCard(viewModel),
          detailHtml: `
            <div class="data-sources-detail-stack">
              ${renderEngineSummaryCard(
                viewModel.selectedEngine,
                viewModel.engineMappingsByEngine[viewModel.selectedEngine?.engine_id || ''] || [],
                viewModel.engineDetailError
              )}
              ${renderMappingSummaryCard(viewModel.selectedMapping, viewModel.mappingDetailError)}
              ${renderRoutingContractCard(viewModel)}
            </div>
          `,
        })}
      </div>
    `;
  }

  function render() {
    return `<div data-role="execution-engines-body">${renderBody({
      engines: [],
      mappings: [],
      sources: [],
      selectedEngineId: '',
      selectedMappingId: '',
      selectedEngine: null,
      selectedMapping: null,
      listError: null,
      engineDetailError: null,
      mappingDetailError: null,
      engineMappingsByEngine: {},
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
          <p class="panel-note">POST /engines registers a new execution engine. This does not create any source-to-engine mapping or semantic typed binding.</p>
          <form class="source-form-grid" data-role="form">
            <label>
              Engine Type
              <select name="engine_type">
                <option value="duckdb">duckdb</option>
                <option value="trino">trino</option>
              </select>
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
              Default Namespace JSON
              <textarea name="default_namespace_json" placeholder="{&#10;  &quot;catalog&quot;: null,&#10;  &quot;schema&quot;: null&#10;}"></textarea>
            </label>
            <label>
              Deployment Capabilities JSON
              <textarea name="deployment_capabilities_json" placeholder="{&#10;  &quot;supported_step_types&quot;: [&quot;metric_query&quot;]&#10;}"></textarea>
            </label>
            <label>
              Policy JSON
              <textarea name="policy_json" placeholder="{&#10;  &quot;allowed_step_types&quot;: [],&#10;  &quot;required_policy_support&quot;: []&#10;}"></textarea>
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

  function ensureMappingFormModal() {
    let overlay = document.getElementById('mapping-form-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'mapping-form-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="mapping-form-title">
        <div class="modal-card-stack">
          <div class="shell-card-title">
            <h3 id="mapping-form-title">Create Mapping</h3>
            <span class="shell-chip">authority to execution</span>
          </div>
          <p class="panel-note" data-role="copy">Create Mapping connects a source to an execution engine through explicit catalog_mappings. Semantic typed bindings stay in Semantic Catalog.</p>
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
            <label>
              Status
              <select name="status">
                <option value="active">active</option>
                <option value="inactive">inactive</option>
                <option value="deprecated">deprecated</option>
              </select>
            </label>
            <label>
              Catalog Mappings JSON
              <textarea name="catalog_mappings_json" placeholder="[{&#10;  &quot;authority_catalog&quot;: &quot;main&quot;,&#10;  &quot;execution_catalog&quot;: &quot;duckdb_runtime&quot;,&#10;  &quot;default_schema&quot;: null&#10;}]"></textarea>
            </label>
            <div class="detail-error" data-role="error" style="display:none;"></div>
            <div class="detail-actions">
              <button type="button" class="btn" data-role="cancel">Cancel</button>
              <button type="submit" class="btn btn-primary" data-role="submit">Create Mapping</button>
            </div>
          </form>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target?.dataset?.role === 'cancel') {
        closeModal('mapping-form-modal');
      }
    });
    return overlay;
  }

  function refreshCurrentExecutionEngines() {
    const panel = document.getElementById('panel-execution-engines');
    const route = ctx.getCurrentRoute();
    if (panel && route?.tab === 'execution-engines') {
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
    const defaultNamespaceInput = form.querySelector('[name="default_namespace_json"]');
    const deploymentCapabilitiesInput = form.querySelector('[name="deployment_capabilities_json"]');
    const policyInput = form.querySelector('[name="policy_json"]');

    engineTypeInput.value = 'duckdb';
    displayNameInput.value = '';
    connectionInput.value = JSON.stringify({ path: '/tmp/demo-engine.duckdb' }, null, 2);
    defaultNamespaceInput.value = JSON.stringify({ catalog: null, schema: null }, null, 2);
    deploymentCapabilitiesInput.value = JSON.stringify({}, null, 2);
    policyInput.value = JSON.stringify({ allowed_step_types: [], required_policy_support: [] }, null, 2);
    if (errorBox) {
      errorBox.style.display = 'none';
      errorBox.innerHTML = '';
    }

    form.onsubmit = async (event) => {
      event.preventDefault();
      let connection = {};
      let defaultNamespace = {};
      let deploymentCapabilities = {};
      let policy = {};
      try {
        connection = JSON.parse(connectionInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Connection JSON is invalid.');
        return;
      }
      try {
        defaultNamespace = JSON.parse(defaultNamespaceInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Default Namespace JSON is invalid.');
        return;
      }
      try {
        deploymentCapabilities = JSON.parse(deploymentCapabilitiesInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Deployment Capabilities JSON is invalid.');
        return;
      }
      try {
        policy = JSON.parse(policyInput.value || '{}');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Policy JSON is invalid.');
        return;
      }
      try {
        const created = await ctx.adminApi.createEngine({
          engine_type: engineTypeInput.value.trim(),
          display_name: displayNameInput.value.trim(),
          connection,
          default_namespace: defaultNamespace,
          deployment_capabilities: deploymentCapabilities,
          policy,
        });
        toast('Engine created.', 'success');
        closeModal('engine-form-modal');
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: created.engine_id, mappingId: '' },
          'replace'
        );
      } catch (error) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderStructuredError(error, 'Create Engine failed.');
      }
    };

    openModal('engine-form-modal');
  }

  async function openMappingFormModal(sources, engines) {
    const overlay = ensureMappingFormModal();
    const form = overlay.querySelector('[data-role="form"]');
    const errorBox = overlay.querySelector('[data-role="error"]');
    const copy = overlay.querySelector('[data-role="copy"]');
    const submit = overlay.querySelector('[data-role="submit"]');
    const sourceSelect = form.querySelector('[name="source_id"]');
    const engineSelect = form.querySelector('[name="engine_id"]');
    const priorityInput = form.querySelector('[name="priority"]');
    const statusSelect = form.querySelector('[name="status"]');
    const catalogMappingsInput = form.querySelector('[name="catalog_mappings_json"]');

    sourceSelect.innerHTML = (sources || []).map((source) => `
      <option value="${esc(source.source_id)}">${esc(source.display_name || source.source_id)} · ${esc(source.source_id)}</option>
    `).join('');
    engineSelect.innerHTML = (engines || []).map((engine) => `
      <option value="${esc(engine.engine_id)}">${esc(engine.display_name || engine.engine_id)} · ${esc(engine.engine_type || '-')}</option>
    `).join('');
    priorityInput.value = '0';
    statusSelect.value = 'active';
    catalogMappingsInput.value = JSON.stringify(
      [{ authority_catalog: 'main', execution_catalog: 'duckdb_runtime', default_schema: null }],
      null,
      2
    );
    if (errorBox) {
      errorBox.style.display = 'none';
      errorBox.innerHTML = '';
    }

    const hasSources = Array.isArray(sources) && sources.length > 0;
    const hasEngines = Array.isArray(engines) && engines.length > 0;
    if (!hasSources || !hasEngines) {
      submit.disabled = true;
      copy.textContent = !hasSources
        ? 'Create at least one data source before creating a source-to-engine mapping.'
        : 'Create at least one execution engine before creating a source-to-engine mapping.';
      if (errorBox) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderEmptyState(copy.textContent);
      }
    } else {
      submit.disabled = false;
      copy.textContent = 'Create Mapping connects a source to an execution engine through explicit catalog_mappings. Semantic typed bindings stay in Semantic Catalog.';
    }

    form.onsubmit = async (event) => {
      event.preventDefault();
      let catalogMappings = [];
      try {
        catalogMappings = JSON.parse(catalogMappingsInput.value || '[]');
      } catch {
        errorBox.style.display = '';
        errorBox.innerHTML = renderErrorState('Catalog Mappings JSON is invalid.');
        return;
      }
      try {
        const created = await ctx.adminApi.createMapping({
          source_id: sourceSelect.value,
          engine_id: engineSelect.value,
          priority: Number(priorityInput.value || 0),
          status: statusSelect.value,
          catalog_mappings: catalogMappings,
        });
        toast('Mapping created.', 'success');
        closeModal('mapping-form-modal');
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: '', mappingId: created.mapping_id },
          'replace'
        );
      } catch (error) {
        errorBox.style.display = '';
        errorBox.innerHTML = renderStructuredError(error, 'Create Mapping failed.');
      }
    };

    openModal('mapping-form-modal');
  }

  function handleDeleteMapping(mapping) {
    openDangerConfirm({
      title: 'Delete Mapping',
      objectLabel: mapping.mapping_id,
      impactScope: 'Removes the source-to-engine routing association for this mapping and may change engine resolution for the source.',
      reversible: 'No',
      confirmLabel: 'Delete Mapping',
      detailsHtml: renderDetailList([
        { label: 'source_id', value: mapping.source_id || '-' },
        { label: 'engine_id', value: mapping.engine_id || '-' },
        { label: 'priority', value: String(mapping.priority ?? 0) },
        { label: 'warning', value: 'This deletes a source-to-engine mapping only. Semantic typed bindings are unaffected.' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.deleteMapping(mapping.mapping_id);
          toast('Mapping deleted.', 'success');
          ctx.applyAdminRoute(
            { ...ctx.getCurrentRoute(), tab: 'execution-engines', mappingId: '', engineId: '' },
            'replace'
          );
        } catch (error) {
          toast(normalizeApiError(error, 'Delete Mapping failed.').message, 'error');
          refreshCurrentExecutionEngines();
        }
      },
    });
  }

  async function hydrate(panel, route) {
    const renderVersion = ++executionEnginesRenderVersion;
    let lastEngines = [];
    let lastMappings = [];
    let lastSources = [];
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
      mappings: [],
      sources: [],
      selectedEngineId: route.engineId || '',
      selectedMappingId: route.mappingId || '',
      selectedEngine: null,
      selectedMapping: null,
      listError: null,
      engineDetailError: null,
      mappingDetailError: null,
      engineMappingsByEngine: {},
    });

    try {
      const [rawEngines, rawMappings, rawSources] = await Promise.all([
        ctx.adminApi.listEngines(),
        ctx.adminApi.listMappings(),
        ctx.adminApi.listSources(),
      ]);
      const engines = extractItems(rawEngines);
      const mappings = extractItems(rawMappings);
      const sources = extractItems(rawSources);
      lastEngines = engines;
      lastMappings = mappings;
      lastSources = sources;

      let selectedEngineId = engines.some((item) => item.engine_id === route.engineId) ? route.engineId : '';
      let selectedMappingId = mappings.some((item) => item.mapping_id === route.mappingId) ? route.mappingId : '';
      if (!selectedEngineId && !selectedMappingId) {
        selectedEngineId = engines[0]?.engine_id || '';
        if (!selectedEngineId) {
          selectedMappingId = mappings[0]?.mapping_id || '';
        }
      }

      safeRender({
        engines,
        mappings,
        sources,
        selectedEngineId: selectedEngineId,
        selectedMappingId: selectedMappingId,
        selectedEngine: null,
        selectedMapping: null,
        listError: null,
        engineDetailError: null,
        mappingDetailError: null,
        engineMappingsByEngine: buildEngineMappingsByEngine(mappings),
      });

      if (route.engineId !== selectedEngineId || route.mappingId !== selectedMappingId) {
        ctx.applyAdminRoute({ ...route, engineId: selectedEngineId, mappingId: selectedMappingId }, 'replace');
        return;
      }

      let selectedEngine = null;
      let selectedMapping = null;
      let engineDetailError = null;
      let mappingDetailError = null;

      if (selectedEngineId) {
        try {
          selectedEngine = await ctx.adminApi.getEngine(selectedEngineId);
        } catch (error) {
          engineDetailError = normalizeApiError(error, 'Engine detail unavailable.');
        }
      }

      if (selectedMappingId) {
        try {
          selectedMapping = await ctx.adminApi.getMapping(selectedMappingId);
        } catch (error) {
          mappingDetailError = normalizeApiError(error, 'Mapping detail unavailable.');
        }
      }

      safeRender({
        engines,
        mappings,
        sources,
        selectedEngineId,
        selectedMappingId,
        selectedEngine,
        selectedMapping,
        listError: null,
        engineDetailError,
        mappingDetailError,
        engineMappingsByEngine: buildEngineMappingsByEngine(mappings),
      });
    } catch (error) {
      safeRender({
        engines: lastEngines,
        mappings: lastMappings,
        sources: lastSources,
        selectedEngineId: route.engineId || '',
        selectedMappingId: route.mappingId || '',
        selectedEngine: null,
        selectedMapping: null,
        listError: normalizeApiError(error, 'Execution Engines unavailable.'),
        engineDetailError: null,
        mappingDetailError: null,
        engineMappingsByEngine: {},
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
    panel.querySelectorAll('[data-action="create-mapping"]').forEach((button) => {
      button.addEventListener('click', () => {
        void openMappingFormModal(viewModel.sources, viewModel.engines);
      });
    });
    panel.querySelectorAll('[data-action="select-engine"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: button.dataset.engineId || '', mappingId: '' },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="select-mapping"]').forEach((button) => {
      button.addEventListener('click', () => {
        ctx.applyAdminRoute(
          { ...ctx.getCurrentRoute(), tab: 'execution-engines', engineId: '', mappingId: button.dataset.mappingId || '' },
          'push'
        );
      });
    });
    panel.querySelectorAll('[data-action="delete-mapping"]').forEach((button) => {
      button.addEventListener('click', () => {
        if (viewModel.selectedMapping) {
          handleDeleteMapping(viewModel.selectedMapping);
        }
      });
    });
  }

  return { render, hydrate };
}
