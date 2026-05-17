# AOI Changelog

## 0.2.0 - 2026-05-16

### Added

- Derived request namespace at `$defs.derived_requests`.
- `validate` derived request contract using AOI `Slice` and `Hypothesis`.
- `attribute` derived request contract using AOI `Slice`, non-empty `dimensions`, fixed `decomposition_method`, and bounded `decomposition_limit`.
- `diagnose` derived request contract using AOI `TimeScope`, `Expression`, `Slice`, explicit `candidate_dimensions`, and bounded follow-up/decomposition limits.
- Validate request example under `examples/validate/`.
- Attribute request example under `examples/attribute/`.
- Diagnose request examples under `examples/diagnose/`.

### Changed

- AOI scope now includes seven atomic requests plus the derived `validate`, `attribute`, and `diagnose` request contracts.
- `CompareType` now exposes alignment strategies only: `normal`, `holiday_aligned`, `weekday_aligned`, and `holiday_and_weekday_aligned`.
- `diagnose(auto_detect).granularity` now uses the standard `TimeGranularity` values instead of a narrower diagnose-only enum.

## 0.1.0 - 2026-05-08

Initial draft publication.

### Added

- Single canonical JSON Schema at `schema/aoi.schema.json`.
- Narrative specification at `spec.md`.
- Minimal examples for observe, compare, and decompose.
- Core `CompareType` enum.
- Numeric result range and high/low semantics for deltas, decomposition contributions, anomaly scores, association coefficients, p-values, and forecast intervals.
- Request, success artifact, and failure artifact examples for all seven atomic intents.

### Deferred

- Formal conformance fixtures.
- Transport bindings.
- Governance and certification process.
