# OSI-Marivo Extensions Changelog

## 1.2.0 - 2026-05-19

### Changed

- Added optional `required_prefix` field to `MarivoFieldExtension`. Required when `format` is `hh` or `h`. Declares the field name of the date-format time field that provides date context for this hour-only field. Enables composite date+hour time axis auto-discovery for tables with separate date and hour partition columns.
- Added `hh` as a valid `format` value for hour-only time fields. Enables standalone hour partition columns (e.g., `log_hour: HH`) as time fields with `required_prefix` pairing.

## 1.1.0 - 2026-05-19

### Changed

- Added required `data_type` field to `MarivoFieldExtension`. Values: `date`, `timestamp`, `string`, `integer`. All time fields must now explicitly declare their physical SQL column type — Marivo does not infer it.
- Added optional `format` field to `MarivoFieldExtension`. Required when `data_type` is `string` or `integer`. Declares the temporal format pattern (e.g., `yyyymmdd`, `yyyy-mm-dd`, `yyyymmddhh`, `yyyymmdd-hh`, `yyyy-mm-dd-hh`, `yyyymmddthh`, `epoch_seconds`, `epoch_days`). Enables string-type partition columns as first-class time fields with pushdown-friendly SQL predicates.

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
