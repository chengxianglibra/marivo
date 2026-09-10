# Slice 7b: private Event reducers and subject selection

Status: private Slice 7b implementation and technical acceptance complete on
2026-09-10. Slice 7c, Lifecycle and public activation remain separately gated.

## Baseline and accepted scope

The approved plan starts from the committed Slice 7a baseline on `lazy-dataset`,
HEAD `fb05f180`. The planning baseline's 928 executable files matched candidate
`651abecb217dcbd10dbfcef5b3bc1a39b2c6087f149614999f0f3068840f7728`.
This slice adds private funnel, time-to-event and DroppedBefore selection from
logical and retained engine journeys, plus the Metric -> Event -> Population ->
Metric loop. Public activation, Event comparison/attribution, Lifecycle, commits,
pushes and releases are outside this work.

## Confirmed contract amendments

- The first missing assigned step determines subsequent reach truth: unknown
  propagates, while proven absence makes subsequent steps unreachable.
- PatternStep admission uses the complete retained Event/participant/key value,
  exactly once in the receiver Pattern. Equal copied/recovered values remain valid.
- Time-to-event ordering uses subject identity, from_time, from_event_identity,
  then journey_id, with nulls last. It needs no additional retained sort state.
- Funnel resolved-cohort counts exclude all unknown target reach; censored-entry
  counts include only entered subjects with unknown target reach. Empty ungrouped
  funnels emit dense zero rows; grouped output contains only realized groups.

The owning Subject/Event/Lifecycle design records these amendments before code.

## Implementation ownership

- Domain worker: paired methods, payloads, schemas, admission and filter contracts.
- Compiler worker: native relation algorithms, scalar proofs, axis enrichment and
  independently calculated numerical references.
- Persistence worker: closed codecs, shape-owned Evidence, row validation and ordering.
- Integration owner: shared registry, compiler dispatch, Runtime source context,
  Population continuation, publication orchestration and cross-process acceptance.

All owners preserve concurrent changes. New semantic enrichment captures explicit
current Session context once; pure recovery and retained reducers remain independent
of a source catalog. Identity calculation is source-required; local/object journey
receipts do not authorize a local fallback.

## Acceptance scope

The completed gates cover focused construction/numeric/storage tests, touched-module
and new-test typing, the compact broad daily gate, bounded focused Event and shared
membership Runtime gates, and independent review. Final evidence binds one executable
fingerprint. Separate producer, source-offline continuation and cold binding processes verify
empty and uncertain membership, immutable reuse, cancellation/failure atomicity and
identity redaction. This record and the cutover matrix close only private Slice 7b.

## Implementation and review boundaries

- Both logical-source and retained-engine compilation use native shared reach
  classification, funnel, selected-step duration and subject selection. Exact
  input-node coverage and validation fences prevent nested Event/Population
  graphs from overwriting one another's proof. Unknown selection fails before
  any downstream filter, sample, membership join or publication.
- Event axes use the first assigned occurrence, governed to-one paths and exact
  temporal uniqueness. Independent review reproduced an axis named `journey_id`
  grouping by the opaque journey coordinate. Construction now rejects all
  journey/output column collisions, and cold schema validation pins each axis
  name to its exact semantic reference. Existing Ref grammar rejects internal
  scratch names before source execution.
- Source output, retained output and local result filtering preserve Pattern
  ordinal funnel order. TTE uses the selected pair's subject/time/occurrence/
  journey order with nulls last. Local duration and nullable occurrence identity
  conversion retain the original physical schema during publication.
- Result codecs, native proofs and bounded local validators own their exact
  shape. Funnel rates use native double-operand division, including counts above
  2**53; count equations stay exact. Empty TTE batches still validate occurrence
  tuple schema. Neither Event result owns additional retained parts.
- Selection publishes unique complete Population rows, its own membership
  definition and scalar selection Evidence. Shared sampling provenance and the
  original selection time survive downstream refinement; result filtering only
  carries the existing common sampling part.

The first candidate check passed focused defaults and then exposed two old 7a
Runtime test annotations that assumed every Event Evidence value was a journey.
Those tests now explicitly narrow the shape. The historical attempt remains in
`evidence/slice-7b/initial-check/`; final acceptance below uses a new unchanged
candidate and fresh gates.

## Frozen candidate and final gates

The accepted 944-file executable candidate is
`95b98ac18210a579824b94e97fe672ac7e9a9db01390667f9802504cbc5f9d03`.
The manifest covers all `marivo/**/*.py`, `tests/**/*.py`, `pyproject.toml`,
`Makefile` and `.importlinter`. Its digest uses sorted UTF-8 path, NUL, exact file
bytes, NUL. Documentation-only acceptance updates do not change the candidate.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused 7b and affected 7a construction, native references, storage and source-free construction | 245 passed | 16.194 s |
| Explicit Event test typing with package bases | 22 files passed | 1.777 s |
| `make check-agent` | 6,992 default tests; format/lint, import contracts, 456-file typing and API docs passed | 123.976 s |
| Event Runtime, including affected 7a coverage, temporal, membership and fresh-process recovery | 83 passed | 101.849 s |
| Shared entity-Candidate membership, retained membership, sampling and materialization Runtime | 80 passed | 65.930 s |

`evidence/slice-7b/candidate.json` retains per-file SHA-256 values;
`gates.json` retains exact commands, exit codes, elapsed times and log hashes.
Every gate verified the same manifest before and after execution.
`verify_candidate.py` is the replayable gate driver. Runtime gates explicitly
use `RUNTIME_WORKERS=2`; no release check or MinIO/object-service gate ran.

Independent read-only review found and reproduced the axis-name collision.
Its repair and the subsequent local dtype/dispatch increments were re-reviewed,
including nine targeted default checks and three in-memory full/nullable/empty
TTE roundtrips. `independent-review.json` binds the final no-findings conclusion
to this same candidate. Full Runtime evidence is the separate gate above.

## Fresh-process and privacy evidence

`slice-7b-engine-runtime.json` records producer, continuation and cold interpreter
PIDs `81205`, `81209` and `81266`, with identical candidate fingerprints.
Production materializes Metric membership and a journey. Continuation removes
both occurrence tables, then executes ungrouped and null-axis funnels, TTE,
DroppedBefore selection and Metric observation over the selected Population.
Cold reconstruction returns the exact same five results and bindings with
placement, compilation and source-backend creation forbidden.

Continuation and cold recovery have identical result hashes, receipts and
shape-owned Evidence. Store counts remain seven Runs, seven terminals, seven
Artifacts, seven Evidence records and six input edges, with zero resource-journal
entries. Cold recovery adds no Run or publication and performs no primary query,
source fence, validation query or identity transfer. The Metric loop preserves
one selected subject and its independently expected revenue of 100.0; grouped
counts reconcile exactly, including the real null group.

The same candidate also re-runs 7a's local-file and engine three-process journey
acceptance, retained separately as `slice-7a-local-runtime.json` and
`slice-7a-engine-runtime.json`. Local Parquet reducer-output filtering passes
with and without inherited sampling, including complete ordered funnel rows,
nullable occurrence identities, duration values and a complete empty TTE result.
Local/object journey receipts still cannot run identity-bearing reducers.

The 5,000-subject native case forbids Python primary-row transfers and local
workers. Runtime assertions scan errors, persisted metadata, Run/Evidence/Findings,
SQL statement logs and object names for subject and occurrence identity canaries.
Source/offline corruption, unknown selection, cancellation and commit failures
publish no partial Artifact or Evidence and leave no owned resource entries.
Every successful new Event/selection result has zero Findings; Event results
have no domain-owned retained parts, while inherited common sampling state is
preserved when present.

Runtime versions: Python 3.12.13, DuckDB 1.5.3, Ibis 12.0.0, PyArrow 25.0.1 and
pandas 2.3.3. Only private Slice 7b is closed. No public export, Help, packaged
workflow skill or site surface is activated. The workspace remains uncommitted;
no staging, commit, push or release was performed.

## Review follow-up and suggestion disposition

The earlier acceptance and supplemental review describe candidate `95b98ac1...`.
A subsequent read-only Runtime review found that new selected Population inputs
could observe a scalar Metric but could not add a subject Dimension: their
identity-only membership spine lacked the current subject column. The same
underlying limitation already affected retained Population inputs on base
`fb05f180`; this follow-up fixes the shared explicit-enrichment seam.

Metric coordinate construction now joins current governed subject rows only when
Dimensions/time axes require missing source columns. The selected identity set
remains authoritative, and a missing atemporal current subject fails validation
before publication. Pure selection and identity-only Metric continuation keep
their source-independent membership behavior. New Runtime cases cover logical,
retained and sampled selections, source-offline occurrence tables, a true null
axis value, exact Metric values and atomic failure for missing subject rows.

| Review suggestion | Disposition |
| --- | --- |
| Reducer parameter docstrings | Added parameter descriptions to both Event states' funnel, time-to-event and selection methods, and to the new result filter. `CLAUDE.md` links to the same `AGENTS.md` requirement; these implementations remain private until cutover. |
| Repeated TTE ordering tuple | Extracted one domain-owned `TIME_TO_EVENT_ORDER` for construction and validation. Independent numerical/storage ordering checks remain separate. |
| Repeated Pydantic revalidation | Retained the two concrete checks: PatternStep and DroppedBefore are different trust-boundary types. A generic helper would add abstraction without removing a distinct rule. |
| Abbreviated validator methods | Renamed the two production helpers to `_time_to_event` and `_time_to_event_schema`. |
| Sibling private helpers | Retained existing internal package reuse. No repository rule forbids this, and copying the governed-version/identity logic or adding another module would enlarge the change. Journey field names continue to have one owner. |
| Assertion and two batch passes | Replaced the assertion with an explicit structured status check. Kept validation and scalar summarization separate over each bounded batch; no measured performance issue justifies merging their responsibilities. |
| Nested first-step access | Retained the single expression; an extra local alias would not change ownership or behavior. |
| Runtime versus semantic owner local | Retained intentional owner narrowing only for explicit enrichment. Both variants share the same Session, Store and action port; pure reducers retain Runtime ownership. |
| Replace cumulative TTE entry coverage with only from-step coverage | Rejected: the first missing step can precede from_step, and its unknown truth propagates. The initial step is an observed journey anchor, so its entry cannot be unknown. The suggested replacement would reject valid propagated uncertainty. A new four-step Runtime case proves live publication and cold recovery with unknown B and complete C when selecting C -> D, plus rejection of forged anchor entry uncertainty. |

The existing numerical reference already covers step pairs `(0, 1)`, `(0, 2)`
and `(1, 2)`. TTE admits repeated-attempt matching; only funnel and DroppedBefore
require `first_per_subject`. No operator admission was changed based on the
review's contrary summary.

The prior `independent-review.json` explicitly identifies its author as the
persistence implementation agent: it is supplemental cross-module review, not
independent validation of that author's persistence implementation. The earlier
separate read-only review supplied the axis-collision finding. Neither historical
review is relabeled as a new independent review of this follow-up. This revision
records the external/local review dispositions and fresh executable gates below.

### Review-fix accepted candidate

The current accepted 944-file executable candidate is
`d74bcafbc1e1fccdd0f41ff2121968b22768c3dc4ba32a4c84d55bd4c5fd4c6f`.
It supersedes the earlier candidate for current acceptance; historical evidence
is preserved. Fresh evidence, per-file hashes, exact commands and log hashes live
under `evidence/slice-7b/review-fix/`, including `review-resolution.json` and the
replayable `verify_candidate.py`.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused Event default tests | 245 passed | 14.612 s |
| Explicit Event test typing | 22 files passed | 1.182 s |
| `make check-agent` | 6,992 default tests; format/lint, import contracts, 456-file typing and API docs passed | 124.453 s |
| Event Runtime including review regressions and affected 7a | 88 passed | 109.599 s |
| Shared membership, sampling and materialization Runtime | 80 passed | 65.429 s |

All five gates preserved the same executable fingerprint. New three-process
reducer evidence records PIDs `97600`, `97603` and `97748`, the same candidate
before/after, unchanged cold Store counts, zero cold primary queries and zero
cold identity transfer. Both 7a storage variants also re-ran their three-process
journeys on this candidate. Runtime concurrency stayed at two workers maximum.

The prior P2 Metric enrichment finding is resolved. The suggested TTE coverage
change is rejected by direct positive and negative Runtime evidence, not deferred.
Other review suggestions and the historical review-independence limitation are
recorded above. This follow-up creates no new independent-review claim and does
not activate Slice 7c, Lifecycle, public APIs or release work. No staging, commit,
push or publication was performed.
