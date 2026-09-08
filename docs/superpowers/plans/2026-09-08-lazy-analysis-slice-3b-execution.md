# Slice 3b: retained-state folds and checkpoint continuations

Status: Slice 3b implemented and accepted on 2026-09-08; parent Slice 3 is complete.

## Parent milestone and prerequisite units

Slice 3b completes the retained-input half of Slice 3. Slices 3a, 4a and 4b are
accepted. Planning verified the 698-file Slice 4b candidate digest
`b3770bb32139cc17a38c61fe7d4d06ecc6ac9ad5158cc291c777919947c8c763` and 64 focused
baseline tests. Implementation begins at committed Slice 4b `431492b2`; the
working tree is clean. Existing changes made outside this unit are preserved.

## User-visible or runtime outcome

Private Metric row operations select primary rows and exact contribution parts
together. Materialized Entity aggregation and logical/materialized rollup use
registered exact folds. Shared engine checkpoints supply resolved membership to
independent observations without replaying their origins or resampling.

## Frozen contract owners consumed

The accepted Dataset Core, Observation Model, Typed Operators, Planner and
Pushdown, Materialization Runtime and public-cutover designs own all behavior.
Observation owns contribution and time meaning; operators own algorithms;
compiler owns placement/lowering; Runtime owns receipt access and publication.

## Exact method, backend, storage, and fixture scope

The existing DuckDB 1.5.3 / Ibis 12.0.0 source adapter, pandas 2.3.3 local methods
and PyArrow 25.0.1 storage reads are used. Existing 4a/4b guards remain unchanged.
Storage is local Parquet, immutable DuckDB engine and versioned S3-compatible
Parquet through the existing adapters. Final shared-adapter regression enables
the isolated pinned MinIO service used by Slice 4b.

Methods are part-aware `where`, `metric`, `rank`, `limit`, retained `aggregate`,
and `rollup(drop_dimensions=..., grain=..., drop_time=...)`. Logical aggregate
keeps governed source recomputation; rollup always folds current inputs. Exact
sum/count/linear/extrema/component-state and admitted temporal folds register
Ibis and pandas implementations. Unproved distributions, allocations, alignment
or noncommuting reductions fail before execution.

## Exact files owned

- Observation: `contracts.py`, `aggregation.py`, `metric.py`,
  new `fold_contracts.py` and `rollup.py`; the family-semantics encode/decode
  sections of `materialization/contracts.py`.
- Compiler: `normalize.py`, `lowering.py`, `placement.py`.
- Operators: `registry.py`, `row.py`, new `rollup.py`.
- Runtime: `materialization/admission.py`, `publication.py`, `local.py`,
  `local_worker.py`, `reads.py`, and new `retained.py`.
- Tests: focused retained-fold, parts, checkpoint, codec and placement tests;
  new Slice 3b runtime worker/fixtures and acceptance tests. Shared fixtures use
  existing production constructors and isolated `tmp_path` stores.
- Documentation: this execution record and the Slice 3b/parent Slice 3 acceptance
  sections of the public-cutover plan; the `.gitignore` exception for this record.

## Shared seams changed

Metric row semantics carry bounded closed fold authority and time coverage for
cold construction, without an executable source graph. Existing keyed retained
parts remain the storage contract. The worker transports primary plus named
parts once and passes private DataFrames directly between local steps. Engine
scan leaves expose selected immutable relations to exact native methods.

## Public additions

None. New Dataset methods and contracts remain private.

## Public removals

None. The public eager surface remains unchanged until Slice 8.

## Persistence changes

Extend the private Metric family descriptor and its exact codec; no alternate
decoder, migration or public Store-generation switch. Retain existing Artifact
receipts, required-part roles, sampling receipt and atomic publication protocol.

## Implementation order

1. Register cold fold authority and pure paired constructors.
2. Implement exact row/part transformations and native/local folds.
3. Integrate selected-part reads, budgets, output validation and publication.
4. Extend resolved engine membership and shared checkpoint authority.
5. Run independent numeric, negative, failure and fresh-process journeys.
6. Bind the broad final gate and runtime evidence to one candidate manifest.

## Focused positive tests

Selected ratio A=8/10 versus B=1/10 remains 0.8; customer-day selection never
restores other days. Mean/weighted/ratio folds match source references. Combined
rollup equals time then Dimension calls, including partial periods, time removal,
empty scalar and repeated checkpoints. Sampling receipts remain unchanged.

## Adjacent negative tests

Reject unsafe device-peak sums, overlapping tags, unaligned endpoints/coverage,
unsupported distinct/distribution folds and cumulative Entity reduction. Reject
wrong-state shapes, invalid grains, true Entity-by-time identity projection,
foreign Session/domain and local/object membership import.

## Failure injections

Required-part absence/corruption/role/schema/key/count mismatches fail closed.
Combined input, conversion, intermediate/output/RSS and deadline budgets include
parts. Publication failures expose no partial Artifact/Evidence and leave the
input checkpoint unchanged; proven-terminal resources are cleaned. Lost commit
acknowledgement recovers the same committed result.

## Real runtime journey

Parquet and engine journeys use distinct producer, continuation and cold-binding
processes. Parquet continues after moving source storage offline. Engine deletes
membership origin and feeds the exact checkpoint to two downstream definitions,
including scoped, sampled and resolved versioned membership. January membership,
February observation and omitted observation scope remain independent.

## Capability-to-acceptance row and evidence locations

This unit owns the matrix row "Retained parts, aggregate/fold/rollup, checkpoint
membership" and the combined Slice 3 gate. Tests write evidence under temporary
directories. An explicit evidence environment variable may retain candidate
manifests, SQL, worker handoffs, receipts, Store counts and no-origin counters
under ignored `docs/superpowers/plans/evidence/`.

## Disclosure updates

Private `.contract()` derives admissible folds from registered row and retained
authority identically after cold recovery. Public Help, exports, site docs,
skills and Session facade are unchanged.

## Explicitly deferred contracts

Slice 4c owns full concurrency/external-fencing; Slice 4d owns its remaining
runtime boundary matrix. Slices 5-7 own later analytical/domain families. Slice
8 owns public/persistence cutover; Slice 9 owns real-Agent acceptance. This unit
does not commit, push or release.

## Exit gate

Focused tests, no-I/O construction, touched-module typing and `make check-agent`
pass with real adapter regression enabled. Fresh runtime records use matching
before/after candidate manifests and show exact outputs, required parts,
input edges and no-origin/cold-binding behavior. Only then mark Slice 3b and
the parent Slice 3 accepted.

## Implemented boundaries and acceptance owners

Cold Metric authority is a closed, versioned component graph with independent
Entity/Dimension/time fold proofs. The codec validates the complete canonical
payload within its existing bounded envelope; it has no compatibility decoder.
Required state is keyed by the complete Entity/Dimension/time contribution key.
Generated rank fields do not join that key. Projection retains the selected
Metric dependency closure, including any intermediate fold dependencies.

Cumulative time folds preserve the selected evaluation endpoint and observed
coverage. A cumulative Dimension fold requires aligned endpoints and a provably
contiguous observed interval, or the canonical all-empty state. Clipped intervals
remain valid without claiming complete calendar coverage. Different internal
gaps are not treated as equivalent from equal summary fields. Empty final-scalar
folds retain registered empty/null values and zero support; cumulative endpoints
remain null instead of inventing an observation time.

The exact source prefix can feed one wide stream into one bounded worker. Primary,
component and sampling inputs share the existing complete-input budget. Every
local successor receives the preceding private DataFrames directly. Required
part receipt, schema, contribution keys, support, coverage and primary-value
reconciliation precede consumption/publication. Unselected parts do not prevent
an otherwise valid projection or primary-only read.

| Acceptance row | Independent tests and runtime evidence |
| --- | --- |
| Closed fold authority, exact per-axis admission, grain/interval rules and cold construction | `test_lazy_observation_contracts.py`, `test_lazy_materialization_codec.py`, `test_lazy_observation_runtime_no_io.py` |
| Native current-row folds, part reconciliation and normalized time-then-Dimension calls | `test_lazy_retained_compiler.py`, `test_lazy_source_algebra.py` |
| Independent numerical references, selected 80% ratio, customer-day selection, component folds, temporal coverage and unsafe folds | `test_lazy_retained_fold_matrix.py`, `test_lazy_local_fold.py` |
| Source prefix, local suffix, rank/limit/projection, missing required versus unused parts, repeated checkpoint | `test_lazy_retained_runtime.py`, `test_lazy_local_placement.py` |
| January/February/omitted observation scope, sampled and snapshot/validity-selected engine membership, selected Metric authority, forbidden identity topologies | `test_lazy_retained_membership.py`, `test_lazy_retained_runtime.py` |
| Complete-input/part budgets, deadline/RSS/output guards and atomic publication failures | `test_lazy_local_guards.py`, `test_lazy_local_fold.py`, `test_lazy_retained_failures.py` |
| Three-process Parquet and engine checkpoints, two exact input edges and cold binding with no origin replay | `test_lazy_retained_runtime_acceptance.py`, `lazy_retained_runtime_worker.py` |
| Real versioned object fold and shared immutable engine/object adapters | `test_lazy_retained_failures.py`, `test_lazy_adapter_parts.py`, `test_lazy_adapter_failures.py`, `test_lazy_adapter_runtime_acceptance.py` |

Test paths in this table are relative to `tests/`. The real-service regression
uses only the isolated `marivo-slice3b-minio` container (label `marivo.slice=3b`),
loopback port 32768, no host bind mounts, and separate versioned test buckets.
The image creates an anonymous `/data` volume, which is removed with the owned
container after verification.
The pinned image is `minio/minio:RELEASE.2025-09-07T16-13-09Z`, digest
`sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
boto3/botocore are 1.43.89 and mypy-boto3-s3 is 1.43.66.

## Final gate and immutable evidence

`MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:32768 make check-agent` passes lint,
import contracts, typing for **370 source files**, **6,352 tests in 437.82
seconds**, and API documentation construction. The full gate includes the
23-case Runtime fold matrix, construction no-I/O checks, publication/part and
budget failures, and real MinIO shared-adapter regressions. No S3 test is skipped.
Scoped implementation typing and independent numerical tests passed before the
full gate. Final formatting and whitespace checks pass.

The final candidate contains 711 Python source/test and configuration files,
using the existing ordered path-plus-content manifest definition from the 4b
acceptance harness. The before/after SHA-256 is
`170ed26d8f829192c968509f0c069608130c68915dc54c14decee64ad4e97774`.
All four Runtime evidence files match it, and the gate record binds their hashes
and the final log. Acceptance documentation is outside that code manifest.

| Final evidence | Recorded result |
| --- | --- |
| [Gate record](evidence/slice-3b-gate.json) and [complete check log](evidence/slice-3b-check.log) | Same candidate before/after; full gate passed with real S3 enabled |
| [Parquet three-process journey](evidence/slice-3b-local-runtime.json) | Producer/continuation/cold PIDs 73627/74116/75022; source moved offline; two exact checkpoint continuations; retained results `[140, 140/3, 50, 140/3]` and mean `100` |
| [Engine three-process journey](evidence/slice-3b-engine-runtime.json) | Producer/continuation/cold PIDs 75172/75209/75262; sampled membership origin removed; two independent source observations consume the same engine Artifact |
| [Persisted Store audit](evidence/slice-3b-store-audit.json) | Each 3b journey has three succeeded Runs/Artifacts/Evidence, two exact input edges to the producer Artifact, and no remaining resources |
| [Shared engine adapter](evidence/slice-3b-adapters/slice-4b-engine-runtime.json) | Independent immutable receipt continuation and cold binding pass on this candidate |
| [Shared object adapter](evidence/slice-3b-adapters/slice-4b-object-runtime.json) | Real version-pinned S3 journey and cold binding pass on this candidate |
| [Service cleanup](evidence/slice-3b-service-cleanup.json) | Owned container and anonymous data volume removed; Colima returned to its initial stopped state; unrelated stopped containers preserved |

Cold `execute()` statistics contain no source statement, validation query,
transfer, copy or worker. Store counts and output identities remain unchanged.
The worker also asserts exact input Artifact references; the separate read-only
Store audit retains the actual producing Runs and input edges. Runtime evidence
includes part receipts, SQL, local handoffs, process identities and resource
outcomes. Its SQL inventory explicitly counts Runtime logical statements, not
adapter-internal metadata wire calls.

The final audit corrected publication after consecutive logical observations:
the final unary chain now resolves its nearest owning Observation and preserves
the actual selected membership fingerprint. A real engine regression verifies
both the value and authority. Cumulative coverage and specialized Entity-key
distinct also have actual local/engine execution proofs, not only construction
tests.

Slice 3b and the combined Slice 3 gate are complete. The implementation remains
private and uncommitted; no push, release or public persistence switch occurred.

## Review follow-up: 2026-09-08

The review assessed the preceding candidate against the owning contracts and
actual Runtime paths. The bounded fixes, additional tests and refreshed final
gate are complete. This follow-up preserves independent membership
and observation authority and does not introduce a new approximation field or
allocation API.

| Review recommendation | Disposition and evidence |
| --- | --- |
| Linked execution record is ignored | Fixed: `.gitignore` explicitly includes this record, following 4a/4b. The record is visible to normal Git staging; no staging or commit is performed by this task. |
| Checkpoint and new observation must use the same version | Rejected as a behavior change. Observation Model's "Versioned membership resolution" explicitly fixes checkpoint members while consumers use their own observation anchors. Snapshot/validity Runtime tests keep January members `(1,2)` and independently read January `30`, February `311` after a fresh source update, and unscoped `3000`. New versioned Metric facts remain in `required_entities`; dropping their source correctly blocks a new action. |
| No constructive Entity-only uniqueness proof | No defect found. Functional coordinate paths produce `entity_unique` authority used at construction, and lowering validates realized uniqueness. A customer-day filter leaving just one actual row still fails logical and materialized membership admission because its shape lacks that proof. |
| Unequal mean coverage has no admitted positive proof | Added local/engine Runtime evidence for exact sufficient state: sum `130`, non-null count `3`, row count `4` fold to `130/3`; selecting the day with two observations gives `100/2`. Unproved temporal-mean/semi-additive cases continue to reject. |
| Conserving allocation lacks positive evidence | Added governed contribution facts `40+60=100`, with selected-tag value `40`, on local/engine checkpoints after source removal. These values have a disjoint contribution partition. Removed the unproducible `allocated` decoder/admission label and added codec rejection: a bare label is not an allocation proof. |
| Approximation annotation is lost | No loss found in the represented sampling contract. New sampled Population-to-Metric-to-Metric membership tests preserve the exact realization, sampling part, population authority and `show()` approximation disclosure through cold recovery. Core row-set remains cardinality plus ordering, as its owning contract requires. |
| Only tiny object retained inputs are exercised | Added real MinIO coverage for 20,000 Entity rows and two named retained parts (three fixed-version payloads). Source-offline `where` then `aggregate` uses the existing guarded worker and 100,000 combined-row limit, preserves the checkpoint, and supports binding reuse/cold reads. Implicit object-membership upload into a source remains rejected. |
| Potential raw IndexError from catalog identity parsing | Hardened with `partition`, so a missing separator follows the existing structured contribution-proof error. Current valid identities keep the same meaning. |
| Repeated keyed-part alignment and builtin widths | Consolidated the three identical alignment checks in `aligned_part_positions`; shared builtin seconds/month widths between admission and pandas bucketing. Required support/coverage checks remain with their existing owners. |
| Nested component type-dispatch expression | Replaced with a typed predicate table; unknown classes still fail closed. |
| Merge both backend fold interpreters/bucketing into one abstraction | Not adopted. Ibis builds deferred expressions and validation queries; pandas evaluates bounded concrete state. Both consume the same closed semantic authority. Separate backend implementation and independent numerical/calendar parity tests preserve that distinction without a new generic interpreter. |
| Split long functions, factor every registry-edit test prelude, remove Runtime hook | Not adopted as part of this fix. No concrete defect was shown; test-specific governed definitions stay explicit, and the existing failure hook predates 3b. |

Additional regression owners are `tests/test_lazy_retained_review_folds.py`
(six local/engine numerical cases), `tests/test_lazy_retained_review_runtime.py`
(sampling disclosure and real large object input), and three new cases in
`tests/test_lazy_retained_membership.py`. All eleven cases pass their focused
gates. The shared alignment/codec changes also pass 76 focused tests and scoped
typing/lint. A typed guard on version equality is deliberately absent: it would
reject the accepted January-membership/February-observation behavior.

Local ignored evidence from the refreshed gate is retained under
`docs/superpowers/plans/evidence/slice-3b-review/`; it is regenerated by running
the acceptance tests with `MARIVO_SLICE3B_EVIDENCE_DIR` set to that directory and
`MARIVO_SLICE4B_EVIDENCE_DIR` set to its `adapters/` child. The MinIO endpoint is
supplied through `MARIVO_TEST_S3_ENDPOINT`. The preceding evidence is preserved
as historical evidence for its original candidate.

The refreshed `make check-agent` passes lint/import contracts, typing for **370
source files**, **6,363 tests in 462.66 seconds**, and API documentation
construction, with real MinIO enabled and no S3 tests skipped. All **33 modified
Python files** pass formatting checks, and `git diff --check` passes.

The fresh **713-file** source/test/configuration candidate before and after the
gate is SHA-256
`34c8cb9458d0e83529092e6279639f68c3ac2a02302b246ef319416efeb6f78b`.
It supersedes the preceding 711-file candidate for the completed Slice 3b gate.
The local `slice-3b-review/gate.json` and `check.log` bind this result. Its four
runtime JSON files retain matching manifests, SQL, receipts and fresh process
identities: Parquet 14179/14344/15410; engine 15646/15969/17593; shared engine
adapter 14171/14349/14660; shared object adapter 15082/15496/15669.

The refreshed `store-audit.json` verifies each 3b journey still has three
succeeded Runs/Artifacts/Evidence, two exact input edges to the same checkpoint,
and no remaining resource obligations. Cold execution introduces no Run, query,
transfer, copy or worker. `object-input-audit.json` retains the 20,000-row
checkpoint's three fixed-version receipts and its one-row folded output;
the independent oracle selects 8,571 contributions totaling 51,426 with mean 6.
The recorded source is offline and the resource journal is empty.

The review container is separately named `marivo-slice3b-review-minio` and labeled
`marivo.slice=3b-review`; it uses the same pinned MinIO image, unchanged client
versions, isolated versioned buckets and existing resource limits. The local
`service-cleanup.json` records removal of its anonymous data volume and return
of Colima to its prior stopped state after the final gate. Unrelated containers
are preserved. No commit, push, release or public surface change was performed.
