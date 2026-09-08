# Slice 4b: engine and object Artifact adapters

Status: Slice 4b implemented and accepted on 2026-09-08; parent Slice 4 remains open.

## Prerequisites and authority

Entry HEAD is `73e085da`, the committed and accepted Slice 4a implementation.
The entry working tree is clean. The planning baseline passed 68 codec, storage
and placement tests. The accepted materialization-runtime and planner designs
own receipts, placement and publication; the public cutover plan owns scope.

## Frozen implementation scope

- DuckDB 1.5.3 / Ibis 12.0.0 is the first engine adapter. It writes independent
  version-addressed database files from the original source connection, without
  Arrow collection or local-output upload. Each selected payload has its own
  exact immutable file version and relation. Reads validate that version and
  attach read-only. A shared producer realization supplies primary and parts.
- Versioned S3-compatible Parquet is the first object adapter. boto3 accesses
  exact version IDs; PyArrow decodes bounded selected payloads. A dedicated
  versioned MinIO bucket provides real service acceptance. Record the tested
  server/client versions with final evidence. Credentials are current private
  access bindings, never stored metadata or diagnostics.
- Closed local/engine/object receipts and one typed Runtime target extend the
  existing v3 publication protocol. Local remains the default. Binding hits
  bypass configuration and preserve the existing Artifact. Engine/object policy
  has an explicit total storage budget; fixture policy is 128 MiB. Transfer uses
  8 MiB batches; collection and local methods retain Slice 4a limits.
- Population engine checkpoint -> same-domain observe is the minimal identity
  continuation. Full Metric row/part selection and folds remain Slice 3b.

## Files and seams owned

Implementation ownership under `marivo/analysis/`: materialization contracts,
storage, new target/engine/object/reader modules, admission, publication, recovery,
resources, reconciliation and Store; compiler placement, nodes, normalization
and lowering; only the Observation membership admission needed for the minimal
Population checkpoint. The private read-worker handoff carries exact access
bindings ephemerally. The public Session and exports do not change.

Tests own focused receipt/adapter/target/checkpoint/failure and fresh-process
runtime modules, with shared builders only when genuinely reused. Development
dependencies supply boto3 and typing. No public package extra or Help switch.

## Implementation and acceptance order

1. Extend closed receipt codecs, configured target/access values and ownership.
2. Add the engine writer/reader and immutable scan-leaf compiler seam.
3. Add versioned object writes, selected PyArrow reads and exact cleanup.
4. Integrate all output/part paths with v3 atomic publication and cold recovery.
5. Test round trips, mutation, selected-part isolation, budgets, configuration,
   foreign-domain rejection, reservation/finalization/publication failure and
   harmless cleanup; preserve unresolved termination as recovery-blocked.
6. Run real engine and object journeys in fresh interpreters, then scoped typing
   and `make check-agent`. Bind final evidence to the exact candidate manifest.

Each external create/upload is reserved first. Finalization validates every
required receipt; only metadata publication transfers ownership. Failure never
selects another sink, guesses cleanup or publishes a partial bundle. The full
contention/external-fencing matrix remains Slice 4c.

## Completion

Record actual SQL, object version requests, exact values, Run/Artifact/Evidence
counts and journal outcomes. Cold binding recovery adds no Run, query, storage
copy or worker. Real S3 service acceptance is mandatory; mocks cannot close 4b.
Update the cutover matrix and acceptance record only after all gates pass.
No public cutover, commit, push or release is authorized by this unit.

## Tested implementation boundaries

The identity continuation is an unsampled, non-versioned Population checkpoint
feeding same-domain `observe`. Native engine `where`, `metric`, `rank`, and
`limit` reuse already registered primary-only Metric row methods. Both writers
retain existing contribution/denominator and sampling parts independently;
transforming those parts remains Slice 3b. Engine file sizes are measured after
each detached native write and checked against the combined cap before
publication. This is a publication/storage budget, not an operating-system disk
quota. Native execution retains its existing memory, temporary-space and deadline
controls. Object bytes include all data payloads and their manifests, with
complete version and content verification before publication.

| Bound | Frozen value |
|---|---|
| Local output bytes | 67,108,864 |
| Engine/object combined stored bytes | 134,217,728 |
| Arrow batch / object range request bytes | 8,388,608 |
| Parquet row group rows | 1,024 |
| Complete collection/local input rows | 100,000 |
| Complete collection/local input/output bytes | 67,108,864 |
| Local intermediate bytes / worker RSS bytes | 268,435,456 / 536,870,912 |
| Collection/local action deadline | 60 seconds |
| Object manifest bytes | 65,536 maximum |
| S3 connect/read timeout / total attempts | 5 seconds / 10 seconds / 1 |

The service is an isolated Docker MinIO container named
`marivo-slice4b-minio`, labeled `marivo.slice=4b`, with no mounted data volume.
The run uses loopback port 32768 and a separate versioned bucket per test.
Fixtures remove only their bucket's exact versions. The server image is
`minio/minio:RELEASE.2025-09-07T16-13-09Z`, digest
`sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
Clients are boto3/botocore 1.43.89 and mypy-boto3-s3 1.43.66; the native pair is
DuckDB 1.5.3 / Ibis 12.0.0, with PyArrow 25.0.1. The Docker daemon was started
through the existing Colima instance for this acceptance run.

| Acceptance row | Independent evidence owner |
|---|---|
| Closed codec, unknown protocols, exact counts and metadata-only recovery | `tests/test_lazy_artifact_receipts.py` |
| Native producer, object round trip, no membership origin replay | `tests/test_lazy_artifact_adapters.py` |
| Independent primary/part reads, sampling state, exact combined budget and large stored input | `tests/test_lazy_adapter_parts.py` |
| Reserved writes, rollback, lost commit acknowledgement, mutation, exact-key collision and credential redaction | `tests/test_lazy_adapter_failures.py` |
| Foreign-domain/local/object identity rejection and pandas-to-engine rejection | `tests/test_lazy_adapter_failures.py` |
| Unknown request termination blocks only its Session; harmless terminal garbage remains journaled | `tests/test_lazy_adapter_failures.py` |
| Fresh engine/object production, continuation and cold binding, real SQL and version-pinned GET logs | `tests/test_lazy_adapter_runtime_acceptance.py` and `tests/lazy_adapter_runtime_worker.py` |

Failure-path testing found and repaired two integration issues: complete object
input row limits now reject before spawning a local worker, and read/access
errors retain their safe error stage while the Run terminal records the owning
execution phase from the closed v3 protocol. A storage target and its access
bindings are captured once per action, so callbacks cannot switch its writer.

## Reproduction

Install the repository development dependencies in `.venv`, start an isolated
instance of the pinned MinIO image, mapping container port 9000 to host loopback
port 32768 (`127.0.0.1:32768:9000`). The
fixture uses disposable `minioadmin` test credentials and creates a new versioned
bucket for every test. Do not select a production endpoint. For this run:

```sh
MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:32768 \
MARIVO_SLICE4B_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence" \
make check-agent
```

Without an explicitly selected endpoint, S3 tests skip and cannot close this
acceptance gate. Runtime tests always write evidence into their own temporary
directory; `MARIVO_SLICE4B_EVIDENCE_DIR` explicitly opts into retaining a local
copy. Evidence records use fixture identities and safe version-pinned request
facts; they contain no access bindings or credentials.

## Initial acceptance: 2026-09-08

The review follow-up gate below supersedes this initial candidate evidence.

- Final `make check-agent` passes: lint and import contracts, typing for 365
  modules, **6,251 tests in 169.77 seconds**, and API documentation construction.
  The real MinIO endpoint was present throughout; no S3 acceptance was skipped.
- Scoped typing passes for the 29 compiler/materialization/observation modules
  and eight adapter/receipt/storage test modules. Repository Python formatting
  and `git diff --check` pass.
- The two runtime journeys each use three distinct fresh interpreters. Engine
  production uses native SQL with no data Arrow upload; its Population checkpoint
  continues after dropping the original membership table. Revenue values are
  40, 100, 7 and null for customer identities 1 through 4. Object production and
  continuation use exact-version GETs; the original source is moved offline
  before the bounded local continuation returns revenues 100 and 30.
- Each Session finishes with exactly two terminal Runs, two Artifacts, two
  Evidence envelopes, one Artifact-input edge and zero resource obligations.
  The third process recovers the continuation's exact Artifact with no new Run,
  object request, storage copy, worker, profile resolution or source query.

The final 697-file ordered-content candidate SHA-256 is
`3f7935bf21d6ab8420de43964a2d8cbb33e1552f348e8b7cf76429b777e56012`. The digest covers sorted
library/test Python paths and `pyproject.toml`, `Makefile`, `.importlinter` as
UTF-8 path, NUL, file bytes, NUL; documentation and generated files are excluded.
Both runtime records have identical before/after manifests matching this value.

- Engine Session `session_610995ef727e4e35b7dd33847f396913`; continuation Artifact
  `artifact_1c2fd1b8e49d4357b6cd2c4a7a2949dd`; process IDs 62489, 62698, 63009.
- Object Session `session_7e59d82ef49b433ab186576cb1e2af69`; continuation Artifact
  `artifact_d1420c5914454caeb0d02c4094c8969c`; process IDs 63043, 63150, 63313.

Local raw evidence (ignored, retained explicitly) is available at
[evidence/slice-4b-engine-runtime.json](evidence/slice-4b-engine-runtime.json),
[evidence/slice-4b-object-runtime.json](evidence/slice-4b-object-runtime.json), and
[evidence/slice-4b-check.log](evidence/slice-4b-check.log).
These are actual library Runtime/service journeys, not real-Agent acceptance.

Slice 4b closes only the two adapters and the minimum identity checkpoint.
Slice 3b retains full row/part transformations and folds; Slice 4c retains the
full concurrency and cold coordination matrix; Slice 4d and the parent Slice 4
remain open. Public exports, Help, Session facade and the public persistence
generation remain unchanged. No commit, push or release was performed.

After the final gate, the owned MinIO container was removed. No other running
Docker containers were present, and Colima was stopped to restore its entry
state. The pinned image remains cached; acceptance evidence remains on disk.


## Adversarial review decisions: 2026-09-08

The review is against the entire uncommitted Slice 4b candidate over `73e085da`.
The existing owning contracts, actual constructor/lowering behavior and focused
real-backend probes determine the decisions below; code-smell labels alone do
not justify expanding the slice.

| Report item | Decision and evidence |
|---|---|
| Standards 1: `S3Access` repr | Rejected as already disproved: `repr=False` hides fields. The report's separate claim that credentials never enter worker IPC is incorrect: object terminal reads receive the selected current access binding through private stdin IPC. The binding is ephemeral and excluded from receipts, Store, logs and errors. No credential proxy is introduced. |
| Standards 2: dev-only boto3 | Intentional private adapter scope, already specified in this execution plan and the authorized user plan. Install the repository `dev` dependencies to exercise private object storage. Missing boto3 fails during storage selection before source work. Public installation extras and facade disclosure remain Slice 8; no new runtime extra is added here. |
| Standards 3: long admission method | Deferred. The method explicitly coordinates reservation, one producer, publication and failure readback. No observable defect was established from length or repeated lazy imports; a wholesale extraction would enlarge this review fix. |
| Standards 4: capability literals | Partially adopted: the S3 request capability has one owner alongside request proof lifecycle. Closed persisted resource-kind discriminators remain literal variants; no extensible dispatch registry or speculative adapter hierarchy is introduced. |
| Standards 5: unbounded request proofs | Adopted. Store publication, failure and discharge remove exact S3 proofs only after durable journal deletion. Rollback retains them. Real service tests cover success, rollback/readback and lost commit acknowledgement, including proof presence before commit and absence after terminal publication. The separate pre-existing local-process proof mechanism belongs to the earlier foundation and is unchanged. |
| Standards 6: deferred cleanup / error sanitization | Rejected as a fallback defect. Incomplete version listing preserves the exact cleanup obligation; it neither changes storage nor claims cleanup success. This is the specified harmless-terminal-garbage policy. Re-raising outside the SDK exception handler deliberately strips raw credential-bearing exception chains; the generator reader uses the same security boundary for streaming errors. No generic error-wrapper abstraction is added. |
| Standards 7: repeated prefix / limits / schema digest | Partially adopted: the object Artifact prefix now has one pure ownership helper shared by Store and adapter. Limits at admission, transfer and complete collection guard different allocation boundaries and remain independent. Small schema-hash expressions do not justify another abstraction. |
| Standards 8: reader dependency cycle | Adopted. Storage owns bounded worker IPC and accepts an explicit reader callable; the external reader selects its own process entry. Storage no longer imports external reads, and external request JSON is decoded once in the worker. The real object worker's no-DuckDB guard now targets this explicit entry. |
| Standards 9: ownership predicate can raise | Retained and documented. Malformed persisted locators are integrity errors, not a negative ownership match. Converting them to `False` could allow corrupted cleanup metadata to escape validation. |
| Standards 10: three-field native write tuple | Deferred. It is one typed, private handoff of backend, binding and compiled recipe, destructured at a single writer boundary; no behavioral ambiguity was demonstrated. |
| Standards 11: default engine versions | Adopted. `EngineBinding.adapter_versions` is required and supplied by Runtime from the actual interpreter; there is no duplicate default version literal. Placement still checks the registered tested pair. |
| Standards 12: cross-module `_PHASES` | Adopted. The contracts owner exposes the private `run_failure_phase` projection; admission no longer imports its internal inventory. |
| Spec a1: native `.metric()` missing | Defect claim rejected; coverage request adopted. Construction creates the exact selected output schema and lowering explicitly selects it. A real two-metric engine Artifact is projected independently to each metric after its original database is moved offline, with exact values, dropped-column assertions and no source resolution, pandas worker or Arrow data transfer. No duplicate payload-driven projection is needed. |
| Spec a2: captured parameter changes | Clarified and tested. A datasource/adapter domain is distinct from an immutable definition's non-secret source parameters. The materialization design's "Parameterized source values are definition-bound" section forbids recapturing old origins or using raw historical values as reuse certificates. A real JSON-backed engine checkpoint retains alpha membership while a new observation captures beta and makes exactly one beta request; old membership is not replayed. Changed datasource declarations remain rejected by the existing foreign-domain tests. |
| Spec a3: writer library versions absent from receipt | Rejected. The owning common receipt contract explicitly says writer library fingerprints are diagnostics rather than compatibility/reuse gates. "Version" in this adapter's immutable receipt means the content-addressed backing. Current tested adapter eligibility and exact backing validation remain separate checks. No historical DuckDB/Ibis fingerprint is added to the receipt. |
| Spec b1: entry baseline wording | Clarified. Entry HEAD was committed Slice 4a `73e085da` and the entry tree was clean. It already included the work that had been uncommitted at planning time; it was not an uncommitted implementation baseline. |
| Spec b2: local `use_threads=False` | Adopted. Local Parquet decoding keeps PyArrow's prior threaded behavior. Only the custom S3 range stream selects `use_threads=False`. |
| Spec b3: service ports | Clarified as container port 9000 mapped to host loopback port 32768; the values describe the two ends of one mapping. |

The private API and stored receipt generation do not grow for these review fixes.
Full row/part transformations, remote coordination, public cutover and release
remain owned by their later slices.


## Review follow-up gate: 2026-09-08

- `make check-agent` passes: lint, import contracts, typing for 366 modules,
  **6,256 tests in 230.57 seconds**, and API documentation construction. The
  real MinIO endpoint was enabled throughout this gate.
- Focused adapter/worker/storage tests: 91 passed. Scoped implementation typing
  checks 30 modules; all three changed runtime test modules pass typing.
  Python formatting and `git diff --check` pass.
- Real engine and object journeys again use three fresh interpreters each.
  Both finish with two terminal Runs, two Artifacts/Evidence envelopes, one
  input edge and zero resource obligations. Cold binding recovery adds no
  object request, source query, Run, storage copy or worker.

Final candidate: **698 files**, SHA-256
`b3770bb32139cc17a38c61fe7d4d06ecc6ac9ad5158cc291c777919947c8c763`. Both before/after runtime manifests
match this candidate under the same ordered-content protocol as the initial
acceptance. The initial evidence above is historical.

- Engine Session `session_6fdeca102e0443788dd3113a4d921ec1`, continuation
  `artifact_b06498b4567a4d19ba5dda4467976d5b`; process IDs 6848, 7102, 7412.
- Object Session `session_61c3dc62d8334bfca89bdd8be2af1d94`, continuation
  `artifact_15a43b828b9a4313a696cacbf418cb9e`; process IDs 7459, 7612, 7817.

Final raw evidence:
[engine runtime](evidence/slice-4b-review/slice-4b-engine-runtime.json),
[object runtime](evidence/slice-4b-review/slice-4b-object-runtime.json),
[complete gate log](evidence/slice-4b-review-check.log).
The evidence is explicitly retained local ignored output. No commit, push,
release or public cutover was performed. Parent Slice 4 remains open.

The review's isolated MinIO container was removed after this gate. With no
other running containers present, Colima was stopped again; raw evidence is
retained and the entry service state is restored.
