# AnalysisFrame implementation progress

Date: 2026-10-19

Status: S0-S2 accepted; S2 local commits in progress. This record tracks implementation, not release or
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
- Subsequent user steering requires a fresh subagent for each new subtask;
  never reuse an agent across tasks. Agents already executing S2 finish only
  their current task. The just-dispatched reused S6 explorer was interrupted
  and replaced by the fresh `explore_s6_bridge` (gpt-6-luna max); future
  development/review scopes also receive distinct agents. Repair/review rounds
  within the same task retain its identity and two-round limit.
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
| S1 | Closed structure/carriers, name paths, precise input binding, local service split | A01/A02/A14/A17 | Complete: both packages committed | Structure 2/2; services 1/2 |
| S2 | Shared evaluation without publication; fixed leaves; ordinary operators and memory result extraction | A03-A05/A13/A14/A16/A17/A19-A21 | Accepted; compiler/lifecycle commits next | Compiler 2/2 clear; lifecycle 2/2 clear |
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

S1 structure accepted commit:
`d6b4707c65aabaf3418aecd926f35ae4255fc5d6` (`feat: establish private
AnalysisFrame structure and exact bindings`). Normal formatting, lint,
import-contract and full-source mypy hooks passed. The exact ten accepted
source/test/fixture files and progress ledger were committed; worktree clean
after verification. No push. Both S1 packages are now complete.

## S2 plan and ownership

Accepted plans precede development: `evidence/analysis-frame/s2-execution-plan.md`
and `s2-lifecycle-plan.md`, informed by `s2-compiler-plan.md` and the S3 storage
plan. Baseline: S1 structure commit `d6b4707c65`.

- Compiler/numeric owner (`develop_frame_services`, gpt-6.1-sol high): pure
  expression algebra and schema inference; one FrameInput and one closed
  FrameCompute stage in the existing QueryPlan; source child plan composition;
  exhaustive demand/lowering/fusion rules; existing scheduler dispatch;
  data-only ordinary kernels and a typed attachment sidecar. Owns compiler,
  scheduling/execution handoff files and new focused plan/execution tests.
- DSL/evaluation owner (`develop_frame_structure`, gpt-6.1-sol high): Frame
  ordinary methods and compute/show/export, pure shared source capture and
  current structure adaptation, explicit source bindings, runless evaluation,
  one source-free completed-result/audit aggregate, and focused lifecycle tests.
  Uses module-private source builders instead of temporary public Session APIs.
- The pure compile tree has source graph leaves, fixed input-ID/schema leaves
  and admitted ordinary operations. Invocation-only source/fixed bindings and
  callbacks never enter completed backing. Source children are composed into
  the same schedule, not executed through a second scheduler.
- Validate full fixed data before projection, Top-0 or consumer checks;
  preserve source checks before stripping execution layout/components. Close
  every source owner before returning independent complete data. Compute never
  acquires writer admission or creates Run, journal or output files.
- FrameCompute is a concrete closed QueryStage variant; do not add another
  optional program arm to LocalCompute. Inference has one owner shared by DSL
  construction and concrete planning. S3 supplies actual stored reads/publish.
- Source facts must be exact and source-free: current OutputEvidence supports
  pure scalar facts without a text-length limit; canonical filter tokens can
  represent scalar source constraints. Membership/Population token fingerprints
  cannot replace actual complete-key scope. Preserve full parameter facts and
  test long filters/keys; do not store Metric formulas or producer graphs.
- Calendar encoding handoff is in `calendar-codec-contract.md`: freeze civil
  day+ labels, offset-bearing subday labels, canonical named keys and original
  finite-axis ordinals. Named descriptors need explicit level before S3.
  Structured grain capture requires a producer fix because the current grid
  and CalendarCoordinate keep display tokens only; decide that exact narrow
  producer change before implementing calendar extraction, not by parsing
  display text. Ordinary work can proceed independently.

Calendar producer choice is now fixed for S2: retain authoritative `grain_spec:
Grain` in BoundTimeGrid and CalendarCoordinate, derive existing grain_token/
grain display properties, update the one grid constructor, two coordinate
producers and direct test fixtures, and replace the existing source consumers'
token parsing with structured fields. The existing definition graph already
encodes TemporalGrain. The lifecycle owner owns this narrow upstream change;
no new registry, side cache or current-model lookup is added. This is not a
change to the frozen accepted S1 candidate. Add named `level` before S3 fixes
the new wire format.

After joint S2 acceptance, commit compiler/execution first, then DSL/evaluation.
Compiler production/tests use the S1 data API plus their own new stage algebra;
they do not depend on the peer's new result/audit aggregate or public DSL.
End-to-end Frame tests belong to the second package. This keeps each accepted
commit independently importable while preserving the two-package ownership.

Success: source/literal/memory compute and ordinary operations satisfy the
assigned A03-A05/A13/A14/A16/A17/A19-A21 witnesses; all required checks and
cleanup precede a result, memory operations do not reread source, and the
completed result has no execution/source objects. Each package plans, develops,
validates and receives at most two independent review rounds; commit each only
after acceptance. Full stored and public cutover success remains S3-S8.

S2 development is in progress. The closed stage union also requires explicit
source-only dispatch in `compiler/queries.py`, `materialization/source_original.py`
and `query_key_fusion.py`; these narrow consumers belong to the compiler owner.
Fixed Frame stages must not enter source fusion. The lifecycle owner reports
91 source-capture/structured-grain regression tests and nine-module typing
passed; this is intermediate evidence, not S2 acceptance. Its current result
aggregate is `CompletedFrameResult(data, metadata)`, with one `FrameAudit` in
metadata and evaluation time derived only from its current record. The source
builders are shared through `dsl/source_capture.py`. Interfaces remain under
development until the joint candidate is frozen.

The store explorer is checking the S3 temporary Query gateway against the
single new Frame protocol before S3 dispatch. This is read-only planning;
no S3 production changes or additional codec are authorized by that task.

S2 source-fact encoding is constrained to gated source-scope nodes: full scalar
filter tokens and complete LiteralPopulation joint-key tuples, with inert
set/project/product/filter and source identity/path facts where actually used.
It has no decoder, replay, Metric formula or output production graph. An
unsupported QueryKeys constraint fails explicitly rather than being replaced
by a fingerprint or traversed into the old graph; S6 owns the actual fixed/lazy
Frame-key bridge and its complete-key facts. Long strings, long membership
lists and composite keys require exact-scope witnesses before S2 acceptance.

Two development boundaries were clarified before final validation. First,
compiler-to-materialization imports are forbidden by the existing import
contract: the compiler owner extracts the existing pure schema classes into
`core/data_schema.py`, with `artifact_data.py` explicitly reexporting those
same class objects. This includes the already-authorized named calendar level;
there is one type authority and no wire-name/behavior change. Second, source
compute retains the existing datasource execution diagnostic logs. Target
section 8.1 forbids persistent Run/Artifact/journal/intermediate-result saves,
not existing source diagnostics. Pure memory/literal paths must create no files;
source tests distinguish diagnostics from forbidden analysis/result state.
No new logging mode or change to `_execution_log.py` is needed.

Intermediate validation after schema extraction: compiler owner reports 152
focused tests, 17-module typing and 19-file lint/import checks passed, with
additional narrow Top-0/check-order cases afterward. Lifecycle owner reports
seven end-to-end source cases passed, including source recompute, offline
fixed derivation, row mean 55 versus original 1000/91, numeric/source-close
failure without publication, complete long-scope facts, general composite
Population and isolated Session GC. An actual nullable source identity is
rejected rather than accepted through relabelled metadata. These reports
remain developer evidence; the joint default gate and independent S2 review
were pending when those reports were produced.

Compiler package is now frozen and its first independent review is running.
Candidate: the exact 19 paths in `s2-compiler-review-1-manifest.json`, manifest
SHA256 `ad2b9a82edfa8406186a8df01617331b5c2724dcd203f034ad4d31bd88e96bf8`,
against S1 commit `d6b4707c65`. Final developer checks are 152 selected
regressions, followed by 27 focused tests after three test-only corruption
witnesses; 17-source typing and 19-file lint/import checks pass. Full report:
`s2-execution-result.md`. Lifecycle development is outside this frozen review
scope; joint default acceptance still waits for both packages.

Compiler first review has a reproduced fixed-input validation issue: a corrupt
unused float column containing positive infinity survives Top-0, projection or
a constant-false filter, despite the finite-float contract. Raw independent
probe: `s2-compiler-review-1-nonfinite.log`. The frozen files remain unchanged
until the first report is complete; this issue blocks acceptance. Lifecycle
final developer checks passed 42 focused tests, 15-source typing and 20-file
lint/import checks, then a pre-freeze self-check found the known ReadFields
wrapper needed a gated source-scope extraction to preserve its Population
constraint. That narrow fix and an actual read-scope witness are in progress.
The joint default gate will follow required fixes on the final source.

Lifecycle source is now frozen: `s2-lifecycle-frozen.sha256` records the exact
20 owned files; report `s2-lifecycle-result.md`. Final developer evidence is
43 tests (5.73 seconds), 15-source typing and 20-file lint/import checks. It
adds the second original-quantity witness (bucket distinct counts sum to 4,
original distinct is 3), zero-axis Population empty/singleton, complete actual
read scope and frozen-calendar cases. Fresh independent agent
`review_s2_lifecycle` (gpt-6-astra high) is running its first review. No source
or test writes are permitted until review feedback is assigned to its owner.

Compiler review round 1 is complete: one P2, with a direct construction probe
for both infinity signs (no private mutation). Isolated baseline-plus-19
validation passed 27 new tests and 11 frozen-v1 tests; all hashes matched.
Report: `s2-compiler-review-1.md`. The compiler developer is repairing the
shared complete-data boundary within the same S2 task, preserving NA and
covering primary/attachment data before downstream pruning.

Lifecycle review round 1 independently reproduced a pending-card P2: source
filters and local top/where expressions with the same structure cannot be
distinguished except by input ID because captured scope, backing kinds and
pending operation are omitted. Report is still in progress; no lifecycle
source changes begin before it closes. The reviewer passed 76 focused
lifecycle and existing source-original/correspondence regression tests.

Lifecycle first review is complete with two P2 findings: the pending-card gap
and a numeric-failure-plus-source-close-failure path that leaks the raw close
exception through the evaluator's `ran` guard. Both remain no-publication
failures, but require consistent structured errors. All 20 hashes matched;
report `s2-lifecycle-review-1.md`. Its developer is planning/repairing both
within the same task before the second and final independent round.

Finite validation repair uncovered an existing intentional distinction:
legacy transient Query selection permits infinite scores, so globally changing
`normalize_stage_frame` would break that current contract. Keep transient
normalization and add one shared `normalize_complete_frame` owner in
graph_exchange for complete data. Compiler task enforces it for fixed inputs
and after source adaptation, before pruning/transport; lifecycle task wires
CompletedFrameData's primary/attachment construction to that same helper.
This preserves independent compiler-first commits and avoids duplicate finite
logic or unrelated legacy behavior changes. The initial global repair failure
is retained as `s2-execution-repair-tests-after.log`.

Compiler repair candidate is frozen for round 2 (the final independent round):
`s2-execution-repair-manifest.json`, SHA256
`1cf3791a1cbe51ed896b081e1c8eb332eed40d322591ec764768195eebdaa265`.
Only two compiler-owned source files and two focused tests changed from
round 1; the other 15 hashes are unchanged. Developer verification passed
141 focused regressions (21.34 seconds), changed-source typing and
lint/import checks, including the existing intentional transient-inf cases.

Fresh explorer `explore_s6_bridge` completed the S6 same-scheduler/carrier plan
in `s6-bridge-plan.md`, including the exact pre-consumption Deferred carrier
seam. Coordinator clarified that two distinct stored handles with the same
stable input identity must be compatible/share a read, not fail Python object
identity. S6 development has not started. Fresh explorer
`explore_s7_cutover_surface` is preparing the separate public-cutover plan
from the existing S0 consumer inventory; it is read-only.

Compiler final independent review: **No findings**. The original six direct
infinity/pruning counterexamples all reject as MaterializationError; empty,
NA and finite-data positive probes pass. Independent isolated regression ran
109 tests, including legacy transient infinity selection and frozen-v1 golden
shape checks; every one of the 19 candidate hashes matched. Report:
`s2-compiler-review-2.md`. No third round is permitted or needed. Compiler
acceptance/commit still awaits the joint default gate and lifecycle's actual
constructor handoff review. Lifecycle repairs now pass the three originally
failing card/cleanup cases and seven constructor finite/NA cases, with final
focused/type verification underway before its second review.

Both repaired packages are frozen. Joint final source manifest covers 757
source/test files: `s2-joint-final-source-manifest.json`, SHA256
`474192f4c7342a96d7ca3ad16ccc083e9cfab79288fb80c91bcaec2a46af8f60`.
Full-source typing passed all 312 modules (`s2-joint-typecheck.log`).
Unrestricted default `make test` is running on that source; lifecycle's second
and final independent review runs concurrently against its new 20-file
manifest. Its final repair developer evidence is 119 actual related tests
(not the initial estimated 143), followed by two narrow delta checks,
15-source typing and 20-file lint/import checks. Fresh developer
`develop_s4_ordinary` may only prepare its S4 plan while this candidate is
frozen; no S4 source changes are dispatched yet.

Lifecycle final independent review: **No findings**; both original P2 findings
are resolved. Independent actual DuckDB source infinity probes reject full,
Top-0, projection and false-filter variants; nullable data and isolation pass.
All 20 hashes match, with 52 focused tests passed (8.79 seconds). Report:
`s2-lifecycle-review-2.md`. Both S2 tasks have used their two review rounds.

The first joint unrestricted default run finished **4,163 passed / 1 failed**
(2,927 warnings, 149.68 seconds). The sole failure is an outdated test spy in
`test_metric_observation_resolution.py`: it patches the old query-module
location of `consume_metric_in_state`, now correctly owned by shared
`source_capture.py`. The lifecycle owner is authorized to retarget only that
spy while preserving resolution-count, contribution and numerical assertions.
No production change or compatibility export is needed. Coordinator will
inspect the test-only diff and rerun default tests; this is integration
verification, not a third independent review round. The original failure log
remains intact as `s2-joint-default-tests.log`.

S2 final acceptance: coordinator inspected the exact three spy-owner changes;
all original assertions remain. The 20 reviewed lifecycle paths and all
runtime sources are unchanged. The final 757-file manifest is
`s2-accepted-source-manifest.json`, SHA256
`d2c549587d6b195da4d8c26803cd9214ee03f50203531c20f3793cdaa110c506`.
Final unrestricted default tests passed **4,164 tests** (2,927 warnings,
137.80 seconds), raw `s2-final-default-tests.log`. Full 312-module typing
remains valid for this exact source. Both final independent reviews are clear.
No external Runtime/release or public-cutover claim is made. Commit the
accepted 19-file compiler package first, then the 21-file lifecycle package;
there are no unowned worktree changes. Preserve the peer package during
normal hooks and verify all 757 content hashes after restoration.

S2 compiler accepted commit:
`48867bfb3b2498fe5e965eae95bdee56c4f4ccea` (`feat: compile Frame data
plans through the existing scheduler`). All normal formatting/lint/import and
full-source mypy hooks passed on the independently importable compiler-only
tree. Its exact 19 source/test files plus the two owning records were
committed. The temporary lifecycle-only stash was applied, all 757 accepted
source/test hashes matched, and only that verified temporary stash was dropped.
Both preexisting user stashes remain unchanged. The lifecycle package is
accepted and restored, ready for its separate commit.

## Post-S2 sequencing decision

The read-only `s3-gateway-handoff.md` confirms that legacy identity-relation
correspondence has no lossless representation in the target calendar-only
paired axis. Keeping every legacy Query route across a Store 12 switch would
also require temporary durable audit variants and substantial adapter code
whose sole consumer is retired at S7. Do not implement that transitional
legacy evaluator, second protocol, or permanent legacy audit vocabulary.

Use the implementation plan's permitted early S4/S6 data foundations after
S2, and develop the source-free calendar producer/matcher against the frozen
coordinate contract when its ordinary pairing prerequisite is ready. Accept
and commit these explicitly scoped private subtasks; their stage exits still
await Store/cold witnesses. Coordinate the Store 12 active writer switch with
the S7 runtime entry-point and unsupported-receiver retirement in one reviewed
candidate. The final publisher consumes only the completed S2 result. Surface
documentation and the remaining persistent/cold acceptance follow the same
final contract. No claim of full S3-S6 acceptance precedes those witnesses.

This sequencing preserves one final protocol and avoids a publicly published
intermediate API. It changes implementation order, not the target or A01-A24
requirements. S3 remains planned, not accepted. The ordinary S4 plan is being
prepared before its development dispatch.

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
