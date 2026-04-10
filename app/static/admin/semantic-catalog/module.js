import { createCoreSemanticCatalogConfig } from "./core-config.js";
import { createSupportingSemanticCatalogConfig } from "./supporting-config.js";

export function createSemanticCatalogModule(ctx) {
  const { shared } = ctx;
  const {
    esc,
    toast,
    renderEmptyState,
    renderStructuredError,
    renderJsonPanel,
    renderDetailList,
    renderResultsCount,
    renderAdminTableCard,
    renderAdminDetailCard,
    renderAdminListDetailLayout,
    normalizeApiError,
    statusBadge,
    fmtDate,
    openDangerConfirm,
  } = shared;

  const SEMANTIC_CATALOG_CONFIG = {
    ...createCoreSemanticCatalogConfig(statusBadge),
    ...createSupportingSemanticCatalogConfig(statusBadge),
  };

  let semanticCatalogRenderVersion = 0;
  const semanticCatalogUiState = {
    statusBySubtab: {},
    formModeBySubtab: {},
    relatedBindingsFilter: null,
    focusRefBySubtab: {},
    helperDataByKey: {},
    publishErrorsByKey: {},
    formErrorsByKey: {},
  };

  function countStatuses(items) {
    return (items || []).reduce((acc, item) => {
      const status = String(item?.status || "unknown").toLowerCase();
      acc[status] = (acc[status] || 0) + 1;
      return acc;
    }, {});
  }

  function formatMaybeDate(value) {
    return value ? fmtDate(value) : "-";
  }

  function extractItems(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
  }

  function semanticConfigForSubtab(subtab) {
    return SEMANTIC_CATALOG_CONFIG[subtab] || SEMANTIC_CATALOG_CONFIG.entities;
  }

  function semanticStatusFilterForSubtab(subtab) {
    return semanticCatalogUiState.statusBySubtab[subtab] || "all";
  }

  function semanticFormModeForSubtab(subtab) {
    return semanticCatalogUiState.formModeBySubtab[subtab] || "create";
  }

  function semanticFocusedRefForSubtab(subtab) {
    return semanticCatalogUiState.focusRefBySubtab[subtab] || "";
  }

  function semanticObjectId(config, item) {
    return String(item?.[config.idField] || "");
  }

  function semanticStableRef(config, item) {
    return String(config.stableRef(item) || "");
  }

  function semanticDisplayName(config, item) {
    return String(config.displayName(item) || semanticStableRef(config, item) || semanticObjectId(config, item));
  }

  function semanticHelperKey(subtab, objectId) {
    return `${subtab}:${objectId || "none"}`;
  }

  function semanticHelperState(subtab, objectId) {
    return semanticCatalogUiState.helperDataByKey[semanticHelperKey(subtab, objectId)] || {};
  }

  function resetSemanticHelperState(subtab, objectId) {
    semanticCatalogUiState.helperDataByKey[semanticHelperKey(subtab, objectId)] = {
      resolveResult: null,
      resolveError: null,
      graphResult: null,
      graphError: null,
      plannerContextResult: null,
      plannerContextError: null,
      plannerContextSessionId: "",
    };
  }

  function semanticPublishError(subtab, objectId) {
    return semanticCatalogUiState.publishErrorsByKey[semanticHelperKey(subtab, objectId)] || null;
  }

  function setSemanticPublishError(subtab, objectId, error) {
    semanticCatalogUiState.publishErrorsByKey[semanticHelperKey(subtab, objectId)] = error;
  }

  function semanticFormErrorKey(subtab, mode, objectId) {
    return `${subtab}:${mode}:${objectId || "create"}`;
  }

  function semanticFormError(subtab, mode, objectId) {
    return semanticCatalogUiState.formErrorsByKey[semanticFormErrorKey(subtab, mode, objectId)] || null;
  }

  function setSemanticFormError(subtab, mode, objectId, error) {
    semanticCatalogUiState.formErrorsByKey[semanticFormErrorKey(subtab, mode, objectId)] = error;
  }

  function semanticEndpointForSubtab(subtab) {
    const lookup = {
      entities: {
        list: (status) => ctx.adminApi.listSemanticEntities(status),
        get: (objectId) => ctx.adminApi.getSemanticEntity(objectId),
        create: (payload) => ctx.adminApi.createSemanticEntity(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticEntity(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticEntity(objectId),
      },
      metrics: {
        list: (status) => ctx.adminApi.listSemanticMetrics(status),
        get: (objectId) => ctx.adminApi.getSemanticMetric(objectId),
        create: (payload) => ctx.adminApi.createSemanticMetric(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticMetric(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticMetric(objectId),
      },
      "process-objects": {
        list: (status) => ctx.adminApi.listSemanticProcessObjects(status),
        get: (objectId) => ctx.adminApi.getSemanticProcessObject(objectId),
        create: (payload) => ctx.adminApi.createSemanticProcessObject(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticProcessObject(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticProcessObject(objectId),
      },
      dimensions: {
        list: (status) => ctx.adminApi.listSemanticDimensions(status),
        get: (objectId) => ctx.adminApi.getSemanticDimension(objectId),
        create: (payload) => ctx.adminApi.createSemanticDimension(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticDimension(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticDimension(objectId),
      },
      time: {
        list: (status) => ctx.adminApi.listSemanticTime(status),
        get: (objectId) => ctx.adminApi.getSemanticTime(objectId),
        create: (payload) => ctx.adminApi.createSemanticTime(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticTime(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticTime(objectId),
      },
      "enum-sets": {
        list: (status) => ctx.adminApi.listSemanticEnumSets(status),
        get: (objectId) => ctx.adminApi.getSemanticEnumSet(objectId),
        create: (payload) => ctx.adminApi.createSemanticEnumSet(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticEnumSet(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishSemanticEnumSet(objectId),
      },
      "typed-bindings": {
        list: (status) => ctx.adminApi.listTypedSemanticBindings(status),
        get: (objectId) => ctx.adminApi.getTypedSemanticBinding(objectId),
        create: (payload) => ctx.adminApi.createTypedSemanticBinding(payload),
        update: (objectId, payload) => ctx.adminApi.updateTypedSemanticBinding(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishTypedSemanticBinding(objectId),
      },
      "compatibility-profiles": {
        list: (status) => ctx.adminApi.listCompatibilityProfiles(status),
        get: (objectId) => ctx.adminApi.getCompatibilityProfile(objectId),
        create: (payload) => ctx.adminApi.createCompatibilityProfile(payload),
        update: (objectId, payload) => ctx.adminApi.updateCompatibilityProfile(objectId, payload),
        publish: (objectId) => ctx.adminApi.publishCompatibilityProfile(objectId),
      },
    };
    return lookup[subtab] || lookup.entities;
  }

  function semanticRelatedBindingsFilterLabel(filter) {
    if (!filter?.ref) return "-";
    return filter.originSubtab ? `${filter.ref} (${filter.originSubtab})` : filter.ref;
  }

  function semanticListMatchesRelatedBindingFilter(item, filter) {
    const filterRef = filter?.ref;
    if (!filterRef) return true;
    const bindingRefs = [
      item?.header?.bound_object_ref,
      ...(item?.interface_contract?.imports || []).map((entry) => entry.binding_ref),
      ...(item?.interface_contract?.carrier_bindings || []).flatMap((entry) => [
        entry.primary_entity_ref,
        entry.source_object_ref,
        entry.carrier_locator,
      ]),
      ...(item?.interface_contract?.field_bindings || []).flatMap((entry) => [
        entry.semantic_ref,
        entry.surface_ref,
        entry?.target?.target_key,
      ]),
    ].filter(Boolean);
    return bindingRefs.includes(filterRef);
  }

  function semanticListMatchesFocusedRef(config, item, filterRef) {
    if (!filterRef) return true;
    if (typeof config.matchesFocusRef === "function") {
      return config.matchesFocusRef(item, filterRef);
    }
    const refs = [
      semanticStableRef(config, item),
      semanticDisplayName(config, item),
      semanticObjectId(config, item),
      ...(config.relatedRefs(item) || []).map((entry) => entry.ref),
    ].filter(Boolean);
    return refs.includes(filterRef);
  }

  function semanticSubtabForRef(ref) {
    if (String(ref).startsWith("entity.")) return "entities";
    if (String(ref).startsWith("metric.")) return "metrics";
    if (String(ref).startsWith("process.")) return "process-objects";
    if (String(ref).startsWith("dimension.")) return "dimensions";
    if (String(ref).startsWith("time.")) return "time";
    if (String(ref).startsWith("enum.")) return "enum-sets";
    if (String(ref).startsWith("binding.")) return "typed-bindings";
    return "";
  }

  function renderSemanticStatusFilters(subtab, items) {
    const counts = countStatuses(items);
    const current = semanticStatusFilterForSubtab(subtab);
    const options = [
      { value: "all", label: "All statuses", count: items.length },
      { value: "draft", label: "Draft", count: counts.draft || 0 },
      { value: "published", label: "Published", count: counts.published || 0 },
    ];
    return `
      <div class="detail-actions">
        ${options
          .map(
            (option) => `
          <button
            type="button"
            class="btn btn-sm ${current === option.value ? "btn-primary" : ""}"
            data-action="set-semantic-status"
            data-status="${option.value}"
          >${esc(option.label)} (${esc(String(option.count))})</button>
        `
          )
          .join("")}
      </div>
    `;
  }

  function renderSemanticCatalogRows(config, items, selectedObjectId) {
    if (!items.length) {
      return `
        <tr>
          <td colspan="6">${renderEmptyState(`No ${config.label.toLowerCase()} found for the current status filter.`)}</td>
        </tr>
      `;
    }
    return items
      .map((item) => {
        const objectId = semanticObjectId(config, item);
        const stableRef = semanticStableRef(config, item) || "-";
        const displayName = semanticDisplayName(config, item) || "-";
        const isSelected = objectId === selectedObjectId;
        return `
          <tr class="${isSelected ? "is-selected" : ""}">
            <td>
              <button
                type="button"
                class="selectable-list-item ${isSelected ? "is-active" : ""}"
                data-action="select-semantic-object"
                data-object-id="${esc(objectId)}"
              >
                <span class="selectable-list-title">${esc(objectId)}</span>
                <span class="selectable-list-meta">${esc(stableRef)}</span>
              </button>
            </td>
            <td>${esc(stableRef)}</td>
            <td>${esc(displayName)}</td>
            <td>${statusBadge(item.status)}</td>
            <td>${esc(String(item.revision ?? "-"))}</td>
            <td>${esc(formatMaybeDate(item.updated_at))}</td>
          </tr>
        `;
      })
      .join("");
  }

  function renderSemanticCatalogListCard(route, viewModel) {
    const config = viewModel.config;
    const filterState = viewModel.relatedBindingsFilter;
    const focusedRef = viewModel.focusedRef;
    const filterNote =
      route.subtab === "typed-bindings" && filterState?.ref
        ? `Filtered to bindings grounded by ${filterState.ref}.`
        : focusedRef
          ? `Filtered to objects related to ${focusedRef}.`
          : `${config.listEndpointLabel} backs the shared Semantic Catalog list contract.`;
    return renderAdminTableCard({
      title: config.listTitle,
      count: viewModel.items.length,
      countLabel: "object(s)",
      note: filterNote,
      columns: ["object_id", "stable_ref", "display_name", "status", "revision", "updated_at"],
      actionsHtml: `
        <div class="detail-actions">
          <button type="button" class="btn btn-sm btn-primary" data-action="open-semantic-form" data-form-mode="create">Create ${esc(config.singularLabel)}</button>
          ${
            viewModel.selectedItem && String(viewModel.selectedItem.status || "").toLowerCase() !== "published"
              ? '<button type="button" class="btn btn-sm" data-action="open-semantic-form" data-form-mode="edit">Edit Selected</button>'
              : ""
          }
          ${
            route.subtab === "typed-bindings" && filterState?.ref
              ? '<button type="button" class="btn btn-sm" data-action="clear-related-bindings-filter">Clear Related Binding Filter</button>'
              : ""
          }
          ${focusedRef ? '<button type="button" class="btn btn-sm" data-action="clear-semantic-focus-ref">Clear Related Object Filter</button>' : ""}
        </div>
      `,
      countHtml:
        renderResultsCount(viewModel.items.length, "object(s)") +
        renderSemanticStatusFilters(route.subtab, viewModel.unfilteredItems),
      rowsHtml: renderSemanticCatalogRows(config, viewModel.items, viewModel.selectedObjectId),
      errorHtml: viewModel.listError ? renderStructuredError(viewModel.listError, `${config.label} unavailable.`) : "",
    });
  }

  function renderSemanticLifecycleCard(selectedItem, config, publishError) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Lifecycle Summary",
        statusHtml: '<span class="shell-chip">no object selected</span>',
        note: "Select an object to inspect draft/published lifecycle, publish guardrails, and structured publish errors.",
        bodyHtml: renderEmptyState("Select a semantic object to inspect lifecycle and publish controls."),
      });
    }
    const isPublished = String(selectedItem.status || "").toLowerCase() === "published";
    return renderAdminDetailCard({
      title: "Lifecycle Summary",
      statusHtml: statusBadge(selectedItem.status),
      note: isPublished
        ? "Published objects are frozen and stay read-only in T7. Published revision freeze state is explicit in the mixed form shell."
        : "Draft objects can publish through the shared confirmation flow. Publish failures render structured error details instead of raw transport text.",
      bodyHtml: `
        ${renderDetailList([
          { label: "status", valueHtml: statusBadge(selectedItem.status) },
          { label: "revision", value: String(selectedItem.revision ?? "-") },
          { label: "updated_at", value: formatMaybeDate(selectedItem.updated_at) },
          {
            label: "freeze_rule",
            value: isPublished ? "published objects are read-only" : "draft objects remain editable in the shared form shell",
          },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-danger" data-action="publish-semantic-object" ${isPublished ? "disabled" : ""}>Publish</button>
        </div>
        ${publishError ? renderStructuredError(publishError, "Publish failed.") : ""}
        <p class="panel-note">${esc(`Publish ${config.singularLabel} uses the shared confirmation flow and keeps publish failure detail structured.`)}</p>
      `,
    });
  }

  function renderSemanticHelperActionsCard(selectedItem, viewModel) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Dependency Helpers",
        statusHtml: "<span class=\"shell-chip\">Resolve / View Related Bindings / View Catalog Graph</span>",
        note: "Resolve, View Related Bindings, and View Catalog Graph stay in the shared shell so later object pages do not re-implement helper entrypoints.",
        bodyHtml: renderEmptyState("Select an object to run Resolve, View Related Bindings, or View Catalog Graph."),
      });
    }
    const stableRef = semanticStableRef(viewModel.config, selectedItem);
    const helperState = viewModel.helperState;
    const resolveDisabled = stableRef ? "" : "disabled";
    const relatedDisabled = stableRef ? "" : "disabled";
    return renderAdminDetailCard({
      title: "Dependency Helpers",
      statusHtml: "<span class=\"shell-chip\">Resolve / View Related Bindings / View Catalog Graph</span>",
      note: "Resolve uses GET /semantic/resolve/{name}. View Related Bindings reuses the typed binding list. View Catalog Graph uses GET /catalog/graph. Planner Context stays an on-demand helper for dependency debugging only.",
      bodyHtml: `
        ${renderDetailList([
          { label: "stable_ref", value: stableRef || "-" },
          { label: "helper_scope", value: viewModel.config.label },
          { label: "binding_filter", value: semanticRelatedBindingsFilterLabel(viewModel.relatedBindingsFilter) },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-sm" data-action="semantic-resolve" ${resolveDisabled}>Resolve</button>
          <button type="button" class="btn btn-sm" data-action="view-related-bindings" ${relatedDisabled}>View Related Bindings</button>
          <button type="button" class="btn btn-sm" data-action="view-catalog-graph">View Catalog Graph</button>
        </div>
        ${helperState.resolveError ? renderStructuredError(helperState.resolveError, "Resolve failed.") : ""}
        ${helperState.resolveResult ? renderJsonPanel("Resolve Result", helperState.resolveResult, "No resolve result.") : ""}
        ${helperState.graphError ? renderStructuredError(helperState.graphError, "Catalog Graph failed.") : ""}
        ${helperState.graphResult ? renderJsonPanel("Catalog Graph", helperState.graphResult, "No graph result.") : ""}
        ${
          ["metrics", "process-objects", "compatibility-profiles"].includes(viewModel.route.subtab)
            ? `
            <div class="admin-shell-card">
              <h3>Planner Context Helper</h3>
              <p class="panel-note">GET /sessions/{session_id}/planner-context remains optional and only helps explain dependencies when Resolve and Graph are insufficient.</p>
              <label>
                Session ID
                <input type="text" data-role="planner-context-session-id" value="${esc(helperState.plannerContextSessionId || "")}" placeholder="sess_..." />
              </label>
              <div class="detail-actions">
                <button type="button" class="btn btn-sm" data-action="semantic-planner-context">Load Planner Context</button>
              </div>
              ${helperState.plannerContextError ? renderStructuredError(helperState.plannerContextError, "Planner Context failed.") : ""}
              ${helperState.plannerContextResult ? renderJsonPanel("Planner Context", helperState.plannerContextResult, "No planner context result.") : ""}
            </div>
          `
            : ""
        }
      `,
    });
  }

  function renderSemanticObjectSummaryCard(viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Object Summary",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Each Semantic Catalog subtab reuses the same summary shape: object_id, stable_ref, display_name, status, revision, updated_at.",
        bodyHtml: renderEmptyState("Select an object from the list to inspect summary fields and raw JSON."),
      });
    }
    return renderAdminDetailCard({
      title: "Object Summary",
      statusHtml: statusBadge(selectedItem.status),
      note: `${viewModel.config.singularLabel} summary now includes object-specific contract fields instead of only the shared list columns.`,
      bodyHtml: `
        ${renderDetailList(viewModel.config.detailFields(selectedItem))}
        ${renderJsonPanel("Raw JSON Panel", selectedItem, "No object payload.")}
      `,
    });
  }

  function renderSemanticContractSummaryCard(viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Interface / Payload Summary",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Core four pages and supporting pages all expose object-specific interface / payload summaries before raw JSON inspection.",
        bodyHtml: renderEmptyState("Select an object to inspect interface and payload summary fields."),
      });
    }
    const summaryFields = viewModel.config.interfaceSummaryFields?.(selectedItem) || [];
    return renderAdminDetailCard({
      title: viewModel.config.interfaceSummaryTitle || "Interface / Payload Summary",
      statusHtml: "<span class=\"shell-chip\">contract summary</span>",
      note: "All eight T7 object pages keep structured contract summaries readable in the detail pane while nested contracts remain available in JSON editors.",
      bodyHtml: summaryFields.length
        ? renderDetailList(summaryFields)
        : renderEmptyState("No additional interface or payload summary fields for this object."),
    });
  }

  function renderSemanticOperatorGuidanceCard(viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (typeof viewModel.config.operatorGuidanceFields !== "function") {
      return "";
    }
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: viewModel.config.operatorGuidanceTitle || "Operator Guidance",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Object-specific operator guidance stays with each subpage instead of being buried in raw JSON.",
        bodyHtml: renderEmptyState("Select an object to inspect object-specific operator guidance."),
      });
    }
    const guidanceFields = viewModel.config.operatorGuidanceFields(selectedItem) || [];
    return renderAdminDetailCard({
      title: viewModel.config.operatorGuidanceTitle || "Operator Guidance",
      statusHtml: "<span class=\"shell-chip\">object-specific hints</span>",
      note:
        viewModel.config.operatorGuidanceNote ||
        "Operator guidance calls out the parts of this object that most often block publish or downstream grounding.",
      bodyHtml: guidanceFields.length
        ? renderDetailList(guidanceFields)
        : renderEmptyState("No additional operator guidance for this object."),
    });
  }

  function renderSemanticRelationshipSummaryCard(viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Relationship Summary",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Object-specific dependency summaries and related ref jumps are available once an object is selected.",
        bodyHtml: renderEmptyState("Select an object to inspect related semantic refs, carriers, and dependency links."),
      });
    }
    const relationshipFields = viewModel.config.relationshipFields(selectedItem) || [];
    const relatedRefs = viewModel.config.relatedRefs(selectedItem) || [];
    return renderAdminDetailCard({
      title: "Relationship Summary",
      statusHtml: "<span class=\"shell-chip\">related refs</span>",
      note: "Suggested relation jumps keep typed semantic objects, typed bindings, and compatibility profiles connected without exposing raw SQL or legacy mapping flows.",
      bodyHtml: `
        ${relationshipFields.length ? renderDetailList(relationshipFields) : renderEmptyState("No additional relationship metadata for this object.")}
        <div class="detail-actions">
          ${
            relatedRefs.length
              ? relatedRefs
                  .map(
                    (entry) =>
                      `<button type="button" class="btn btn-sm" data-action="jump-semantic-ref" data-semantic-ref="${esc(entry.ref)}">${esc(entry.label)}</button>`
                  )
                  .join(" ")
              : '<span class="shell-chip">No related semantic refs</span>'
          }
        </div>
      `,
    });
  }

  function renderSemanticFieldInput(field, disabled) {
    if (field.multiline) {
      return `
        <label>
          ${esc(field.label)}
          <textarea name="${esc(field.name)}" ${disabled ? "disabled" : ""} placeholder="${esc(field.placeholder || "")}">${esc(field.value || "")}</textarea>
        </label>
      `;
    }
    return `
      <label>
        ${esc(field.label)}
        <input name="${esc(field.name)}" type="text" value="${esc(field.value || "")}" ${disabled ? "disabled" : ""} placeholder="${esc(field.placeholder || "")}" />
      </label>
    `;
  }

  function renderSemanticJsonInput(field, disabled) {
    return `
      <label>
        ${esc(field.label)}
        <textarea name="${esc(field.name)}" ${disabled ? "disabled" : ""} placeholder="${esc(field.placeholder || "")}">${esc(field.value || "")}</textarea>
      </label>
    `;
  }

  function renderSemanticFormShellCard(viewModel) {
    const mode = semanticFormModeForSubtab(viewModel.route.subtab);
    const selectedItem = viewModel.selectedItem;
    const isPublished = String(selectedItem?.status || "").toLowerCase() === "published";
    const editDisabled = !selectedItem || isPublished;
    const formFields = viewModel.config.formFields(selectedItem, mode);
    const jsonFields = viewModel.config.jsonFields(selectedItem, mode);
    const formError = semanticFormError(viewModel.route.subtab, mode, viewModel.selectedObjectId);
    const readOnly = mode === "edit" && isPublished;
    const title = mode === "edit" ? `Edit ${viewModel.config.singularLabel}` : `Create ${viewModel.config.singularLabel}`;
    return renderAdminDetailCard({
      title,
      statusHtml: `<span class="shell-chip">${esc(mode === "edit" ? "edit-mode" : "create-mode")}</span>`,
      note: `${viewModel.config.createEndpointLabel} and ${viewModel.config.updateEndpointLabel} back this mixed form. Structured fields cover stable refs and high-signal metadata; nested contracts stay in JSON editors.`,
      bodyHtml: `
        ${renderDetailList([
          { label: "subtab", value: viewModel.config.label },
          { label: "form_mode", value: mode },
          { label: "selected_object", value: selectedItem ? semanticObjectId(viewModel.config, selectedItem) : "-" },
          { label: "freeze_rule", value: isPublished ? "published revision freeze" : "draft objects can create, save, and publish" },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-sm btn-primary" data-action="open-semantic-form" data-form-mode="create">Create</button>
          <button type="button" class="btn btn-sm" data-action="open-semantic-form" data-form-mode="edit" ${editDisabled ? "disabled" : ""}>Edit</button>
        </div>
        ${mode === "edit" && !selectedItem ? renderEmptyState(`Select a ${viewModel.config.singularLabel.toLowerCase()} before editing.`) : `
          <form class="source-form-grid" data-role="semantic-form">
            ${formFields.map((field) => renderSemanticFieldInput(field, readOnly)).join("")}
            ${jsonFields.map((field) => renderSemanticJsonInput(field, readOnly)).join("")}
            ${readOnly ? '<p class="panel-note">Published revision freeze keeps edit fields read-only until a new draft is created server-side.</p>' : ""}
            ${formError ? renderStructuredError(formError, `${title} failed.`) : ""}
            <div class="detail-actions">
              <button type="submit" class="btn btn-primary" data-role="submit-semantic-form" ${readOnly ? "disabled" : ""}>${esc(mode === "edit" ? "Save Changes" : `Create ${viewModel.config.singularLabel}`)}</button>
            </div>
          </form>
        `}
      `,
    });
  }

  function renderBody(viewModel) {
    return renderAdminListDetailLayout({
      primaryHtml: renderSemanticCatalogListCard(viewModel.route, viewModel),
      secondaryHtml: renderSemanticLifecycleCard(viewModel.selectedItem, viewModel.config, viewModel.publishError),
      detailHtml: `
        ${renderSemanticObjectSummaryCard(viewModel)}
        ${renderSemanticContractSummaryCard(viewModel)}
        ${renderSemanticRelationshipSummaryCard(viewModel)}
        ${renderSemanticOperatorGuidanceCard(viewModel)}
        ${renderSemanticHelperActionsCard(viewModel.selectedItem, viewModel)}
        ${renderSemanticFormShellCard(viewModel)}
      `,
    });
  }

  function render(route) {
    const config = semanticConfigForSubtab(route.subtab || "entities");
    return `<div data-role="semantic-catalog-body">${renderBody({
      route,
      config,
      items: [],
      unfilteredItems: [],
      selectedObjectId: route.objectId || "",
      selectedItem: null,
      listError: null,
      publishError: null,
      helperState: {},
      relatedBindingsFilter: semanticCatalogUiState.relatedBindingsFilter,
      focusedRef: semanticFocusedRefForSubtab(route.subtab),
    })}</div>`;
  }

  async function runSemanticResolve(route, selectedItem) {
    const config = semanticConfigForSubtab(route.subtab);
    const stableRef = semanticStableRef(config, selectedItem);
    if (!stableRef) {
      toast("Resolve requires a stable_ref.", "error");
      return;
    }
    const helperKey = semanticHelperKey(route.subtab, route.objectId);
    const helperState = semanticCatalogUiState.helperDataByKey[helperKey] || {};
    helperState.resolveError = null;
    helperState.resolveResult = null;
    semanticCatalogUiState.helperDataByKey[helperKey] = helperState;
    try {
      helperState.resolveResult = await ctx.adminApi.resolveSemantic(stableRef);
    } catch (error) {
      helperState.resolveError = normalizeApiError(error, "Resolve failed.");
    }
    ctx.renderCurrentRoute();
  }

  async function runSemanticCatalogGraph(route, selectedItem) {
    const config = semanticConfigForSubtab(route.subtab);
    const root = semanticStableRef(config, selectedItem) || semanticObjectId(config, selectedItem);
    if (!root) {
      toast("Catalog Graph requires a selected object.", "error");
      return;
    }
    const helperKey = semanticHelperKey(route.subtab, route.objectId);
    const helperState = semanticCatalogUiState.helperDataByKey[helperKey] || {};
    helperState.graphError = null;
    helperState.graphResult = null;
    semanticCatalogUiState.helperDataByKey[helperKey] = helperState;
    try {
      helperState.graphResult = await ctx.adminApi.getCatalogGraph(root);
    } catch (error) {
      helperState.graphError = normalizeApiError(error, "Catalog Graph failed.");
    }
    ctx.renderCurrentRoute();
  }

  async function runSemanticPlannerContext(route, sessionId) {
    if (!sessionId) {
      toast("Planner Context requires a session_id.", "error");
      return;
    }
    const helperKey = semanticHelperKey(route.subtab, route.objectId);
    const helperState = semanticCatalogUiState.helperDataByKey[helperKey] || {};
    helperState.plannerContextSessionId = sessionId;
    helperState.plannerContextError = null;
    helperState.plannerContextResult = null;
    semanticCatalogUiState.helperDataByKey[helperKey] = helperState;
    try {
      helperState.plannerContextResult = await ctx.adminApi.getPlannerContext(sessionId);
    } catch (error) {
      helperState.plannerContextError = normalizeApiError(error, "Planner Context failed.");
    }
    ctx.renderCurrentRoute();
  }

  function handleViewRelatedBindings(route, selectedItem) {
    const config = semanticConfigForSubtab(route.subtab);
    const stableRef = semanticStableRef(config, selectedItem);
    if (!stableRef) {
      toast("View Related Bindings requires a stable_ref.", "error");
      return;
    }
    semanticCatalogUiState.relatedBindingsFilter = {
      ref: stableRef,
      originSubtab: route.subtab,
    };
    semanticCatalogUiState.formModeBySubtab["typed-bindings"] = "edit";
    ctx.applyAdminRoute(
      { ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: "typed-bindings", objectId: "", bindingId: "" },
      "push"
    );
  }

  function handleJumpSemanticRef(ref) {
    const targetSubtab = semanticSubtabForRef(ref);
    if (!targetSubtab) {
      toast(`No Semantic Catalog target for ${ref}.`, "error");
      return;
    }
    semanticCatalogUiState.focusRefBySubtab[targetSubtab] = ref;
    ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: targetSubtab, objectId: "", bindingId: "" }, "push");
  }

  function handlePublishSemanticObject(route, viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      toast("Select an object before publishing.", "error");
      return;
    }
    const objectId = semanticObjectId(viewModel.config, selectedItem);
    const stableRef = semanticStableRef(viewModel.config, selectedItem);
    openDangerConfirm({
      title: `Publish ${viewModel.config.singularLabel}`,
      objectLabel: objectId,
      impactScope: "Promotes the current draft into the published semantic catalog and freezes further edits for this revision.",
      reversible: "No",
      confirmLabel: "Publish",
      detailsHtml: renderDetailList([
        { label: "object_id", value: objectId },
        { label: "stable_ref", value: stableRef || "-" },
        { label: "status", value: selectedItem.status || "-" },
        { label: "warning", value: "Publish failures surface structured validation or compatibility errors." },
      ]),
      onConfirm: async () => {
        try {
          setSemanticPublishError(route.subtab, objectId, null);
          await semanticEndpointForSubtab(route.subtab).publish(objectId);
          toast("Semantic object published.", "success");
          await refreshCurrentSemanticCatalog();
        } catch (error) {
          const normalized = normalizeApiError(error, "Publish failed.");
          setSemanticPublishError(route.subtab, objectId, normalized);
          toast(normalized.message, "error");
          ctx.renderCurrentRoute();
        }
      },
    });
  }

  function semanticFormValues(form) {
    return Array.from(new FormData(form).entries()).reduce((acc, [key, value]) => {
      acc[key] = typeof value === "string" ? value : "";
      return acc;
    }, {});
  }

  async function handleSemanticFormSubmit(viewModel, form) {
    const mode = semanticFormModeForSubtab(viewModel.route.subtab);
    const endpoints = semanticEndpointForSubtab(viewModel.route.subtab);
    const objectId = viewModel.selectedObjectId;
    try {
      setSemanticFormError(viewModel.route.subtab, mode, objectId, null);
      const values = semanticFormValues(form);
      const payload =
        mode === "edit"
          ? viewModel.config.buildUpdatePayload(values, viewModel.selectedItem)
          : viewModel.config.buildCreatePayload(values, viewModel.selectedItem);
      const result = mode === "edit" ? await endpoints.update(objectId, payload) : await endpoints.create(payload);
      const nextObjectId = semanticObjectId(viewModel.config, result) || objectId;
      semanticCatalogUiState.formModeBySubtab[viewModel.route.subtab] = "edit";
      toast(`${viewModel.config.singularLabel} ${mode === "edit" ? "updated" : "created"}.`, "success");
      ctx.applyAdminRoute(
        { ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: viewModel.route.subtab, objectId: nextObjectId, bindingId: "" },
        "replace"
      );
    } catch (error) {
      const normalized =
        error instanceof Error
          ? { message: error.message, detail: error.message }
          : normalizeApiError(error, `${mode === "edit" ? "Update" : "Create"} failed.`);
      setSemanticFormError(viewModel.route.subtab, mode, objectId, normalized);
      ctx.renderCurrentRoute();
    }
  }

  function bindEvents(panel, viewModel) {
    panel.querySelectorAll('[data-action="set-semantic-status"]').forEach((button) => {
      button.addEventListener("click", () => {
        semanticCatalogUiState.statusBySubtab[viewModel.route.subtab] = button.dataset.status || "all";
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: viewModel.route.subtab, objectId: "" }, "replace");
      });
    });

    panel.querySelectorAll('[data-action="select-semantic-object"]').forEach((button) => {
      button.addEventListener("click", () => {
        resetSemanticHelperState(viewModel.route.subtab, button.dataset.objectId || "");
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: viewModel.route.subtab, objectId: button.dataset.objectId || "" }, "replace");
      });
    });

    panel.querySelectorAll('[data-action="open-semantic-form"]').forEach((button) => {
      button.addEventListener("click", () => {
        semanticCatalogUiState.formModeBySubtab[viewModel.route.subtab] = button.dataset.formMode || "create";
        setSemanticFormError(viewModel.route.subtab, button.dataset.formMode || "create", viewModel.selectedObjectId, null);
        ctx.renderCurrentRoute();
      });
    });

    panel.querySelectorAll('[data-action="publish-semantic-object"]').forEach((button) => {
      button.addEventListener("click", () => {
        handlePublishSemanticObject(viewModel.route, viewModel);
      });
    });

    panel.querySelectorAll('[data-action="semantic-resolve"]').forEach((button) => {
      button.addEventListener("click", async () => {
        await runSemanticResolve(viewModel.route, viewModel.selectedItem);
      });
    });

    panel.querySelectorAll('[data-action="view-catalog-graph"]').forEach((button) => {
      button.addEventListener("click", async () => {
        await runSemanticCatalogGraph(viewModel.route, viewModel.selectedItem);
      });
    });

    panel.querySelectorAll('[data-action="view-related-bindings"]').forEach((button) => {
      button.addEventListener("click", () => {
        handleViewRelatedBindings(viewModel.route, viewModel.selectedItem);
      });
    });

    panel.querySelectorAll('[data-action="clear-related-bindings-filter"]').forEach((button) => {
      button.addEventListener("click", () => {
        semanticCatalogUiState.relatedBindingsFilter = null;
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: "typed-bindings", objectId: "" }, "replace");
      });
    });

    panel.querySelectorAll('[data-action="clear-semantic-focus-ref"]').forEach((button) => {
      button.addEventListener("click", () => {
        semanticCatalogUiState.focusRefBySubtab[viewModel.route.subtab] = "";
        ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: viewModel.route.subtab, objectId: "" }, "replace");
      });
    });

    panel.querySelectorAll('[data-action="jump-semantic-ref"]').forEach((button) => {
      button.addEventListener("click", () => {
        handleJumpSemanticRef(button.dataset.semanticRef || "");
      });
    });

    panel.querySelectorAll('[data-action="semantic-planner-context"]').forEach((button) => {
      button.addEventListener("click", async () => {
        const input = panel.querySelector('[data-role="planner-context-session-id"]');
        await runSemanticPlannerContext(viewModel.route, input?.value?.trim() || "");
      });
    });

    panel.querySelectorAll('[data-role="semantic-form"]').forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await handleSemanticFormSubmit(viewModel, form);
      });
    });
  }

  async function hydrate(panel, route) {
    const renderVersion = ++semanticCatalogRenderVersion;
    const config = semanticConfigForSubtab(route.subtab || "entities");
    const statusFilter = semanticStatusFilterForSubtab(route.subtab);
    const endpoints = semanticEndpointForSubtab(route.subtab);
    const safeRender = (viewModel) => {
      if (renderVersion !== semanticCatalogRenderVersion) return;
      const target = panel.querySelector('[data-role="semantic-catalog-body"]');
      if (target) {
        target.innerHTML = renderBody(viewModel);
        bindEvents(panel, viewModel);
      }
    };

    safeRender({
      route,
      config,
      items: [],
      unfilteredItems: [],
      selectedObjectId: route.objectId || "",
      selectedItem: null,
      listError: null,
      publishError: null,
      helperState: semanticHelperState(route.subtab, route.objectId),
      relatedBindingsFilter: semanticCatalogUiState.relatedBindingsFilter,
      focusedRef: semanticFocusedRefForSubtab(route.subtab),
    });

    try {
      const payload = await endpoints.list(statusFilter === "all" ? null : statusFilter);
      const unfilteredItems = extractItems(payload);
      const focusedRef = semanticFocusedRefForSubtab(route.subtab);
      const focusedItems = focusedRef
        ? unfilteredItems.filter((item) => semanticListMatchesFocusedRef(config, item, focusedRef))
        : unfilteredItems;
      const filteredItems =
        route.subtab === "typed-bindings" && semanticCatalogUiState.relatedBindingsFilter?.ref
          ? focusedItems.filter((item) => semanticListMatchesRelatedBindingFilter(item, semanticCatalogUiState.relatedBindingsFilter))
          : focusedItems;
      let selectedObjectId = filteredItems.some((item) => semanticObjectId(config, item) === route.objectId) ? route.objectId : "";
      if (!selectedObjectId && filteredItems.length) {
        selectedObjectId = semanticObjectId(config, filteredItems[0]);
      }
      if (route.objectId !== selectedObjectId) {
        ctx.applyAdminRoute({ ...route, tab: "semantic-catalog", subtab: route.subtab, objectId: selectedObjectId, bindingId: "" }, "replace");
        return;
      }

      let selectedItem = filteredItems.find((item) => semanticObjectId(config, item) === selectedObjectId) || null;
      let detailError = null;
      if (selectedObjectId) {
        try {
          selectedItem = await endpoints.get(selectedObjectId);
        } catch (error) {
          detailError = normalizeApiError(error, `${config.label} detail unavailable.`);
        }
      }
      if (!semanticCatalogUiState.helperDataByKey[semanticHelperKey(route.subtab, selectedObjectId)]) {
        resetSemanticHelperState(route.subtab, selectedObjectId);
      }
      safeRender({
        route,
        config,
        items: filteredItems,
        unfilteredItems,
        selectedObjectId,
        selectedItem,
        listError: detailError,
        publishError: semanticPublishError(route.subtab, selectedObjectId),
        helperState: semanticHelperState(route.subtab, selectedObjectId),
        relatedBindingsFilter: semanticCatalogUiState.relatedBindingsFilter,
        focusedRef,
      });
    } catch (error) {
      safeRender({
        route,
        config,
        items: [],
        unfilteredItems: [],
        selectedObjectId: "",
        selectedItem: null,
        listError: normalizeApiError(error, `${config.label} unavailable.`),
        publishError: null,
        helperState: semanticHelperState(route.subtab, route.objectId),
        relatedBindingsFilter: semanticCatalogUiState.relatedBindingsFilter,
        focusedRef: semanticFocusedRefForSubtab(route.subtab),
      });
    }
  }

  async function refreshCurrentSemanticCatalog() {
    const panel = document.getElementById("panel-semantic-catalog");
    const route = ctx.getCurrentRoute();
    if (panel && route?.tab === "semantic-catalog") {
      await hydrate(panel, route);
    }
  }

  return { render, hydrate };
}
