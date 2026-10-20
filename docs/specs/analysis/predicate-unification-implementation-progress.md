# Predicate unification implementation progress

Status: implemented; U01–U08 accepted, 2026-10-20. Local broad gate and focused
PostgreSQL Runtime evidence are complete; package/release acceptance was not run.

Owning proposal: [predicate-unification-design.md](predicate-unification-design.md).
Starting HEAD: `9f2d92508e1a569cb2de5c3504b3a75581e5f0e5`.

## Scope and working assumptions

Implement S1–S4 and acceptance U01–U08 from the proposal. Native Deferred
conditions retain boolean structure and complete dependencies. Field and
membership leaves share one private condition representation. Population.where
remains membership-only; AnalysisFrame.where consumes its own boolean Column.
No storage format, provider capability, release, or unrelated proposal expansion
is authorized. Commit and push actions are handled separately from implementation
acceptance. AGENTS.md and packaged skills are read-only unless separately authorized.

The initial worktree contains unrelated edits to analysis/README.md and untracked
attribute, correlation, observe redesign and public-surface documents. Preserve
them. The owning predicate proposal is also initially untracked and is input to
this work, not proof of executable behavior.

## Work ownership and dependencies

| Stage | Owner | Owned scope | Dependencies | Deliverable | Status |
| --- | --- | --- | --- | --- | --- |
| S1 | capture agent | definition records/graph, semantic Deferred consumption, Population DSL, source capture and Frame input dependency capture | proposal | Unified inert condition and native membership capture; complete input closure | Implemented; focused checks passed |
| S2 | execution agent | analysis binding/compiler/materialization, Session entrypoints and member execution | S1 interface | Full boolean evaluation, strict check domains, no membership prefilter for OR | Complete |
| S3 | Column agent | Column/Frame, result filter cleanup and focused tests | none | Ordinary boolean Column; exact-input and nullable selection preserved | Implemented; focused checks passed |
| S4 | coordinator | Help/export/API/site/current specs, integration acceptance and cleanup | S1–S3 | Public disclosure cutover and U01–U08 evidence | Complete |
| R1/R2 | independent reviewers | complementary read-only execution and Column/disclosure scopes | integrated implementation | R1's single P2 closed; R2 has no unresolved P0–P2 | Complete, 2/2 rounds |

Agents share the worktree, preserve others' edits, and coordinate interface
changes before dependent migrations. Each stage reports modified paths, checks,
failures and limitations. The coordinator owns this ledger.

## Acceptance ledger

| ID | Required evidence | Status | Evidence |
| --- | --- | --- | --- |
| U01 | Public mixed conditions across observe/Metric.where/members; membership-only Population.where; grammar repairs | Accepted | Six-row public consumers; reverse/nested/repeated where; native grammar and consumer repair tests; graph membership-method checks |
| U02 | Nullable truth table, null-safe correlated tuples and complete composite identity | Accepted | AND/OR/NOT and NOT-AND identity/amount oracles; correlated field Null and composite tenant identity checks |
| U03 | Empty/unit domains, relative complement, empty aggregate policy and retained output coordinates | Accepted | Empty/unit/complement/output-domain public test; existing Frame/Population bridge witnesses |
| U04 | Static validation of all leaves, strict field checks over original input and preserved producer errors | Accepted | Strict AND/OR divide checks; guarded numeric ifelse; eager scalar condition reads and selected-only output lookups; constant producer failure |
| U05 | Independent roots, pairing/allocation, time authority and fixed Population.count domain | Accepted | Independent-root total 160, weighted pair result 40, literal domain count 4 versus source count 3; existing allocation/window/grain tests |
| U06 | Public population_from dependencies, exact strong Frame inputs, ownership, fixed/pending keys | Accepted | Persisted versus pending selection after source mutation: 60 versus 160; declared-Metric strong inputs, exact descriptors/ownership and constant failing producers |
| U07 | Ordinary boolean Column, exact Frame identity, nullable where, order/axes/presence and stored input | Accepted | test_boolean_column.py: plain constructors, True-only selection, distinct NA/presence and source-offline stored filtering |
| U08 | Source-free construction/show, canonical Help/exports, retired paths removed, EN/ZH examples | Accepted | Metadata-only construction/show; canonical topic and retired-page/export tests; bounded captured scope; API/site checks and R2 disclosure review |

The new public numeric oracles live in
[test_predicate_unification.py](../../../tests/analysis/materialization/test_predicate_unification.py).
Source-free capture, output Column and Help checks extend their owning suites.
All affected default tests ran together on the final freeze, in addition to the
focused evidence below.

## Validation and review record

- Initial inspection: proposal baseline matches current HEAD; worktree state recorded above.
- Validation starts with focused behavior, lint and typing. Final unified local
  acceptance requires unrestricted `make check-agent`, affected API/site checks
  and final source/diff inspection. Runtime lowering changes require relevant
  already-supported provider witnesses, recorded separately from local evidence.
- S1: 99 focused tests passed; typing passed for 21 source files and lint for
  23 targets. S3: 118 focused tests passed, then 2 final U07 tests passed.
- Coordinator public witnesses and predicate disclosure: 16 passed in 15.39s
  (`public-final.log`). Covers U01–U06 with independent literal oracles, plus
  source grammar/disclosure. Existing focused tests own remaining lifecycle,
  allocation, time and exact-input variants. A final NOT-AND oracle additionally
  distinguishes NA AND True from False AND True, with O2/O4/O5/O6 and amount 95;
  all five public forms passed in `coordinator-gate-repairs.log` and the final gate.
- Unrestricted `make check-agent`: exit 0; lint, formatting and import contracts
  passed; typing passed; 4,800 default tests passed in 203.45s; API docs built
  (`check-agent-final.log`). Slow, Runtime and release tests are excluded by
  the repository's default gate and are not counted as broad local evidence.
- Site content: 381 required files verified (`site-content.log`). Full site/API
  build passed (`site-build-repaired.log`), including 44 latest usage pages,
  343 internal route/anchor targets and 308 original documentation routes.
- PostgreSQL table Runtime: nullable mixed conditions with fixed composite
  identity Frame keys passed for observe, Metric.where and members. MySQL,
  Trino and ClickHouse were skipped; no new qualification is claimed for them.
  Final focused command:
  `MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/analysis/numeric/test_remote_numeric_query.py::test_mixed_predicate_keeps_null_rows_and_complete_identity_membership' RUNTIME_WORKERS=1`.
  Result: 1 passed, 3 skipped, 1 deselected (`execution-postgres-mixed.log`).
- No package or release acceptance was run. Packaged skills were inspected;
  their workflow boundaries remain consistent and require no edits.
- Formal review: R1 found one P2, the updated Population spec still promising
  retired Predicate.show. Receiver-owned guidance and the V2 validation row
  were repaired. R2 has no findings and no unresolved P0–P2 in either scope.
- Final Python freeze: 59 files, digest
  `42d9c5e5cfb13300d3c070c03205096f91269fc11962fb071e851641143e8bc6`.
  Both R2 reviewers and the coordinator verified every file hash. Execution
  source is unchanged from R1; later production repairs concern bounded display
  and Help wording. Reports, manifests and raw failures are retained together.

## Failures, repairs and blockers

Raw command evidence, review reports and freeze manifests are retained at
`evidence/predicate-unification/`; original working logs remain at
`/tmp/marivo-predicate-unification-evidence/`.

| Failure | Classification and repair | Rerun |
| --- | --- | --- |
| First public run: fixture Measure omitted required additivity | Test declaration error; specify non_additive for divisor | Later public runs passed |
| Unified membership marker absent during planner lowering; common-condition producer/parameter closure omitted | Implementation regressions; retain marker schema and traverse public condition dependencies | Legacy cases passed; public-final 16 passed |
| Nullable tuple oracle compared pandas NA directly to None | Oracle representation error; assert nullable mask and concrete coordinate values | Public-final passed |
| Old internal condition shape and retired disclosure text assertions | Stale expectations; update to unified native condition/current Help text | S1 99 passed; integrated repair 2 passed |
| Guard witness used a boolean ifelse outside the existing admitted field contract | Test construction error; use existing numeric ifelse followed by comparison | Public-final passed |
| Output-only versioned scalar check ran on unselected contributions | Implementation regression; preserve original domain for condition reads while guarding output/reduction-only checks by the final mask | Legacy explicit-version witness passed; dedicated condition-read witness passed |
| Initial API/site build: docutils unexpected indentation in in_population docstring | Documentation formatting defect; use normal multiline sections | Full API/site build and final broad API stage passed |
| First unrestricted gate: 11 failed, 4,689 passed | Migrate old private condition fixtures, token positions and retired predicate.show expectations while preserving ownership/edge/check/value oracles; restore explicit source-vs-Frame Help boundary; repair bounded scope display | Targeted capture 8, coordinator 13 and execution 2 cases passed; final unrestricted gate 4,800 passed |
| Nested Population scope replaced by scope=omitted | Disclosure regression; avoid duplicate field details and truncate within the existing 600-byte fact ceiling while retaining membership-producer traversal | Large-domain/nested/unicode tests and independent mixed-producer show probe passed |
| R1 P2: retired Predicate.show guidance in current Population spec | Replace with receiving Population/AnalysisFrame.show and semantic.predicate | R2 disclosure review closed the finding |

No unresolved blockers remain. The reviewer reproduced the pre-existing
`slot_999` literal-display issue against the exact baseline and recorded it in
`review-r1-execution.md`; it was not introduced or repaired by this implementation.
This does not qualify untested providers, package installation, or release paths.

During implementation, another task committed the initially untracked correlation
design/plan and its README entry as `657334c78d58d3265e5d3be38b2819bd561233ed`.
Those changes are outside this task and were preserved. Final review uses the
current HEAD plus this task's worktree source diff, rather than attributing the
external documentation commit to this implementation.
