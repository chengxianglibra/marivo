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
    filtersBySubtab: {},
    formModeBySubtab: {},
    relatedBindingsFilter: null,
    focusRefBySubtab: {},
    helperDataByKey: {},
    publishErrorsByKey: {},
    formErrorsByKey: {},
  };

  function countValues(items, key) {
    return (items || []).reduce((acc, item) => {
      const value = String(item?.[key] || "unknown").toLowerCase();
      acc[value] = (acc[value] || 0) + 1;
      return acc;
    }, {});
  }

  function countHasBlockers(items) {
    return (items || []).reduce(
      (acc, item) => {
        const key = Number(item?.blocker_count || 0) > 0 ? "with" : "without";
        acc[key] += 1;
        return acc;
      },
      { with: 0, without: 0 }
    );
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

  function semanticFiltersForSubtab(subtab) {
    return (
      semanticCatalogUiState.filtersBySubtab[subtab] || {
        lifecycle: "all",
        readiness: "all",
        blockers: "all",
      }
    );
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
        list: (status, options) => ctx.adminApi.listSemanticEntities(status, options),
        get: (objectId) => ctx.adminApi.getSemanticEntity(objectId),
        create: (payload) => ctx.adminApi.createSemanticEntity(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticEntity(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticEntity(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticEntity(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticEntity(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticEntity(objectId),
      },
      metrics: {
        list: (status, options) => ctx.adminApi.listSemanticMetrics(status, options),
        get: (objectId) => ctx.adminApi.getSemanticMetric(objectId),
        create: (payload) => ctx.adminApi.createSemanticMetric(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticMetric(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticMetric(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticMetric(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticMetric(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticMetric(objectId),
      },
      "process-objects": {
        list: (status, options) => ctx.adminApi.listSemanticProcessObjects(status, options),
        get: (objectId) => ctx.adminApi.getSemanticProcessObject(objectId),
        create: (payload) => ctx.adminApi.createSemanticProcessObject(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticProcessObject(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticProcessObject(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticProcessObject(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticProcessObject(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticProcessObject(objectId),
      },
      dimensions: {
        list: (status, options) => ctx.adminApi.listSemanticDimensions(status, options),
        get: (objectId) => ctx.adminApi.getSemanticDimension(objectId),
        create: (payload) => ctx.adminApi.createSemanticDimension(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticDimension(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticDimension(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticDimension(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticDimension(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticDimension(objectId),
      },
      time: {
        list: (status, options) => ctx.adminApi.listSemanticTime(status, options),
        get: (objectId) => ctx.adminApi.getSemanticTime(objectId),
        create: (payload) => ctx.adminApi.createSemanticTime(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticTime(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticTime(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticTime(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticTime(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticTime(objectId),
      },
      "enum-sets": {
        list: (status, options) => ctx.adminApi.listSemanticEnumSets(status, options),
        get: (objectId) => ctx.adminApi.getSemanticEnumSet(objectId),
        create: (payload) => ctx.adminApi.createSemanticEnumSet(payload),
        update: (objectId, payload) => ctx.adminApi.updateSemanticEnumSet(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateSemanticEnumSet(objectId),
        activate: (objectId) => ctx.adminApi.activateSemanticEnumSet(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateSemanticEnumSet(objectId),
        publish: (objectId) => ctx.adminApi.publishSemanticEnumSet(objectId),
      },
      "typed-bindings": {
        list: (status, options) => ctx.adminApi.listTypedSemanticBindings(status, options),
        get: (objectId) => ctx.adminApi.getTypedSemanticBinding(objectId),
        create: (payload) => ctx.adminApi.createTypedSemanticBinding(payload),
        update: (objectId, payload) => ctx.adminApi.updateTypedSemanticBinding(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateTypedSemanticBinding(objectId),
        activate: (objectId) => ctx.adminApi.activateTypedSemanticBinding(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateTypedSemanticBinding(objectId),
        publish: (objectId) => ctx.adminApi.publishTypedSemanticBinding(objectId),
      },
      "compatibility-profiles": {
        list: (status) => ctx.adminApi.listCompatibilityProfiles(status),
        get: (objectId) => ctx.adminApi.getCompatibilityProfile(objectId),
        create: (payload) => ctx.adminApi.createCompatibilityProfile(payload),
        update: (objectId, payload) => ctx.adminApi.updateCompatibilityProfile(objectId, payload),
        validate: (objectId) => ctx.adminApi.validateCompatibilityProfile(objectId),
        activate: (objectId) => ctx.adminApi.activateCompatibilityProfile(objectId),
        deprecate: (objectId) => ctx.adminApi.deprecateCompatibilityProfile(objectId),
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

  function renderSemanticFilterGroup(filterKey, options, current) {
    return `
      <div class="detail-actions semantic-filter-group">
        ${options
          .map(
            (option) => `
          <button
            type="button"
            class="btn btn-sm ${current === option.value ? "btn-primary" : ""}"
            data-action="set-semantic-filter"
            data-filter-key="${filterKey}"
            data-filter-value="${option.value}"
          >${esc(option.label)} (${esc(String(option.count))})</button>
        `
          )
          .join("")}
      </div>
    `;
  }

  function renderSemanticReadinessFilters(subtab, items) {
    const filters = semanticFiltersForSubtab(subtab);
    const lifecycleCounts = countValues(items, "lifecycle_status");
    const readinessCounts = countValues(items, "readiness_status");
    const blockerCounts = countHasBlockers(items);
    return `
      <div class="semantic-filter-stack">
        <div>
          <p class="panel-note">Lifecycle</p>
          ${renderSemanticFilterGroup(
            "lifecycle",
            [
              { value: "all", label: "All lifecycle", count: items.length },
              { value: "draft", label: "Draft", count: lifecycleCounts.draft || 0 },
              { value: "active", label: "Active", count: lifecycleCounts.active || 0 },
              { value: "deprecated", label: "Deprecated", count: lifecycleCounts.deprecated || 0 },
            ],
            filters.lifecycle
          )}
        </div>
        <div>
          <p class="panel-note">Readiness</p>
          ${renderSemanticFilterGroup(
            "readiness",
            [
              { value: "all", label: "All readiness", count: items.length },
              { value: "ready", label: "Ready", count: readinessCounts.ready || 0 },
              { value: "not_ready", label: "Not Ready", count: readinessCounts.not_ready || 0 },
              { value: "stale", label: "Stale", count: readinessCounts.stale || 0 },
            ],
            filters.readiness
          )}
        </div>
        <div>
          <p class="panel-note">Has Blockers</p>
          ${renderSemanticFilterGroup(
            "blockers",
            [
              { value: "all", label: "All blocker states", count: items.length },
              { value: "with", label: "With Blockers", count: blockerCounts.with },
              { value: "without", label: "Without Blockers", count: blockerCounts.without },
            ],
            filters.blockers
          )}
        </div>
      </div>
    `;
  }

  function semanticBlockerCount(item) {
    return Number(item?.blocker_count ?? item?.blocking_requirements?.length ?? 0);
  }

  function semanticReadinessBadge(readinessStatus) {
    const readiness = String(readinessStatus || "unknown").toLowerCase();
    return `${statusBadge(readiness)}${
      readiness === "stale"
        ? ' <span class="shell-chip semantic-readiness-chip">stale needs operator review</span>'
        : ""
    }`;
  }

  function renderSemanticCatalogRows(config, items, selectedObjectId) {
    if (!items.length) {
      return `
        <tr>
          <td colspan="8">${renderEmptyState(`No ${config.label.toLowerCase()} found for the current readiness filters.`)}</td>
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
            <td>${statusBadge(item.lifecycle_status || "unknown")}</td>
            <td>${semanticReadinessBadge(item.readiness_status || "unknown")}</td>
            <td>${esc(String(semanticBlockerCount(item)))}</td>
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
      columns: [
        "object_id",
        "stable_ref",
        "display_name",
        "lifecycle",
        "readiness",
        "blockers",
        "revision",
        "updated_at",
      ],
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
        renderSemanticReadinessFilters(route.subtab, viewModel.unfilteredItems),
      rowsHtml: renderSemanticCatalogRows(config, viewModel.items, viewModel.selectedObjectId),
      errorHtml: viewModel.listError ? renderStructuredError(viewModel.listError, `${config.label} unavailable.`) : "",
    });
  }

  function semanticReadinessSummary(selectedItem) {
    const readiness = String(selectedItem?.readiness_status || "").toLowerCase();
    if (readiness === "ready") {
      return "Ready objects are eligible for catalog, resolve, and runtime default consumption.";
    }
    if (readiness === "stale") {
      return "Stale means the object was previously usable but a dependency or pinned profile is no longer aligned.";
    }
    return "Not ready means this object is active or draft but blocked from safe consumption until the listed requirements are resolved.";
  }

  function renderSemanticRefPills(refs, emptyCopy, actionLabel) {
    if (!refs?.length) {
      return renderEmptyState(emptyCopy);
    }
    return `
      <div class="shell-chip-group">
        ${refs
          .map(
            (ref) => `
              <button
                type="button"
                class="btn btn-sm"
                data-action="${actionLabel}"
                data-semantic-ref="${esc(ref)}"
              >${esc(ref)}</button>
            `
          )
          .join("")}
      </div>
    `;
  }

  function renderBlockingRequirementsCard(selectedItem) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Blocking Requirements",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Why-not-ready is front-loaded here so operators do not need to trigger a runtime failure first.",
        bodyHtml: renderEmptyState("Select an object to inspect blocker codes and dependency refs."),
      });
    }
    const blockers = selectedItem.blocking_requirements || [];
    return renderAdminDetailCard({
      title: "Blocking Requirements",
      statusHtml: `<span class="shell-chip">${esc(`${blockers.length} blocker(s)`)}</span>`,
      note: "Each blocker exposes code, message, and optional subject/dependency refs for debugging.",
      bodyHtml: blockers.length
        ? `
            <div class="semantic-blocker-list">
              ${blockers
                .map(
                  (blocker) => `
                    <div class="semantic-blocker-item">
                      <div class="semantic-blocker-header">
                        ${statusBadge("not_ready")}
                        <code>${esc(blocker.code || "UNKNOWN_BLOCKER")}</code>
                      </div>
                      <p>${esc(blocker.message || "No blocker message provided.")}</p>
                      <div class="shell-chip-group">
                        ${
                          blocker.subject_ref
                            ? `<span class="shell-chip"><strong>subject</strong> ${esc(blocker.subject_ref)}</span>`
                            : ""
                        }
                        ${
                          blocker.dependency_ref
                            ? `<span class="shell-chip"><strong>dependency</strong> ${esc(blocker.dependency_ref)}</span>`
                            : ""
                        }
                      </div>
                    </div>
                  `
                )
                .join("")}
            </div>
          `
        : renderEmptyState("No blocking requirements. This object is currently not blocked by readiness guardrails."),
    });
  }

  function renderDependenciesCard(selectedItem) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Dependencies",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Dependency refs come from the semantic detail payload.",
        bodyHtml: renderEmptyState("Select an object to inspect dependency refs."),
      });
    }
    return renderAdminDetailCard({
      title: "Dependencies",
      statusHtml: `<span class="shell-chip">${esc(String((selectedItem.dependency_refs || []).length))} dependency ref(s)</span>`,
      note: "Dependency jumps help move directly to upstream semantic objects or bindings.",
      bodyHtml: renderSemanticRefPills(
        selectedItem.dependency_refs || [],
        "No dependency refs on this object.",
        "jump-semantic-ref"
      ),
    });
  }

  function renderDependentsCard(selectedItem) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Dependents",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Dependent refs are still a stubbed detail field in this phase.",
        bodyHtml: renderEmptyState("Select an object to inspect dependent refs."),
      });
    }
    const dependents = selectedItem.dependent_refs || [];
    return renderAdminDetailCard({
      title: "Dependents",
      statusHtml: `<span class="shell-chip">${esc(String(dependents.length))} dependent ref(s)</span>`,
      note: dependents.length
        ? "Dependent refs are available for jump navigation."
        : "Dependent refs are currently stubbed in the semantic detail response when reverse expansion is not implemented yet.",
      bodyHtml: renderSemanticRefPills(
        dependents,
        "No dependent refs are available yet.",
        "jump-semantic-ref"
      ),
    });
  }

  function renderCapabilitiesCard(selectedItem) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Capabilities",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Capability flags summarize what the object can support once selected.",
        bodyHtml: renderEmptyState("Select an object to inspect capability flags."),
      });
    }
    const capabilities = selectedItem.capabilities || {};
    const capabilityEntries = Object.entries(capabilities);
    return renderAdminDetailCard({
      title: "Capabilities",
      statusHtml: `<span class="shell-chip">${esc(String(capabilityEntries.length))} capability field(s)</span>`,
      note: "Capabilities are shown directly so operators can distinguish usable objects from merely published ones.",
      bodyHtml: capabilityEntries.length
        ? `
            ${renderDetailList(
              capabilityEntries.map(([key, value]) => ({
                label: key,
                value:
                  typeof value === "object" && value !== null
                    ? JSON.stringify(value)
                    : String(value),
              }))
            )}
            ${renderJsonPanel("Capabilities JSON", capabilities, "No capabilities payload.")}
          `
        : renderEmptyState("No capability flags are exposed for this object."),
    });
  }

  function renderSemanticLifecycleCard(selectedItem, config, publishError) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Lifecycle",
        statusHtml: '<span class="shell-chip">no object selected</span>',
        note: "Select an object to inspect storage status, derived lifecycle, and lifecycle actions.",
        bodyHtml: renderEmptyState("Select a semantic object to inspect lifecycle and action controls."),
      });
    }
    const isPublished = String(selectedItem.status || "").toLowerCase() === "published";
    const isDeprecated = String(selectedItem.status || "").toLowerCase() === "deprecated";
    return renderAdminDetailCard({
      title: "Lifecycle",
      statusHtml: statusBadge(selectedItem.lifecycle_status || selectedItem.status),
      note: isPublished
        ? "Published storage state maps to lifecycle active, but activation still does not guarantee readiness."
        : "Validate runs check-only guardrails. Activate adds the object to the formal catalog without implying ready.",
      bodyHtml: `
        ${renderDetailList([
          { label: "status", valueHtml: statusBadge(selectedItem.status) },
          {
            label: "lifecycle_status",
            valueHtml: statusBadge(selectedItem.lifecycle_status || "unknown"),
          },
          { label: "revision", value: String(selectedItem.revision ?? "-") },
          { label: "updated_at", value: formatMaybeDate(selectedItem.updated_at) },
          {
            label: "freeze_rule",
            value: isPublished || isDeprecated
              ? "active and deprecated revisions are read-only"
              : "draft objects remain editable in the shared form shell",
          },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-sm" data-action="validate-semantic-object" ${isDeprecated ? "disabled" : ""}>Validate</button>
          <button type="button" class="btn btn-danger" data-action="activate-semantic-object" ${isPublished || isDeprecated ? "disabled" : ""}>Activate</button>
          <button type="button" class="btn btn-sm" data-action="deprecate-semantic-object" ${!isPublished ? "disabled" : ""}>Deprecate</button>
        </div>
        ${publishError ? renderStructuredError(publishError, "Lifecycle action failed.") : ""}
        <p class="panel-note">${esc(`Activate ${config.singularLabel} enters the formal catalog, but ready still depends on bindings, dependencies, and profiles.`)}</p>
      `,
    });
  }

  function renderSemanticReadinessCard(selectedItem) {
    if (!selectedItem) {
      return renderAdminDetailCard({
        title: "Readiness",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Readiness explains whether the object is actually usable, separate from storage status.",
        bodyHtml: renderEmptyState("Select an object to inspect readiness status and why-not-ready detail."),
      });
    }
    const readiness = String(selectedItem.readiness_status || "unknown").toLowerCase();
    const blockers = semanticBlockerCount(selectedItem);
    return renderAdminDetailCard({
      title: "Readiness",
      statusHtml: semanticReadinessBadge(readiness),
      note: "Why-not-ready appears here before helper actions so blocker triage starts in the detail pane.",
      bodyHtml: `
        ${renderDetailList([
          {
            label: "readiness_status",
            valueHtml: semanticReadinessBadge(readiness),
          },
          {
            label: "blocker_count",
            value: String(blockers),
          },
          {
            label: "operator_summary",
            value: semanticReadinessSummary(selectedItem),
          },
          {
            label: "stale_hint",
            value:
              readiness === "stale"
                ? "Stale objects need dependency or pinned revision review before they should be trusted."
                : "-",
          },
        ])}
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
        title: "Summary",
        statusHtml: "<span class=\"shell-chip\">waiting for selection</span>",
        note: "Each Semantic Catalog subtab reuses the same summary shape and adds derived lifecycle/readiness fields.",
        bodyHtml: renderEmptyState("Select an object from the list to inspect summary fields and raw JSON."),
      });
    }
    const summaryFields = [
      ...viewModel.config.detailFields(selectedItem),
      {
        label: "lifecycle_status",
        valueHtml: statusBadge(selectedItem.lifecycle_status || "unknown"),
      },
      {
        label: "readiness_status",
        valueHtml: semanticReadinessBadge(selectedItem.readiness_status || "unknown"),
      },
      {
        label: "blocker_count",
        value: String(semanticBlockerCount(selectedItem)),
      },
    ];
    return renderAdminDetailCard({
      title: "Summary",
      statusHtml: semanticReadinessBadge(selectedItem.readiness_status || selectedItem.status),
      note: `${viewModel.config.singularLabel} summary now includes object-specific contract fields instead of only the shared list columns.`,
      bodyHtml: `
        ${renderDetailList(summaryFields)}
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
    const objectStatus = String(selectedItem?.status || "").toLowerCase();
    const isPublished = objectStatus === "published";
    const isDeprecated = objectStatus === "deprecated";
    const editDisabled = !selectedItem || isPublished || isDeprecated;
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
          {
            label: "freeze_rule",
            value: isPublished || isDeprecated
              ? "active and deprecated revision freeze"
              : "draft objects can create, save, validate, and activate",
          },
        ])}
        <div class="detail-actions">
          <button type="button" class="btn btn-sm btn-primary" data-action="open-semantic-form" data-form-mode="create">Create</button>
          <button type="button" class="btn btn-sm" data-action="open-semantic-form" data-form-mode="edit" ${editDisabled ? "disabled" : ""}>Edit</button>
        </div>
        ${mode === "edit" && !selectedItem ? renderEmptyState(`Select a ${viewModel.config.singularLabel.toLowerCase()} before editing.`) : `
          <form class="source-form-grid" data-role="semantic-form">
            ${formFields.map((field) => renderSemanticFieldInput(field, readOnly)).join("")}
            ${jsonFields.map((field) => renderSemanticJsonInput(field, readOnly)).join("")}
            ${readOnly ? '<p class="panel-note">Active and deprecated revision freeze keeps edit fields read-only until a new draft is created server-side.</p>' : ""}
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
        ${viewModel.detailError ? renderStructuredError(viewModel.detailError, `${viewModel.config.singularLabel} detail unavailable.`) : ""}
        ${renderSemanticObjectSummaryCard(viewModel)}
        ${renderSemanticReadinessCard(viewModel.selectedItem)}
        ${renderBlockingRequirementsCard(viewModel.selectedItem)}
        ${renderDependenciesCard(viewModel.selectedItem)}
        ${renderDependentsCard(viewModel.selectedItem)}
        ${renderCapabilitiesCard(viewModel.selectedItem)}
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
      detailError: null,
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
      { ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: "typed-bindings", objectId: "" },
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
    ctx.applyAdminRoute({ ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: targetSubtab, objectId: "" }, "push");
  }

  async function handleValidateSemanticObject(route, viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      toast("Select an object before validating.", "error");
      return;
    }
    const objectId = semanticObjectId(viewModel.config, selectedItem);
    try {
      setSemanticPublishError(route.subtab, objectId, null);
      const result = await semanticEndpointForSubtab(route.subtab).validate(objectId);
      if (result?.validation?.blocking_requirements?.length) {
        toast("Validation completed with blockers.", "error");
      } else {
        toast("Validation passed.", "success");
      }
      await refreshCurrentSemanticCatalog();
    } catch (error) {
      const normalized = normalizeApiError(error, "Validate failed.");
      setSemanticPublishError(route.subtab, objectId, normalized);
      toast(normalized.message, "error");
      ctx.renderCurrentRoute();
    }
  }

  function handleActivateSemanticObject(route, viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      toast("Select an object before activating.", "error");
      return;
    }
    const objectId = semanticObjectId(viewModel.config, selectedItem);
    const stableRef = semanticStableRef(viewModel.config, selectedItem);
    openDangerConfirm({
      title: `Activate ${viewModel.config.singularLabel}`,
      objectLabel: objectId,
      impactScope: "Promotes the current draft into the formal semantic catalog without implying readiness.",
      reversible: "No",
      confirmLabel: "Activate",
      detailsHtml: renderDetailList([
        { label: "object_id", value: objectId },
        { label: "stable_ref", value: stableRef || "-" },
        { label: "status", value: selectedItem.status || "-" },
        { label: "warning", value: "Activation failures surface structured validation or compatibility errors." },
      ]),
      onConfirm: async () => {
        try {
          setSemanticPublishError(route.subtab, objectId, null);
          await semanticEndpointForSubtab(route.subtab).activate(objectId);
          toast("Semantic object activated.", "success");
          await refreshCurrentSemanticCatalog();
        } catch (error) {
          const normalized = normalizeApiError(error, "Activate failed.");
          setSemanticPublishError(route.subtab, objectId, normalized);
          toast(normalized.message, "error");
          ctx.renderCurrentRoute();
        }
      },
    });
  }

  function handleDeprecateSemanticObject(route, viewModel) {
    const selectedItem = viewModel.selectedItem;
    if (!selectedItem) {
      toast("Select an object before deprecating.", "error");
      return;
    }
    const objectId = semanticObjectId(viewModel.config, selectedItem);
    openDangerConfirm({
      title: `Deprecate ${viewModel.config.singularLabel}`,
      objectLabel: objectId,
      impactScope: "Moves the active catalog object into deprecated storage status and removes it from the active catalog.",
      reversible: "No",
      confirmLabel: "Deprecate",
      detailsHtml: renderDetailList([
        { label: "object_id", value: objectId },
        { label: "status", value: selectedItem.status || "-" },
        { label: "warning", value: "Deprecation changes lifecycle only; it does not repair readiness blockers." },
      ]),
      onConfirm: async () => {
        try {
          setSemanticPublishError(route.subtab, objectId, null);
          await semanticEndpointForSubtab(route.subtab).deprecate(objectId);
          toast("Semantic object deprecated.", "success");
          await refreshCurrentSemanticCatalog();
        } catch (error) {
          const normalized = normalizeApiError(error, "Deprecate failed.");
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
        { ...ctx.getCurrentRoute(), tab: "semantic-catalog", subtab: viewModel.route.subtab, objectId: nextObjectId },
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
    panel.querySelectorAll('[data-action="set-semantic-filter"]').forEach((button) => {
      button.addEventListener("click", () => {
        const current = semanticFiltersForSubtab(viewModel.route.subtab);
        semanticCatalogUiState.filtersBySubtab[viewModel.route.subtab] = {
          ...current,
          [button.dataset.filterKey || "lifecycle"]: button.dataset.filterValue || "all",
        };
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

    panel.querySelectorAll('[data-action="validate-semantic-object"]').forEach((button) => {
      button.addEventListener("click", async () => {
        await handleValidateSemanticObject(viewModel.route, viewModel);
      });
    });

    panel.querySelectorAll('[data-action="activate-semantic-object"]').forEach((button) => {
      button.addEventListener("click", () => {
        handleActivateSemanticObject(viewModel.route, viewModel);
      });
    });

    panel.querySelectorAll('[data-action="deprecate-semantic-object"]').forEach((button) => {
      button.addEventListener("click", () => {
        handleDeprecateSemanticObject(viewModel.route, viewModel);
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
    const semanticFilters = semanticFiltersForSubtab(route.subtab);
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
      detailError: null,
      publishError: null,
      helperState: semanticHelperState(route.subtab, route.objectId),
      relatedBindingsFilter: semanticCatalogUiState.relatedBindingsFilter,
      focusedRef: semanticFocusedRefForSubtab(route.subtab),
    });

    try {
      const payload = await endpoints.list(null, { detail: false });
      const unfilteredItems = extractItems(payload);
      const focusedRef = semanticFocusedRefForSubtab(route.subtab);
      const focusedItems = focusedRef
        ? unfilteredItems.filter((item) => semanticListMatchesFocusedRef(config, item, focusedRef))
        : unfilteredItems;
      const bindingFilteredItems =
        route.subtab === "typed-bindings" && semanticCatalogUiState.relatedBindingsFilter?.ref
          ? focusedItems.filter((item) => semanticListMatchesRelatedBindingFilter(item, semanticCatalogUiState.relatedBindingsFilter))
          : focusedItems;
      const filteredItems = bindingFilteredItems.filter((item) => {
        if (
          semanticFilters.lifecycle !== "all" &&
          String(item?.lifecycle_status || "").toLowerCase() !== semanticFilters.lifecycle
        ) {
          return false;
        }
        if (
          semanticFilters.readiness !== "all" &&
          String(item?.readiness_status || "").toLowerCase() !== semanticFilters.readiness
        ) {
          return false;
        }
        if (semanticFilters.blockers === "with" && semanticBlockerCount(item) === 0) {
          return false;
        }
        if (semanticFilters.blockers === "without" && semanticBlockerCount(item) > 0) {
          return false;
        }
        return true;
      });
      let selectedObjectId = filteredItems.some((item) => semanticObjectId(config, item) === route.objectId) ? route.objectId : "";
      if (!selectedObjectId && filteredItems.length) {
        selectedObjectId = semanticObjectId(config, filteredItems[0]);
      }
      if (route.objectId !== selectedObjectId) {
        ctx.applyAdminRoute({ ...route, tab: "semantic-catalog", subtab: route.subtab, objectId: selectedObjectId }, "replace");
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
        listError: null,
        detailError,
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
        detailError: null,
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
