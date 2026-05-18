# OSI-Marivo Extensions Changelog

## 1.0.0 - 2026-05-18

### Changed

- Added the `time_granularities` enum: `hour`, `day`, `week`, `month`, `quarter`, `year`.
- Added required MARIVO field extension payload `support_min_granularity` for every time field.
- Time fields now require exactly one MARIVO field extension; non-time fields reject field extensions.

## 0.1.0 - 2026-05-09

Initial publication.

### Added

- Narrative specification for all MARIVO vendor extensions (spec.md).
- Canonical JSON Schema lives in osi-marivo-spec/schema/osi-marivo.schema.json.
- Human-readable YAML schema view.
- Examples: minimal, complete, and per-entity.
- Vendor enumeration narrowed to the MARIVO namespace.

### References

- Targets OSI Core Metadata Spec v0.1.1.
