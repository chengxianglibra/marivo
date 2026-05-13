---
status: approved
created: 2026-05-13
---

# Semantic Layer Document Surface Design

## Problem

Marivo's semantic-layer MCP and HTTP surfaces currently expose fine-grained CRUD operations for
semantic models, datasets, fields, metrics, relationships, and readiness. That shape is precise for
programmatic resource management, but it is not agent-friendly. It makes agents choose among many
low-level tools and encourages Marivo to accumulate authoring behavior that agents can handle better
by editing structured JSON.

The target direction is to make OSI-Marivo JSON documents the semantic-layer authoring boundary.
Agents should collect business intent, inspect datasource metadata, draft a complete JSON document,
validate it, get user approval, and import it. Marivo should provide clear schemas, examples,
validation feedback, transactional import/export, and simple read surfaces.

## Goals

- Reduce the semantic-layer external surface to list, get, import, export, and validate.
- Support stdio MCP workflows where agents can pass OSI-Marivo JSON inline or through local files.
- Make validation the quality gate for schema, references, Marivo extensions, and runtime grounding.
- Use whole-model replacement semantics for imports, so the JSON document is the source of truth.
- Remove old semantic CRUD MCP/HTTP endpoints and their no-longer-needed application service methods.
- Update the `marivo-semantic-layer` skill to guide agents through JSON authoring and validation,
  not staged CRUD writes.

## Non-Goals

- No compatibility layer for the old dataset/field/metric/relationship CRUD MCP or HTTP endpoints.
- No new schema-discovery tool; authoring context should live in specs, docs, tool schemas, skill
  references, examples, and validation feedback.
- No UI work.
- No public publishing/admin workflow.
- No external version management inside Marivo; versioning remains outside the semantic import
  surface.

## External Surface

The semantic-layer surface becomes:

| Capability | HTTP | stdio MCP |
|---|---|---|
| List semantic models | `GET /semantic-models` | `list_semantic_models` |
| Get a semantic model | `GET /semantic-models/{model}` | `get_semantic_model` |
| Import OSI semantic models | `POST /semantic-models/import` | `import_osi_semantic_models` |
| Export OSI semantic models | `GET /semantic-models/export` | `export_osi_semantic_models` |
| Validate OSI semantic models | `POST /semantic-models/validate` | `validate_osi_semantic_models` |

Remove the previous model create/update/delete endpoints and all dataset, field, metric,
relationship, and readiness CRUD endpoints/tools from HTTP and MCP. `list_semantic_models` and
`get_semantic_model` remain read-only inspection surfaces. All writes go through
`import_osi_semantic_models`.

## Stdio MCP File and Inline JSON Contract

The stdio MCP tools support both inline and file-based JSON so agents can choose the most convenient
authoring mode:

| Tool | Input and output |
|---|---|
| `validate_osi_semantic_models` | Accepts either `document` or `input_path`. |
| `import_osi_semantic_models` | Accepts either `document` or `input_path`; always validates before writing. |
| `export_osi_semantic_models` | Returns an inline document and writes the same JSON to `output_path` when provided. |

Tool contracts should reject ambiguous inputs, such as both `document` and `input_path` when their
meaning would conflict. File read/write failures return structured tool errors instead of being
converted into validation failures.

HTTP remains JSON-native: validate and import accept JSON request bodies, and export returns JSON.

## Validation Contract

`validate_osi_semantic_models` is a full quality gate, not just JSON Schema validation. It validates:

1. OSI-Marivo schema conformance.
2. Duplicate names within each semantic scope.
3. Internal references, including dataset primary keys, unique keys, relationship fields, time
   fields, and metric dependencies that can be resolved from the document.
4. MARIVO custom extensions, including datasource grounding, metric additivity, observed dataset,
   observation grain, and primary time field.
5. Runtime datasource grounding: datasource accessibility, dataset relation existence, and field
   resolvability against the live datasource catalog.

The response must be structured and repair-oriented:

```json
{
  "valid": false,
  "schema_version": "0.1.1",
  "errors": [
    {
      "code": "UNKNOWN_FIELD",
      "message": "Metric order_revenue references field amount, but dataset orders has no field amount.",
      "json_pointer": "/semantic_model/0/metrics/0/expression",
      "severity": "error",
      "hint": "Add dataset field orders.amount or update the metric expression."
    }
  ],
  "warnings": [],
  "summary": {
    "models": 1,
    "datasets": 1,
    "fields": 8,
    "metrics": 3,
    "relationships": 0
  }
}
```

Validation errors should use JSON Pointer wherever possible so agents can edit the source document
reliably. Runtime validation errors should include enough context to repair grounding issues, such as
datasource id, schema, table, and column names when available.

## Import Contract

`import_osi_semantic_models` always runs the same validation path first. If validation fails, import
returns the validation errors and writes nothing.

For each semantic model in the document:

- If the model does not exist for the current owner scope, create it.
- If the model exists, replace the whole model graph with the imported model.
- Replacement includes datasets, fields, metrics, and relationships. Any old child object missing
  from the imported JSON is deleted.
- The import document is the source of truth for the resulting model graph.

Import is transactional. If any model in the import document fails validation or persistence, no
model from that import is partially written. Successful responses return an import report and summary
rather than exposing low-level CRUD details.

## Export Contract

`export_osi_semantic_models` accepts an optional `semantic_model_name`. When the name is provided,
the export contains that single visible model. When omitted, the export contains all semantic models
visible to the current requester. The exported JSON should be valid according to
`validate_osi_semantic_models` unless live datasource state changed after the model was stored.

For stdio MCP, `output_path` writes the exact exported document to disk and still returns the
document or a compact summary, so agents can either inspect the response or work with the file.

## Agent Authoring Context

Agents need enough information to draft correct OSI-Marivo JSON before validation. This design does
not add a schema-discovery tool, but it requires authoring context to be explicit and easy to find:

- Document the OSI-Marivo JSON Schema path and schema version in API docs and skill references.
- Keep MCP DTO schemas simple and explicit: `document`, `input_path`, `output_path`, and validation
  options only.
- Provide minimal, standard, and richer OSI-Marivo JSON examples in
  `marivo-semantic-layer/references/modeling.md` or an adjacent skill reference.
- Ensure validate/import/export responses include `schema_version`.
- Ensure validation errors include `json_pointer` and actionable `hint` values.

This keeps Marivo focused on specifications and quality gates instead of embedding a semantic
authoring wizard in the server.

## Skill Workflow

The `marivo-semantic-layer` skill should be rewritten around a document-first workflow:

1. Collect business context: metric definitions, reporting requirements, glossary terms, dashboard
   behavior, and domain constraints.
2. Inspect datasource metadata through datasource browse and preview tools.
3. Draft a complete OSI-Marivo JSON document, preferably in a local file for non-trivial models.
4. Run `validate_osi_semantic_models`.
5. Fix validation errors and repeat until validation passes.
6. Present the validated semantic model document or a concise summary to the user for confirmation.
7. After explicit user approval, run `import_osi_semantic_models`.
8. Confirm the imported state with `get_semantic_model` or `export_osi_semantic_models`.

The skill should no longer instruct agents to create datasets, fields, metrics, or relationships
through separate CRUD tools.

## Implementation Boundaries

Transport layers should stay thin:

```text
HTTP / stdio MCP
  -> transport DTOs and file handling
  -> semantic document application service
  -> validate / import / export helpers
  -> metadata storage and datasource catalog checks
```

The semantic application service should expose only list, get, validate, import, and export. Storage
mapping helpers and repositories may remain as implementation details. No transport should call
dataset/field/metric/relationship CRUD application methods because those methods should no longer be
part of the external semantic management contract.

## Error Handling

- Validation failures are normal responses from validate and pre-write import.
- Persistence failures during import abort the transaction and return a structured error.
- File read/write failures in stdio MCP are reported as file errors, not schema errors.
- Missing user identity or inaccessible datasource errors should fail closed and include clear
  remediation text.
- Import must never report success if it skipped invalid models or wrote only part of a document.

## Testing Strategy

Tests should cover:

- HTTP route inventory excludes removed CRUD and readiness routes.
- MCP tool inventory excludes removed CRUD and readiness tools.
- `validate_osi_semantic_models` catches schema errors, duplicate names, broken references,
  invalid Marivo extensions, inaccessible datasources, missing tables, and missing fields.
- `import_osi_semantic_models` calls the same validation path and writes nothing on failure.
- Import replaces the whole existing model graph for same-name models.
- Stdio inline JSON and `input_path` behave equivalently for validate/import.
- Stdio export writes `output_path` and returns the expected response shape.
- API docs and `marivo-semantic-layer` skill references no longer recommend semantic CRUD writes.

## Open Decisions Resolved

- This is a breaking change. Do not keep old semantic CRUD routes or tools as compatibility aliases.
- Import uses whole-model replacement for same-name models.
- Stdio MCP supports both inline JSON and file paths.
- Validation includes schema, internal references, Marivo extensions, and runtime datasource checks.
- Authoring context is provided through docs, skill references, tool schemas, examples, and validation
  feedback rather than a separate schema-discovery tool.
