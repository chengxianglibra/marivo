# AOI v0.2

AOI (Analysis Operation Interface) is a schema-only standard for analysis operation contracts. It defines how callers invoke atomic analysis intents, derived request contracts, and consume portable artifacts.

## Status

Version: 0.2.0

Status: draft

AOI v0.2 defines foundation primitives, seven atomic intents, two derived request contracts (`validate`, `attribute`), artifact contracts, and failure contracts. It does not define transport, runtime sessions, private metadata, arbitrary DAG composition, capability subsets, or a conformance test suite.

## Layout

```text
aoi-spec/
  README.md
  VERSION
  CHANGELOG.md
  spec.md
  schema/
    aoi.schema.json
    aoi.schema.yaml
  examples/
    observe/
    compare/
    decompose/
    correlate/
    detect/
    test/
    forecast/
    validate/
    attribute/
```

`schema/aoi.schema.json` is the canonical validation entry point. It keeps all reusable types under `$defs` so the public schema can be copied, reviewed, and validated without resolving cross-file references. `schema/aoi.schema.yaml` is an OSI-style readable contract view with top-level enumerations and snake_case schema names.

## Validate Examples

From the repository root:

```bash
npx --yes ajv-cli@5.0.0 validate --spec=draft2020 -s aoi-spec/schema/aoi.schema.json -d "aoi-spec/examples/**/*.json"
```

The command validates every JSON example against the AOI v0.2 schema.

Each intent directory contains request examples, successful artifact examples, and blocking failure artifact examples. Intents with multiple parameter or result shapes include separate examples for those shapes, such as scalar/time-series/segmented observe and compare requests.

## Scope

Included:

- Foundation primitives: `Expression`, `TimeScope`, `TimeGranularity`, `CompareType`, `AnalysisFailure`, `Hypothesis`
- Atomic requests: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
- Derived requests: `validate`, `attribute`
- Artifact types: `scalar_observation`, `time_series_observation`, `segmented_observation`, `scalar_delta`, `time_series_delta`, `segmented_delta`, `delta_decomposition`, `anomaly_candidates`, `association_result`, `hypothesis_test_result`, `forecast_series`

Excluded from v0.2:

- Derived intents other than `validate` and `attribute`, such as `diagnose`
- Composition or DAG schemas
- Transport bindings
- Runtime sessions, evidence graphs, planning, and caching
- Capability declaration manifests or partial-implementation support matrices
- Vendor extensions and private metadata envelopes
- Formal conformance fixtures
