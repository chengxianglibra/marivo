# Slice 5b: private additive and component-mix attribution

Status: implementation complete; full private technical acceptance pending.

## Prerequisite and authorization

Slice 5a is privately accepted by its execution record and the public-cutover
plan. Implementation starts from clean `lazy-dataset` commit
`b6639ce618821613660e4b1658513584078b8781`. The owner approved this plan and
the field/ranking clarifications below on 2026-09-08. The planning check found
all 762 files in the 5a accepted candidate unchanged and passed 54 focused
comparison, numeric, publication and Finding tests. That is not a 5b gate.

## Runtime outcome and frozen contracts

Consume Dataset Core, Observation Model, Typed Operators, Planner/Pushdown,
Materialization Runtime, and Session Runtime Read designs. Produce complete
private joint/hierarchy Attribution through `delta.attribute(...)`, execute,
publish atomically, recover, and continue retained rows. Register only
`additive_difference@v1` and `component_mix@v1` in this unit. Exact admitted
sum/count and semi-additive folds use the former; admitted ratio, mean and
weighted-mean numerator/basis components use the latter.

The owner confirmed component-mix `current_value` and `baseline_value` store
allocated side terms. Contribution rank is ordinal within scope/resolution,
descending absolute contribution followed by typed key order. Positive and
negative shares use max(c, 0) and max(-c, 0) over the corresponding pool;
empty pools are undefined. Undefined overall endpoints fail rather than being
converted to a zero Delta.

Independent endpoints are computed through the exact fold/finalize closure
over complete selected pre-mapping state. Mapping/component totals and
contributions are checked against those endpoints, never used to invent them.
Logical missing-axis expansion also checks the original selected Delta
endpoints. No materialized origin can be expanded or replayed.

## Backend, storage and fixture scope

Use the tested DuckDB/Ibis adapter and exact pandas continuation. Preserve
original selections, source captures, sampled realization sharing and paired
time ordinals during logical expansion. Entity scope remains source-required;
source validation publishes aggregate checks without moving Entity identities
into local quality calculations. A local frontier is final for its dependent
successors. Complete inputs, parts, mapping/hierarchy growth and method output
remain within existing runtime guards.

Exercise local Parquet, immutable DuckDB engine and versioned S3-compatible
object receipts where each shape is admitted. Runtime fixtures use isolated
temporary projects, fresh interpreters and a dedicated versioned MinIO service.
The fixture and runtime-marker rules in `marivo-test-fixtures` apply.

## Exact code ownership and shared seams

- Operator owner: new `operators/attribute.py`, `attribution.py`,
  `attribution_contracts.py`, `attribute_values.py`, `delta_state.py`;
  existing `operators/contracts.py`, `compare.py`,
  `delta.py`, `errors.py`, `registry.py`, `row.py`, `row_values.py`;
  attribution contract/numeric tests and pure fixtures.
- Compiler/execution owner: new focused Attribution compiler modules; existing
  `compiler/nodes.py`, `normalize.py`, `lowering.py`, `placement.py`,
  `comparison.py`; `materialization/admission.py`, `local_worker.py`;
  attribution compiler/runtime tests and their focused helpers; the pure
  `operators/attribute_expansion.py` construction helper is also assigned here.
- Retained/publication owner: `materialization/retained.py`, `contracts.py`,
  `publication.py`, `store.py`, `recovery.py`, `storage.py`, `comparison_codec.py`,
  `comparison_publication.py`, and new focused Attribution codec/publication
  modules; `evidence/_dataset_types.py`, `_dataset_codec.py`, `_dataset_reads.py`;
  attribution publication/retained integrity tests.
- Coordinator: `observation/contracts.py`, `predicates.py`, `ordering.py`,
  `fold_contracts.py` and `coordinates.py` only where shared admission requires;
  `tests/lazy_observation_fixtures.py`, dedicated three-process runtime tests and
  worker, no-I/O regression extensions; owning designs, cutover allocation,
  this execution record and final gate evidence; `datasets/registry.py` and
  `actions.py` receive exact per-role shape admission and private consumer
  disclosure for expansion nodes. `datasets/descriptors.py` registers the
  exact positive-arity `bool_tuple:N` primitive and equal-arity physical
  refinement. `test_lazy_attribution_masks.py` covers these Core seams and
  local exact-mask validation. `materialization/local.py` validates Arrow
  list-backed mask keys without pandas dictionary encoding.

Module paths above are relative to `marivo/analysis/`; tests are under `tests/`.
Contributors coordinate immutable Attribution payload/semantics, Delta retained
parts, mask representation, and publication result interfaces before changing
shared consumers. They preserve others' work and use existing registry,
execution identity, writer and Store authority rather than parallel mechanisms.

Expansion uses internal `metric.expand_axes` and `delta.attribute_expanded`
producer registrations. Their consumer registrations are not discoverable;
the registry still validates their exact roles during construction. The
normal Delta continuation remains `attribute`. Attribution `where`, `rank`
and `limit` are result-only row operations: all emit zero new Findings and
preserve the original complete reconciliation proof and lineage.

## Implementation and acceptance sequence

1. Freeze this record and amend the owner-confirmed field contracts.
2. Implement complete private family/admission/numeric contracts and retained
   per-side component authority through Compare and Delta row selections.
3. Integrate logical axis expansion, exact source lowering and guarded local
   execution, including tuple-mask physical and predicate handling.
4. Integrate complete validation, Evidence, scoped Contribution Findings and
   immutable cold recovery in the existing atomic publication transaction.
5. Run independent positive/negative numerical and runtime cases, then freeze
   one candidate and run the broad gate with source manifests and full logs.

The matrix covers both layouts and methods over each admitted Delta shape;
logical/materialized and mixed Metric operands; one-sided/null/zero/negative
values; numeric overflow; selection before expansion; original time ordinals;
unrequested scope coordinates; shared sampling; mapped Other parents; real null,
Other and inactive-mask identities; nonadditive/overlapping partition rejection;
per-resolution endpoint reproduction and reconciliation; no-I/O barriers;
missing/corrupt/later parts and combined-budget failures before invocation;
worker loss/deadline/atomic rollback; 1000/1001 deterministic Finding extraction;
and source/local parity. Row continuations retain original reconciliation
authority; rank/limit produce no new Findings.

Three-process producer/continuation/cold-reader journeys must close the origin,
attribute a retained Delta, continue Attribution, verify rows/contracts/Findings,
and prove exact-key reuse with no new Run or source work. Source-required Entity
journeys prove no identities in metadata, Evidence, Findings, errors or cards.

## Implementation evidence map

| Contract | Owning regression evidence |
| --- | --- |
| Exact method/partition admission, scoped typed rows, private continuations and no-I/O construction | `test_lazy_attribute_contracts.py`, `test_lazy_attribution_masks.py`, `test_lazy_source_construction_no_io.py`, `test_lazy_observation_runtime_no_io.py` |
| Independent additive/component arithmetic, signed pools, exact rank, null/Other/hierarchy mapping and source/local parity | `test_lazy_attribute_numeric.py`, `test_lazy_attribute_compiler.py` |
| Logical axis expansion after original selections, paired time ordinals, shared sampling and source-only Entity validation | `test_lazy_attribute_compiler.py`, `test_lazy_attribute_source_runtime.py` |
| Logical/materialized operand combinations and distinct source domains feeding one local compare/attribute chain | `test_lazy_attribute_operand_runtime.py`, shared `lazy_compare_runtime_fixtures.py` |
| Exact side state, proof/fold-authority codecs, scoped Finding identity, cap/order/corruption and atomic rollback | `test_lazy_attribution_publication.py`, `test_lazy_delta_publication.py` |
| Complete later side parts, combined input, method/intermediate/output budgets, worker timeout/exit and atomic cleanup | `test_lazy_attribute_guards.py` |
| Source-closed retained Delta, three receipt families, both methods, row continuation, missing-axis barrier and third-process exact reuse | `test_lazy_attribution_runtime_acceptance.py`, `lazy_attribution_runtime_worker.py` |
| Exact consumed-state demand through projection, identity-only observation, earlier folds, Compare branches and Attribution continuations | `test_lazy_retained_roles.py`, `test_lazy_retained_runtime.py`, `test_lazy_retained_membership.py`, `test_lazy_retained_review_runtime.py` |

The implementation also corrects shared seams exposed by these actual paths:
internal producers retain explicit operand-role admission without a second
discoverable continuation; engine reads of unordered Dataset rows use the full
typed key order required by the storage validator; source validation reuses
compiled scalar SQL; and complete Delta side-state validation precedes the local
Attribution method. These changes remain within the existing authority and
execution owners.

Source validation checks side-presence flags against the primary Delta before
Top-K can combine partitions, reproduces each present row endpoint and checks
every complete scope against independent pre-mapping folds. Decimal selection
and Finding magnitude use exact absolute values without ambient-context
rounding. Typed worker failures preserve the owning structured repair.
Empty unscoped inputs still validate independent endpoints: exact empty count
returns no partitions, while undefined sum/mean endpoints fail. Empty scoped
inputs create no synthetic scope. The graph-lifetime regression uses actual
Metric component state and verifies that its allocation is released while new
Delta side-state allocations remain live.

The broad Runtime regression loop also preserves prior slices: mixed logical
and engine-retained Entity Metrics bind their exact immutable component parts
through the existing retained compiler; Delta row continuations dispatch to
Delta state validation; and keyless scalar engine results do not request an
empty sort. All 20 prior comparison topologies and the affected engine
fold/failure and Population adapter journeys were rechecked before the final
frozen gate. Earlier incomplete or failed attempts remain excluded from
acceptance.

Retained component demand follows the exact consumer path: Metric projection
reads its selected state, observation consumes Population identity without
numeric components, and Compare, folds and Attribution require their complete
operand state. The source compiler uses those same selected immutable parts.
An engine identity-only regression deletes the unused component backing before
reuse and verifies that no part-schema read occurs. The six prior-slice Runtime
regressions exposed by overbroad demand and both mixed Entity comparison paths
passed before the final candidate was frozen.

During implementation, the independent workflow task committed
`9a3ee671` on top of the clean starting commit. Its daily-versus-Runtime Makefile
routing and repository guidance remain untouched by this slice. Final gate
manifests include the resulting current configuration.

## Gate and deferrals

Run focused tests and explicit touched source/test typing and lint, then
`make check-agent` and an explicit full Runtime invocation with versioned MinIO
and no S3 skips. A concurrent repository workflow change removed Runtime tests
from the default broad check and folded compact test/typecheck flags into
`make test` and `make typecheck`. Those unrelated changes are preserved; the
owner-approved executing-slice gate still requires its full Runtime evidence.
Retain full logs, unchanged source manifests and fresh Runtime
records under `evidence/slice-5b/`. Only this complete gate can accept 5b.

## Supplied review disposition

The owner supplied a review during the frozen gate on 2026-09-09. The running
Runtime attempt was stopped, its owned process group was verified empty, and
`gate-20260908T163608Z/interrupted.json` excludes it from acceptance. Its passing
default check does not substitute for a complete final gate.

The subsequent unchanged-candidate parallel Runtime attempt encountered host
contention: one existing cold-reader test exceeded its 30-second subprocess
deadline while diagnostics still showed Python imports at 20 seconds. The same
Artifact took 40.4 seconds under that load and 8.0 seconds after stopping the
owned gate process group, with identical rows and zero source queries. Both
affected original tests then passed sequentially without code or deadline
changes. `gate-20260908T164553Z/interrupted.json` excludes that attempt. The
replacement complete test invocations use two xdist workers: the next
default-suite attempt also exceeded an existing process-join deadline under
eight-worker load (`gate-20260908T165857Z`, 6043 passed and one failed), with the
same candidate unchanged. All default and Runtime cases, assertions and
deadlines remain selected, and repository configuration is unchanged. The
diagnostic record distinguishes captured full retest logs
from the explicitly labeled earlier failure excerpt.

- Remove the two unreachable/redundant branches and correct the Entity
  Delta/Attribution row-operation diagnostic.
- Share only the Core boolean-tuple arity parser and exact-value normalizer.
  Each boundary retains its own container admission, physical checks and typed
  errors. Cache contract-invariant masks and axis names once per row validator
  or publication scan, retaining all per-row validation.
- Preserve the specified Top-K scores: `abs(C_i) + abs(B_i)` for additive and
  `abs(W_i,current) + abs(W_i,baseline)` for component mix. These are explicitly
  fixed by the owning design and differ intentionally from contribution rank.
  Add independent source/local one-sided Top-K regressions: exact-empty-zero
  count succeeds; undefined null-empty sum is rejected before mapping.
- Preserve signed additive component bases. The contract permits signed basis
  and requires defined overall endpoints and consistent zero-basis state;
  it does not impose nonnegative weights. Add independent signed-basis
  numerical/source parity evidence instead of adding a new restriction.
- Compare requires matching sampling definitions before deriving Delta
  approximation. Add both asymmetric sampling rejection directions; choosing
  the current side after equality admission does not label a sampled baseline
  exact.
- Preserve the distinct semi-additive checks: retained Delta validates a legal
  coverage interval, while spatial Attribution folding requires matching,
  complete evaluation-end coverage. The stricter latter condition matches the
  existing non-time Observation fold contract and is not validation drift.
- Keep the Top-K selection bound and Finding emission bound independent. Keep
  original-schema and expanded-schema axis lookup separate. Their matching
  literals/expressions do not establish a shared semantic owner.
- Keep the private complete Attribution proof relation needed by source-side
  summaries after row continuations. Temporary validation-object identity maps
  remain confined to one live compilation/execution and are never persisted.
  Generated source-summary field names belong to the closed Attribution row
  schema and are covered by real source validation/parity; no silent rename
  path or incorrect proof result was established by these structural concerns.

No new public export, Help target, facade dispatch, migration, dual decoder or
current user-documentation claim is added. Distinct membership belongs to 5c,
distribution Shapley to 5d, Event attribution to 7c, public activation to 8 and
public Agent acceptance to 9. Parent Slice 5 remains open.

## Scoped commit checkpoint (2026-09-09)

The owner requested a scoped commit after supplying the revised repository
workflow. That workflow reserves full Runtime acceptance for release work;
ordinary commits reuse relevant validation and do not start or regenerate a
full Runtime gate. The already-running daily check was allowed to finish, and
the harness was stopped before its scheduled full Runtime phase. No complete
5b technical gate is claimed by this commit.

The unchanged implementation candidate has 787 source/test/configuration files,
SHA-256 `8f8d2934466942e0243e4521c5f41ce94eb7d0ff3d245a4345992eeb3060ac6a`.
Its 6044 default tests passed both before the host-contention investigation and
in the final two-worker daily run. All 61 changed Python files passed explicit
mypy; the final required Ruff fix/format pass left all 61 unchanged. Lint,
import-boundary checks and API-documentation checks remain part of the daily
check. The implementation and supplied review follow-up have the focused
operator/compiler/publication/guard/runtime results recorded above, including
three-storage, three-process Attribution journeys from the earlier candidate.
Those earlier Runtime records do not substitute for a completed full gate on
the final candidate.

Daily logs and the supplied-review contention diagnostics are retained locally
under `evidence/slice-5b/gate-20260908T170959Z/`; generated logs and temporary
runtime state are excluded from the commit. The scoped commit contains only
the implementation, regression tests, owning design amendments and this record.
The owned MinIO container was removed after validation. With no other containers
running, Colima was restored to its initially stopped state; the Docker context
remained `default`.
