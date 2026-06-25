# Authoring Discover Design

Date: 2026-06-25
Status: Approved design, pending written-spec review
Related: `agent-guide.md` ("Agent-Facing Surface Principles"),
`docs/specs/semantic/stepwise-authoring-design.md`,
`docs/superpowers/specs/2026-06-11-unified-datasource-surface-design.md`,
`docs/superpowers/specs/2026-06-21-semantic-column-authoring-design.md`

## Problem

Marivo semantic-layer authoring currently exposes datasource facts through
`md.inspect_table`, `md.inspect_columns`, and `md.probe_join_keys`. Those
functions are useful physical primitives, but they make the agent choose among
low-level tools before it can follow the semantic authoring ladder. They also
split one authoring task across multiple public entry points: an agent building
a measure can call `inspect_columns`, then `prepare_measure`, then maybe
`preview`; an agent building a relationship calls `probe_join_keys`, then
`prepare_relationship`. The user-facing path is mechanically correct but not
agent-simple.

The desired contract is stricter: Marivo should expose one datasource discovery
entry point per semantic authoring need, return typed evidence that is easy for
agents to read with `.show()`, and keep lower-level inspection/probe operations
as implementation details. This is a breaking public-surface cleanup; no
compatibility shims, deprecation aliases, or data migrations are in scope.

Marivo still must not become a semantic inference engine. Discovery should
compile deterministic datasource evidence and rule signals. The agent remains
responsible for reasoning over evidence, deciding what to ask the user, and
authoring exactly one semantic object at a time.

## Goals

- Replace agent-facing `md.inspect_*` and `md.probe_join_keys` with a
  `md.discover_*` family aligned to semantic authoring rungs.
- Keep each public discovery function single-purpose so agents do not choose a
  generic `purpose=` value.
- Return typed result objects that satisfy the shared `AgentResult` protocol:
  bounded `repr`, deterministic `.render()`, and readable `.show()`.
- Include deterministic `signals` and `issues`, each tied to a `rule_id` and
  source evidence. Do not return semantic recommendations or business
  definitions.
- Preserve bounded scan behavior through scope helper functions that return
  `md.ScanScope` and through `ScanReport` evidence.
- Keep runtime datasource values as transient evidence. Do not persist sampled
  dimension values into semantic objects, `ai_context`, or a value cache.
- Keep `md.raw_sql` as an explicit read-only escape hatch for diagnostics that
  `discover_*` cannot express.
- Rewrite the packaged `marivo-semantic` skill to teach `md.discover_*` as the
  datasource-first evidence path and `ms.prepare_*` as the semantic authoring
  readiness path.

## Non-Goals

- No backward compatibility for `md.inspect_table`, `md.inspect_source`,
  `md.inspect_columns`, `md.probe_join_keys`, `ColumnInspection`, or
  `JoinKeyProbe` as public names.
- No auto-authoring semantic objects.
- No business-meaning inference such as choosing gross vs. net revenue, default
  business time, additivity policy, currency unit, or status exclusions.
- No confidence scores, model-style recommendations, or automatic
  `should_author=True` fields.
- No persistence of sampled distinct dimension values as semantic
  `ai_context`, `observed_values`, enum declarations, or other authored
  semantic metadata. Source tables remain the truth for runtime values.
- No normalization-rule inference. Value normalization belongs to the agent,
  user-confirmed business policy, explicit semantic expressions, or authoritative
  dimension tables.
- No raw SQL inside semantic decorator or helper expression bodies. SQL remains
  an escape hatch or provenance metadata via `ms.from_sql(...)`.
- No change to metric tiering: row-level facts remain measures, tier-1 metrics
  default to `ms.aggregate`, and derived metrics remain semantic-registry
  constructs.

## Public Datasource Surface

The agent-facing datasource evidence surface becomes:

```python
md.discover_entity(
    datasource: md.DatasourceRef,
    source: md.DatasetSource,
    *,
    scope: md.ScanScope | None = None,
) -> EntityDiscoveryResult

md.discover_dimensions(
    datasource: md.DatasourceRef,
    source: md.DatasetSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: md.ScanScope | None = None,
) -> DimensionDiscoveryResult

md.discover_time_dimensions(
    datasource: md.DatasourceRef,
    source: md.DatasetSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: md.ScanScope | None = None,
) -> TimeDimensionDiscoveryResult

md.discover_measures(
    datasource: md.DatasourceRef,
    source: md.DatasetSource,
    *,
    columns: tuple[str, ...] | None = None,
    scope: md.ScanScope | None = None,
) -> MeasureDiscoveryResult

md.discover_relationship(
    *,
    from_side: md.JoinSide,
    to_side: md.JoinSide,
    scope: md.ScanScope | None = None,
    key_sample_size: int = 500,
) -> RelationshipDiscoveryResult

md.discover_dimension_values(
    datasource: md.DatasourceRef,
    source: md.DatasetSource,
    *,
    column: str,
    scope: md.ScanScope | None = None,
    limit: int = 50,
) -> DimensionValueDiscoveryResult
```

`md.preview(...)` remains the bounded raw row preview. `md.raw_sql(...)` is
added as an explicit escape hatch. The removed public names are:

```text
md.inspect_table
md.inspect_source
md.inspect_columns
md.probe_join_keys
ColumnInspection
JoinKeyProbe
```

Internal modules may keep `_inspect_source`, `_inspect_columns`, and
`_probe_join_keys` helpers. They must not be re-exported, listed in `md.help()`,
or taught in skills/docs as agent-facing APIs.

`md.DatasetSource` becomes the public datasource-side alias for supported
physical sources returned by `md.table(...)`, `md.parquet(...)`, and
`md.csv(...)`. It mirrors the semantic authoring `DatasetSource` alias so public
discovery signatures do not expose a module-internal IR import path.

`datasource` is intentionally typed as `md.DatasourceRef`, returned by
`md.ref("warehouse")`, instead of a raw string. The datasource ref identifies
the configured connection and execution environment; `source` identifies the
physical table or file inside that datasource. They stay separate because they
have different semantics and different validation errors.

## Scope Helpers

Agents should not construct `md.ScanScope(...)` directly in ordinary authoring
flows. `ScanScope` remains the concrete value type returned by helpers and shown
in result evidence, but the public examples and skill instructions should use
intent-shaped helper functions:

```python
md.latest_partition(
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> md.ScanScope

md.partition(
    values: Mapping[str, str],
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> md.ScanScope

md.unpruned(
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> md.ScanScope
```

`md.latest_partition()` is the default helper for bounded discovery. It records
that discovery should use the latest available partition when the source has
partition metadata. `md.partition({...})` expresses a concrete partition
selection. `md.unpruned(...)` is the explicit escape from partition pruning and
must surface an informational issue in discovery results so agents can see that
the scan was intentionally broader.

Low-level `md.ScanScope(...)` construction is reserved for advanced debugging
and internal implementation. It should not appear in the `marivo-semantic`
default authoring recipes.

## Result Family

Discovery uses a shared result vocabulary plus kind-specific result types. It
does not use a single optional-field mega result.

```python
DiscoverySeverity = Literal["blocker", "warning", "info"]

@dataclass(frozen=True)
class DiscoverySignal:
    rule_id: str
    kind: str
    subject: str
    evidence: Mapping[str, object]

@dataclass(frozen=True)
class DiscoveryIssue:
    rule_id: str
    kind: str
    severity: DiscoverySeverity
    subject: str
    message: str
    evidence: Mapping[str, object]

@dataclass(frozen=True)
class SemanticJudgmentTarget:
    object_kind: Literal[
        "entity",
        "dimension",
        "time_dimension",
        "measure",
        "relationship",
    ]
    field_path: str
    question: str
    owner: Literal["agent", "user_or_project_context"]
```

Each result type is a frozen dataclass with `repr=False` and implements
`render()`, `show()`, and bounded `__repr__()` through `marivo.render`. Every
result includes:

- `datasource`
- `source`
- `table_metadata`
- `scan`
- `signals`
- `issues`
- `judgment_targets`
- kind-specific `candidates` or relationship evidence

`judgment_targets` is a deterministic checklist derived from the discover API
kind, not from a reasoning pass over the result data. It must stay separate
from evidence: no per-target `evidence_refs`, no sufficiency flag, no
confidence score, and no recommended next action. Marivo can tell the agent
which semantic authoring fields usually require judgment for this object kind;
the agent decides whether the evidence is enough, whether more exploration is
needed, or whether the user/project context must answer the question.

Judgment targets must use real semantic authoring field or parameter paths.
Fields nested under `ai_context` must be written with the `ai_context.` prefix,
such as `measure.ai_context.business_definition`. Targets must not mention
fields that belong to a different semantic layer. For example,
`metric.aggregation` belongs to `ms.aggregate(...)` metric authoring and must
not appear in `discover_measures(...)`.

The `.show()` output must be explicit that discovery is evidence only. A result
card should use language such as:

```text
MeasureDiscoveryResult datasource=warehouse source=orders candidates=3
status: evidence_only rows=1000 partition=latest truncated=False issues=1
judgment targets:
- measure.column: decide whether the candidate column is a row-level quantitative fact
- measure.name: choose the semantic measure label
- measure.unit: decide the authoritative unit, if any
- measure.additivity: decide additive, semi-additive, or non-additive policy
- measure.ai_context.business_definition: write the measure's business meaning
columns: column | type | signals | issues
amount | DOUBLE | numeric_type, unit_token_observed | nullable
refund_amount | DOUBLE | numeric_type, negative_values_present | nullable
available:
- .candidates
- .signals
- .issues
- .judgment_targets
- .scan
- .render()
- .show()
```

The text must not say that a candidate should be authored. It should show enough
structured evidence for the agent to decide whether to call `ms.prepare_*`,
perform another bounded discovery query, use `md.raw_sql`, or ask the user a
business question.

Dimension value distribution is a runtime data question, not an authored
semantic-object property. `md.discover_dimension_values(...)` returns bounded
current value evidence for one column when an agent needs to construct or check
filters. Its result must carry `scope`, `scan`, `limit`, truncation state, and a
clear "not exhaustive" status unless the backend can prove completeness within
the configured budget. Agents may use this result immediately, but Marivo must
not copy those values into semantic `ai_context` or persist them as enum
metadata.

## Candidate Types

Discovery candidates are datasource evidence, not semantic objects.

```python
@dataclass(frozen=True)
class EntityDiscoveryCandidate:
    table: str
    primary_key_candidates: tuple[PrimaryKeyCandidate, ...]
    time_like_columns: tuple[str, ...]
    partition_columns: tuple[str, ...]
    column_profiles: tuple[ColumnProfile, ...]
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

@dataclass(frozen=True)
class ColumnDiscoveryCandidate:
    column: str
    profile: ColumnProfile
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

@dataclass(frozen=True)
class TimeColumnDiscoveryCandidate:
    column: str
    profile: ColumnProfile
    detected_formats: tuple[FormatCandidate, ...]
    value_range: tuple[object | None, object | None]
    partition_aligned: bool
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

@dataclass(frozen=True)
class RelationshipDiscoveryEvidence:
    from_side: JoinSide
    to_side: JoinSide
    key_type_evidence: tuple[KeyTypeEvidence, ...]
    sampled_key_count: int
    matched_key_count: int
    match_rate: float
    max_rows_per_key: int
    avg_rows_per_key: float
    cardinality_evidence: Literal["one_to_one", "many_to_one", "indeterminate"]
    from_scan: ScanReport
    to_scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

@dataclass(frozen=True)
class DimensionValueFact:
    value: object
    count: int

@dataclass(frozen=True)
class DimensionValueDiscoveryResult:
    datasource: DatasourceRef
    source: DatasetSource
    column: str
    values: tuple[DimensionValueFact, ...]
    complete: bool
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
```

`PrimaryKeyCandidate`, `FormatCandidate`, `ColumnProfile`, `ScanScope`,
`ScanReport`, `JoinSide`, and `TableMetadata` remain usable public value types
because they are exposed through discovery results or discovery inputs. They
are not presented as independent agent actions. `md.JoinSide` should also carry
a `DatasourceRef` instead of a raw datasource string.

## Judgment Target Templates

Discovery results should remind agents which semantic authoring judgments are
still required for the object kind. These targets are templates, not mapped
conclusions. They should appear as a separate `.judgment_targets` attribute and
a separate `.show()` section after status and before evidence.

`md.discover_entity(...)` targets:

- `entity.name`
- `entity.primary_key`
- `entity.ai_context.business_definition`

`md.discover_dimensions(...)` targets:

- `dimension.column`
- `dimension.name`
- `dimension.ai_context.business_definition`

`md.discover_time_dimensions(...)` targets:

- `time_dimension.column`
- `time_dimension.name`
- `time_dimension.granularity`
- `time_dimension.parse`
- `time_dimension.is_default`
- `time_dimension.ai_context.business_definition`

`md.discover_measures(...)` targets:

- `measure.column`
- `measure.name`
- `measure.unit`
- `measure.additivity`
- `measure.ai_context.business_definition`

`md.discover_measures(...)` must not include `metric.aggregation`,
`metric.measure`, or any other metric-layer target. Metric aggregation decisions
belong to later metric authoring, such as `ms.aggregate(...)`, after the measure
exists.

`md.discover_relationship(...)` targets:

- `relationship.name`
- `relationship.from_entity`
- `relationship.to_entity`
- `relationship.keys`
- `relationship.ai_context.business_definition`

`md.discover_dimension_values(...)` has no authored semantic field target by
default because distinct values are runtime evidence, not semantic metadata. If
shown at all, its target should be a non-authoring usage judgment such as
"decide current filter values from runtime evidence"; it must not mention enum
metadata, observed values, or `ai_context` persistence.

## Internal Inspection Enhancements

The public `discover_*` family may be implemented by enhancing private
inspection/profiling helpers. The right response to missing datasource evidence
is to improve those helpers, not to remove deterministic rules prematurely.
Only rules that require business meaning, normalization policy, or semantic
authoring choices should be removed from the Marivo rule layer.

The implementation should extend the current metadata/profile/probe evidence
model with these bounded facts when the backend can provide them cheaply:

- table metadata: declared primary keys, unique constraints or unique indexes,
  approximate or exact row count when available without an expensive scan,
  table comment, column comments, nullability, partition metadata, and backend
  capability warnings;
- column profile: scanned row count, non-null count, null count, empty string
  count, distinct count, distinct ratio, top-value concentration, min/max,
  numeric sign counts, string length statistics, coarse type family, and
  sampled pattern summary;
- time profile: supported parse candidates, ambiguous parse candidates, sampled
  parse success counts, value range, and partition alignment evidence;
- relationship probe: left and right key type evidence, null key counts,
  duplicate key counts per side, sampled distinct key counts, sampled match
  counts, fanout counts, and exactness/truncation flags;
- partition profile: resolved latest partition value, resolver method,
  unresolved reason, and whether the scan was explicitly unpruned.

All such fields remain evidence. They do not create semantic objects and do not
decide business meaning. If a fact cannot be produced within the scan budget,
the result should include a bounded warning or capability issue instead of a
weak semantic guess.

## Rule Catalog

Discovery rules are deterministic checks over metadata, bounded profiles, and
bounded join probes. Each rule emits a signal or issue with a stable `rule_id`.
Rules may be adjusted over time, but they must never claim business semantics.

Rule names should describe the evidence source. Prefer `sampled`, `observed`,
`candidate`, `shape`, or `metadata` wording over definitive semantic labels.
For example, a sampled unique column is primary-key evidence; it is not proof of
the entity primary key unless backed by declared datasource constraints or user
confirmation.

### Shared Scan and Metadata Rules

- `discovery_scan_truncated`: emit warning issue when `ScanReport.truncated` is
  true.
- `discovery_column_limit_truncated`: emit warning issue when `scope.max_columns`
  omitted columns.
- `discovery_metadata_warning`: forward table metadata warnings as issues.
- `discovery_unpruned_scan`: emit info issue when `scope.partition is None`.
- `discovery_latest_partition_unresolved`: emit warning issue when a source has
  partition metadata but latest partition could not be resolved to a concrete
  value within the available backend capability.

### Entity Rules

- `entity_declared_primary_key`: signal for datasource-declared primary key
  metadata when the backend exposes it.
- `entity_declared_unique_key`: signal for datasource-declared unique
  constraints or unique indexes when the backend exposes them.
- `entity_sampled_unique_column`: signal for each non-null column whose sampled
  distinct count equals rows scanned.
- `entity_no_primary_key_evidence`: warning issue when neither declared key
  metadata nor sampled unique-column evidence is available within the scan
  budget. This is evidence absence, not proof that the table has no key.
- `entity_temporal_column_detected`: signal for columns with date/datetime type
  or supported sampled date string formats.
- `entity_partition_column_detected`: signal for metadata partition columns.
- `entity_many_columns`: info issue when column count exceeds the bounded
  display or scan limit.

### Dimension Rules

- `dimension_low_cardinality`: signal when sampled distinct count is low.
- `dimension_high_cardinality`: signal when sampled distinct ratio is high.
- `dimension_boolean_like`: signal for boolean typed or two-valued sampled
  columns.
- `dimension_identifier_shape`: signal for identifier-shaped columns based on
  name, type family, nullability, sampled uniqueness, and cardinality evidence.
- `dimension_text_shape`: signal for text-shaped columns based on string type,
  length statistics, sampled distinct ratio, and top-value concentration.
- `dimension_empty_values_present`: warning issue for empty strings.
- `dimension_nullable`: info issue for sampled nulls.

These rules describe column shape only. They do not make enum completeness
claims and they do not produce value normalization groups. If an agent needs
current distinct values for filtering, it should call
`md.discover_dimension_values(...)` at analysis or authoring time and treat the
returned values as transient, bounded evidence.

### Dimension Value Rules

- `dimension_values_top_values`: signal carrying bounded value/count rows for a
  single requested column.
- `dimension_values_truncated`: warning issue when returned values hit `limit`
  or scan truncation means the result is not exhaustive.

Marivo intentionally does not emit normalization rules such as case folding,
punctuation folding, or synonym mapping. Those transformations can be wrong
without business context. Agents that suspect normalization is needed should use
bounded value evidence, project documentation, authoritative dimension tables,
or user confirmation to decide the policy.

### Time Dimension Rules

- `time_native_date`: signal for date typed columns.
- `time_native_timestamp`: signal for datetime/timestamp typed columns.
- `time_string_parse_candidate`: signal for sampled strings matching supported
  `ms.strptime(...)` formats.
- `time_integer_parse_candidate`: signal for integer date encodings only when
  sampled values, value range, and supported parse rules identify a specific
  candidate encoding.
- `time_integer_parse_ambiguous`: warning issue when sampled integer values can
  match multiple plausible encodings, such as epoch seconds, epoch
  milliseconds, `YYYYMMDD`, or compact hour buckets.
- `time_partition_aligned`: signal when the column is a metadata partition
  column.
- `time_no_parse_candidate`: warning issue for a requested column with no
  native temporal type and no supported sampled format.
- `time_ambiguous_hour_only`: blocker issue for hour-only sampled formats that
  cannot identify a date.

### Measure Rules

- `measure_numeric_type`: signal for numeric typed columns.
- `measure_non_numeric_type`: blocker issue for requested non-numeric columns.
- `measure_negative_values_present`: signal for sampled negative values.
- `measure_zero_values_present`: signal for sampled zero values.
- `measure_nullable`: info issue for sampled nulls.
- `measure_unit_token_observed`: optional signal when metadata comments contain
  a recognizable unit token. The raw comment and token evidence must be exposed;
  Marivo must not decide that the token is the authoritative business unit.

### Relationship Rules

- `relationship_key_type_evidence`: signal carrying left/right key type
  families and comparison notes.
- `relationship_key_type_mismatch_observed`: warning issue when key type
  families differ in a way that may require casting or user review.
- `relationship_match_rate`: signal carrying sampled match counts and rate.
- `relationship_no_matches_sampled`: warning issue when sampled match rate is
  zero.
- `relationship_fanout_observed`: warning issue when any sampled key maps to
  more than one right-side row.
- `relationship_probe_truncated`: warning issue when either side scan is
  truncated.

The rule catalog intentionally excludes these semantic or policy decisions:

- measure additivity, semi-additivity, and default aggregation;
- authoritative measure unit selection;
- timezone interpretation policy for timestamp columns;
- distinct dimension value persistence or enum completeness;
- value normalization, including case folding, punctuation folding, synonym
  mapping, and business-specific grouping.

Discovery results may show static notices for these boundaries in `.show()`,
but they must not emit them as datasource-derived rules. Agents decide these
items from bounded evidence, project documentation, authoritative dimension
tables, existing semantic objects, or user confirmation.

## Raw SQL Escape Hatch

`md.raw_sql` is public and explicit:

```python
md.raw_sql(
    datasource: md.DatasourceRef,
    sql: str,
    *,
    limit: int = 100,
    reason: str,
    include_types: bool = True,
) -> RawSqlResult
```

Constraints:

- read-only single statement by default;
- bounded fetch by `limit`;
- backend connection is closed before return;
- result includes SQL text, datasource, backend type, rows, columns, types,
  sample policy, and warnings;
- `.show()` labels it as `escape_hatch`;
- using raw SQL as semantic evidence requires the agent to cite it in the
  decision ledger or provenance notes;
- raw SQL never becomes an executable semantic expression body.

This escape hatch exists for diagnostics that `discover_*` cannot express, not
for the normal authoring ladder.

## Semantic Prepare Integration

`ms.prepare_*` remains the semantic authoring readiness surface. It should use
the same internal datasource primitives or consume `md.discover_*` results, but
its contract is different from discovery:

- `md.discover_*` works from physical datasource sources and returns datasource
  evidence.
- `ms.prepare_*` works in the semantic project context and returns authoring
  `Brief` objects with registry matches, ladder prerequisites, semantic object
  status, `AssessmentIssue`, and `AuthoringQuestion`.

The initial implementation can keep `ms.prepare_*` using internal helpers
directly to avoid a large dependency cycle. The public behavior must align with
the new surface: docs and skills should describe prepare as consuming the same
evidence model, not as calling public `md.inspect_*` or `md.probe_join_keys`.

Suggested mapping:

- `ms.prepare_entity(...)` uses entity discovery evidence plus registry matches.
- `ms.prepare_dimension(...)` uses dimension discovery evidence for the chosen
  column plus existing dimension matches and shadowing checks.
- `ms.prepare_time_dimension(...)` uses time discovery evidence plus existing
  time dimensions and parse/granularity checks.
- `ms.prepare_measure(...)` uses measure discovery evidence plus existing
  measure matches and semantic additivity/unit requirements.
- `ms.prepare_relationship(...)` uses relationship discovery evidence plus
  registry relationship matches and key ref validation.
- `ms.prepare_cross_entity_metric(...)` remains mostly semantic-registry based,
  using measure discovery only for root measure column profiles when requested.
- `ms.prepare_derived_metric(...)` remains registry-only.

## Revised `marivo-semantic` Skill Flow

The packaged skill should teach this source-to-semantic ladder:

```text
1. Register or select datasource refs with md.list()/md.describe()/md.test();
   bind them as warehouse = md.ref("warehouse").
2. Use md.discover_entity(...) before authoring each entity.
3. Use ms.prepare_entity(...) to get semantic authoring readiness.
4. Author one entity, then ms.verify_object(...).
5. Use md.discover_dimensions(...), md.discover_time_dimensions(...), or
   md.discover_measures(...) to select candidate columns.
6. For exactly one candidate, call the matching ms.prepare_* API.
7. Ask the user only for unresolved business semantics or policy.
8. Author exactly one object, then ms.verify_object(...).
9. Use md.discover_relationship(...) before ms.prepare_relationship(...).
10. Use md.discover_dimension_values(...) only when current value distribution
    is needed for filter construction or targeted clarification; do not persist
    returned values into semantic objects.
11. Use catalog.preview(...) after authored objects for runtime smoke checks.
12. Run ms.readiness(...) before analysis handoff.
```

The skill must stop teaching `md.inspect_table`, `md.inspect_columns`,
`md.inspect_source`, and `md.probe_join_keys`. It should mention internal
inspection only as an implementation detail, not as an agent action.

The skill should also state the evidence boundary:

- discovery facts are bounded-sample evidence only;
- signals and issues are deterministic rule outputs;
- discovery does not infer business meaning;
- datasource access uses md.DatasourceRef values, not raw datasource strings;
- scope uses helpers such as md.latest_partition(), md.partition({...}), or
  md.unpruned(...), not direct md.ScanScope(...) construction in recipes;
- distinct dimension values are runtime data facts and are explored on demand,
  not authored into `ai_context`;
- normalization policy is outside Marivo discovery and must be decided by the
  agent from business context, documentation, dimension tables, or user input;
- `md.raw_sql` is a scratch diagnostic escape hatch;
- `ms.prepare_*` and `ms.verify_object` are still required before advancing the
  ladder.

## Public Surface and Documentation Changes

Implementation must update the whole public surface slice in one change:

- `marivo.datasource.__all__`;
- `md.help()` metadata and public-surface snapshots;
- `docs/api/datasource.rst`;
- `docs/specs/semantic/stepwise-authoring-design.md`;
- `docs/specs/semantic/python-semantic-layer.md` if it references datasource
  evidence;
- `site/src/content/docs/*/latest/` pages that teach datasource evidence;
- `marivo/skills/marivo-semantic/` references and examples;
- public-surface tests;
- datasource discovery result protocol tests;
- semantic prepare tests that currently expect public inspect/probe calls.

Because this is breaking, tests and docs should be updated to the target
contract without deprecation paths.

## Testing Strategy

Add or rewrite focused tests before implementation:

- `md.__all__` no longer includes removed inspection/probe names.
- `md.help()` lists the `discover_*` family, `raw_sql`, and scope helpers.
- Public discovery signatures require `DatasourceRef` values and examples use
  `warehouse = md.ref("warehouse")`.
- Scope helper tests verify `md.latest_partition()`, `md.partition({...})`, and
  `md.unpruned(...)` return the expected `ScanScope` values.
- Every discovery result conforms to `AgentResult`.
- Every discovery result exposes `.judgment_targets` separately from evidence;
  targets contain real semantic authoring field or parameter paths, include
  `ai_context.` prefixes for AI context fields, and do not contain
  `evidence_refs`, sufficiency flags, confidence scores, or recommended
  actions.
- `discover_measures` judgment target tests assert that measure targets include
  `measure.additivity`, `measure.unit`, and
  `measure.ai_context.business_definition`, and do not include
  `metric.aggregation` or other metric-layer fields.
- Internal inspection/profile tests cover declared key metadata when available,
  enriched column profile fields, parse ambiguity evidence, relationship key
  type evidence, and latest-partition resolver outcomes.
- `discover_entity` returns table metadata, scan report, primary-key candidate
  evidence, time-like signals, and truncation issues.
- `discover_dimensions` emits low-cardinality, identifier-shape, text-shape,
  nullable, and empty-value signals/issues from bounded samples.
- `discover_dimension_values` returns bounded current value/count evidence,
  marks non-exhaustive results, and never suggests persistence into semantic
  `ai_context` or enum metadata.
- `discover_time_dimensions` emits native temporal and parse-candidate signals
  and reports ambiguous integer parses or unsupported hour-only formats.
- `discover_measures` emits numeric/non-numeric, negative, zero, nullable, and
  optional unit-token evidence without choosing a unit or aggregation.
- `discover_relationship` replaces `probe_join_keys` and reports key type
  evidence, sampled match rate, and fanout evidence.
- `md.raw_sql` is bounded, read-only by default, closes connections, and labels
  results as escape-hatch evidence.
- `ms.prepare_*` tests still pass after switching to internal discovery
  helpers.
- `make examples-check` covers the updated semantic skill examples.

Run the narrowest relevant tests first, then broaden to:

```bash
make test
make typecheck
make lint
make examples-check
```

## Open Decisions Closed by This Spec

- `purpose=` is rejected. Public functions are separate `discover_*` entry
  points.
- `probe_join_keys` is not public. Relationship key evidence is
  `md.discover_relationship(...)`.
- `inspect_*` is not agent-facing. Internal helpers can remain private.
- `discover_*` emits deterministic evidence, signals, and issues only.
- Dimension value exploration is on-demand runtime evidence; sampled values are
  not persisted into semantic objects.
- Value normalization rules are outside Marivo discovery.
- `md.raw_sql` is public but explicitly marked as an escape hatch.
- No compatibility, alias, or migration layer is included.
