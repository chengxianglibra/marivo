# AnalysisFrame implementation progress

Date: 2026-10-19

Status: S0 complete; S1 plans/development in progress. This record tracks implementation, not release or
provider qualification. The public API remains unchanged until S7.

## Authority and baseline

- User-authorized goal: implement S0 through S8 of
  [the implementation plan](analysis-frame-implementation-plan.md), including
  changes to both packaged skills. The user's subsequent instruction authorizes
  a local commit after each subtask passes acceptance. Push and package
  publication remain unauthorized. `AGENTS.md` is outside the edit scope.
- Code baseline: `acfc03617afd59be246b3fb7f025b905bf6b5024`.
- Target: [AnalysisFrame design](query-result-reuse.md), Git blob
  `e2da1c58437ff5c2882242efde3f105b44e68fa4`.
- Implementation plan blob:
  `f887e4b1984d4949b52861de4e05f7eb19f7c507`.
- During S0, an external actor advanced HEAD to
  `ab1c39b31ee060b66a3786926566a3ef8ed56cba` (`docs: plan AnalysisFrame
  implementation`), committing the four original documentation changes. The
  target/plan hashes and runtime source are unchanged; the coordinator did not
  create that external commit. Future candidates use this documentation-inclusive HEAD.
- Original worktree: modified `docs/specs/analysis/README.md`; untracked
  `analysis-frame-implementation-plan.md`, `query-result-reuse.md`, and
  `attribute-design.md` in this directory. Preserve these original changes.
- The user explicitly chooses the target proposal for implementation. Current
  contracts describe the starting behavior; their conflicting public contracts
  change together at S7. Attribute remains a separate future proposal.

## Team and task protocol

- Coordinator: primary agent; owns this record, task dependencies, integration,
  evidence, and final acceptance.
- Exploration: `gpt-6-luna`, reasoning `max`.
- Development: `gpt-6.1-sol`, reasoning `high`.
- Independent review: `gpt-6-astra`, reasoning `high`.
- Every task records a concrete plan, owned files, contract/acceptance IDs, and
  checks before development. Workers share the checkout, preserve others'
  changes, and coordinate overlaps before editing.
- Each task has at most two review rounds. Fix every P0/P1/P2 finding; preserve
  original findings and failures. After round two, unresolved findings block
  completion rather than starting a third review. Coordinator verifies fixes
  and test evidence. Reviewers do not author the implementation they review.
- After acceptance, commit only the accepted task paths/hunks with normal
  hooks and substantive agent attribution. The coordinator stages/commits;
  workers pause file writes around hook stash/restore. Keep ongoing peers'
  changes intact. No push.
- Raw command output and detailed exploration/review records are retained under
  `evidence/analysis-frame/` (ignored local evidence); durable summaries and
  exact commands/results belong here.

## Stage queue

| Stage | Plan and exit | Acceptance | Status | Reviews |
| --- | --- | --- | --- | --- |
| S0 | Freeze target; trace producers/consumers; pin signatures, axis fields, retirement and error order | A01-A24 ownership | Complete: no review findings | 1/2 |
| S1 | Closed structure/carriers, name paths, precise input binding, local service split | A01/A02/A14/A17 | Development: two disjoint work packages | 0/2 |
| S2 | Shared evaluation without publication; fixed leaves; ordinary operators and memory result extraction | A03-A05/A13/A14/A16/A17/A19-A21 | Pending S1 | 0/2 |
| S3 | Store 12, lossless output/attachments, stored reader, audit and atomic publication | A02/A03/A14-A23 | Pending S2 | 0/2 |
| S4 | Full-axis alignment/comparison, independent presence and deterministic order | A06-A08/A11/A19 | Pending S2; exit needs S3 | 0/2 |
| S5 | Frozen calendars, partial nominal matches and single-level paired coordinates | A07-A11/A14/A15/A18/A19 | Pending S3/S4 | 0/2 |
| S6 | Explicit Population bridge, fixed versus lazy selection, complete joint keys | A04/A12/A14/A15 | Pending S2; exit needs S3/S5 | 0/2 |
| S7 | One public API; consumer-traced retirement; Help, errors, CLI, docs, skills and cutover notes | A01/A13/A16/A23/A24 | Pending S1-S6 | 0/2 |
| S8 | Freeze candidate; independent business witnesses; unrestricted broad gate and affected Runtime/slow checks | A01-A24 | Pending S7 | 0/2 |

## S0 plan

1. Read the complete target and stage plan; record hashes and original changes.
2. Explore three disjoint responsibilities: core structure/operations,
   evaluation/persistence, and disclosure/retirement. Exploration is read-only.
3. Integrate exact file/function consumers, target signature choices, closed
   structure variants, FrameAxis fields, and required error priority into an S0
   contract/inventory record. Assign each acceptance ID to its implementation
   stage and owning tests; do not invent temporary public entry points.
4. Obtain independent review of S0, repair P2+ findings, then dispatch S1
   development with explicit file ownership and recorded plans.

Success: every target clause has a task/test owner, no unresolved target
contradiction, and the baseline remains distinguishable from implementation.

S0 development result: completed
[the contract/inventory](analysis-frame-implementation-contract.md). All three
exploration reports are saved under `evidence/analysis-frame/s0-*.md`.
Concrete choices freeze FrameAxis `token/name/role/key_names`, preserved source
signatures and private `_Statistic`, error ordering, consumer-specific
retirement and execution-scoped source cleanup. Current source reservations
have no producer of the legacy control-close recovery variants. The stale
maintainer test path is replaced in this task's commands by the existing
`tests/packaging/test_skills.py` owner. S0 candidate: HEAD `ab1c39b31e` plus
the two new implementation record documents. `git diff --check` passed.

S0 review round 1 (`gpt-6-astra`, high): **No findings**. Report:
`evidence/analysis-frame/s0-review-1.md`. Independently verified preserved
signatures, field choices, error ordering, test owners, scoped retirement and
current cleanup producers. Static review only; S0 is complete, not runtime
acceptance. No second S0 review is required.

## S1 work packages

- Structure/identity developer owns concrete/pending structure, name paths,
  private Frame/carrier and FrameAxis/Column binding plus focused structure
  tests. It does not own Session/runtime assembly or public disclosure yet.
  Its accepted plan is `evidence/analysis-frame/s1-structure-plan.md`:
  reuse concrete DataSchema, use a distinct pending schema form, current
  immutable name/presence paths, closed private carriers, exact handle IDs and
  dependency retention. Add no lifecycle/operator stubs. Calendar/paired
  descriptors stay closed; full codec/mapping validation remains S3/S5.
- Local-service developer owns `materialization/frame_services.py`,
  `materialization/admission.py`, limited Session assembly if needed, and
  `tests/analysis/session/test_frame_services.py`. Its plan is
  `evidence/analysis-frame/s1-services-plan.md`: frozen Store+Session service,
  original Store instance, actual local reads and exact ownership; no resolver,
  production Session, Catalog, source callback or event hook. Current runtime
  call shapes delegate local reads. S3 adds real publication rather than a
  placeholder method.
- The service plan was narrowed before implementation: existing DatasetRuntime
  remains the explicit source-capable owner. A forwarding SourceServices
  wrapper has no current consumer and is omitted. LocalFrameServices never
  points back to DatasetRuntime.
- Both developers are `gpt-6.1-sol`, high. They coordinate the shared service
  interface before edits. One combined S1 review has at most two rounds;
  development checks do not count as independent review.

S1 local-service development is frozen pending combined review. Result:
`evidence/analysis-frame/s1-services-result.md`. The new service has only
Store/Session fields; runtime/Session delegate actual local reads/history.
Focused command covering service tests, Query/Artifact boundary and Session
graph passed **40 tests** (3.94 seconds); typing passed for three source files,
lint/format/import checks passed for four files. First-run peer-incomplete
imports/types and the developer's incorrect flat-export/missing-file error
expectations were fixed; raw failures remain in the named evidence logs.
No full A14/A17 or S1 completion claim yet; the structure package is still
being implemented. Combined S1 default tests and independent review remain.

## Evidence and limitations

- Initial worktree and target hashes captured before runtime implementation.
- S0 baseline command: `make test
  TESTS='tests/analysis/session/test_query_artifact_boundary.py
  tests/analysis/materialization/test_query_results.py'` passed: 58 tests,
  41 existing warnings, 8.84 seconds. Raw output:
  `evidence/analysis-frame/s0-baseline-tests.log`. This is a narrow baseline,
  not the final broad gate or AnalysisFrame evidence.
- Full `make check-agent`, affected Runtime, slow, and release checks have not
  run. Full release acceptance is outside this implementation scope.
- S1 environment planning: TCP probes for the configured dedicated PostgreSQL
  (15432), MySQL (23306), Trino (18080) and ClickHouse (18123) endpoints found
  all four unavailable. This is environment availability only, not a product
  failure or provider qualification. Provision only actually affected isolated
  profiles when their Runtime checks are ready; do not replace required
  provider evidence with skips or start unrelated services.
- No final implementation or cutover claim is made by this progress record.
