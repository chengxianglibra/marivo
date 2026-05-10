# OSI-Marivo Vendor Extensions

MARIVO vendor extensions for the OSI (Open Semantic Interchange) Core Metadata Spec v0.1.1. This specification defines how Marivo-specific semantic metadata is carried within standard OSI documents using the `custom_extensions` mechanism.

## Status

Version: 0.1.0

Status: draft

## Layout

```text
osi-marivo-spec/
  README.md
  VERSION
  CHANGELOG.md
  spec.md
  schema/
    osi-marivo.schema.json
    osi-marivo.schema.yaml
  examples/
    minimal/
    complete/
    per-entity/
```

`schema/osi-marivo.schema.json` is the canonical validation entry point. It is self-contained (OSI Core types inlined) so validation works without resolving external references. `schema/osi-marivo.schema.yaml` is a human-readable contract view.

## Validate Examples

From the repository root:

```bash
npx --yes ajv-cli@5.0.0 validate --spec=draft2020 \
  -s osi-marivo-spec/schema/osi-marivo.schema.json \
  -d "osi-marivo-spec/examples/**/*.json"
```

## Scope

Included:

- MARIVO vendor extension payloads for: Dataset, Metric
- Extension mechanism documentation (the `custom_extensions` envelope pattern)

Excluded:

- OSI Core type definitions (referenced, not redefined — see OSI Core Metadata Spec v0.1.1)
- Transport bindings, runtime sessions, or API endpoint definitions
- Analysis operation contracts (see sibling `aoi-spec/`)
