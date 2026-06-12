# Stepwise Semantic Authoring Design

Status: draft design.

This document defines the target-state agent workflow and API surface for
Marivo semantic layer construction. It supersedes
`docs/specs/semantic/authoring-pipeline-design.md` (three-phase flow,
`assess_authoring`, `AuthoringSourceInput`) and the workflow sections it in
turn superseded. It complements
`docs/specs/semantic/python-semantic-layer.md`, which continues to own the
semantic object model and decorator contracts.

## Problem Statement

The current pipeline (three phases: discover, batch-author, single readiness
closeout) has four structural gaps for agent-driven modeling:

1. **No per-object control.** Phase 2 authors many objects between reloads, so
   the first error surfaces at closeout, far from the decision that caused it.
   There is no enforced order across object kinds and no point where the agent
   must prove it has enough information before writing one object.
2. **One generic assessment for every kind.** `assess_authoring(object_kind=…)`
   returns untyped `EvidenceFact` lists. The facts a dimension decision needs
   (value distribution) differ from what a relationship needs (join-key
   overlap), but the API cannot express either specifically.
3. **Unscoped scans.** Sampling executes `select(...).limit(n)` with no
   partition pruning. On partitioned lakehouse tables this is costly, and the
   sample lands on an arbitrary (often oldest) split, producing stale evidence
   for enum values, time formats, and join keys.
4. **Duplicated references.** `AuthoringSourceInput` re-declares
   `datasource` + table + columns even after the entity is registered, and
   physical inspection exists twice (`md.inspect_table` returning
   `TableMetadata`, `project.inspect_table` returning `TableContext`).

## Design Requirements

1. A strictly controlled, small-step agent flow: build
   domain, entity, dimension, time dimension, single-entity metric,
   relationship, cross-entity base metric, derived metric — in that order, one
   semantic object at a time. The agent defines an object only when
   information is sufficient; otherwise it asks the user or abandons the
   candidate.
2. The semantic API supplies the information needed to build each object, and
   API responsibility boundaries match the flow's granularity.
3. Each object kind has a preparation API that gathers the kind-specific data
   (metadata, bounded sampling, registry analysis).
4. Data access respects large tables: partition-pruned scans by default,
   explicit scan-row and column caps, structured scan reports.
5. Datasource and semantic object references are unified; nothing is declared
   or passed twice.

## Core Principles

- **Stateless steps.** Each agent step runs in a fresh interpreter. Every API
  call is self-contained: inputs are refs plus scan controls, outputs are
  frozen DTOs. No build-session state is persisted; recovery is re-running the
  current step.
- **Two kinds of order enforcement.** Dependency order is enforced in code
  (hard gates fail closed). Pure kind ordering is enforced by the skill and
  surfaced as advisory warnings.
- **Evidence vs business sufficiency.** A `*Brief.status` judges only
  evidence-side sufficiency (metadata reachable, samples collected,
  prerequisites loaded). Business decisions the evidence cannot settle are
  externalized as `AuthoringQuestion`s for the agent's knowledge or the user.
- **Fail closed.** Missing backend access, unresolvable partition scope, and
  missing prerequisites are structured errors, never degraded best-effort
  results.
- **Sample facts are not table truth.** Every sample-derived number carries
  the scan scope that produced it and is flagged approximate.
- **The API provides facts; the agent provides creative decisions.** No
  prepare API generates code, names, or business definitions.

## Persistence Boundary

Carried forward unchanged from the superseded pipeline design. Marivo
persists only durable project artifacts:

- authored semantic Python files under `.marivo/semantic/<domain>/_domain.py`;
- explicit decision-ledger records (user confirmations, dangerous choices,
  abandoned candidates);
- datasource definitions and project configuration.

Inspection and preparation results are fresh observations. Implementations may
keep short-lived in-process caches inside one `SemanticProject` instance, but
observations are never reused across sessions as authoritative input.

## The Authoring Ladder

Within one domain, objects are built in eight rungs, kind-major:

```text
1 domain
2 entity                  (one per physical table, one at a time)
3 dimension               (per entity, one column at a time)
4 time_dimension          (per entity)
5 metric                  (single-entity base metrics)
6 relationship
7 cross-entity base metric
8 derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
ladder rung.

### The Per-Object Cycle

Every rung iterates the same cycle, one semantic object per iteration:

```text
prepare_<kind>(...) -> Brief
  ├─ status == "blocked"      -> fix the blocker (access, scope, missing
  │                              prerequisite) or abandon the candidate
  ├─ blocking questions open  -> answer from documented knowledge, or ask the
  │                              user (AskUserQuestion mapping below); record
  │                              the answer as a ledger confirmation
  │     └─ unanswerable       -> abandon: record authoring_abandoned, skip
  └─ status == "sufficient" and no open blocking question
        -> append exactly ONE object to _domain.py
        -> verify_object(ref)      (fix loop until passed)
        -> next object
```

The skill forbids writing more than one semantic object per cycle and forbids
advancing past a failing `verify_object`.

## Control Model

### Hard Gates (enforced in code, fail closed)

| API | Precondition |
| --- | --- |
| `prepare_entity` | `domain` ref loaded; datasource resolvable |
| `prepare_dimensions` / `prepare_time_dimension` / `prepare_metric` | `entity` ref loaded |
| `prepare_metric(filter_dimensions=…)` | every referenced dimension loaded |
| `prepare_relationship` | both entity refs and all join-key dimension refs loaded |
| `prepare_cross_entity_metric` | all entity refs loaded; a relationship path exists between root and every joined entity (missing path is a `blocked` issue naming the gap) |
| `prepare_derived_metric` | every component metric ref loaded |
| `verify_object` | ref resolves after load; project datasource access available |

Gate failures raise structured errors with kind `missing_prerequisite`
(or `datasource_unreachable`), the missing refs, and the rung that produces
them.

### Soft Ladder (enforced by skill, surfaced as advisories)

Pure kind ordering — e.g. declaring metrics before any time dimension exists —
is driven by the skill checklist. `verify_object` emits advisory warnings for
detectable inversions (kind `ladder_order_advisory`), such as a metric on an
entity that has no time dimension yet.

## Unified Reference Model

Physical references appear in exactly two places:

1. **Phase-0 discovery** — `md.*` inspection APIs taking a datasource name
   plus `md.table(...)` / `md.file(...)`.
2. **`prepare_entity` input** — the single bridge from physical to semantic.

After an entity is registered, every API input is a semantic ref
(`"sales.orders"` strings or typed `EntityRef` / `DimensionRef` / `MetricRef`,
i.e. `SemanticRefInput`). Prepare and verify resolve physical sources and
datasources from the registry; the agent never re-supplies
datasource/table/column tuples for registered objects.

The `table(...)` / `file(...)` source constructors move to
`marivo.datasource`; `ms.table` / `ms.file` remain as re-exported aliases so
entity authoring is unchanged. There is exactly one physical table reference
shape in the library: `md.table("orders", database="sales_mart")`.

All data-touching project APIs resolve datasource access from
`.marivo/datasource` per call and close connections before returning.
Explicit `bind_datasource_access` choreography is removed from the agent
contract (implementations may keep injection hooks for tests).

## Datasource Inspection Surface (`marivo.datasource`)

All physical inspection lives in `marivo.datasource`. The semantic layer
composes these primitives and never re-implements them.

### Scan Controls

```python
@dataclass(frozen=True)
class ScanScope:
    partition: Mapping[str, str] | Literal["latest"] | None = "latest"
    max_rows: int = 1000          # scan row cap per call
    max_columns: int = 50         # scanned column cap per call
    timeout_seconds: int | None = 30

@dataclass(frozen=True)
class ScanReport:
    partition_used: Mapping[str, str] | None
    partition_resolution: Literal["explicit", "latest", "none", "unpruned"]
    rows_scanned: int
    columns_scanned: tuple[str, ...]
    truncated: bool
    elapsed_seconds: float
    warnings: tuple[str, ...]
```

`partition` semantics:

- explicit mapping — exact pruning predicate;
- `"latest"` (default) — resolve the newest non-empty partition from partition
  metadata (no data scan);
- `None` — explicit acknowledgment of an unpruned scan; allowed, reported as
  `partition_resolution="unpruned"` with a warning.

`"latest"` resolution rules:

1. Read `TableMetadata.partitions`. Unpartitioned table → `resolution="none"`,
   plain bounded LIMIT.
2. If enumerable, pick the maximum partition value (composite max for
   multi-level partitions; prefer the newest partition with a known non-zero
   row count; if a scan returns zero rows, step back to the next-newest at most
   3 times, with a warning).
3. Partitioned but not cheaply enumerable → fail closed with structured error
   `partition_scope_required`, instructing the caller to pass an explicit
   `partition={...}` or an explicit `partition=None`.

Per-backend partition enumeration channels: Hive/Iceberg via Trino
`"<table>$partitions"`; ClickHouse `system.parts`; MySQL
`information_schema.partitions`; DuckDB and file sources have no partition
concept. Partition scoping serves both cost (pruned scans) and evidence
freshness (LIMIT-only samples land on arbitrary, often oldest, splits and
mislead enum/time-format/join-key judgments).

`timeout_seconds` keeps the current best-effort semantics: budget is checked
before and after the read; profiling is skipped with a warning when exceeded.

### `md.inspect_table(datasource, source) -> TableMetadata`

Pure metadata, zero row reads.

```python
md.inspect_table("warehouse", md.table("orders", database="sales_mart"))
```

`TableMetadata` keeps its current fields (`backend_type`, `comment`,
`columns: tuple[ColumnMetadata, ...]` with name/type/nullable/comment/ordinal,
`is_view`, `view_definition`, `warnings`) and adds:

```python
partitions: PartitionInfo | None        # None = unpartitioned

@dataclass(frozen=True)
class PartitionInfo:
    columns: tuple[ColumnMetadata, ...]
    total_count: int | None              # approximate, when cheaply known
    latest: tuple[PartitionValue, ...]   # bounded newest-K (default 10)
    enumerable: bool                     # False -> "latest" fails closed

@dataclass(frozen=True)
class PartitionValue:
    values: Mapping[str, str]
    row_count: int | None                # when the backend exposes it
```

`md.inspect_source` is merged into this API and removed.

### `md.inspect_columns(datasource, source, *, columns=None, scope=ScanScope()) -> ColumnInspection`

One bounded scan, multi-column profiling. `columns=None` means all columns,
capped by `scope.max_columns` with an explicit truncation warning — this is
the whole-table light profile used by `prepare_entity`.

```python
md.inspect_columns(
    "warehouse",
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.ScanScope(partition={"dt": "20260611"}),
)
```

```python
@dataclass(frozen=True)
class ColumnInspection:
    datasource: str
    source: TableSource | FileSource
    profiles: tuple[ColumnProfile, ...]
    scan: ScanReport

@dataclass(frozen=True)
class ColumnProfile:        # moves here from marivo.semantic.dtos
    column: str
    data_type: str
    nullable: bool | None
    comment: str | None
    null_count: int          # within sample
    empty_count: int
    distinct_count: int
    top_values: tuple[tuple[object, int], ...]   # top-10
    sample_values: tuple[object, ...]            # raw glimpse, <= 10
    min_value: object | None                     # sample-local
    max_value: object | None
```

Profile statistics are computed client-side over the bounded sample frame; no
aggregate pushdown, so scan cost is bounded by `max_rows × columns`. Time
format inference is not in this layer: the datasource returns raw values;
temporal interpretation belongs to `marivo.semantic` (`time_format.py`).

### `md.probe_join_keys(from_side, to_side, *, scope=ScanScope(), key_sample_size=500) -> JoinKeyProbe`

New primitive backing `prepare_relationship`.

```python
md.probe_join_keys(
    from_side=md.JoinSide("warehouse", md.table("orders"), columns=("customer_id",)),
    to_side=md.JoinSide("warehouse", md.table("customers"), columns=("customer_id",)),
)
```

Implementation strategy: sample up to `key_sample_size` distinct keys from the
from-side within `scope`, then run a bounded `IN`-list membership query on the
to-side and count per-key duplication. Two independent queries plus
client-side comparison — works across datasources, never full-scans the
to-side.

```python
@dataclass(frozen=True)
class JoinSide:
    datasource: str
    source: TableSource | FileSource
    columns: tuple[str, ...]

@dataclass(frozen=True)
class JoinKeyProbe:
    type_compatible: bool
    sampled_key_count: int
    matched_key_count: int
    match_rate: float                    # approximate
    max_rows_per_key: int                # to-side duplication
    avg_rows_per_key: float
    cardinality_estimate: Literal["one_to_one", "many_to_one", "indeterminate"]
    from_scan: ScanReport
    to_scan: ScanReport
```

### `md.preview`

Unchanged contract (bounded row glimpse, LIMIT early termination), plus an
optional `scope: ScanScope | None = None` parameter. Evidence-grade sampling
always goes through `inspect_columns`.

## Semantic Prepare Surface (`marivo.semantic`)

### Common Envelope

```python
BriefStatus = Literal["sufficient", "needs_input", "blocked"]
```

Every Brief carries `status: BriefStatus`,
`questions: tuple[AuthoringQuestion, ...]`,
`issues: tuple[AssessmentIssue, ...]`, kind-specific typed fact fields, and —
for data-touching kinds — `scan: ScanReport` (the relationship Brief carries
its scan reports inside `JoinKeyProbe` instead). Generic `EvidenceFact` lists
are removed; facts are concrete typed fields. Fields described as `str` over a
closed vocabulary (granularity, additivity, decomposition kind, verification
status, cardinality) reuse the existing `Literal` types from
`marivo.semantic.typing` / `marivo.semantic.ir`; this document does not
introduce parallel string vocabularies. All Briefs follow the Result Contract
(`.show()`, `.render()`, one-line `repr`).

Status semantics:

| Status | Meaning | Agent action |
| --- | --- | --- |
| `blocked` | Prerequisite, source, column, access, or scope failure; answers cannot fix it | Fix inputs/environment or abandon |
| `needs_input` | Evidence collected, but blocking business questions remain | Answer from knowledge or AskUserQuestion; record confirmation; then author without re-prepare |
| `sufficient` | Evidence complete, no open blocking question | Author exactly one object |

`IssueKind` vocabulary extends to: `missing_prerequisite`,
`partition_scope_required`, `datasource_unreachable`, `missing_source`,
`missing_column`, `type_incompatible`, `unreachable_entity`,
`duplicate_candidate`, `static_check_failed`, `authored_object_invalid`,
`ladder_order_advisory`.

Reuse-before-add is baked into Briefs: each prepare reports existing or
similar registered objects as facts (`duplicate_*` / `similar_*` fields), so
searching is not a separate choreographed step.

### `prepare_domain(*, name: str) -> DomainBrief`

Registry-only. Facts: existing domains with descriptions and business
definitions, exact name conflict, similar domains by synonym/keyword overlap.
Questions: business-boundary confirmation (advisory).

```python
@dataclass(frozen=True)
class DomainBrief:
    status: BriefStatus
    proposed_name: str
    name_conflict: bool
    existing_domains: tuple[DomainSummary, ...]
    similar_domains: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
```

### `prepare_entity(*, datasource, source, domain, scope=ScanScope()) -> EntityBrief`

The physical-to-semantic bridge. Means: `md.inspect_table` plus whole-table
light `md.inspect_columns`, then semantic interpretation.

```python
@dataclass(frozen=True)
class EntityBrief:
    status: BriefStatus
    datasource: str
    source: TableSource | FileSource
    domain: str
    table: TableMetadata                      # full metadata incl. partitions
    column_profiles: tuple[ColumnProfile, ...]
    primary_key_candidates: tuple[PrimaryKeyCandidate, ...]
    versioning_hints: VersioningHints
    time_like_columns: tuple[str, ...]
    existing_entity: str | None               # same source already modeled
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

@dataclass(frozen=True)
class PrimaryKeyCandidate:
    columns: tuple[str, ...]
    sampled_unique: bool          # within scan scope only
    distinct_ratio: float

@dataclass(frozen=True)
class VersioningHints:
    snapshot_partition: str | None        # partition column with snapshot cadence
    cadence_estimate: str | None          # e.g. "day"
    validity_pair: tuple[str, str] | None # (valid_from, valid_to) candidates
```

`time_like_columns` includes typed temporal columns plus string/integer
columns whose sampled values match known time formats (semantic-side
inference). Typical questions: row grain confirmation, primary key choice,
snapshot vs event table.

### `prepare_dimensions(*, entity, columns, scope=ScanScope()) -> tuple[DimensionBrief, ...]`

One scan for many columns, one Brief per column. Batch preparation is a scan
economy; authoring remains one dimension at a time.

```python
@dataclass(frozen=True)
class DimensionBrief:
    status: BriefStatus
    entity: str
    column: str
    profile: ColumnProfile
    value_shape: Literal[
        "enum_like", "id_like", "numeric", "boolean_like", "temporal_like", "free_text"
    ]
    duplicate_dimensions: tuple[str, ...]   # existing dims over same column
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport                        # shared across the batch
```

Typical questions: business meaning of enum codes, normalization policy.

### `prepare_time_dimension(*, entity, column, scope=ScanScope()) -> TimeDimensionBrief`

Single-column temporal probe: sampled values, strptime candidate matching with
backend dialect caveats (e.g. Trino/Presto `%i` minutes), range, partition
alignment, cadence evidence.

```python
@dataclass(frozen=True)
class TimeDimensionBrief:
    status: BriefStatus
    entity: str
    column: str
    profile: ColumnProfile
    detected_formats: tuple[FormatCandidate, ...]
    value_range: tuple[object | None, object | None]
    partition_aligned: bool                  # column is a partition key
    granularity_evidence: Granularity | None # existing granularity Literal
    cadence_estimate: tuple[int, str] | None # sample_interval evidence
    existing_time_dimensions: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

@dataclass(frozen=True)
class FormatCandidate:
    strptime: str
    match_rate: float
    backend_caveats: tuple[str, ...]
```

Typed temporal columns (`date` / `datetime` / `timestamp`) return no format
candidates; the Brief reminds the author that `date_format` must be omitted
for them. Typical questions: business time-axis choice when multiple
candidates exist (blocking), timezone, `date_format` confirmation.

### `prepare_metric(*, entity, measure_columns, filter_dimensions=(), scope=ScanScope()) -> MetricBrief`

Single-entity base metrics.

```python
@dataclass(frozen=True)
class MetricBrief:
    status: BriefStatus
    entity: str
    measure_profiles: tuple[ColumnProfile, ...]  # range/negatives/nulls
    filter_dimension_values: tuple[DimensionValueFact, ...]
    time_dimensions: tuple[str, ...]             # empty -> ladder advisory
    similar_metrics: tuple[str, ...]             # name/synonym matches
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

@dataclass(frozen=True)
class DimensionValueFact:
    dimension: str
    top_values: tuple[tuple[object, int], ...]
```

`measure_columns` may be empty for pure row-count metrics; measure profiling
is then skipped while filter-dimension and registry facts are still
collected. Typical questions: unit, filter caliber (refunds/test orders),
additivity, decomposition, `verification_mode` (is there `source_sql`?).

### `prepare_relationship(*, from_entity, to_entity, from_dimensions, to_dimensions, scope=ScanScope()) -> RelationshipBrief`

Means: registry checks plus `md.probe_join_keys` with sources resolved from
the two entities.

```python
@dataclass(frozen=True)
class RelationshipBrief:
    status: BriefStatus
    from_entity: str
    to_entity: str
    from_dimensions: tuple[str, ...]
    to_dimensions: tuple[str, ...]
    probe: JoinKeyProbe
    to_entity_versioning: str | None        # snapshot/validity interaction note
    existing_relationships: tuple[str, ...] # duplicate path check
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
```

Typical questions: business direction confirmation; blocking question when the
sampled cardinality contradicts the intended declaration.

### `prepare_cross_entity_metric(*, root_entity, entities, measure_columns, scope=ScanScope()) -> CrossEntityMetricBrief`

```python
@dataclass(frozen=True)
class CrossEntityMetricBrief:
    status: BriefStatus
    root_entity: str
    entities: tuple[str, ...]
    join_paths: tuple[JoinPathFact, ...]
    unreachable_entities: tuple[str, ...]   # no relationship path -> blocked
    measure_profiles: tuple[ColumnProfile, ...]  # root-entity measures
    root_time_dimensions: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

@dataclass(frozen=True)
class JoinPathFact:
    from_ref: str
    to_ref: str
    relationship: str
    cardinality: str            # declared on the relationship
    fanout_risk: bool           # one-to-many edge
```

Typical questions: `fanout_policy` (`block` vs `aggregate_then_join`), root
grain confirmation.

### `prepare_derived_metric(*, numerator, denominator=None, weight=None) -> DerivedMetricBrief`

Registry-only. Exactly one of `denominator` (ratio) or `weight`
(weighted average) must be provided; ambiguity fails closed.

```python
@dataclass(frozen=True)
class DerivedMetricBrief:
    status: BriefStatus
    decomposition_kind: Literal["ratio", "weighted_average"]
    components: tuple[ComponentFact, ...]
    propagated_verification: str            # projected status
    unit_hint: str | None                   # e.g. "CNY/{user}"
    similar_metrics: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]

@dataclass(frozen=True)
class ComponentFact:
    ref: str
    role: Literal["numerator", "denominator", "weight"]
    additivity: str
    decomposition_kind: str
    verification_status: str
    unit: str | None
```

Typical questions: ratio vs weighted-average intent, unit.

## `project.verify_object(ref, *, scope=ScanScope()) -> VerifyResult`

The per-object verification step. Backend access is required: when project
datasource access cannot be resolved, `verify_object` fails closed with a
structured `datasource_unreachable` error. There is no degraded static-only
mode.

Internal sequence:

1. **In-process full load** — executes local authoring files only; no backend
   access. Full reload is acceptable per object because the expensive work is
   backend previews, which are scope-bounded below.
2. **Static object checks** — absorbs `inspect_authored_object`
   responsibilities (ref resolution, structural validation, blast radius).
3. **Kind-adapted bounded runtime validation:**

| Kind | Runtime validation |
| --- | --- |
| domain | static only (load + registry presence) |
| entity | partition-scoped preview (`ScanScope`, default latest) |
| dimension | expression evaluation over the same bounded scan |
| time_dimension | actual `date_format` parse over scoped sample + granularity check |
| metric / cross-entity metric | materialize + compile + scope-bounded execution (the scope predicate is injected into the root entity source for partitioned entities; this validates executability and type — value correctness belongs to parity) |
| relationship | static only (join probing already happened in prepare; not repeated) |
| derived metric | compile only |

Additional behavior:

- Auto-recording (`metric_decomposition`, `time_dimension_identity`) happens
  during verify's load, replacing the manual "reload after authoring" rule.
- Soft-ladder advisory warnings are emitted here
  (`ladder_order_advisory`).

```python
@dataclass(frozen=True)
class VerifyResult:
    status: Literal["passed", "failed"]
    ref: str
    kind: AuthoringObjectKind
    issues: tuple[AssessmentIssue, ...]
    warnings: tuple[AssessmentIssue, ...]
    scan: ScanReport | None      # None for registry-only kinds
    auto_recorded: tuple[str, ...]
```

`AuthoringObjectKind` gains `"domain"`. Cross-entity base metrics verify as
kind `"metric"`; the cross-entity distinction exists only at the prepare
stage, where the required facts differ.

`status == "failed"` means fix and re-verify; the skill forbids advancing.

## Readiness Closeout

`project.readiness(refs)` remains the final gate: full re-check, parity for
`sql_parity` metrics, richness summary — and now an **abandoned-candidates
list**. Its role shifts: per-object verification has already surfaced
structural and runtime errors, so readiness is final consistency plus
parity/richness aggregation, not first error discovery.

Readiness previews adopt the same `ScanScope` partition defaults as
`verify_object` (today's unpruned `preview_limit=20` previews are aligned to
the scan layer).

## Abandon Protocol

When a candidate cannot reach sufficiency — the user cannot answer a blocking
question, or required evidence is unobtainable:

1. Record a decision-ledger entry via the existing `record_decision` path with
   the new decision kind `authoring_abandoned`: subject ref, object kind,
   reason, open question ids; candidate detail uses the existing
   `RejectedCandidate` shape.
2. Skip the object and continue the ladder. Dependents are naturally stopped
   by hard gates, with structured errors naming the missing prerequisite.
3. `readiness` reports the abandoned list. The record is informational: a
   later session may re-prepare the same candidate; abandonment is not a
   permanent block.

## AuthoringQuestion → User Question Mapping (skill rule)

When the agent cannot answer a question from documented knowledge, it asks the
user through its question tool (AskUserQuestion in Claude Code):

| `AuthoringQuestion` field | User question field |
| --- | --- |
| `prompt` + `reason` | question body |
| `decision_kind` | header (mapped to ≤ 12 chars) |
| `options` | options (top 4 by evidence support; remainder via free-text "Other") |
| `default_option` | listed first, marked recommended |

Answers are recorded as ledger confirmations so reruns are traceable.
Questions with `readiness_effect="blocks"` must be resolved before authoring;
`advisory` questions may proceed on defaults.

## Skill Contract

`marivo-skills/marivo-semantic/` is restructured around the ladder:

- **SKILL.md** stays short and routing-focused. Non-negotiables shrink to:
  ladder order; prepare-before-author; one object per write;
  verify-before-next; the question mapping; the abandon protocol; scan-scope
  discipline (never pass `partition=None` without stating why).
- **references/workflow.md** — rewritten as the eight-rung ladder with the
  per-object cycle, including runnable per-rung examples.
- **references/object-briefs.md** (new) — per-kind Brief field tables,
  sufficiency criteria, question-mapping table.
- **references/datasource.md** — `md.inspect_table` / `md.inspect_columns` /
  `md.probe_join_keys` / `ScanScope` usage.
- **references/evidence-and-ledger.md** — gains the abandon protocol and
  confirmation recording.
- **references/closeout.md**, **references/pitfalls.md**,
  **references/examples/** — updated to the new APIs.

Detailed rules live in references, not SKILL.md.

## API Change Summary

Breaking, no compatibility shims.

### Removed

| Symbol | Disposition |
| --- | --- |
| `SemanticProject.assess_authoring` | replaced by per-kind `prepare_*` |
| `SemanticProject.check_authoring_inputs` / `marivo.semantic.authoring_check` | internals rebuilt under `prepare_*` |
| `AuthoringSourceInput` | physical inputs only exist at `prepare_entity`; elsewhere semantic refs |
| `SemanticProject.inspect_authored_object` | merged into `verify_object` |
| `SemanticProject.inspect_table` / `inspect_columns` | physical inspection lives in `marivo.datasource` |
| `md.inspect_source` | merged into `md.inspect_table` |
| `TableContext`, `ColumnContext`, `ColumnEvidence`, `SourceEvidencePack` | replaced by `TableMetadata` / `ColumnInspection` |
| `MetadataOnlyPolicy`, `BoundedProfilePolicy`, `SelectedColumnsPolicy` | replaced by `ScanScope` |
| `EvidenceFact` (generic facts) | replaced by typed Brief fields |
| `ReviewStatus` | replaced by `BriefStatus` |
| `bind_datasource_access` (agent contract) | per-call resolution from `.marivo/datasource` |

### Added

| Module | Symbols |
| --- | --- |
| `marivo.datasource` | `ScanScope`, `ScanReport`, `PartitionInfo`, `PartitionValue`, `ColumnInspection`, `ColumnProfile` (moved), `JoinSide`, `JoinKeyProbe`, `probe_join_keys`, `table()` / `file()` constructors (with `ms.*` aliases retained) |
| `marivo.semantic` | `prepare_domain`, `prepare_entity`, `prepare_dimensions`, `prepare_time_dimension`, `prepare_metric`, `prepare_relationship`, `prepare_cross_entity_metric`, `prepare_derived_metric`; `DomainBrief`, `EntityBrief`, `DimensionBrief`, `TimeDimensionBrief`, `MetricBrief`, `RelationshipBrief`, `CrossEntityMetricBrief`, `DerivedMetricBrief` and their fact DTOs; `BriefStatus`; `verify_object` / `VerifyResult`; ledger decision kind `authoring_abandoned` |

### Kept

`readiness` (scan-aligned), `preview_dataset` / `preview_field` /
`preview_metric` and `parity_check` (debugging helpers), `richness`
(advisory), `SemanticCatalog` browsing (`ms.load()`, `list`, `get`,
`details`), `ms.help`, the decorator/builder authoring surface, and the
decision ledger.

## Supersession

| Document | Superseded content | Replacement |
| --- | --- | --- |
| `authoring-pipeline-design.md` | three-phase flow, `assess_authoring`, `AuthoringSourceInput`, internal `check_authoring_inputs` contract | this document |
| `authoring-pipeline-design.md` | persistence boundary, readiness closeout contract | carried forward here |
| `python-semantic-layer.md` | none of the object model; reader/workflow references to `assess_authoring` and project-level inspection | updated to this document |

## Documentation, Help, and Typing Impact

- New public functions carry full docstrings (purpose, parameters, return,
  example, constraints) and concrete types — no `Any`, no ambiguous unions.
- `ms.help` / `describe` cover every new public symbol (`prepare_metric`,
  `ScanScope`, `verify_object`, each Brief, …).
- `agent-guide.md` is unaffected (repository-wide rules unchanged).

## Testing

- Unit: scope resolution (`latest`, explicit, `None`, non-enumerable fail
  closed), per-backend partition enumeration adapters, each `prepare_*` gate
  and Brief derivation, `verify_object` per-kind runtime behavior and
  fail-closed backend requirement, abandon-record round-trip.
- Shared fixtures per `tests/conftest.py` / `tests/shared_fixtures.py`;
  DuckDB templates for scan-layer tests; live-backend partition enumeration
  covered by existing MySQL/ClickHouse/Trino integration suites where
  available.
- Entrypoints: `make test TESTS=...` narrow first, then `make test`,
  `make typecheck`, `make lint`.

## Acceptance Criteria

- An agent can build a domain end-to-end strictly through the ladder, one
  object per cycle, with every data touch partition-scoped or explicitly
  acknowledged as unpruned.
- Every `prepare_*` returns kind-specific typed facts sufficient to author
  that kind or a structured reason why not.
- No API input ever repeats datasource/table/column tuples for a registered
  semantic object.
- Out-of-dependency-order calls fail closed with `missing_prerequisite`.
- A blocked question can be answered via the user-question mapping or resolved
  by abandonment, and both outcomes are traceable in the ledger and the
  readiness report.
- `verify_object` catches structural and runtime errors immediately after each
  object is written; `readiness` reports no first-discovery structural errors
  in a ladder-followed build.
