# Temporal filtering implementation progress

## Scope and candidate

- Authorized 2026-10-22: complete the implementation plan using agent-team-development; automatic reviewed local commit, no push.
- Base: `27f60de80f0a5a394e51b2a42fa95fb69c64bb76`. Initial dirty files: proposal and implementation plan (both task-owned untracked documents). Preserve all unrelated edits.
- Contracts: temporal-timezone-filtering-proposal.md and implementation-plan.md; current temporal-semantics.md describes the prior native membership policy and must be updated with implementation.
- User decision: hour_prefix inherits prefix timezone; no independent timezone parameter. CivilDate authority remains timezone-free; composite binding uses prefix source context/default.
- Evidence: temporal-timezone-filtering-evidence/. Candidate hashes exclude evidence and this progress ledger.
- Required: A01–A14, focused local checks, unrestricted check-agent, relevant real-provider checks, independent review and final public acceptance. Release is outside scope.
- Roles: exploration gpt-6-luna/max; development gpt-6.1-sol/xhigh; review gpt-6-astra/high; acceptance gpt-6-luna/max. Explicit assignments below.

## Interface and dependency checks before dispatch

- source_time.py is shared by T1/T2/T3; one core developer owns it and new private boundary helpers. Adapter work is restricted to datasource files and tests.
- Consumer integration depends on the core helper contract; explorer first traces consumers read-only and reports the minimal interface needs before implementation dispatch.
- g(b) only defines non-strict lower boundaries; strict/equality at gap plateaus require separate proof. Null projected booleans must not be replaced by WHERE-only constants.
- CivilDate SourceTimeAuthority prohibits read_timezone: prefix inheritance must use its source context, never report boundary_timezone.
- Existing legal C paths remain executable. D01 raw local membership must switch filters and bucket assignment together.

## Tasks and assignments

| Task | Dependencies | Owner / scope | Acceptance | State | Review rounds |
| --- | --- | --- | --- | --- | --- |
| Core T1/T2/T3/T6 | baseline | core developer; source_time, private boundary helper, time_filter/temporal_sql, prefix validator, focused temporal/semantic tests | A01–A06,A11,A12 | accepted | 2 |
| Adapters T1 | baseline | adapter developer; datasource adapters/profiles and datasource tests only | A01,A13 | accepted | 2 |
| Consumers T4/T5 | core interface | read-only explorer first; integration implementation assigned after interface handoff | A07–A10 | accepted | 2 |
| Disclosure T7 | final behavior | coordinator; Help/docs/site | A14 | accepted | 2 |
| Integrated T8 | all above | independent reviewer and acceptance agents | A01–A14 | accepted | 2 |

## Findings

| ID / round | Severity / scenario | Evidence | Repair owner | Independent closure | Status |
| --- | --- | --- | --- | --- | --- |

## Evidence and handoffs

Implementation, independent review and acceptance evidence are recorded below; developer handoffs alone were not acceptance.

## Closeout

Final42-file candidate independently accepted and unrestricted gate passed. Automatic scoped local commit receipt is recorded in temporal-timezone-filtering-evidence/local-commit.json after normal hooks; no push. Raw generated reports/logs remain local and are excluded from the commit.

### Dispatch 1

- `/root/core`: gpt-6.1-sol/xhigh, core ownership above; reports `evidence/core*`.
- `/root/adapters`: gpt-6.1-sol/xhigh, datasource-only ownership; reports `evidence/adapters*`.
- `/root/consumer_explore`: gpt-6-luna/max, read-only consumer trace; report `evidence/consumer-exploration.md`.
- Coordinator owns ordinary docs, semantic `_authoring_values.py`, `_capabilities/registry.py`, `constraints.py` and EN/ZH site examples. No packaged skills/AGENTS changes authorized or made.
- Shared interface approved: `source_time_predicate(raw, axis, authority, *, start, end, prefix=None, prefix_axis=None)` and `source_time_comparison(..., bound, comparison, prefix, prefix_axis)`. Return None for native fallback. Hour prefix passes raw prefix and its axis; source_time accepts private prefix_timezone context.
- ClickHouse native Timestamp classification uses known concrete engine + original physical dtype; no new metadata format needed. Parsed encodings must not be classified as physical Instants.
- Initial service inspection: dedicated socket missing and relevant localhost services refuse connections. Adapter owner investigating existing dedicated VM lifecycle before declaring Runtime blocked.

### Coordinator disclosure self-checks (development evidence)

- `make test TESTS='tests/semantic/test_semantic_live_help.py tests/semantic/test_semantic_help_examples.py'`: 90 passed; `temporal-timezone-filtering-evidence/disclosure-tests.log`.
- `npm run verify:content` in site: passed, 363 required files verified; `temporal-timezone-filtering-evidence/site-content.log`.
- Changed disclosure modules: Ruff lint and format check passed. These checks predate integration and do not replace the final unrestricted gate or independent review.

### Consumer trace handoff (exploration, not implementation)

- Existing raw tuple `time_encodings[path]` can feed selected_time and period filters, but bucket CASE also exists in compiler/observation.py, graph_source_execution.py attribution paths and population_condition.py.
- member_version must retain each endpoint's axis/authority independently and preserve raw open-end sentinel handling.
- SampleCoverage uses projected keys in methods/local.py; expected instants alone cannot fix a native-projection/wall-membership mismatch. Consumer implementation must bind expectations to the actual carrier without altering public native projections.
- Plan expanded consumer writable scope to the actual internal schema/coverage handoff modules only as required by these concrete callers; no public persistence schema change intended.

### Dispatch 2 / environment recovery

- `/root/consumer_explore` supplied actionable trace; coordinator interrupted remaining report expansion to release capacity. Its delivered observations are recorded above; no product edits.
- `/root/consumers`: gpt-6.1-sol/xhigh; T4/T5 ownership as above plus necessary internal coverage schema/carrier handoffs. Reports `temporal-timezone-filtering-evidence/consumers*`. Approved core interface supplied; integration begins against that stable contract.
- Dedicated pre-existing marivo-multisource Colima profile resumed successfully with documented limits; unrelated default profile remains untouched. Adapter owner starting relevant MySQL/ClickHouse services; no volume reset or MinIO.
- Packaged skills inspected for changed temporal claims; no conflicting native gap/fold or inverse policy found in their current text. No packaged-skill edit required at this point.

### Development integration decisions

- Consumer buckets will be private columns projected by prepare_fields and carried through existing checked joins. This avoids persisting raw Ibis lineage or expanding public schema. Narrow additional caller found: compiler/original.py bucket selection; consumer owner assigned it.
- Sampling must preserve native numeric grouping while checking exact raw sample presence. Coordinator rejected O(required-points) SQL column expansion as a default implementation; consumer owner is choosing a bounded per-axis carrier.
- Core focused development run: 100 passed (intermediate candidate, not final gate). Earlier failing expectations for native named-zone residual predicates are retained in core logs and updated only for the authorized D01 policy.
- Site full `npm run build` passed including Astro check/build, install scripts, 308 routes, 26 latest pages, 344 route/anchor targets, bilingual blocks and 72 migration pages. Raw site-build.log.
- Actual MySQL 8.4.11 and ClickHouse 26.3.33.24 services ready; adapter Runtime qualification in progress. Dedicated profile only; no unrelated service reset.

### Real-provider defect discovered during development

- ClickHouse non-UTC reader fixture reproduced encoded parse 8-hour drift (expected UTC 2026-04-30 16:30, observed 2026-05-01 00:30). Failure preserved in adapters-runtime-before-parser-fix.log / adapters-runtime-tests.log.
- Core owner repairing UTC native parse followed by actual source-zone localization; encoded reader/default origin must not be mislabeled physical Instant. Runtime rerun pending; not a formal review round.

### Provider development checks

- MySQL 8.4.11 + ClickHouse 26.3.33.24 focused Runtime: 4 passed in 2.27s after parser repair. Non-UTC CH native instants/microsecond raw predicate, Shanghai encoded projection, date-only prefix, six comparison operators with fold/gap/nulls; MySQL TIMESTAMP transport vs DATETIME plus actual missing named-zone refusal. Raw adapters-runtime-tests.log and provider JSON receipts.
- Core focused local tests: 104 passed on intermediate source. Final core handoff and scoped hashes pending.
- Trino connector checks will follow existing profile lifecycle after CH consumers no longer require that service; no concurrent mutually exclusive CH/Trino profiles.

### Formal R1 and prefix provider repair

- `/root/review`: gpt-6-astra/high, independent marivo-review R1 for frozen core/disclosure and adapter source; consumer scope not yet in R1. Report review-r1-core.md. Core freeze refreshed after transparent Reference/SelfReference handling: 110 local tests passed.
- Trino 483 real Iceberg/Hive string/integer Runtime and EXPLAIN/scan assertions: four passed, 10.98s; adapters-trino-runtime-tests.log.
- Final CH composite assertion exposed DATE-to-timestamp session adoption: Shanghai May1/hour0 in NY session projected Apr30 20:00Z instead of16:00Z. Raw adapters-runtime-before-prefix-fix.log preserved. source_time.py freeze revoked; adapter developer explicitly assigned this narrow source fix while core agent inactive. Reviewer notified to rebind source_time candidate after repair; other frozen scope remains valid. Formal R1 is ongoing, not reset.

### R1 finding F-R1-01

- Severity P1, core/time_filter: prepared localized column followed by `.view()` loses exact raw wall membership because `_resolved` follows Project/Filter but not Reference. Independent reviewer probe NY gap raw02:30/03:00 in [07:00Z,07:15Z) selects only03:00 rather than both. Repair owner core; closure pending. Formal core R1 used=1.
- CH prefix development defect repaired using explicit UTC DATE-to-timestamp carrier before hour arithmetic; real Runtime four passed2.05s, narrow lint passed. Adjacent checks and refreshed hashes in adapter final report pending.

### Adapter handoff / R1 repair dispatch

- Adapter DONE (ready for independent review, not accepted): adapters-report.md and adapters-hashes.json. Datasource72 local, adjacent core110, MySQL/CH Runtime4, Trino Runtime4 passed; typing/lint clean. source_time rebound to `ab193da7bf47284339a61a29758e0638b9f5b7e248719eb0cf649783ef203240`.
- `/root/core` resumed with retained gpt-6.1-sol/xhigh for F-R1-01 only, ownership time_filter.py and its regression test. Other source frozen except ongoing consumer work. No extra formal review round scheduled yet.
- Independent R1 preliminary report and reproduction: review-r1-core.md; reviewed 23-file scoped hash manifest. No additional actionable core/adapter/disclosure finding as of this handoff. Consumer R1 not yet started.

### Integrated candidate and ongoing R1

- Initial integrated source freeze: integrated-candidate.json, 36 files; all developer writes stopped at handoff. Independent acceptance `/root/acceptance` explicitly gpt-6-luna/max, report acceptance.md; public independent oracle probes, no product writes.
- First unrestricted gate stopped at lint because review probe .py artifacts were included by global Ruff; renamed evidence scripts to .py.txt without changing contents, preserving failure in check-agent-initial-evidence-shape.log. Gate restarted; this is evidence shape repair, not product source change.
- R1 F-R1-02 proposed P1: adapter verification method has no actual production caller. Reviewer establishing exact public repro; consumer developer resumed with retained gpt-6.1-sol/xhigh for narrow production wiring and public regression. Acceptance notified candidate will need rebind. This candidate's broad gate is diagnostic until source refreeze.

- Coordinator interrupted the superseded-candidate broad gate before source repair (1514 tests passed before KeyboardInterrupt; no green verdict). Raw check-agent-superseded-r1.log. Final unrestricted gate will run after repaired source freeze; this avoids treating a moving-tree run as final evidence.

### Additional R1 findings

| Finding | Severity / scenario | Independent evidence | Repair owner | Closure |
| --- | --- | --- | --- | --- |
| F-R1-01 | P1 prepared .view() loses D01 raw membership | review-r1-reference-repro.log; repair core-r1-repair.md | core | Reviewer rerun review-r1-reference-closure.log; final R1 report confirmation pending |
| F-R1-02 | P1 MySQL verifier unreachable in actual analysis | No production caller beyond method definitions; reviewer public repro underway | consumers | pending production wiring + regression |
| F-R1-03 | P1 ClickHouse private hour-prefix sampling clock adopts NY session | Actual read-only query expected neutral May1T00Z, gotMay1T04Z; review-r1-clickhouse-coverage.log | consumers | pending shared neutral composition repair + provider regression |

Consumer owner authorized narrow source_time helper extraction only if necessary to share existing correct neutral DATE/hour composition with coverage. This expands repair scope, not public API. R2 will include that helper. Acceptance agent notified of both consumer findings; current candidate remains unaccepted.

### Approved R1 repair interface

- F-R1-03: extract existing neutral hour composition helper from source_time and reuse for coverage; preserve native public projection and raw sample presence semantics.
- F-R1-02: execution boundary read_stage extracts literal zone arguments only from actual compiled MySQL CONVERT_TZ using existing sqlglot; verify through narrow source-session-owned connection method before execute, cache successful checks for that connection. No eager compile-time probe, no unrelated raw-only count probe. Consumer authorized narrow datasource SourceSession implementation and removal of superseded unused service wrapper/cache, preserving adapter verification function/provider statement.
- Root accepted both P1s and assigned consumer owner; reviewer notified of exact repair scope and rebind. R1 remains one round, R2 reserved for repairs and adjacent regression review. No source accepted or committed yet.

### Formal R1 completed

- Reports review-r1-core.md and review-r1-consumers.md: three P1 findings, no additional actionable findings. Core/adapters/disclosure/consumer formal rounds used=1 each.
- F-R1-01 independently CLOSED: original unchanged repro now selects both rows with exact raw SQL; review-r1-reference-closure.log.
- R1 independent evidence: 48 explicit boundary/prefix nullable cases passed, four live Help leaves resolved, CH private carrier failure observed on actual provider. Original manifests retained; only authorized query_preparation repair changed from consumer freeze.
- F-R1-02/03 remain open pending consumer repair and R2. Reviewer found no contract conflict in approved exact compiled-CONVERT_TZ execution verification design.

### R1 consumer repair development status

- Consumer reports F02/F03 implementation integrated: SourceSession connection owns successful verification cache, obsolete service wrapper/cache removed, read_stage checks only actual compiled MySQL CONVERT_TZ literal zones before execute. Existing cache lifecycle/failure assertions migrated rather than dropped.
- Neutral hour helper shared between source projection and coverage. Repair scope includes datasource/adapters.py/runtime.py, source_time helper and consumer call sites, plus focused tests. New actual-service public regressions running for MySQL projection failure/raw-only no-probe and CH nonUTC hour-prefix sampled mean. Freeze and independent closure still pending.

### Repair test scope and independent acceptance probes

- MySQL compiled SQL retains unused inner conversion projections even when the backend prunes them. Approved existing sqlglot projection pushdown on an inspection-only AST to identify genuinely referenced CONVERT_TZ zones; submitted SQL is unchanged. Raw count zero-probe regression now passes. No generic authority inventory or production SQL rewrite added.
- Verification is per actual source-read execution boundary, preserving independent identity stages; blanket no-read assertions on a multistage session.read were an invalid test expectation. Consumer retains distinct single-stage predicate rejection and conversion-statement rejection checks.
- Acceptance first probe used nonexistent AnalysisFrame.attrs (invalid harness); preserved failure and corrected to public to_pandas. Its next hand oracle mistakenly included01:50 fold clock outside [05:30Z,06:30Z); corrected explicit expectation using later occurrence06:50Z, preserved failure. These are probe corrections, not product defects or accepted results. Final freeze rerun still required.

### Repaired integrated freeze / R2

- F02/F03 developer handoff DONE: consumers-r1-repairs.md, consumers-r1-candidate.json7files; Runtime2 passed2.50s, local17 passed, typing5 and lint7 clean. Cache now lives on SourceSession; obsolete service cache removed. Actual read counts recorded in consumers-r1-{mysql,clickhouse}.json.
- Final repaired integrated freeze: integrated-candidate-r2.json38files (source/docs/tests; evidence and ledger excluded). All product writes stopped. Root unrestricted gate running check-agent-final.log.
- `/root/review` resumed retained gpt-6-astra/high for formal R2 and independent closure F02/F03/adjacent; rounds used=2, ceiling reached after this round. Report review-r2.md.
- `/root/acceptance` notified to rerun independent public probe against38-file hashes. Environment discrepancy: source import is repo-local, but script metadata reports0.5.5.dev0 while root-cwd metadata0.6.0.dev0. Root performed no environment sync; investigate distribution metadata path/order, use explicit source PYTHONPATH and record exact origins, no product version change authorized/needed by this task.

### Independent final public acceptance / R2 closure progress

- Acceptance PASS for bounded independent public workflows, acceptance.md; all38 candidate hashes verified before/after. Hand-derived fold/gap complete10/5-row domains, NULL/empty keys, equivalent Population members, clipped window220, validity endpoints/open ends, repeated-hour empty sample and genuine missing sample detection, hour_prefix mean1 and missing-hour failure all passed.
- Probe uses explicit repository PYTHONPATH + absolute .venv interpreter; final version and distribution0.6.0.dev0 via repo egg-info before/after chdir. Earlier0.5.5 display was installed dist-info search ordering, not source mismatch. No installed-wheel claim.
- Root final-candidate MySQL/CH Runtime: six passed12.96s, runtime-final-mysql-clickhouse.log.
- Reviewer R2 independently reran F01 original repro,48 boundary cases, both public repair Runtime tests(2 passed10.93s) and actual CH neutral carrier under UTC/NY/Shanghai (all exact midnightUTC); all38 hashes verified. Final R2 verdict pending adjacent SQL/cache review.
- CH service no longer needed by R2. Root documented manager switch to Trino, final candidate connector run in progress. No source changes since integrated-candidate-r2 freeze.

### Final gate diagnostics still pending

- Unrestricted final gate has two failing test indicators at roughly61%; exact nodes await pytest completion. No green broad verdict/commit yet; investigate exact failures before closure.
- Final Trino manager health wait initially exited unhealthy, but logs show normal server startup and subsequent status healthy. Preserved trino-final-start.log and trino-final-start-failure.log; resumed reader setup/focused tests against the now-ready service without reset/restart.

- Final repaired-candidate Trino Iceberg/Hive Runtime: four passed31.58s under concurrent host load, runtime-final-trino.log. Health timeout recovered without reset; exact service state healthy before setup/testing.
- R2 investigating potential bounded positional GROUP BY inspection issue: projection pushdown may remove conversion that determines grouped-count cardinality. Not yet classified until actual Ibis/public-path repro; no third broad review scheduled. Full gate nearing completion with two failures still pending exact nodes.

### R2 residual and broad gate exact failures

- Broad gate completed: lint/import contracts and typing passed; default tests5361passed/3failed. Failure preserved check-agent-r2-failures.log; API docs stage not reached. No broad pass or commit.
- G01 SQL audit scanner requires exact ownership classification for new read_stage SQL AST inspection. Consumer owner to classify justified inspection-only operations without weakening scanner.
- G02 test_source_query_fusion bridge evidence loses producer/consumer period_start/end (bothNone instead of2distinct captured ranges). Consumer owner must repair actual audit metadata, keep original assertion.
- G03 test_analysis_temporal native conversion expected527/150 but first450. Core owner resumed gpt-6.1-sol/xhigh to independently derive raw-clock expected sets and distinguish intended D01 change from product defect; no blind expectation edit.
- F-R2-01 P2 accepted bounded residual: actual Ibis grouped-count SQL with positional GROUP BY1 loses required zone under inspection-only projection pushdown. No public Session wrong-result reproduction claimed. Consumer owner normalizes exact positional dependencies before inspection pruning; actual SQL remains unchanged. Reviewer will independently close this named finding, not run a third broad review.
- Reviewer notified of exact gate nodes and narrow repair ownership; R2 remains ongoing. Source freeze revoked for these bounded repairs; prior provider/public evidence must be rebound or rerun as affected.

- R2 report review-r2.md complete: F01–F03 independently closed; F-R2-01 P2 open. Formal review ceiling used=2. Reviewer will perform only named residual/gate repair closure and necessary adjacent checks, no third broad review. Restoring captured read-period provenance separately from raw filtering is contract-consistent; reviewer sees no new full-review requirement for that bounded repair.

### Bounded closure implementation

- Consumer repair: `_mysql_source_timezones` expands literal positional GROUP BY on inspection-only copied expressions before projection pruning. Actual generated Ibis grouped/distinct/filter/raw queries assert retained zone sets. SQL scanner unchanged; exact helper calls classified inspection-only with explicit issued-SQL invariance justification.
- G02 uses private predicate_prepared on PeriodFilter while retaining captured start/end/timezone; graph lowering skips only duplicate filter application, evidence still reads logical.period. Focused verification pending.
- G03 diagnosis independently confirmed physical fixture TIMESTAMPTZ Instant. Injected +1h render old expected527/150 admittedJuly31value77 and excludedAug31value400. Raw Instant membership correctly gives450/150/400/0. Core assigned test-only direct raw SQL oracle repair, preserving shifted native projection and no standalone rule-audit assertions; no product source change required.
- R2 named closure handoff continues; no third formal review. Consumer starting CH again for final2Runtime after completed Trino4; no concurrent mutually exclusive services.

### Final instruction conformance and reviewer capacity

- G03 test direct typing found10 unchanged baseline closed-product union errors outside the modified function. To satisfy modified-module typing, core assigned narrow local binding+`isinstance(..., mv.Population)` assertions at those exact existing product sites. No conversion to by-tuples, casts, ignores, helper framework, or public API changes; original product behavior/coverage retained. Await refreshed module hash and clean focused typing/tests.
- Independent reviewer turn failed with selected model at capacity after R2 report. Retried same assigned gpt-6-astra/high for named closure only; no silent model substitution. Existing R1/R2 evidence retained, closure/commit pending retry.

### Independent named closure

- Reviewer independently ran16targeted tests: SQL regression, unchanged scanner tests, unchanged Frame bridge owning-period evidence, two public gap/fold consumers, rawInstant/no-ruleaudit oracle. F-R2-01/G01/G02 closed; actual Ibis grouped-count+5adjacent inspection shapes correct and rawcount has no required zones. Five repair hashes match.
- G03 same-module type hygiene now local tests28passed and ordinary typing1file clean, no casts/ignores/APIchanges. Final guard-only hash inspection pending reviewer; semantic/oracle closure already independently passed.
- No third broad formal review. Final global re-freeze/gate/public acceptance rerun pending.

### Final42-file candidate binding

- integrated-candidate-final.json42files; manifestSHA25698d14127375fba3e91fca109115b69a0c2a20c391094f3c9c2e186aab6321cb2. No source writes after freeze.
- Independent reviewer final named closure complete, all42hashes matched review-r2-final-manifest.log. F-R1-01/02/03,F-R2-01,G01/G02/G03 CLOSED, no unresolvedP0/P1/P2; review-r2.md. Two formal rounds total, named residual closure only thereafter. Assigned reviewer model retained despite capacity retry.
- Independent unchanged publicprobe PASS on final42, allhashesverifiedbefore/after acceptance-candidate-final-{before,after}.log; final source+metadata0.6.0.dev0 pinned. acceptance.md updated; public subset limits remain explicit.
- Root accepted-candidate MySQL/CH Runtime6passed9.38s, runtime-accepted-mysql-clickhouse.log; final receipts under final-provider/. Last Trino final-candidate connector rerun in progress; unrestricted gate check-agent-accepted.log in progress.

## Acceptance-ID evidence map

This maps the planned criteria to scoped evidence; the unrestricted final gate remains a separate required integration result.

| ID | Evidence / actual scope |
| --- | --- |
| A01 | core source-authority tests; adapter72 local tests; final-provider MySQL/CH native-kind, transport and authority receipts |
| A02 | core explicit NY/Shanghai/LordHowe/Apia wall oracles; reviewer48 independent nullable boundary/prefix cases; final public fold/gap probe |
| A03 | core all-six-operator truth/NOT/OR, quantization/physical-limit tests; actual MySQL/CH nullable wall predicates; independent reviewer strict plateau cases |
| A04 | native Instant raw SQL oracle in G03; source-authority/CivilDate tests and actual CH precise Instant predicate; legal B/C retained |
| A05 | core eight ordered encoding cases/string+integer; Trino Iceberg/Hive string/integer native equivalence and scan constraints |
| A06 | unchanged hour_prefix signature/Help, core native+encoded prefix tests; actual CH nonUTC projection/coverage and independent UTC public24-hour mean/missing-hour |
| A07 | independent complete10/5-row Population domains inclNULL/empty keys and equivalent members; unchanged source-fusion scope-evidence regression restored |
| A08 | core/consumer calendar/window tests; independent empty firstfold cell and clipped10-minute window220; existing sparse/window suites in broad gate |
| A09 | wall-version selection tests; independent public validity immediately before/at/after transition and NULL/sentinel open ends |
| A10 | public false-fold-missing avoidance, skipped-clock cannot mask missing03:00, adding real sample succeeds; actual CH sampled hour-prefix mean |
| A11 | core unowned/nonordered/native fallback tests and existing native execution suites; no new optimization-miss error family |
| A12 | core SQL/lineage/constants tests, reviewer preparedReference repro, final provider SQL receipts; Trino physical claims limited to tested EXPLAIN/scan fixtures |
| A13 | actual MySQL8.4.11 and CH26.3.33.24 six focusedRuntime cases; actual Trino483 Iceberg/Hive four connector cases; no PostgreSQL/new-server/fullconnector qualification claimed |
| A14 | Help90 focused pass, registry/constraint/docstrings + EN/ZH pages aligned; site content/fullbuild passed; no new public signature/Store format/source-lineage persistence, so no new installed/cold project mandated |

Independent acceptance report deliberately states its bounded public subset. Coordinator integrates it with independent R1/R2 closure, existing focused/default suites and actual-provider evidence rather than claiming that one probe exercised all14criteria.

### Environment-only final gate timeout

- Accepted-candidate final-provider Runtime allpassed: MySQL/CH6in9.38s, Trino4in9.82s; runtime-accepted-*.log. No source changes.
- Unrestricted gate source/static stages passed; default tests5367passed/1failed, only documentation subprocess time_examples.py exceeded existing60s under concurrent VM/provider/broad-suite load. Original check-agent-documentation-timeout.log preserved; prior semantic/audit failures did not recur. API docs stage not reached, so broad gate not yet green.
- Coordinator restoring initial dedicated test environment stopped state (Trino/MySQL via documentedmanager, then marivo-multisource Colima only; volumes retained, unrelateddefaultprofile untouched). Original exact documentation node rerun after resource release, no timeout weakening/source edits. Full unrestricted gate will be retried after diagnosis.

- Exact documentation node passed unchanged after resource release:1passed27.15s, documentation-timeout-recheck.log. Dedicated marivo-multisource VM restored stopped, volumes retained; environment-restore.log. No timeout/test/source weakening.
- Same42-file source, no concurrent provider workload: unrestricted gate restarted check-agent-accepted.log. Prior accepted provider/public/review manifests remain bound to identical source.

## Final accepted result

- Unrestricted exact command `env -u TESTS -u LINT_TARGETS -u TYPECHECK_TARGETS -u PYTEST_ADDOPTS make check-agent` exited0:811files formatted/lint/import contracts passed; typing331sourcefiles passed;5368defaulttests passed in247.14s; API docs built. Raw check-agent-accepted.log.
- Relevant actual-provider Runtime finalcandidate: MySQL/CH6passed9.38s and Trino4passed9.82s; no skips. Site content/fullbuild passed. Independent publicprobe and all42sourcehashes verified; two formal reviewrounds plus named residual closure, no openP0/P1/P2.
- Source/session defaults, native projection, valid B/C paths, cross-domain ownership and frozen results preserved. Hour-prefix source timezone inherited; no new public parameter, Store migration, packaged-skill or AGENTS edits.
- Initial service stopped state restored for this task's dedicated VM; volumes retained. Source did not change after final42-file freeze.
- No full release, installed-wheel, PostgreSQL Runtime or untested connector/index claim. Existing frozen-state contracts unchanged; no new cold-recovery project required.
- Coordinator stages only the42reviewedsource/docs/testfiles plus this curated ledger. Generated logs/reports/receipts remain reachable in the local task evidence directory and are not staged. Local commit receipt and final staged/post-hook scope are written after commit; no push.
