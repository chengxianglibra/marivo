# AOI Changelog

## 0.2.0 - 2026-05-16

### Added

- Derived request namespace at `$defs.derived_requests`.
- `validate` derived request contract using AOI `Slice` and `Hypothesis`.
- `attribute` derived request contract using AOI `Slice`, non-empty `dimensions`, fixed `decomposition_method`, and bounded `decomposition_limit`.
- Validate request example under `examples/validate/`.
- Attribute request example under `examples/attribute/`.

### Changed

- AOI scope now includes seven atomic requests plus the derived `validate` and `attribute` request contracts.

## 0.1.0 - 2026-05-08

Initial draft publication.

### Added

- Single canonical JSON Schema at `schema/aoi.schema.json`.
- Narrative specification at `spec.md`.
- Minimal examples for observe, compare, and decompose.
- Core `CompareType` enum with `normal`, `yoy`, `mom`, `wow`, `holiday_aligned_yoy`, `weekday_aligned_yoy`, and `weekday_aligned_mom`.
- Numeric result range and high/low semantics for deltas, decomposition contributions, anomaly scores, association coefficients, p-values, and forecast intervals.
- Request, success artifact, and failure artifact examples for all seven atomic intents.

### Deferred

- Formal conformance fixtures.
- Transport bindings.
- Governance and certification process.
