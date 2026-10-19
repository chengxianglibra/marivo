# AnalysisFrame implementation progress

Date: 2026-10-19

Status: S0 and S1 accepted; S2 planning complete. This record tracks implementation, not release or
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
| S1 | Closed structure/carriers, name paths, precise input binding, local service split | A01/A02/A14/A17 | Accepted; structure commit next | Structure 2/2; services 1/2 |
| S2 | Shared evaluation without publication; fixed leaves; ordinary operators and memory result extraction | A03-A05/A13/A14/A16/A17/A19-A21 | Plans prepared; awaiting S1 structure commit | 0/2 |
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

S0 accepted commit: `f24c008281936c4f54323311277e89f6c043c439`
(`docs: freeze AnalysisFrame implementation contracts`). Normal commit hooks
passed; their Python checks correctly skipped this documentation-only commit.
Pre/post content manifests confirmed all 741 peer source/test files unchanged
across hook stash/restore. Only the two S0 record documents were committed;
S1 source/tests remained in progress. Raw output: `s0-commit.log`.

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
- The user's per-subtask commit rule applies to both work packages: after
  joint S1 acceptance, commit local services first and structure second, with
  each accepted package's exact files and attribution. Freeze worker writes
  and safely shelve only pending peer paths if normal hooks need a consistent
  isolated snapshot; verify content manifests before restoring development.

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

S1 structure development is frozen. Result:
`evidence/analysis-frame/s1-structure-result.md`. Nineteen structure tests and
92 related regression tests passed; six-module typing, lint/format/import
checks and whitespace passed. Development repaired shared array storage and
discarded arbitrary producer attrs at completed-data extraction. Ordinary axes
preserve inert plain UTC anchors; pending completion checks typed metadata.
Exact handles retain only the current input. Raw first failures are retained.

The joint S1 candidate is HEAD `f24c008281` plus both owned source/test packages;
all source/test hashes are frozen in
`evidence/analysis-frame/s1-candidate-source-manifest.json`. Unrestricted default
tests run with `env -u TESTS -u TYPECHECK_TARGETS -u LINT_TARGETS make test`;
independent S1 review round 1 checks both packages against that source. S2/S3
plans are saved as `s2-compiler-plan.md` and `s3-storage-plan.md`; no S2/S3
production edits have started.

S1 review round 1 has independently reproduced a P1 protocol regression:
new dataclass fields changed terminal v1 encoding and strict decoding while
the Store remains generation 11; a baseline-written scalar Artifact no longer
opens. The bounded correction is one frozen existing v1 wire fieldset,
rejecting new Frame metadata from the old codec, until S3 replaces the sole
protocol and generation together. No dual reader or migration is planned.
The reviewer also reproduced nullable identity-key and global paired-token
closure gaps; final finding locations and fixes follow the round-1 report.
Services currently have no reported finding. The source remains frozen while
the reviewer completes this round; no S1 acceptance is claimed yet.

Initial frozen S1 default gate passed: **4,078 tests**, 2,926 warnings,
211.45 seconds, via the unrestricted `make test` command above. Raw output:
`evidence/analysis-frame/s1-default-tests.log`. This does not discharge the
independently reproduced protocol/structure findings. Rerun affected checks
after their correction; final source must own its own broad evidence.

Final round-1 report: `evidence/analysis-frame/s1-review-1.md`. Services:
**No findings**, accepted with focused/default/static evidence; local commit
next. Structure: **three findings** (P1 wire-shape drift, P2 nullable identity
keys, P2 complete descriptor token/attachment-role closure). Fix all before
the second and final structure review. Attachment-role collision was separately
reproduced, not inferred from finite payload validation still owned by S3.
S1 services and structure will be separate accepted-scope commits.

S1 services accepted commit:
`f3bd1308323473b14cc7c6a67bc7b34d18446527` (`refactor: isolate local Frame
services from source runtime`). The isolated accepted snapshot passed 40 tests
(4.12 seconds), then normal formatting/lint/import and full-source mypy commit
hooks passed. Only the four service source/test files and progress ledger were
committed. Pending structure paths were safely shelved/restored; all 741
source/test file hashes exactly match the pre-commit manifest. The verified
temporary stash was removed after restoration. No push.

S1 structure findings are repaired and source is frozen for the second/final
review. The current v1 codec accepts exactly its pre-S1 wire fieldsets and
rejects new Frame metadata; a 1,704-byte fixture generated by the exact
`f24c008281` baseline proves byte-identical roundtrip. Identity components are
nonnullable; ordinary field null categories remain valid. The complete local
descriptor namespace rejects conflicting tokens and attachment role/owner
aliases, allowing reuse only of an identical CalendarDataAxis descriptor.
Expanded owned scope: `terminal_protocol.py`, `test_terminal_v1_schema.py`,
and `terminal_v1_frozen.json`. S3 replaces this one v1 protocol with the new
generation; it must remove the interim frozen-field boundary then.

Repaired focused checks passed **110 tests** (41 existing warnings, 12.54
seconds), seven-module typing and lint/format/import checks. Five initial
repair-test witness failures used noncanonical JSON before reaching their
intended check; corrected to the owning canonical_json, with raw failures
retained in `s1-structure-result.md` and logs. Final source manifest is
`evidence/analysis-frame/s1-final-source-manifest.json`. Unrestricted default
tests rerun as `s1-final-default-tests.log`; final independent review has no
third round. No S2 production changes have started.

S1 final independent review: **No findings**. Report:
`evidence/analysis-frame/s1-review-2.md`. The reviewer independently proved
baseline-write/candidate-read value 7 and candidate-write/baseline-read value 9,
ran 35 focused tests, and cleared all original identity/namespace counterexamples.
All 743 source/test hashes match the final candidate. Final unrestricted
default tests passed **4,094 tests** (2,926 warnings, 202.47 seconds). This
source owns the final S1 default evidence; typing/lint and both reviews are
recorded above. S1 is accepted for its private structure/service scope;
evaluation, complete persisted coordinate codecs and public cutover remain
S2-S8 work. Commit the ten accepted structure source/test/fixture files next.

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
