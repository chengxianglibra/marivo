# AOI v0.1

AOI (Analysis Operation Interface) is a schema-only standard for analysis operation contracts. It defines how callers invoke atomic analysis intents and how implementations return portable artifacts.

## Status

Version: 0.1.0

Status: draft

AOI v0.1 defines foundation primitives, seven atomic intents, artifact contracts, failure contracts, and a capability declaration schema. It does not define transport, runtime sessions, private metadata, derived intents, or a conformance test suite.

## Layout

```text
aoi-spec/
  README.md
  VERSION
  CHANGELOG.md
  spec.md
  schema/
    aoi.schema.json
  examples/
    observe/
    compare/
    decompose/
    capability/
```

`schema/aoi.schema.json` is the canonical validation entry point. It keeps all reusable types under `$defs` so the public schema can be copied, reviewed, and validated without resolving cross-file references.

## Validate Examples

From the repository root:

```bash
npx --yes ajv-cli@5.0.0 validate --spec=draft2020 -s aoi-spec/schema/aoi.schema.json -d "aoi-spec/examples/**/*.json"
```

The command validates every JSON example against the AOI v0.1 schema.

## Scope

Included:

- Foundation primitives: `Predicate`, `TimeScope`, `TimeGranularity`, `CompareType`, `ArtifactRef`, `ArtifactItemRef`, `StepRef`, `AnalysisFailure`, `HypothesisContract`
- Atomic requests: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
- Artifact types: `scalar_observation`, `time_series_observation`, `segmented_observation`, `scalar_delta`, `time_series_delta`, `segmented_delta`, `delta_decomposition`, `anomaly_candidates`, `association_result`, `hypothesis_test_result`, `forecast_series`
- Capability declaration

Excluded from v0.1:

- Derived intents such as `attribute`, `diagnose`, and `validate`
- Composition or DAG schemas
- Transport bindings
- Runtime sessions, evidence graphs, planning, and caching
- Vendor extensions and private metadata envelopes
- Formal conformance fixtures
