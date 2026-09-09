# Slice 5d: private distribution-Shapley attribution

Status: private implementation and technical acceptance complete (2026-09-09).

## Accepted scope

The owner approved exact median/percentile and explicit DuckDB T-Digest
percentile, including real Runtime acceptance. Both remain private until Slice
8. The existing public semantic declaration and analysis surface stay unchanged.
The baseline is clean `lazy-dataset` commit `eb593ae3`; the preceding Slice 5c
work was committed between planning and implementation. Its contents are
preserved. The baseline manifest is in `evidence/slice-5d/baseline.json`.

The owner defines each distribution Attribution row's `current_value` and
`baseline_value` as the weighted upper and lower coalition values in the exact
Shapley formula. Their difference is `contribution`; they are not partition
percentiles and their side sums need not equal the overall endpoints.

## Implementation

The implementation reuses `MetricFoldAuthorityV1`, registered retained-part
roles, `CompiledDataset`, existing physical source/local steps, the guarded
worker and existing Attribution publication. The only new transfer input is a
closed `CoalitionInput`; it is not a Dataset, Artifact or public result family.


Capture a closed method/q contract and exact source-private value-frequency
authority. Preserve it through governed observation, selections, logical axis
expansion, comparison, engine checkpoints and cold reads. Reuse the existing
Delta/Attribution families, retained relations, receipts and publication.

One compatible source domain owns complete mapping and coalition evaluation.
Top-K scores use current plus baseline non-null frequency. Every mapped scope
and resolution has at most eight players. Exact interpolation uses cumulative
frequencies; T-Digest replays ordered values in the engine with pinned method
and adapter versions. Independent endpoints use the same registered recipe.
No source distribution or Entity identity is collected into Python.

A closed physical preparation boundary transfers complete coalition values,
non-identity player coordinates and independent endpoints to the guarded local
worker. It verifies coverage before exact Shapley arithmetic. Dependent row
operations stay local; final output uses local or object storage. Engine output,
Entity-scoped numerical inputs, incompatible source domains and generic
distribution folds are not admitted.

Retained state integrity, receipts, complete-input/method/output/deadline guards,
reconciliation, scoped Findings and inherited approximation are mandatory.
Missing state never authorizes origin replay. Every failure is atomic.

## Acceptance

Use independent sorted-percentile/permutation references and independently
queried ordered DuckDB approximate coalitions. Cover joint/hierarchy, Top-K and
Other/null masks, repeated scopes, eight/nine players, one-sided and undefined
coalitions, sampling, all operand authority orders, source-closed continuation,
three-process recovery and exact binding reuse. Exercise malformed or missing
state, receipt mutation, combined budgets, forbidden transfers and cancellation.

Run focused default tests and typing/lint, `make check-agent`, and necessary
focused Runtime tests including isolated versioned object storage. Freeze the
candidate and retain terminal records and logs under `evidence/slice-5d/`.
Do not run the complete release Runtime gate or claim 5b/parent Slice 5 closure.
This task does not authorize a commit, push, release or public activation.

## Boundary verification details

The numerical oracle enumerates every permutation for one, two, four and eight
players and compares independent empirical-percentile coalition values. A
separate mapped-player oracle derives Top-K from current plus baseline
frequencies, distinguishes genuine null from Other and verifies hierarchy
prefix games independently rather than summing their child contributions.
Approximate reference coalitions use a separately executed ordered DuckDB
`approx_quantile` query.

Source checks cover every finite endpoint and coalition, complete scope and
resolution coverage, duplicate/missing player and coalition identities, and
positive unique frequency state. The source-certified total coalition count
also prevents loss of an entire otherwise valid scope during transfer. All
input validation precedes Shapley arithmetic. Input/method budgets count the
complete coalition relation; output limits count actual Attribution rows.

Runtime cases cover all four Metric operand authority orders, logical and
retained Delta inputs, logical axis expansion, one shared sampling realization,
eight/nine mapped-player boundaries, one-sided support and undefined games.
Mutated or missing private state leaves primary reads available but blocks
consumption; a receipt mutation after output rename rolls back publication.
Input, method, output and deadline failures publish no Artifact/Evidence or
owned resource. Adjacent source-private, retained-fold and attribution Runtime
cases exercise the reused adapters and assertion batching.

Exact and approximate Decimal/float32 sources produce the declared float64
percentile, and source-private state retains the original numeric values.
Generic Decimal declarations use the receipt's resolved physical precision.
Cold metadata rejects mismatched side percentile methods even when their
Attribution method id is the same. Projection accepts the existing Metric ref;
method selection belongs only to the private observation input.

The final sink matrix covers exact and T-Digest methods on local and isolated
versioned object storage. Each journey uses three processes: produce an engine
Delta and remove its origin; recover and publish hierarchy Attribution plus a
rank/limit continuation; recover and reuse both exact bindings with source
construction, compilation, placement and worker execution prohibited. Artifact,
Evidence, Findings, row/row-set fingerprints and Session snapshots must match.

## Final candidate and gate

The final **827-file** source/test/configuration candidate is
`ec836bd9f12025fb40a87cc872c20a0e1b55ee189655741a7e754e870d728260`,
on base commit `eb593ae3`. The ordered manifest covers `marivo/**/*.py`,
`tests/**/*.py`, `Makefile`, `pyproject.toml` and `.importlinter`; documentation,
generated API pages and evidence files are excluded. Source/test/configuration
hashes are unchanged before and after both gates and every recovery journey.

- `make check-agent`: **6,299 passed**, zero skips; lint, import contracts,
  **411 source modules** under type checking and API documentation build passed.
- Strict touched-test type checking: **9 files**, no issues.
- Focused `make runtime-test`: **64 passed**, zero skips, two workers. This
  includes the distribution Runtime matrix plus affected distinct-source,
  private-part, retained-fold and Attribution source regressions. The complete
  release Runtime suite was not invoked.
- Four local/object exact/T-Digest recovery records bind **12 separate
  processes**. Cold exact reuse has no source statements or transferred rows,
  preserves Artifact/Evidence/Findings identity and leaves Session state intact.
- Supplemental Runtime execution verifies logical missing-axis expansion with
  original selection, materialized missing-axis rejection and incompatible
  source-owner rejection. The two rejected actions change no persistent state.

The acceptance environment used Python 3.12.13, DuckDB 1.5.3, Ibis 12.0.0,
pandas 2.3.3 and PyArrow 25.0.1. Object acceptance used isolated versioned MinIO
`RELEASE.2025-09-07T16-13-09Z` at `127.0.0.1:19060`. The task-owned service was
stopped after acceptance; fixture buckets were cleaned by their owning tests.

Local evidence:

- [Candidate manifest](../../../evidence/slice-5d/candidate-files.json)
- [Final gate and log/record digests](../../../evidence/slice-5d/final-gate.json)
- [Daily gate log](../../../evidence/slice-5d/daily-acceptance.log)
- [Focused Runtime log](../../../evidence/slice-5d/runtime-acceptance.log)
- [Supplemental Runtime record](../../../evidence/slice-5d/expansion-runtime.json)
- [Recovery records](../../../evidence/slice-5d/runtime/)

These final records supersede the intermediate iteration logs, including the
pre-fix budget, method-authority and numeric-type checks. This closes only the
private Slice 5d implementation and technical gate. Slice 5b, parent Slice 5,
independent review approval, public cutover and release remain separate. No
commit, push, release, public export, public Help or public declaration switch
was performed.

## Review follow-up (2026-09-09)

The owner authorized evaluating and adopting the supplied review suggestions.
The workspace now includes independent testing-policy commit `5739a13b`; its
local functional / separate object-connector coverage is preserved. The earlier
827-file candidate and MinIO evidence remain historical, not evidence for this
follow-up candidate. Final follow-up checks are recorded separately below.

| Review item | Disposition |
| --- | --- |
| Standards 1 / Spec c: repeated resolution check | Accepted. Remove the redundant post-arithmetic check; retain complete coverage validation before arithmetic. |
| Standards 2: repeated method/state inventories | Partially accepted. `INDEPENDENT_RESOLUTION_METHODS` owns the shared row-resolution meaning in construction/decoding. `source_private_part_authorities` owns the membership/distribution inventory. Source placement, proof requirements and local eligibility keep their distinct method dispatch rather than sharing a misleading universal method set. |
| Standards 3: repeated compilation predicates/returns | No extra abstraction. Logical compilation flushes ordered sample preparations; retained compilation normalizes physical types; placement establishes a source-to-local boundary. Their short method checks serve different boundaries, and factoring just the constructor would hide the preparation difference without removing an invariant. |
| Standards 4: misleading membership names | Accepted. Generic role/part/validation/transfer helpers and compiler variables now use source-private names. Actual distinct-only contracts keep membership terminology. Callers are renamed with no compatibility aliases. |
| Standards 5 / Spec b3: preview naming and order | Accepted. Render named quantile fields and insert the complete ordered list once. Document private method/q/approximation disclosure and verify two authored methods plus retained projection through Runtime. |
| Standards 6: shared Arrow type helper import | No relocation. This is existing shared type-policy reuse and violates no import contract. Moving it across every reader/writer for this review would add unrelated churn; no new local type-policy copy is introduced. |
| Spec a1: cancellation evidence | Accepted coverage gap. Deadline tests alone were insufficient evidence of explicit cancellation. Add an actual blocked DuckDB source query, real worker, and parent KeyboardInterrupt; verify source interruption, worker termination, failed Run, and unchanged Artifact/Evidence/resource tables. |
| Spec a2: positive Metric projection | Accepted. Check exact/approximate logical projections and source-offline retained projection preserve the original method/q and values. |
| Spec b1: public observe input widening | Rejected premise. `session/_lazy_sources.py` defines an explicitly private facade; the active public `Session.observe` in `session/core.py` and its eager Frame input/output contract are unchanged. No exports or Help entry activate the private facade. |
| Spec b2: automatic distribution retention | Clarified owning documentation. Supported private percentile observations always bind the registered state; construction is metadata-only, while engine execution captures/validates the basis. This is required by the selected checkpoint/continuation contract, not a new public default or an optional mode. |

The cancellation test independently observes native `InterruptException` and
checks that the actual worker PID no longer exists. Its canary is absent from
the surfaced safe failure. This closes the earlier explicit-cancellation
coverage gap; the original deadline evidence is not relabeled as cancellation.

### Follow-up gate

The final **830-file** candidate is
`10826fd69be2be4e4e6e308f9b91c251f3a88f7c7a3e2d86c32c7d5a7bbd8ed4`
on base `5739a13b`. Source/test/configuration content remained unchanged across
both gates and both recovery records:

- `make check-agent`: **6,321 passed**, zero skips; lint, import contracts,
  typing of **412 source modules**, and API documentation passed.
- Strict touched-test typing: **10 files**, no issues.
- Focused `make runtime-test-agent`: **64 passed**, zero skips. The JUnit report
  explicitly records the new cancellation and ordered-preview/retained-projection
  tests as passed. No external object service was required or started.
- Two exact/T-Digest local-file recovery records bind **six separate processes**;
  cold exact bindings preserve snapshots and issue no source statements.

[Follow-up gate and evidence digests](../../../evidence/slice-5d/review-followup/final-gate.json),
[candidate manifest](../../../evidence/slice-5d/review-followup/candidate-files.json),
[daily log](../../../evidence/slice-5d/review-followup/daily.log),
[Runtime log](../../../evidence/slice-5d/review-followup/runtime.log), and
[JUnit results](../../../evidence/slice-5d/review-followup/runtime.xml)
are the current review-follow-up evidence. The earlier object-storage journeys
remain evidence for their historical candidate. Object connector smoke belongs
to the separate current gate in `docs/testing/runtime-coverage.md`.

No commit or push was made by this follow-up. It preserves private Slice 5d
completion without claiming Slice 5b, parent Slice 5, public cutover or release.
