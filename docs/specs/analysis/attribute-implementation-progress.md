# Attribute implementation progress

Date: 2026-10-20

Status: **P0–P6 and latest-main integration I1 source accepted**. Implementation,
integration repairs and final source acceptance are complete. Local Git finish
evidence is recorded below; the earlier P6 acceptance remains historical.
Every task planned before development and used at most two formal review rounds;
no P2-or-higher finding remains. Historical plans/failures below stay preserved.

Final candidate: `9f55219e56134c94cb742f5607cf8625145cf6bd907e71bab1bb312849306ac9`.
Unmodified `make check-agent`: **5,040 passed**, full lint/314-source typing/API
docs green. Separate PostgreSQL Runtime **19 unique relevant nodes**, context
slow **2**, current site content/build/postchecks and independent real-source
detached cold continuation all passed. Astra P6 round1/2: **No findings**.
Task-owned PG/profile/VM stopped; no volumes deleted. This acceptance describes
the frozen pre-integration candidate. Local commit and main integration were
subsequently authorized; see the integration task below. No push is authorized.

Final [AT01–AT14 index](../../../evidence/attribute/p6-at-evidence-index-final.md),
[machine matrix](../../../evidence/attribute/p6-at-evidence-matrix-final.json),
[acceptance](../../../evidence/attribute/p6-accepted.json) and
[independent review](../../../evidence/attribute/p6-review-round1.md) are local
durable evidence. Raw artifacts remain intentionally ignored under evidence/;
the tracked-scope source and this progress record remain available for review.
Local/static, selected Runtime/cold and public acceptance do not claim installed
package, full provider-matrix or release acceptance.

## Authorization and baseline

- User request: implement [the plan](attribute-implementation-plan.md) in an
  independent worktree; plan each task before development, fix every P0/P1/P2
  finding, allow at most two review rounds per task, and persist progress.
- Worktree: `/Users/lichengxiang/.codex/worktrees/attribute-implementation/marivo`.
- Branch: `codex/attribute-implementation`.
- Starting HEAD: `c2cef31ec909d2205726d7d743ca14665818ed8a`.
- Target: [Attribute design](attribute-design.md), including the design changes
  committed in `ab9199acfc`. The subsequent starting-HEAD fix is included.
- The implementation plan was untracked in the original checkout; copied
  unchanged into this worktree before implementation. Original checkout retained.
- Environment: this worktree reuses the original checkout's `.venv` via a local
  symlink. Check imported product origin against this worktree before testing.
- Later read-only isolation check found substantial unrelated concurrent work
  in the original checkout. This task has not edited or merged those changes;
  its baseline remains the isolated starting HEAD above. Reverified current
  `marivo.__file__` resolves inside this worktree. Subprocess/cold evidence must
  retain that isolation rather than fall through to the original editable path.
- The implementation request did not authorize Git commits, push, publication,
  AGENTS.md or packaged-skill edits. The later local commit and fast-forward
  integration request authorizes those Git operations only; push, publication
  and protected-file edits remain outside scope.

## Team protocol

- Coordinator: primary agent; owns this record, integration and final evidence.
- Exploration agents: `gpt-6-luna`, reasoning `max`.
- Development agents: `gpt-6.1-sol`, reasoning `high`.
- Independent review agents: `gpt-6-astra`, reasoning `high`.
- Every task records owned files, dependencies, acceptance IDs and checks before
  development. Agents use this worktree explicitly, preserve peers' edits and
  never commit or edit coordinator-owned records.
- Review round 1 assesses the candidate. Development repairs findings; round 2
  verifies repairs and related regressions. Any unresolved P2+ finding after
  round 2 blocks task acceptance; no third formal review for the same task.
- Keep original failures and findings under `evidence/attribute/`; summaries
  here link to them. Local, Runtime, slow and release conclusions stay separate.

## Task ledger

| Task | Dependency | Owner | State | Review rounds | Acceptance |
| --- | --- | --- | --- | --- | --- |
| P0 contract freeze | baseline | explore_p0_request / explore_p0_execution / develop_p0_contract / review_p0_round1 | accepted; 2 P2 closed | 2/2 | AT01–AT04/AT13 contract only |
| P1 explicit request/default joint investigation | P0 | explore_p1_default / develop_p1_default / review_p1_round1 | accepted; 1 P1 + 2 P2 closed | 2/2 | AT01–AT04 local default scope |
| P2 common actual inputs/components/parent mapping | P1 | develop_p2_inputs / review_p2_round1 | accepted; four original findings plus final R4 residual closed | 2/2 | AT05/AT06/AT09 ordinary input proof |
| P3 exact rules/errors/atomic success | P2 for integration; P0 for pure rules | develop_p3_rules / develop_p3_integration / develop_p3_temporal / develop_p3_oracles / review_p3_round1 | accepted; P1 and P2 fixed,21 independent probes green | 2/2 | AT07–AT11 local qualified scope |
| P4 method facts/codec/cold DSL continuation | P3 for integration; P0 for pure model | develop_p4_facts_model / develop_p4_carriers / develop_p4_storage / review_p4_round1 | accepted; P1/P2 closed, final review clean | 2/2 | AT12/AT13 local/cold scope |
| P5 public disclosure and documentation | P1–P4 | develop_p5_disclosure / develop_p4_storage (docs only) / review_p5_round1 | accepted; green full gate and No findings | 1/2 | AT14 local/public scope |
| P6 unified final acceptance | P5 | coordinator / develop_p6_local_acceptance / develop_p6_postgres + independent acceptance | accepted9f55219e; full gates green; No findings | 1/2 | AT01–AT14 |

## Final acceptance preparation

- Coordinator pre-edit plan: `evidence/attribute/p6-acceptance-plan.md`.
  Preparation does not release P3/P4/P5 source dependencies or certify any AT.
- Final evidence will map AT01–AT14 to actual tests/probes, freeze all changed
  source/test/docs paths, and run one un-narrowed `make check-agent` after the
  final source changes. Relevant slow/cold, Runtime and site evidence remains
  separate. No full release gate or unrelated provider matrix is planned.
- One PostgreSQL default-Attribute Runtime witness may extend the existing
  original-source PostgreSQL test module after the public API is stable. It
  will reuse existing source setup/cleanup and compare exact keys, nullable
  integers/presence and a fixed parent selection against independent SQL.
  Runtime contribution qualification remains explicitly fail-closed for that
  provider; preparing PostgreSQL does not qualify contribution execution.

## P0 plan before development

- Inspect the existing request, coordinate, observation/component and codec seams.
- Choose the smallest concrete source-request and parent-handoff contract that
  preserves explicit inputs, ordinary bridge limits, calendar/paired restrictions,
  joint grouping, strict contribution bool and ordinary Frame continuation.
- Define method facts, propagation and version handling before implementation.
- Owned files for P0 development: `docs/specs/analysis/attribute-design.md` and
  only directly conflicting owning analysis design sections if necessary.
  Coordinator owns the plan and this progress record; no production edits in P0.
- Verification: local links, Python example syntax, explicit type/signature and
  schema/propagation consistency; independent contract review, maximum two rounds.
- Evidence: exploration and subsequent review reports in `evidence/attribute/`.
- Request explorer: `explore_p0_request` (`gpt-6-luna`, `max`). Execution/codec
  explorer: `explore_p0_execution` (same model/effort). Contract developer:
  `develop_p0_contract` (`gpt-6.1-sol`, `high`); development waits for dependent
  exploration conclusions before fixing those parts of the contract.
- Both explorations completed read-only. Reports:
  `evidence/attribute/p0-request-exploration.md` and
  `evidence/attribute/p0-execution-exploration.md`.
- Developer's concrete pre-edit plan: `evidence/attribute/p0-development-plan.md`.
- Independent Fraction arithmetic retained the additive, global/local ratio,
  linear and nonlinear counterexamples: `evidence/attribute/p0-arithmetic-witness.json`.
  This validates formulas only, not product execution or Runtime support.
- Candidate: only `attribute-design.md`, SHA-256
  `07d24a648ea2112cfc5f5e48310c8829d8b3c8b4e76ce54559867dbc24006b1c`;
  developer result `evidence/attribute/p0-development-result.md`.
- Developer checks passed: 10 Python blocks parsed, 8 local links, diff whitespace,
  and mathematical section 6 byte-identical to starting HEAD. Production remains unchanged.
- Formal review round 1 assigned to `review_p0_round1` (`gpt-6-astra`, `high`),
  candidate writes paused. Finding report will remain at
  `evidence/attribute/p0-review-round1.md`.
- P1-specific implementation exploration may proceed read-only while P0 is under
  review; P1 development remains dependent on P0 acceptance. Explorer:
  `explore_p1_default` (`gpt-6-luna`, `max`), evidence
  `evidence/attribute/p1-default-exploration.md`.
- Round 1 result: **two P2 findings, no P0/P1**. R1-F1: full parent/coordinate
  facts embedded in manifest violate bounded open/show(n=0). R1-F2: cross-endpoint
  linear coefficient/topology compatibility is not closed. Original report:
  `evidence/attribute/p0-review-round1.md` (immutable).
- Same P0 developer assigned repairs; pre-edit repair plan and result live at
  `evidence/attribute/p0-repair-plan.md` and `p0-repair-result.md`.
  P0 remains unaccepted; only one formal review round remains.
- Both P2 repairs submitted: bounded stored descriptors plus owned method payloads;
  contribution-only equality of consumed linear topology/operators/coefficients.
  New candidate SHA-256:
  `f426739cc15feccfabf4165b043f178bdcec60e15d5f6e53cc5cf1e6b716b763`.
  Developer rechecked AST/links/diff and byte-identical mathematical section.
- Final round 2 assigned to the same independent reviewer, output
  `evidence/attribute/p0-review-round2.md`. No third formal round is allowed.
- Round 2: **No findings**; R1-F1/R1-F2 closed, no remaining P2+. Candidate hash
  unchanged; original round-1 report retained. P0 accepted as design only.

## P1 plan before development

- Accepted input contract: P0 candidate SHA-256
  `f426739cc15feccfabf4165b043f178bdcec60e15d5f6e53cc5cf1e6b716b763`.
- Exploration: `evidence/attribute/p1-default-exploration.md`; default Attribute
  is one normal Frame operation in the existing schedule, not sequential compute.
- Developer: `develop_p1_default` (`gpt-6.1-sol`, `high`); must write a concrete
  pre-edit file/check plan in `evidence/attribute/p1-development-plan.md`.
- Ownership: request/Session capture, narrow attribute binding/program/kernel,
  required Frame AST/schema/compiler/evaluator dispatch, and focused default-path
  tests. No contribution formulas, method-payload codec/version changes, public
  export/Help/site synchronization, protected files or coordinator records.
- Compose parent restriction with each endpoint's own where/members, preserve
  complete Population domain policy and exact/pending Frame selection. Calendar
  and paired projection remains attribute-owned; ordinary bridge stays narrow.
- Validate AT01–AT04: strict input/ownership checks, joint/domain semantics,
  parent exact versus child outer, zero-parent/zero-finegroups checks, same final
  schedule, nullable values/presence and canonical output. Use existing fixtures.
- Run focused tests, modified-module typing and lint; retain raw failures and
  fixes. Independent review maximum two rounds after candidate freeze.
- Integration constraint: default parent-domain checks must not evaluate an
  unrequested overall Metric and introduce unrelated numeric failures (e.g.
  valid grouped ratios whose parent denominators cancel to zero). Independent
  default domain reads are not contribution-input proof; P2/P3 must share the
  actual total/fine inputs when contribution is enabled.
- Implementation uses a private `AttributeKeys` relation alongside ordinary
  `FrameKeys`, plus one Attribute Frame operation/program in the existing
  scheduler. Additional private parameter/projection files are within P1 scope;
  ordinary Population bridge admission is unchanged.
- Developer is constructing safe parent-domain counts over captured original
  scopes instead of evaluating unrequested overall formulas. Required source,
  membership, window/sample and complete-domain obligations must remain; a
  cancelling-denominator witness is required. Default-only independent reads
  are explicitly not the later contribution common-input proof.
- First shared default gate: `make test` exited 2, **4769 passed / 5 failed**
  in 259.79s (`evidence/attribute/p1-make-test.log`). All five failures concern
  premature public Session.attribute discovery/Help or the existing public
  Session inventory; no execution failure was observed in that run.
- Publication correction: stage the thin acquisition as private `Session._attribute`
  for P1–P4 and private-input tests. P5 replaces it with the sole public
  `Session.attribute`, removing the temporary private acquisition; no alias.
  Existing public surface assertions stay intact until P5. The target design
  continues to own the eventual public signature.
- Before follow-up edge repairs: 17 Attribute tests passed in 5.96s, related
  suites 162 passed, modified-source typing passed 28 modules, lint/import
  contracts passed 29 owned Python paths. These are development snapshots,
  not final P1 acceptance. Original failures remain in evidence logs.
- P4 source freeze released after this shared run; P1 is completing focused
  member-count/calendar and derived-parent boundary repairs before a new gate.
- Follow-up: 19 focused cases passed in 5.91s, including calendar Population
  parents, original member_count/members/where policy, reduced Attribute values
  as a new ordinary parent without generated-token collision, and owning paired
  full-directory validation. The second shared `make test` is running on frozen
  P1 code; P4's stable isolated model tests are included but separately owned.
- P1 owned snapshot: 29 files, digest
  `d4158c13caa0f27e0513624a77fdb82a1db57409a025392ea4d68e936a2368d2`;
  exact hashes and patch: `evidence/attribute/p1-candidate-snapshot.json`,
  `p1-candidate.patch`. P0 accepted hash was reverified.
- Formal round 1 assigned to `review_p1_round1` (`gpt-6-astra`, `high`) while
  the second full gate runs on frozen source. Review cannot close until its
  final outcome is considered. P4 isolated model files are excluded from P1
  review ownership; no P1 source writes occur during either check.
- Second unmodified `make test`: **PASS, 4827 tests, 643 warnings, 152.65s**,
  exit 0 (`evidence/attribute/p1-make-test-final.log`). Snapshot digest unchanged.
  P1 final focused 19, typing 28 production paths, lint 29/import checks passed.
  Reviewer received this complete outcome; P1 acceptance still awaits findings.
- P4 writer resumed after the shared gate; only its excluded unexposed model/test
  files may change. P1 production remains frozen through review round 1.
- Round-1 reviewer independently reproduced three preliminary issues (full
  severity-ordered report pending): member_count parent scope is only
  binding-validated and unselected members reach coverage; Population parent
  memberships are lost when rebuilding projection; distinct effective member
  entity roots with same integer key shape are wrongly aligned. No P1 repairs
  start before the complete first-round report; original probes are retained.
- Complete round-1 report: `evidence/attribute/p1-review-round1.md`, **R1 P1**
  incompatible effective member identities silently align; **R2 P2** selected
  parent scope is not applied before MemberCount coverage; **R3 P2** existing
  Population membership bindings are discarded. P1 remains blocked.
- Same developer assigned all three repairs with pre-edit plan
  `evidence/attribute/p1-repair-plan.md`, result `p1-repair-result.md`, new
  candidate snapshot and final checks. Original report/probes stay immutable.
  Endpoint where remains validation-only for MemberCount; parent scope is a
  distinct restriction. Membership parent overlap across independent totals
  stays legal; only new-by partition checks belong to later contribution work.
- Developer reports all three repairs implemented and 28 focused Attribute
  cases passing. Added regressions cover unrelated/same-root member identities,
  parent-scoped complete candidates, retained correlated membership parents,
  compound identities and ordinary-before-calendar member layouts. One narrow
  compiler/queries.py ownership extension was planned before editing. Focused
  typing/lint and related tests precede a new shared freeze and unmodified
  default gate; this is a repair candidate, not acceptance.
- Repair checks now passed: 28 Attribute tests, 228 related regressions, typing
  for 29 modified production modules, and lint/import checks for 30 owned paths.
  Source frozen and final unmodified `make test` started. P3 foundation remains
  evidence-plan/read-only until the shared gate completes.
- Final repair shared gate: **4,839 passed**, 677 warnings, 140.42s, exit 0;
  raw `evidence/attribute/p1-repair-make-test.log`. Repair report:
  `p1-repair-result.md`; 30-path candidate snapshot:
  `p1-repair-candidate-snapshot.json`, aggregate SHA-256
  `5bd1e5110f45a9e3ecd7c4316e5bbfd7fb15e47222cd1cc374d12b4e2bc307bb`.
  Source hashes and original review/probes unchanged after the gate.
- Same reviewer assigned final formal round **2/2**, output
  `evidence/attribute/p1-review-round2.md`. P1 stays frozen; disjoint unexposed
  P3 rule foundation may develop after the shared gate. No third round.
- Final formal round 2: **No findings**, R1–R3 closed, no unresolved P2+.
  Reviewer reran 28 owning tests and six independent controls, original probes,
  and verified all 30 frozen hashes/aggregate plus original evidence. Coordinator
  independently verified the same frozen scope. Acceptance record:
  `evidence/attribute/p1-accepted.json`. P1 acceptance is local default execution
  only; the temporary private entry, contribution, persistence and public
  disclosure boundaries stay explicit.

## Acceptance tracker

AT01–AT04 have accepted P1 local default-path evidence; public/final reacceptance
is pending. AT05–AT14 remain open beyond the explicitly recorded pure-model and
private-input witnesses. The plan's acceptance table owns their meaning; final
AT01–AT14 closure requires P6 on the frozen full candidate, not stage evidence
alone.

## Read-only preparation for P5

- Before P5 development, inventory only the exact owning disclosure/export/Help,
  dynamic rendering, latest EN/ZH documentation and packaged-skill consumers.
- Inputs: accepted baseline plus P0 candidate; do not advertise unimplemented APIs.
- Sole exploration output: `evidence/attribute/p5-disclosure-exploration.md`.
- No production/docs/skills edits; P5 development still waits for P1–P4.
- `explore_p5_disclosure` (`gpt-6-luna`, `max`) completed this inventory.
  Registry links must resolve attribute targets without shadowing their Kind;
  active Store/version docs and latest EN/ZH consumers are listed in the report.
  Possible skill wording clarification is recorded for later assessment against
  actual show/typed-field guidance; no protected edit or approval is inferred.
- Developer `develop_p5_disclosure` (`gpt-6.1-sol`, `high`) prepares exact
  `evidence/attribute/p5-development-plan.md` using that inventory. It may save
  an explicitly unimplemented concrete docs patch under evidence, but must not
  edit production/tests/current docs/site/protected files before accepted P1–P4
  and coordinator release. Assess actual skill contradictions only; optional
  wording clarification does not create an approval block. P5 formal review 0/2.
- Initial protected-skill assessment finds no required edit: bounded show plus
  a typed facts pointer remains compatible with existing workflow guidance.
  Native type navigation must support attribute targets without changing their
  Kind, and inherited fact-record show methods need one canonical callable
  owner rather than wrappers/ambiguous duplicate registrations.
- P5 concrete plan saved and coordinator reviewed; exact public/type/field
  ownership, runtime-route limits, API/current/latest bilingual owners and
  validation are bounded. Only the evidence plan was written. All dependent
  public/current-doc edits remain gated on accepted P1–P4 and root release.

## Read-only preparation for P2/P3

- Trace only closed Metric expression/reducer roles and existing proof facts for
  additive, ratio/mean/weighted, linear, partition identity, per and windows.
- Reuse P0's common-input execution trace; identify concrete rule admission and
  missing obligations, not another broad scheduler exploration.
- Sole exploration output: `evidence/attribute/p2-rule-proof-exploration.md`.
- No code changes; P2/P3 development still depends on accepted P1/P2 respectively.
- `explore_p2_rule_proofs` (`gpt-6-luna`, `max`) completed the report.
  Main missing proof: each scoped root identity maps completely to one joint
  tuple; count-distinct additionally requires disjoint counted values. Existing
  component duplicate-within-group checks alone do not prove either condition.
- Related read-only follow-up maps P1's frozen explicit request/program to one
  common actual input per endpoint/root, including missing classification before
  unmatched exclusion and same-root N/D roles. Sole additional report:
  `evidence/attribute/p2-common-input-integration-exploration.md`. No P2 code
  development starts before P1 acceptance.
- P2 developer `develop_p2_inputs` (`gpt-6.1-sol`, `high`) assigned read-only
  preparation and exact pre-edit `evidence/attribute/p2-development-plan.md`.
  Repaired P1 introduces `CoordinateBinding.parent_scope` and
  `FormulaProgram.parent_scope/parent_slots`; P2 must consume these current facts
  rather than the earlier frozen exploration snapshot. Source edits await P1
  acceptance and explicit coordinator release.
- P2's stage exit is the private real preparation/plan-execution handoff of
  checked sufficient parent/fine inputs, consumed by P3's exact arithmetic.
  `contribution=True` must remain unavailable until the complete P3 path is
  wired; P2 must never return a default Frame as a successful contribution
  request. The existing unified scheduler/executor owns these checks, with no
  new runner or production test-only branch.
- Concrete P2 plan saved and coordinator reviewed before edits:
  `evidence/attribute/p2-development-plan.md` (15 production and two focused
  test paths). Source/test edits still gated on P1 final acceptance. The plan
  consumes the P3 peer's closed rule/occurrence handoff and retains the private
  unavailable-True boundary until complete contribution arithmetic is wired.
- The plan now includes the two real exhaustive dispatchers
  `graph_local_execution.py` and `query_runtime_types.py` (17 production paths),
  amended before edits. After P1's final acceptance, coordinator released P2
  development. Preserve accepted default behavior; formal P2 review remains 0/2.
- P2 implementation decision under validation: baseline and classified relations
  may be constructed before execution, but one statement/read counter alone is
  insufficient if it can rescan the physical input. Prefer a single
  left-preserving classified stream or positively qualify the existing shared
  statement-snapshot/materialized relation per provider. No provider-name
  assumption or generic optimizer widening closes that proof.
- Initial positive Attribute route is the existing original-input qualification
  plus native DuckDB physical TableSourceIR leaves under its statement snapshot,
  exact same root/scope and one execution receipt/carrier dependency. Unproved
  remote/file/topology routes refuse contribution explicitly; ordinary default
  investigation stays available. This actual support boundary must be disclosed
  and Runtime defaults/refusals assessed separately in P5/P6.
- Private P2 preparation returns a concrete sufficient-component matrix with
  complete parent mapping through the existing plan/executor and checked stage
  carrier. It does not claim final contribution success. Captured Frame handles
  stay in binding-owned requests; core programs retain closed descriptors.
- First source checkpoint: private AttributeInputRequest captures parent and
  endpoint/projection/rule inputs without safe-domain observations; tagged
  SourceRead/AttributeRootLayout composes original parent/fine reducer values,
  component roles and scoped/assigned identity rows in one statement. Checked
  StageOutput.AttributeRootInputs carries matrices/receipt identity; coverage
  and per-parent one-by checks are implemented. Frame/evaluator wiring and first
  behavioral witnesses remain in progress. Two initial local typing failures
  were repaired; no behavioral or P2 acceptance is claimed yet.
- First same-root N/D behavioral witness failed before source I/O: original
  physical-leaf qualification treated the explicit planned parent-key parameter
  as an unbound physical source. Repair remains Attribute-owned and recognizes
  only the exact typed planned parameter object/slot with completed bridge/check
  dependencies, never a name-based or generic unbound-leaf exemption. Preserve
  the raw failure and unknown-leaf negative witness.
- One further pre-edit ownership amendment adds `dsl/frame.py` only for private
  preparation-operation description exhaustiveness (18 production paths).
  In-flight variable-collision/union-narrowing typing failures are recorded and
  owned by P2, not repaired in the pure-rule dependency by its peer.
- Exact prepared physical/parent placeholders now qualify without changing the
  generic original-input qualifier. First N/D execution reaches the existing
  FormulaProgram and private FrameCompute with one root read per endpoint.
  Preserved integration failures: repeated references must retain the same
  source-stage position in FormulaProgram inputs (not deduplicate positions),
  and fulfilled source checks must leave the pending list. Both repaired with
  no new read/arithmetic; matrix structure validation remains under repair.
  Distinct counted-value partition and per-leaf input_count are now carried.
- First end-to-end private P2 witness now passes: same-root N/D uses two leaves
  per root, one contribution-root statement per endpoint (two total), original
  FormulaProgram plus preparation FrameCompute, expected parent totals and
  matching source partition receipt identity. Raw `p2-sixth-behavior.log`;
  earlier failures retained. Native-mask, membership/parent-overlap,
  compound/null/empty and exact dependency witnesses remain in development.
- P2/P3 handoff now explicitly requires per-occurrence input_count and
  effective_count with the actual support policy/unit, plus role-specific
  compiled physical types and source-owned units. Weighted N/D must not inherit
  a finished mean's carrier; no sum/mean or equal-count inference closes this.
  Source RowExpression unit authority is being traced before assigning units.
- Six focused P2 source witnesses passed (`p2-base-witnesses.log`): shared N/D
  reads, mean/weighted masks, compound/null parents, distinct counted-value
  overlap rejection and duplicate physical-root identity failure retaining its
  existing component owner. A 15-modified-module typing checkpoint passed;
  subsequent membership-work typing is still in flight. Selected no-fine
  parents retain typed known_empty and explicit input/effective support counts.
- Expanded P2 suite reached **12/12 passing**: correlated membership parent
  overlap, unmatched-exclude preclassification coverage, overlapping new-by
  membership rejection, repeated relationship assignment idempotence, invalid
  physical identity, selected absent parents and explicitly empty parents.
  Empty-sidecar/pandas-coercion failures retained and repaired at typed carrier
  boundary. Role units reuse existing semantic unit algebra over captured row
  metadata; native weighted N/D keep distinct role units and compiled types.
- The final contribution operation will compose AttributeInputsValues as an
  ancestor in one existing Frame plan/executor. The thin private
  evaluate_attribute_inputs entry is preparation/testing only, never called
  nested inside a compute kernel. Existing source lifetime/check retention owns
  the final evaluation.
- P2 reached 15 focused input cases and a separate 28/28 default-path pass.
  One combined run raced a subsequent outer-role fix; original failure retained,
  stable rerun pending. Numeric integration found direct source reduction
  bypassed lower_observation's native Decimal/Duration finishing fields. P2 is
  replacing that shortcut with the existing original observation/finisher
  inside the same root statement; temporary refusal is not final numeric-scope
  narrowing. No P2 acceptance before this is closed.
- Native finishing correction now transports lower_observation's exact hidden
  check/finish fields in the same root statement, then calls the existing
  _complete_source_frame for parent/fine originals. Decimal mean passed and
  input tests reached 16 passing. Duration's admitted interval dtype metadata
  was restored; native integer mean's sufficient sum uses exact Decimal38 rather
  than imposing ordinary int64 sum overflow on a valid mean. Focused edge
  verification remains active; raw failures are retained.
- Numeric edge witnesses now pass, including original Decimal/Duration native
  finishing and the separate exact widened Int64-mean component.
- P2 focused witnesses reached **22 passing**, including one extra root adding
  one read per endpoint, a source changing every execute batch with matched
  same-endpoint parent/fine inputs, and exact dependency order for both parent
  bridges before either root. Added typed AttributeInputError.reason and an
  explicit parent/fine aggregate-coverage receipt so missing aggregates cannot
  be mislabeled known_empty. Final boundary audit/related run/freeze pending.
- P2 reached **23 focused passing**, including explicit empty zero-axis parents
  under weighted zero-denominator error policy (no nonexistent total checked).
  Both endpoint native statements now compile/qualify before business reads.
  Stable related tests, formatting, typing/lint and exact candidate freeze are
  pending. Coordinator will run one unmodified default gate on that frozen
  source, alongside formal P2 round 1; no competing shared broad gate.
- P2 frozen candidate: 15 production paths plus one test, SHA-256
  `d581e65e620c4e7cd5a7b6d324d8527b77350105e5835cd1dcae9d37219df76e`;
  P2-only patch SHA-256
  `a4266a206d69ca9d3ee363541bf31d0787f39f9dfd9ba4475a9aea295f37cabc`.
  Snapshot/result/handoff: `p2-candidate-snapshot.json`,
  `p2-development-result.md`, `p2-validation-evidence.json`. Planned paths not
  actually changed were omitted; existing original/observation/optimization
  owners reused unchanged. All 19 P1 paths outside P2 remain byte-identical.
- Final stable related run: **145 passed**, 178 warnings, 17.05s; all 15
  production files typed and 16 Python paths linted/formatted/import checked.
  Coordinator started one unmodified `make test` with all modified/new Python
  file hashes tracked before/after, raw `p2-make-test.log` and evidence JSON.
- `review_p2_round1` (`gpt-6-astra`, `high`) assigned formal round **1/2** on the
  frozen candidate, output `evidence/attribute/p2-review-round1.md`. Mandatory
  temporal/final-arithmetic/P4/P5 boundaries are explicit; real dependency
  defects remain reviewable. No source writes before consolidated findings.
- A crossed error-context planning message briefly proposed reopening; the
  developer confirmed **no source/test edit occurred** and root kept the freeze.
  Evidence-only handoff will identify typed failing endpoint/output/parent
  context and any AT11/P3 amendment. Reviewer assesses essential information
  preservation; no text parsing or premature public-diagnostic acceptance.
- Round-1 preliminary material reproduction: mixed selected parent domains
  containing present Int64 totals and known-empty siblings fail when
  complete_attribute_parent_domain builds a DataFrame from int/None records,
  infers float64 and is rejected by lossless int64 normalization. Reviewer is
  checking the same path with a >2**53 value and completing the consolidated
  report. No repairs before that report; original candidate remains frozen.
- Frozen unmodified default gate completed: **4,903 passed**, 707 warnings,
  196.30s, exit 0; all tracked modified/new Python hashes unchanged. This does
  not close independent findings. Raw `p2-make-test.log`, source hashes and
  command record in `p2-make-test-evidence.json`.
- Additional preliminary independent findings: completed native source values
  are sorted by full keys then assigned positionally into unsorted component
  batches, swapping valid parent/fine originals; endpoint conditions can erase
  original scalar lookup checks, while a missing lookup can change its original
  error owner/repair. Deterministic row-reordering and conflicting-lookup probes
  retained. Reviewer is consolidating four findings before repairs.
- Error-context assessment remains a mandatory P3/AT11 source amendment, not
  accepted public diagnostics: current error discards typed failure witnesses.
  Exact source/layout/check sites and needed closed source/partition contexts
  are recorded at the end of `p2-development-result.md`. No string parsing.
- Consolidated formal round 1 complete: `evidence/attribute/p2-review-round1.md`.
  **R1 P1** finished originals lose full-key association; **R2 P1** an ambiguous
  scalar predicate erases its required preparation check; **R3 P2** parent
  completion infers/rounds nullable Int64; **R4 P2** blanket numeric dispatch
  changes original error location/repair/source/coordinates. Reviewer reran
  24 focused cases and deterministic independent probes; all frozen hashes and
  prior evidence unchanged. P2 blocked despite green 4,903-test default gate.
- Same developer assigned all four repairs with pre-edit
  `evidence/attribute/p2-repair-plan.md`, separate repair result/snapshot/raw
  logs, relevant tests/typing/lint then coordinated new broad gate and final
  round 2. Preserve original report/probes/candidate/broad logs. Luna explorer
  traces only the R2/R4 original check-domain/dispatch reuse, evidence-only
  `p2-scalar-check-repair-exploration.md`; no extra review cycle or code owner.
- Pre-edit repair plan saved and coordinator read: four existing owned paths
  only, complete-key association, exact nullable construction, prepared check
  domain with genuine parent/period scope, and one existing owner dispatch.
  Original review/first-candidate/broad files are hash-recorded in
  `p2-round1-preserved-evidence.json`. Source repair may proceed under that plan;
  no new shared gate before the replacement candidate freezes.
- R3 tracing identified one necessary pre-edit ownership extension:
  `methods/local.py::population_domain` also infers float from finite nullable
  Int64 literal keys before parent completion. Repair that exact scalar/key
  construction through its existing typed boundary, include ordinary Population
  regressions/module typing, and preserve P1 member-scope behavior. Replacement
  P2 snapshot will include this additional shared owner.
- The stronger `2**53+1` plus Null parent-key regression then reached two later
  untyped constructors: Attribute parent projection and PopulationValues output.
  Developer amended the repair plan before editing `methods/frame_attribute.py`
  and the single `methods/query_values.py` constructor. Reuse existing exact
  frozen-key/typed StageOutput boundaries; do not cast already rounded floats.
  Each distinct first failure stays in new repair logs. These necessary shared
  fixes are included in the replacement candidate and related default/Population
  regressions rather than bypassed only in the contribution path.
- Completed R2/R4 owner trace: `p2-scalar-check-repair-exploration.md`.
  Preserve filtered values and root-duplicate markers before membership fanout,
  but carry `prepare_fields()`'s genuinely scoped pre-predicate relation into
  existing `_attach_preparation_checks` in the same source statement. Reuse the
  existing source completion check dispatcher, including known-empty weighted
  parent error context; an empty selected parent set has no nonexistent total
  obligation. No source edits or tests by the explorer.
- Repair checkpoint: **203 related tests passed**, 191 warnings, 26.26s;
  seven actually changed source modules typecheck clean, eight source/test
  paths pass scoped formatting/lint/import contracts. Exact-key refined,
  boundary and scalar-filter original probes exit 0 unchanged. The initial
  unrefined probe remains exit 1 on its two positional/order expectations that
  the first review had already disclaimed; preserve that output and distinguish
  it from the owning exact-key probes. Source/test edits are paused while the
  developer records the replacement snapshot/patches. New broad gate and final
  round 2 remain pending; this checkpoint does not accept P2.
- Replacement candidate frozen: 18 paths (17 production modules plus the input
  test), SHA-256 `9094857946f909563141892a8124eff9633d7140a08ae822a63ea9fcaf082e8f`.
  Full P2 patch against accepted P1: `p2-repair-candidate.patch`, SHA-256
  `125cd6124b545714398ce9053c2b421facfbd1b6d135fc552aed6c1621e9430c`;
  repair-only delta: `p2-repair-round1-delta.patch`, SHA-256
  `de50e14917497a633d45e280d74ddb4e8d2072b574716cd1ccf8687bab66ad06`.
  Eight paths changed for repair (seven source plus test); exact-key scalar
  fixes add existing local/query_values shared owners. Whole-candidate typing
  passes all 17 source modules; lint/format/import contracts pass all 18 paths.
- Coordinator verified all 18 candidate file hashes, both patches and accepted
  design: `p2-repair-coordinator-hash-verification.json`. Unified `make test`
  started with 40 changed/new Python paths hash-frozen before/after. Same
  independent reviewer assigned final round 2, `p2-review-round2.md`; source and
  tests remain frozen. No third review round is authorized for P2.
- Replacement frozen broad gate: **4,912 passed**, 719 warnings, 284.01s,
  exit 0. Raw `p2-repair-make-test.log`; total elapsed 286.62s and all 40 changed/
  new Python hashes unchanged in `p2-repair-make-test-evidence.json`.
- Final round 2 independently confirms R1–R3 and the major R4 owner repairs, but
  isolates a residual **R4 P2**: only the parent sum overflows while each child
  fits, yet dispatch uses fine-stage keys and invents a Null fine coordinate.
  Ordinary parent error reports only the actual complete parent key. Raw
  `p2-review-round2-parent-overflow-probe.log`; focused reviewer scope 81 passes.
  P2 is blocked pending this repair. The second-round report/probe will be
  preserved; root will require a pre-edit narrow repair plan, original probe
  and targeted regression checks. No third formal P2 review will be started.
- Final second-round report is now sealed: `p2-review-round2.md`. Coordinator
  preserved 18 report/probe/candidate/gate hashes in
  `p2-final-r4-preserved-evidence.json`. Read and accepted the new two-file plan
  `p2-final-r4-repair-plan.md` before releasing its bounded source/test edits.
  The actual tagged layout distinguishes parent/fine numeric payloads; keep
  eager/deferred checks and preparation-domain behavior, correct only the
  applicable coordinate construction. Parent-only and cancellation-based
  fine-only overflow controls plus the original isolated probe own closure.
  Findings from the second round still require repair; verification of this
  concrete residual is not a third formal review. P2 remains blocked meanwhile.
- Final R4 repair frozen, exactly the two released paths changed: candidate
  `bde5508ec296c9d21aa552234898860c560d1aec5e9694dfd7a6ecd7b6cce446`.
  Full patch `p2-final-r4-candidate.patch`, SHA-256
  `7a9cc154272911c5565cacccda8000b3f871cf19778e338f7ed72d8ad5b8a2a4`;
  residual delta `p2-final-r4-delta.patch`, SHA-256
  `162c425c80041299b1a4c441eed2e0ae2a7c500ac59bd9fdcbb00cf0a6590298`.
  Developer's two scope controls, **205 related tests**, modified module typing
  and scoped lint/format/import contracts pass. All 130 preserved evidence
  hashes and other 38 Python paths from the prior gate remain unchanged.
- Coordinator read the bounded delta, verified 36 final candidate/round-2
  evidence hashes, and independently reran the immutable isolated parent-only
  probe: exit 0, ordinary/private errors have identical parent-only coordinates,
  no invented Null fine key, `ASSERTION_FAILURES []`. Raw
  `p2-final-r4-coordinator-parent-probe.log`. New two-scope control run plus
  frozen unmodified `make test` is running; P2 acceptance waits for that result.
  The sealed round-2 finding/report remains unchanged and no third formal round
  has been requested or represented as completed.
- Final residual-repair gate: **4,914 passed**, 647 warnings, 362.54s, exit 0;
  coordinator's independent two-scope controls also passed (3.42s). Raw
  `p2-final-r4-make-test.log` and `p2-final-r4-coordinator-scope-controls.log`;
  total command elapsed 376.13s, all 40 changed/new Python hashes unchanged.
  Coordinator verified final 18 file hashes against the gate, both final
  patches and accepted design. **P2 accepted**, `p2-accepted.json`.
  R1–R3 closure comes from formal round 2; the residual R4 closure comes from
  the bounded repair plus preserved independent oracle, coordinator scope
  controls and new broad gate. Final residual patch was not formally reviewed
  again. Two-round limit respected; no P2 finding remains unresolved.
  This accepts private ordinary preparation only; mandatory P3 temporal,
  arithmetic and typed-diagnostic duties, P4/P5 and final reacceptance remain.
- Recorded responsibility transfer: freeze P2's proved ordinary-root input path
  with calendar/window/sample guard intact. P3 must own the temporal source-layout
  qualification extension, complete calendar/paired-parent contribution mapping
  and one positive fixed local window spatial witness before full P3 closure.
  It must amend exact ownership first and retain expanded logical occurrence,
  fixed window/period proof fibers and one source statement per endpoint/root.
  These AT05/AT06/AT07 temporal obligations are pending P3, not dropped or
  accepted omissions. Complex per remains fail-closed without exact proof.
- Existing Luna explorer is documenting only the concrete native field/finisher
  owner in `evidence/attribute/p2-native-finishing-exploration.md`; no source or
  test changes by the explorer. This focused trace confirms hidden fields stay
  transient and native_query_finish is not the numerical finisher.

## P3 pure-rule foundation plan before development

- Continuation owner `develop_p3_integration` (`gpt-6.1-sol`, `high`) is
  preparing only `evidence/attribute/p3-integration-release-plan.md` against
  P2's repaired carrier. Original arithmetic/foundation plans and evidence stay
  unchanged. No source/test edits before accepted P2 and explicit coordinator
  release; final P2 review/gate source freeze remains intact. Exact temporal
  source ownership, one unified schedule, existing numeric owners, typed error
  context and inert P4 payload remain required, with the same P3 0/2 budget.
- Read-only release-plan completed and coordinator reviewed: 384 lines,
  SHA-256 `4636c36d3f35c7346f7a729807e92830534336882a82255ba10803545037de1a`.
  Records necessary pending-summary and typed MaterializationError context
  ownership amendments before editing, with TYPE_CHECKING import direction
  avoiding a runtime cycle. Final repaired P2 handoff will be captured separately;
  original plans/foundation evidence remain unchanged. Still no P3 source release.
- P2 accepted and P3 source ownership explicitly released. Pre-edit handoff
  verification saved in `p3-release-checkpoint.md`: all final 18 P2 paths and
  both rule foundation hashes match; source numeric checks use real tagged
  parent/fine scope. Typed inert payload stays out of common metadata/audit;
  P4 completion attaches identity and its full-result carrier after cleanup.
- Coordinator contract check identified a necessary correction to the old P4
  single-final-ID plan for pending DSL: a filtered pending Attribute cannot
  certify the derived result, and two pending Attribute producers cannot get
  one conflicting origin. Supplemental pre-edit requirement:
  `p4-origin-handoff-clarification.md`. P3 retains actual typed producer/payload
  association in the existing scheduler; P4 owns correct direct-versus-ancestor
  origin allocation and ordered inheritance. No P0 contract change, generic
  lineage or extra formal review. Pending unary/binary/same-input cases join
  the existing propagation assertions.
- First P3 real additive path passes: `p3-additive-third.log`, 1 test in 4.34s;
  pending show(n=0) no business read, exactly two endpoint/root source executes,
  C=(60,-20), shares=(1.5,-0.5), absolute=(60,-20), all through one existing
  schedule and ordinary completion. First two raw failures remain: temporary
  duplicate arithmetic slot names, then a test's flat key instead of the actual
  pandas MultiIndex. This is a narrow implementation witness, not P3 acceptance.
- Root assigned `develop_p3_temporal` (`gpt-6.1-sol`, `high`) READ-ONLY plan
  preparation in `p3-temporal-development-plan.md`. Proposed exclusive transfer
  is five existing source files: compiler attribute_inputs/graph_lowering,
  source execution/query_components and methods/frame_attribute; one new
  temporal test module. Main must first stabilize/hash explicit side/occurrence/
  role/matrix/context/proof interfaces, then root releases non-overlapping
  ownership. Until that transfer neither second developer nor main may assume
  concurrent shared edits are allowed. All work remains one P3 review budget
  0/2; no additional formal subtask review is introduced.
- Main integration typing checkpoint passes 12 source modules; closed
  side/use/occurrence/role diagnostic handoff then passes its four-module check.
  Five real rule-family positives now pass in `p3-families-first.log`, 17.96s,
  covering additive, direct ratio, mean, weighted mean and linear initial paths.
  Wider exact masks/numeric/error/facts/atomicity verification is still pending.
- Temporal pre-edit plan saved and coordinator read: SHA-256
  `d6c640882eacc9b0b2f8a16e06312b68435a8130cdee4957756f38097d41b62a`;
  transfer and agreed interfaces in `p3-temporal-transfer-handoff.md` and
  `p3-temporal-interface-agreement.md`. Coordinator independently verified all
  five transfer hashes. Main has stopped writing them and core descriptors are
  implemented. **Temporal ownership released**, exclusively those five existing
  production files plus the new temporal test module. Core/rules/consumer/
  evaluator remain main-owned. No shared writes, singular descriptor aliases,
  additional formal reviews or competing broad gate.
- Matrix agreement preserves original role=value per expanded slot and combines
  components/support by logical occurrence through ordered exact source/window
  descriptors. The temporal-owned helper maps structurally active fibers using
  full saved parent keys; numerical NA never selects a fiber. Static window
  proof is an obligation, not actual qualification. Mandatory T01–T07 cover
  frozen ordinary/paired mapping, same-statement lookback/fibers, original masks/
  errors, overlap/coverage, structural missing and required-check retention.
- Luna max pending-producer trace completed read-only:
  `p3-pending-producer-identity-exploration.md`. Actual producer is FrameCompute
  stage_id, direct final compares against plan.publish; repeated handles/node
  memo share a producer, distinct nodes do not. Input order follows explicit
  dependencies rather than callback arrival. A narrow follow-up checks indirect
  Population/SourceRead parameter dependencies before P4's final order mapping.
- Follow-up `p3-pending-producer-dependency-order.md` confirms all typed stage
  inputs are needed for reachability, including population LocalCompute and
  SourceRead parameters. Explicit sidecar order comes from original collect/
  frame_key_inputs, not semantic parameter ordering. Main retains the narrow
  expression/input identity bridge for P4; original explorer reports stay intact.
- Necessary original-formula error ownership amendment approved before edit:
  main may add the optional concrete Attribute formula context at existing
  `methods/local.py` raise points and its actual caller, without changing
  numeric algorithms or original error fields. Its new pre-edit amendment must
  specify actual metric/path/key association, including paired/calendar source
  versus original parent keys; no parsing or first-parent guess. Five temporal
  source files stay exclusively temporal-owned.
- First temporal positives: ordinary calendar test passes with two daily parent
  domains, each C=(60,-20), shares=(1.5,-0.5), and two actual endpoint/root reads.
  `p3-temporal-calendar-third.log` retains first/second failures. Subsequently
  `p3-temporal-three-routes-second.log` has calendar and cross-period paired
  positives passing; fixed-window source/native finishing succeeds but its
  endpoint formula concat still fails on duplicate columns. Main owns the
  bounded local/caller repair; temporal does not cross ownership. These are
  initial route witnesses, not complete T01–T07 or P3 acceptance.
- All three mandatory temporal positives now pass together:
  `p3-temporal-three-routes-third.log`, 3 passed, 20.50s. Ordinary calendar and
  cross-period paired parents each preserve C=(60,-20), shares=(1.5,-0.5).
  Fixed AllHistory windows match independent SQL including June/July lookback;
  repeated physical identity across endpoint fibers is legal. Exactly two
  endpoint/root business executes and pending show(0) zero reads. Expanded
  originals finish natively, logical components/support use structural exact-one
  fiber mapping in one native statement per root. Typed negative/proof/error
  controls and broader P2 regressions/typing/lint remain pending before freeze.
- Temporal checkpoint `p3-temporal-input-regression-third.log`: **43 passed**,
  55.29s, covering eight temporal cases and the accepted P2 input module.
  Negative controls retain same-window overlap/coverage under top(0), actual
  typed window/use/identity witnesses, ordinary-calendar parent/fine overflow
  owner/repair without padded Null keys, and structural-outer missing scope.
  First/second failures remain, including a missing normalizer import repaired
  before the passing run; original check-handler signature is retained while
  typed context is attached outside it. Calendar native masks/precision,
  known-empty/full-bucket/damaged-directory controls and final static checks
  remain before the temporal candidate can freeze.
- Main `p3-main-types-third.log` passes **14 production modules**, including
  bounded local formula-error context, typed evaluator translation and the
  actual producer association memo. Initial 17-case main checkpoint had 16
  passes; pending Attribute→Population→new source exposed an unrelated inherited
  carrier being mistaken for the current endpoint. Main narrowed special
  expanded views to the actual stage's explicit Attribute binding and is
  rerunning 23 edge cases. Actual production records now retain input_id/index/
  is_final plus inert payload, using all-stage reachability and original
  collect order; no execution token enters payload or completed result.
- Main's original-formula ownership amendment is recorded before editing in
  `p3-formula-context-amendment.md`; coordinator read the concrete fields/caller
  and retained projection dependency. Numeric policies and original error
  fields stay unchanged, paired/calendar mappings come from actual frozen
  projections, and the ordinary optional-None path remains ordinary.
- Temporal focused suite now **16 passed**, `p3-temporal-complete-first.log`,
  85.79s while main runs in parallel. Related suites and five-source typing
  continue. Scoped Ruff/format passes, but full import-contract check exposes
  compiler.frame_plan→new runtime consumer→materialization/session. Main owns
  the correction: keep pure schema inference in a compiler/core owner and the
  runtime consumer downstream. No guard narrowing, new allowlist or guard-test
  change; original failed lint output remains. Temporal does not edit main files.

- `develop_p3_rules` (`gpt-6.1-sol`, `high`) prepares the closed rule and
  component-role handoff from the accepted P0 contract and existing Metric IR.
  This independent foundation does not start dependent P3 execution integration.
- Expected sole ownership: new `binding/attribute_rules.py` and focused
  `tests/analysis/graph/test_attribute_rules.py`; exact pre-edit plan:
  `evidence/attribute/p3-rules-foundation-plan.md`.
- Recognize ordered actual Additive/RatioComponent/Linear structure and fixed
  typed coefficients; no source reads, arithmetic, versions/fingerprint gate,
  algebra normalization, generic proof registry or public exposure.
- While P1 prepares its final shared gate, the writer may only read and save
  the evidence plan. Source/test edits require the coordinator's explicit
  release after that gate. Full P3 review remains capped at two rounds; this
  foundation does not create an extra formal review cycle.
- Shared gate completed; writer released for only the two planned new files.
  The concrete plan preserves additive priority for recursively additive fixed
  linear trees, including proved composite N/D components, and uses linear for
  combinations involving ratio rules. No leaf-only narrowing of §6.2–6.3.
- Pure-rule candidate frozen: **39 focused tests passed**, 2.85s; one-module
  typing, scoped lint/format/import contracts and diff whitespace passed.
  Result/API handoff: `evidence/attribute/p3-rules-foundation-result.md`.
  Source SHA-256 `ab77aa8af648e12c3c7fe2c7f594f43e7f7e25180fbf7f019bc054f40a86cf44`;
  test SHA-256 `a09f793c676250decd51d48737dba548bc93366328de3556b0a19295393906f1`.
  P2 received exact signatures. Full P3 arithmetic/review remains pending.
- Existing rule/proof explorer assigned a narrow read-only qualification check
  for a concrete local original-input fixed-window spatial sum and simple versus
  complex per sample requirements. Sole output:
  `evidence/attribute/p3-window-sample-qualification-exploration.md`. This avoids
  silently treating the foundation's missing-proof guard as the final supported
  window/per boundary; no source changes or new formal review round.
- Same P3 developer assigned read-only arithmetic planning, coordinated with
  P2's emerging real matrix/carrier and existing numeric owners. Sole additional
  plan: `evidence/attribute/p3-arithmetic-development-plan.md`. No source/test
  edits for this dependent work before accepted P2 and coordinator release.
- Concrete arithmetic plan now saved and coordinator reviewed. It identifies
  bounded source/test owners, the existing original formula/numeric adapters,
  full-parent Q/contribution/share and original error handling, positive local
  window and cross-period paired-parent proof, typed P4 facts handoff, and
  independent AT07–AT11 Fraction/Decimal/Duration/overflow/NA witnesses. Stable
  accepted P2 matrix names/hashes and source-edit release remain pending.
- Window follow-up identified existing local table witness
  `test_source_spatial_rollup_preserves_lookback_and_endpoint_occurrences`:
  fixed daily endpoints, all-history sum and repeated root identities preserve
  lookback and endpoint occurrence. The existing DuckDB qualifier can admit the
  physical shape, but no existing test proves Attribute's new-by partition.
  P2/P3 must carry exact endpoint/window key, period/time/root scope and prove
  coverage per parent/window fiber before claiming the positive new route.
- P3 planning reuses `FormulaProgram`/`formula_columns` and `query_combine` with
  the existing lower_metric_formula numeric checks for original values, rather
  than adding an arithmetic kernel. An inert typed parent/coordinate facts
  payload can later receive P4's one final result identity; no execution recipe
  or component carrier is persisted.
- Planning found one pure-rule candidate defect before integration/review:
  CalendarCoordinate.identity hashes full endpoint scope/directory, so comparing
  it across endpoints wrongly rejects legal distinct periods. Coordinator
  released only the existing two-file pure-rule owner to amend its plan and
  repair this comparison with positive cross-period and negative grain/timezone
  controls. Per-side full frozen-directory validation remains authoritative;
  dependent arithmetic writes are still gated on P2 acceptance.
- Calendar foundation repair candidate: **40 focused tests passed**, 3.23s,
  scoped lint/format/import contracts passed. Source SHA-256
  `ce8caec45dbb215e521d8e14ed57181c1b2b8a372c4ac83073f9be974aeeb65d`;
  test SHA-256 `06b7b8b0d3bb972d3a5e09993014d6e3d8351aece2be988ce9abf34bb3c958fa`.
  Narrow typing currently traverses known in-flight P2 dispatcher errors; raw
  failure retained and rerun pending P2 repairs. No rule-module error reported.
- One requested rerun after P2's temporary clean checkpoint hit a new in-flight
  `query_components.py:252` pandas record typing error, exit 2; no rule-module
  diagnostic. Raw `p3-rules-calendar-repair-typecheck-final.log` and explicit
  failed status retained. Wait for a stable P2 typing checkpoint/freeze before
  rerunning; foundation typing is not declared green.
- At P2's frozen checkpoint coordinator reran the narrow module typing:
  **passed**, no issues in one source file, exit 0; raw
  `p3-rules-calendar-repair-typecheck-frozen.log`. Two repaired source/test
  hashes unchanged. Developer may update only evidence/read-only arithmetic
  ownership amendments while P2's gate/review runs; no source edits.
- Completed window/sample report:
  `evidence/attribute/p3-window-sample-qualification-exploration.md`. A simple
  ordinary same-root sample sum is only a candidate if complete sample support
  and the sample/by tuple identity are actually carried and checked; current
  sampled booleans alone do not prove it. Complex/order-sensitive per stays
  fail-closed until its exact sequence/support/policies are proven.

## P4 facts-model foundation plan before development

- This independent foundation depends only on accepted P0's exact pure record
  schema, not on P1 execution or P3 producers. P4 integration and acceptance
  still wait for P3; no stage is accepted out of dependency order.
- Developer: `develop_p4_facts_model` (`gpt-6.1-sol`, `high`). Sole production
  ownership: new `marivo/analysis/core/frame_method_facts.py`; focused tests:
  new `tests/analysis/graph/test_frame_method_facts.py`. No existing Frame,
  evaluator, codec, Store, public export, Help or documentation edits.
- Implement only accepted immutable concrete fact records, scalar/key/structural
  validation and bounded repr/show, with no source handles or storage I/O.
- Validate AT13's pure structure/type/association and rendering boundaries using
  meaningful focused tests plus module typing/lint. Record the pre-edit concrete
  plan/result in `evidence/attribute/p4-facts-model-*.md`.
- Module remains an unexposed integration candidate until P4's full two-round
  review; no separate formal review cycle or extra review budget is introduced.
- Foundation candidate now collection-stable: 51 focused tests passed in 4.08s;
  focused source typing passed. No independent arithmetic kernel was added;
  exact numeric promotion/consistency remains an explicit P4 integration
  responsibility of the existing numeric owner. Final model lint pending.
- Final pure-model candidate ready, still unreviewed as part of P4: **54 focused
  tests passed in 3.14s**, focused typing, lint/format/import contracts passed.
  Result: `evidence/attribute/p4-facts-model-result.md`. Module SHA-256
  `9a524a4bdddca462f411799f50776e103fe21a160ce7f6f9ef56138e66871759`;
  test SHA-256
  `d9252e9fe5c068b73dd74955c0fbca54368c1fbfb2330e1befc63c53345067b2`.
  No consumer imports or public exposure. Writer paused; numerical consistency
  and cross-input origin conflict checks remain explicit integration work.
- Read-only `explore_p4_codec` (`gpt-6-luna`, `max`) inventories exact existing
  attachment/receipt, lazy-open and DSL propagation seams for the coordinated
  Store 13/frame_output-v2 integration. Sole output:
  `evidence/attribute/p4-codec-propagation-exploration.md`. It does not alter the
  pure model or start dependent P4 integration before P3 acceptance.
- Exploration completed, evidence-only, with exact terminal carrier/codec,
  publication/store/evidence digest constants and propagation seams. No tests
  or product edits were made by the explorer.
- `develop_p4_integration` (`gpt-6.1-sol`, `high`) prepares only the exact
  `evidence/attribute/p4-integration-development-plan.md` from these traces and
  the existing pure model/P3 payload plan. No source/test/Store edits before
  accepted P3 and coordinator release. Full P4 review remains 0/2 and includes
  the unreviewed pure-model foundation; no extra formal foundation review.
- Concrete P4 plan saved and coordinator reviewed (SHA-256
  `701d4cf061b620ca95afcba5aaca9934f120064df49e2490d06b91aa4be9e946`).
  Exact owners include two narrow numeric-consistency/payload codec helpers,
  existing carriers/readers/publication and focused existing cold/fault suites.
  Stable accepted P3 producer/rounding handoff remains the pre-edit gate.
- P4 continuation owner `develop_p4_storage` (`gpt-6.1-sol`, `high`) prepared
  only `p4-integration-release-plan.md`, 141 lines; coordinator read and accepted
  its pre-edit scope. Final SHA-256
  `73371c8ac2bdb9c709cafaad622f7e56e184cdf7b7f18216ce042abc7b7c2940`.
  It corrects pending producer origins/explicit order under the accepted design,
  preserves original source/model hashes and the original plan, and closes the
  full-facts versus row-only/lazy/owned payload obligations. No source or test
  edits. P4 stays paused until accepted P3 plus exact actual payload/numeric/
  producer handoff and root release; review remains 0/2 including pure model.

## Checks and failures

- Setup: created isolated managed worktree and branch; copied the existing plan;
  verified the product import resolves inside this worktree.
- Baseline focused tests: `make test TESTS='tests/analysis/session/test_analysis_frame.py tests/analysis/materialization/test_frame_correspondence.py'`
  passed, 67 tests in 3.86s; raw output: `evidence/attribute/baseline-focused.log`.
- Help discovery first tried the invalid shorthand `analysis.observe`; the native
  structured error supplied `analysis.session.observe`. Corrected canonical
  Help calls are captured in `evidence/attribute/baseline-help.txt`. This was a
  coordinator discovery error, not a product failure.
- No Runtime qualification or release checks have run yet.
- Initial read-only Runtime readiness found PostgreSQL, MySQL, Trino and
  ClickHouse test ports offline and their opt-in flags disabled. Snapshot:
  `evidence/attribute/initial-runtime-readiness.json`. This is environment state,
  not a contribution-route qualification result; relevant services can be
  prepared later when the changed route and acceptance scope are known.
- Later manager check confirms the dedicated `marivo-multisource` daemon is
  stopped; existing profile/config/credentials are present and free disk is
  54.54 GiB. Root is starting that existing dedicated VM (`--activate=false`)
  and will prepare PostgreSQL only for later affected default-route Runtime
  evidence. Raw `runtime-colima-start.log`; no unrelated profile/resource or
  credential mutation. Environment startup is not product acceptance.
- Dedicated VM and PostgreSQL preparation completed, exit 0. Raw
  `runtime-colima-start.log` and `runtime-postgres-start.log`; PostgreSQL 17.11
  reader privileges verified. No Attribute Runtime test has run. Root owns the
  started PostgreSQL profile lifecycle; do not stop other profiles/containers or
  remove volumes. P6 will distinguish environment readiness from route evidence.
- Site build dependencies are being installed with the existing lockfile in
  this worktree (`npm --prefix site ci --no-audit --no-fund`), Node 22.19.0;
  raw `site-dependencies.log`. This prepares later P5 content/build validation
  and does not activate P5 edits or count as documentation acceptance.
- Site dependency preparation completed, exit 0, 546 packages in 7s; existing
  npm peer/deprecation warnings retained. package.json/package-lock.json remain
  unchanged. No dependency/version repair or site-content/build acceptance is
  claimed. Progress file queued in this task's Codex file panel for visibility.

## P3 final candidate preparation

- The compiler-to-runtime import defect is repaired under the recorded pre-edit
  `p3-schema-inference-amendment.md`: pure schema/type inference now has one
  source-free owner in `core/attribute.py`. Full import contracts and the main
  scoped lint/format gate pass (`p3-main-lint-third.log`); no guard, allowlist or
  gate was weakened. Main focused run passed 69 cases in 27.71s.
- Temporal focused run passed 16 cases in 85.79s. Its seven-scope related run
  preserved 141 passes and one failure caused by a long-lived worker importing
  across the in-flight pure-schema move (`p3-temporal-related-final.log`). This
  failure is not declared green: the unchanged command will run again after the
  developers agree on a stable checkpoint. Five temporal source modules pass
  typing. Deterministic full-key witness ordering remains within its planned
  diagnostic scope and will be covered by final focused/static checks.
- Both developers must explicitly stop source/test edits and provide exact owned
  hashes plus the actual payload, numeric and producer-identity handoff. Root
  will then freeze the complete candidate, run unmodified `make test`, and open
  P3 formal review round 1. P4 stays paused; P3 review remains 0/2.
- Final-acceptance preparation assigns `explore_p6_acceptance_map` (Luna max)
  a read-only AT01–AT11 test-assertion map while P3 settles. Its sole output is
  `evidence/attribute/p6-acceptance-map-preparation.md`; it may inspect the
  worktree's existing tests/contracts but must neither edit product/tests nor
  run acceptance/review. P4/P5-dependent AT12–AT14 stay explicitly pending.
- Pre-freeze diagnostic check found paired source/proof failures retain the
  actual source bucket but do not identify the corresponding original selected
  parent. Section 2.1/AT11 requires the complete applicable parent key. Root
  approved a bounded pre-edit amendment (`p3-source-parent-context-amendment.md`)
  reusing the real projection dependency and existing typed full-key mapping.
  Main owns core/compiler/evaluator changes; temporal owns its existing source
  runtime paths. Preserve original source witness/received/repair and checks;
  do not guess a bucket, choose a first match or add a lineage resolver. Record
  the exact closed interface before editing and rerun final checks only after
  both owners agree on a stable checkpoint. This remains pre-review P3 work.
- Coordinator rechecked accepted design, AGENTS.md, both packaged skills and
  site dependency declarations against their frozen hashes: unchanged. Current
  import resolves inside this managed worktree, not the original editable
  checkout. This is isolation/protected-scope evidence, not an execution gate.
- Read-only AT map completed (`p6-acceptance-map-preparation.md`, SHA-256
  `5438ef035b548d95a5c0d38fda667fc873c0b9b4edfa96629e5c7bfa2cdbb1f0`).
  It distinguishes inspected assertions from actual pass evidence and requires
  refresh after final freeze. Root confirmed two mandatory P3 arithmetic
  examples are not yet independently asserted: the global/local B-subgroup
  Fraction oracle and distinct-child A−2B. A bounded test-only developer task
  (`develop_p3_oracles`, Sol high) now owns only a new
  `tests/analysis/materialization/test_attribute_contribution_oracles.py` and
  its evidence plan/results. It will add these exact oracles plus positive
  count/distinct identity and native weighted-mask controls using existing
  fixtures, without product changes or a new test harness. Test failures go
  to the owning developer; all P3 writers must stop before the shared freeze.
  These assertions remain within P3's existing 0/2 formal budget.
- Root read the completed source-parent context amendment. Its closed binding
  carries the real completed projection index; the runtime context maps whole
  typed source keys to original parents with exact uniqueness validation, and
  leaves native error witnesses and window wrappers intact. It adds no source
  statement or source-hook signature. The existing pre-edit release applies;
  paired right-side numeric and window partition failures will verify both
  namespaces before the joint freeze.
- Both production owners confirmed a post-amendment stable checkpoint. Main
  starts its final contribution/rules checks; the test-only developer starts
  its five independent oracle cases. Temporal will rerun the original seven
  related scopes after main finishes, avoiding duplicate active scopes. Its
  latest five-source typing passes. A new paired-witness assertion initially
  assumed a positional source-column order; that oracle defect was corrected
  while retaining the raw failure and actual source ordering. Production was
  not changed in response to that assertion. Full candidate freeze still waits
  for all three owners' final results and stopped edits.
- Main is ready and stopped: **73 focused passed in 11.92s**, all 14 source
  modules typed, 16 paths linted/formatted with full import contracts. Its
  16-path aggregate is
  `a63db2e95effeba8252c10edfc8a022e16d8535983aef92e853ed053224cb73a`;
  exact files are in `p3-main-candidate-snapshot.json`. Root read
  `p3-main-ready.md` and `p3-main-producer-numeric-handoff.md`.
- The independent oracle developer is ready and stopped: **5 passed in 4.93s**,
  focused lint/format/import contracts and diff checks pass. Exact test hash
  `e09fa69494795508c6bd2f6bc6f534ed81a87e6dd1c651f70fd2185b42a5c907`.
  Root read `p3-oracles-development-result.md`. The first commands themselves
  passed but a zsh read-only `status` variable made their wrapper exit 1; that
  separate wrapper failure and first green logs are preserved. Final wrappers
  use a task-specific variable and both exit 0; no test/product fix was needed.
- Temporal's post-amendment **18 focused passed in 12.39s**. Its unchanged
  original seven-scope related run is in progress; five-source typing and full
  scoped lint remain required on this exact candidate. All six temporal paths
  are stopped. Older temporal snapshots/patches remain historical; final
  post-amendment files will use new evidence names. No formal review or broad
  acceptance has yet begun for this P3 candidate.
- Temporal final related result: **148 passed, 95 warnings, 37.60s**, exit 0;
  final five-source typing and six-path full lint/format/import contracts pass.
  All six paths stopped; final exact hashes are preserved in the newly named
  `p3-temporal-final-candidate-snapshot.json`; patch SHA-256
  `10d086508ebd9bb0fb89491acc24a3d12d6610a429257fdc1e8b63bf7637ff7d`.
- Root verified main's 16 paths, temporal's six paths and the oracle file against
  their stopped hashes. Combined **23-path P3 candidate**:
  `39f900e7eeda444c5f81a03369eb1a832d33a1139e3489f6855a397e47fa8269`.
  `p3-candidate-snapshot.json` also freezes all 45 changed/new Python paths;
  complete P3-owned-path patch to HEAD SHA-256
  `6ed97e4b90a788a5690939783e6e4fc787658b5d4bc83e5e85e0428bff2827e0`.
  Root verification: `p3-coordinator-freeze-verification.json`.
- The first coordinator gate launch was stopped by its import-origin guard
  before any test/snapshot: a file-run script resolved the shared environment's
  original editable checkout. Raw startup failure is preserved in
  `p3-coordinator-gate-launch-origin-failure.log`. Correct launch explicitly sets
  `PYTHONPATH` to this worktree and retains the guard. The unmodified `make test`
  is now running; root records before/after all 45 Python hashes. No narrow
  variables or Runtime/release flags were introduced.
- P3 formal round 1 is assigned to `review_p3_round1` (Astra high) against the
  sealed 23-path candidate. Scope: `p3-formal-review-scope.md`. All source/test
  writers remain stopped until the report is sealed; only new evidence may be
  written. P4 stays paused. The combined gate and formal review are not yet
  successful acceptance.
- While that candidate remains read-only, P4 continuation owner may prepare
  only `p4-p3-frozen-handoff-readback.md`: reconcile its existing pre-edit plans
  with the actual frozen P3 payload/producer/numeric interface and identify exact
  path responsibilities. This is read-only preparation, not P3 review or P4
  source release. P4 development still requires accepted P3 plus root release.
- The same Luna max acceptance mapper may write only a new
  `p6-acceptance-map-p3-frozen-delta.md`, reconciling its earlier gaps with the
  now-frozen P3 oracle tests and existing accepted P1/P2 independent probes.
  Do not rewrite the earlier map, run tests/review, or create more test scope;
  separate genuinely unproved exact AT obligations from assertions already
  covered in preserved evidence. AT12–AT14 and final pass status stay pending.
- Frozen P3 unmodified `make test` completed **4,970 passed, 647 warnings,
  380.91s**, exit 0 (total elapsed384.45s). All 45 changed/new Python hashes
  remained identical; worktree import origin verified. Raw `p3-make-test.log`
  and `p3-make-test-evidence.json`. This is default/local evidence only.
- Independent reviewer confirmed all 45 source hashes, accepted design, full
  patch and canonical aggregate. A large-int64 weighted-mean component edge is
  being independently isolated against the successful ordinary complete-parent
  route; it is a suspected failure, not yet a sealed finding. Probe API/assertion
  mistakes are retained separately. No source repair starts before the round-1
  report is sealed; green broad tests do not dismiss a counterexample.
- Root read P4's frozen handoff readback (SHA-256
  `81478f4f374dea152b6cadb3b09e7c41f919c8a20816ba610c8eb223e1799fea`).
  It confirms the actual interface needs no new producer owner and proposes a
  feasible two-developer 20-path split with explicit carrier/validator/codec
  interfaces. The proposal remains read-only until accepted P3 and root release.
- Reviewer independently confirmed the weighted-mean counterexample: ordinary
  complete-parent and default grouped routes succeed; contribution source
  transport overflows its un-widened int64 pair component. The temporal owner
  may now prepare only a new read-only
  `p3-weighted-overflow-repair-preparation.md`, tracing the original native
  numeric owner/masks/type policy and exact repair paths. No tests or source
  edits, no formal-review role or extra round. Actual repair release still
  waits for the sealed complete round-1 report and a finding-specific plan.
- Root read the refined assertion map
  `p6-acceptance-map-p3-frozen-delta.md` (SHA-256
  `3530532238f6880107cbb99bea53ff8bc765f276883ff283144b53a147ba69e6`).
  The exact new numerical examples are mapped; remaining default max/cancellation,
  compare bridge/fresh-denominator and full-domain mapping obligations will be
  reconciled with the sealed independent review before P6 adds any tests. A
  speculative count-by-window cross-product is not required: keep supported
  role/axis proof and the actual window qualification evidence distinct.
- P4 carrier continuation owner `develop_p4_carriers` (Sol high) may prepare
  only `p4-carriers-development-plan.md` against the proposed A/B split. Its
  future A paths are exactly the ten carrier/propagation/numeric/read/test paths
  in the readback; B remains `develop_p4_storage`. No source/test edits or gates
  are released. Both plans must consume the final accepted P3 numeric handoff,
  not the current pre-repair candidate, before actual development.
- Reviewer proved a second P3 integration failure: a pending Attribute selection
  converted through `population_from` and supplied to an explicit new Attribute
  request leaks ancestor preparation carriers into the new request's parent
  completion, causing a key-width mismatch. Same-width wrong-result risk is
  still under investigation. Main may prepare only the new read-only
  `p3-transport-repair-preparation.md`, tracing actual stage/request ownership
  and the minimum carrier-boundary repair. No source/test edits or probes start
  before the full round-1 report is sealed; this is development preparation.
- The second failure is now a proven **P1 wrong result** on the same-width
  control: pending A (region parent, channel groups) selects positive channel A,
  then explicit new B restricts to channel A. B's contribution is20 in both
  fixed/pending controls, but pending returns share1.3333 from ancestor delta15
  instead of fresh delta20/share1.0. The new invocation selects an ancestor
  `AttributeRootInputs` with matching use context. Independent evidence:
  `p3-review-round1-nested-scope.py/.log`. Main's preparation must address actual
  ephemeral carrier ownership; P4 historical method-fact propagation is separate.
  No repair has been released while round 1 remains open.
- Round 1 is now **sealed: one P1 (R1-F1) and one P2 (R1-F2)**, no other
  actionable findings. Report `p3-review-round1.md`, SHA-256
  `079e42c3b2e92028bf52b215cc9da41bdb764fbeeada343cdd2dd25d3300bbcd`;
  independent after-verification reconstructs the patch and confirms all45
  hashes/HEAD/design unchanged. Multi-parent/multi-metric/Null, recursive linear,
  count/window, paired fresh-source and cleanup controls are recorded separately
  from the preserved real failures and probe-oracle mistakes.
- Root read the sealed report and both repair preparations, reverified the45
  frozen Python hashes, then wrote `p3-round1-repair-plan.md` before release.
  Main repairs only graph_local_execution + its contribution tests; temporal
  repairs only weighted component compilation + its temporal tests. These four
  disjoint paths are now released. No new production owner or review budget.
  Native rounded float parent delta remains share authority in the large-value
  case; do not reconcile it to the exact Fraction sum or change native finishing.
- Both developers must agree on a stable source checkpoint before final focused,
  related, typing and full lint checks, stop edits, then provide new repair hashes
  and handoff evidence. Root will freeze a new named candidate, run unmodified
  make test and open the remaining final round2. P3/P4 source dependencies stay
  blocked until both findings are actually closed.
- P4 A's concrete plan was read by root and is ready but paused:
  `p4-carriers-development-plan.md`, SHA-256
  `fd816ba3ad1fa222329969e68cfb36c51794670e3539b36631b23b3f707e674e`.
  It uses exactly the proposed ten A paths and shared closed interfaces; reuse
  of unchanged numeric owners needs no new source edit. Final accepted repaired
  P3 hashes/policy and explicit A/B interface closure remain pre-edit gates.
- Root matched A's proposed interfaces with B's readback and the exact design
  descriptor, then recorded `p4-interface-agreement.md`. The ten-path split,
  concrete codec/validator/read signatures and serialized small interface
  checkpoints are now closed for future release. This introduces no source
  change; accepted repaired P3 and root release remain mandatory.
- R1-F1 first regressions passed numerical/key/read/ephemeral-boundary checks,
  then two new assertions incorrectly assumed flat producer order A,B. Actual
  collected explicit order is outer B then dependency A, while execution runs A
  first. Main preserves the existing seam and corrects only the oracle to join
  by actual input IDs/order. Per-expression P4 fold remains the order authority.
  The raw first failures stay preserved.
- R1-F2 source repair is two lines, after the checked row product. Its three
  focused regressions pass, with source typing/full lint green. Ibis's actual
  sum dtype is Decimal(38,2); the first new tests assumed38,0 and were corrected
  to actual metadata, preserving the failure log. The original five-oracle run
  has four passes and one stale weighted int64 assertion. Root inspected it and
  recorded `p3-round1-repair-oracle-amendment.md` before releasing the original
  oracle owner to update only the exact weighted Decimal dtype assertion and
  rerun its five cases. Direct-ratio assertions and all numeric/mask checks stay.
- All repair owners are ready and stopped: main76 focused/typing/lint green;
  temporal3 new plus154 related/typing/lint green; oracle5 cases in5.45s and
  full scoped lint green. Root read all three new results/handoffs. Original
  collected producer order was preserved; widened final weighted carriers are
  exact Decimal38,2 from actual Ibis metadata. No hidden native-finisher change.
- Root verified exactly **five changed paths** versus round1 (two production,
  three tests), all other40 Python hashes unchanged, owner snapshots consistent,
  and all17 sealed review artifacts byte-identical. Evidence:
  `p3-repair-scope-verification.json`. Root reconstructed all23 original baseline
  files from HEAD plus the immutable full patch, verified every old hash, and
  generated an exact five-path repair delta without editing the worktree.
- Repaired23-path candidate:
  `d06672eb18074f4cc94ba9600b3c3beff9613ad4441a4a67d49e0c43a58fc416`.
  Full patch SHA-256
  `bffe25fb17a7bb1c49c6253ef4032dbed1749094c240da18eb8eb319b42c95ff`;
  exact round1-to-repair delta SHA-256
  `7a50af5ae41b1d578030ffc98edb420e5a52ff0d158ef7229e574ca90ca3131f`.
  Snapshot `p3-repair-candidate-snapshot.json`, delta/verification
  `p3-repair-delta.patch` / `p3-repair-delta-verification.json`.
- Root started a new unmodified make test with explicit worktree PYTHONPATH;
  old gate artifacts are preserved. New outputs are
  `p3-repair-make-test.log` / `p3-repair-make-test-evidence.json`. All45 changed
  Python files are frozen before/after. Same Astra high reviewer receives the
  final formal round2 for both fixes and related regressions; no third round.
  Previously blocked probes may reveal stale producer-order/old-failure-capture
  assertions after the numeric fix; preserve those raw oracles and use new
  independently justified assertions without rewriting the sealed evidence.
- Repaired frozen candidate's unmodified **make test passed4,976 tests,
  647 warnings,263.15s**, exit0 (total265.15s). All45 before/after Python hashes
  match and import origin is this worktree. Raw
  `p3-repair-make-test.log` / `p3-repair-make-test-evidence.json`.
  Final independent round2 is still in progress; P3 is not yet accepted.
- Final round2 is now **sealed and accepted**: R1-F1 P1 and R1-F2 P2 closed,
  no new P0–P3 finding, **21 independent probes passed**. Report SHA-256
  `5e4435b17c1b6a8341812486c0944f2dddd21e8fa6e08146a9d29dc10a84c6be`;
  before/after SHA-256
  `e82421f734f0b865e46513bff0c341240b5e745930cfe1683f882c323155e9d7` /
  `1027402e40b2aa213267eb735488f27542978902f8649d453f68723f6cf8c5e1`.
  Independent exact patch/delta, all45 source hashes and original17 artifacts
  remain unchanged. Native float2048 parent delta and .25/.5 shares are correctly
  preserved in the large-value witness; no reconciliation was introduced.
- Root read the final report and reverified current45 hashes, full patch/delta,
  design/protected hashes and final gate. Accepted record: `p3-accepted.json`,
  candidate `d06672eb18074f4cc94ba9600b3c3beff9613ad4441a4a67d49e0c43a58fc416`.
  This closes bounded private/local P3, not P4/P5/P6, Runtime or release.

## P4 source release after accepted P3

- Pre-edit plans/readback/interface agreement were read before release. Root
  saved `p4-source-release-checkpoint.json`: accepted P3 record, exact20 A/B
  paths and their current hashes (or absent new helpers), with no overlap.
- A `develop_p4_carriers` owns the ten carrier/propagation/numeric/read/storage-
  test paths; B `develop_p4_storage` owns ten descriptor/codec/publication/Store-
  version/recovery paths. Both use Sol high, preserve peers and write a concrete
  local checkpoint before edits. B closes the small descriptor/table/helper
  interface first; A then closes carriers/validator and both coordinate a stable
  complete v2 surface before shared checks. Do not run workers across in-flight
  interface moves or start competing broad gates.
- P4 includes pure-model foundation and at most two formal rounds total, now0/2.
  Full facts remain separate from common metadata/current coordinates; saved
  descriptors stay lazy and payloads are republished under each new Artifact's
  own atomic transaction. Per-expression histories and exact cold numeric policy
  use the final accepted P3 handoff. No new source owner, protected edit, commit,
  service or P5 disclosure change is released without a concrete amendment.
- A focused active-version search found four existing test owners outside the
  original20 paths pinning Store12/output-v1 and one current reconciliation
  module header. Before edits, root recorded
  `p4-version-assertions-amendment.md` and granted these five additional existing
  paths to B, with a possible current-test rename to terminal_v2_schema.py.
  Frozen terminal_v1_frozen.json remains byte-identical historical rejection
  evidence; invalid-version mutation cases stay invalid after the cutover.
  No new behavior/test matrix or A-file overlap is introduced.

- Both P4 developers are now ready and stopped. A final164 owning tests,
 56 accepted-P3 dependency tests,8-source typing and10-path lint passed;
 B102 owning tests,9-source typing and15-path lint passed. Root read both
 results and verified every owned hash/deleted path before integration.
- Root's first gate recorder stopped before tests because the protected-baseline
 paths are a list rather than a mapping. The recorder now converts the exact
 path/worktree_sha256 entries; no product/test change or weakened guard.
 Original failure is retained in `p4-gate-launch-failure.log`.
- Frozen P4 candidate `699bf57529807c94573bb45ab87509a13c51a8835e5402a96737f0f0b80eb22d`:
 26 path states including the deleted old schema-test module. Full patch SHA-256
 `fc695f351bec68c3f83e62fff693ac668a468e9bd9818d0234c52130039397c7`.
 `p4-candidate-snapshot.json` freezes all66 changed Python path states, design,
 protected files and historical v1 fixture. One unmodified make test is running.
- Independent Astra high `review_p4_round1` owns formal round1, including the
 pure-model foundation, complete-history/lazy-read/codec/atomic/cold boundaries.
 It writes its plan before probes and before/after source verification. P5 only
 revalidates its already prepared plan read-only against the actual P4 surface;
 no P5 source release while P4 acceptance is pending.

- Frozen P4 unified make test finished **4993 passed /6 failed /647 warnings,
 263.51s**, exit2; all66 source/test path states remained unchanged. This is
 a failed gate, not P4 acceptance. `p4-make-test.log` and its evidence preserve
 the complete raw result.
- Root traced three telemetry failures to a real Store cutover omission:
 `marivo/telemetry/__init__.py:417` still reads generations/v12, so a resumed
 Store13 Session is labeled new and successful internal-load declarations are
 not suppressed. Three other failures pin current generation12 in zero-init,
 query/artifact-boundary and public-composition tests. The reviewer receives
 the actual failures and independently assesses the cutover finding.
- B is only preparing `p4-cutover-gate-repair-preparation.md` read-only for
 telemetry plus the three exact test owners and a focused active-version scan.
 No repair source edit is released before formal round1 is sealed; original
 P4 candidate/patch/gate and all failure artifacts remain immutable.
- P5 post-P4 readback is ready and stopped: `p5-post-p4-plan-readback.md`,
 SHA-256 `da87f2c9169b99ac57f8402cac8764cdb7b43d570ebd674e8a70161723ee44e1`.
 It proposes concrete public annotation expansion instead of exposing private
 union aliases, and distinguishes full compute from current-data export reads.
 Root has not released P5 source work.

- Formal P4 round1 is sealed with **F1 P1 fractional Decimal scalar** and
 **F2 P2 Store13 telemetry/active-check cutover**. Report SHA-256
 `bdbd1222658460d996941c6cbf3e5053dcc6b0277a5b9fbdc79b6103b42e74ed`.
 Independent before/after74 checks match;15 native roundtrips, reverse history,
 lazy/corrupt/each-input receipts, publication faults/lostack and real detached
 multi-parent all-axis cold continuation passed. Original failing Decimal probe
 and successful ordinary/default controls remain preserved.
- Root read the sealed report and B preparation, verified all66 frozen Python
 states and sealed evidence, then wrote `p4-round1-repair-plan.md` and its
 release checkpoint. B may repair exactly telemetry plus the three new active
 test owners and its existing discovery case. A remains read-only until its
 concrete coefficient/native-promotion plan is accepted; no P5 release.

- B's six original failures now pass, but its proposed function-local layout
 import broke two real architecture contracts through telemetry. Root read the
 raw lint failure and wrote `p4-cutover-architecture-amendment.md`: retain the
 existing lower-layer direct path and update only v12→v13. No dynamic import,
 weakened import rule or new version framework; independent current-layout
 telemetry tests remain unchanged to catch drift.
- A's concrete Decimal preparation is now read. Root released only its finite
 unchanged-source Phase0 native Decimal-child schema/value controls in
 `p4-decimal-phase0-release.md`, with exact five-path baseline. This resolves
 native promotion facts before coefficient source policy; no A edit is released.
- Final P6 local acceptance preparation is narrowed to six exact gaps in three
 existing modules (`p6-local-case-amendment.md`), using the refreshed Luna map.
 It excludes already-covered paired freshness, count/window combinations and
 multi-parent atomicity. It remains planning only, dependent on accepted P5.

- B cutover repair is ready/stopped:6 original failures and102 complete owning
 tests pass; telemetry typing, five-path lint/global import contracts and diff
 pass. Result SHA-256
 `d741f832881e4b38760dc820c38aa28fdb97d5d161b65eb0c73f6db4b7816ed0`;
 snapshot `p4-cutover-repair-final-snapshot.json`. The original two architecture
 failures remain unchanged evidence; final code changes only the active literal.
- A Phase0 passed10 native/default controls on unchanged five-path source.
 Source sums/addition are Decimal38,2 but actual fractional literal's native
 result maps to38,0. Root rejected the provisional concrete-scalar-only typing
 fix, which would alter original promotion. `p4-decimal-native-policy-amendment.md`
 releases one finite nonintegral-value control and asks for a revised native
 literal-operation versus physical-diagnostic separation before source edits.
 This is actual policy verification, not a new review round or provider matrix.

- Root directly checked both Phase0 structured JSON files and found the
 developer's initial native summary wrong: **all ordinary completed outputs
 are Decimal38,9**, while only default Attribute is38,0. The unchanged-source
 nonintegral control also succeeds ordinarily and fails on Attribute's38,0
 rescale. This is an actual Attribute/native output mismatch, not an original
 Metric failure to preserve. Old summaries/raw files remain unchanged; A writes
 a separate errata. No scalar source repair has started.
- Root stopped the fallback38,0-based proposal and assigned Luna max
 `explore_decimal_native_schema` a specific read-only producer/consumer trace:
 where ordinary completion receives actual source type, where Attribute loses
 it, and the smallest same-input-compatible seam for default/contribution.
 This is development exploration within the existing F1 repair, not another
 formal review. Scalar diagnostics and actual native operation output types
 must both be correct; no forced output dtype or new finisher is authorized.

- A's separate errata `p4-decimal-phase0-errata.md` is sealed with SHA-256
 `b6bf711880fb808c9db913d9c57e0823cf476d51622754d14e0d4e1231469f11`.
 Root has not accepted the prior fallback-based source plan. Luna located the
 ordinary actual-type chain: existing terminal fusion -> SourceRead native
 formula -> source_columns -> runtime effective_frame_schema. Attribute's
 requested-endpoint branch skips that fusion; its static fallback loses the
 physical original carrier. Default/contribution branch details remain under
 focused exploration before the final exact source-path amendment.
- A is now preparing `p4-native-scalar-repair-development-plan.md` from those
 facts. It must preserve actual ordinary types/values and no-extra-read common
 input proof; complete stored facts may replay arithmetic in their captured
 actual-native carriers after structural association, matching existing ratio
 policy. No new scalar source edit, forced fallback type or finisher exists yet.
- P6 PostgreSQL developer wrote its concrete plan and stopped read-only; the
 local developer is preparing the exact three-module cases. AT06's invariant
 is observable typed rejection of duplicated full aggregate keys, not an
 implementation-mirroring reason if an earlier original owner correctly rejects.
 Both P6 edits remain dependent on accepted P5 and root release.

- Luna refined its early default hypothesis: default already attempts terminal
 fusion (requestNone/requestedempty); `native_formula` instead rejects the
 injected AttributeKeys Population filter. Parent/key and coverage semantics
 must stay, so a guard deletion is not a valid fix. Contribution uses its
 separate same-statement component bundle and needs native formula carrier
 capture there. This corrects the earlier tentative requested-guard explanation
 before any source edit; final evidence/actual branches govern the repair.
- Root read the concrete P6 PostgreSQL and local development plans. Local plan
 SHA-256 `4d59f6a97971011e0aff0c062f574298626123714f3fa4643161c92b368ee95f`
 specifies four new functions plus three existing-case extensions, using current
 fixtures and SQL oracles. PostgreSQL names one Runtime test with exact integer,
 NULL/presence and fixed-parent semantics. Both developers stopped plan-only;
 no P6 tests/SQL/probes or source edits ran and accepted P5 remains the gate.

- Root read A's replacement actual-native plan (eight production/two existing
 test paths), SHA-256
 `67a2379974b8a517819c159369636fca1067c77a2dfd8fd8e998df96e073ffbc`.
 It adds original formula results to the same qualified input statement and
 carries actual typed values through the existing matrix/consumer/schema/facts
 owners. It preserves exact raw-input lifecycle and qualified DuckDB boundary.
 B prepares the disjoint default native_formula/query_key_fusion/test-frame
 three-path plan; root waits only for that concrete safe-parent/native interface
 closure before writing the exact expanded source release. No source A edits.

- Both replacement plans are read and accepted. B's exact three-path plan SHA
 `1ff47dfad4a5fc547e7c736e21d891c37bfda840820ec65fa2f06b3eafaa0d64`
 preserves the existing lower_native_formula signature, narrowly carries exact
 AttributeKeys external parameters/checks and leaves ordinary qualification.
 A can consume the unchanged low-level original lowerer; no new shared API or
 dependency on B's default admission is needed for true common-input proof.
- Root reverified the original66 states except the five known cutover changes,
 all completed cutover hashes, protected/design bytes and29 sealed artifacts.
 `p4-native-repair-source-release-checkpoint.json` and source-release.md now
 release exact A10/B3 disjoint paths. Each developer checkpoints before edits,
 coordinates source stability before tests, then stops for one combined freeze,
 unmodified default gate and the remaining final P4 round2. No P5/P6 source yet.

- B ran the first real default nonintegral regression in an explicitly agreed
 A-source-stable window. Ordinary four controls returned38,9; Attribute still
 failed at38,0 (`p4-default-native-focused-first.log`, preserved). Actual planner
 parent leaf is the exact private PopulationValues(scope)->FrameKeysInput rather
 than the initial KeyImage-only prototype. B is correcting that exact same-scope,
 single-leaf, full-layout mapping inside its already released query_key_fusion
 path; generic Population remains refused. No expected value was weakened and
 this initial failure is not green/default acceptance.

- B's first real default Decimal regression now passes1 in1.71s after correcting
 the exact private scope shape; its two source files are stable for A's checks.
 This is focused development evidence, not final default/P4 acceptance.
- A's first new five-case run passed the pure control but four True cases failed
 at the existing graph_local_execution formula view selector: it recognizes only
 reduction slots, not the newly projected native full-Metric slot. Root read the
 raw failure/selector/closed descriptor and verified this extra path still matches
 frozen P3 before editing. `p4-native-view-consumer-amendment.md/.json` releases
 only that consumer to A, preserving the accepted P3 numeric-prefix lifecycle.
 A now owns nine production/two existing tests; first failures remain preserved.

- B's complete new default-native regression now passes1 in1.71s
 (`p4-default-native-focused-fifth.log`): ordinary/default actual38,9 USD
 nullable carriers, exact60.25/-19.75, one-sided NULL/presence, retained selected
 East excluding west999, explicit empty zero-axis domain, show0 no read/Run and
 no contribution proof. This fixture uses four default source producers (two
 parent-domain counts plus two fine originals), distinct from true common2 reads.
- B retained all four earlier raw failures: the real missing private scope
 shape; a test-only two-producer assumption; NA comparison oracle misuse; and
 an invalid empty Session.values setup. Later oracle/setup repairs do not change
 expected actual38,9 or numeric values. Two B source files are stable while A
 closes its newly released source-view consumer before related tests.

- A's full contribution/pure-facts modules now pass98 in19.63s. The second new
 test run's two remaining failures were wrong test assumptions that additive
 formulas save FrameLinearMethod; the accepted classifier correctly saves
 FrameAdditiveMethod, while the mean-linear case retains its captured coefficient.
 Those oracle failures remain preserved. Three original formula-error tests also
 exposed overbroad native check uses; A repaired only its released compiler path
 to select the actual Metric/occurrence leaves, retaining original error owner.
- A's nine-source typing and eleven-path lint/import checks passed; relevant
 temporal/input/storage dependencies are running on stable source. Final A
 hashes/result remain pending. No root broad/default gate has started yet.
- B default-native is ready/stopped:81 passed/2 deselected, two-source typing,
 three-path lint/global imports/diff passed. Result SHA-256
 `fb0692582fb7b9b2ae8014f2614bcbfc9e5ae98ffb1c53028ea28802ea2d7fd4`;
 `p4-default-native-final-snapshot.json` records tested A dependency hashes.
 A's later exact check-use filtering occurred after B's test run, so the final
 combined root freeze/gate must cover that actual final source. Five cutover
 after hashes, codec and29 sealed artifacts remain unchanged.

- Both native repair owners stopped. Root read final A/B results and verified
 all19 repair-path after hashes, original before hashes, no unowned Python drift,
 protected/design/history bytes and45 immutable evidence files. Concatenated
 disjoint owner deltas were independently reverse-applied to reconstruct every
 baseline byte and forward-applied to reproduce every final byte. Exact combined
 delta SHA-256 `d0abccf2f4519c46ae5a1df51271c6067e982674b15bc5407e2ff99219fab070`.
- Repaired P4 candidate:
 `6994e9a8ef9758c1f38a570519ac0f00102a21d3b7febc3f94046d2c3ecbf4bb`,
42 P4 path states,72 total changed Python states. Full patch SHA-256
 `46fed78b14693af3b3a7f410f5aad3774c7245f7c7cd8b1aa328a7c8a19cd938`.
 Snapshot `p4-native-repair-candidate-snapshot.json`; handoff/delta verification
 `p4-native-repair-coordinator-handoff.json`. One new unmodified make test is
 running with explicit worktree PYTHONPATH and before/after hash guards.
- Final P4 formal round2 remains unused while this repaired candidate's broad
 result is pending. Root will require a green frozen default gate before opening
 that last round; this avoids consuming its remaining budget on an unverified
 shared source. P5/P6 source/tests remain unreleased and their plans ready.

- The repaired candidate's unmodified default gate completed: **5,004 passed,
  one failed,651 warnings in290.09s** (292.52s wall time). All72 source states,
  protected/design/history and45 sealed artifacts remained unchanged. Original
  `p4-native-repair-make-test.log` and its evidence JSON are retained unchanged.
  The sole failure is the static SQL ownership audit interpreting the newly
  added dataclass metadata replacement in `read_stage` as SQL text rewriting.
  Root requests a concrete cause/minimal repair plan before releasing only that
  existing source owner; no audit whitelist, test weakening or third review.
- The finite filtered two-root Decimal control retained all issued statements,
  independent SQL and actual carriers in
  `p4-native-repair-multi-root-control.py/.log/.json`. All72 frozen states were
  unchanged. Ordinary current parent/fine already fail in the existing host
  `_combine_program` Decimal(38,0) conversion; default/True retain that owner.
  The script's trailing scale9 hypothesis is disproved by actual integral base
  outputs38,0 and is explicitly corrected in the JSON interpretation. This is
  not evidence of an Attribute-only native-type regression; no extra matrix,
  forced numeric finisher or source edit is justified by this control.
- Root read and accepted `p4-native-sql-audit-repair-plan.md`: extract the exact
  returned-column capture into one concrete typed AttributeRootLayout helper in
  the same source module. Statement compilation/submission stay in `read_stage`;
  no audit/test/manifest change. Before editing, all72/protected/design states
  were verified and the one-path release recorded in
  `p4-native-sql-audit-root-release.json`. The remaining P4 review is still unused.
- The one-path helper repair is stopped: unchanged SQL audit8 passed5.59s,
  full contribution/pure-facts modules98 passed25.40s, typing/lint/import/diff
  passed. Source after SHA-256
  `966c664f30e2f930c33e9c71d1307f912a92b6923574c8da2592b6a1014a7d44`.
  Result `p4-native-sql-audit-repair-result.md`; root read its exact patch,
  verified71 untouched states plus the released after hash, and independently
  reverse/forward-applied the delta to reproduce the before/after bytes.
  `p4-sql-audit-repair-coordinator-handoff.json` freezes72 Python states and57
  earlier artifacts. A separate unmodified `make test` is running; prior failed
  gates remain sealed. Only root ledger/evidence preparation continues.
- New frozen P4 candidate:
  `ad8fab69066bbffd9c95bf1bad44155fc08886f4f920faff74f1f757ef752524`;
  full patch SHA-256
  `cc7bec4e4e727ff9451714636892a837b283a44ba90d047c8e91628c26874758`.
  `p4-sql-audit-repair-candidate-snapshot.json` retains42 P4 scope states and72
  total changed Python states. The SQL audit itself is byte-identical.
- P5 API owner refreshed the final native-carrier handoff read-only in
  `p5-final-p4-handoff-readback.md`: actual native type is authoritative,
  Decimal38,9 is witness-specific, default's four producers do not prove the
  True two-execute common-input contract, and qualification is unchanged.
  Root read and accepted this clarification. A disjoint Sol/high docs owner is
  preparing the exact current-document plan; API owner retains source/tests/Help
  and generated API validation. Both remain stopped until accepted P4.
- Luna/max prepares a compact AT01–AT14 evidence index from existing reports
  and concrete test names. It may write only new planning/evidence Markdown;
  actual final acceptance and Runtime claims remain pending P5/P6 execution.
- The final repaired P4 candidate's unmodified `make test` is **green:5,005
  passed,651 warnings in271.19s**,280.94s wall,exit0. All72 before/after source
  states match; protected/design/history and57 sealed artifacts are unchanged.
  Original earlier failures remain intact. Root verified these results before
  releasing the final formal round2 to the same Astra/high reviewer in
  `p4-final-review-root-release.json` (62 immutable evidence files).
  No third P4 formal round is available and P5 remains dependent on acceptance.
- Root accepted `p5-docs-development-plan.md`, SHA-256
  `3b8905821ef8be3c8f207bafa12d63328501b947d1cb58860309ad25592d732c`:
  docs owner receives exactly ten current API/spec paths and six latest EN/ZH
  pairs. API owner retains every product/test/Help/export change, actual public
  examples and API generation. No overlapping authored files. Root separately
  owns a future design-intro status-only change with the P0 contract body frozen.
  Both developers are stopped pending accepted P4 and the exact root release.
- AT index draft `p6-at-evidence-index-draft.md`, SHA-256
  `b86c091431fa49d48acff2228d44c9bbe516b89e3180974d196d3f7bbc49a1a4`,
  maps concrete accepted/private cases and pending public/Runtime evidence.
  Root read the map; it is preparation only and will receive a separate final
  successor rather than retroactively rewriting its pending-stage statements.
- P4 final round2 is **No findings**. Report SHA-256
  `1df0a0ef79b25e590a0398ac4863ddffea62e373f616cc1ec88eee722280ed72`;
  new review-evidence seal SHA-256
  `95321a3a0db65f3b1c80bafd3faabde4549e343a091ee1d81063c6851749dd75`.
  Both before/after verifiers independently checked141 path states with no
  mismatch. Root read the complete report and verified current72 source states,
  protected files and62 sealed artifacts. `p4-accepted.json` accepts the exact
  `ad8fab...` candidate; no third P4 review was used.
- Independent final evidence closes scalar/native-carrier and Store13 issues;
  real two-metric Decimal output survives detached all-axis reduction, deletion
  of original files/source/models, cold recovery and republishing. Persisted
  history/lazy/each-input receipt/fault/lost-ack boundaries pass; cutover14 pass.
  One raw review-oracle failure omitting native half-even intermediate rounding
  remains intact, with a separately corrected successful independent oracle.
  Unfiltered multiroot ordinary fine/default/True retain the same original host
  conversion failure, not blanket qualification or an Attribute-only regression.
- Root now releases P5 through `p5-root-source-release.json`: exact33 API/test
  paths and22 disjoint docs paths with before hashes and whole-worktree baseline.
  Sole public rename, eleven concrete exports, static Help fields/shared show,
  existing bounds, verified examples and current bilingual docs are authorized.
  New context journey raw metrics/cap proposal must be reviewed before updating
  its new baseline entry; existing budgets and protected skills remain unchanged.
  Root owns design-intro-only metadata, candidate freezes and formal gates.
- Coordinator's P5 status plan/result are
  `p5-design-status-plan.json` and `p5-design-status-result.json`. Only the
  design opening now points to current API docs and this stage ledger while
  retaining the historical P0 baseline. New whole-file SHA-256
  `86acfd167e80109ad1b689444dc1f7dfc0514c0e0742b0ecef230633b212c63d`;
  every byte from section1 onward remains identical, body SHA-256
  `4da341645253018ee17c6d5f35024b204e67c55afa88826c94de0e8bb300fc19`.
  Both P5 developers were notified; accepted P0/P4 historical hashes and sealed
  review evidence were not rewritten. Scoped diff whitespace check passed.
- P5 first focused surface/CLI/example run:192 passed,2 failed
  (`p5-surface-first.log`, retained). One failure is a new traversal test stopping
  at valid static attribute pages. The concrete public unsupported-expression
  error also still points to root Help rather than the now-published callable.
  Root read the error producer/consumer and released only `core/model.py` in
  `p5-help-repair-owner-amendment.json`: three exact changed-scope locations
  select their real canonical Help pages; unrelated CoreRuleErrors retain their
  current root target. Existing owned tests verify the actual repairs. No
  exception/numeric/proof/codec behavior change or generic routing framework.
- New Attribute context journey was recorded separately before baseline edits:
 13,682 raw /13,494 normalized characters,11 pages,zero repeats; raw collection
  test1 passed1.19s but was not budget acceptance. Root inspected all11 outputs
  and independently verified the normalized sum, then approved only the new
  separate cap14,000 characters/11 pages/zero repeats (506 characters headroom)
  in `p5-attribute-context-cap-root-review.json`. Existing baseline metrics,
  outputs, normalized outputs and budget references must remain identical.
  The new journey is not a historical savings claim; an unrecorded enforcement
  run and independent P5 review remain required. Existing Help limits stay fixed.
- P5 API developer retained all34 pre-edit hashes but not byte copies. Before
  reconstructing any baseline, root wrote `p5-baseline-recovery-plan.json`, then
  recovered all34 exact before files from HEAD or individually applied sealed
  accepted-stage candidate patches. Every recovered byte sequence matches the
  root's pre-edit hash; no original-checkout code or inference was used.
  `p5-proven-baseline-manifest.json` records each source/patch hash and the
  evidence-only archive. This enables a proven P5-only reverse/forward delta.
- P5 final surface checks197 passed9.89s; broader focused489 passed51.51s;
  typing159 sources passed. New and historical context-budget enforcement2
  passed; verbatim EN/ZH tutorial plus three guide blocks2 passed3.31s. Source
  and22 authored docs stopped before generation. First API generation invoked
  shared-venv Sphinx without explicit PYTHONPATH and imported the original
  checkout, causing19 import failures. `p5-docs-api-first.log` is retained as
  invalid-environment evidence; corrected API/site runs must pin this worktree.
- Both P5 owners stopped with all required checks green. Source result SHA-256
  `83e4245abfc07c578c9513555587472f684fe5c19dfc5eed2078a234ce08dca1`;
  docs result `9d2d9fbd535b5eace835b5b7e06f6007213646ce5d770a8f2a7b8f88142b1651`.
  Correct-worktree API generation and strict site build/content/postchecks passed
  (381 content files,308 original routes,44 latest usage pages,343 targets).
  Final public checks197 passed; final docstrings/exports/verbatim EN/ZH guide
  and same-tutorial refusal13 passed; final159-source typing and28-path lint/
  import checks passed. Original invalid-environment and two real Sphinx example
  warnings remain retained; the final public docstring code block is corrected.
- Root independently verified both owner baseline-relative deltas and all57
  before/after states by reverse/forward application; no unowned worktree drift,
  protected change, prior-artifact change or P0 body change exists. Frozen P5
  candidate `9bf4837ceef016d7c8063cd223e8f96328993058fd2af371bc4ef53b6df399a2`:
 52 P5 changed paths,116 total candidate path states,180 immutable artifacts.
  Combined stage delta SHA-256
  `d7652d076c7dd2c06cdba42ff288e0057bcdcd42df75633184934ad87a7769b6`;
  complete HEAD patch SHA-256
  `48d446ab5b8e917961e357e999e0a0f8eae17c8a57c43f875065a01bee000383`.
  `p5-coordinator-handoff.json` owns the exact freeze. Root starts unmodified
  `make test` and independent Astra/high formal P5 round1 on that same immutable
  candidate. No source/docs edits may race either; coordinator ledger remains
  explicitly mutable metadata. P6 test edits remain dependent on accepted P5.
- P5 full frozen default gate is **green:5,035 passed,651 warnings in296.69s**,
 299.38s wall,exit0. All116 candidate states and180 protected evidence files
  remained unchanged. Formal round1 is **No findings**, report SHA-256
  `5a7f990a2902e4e5d941b9311d14b9475865eaed912a2cc791e34408e46ad24d`.
  Independent review resolved11 types/47 fields,224 changed-page closure and177
  pages from root-only discovery; getter remained forbidden, four CLI pages,
  real tutorial/default/True/refusal/continuation and normal budget enforcement
  passed. Its first overstrict CLI equality oracle remains separately preserved.
  Source,57 owned states,protected files and180 sealed artifacts match before/
  after. Root read the report and verified all current hashes and review seal.
- `p5-accepted.json` accepts exactly `9bf483...` with1/2 rounds used. No fix or
  second formal round is needed; the cap is a maximum. Runtime/P6/release are
  not claimed by this public-surface acceptance.
- Root releases P6 only now through `p6-root-test-release.json`: three local
  modules and one existing PostgreSQL module, with actual before bytes saved in
  `p6-proven-baseline/`. Both concrete plans are accepted; no product correction
  is preauthorized. AT06 accepts the correct earlier original typed error owner
  if it rejects a duplicated full key, rather than forcing a private reason.
  Public syntax, independent SQL, exact keys/types/units/NA/presence and no-Run
  boundaries remain required. Existing root-owned PostgreSQL is reused; no new
  services/provider matrix, source/doc edits or competing broad gate.
- P6 local first focus retained3 passed/4 failed; three assertions expected
  pandas Int64 rather than the existing exact Arrow int64 export carrier. A
  carrier-corrected run retained5 passed/1 failed at an overly narrow error
  owner assertion. Root approved two concrete oracle amendments before edits:
  mixed Population tuples preserve original SemanticExpressionError and its
  kind/received/repair; the unchanged full compound parent-row clone preserves
  original MaterializationError at dataset.stage_output and exact key uniqueness
  fields. No wrapper/private reason is forced. Final focus7/7 passed15.28s;
  full three-module checks are running against frozen test hashes.
- P6 exact PostgreSQL Runtime first run is **1 failed in2.04s**, on unchanged
  accepted P5 source and unchanged formatted test bytes. Native cursor SQL and
  literal full-key oracle agree: EAST/A left9007199254740993,right9007199254740933,
  change60; public default gives rounded left9007199254740992.0 and change59.0.
  Fixed parent selection, fresh source mutation, lazy construction/show/export
  and no-default-facts boundaries passed before the numeric assertion; later
  assertions are not claimed. Raw log/JSON and exact failed bytes remain sealed
  in `p6-postgres-first-failure.md` and companion artifacts. A later unreachable
  dtype assertion was corrected to existing Arrow int64, with no numeric change;
  the current stopped test has no passing Runtime evidence yet.
- Root read semantic overview/datasource owning contracts and live datasource
  Help before any source release. They explicitly describe DuckDB-only Arrow
  conversion and accept existing provider/Ibis numerical transport loss; this
  observation is therefore not yet attributed as an Attribute-only regression.
  Static trace points to PostgreSQL's pandas inference before graph_exchange.
  Luna/max is examining the installed native Arrow path; Sol/high prepares an
  exact pre-edit source/test/current-doc plan and finite ordinary/native cursor/
  native Arrow controls. Any PostgreSQL transport guarantee is an intentional,
  bounded owning-contract amendment, not a float-to-int repair or silent oracle
  weakening. Product source remains frozen pending that evidence/release.
- The same focused contract read found current semantic overview architecture
  statements still naming Store12. They will be explicitly included in the
  owning-doc plan as current Store13 alignment; historical records stay intact.

- P6 local owner stopped green:108 passed41 existing warnings in91.37s; root
  independently verified all three final test hashes and all309 product Python
  states still match accepted P5. Local final delta/result remain sealed.
- Root read the final Luna transport report and approved finite pre-source PG
  controls only (`p6-postgres-control-root-release.json`). Candidate contract
  uses one provider-wide native Arrow path, with collect_bounded explicitly
  checked; diagnostic purpose strings must not select transport. Optional Help/
  context-baseline changes are excluded because backend docs own the new fact.
  One existing overflow witness joins the finite controls to establish actual
  failure ownership before any source release. No product edits yet.

- Root independently reverse/forward-applied the local P6-only patch in a
  temporary directory and compared every before byte to the root archive and
  every after hash to current tests (`p6-local-root-delta-proof.json`).
- Successor AT01–AT14 final draft/matrix is complete (14 entries,57 hashed
  evidence references), with P4/P5 accepted and final PG/freeze/gates/Astra/
  cleanup explicitly pending. Original drafts and failures remain intact.
- Root final coordinator plan/scripts are prepared, not yet executed. Nine
  proposed transport source/test/docs paths have exact pre-edit byte copies
  in `p6-proven-baseline/`; no source release exists yet. The first finite PG
  control run confirmed ordinary observe loss, then stopped at an over-requested
  sort capability in its setup. A successor removes that setup-only request;
  no product or numeric-oracle change is authorized by that correction.

- Root read all finite PG conclusions and actual native mixed/checked-query
  records. Native Arrow exactly preserves full keys/int64+NULL; old ordinary
  observe also rounds, and mixed DATE/timestamp representations change natively
  while finite values/instants agree. Bare SUM overflows Arrow, but actual public
  range-checked SQL returns NULL+check and preserves signed-int64-sum ownership.
- The unchanged existing Runtime overflow node independently fails only old
  returned-row count1 versus actual2; compute/persist errors and no-publication
  assertions pass. This remains OPEN for separate Luna producer/contract trace,
  not attributed to the proposed transport and not silently relaxed.
- `p6-postgres-transport-root-release.json` releases exactly nine checkpointed
  paths: PG owner source+adapter-test, separate Sol/high owner seven docs. This
  intentionally extends provider-wide PG integer transport, accepting native
  DATE/Python-date and microsecond timestamp representation within owning native
  type limits. No purpose dispatch, numeric repair, fallback, Help/budget or
  packaged-skill change. Existing overflow oracle remains locked pending trace.

- Minimal PG source branch/new mixed adapter test are green, including the new
  public Attribute Runtime witness. Full PG module retained14 passed/4 failed:
  two native-pandas test oracles lose integer bits, grouped overflow count is a
  historical stale scalar expectation, and five-metric read count is under trace.
- Luna traced overflow to historical migration commit2bed6022be, which removed
  rollup but retained one-row assertion. Current grouping has exactly A/B rows;
  no product topology repair is indicated. Root releases only exact[2] plus
  native cursor replacements for scalar/drill oracles under the pre-edit test
  amendment plan. Exact literals/keys/NULL/error/publication assertions stay.
  Metadata node remains locked. The report hash references the pre-edit control
  snapshot, not the now-changed Arrow source. All raw failures remain sealed.
- Seven current transport docs stopped after exact reversible-byte/link/static
  checks. They bound the guarantee to governed SourceSession/bounded reads,
  describe native representation limits and preserve direct Ibis/raw SQL owners.

- Root read the exact seven-doc delta/result and verified current after hashes.
  Minimal transport source is exactly one provider condition, hash7a77cced...;
  new Attribute Runtime1 and adapter Runtime1 pass; existing PG adapter1 and
  unchanged default adapter27 pass; source typing/lint pass.
- Luna traced the five-metric sequence to five current SourceReads with no
  registered merge equivalence; the old single-read Query/rollup assertion
  survived historical migration. Root pre-edit metadata amendment releases only
  exact[1,1,1,1,1], retaining all values and actual collation-count2 assertion.
  Final full PG module must pass after all four bounded oracle corrections.

- Remaining metadata assertion reached actual0 versus historical2. Luna traced
  ordinary observe to no original-string qualification, even with grouped keys;
  only current Attribute endpoints own that path. The ordinary-only fixture
  plan is blocked; a current public Attribute successor must exercise cache
  reuse per endpoint, preserving nonvacuous metadata coverage.
- Pre-final P6 F1/P2: Luna found provider-wide PG Arrow would regress supported
  JSON authoring preview (decoded dict/list versus native Arrow string carrier).
  Root records `p6-postgres-typed-read-root-finding.md` and blocks that candidate.
  Successor plans will narrow PG Arrow to the captured canonical typed Analysis
  requirement; authoring/basic/bounded PG stays native pandas. Purpose strings
  remain diagnostic. Prior provider-wide release/results are historical and
  superseded for final acceptance; no extra formal round has been consumed.

- Root read and releases both compact successor plans in
  `p6-postgres-qualified-root-release.json`. Private issued proof captures actual
  immutable requirements; only canonical query.source_read v1 selects PG Arrow.
  Existing one-fixture adapter test now preserves JSON basic/bounded reads under
  query-like diagnostic labels and typed integer reads under a non-query label.
- Current metadata fixture moves to public Attribute, where original-string
  qualifications are actually consumed. Five unmerged metric reads per endpoint
  times two endpoints/four native joint groups imply ten four-row source reads;
  each PreparedSources caches two physical string qualifications, four metadata
  calls total. Exact independent full-key left/right/zero/presence assertions
  stay. Unexpected contrary traces must be classified, never silently relaxed.
- Qualified successor seven-doc amendment stopped after exact-byte/static checks;
  original provider-wide archive and all raw failures remain unchanged.

- Qualified-source Runtime checks pass: JSON/opposing-purpose adapter1, P6
  Attribute1, existing PG adapter1, default adapter27; typing/lint/diff green on
  the captured typed-proof candidate. The first metadata candidate validates all
  native values/keys/deltas/presence but its new exact physical count was wrong:
  actual ten1-row original-parent stages plus ten4-row fine stages. Design§8
  promises no default PG execute count; root removes only that new internal-
  schedule assertion, retaining raw failure and independent source-count note.
- Reached metadata count is actually0. Root independently traced collect and
  requested branches: defaultFalse never enters original contribution-source
  preparation; True calls DuckDB-only prepare_attribute_sources before the
  remote original-string qualifier. Therefore the old Query/rollup PG metadata
  assertion has no current public consumer. Earlier exploration inferred this
  reachability incorrectly; those reports stay historical, not acceptance.
  Root releases deletion of the retired metadata node, preserving current native
  grouped/scalar/CHAR/newP6 tests; no unsupported source proof/cache is added.
- P6 F1 follow-through: collect_bounded manufactured its PhysicalRequirement
  from diagnostic purpose. Exact query.source_read would defeat the intended
  split. `p6-postgres-final-owner-amendment.md` releases fixed existing basic
  qualification and an exact-collision JSON witness, with purpose kept only on
  compile/log. This is the final source-owner correction before required gates.
- Root corrected a dispatch mistake: messages to a completed developer had not
  resumed execution. followup_task resumed it; current agent state is monitored.
  Source/docs and all evidence stayed intact during that wait.

- Final PG owner STOPPED with all checks green on adapters c76a4814..., adapter
  test0bd873fb..., PG moduled4b0d8e2...: remaining full module17 passed6.80s,
  new adapter1, P6 Attribute1, existing adapter1, default adapter27, typing/lint/
  diff. Final root-baseline3-file delta c41f26e9... reverse/forward exact; all
  failures remain sealed. Final owner result SHA
  f787bf5ffa6d0e027ee437bda565fd0f649e772601ee30718fa3a3611a3910af.
- Root independently verified all13 P6 before/after byte states and absence of
  unexpected changes; froze candidate
  9f55219e56134c94cb742f5607cf8625145cf6bd907e71bab1bb312849306ac9:
  123 changed path states,1235 whole-tree states,411 immutable evidence files.
  Full HEAD patch SHA de0e44aa7d60a78ded1ba79535b35499dd4ffdf6180e15c702c5f6b4a24f5b05.
  Protected files and all prior accepted artifacts remain unchanged.
- Unmodified un-narrowed make check-agent started on this final source. The
  separate normal two-context slow enforcement is running. Astra/high final P6
  formal round1/2 was dispatched on the same frozen handoff; current final review
  brief supersedes preliminary metadata assumptions. No source/tests/docs edits
  may race checks/review; this coordinator ledger is explicit mutable metadata.

- Final unmodified make check-agent is **green:5,040 passed651 warnings in
  229.18s**,248.78s wall; full lint,314-source typing and strict API generation
  passed. Separate normal context budgets2 passed15.38s; site content381 files,
  build359 pages and postchecks308 routes/44 latest pages/343 targets passed.
  Every gate preserves all1235 worktree states and411 sealed artifact guards.
- Astra formal P6 round1 is **No findings**, report SHA
  91b3c39716d874baa9b17f8397a5d42005a1a369201dde07d96f0fe3c6fc4baa;
  seal SHA54d545f9d64e84007a480bed4690b9e3d6c3caa84cd352ef1d8eda90e133939a.
  Independent10 local/3 PG Runtime tests pass. Actual public two-metric source
  result is reduced60.5, original attachments/model/database removed, and fresh
  noCatalog/noSource process computes/compares/re-publishes/reopens121 while
  preserving both original method facts. P6 F1/P2 JSON boundary is independently
  closed, including exact-purpose collision. No second round is needed.
- Cleanup read current Docker inventory and PG client groups: only task PG
  running, no other clients. Stopped only postgres-analysis and then dedicated
  marivo-multisource VM after confirming no remaining containers; default profile
  remains stopped, no volumes deleted. Evidence p6-environment-cleanup.json.
- Root read review, verified all123/1235/13/5/411 states plus14 review artifacts,
  final branch/HEAD and empty staged index. Final AT01–AT14 index/matrix and
  p6-accepted.json accept candidate9f55219e with no unresolved P2+. P0–P4 used
  2/2 formal rounds each, P5/P6 used1/2 each. All pending draft slots are now
  filled in successor final artifacts; historical records were not rewritten.
  No commit/push/release/original-checkout merge or protected edit occurred.

## Local commit and latest-main integration

User authorization (2026-10-20): commit this worktree's Attribute changes using
`commit-attribution`, then fast-forward local main; resolve integration according
to the latest local main optimization goals. No push.

Task I1 — plan before development:

- Baselines: Attribute `c2cef31ec909d2205726d7d743ca14665818ed8a`; local main
  `a56cb534eee34ecfdb8032229b53ec26128e9922`. Preserve main's unrelated document
  drafts and the original accepted candidate/evidence.
- First create the scoped, normally hooked Attribute snapshot commit. Reuse the
  existing matching P6 acceptance for that unchanged source snapshot.
- Rebase the unpublished task commit onto current local main. Preserve main's
  simplified Population API, unified Deferred predicates/grouping, explicit
  observe domains and interval axes, correlation statistics, and trimmed public
  surfaces. Adapt Attribute to those owners instead of restoring removed APIs.
- Coordinator owns this ledger, Git integration and final evidence. Exploration
  uses Luna max; development uses Sol high; independent review uses Astra high.
  Assign disjoint source/test/docs ownership after conflicts are known.
- Preserve both main's interval persistence and Attribute's method facts with one
  explicit current storage contract; choose the format version from actual wire
  compatibility rather than coincidental version-number equality.
- Verify conflict resolution with typing/lint, focused Attribute and affected-main
  tests, then unmodified `make check-agent` for the integrated source. Run relevant
  additional checks only where the integration changes a previously accepted
  boundary. Keep local, selected Runtime and release evidence distinct.
- Independent review: at most two rounds for I1; fix all P2+ findings before
  accepting. Stop closeout if P2+ remains after round two.
- Final normal commit hooks remain enabled. Fast-forward main only after source
  acceptance, preserving pre-existing main WIP. Verify both HEADs, commit scope,
  attribution and remaining WIP; do not push.

State: snapshot verified against all 123 frozen candidate paths; index empty.
I1 development and formal review have not started.

### I1 execution checkpoint

- Normally hooked snapshot commit: `dbe96a16e881fb8a38811e82147ca5827295b32e`.
  All 123 candidate path digests matched frozen P6; the additional progress file
  records the local integration plan. Git detected the schema-test rename, so
  the commit has 123 changed file entries (124 no-rename paths). Hooks passed.
- Recovery branch: `codex/attribute-accepted-snapshot`. Rebase onto main
  `a56cb534eee34ecfdb8032229b53ec26128e9922` is paused for semantic integration.
- Read-only explorations: `explore_main_contracts` and
  `explore_main_frame_surface` (`gpt-6-luna`, max). Their current producer/consumer
  findings establish Grouping/Deferred/private Population captures and the
  closed storage-format collision.
- Planned development owners (`gpt-6.1-sol`, high), each with a persisted
  pre-development plan under `evidence/attribute/i1-*-plan.md`:
  `integrate_pipeline` — binding/compiler/source/Population dependency execution
  and focused input/contribution tests; `integrate_frames_storage` — Frame,
  correlation coexistence, method facts/cold storage and related tests;
  `integrate_public_docs` — Session/Help/exports/current docs and surface tests.
  Coordinator retains ledger, Git index/integration and acceptance evidence.
- Version decision: Store 14 / `marivo.frame_output/v3` / `frame-output-v3` /
  `frame.v3`. Main's closed Store13/v2 includes interval directories; Attribute
  adds required method-fact descriptors. Old Store13 is refused unchanged;
  neither a permissive decoder nor a compatibility alias is introduced.
- Endpoint adaptation: `where` is Deferred, allocation is consumer-owned
  `memberships`, and counts carry their own Population. Attribute's explicit
  endpoint `during` uses the current time-axis binder privately; shared
  observe does not regain removed public arguments.
- Main's seven existing document WIP files were snapshotted without mutation in
  ignored `evidence/attribute/i1-main-wip-initial.json`. Their drafts remain in
  the main checkout. No main branch update or push has occurred.
- Formal I1 reviews: 0/2. Development and integration checks pending.

### I1 development verification checkpoint

- All text conflict markers removed; rebase index resolution remains coordinator
  owned. Protected AGENTS/packaged skills match latest main exactly.
- Partial development checks: 132 closed protocol/publication/method-fact cases
  passed; owned Frame/storage typing (25 modules), pipeline typing (29 modules),
  public typing (22 modules), and scoped lint passed. These are iteration evidence,
  not yet the final integrated gate.
- New exact endpoint TimeScope ordinary/contribution witnesses and interval/paired
  interval parent early-rejection witnesses passed (4 cases). Attribute parent
  admission stays ordinary/calendar/paired-calendar; no new projection encoding
  architecture was added. Method-fact interval history cold continuation passed.
- Current main's removed `get_or_create` and resume(by=...) calls are being ported
  in tests/isolated workers. Raw earlier failures remain in the i1 logs. Two
  ordinary-observe check-domain expectations require exact current-main baseline
  classification before choosing a correction; no oracle weakening by assumption.
- Site content verification: 381 required files passed. API/site builds remain
  pending final source. No formal I1 review yet (0/2).
- Root started the previously stopped `marivo-multisource` VM and only its existing
  `postgres-analysis` profile for selected post-integration Runtime checks. PG17.11
  reader privilege qualification passed. These task-owned starts must be restored
  to stopped state after checks, subject to no other users/containers.

### I1 formal round 1 and repair plan

- Frozen source: `52dda0c5d667f73c42bf2aee57630e650fc2ebc5915f451a467f9ebc9f4ac35b`;
  123 candidate entries / 1,248 tracked files unchanged throughout round one.
- Astra formal review 1/2 (`evidence/attribute/i1-review-round1.md`): two P2
  findings, one P3. F1: multi-identity compound count domain loses key grouping;
  F2: ArtifactDigest still advertises v2 for v3 output; F3: two current doc
  references still say endpoint members. Independent 5 passing probes include
  contribution/correlation and detached source-forbidden cold continuation.
- Unmodified full check-agent round1: lint/imports and typing passed; default
  tests stopped with 12 failures / 5,117 passes. Every failure reports the shared
  dataset.population_coverage check in methods/local.py for existing explicit
  output-domain followups. Raw failures: i1-check-agent-round1.log. API stage
  was not reached. This candidate is not accepted.
- Repair plan before edits: pipeline owner fixes F1 with authoritative complete
  key-group metadata and one direct-observe regression; Frame/storage owner
  fixes F2 and classifies/repairs the shared output-domain coverage regression
  against actual current main contracts (retain Attribute contribution partition
  proof and strict errors); public owner fixes F3. Keep all original failures.
  Do not restore old Population/members APIs or weaken assertions for green tests.
- Re-run each affected focused scope and all 12 broad-gate failures before
  re-freezing. Final formal review will be round 2/2; no third round. Run the
  unmodified full gate on the final repaired frozen candidate and repeat selected
  affected Runtime/public build checks. Unresolved P2+ blocks local closeout.
- The three selected PG17.11 Runtime cases passed on round1. Root verified no
  other PostgreSQL clients or VM containers; task-started PG/VM were stopped and
  both Colima profiles are again stopped. Volumes preserved.

### I1 repaired source freeze for final round 2

- F1 repair: authoritative count MemberInput.axis_mapping from prepared
  SourceSchema.key_groups is carried through parent, parameter and endpoint
  planning. Each MemberCount producer validates every requested coordinate; the
  last mapping owns the last count-only output schema. No physical-width guess.
  Current independent F1 probe and direct-observe regression pass; 180 related
  tests, typing3 and lint4 pass. Evidence: i1-f1-repair-result.md.
- F2 repair: public ArtifactDigest literal/default/validation now v3, with a
  persisted/reopened public assertion. F3 fixes both endpoint memberships prose
  references and states counted Population ownership.
- Main output-domain regression repaired by removing the unowned global coverage
  rejection. Main and Attribute section3 both permit selected candidate subsets;
  original actual-input assignment/component/partition proofs retain their owners.
  All12 existing main failures pass unchanged. New default candidate tests assert
  exact selected/empty results. No existing main test oracle was rewritten.
- The broad repair iteration recorded384pass/1pre-fix F1 test-label mismatch; the
  subsequently corrected current F1 and F2 nodes both pass. This iteration is not
  substituted for the required final full gate on one frozen source. Accurate
  evidence: i1-frame-repair-result.md, i1-r1-f3-docs-result.md.
- Final allowed formal review will be round2/2 on the new freeze. Full check-agent,
  selected PG Runtime, API/site builds and postchecks remain final closeout gates.

### I1 final source acceptance and local Git finish

- Accepted source candidate: `ff56b60ae3893f63907a5447d33b01567c822240d9b06668c9b246f9a2f7c971`.
  All124 candidate entries and1,248 tracked files match the round2 freeze, with
  only this progress metadata mutable.
- Unmodified `make check-agent`: **5,130 passed**, full lint/import contracts,
  full default typing targets and API documentation passed. Raw final log:
  `evidence/attribute/i1-check-agent-final.log`.
- Final selected PostgreSQL17.11 Runtime: **3 passed** (Attribute exact/fixed
  parents, native period binding, mixed-value integer transport). The earlier
  P6 nineteen-node evidence belongs to its original candidate and is not counted
  as a new nineteen-node integration gate.
- Site content381 required files passed; normal build359 pages passed; postbuild
  verified308 original routes,44 latest usage pages and343 internal targets.
- Astra formal round2/2: **No findings**. F1/F2/P3-doc F3 closed. Independent
  **12 passed** include multi-count/reversed-axis/fixed-parameter/compound parent,
  source-forbidden detached cold continuation, signed selected contributions and
  subset/empty output retention of original method facts. Actual overlap and
  duplicate source identity proofs still fail before publishing.
- Root verified no other clients/containers before stopping task-started PG/VM.
  Both Colima profiles returned to stopped; no volumes deleted.
- Protected files remain unchanged. No installed-package, full provider matrix or
  release acceptance is claimed. Current parent admission/qualified contribution
  bounds are disclosed in the owning current docs.
- Local commit/fast-forward operations follow this source acceptance with normal
  hooks and scoped staging. Their final commit IDs, branch equality, source guard,
  restored main WIP and post-merge smoke result are persisted in
  `i1-local-finish.json` under the raw evidence directory below. Git history is
  authoritative for final commit identity. No push is authorized.
- Raw integration evidence remains intentionally ignored and durable in
  `/Users/lichengxiang/.codex/worktrees/attribute-implementation/marivo/evidence/attribute/`.
  The managed worktree and accepted-snapshot recovery branch are retained.
