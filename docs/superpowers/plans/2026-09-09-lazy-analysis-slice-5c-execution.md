# Slice 5c: private distinct-membership attribution

Status: private Slice 5c implementation and technical acceptance complete.

## Authorized outcome and entry state

The owner approved the execution plan and instructed implementation on
2026-09-09. The entry shared 5b baseline was commit `e7e2ca89`; the eight unrelated
staged documentation and fixture-performance files were committed concurrently
as `b4123bb7`. Their original index blobs match that commit exactly, and this
task leaves the index clean. The entry manifest and verification are recorded in
`evidence/slice-5c/entry-baseline.json` and `concurrent-baseline.json`.
The planning entry check passed 62 focused Attribution contract, numerical,
publication and mask tests; this is not Slice 5c acceptance.

Implement `distinct_membership@v1` through the existing Delta and Attribution
registrations. Consume the accepted Typed Operators, Observation Model,
Dataset Core, Planner and Materialization Runtime designs. No public export,
Help entry, eager facade dispatch, migration or second publication family is added.

## Frozen implementation choices

An explicitly admitted exact root count-distinct contract requires source-private
membership state at definition construction. Its Metric/Delta checkpoints use
the configured compatible engine target: primary and all parts share one sink
and publication transaction. Local/object targets cannot omit required state,
choose another sink, or upload inputs. Final identity-free Attribution may use
the existing admitted local/object writer. Unsupported distinct graphs remain
opaque and do not gain Attribution or generic fold authority.

Membership is a separately registered, coordinate-keyed source relation with
its own cardinality and immutable receipt. Raw keys never enter local Arrow or
pandas inputs, metadata, Findings, errors, logs, or public result columns.
Engine-native integrity checks return only schema facts and aggregate counts;
opaque storage-byte hashing is permitted. Required state is never reconstructed
from an Artifact origin.

Top-K scores count distinct memberships in the current/baseline union. Mapping
runs once in authored axis order within already mapped parents, including Other.
Each resolution independently deduplicates mapped key memberships, computes
side-specific degrees and allocates `1 / degree`. Independent unmapped scope
endpoints validate the allocation and every resolution reconciles. Distinct
Attribution has float64 allocations, independent resolutions and
`rollup_safe=False`; hierarchy parents are never sums of child allocations.

## Ownership and sequence

- Contract owner: exact membership authority, method/shape admission, source-only
  registrations and construction fixtures/tests.
- Source coordinator: governed contribution preparation, membership propagation
  through selections and comparisons, logical expansion, source recipe assembly,
  runtime journeys, owner documentation and candidate gate evidence.
- Runtime owner: independent retained source relations, engine writer and receipts,
  consumed-state admission, source-only readers and integrity inspection.
- Attribution owner: exact source allocation, existing proof/codec/publication
  extension and independent numerical/publication tests.

Freeze the shared part interfaces before integration. Preserve other workers'
edits and the preexisting index. Start with focused checks; integrate complete
source and retained paths before recording an unchanged acceptance candidate.

## Acceptance matrix and gate

Cover repeated/overlapping memberships, duplicate source rows, null and empty
keys, one-sided coordinates, union Top-K scoring, Other-induced degree changes,
independent hierarchy, scope and paired-time preservation, original selection
before expansion, shared sampling, and supported scalar/composite identity keys.
Independent fixture-set/Fraction references must reproduce both endpoints and
each allocation without calling the production algorithm.

Exercise all-logical, retained engine Metrics, ordered mixed operands and a
retained Delta after origin shutdown. Three fresh processes prove production,
continuation, cold reads and exact-key reuse without a new Run or source work.
Validate target/domain/local-frontier rejection, missing/corrupt/duplicate or
foreign-key parts, endpoint contradictions, raw-key transfer guards, deadline
and atomic rollback. Ordinary primary reads do not validate unused parts;
consuming operations and full integrity do.

Run touched typing/lint, focused default tests, relevant Runtime cases with no
skips in the selected acceptance matrix, and `make check-agent`. Preserve full
logs, source manifests and terminal Run/Artifact/Evidence records under
`evidence/slice-5c/`. Full release Runtime acceptance is not this slice's gate.
Only successful candidate-bound evidence closes 5c. Slice 5d, public activation
and public Agent acceptance remain later work. No commit, push or release is
authorized by this task.

## Implemented behavior

The definition captures one closed distinct authority from the normalized
computation graph. Scalar keys and complete governed Entity identities retain
their exact source type. Filters and cumulative evaluation remain attached to
the original contribution preparation; composite arithmetic and endpoint-changing
time folds do not gain membership authority.

The compiler carries independent membership relations through Metric selection,
projection, admitted folds, comparison side alignment and retained scans. A
cumulative time-last fold first selects membership at the original final
evaluation endpoint, then changes coordinates. Logical missing-axis expansion
uses the original selected comparison ordinals and includes prior contributing
partitions even when the selected day has no source row for that partition.
Explicit Population fanout uses the existing governed coordinate paths.

The engine writer freezes primary and independently sized membership relations
in the same source transaction. Native readers validate schema, key nullability,
coordinate/key uniqueness, primary support, exact endpoint counts and immutable
receipts. Generic local readers reject membership before allocating an iterator.
The existing Attribution publication path requires the source reconciliation
proof, publishes only allocations and preserves inherited proof for derived
where/rank/limit results.

Integration regressions also cover hierarchy output ordering: source recipes now
order the public stream by the declared row-key order, which the local writer
requires for deterministic Finding validation. The shared materialization test
builder requests only the retained registrations admitted by its exact row.

Independent review identified two additional failure boundaries. Cold membership
authority now rejects incompatible component arithmetic, retained state, empty/null
rules and merge modes. Consumed engine receipts are rechecked before publication
for both engine-only and mixed logical/materialized source bindings, including a
membership file changed after output rename. This keeps a staged output from
committing against mutated retained input authority.

## Validation environment

The object-output journey uses an isolated native MinIO process bound to
`127.0.0.1:19050`, with unique versioned fixture buckets and a task-specific data
directory. The official darwin-arm64 release is
`RELEASE.2025-09-07T16-13-09Z`; its downloaded SHA-256 was checked against the
official checksum (`7c3b3039b76e55a1b80935848ed83998d5e8d317374f87851f46a019ff5c0aa4`).
The focused Runtime gate uses this service only for identity-free Attribution
outputs; membership checkpoints use the engine adapter. No release gate runs.
The task-owned service was stopped after successful acceptance.

## Initial candidate and acceptance

The review-follow-up candidate below supersedes this initial acceptance record.

The unchanged candidate on branch `lazy-dataset`, base HEAD `b4123bb7`, contains
802 source, test and configuration files. Its ordered-content SHA-256 is
`78211d0264d1884329d4481d470af55bc3bee4bad05f3f1cf3170e453c826a08`.
`candidate-files.json` records every file hash; `final-gate.json` verifies the
before/after candidate and hashes the terminal logs and Runtime records. Both
files live under the project-local `evidence/slice-5c/` directory.

- `make check-agent`: passed, including lint/import contracts, 400 source modules
  typed, 6,226 default tests passed with two existing skips, and API docs built.
- Strict Python 3.10 typing for all 12 touched test/helper modules: passed.
- Focused Runtime gate with four workers: 54 passed, zero skips, 180.49 seconds.
  It includes all new distinct Runtime modules and six adjacent retained and
  Attribution modules. Full Runtime release acceptance was not run.
- All 29 terminal Runtime records contain matching before/after manifests: 58
  embedded manifests agree with the candidate. Eight records directly cover the
  distinct LL/LM/ML/MM operand orders, one shared sampled realization, and three
  separate-process local/engine/object publication and cold-reuse journeys.
  The remaining 21 records cover adjacent Attribution and retained behavior.
- `git diff --check` passes; the index is clean and the eight preexisting staged
  blobs remain intact in the concurrent baseline commit.

The first simultaneous daily/Runtime run recorded a subprocess timeout and a
source action failure (52 Runtime tests passed). Both failing cases passed in an
isolated 58.11-second rerun. The complete Runtime gate then passed after the daily
gate with four workers, without changing code or execution deadlines. Preserve
`runtime-final.log` and `runtime-failure-recheck.log` as diagnostics; the accepted
Runtime log is `runtime-final-4workers.log`.

Failure evidence includes missing/replaced physical Delta membership with the
origin offline, readable ordinary primary rows, blocked consumed-state reads,
foreign-contract and independent-source rejection, mutation of a consumed
membership after output rename, atomic part failure, and actual DuckDB deadline
interruption with redacted exception chains and persisted Run state. No failed
action publishes a partial Artifact or leaves owned resources.

This closes the private technical gate for Slice 5c only. Slice 5b's separate
acceptance status, Slice 5d, parent Slice 5, public activation, independent review
approval and release approval remain separate. No commit, push or release was
performed.

## Review follow-up dispositions

The owner requested assessment and adoption of the supplied review, and repair
of every failing verification test. The follow-up starts from `8384d5f0`, which
contains a separately committed Runtime performance change; those changes and
the existing uncommitted 5c implementation are preserved. Entry hashes and the
index snapshot are recorded in `evidence/slice-5c/review-followup/entry.json`.

### Standards axis

| Review item | Disposition and evidence |
| --- | --- |
| 1: unused `membership_state_columns` | Adopted. Removed the helper and assertions that mirrored it. Physical schema behavior remains covered through real retained readers and validators. |
| 2: multiple private-key constants | Adopted. Production consumers import the one `DISTINCT_KEY_COLUMN` value from the membership contract owner. Local import aliases do not create another literal definition. |
| 3: duplicate integrity algorithms | Partially adopted. The source compiler and native cold-reader checks retain independent execution implementations and privacy boundaries. Both now consume the same endpoint-name metadata. The former string operations were equivalent for valid roles; no numerical disagreement was established. |
| 4: repeated retained variants and contract IDs | Partially adopted. Runtime guards share the canonical membership contract IDs. The closed projection/independent-relation union remains: it encodes different cardinalities and writer behavior, and its type checks are required narrowing. |
| 5: cumulative anchor repetition | Adopted. Pure compiler temporal helpers own bucket boundaries and cumulative lower bounds. Standalone exclusive endpoints still reset to the immediately preceding period; displayed buckets reset from their bucket start. |
| 6: `distinct_fold` importing `_Compiler` | Adopted. Fold lowering imports the pure bucket function directly. |
| 7: method-normalization ternary | Adopted with a typed `match`, preserving the same admitted method set without adding a second method registry. |
| 8: erased authority error reason | Adopted with privacy constraints. Structured repair retains exact owner-issued reasons; arbitrary `ValueError` arguments, causes and contexts remain redacted. Missing/cyclic metadata roots also fail explicitly. |
| 9: construction assertions | No new error layer. These assertions follow `_target_measure_type` validation of immutable source facts. Three missing-input regressions prove the existing `SemanticLoadError` owns reachable failures and repair guidance. |

### Spec axis

| Review item | Disposition and evidence |
| --- | --- |
| (a)1: untested trailing and partial-end expansion | Adopted. Added selected weekly trailing-14-day and monthly reset expansion cases with partial observation ends, original ordinals, earlier-only partitions and excluded future rows. The previous expansion case used all-history; reset coverage was fold-only. |
| (a)2: producer registrations versus required parts | Kept as separate facts. Producer IDs/versions advertise registered capabilities; exact row authority determines which retained parts are required. Tests verify ordinary and distinct Metric/Delta rows receive different required-state sets. |
| (b)1: unused helper scope | Resolved by Standards item 1. |
| (b)2: expansion marker scope | Adopted. The special marker is set only when the definition carries distinct membership authority. |
| (c)1: independently recomputed paired ordinals | Adopted. Memberships join the final Delta side-time and coordinate mapping and carry its ordinal verbatim. Tests cover selection, renamed fields, null dimensions, nonchronological ordinals and absent sides. |
| (c)2: receipt ancestor-name false positives | Adopted. Receipt-only guards recognize the adapter-owned `parts/<role>/<payload>` position; real primary reads under membership-named ancestor directories succeed, while actual private part reads still fail before iterator creation. |

### Review follow-up gate

The unchanged follow-up candidate contains 811 source, test and configuration
files on base HEAD `8384d5f0`, with ordered-content SHA-256
`872f46dcc6bd1e7a3a0ce287acdd648720ca59f0090cc121e6c6626457cdf6e1`.
The evidence index, complete file hashes and terminal logs are under
`evidence/slice-5c/review-followup/`.

- `make check-agent` passed: 6,276 default tests passed, two existing skips,
  404 source modules typed, lint/import checks and API documentation built.
- All 14 touched test/helper modules passed strict Python 3.10 typing.
- The same nine focused Runtime modules passed with four workers: 54 tests,
  zero skips, 114.84 seconds. The 29 Runtime records contain 58 matching
  candidate manifests, including all eight distinct acceptance records.
- The extraction's temporary `bucket_end` name collision was repaired; all
  affected cumulative/compiler/fold regressions and both new partial-window
  cases passed, followed by the complete daily and focused Runtime gates.
- Independent bounded inspection confirmed that temporal extraction preserves
  standalone exclusive-end reset, displayed-bucket reset, clipped trailing
  bounds and retained fold endpoints, and that membership views retain Delta
  ordinals without recomputation.

No verification failures remain in these scopes. At this gate the index was
unchanged and clean, and the task-owned versioned MinIO service was stopped.
The owner subsequently authorized a scoped commit. Commit preparation reuses
the unchanged Python candidate and the passing gates above. The original
private-only slice boundary remains.
