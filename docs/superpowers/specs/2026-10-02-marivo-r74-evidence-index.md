# R7.4 funnel, allocation and nonempty Findings evidence

Baseline: `panda`, `01cdbe3571386bf3544602281681c03d0e0f5ab0`, clean at entry.
Date: 2026-10-02. Status: bounded local R7.4 implementation and validation complete.
Work remains uncommitted. This record grants only the bounded local R7.4 scope.

## Execution and retained-state owners

- `LogicalJourneyResult.funnel(axes=())` and its Materialized counterpart consume
  the original canonical first-per-subject assignment. Paired FunnelResult and
  FunnelComparisonResult expose receiver-owned handles and exact `read`; allocation
  reuses the existing AttributionResult family. Every-start and foreign handles
  reject. `funnel_loss_rate(step=...)` binds an exact noninitial retained step.
- `funnel.entry_axes`, `funnel.reduce`, `funnel.compare`, `funnel.read` and
  `funnel_ratio_mix` join the existing method registry, graph, Runtime and Store 7.
  Source preflight and Ibis preparation capture every eligible start's historical
  entry coordinates before local matching. Full paths, version bindings, actual
  Null and complete tuples survive retention. The consumer uses the actual assigned
  start; it never rematches to obtain a different source route.
- Seven checked int64 counts reproduce the ungrouped target. Empty ungrouped
  inputs retain dense steps. Three rates finish exact Fraction components once;
  initial loss and zero denominators retain distinct Undefined reasons. Comparison
  checks exact pattern, policy, Subject, explicit population, Event/axis definitions,
  time basis, cohort/follow-up lengths and complete coverage. Complete outer pairing
  retains missing-side presence and justifies zero counts without inventing rates.
- Ratio-mix loss and denominator terms use the frozen Fraction formulas. Joint and
  every hierarchy resolution independently reconcile both allocated sides and
  the original overall target. Top-K uses combined resolved entries from both
  endpoints and one mapping; typed Other, active/other masks, deterministic ranks,
  zero-delta/pool share reasons and per-term rounding bounds survive views.
- Logical axis expansion has an explicit dependency on the original comparison
  and the same assignment. Fixed inputs require retained axes and components.
  Filtering keeps original reconciliation scope and removes complete-partition
  continuation. Owned reads and ranking preserve original component authority;
  exact-step reads reject omitted or additional steps during exchange validation.
- Canonical assignment validation, retained coverage reconstruction and component
  reproduction run before returning rows. The existing graph DAG, Store 7,
  continuation/execution-key and method/state/part v1 envelopes remain unchanged.

## Placement assessment and physical qualification

R7.3 matching is a registered local consumer of governed Ibis captures. A native
funnel aggregation could reduce a native assignment, but this graph has no qualified
native assignment producer with the same proved order and predecessor-interval
coverage. The legacy SQL/array matcher is therefore not reused or rerun. Uploading
the local assignment to source would violate the accepted route. The explicit
R7.4 source route is `ibis_python` after Ibis preparation, and fixed/cold consumers
use `artifact_python`. Native source funnel and remote backends are unqualified.

The [qualification record](2026-10-02-marivo-r74-qualification.json) preserves all
810 immutable P07/P08/P09 requirement IDs from the R7.1 snapshot. This invocation
exercises K22 (single int64 Subject and occurrence keys), T01 (DuckDB native table,
UTC/us) and T07 (local Parquet, UTC/us), across source/fixed/cold: 18 cells. Snapshot
and validity entry axes are tested in both forms, including independent cold
continuation. The other 792 cells remain explicitly **unverified**, with no inherited
R7.2/R7.3 pass. This finite qualification does not grant all key/time profiles, native,
remote, same-wheel or full R7 acceptance. Actual captured-time precision follows the
accepted R7.2 amendment.

## Findings and atomic publication

`graph.funnel_delta_findings@v1` and
`graph.funnel_contribution_findings@v1` produce the existing closed business bodies.
The retained closed Finding policy binds producer/state/extractor/policy versions,
ordered capture or Artifact input identities, and eligible/emitted/truncated counts.
Both policies use cap=1000 with exact magnitude/full typed-key ordering. Finding
identity includes Artifact, definition/scope, coordinates and body digest. Public
bodies disclose no raw Subject or occurrence identities.

Artifact, Evidence, Findings and succeeded terminal publish in the existing Store
transaction. Initial publication, nonempty Artifact lookup, public digest/page/item
read, cold recovery and exact hit validate the full collection, body, identity,
version/count/digest/input bindings and receipts. Nonproducer artifacts retain the
explicit `graph.no_findings@v1` zero policy; public reads verify receipts, and an
injected nonempty collection rejects. No eligible rows keeps the producer extractor
authority. Session Artifact summaries use the same validated count/set digest.

Independent producer/continue/recover processes serialize bodies, page and item
reads, exact-hit identity, and snapshot/validity axis results. Cold phases prohibit
DuckDB connections, source sessions and current Semantic loading. Fixed consumers
use retained components and assignment without origin matching.

## Independent test owners

All new cases live in `tests/test_analysis_funnel_r74.py`, using the shared governed
fixture and isolated subprocess worker rather than a parallel executor.

| Requirement | Executable evidence |
| --- | --- |
| V07 | Independently expected assignment counts, Unknown reach, dense empty steps, int64 overflow and counts beyond float64 integer precision; table/Parquet public counts, owned reads, foreign handles, initial/exact-step rejection, period/pattern/population/follow-up/completeness checks and every-start rejection |
| V08 | Independent `7/17 - 8/20 = 1/85` Fraction oracle; joint/hierarchy, asymmetric combined Top-K, actual Null versus typed Other, loss/denominator terms, side reconciliation, outer missing coordinates, zero total/pools, rank/table, logical axis expansion, fixed missing-axis rejection and filtered scope |
| A09 funnel / V13 | Public table and local Parquet funnel -> compare -> allocation -> owned relation/rank/table; source mutation changes reevaluation while fixed components remain stable; trace proves every source batch precedes local matching/reduction |
| Historical axes | Snapshot and validity entry values differ between periods, with real Null and literal Other; all eligible starts are captured before assignment; both source forms retain full paths/version scope and cold fixed continuations |
| V14 | Artifact/Evidence/Finding/terminal/before-commit hooks, extractor exception, cancellation and common deadline publish no partial artifact/evidence/finding or successful terminal; existing artifacts remain recoverable |
| V15 / F14 | Nonempty compare and allocation bodies, keyset pages and individual lookup; 1003 eligible -> 1000 emitted + 3 truncated; missing/body/identity/ordinal/count/digest/version corruption, cross-Artifact swaps, input/producer changes and receipt tampering reject; source-offline independent continuation/recovery/exact hit |
| Disclosure | Four paired exports, precise types/docstrings, native Help owners and budgets, dynamic contracts/repairs, unchanged CLI Help bootstrap, identical executed English/Chinese latest examples, API docs and export/drift tests |
| Retirement | Explicit test proves removed modules/registrations/old Event.compare unreachable while shared R8 arithmetic remains; requirement-ID test proves all original cells remain and unexercised cells stay unverified |

## Legacy retirement dispositions

Replacement executable, old consumer unreachable and physical deletion are separate
facts. The public source/fixed replacement and nonempty/fault tests establish the
first. Family registrations, old Event.compare dispatch and extractor consumers are
removed, establishing the second. The following 11 exclusive modules are physically
deleted, establishing the third:

- `domains/funnel_delta.py`, `funnel_attribution.py`, `funnel_registry.py`;
- `domains/event_comparison.py`, `event_attribution.py` and their two `_values.py` modules;
- `compiler/event_comparison.py`, `event_attribution.py`;
- `materialization/event_comparison_codec.py`, `event_comparison_publication.py`.

The exclusive old descriptor's `funnel_evidence` codec field is also removed, with
no alias, dual read or migration fallback. Four exclusive legacy tests/worker files
are removed; independent arithmetic/count checks now belong to the new tests.
`operators/attribute_values.py`, shared row predicates, Event reducers/matcher,
Lifecycle and R8 consumers remain because they have actual owners. The existing
closed FunnelDeltaFindingValueV1 body stays as F14 requires. M02/M04/M06 are not
wholesale deletion-qualified; M05's exclusive private Delta/Attribution chain is.

## Validation receipts

- Shared affected Runtime command: `make runtime-test TESTS='tests/test_analysis_funnel_r74.py tests/test_analysis_journey_matching_r73.py tests/test_analysis_domain_preparation_r72.py tests/test_analysis_recovery_r67.py tests/test_cutover_documentation_examples.py tests/test_analysis_attribution_runtime_r66.py tests/test_analysis_comparison_runtime_r62.py tests/test_analysis_display_r65.py'`:
  **408 passed in 1132.23 seconds**, two workers. The later exact-read, historical
  cold-process and documentation additions are covered by the final R7.4 gate below.
- `make runtime-test TESTS='tests/test_analysis_funnel_r74.py'`:
  **42 passed in 213.77 seconds**, two workers. This covers all cases then present,
  including six independent table/Parquet plain/snapshot/validity process journeys,
  exact-step recovery/ranking, zero-policy injection and executed bilingual examples.
- The cap case was strengthened after that gate to use fixed comparison plus
  independent continuation/recovery processes, all ten pages, item lookup, owned
  reads and exact hit: `make runtime-test TESTS='tests/test_analysis_funnel_r74.py -k finding_cap'`,
  **1 passed in 193.74 seconds**. Counts remain 1003 eligible, 1000 emitted, 3 truncated.
- Final process/summary/empty-policy closeout:
  `make runtime-test TESTS='tests/test_analysis_funnel_r74.py -k "independent_producer or public_compare_nonempty or no_eligible"'`,
  **9 passed in 122.98 seconds**. Origin matching is explicitly poisoned alongside
  source/DuckDB/current Semantic; Session graph summaries reproduce the validated
  Finding count/set digest, and no eligible rows keeps the producer policy/version.
  These repeated checks are not added to the unique Runtime count.
- `make check-agent`: passed, including lint/import contracts, typing of 429 modules,
  **5367 default tests passed, 5 skipped**, and API documentation generation.
- `npm --prefix site run build`: passed, **321 pages**, English/Chinese indexes and
  standard/Chinese install-script outputs verified.
- Final `git diff --check`: passed.

Intermediate failures were repaired: historical path checks accidentally referenced
an as-yet-unprepared leaf; zero-policy lookup redundantly read fixed inputs; exact
read selection was not reproduced during recovery/ranking; disclosure snapshots
retained deleted registrations; API heading underline was short. A documentation
example assertion expected one Finding even though the correct allocation emits
four; the assertion now checks the actual independent fixture result. These earlier
runs are debugging evidence, not passing gates.

AGENTS.md and packaged skills are unchanged. No commit, push, publication, release,
same-wheel gate, release-check, MinIO or remote execution occurred. R7.5-R7.9 and
full R7/R8-R10 qualification remain with their owning phases.
