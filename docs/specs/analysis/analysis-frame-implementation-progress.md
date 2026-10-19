# AnalysisFrame implementation progress

Date: 2026-10-20

Status: **S0-S8 complete and accepted.** AnalysisFrame is the canonical public
analysis surface, with completed `frame_output.v1` results in Store 12. All
owning P2+ findings are closed within at most two review rounds. All accepted
subtasks are locally committed; the final progress record is committed separately.

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

## Final stage status

| Stage | Accepted result | Reviews |
| --- | --- | --- |
| S0 | Frozen contract, inventories and A01-A24 ownership committed | 1/2 clear |
| S1 | Closed Frame structures and local service boundary committed | Structure 2/2; services 1/2 clear |
| S2 | Existing scheduler evaluation, fixed leaves and no-publication compute committed | Compiler/lifecycle 2/2 clear |
| S3 | Store 12 lossless reader, audit and atomic publication integrated in `716d2081e5b7` | 2/2 clear |
| S4 | Full-axis alignment, comparison, presence and order; stored/cold exit accepted | Ordinary 2/2; stage-exit 1/2 clear |
| S5 | Frozen nominal calendars, partial matching and paired coordinates; stored/cold exit accepted | Foundation 2/2; temporal/stage-exit 1/2 clear |
| S6 | Explicit fixed-key bridge, current source restriction and qualified tuple/null/empty routes accepted | 2/2; real PostgreSQL/MySQL routes clear |
| S7 | Public cutover, retirement, consumers, Help, errors, CLI, EN/ZH docs and both skills committed | Runtime/docs/T1a/T1b 2/2; other owning tasks 1/2 clear |
| S8 | Five independent business witnesses, final broad/slow/fault/Runtime and same-wheel installed acceptance passed | Independent acceptance and repair reviews clear; no unresolved P2+ |

## Final acceptance

- Code commit before this coordinator-only record: `f2be3c5abcdf330858c1322cf178390e8d0c6c22`.
- Final source freeze: `3ba19a97feec53f675d788e6dc750ab04840924c5b6c88fbdc53064f1b625ca4`;
  all 98 production entries and all 299 production Python modules match.
- Execution freeze: `4d652064a4bdc193dd00c07e3db76a664bf4cc80e367a5ef9cea77485ae7fc87`;
  all 1,213 tracked execution files remain unchanged. This coordinator record is
  deliberately excluded from that execution freeze.
- Unrestricted final `make check-agent`: **4,666 passed**, 631 warnings, 184.69s;
  formatting, lint, import contracts, typing and API docs all exit 0.
- Focused final slow/cold: **12 passed**; publication/fault/storage: **161 passed**.
- Final actual Runtime: PostgreSQL/MySQL **22 passed**; Trino **35 passed**;
  ClickHouse **32 passed**. Other-profile skips are recorded separately.
- Fresh independent business acceptance: **5 passed**, including explicit
  names/alignment, calendar/presence, current-row versus source arithmetic,
  fixed Top versus changed source, and guarded cold order/filter/reduce/republish.
- One final wheel: `5cc28c37fc2a62e6791e1892e5caef45b38247ae5cc73b10319f2ac782b92c86`.
  Build, twine and the 301-product-file content contract pass. Base installed
  selection: **20 passed**. The same wheel passes actual installed source
  journeys for **PostgreSQL, MySQL, Trino and ClickHouse**, each with distinct
  producer/offline/cold PIDs, exact saved snapshots/descriptors/oracle and zero
  cold Runs, artifacts or resources added.
- Root independently verifies every final installed receipt, source/wheel hash
  and PID/counter assertion. `evidence/analysis-frame/s8-acceptance-matrix.json`
  closes all A01-A24 items; the local commit ledger records exact accepted scopes,
  attribution and normal hooks. Original failures, fixes and probes remain evidence.
- The dedicated `marivo-multisource` VM is restored to its original OFF state.
  The temporary managed worktree is archived. Its complete ignored evidence is
  preserved in `isolated-storage-full-evidence.tar.gz`, SHA256
  `381e6e60d1622304640ad5bd6be4ec6a6cdcd76cef9e2c455f15253ffcb43e54`.
  Both original user stashes remain unchanged. No push or release was performed.

## Accepted implementation commits

| Task | Commit |
| --- | --- |
| S0 | `f24c00828193` |
| S1 services | `f3bd13083234` |
| S1 structure | `d6b4707c65aa` |
| S2 compiler | `48867bfb3b24` |
| S2 lifecycle | `1870657b0a9c` |
| S4 ordinary | `e677814887d4` |
| S5 calendar | `50663acaf40d` |
| S6 bridge | `9ce4783dc2e7` |
| S3/S7 coherent core | `716d2081e5b7` |
| membership | `220f4284b350` |
| effective-types | `29530e67d620` |
| residual | `acf7f7ffe6b8` |
| exact-keys | `7a87033dd258` |
| exact-mean | `b64430d168bc` |
| helpers | `4e376d25b84f` |
| disclosure | `cc1f29eccdcc` |
| docs-skills | `8e21b814ff44` |
| t1a | `2bed6022be3b` |
| t1b | `a9b54834f01e` |
| t1c | `068342aa7c41` |
| t2a | `bfb439e5a71e` |
| t2b | `8245bd4c0787` |
| t3a | `505a5cb44af7` |
| t3b | `578b67ce3e75` |
| t3c | `1d8ec1a01652` |
| integration | `6c5bba2ed1ef` |
| stage-exit | `cdff78d1ce79` |
| native-overflow | `3d7d9f36ac71` |
| clickhouse-decimal | `3ac03b60ebd3` |
| key-error-consumers | `9542a0f5a9bf` |
| installed-native-oracle | `ffae79aa30dd` |
| installed-surface-identity | `f2be3c5abcdf` |

The chronological entries below preserve plans, failures, review rounds and
intermediate evidence; their pending labels describe those historical candidates.

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

S2 lifecycle accepted commit:
`1870657b0a9cffc4d446bff0441b68bd4bc54978` (`feat: compute AnalysisFrames
without publication`). Exact 21 source/test paths plus progress record were
committed. Normal hooks, including full-source mypy and import contracts,
passed. All 757 accepted content hashes still match; worktree was clean and
both original user stashes unchanged after verification. S2 is complete for
its private source/literal/memory scope, not stored/public-cutover scope.

## S4 ordinary foundation plan and dispatch

Fresh developer `develop_s4_ordinary` (gpt-6.1-sol high) completed and received
coordinator acceptance of `s4-ordinary-implementation-plan.md` before source
edits. Starting point is S2 commit `1870657b0a`. It owns the closed binary
operation/inference and audit additions; Frame compiler/program/local dispatch;
new pure `methods/frame_comparison.py`; private DSL methods and their bounded
metadata/audit wiring; focused ordinary correspondence/plan tests. Exact paths
and proposed interfaces are in the accepted plan. Additional ownership needs
the concrete consumer reason first. This agent will not receive S5/S6 tasks.

Deliver ordinary field/identity complete explicit maps and zero-axis inference
through the same scheduler. Validate full-key exact/outer domains, left-first
row order, four compare value groups, independently inherited presence, NA/
zero/Duration/Decimal/overflow rules and strict obligations before pruning.
Self-combination reads a shared input once while counting both consumer
references. Use hand-built numerical/key/order oracles and actual source-plus-
fixed integration, with no semantic ComparisonEndpoint or second executor.
S5 still owns all calendar matching/shared-directory cases; S3 still owns
stored/cold witnesses. This subtask's acceptance must not claim those stage
exits. Independent fresh review is limited to two rounds before its local
accepted commit. Source development is now dispatched within that scope.

Fresh developer `develop_s6_bridge` is preparing its own concrete implementation
plan only. It confirmed the target does not require nonempty `on`: the existing
zero-axis Population contract distinguishes singleton `{()}` from the empty
domain, using an internal unit marker for physical transport. The bridge will
preserve this for explicit `on=()` without reading rows during construction.
Its proposed independent carrier/definition/DSL phase waits for coordinator
acceptance; shared compiler/evaluator changes wait for S4 handoff. Fresh
explorer `explore_s5_calendar_match` is planning pure frozen-calendar matching
and the S3 codec/interpretation handoff. Neither task has source-edit dispatch.

Coordinator accepted `s6-bridge-implementation-plan.md` after its focused
producer/consumer trace. Phase A has eight exclusive definition/carrier/DSL
paths and a construction test; phase B attaches original Deferred sidecars
before capture, then eliminates a compiler-only Frame-key leaf into one
existing schedule and qualified parameter transport. Both are internal phases
of this single assigned S6 foundation task, with one final independent review
and accepted commit. Compatible independently opened stored handles share an
exact input ID; `on=()` retains empty-versus-singleton semantics. S6 remains
idle before source dispatch while S4 freezes its acceptance candidate.

S4 ordinary candidate is frozen at its exact 12-path manifest
`d4b768529cac5610c9fdeeb0b69ced72b0fc013d2866b1b77fb3d54a0714eada`.
Focused regression: 245 passed; the subsequent two-case delta overlaps one
test and is not added as 247 distinct tests. All ten modified source modules
passed typing; all twelve paths passed lint/format/import checks. Coordinator
unrestricted `make test` passed **4,217 tests**, 2,927 warnings, 132.75 seconds
(`s4-ordinary-default-tests.log`). A fresh `review_s4_ordinary` Astra high agent
is independently reviewing round one. No S4 acceptance precedes that result.

Fresh S5 explorer delivered `s5-calendar-match-plan.md`. Coordinator accepted
the shared pure directory/map validator seam and the strict match-only partition
checks, with ordinary calendar equality preserving original labels/ordinals.
Presentation choice: retain raw-key MultiIndex exports and use isolated typed
frozen interpretation attrs plus positive preview resolution; S3 owns the
exporter/formatter integration. New developer `develop_s5_calendar_matching`
is preparing its own plan before any source dispatch. New developer
`develop_s3_storage_cutover` is refreshing the Store 12 plan against actual S2
carriers and the coordinated S7 switch; its stale temporary Query gateway is
explicitly excluded. Neither new developer has source-edit dispatch.

S4 round-one independent probing found a concrete P2 despite the green default
suite: valid Arrow nanosecond Timestamp/Duration keys at the minimum int64
value become pandas `NaT` on scalar extraction and were collapsed to the null
category by `_index`. Exact could accept unequal domains; outer could collapse
two rows. Original probes/logs are retained as `s4-review-key-extrema.py` and
`.log`. Round one is still completing; the frozen candidate remains unchanged
until its complete repair request.

S6 source phase A is now dispatched to its same assigned developer after the
S4 default snapshot completed. Its eight exclusive paths are bounded by the
accepted plan; `core/frame_data.py` ownership is only the SourceFrameBinding
sidecar/import hunk. S5 may later receive the separate CompletedFrameData
coordinate-validation hunk after explicit coordination. No S6 shared compiler,
evaluator or public Session edit is dispatched yet.

S4 first review completed with that single P2 and eight independently failing
temporal-key regressions, plus 62 passing focused tests and randomized ordinary
domain/arithmetic probes. Its same developer is repairing exact Arrow validity
and temporal ticks before the second and final independent review. The original
frozen default result remains historical evidence until the repair is retested.

Coordinator accepted `s5-calendar-implementation-plan.md` and dispatched only
its two new pure-core/test paths for internal phase A. The shared paired-map
slot layout and presentation choice are now frozen in the implementation
contract. Existing binary/compiler/evaluator files remain unavailable until S4
acceptance. S5 phase B precedes S6 phase B on those shared files; S6 phase A
continues independently. The refreshed S3 plan is accepted but remains idle
before implementation dispatch and the coordinated S7 runtime plan.

S4 repaired candidate is frozen in `s4-ordinary-repair-manifest.json`, SHA256
`09e72875e1bf74fe8dd5c2dc338202f0d0778ac4918a46ba0f6981782804bdcd`.
Only the authorized kernel/test paths differ from round one. Arrow validity
and physical integer ticks/days preserve real temporal extrema separately from
null. Original eight independent failures now pass; 137 focused regressions
and changed typing/lint/import checks passed. Final independent round two:
**No findings** (`s4-ordinary-review-2.md`), including the unchanged original
eight, nine neighboring joint temporal-key probes and 75 focused tests. No
P2+ remains in this bounded ordinary task; no third review is planned.

Both active S5/S6 developers explicitly paused source writes and checks. Their
exact nine current paths are isolated in verified temporary stash
`c3e1dbd4e447971bd17622d002886f0c83545b77`; original hashes are in
`s4-peer-isolation-manifest.json`. The two original user stashes remain intact.
Coordinator final isolated default tests and normal-hook commit follow, then
restore/hash verification before peer resume. Fresh developer
`develop_s7_runtime_cutover` is preparing its own plan only, based on the
accepted S3 storage and S5/S6 interfaces. No S7 source dispatch has occurred.

S4 final isolated acceptance: unrestricted `make test` passed **4,230 tests**,
2,927 warnings, 132.06 seconds (`s4-ordinary-final-default-tests.log`). The exact
repaired twelve paths still match the final independent review. Source/test
content is frozen in `s4-ordinary-accepted-source-manifest.json`. Accept this
bounded ordinary field/identity/zero-axis foundation and commit its twelve
paths plus owning contract/progress records with normal hooks. Stored/cold
S4 witnesses, S5 matching, S6 bridging and public cutover remain pending.

S4 accepted local commit: `e677814887d4eda0434931855bcd8face8bdb8dd`
(`feat: align and compare complete Frame data domains`). All normal hooks,
including full-source mypy and import contracts, passed. Its exact fourteen
paths were verified; all 759 accepted source/test hashes matched before peer
restore. All nine peer hashes and twelve reviewed S4 hashes matched afterward.
Only the verified temporary stash was dropped; both original user stashes are
unchanged and the index is clean. S6 resumed its same-task phase A. S5 resumed
phase A and is authorized to enter its accepted phase B after pure checks pass;
its shared-file ownership excludes S6's sidecar hunk and S3 lifecycle/exporter.

S5 pure phase A passed 43 hand-built/adversarial tests, typing, lint/format and
import contracts. Its approved schema builders and validators are the concrete
S3 handoff; phase B is now integrating them with the same binary schedule.
S6 phase A is frozen in `s6-phase-a-manifest.json`: nineteen new construction
tests and focused authoring/definition regressions passed (43 final tests),
seven-module typing and eight-path lint/import checks passed. Inert definition,
strong sidecars, exact stored-ID compatibility and full wrapper/Deferred
propagation exist; actual bridge execution and independent acceptance await B.

Coordinator accepted `s7-runtime-implementation-plan.md`: exact runtime/public
paths, consumer-traced retirement, seven owned public/boundary test modules and
a narrowly owned shared fixture hunk. Eight disjoint proposed test-migration
partitions preserve actual source and numeric assertions; a grep hit alone does
not authorize deletion. The inseparable Store 12/runtime switch may use one
coherent accepted commit instead of an incompatible intermediate revision.
Runtime source dispatch still waits S5 then S6 shared handoff.

S3 received only its bounded canonical-record dependency phase: one actual
OutputFinding relocation/reexport and frame_audit import change in three
paths, preserving exact current v1 wire bytes. No generation/manifest/publisher
switch is dispatched. New developer `develop_s7_disclosure` is preparing a
plan only for native Help, result/error guidance and independent surface tests.

S3 canonical-record phase is frozen in `s3-record-dependency-result.md` and
its three-path hash record. One actual OutputFinding class and exact Evidence/
Coordinate aliases retain identity; v1 receipt/protocol/record/audit tests passed
(66 tests), with typing/lint/import checks clear. No Store 12 activation yet.
Coordinator accepted the S7 disclosure plan and docs/skills plan: exact native
registry/guidance/surface ownership; twelve current analysis docs, five bounded
semantic/API consumers, 28 paired latest EN/ZH pages and both explicitly
authorized packaged skills. Their source/content implementation remains idle
until actual public activation. Historical docs and AGENTS remain untouched.

Fresh T1a developer `develop_s7_original_source_tests` completed an accepted
plan covering all 115 existing test functions in its exact fifteen-file scope.
Original source quantities/checks have explicit supported destinations; old
historical calendar-window/customer-fiber receiver output has no ordinary-axis
bridge equivalent and is separately retired. No test edits are dispatched.
Fresh T1b source/transport and T2a numeric developers are planning their own
exact disjoint subsets only; source fixes remain with their owning developers.

S5 source/memory candidate is frozen after 341 selected regressions and a
two-case test-only delta (one overlap), seven-module typing and eleven-path
lint/import checks. Only seven production modules and four focused test paths
changed; no compiler or frame_evaluation production edit was needed. One binary
adapter owns attachments and the unary evaluator now takes only FrameProgram.
Original manifest `s5-calendar-frozen-manifest.json` includes two nonowned S6
lines in shared frame_data. Coordinator isolated ten paused S3/S6 paths in
temporary stash `fc50fe24d3d13e7fa6b00ce1821eb175b64c4f03` and removed only
those two recorded S6 lines. Exact isolated review manifest:
`8a82ec7c494dd65553ef4bab6972b8a17e4fa5e6dce037213f0ef95905fe21f5`.
Fresh `review_s5_calendar` Astra high is running round one; coordinator is
running unrestricted default tests on this isolated candidate. Restore saved
peer hashes/hunks after acceptance; do not claim stored/cold/full S5 exit yet.

S5 isolated round-one default gate passed **4,303 tests**, 2,927 warnings,
359.95 seconds (`s5-calendar-default-tests.log`); all eleven candidate hashes
remain exact. Independent review nevertheless reproduced a construction-time
P2 on actual source Frames: PendingDataAxis drops captured inert calendar
interpretation, so incompatible grains/unsupported match rules can survive
construction and value-name errors can incorrectly win. Three original probes
are retained in `s5-review-pending-first.log`; review round one is still
completing. Repair must preserve calendar facts in the current pending
structure, without scanning a producer DAG or accessing Catalog/source rows.
The green round-one gate is not final acceptance of the repaired candidate.

S5 first independent report is complete: exactly one P2, plus 148 passing
focused tests, 120 randomized joint-domain oracles and a qualified UTC SQL
source-plus-fixed witness. Same developer received repair dispatch after a
written repair plan. Actual additional ownership is pending structure in
core/frame_structure, captured calendars in dsl/frame_sources, and only the
source-schema concretization helper in frame_evaluation. Retain closed inert
calendar facts, propagate through unary structure, validate before values for
pending/concrete/mixed carriers, and freeze before the final second review.

T1b's seventeen-file/95-function and T2a's twenty-nine-file/204-test plus
99-helper plans are accepted, without test-edit dispatch. A fresh
`develop_s7_shared_test_helpers` task now solely owns the planned adaptations
of domain_recovery_worker, support/source_trace, statistics/pandas_workflows
and packaging/relation_journeys. This supersedes stale T1b/T3 ownership labels
in earlier dependency plans. All caller files stay with their original
migration owners. Runtime still owns only its named shared_fixtures numeric
program helper hunk. Avoid duplicate snapshot/receipt codecs or implicit
publication just to obtain helper metadata. T1c materialization/recovery
planning is in progress; no test migration is implemented yet.

S5 same-task R1 repair is frozen in the expanded fifteen-path manifest
`s5-calendar-r1-repair-frozen-manifest.json`, SHA256
`88db66266d1f4189a01f4d744ed59733df97a20aabcb656e924e057cc9803817`.
The original three independent failing probes are unchanged and now pass.
Final focused regression passed 348 tests; ten source modules pass typing,
fifteen source/test paths pass lint/format/import checks. The pending axis
family now retains known CalendarDataAxis/PairedDataAxis descriptors, source
capture freezes once, and current structure propagates them without ancestor
or Catalog lookup. The sole additionally migrated S4 case is its paired-axis
priority fixture. Root started renewed unrestricted default tests and the same
reviewer's final second round; the earlier 4,303-pass result is historical.

All eight disjoint S7 test migration plans are now accepted for later dispatch:
T1a original-source (15 paths/115 test functions), T1b source/transport
(17/95), T1c materialization/recovery (30/165), T2a numeric (29/204), T2b
graph/source execution (32/121), T3a Session (7/57), T3b temporal (7/51),
and T3c statistics/export (15/51). Counts describe AST inventory, not passing
execution. Each has a complete retained/migrated/retired-producer ledger.
S5/S6 shared test paths remain reserved until explicit handoff. No migration
implementation is dispatched before real S3/S7 APIs and helper interfaces.

The separate accepted four-file helper plan owns domain recovery snapshots,
source tracing, external pandas utilities and packaged relation journeys.
Snapshots accept completed/persisted Frame and optionally an already collected
DataFrame without re-reading; pending input refuses. SourceTrace takes Frame
(or None) and inert audit facts while preserving actual SQL submission hooks.
No helper becomes a second codec, publisher, implicit evaluator or Artifact
data route. Callers stay with their partition owners. T1c/T2b/T3a/T3b/T3c
handoffs explicitly use these interfaces; source fixes remain with source owners.

S5 repaired-snapshot unrestricted default gate passed **4,309 tests**,
2,927 warnings, 186.11 seconds, exit 0
(`s5-calendar-r1-final-default-tests.log`). Coordinator verified all fifteen
candidate hashes and the unchanged original probe file, and froze the full
source/test hash record in `s5-calendar-accepted-source-manifest.json`.
Final independent R2 remains in progress; no acceptance precedes its result.

S5 final independent round two is **No findings**
(`s5-calendar-review-2.md`). The unchanged original probes and UTC SQL witness
passed four cases; ten new independent neighboring probes passed, including
mixed calendar/ordinary axes, reversed explicit mapping, same-day channel gaps,
full nominal-map retention, exact metadata drift refusals and closed attachment
references. A further 140 focused regressions passed. All fifteen candidate
hashes remain exact. No P2+ remains; no third round is used. Combined with the
renewed 4,309-pass unrestricted default gate, accept the private source/memory
calendar foundation and commit its fifteen source/test paths plus two owning
records with normal hooks. Store/cold/presentation and public cutover remain
pending. The protected S3/S6 peer stash will be restored and verified afterward.

S5 accepted local commit: `50663acaf40d3cc3493fe7e4ffc5f376ad761fe1`
(`feat: match Frame calendars from frozen coordinate directories`). Normal
hooks, including full-source mypy and import contracts, passed. Exact seventeen
commit paths and all 762 source/test content hashes were verified. Root applied
temporary stash `fc50fe24d3d13e7fa6b00ce1821eb175b64c4f03`, restored only the
two recorded S6 shared-file lines, and verified all ten peer hashes plus every
S5 nonshared hash and the accepted S5 shared base. Only that temporary stash
was dropped; both original user stashes remain intact and the index is clean.
Restoration evidence: `s5-calendar-commit-restoration.json`.

The same S6 developer is now dispatched into its accepted phase B execution
integration. Its restored phase A adapts only necessary typed consumers to the
new closed pending-axis family; source capture retains sidecars before semantic
consumption, and joint-key projection/dependent reads enter the one existing
QueryPlan/scheduler. S3 remains frozen at its canonical-record dependency phase;
S7 source and all migrated-test implementations remain undispatched.

Coordinator created and attached the managed worktree
`/Users/lichengxiang/.codex/worktrees/analysis-frame-storage/marivo` from the
accepted S5 commit. This isolates S3 storage changes while S6 continues in the
main checkout. The three canonical-record files were copied byte-for-byte;
shared dependency runtime resolves imports to the new worktree source. The
same S3 task is dispatched into its accepted full implementation after a short
workflow addendum. Main S3 files remain frozen. No temporary Query facade or
second codec is authorized to make an intermediate revision pass; the S7
runtime owner supplies concrete public-consumer retirement before acceptance.
Its bounded stored-read hook will be integrated only after S6 source freeze.
S3 and S7 still require one coherent final candidate, independent review and
normal-hook commit; the extra checkout does not certify a stage exit.

S3's first actual preview consumer exposed a bounded validation seam: a
selected sparse calendar record set cannot satisfy the complete directory's
0..n-1 sequence. Root authorized a same-S3-task extension in its isolated
core/frozen_calendar owner after a written addendum: one shared record validator
for exact schema/type/labels/keys/unique nonnegative original ordinals/bounds;
the existing complete-directory wrapper still enforces the full sequence.
Preview records never masquerade as a complete directory. Pair-record checks
share their existing owner and validate consumed references. This extension
requires sparse-preview tests and unchanged strict full S5 regressions in S3's
review; it does not reopen or relabel the accepted S5 snapshot.

Fresh Luna exploration `s7-remaining-consumers-plan.md` found remaining
outside-analysis test consumers. Fresh developer `develop_s7_integration_tests`
is planning the exact three semantic/datasource function scopes and twelve
packaging worker/caller files. Existing shared relation/statistics workers stay
with their owners. The old installed source probe's unqualified-remote premise
must be traced against the actually admitted numerical source route; a stale
refusal is not a new Frame contract. Historical ecommerce runs remain unchanged;
any later independent reacceptance uses a new evidence directory.

The dedicated `marivo-multisource` Colima VM is now started with its
repository-documented isolated profile and unchanged volumes. PostgreSQL 17.11
at 15432 and MySQL 8.4.11 at 23306 are ready; their setup verified reader write/
CREATE restrictions. Initial available disk was 67.17 GiB (the manager's
8 GiB threshold concerns disk space). Trino/ClickHouse are not started. Raw
setup logs and `s6-runtime-ready-profiles.json` retain environment evidence only;
no Frame transport/provider test or product qualification has yet run.

S3 reports its first real private Store12 memory.persist smoke passing,
27 new Frame-storage tests and 176 S5/core regressions. These are development
checks, not accepted source/protocol or full S3 exit. Its concrete old-public
consumer blockers are preserved in the first typing log. The same accepted S7
runtime task is now dispatched into an initial owned-source phase in the
isolated S3 worktree: remove the old admission gateway, narrow Artifact to
audit-only reading and route Session/public factories. It holds S6-dependent
bridge/QueryKeys cleanup until the accepted main-checkout source handoff.
No compatibility adapter is authorized. S3 retains exclusive ownership of
all terminal_publication tests and their individual fault-oracle migration.

Root approved S3's exact additional protocol-consumer hunks:
ArtifactDigest.digest_version's literal/default/check in evidence/_dataset_types
becomes frame-output-v1; reconciliation's module docstring becomes Store12.
No other evidence or reconciliation behavior is broadened by those approvals.

S6's first executable private bridge group passed 25 cases and 164 source/
definition typing modules. Its actual PostgreSQL/MySQL Frame-fed tuple/null/
empty/zero-axis/large-identity Runtime case is written but unrun; root will run
it after the local source freeze with both opt-ins and one Runtime worker.
No provider/cold/public acceptance is inferred from the local group.

The remaining fifteen-file integration-test plan is accepted, including the
bounded installed remote positive produce/offline/cold dispatch. It preserves
all source facts/native SQL/Fraction oracles and retains the independent raw
missing-table failure/privilege/cleanup checks. Its implementation awaits
actual source and helper handoffs. No historical ecommerce output is edited.

Runtime initial source phase is frozen at six files in the isolated worktree.
Its actual values -> compute(no Run) -> persist -> persisted_frame -> stored
persist-self -> audit probe passed without Catalog/source initialization.
CalendarAxis.coarsen alone was removed (twelve lines); both native reflection
checks now pass, with focused typing/lint/import checks. Source calendar/grid
kernels are retained. Full Query/QueryKeys cleanup and the public bridge still
await S6. No full runtime task acceptance is claimed.

Disclosure initial checkpoint is frozen at 21 paths, manifest SHA256
`f67ef717d20808968a86def8d0c42e7f885fea84a9946c54230c75d39ca10c9a`.
The eager registry is restored; nine typing modules, two public typing tests,
46 guidance tests and 134 static/CLI cases passed. Its two remaining physical
coarsen dependencies were subsequently fixed by runtime's owning change.
The final bridge/dynamic/docs-dependent tests and final context budget remain
pending; the budget baseline was not changed. S3 resumed its owning tests,
card/error repair hunks and Frame method docstring proposals.

The same docs/skills task is authoring in the isolated worktree after a workflow
addendum. Exact additional ownership docs/testing/runtime-coverage.md updates
current consumers/qualification claims while preserving historical evidence.
Final executable examples/docs-api/site gates wait the coherent source; both
packaged skills are explicitly authorized, and AGENTS/history remain excluded.

S3's real consumer tests found the timestamp[s] Parquet widening overflow at
valid int64-min ticks; its owned physical schema now uses exact int64 ticks for
that logical type, retaining logical dtype/timezone and the sole protocol.
All timestamp/duration units, null/composite keys and cold republish witnesses
are being exercised. The invalid Card.max_output_bytes continuation is a real
Frame.show defect and was repaired with existing Card.omission_recovery;
the original test assertion remains. Shared full/preview validation additionally
checks inherited true presence implies existing generated outer presence,
without equating presence and value NA or reading outside a preview prefix.

S6's first freeze had 26 owned paths, 53 focused passes and full 317-source
mypy. Final default on that snapshot had 4,346 passes and one stale assertion:
the prior compare/select test expected a raw semantic numeric exception from
Frame.compute. The new owning MaterializationError still enforces the check
and retains its semantic cause; root approved only that test's assertion/import
hunk, with direct kernel expectations unchanged. Pre-repair evidence is kept.

Root's actual two-provider Frame-key Runtime invocation failed both cases
before their first business result (`s6-root-postgres-mysql-runtime-first.log`,
1.89 seconds). It exposed raw Ibis String(length=None) versus VARCHAR(8)
comparison in target preparation, although the admitted Frame string type has
no length parameter. Same-task pre-R1 repair is dispatched using the existing
closed read/type normalization, retaining incompatible physical-type refusals
and original logs. Both services remain ready. No review round or provider
qualification has been consumed/claimed; a fresh freeze and reruns are required.

S6 pre-R1 normalization repair passed both actual PostgreSQL/MySQL cases
with no skips (`s6-postgres-mysql-runtime-second.log`, 3.95 seconds); the case
also feeds a source-produced joint Frame back into the bridge. Focused
construction/bridge/evaluation/correspondence regression passed 116 cases,
full source typing passed 317 files, and 27-path lint/import checks passed.
Fresh 27-path manifest SHA256:
`b3c6bd157247ccb2d603a0ad82b564cdf24dd8c2808cdd75ca53136af0a06c13`.
Root verified all owned and three peer hashes. Fresh Astra high reviewer
`review_s6_bridge` is running R1 while the final normalized default completes.

R1 independently reproduced a real mixed QueryKeys scope gap: new Frame
capture admits it, then source-fact extraction rejects it at execution. The
accepted development plan had explicitly mentioned that mixed shape, so this
is a P2 rather than a waived historical route. Root's documented repair decision
is early refusal only in new private Frame capture, recursively including
nested/mixed operands, with explicit Frame/population_from repair. The old
public Query paths remain unchanged until S7. This supersedes the provisional
mixed-operand sentence without introducing a transitional legacy evaluator,
producer-graph serialization or durable legacy audit variant. Source repair
waits the final R1 report; no new review round is started in the meantime.

Root's additional S3 typed-data probe exercised the actual reducer against
nine accepted timestamp/duration/date minimum-key cases and found overflow or
NaT key loss even after the nullable GroupBy length-hint fix. The owned S3
dependency repair now covers exact typed key grouping/array retention using
existing key helpers, preserving canonical order and statistic kernels.
Original raw diagnostics are retained; source/provider admission is unchanged.
These are actual integrity failures, not speculative defensive cases.

S6 R1 is complete with one P2 and 30 passing independent probes. The same
S6 development task is now repairing the documented new-Frame-only early
QueryKeys refusal before source preparation; old public Query remains intact.
The final review will be R2, with no third round. The original admitted-then-
failed mixed probe remains historical evidence of the repaired contract gap.
The pre-repair normalized default log ends at 94% without a completion summary
and no process remains; it is incomplete evidence, not a successful gate.

S3's isolated source is frozen at 21 source and five test/worker paths in
`s3-frozen-owned-manifest.json`. Development checks passed typing for 21
modules, lint/import for 26 paths, 265 relevant tests and 47 coordinate/receipt
regressions. The earlier complete scope retains its sole pending S6 numeric
error translation failure. All 18 exact typed minimum/null/composite reducer
cases passed, including nine fresh-process stored reductions. Canonical
ordering uses stable typed positional sorts rather than Python scalar boxing;
original failures remain recorded. No independent S3 review or acceptance is
claimed before S6 integration and the stored-reader evaluator handoff.

A fresh Luna task is preparing the bounded S8 unified acceptance mapping
against A01–A24 and the required independent business witnesses. It does not
run gates or certify the evolving candidate.

S7 docs/skills initial authoring is frozen at 50 paths in the isolated checkout,
draft manifest SHA256
`453bf23b5f2ecd31f3ecd81c8a5ef5cf24e84c82351a87973c6e08a5d6a31eb2`.
Static validation parsed 131 Python fences; one unchanged schematic placeholder
is explicitly excluded from a parsing-pass claim. Paired EN/ZH Python fences
match, focused links resolve and the API RST reflects 43 current exports.
Scenario independent appendices remain unchanged. Executable examples, Help,
packaging/API/site gates and independent review remain pending source freeze.

The same accepted shared-helper task is now implementing its exact four owned
files in the isolated checkout. Snapshot/trace/statistics use actual frozen
Frame services now; bridge-dependent relation execution waits the real public
population_from handoff. No stub or private substitute is authorized.

S6's R1 repair is frozen for R2 within the same 27 paths. Focused tests passed
102 cases, full-source mypy passed 317 modules, owned lint/import passed and
both actual PostgreSQL/MySQL cases passed without skips. The new default is
still active and has no final result yet. The guard distinguishes the exact
temporary ReadFields-owned QueryKeys envelope from real legacy producers;
S7 removes that internal envelope with its producer/consumer retirement.

Root independently reproduced the same typed canonical ordering issue in
TopValues after the reducer repair: valid date32-min/string composite keys
with a tied score fail in pandas multi-column categorical conversion. Raw
failure: `s3-root-top-extreme-key-probe.log` in the isolated checkout. The
same S3 task will repair that owned tie-order seam before independent review,
using the established stable typed ordering and retaining score NA-last and
complete-key tie semantics. The prior S3 freeze is a development checkpoint,
not the final accepted candidate.

S8 planning is complete in `s8-unified-acceptance-plan.md`. Root verified the
listed existing Runtime/slow/installed test names against current definitions.
The five final independent business witnesses have explicit evidence-only
destinations. T3b additionally receives the exact name for its already-planned
full cold outer/Top continuation; the differing-value-name align/ratio witness
is assigned to the final independent oracle. These names are plans, not passes.
Affected Trino/ClickHouse routes still require actual service-phase execution
if retained as affected by the final candidate; initial unavailability does
not waive that evidence.

Shared helpers phase A is frozen at exactly three isolated paths. Support and
unchanged forecast tests passed five cases; independent helper probes passed
18 typed minimum/null/composite cases and two forbidden-source cold readers.
Lint/import passed; the sole typing diagnostic is the runtime-owned retired
Session._execute_query fixture call. The fourth helper, relation_journeys,
remains unchanged until the real bridge handoff. No helper task acceptance or
commit is claimed at this partial checkpoint.

S3's Top correction is frozen: only frame_values.py and its owned storage test
changed from the prior 26-path checkpoint. All nine temporal/date types cover
composite/null minimum keys, score directions, full/cutoff ties and score NA-last;
memory/persist/readback and nine cold readers retain exact physical keys.
Focused checks passed 42 cases, full storage/kernel checks 112, typing 21
modules and lint/import 26 paths. The original root failure remains preserved.

The same accepted T2a numeric-test task is now implementing its exact 29 paths
in the isolated checkout against the actual frozen helper interfaces. Independent
current-row/numeric/source-Metric migrations proceed now; bridge/stored-root
dependencies wait their real integration. Runtime exclusively owns the shared
numeric-program fixture fix. No source change or private substitute is delegated
to this test owner, and no task acceptance is claimed from partial checks.

S6 final R2 reports **No findings**: 11 new independent bypass/control probes,
30 unchanged unaffected R1 probes and 19 capture-matrix/declared-closure checks
passed. The original superseded mixed-admission probe was explicitly deselected
and remains unchanged. All 27 owned, three peer and full Python snapshot hashes
match; eight historical evidence files remain byte-identical. R2 manifest SHA256
`99a7706b19ddcc4bf48b8fd1e90493f61564ac01f0f860694152285f0889c260`.
No third review is planned. Main default/standalone acceptance and commit are
still pending, so this is not full S6 storage/public qualification.

Root transferred this reviewed development snapshot into the isolated checkout
to unblock dependent development: 26 whole files match the manifest; Session
received only the exact members sidecar hunk, preserving its runtime changes.
`s6-reviewed-isolated-handoff.json` records the transfer. The same S3 task now
applies its previously written stored-read hook and verifies the original
numeric-error red case, source-free stored-root continuation and owned gates.
No main source changes or premature main acceptance occurred during transfer.

S6's repaired default completed with an observed exit code 0: **4,370 passed**,
2,927 warnings, 511.41 seconds. Developer and root both verified the unchanged
reviewed hashes. Root now runs an unrestricted standalone `make check-agent`
after isolating exactly the three S3 canonical peer files in temporary stash
`d26ad80727c73d41741de80e3c0cb409b0d1bb7e`. Both original user stashes are
preserved. The isolation/restoration ledger is
`s6-standalone-commit-isolation.json`; restore and verify those exact peer hashes
after the normal-hook S6 commit, then drop only that temporary stash.

The same S7 runtime task is now implementing phase B in the isolated checkout.
Its real public bridge and shared numeric-program fixture are the first handoff;
then it closes the actual Query/QueryKeys consumers without deleting retained
source Metric kernels. S3 exclusively holds the stored evaluator hook until
release. Old QueryKeys capture tests receive explicit retirement ownership
before edits, while supported Frame bridge assertions remain preserved.

Runtime's actual first phase-B handoff is available in the isolated checkout:
Session.population_from checks exact AnalysisFrame type and foreign local owner
before touching Catalog, then calls the real S6 factory. The shared fixture is
now numeric_program_frame, with no old-name alias. Callers have this concrete
handoff. S3's actual stored read hook passed five integration controls, including
the original numeric-error/no-Run case and source-free stored-root derive,
compare/calendar match, reduce and republish. Final owned checks remain active.

Root approved runtime's exact retirement of the three tests that only certify
the temporary old Query producer boundary (18-route guard, declared legacy
closure and old-public-Query execution control), plus newly unused imports.
All actual Frame controls, including nested Metric.where, remain required;
historical R1/R2 failures and probes remain unchanged. This is a producer
retirement decision, not permission to remove retained source-kernel oracles.

T2a reproduced a route distinction rather than silently changing its oracle:
direct source Decimal AVG(1.01, 1.02) returns 1.01 under native Ibis/provider
precision, while the retired host original-rollup asserted half-even 1.02.
Root verified the pre-existing current source-precision contract. The direct
source assertion will use its existing independent literal SQL AVG/CAST rows;
the exact 1.02 host arithmetic witness remains on a fixed per-member Frame's
current-row mean reduction. Raw failures and the two route meanings stay in
the test ledger. No backend cast, native precision change or claimed bitwise
equivalence is authorized.

Runtime's intrinsic read replacement is an approved closed ReadQuery with an
explicit Population dependency and projection facts. It replaces the actual
QueryKeys producer/consumer seam, compiles through the same keys-only source
stage and ReadFields projection, and must retain functional identity, complete
keys, null/empty behavior, ownership, source checks and inert source audit.
It is internal, with no new public type or compatibility executor.

S3's integrated checkpoint is frozen at 27 owned paths (22 source and five
tests/workers), including only the stored read_fixed assert/return hunk in the
evaluator. Its completed owned run passed 341 cases; additional public cold
ordinary/builtin/named controls passed three cases and direct stored Top passed
nine. The original numeric cause/no-Run red is green. Runtime now owns further
evaluator closure changes while preserving the stored hook. Final S3 typing
honestly records 30 external diagnostics in 17 peer modules during live IR
cleanup; no hook error remains. This is not final acceptance or a clean final
typing claim. Review waits a coherent source checkpoint.

The same accepted T1a task has begun its 15-file original-source test migration
in the isolated checkout. T1a and T2a hold new execution/typechecks during the
known QueryKeys/ReadQuery import transition, preserving raw failures and doing
only their owned static edits. Retained source Metric kernels, independent
SQL/math oracles and actual Frame semantics remain required; no compatibility
alias, blanket oracle replacement or pending-test xfail is authorized.

Runtime reached a coherent production-import checkpoint after the explicit
ReadQuery replacement and actual old Query/QueryKeys producer deletion.
This is not numerical acceptance. Its first real construction/bridge/read run
passed 39 cases with one test-setup failure: the metadata-only factory spy
used the retired LocalFrameServices.read name. Root authorized only that spy's
read_complete rename, retaining the forbidden callback and no-read assertions.
T1a/T2a may now rerun their migrated focused cases; transient import failures
remain historical until their actual controls pass. Source typing and complete
consumer cleanup remain in flight.

S6 standalone acceptance is complete on the final reviewed 27-path candidate,
with the three S3 peer changes absent: unrestricted `make check-agent` exited
0. Format/lint/import contracts passed, full typing passed 316 modules,
**4,370 default tests passed** with 2,927 warnings in 679.65 seconds, and API
docs built successfully. The standalone source/test manifest records 746
Python files and differs from R2 only by the isolated peer context. The local
normal-hook commit follows immediately; full S6 stored/cold stage qualification
continues with S3/S7 rather than being inferred from this private foundation.

S6 accepted local commit:
`9ce4783dc2e7097652f527660a3996f7c8194939`
(`feat: bridge Frame key domains into source plans`). Exact scope: 27 reviewed
source/test paths and the two coordinator contract/progress documents.
Normal hooks passed, including full source mypy and import contracts. Root
verified all 29 commit paths and all 746 standalone source/test hashes, restored
the exact three S3 peer hashes, dropped only its temporary stash, and preserved
both original user stashes. `s6-commit-restoration.json` records the result.
No push occurred. The isolated checkout already contains this reviewed source;
do not recopy it over ongoing runtime retirement changes.

Test execution is now held until a **full production-source freeze**, rather
than a temporary import checkpoint followed by further receiver deletion.
Runtime found and is restoring two genuinely live common-component lowering
helpers removed with the adjacent receiver authorization helpers. Original
144-pass/six-fail and typing diagnostics remain preserved. The calendar/fixed
tests retain every no-source/Catalog/timezone/value-read tripwire while only
their removed prepare_context spy is renamed or removed when the replacement
is already covered.

T2a has a concrete unresolved source construction report: standalone
Session.values with a declared composite (tenant, id) identity plus nullable
category rejects the valid complete ('a', 1) key at literal schema preparation.
Runtime owns the producer investigation; no flattening, key alteration or
private evaluation bypass is authorized. Raw result:
`s7-t2a-comparison-calendar-reference.log` in the isolated checkout.

T1a's member_count=3 report was resolved by the existing explicit-domain
contract, without a source patch: observe's common where condition does not
reselect the given three-member domain. The migrated control retains 3;
explicitly rebuilding members with the selected East key predicate must yield
2, and the original overall 340/3 ratio remains required. This separates
explicit source reconstruction from the retired fine-result history receiver.

Runtime restored the two live common-component lowering helpers and passed
typing for 161 affected files. The composite literal defect is traced to
removing the necessary literal_schemas preparation with the old ResultQuery
branch; its explicit literal-root preparation now uses the real bound Entity
schema. Focused regression remains pending the full freeze. Root additionally
approved only the Frame.top constructor's obsolete empty partitions argument
removal so the dead partition/coarsening grammar can close. S3 is informed of
that exact Runtime-owned one-line exception in its Frame file.

T2a static migration is frozen at 29 owned paths, 28 modified, with all 204
original test names retained. T1a static migration is frozen at 15 paths with
the complete 115-to-108 destination ledger (ten retired/renamed and three new
current/source witnesses). AST/format/Ruff/diff checks pass for both scopes.
Earlier partial 137/70/50-case outputs remain separate runs with original
failures preserved; none is a final acceptance. Both agents have finished
their turns and await same-task execution resumption after full source freeze.
The shared helper task is authoring its fourth relation-journey file under
the same execution hold. No additional user task or agent reuse is created.

The isolated production candidate is now **fully frozen**. Runtime reports no
remaining production edits or active checks. Shared 96-path manifest:
`s7-production-freeze-manifest.json`, SHA256
`ca74e9851769704609eed9c60c5ae088f5a9baa9e7f89cf5d3c47473e556285f`.
Final affected typing passed 161 modules; lint, compile/import passed; actual
controls passed **151 cases, two deselected, 15.62 seconds**. The previously
reported complete composite identity plus nullable-category Session.values
case now passes, as do explicit bridge/read, numeric refusal and no-Run checks.
Production remains frozen while test owners validate; an actual new defect
requires an explicit candidate refresh rather than silent continued cleanup.

The same accepted T1c task is now implementing its exact 30 materialization
paths. Its first recovery_worker handoff already uses persisted_frame, collects
once for the shared snapshot, preserves exact [2,0,4]/sum6/positive2 and all
source-forbidden/no-new-Run/resource checks, and retains Frame.show(n=1) as a
positive preview. Runtime's seven boundary-test migrations remain the same
pending task. Helper phase B and owned T1c checks may now run against the frozen
source; T1a/T2a resume validation as execution slots become available.

Root independently verified all 96 production freeze entries and captured all
299 production Python hashes. Unnarrowed `make typecheck` on this coherent
snapshot exited 0: **304 source/typing files passed**. Raw output is
`s7-root-frozen-full-typecheck.log` in the isolated checkout. This clears the
earlier transient external S3 source diagnostics for that snapshot; it does
not certify unmigrated test-helper dependencies or the final broad gate.

Helper phase B passed all four real three-process relation scenarios plus the
actual a12-to-a06 caller alias. The first a07 failure exposed the old helper's
DataFrame.to_json precision truncation; its one-way scalar serializer now
retains represented values, and the independent Fraction oracle stays intact.
All four helper lint/format/import checks pass. Final helper typing still
reports only the outside-owner unmigrated packaging graph journey dependency.

T1c's compiler/preparation group passed 63 cases. Its schema-drift planning
failure remains a direct typed SemanticExpressionError under the already
established S6 planning boundary, with exact fields and zero new Store records.
A separate concrete execution gap is queued for bounded Runtime repair:
an unexpected RuntimeError from SourceSession.execute during run_frame_plan
escapes without the structured MaterializationError/cause expected by the
public execution boundary. Already typed domain errors, planning/preparation
and BaseException control flow must retain their owners. No test weakening or
blanket catch across capture/preparation/cleanup is authorized.

The bounded execution repair is frozen at manifest SHA256
`5d48d28b8ea399a8f5d8c6bc39d1b97ddaec24278357ccba8e846c95388d6d43`.
Root compared it mechanically with ca74: exactly frame_evaluation.py changed.
Twenty-five actual fault, semantic, cleanup, control-flow, planning, fixed and
typed-datasource controls passed; 161-source typing, lint/import and compile
passed. Typed errors retain instance identity; unexpected causes are retained
without inspecting potentially unsafe message text. No capture/preparation/
planning/cleanup blanket catch was added. T1c/T2a final validation is resumed.

Disclosure's final actual-method audit found the one missing public target:
Session.population_from. Root authorized only session/_disclosure.py registration
and dsl/_population_disclosure.py producer edges, with existing navigation and
unchanged renderer/budget caps. This static overlay must receive its own freeze
hash. Additional test-only ownership is limited to the old deviation-typing
witness, synthetic rendering recovery text and the four-function handed-off
tail of query_artifact_boundary.py; runtime retains that file's other functions.

T1c static migration now covers all 30 owned paths and nine workers with Ruff
clean. Concurrency prepares a complete fixed result before the count snapshot
and tests contention only at persist; obsolete last_run_ref evidence is removed
while actual Artifact identity, full counts and ownership/recovery checks stay.
T2b receives the exact corresponding caller handoff. Both test scopes still
require final execution, typing, independent review and acceptance.

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
requirements. S3 remains planned, not accepted. The ordinary S4 foundation is
in development under its accepted plan.

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


## S7 source metadata freeze and test migration handoff

Root verified the 97 changed/deleted production entries and all 299 production
Python files in `s7-production-metadata-freeze-manifest.json` (SHA256
`709f4cddf959ce0f39d7c2b1f52d3f440c28585ae3d029930bb3b24b1559694a`).
This extends the preserved 7808 public freeze by exactly two approved static
files: pending Frame.show now walks only current explicit source leaves for
captured scope, period and anchors, labels actual ReadQuery as read, and stops
at fixed carriers; FrameAxis.role uses the public closed Literal and complete
docstrings. No evaluator, lifecycle, planner, numeric or storage semantics
changed in these overlays. S3's associated regressions passed 93 cases. The
production execution proof remains the separately preserved 5d48 freeze.

T2a's exact 29-path migration is write-frozen: 265 default and 19 slow/cold
cases passed, with exact typing, lint and all 204 original test names retained.
T1c's exact 30-path candidate is write-frozen: 244 default, 13 slow/cold/worker
and 18 fault/concurrency cases passed. Four shared helpers are frozen with all
four real three-process relation journeys and additional lifecycle/physical
temporal-key controls passing. T1c/helper typing currently has no owned errors
but retains ten outside-owner legacy graph_journeys diagnostics. These are
candidate results, not independent acceptance or stage completion.

The same integration task is implementing its accepted 15-path plan, prioritizing
graph_journeys to clear that external dependency. The same disclosure task is
resumed for current captured read-fact audit projection and dynamic/tail tests;
its old context caps remain fixed, with final journey measurements deferred
until current documentation examples finish validation. Runtime owns the seven
remaining boundary-test migrations and preserves disclosure's shared-file tail.
Every newly dispatched task uses a fresh agent; later phases and repairs stay
with their existing task agent. Independent reviews remain limited to two
rounds per task, and accepted subtasks are committed locally without pushing.

Root reran exact shared-helper plus T1c typing after integration migrated the
graph journey dependency: all **34 files passed**, actual exit0. This clears
the prior ten outside-owner diagnostics for that checkpoint; graph behavior
and its full integration task remain pending. Raw log:
`s7-root-helper-materialization-types.log`.


S3 independent review round1 is assigned to a fresh Astra-high reviewer on
`s3-review-candidate-manifest.json` (SHA256
`bee1ac90da5e98e504475da1189e80e297244d8303ed9ddea62f03554a7c71b5`).
It binds 27 exact current source/test paths and the 709f global source freeze;
shared evaluator ownership is only S3's fixed-reader hook. No stage acceptance
or commit is claimed before its findings are resolved.

Integration preserved a real old credential test failure and requested boundary
clarification. Wrong credential resolver scope and datasource metadata failures
occur in preparation before run_frame_plan. Target design retains existing owning
error types, and accepted 5d48 explicitly preserves typed MarivoError instances
and excludes preparation/planning from its execution wrapper. Tests therefore
assert the direct owning DatasourceCredentialScopeError/DatasourceConnectionError
with concrete repair, original secret redaction, no-Run/resource and project-byte
checks. Do not restore an enclosing AnalysisError merely to retain an old execute
wrapper. The integration plan receives an explicit bounded addendum for the
second affected credential test function already within its owned file; all
original redaction assertions remain. Unexpected failures in actual scheduled
execution remain the separately tested structured MaterializationError boundary.

Disclosure's pre-context checkpoint is frozen at manifest SHA256
`69a28243b6e5f95cd2145ef9a2c1686e6b853cef1f6fa1fad849d4350483b4d0`:
268 surface/CLI and 17 audit/tail checks passed, 10 source/witness typing and 22
owned-test lint/import checks passed. The original context baseline/caps are
byte-identical; no final context-journey measurement has yet run. The slot is
released for other accepted tasks until current docs/examples finish validation.


Runtime and its seven boundary migrations are now write-frozen: 78 default and
2 slow/cold cases passed, exact eight-file typing/lint/import checks passed.
The test manifest is `a1da114deca7299cd7685da601117cab932a7ac4fef954e6b0947ba636eb82a3`;
all production hashes remain709f. Root prepared the exact source/test review
scope in `s7-runtime-review-candidate-manifest.json` (SHA256
`969a70c7ead55749ba946c037c92f34c47444d998291b0214c4200703833530a`),
73 paths with explicit shared-file/hunk ownership and the disclosure-only tail
excluded. Fresh independent review is queued, not completed.

The same docs/skills task is resumed for actual examples, packaged-skill/Help,
API docs and site verification against the frozen public API. Remaining
accepted test plans (source transport, graph, session, temporal, statistics)
await execution slots. Integration prioritizes real local/cold journeys and
authorized PostgreSQL/MySQL UUID-owned source probes; final one-wheel installed
acceptance remains S8, and Trino/ClickHouse remain unstarted. No provisional
wheel, release-check or MinIO is required.


S3 round1 completed with **two confirmed P2 findings** and 146 independent
focused tests passing: positive preview leaked FileNotFoundError for missing
primary/required attachment, and attachment selection converted the full key
row group to pandas/Python records before selecting the requested rows. The
latter is distinct from permitted full Arrow key scan/hash/decompression I/O.
All 27 review paths, 97 production entries and 299 Python hashes matched.
S3 cannot be accepted yet. Its same developer is authorized a plan-first,
sole graph_reading.py source overlay plus owned storage tests. Preserve typed
read causes/repair and n0 metadata behavior; select required attachment records
before pandas/Python materialization. R2 is the remaining independent review
round, using the same assigned reviewer after exact candidate refresh.

Integration preserved PostgreSQL positive/offline/cold passes and a MySQL
weighted-mean mismatch (native21.6667 versus mathematical65/3). The committed
source policy explicitly accepts native Ibis/provider precision loss without
standardization casts. Root authorized an independent raw-SQL/direct-native-Ibis
oracle with exact numerator130/denominator6, native dtype/value/SQL and the
separate Fraction65/3 fact. Public output must match that independent native
result tightly; no tolerance inflation or source precision cast is authorized.
Cold receipt normalization is scalar/provenance only, with full snapshot and
source-absence checks retained. This remains unqualified until actually run.


Actual integration focused source probes now pass PostgreSQL and MySQL, including
positive computations, no-Run checks, persist without source replay, finally-owned
cleanup, raw offline failure and distinct no-model/source cold readers. Independent
native weighted values are PostgreSQL float64 21.666666666666668 and MySQL float64
21.6667, both with exact raw components130/6 and separately retained Fraction65/3.
This validates the focused developer probes only; final frozen provider Runtime
routes and installed-wheel acceptance are still pending S8. Twelve local
three-process journey groups also passed their independent oracles.

Docs/skills actual examples are stable: all50 file hashes remain453bf23b…,
including both skills; tutorial/guides/cutover/new-source selection and nine
multiround ledger fences passed,28 Help targets and3 packaged-skill tests passed.
Site content verification passed381 files. Correct isolated-source API build,
with PYTHONPATH bound to this checkout, revealed19 docutils errors/warnings in
15 source docstrings; site build prebuild is blocked by the same failures.
Both actual failures are retained, not reported as successful builds.
A **fresh** Sol-high docstring-rendering task owns only the implicated docstrings
in frame.py,frame_axis.py,artifact.py,session/core.py. It must plan first, prove
AST equivalence excluding leading docstrings, preserve all APIs/claims and
produce a four-file text-only overlay. No Sphinx suppression/config change is
authorized. The existing docs task is checkpointed until source repair; final
context measurements wait for the same refreshed static source.


Source is refrozen at `s7-production-preview-docstring-freeze-manifest.json`,
SHA256 `abee490906d0d4c0f6ec1eea24216c284514287c811950a0ca56009ecadd2212`.
Root verified97 entries/all299 Python files and independently proved all4
formatting files AST-equivalent with their saved709f preimages. Exactly5 source
paths changed: the S3 reader and4 docstring files. Actual docs-api now exits0
with zero warnings/errors;68 Help/docs checks,4-file typing/lint pass. S3 repairs
pass13 targeted/159 related/22source typing/27lint. Fresh27-path S3 R2 manifest
is `ed4c1623a8ae18ddea8a0cd95df1c21dd6cff491395e9fbe24cefa3713876408`;
the same assigned independent reviewer is performing the second/final round.
No source writers remain active. Runtime review scope is refreshed separately
as `s7-runtime-review-current-manifest.json` (SHA256
`bec1b296eb72acf8de8356dc593e60f8917ff869850bb70cad408caecef10ce2`).

T3c statistics and T1a original-source validation are active on their own
accepted plans. T3c's priority worker keeps the a11 callable/report handshake,
now passes actual typing and an independent three-process cold case. Remaining
external physical_workloads legacy diagnostics belong to queued T2b, never the
shared4-helper owner. A compact coordinator-state.json under ignored evidence
records each task's exact agent, status, candidate and review count alongside
this committed-progress document; no cross-task agent reuse is introduced.


S3 second/final independent round completed **No findings**: both original P2
probes now pass, neighboring cross-row-group/order/duplicate/typed-error controls
pass and159 independent tests pass. Reviewed27 scope files,97 production entries
and299 Python files match abee freeze. This clears S3's independent code review;
its public writer/reader switch remains one coherent acceptance with the S7
gateway, whose fresh Astra reviewer is now in R1. No premature partial gateway
commit or compatibility writer is introduced. Root's full15 integration typing
also passes after the T3c worker handoff; actual unchanged a11 complete journey
passes produce/fixed/cold across3 distinct processes.

Root site build on the correct isolated source passed API documentation prebuild,
then stopped because isolated site dependencies were absent (astro not found).
This is preserved separately from the repaired source-docstring errors. Install
only the checked-in locked site dependencies in the isolated worktree, preserving
main's existing node_modules, then rerun the same actual site build. No source,
lockfile, global tool or configuration change is authorized by this environment
repair.


Root site build completed actual exit0 after isolated npm ci from the unchanged
lockfile:359 pages built;308 original documentation routes,44 latest pages and
343 internal route/anchor targets verified; English/Chinese install outputs pass.
Root reverified every authored docs/skill hash (all50 unchanged),97 source entries
and299 Python hashes, and equal main/isolated lockfile hashes. Evidence is
`s7-root-docs-site-verification.json`; the absent-astro failure remains separately
preserved. Current docs/examples are stable for final context measurement once
S7 runtime's pending canonical error-Help findings are resolved.

T3c is development-frozen:15 owned paths/14 modified,51 original test functions
retained;85 default,6 own slow,8 additional cold parameters and actual a11
three-process route pass; exact15 typing/lint pass. No source/shared helper drift.
Its fresh independent review is queued. T2b is now implementing its accepted32
paths, prioritizing physical_workloads to clear T1a's external legacy typings;
T1a retains independent source native-precision and exact fixed-row Fraction
witnesses, not casts or looser tolerances. Runtime R1 is confirming constructor
mapping-admission errors and incorrect compute Help targets against exact base;
no source writer is dispatched until its final concrete report.


S7 Runtime R1 finalized **two P2 findings**, with exact-base proof: malformed
read/values mappings None/False/0 regress from six CoreRuleErrors to raw
TypeErrors, and actual source-cleanup repair points to an unregistered
analysis.frame.compute target. The closed inventory is14 explicit target/
help_target literals across8 Runtime files; canonical analysis_frame.compute
resolves. Independent2 valid-input controls and78 boundary tests passed; final
73/97/299 hashes match abee. The same Runtime developer is authorized a written
plan first, mapping admission in frame_sources plus those8 literal-only source
paths, and its existing7 test-file scope. Preserve valid-input execution,
output-name/source capture rules, typed exception boundaries and diagnostic
location text. No Help alias or broad catch is authorized. The same reviewer
gets final R2 after exact overlay/global-source refresh; S7 remains unaccepted.

T2b's priority physical_workloads helper handoff now passes actual typing and
retains values/total/builders/report signatures. validate consumes one completed
Frame export, actual schema/attachments and independent seed arithmetic, with
no hidden compute/persist. T1a is rerunning its final typing/default scope after
this external dependency clears; no shared4-helper ownership is expanded.


Runtime final independent R2 completed **No findings** after the two bounded
P2 repairs. The original nine probes, ten adjacent checks and69 affected-file
tests pass;73 candidate entries and97/299 production hashes match runtime
freeze975c2b. S3 and Runtime have each used their maximum two independent
review rounds and are clear; their coherent public integration remains pending.

T1a, T2b, T3a and T3b are now development-frozen, respectively15/32/7/7 paths.
Actual default checks pass149/157/67/66; T2b16 slow and T3b7 slow cases pass,
including the required non-key outer/Top order through two source-free cold
processes and republication. Original function/assertion migration ledgers and
typing/lint receipts are preserved. Each awaits a fresh task-specific reviewer.
T3a retained its complete mixed-graph Help/schema assertions locally; no
assertion transfer or duplicate Disclosure test was introduced.

Disclosure's final31-path plus shared-tail candidate is frozen. Actual context
acceptance with recording unset passes all3 tests:30633 charged characters
against the unchanged32845.7 aggregate cap,34 pages and22 Help calls; exact
budget_reference values,1.10 individual allowance and current_mean<6 gate
remain unchanged. A fresh Astra reviewer is in R1, with static review proceeding
while the following separately owned one-file source repair finishes.

T1b exposed a real public MembershipAxis audit serialization gap before source
execution. A new Sol developer, never a reused task agent, owns only
frame_source_facts.py and new tests in test_frame_evaluation.py. Its explicit
closed three-field MembershipBinding encoding now passes the unchanged T1b
20/10/0, null-domain, mean and classify witnesses. Root will freeze the exact
overlay and dispatch a fresh independent review before accepting it; S3/Runtime
review counts are not reset or extended. T1b's other17-path final checks continue.


Root independently froze the membership repair as global f58ad28e: exactly one
production file changes against975c, all299-file inventory keys remain equal,
and all21 existing test/helper definitions are AST-identical. The added explicit
canonical membership encoder is seven lines;43 affected tests and focused typing/
lint/import checks pass. Fresh membership R1 is active alongside Disclosure R1.
No production writer remains active; T1b final test-only checks use this freeze.


Membership independent R1 completed **No findings**: adversarial closed encoding
probes and5 public compute/audit/store controls pass;97/299 hashes verified.
T1b is frozen on17 accepted paths:125 default,11 slow,17 typing/lint pass;102
Runtime cases are collection-only. All95 original functions are accounted for.

First coherent unrestricted check-agent stopped at formatting of three existing
stage-exit tests before any later gate ran. Root's ownership reconciliation also
found two of those earlier authorized Runtime test hunks absent from its73-file
review inventory. A fresh bounded developer now owns exactly those3 test files,
format-only current edits, original-oracle ledger and focused checks; a fresh
reviewer will inspect their shared-stage exit test scope separately, without a
third Runtime/S3 source review. Production f58 freeze remains unchanged. Root
full make typecheck on f58 passed304 discovered source/typing files.


Disclosure independent R1 completed **No findings** with240 scoped tests, four
independent probes, seven shared-tail cases, two numerical semantic controls
and typing/lint clear. All31+tail and97/299 hashes match f58. Docs/skills R1
confirmed two P2 issues: stale literal reconstruction/no-continuation guidance
and a deleted CRM validity/data premise still linked by the temporal example.
The same docs developer is repairing those exact owning examples before R2.

The first coherent default run actually completed3968 passed,11 failed and2
collection errors in230.60s, after unrestricted formatting/lint/imports and304
typing files passed. API documentation did not run after the test failure. Raw
failure output remains intact. A fresh Luna exploration owns the twelve omitted
consumer modules and actual closed SQL-submitter manifest; telemetry failures
must be investigated rather than weakening suppression assertions. The three
stage-exit tests now independently pass73 cases, exact typing/lint, and preserve
184 original assertions,68 signatures and36 exception checks after concrete
local narrowing; their fresh review is queued.

Actual PostgreSQL/MySQL Runtime on f58 completed20 passed,2 failed,17 skipped
(other profiles),9 deselected. Both real Frame-key routes and all3 MySQL typed
parameter cases pass. The failures expose native nullable integer values whose
StageOutput has an effective float type while Frame adaptation reapplies the
declared int64 schema. The existing owning contract accepts native source value
precision/effective types while keys/null/unit/time correctness stays strict.
Root will dispatch a fresh bounded source task to establish and repair this
handoff, with no driver precision casts or tolerance weakening. These failures
are not provider qualification passes or final S8 acceptance.


Original-source T1a R1 independently confirmed two P2 oracle losses: complete
category/tenant/value associations in three-provider detail tests, and ordered
calendar endpoint/customer/value plus frozen bounds in Top3. The same developer
adds those assertions on exactly three files;11 affected local checks, four
assertion-sensitivity probes and focused typing/lint pass, preserving the original
assertions and other12 files. Final R2 will use the refreshed production freeze.

Docs R1 repairs are frozen on exactly two files, preserving other48 files and
historical appendices. Actual restored CRM validity/key/data premises and linked
anchor consumers pass; fixed relationship keys remain fixed after source mutation,
C404 refuses, and packaged skills3 checks pass. Final R2 is queued.

The residual-consumer Luna handoff identified exactly twelve test modules, one
dead SQL-submitter tuple in scripts/sql_ownership_audit.py and one real telemetry
generation defect. A fresh Sol developer owns this closed14-file task. Telemetry
now changes only its existing v11 path literal to v12; root verified the preimage
and exact one-literal delta, preserving recovery suppression and avoiding a
telemetry-to-analysis import cycle or old-generation dual read.

The fresh effective-value-type task is development-frozen on four source modules
and one new test file:8 new and295 existing tests pass, all source typing/lint
clear, and both unchanged failing PostgreSQL/MySQL Runtime cases now pass. The
repair uses exact-slot numeric value types and invocation-local replacement of
existing Frame programs/core inference; it introduces no source casts, driver
change, new executor or extra source reads. Root independently froze all98
production entries and299 Python files as3996b86e, proving exactly these four
source changes plus the telemetry literal against f58. Fresh effective-type R1
is active. No production writer remains active.


Third coherent unrestricted check-agent completed actual exit0:4476 default
tests passed,631 warnings,210.01s; full formatting/lint/imports,304 discovered
typing files and API documentation all pass. This is frozen3996 source evidence,
not final main/installed/four-provider acceptance. PostgreSQL/MySQL selected
Runtime now passes22 with17 other-profile skips and9 deselections.

T1a and docs/skills final R2 each completed **No findings**, closing both owning
P2 pairs at their two-round limit. Shared four-helper and residual14-path R1
each completed **No findings**, with actual independent cold/telemetry/source
guards, full narrowed typing/lint and source hash proof. T2a numeric R1 has48
local,5 cold and independent math/no-Run controls green; final closure waits
for the following separately owned S8 repairs.

Trino actual Runtime passed34 with one failure in a migrated wide-original
expectation (48 other-profile skips). A fresh Luna exploration traced the old
grouped host-original rollup vs new explicit overall native weighted mean; a
fresh Sol qualification task independently proved Trino native BIGINT overflow
and ClickHouse native wrapping, separated from widened Decimal/manual math.
Both provider parameters remain; only the owning function/imports change, with
all other T1a R2 bytes preserved. Its additional fixed Frame mean witness exposed
a real local-kernel bug and stays failing until that separate repair completes.

Independent helper probes also exposed a pre-existing upstream pandas duplicate
misclassification for unique Arrow temporal[MIN,null,MIN+1]. Because exact full
keys are required by the Frame carrier contract, a fresh S8 task repairs only
graph_exchange normalization using normalized Arrow scalar complete tuples,
preserving float NaN identity, null validity and row order. New160 tests,285
adjacent tests, exact typing/lint and original cold helper probes pass. Root
froze its sole source delta as55520a68,98 entries/all299 files; fresh R1 active.

A fresh local-mean task now owns only frame_values.py and a new focused test
module. It preserves exact integer/Decimal subtotals until mean's declared final
conversion; shared checked_sum, SUM/retained-state overflow checks, float fsum
and Duration rules remain unchanged. This addresses the actual valid mean of
two2**62 rows being rejected at a2**63 subtotal. Root will refresh the final
source freeze and rerun affected evidence plus the final main broad gate.


The S8 exact-mean source candidate is frozen as f949a7216ec1e480, with only
frame_values.py changed against the preceding exact-key freeze. Exact-key R1,
exact-mean R1, numeric T2a R1, materialization T1c R1 and graph T2b R1 all
completed **No findings**. Independent materialization checks passed247 local,
13 cold,1 recovery-parent,18 fault/concurrency and5 guard/no-replay cases; graph
checks passed52 local,12 cold/concurrency and separate plan/no-replay probes.
Exact-mean review passed62 tests plus243 independent numeric cases, preserving
strict SUM overflow and cold integer/Decimal results. No production edits came
from reviewers.

Actual ClickHouse Runtime on f949 completed27 passed,5 failed,49 other-profile
skips and7 deselections. The corrected native-boundary versus fixed-wide-mean
case passed. One real source defect remains: native Decimal division reaches
an isNaN check through a projection alias, bypassing the existing direct-Divide
ClickHouse guard. Three weighted-mean assertions use a widened Decimal oracle
instead of the explicit native expression, and one overflow test attempts to
persist an already-overflowing native expression as its safe prior result.
A new independently owned Sol task first writes a plan, then will repair the
closed source/test scope; all raw failures and independent native SQL outcomes
remain evidence. T1a and T2a owning review counts are not restarted; these new
S8 overlays receive their own review and outside-hunk preservation proof.

Fresh independent R1 agents now own T1b source transport and T3a session tests.
Each new task continues to use a fresh agent. Final main integration, accepted
subtask commits, broad gate, four-provider qualification and one-wheel installed
acceptance remain outstanding; current evidence is not promoted to those gates.


T1b fresh R1 established two P2 test-oracle losses despite125 default and11
slow checks passing: remote grouped assertions accept corrupted off-diagonal
values for both null policies, and numeric_top/selected_drill no longer reject
extra source batches. Independent counterexamples and actual current10k traces
are retained. The same task author will restore complete value/null matrices and
pin the current [10000]/[1] batches, then receive its final R2. This is an
acceptance blocker, not a provider numerical failure.


The ClickHouse exact-alias repair is frozen as3ba19a97feec53f6:98 entries and
299 production Python files match, with only graph_source_execution.py changed
against f949. It follows the exact Field owner through Project or Aggregate
before the existing Decimal Divide rule;12 local guard cases and7 affected
actual ClickHouse cases pass. Fresh independent R1 is active. Root then ran the
full selected ClickHouse batch:32 passed,49 other-profile skips,7 deselected,
actual exit0. PostgreSQL/MySQL final-source rerun also exits0:22 passed,17
other-profile skips,9 deselected. Trino is restarting serially for its final
source run. None of these skips is counted as a provider pass.


T1b final R2 completed **No findings**: both P2 coverage losses are closed;
60 independent key/matrix/batch corruptions are rejected, original assertions
remain, and the other15 files are byte-identical. T3b temporal R1 and S8
ClickHouse correction R1 also complete **No findings** with exact own-scope
and final production hash proofs. Actual final-source Trino now exits0 with
35 passed and48 other-profile skips; all four required backend selections have
passed on3ba19a97. The one-function native-overflow qualification has a fresh
independent reviewer and final real Trino/ClickHouse evidence.

Root copied360 frozen candidate paths to main and verified all98 production
entries and299 Python files, preserving reserved docs, local canonical S3 files
and both user stashes. Main unrestricted check-agent is running. To honor
separate accepted-task commits without creating incompatible source boundaries,
the core staged tree reconstructs exactly the reviewed975c2b72 baseline across
all299 modules, with105 source/owning-test paths. Nine source-file overlays
remain in the working tree and will be staged in their own accepted task
commits. Current source stays3ba19a97 for the gate; normal commit hooks wait
until that run exits to avoid changing imports during tests.


All22 newly accepted task commits are now recorded in the local commit ledger,
ending9542a0f5a9bfc603c77427bb7aea79001b188a5d, in addition to the earlier8
stage commits. Every commit used normal hooks, exact reviewed path/preimage
staging, substantive attribution and post-hook candidate/stash verification.
One hook attempt was interrupted because root concurrently updated this
progress file; all360 code/test/doc candidate paths were proven unchanged,
the two own staged paths were reset for retry, and normal hooks then passed.
The raw interruption remains evidence. Root no longer edits tracked progress
while commit hooks run.

The final key-error test consumer task R1 completed **No findings**:47 tests,
two-file typing/lint and exact source98/299 closure pass. Both files are copied
to main and committed9542a0f5; all five original stage refusal inputs remain,
an explicit typed Int64 missing-key case is added, and the one pre-existing
datetime typing fixture has an exact data-equivalence proof. Production source
stays3ba19a97. All test-only overlays are now included in the361-path candidate.

Root final-source focused slow/cold selection exits0:12 passed,16 warnings,
31.14s, covering the exact planned witnesses including W5. Final focused
publication/fault/storage checks exit0:161 passed,1 warning,32.32s. The main
unrestricted broad rerun and a single final wheel with selected base/four-backend
installed journeys remain the required closeout gates. No owning P2+ remains.


Main unrestricted second check-agent exits0:4666 passed,631 warnings,203.20s;
all formatting/lint/imports/typing and API documentation stages pass. The single
final wheel/sdist pass build/twine/301-product-file content checks: wheel
5cc28c37fc2a62e6791e1892e5caef45b38247ae5cc73b10319f2ac782b92c86,
sdist e814411198c4f186ef428a7eea5bf5322037883a49d2373f3eefed2f0af1251e.
Pre-existing dist artifacts were preserved.

Installed initial outcomes remain raw evidence: PostgreSQL passes; MySQL's new
mysqlclient extension lacks libmariadb linkage, so root rebuilds only that isolated
driver using explicit installed connector flags. Trino exposes a private oracle
integer-division21 versus declared public Float64 division21.666 mismatch. A
fresh Sol one-function task mirrors actual Trino public operator semantics while
retaining raw21,components130/6 and mathematical65/3; fresh R1 active.
Base installed cases have19 passes and one environment-digest comparison failure.
A fresh Luna proves that digest derives from version|interpreter|package_path;
analysis exports/Help already match. A fresh Sol snapshot task normalizes only
that opaque environment identity, retaining version and every static contract.
Both test-only tasks preserve all production and final-wheel bytes. Separate
accepted commits, same-wheel retries and a final broad rerun remain required.


Both late installed-helper tasks completed fresh independent R1 **No findings**
and normal-hook local commits:ffae79aa30dd24c397163ef92d50f989f06e4c9e
(Trino operator-semantic oracle),f2be3c5abcdf330858c1322cf178390e8d0c6c22
(environment identity snapshot). The existing301 product files,299 production
Python files and single5cc28c37 wheel are unchanged. Raw native integer SQL,
mathematicalFraction and source operand semantics remain independently recorded;
masked fingerprint normalization reuses its owner and retains version/static
contract sensitivity. MySQL driver source rebuild now links libmariadb and
imports successfully; the fresh retry uses those explicit environment flags.
Final frozen broad and same-wheel installed retries now follow.


Final S8 closeout: all required gates are green on the frozen code and single
wheel. Both installed-oracle corrections are test-only and preserve all package
bytes. MySQL fresh-driver reconstruction fixes the isolated cached native
extension linkage; no product dependency or source was changed. Root verifies
four producer/offline/cold PID triplets and exact saved state, then restores the
dedicated VM and archives the temporary worktree after preserving its evidence.
All owning P2+ findings are closed, all accepted tasks have normal-hook local
commits, and this coordinator-only record preserves the final accepted state.
