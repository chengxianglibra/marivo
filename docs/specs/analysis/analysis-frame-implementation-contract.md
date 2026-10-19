# AnalysisFrame implementation contract and consumer inventory

Date: 2026-10-19

Status: S0 accepted after one independent review. This document makes implementation choices within
[the target design](query-result-reuse.md); it does not expose a second public
API. Baseline and review status are recorded in
[the progress ledger](analysis-frame-implementation-progress.md).

## Fixed public choices

The complete Frame method signatures are those in target section 3. Keep the
existing `Session.observe`, `Session.read`, and `Session.values` parameter
contracts, changing their return types to `AnalysisFrame`. Replace
`Session.query(population)` with `Session.frame(population)`. The other new
Session methods are exactly `persisted_frame(artifact_id: str)` and
`population_from(frame, *, on=...)`; their target signatures are not overloaded
with Artifact, raw tables, strings standing for axes, or optional source maps.

The preserved source signatures, with only their return type changed, are:

```python
observe(metrics: Mapping[str, Deferred], *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...] | Population = (),
        where: Deferred | PopulationPredicate | None = None,
        members: Population | None = None) -> AnalysisFrame
read(population: Population, fields: Mapping[str, Deferred]) -> AnalysisFrame
values(*, by: tuple[Deferred | AxisBinding, ...],
       columns: Mapping[str, str | None], rows: tuple[ValueRow, ...]) -> AnalysisFrame
```

`ValueRow` and the unfinished `_Statistic` remain existing private typing
protocols; the proposal's `Statistic` notation does not add a public export.
The implementation of `reduce` uses `Mapping[str, _Statistic]`.

`FrameAxis` is acquired only from `frame.axes` or `frame.axis(name)`. Its public
read-only fields are:

| Field | Type | Meaning |
| --- | --- | --- |
| `token` | `str` | Local axis token, meaningful only within the bound input |
| `name` | `str` | Exact current display name; duplicate names remain ambiguous |
| `role` | `Literal["field", "identity", "calendar", "paired"]` | Complete coordinate role |
| `key_names` | `tuple[str, ...]` | Ordered names of every physical component of the complete key |

It has a bounded single-line repr and metadata-only `show() -> None`. Its exact
input binding and dependency reference stay private. Type and attribute Help
resolve without constructing or executing a handle. Complete physical types
remain owned by the current structure; pending types are explicitly unresolved.
Neither a token nor a name proves equality between independent inputs.

Pending value types do not make already captured coordinate interpretation
unknown. The closed pending-axis family contains unresolved ordinary field or
identity axes plus exact frozen calendar or paired descriptors. Source capture
freezes a calendar once into the current schema and its attachment; unary and
binary construction retain those inert facts. Calendar compatibility and rule
applicability therefore run before value validation for pending, memory and
mixed inputs alike. Completion must preserve each known descriptor exactly;
construction never recovers it from a producer graph or live Catalog.

All user value names use one validator: a nonempty single segment, excluding
dot, backslash, control characters, `left_present`, and `right_present`.
Generated paths are immutable segment tuples; values and presence have distinct
ordered directories. An operator never scans old producer nodes to recover
these directories. `output_names` excludes coordinates and presence.

Identity choices follow the target: independent binding tokens for expression
and memory roots, stable Store/Session/Artifact input identity for stored roots,
and one result ID per successful evaluation. Carrier conversion retains result
identity but creates a fresh binding unless the lifecycle table returns self.
`row_count()` is the only input-independent unfinished statistic.

## Validation priority

For align/compare construction, validate in this order:

1. Parameter shape/policies and Store/Session ownership.
2. Reject any paired input axis.
3. Require equal axis counts, with a structural repair rather than `on`/outer.
4. Resolve the two allowed automatic cases, or validate a complete explicit
   bijection. Never supplement a partial mapping.
5. Validate bound handles, roles, complete key types, time interpretation and
   calendar match applicability. Then validate compare value names/types/units.

Full-key uniqueness, actual-domain exact coverage, arithmetic checks, and
attachment integrity belong to evaluation/consumption; optimization cannot
erase these obligations. Preview count validation precedes reads in every
carrier. Persisted metadata success does not certify unread payloads.

## Stage and test ownership

The implementation plan's A01-A24 matrix is authoritative. The following
owners cover every target section-12 clause; tests extend existing owners or
add a file only for the distinct new Frame responsibilities.

| Target responsibility | Acceptance IDs | Stage owner | Test owners |
| --- | --- | --- | --- |
| One type, names, fields, precise handles and lifecycle | A01-A03 | S1/S2/S3/S7 | New `tests/analysis/graph/test_frame_structure.py` and `tests/analysis/session/test_analysis_frame.py`; migrate `test_query_artifact_boundary.py` at S7 |
| General Population frame and explicit source bridge | A04/A12 | S2/S6 | `tests/analysis/materialization/test_population_membership.py`, `test_source_population_fusion.py`; focused Frame bridge cases |
| Current-row statistics, numeric/Duration/NA checks and original-quantity counterexamples | A05/A13 | S2/S7 | `tests/analysis/materialization/test_query_results.py`, `tests/analysis/numeric/test_scalar_types.py`; migrate owning cases rather than preserving retired calls |
| Align/compare, inference/error order, presence and complete actual domains | A06-A08/A11 | S4 | `tests/analysis/materialization/test_query_correspondence.py`, `test_query_results.py`; new data-only carrier cases |
| Frozen calendars, partial nominal matching, paired projection and codec | A09/A10/A11 | S3/S5 | `tests/analysis/materialization/test_query_correspondence.py`, `test_named_calendar_windows.py`; focused frozen-coordinate codec/cold worker cases |
| No production graph, source isolation, ownership lifetime, detached loaded results | A14/A15/A17/A18 | S1-S6 | `tests/analysis/session/test_analysis_frame.py`; `test_producer_recovery.py`, `test_graph_dataset_reads.py`, `test_artifact_receipts.py` and existing cold workers |
| Metadata-only show/audit and bounded preview | A16 | S2/S3/S7 | `test_query_artifact_boundary.py`, `test_artifact_receipts.py`, `test_terminal_publication.py`; Frame preview cases |
| Operator/SQL/merge/file sequence order | A19 | S2-S5 | `test_query_results.py`, `test_query_optimization.py`, affected source numeric/remote tests and cold workers |
| Audit inheritance and evaluate-before-publication | A20/A21 | S2/S3 | `test_terminal_publication.py`, `test_query_preparation.py`; Frame lifecycle/audit cases |
| Failure atomicity, writer concurrency, unknown outcome | A22 | S3/S8 | `test_terminal_publication.py`, `test_materialization_guard.py`, `test_native_graph_failures.py` and concurrency/recovery workers |
| Store generation/protocol, refusal of old data and unchanged bytes | A23 | S3/S7 | `test_versioned_ownership.py`, `test_artifact_receipts.py`, `test_analysis_state.py` |
| Exports, signature/Help/CLI/cards, docs/skills and RawSqlResult separation | A24 | S7/S8 | `tests/surface/`, `tests/packaging/test_skills.py`, affected datasource raw-SQL tests and API-doc gate |

## Integration boundaries

- Reuse the current compiler, fixed schedule, local kernels, source execution,
  Arrow codec, writer guard, journal and atomic commit. Fixed inputs enter that
  compiler; they do not use a second data executor.
- Keep `StageOutput` as an execution handoff. Extract complete independent data
  before a successful memory return; exclude graph/layout execution objects,
  semantic objects, components and Population execution frames from backing.
- A local service owns exact Store/Session identity, reading and publication;
  source configuration/resolvers/Catalog belong to explicit source preparation.
  No memory/stored backing or callback captures the production Session.
- S1-S6 remain private and unexported. Temporary adapters are implementation
  scaffolding only, with removal owned by S7. Do not publish compatibility aliases.
- Preserve required source Metric components and numerical checks until their
  actual consumers are removed. Receiver retirement is not a global component
  deletion.

## Actual producers, consumers and retirement

The read-only exploration records are `evidence/analysis-frame/s0-core.md`,
`s0-store.md`, and `s0-surface.md`. Concrete owners are:

| Contract | Producer | Consumers and cutover action |
| --- | --- | --- |
| Query / QueryKeys / ResultQuery | `dsl/query.py::_result`, `core/graph.py::capture_query` | `compiler/queries.py`, `compiler/populations.py`, `materialization/query_population.py`, key transport/fusion. Replace captured-production dependencies with explicit fixed inputs and typed Frame key projection; retain private source/compiler operation names only with current consumers. Remove public Query and its bound Session callback at S7. |
| BoundSignature / Column / Statistic | `core/model.py`, `dsl/query.py::__getitem__/coord` | `core/graph.py::input_graph`, `dsl/column.py`, reduce and compiler memoization. Separate structure from source declarations; keep fingerprints for compilation, use exact input IDs for handles. |
| ComparisonEndpoint | `binding/correspondence.py` | `compiler/query_program.py`, `methods/query_comparison.py` and Population projections. Data-side matching uses frozen descriptors; nominal bucket mapping must be independent from full-row endpoint presence. |
| StageOutput | `materialization/graph_exchange.py` | Local/source execution, original Metric kernels, drill and Population execution. Keep execution-only metadata inside one invocation; extract an independent completed result before returning memory. |
| ArtifactReader / TerminalReader | `dsl/artifact.py`, `materialization/graph_reading.py` | Artifact data/audit methods and Session reopening. Move data reads to Frame, retain audit reads, switch exact ID guidance to persisted_frame. |
| InvocationInput | `materialization/terminal_store.py::admit`, `terminal_protocol.py` | `_lazy_read_model.py`, `_lazy_runtime_reads.py`, graph/read validation. Replace query marker with existing result ID; admit only publication after evaluation. |
| display_order / CanonicalOrder | `terminal_publication.py::_publication`, `terminal_storage.py::canonical_frame` | `graph_reading.py::OutputRead`, `pandas_export.py::export_frame`. Delete order attachments, primary key sorting and reader sorting assumptions together; keep uniqueness and receipt validation. |

Retirement is consumer-specific:

| Route | Disposition | Preserved source responsibility |
| --- | --- | --- |
| Frame rollup/drill | Remove; explicitly reconstruct observe/read at the requested grain/fields | Metric formula/components and source attribute reading |
| Frame share | Remove; no equivalent contribution-share guarantee from column division | `share_additive` and other checks while actual source consumers remain |
| Frame calendar coarsening | Remove; reduce accepts existing FrameAxis only | Source calendar/grain construction and observation |
| top partition_by | Remove; no per-partition top replacement in this version | Source/internal consumers only if still required |
| Frame population() | Remove; only session.population_from with required explicit on | Population sets/relations, complete identity and source key transfer |
| relationship corresponding / broadcast | Remove Frame factories/routes and unsupported receiver branches; no generic replacement | CalendarCorrespondence factories; Population projection types only if their independent supported consumer remains after bridge migration |
| Query execute / Session.query | Remove; compute/persist and Session.frame replace them | Existing compiler, scheduler and numerical source kernels |
| Session.artifact / Artifact table methods | Remove; persisted_frame and Frame data methods replace them | Artifact commit/audit/findings/evidence view; RawSqlResult's separate reader |

## Source cleanup decision

Evaluation owns an execution-scoped `ExitStack` and existing `SourceSession`
cleanup. Every source owner must close successfully before result extraction
can return memory. A close failure is a typed evaluation failure with no Run,
Artifact or resource journal; do not return partial data or retry computation.
The current `open_query_sources` enters `SourceSession`, whose `close` requires
confirmed owned release (`datasource/adapters.py::_release_backend`).

The apparent conflict with durable MySQL/ClickHouse control obligations is not
a current producer conflict: `backend_reservation` creates only
`read_only_execution@v1`; the two control-close capability strings occur only
in recovery's fail-closed reader in `materialization/resources.py`, with no
current reservation producer. Preserve recovery of existing publication
obligations under the original writer guard. Do not invent a persistent
evaluation Run or second cleanup journal. S2 must test close failure before a
memory return; S3 must separately preserve unknown publication coordination.

The maintainer skill's `tests/test_packaged_skill_shape.py` path is stale in
this checkout. Use the actual owning `tests/packaging/test_skills.py` and
affected live Help; no packaged maintainer instructions are edited for this.

## Frozen calendar encoding for S2-S5

`calendar.v1` uses one finite directory, captured before row filtering. Its key
is the complete typed bucket key. Its nonnull fields are `nominal_label`
(string), `ordinal` (int64), and `nominal_start`, `nominal_end`, `actual_start`,
`actual_end` (timestamp[us, UTC]). Bounds are copied from the declared cells.
The ordinal is the original zero-based position in CalendarCoordinate.cells,
not the filtered row rank or a named snapshot's global period ordinal.

- Builtin day/week/month/quarter/year labels are the captured nominal start's
  civil date, `YYYY-MM-DD`; unit/step belong to the descriptor.
- Builtin second/minute/hour labels use the captured local nominal start's ISO
  timestamp with microseconds and numeric UTC offset, distinguishing DST folds.
- Named labels use canonical JSON scalar period keys from the exact captured
  snapshot and level, including the implicit civil-date day level. The inert
  named descriptor must include calendar identity, snapshot digest and level.

Freeze labels at source binding. Readers and matchers cannot recreate bounds
or labels from a model, CalendarAxis, current timezone rules or a display token.
Source capture must preserve structured grain facts at the owning producer;
S2 makes BoundTimeGrid.grain_spec and CalendarCoordinate.grain_spec authoritative
and derives their existing display-token properties. Their source producers
must propagate the actual Grain; no token parser, side cache or model reload
supplies a missing definition. Completed Frame descriptors retain only inert
builtin unit/step or named identity/digest/level, not the Grain object.

A paired mapping has the complete typed paired key plus nullable left/right
bucket-key columns with exact endpoint key types. Each endpoint is completely
present or absent; both cannot be absent. Each present key resolves uniquely
in its frozen directory. Mapping is independent of row-level presence.

S3 validates closed descriptors, receipts, exact types, unique keys/labels/
ordinals, complete original ordinal sequence, and valid per-bucket bounds.
S5 additionally requires continuous nonoverlapping nominal partitions and full
actual bucket coverage for every match, even after filtering removes rows.
S5 first builds a partial nominal correspondence without `how`, then applies
exact/outer to actual complete row keys. Extra directory buckets add no rows.
A partial actual bucket remains readable/persistable but cannot enter match.

S5/S3 concrete attachment handoff: the paired-map key columns are exactly the
PairedDataAxis columns. Ordered endpoint value slots/names are `left.<index>`
then `right.<index>`, with segment paths `("left", str(index))` and
`("right", str(index))`. Each endpoint column retains its exact physical type
and is nullable. One pure core layout builder owns this schema; codec, reader
and matching adapters consume it rather than maintaining separate inventories.
Shared full-coordinate validation checks every declared directory/map and all
primary key references. Matchability remains a separate stronger requirement.

Pandas presentation retains raw physical coordinate keys under the existing
MultiIndex. Full exports include isolated typed frozen interpretation in attrs;
positive previews resolve only the displayed keys and required endpoints to
captured labels/bounds. Metadata-only previews do not read attachments. This is
a presentation choice within the target's self-contained interpretation and
copy-isolation guarantees, not a new calculation contract or endpoint API.
