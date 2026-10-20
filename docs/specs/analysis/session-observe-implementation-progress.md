# Session / observe implementation progress

Status: complete, 2026-10-20. S1-S6 implemented; no unresolved P2+ findings. This record owns coordination and actual evidence; the implementation plan and redesign own intended behavior.

## Baseline and scope

- Base HEAD: `622a930b7adba834744ad285789782e1d4d6e6b2`.
- Pre-existing changes: analysis README; untracked attribute plan, Session redesign/review/implementation plan, public-surface progress. Preserve these edits.
- User authorized implementation and agent coordination. Explorer: gpt-6-luna/max; development: gpt-6.1-sol/high; review: gpt-6-astra/high.
- Packaged skills and AGENTS.md are not authorized for edits. No commit, push or publication requested.
- Each task records its bounded plan before development. Review has at most two rounds per task; unresolved P2+ blocks closure.
- Reuse fixtures, scheduler, compiler and publication. No compatibility layer or redundant validation matrix.

## Task ledger

| Task | Plan / owned responsibility | Dependencies | Owner | Status | Review rounds | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| E | Locate concrete S1-S4 seams and safe ownership boundaries; read-only | baseline | explore | complete | n/a | Distinct interval data directory; count and source-binding consumer audit |
| A | S1-S2 explicit output domains, Population-bound count, interval source capture and execution; focused semantic tests and typing | E | observe_dev | implemented; reviewed | 2/2 | O01-O18, O23, O27 |
| B | S3 interval inert data, directories, equality/ordinal and S4 codec/version boundary; focused matching/storage tests | E; coordinate interval interface with A | interval_dev | implemented; reviewed | 2/2 | O13, O19-O20, O24-O26, O29 |
| C | S4 strict Session lifecycle, timezone, resolved Entity members and lazy source-free construction; focused Session tests | E; integrate shared core.py edits with A | session_dev | implemented; reviewed | 1/2 | 51 focused pass; R1 No findings |
| D | S5 public Help/exports/errors/telemetry, caller/doc/latest EN-ZH migration and Attribute proposal alignment | A-C interfaces | coordinator | complete; reviewed | 2/2 | O01, O28; both R2 P2 findings closed, no unresolved P2+ |
| V | S6 final regular gate, affected Runtime, wheel and offline recovery | A-D | coordinator | complete | n/a | Full check-agent passed:4766 tests; PostgreSQL5; frozen wheel install/offline journey |
| V1 | Remove redundant identity scan in interval product, preserving independently consumed domains | A-D integration | observe_dev | complete; reviewed | 1/2 | Original two-read oracle restored; exact identity/failure probes passed |

## Events and failures

- Initial worktree and owning skills read. No implementation checks run yet.
- Exploration dispatched before development; implementation file ownership will be recorded before each developer starts.

## Review findings

The chronological entries below preserve original findings and failures. The task ledger and final review entries own current status.

- A plan: neutral IntervalCoordinate and IntervalKeys; each P.count captures its own dependency; source compiler schedules domains without global member context. A edits core.observe first (now released to C).
- B plan: distinct inert interval axis/directory; each directory record retains start/end/timezone, including mixed-zone outer unions; paired endpoints extended, codec/layout bumped once.
- C plan: strict Session create/exact-ID recovery, frozen timezone, remove activation/current/graph, preserve writer guard and semantic preview source bindings. C owns core methods except observe, frame_sources.frame and frame_population.
- D plan before edits: coordinator removes retired public exports and repairs top-level guidance, migrates lifecycle callsites outside C-owned Session tests, then aligns Help and current docs after interfaces settle. Preserve historical records and packaged skills.

- D initial surface run: 28 passed, 4 failed (retired exports/current route, old observe Help assertions, Ref passed to members). Raw `/tmp/marivo-session-observe-D-surface-initial.log`; owning expectations migrated, rerun started.
- C focused lifecycle/source-free/timezone: 39 passed (`/tmp/marivo-session-observe-C-tests3.log`). Full Session migration/typing still pending; not formally reviewed.
- D migration changed public lifecycle calls in 106 test files and initial observe during/members call shapes in 106 files. These edits are migration work, not evidence; changed time axes and count semantics require focused/gate triage.
- Shared interval interface: neutral IntervalCoordinate(identity,start,end,timezone); inert IntervalDataAxis with finite per-record start/end/timezone directory, distinct from calendars.

- C final focused tests: 51 passed; lint/import checks passed. R1 reviewer: gpt-6-astra/high, read-only, **No findings (P2+)**. Reviewed 28-file aggregate stayed `fdd2bcad72512f5daaa8dd27bae30fdbaf4d3e80f402629de7b434ac3ab7bc5b`; manifests `/tmp/marivo-review-c-{before,after}.json`. Independent 40-test run and fresh-Store create race/read-only inspection/reconcile probe passed. No broad/Runtime/package evidence inferred. One round used; no second round needed without subsequent C changes.
- A reports 44 query-results tests, 19 capture/count tests, 2 interval/window tests and 4 numeric subset tests passing; final checks pending.
- B reports 162/163 first rerun; final assertion migration preserves raw captured scopes while allowing bounded show omission. Final checks pending.
- D surface rerun: 31 passed (`/tmp/marivo-session-observe-D-surface2.log`). Numeric migration rerun: 83 passed (`/tmp/marivo-session-observe-D-numeric3.log`, final summary pending capture). Old failures preserved separately; field-vs-Population predicate composition migrated via existing Metric.where rather than new grammar.
- D docs task planned before development: docs_dev owns current analysis specifications/examples, latest EN/ZH and minimal Attribute proposal/plan alignment. Historical records, redesign/review/plan/progress and packaged skills excluded. Model gpt-6.1-sol/high.
- Default test gate started at current integration candidate, raw `/tmp/marivo-session-observe-default-initial.log`; this is diagnosis, not a passing gate.

- A source freeze: 86 focused +20 final narrow tests passed, typing37 files and lint40/import contracts passed. Formal A R1 assigned to review_a (gpt-6-astra/high); no source edits during review.
- B source freeze: 163 focused tests, typing17 files, lint19/import contracts passed. Formal B R1 assigned to review_c; preliminary frozen-read timezone dependency under investigation, not yet final finding record.
- First default diagnosis finished: 2114 passed,29 failed,2 errors in134.59s (`/tmp/marivo-session-observe-default-initial.log`). Main categories: retired lifecycle/format/capability expectations, explicit interval axes/period matching and remaining call migration. No passing broad evidence yet.
- Relevant Runtime environment: dedicated marivo-multisource Colima socket has healthy PostgreSQL analysis container; default Docker socket unavailable. No containers started or modified.
- D further plan: migrate telemetry to create/exact Session ID shape and preserve identity privacy; synchronize SQL-submit ownership inventory after activation removal; retain semantic meaning of changed result-coordinate assertions. Focused integration checks running `/tmp/marivo-session-observe-D-integration1.log`.

### B R1 original finding (P2; closed in R2)

Keep saved interval reads independent of the current timezone database. At `core/frozen_calendar.py:177-180`, `read_interval_directory()` calls `parse_timezone(zone)` and constructs live ZoneInfo. Persist Asia/Shanghai, patch `marivo._timezones.ZoneInfo` to raise ZoneInfoNotFoundError: stored interval to_pandas fails with CoreRuleError; equivalent calendar read succeeds. Boundaries and normalized identity are already saved; frozen recovery must not consult current IANA rules.

Reviewer gpt-6-astra/high bound unchanged19-file snapshot `ddb3d470045b80356465c393b31aae72fa3c2a86e2540a6e05f74d760ac9caa2`; manifests `/tmp/marivo-review-b-{before,after}.json`. Independent163 tests and local-key/order/empty-domain probes passed. No broad/Runtime/install qualification.

B fix plan before editing: normalize timezones only at source capture; validate saved names structurally while retaining exact names, bound/type/uniqueness checks. Add one focused stored-read/match regression with live timezone lookup disabled. Then rerun affected checks and request R2; any remaining P2+ blocks B closure.

### A R1 original findings (closed in R2)

- **P1**, methods/local.py:330: observe({'n': P.count(), 'mean': amount.sum().per(region).mean()}) fails because recursive child FormulaProgram inherits unrelated top-level counts whose output_mapping omits internal sample key.0. P.count().per(region).sum() also fails. Bind counts only where actually consumed and classify against actual sample axes.
- **P2**, binding/observe.py:212: amount.sum().per(mv.product(scope, customers)).mean() captures interval samples but filters IntervalCoordinate out of sample axes, causing one-column schema for two-column domain and ValueError on execution. Support the interval sample or explicitly reject an unsupported shape during capture, never accept a malformed plan.

Reviewer gpt-6-astra/high source41-file hash unchanged `1c7784bb2f8af76e141750afa924b50391ac4db8d04c0249ef41391b5cf61d13`, manifests `/tmp/marivo-review-a-{before,after}.json`. Independent `/tmp/marivo-review-a-final-probes.log`:2 failed,3 passed; `/tmp/test_marivo_review_a.py` retains probes. Count-branch ineffective where/window guard suspicion withdrawn after correct owning exception verified.

A fix plan before editing: restrict recursive count dependencies and bind legitimate count/sample coordinates; close interval sample capture/execution boundary using smallest contract-consistent route; add regression assertions to existing focused tests, rerun numeric/type/lint then one R2. No third review round; any remaining P2+ blocks closure.

### B R2 final review

Original P2 closed; No new P2+. Reader no longer resolves live timezone rules. Independent five focused tests and original failure probe pass; developer164 tests/type/lint pass. Two-file fix snapshot stable `3b412c700c00ee8c5d149ef557b91b90d5ad0605149aebfb2e6083f4175f01d8`, manifests `/tmp/marivo-review-b-r2-{before,after}.json`. B used2/2 rounds; no unresolved findings, no broad/Runtime/install evidence implied.

### D integration task plans before edits

- session_dev owns tests/analysis/{graph,numeric,temporal}, excluding its already-reviewed timezone implementation tests; diagnose once, migrate explicit time/ordinary-axis bridges, ordinal periods, lifecycle and domain assertions, preserve numerical meaning. No source edits; report suspected code defects. Logs `/tmp/marivo-session-observe-D-numeric-migration*.log`.
- docs_dev docs frozen:37 docs,158 Python blocks,116 links and1371 narrative calls statically checked; no retired call shapes. No runtime proof. Then owns tests/analysis/{materialization,statistics} excluding A/B focused tests and A EOF window regressions. Diagnose once with maxfail0, preserve cold/corruption/source checks and repair actual current contract expectations; no source edits. Logs `/tmp/marivo-session-observe-D-materialization-migration*.log`.
- Root owns remaining surface/project/packaging/SQL-ownership integration, native Help and telemetry. Integration reruns have reduced first group to7 failures (184passed) and exposed11 second-group failures (73passed), handed to test owners; raw D-integration1b/integration2 logs preserved. These remain diagnosis, not final passing gates.

### A R2 final review

Original P1 count/per dependency issue and P2 interval internal-sample issue closed; No findings and no unresolved P2+. Independent11 probes +2 owning regressions passed; logs `/tmp/marivo-review-a-r2-{probes,owning}.log`.42-source-file snapshot stable `c5cfe0fb2acb0547d037bbee64aeb171dba07dd2420e4e7616c81f45200d194b`, manifests `/tmp/marivo-review-a-r2-{before,after}.json`. A used2/2 rounds; no broad/Runtime/install evidence inferred.

- D public integration:457 passed,3 remaining failures (ordinary bridge index, retired Runtime graph protocol receiver, embedded typing old members/during); patched and focused rerun started `/tmp/marivo-session-observe-D-public-focused.log`.
- D graph/numeric/temporal full diagnosis:195failed/556passed, mostly flattened-key names, period matching and tuple/None helper migration; shared test helper repair in progress. Raw D-numeric-migration1 log preserved. No production regression inferred without repro.
- Full typecheck started (`/tmp/marivo-session-observe-typecheck-initial.log`).

### D public/docs R1

No findings P2+. Reviewer inspected13 public/disclosure production files+37 docs;50-file snapshot stable `0c9b43bbbac85626b5fd1afcf942954aaf817ecea07e67c9c6491869229c4c32`, manifests `/tmp/marivo-review-d-{before,after}.json`. Independent78 tests passed (`/tmp/marivo-review-d-focused.log`), Live Help/retired-route probes passed, SQL ownership298 files/225 records/zero failures. One round used; current test migration remains integration work, not evidence that all scenarios passed.

### Current integration evidence

- Full typing **passed303 source files** (`/tmp/marivo-session-observe-typecheck-final.log`). Initial two stale interval Literal snapshots failed and were updated; no production typing errors remained.
- PostgreSQL Runtime **5 passed8.65s** (`/tmp/marivo-session-observe-runtime-postgres.log`): string/null/NUL source-key filters, native time dependencies, saved cold read. Existing healthy dedicated environment reused; no other provider qualification implied.
- `make pypi-build pypi-check` passed. Wheel SHA256 `d92998ec7ca9d7bb658b36f30a5717b3f3ab2052675ba3da7c4fff50522a9c9b`; archive content300 product files. Raw `/tmp/marivo-session-observe-package-build.log`.
- Actual pip installation in temporary target, distinct producer PID66576 and cold PID67168 imported the installed wheel. Producer oracle revenue30/declared identities3/per-person10 passed. After renaming both models and datasource offline, cold exact-ID resume/read/ordinal compare with live ZoneInfo disabled returned absolute23 and created no new Runs. Raw `/tmp/marivo-session-observe-installed-journey.log` and `installed-cold.log`; initial cold harness used nonexistent Run.id, corrected to owning run_id without product changes and reran cold phase. Temporary run `/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-session-observe-wheel-acl7e64c`.
- Public focused92-case run:91passed/1 remaining ordinary bridge-index assertion; patched, final selection test1passed. Raw D-public-focused and D-selection-final logs. Final broad gate still pending.

### V1 integration defect: duplicate source identity reads (planned before fix)

D migration retained the existing two-read oracle in `test_encoded_time_runtime::test_observation_preserves_population_windows_and_query_count`. Actual trace has three reads: metric order scan, SourceIdentities customer scan, and the same customer scan inside interval×identity product. Literal interval itself reads no source. Raw `/tmp/marivo-session-observe-D-encoded-source-trace-full.log`.

This is a newly discovered S6 scheduling integration defect, not a remaining A/B R1 finding. A/B retain their completed2/2 reviews; no third review round will be started for them. V1 plan: inspect the existing population closure/stage consumers, eliminate only the redundant evaluation while preserving independently consumed counts/predicates and source checks. Reuse the existing query-count/numeric witness, verify pending/fixed source products and typing. V1 has its own bounded integration change/review record (0/2); no optimization framework or new qualification matrix.

- D materialization/statistics migration complete:255 focused passed before3 stale assertions repaired; final4 cases passed;18 changed test files typing/lint clean; one three-process slow statistics journey passed. Initial interrupted67fail/732pass remains diagnostic only. Logs D-materialization-migration-{focused2,final-cases,cold,type-final,lint-final}.log.
- D graph/numeric/temporal migration: focused135-case rerun128pass/7fail followed by33 exact rerun31pass/2fail; final2 capability cases now pass.93-file lint/import contracts clean. Broader owned-test typing pending. Encoded two-read oracle deliberately preserved until V1 fix.
- Current full lint passed (`/tmp/marivo-session-observe-lint-final.log`). Installed wheel300 product files exactly matched checkout before V1 integration change; final wheel will be rebuilt if V1 changes production.
- Full default diagnostic started with maxfail0 once to collect remaining failures past previously truncated scopes (`/tmp/marivo-session-observe-default-complete-diagnosis.log`). It is not the final unscaled `make check-agent`; no new default/gate green claim yet.

### D R2 original packaging finding (P2; closed)

Installed acceptance helpers retained pre-interval axis assumptions despite migrated by inputs. Reproduced direct helper `_produce(_create(temp,'j2','parquet'),'j2')`: `tests/packaging/graph_journeys.py:204` maps only axis0 on two-axis interval+identity inputs, causing complete axis bijection failure. Same patterns in relation_journeys (219/249 etc), hierarchy_journey (95/98 reductions), versioned_journey bridges/oracles.

Bounded fix plan before edits: migrate only modified installed helper time/ordinary-axis mappings, explicit ordinal period correspondence, reductions and full-key oracles. Preserve raw-fact numerical expectations. Run targeted helper producer/offline/cold routes, not full release-check or a provider matrix. R2 remains active; preserve original finding and inspect final patches within this final allowed round.

### V1 R1

No findings P2+. Original encoded two cases+direct consumer two variants pass; independent pending/fixed factors1/0reads, exact composite BIGINT identity1read and emptyfactor/sourcefailure propagation pass. Two-file snapshot stable `6b78e88054e2617c74b280cc11226f8994586a4a5281b5baaaaeee0940466565`, manifests `/tmp/marivo-review-v1-{before,after}.json`. Custom oracle initially assumed split pandas identity columns; corrected to existing tuple export, raw preserved. V1 used1/2 rounds, closed.

- Complete default diagnosis:4753passed/8failed.2encoded failures used pre-V1 worker imports and pass current39-test set;2late numeric/temporal oracle migrations delegated for exact rerun;4root fixes are Store13/current Help target, resolved Entity input and obsolete protocol error text. Raw `/tmp/marivo-session-observe-default-complete-diagnosis.log`.

### D R2 captured display compaction plan

Before edits: replace Frame.show's truncated lossless scope JSON with the existing bounded scope_summary display. Preserve the current axes card, exact interval bounds/timezone, joint Population tuples/product roles, field and contribution restrictions, and count-domain identity. Add only presentation-owned IntervalKeys summary support if needed. Do not change source_scope/captured_source_scopes, stored facts, fingerprints, compiler, Help, packaged skills, historical budget reference or test limits. Validate focused disclosure/no-read checks, typing/lint and the actual slow context-budget test without MARIVO_HELP_BUDGET_RECORD; send the frozen candidate to review_c within active D R2.

### Final integration checks and bounded follow-up

- Unscaled `make check-agent`: full lint and typing passed; **4763 default tests passed in228.02s**. Last API documentation stage failed only on removed member_count/ArtifactSummary/SessionGraph directives. Plan before editing: remove those three obsolete RST entries, then rebuild owning docs. `make docs-api-agent` now passes (`/tmp/marivo-session-observe-api-final.log`). Original full log preserved at `/tmp/marivo-session-observe-check-agent-final.log`; this is not yet a fully passing gate.
- D-R2-1 packaging fix independently verified: original j2 repro passes, four helpers retain numerical oracles and complete keys. Developer ran11 Parquet routes in33 producer/fixed/cold processes, typing/lint passed. Candidate diff SHA256 `f922fdbd9b6d6633070d822967f95d43f672cac40cc8fcedc6398d2028e58e1d`. These checkout helper journeys remain separate from installed-wheel evidence.
- Slow context journey exposed display growth:35492 characters exceeds original aggregate32845.7; comparison7334, selected4790, missing5106 exceed individual caps. Raw `/tmp/marivo-session-observe-context-slow.log`; recording-only `/tmp/marivo-session-observe-context-current.json` is measurement, not acceptance. Reviewer traced growth to repeated interval/identity/owner serialization in pending Frame scope display.
- D follow-up plan before edits: docs_dev compacts Frame.show using existing captured-scope summaries, retaining exact interval bounds/timezone and joint-domain facts. No compiler, stored scope, fingerprint, receipt, budget baseline, page/repeat or aggregate-limit changes. Review remains inside D's active final R2. Rebuild final package after any presentation source change.
- PostgreSQL current-source Runtime5passed10.12s (`/tmp/marivo-session-observe-runtime-postgres-final.log`). Final pre-display package build/check and actual installed producer/cold journey passed (`/tmp/marivo-session-observe-package-final.log`, `installed-final.log`); final-source package refresh still required after display change.

### D R2 original presentation follow-up (closed at final candidate)

Original unchanged-budget slow test passed after compaction, but reviewer found a concrete identity loss before closure: scalar counts over two literal domains with the same key text and different Entities show identical LiteralPopulation summaries; no output axes exist to reveal the owning Entity. Fix plan before editing: add concise literal-axis semantic path/role in the existing node summary and retain a same-key/different-Entity no-read witness. Also migrate two pre-existing deep/shared-storage display spies to the current presentation helper while keeping raw saved-scope checks in their owning module. No saved-scope or budget change.

The first post-compaction broad run was interrupted before completion to bind the corrected candidate; its partial log is `/tmp/marivo-session-observe-check-agent-complete.log`, not passing evidence. The intermediate package was built and byte-matched, but will be superseded after final presentation freeze.

The same D R2 presentation identity finding also affects count domains captured from distinct fixed FrameKeys: generic node names omit the captured Frame input ID and binding targets. Extend only that node summary with its inert input ID and target descriptors, and rerun the reviewer's two original probes. This remains one bounded presentation correction within D R2, not another review round.

Reviewer formalized the above as **D-R2-2 [P2] Preserve captured count-domain identity in compact pending display**, `_scope.py` LiteralPopulation summary and missing FrameKeys specialization. Original two public probes reproduced; value computation unaffected. Pre-repair display hash `770d71d38a3d98b1573c75a3a5f3fad151a7462e29a2c0064190a518445be720`, manifest `/tmp/marivo-review-d-r2-display-before-fix.json`.

### Design acceptance coverage

Paths below are repository-relative; O identifiers retain the redesign's meanings. These are owning existing tests and combined journeys, not a new acceptance framework.

| Design cases | Concrete evidence owner |
| --- | --- |
| O01, O27, O28 | `test_observation_binding.py`, `tests/surface/` export/Help/typing/repair checks; native CLI, API RST and current EN/ZH static alignment |
| O02-O06, O15-O17 | `test_query_results.py` explicit-domain/joint-key/empty-unit/count cases; `test_deferred_windows.py` time-repeat and ineffective-condition checks; installed revenue30/count3/per-person10 oracle |
| O07-O09, O18 | Existing numeric/context/graph tests migrated with full coordinates and unchanged raw-value oracles; query-results fixed selection/original mean/multiple datasource cases |
| O10-O12, O14 | `test_deferred_windows.py`, `test_named_calendar_windows.py`, `test_population_membership.py`; interval history+hard filter, calendar rules and original allocation denominator |
| O13, O19-O20, O29 | `test_frame_correspondence.py`, `test_frame_storage.py`; declared directory survives selection/reduction/cold load; complete on and explicit ordinal; no implicit squeeze/bridge |
| O21-O22 | `test_analysis_session_helpers.py`, `test_session_discovery.py`, `test_analysis_session_timezone.py`; strict race, exact recovery, read-only history and frozen system/fixed timezone |
| O23 | Existing source-contract/error tests, explicit empty-domain dependency checks, V1 checked-factor/failure probes; no empty result swallowing source failures |
| O24-O26 | Store/terminal protocol and recovery tests, interval corruption/cold tests; actual installed-wheel offline recovery with live ZoneInfo disabled; old bytes remain untouched |

### Final display candidate

- D-R2-2 repaired at owning presentation nodes: literal count domains retain Entity role/path; FrameKeys retain input ID and target mapping. Both original review probes pass under source/fixed-row read guards (`/tmp/marivo-review-d-r2-display-probes.log`).
- Developer7 focused disclosure/storage checks passed13.45s; actual slow context-budget1passed22.07s without recording mode. Historical baseline and all caps unchanged. Typing3 files, lint/import4 files and diff checks passed.
- Four-file final display diff SHA256 `957ffe9b879354bd7fb37c04f16ae5b22d03c4b6da0b2cc11e7efc2208efd182`; `/tmp/marivo-session-observe-D-display-candidate.sha256`. No further source/test edits planned; final gate `/tmp/marivo-session-observe-check-agent-frozen.log` running against this candidate.
- Final rebuilt wheel SHA256 `d98385aced45882568f5ec5d407e324422e84fe8a45ae05321e580a9a113eaf2`, sdist `b2b26536b41d984650983c08d4278de1b3ac03e2f61618c6724e5572b179f7ee`; archive checks passed and all300 product files byte-match checkout. Product manifest SHA256 `bae2bf6cf14172b2564f4e98a7e81357917310031089a0c50f1de8d9ab65bce1` (`/tmp/marivo-session-observe-final-product-manifest.json`). Earlier package hashes are historical only.

### D R2 final review

**No unresolved P2+; D-R2-1 and D-R2-2 closed.** D used2/2 formal rounds, no third round. Reviewer independently reran the original packaging failure and both display failures; seven focused disclosure/storage tests passed12.14s. Final199-file test/script/API-doc/presentation snapshot stable `c4eed898f59e632084b1abfd20681edd9995a9bc42f09bd520e03a9f92419aab`; manifests `/tmp/marivo-review-d-r2-final-{before,after}.json`. Twelve initial files changed and four entered scope during the held round; final deltas inspected. Original50 D R1 files unchanged. No numerical/ownership/presence/period/corruption/cold assertions were abandoned. Review does not substitute for final parent-owned gate or installed/Runtime evidence.

### Final installed boundary

`make pypi-build pypi-check` passed for the frozen source (`/tmp/marivo-session-observe-package-frozen.log`). Actual pip installation and independent producer/cold subprocesses passed (`/tmp/marivo-session-observe-installed-frozen.log`): producerPID84317 imported the installed package and verified revenue30, declared identities3, per-person10; coldPID84380 imported the same installed package, with model/datasource renamed offline and live ZoneInfo forced unavailable, resumed exact IDs and returned interval revenue30 and ordinal absolute23 without creating Runs. Temporary root `/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-session-observe-wheel-6l7vtiyi`. Final wheel and300-file manifest hashes are recorded above. This is installed evidence, separate from checkout helpers and PostgreSQL Runtime qualification.

## Final handoff

S1-S6 complete. Final unscaled **`make check-agent` passed**: full lint/import checks, typing303 source files, **4766 default tests passed in321.40s**, and API documentation build. Raw final log `/tmp/marivo-session-observe-check-agent-frozen.log`; prior failed/interrupted logs above remain historical evidence. No source/test changes followed the final freeze.

- A2/2, B2/2, C1/2, D2/2 and bounded integration V1 1/2 formal review rounds. All P1/P2 findings closed; none unresolved. Original findings and stable review hashes retained above.
- Targeted PostgreSQL Runtime5passed; no other-provider or full-release qualification claimed. Final unchanged-budget slow journey, process-isolated statistics cold journey and11 local packaging-helper routes passed.
- Frozen package build/check,300-file checkout byte match, actual installed producer and offline cold recovery passed. Final wheel `d98385aced45882568f5ec5d407e324422e84fe8a45ae05321e580a9a113eaf2`; manifest `bae2bf6cf14172b2564f4e98a7e81357917310031089a0c50f1de8d9ab65bce1`.
- Public cutover is Store13 / `marivo.frame_output/v2`; old data is refused without migration or mutation. Current owning docs, API reference and latest English/Chinese examples aligned. Packaged skills inspected and did not require edits; neither packaged skills nor AGENTS.md changed.
- Worktree remains uncommitted. Pre-existing edits preserved. No push, release, unrelated container startup or full release-check performed.
