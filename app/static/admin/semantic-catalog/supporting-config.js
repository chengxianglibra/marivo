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

function semanticLatestEnumVersion(item) {
  const versions = item?.versions || [];
  return versions.length ? versions[versions.length - 1] : null;
}

export function createSupportingSemanticCatalogConfig(statusBadge) {
  return {
    "process-objects": {
      label: "Process Objects",
      singularLabel: "Process Object",
      listTitle: "Process Object Catalog",
      idField: "process_contract_id",
      stableRef: (item) => item?.header?.process_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.process_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.process_contract_id },
        { label: "stable_ref", value: item.header?.process_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "process_type", value: item.header?.process_type || "-" },
        { label: "contract_mode", value: item.interface_contract?.contract_mode || "-" },
        { label: "context_kind", value: item.interface_contract?.context_kind || "-" },
        { label: "anchor_time_ref", value: item.interface_contract?.anchor_time_ref || "-" },
        {
          label: "exported_dimension_refs",
          value: (item.interface_contract?.exported_dimension_refs || []).join(", ") || "-",
        },
      ],
      listEndpointLabel: "GET /semantic/process-objects",
      createEndpointLabel: "POST /semantic/process-objects",
      updateEndpointLabel: "PUT /semantic/process-objects/{process_contract_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "New User Cohort",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Process contract description",
                multiline: true,
              },
            ]
          : [
              {
                name: "process_ref",
                label: "Process Ref",
                value: "",
                placeholder: "process.new_user_cohort",
              },
              {
                name: "display_name",
                label: "Display Name",
                value: "",
                placeholder: "New User Cohort",
              },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Context provider for new users",
                multiline: true,
              },
              {
                name: "process_type",
                label: "Process Type",
                value: "cohort_definition",
                placeholder: "cohort_definition",
              },
              {
                name: "process_contract_version",
                label: "Contract Version",
                value: "process.v1",
                placeholder: "process.v1",
              },
              {
                name: "contract_mode",
                label: "Contract Mode",
                value: "context_provider",
                placeholder: "context_provider",
              },
            ],
      jsonFields: (item) => [
        {
          name: "interface_contract_json",
          label: "Interface Contract JSON",
          value: semanticJsonText(item?.interface_contract, {
            contract_mode: "context_provider",
            context_kind: "cohort_membership",
            population_subject_ref: "subject.user",
            membership_cardinality: "exclusive_one",
            anchor_time_ref: "time.signup_time",
            exported_dimension_refs: ["dimension.signup_week"],
          }),
          placeholder:
            '{\n  "contract_mode": "context_provider",\n  "context_kind": "cohort_membership",\n  "population_subject_ref": "subject.user",\n  "membership_cardinality": "exclusive_one",\n  "anchor_time_ref": "time.signup_time",\n  "exported_dimension_refs": ["dimension.signup_week"]\n}',
        },
        {
          name: "payload_json",
          label: "Process Payload JSON",
          value: semanticJsonText(item?.payload, {
            process_type: item?.header?.process_type || "cohort_definition",
            cohort_key: "new_users",
            entry_population: { base_population_ref: "population.users" },
            cohort_anchor_ref: "time.signup_time",
          }),
          placeholder:
            '{\n  "process_type": "cohort_definition",\n  "cohort_key": "new_users",\n  "entry_population": {\n    "base_population_ref": "population.users"\n  },\n  "cohort_anchor_ref": "time.signup_time"\n}',
        },
      ],
      buildCreatePayload: (values) => ({
        header: {
          process_ref: values.process_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          process_type: values.process_type,
          process_contract_version: values.process_contract_version,
        },
        interface_contract: {
          ...semanticParseJson(values.interface_contract_json, "Interface Contract JSON", {}),
          contract_mode: values.contract_mode,
        },
        payload: semanticParseJson(values.payload_json, "Process Payload JSON", {}),
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        interface_contract: semanticParseJson(values.interface_contract_json, "Interface Contract JSON", {}),
        payload: semanticParseJson(values.payload_json, "Process Payload JSON", {}),
      }),
      relationshipFields: (item) => [
        {
          label: "exported_dimension_refs",
          value: (item?.interface_contract?.exported_dimension_refs || []).join(", ") || "-",
        },
        { label: "anchor_time_ref", value: item?.interface_contract?.anchor_time_ref || "-" },
      ],
      relatedRefs: (item) =>
        [
          item?.interface_contract?.anchor_time_ref
            ? { label: "Anchor Time", ref: item.interface_contract.anchor_time_ref }
            : null,
          ...(item?.interface_contract?.exported_dimension_refs || []).map((ref) => ({
            label: `Exported ${ref}`,
            ref,
          })),
        ].filter(Boolean),
    },
    dimensions: {
      label: "Dimensions",
      singularLabel: "Dimension",
      listTitle: "Dimension Catalog",
      idField: "dimension_contract_id",
      stableRef: (item) => item?.header?.dimension_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.dimension_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.dimension_contract_id },
        { label: "stable_ref", value: item.header?.dimension_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "structure_kind", value: item.interface_contract?.value_domain?.structure_kind || "-" },
        { label: "value_type", value: item.interface_contract?.value_domain?.value_type || "-" },
        { label: "domain_kind", value: item.interface_contract?.value_domain?.domain_kind || "-" },
        {
          label: "required_time_anchor_ref",
          value: item.interface_contract?.time_derived_requirement?.required_time_anchor_ref || "-",
        },
        { label: "parent_dimension_ref", value: item.interface_contract?.grouping?.parent_dimension_ref || "-" },
      ],
      listEndpointLabel: "GET /semantic/dimensions",
      createEndpointLabel: "POST /semantic/dimensions",
      updateEndpointLabel: "PUT /semantic/dimensions/{dimension_contract_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "Signup Week",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Time-derived signup bucket",
                multiline: true,
              },
              {
                name: "structure_kind",
                label: "Structure Kind",
                value: item?.interface_contract?.value_domain?.structure_kind || "flat",
                placeholder: "flat",
              },
              {
                name: "value_type",
                label: "Value Type",
                value: item?.interface_contract?.value_domain?.value_type || "string",
                placeholder: "string",
              },
              {
                name: "domain_kind",
                label: "Domain Kind",
                value: item?.interface_contract?.value_domain?.domain_kind || "open",
                placeholder: "open",
              },
            ]
          : [
              {
                name: "dimension_ref",
                label: "Dimension Ref",
                value: "",
                placeholder: "dimension.signup_week",
              },
              {
                name: "display_name",
                label: "Display Name",
                value: "",
                placeholder: "Signup Week",
              },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Week of signup",
                multiline: true,
              },
              {
                name: "dimension_contract_version",
                label: "Contract Version",
                value: "dimension.v1",
                placeholder: "dimension.v1",
              },
              { name: "structure_kind", label: "Structure Kind", value: "flat", placeholder: "flat" },
              { name: "value_type", label: "Value Type", value: "string", placeholder: "string" },
              { name: "domain_kind", label: "Domain Kind", value: "open", placeholder: "open" },
            ],
      jsonFields: (item) => [
        {
          name: "grouping_json",
          label: "Grouping JSON",
          value: semanticJsonText(item?.interface_contract?.grouping, null),
          placeholder:
            '{\n  "hierarchy_type": "calendar_rollup",\n  "parent_dimension_ref": "dimension.signup_month"\n}',
        },
        {
          name: "time_derived_requirement_json",
          label: "Time Derived Requirement JSON",
          value: semanticJsonText(item?.interface_contract?.time_derived_requirement, null),
          placeholder: '{\n  "required_time_anchor_ref": "time.signup_time"\n}',
        },
      ],
      buildCreatePayload: (values) => ({
        header: {
          dimension_ref: values.dimension_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          dimension_contract_version: values.dimension_contract_version,
        },
        interface_contract: {
          value_domain: {
            structure_kind: values.structure_kind,
            value_type: values.value_type,
            domain_kind: values.domain_kind,
          },
          grouping: semanticParseJson(values.grouping_json, "Grouping JSON", null),
          time_derived_requirement: semanticParseJson(
            values.time_derived_requirement_json,
            "Time Derived Requirement JSON",
            null
          ),
        },
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        interface_contract: {
          value_domain: {
            structure_kind: values.structure_kind,
            value_type: values.value_type,
            domain_kind: values.domain_kind,
          },
          grouping: semanticParseJson(values.grouping_json, "Grouping JSON", null),
          time_derived_requirement: semanticParseJson(
            values.time_derived_requirement_json,
            "Time Derived Requirement JSON",
            null
          ),
        },
      }),
      relationshipFields: (item) => [
        {
          label: "required_time_anchor_ref",
          value: item?.interface_contract?.time_derived_requirement?.required_time_anchor_ref || "-",
        },
      ],
      relatedRefs: (item) =>
        [
          item?.interface_contract?.time_derived_requirement?.required_time_anchor_ref
            ? {
                label: "Required Time Anchor",
                ref: item.interface_contract.time_derived_requirement.required_time_anchor_ref,
              }
            : null,
          item?.interface_contract?.grouping?.parent_dimension_ref
            ? { label: "Parent Dimension", ref: item.interface_contract.grouping.parent_dimension_ref }
            : null,
        ].filter(Boolean),
    },
    time: {
      label: "Time",
      singularLabel: "Time Semantic",
      listTitle: "Time Catalog",
      idField: "time_contract_id",
      stableRef: (item) => item?.header?.time_ref || "",
      displayName: (item) => item?.header?.display_name || item?.header?.time_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.time_contract_id },
        { label: "stable_ref", value: item.header?.time_ref || "-" },
        { label: "display_name", value: item.header?.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "semantic_roles", value: (item.header?.semantic_roles || []).join(", ") || "-" },
        { label: "role_count", value: String(item.header?.semantic_roles?.length ?? 0) },
        { label: "time_contract_version", value: item.header?.time_contract_version || "-" },
      ],
      listEndpointLabel: "GET /semantic/time",
      createEndpointLabel: "POST /semantic/time",
      updateEndpointLabel: "PUT /semantic/time/{time_contract_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.header?.display_name || "",
                placeholder: "Signup Time",
              },
              {
                name: "description",
                label: "Description",
                value: item?.header?.description || "",
                placeholder: "Primary signup timestamp",
                multiline: true,
              },
              {
                name: "semantic_roles",
                label: "Semantic Roles",
                value: semanticListText(item?.header?.semantic_roles),
                placeholder: "business_anchor\nmeasurement",
                multiline: true,
              },
            ]
          : [
              { name: "time_ref", label: "Time Ref", value: "", placeholder: "time.signup_time" },
              {
                name: "display_name",
                label: "Display Name",
                value: "",
                placeholder: "Signup Time",
              },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "Primary signup timestamp",
                multiline: true,
              },
              {
                name: "time_contract_version",
                label: "Contract Version",
                value: "time.v1",
                placeholder: "time.v1",
              },
              {
                name: "semantic_roles",
                label: "Semantic Roles",
                value: "business_anchor\nmeasurement",
                placeholder: "business_anchor\nmeasurement",
                multiline: true,
              },
            ],
      jsonFields: () => [],
      buildCreatePayload: (values) => ({
        header: {
          time_ref: values.time_ref,
          display_name: semanticOptionalText(values.display_name),
          description: semanticOptionalText(values.description),
          time_contract_version: values.time_contract_version,
          semantic_roles: semanticSplitRefs(values.semantic_roles),
        },
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        semantic_roles: semanticSplitRefs(values.semantic_roles),
      }),
      relationshipFields: (item) => [
        { label: "semantic_roles", value: (item?.header?.semantic_roles || []).join(", ") || "-" },
        {
          label: "binding_surfaces",
          value: "Typed bindings should ground this time ref through carrier time_surfaces.",
        },
      ],
      relatedRefs: () => [],
      operatorGuidanceTitle: "Time Usage Guidance",
      operatorGuidanceNote:
        "Time contracts remain lightweight by design; the operator view focuses on role coverage and how bindings will expose them.",
      operatorGuidanceFields: (item) => [
        { label: "primary_roles", value: (item?.header?.semantic_roles || []).join(", ") || "-" },
        {
          label: "grounding_path",
          value: "Use Typed Bindings to expose time_surfaces before metrics, processes, or dimensions rely on this time ref.",
        },
        {
          label: "publish_expectation",
          value: "Publish freeze makes the current role set read-only; changing role coverage requires a new draft revision.",
        },
      ],
    },
    "enum-sets": {
      label: "Enum Sets",
      singularLabel: "Enum Set",
      listTitle: "Enum Set Catalog",
      idField: "enum_set_contract_id",
      stableRef: (item) => item?.header?.enum_set_ref || "",
      displayName: (item) => item?.display_name || item?.header?.enum_set_ref || "",
      detailFields: (item) => [
        { label: "object_id", value: item.enum_set_contract_id },
        { label: "stable_ref", value: item.header?.enum_set_ref || "-" },
        { label: "display_name", value: item.display_name || "-" },
        { label: "status", valueHtml: statusBadge(item.status) },
        { label: "revision", value: String(item.revision ?? "-") },
        { label: "updated_at", value: item.updated_at || "-" },
      ],
      interfaceSummaryTitle: "Interface / Payload Summary",
      interfaceSummaryFields: (item) => [
        { label: "value_type", value: item.header?.value_type || "-" },
        { label: "versions", value: String(item.versions?.length ?? 0) },
        {
          label: "latest_version",
          value: semanticLatestEnumVersion(item)?.enum_version || "-",
        },
        {
          label: "latest_value_count",
          value: String(semanticLatestEnumVersion(item)?.values?.length ?? 0),
        },
      ],
      listEndpointLabel: "GET /semantic/enum-sets",
      createEndpointLabel: "POST /semantic/enum-sets",
      updateEndpointLabel: "PUT /semantic/enum-sets/{enum_set_contract_id}",
      formFields: (item, mode) =>
        mode === "edit"
          ? [
              {
                name: "display_name",
                label: "Display Name",
                value: item?.display_name || "",
                placeholder: "Country Code",
              },
              {
                name: "description",
                label: "Description",
                value: item?.description || "",
                placeholder: "ISO country codes",
                multiline: true,
              },
            ]
          : [
              {
                name: "enum_set_ref",
                label: "Enum Set Ref",
                value: "",
                placeholder: "enum.country_code",
              },
              {
                name: "display_name",
                label: "Display Name",
                value: "",
                placeholder: "Country Code",
              },
              {
                name: "description",
                label: "Description",
                value: "",
                placeholder: "ISO country codes",
                multiline: true,
              },
              { name: "value_type", label: "Value Type", value: "string", placeholder: "string" },
            ],
      jsonFields: (item) => [
        {
          name: "versions_json",
          label: "Versions JSON",
          value: semanticJsonText(item?.versions, [
            {
              enum_version: "v1",
              values: [{ value_key: "US", raw_value: "US", label: "United States" }],
            },
          ]),
          placeholder:
            '[\n  {\n    "enum_version": "v1",\n    "values": [\n      {\n        "value_key": "US",\n        "raw_value": "US",\n        "label": "United States"\n      }\n    ]\n  }\n]',
        },
      ],
      buildCreatePayload: (values) => ({
        header: { enum_set_ref: values.enum_set_ref, value_type: values.value_type },
        display_name: values.display_name || "",
        description: values.description || "",
        versions: semanticParseJson(values.versions_json, "Versions JSON", []),
      }),
      buildUpdatePayload: (values) => ({
        display_name: semanticOptionalText(values.display_name),
        description: semanticOptionalText(values.description),
        versions: semanticParseJson(values.versions_json, "Versions JSON", []),
      }),
      relationshipFields: (item) => [
        {
          label: "latest_version",
          value: semanticLatestEnumVersion(item)?.enum_version || "-",
        },
        {
          label: "latest_value_keys",
          value:
            (semanticLatestEnumVersion(item)?.values || [])
              .slice(0, 5)
              .map((entry) => entry.value_key)
              .join(", ") || "-",
        },
      ],
      relatedRefs: () => [],
      operatorGuidanceTitle: "Enum Governance Guidance",
      operatorGuidanceNote:
        "Enum sets are versioned value domains. This view emphasizes which snapshot is latest and whether the governed domain is ready for downstream dimensions.",
      operatorGuidanceFields: (item) => [
        {
          label: "latest_enum_version",
          value: semanticLatestEnumVersion(item)?.enum_version || "-",
        },
        {
          label: "latest_value_count",
          value: String(semanticLatestEnumVersion(item)?.values?.length ?? 0),
        },
        {
          label: "dimension_contract_expectation",
          value: "Enumerated dimensions should reference this enum set version explicitly in their own contract payloads.",
        },
      ],
    },
  };
}
