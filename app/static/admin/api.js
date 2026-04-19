export function parseResponseError(response, fallbackMessage) {
  return response.text().then((text) => {
    let detail = text || response.statusText || fallbackMessage;
    let message = fallbackMessage;
    try {
      const parsed = text ? JSON.parse(text) : null;
      if (parsed && typeof parsed === 'object') {
        if (Object.hasOwn(parsed, 'detail')) {
          detail = parsed.detail;
          if (typeof parsed.detail === 'string' && parsed.detail) {
            message = parsed.detail;
          } else if (parsed.detail && typeof parsed.detail.message === 'string') {
            message = parsed.detail.message;
          }
        } else {
          detail = parsed;
        }
        if (typeof parsed.message === 'string' && parsed.message) {
          message = parsed.message;
        }
      }
    } catch {
      if (text) {
        message = text;
      }
    }
    return {
      status: response.status,
      message,
      detail,
      transport: `HTTP ${response.status}`,
    };
  });
}

export async function requestJson(path, fallbackMessage) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw await parseResponseError(response, fallbackMessage);
  }
  return response.json();
}

export async function requestText(path, fallbackMessage) {
  const response = await fetch(path);
  if (!response.ok) {
    throw await parseResponseError(response, fallbackMessage);
  }
  return response.text();
}

export async function sendJson(path, method, payload, fallbackMessage) {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: payload == null ? null : JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await parseResponseError(response, fallbackMessage);
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function sendDelete(path, fallbackMessage) {
  const response = await fetch(path, { method: 'DELETE' });
  if (!response.ok) {
    throw await parseResponseError(response, fallbackMessage);
  }
  if (response.status === 204) return null;
  return response.json();
}

function semanticListQuery(status = null, options = {}) {
  const search = new URLSearchParams();
  if (status) search.set('status', status);
  if (options.detail) search.set('detail', 'true');
  const query = search.toString();
  return query ? `?${query}` : '';
}

export function createAdminApi() {
  return {
    parseError: parseResponseError,
    getJson: requestJson,
    getText: requestText,
    getHealth() {
      return requestJson('/health', 'Health Summary unavailable.');
    },
    getMetrics() {
      return requestJson('/metrics', 'Metrics Summary unavailable.');
    },
    getMetricsPrometheus() {
      return requestText('/metrics?format=prometheus', 'Metrics raw text unavailable.');
    },
    listSources() {
      return requestJson('/sources', 'Data Sources unavailable.');
    },
    getSource(sourceId) {
      return requestJson(`/sources/${encodeURIComponent(sourceId)}`, 'Source Summary unavailable.');
    },
    createSource(payload) {
      return sendJson('/sources', 'POST', payload, 'Create Source failed.');
    },
    updateSource(sourceId, payload) {
      return sendJson(`/sources/${encodeURIComponent(sourceId)}`, 'PUT', payload, 'Edit Source failed.');
    },
    deleteSource(sourceId) {
      return sendDelete(`/sources/${encodeURIComponent(sourceId)}`, 'Delete Source failed.');
    },
    runSourceSync(sourceId) {
      return sendJson(`/sources/${encodeURIComponent(sourceId)}/sync`, 'POST', {}, 'Run Sync failed.');
    },
    getSourceSyncStatus(sourceId, jobId) {
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/sync/${encodeURIComponent(jobId)}`,
        'Sync status unavailable.'
      );
    },
    listSourceSelections(sourceId) {
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/sync/selections`,
        'Sync selections unavailable.'
      );
    },
    replaceSourceSelections(sourceId, payload) {
      return sendJson(
        `/sources/${encodeURIComponent(sourceId)}/sync/selections`,
        'POST',
        payload,
        'Manage Selections failed.'
      );
    },
    clearSourceSelections(sourceId) {
      return sendDelete(
        `/sources/${encodeURIComponent(sourceId)}/sync/selections`,
        'Clear selections failed.'
      );
    },
    deleteSourceSelection(sourceId, selectionId) {
      return sendDelete(
        `/sources/${encodeURIComponent(sourceId)}/sync/selections/${encodeURIComponent(selectionId)}`,
        'Delete selection failed.'
      );
    },
    listCatalogSchemas(sourceId) {
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/catalog/schemas`,
        'Catalog schemas unavailable.'
      );
    },
    listCatalogTables(sourceId, schemaName) {
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/catalog/tables?schema=${encodeURIComponent(schemaName)}`,
        'Catalog tables unavailable.'
      );
    },
    listSourceObjects(sourceId, params = {}) {
      const search = new URLSearchParams();
      if (params.type) search.set('type', params.type);
      if (params.schema) search.set('schema', params.schema);
      const query = search.toString();
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/objects${query ? `?${query}` : ''}`,
        'Source objects unavailable.'
      );
    },
    listEngines() {
      return requestJson('/engines', 'Execution Engines unavailable.');
    },
    listSessions(params = {}) {
      const search = new URLSearchParams();
      if (params.status) search.set('status', params.status);
      if (params.sessionId) search.set('session_id', params.sessionId);
      const query = search.toString();
      return requestJson(`/sessions${query ? `?${query}` : ''}`, 'Analysis Ops unavailable.');
    },
    // GET /sessions/{session_id}
    getSession(sessionId) {
      return requestJson(
        `/sessions/${encodeURIComponent(sessionId)}`,
        'Session detail unavailable.'
      );
    },
    // POST /sessions/{session_id}/terminate
    terminateSession(sessionId, payload) {
      return sendJson(
        `/sessions/${encodeURIComponent(sessionId)}/terminate`,
        'POST',
        payload,
        'Terminate Session failed.'
      );
    },
    getSessionRuntimeStatus(sessionId) {
      return requestJson(
        `/sessions/${encodeURIComponent(sessionId)}/runtime-status`,
        'Session Runtime unavailable.'
      );
    },
    getPropositionRuntimeStatus(sessionId, propositionId) {
      return requestJson(
        `/sessions/${encodeURIComponent(sessionId)}/propositions/${encodeURIComponent(propositionId)}/runtime-status`,
        'Proposition Runtime unavailable.'
      );
    },
    getArtifactRuntimeStatus(sessionId, artifactId) {
      return requestJson(
        `/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(artifactId)}/runtime-status`,
        'Artifact Runtime unavailable.'
      );
    },
    listJobs(params = {}) {
      const search = new URLSearchParams();
      if (params.sessionId) search.set('session_id', params.sessionId);
      if (params.status) search.set('status', params.status);
      const query = search.toString();
      return requestJson(`/jobs${query ? `?${query}` : ''}`, 'Jobs unavailable.');
    },
    getJob(jobId) {
      return requestJson(`/jobs/${encodeURIComponent(jobId)}`, 'Job detail unavailable.');
    },
    getEngine(engineId) {
      return requestJson(`/engines/${encodeURIComponent(engineId)}`, 'Engine detail unavailable.');
    },
    createEngine(payload) {
      return sendJson('/engines', 'POST', payload, 'Create Engine failed.');
    },
    listBindings(params = {}) {
      const search = new URLSearchParams();
      if (params.sourceId) search.set('source_id', params.sourceId);
      if (params.engineId) search.set('engine_id', params.engineId);
      const query = search.toString();
      return requestJson(`/bindings${query ? `?${query}` : ''}`, 'Binding Inventory unavailable.');
    },
    getBinding(bindingId) {
      return requestJson(`/bindings/${encodeURIComponent(bindingId)}`, 'Binding detail unavailable.');
    },
    createBinding(payload) {
      return sendJson('/bindings', 'POST', payload, 'Create Binding failed.');
    },
    deleteBinding(bindingId) {
      return sendDelete(`/bindings/${encodeURIComponent(bindingId)}`, 'Delete Binding failed.');
    },
    listSourceEngines(sourceId) {
      return requestJson(
        `/sources/${encodeURIComponent(sourceId)}/engines`,
        'Source-engine relationship unavailable.'
      );
    },
    listSemanticEntities(status = null, options = {}) {
      return requestJson(
        `/semantic/entities${semanticListQuery(status, options)}`,
        'Entity Catalog unavailable.'
      );
    },
    getSemanticEntity(objectId) {
      return requestJson(
        `/semantic/entities/${encodeURIComponent(objectId)}`,
        'Entity detail unavailable.'
      );
    },
    createSemanticEntity(payload) {
      return sendJson('/semantic/entities', 'POST', payload, 'Create Entity failed.');
    },
    updateSemanticEntity(objectId, payload) {
      return sendJson(
        `/semantic/entities/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Entity failed.'
      );
    },
    validateSemanticEntity(objectId) {
      return sendJson(
        `/semantic/entities/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Entity failed.'
      );
    },
    activateSemanticEntity(objectId) {
      return sendJson(
        `/semantic/entities/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Entity failed.'
      );
    },
    deprecateSemanticEntity(objectId) {
      return sendJson(
        `/semantic/entities/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Entity failed.'
      );
    },
    publishSemanticEntity(objectId) {
      return this.activateSemanticEntity(objectId);
    },
    listSemanticMetrics(status = null, options = {}) {
      return requestJson(
        `/semantic/metrics${semanticListQuery(status, options)}`,
        'Metric Catalog unavailable.'
      );
    },
    getSemanticMetric(objectId) {
      return requestJson(
        `/semantic/metrics/${encodeURIComponent(objectId)}`,
        'Metric detail unavailable.'
      );
    },
    createSemanticMetric(payload) {
      return sendJson('/semantic/metrics', 'POST', payload, 'Create Metric failed.');
    },
    updateSemanticMetric(objectId, payload) {
      return sendJson(
        `/semantic/metrics/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Metric failed.'
      );
    },
    validateSemanticMetric(objectId) {
      return sendJson(
        `/semantic/metrics/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Metric failed.'
      );
    },
    activateSemanticMetric(objectId) {
      return sendJson(
        `/semantic/metrics/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Metric failed.'
      );
    },
    deprecateSemanticMetric(objectId) {
      return sendJson(
        `/semantic/metrics/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Metric failed.'
      );
    },
    publishSemanticMetric(objectId) {
      return this.activateSemanticMetric(objectId);
    },
    listSemanticProcessObjects(status = null, options = {}) {
      return requestJson(
        `/semantic/process-objects${semanticListQuery(status, options)}`,
        'Process Object Catalog unavailable.'
      );
    },
    getSemanticProcessObject(objectId) {
      return requestJson(
        `/semantic/process-objects/${encodeURIComponent(objectId)}`,
        'Process Object detail unavailable.'
      );
    },
    createSemanticProcessObject(payload) {
      return sendJson(
        '/semantic/process-objects',
        'POST',
        payload,
        'Create Process Object failed.'
      );
    },
    updateSemanticProcessObject(objectId, payload) {
      return sendJson(
        `/semantic/process-objects/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Process Object failed.'
      );
    },
    validateSemanticProcessObject(objectId) {
      return sendJson(
        `/semantic/process-objects/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Process Object failed.'
      );
    },
    activateSemanticProcessObject(objectId) {
      return sendJson(
        `/semantic/process-objects/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Process Object failed.'
      );
    },
    deprecateSemanticProcessObject(objectId) {
      return sendJson(
        `/semantic/process-objects/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Process Object failed.'
      );
    },
    publishSemanticProcessObject(objectId) {
      return this.activateSemanticProcessObject(objectId);
    },
    listSemanticDimensions(status = null, options = {}) {
      return requestJson(
        `/semantic/dimensions${semanticListQuery(status, options)}`,
        'Dimension Catalog unavailable.'
      );
    },
    getSemanticDimension(objectId) {
      return requestJson(
        `/semantic/dimensions/${encodeURIComponent(objectId)}`,
        'Dimension detail unavailable.'
      );
    },
    createSemanticDimension(payload) {
      return sendJson('/semantic/dimensions', 'POST', payload, 'Create Dimension failed.');
    },
    updateSemanticDimension(objectId, payload) {
      return sendJson(
        `/semantic/dimensions/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Dimension failed.'
      );
    },
    validateSemanticDimension(objectId) {
      return sendJson(
        `/semantic/dimensions/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Dimension failed.'
      );
    },
    activateSemanticDimension(objectId) {
      return sendJson(
        `/semantic/dimensions/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Dimension failed.'
      );
    },
    deprecateSemanticDimension(objectId) {
      return sendJson(
        `/semantic/dimensions/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Dimension failed.'
      );
    },
    publishSemanticDimension(objectId) {
      return this.activateSemanticDimension(objectId);
    },
    listSemanticTime(status = null, options = {}) {
      return requestJson(
        `/semantic/time${semanticListQuery(status, options)}`,
        'Time Catalog unavailable.'
      );
    },
    getSemanticTime(objectId) {
      return requestJson(`/semantic/time/${encodeURIComponent(objectId)}`, 'Time detail unavailable.');
    },
    createSemanticTime(payload) {
      return sendJson('/semantic/time', 'POST', payload, 'Create Time failed.');
    },
    updateSemanticTime(objectId, payload) {
      return sendJson(
        `/semantic/time/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Time failed.'
      );
    },
    validateSemanticTime(objectId) {
      return sendJson(
        `/semantic/time/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Time failed.'
      );
    },
    activateSemanticTime(objectId) {
      return sendJson(
        `/semantic/time/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Time failed.'
      );
    },
    deprecateSemanticTime(objectId) {
      return sendJson(
        `/semantic/time/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Time failed.'
      );
    },
    publishSemanticTime(objectId) {
      return this.activateSemanticTime(objectId);
    },
    listSemanticEnumSets(status = null, options = {}) {
      return requestJson(
        `/semantic/enum-sets${semanticListQuery(status, options)}`,
        'Enum Set Catalog unavailable.'
      );
    },
    getSemanticEnumSet(objectId) {
      return requestJson(
        `/semantic/enum-sets/${encodeURIComponent(objectId)}`,
        'Enum Set detail unavailable.'
      );
    },
    createSemanticEnumSet(payload) {
      return sendJson('/semantic/enum-sets', 'POST', payload, 'Create Enum Set failed.');
    },
    updateSemanticEnumSet(objectId, payload) {
      return sendJson(
        `/semantic/enum-sets/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Enum Set failed.'
      );
    },
    validateSemanticEnumSet(objectId) {
      return sendJson(
        `/semantic/enum-sets/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Enum Set failed.'
      );
    },
    activateSemanticEnumSet(objectId) {
      return sendJson(
        `/semantic/enum-sets/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Enum Set failed.'
      );
    },
    deprecateSemanticEnumSet(objectId) {
      return sendJson(
        `/semantic/enum-sets/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Enum Set failed.'
      );
    },
    publishSemanticEnumSet(objectId) {
      return this.activateSemanticEnumSet(objectId);
    },
    listTypedSemanticBindings(status = null, options = {}) {
      return requestJson(
        `/semantic/bindings${semanticListQuery(status, options)}`,
        'Typed Binding Catalog unavailable.'
      );
    },
    getTypedSemanticBinding(objectId) {
      return requestJson(
        `/semantic/bindings/${encodeURIComponent(objectId)}`,
        'Typed Binding detail unavailable.'
      );
    },
    createTypedSemanticBinding(payload) {
      return sendJson('/semantic/bindings', 'POST', payload, 'Create Typed Binding failed.');
    },
    updateTypedSemanticBinding(objectId, payload) {
      return sendJson(
        `/semantic/bindings/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Typed Binding failed.'
      );
    },
    validateTypedSemanticBinding(objectId) {
      return sendJson(
        `/semantic/bindings/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Typed Binding failed.'
      );
    },
    activateTypedSemanticBinding(objectId) {
      return sendJson(
        `/semantic/bindings/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Typed Binding failed.'
      );
    },
    deprecateTypedSemanticBinding(objectId) {
      return sendJson(
        `/semantic/bindings/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Typed Binding failed.'
      );
    },
    publishTypedSemanticBinding(objectId) {
      return this.activateTypedSemanticBinding(objectId);
    },
    listCompatibilityProfiles(status = null) {
      return requestJson(
        `/compiler/compatibility-profiles${status ? `?status=${encodeURIComponent(status)}` : ''}`,
        'Compatibility Profile Catalog unavailable.'
      );
    },
    getCompatibilityProfile(objectId) {
      return requestJson(
        `/compiler/compatibility-profiles/${encodeURIComponent(objectId)}`,
        'Compatibility Profile detail unavailable.'
      );
    },
    createCompatibilityProfile(payload) {
      return sendJson(
        '/compiler/compatibility-profiles',
        'POST',
        payload,
        'Create Compatibility Profile failed.'
      );
    },
    updateCompatibilityProfile(objectId, payload) {
      return sendJson(
        `/compiler/compatibility-profiles/${encodeURIComponent(objectId)}`,
        'PUT',
        payload,
        'Update Compatibility Profile failed.'
      );
    },
    validateCompatibilityProfile(objectId) {
      return sendJson(
        `/compiler/compatibility-profiles/${encodeURIComponent(objectId)}/validate`,
        'POST',
        null,
        'Validate Compatibility Profile failed.'
      );
    },
    activateCompatibilityProfile(objectId) {
      return sendJson(
        `/compiler/compatibility-profiles/${encodeURIComponent(objectId)}/activate`,
        'POST',
        null,
        'Activate Compatibility Profile failed.'
      );
    },
    deprecateCompatibilityProfile(objectId) {
      return sendJson(
        `/compiler/compatibility-profiles/${encodeURIComponent(objectId)}/deprecate`,
        'POST',
        null,
        'Deprecate Compatibility Profile failed.'
      );
    },
    publishCompatibilityProfile(objectId) {
      return this.activateCompatibilityProfile(objectId);
    },
    resolveSemantic(name) {
      return requestJson(`/semantic/resolve/${encodeURIComponent(name)}`, 'Resolve failed.');
    },
    getCatalogGraph(root) {
      return requestJson(
        `/catalog/graph?root=${encodeURIComponent(root)}&depth=2`,
        'Catalog Graph unavailable.'
      );
    },
    getPlannerContext(sessionId) {
      return requestJson(
        `/sessions/${encodeURIComponent(sessionId)}/planner-context`,
        'Planner Context unavailable.'
      );
    },
    async listApprovals(params) {
      const query = params ? `?${new URLSearchParams(params)}` : '';
      return requestJson(`/approvals${query}`, 'Approvals badge unavailable.');
    },
    listPolicies() {
      return requestJson('/policies', 'Policies unavailable.');
    },
    getPolicy(policyId) {
      return requestJson(`/policies/${encodeURIComponent(policyId)}`, 'Policy detail unavailable.');
    },
    createPolicy(payload) {
      return sendJson('/policies', 'POST', payload, 'Create Policy failed.');
    },
    updatePolicy(policyId, payload) {
      return sendJson(
        `/policies/${encodeURIComponent(policyId)}`,
        'PUT',
        payload,
        'Update Policy failed.'
      );
    },
    deletePolicy(policyId) {
      return sendDelete(`/policies/${encodeURIComponent(policyId)}`, 'Delete Policy failed.');
    },
    listQualityRules(params = {}) {
      const search = new URLSearchParams();
      if (params.table) search.set('table', params.table);
      const query = search.toString();
      return requestJson(`/quality-rules${query ? `?${query}` : ''}`, 'Quality Rules unavailable.');
    },
    createQualityRule(payload) {
      return sendJson('/quality-rules', 'POST', payload, 'Create Quality Rule failed.');
    },
    deleteQualityRule(ruleId) {
      return sendDelete(
        `/quality-rules/${encodeURIComponent(ruleId)}`,
        'Delete Quality Rule failed.'
      );
    },
    getApproval(requestId) {
      return requestJson(
        `/approvals/${encodeURIComponent(requestId)}`,
        'Approval detail unavailable.'
      );
    },
    approveApproval(requestId, payload) {
      return sendJson(
        `/approvals/${encodeURIComponent(requestId)}/approve`,
        'POST',
        payload,
        'Approve request failed.'
      );
    },
    rejectApproval(requestId, payload) {
      return sendJson(
        `/approvals/${encodeURIComponent(requestId)}/reject`,
        'POST',
        payload,
        'Reject request failed.'
      );
    },
    autoFlagApprovals(sessionId, payload) {
      return sendJson(
        `/sessions/${encodeURIComponent(sessionId)}/approvals/auto-flag`,
        'POST',
        payload,
        'Auto-flag approvals failed.'
      );
    },
    governanceCheck(payload) {
      return sendJson('/governance/check', 'POST', payload, 'Governance Check failed.');
    },
    routingResolve(payload) {
      return sendJson('/routing/resolve', 'POST', payload, 'Routing Resolve failed.');
    },
  };
}
