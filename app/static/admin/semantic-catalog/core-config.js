function semanticOptionalText(value) {
  const trimmed = String(value || "").trim();
  return trimmed ? trimmed : null;
}

function semanticListText(values) {
  return Array.isArray(values) ? values.join("\n") : "";
}

function semanticSplitRefs(rawValue) {
  return String(rawValue || "")
    .split(/[\n,]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function semanticJsonText(value, fallbackValue) {
  return JSON.stringify(value ?? fallbackValue, null, 2);
}

function semanticParseJson(rawValue, label, fallbackValue) {
  const text = String(rawValue || "").trim();
  if (!text) return fallbackValue;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${label} is invalid.`);
  }
}

function typedBindingRefs(item) {
  const refs = [
    item?.header?.binding_ref,
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
  ];
  return refs.filter(Boolean);
}

function countTimeSurfaces(item) {
  return (item?.interface_contract?.carrier_bindings || []).reduce(
    (total, entry) => total + (entry?.time_surfaces?.length || 0),
    0
  );
}

function compatibilityPayloadKind(item) {
  if (item?.profile_kind === "requirement") return "requirement";
  if (item?.profile_kind === "capability") return "capability";
  return "-";
}

export function createCoreSemanticCatalogConfig(statusBadge) {
  return {
    entities: {
      label: "Entities",
      singularLabel: "Entity",
      listTitle: "Entity Catalog",
      idField: "entity_contract_id",
      stableRef: (item) => item?.header?.entity_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.entity_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.entity_contract_id },
        { label: "stable_ref", value: item.header?.entity_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        {
          label: "updated_at",
          value: item.updated_at || "-",
        },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        {
          label: "identity_keys",
          value: (item.interface_contract?.identity?.key_refs || []).join(", ") || "-",
        },
        {
          label: "uniqueness_scope",
          value: item.interface_contract?.identity?.uniqueness_scope || "-",
        },
        {
          label: "id_stability",
          value: item.interface_contract?.identity?.id_stability || "-",
        },
        {
          label: "primary_time_ref",
          value: item.interface_contract?.primary_time_ref || "-",
        },
        {
          label: "stable_descriptors",
          value: String(item.interface_contract?.stable_descriptors?.length ?? 0),
        },
      ],
      listEndpointLabel: "GET /semantic/entities",
      createEndpointLabel: "POST /semantic/entities",
      updateEndpointLabel: "PUT /semantic/entities/{entity_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "User",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Registered platform user",
                multiline: true,
              },
              {
                name: "identity_key_refs",
                label: "Identity Key Refs",
                value: semanticListText(item?.interface_contract?.identity?.key_refs),
                placeholder: "key.user_id",
                multiline: true,
              },
              {
                name: "uniqueness_scope",
                label: "Uniqueness Scope",
                value: item?.interface_contract?.identity?.uniqueness_scope || "global",
                placeholder: "global",
              },
              {
                name: "id_stability",
                label: "ID Stability",
                value: item?.interface_contract?.identity?.id_stability || "stable",
                placeholder: "stable",
              },
              {
                name: "nullable_key_policy",
                label: "Nullable Key Policy",
                value: item?.interface_contract?.identity?.nullable_key_policy || "",
                placeholder: "reject",
              },
              {
                name: "primary_time_ref",
                label: "Primary Time Ref",
                value: item?.interface_contract?.primary_time_ref || "",
                placeholder: "time.user_created_at",
              },
            ]
          : [
              { name: "entity_ref", label: "Entity Ref", value: "", placeholder: "entity.user" },
              { name: "display_name", label: "Display Name", value: "", placeholder: "User" },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Registered platform user",
                multiline: true,
              },
              {
                name: "entity_contract_version",
                label: "Contract Version",
                value: "entity.v1",
                placeholder: "entity.v1",
              },
              {
                name: "identity_key_refs",
                label: "Identity Key Refs",
                value: "key.user_id",
                placeholder: "key.user_id",
                multiline: true,
              },
              {
                name: "uniqueness_scope",
                label: "Uniqueness Scope",
                value: "global",
                placeholder: "global",
              },
              {
                name: "id_stability",
                label: "ID Stability",
                value: "stable",
                placeholder: "stable",
              },
              {
                name: "nullable_key_policy",
                label: "Nullable Key Policy",
                value: "reject",
                placeholder: "reject",
              },
              {
                name: "primary_time_ref",
                label: "Primary Time Ref",
                value: "",
                placeholder: "time.user_created_at",
              },
            ],
      jsonFields: (item) => [
        {
          name: "hierarchy_json",
          label: "Hierarchy JSON",
          value: semanticJsonText(item?.interface_contract?.hierarchy, null),
          placeholder:
            '{\n  "parent_entity_ref": "entity.account",\n  "cardinality_to_parent": "many_to_one",\n  "ownership_semantics": "belongs_to"\n}',
        },
        {
          name: "stable_descriptors_json",
          label: "Stable Descriptors JSON",
          value: semanticJsonText(item?.interface_contract?.stable_descriptors, []),
          placeholder:
            '[\n  {\n    "dimension_ref": "dimension.signup_channel",\n    "cardinality": "one"\n  }\n]',
        },
      ],
      buildCreatePayload: (values) => ({
        header: {
          entity_ref: values.entity_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          entity_contract_version: values.entity_contract_version,
        },
        interface_contract: {
          identity: {
            key_refs: semanticSplitRefs(values.identity_key_refs),
            uniqueness_scope: values.uniqueness_scope || "global",
            id_stability: values.id_stability || "stable",
            nullable_key_policy: semanticOptionalText(values.nullable_key_policy),
          },
          hierarchy: semanticParseJson(values.hierarchy_json, "Hierarchy JSON", null),
          primary_time_ref: semanticOptionalText(values.primary_time_ref),
          stable_descriptors: semanticParseJson(values.stable_descriptors_json, "Stable Descriptors JSON", []),
        },
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        interface_contract: {
          identity: {
            key_refs: semanticSplitRefs(values.identity_key_refs),
            uniqueness_scope: values.uniqueness_scope || "global",
            id_stability: values.id_stability || "stable",
            nullable_key_policy: semanticOptionalText(values.nullable_key_policy),
          },
          hierarchy: semanticParseJson(values.hierarchy_json, "Hierarchy JSON", null),
          primary_time_ref: semanticOptionalText(values.primary_time_ref),
          stable_descriptors: semanticParseJson(values.stable_descriptors_json, "Stable Descriptors JSON", []),
        },
      }),
      relationshipFields: (item) => [
        { label: "parent_entity_ref", value: item?.interface_contract?.hierarchy?.parent_entity_ref || "-" },
        {
          label: "stable_descriptor_refs",
          value:
            (item?.interface_contract?.stable_descriptors || [])
              .map((entry) => entry.dimension_ref)
              .join(", ") || "-",
        },
      ],
      relatedRefs: (item) =>
        [
          item?.interface_contract?.hierarchy?.parent_entity_ref
            ? { label: "Parent Entity", ref: item.interface_contract.hierarchy.parent_entity_ref }
            : null,
          item?.interface_contract?.primary_time_ref
            ? { label: "Primary Time", ref: item.interface_contract.primary_time_ref }
            : null,
          ...(item?.interface_contract?.stable_descriptors || []).map((entry) => ({
            label: `Descriptor ${entry.dimension_ref}`,
            ref: entry.dimension_ref,
          })),
        ].filter(Boolean),
    },
    metrics: {
      label: "Metrics",
      singularLabel: "Metric",
      listTitle: "Metric Catalog",
      idField: "metric_contract_id",
      stableRef: (item) => item?.header?.metric_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.metric_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.metric_contract_id },
        { label: "stable_ref", value: item.header?.metric_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "metric_family", value: item.header?.metric_family || "-" },
        { label: "observed_entity_ref", value: item.header?.observed_entity_ref || "-" },
        { label: "observation_grain_ref", value: item.header?.observation_grain_ref || "-" },
        { label: "primary_time_ref", value: item.header?.primary_time_ref || "-" },
        { label: "payload_family", value: item.payload?.metric_family || "-" },
      ],
      listEndpointLabel: "GET /semantic/metrics",
      createEndpointLabel: "POST /semantic/metrics",
      updateEndpointLabel: "PUT /semantic/metrics/{metric_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "DAU",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Daily active users",
                multiline: true,
              },
            ]
          : [
              { name: "metric_ref", label: "Metric Ref", value: "", placeholder: "metric.dau" },
              { name: "display_name", label: "Display Name", value: "", placeholder: "DAU" },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Daily active users",
                multiline: true,
              },
              {
                name: "metric_family",
                label: "Metric Family",
                value: "count_metric",
                placeholder: "count_metric",
              },
              {
                name: "observed_entity_ref",
                label: "Observed Entity Ref",
                value: "",
                placeholder: "entity.user",
              },
              {
                name: "observation_grain_ref",
                label: "Observation Grain Ref",
                value: "",
                placeholder: "grain.user",
              },
              { name: "sample_kind", label: "Sample Kind", value: "numeric", placeholder: "numeric" },
              {
                name: "value_semantics",
                label: "Value Semantics",
                value: "count",
                placeholder: "count",
              },
              {
                name: "aggregation_scope",
                label: "Aggregation Scope",
                value: "window",
                placeholder: "window",
              },
              {
                name: "primary_time_ref",
                label: "Primary Time Ref",
                value: "",
                placeholder: "time.activity_date",
              },
              { name: "additivity", label: "Additivity", value: "additive", placeholder: "additive" },
              {
                name: "population_subject_ref",
                label: "Population Subject Ref",
                value: "",
                placeholder: "subject.user",
              },
              {
                name: "metric_contract_version",
                label: "Contract Version",
                value: "metric.v1",
                placeholder: "metric.v1",
              },
            ],
      jsonFields: (item) => [
        {
          name: "payload_json",
          label: "Metric Payload JSON",
          value: semanticJsonText(item?.payload, {
            metric_family: item?.header?.metric_family || "count_metric",
            count_target: {
              name: "active_users",
              semantics: "distinct active users",
              aggregation: "count_distinct",
            },
          }),
          placeholder:
            '{\n  "metric_family": "count_metric",\n  "count_target": {\n    "name": "active_users",\n    "semantics": "distinct active users",\n    "aggregation": "count_distinct"\n  }\n}',
        },
      ],
      buildCreatePayload: (values) => ({
        header: {
          metric_ref: values.metric_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          metric_family: values.metric_family,
          observed_entity_ref: values.observed_entity_ref,
          observation_grain_ref: values.observation_grain_ref,
          sample_kind: values.sample_kind,
          value_semantics: values.value_semantics,
          aggregation_scope: semanticOptionalText(values.aggregation_scope),
          primary_time_ref: semanticOptionalText(values.primary_time_ref),
          additivity: values.additivity,
          population_subject_ref: semanticOptionalText(values.population_subject_ref),
          metric_contract_version: values.metric_contract_version,
        },
        payload: semanticParseJson(values.payload_json, "Metric Payload JSON", {}),
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        payload: semanticParseJson(values.payload_json, "Metric Payload JSON", {}),
      }),
      relationshipFields: (item) => [
        { label: "population_subject_ref", value: item?.header?.population_subject_ref || "-" },
        { label: "related_typed_bindings", value: "Use View Related Bindings to inspect grounding." },
      ],
      relatedRefs: (item) =>
        [
          item?.header?.observed_entity_ref
            ? { label: "Observed Entity", ref: item.header.observed_entity_ref }
            : null,
          item?.header?.primary_time_ref
            ? { label: "Primary Time", ref: item.header.primary_time_ref }
            : null,
        ].filter(Boolean),
    },
    "typed-bindings": {
      label: "Typed Bindings",
      singularLabel: "Typed Binding",
      listTitle: "Typed Binding Catalog",
      idField: "binding_id",
      stableRef: (item) => item?.header?.binding_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.binding_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.binding_id },
        { label: "stable_ref", value: item.header?.binding_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Execution Binding Contract",
      interfaceSummaryFields: (item) => [
        { label: "binding_scope", value: item.header?.binding_scope || "-" },
        { label: "bound_object_ref", value: item.header?.bound_object_ref || "-" },
        {
          label: "carrier_bindings",
          value: String(item.interface_contract?.carrier_bindings?.length ?? 0),
        },
        {
          label: "field_bindings",
          value: String(item.interface_contract?.field_bindings?.length ?? 0),
        },
        {
          label: "imports",
          value: String(item.interface_contract?.imports?.length ?? 0),
        },
        {
          label: "time_surfaces",
          value: String(countTimeSurfaces(item)),
        },
      ],
      listEndpointLabel: "GET /semantic/bindings",
      createEndpointLabel: "POST /semantic/bindings",
      updateEndpointLabel: "PUT /semantic/bindings/{binding_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "Account Binding",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Primary warehouse grounding for account identity",
                multiline: true,
              },
            ]
          : [
              {
                name: "binding_ref",
                label: "Binding Ref",
                value: "",
                placeholder: "binding.account_primary",
              },
              {
                name: "display_name",
                label: "Display Name",
                value: "",
                placeholder: "Account Binding",
              },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Primary warehouse grounding for account identity",
                multiline: true,
              },
              { name: "binding_scope", label: "Binding Scope", value: "entity", placeholder: "entity" },
              {
                name: "bound_object_ref",
                label: "Bound Object Ref",
                value: "",
                placeholder: "entity.account",
              },
              {
                name: "binding_contract_version",
                label: "Contract Version",
                value: "binding.v1",
                placeholder: "binding.v1",
              },
            ],
      jsonFields: (item) => [
        {
          name: "interface_contract_json",
          label: "Interface Contract JSON",
          value: semanticJsonText(item?.interface_contract, {
            imports: [],
            carrier_bindings: [
              {
                binding_key: "primary",
                carrier_kind: "table",
                carrier_locator: "warehouse.accounts",
                binding_role: "primary",
                field_surfaces: [{ surface_ref: "field.account_id", physical_name: "account_id" }],
              },
            ],
            field_bindings: [
              {
                carrier_binding_key: "primary",
                target: { target_kind: "identity_key", target_key: "key.account_id" },
                semantic_ref: "key.account_id",
                surface_ref: "field.account_id",
              },
            ],
            join_relations: [],
            consumption_policies: [],
          }),
          placeholder:
            '{\n  "imports": [],\n  "carrier_bindings": [\n    {\n      "binding_key": "primary",\n      "carrier_kind": "table",\n      "carrier_locator": "warehouse.accounts",\n      "binding_role": "primary",\n      "field_surfaces": [\n        {\n          "surface_ref": "field.account_id",\n          "physical_name": "account_id"\n        }\n      ]\n    }\n  ],\n  "field_bindings": [\n    {\n      "carrier_binding_key": "primary",\n      "target": {\n        "target_kind": "identity_key",\n        "target_key": "key.account_id"\n      },\n      "semantic_ref": "key.account_id",\n      "surface_ref": "field.account_id"\n    }\n  ],\n  "join_relations": [],\n  "consumption_policies": []\n}',
        },
      ],
      buildCreatePayload: (values) => ({
        header: {
          binding_ref: values.binding_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          binding_scope: values.binding_scope,
          bound_object_ref: values.bound_object_ref,
          binding_contract_version: values.binding_contract_version,
        },
        interface_contract: semanticParseJson(values.interface_contract_json, "Interface Contract JSON", {}),
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        interface_contract: semanticParseJson(values.interface_contract_json, "Interface Contract JSON", {}),
      }),
      relationshipFields: (item) => [
        {
          label: "bound_object_ref",
          value: item?.header?.bound_object_ref || "-",
        },
        {
          label: "carrier_locators",
          value:
            (item?.interface_contract?.carrier_bindings || [])
              .map((entry) => entry.carrier_locator)
              .join(", ") || "-",
        },
        {
          label: "source_object_refs",
          value:
            (item?.interface_contract?.carrier_bindings || [])
              .map((entry) => entry.source_object_ref)
              .filter(Boolean)
              .join(", ") || "-",
        },
        {
          label: "imported_binding_refs",
          value:
            (item?.interface_contract?.imports || [])
              .map((entry) => entry.binding_ref)
              .join(", ") || "-",
        },
      ],
      relatedRefs: (item) =>
        [
          item?.header?.bound_object_ref
            ? { label: "Bound Semantic Object", ref: item.header.bound_object_ref }
            : null,
          ...(item?.interface_contract?.imports || []).map((entry) => ({
            label: `Imported ${entry.import_key}`,
            ref: entry.binding_ref,
          })),
          ...(item?.interface_contract?.carrier_bindings || []).map((entry) =>
            entry.primary_entity_ref
              ? { label: `Primary Entity ${entry.binding_key}`, ref: entry.primary_entity_ref }
              : null
          ),
        ].filter(Boolean),
      matchesFocusRef: (item, ref) => typedBindingRefs(item).includes(ref),
      operatorGuidanceTitle: "Binding Grounding Guidance",
      operatorGuidanceNote:
        "Typed bindings are the grounding bridge between typed semantic contracts and physical carriers. This page keeps carrier, field, and import coverage explicit before publish.",
      operatorGuidanceFields: (item) => [
        {
          label: "carrier_bindings",
          value: String(item?.interface_contract?.carrier_bindings?.length ?? 0),
        },
        {
          label: "field_bindings",
          value: String(item?.interface_contract?.field_bindings?.length ?? 0),
        },
        {
          label: "join_relations",
          value: String(item?.interface_contract?.join_relations?.length ?? 0),
        },
        {
          label: "consumption_policies",
          value: String(item?.interface_contract?.consumption_policies?.length ?? 0),
        },
      ],
    },
    "compatibility-profiles": {
      label: "Compatibility Profiles",
      singularLabel: "Compatibility Profile",
      listTitle: "Compatibility Profile Catalog",
      idField: "profile_id",
      stableRef: (item) => item?.profile_ref || "",
      displayName: (item) => item?.profile_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.profile_id },
        { label: "stable_ref", value: item.profile_ref || "-" },
        { label: "display_name", value: item.profile_ref || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "profile_kind", value: item.profile_kind || "-" },
        { label: "subject_kind", value: item.subject_kind || "-" },
        { label: "subject_ref", value: item.subject_ref || "-" },
        { label: "subject_revision", value: String(item.subject_revision ?? "-") },
        { label: "payload_kind", value: compatibilityPayloadKind(item) },
        {
          label: "required_entities",
          value: (item.requirement?.entity_refs || []).join(", ") || "-",
        },
      ],
      listEndpointLabel: "GET /compiler/compatibility-profiles",
      createEndpointLabel: "POST /compiler/compatibility-profiles",
      updateEndpointLabel: "PUT /compiler/compatibility-profiles/{profile_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? []
          : [
              {
                name: "profile_ref",
                label: "Profile Ref",
                value: "",
                placeholder: "compiler_profile.account_count_requirement",
              },
              {
                name: "profile_kind",
                label: "Profile Kind",
                value: "requirement",
                placeholder: "requirement",
              },
              { name: "schema_version", label: "Schema Version", value: "v1", placeholder: "v1" },
              { name: "subject_kind", label: "Subject Kind", value: "metric", placeholder: "metric" },
              {
                name: "subject_ref",
                label: "Subject Ref",
                value: "",
                placeholder: "metric.account_count",
              },
            ],
      jsonFields: (item) => [
        {
          name: "requirement_json",
          label: "Requirement JSON",
          value: semanticJsonText(item?.requirement, { entity_refs: ["entity.account"] }),
          placeholder: '{\n  "entity_refs": ["entity.account"]\n}',
        },
        {
          name: "capability_json",
          label: "Capability JSON",
          value: semanticJsonText(item?.capability, {
            inferential_ready: true,
            supported_sample_summaries: ["numeric_sample_summary"],
          }),
          placeholder:
            '{\n  "inferential_ready": true,\n  "supported_sample_summaries": ["numeric_sample_summary"]\n}',
        },
      ],
      buildCreatePayload: (values) => ({
        profile_ref: values.profile_ref,
        profile_kind: values.profile_kind,
        schema_version: values.schema_version || "v1",
        subject_kind: values.subject_kind,
        subject_ref: values.subject_ref,
        requirement: semanticParseJson(values.requirement_json, "Requirement JSON", null),
        capability: semanticParseJson(values.capability_json, "Capability JSON", null),
      }),
      buildUpdatePayload: (values) => ({
        requirement: semanticParseJson(values.requirement_json, "Requirement JSON", null),
        capability: semanticParseJson(values.capability_json, "Capability JSON", null),
      }),
      relationshipFields: (item) => [
        { label: "subject_ref", value: item?.subject_ref || "-" },
        { label: "subject_revision", value: String(item?.subject_revision ?? "-") },
        { label: "required_entities", value: (item?.requirement?.entity_refs || []).join(", ") || "-" },
        {
          label: "population_subject_refs",
          value: (item?.requirement?.population_subject_refs || []).join(", ") || "-",
        },
        {
          label: "supported_sample_summaries",
          value: (item?.capability?.supported_sample_summaries || []).join(", ") || "-",
        },
      ],
      relatedRefs: (item) =>
        [
          item?.subject_ref ? { label: "Subject Semantic Object", ref: item.subject_ref } : null,
          ...(item?.requirement?.entity_refs || []).map((ref) => ({ label: `Required ${ref}`, ref })),
        ].filter(Boolean),
      operatorGuidanceTitle: "Compile Compatibility Guidance",
      operatorGuidanceNote:
        "Compatibility profiles encode compile-time rules that are not derivable from object contracts alone. The operator view highlights subject freeze state and payload intent.",
      operatorGuidanceFields: (item) => [
        { label: "profile_kind", value: item?.profile_kind || "-" },
        { label: "subject_kind", value: item?.subject_kind || "-" },
        {
          label: "subject_freeze",
          value:
            item?.subject_revision == null
              ? "Unpublished profile or unresolved subject revision"
              : `Pinned to published subject revision ${item.subject_revision}`,
        },
        {
          label: "inferential_ready",
          value:
            item?.capability?.inferential_ready == null
              ? "-"
              : item.capability.inferential_ready
                ? "true"
                : "false",
        },
      ],
    },
  };
}
