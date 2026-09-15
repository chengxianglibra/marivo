# Slice 9d: Real-Agent integration acceptance

Date: 2026-09-13

Status: Complete for Slice 9d on 2026-09-15: 15 independent native Agent
journeys and all 81 obligations accepted. Final evidence and qualifications
are recorded below. Release readiness and publication remain separate.

## Candidate and execution boundary

The starting commit is `8bd3ae8f2`, the committed Slice 9c candidate. The
worktree initially contains only historical `evidence/`. The baseline inventory
preserves 42,025 historical evidence files and fingerprints 1,195 tracked files.
Evidence is retained under `evidence/slice-9d/2026-09-13-real-agent/`.

Each journey uses an independent Claude CLI context, an isolated synthetic
project and the checkout's explicit virtual-environment interpreter. The actual
configured model is recorded, rather than inferred from the CLI brand. The
initial authenticated probe reported `zai-messages/glm-5.3-flash`.

Agents receive public task obligations, authored fixture inputs, packaged
analysis guidance and live Help. They do not receive test sources, independent
expected results, internal implementation details or earlier Agent transcripts.
The controller owns independent numerical checks, source counters, fault
injection and post-process Store verification. Agent scripts and terminal
outcomes are required; journals and dispatch receipts are supplementary.

The current Parquet contract governs retained results, including native DuckDB
execution over immutable Parquet membership. Historical engine-storage wording
does not authorize restoring a removed storage target. No public API growth,
commit, push or publication is included.

## Required journey matrix

Every numbered obligation in the parent plan remains required. Each accepted
variant needs an explicit outcome in the machine-readable matrix; these family
groups do not certify unexecuted variants.

| Journey | Fixture and independent assertion owners |
| --- | --- |
| A | Governed sales source; source-algebra, coordinate, selection and economics assertions |
| B | Sales checkpoint; correlation/entity-outlier and exact cold binding assertions |
| C | Additive, component-mix, distinct and distribution fixtures; attribution numerical and barrier owners |
| D | Governed Event fixture; retained membership and Metric return owners |
| E | Inception-complete Lifecycle fixture; history, reducer and censoring owners |
| F | Independent exact source bindings and local/object Parquet; local placement, input bounds and no-retry owners |
| G | Controller fault schedules; admission, crashes, acknowledgement, contention and reconciliation owners |
| G2 | Two public Sessions and source-offline Delta; original ownership, graph and integrity owners |
| H | Controlled parameterized JSON source; captured values, distinct/equal keys and redaction owners |
| I | Additive, cumulative, ratio, mean and weighted fixtures; temporal/Dimension fold and required-part owners |
| J | Independent forecast histories; every registered model, intervals, coverage and failed publication owners |
| K | Time/driver fixtures; independent Candidate scorers, empty outcomes and barrier owners |
| L | Complete current/baseline Event assignments; funnel reconciliation, compact pandas and censoring owners |
| M | Entity-outlier fixture; Metric/Event/Lifecycle population and cold membership owners |
| N | Retained successes/failures and generation fixtures; scoped reads, three integrity axes and generation rejection owners |

## Acceptance rules

Fresh execution/recovery processes must record actual Session, Run, Artifact and
Evidence identities, row assertions and post-run reads. Exact recovery and
pre-admission rejection must prove no new Run instead of filling fictitious IDs.
Failures remain in evidence, classified as product, fixture, Agent, intentional
boundary or environment outcomes. Product repairs invalidate affected acceptance
and require fresh Agent attempts and regression checks.

Final closure requires all parent-plan variants, affected 9b/9c reruns, the full
`make check-agent` gate, public default/Runtime examples, `make release-test`,
bilingual site checks and whitespace validation on the final candidate. Missing
coverage remains pending. Historical evidence must pass byte-for-byte readback
before handoff. Release readiness and publication are not implied by partial
journey success.

## Historical validation-memory repairs

The pre-repair candidate passed 4,848 default tests, 892 Runtime cases, a separate
captured-binding cold-recovery case, seven live object-storage cases, 29 release
tests and the bilingual site gates. The isolated wheel passed 376 contract tests,
five Runtime examples and the three-process journey with Run counts 1/2/2.
Its 328 package files matched the source bytes. These are baseline gates, not
final acceptance of the repaired candidate.

Journeys A and B passed independent revalidation on candidate
`4e842a6c3cf8cb1bb49b957fccfa2bcd7c0fa6b6ef36028e60f98328c61f693f`.
A includes selection order, four tie policies, governed sampling, negative
controls and cold integrity reads. B uses one checkpoint for correlation and
Entity-outlier discovery with the origin offline; fresh processes recorded Run
counts 1/1/3/3 and source connections 1/0/0/0. C.1-C.4 also passed, while C.5
exposed the additional failure below. These results require revalidation after
the latest shared validation repair.

Journey C reproduced a product failure missed by the baseline Runtime matrix.
A daily comparison over 80 authored rows, followed by expansion of an unretained
Dimension for attribution, combined 42 source validations into one union and
exhausted DuckDB's fixed 256 MiB memory budget. The exact Agent program and a
reduction onto the existing source-attribution fixture both reproduced it.
Controller-only experiments with one-check and eight-check batches succeeded
under the unchanged memory limit and no-spill policy.

The initial eight-check bound passed 4,851 default tests, 893 Runtime cases,
seven live object-storage cases, 29 release tests, public examples and bilingual
site gates on the candidate above. The installed wheel matched all 328 package
source files and passed the cold three-process journey. These gates did not
cover the next real-Agent failure.

C.5 reproduced another validation-memory failure for hierarchy attribution of
a ratio of two sums over channel and customer Dimensions. Eight-check and
four-check batches failed under the fixed native cap. Upfront one-check queries
succeeded for both retained-coordinate and logical axis-expansion programs,
with 12 independently reconciled rows and one primary query. A reduced Runtime
regression using the shared source fixture failed before this correction; a
sum/count control did not reproduce the failure.

Source preparation now plans one query per validation. This prevents unrelated
checks from retaining aggregate state together. Named checks, order, preparation
fences, transactions, the native memory cap and no-spill policy remain unchanged.
There is no retry after a failed validation. The owning Runtime spec records
this contract. Final acceptance and gates remain pending for this correction.

The initial C.5 context was stopped after more than one hour of repeated
exploration. Its exact scripts, observed rows, failures and partial delivery
were preserved. At that point the remaining method variants and cold recovery
required follow-up, and D-N had not been dispatched. The continuation below
supersedes that progress snapshot; process exit alone never grants acceptance.

All failed Agent attempts and controller experiments are retained. The first B
attempt incorrectly re-observed source Metrics; the first A completion report
misread a correct governed mean of 35 as a mean of daily means (which would be
28.333...). Those are Agent errors, not library defects. Historical readback
confirmed all 42,025 pre-existing evidence files remained unchanged.


## Historical continuation and disclosure repair

This section preserves the intermediate checkpoint. The final completion
record below supersedes its pending and paused statuses.

The resumed checkout includes committed Help optimization `db665d899`. Real
public Event authoring then exposed a relationship-lowering defect: `join_on`
stores exact Dimension refs, but Analysis treated them as physical column names
and compared them directly with Entity primary keys. Analysis now resolves each
ref to its exact endpoint column. Internal fixtures use valid semantic refs,
and a public-authoring regression covers both Metric and Event execution.

Before the duration-preview correction below, candidate
`82f6f8a1c22aa042a69214a99efb3876f26ca5b0e0b14f95cb826d3c712a4de3`
passed 4,865 default tests plus lint/type/API documentation, 895 mapped Runtime
cases, a separate captured-binding cold test, 29 release tests, Runtime examples
and bilingual site verification/build. The isolated wheel passed 378 contract
tests, five Runtime examples and three-process production/continuation/recovery
with Run counts 1/2/2; all 328 package files matched the source. These remain
bound to their recorded candidate, not to later edits.

The initial CLI harness manually supplied only the analysis skill and used
`--bare`; it did not register native Marivo skills and loaded an unrelated user
plugin. The corrected harness isolates CLI configuration, preserves the user's
provider/model environment without recording credentials, installs byte-pinned
`marivo-analysis` and `marivo-semantic` in each project's native skill directory,
and enables the Skill tool. Init records confirm both skills, actual analysis
skill invocation and no user plugins. The configured model remains
`zai-messages/glm-5.3-flash`. Prior manual-skill results keep their numerical
scope; native activation is never claimed retroactively. Authored fixtures and
installed skill files are checked against dispatch-time bytes after execution.

| Journey | Current independently established outcome |
| --- | --- |
| A | Native producer arithmetic and six Artifacts verified; two execution-key hits create no Run. The saved cold script embeds an old Artifact id, so delivery repair remains pending. Earlier current-candidate closure programs cover selection order and four ranking tie policies. |
| B | Exact replay with one checkpoint, source-offline correlation/outlier branches and cold recovery; Run counts 1/1/3/3. Original Agent used manually supplied skill guidance. |
| C | All four attribution methods independently reconciled over source and retained joint/hierarchy paths, with actual partition/retained-state/player-limit failures and repairs. Twenty-five attribution outputs were checked; retained three-process Run counts are 8/20/20. Fresh native workflow remains queued. |
| D | Native Skill journey accepted across Metric, Event, selected Population and Metric return. Disabling the Event source permits a new Metric continuation; full source removal permits cold recovery. Run counts 5/6/6. |
| E | Native producer and controller invocation of its exact case functions verify history and all reducers. With only history materialized before source removal, six new reducers succeed; Run counts 1/7/7 and origin connections 1/0/0. Saved identity handoff/cold delivery defects remain separate. |
| F-N | Fresh isolated fixtures and native skills prepared; semantic loading preflights pass. Real-Agent delivery and independent obligation acceptance remain pending. N has a copied retained Store with 24 readable Artifacts and preserved generation fixtures. |

A bounded E delivery-repair context made 30 tool calls without changing its
three scripts; it was interrupted and preserved. Its exploration helped expose
a real presentation defect: `show()` displayed Lifecycle duration values as
unlabeled microsecond numbers, while `to_pandas()` returned correct timedeltas.
The preview now names duration columns and explicitly labels their microsecond
unit, preserving fractional dwell values, nulls, row bounds, identity redaction
and output byte limits. Real Event and Lifecycle regressions failed before the
fix and pass afterward; the existing selected-preview-policy check also passes.
This is a presentation correction, not a numerical or storage migration.

The serial dispatcher is paused during this candidate change. Affected
verification and final candidate gates must run before declaring closure.
`journey-matrix.json`, `native-queue-status.json` and
`progress-native-acceptance.json` retain the detailed current evidence state.
No commit, push, release, tag or publication has been performed.

## Final completion: 2026-09-15

All 15 independent native Claude CLI journeys (A-N, including G2) and all
81 numbered requirements are accepted. The owning specification and the
machine-readable matrix match exactly. Each accepted context has a successful
native terminal record, sealed delivery, independently verified runtime
evidence and explicit obligation references. Both packaged Marivo skills were
registered and byte-pinned in the isolated native environments. The configured
model remained `zai-messages/glm-5.3-flash`; native plugin initialization was
empty. Failed attempts remain preserved and are not counted as successes.

The final runtime-tested candidate is
`c8fb1657a6a0821f069225cab63b44ed72c10f3129a5ef8510be7f5318e25d78`,
at HEAD `db665d899c35c85c4331ef064b9b920f9993a6d5` plus the inventoried worktree
changes. Earlier producer identities and candidate hashes retain their actual
execution provenance. The final shared repair was requalified by 175 retained
Artifacts across the other 14 accepted journeys, using fresh source-forbidden
processes with unchanged Store state, exact rows and healthy integrity axes.
F was independently executed and replayed on this final candidate.

| Final gate | Verified outcome |
| --- | --- |
| `make check-agent` | Lint, typing, API documentation and 4,869 default tests passed |
| Mapped 9b/9c Runtime suite | 904 cases passed; all 12 economics outcomes and five unsupported native-backend rejections mapped to actual test receipts |
| Real object-storage suite | Seven cases passed against the owned versioned MinIO service |
| `make release-test` | 29 tests passed; installed wheel passed 378 contract cases, five Runtime examples and three fresh production/continuation/cold processes |
| Installed source identity | All 328 package files matched the source bytes; Run counts 1/2/2 and no source statements during cold recovery |
| Public documentation | Required default example cases covered by the broad gate; three explicit Runtime examples passed; API build, 343 bilingual content checks and site build passed |
| Preservation | All 42,025 pre-existing evidence files unchanged; all 15 accepted native terminals and 141 sealed primary-delivery files verified |

F's final source-prefix helper now constructs an admitted non-Entity daily
comparison followed by rank. It executed successfully in both storage layouts.
Exact sealed producer, continuation and cold scripts were then replayed in
pristine local and object projects: each produced 10 Artifacts containing 128
independently checked rows, with Run counts 9/10/10 and source entries 10/0/0.
The first retained continuation created a new Run with its source unavailable.
Each layout contained exactly 24 authorized Parquet tables (10 roots and 14
required parts); the object layout had the matching 48 immutable object
versions and no extra publication. Separate guarded controls proved forced
pandas locality, combined decoded-input bounds, exact domain rejection, and
actual compile/runtime prefix failures with no pandas retry or partial output.

This exposed a private retained-part schema comparison defect: Arrow schema
serialization after native Parquet attachment could differ from the physical
Parquet schema despite valid field types. Consumed-part admission now verifies
the physical schema against its receipt while retaining type, count and
non-null validation. Local/object positive and forged-schema negative
regressions, followed by the full gates above, passed. The canonical primary
Dataset schema contract did not change.

The final native F journal is accepted with explicit assessor qualifications:
its current recovery identities resolve to the correct Stores, but the local
JSON's unused cumulative debug ledger retains nine object Artifact and nine
object Run identifiers after a native working-directory mistake. Clean exact
replays, rather than that ledger or the journal's blanket isolation sentence,
own fresh production and physical-inventory proof. The first prefix-control
attempt also used an incorrect project-wide zero-Run assertion; its failure
was preserved, the controller count was corrected, and both real injections
were rerun successfully. Neither is presented as a product failure.

The six basic external-adapter probes remain explicitly historical 9b
connectivity evidence. All 37 datasource files, `pyproject.toml`, and the tested
Ibis/DuckDB versions are unchanged. Current complex native execution is claimed
only for registered DuckDB paths; the other five typed Dataset-root rejections
were rerun on the final candidate. No new complex native adapter support is
inferred from connectivity. Distribution testing used the verified wheel
version `0.5.3.dev0`; editable metadata reported `0.5.5.dev0`. Exact package
bytes, rather than that pre-existing metadata discrepancy, establish tested
source identity. No version or release changes were made for this acceptance.

The final evidence entry point is
[`FINAL-ACCEPTANCE.md`](../../../evidence/slice-9d/2026-09-13-real-agent/FINAL-ACCEPTANCE.md).
It links the complete requirement matrix, native terminal audit, current
runtime gates, installed distribution, F controls and readback, journal
qualifications and preservation receipts. Only this completion record and the
parent plan's status text changed after the frozen executable gates. The
`final-status-closeout/verification.json` bridge records the exact documentation
delta and final whitespace check without relabeling historical executions.

Slice 9d is complete. No commit, push, tag, release, deployment or publication
was performed; those actions require their own authorized workflow.
