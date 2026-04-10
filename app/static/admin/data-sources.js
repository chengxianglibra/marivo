export function createDataSourcesModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    closeModal,
    openModal,
    toast,
    renderEmptyState,
    renderLoadingState,
    renderErrorState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    pollAsync,
    formatKeyValueSummary,
    statusBadge,
    fmtDate,
    openDangerConfirm,
  } = shared;

  let dataSourcesRenderVersion = 0;
  const dataSourcesUiState = {
    syncJobs: {},
    syncErrors: {},
    catalog: {},
  };

  function sourceSyncState(sourceId) {
    return dataSourcesUiState.syncJobs[sourceId] || null;
  }

  function sourceCatalogState(sourceId) {
    return dataSourcesUiState.catalog[sourceId] || {
      schemas: [],
      selectedSchema: '',
      tables: [],
      error: null,
      tablesError: null,
    };
  }

  function sourceSelectionKey(schemaName, tableName) {
    return `${schemaName}.${tableName}`;
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

  function summarizeConnection(config) {
    return formatKeyValueSummary(config);
  }

  function tableCountLabel(items) {
    const count = Array.isArray(items) ? items.length : 0;
    return count === 1 ? '1 table' : `${count} tables`;
  }

  function buildSourceRouteHref(sourceId) {
    return `?tab=data-sources&source_id=${encodeURIComponent(sourceId)}`;
  }

  function buildSourceListRows(sources, selectedSourceId) {
    if (!sources.length) {
      return `
        <tr>
          <td colspan="6">${renderEmptyState('No data sources configured yet.', '<button type="button" class="btn btn-primary" data-action="create-source">Create Source</button>')}</td>
        </tr>
      `;
    }
    return sources.map((source) => `
      <tr
        class="${source.source_id === selectedSourceId ? 'is-selected' : ''} source-inventory-row"
        data-role="source-row"
        data-source-id="${esc(source.source_id)}"
        data-source-href="${esc(buildSourceRouteHref(source.source_id))}"
        tabindex="0"
      >
        <td>
          <a class="selectable-list-item ${source.source_id === selectedSourceId ? 'is-active' : ''}" data-action="select-source" data-source-id="${esc(source.source_id)}" href="${esc(buildSourceRouteHref(source.source_id))}">
            <span class="selectable-list-copy">
              <span class="selectable-list-title">${esc(source.source_id)}</span>
              <span class="selectable-list-meta">${esc(source.display_name || 'Unnamed Source')}</span>
            </span>
            ${statusBadge(source.status)}
          </a>
        </td>
        <td><a class="source-inventory-link" href="${esc(buildSourceRouteHref(source.source_id))}">${esc(source.display_name || '-')}</a></td>
        <td><a class="source-inventory-link" href="${esc(buildSourceRouteHref(source.source_id))}">${esc(source.source_type || '-')}</a></td>
        <td><a class="source-inventory-link source-inventory-link-status" href="${esc(buildSourceRouteHref(source.source_id))}">${statusBadge(source.status)}</a></td>
        <td><a class="source-inventory-link" href="${esc(buildSourceRouteHref(source.source_id))}">${esc(formatMaybeDate(source.last_sync_at))}</a></td>
        <td><a class="source-inventory-link" href="${esc(buildSourceRouteHref(source.source_id))}">${esc(formatMaybeDate(source.updated_at))}</a></td>
      </tr>
    `).join('');
  }

  function renderSourceListCard(viewModel) {
    return renderAdminTableCard({
      title: 'Source Inventory',
      count: viewModel.sources.length,
      countLabel: 'source(s)',
      note: 'GET /sources powers the inventory. last_sync_at is derived client-side from synced source objects and recent sync jobs.',
      columns: ['source_id', 'display_name', 'source_type', 'status', 'last_sync_at', 'updated_at'],
      actionsHtml: `
        <div class="data-sources-header-actions">
          <button type="button" class="btn btn-primary" data-action="create-source">Create Source</button>
          <button type="button" class="btn" data-action="refresh-sources">Refresh</button>
        </div>
      `,
      rowsHtml: buildSourceListRows(viewModel.sources, viewModel.selectedSourceId),
      errorHtml: viewModel.listError ? renderStructuredError(viewModel.listError, 'Data Sources unavailable.') : '',
    });
  }

  function renderSourceSummaryCard(source, sourceError) {
    if (!source) {
      return renderAdminDetailCard({
        title: 'Source Summary',
        statusHtml: '<span class="shell-chip">no source selected</span>',
        note: 'Create a source or select one from the inventory to inspect connection/config and lifecycle controls.',
        bodyHtml: renderEmptyState('Select a source to inspect connection/config summary, sync mode, and catalog entrypoints.'),
      });
    }
    const bodyParts = [
      renderDetailList([
        { label: 'source_id', value: source.source_id },
        { label: 'display_name', value: source.display_name || '-' },
        { label: 'source_type', value: source.source_type || '-' },
        { label: 'sync_mode', value: source.sync_mode || 'all' },
        { label: 'enabled/status', valueHtml: statusBadge(source.status) },
        { label: 'connection/config', value: summarizeConnection(source.connection) },
      ]),
      sourceError ? renderStructuredError(sourceError, 'Source Summary unavailable.') : '',
      `
        <div class="detail-actions">
          <button type="button" class="btn" data-action="edit-source" data-source-id="${esc(source.source_id)}">Edit Source</button>
          <button type="button" class="btn btn-danger" data-action="delete-source" data-source-id="${esc(source.source_id)}">Delete Source</button>
        </div>
      `,
      renderJsonPanel('Connection JSON', source.connection, 'No connection payload.'),
    ];
    return renderAdminDetailCard({
      title: 'Source Summary',
      statusHtml: statusBadge(source.status),
      note: 'GET /sources/{source_id} is the canonical detail fetch for source lifecycle metadata.',
      bodyHtml: bodyParts.join(''),
    });
  }

  function renderSourceSyncCard(source, tables, syncError) {
    if (!source) {
      return renderAdminDetailCard({
        title: 'Sync & Jobs',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Run Sync and recent sync job status appear after a source is selected.',
        bodyHtml: renderEmptyState('No source selected.'),
      });
    }
    const latestObject = pickLatest(tables || [], ['synced_at']);
    const recentJob = sourceSyncState(source.source_id);
    const statusHtml = recentJob
      ? statusBadge(recentJob.status || 'submitted')
      : '<span class="shell-chip">not started in current session</span>';
    const bodyParts = [
      renderDetailList([
        { label: 'sync_mode', value: source.sync_mode || 'all' },
        { label: 'recent job id', value: recentJob?.job_id || '-' },
        {
          label: 'recent job status',
          valueHtml: recentJob
            ? statusBadge(recentJob.status || 'submitted')
            : '<span class="shell-chip">No recent sync job state</span>',
        },
        { label: 'last_sync_at', value: formatMaybeDate(recentJob?.updated_at || latestObject?.synced_at || source.last_sync_at) },
        { label: 'synced tables', value: String((tables || []).length) },
      ]),
      syncError ? renderStructuredError(syncError, 'Sync request failed.') : '',
      `
        <div class="detail-actions">
          <button type="button" class="btn btn-primary" data-action="run-sync" data-source-id="${esc(source.source_id)}">Run Sync</button>
          <button type="button" class="btn" data-action="refresh-sources">Refresh status</button>
        </div>
      `,
    ];
    return renderAdminDetailCard({
      title: 'Sync & Jobs',
      statusHtml,
      note: 'POST /sources/{source_id}/sync starts sync. GET /sources/{source_id}/sync/{job_id} is only available for known job ids, so this page keeps recent sync state for jobs triggered here.',
      bodyHtml: bodyParts.join(''),
    });
  }

  function renderSelectionsCard(source, selections) {
    if (!source) {
      return renderAdminDetailCard({
        title: 'Sync Selections',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Selection management becomes available after a source is selected.',
        bodyHtml: renderEmptyState('No source selected.'),
      });
    }
    const listHtml = selections.length ? `
      <div class="compact-list">
        ${selections.map((selection) => `
          <div class="compact-list-item">
            <div class="compact-list-copy">
              <strong>${esc(selection.schema_name)}.${esc(selection.table_name)}</strong>
              <span>selection_id: ${esc(selection.selection_id)}</span>
            </div>
            <button type="button" class="btn btn-sm" data-action="delete-selection" data-source-id="${esc(source.source_id)}" data-selection-id="${esc(selection.selection_id)}">Remove</button>
          </div>
        `).join('')}
      </div>
    ` : renderEmptyState('No sync selections configured yet.');
    return renderAdminDetailCard({
      title: 'Sync Selections',
      statusHtml: `<span class="shell-chip">${esc(selections.length ? `${selections.length} configured` : 'empty')}</span>`,
      note: 'Manage Selections uses GET/POST/DELETE /sources/{source_id}/sync/selections and keeps source catalog browsing separate from semantic authoring.',
      bodyHtml: `
        ${listHtml}
        <div class="detail-actions">
          <button type="button" class="btn" data-action="manage-selections" data-source-id="${esc(source.source_id)}">Manage Selections</button>
          <button type="button" class="btn" data-action="clear-selections" data-source-id="${esc(source.source_id)}">Clear All</button>
        </div>
      `,
    });
  }

  function renderCatalogCard(source, catalogState) {
    if (!source) {
      return renderAdminDetailCard({
        title: 'Catalog Browser',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Browse Catalog loads live schemas and tables only after source selection.',
        bodyHtml: renderEmptyState('No source selected.'),
      });
    }
    const schemas = catalogState.schemas || [];
    const tables = catalogState.tables || [];
    const selectedSchema = catalogState.selectedSchema || '';
    let browserHtml = '';
    if (catalogState.error) {
      browserHtml = renderStructuredError(catalogState.error, 'Catalog schemas unavailable.');
    } else if (!schemas.length) {
      browserHtml = renderEmptyState('No schema available from the live catalog for this source.');
    } else {
      const tableBody = catalogState.tablesError
        ? renderStructuredError(catalogState.tablesError, 'Catalog tables unavailable.')
        : (!selectedSchema
          ? renderEmptyState('Select a schema to browse live tables.')
          : (!tables.length
            ? renderEmptyState('No table found for the selected schema.')
            : `
              <div class="catalog-table-list">
                ${tables.map((table) => `
                  <div class="catalog-table-item">
                    <strong>${esc(table.name)}</strong>
                    <p>schema: ${esc(table.schema || selectedSchema)}</p>
                    <p>properties: ${esc(formatKeyValueSummary(table.properties))}</p>
                  </div>
                `).join('')}
              </div>
            `));
      browserHtml = `
        <div class="catalog-browser-grid">
          <div class="catalog-schema-list">
            ${schemas.map((schema) => `
              <button type="button" class="btn btn-sm catalog-schema-button ${selectedSchema === schema.name ? 'is-active' : ''}" data-action="select-catalog-schema" data-source-id="${esc(source.source_id)}" data-schema-name="${esc(schema.name)}">
                <span>${esc(schema.name)}</span>
                <span class="shell-chip">${esc(formatKeyValueSummary(schema.properties))}</span>
              </button>
            `).join('')}
          </div>
          <div>${tableBody}</div>
        </div>
      `;
    }
    return renderAdminDetailCard({
      title: 'Catalog Browser',
      statusHtml: `<span class="shell-chip">${esc(selectedSchema || 'live catalog')}</span>`,
      note: 'Browse Catalog uses GET /sources/{source_id}/catalog/schemas and GET /sources/{source_id}/catalog/tables to inspect live catalog metadata without persisting source objects.',
      bodyHtml: browserHtml,
    });
  }

  function renderSourceObjectsCard(source, tables) {
    if (!source) {
      return renderAdminDetailCard({
        title: 'Synced Source Objects',
        statusHtml: '<span class="shell-chip">idle</span>',
        note: 'Source Objects stay read-only here and do not turn into semantic contracts on this page.',
        bodyHtml: renderEmptyState('No source selected.'),
      });
    }
    const latestTable = pickLatest(tables || [], ['synced_at']);
    const summaryHtml = (tables || []).length ? `
      <div class="source-object-summary">
        <span class="shell-chip">${esc(tableCountLabel(tables))}</span>
        <span class="shell-chip">latest sync ${esc(formatMaybeDate(latestTable?.synced_at))}</span>
        <span class="shell-chip">GET /sources/${esc(source.source_id)}/objects?type=table</span>
      </div>
      <div class="compact-list">
        ${(tables || []).slice(0, 6).map((table) => `
          <div class="compact-list-item">
            <div class="compact-list-copy">
              <strong>${esc(table.native_name)}</strong>
              <span>${esc(table.fqn || '-')}</span>
            </div>
            <span class="shell-chip">${esc(formatMaybeDate(table.synced_at))}</span>
          </div>
        `).join('')}
      </div>
    ` : renderEmptyState('No synced source objects yet. Run Sync or configure selections first.');
    return renderAdminDetailCard({
      title: 'Synced Source Objects',
      statusHtml: `<span class="shell-chip">${esc(tableCountLabel(tables || []))}</span>`,
      note: 'GET /sources/{source_id}/objects?type=table returns the synced table inventory. This is a read-only source inventory and semantic object authoring remains in Semantic Catalog.',
      bodyHtml: summaryHtml,
    });
  }

  function renderBody(viewModel) {
    return `
      <div class="data-sources-page">
        ${renderAdminListDetailLayout({
          primaryHtml: renderSourceListCard(viewModel),
          secondaryHtml: viewModel.listError ? '' : '',
          detailHtml: `
            <div class="data-sources-detail-stack">
              ${renderSourceSummaryCard(viewModel.selectedSource, viewModel.sourceError)}
              ${renderSourceSyncCard(viewModel.selectedSource, viewModel.tables, viewModel.syncError)}
              ${renderSelectionsCard(viewModel.selectedSource, viewModel.selections)}
              ${renderCatalogCard(viewModel.selectedSource, viewModel.catalogState)}
              ${renderSourceObjectsCard(viewModel.selectedSource, viewModel.tables)}
            </div>
          `,
        })}
      </div>
    `;
  }

  function render() {
    return `<div data-role="data-sources-body">${renderBody({
      sources: [],
      selectedSourceId: '',
      selectedSource: null,
      sourceError: null,
      selections: [],
      tables: [],
      syncError: null,
      catalogState: {},
      listError: null,
    })}</div>`;
  }

  function ensureSourceFormModal() {
    let overlay = document.getElementById('source-form-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'source-form-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="source-form-title">
        <div class="modal-card-stack">
          <div class="shell-card-title">
            <h3 id="source-form-title">Create Source</h3>
            <span class="shell-chip" data-role="mode-label">create</span>
          </div>
          <p class="panel-note" data-role="copy">POST /sources creates a new source entry. PUT /sources/{source_id} updates display name, connection, and sync_mode.</p>
          <form class="source-form-grid" data-role="form">
            <label>
              Source Type
              <input name="source_type" type="text" placeholder="duckdb" />
            </label>
            <label>
              Display Name
              <input name="display_name" type="text" placeholder="Local Demo Source" />
            </label>
            <label>
              Sync Mode
              <select name="sync_mode">
                <option value="all">all</option>
                <option value="by_select">by_select</option>
                <option value="none">none</option>
              </select>
            </label>
            <label>
              Connection JSON
              <textarea name="connection_json" placeholder="{&#10;  &quot;path&quot;: &quot;/tmp/demo.duckdb&quot;&#10;}"></textarea>
            </label>
            <div class="detail-error" data-role="error" style="display:none;"></div>
            <div class="detail-actions">
              <button type="button" class="btn" data-role="cancel">Cancel</button>
              <button type="submit" class="btn btn-primary" data-role="submit">Save Source</button>
            </div>
          </form>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target?.dataset?.role === 'cancel') {
        closeModal('source-form-modal');
      }
    });
    return overlay;
  }

  function ensureSelectionModal() {
    let overlay = document.getElementById('selection-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'selection-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="selection-modal-title">
        <div class="modal-card-stack">
          <div class="shell-card-title">
            <h3 id="selection-modal-title">Manage Selections</h3>
            <span class="shell-chip" data-role="selection-source">source</span>
          </div>
          <p class="panel-note">Manage Selections writes the full selection set back through POST /sources/{source_id}/sync/selections.</p>
          <label class="source-form-grid">
            <span>Schema</span>
            <select data-role="schema-select"></select>
          </label>
          <div data-role="selection-error"></div>
          <div data-role="table-checklist"></div>
          <div class="detail-actions">
            <button type="button" class="btn" data-role="cancel">Cancel</button>
            <button type="button" class="btn btn-primary" data-role="save">Save Selections</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay || event.target?.dataset?.role === 'cancel') {
        closeModal('selection-modal');
      }
    });
    return overlay;
  }

  async function enrichSourcesWithSyncMetadata(sources) {
    const records = await Promise.all(sources.map(async (source) => {
      try {
        const tables = await ctx.adminApi.listSourceObjects(source.source_id, { type: 'table' });
        const latestTable = pickLatest(tables, ['synced_at']);
        const syncJob = sourceSyncState(source.source_id);
        return {
          ...source,
          last_sync_at: syncJob?.updated_at || latestTable?.synced_at || '',
        };
      } catch {
        const syncJob = sourceSyncState(source.source_id);
        return {
          ...source,
          last_sync_at: syncJob?.updated_at || '',
        };
      }
    }));
    return records;
  }

  async function loadCatalogState(sourceId) {
    const existing = sourceCatalogState(sourceId);
    try {
      const schemas = extractItems(await ctx.adminApi.listCatalogSchemas(sourceId));
      const selectedSchema = schemas.some((item) => item.name === existing.selectedSchema)
        ? existing.selectedSchema
        : (schemas[0]?.name || '');
      let tables = [];
      let tablesError = null;
      if (selectedSchema) {
        try {
          tables = extractItems(await ctx.adminApi.listCatalogTables(sourceId, selectedSchema));
        } catch (error) {
          tablesError = normalizeApiError(error, 'Catalog tables unavailable.');
        }
      }
      const nextState = { schemas, selectedSchema, tables, error: null, tablesError };
      dataSourcesUiState.catalog[sourceId] = nextState;
      return nextState;
    } catch (error) {
      const nextState = {
        schemas: [],
        selectedSchema: '',
        tables: [],
        error: normalizeApiError(error, 'Catalog schemas unavailable.'),
        tablesError: null,
      };
      dataSourcesUiState.catalog[sourceId] = nextState;
      return nextState;
    }
  }

  async function hydrate(panel, route) {
    const renderVersion = ++dataSourcesRenderVersion;
    let lastSources = [];
    const safeRender = (viewModel) => {
      if (renderVersion !== dataSourcesRenderVersion) return;
      const target = panel.querySelector('[data-role="data-sources-body"]');
      if (target) {
        target.innerHTML = renderBody(viewModel);
        bindEvents(panel, viewModel);
      }
    };

    safeRender({
      sources: [],
      selectedSourceId: route.sourceId || '',
      selectedSource: null,
      sourceError: null,
      selections: [],
      tables: [],
      syncError: null,
      catalogState: {},
      listError: null,
    });

    try {
      const rawSources = await ctx.adminApi.listSources();
      const sources = await enrichSourcesWithSyncMetadata(extractItems(rawSources));
      lastSources = sources;
      const firstSourceId = sources[0]?.source_id || '';
      const selectedSourceId = sources.some((item) => item.source_id === route.sourceId)
        ? route.sourceId
        : firstSourceId;

      safeRender({
        sources,
        selectedSourceId: sources.some((item) => item.source_id === route.sourceId) ? route.sourceId : '',
        selectedSource: null,
        sourceError: null,
        selections: [],
        tables: [],
        syncError: null,
        catalogState: {},
        listError: null,
      });

      if (route.sourceId !== selectedSourceId) {
        ctx.applyAdminRoute({ ...route, sourceId: selectedSourceId }, 'replace');
        return;
      }

      if (!selectedSourceId) {
        safeRender({
          sources,
          selectedSourceId: '',
          selectedSource: null,
          sourceError: null,
          selections: [],
          tables: [],
          syncError: null,
          catalogState: {},
          listError: null,
        });
        return;
      }

      const selectedSourceSeed = sources.find((item) => item.source_id === selectedSourceId) || null;
      safeRender({
        sources,
        selectedSourceId,
        selectedSource: selectedSourceSeed,
        sourceError: null,
        selections: [],
        tables: [],
        syncError: null,
        catalogState: sourceCatalogState(selectedSourceId),
        listError: null,
      });

      const [sourceDetailResult, selectionsResult, tablesResult, catalogStateResult] =
        await Promise.allSettled([
          ctx.adminApi.getSource(selectedSourceId),
          ctx.adminApi.listSourceSelections(selectedSourceId),
          ctx.adminApi.listSourceObjects(selectedSourceId, { type: 'table' }),
          loadCatalogState(selectedSourceId),
        ]);

      const sourceDetail = sourceDetailResult.status === 'fulfilled' ? sourceDetailResult.value : null;
      const sourceError = sourceDetailResult.status === 'rejected'
        ? normalizeApiError(sourceDetailResult.reason, 'Source Summary unavailable.')
        : null;
      const selections = selectionsResult.status === 'fulfilled'
        ? extractItems(selectionsResult.value)
        : [];
      const tables = tablesResult.status === 'fulfilled'
        ? extractItems(tablesResult.value)
        : [];
      const catalogState = catalogStateResult.status === 'fulfilled'
        ? catalogStateResult.value
        : sourceCatalogState(selectedSourceId);
      const selectedSource = {
        ...(selectedSourceSeed || {}),
        ...(sourceDetail || {}),
        last_sync_at: sources.find((item) => item.source_id === selectedSourceId)?.last_sync_at || '',
      };
      safeRender({
        sources,
        selectedSourceId,
        selectedSource,
        sourceError,
        selections,
        tables,
        syncError: dataSourcesUiState.syncErrors[selectedSourceId] || null,
        catalogState,
        listError: null,
      });
    } catch (error) {
      safeRender({
        sources: lastSources,
        selectedSourceId: route.sourceId || '',
        selectedSource: null,
        sourceError: null,
        selections: [],
        tables: [],
        syncError: null,
        catalogState: {},
        listError: normalizeApiError(error, 'Data Sources unavailable.'),
      });
    }
  }

  function refreshCurrentDataSources() {
    const panel = document.getElementById('panel-data-sources');
    const route = ctx.getCurrentRoute();
    if (panel && route?.tab === 'data-sources') {
      void hydrate(panel, route);
    }
  }

  function renderSelectionChecklist(schemaName, tables, selectedKeys) {
    if (!schemaName) {
      return renderEmptyState('Select a schema to browse tables.');
    }
    if (!tables.length) {
      return renderEmptyState('No table found for the selected schema.');
    }
    return `
      <div class="checklist-grid">
        ${tables.map((table) => {
          const key = sourceSelectionKey(schemaName, table.name);
          return `
            <label class="checklist-item">
              <input type="checkbox" data-role="table-checkbox" data-schema-name="${esc(schemaName)}" data-table-name="${esc(table.name)}" ${selectedKeys.has(key) ? 'checked' : ''} />
              <span class="checklist-copy">
                <strong>${esc(table.name)}</strong>
                <span>${esc(formatKeyValueSummary(table.properties))}</span>
              </span>
            </label>
          `;
        }).join('')}
      </div>
    `;
  }

  async function openSelectionModal(sourceId) {
    const overlay = ensureSelectionModal();
    const sourceLabel = overlay.querySelector('[data-role="selection-source"]');
    const schemaSelect = overlay.querySelector('[data-role="schema-select"]');
    const errorBox = overlay.querySelector('[data-role="selection-error"]');
    const checklist = overlay.querySelector('[data-role="table-checklist"]');
    const saveButton = overlay.querySelector('[data-role="save"]');

    const selectedKeys = new Set();
    try {
      const existingSelections = extractItems(await ctx.adminApi.listSourceSelections(sourceId));
      existingSelections.forEach((item) => selectedKeys.add(sourceSelectionKey(item.schema_name, item.table_name)));
    } catch (error) {
      toast(normalizeApiError(error, 'Failed to load existing selections.').message, 'error');
    }

    let schemas = [];
    let currentSchema = '';
    let tableRequestVersion = 0;

    const renderModalState = async (schemaName) => {
      currentSchema = schemaName || schemas[0]?.name || '';
      if (schemaSelect) {
        schemaSelect.innerHTML = schemas.length
          ? schemas.map((item) => `<option value="${esc(item.name)}" ${item.name === currentSchema ? 'selected' : ''}>${esc(item.name)}</option>`).join('')
          : '<option value="">No schema available</option>';
      }
      if (!currentSchema) {
        if (checklist) checklist.innerHTML = renderEmptyState('No schema available from the live catalog for this source.');
        return;
      }
      const requestVersion = ++tableRequestVersion;
      if (checklist) checklist.innerHTML = renderLoadingState('Loading live tables...');
      try {
        const tables = extractItems(await ctx.adminApi.listCatalogTables(sourceId, currentSchema));
        if (requestVersion !== tableRequestVersion) return;
        if (checklist) checklist.innerHTML = renderSelectionChecklist(currentSchema, tables, selectedKeys);
        checklist?.querySelectorAll('[data-role="table-checkbox"]').forEach((input) => {
          input.addEventListener('change', (event) => {
            const tableKey = sourceSelectionKey(
              event.currentTarget.dataset.schemaName || '',
              event.currentTarget.dataset.tableName || ''
            );
            if (event.currentTarget.checked) {
              selectedKeys.add(tableKey);
            } else {
              selectedKeys.delete(tableKey);
            }
          });
        });
      } catch (error) {
        if (requestVersion !== tableRequestVersion) return;
        if (checklist) checklist.innerHTML = renderStructuredError(error, 'Catalog tables unavailable.');
      }
    };

    try {
      schemas = extractItems(await ctx.adminApi.listCatalogSchemas(sourceId));
    } catch (error) {
      if (errorBox) errorBox.innerHTML = renderStructuredError(error, 'Catalog schemas unavailable.');
    }

    if (sourceLabel) sourceLabel.textContent = sourceId;
    if (errorBox && schemas.length) errorBox.innerHTML = '';
    await renderModalState(schemas[0]?.name || '');

    if (schemaSelect) {
      schemaSelect.onchange = () => {
        void renderModalState(schemaSelect.value);
      };
    }

    if (saveButton) {
      saveButton.onclick = async () => {
        const payload = {
          selections: Array.from(selectedKeys).sort().map((key) => {
            const [schemaName, ...rest] = key.split('.');
            return { schema_name: schemaName, table_name: rest.join('.') };
          }),
        };
        try {
          await ctx.adminApi.replaceSourceSelections(sourceId, payload);
          toast('Selections updated.', 'success');
          closeModal('selection-modal');
          refreshCurrentDataSources();
        } catch (error) {
          if (errorBox) errorBox.innerHTML = renderStructuredError(error, 'Failed to save selections.');
        }
      };
    }

    openModal('selection-modal');
  }

  function openSourceFormModal(mode, source) {
    const overlay = ensureSourceFormModal();
    const title = overlay.querySelector('#source-form-title');
    const modeLabel = overlay.querySelector('[data-role="mode-label"]');
    const copy = overlay.querySelector('[data-role="copy"]');
    const form = overlay.querySelector('[data-role="form"]');
    const errorBox = overlay.querySelector('[data-role="error"]');
    const sourceTypeInput = form.querySelector('[name="source_type"]');
    const displayNameInput = form.querySelector('[name="display_name"]');
    const syncModeInput = form.querySelector('[name="sync_mode"]');
    const connectionInput = form.querySelector('[name="connection_json"]');
    const submit = overlay.querySelector('[data-role="submit"]');

    title.textContent = mode === 'edit' ? 'Edit Source' : 'Create Source';
    modeLabel.textContent = mode;
    copy.textContent = mode === 'edit'
      ? 'PUT /sources/{source_id} updates display name, connection, and sync_mode.'
      : 'POST /sources creates a new source entry and returns source_id.';
    if (errorBox) {
      errorBox.style.display = 'none';
      errorBox.innerHTML = '';
    }
    sourceTypeInput.value = source?.source_type || 'duckdb';
    sourceTypeInput.disabled = mode === 'edit';
    displayNameInput.value = source?.display_name || '';
    syncModeInput.value = source?.sync_mode || 'all';
    connectionInput.value = JSON.stringify(source?.connection || { path: '/tmp/demo.duckdb' }, null, 2);
    submit.textContent = mode === 'edit' ? 'Save Changes' : 'Create Source';

    form.onsubmit = async (event) => {
      event.preventDefault();
      let connection = {};
      try {
        connection = JSON.parse(connectionInput.value || '{}');
      } catch {
        if (errorBox) {
          errorBox.style.display = '';
          errorBox.innerHTML = renderErrorState('Connection JSON is invalid.');
        }
        return;
      }
      try {
        if (mode === 'edit' && source?.source_id) {
          await ctx.adminApi.updateSource(source.source_id, {
            display_name: displayNameInput.value.trim(),
            sync_mode: syncModeInput.value,
            connection,
          });
          toast('Source updated.', 'success');
        } else {
          const created = await ctx.adminApi.createSource({
            source_type: sourceTypeInput.value.trim(),
            display_name: displayNameInput.value.trim(),
            connection,
          });
          toast('Source created.', 'success');
          closeModal('source-form-modal');
          ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: 'data-sources', sourceId: created.source_id }, 'replace');
          return;
        }
        closeModal('source-form-modal');
        refreshCurrentDataSources();
      } catch (error) {
        if (errorBox) {
          errorBox.style.display = '';
          errorBox.innerHTML = renderStructuredError(error, 'Failed to save source.');
        }
      }
    };

    openModal('source-form-modal');
  }

  async function handleRunSourceSync(sourceId) {
    try {
      delete dataSourcesUiState.syncErrors[sourceId];
      const syncResult = await ctx.adminApi.runSourceSync(sourceId);
      dataSourcesUiState.syncJobs[sourceId] = {
        job_id: syncResult.job_id,
        status: syncResult.status || 'submitted',
        updated_at: new Date().toISOString(),
      };
      refreshCurrentDataSources();
      const finalJob = await pollAsync(
        async () => ctx.adminApi.getSourceSyncStatus(sourceId, syncResult.job_id),
        {
          maxAttempts: 6,
          intervalMs: 500,
          shouldStop: (job) => ['succeeded', 'failed', 'cancelled', 'completed'].includes(
            String(job?.status || '').toLowerCase()
          ),
        }
      );
      dataSourcesUiState.syncJobs[sourceId] = {
        ...(finalJob || {}),
        job_id: finalJob?.job_id || syncResult.job_id,
        updated_at: finalJob?.updated_at || new Date().toISOString(),
      };
      if (String(finalJob?.status || '').toLowerCase() === 'failed') {
        toast('Sync failed.', 'error');
      } else {
        toast('Sync completed.', 'success');
      }
    } catch (error) {
      dataSourcesUiState.syncErrors[sourceId] = normalizeApiError(error, 'Sync request failed.');
      toast(dataSourcesUiState.syncErrors[sourceId].message, 'error');
    }
    refreshCurrentDataSources();
  }

  function handleDeleteSource(source) {
    openDangerConfirm({
      title: 'Delete Source',
      objectLabel: source.source_id,
      impactScope: 'Deletes the source entry, sync jobs, sync selections, and synced source objects if no bindings depend on it.',
      reversible: 'No',
      confirmLabel: 'Delete Source',
      detailsHtml: renderDetailList([
        { label: 'display_name', value: source.display_name || '-' },
        { label: 'source_type', value: source.source_type || '-' },
        { label: 'warning', value: '409 conflicts surface dependencies from bindings or typed bindings.' },
      ]),
      onConfirm: async () => {
        try {
          await ctx.adminApi.deleteSource(source.source_id);
          toast('Source deleted.', 'success');
          const currentRoute = ctx.getCurrentRoute();
          const nextSourceId = currentRoute.sourceId === source.source_id ? '' : currentRoute.sourceId;
          ctx.applyAdminRoute({ ...currentRoute, sourceId: nextSourceId }, 'replace');
        } catch (error) {
          const normalized = normalizeApiError(error, 'Failed to delete source.');
          dataSourcesUiState.syncErrors[source.source_id] = normalized;
          toast(normalized.message, 'error');
          refreshCurrentDataSources();
        }
      },
    });
  }

  function bindEvents(panel, viewModel) {
    panel.querySelectorAll('[data-role="source-row"]').forEach((row) => {
      const selectSource = () => {
        const sourceId = row.dataset.sourceId || '';
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), sourceId }, 'push');
      };
      row.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : null;
        if (target?.closest('[data-action="select-source"]')) return;
        selectSource();
      });
      row.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        selectSource();
      });
    });
    panel.querySelectorAll('[data-action="select-source"]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), sourceId: button.dataset.sourceId || '' }, 'push');
      });
    });
    panel.querySelectorAll('[data-action="refresh-sources"]').forEach((button) => {
      button.addEventListener('click', () => refreshCurrentDataSources());
    });
    panel.querySelectorAll('[data-action="create-source"]').forEach((button) => {
      button.addEventListener('click', () => openSourceFormModal('create', null));
    });
    panel.querySelectorAll('[data-action="edit-source"]').forEach((button) => {
      button.addEventListener('click', () => {
        if (viewModel.selectedSource) {
          openSourceFormModal('edit', viewModel.selectedSource);
        }
      });
    });
    panel.querySelectorAll('[data-action="delete-source"]').forEach((button) => {
      button.addEventListener('click', () => {
        if (viewModel.selectedSource) {
          handleDeleteSource(viewModel.selectedSource);
        }
      });
    });
    panel.querySelectorAll('[data-action="run-sync"]').forEach((button) => {
      button.addEventListener('click', () => {
        const sourceId = button.dataset.sourceId || '';
        if (sourceId) void handleRunSourceSync(sourceId);
      });
    });
    panel.querySelectorAll('[data-action="manage-selections"]').forEach((button) => {
      button.addEventListener('click', () => {
        const sourceId = button.dataset.sourceId || '';
        if (sourceId) void openSelectionModal(sourceId);
      });
    });
    panel.querySelectorAll('[data-action="delete-selection"]').forEach((button) => {
      button.addEventListener('click', async () => {
        try {
          await ctx.adminApi.deleteSourceSelection(button.dataset.sourceId || '', button.dataset.selectionId || '');
          toast('Selection removed.', 'success');
          refreshCurrentDataSources();
        } catch (error) {
          toast(normalizeApiError(error, 'Failed to remove selection.').message, 'error');
        }
      });
    });
    panel.querySelectorAll('[data-action="clear-selections"]').forEach((button) => {
      button.addEventListener('click', () => {
        const sourceId = button.dataset.sourceId || '';
        openDangerConfirm({
          title: 'Clear Sync Selections',
          objectLabel: sourceId,
          impactScope: 'Removes all configured schema/table selections for this source.',
          reversible: 'No',
          confirmLabel: 'Clear Selections',
          onConfirm: async () => {
            try {
              await ctx.adminApi.clearSourceSelections(sourceId);
              toast('Selections cleared.', 'success');
              refreshCurrentDataSources();
            } catch (error) {
              toast(normalizeApiError(error, 'Failed to clear selections.').message, 'error');
            }
          },
        });
      });
    });
    panel.querySelectorAll('[data-action="select-catalog-schema"]').forEach((button) => {
      button.addEventListener('click', () => {
        const sourceId = button.dataset.sourceId || '';
        const schemaName = button.dataset.schemaName || '';
        const existing = sourceCatalogState(sourceId);
        dataSourcesUiState.catalog[sourceId] = { ...existing, selectedSchema: schemaName };
        refreshCurrentDataSources();
      });
    });
  }

  return { render, hydrate };
}
