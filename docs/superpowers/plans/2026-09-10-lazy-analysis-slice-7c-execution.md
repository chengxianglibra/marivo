# Slice 7c execution record

## Scope and implementation

Private implementation and the frozen-candidate gates completed on 2026-09-10.
This change implements private Event funnel comparison and loss-rate attribution
on the existing Delta/Attribution families. Slice 7b is the prerequisite; the
shared Slice 5a–5b protocols are consumed without declaring the independent
Slice 5b gate complete. Public exports, Help, bilingual site activation,
integrated D/L/M journeys, Lifecycle and release work remain outside this slice.
No commit or push is part of this execution.

The exact normalized variants are `compare/event_funnel@v1` and
`attribute/event_funnel_delta@v1`, carried by the existing private operator
registry as `event.compare` and `delta.funnel_attribute`. Both preserve Core's
single definition identity and the shared Artifact publication protocol.
Metric attribution retains its existing overload; Event requires an exact
non-initial `FunnelLossRate` target.

Comparison checks Pattern/matching, subject identity, Population definition,
axes and dependencies, equal cohort duration, time domain and follow-up offset.
Both complete inputs are checked before row filters. Exact step/axis full outer
alignment zero-fills only missing additive coordinates. Each side's rate is
recomputed independently; an undefined comparison keeps a null delta and its
explicit status.

Attribution admits only an ungrouped logical Delta backed by logical funnels
with complete logical or retained journey inputs. It extracts complete
lost/resolved-entry components, enriches governed Dimensions under the first
anchor contract, and does not rematch occurrences. Selected target presence and
complete components must reproduce the original endpoint before presentation
mapping. Aggregate funnel and Delta checkpoints cannot recover assignments.

Native Ibis performs preparation and allocation in the source engine. The
pandas variant receives complete compact aggregate inputs only, checks their
combined method budget, and remains local thereafter. Both implement joint and
hierarchy resolution, exact combined-denominator Top-K mapping, explicit Other
masks, typed null axes, ratio-mix side terms, ranking and shared pool rules.
Every resolution reconciles current, baseline and delta before publication.

The `event_funnel.additive_components@v1` part retains mapped counts and full
resolution totals with contribution coordinates. The existing publication,
receipt, codec, Quality, Validation, Evidence and Finding mechanisms own atomic
publication and cold recovery. Event Delta exposes where/attribute/state;
Event Attribution exposes state actions. Metric continuations are not admitted.

## Acceptance protocol

The executable candidate covers every `marivo/**/*.py`, `tests/**/*.py`,
`pyproject.toml`, `Makefile` and `.importlinter`, including newly created files.
The digest hashes sorted UTF-8 relative path, NUL, exact bytes, NUL. Documentation
updates are outside that executable digest. `evidence/slice-7c/candidate.json`
contains per-file hashes. `verify_candidate.py` records each gate's command,
exit status, elapsed time, log hash and matching before/after candidate hashes in
`gates.json`. Runtime uses at most two workers; no release-check or object service
is part of acceptance.

Independent numeric tests compare full native/pandas outputs and use a separate
closed-form oracle for contribution values. They cover missing/empty coordinates,
zero denominators, exact large counts, actual nulls, joint/hierarchy, Top-K/Other,
positive/negative pools and per-side endpoint reconciliation. Runtime covers
zero total change, full native/pandas Runtime parity on shifted equal-length
cohorts with a nonzero endpoint, logical/retained journeys, compact local execution, cold
Evidence/part corruption, censoring before filters, removed targets, combined
input budgets, and atomic failure/cancellation with successful retry.

`lazy_event_comparison_worker.py` runs in three separate interpreters. Production
retains an engine journey and deletes both occurrence tables. Continuation
computes Delta/Attribution from that journey. Cold reconstruction verifies exact
artifact and definition identity reuse with zero primary/validation queries.
Identity privacy is checked in each phase. `three-process.json` records distinct
PIDs, candidate hashes and query/transfer counts.

## Final gate results

Accepted 956-file executable SHA-256:
`bc47277378aea3dd82851c64a1fb79ca0833454a7c5869c2baae271d0da3dc2e`.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused Event/no-I/O defaults | 278 passed | 17.291 s |
| Explicit Event test typing | 26 files passed | 0.607 s |
| Modified analysis module typing | 325 files passed | 0.477 s |
| make check-agent | 7,024 defaults; lint/imports, 464-file typing and API docs passed | 117.219 s |
| Event Runtime including 7a/7b regressions | 104 passed | 131.346 s |
| Shared membership/sampling/materialization Runtime | 80 passed | 50.105 s |

All six gates preserved the exact before/after candidate. The three independent
processes were producer `49256`, continuation `49258` and cold recovery `49284`;
all recorded the same executable hash. No partial artifacts or Evidence survived
the exercised failures, cancellation, unknown coverage or invalid authority.

The earlier validation-only candidate `8d258ab1...` passed broad defaults before
the additional nonzero shifted-cohort Runtime parity test was added. It is not
the accepted candidate. The final six-gate run above includes that test and the
complete revised test file. The gate-driver formatting issue was repaired before
this final run. No external reviewer or public acceptance is claimed.

Cutover status and recurring Runtime coverage are updated in the same worktree.
The execution record and local evidence are not staged. Public exports/Help,
bilingual site activation, integrated D/L/M journeys and Slice 5b's independent
gate remain deferred. No commit, push, release-check or service startup occurred.

## Review fixes (2026-09-10)

Read-only review of candidate `bc472773...` reproduced two gaps that its green
gates did not cover: a filtered funnel checkpoint could hide censored steps,
and a governed Dimension named `positive` collided with the native allocation
pool. This section supersedes that candidate's acceptance for these boundaries.

Runtime now checks exact retained producer contracts before executing a funnel
comparison. A checkpoint produced by filtering has no pre-filter completeness
authority, so it is rejected with a concrete unfiltered-funnel/journey repair.
The comparison contract includes `event_funnel_checkpoint_scope@v1`, changing
new logical execution identities so pre-fix cache hits cannot bypass this gate.
This rule does not infer or replay lineage. Unfiltered funnel checkpoints remain
admitted, and the complete-row censoring checks still apply to them.

Native share expressions now reference the pool relation itself. Governed axes
named `positive` and `negative` retain their own coordinates and no longer bind
the pool arithmetic. Regression cases compare every source/pandas output column
for both names on nonzero shifted cohorts. Cold local and engine checkpoint
cases assert typed rejection, unchanged Artifact/Evidence counts and no pending
resources. Focused Runtime iteration passed 20 cases.

The revised candidate and final gates are recorded under
`evidence/slice-7c/review-fix/`; earlier evidence is preserved separately.

Historical first-review-fix 956-file executable SHA-256:
`daaff18eeb48fde558149fc9b4c07ff9446c8638764cc490644d58df511e289c`.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused defaults | 279 passed | 28.952 s |
| Explicit Event test typing | 26 files passed | 4.914 s |
| Scoped analysis typing | 325 files passed | 0.504 s |
| make check-agent | 7,025 defaults, lint/imports, 464-file typing and API docs passed | 148.426 s |
| Event Runtime | 108 passed | 159.450 s |
| Shared Runtime | 80 passed | 59.157 s |

Every gate preserved matching before/after hashes. Fresh-process PIDs `70186`,
`70188` and `70220` record the same revised candidate; cold recovery retains zero
execution/validation queries. The original `bc472773...` evidence remains under
`evidence/slice-7c/` as historical evidence, not acceptance of the repaired
boundaries. The earlier repair iteration was superseded when the explicit scope
version was added; all gates above were rerun on the final immutable candidate.
No commit, push, public activation or release-check occurred.

## Adversarial-review assessment (2026-09-11)

The supplied review was checked against the current implementation and the
owning Event/shared contracts. Accepted changes are deliberately bounded:

| Suggestion | Decision and evidence |
| --- | --- |
| Missing compare parameter documentation | Accepted: both paired Event classes now document the compatible logical/materialized baseline parameter. |
| method/causal_claim semantic roles | Accepted: register `method_identity`, use it for both exact generated fields, and pin those fields independently in contract tests. Numeric behavior is unchanged; row identities change with the corrected schema. |
| Native duplicate-coordinate defense | Accepted as path conformance: add per-side native uniqueness proofs over selected exact step/axis keys. Adversarial tests cover both sides and real null axes, matching pandas rejection. Normal funnel production already aggregates by these keys; this does not claim ordinary producers emitted duplicates. |
| Repeated component-name definitions and initial-step check | Accepted: both arithmetic backends consume the domain-owned four-name tuple; compute the local non-initial predicate once. |
| Initial-step zero_denominator meaning | Clarified in the owning spec and pinned in numeric assertions: an initial step has no preceding transition denominator, regardless of its anchor counts. Preserve the existing closed status enum and null rates; missing_side still has precedence. |
| Mirrored Delta dispatch bodies | Deferred: preserve the paired Logical/Materialized class convention; no demonstrated dispatch drift justifies another helper boundary in this slice. |
| Repeated semantic switches and navigation chains | Deferred: owner-local dispatch and explicit immutable metadata access do not establish a correctness defect. Avoid a new cross-layer predicate or wrapper inventory. |
| Test-only primary-result wrapper | Retained: a small private projection of the same production with-parts execution, with no separate allocation algorithm or public export. |
| Finding ordering performance | No algorithm change: the retained set is capped at 1000 and ordering is deterministic. Large-cardinality throughput has not been benchmarked in this assessment; the observation is not treated as a proven performance regression. |
| Public Help/export pinning | Still deferred to public cutover, as required by the slice boundary. |

The corrected candidate is verified separately under
`evidence/slice-7c/adversarial-review/`; previous acceptance records remain
historical snapshots rather than evidence for the new schema.

Current accepted 956-file executable SHA-256:
`74ccc5e85fbf42653f27a77a991b2f3cd9a4cb7ce345078d65414e9ba9e55b07`.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused defaults | 283 passed | 27.786 s |
| Explicit Event test typing | 26 files passed | 4.551 s |
| Scoped analysis typing | 325 files passed | 0.753 s |
| make check-agent | 7,029 defaults; lint/imports, 464-file typing and API docs passed | 153.592 s |
| Event Runtime | 108 passed | 212.715 s |
| Shared Runtime | 80 passed | 87.570 s |

All six gates preserve matching before/after candidate hashes. Fresh processes
`97223`, `97271` and `97332` share this candidate; cold recovery records zero
primary and validation queries. Runtime remained at two workers or fewer.
The complete gate timings are observed wall times, not a controlled performance
comparison or a benchmark of Finding extraction. No commit, push, public cutover,
release-check or external-service startup occurred.
