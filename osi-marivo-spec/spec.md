# OSI-Marivo Vendor Extensions Specification v0.1

**Date:** 2026-05-09
**Status:** Draft
**Targets:** OSI Core Metadata Spec v0.1.1

---

## 1. Introduction

This specification defines the MARIVO vendor extensions for the Open Semantic Interchange (OSI) Core Metadata Spec. The canonical schema only recognizes the MARIVO vendor namespace. These extensions carry Marivo-specific semantic metadata within standard OSI documents, enabling third-party tools to produce and consume Marivo-compatible semantic models.

A valid OSI-Marivo document is a valid OSI document. Tools that do not understand MARIVO extensions can safely ignore them via the standard `custom_extensions` mechanism.

### 1.1 Conformance

An OSI document is **OSI-Marivo conformant** when:

1. It is a valid OSI Core Metadata v0.1.1 document.
2. Every `custom_extensions` entry with `vendor_name: "MARIVO"` has a `data` field that conforms to the corresponding MARIVO extension payload schema defined in this specification.
3. All conditional constraints (Section 3) are satisfied.

### 1.2 Notation

- "MUST", "SHOULD", "MAY" follow RFC 2119 semantics.
- Schema references use JSON Schema draft 2020-12 `$ref` notation.
- All field names use `snake_case`.

---

## 2. Extension Mechanism

OSI Core defines a `custom_extensions` array on Dataset, Field, and Metric objects. Each entry has the shape:

```json
{
  "vendor_name": "MARIVO",
  "data": { }
}
```

The `data` field is a JSON object. Its structure MUST conform to the MARIVO extension payload schema for the parent entity type.

**At most one** MARIVO extension entry is permitted per `custom_extensions` array. Validators SHOULD reject documents with duplicate MARIVO entries on the same entity.

---

## 3. Extension Points

### 3.1 Dataset Extensions

**Payload schema:** `MarivoDatasetExtension`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `datasource_id` | string (minLength: 1) | Required | Marivo datasource reference used for routing, readiness checks, and schema resolution. |

**Semantics:** The `datasource_id` identifies which Marivo datasource connection owns the physical table referenced by `Dataset.source`. It enables the runtime to route queries and validate schema availability.

**Example:** See `examples/per-entity/dataset-datasource.json`

### 3.2 Metric Extensions

**Payload schema:** `MarivoMetricExtension`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `additive_dimensions` | string[] (minItems: 1) | Optional | Field names across which the metric is additive, including ordinary dimensions and time fields. |

**Example:** See `examples/per-entity/metric-full.json`

---

## 4. Shared Types

### 4.1 Expression (reused from OSI Core)

MARIVO metrics reuse the OSI Core `Expression` type for metric logic and any embedded conditions:

```json
{
  "dialects": [
    { "dialect": "ANSI_SQL", "expression": "status = 'active'" }
  ]
}
```

The `dialect` field uses the OSI `Dialect` enum: `ANSI_SQL`, `SNOWFLAKE`, `MDX`, `TABLEAU`, `DATABRICKS`.

### 4.2 AIContext (reused from OSI Core)

All OSI entities support an `ai_context` field for AI tool guidance. This is an OSI Core feature, not a MARIVO extension, but is noted here because it interacts with MARIVO's semantic layer usage.

---

## 5. Validation

### 5.1 Schema Validation

The canonical schema at `schema/osi-marivo.schema.json` validates complete OSI-Marivo documents in a single pass. It inlines OSI Core type definitions so no external schema resolution is required.

```bash
npx --yes ajv-cli@5.0.0 validate --spec=draft2020 \
  -s osi-marivo-spec/schema/osi-marivo.schema.json \
  -d "document.json"
```

### 5.2 Extension Payload Validation

The `data` field in each MARIVO custom extension is a JSON object. Validators can validate it directly against the corresponding `Marivo*Extension` schema.

---

## 6. Compatibility & Versioning

### 6.1 OSI Core Version

This specification targets OSI Core Metadata Spec **v0.1.1**. The `version` field in conformant documents MUST be `"0.1.1"`.

### 6.2 Extension Versioning

MARIVO extensions follow semantic versioning independently of OSI Core:

- **Patch** (0.1.x): Documentation fixes, no schema changes.
- **Minor** (0.x.0): New optional fields added to extension payloads. Existing documents remain valid.
- **Major** (x.0.0): Breaking changes to extension payload schemas.

### 6.3 Backwards Compatibility

New extension fields MUST be optional. Existing valid documents MUST remain valid after minor version bumps. Implementations SHOULD ignore unrecognized fields in extension payloads from newer minor versions.
