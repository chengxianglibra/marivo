# Per-Kind Brief Fields and Actions

Each `prepare_*` API returns a typed Brief with `status`, `issues`,
`questions`, and kind-specific fact fields. This reference lists the fields and
the exact agent action for each status.

## Common Envelope

Every Brief carries:

| Field | Type | Purpose |
| --- | --- | --- |
| `status` | `BriefStatus` | `"sufficient"`, `"needs_input"`, or `"blocked"` |
| `issues` | `tuple[AssessmentIssue, ...]` | Structured problems found during preparation |
| `questions` | `tuple[AuthoringQuestion, ...]` | Unresolved business decisions |
| `matches` | `tuple[RegisteredMatch, ...]` | Already-registered candidates with match basis |

Status actions:

| Status | Agent action |
| --- | --- |
| `blocked` | Fix the blocker (access, scope, missing prerequisite) or abandon the candidate with `authoring_abandoned` |
| `needs_input` | Answer blocking `AuthoringQuestion`s from documented knowledge or ask the user; record the answer as a ledger confirmation |
| `sufficient` | Author exactly one semantic object, then call `verify_object` |

## DomainBrief

`project.prepare_domain(name=...) -> DomainBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `proposed_name` | `str` | The domain name passed to the call |
| `existing_domains` | `tuple[DomainBriefSummary, ...]` | Already-registered domains with descriptions |
| `matches` | `tuple[RegisteredMatch, ...]` | `name_exact` or `synonym_exact` matches |

## EntityBrief

`project.prepare_entity(datasource=..., source=..., domain=..., scope=ScanScope()) -> EntityBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `datasource` | `str` | Datasource name |
| `source` | `TableSource \| FileSource` | Physical source |
| `domain` | `str` | Target domain name |
| `table` | `TableMetadata` | Full metadata including partitions |
| `column_profiles` | `tuple[ColumnProfile, ...]` | Bounded-sample profiles for all columns |
| `primary_key_candidates` | `tuple[PrimaryKeyCandidate, ...]` | Sampled unique columns |
| `versioning_hints` | `VersioningHints` | Snapshot/cadence/validity evidence |
| `time_like_columns` | `tuple[str, ...]` | Columns matching temporal formats |
| `scan` | `ScanReport` | Scan scope and truncation details |

## DimensionBrief

`project.prepare_dimensions(entity=..., columns=..., scope=ScanScope()) -> tuple[DimensionBrief, ...]`

One scan for many columns; one Brief per column. Author one dimension at a time.

| Field | Type | Purpose |
| --- | --- | --- |
| `entity` | `str` | Entity ref |
| `column` | `str` | The inspected column |
| `profile` | `ColumnProfile` | Bounded-sample profile |
| `value_shape` | `Literal[...]` | `"enum_like"`, `"id_like"`, `"numeric"`, `"boolean_like"`, `"temporal_like"`, `"free_text"` |
| `scan` | `ScanReport` | Shared across the batch |

## TimeDimensionBrief

`project.prepare_time_dimension(entity=..., column=..., scope=ScanScope()) -> TimeDimensionBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `entity` | `str` | Entity ref |
| `column` | `str` | The inspected column |
| `profile` | `ColumnProfile` | Bounded-sample profile |
| `detected_formats` | `tuple[FormatCandidate, ...]` | strptime matches with backend caveats |
| `value_range` | `tuple[object \| None, object \| None]` | Sample-local min/max |
| `partition_aligned` | `bool` | Whether this column is a partition key |
| `granularity_evidence` | `Granularity \| None` | Detected granularity from samples |
| `cadence_estimate` | `tuple[int, str] \| None` | Sampled interval evidence |
| `existing_time_dimensions` | `tuple[str, ...]` | Already-registered time dimensions on this entity |
| `scan` | `ScanReport` | Scan scope details |

## MetricBrief

`project.prepare_metric(entity=..., measure_columns=..., scope=ScanScope()) -> MetricBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `entity` | `str` | Entity ref |
| `measure_profiles` | `tuple[ColumnProfile, ...]` | Range/negatives/nulls for measure columns |
| `filter_dimension_values` | `tuple[DimensionValueFact, ...]` | Top values for filter dimensions |
| `time_dimensions` | `tuple[str, ...]` | Empty triggers `ladder_order_advisory` |
| `scan` | `ScanReport` | Scan scope details |

## RelationshipBrief

`project.prepare_relationship(from_entity=..., to_entity=..., from_dimensions=..., to_dimensions=..., scope=ScanScope()) -> RelationshipBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `from_entity` | `str` | From-side entity ref |
| `to_entity` | `str` | To-side entity ref |
| `from_dimensions` | `tuple[str, ...]` | From-side join-key dimension refs |
| `to_dimensions` | `tuple[str, ...]` | To-side join-key dimension refs |
| `probe` | `JoinKeyProbe` | Key match rate, cardinality, and scan reports |
| `to_entity_versioning` | `str \| None` | Snapshot/validity interaction note |

## CrossEntityMetricBrief

`project.prepare_cross_entity_metric(root_entity=..., entities=..., measure_columns=..., scope=ScanScope()) -> CrossEntityMetricBrief`

| Field | Type | Purpose |
| --- | --- | --- |
| `root_entity` | `str` | Root entity ref |
| `entities` | `tuple[str, ...]` | All participating entity refs |
| `join_paths` | `tuple[JoinPathFact, ...]` | Relationship paths between entities |
| `unreachable_entities` | `tuple[str, ...]` | Entities with no relationship path (blocked) |
| `measure_profiles` | `tuple[ColumnProfile, ...]` | Root-entity measure columns |
| `root_time_dimensions` | `tuple[str, ...]` | Time dimensions on root entity |
| `scan` | `ScanReport` | Scan scope details |

## DerivedMetricBrief

`project.prepare_derived_metric(numerator=..., denominator=None, weight=None) -> DerivedMetricBrief`

Registry-only. No datasource access.

| Field | Type | Purpose |
| --- | --- | --- |
| `decomposition_kind` | `Literal["ratio", "weighted_average"]` | Inferred decomposition type |
| `components` | `tuple[ComponentFact, ...]` | Component metrics with additivity and verification |
| `propagated_verification` | `str` | Projected verification status |
| `unit_hint` | `str \| None` | Suggested unit from component units |
