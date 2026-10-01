# R7.1 contract freeze and consumer migration ledger

Date: 2026-10-01. Status: R7.1 contract/static freeze complete; R7.2-R7.9
remain unimplemented. This ledger indexes sole owners and responsibility;
it is not another public API or method registry and grants no Runtime qualification.

## Actual baseline and accepted decisions

Branch `panda`, HEAD `10724b1d5c54019e175a3a1c3e12a19ec9c4a118`.
At entry, staged/unstaged tracked diffs and untracked status were empty.
The JSON snapshot stores full source/test/disclosure AST/text records, every
legacy disposition and all expanded qualification/resource rows in a zlib+base64
inventory_payload. Its uncompressed byte count and SHA256 are bound in the JSON;
the evidence index gives the decode command. Per-path SHA256 values are retained.
The R6.7 acceptance/evidence and R7 plan are read as current handoff; no
previous acceptance is rerun or enlarged. The static snapshot stores exact
baseline owner-input hashes, source/test/disclosure file hashes and import-only
probe outcomes. Product/tests/AGENTS.md/packaged skills are unchanged.
No commit, push, release, external message, Runtime test, wheel, release-check
or MinIO is part of R7.1.

The user confirmed invocation-level Event/Anchor order parameters, preservation
of Duration units with exact intermediate arithmetic and one nearest-even finish,
and initially fixed resource budgets. The later budget simplification supersedes
all count/row/step/memory quotas with one unified private execute time budget. The subsequent source-pushdown instruction
supersedes the draft's default Python matcher/replayer preference: prefer qualified
native/source execution and minimize transferred rows/bytes; retain bounded
ibis_python only for its documented irreducible operation. ClickHouse functions
are investigated as candidates, without granting R9 remote qualification.

## Sole owner decisions F01-F14

| ID | Sole owning section | Frozen decision | Implementation responsibility |
| --- | --- | --- | --- |
| F01 | [API](../../specs/analysis/python-analysis-design.md#r71-frozen-domain-api-target) | Full domain keys and closed Logical/Materialized result pairs; no instance-as-Group or extra family aliases | R7.2-R7.8 |
| F02 | [API](../../specs/analysis/python-analysis-design.md#r71-frozen-domain-api-target) | Required AnalysisDomain population; exact source/fixed overloads and lazy fixed results; old PopulationInput/implicit roots rejected | R7.2/R7.3/R7.5/R7.7 |
| F03 | [Semantic](../../specs/semantic/semantic-object-model.md#r71-frozen-event-and-statemodel-handoff) | Invocation order only on match/Event-role anchors; replay model default and Journey inheritance; closed tie cases/checks are operator-owned | R7.2 |
| F04 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | One explicit capture per repeated Event; canonical earliest assignments, dense reach and final-only exclusive reservation | R7.3 |
| F05 | [Runtime](../../specs/analysis/session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state) | Exact coverage/capture consistency and common-snapshot gate; insufficient follow-up differs from malformed claims | R7.2 |
| F06 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | Five duration states; exact s/ms/us/ns ticks, Fraction interpolation, one HALF_EVEN finish; required Duration row mean only | R7.3/R7.6 |
| F07 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | Owned funnel handles/read; period outer comparison; exact ratio-mix side terms, common Top-K, typed Other and scope | R7.4 |
| F08 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | Real inception, complete Subject classifications, pre-inception disposition, legal/self/zero-duration/illegal/terminal trace, end left limit | R7.5 |
| F09 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | Checkpoint axes, full state/pair domains, completed-window-fragment dwell and exact sufficient state; no summary-to-Subject map | R7.6 |
| F10 | [Timezone](../../specs/analysis/timezone-and-calendar-design.md#r71-frozen-occurrence-and-relative-window-time) | Exact Event/Journey Anchor starts, relative windows and overlap; ambiguous/nonexistent deadline rejection; fixed/mixed boundary API-owned | R7.7 |
| F11 | [Operators](../../specs/analysis/operators-and-frames.md#r71-frozen-domain-method-rules) | Original Omega/status views, exact partition bounds, explicit Subject-image Omega and any/every; no scalar bounds rollup | R7.8 |
| F12 | [Runtime](../../specs/analysis/session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state) | Method/implementation/state/part versions, checks, conditional K, qualification keys and one unified private 600-second execute deadline | R7.2-R7.9 |
| F13 | [Runtime](../../specs/analysis/session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state) | Qualified source pushdown preferred; explicit bounded preparation/local observation schema; no source-after-local/upload or mixed route | R7.2/R7.3/R7.6/R7.7 |
| F14 | [Runtime](../../specs/analysis/session-state-and-runtime.md#r71-frozen-domain-execution-and-retained-state) | Exact producer Finding policies/bodies/versions, counts/sort/cap, input identity, atomic publication and common read/recovery/hit validation | R7.4/R7.9 |

Each decision above is closed as a target; Runtime support is separately planned
or blocked. The owner sections contain concrete signatures, schema roles,
versions, numeric/Cell policies and the unified execute deadline. Existing R0 C18 is refined,
not reopened as research. No statistical_weight, arbitrary matcher/replay seed,
distinct/distribution attribution or scalar retention-bound arithmetic is added.

## Actual consumers and deletion gates M01-M16

Paths below are relative to `marivo/analysis/` unless prefixed with tests/site.
Exact baseline paths, symbols, import/call candidates and line numbers are in the
snapshot. Source admission of remaining Event/Lifecycle families is statically
blocked at legacy migration stage 7; old code is still present/importable. The
probe does not allocate a Session/Run or prove dynamic execution unreachability.

| ID | Actual symbols and consumer chain | Replacement and deletion gate | Shared owner and preserved discriminator |
| --- | --- | --- | --- |
| M01 | session/core.py::Session.events/lifecycle -> session/_lazy_sources.py::LazyEvents.match/LazyLifecycle.replay -> domains make_match/make_replay; observation.metric.PopulationInput | API/graph domain construction; retire R7 optional membership/source branches after explicit-member public replacement | R8 still imports observation membership/Metric contracts; do not remove shared roots |
| M02 | domains/event.py::LogicalEventDataset/MaterializedEventDataset/make_match/register_event; domains/contracts.py::EventDefinition/EventPayload/EventJourneySemantics | JourneyResult and occurrence/matching parameters/state in core/methods; remove Event family producer/registration after public source/fixed replacement | Preserve repeated-Event and no-start counterexamples; fixed old descriptor is not a supported input |
| M03 | compiler/event.py::compile_event_match/_ordered_occurrences/_attempts/event_output_proof; event_sources.py, event_time.py and event_axes.py prepare bindings | Qualified Ibis occurrence/axes preparation and canonical matching, native first; delete family compiler and occurrence-ID ordering admission | R9 native/remote type and source form qualification; canonical witness must survive pushdown |
| M04 | domains/event_reducers.py::funnel/time_to_event; compiler/event_reducers.py; compiler/event_continuation.py; domains/event.py::select_subjects | Registered funnel/duration/truth/read/transport; replace Dataset selection with strict where -> members and exact SubjectBinding | Keep five-status, common-prefix coverage and Journey-vs-Subject oracles |
| M05 | domains/funnel_delta.py, funnel_attribution.py, funnel_registry.py::register_funnel_delta/register_funnel_attribution; event_comparison.py, event_attribution.py and *_values.py | FunnelComparisonResult and existing public AttributionResult through graph; retire private Delta/attribution families and registrations | operators/attribute_values.py arithmetic has actual R8 driver/distribution consumers; split by symbol, preserve exact Fraction oracle |
| M06 | materialization/event_codec.py::decode_semantics/decode_coverage/decode_evidence; event_publication.py::build_event_publication; event_reducer_codec/publication and event_comparison_codec/publication | Current graph method/part receipts, validation and publication; remove old Event descriptor readers/writers together | No dual read; neutral scalar/input-binding helpers belong to actual R8 consumers, not old Event codec |
| M07 | domains/lifecycle.py::LogicalLifecycleDataset/MaterializedLifecycleDataset/make_replay/register_lifecycle; lifecycle_reducers.py payload/semantics and select_subjects consumers | HistoryResult and closed named views plus full Subject ledger; retire lifecycle family and generic where/selection paths | Keep no-interval Subjects and Unknown/NotStarted discriminators |
| M08 | compiler/lifecycle.py::compile_replay/_ambiguity_check; lifecycle_array.py replay/confluence templates; lifecycle_reducers.py | Qualified native or bounded registered replay/view kernels; remove recursive/array SQL, text confluence and ID-derived order | Same-state intervals cannot replace self/zero-duration trace; terminal-only invariant case has full violation identities |
| M09 | materialization/lifecycle_codec.py::validate_descriptor/decode_evidence; lifecycle_publication.py::native_summary/inspect_history; lifecycle_reducer_codec/publication | Canonical graph state and fixed pandas/Arrow validation; retire summary/inspection statements and dedicated recovery codec | Original interval/trigger/coverage corruption oracle; no fixed DuckDB attach |
| M10 | materialization/{postgres,trino,clickhouse}_event_sql.py; event_bundle.py::EventBundleStream; lifecycle_bundle.py::LifecycleBundle; lifecycle_integrity.py::integrity_sql/integrity_queries | Delete packet builders, private Ibis visitor/SQL patch, handwritten replay and integrity SQL after qualified replacement | AN02-AN06/AN08-AN09; R9 qualifies native functions and remote physical guarantees, not a renamed R7 SQL helper |
| M11 | materialization/{postgres,trino,clickhouse}_execution.py Event/History open/compile/bundle callers; source_stage.py family source preparation | Remove Event/History-only prefix, bundle and summary calls; keep shared source adapter connection/control lifecycles | AN07 and Event portion of AN23; R8 candidate/forecast/association and R9 metadata/control remain owned |
| M12 | operators/registry.py::legacy_source_migration_stage/_source_admissions; compiler/source_admission.py::source_admission_fact; placement/lowering/graph_plan/graph_lowering; dataset_execution/graph_observation/graph_local_execution | Single methods registry and exact physical keys; source-first/explicit local placement, dependency collection and exchange; retire R7 family dispatch/markers after replacement | Keep R8 admission blocks; expanding a local-predecessor allowlist is not implementation |
| M13 | core/model.py::DomainKind; core/graph.py::SourceDefinition; methods/semantics/physical/registry; materialization/graph_snapshot/graph_execution/graph_publication/graph_store; Session get_artifact recovery | Closed capture/domain/state variants in current graph/Store protocol; reject old domain descriptors before read/K/cache | Keep existing R5/R6 core and fixed continuations; no origin replay or alias/migration |
| M14 | evidence/_finding_registry.py::finding_registration; materialization/finding_values.py, input_bindings_codec.py; graph_store.py::artifact; graph_publication.py and store.py; evidence/_dataset_reads.py and Session evidence reads | Producer-bound graph Finding policies and atomic nonempty collection; common public digest/page/single read/cold/hit validation; retire old Event body codec consumers | Forecast/association keep actual R8 body/input-binding authority; violations remain domain rows; cap/sort/body corruption oracles |
| M15 | analysis/__init__.py/_public.py and native _help/_capabilities/introspection; cli.py; site/src/content/docs/{docs,zh-cn/docs}/latest; marivo/skills | Connect exact new exports/types/Help/K/repairs and synchronized EN/ZH examples in each implementation package; retire old Dataset targets without redirects | Packaged skill edits require separate explicit approval; unchanged in R7.1; static snapshot records actual public resolutions |
| M16 | tests/test_lazy_event_*, test_lazy_lifecycle_*, backend Event/Lifecycle tests; five Event/Lifecycle workers and their fixtures; exact AST nodes in snapshot | Per-node preserve-oracle/replace-entry disposition; retire old tests/workers only after independent public replacements exist | Remote execution qualification remains R9; old native/local/private tests grant no new Runtime or installed-wheel qualification |

For every M item: replacement executable = **planned**, old dynamic Runtime/codec
unreachability = **unverified**, physical deletion = **not performed**. Static
source admission blockage is a separate baseline observation, never deletion.
AN11/AN12 were removed by R6.7 and are not reconstructed for funnel. AN02-AN10
and the Event portion of AN23 remain R7 closure responsibilities; driver metadata,
transactions and actual non-Event shared consumers retain their R1/R8/R9 owners.

The snapshot records **236** per-node/worker/fixture
legacy dispositions. They preserve independent oracles and require replacement
before retirement. Five actual workers are lazy_event_runtime_worker,
lazy_event_reducer_runtime_worker, lazy_event_comparison_worker,
lazy_lifecycle_worker and lazy_lifecycle_reducer_worker. Remote fixtures/tests
retain explicit R9 physical qualification responsibility. The original dwell
fractional-us test keeps its anti-truncation purpose but its float-unit expectation
is replaced by exact HALF_EVEN tick finish in the new domain method.

## Mandatory qualification targets and V01-V18

The snapshot's qualification_target contains exact immutable requirement IDs,
not executable registry registrations. It expands **50**
parameter/method profiles, nine ordered Subject/occurrence key profiles and ten
source/time profiles into **13500** mandatory cells.
The key profiles independently combine string, int64 and composite(string,int64)
Subject/occurrence identity. Time profiles distinguish DuckDB native us and
Parquet s/ms/us/ns, each with UTC and America/New_York report authority.
Snapshot/validity historical axes must be checked at their method-owned instant.

Each `Q-R7-Pnn-Kso-Tnn-S/F/C` row names its method/value/parameter profile,
complete key/time profile, exact target route, phase and status. S is real source;
F is verified artifact_python continuation; C is a fresh source-offline process
executing actual K. F/C preserve their producing origin profile without inheriting
its pass. All are **planned**, with no executed/pass/skip count in R7.1.
DATE/naive occurrence, mixed inputs, bad keys/versions/order/coverage, insufficient
parts, unbounded candidate preparation, execute timeout, overflow and malformed Findings are mandatory
negative obligations in the V rows below, not new positive source shapes.

Source targets prefer ibis for preparation, first/shared matching, source-capable
Duration mean/transport, funnel, comparison/allocation and Event-origin elapsed
Anchor operations. Exclusive matching and canonical normative replay initially
target ibis_python because the current owner has no public-Ibis qualified
state-dependent reservation/replay kernel. Their native alternatives remain
unverified candidates; local consumers have no source-stage dependency on local
outputs. Calendar/local-Journey observation carries exact preprepared inputs.
Source preference must be assessed before accepting any local qualification.
Any later route/version amendment is explicit, keeps the requirement identity and
its failed/blocked evidence, and cannot remove a mandatory business/type/time cell.

Relative Metric positives pin count, sum I/F/Decimal(38,6)/Duration, original
ratios in those families, multi-root float64 linear and multi-root int64 ratio.
Other R5 Metric methods/scales/expression families retain unverified qualification
and are not inferred from these cells. Additional required business cases must
be added before connection, without interpreting this finite matrix as all-Metric
or all-backend support. Elapsed/calendar cases, source-computable/local predecessor
and empty/nonempty domains are explicit parameter variants, not fallback modes.

Each method profile links its V responsibilities, exact semantic/state/implementation
versions and the private r7_execute_v1 profile. The three
Q-R7-V15-EXECUTE-below/at/above requirements record the single 600-second
execute deadline and atomic outcome for every stage/route. Count, row, step
and memory quotas are removed by the latest user decision. These requirements
are planned and cannot be inferred from shorter executions.
The V table names one accountable package; contributors do not split final
acceptance authority.

Every new filename below reserves future responsibility and is **planned**, not
an existing executable test command. Existing baseline anchors refer to actual
AST definitions/files; their old assertions are inputs, not target pass evidence.

| V | Accountable package and contributors | Planned new or existing disclosure test owner | Independent acceptance obligation | Existing baseline anchor |
| --- | --- | --- | --- | --- |
| V01 | R7.2 (contributors R7.3/R7.5/R7.7) | `tests/test_analysis_domain_preparation_r72.py` | Identity/role/version/Session/static-mode oracle; planned zero-read/Run spies | `tests/test_lazy_event_contracts.py` |
| V02 | R7.2 | `tests/test_analysis_domain_preparation_r72.py` | Explicit partial-order fixtures, closed positive tie cases, changed violation-ID negative | `tests/test_semantic_r23_business_order.py::test_simultaneous_opposite_transitions_have_no_implicit_occurrence_id_order` |
| V03 | R7.2 | `tests/test_analysis_domain_preparation_r72.py` | Raw exact interval/origin coverage oracle, wrong Event/source/version and malformed-vs-insufficient distinction | `tests/test_lazy_event_coverage_runtime.py` |
| V04 | R7.3 | `tests/test_analysis_journeys_r73.py` | Independent scalar occurrence matcher; three policies/repeated Event/one-three steps/shuffle/batching | `tests/test_lazy_event_numeric.py::test_all_policies_match_independent_occurrence_reference` |
| V05 | R7.3 | `tests/test_analysis_journeys_r73.py` | Exact tick/Fraction endpoint oracle, five states and 140/3 versus 60 second units | `tests/test_lazy_event_reducer_numeric.py::test_time_to_event_classifies_selected_pair` |
| V06 | R7.3 (contributors R7.6/R7.7/R7.8) | `tests/test_analysis_journeys_r73.py` | Independent Subject set image/opportunity truth; real same-Run nonempty/empty new Metric observation | `tests/test_lazy_event_membership.py::test_selected_identity_authority_drives_events_without_origin_replay` |
| V07 | R7.4 | `tests/test_analysis_funnel_r74.py` | Raw assignment dense-count oracle, historical axes, complete outer support and three denominator roles | `tests/test_lazy_event_reducer_numeric.py::test_funnel_matches_independent_dense_counts` |
| V08 | R7.4 | `tests/test_analysis_funnel_r74.py` | Independent exact Fraction counts/allocation/side totals; asymmetric Top-K/Other/hierarchy/scope | `tests/test_lazy_event_comparison_numeric.py::test_ratio_mix_reconciles_independent_paths` |
| V09 | R7.5 | `tests/test_analysis_lifecycle_r75.py` | Small scalar state machine with full legal/illegal/terminal trace and per-Subject ledger | `tests/test_lazy_lifecycle_numeric.py::test_complete_native_rows_match_independent_reference` |
| V10 | R7.6 | `tests/test_analysis_history_r76.py` | Independent raw-event checkpoint/pair/Subject image oracle, including no intervals and zero-duration transitions | `tests/test_lazy_lifecycle_reducer_numeric.py::test_same_intervals_different_same_time_transition_multiplicity` |
| V11 | R7.6 | `tests/test_analysis_history_r76.py` | Independent clipped intervals, Fraction interpolation and HALF_EVEN ticks; no mean/p90 of summaries | `tests/test_lazy_lifecycle_reducer_numeric.py::test_fractional_microsecond_statistics_are_not_truncated` |
| V12 | R7.8 (window contributor R7.7) | `tests/test_analysis_anchors_r77.py; tests/test_analysis_retention_r78.py` | Independent ZoneInfo deadlines/occurrence uses, 25/5/70 and any/every three-valued truth tables | `tests/test_analysis_temporal_r55.py` |
| V13 | R7.9 (each package supplies its phase proof) | `tests/test_analysis_domain_recovery_r79.py` | Capture identity/source refresh/shared submission counts; all preparation before local stage; zero fallback/upload/replay | `tests/test_analysis_recovery_r67.py` |
| V14 | R7.9 (Findings contributor R7.4) | `tests/test_analysis_domain_recovery_r79.py` | Fresh producer/continuation/recovery processes with sources/Semantic/DuckDB forbidden; every part/K/Finding independently compared and corrupted | `tests/test_lazy_finding_reads.py::test_full_audit_checks_count_digest_ordinals_identity_and_body_authority` |
| V15 | R7.9 (each package supplies its phase proof) | `tests/test_analysis_domain_preparation_r72.py; tests/test_analysis_domain_recovery_r79.py` | Real cross-batch/empty-schema/close/cancel/timeout/transaction fault oracles; exact issued-vs-driver SQL and row/byte audit | `tests/test_analysis_graph_publication_r44.py` |
| V16 | R7.9 | `tests/test_analysis_domain_recovery_r79.py` | Reverse module/import/registry/worker/codec/runtime probes and AN02-AN10/AN23 closure without deleting actual R8 consumers | `tests/test_lazy_event_comparison_contracts.py` |
| V17 | R7.9 (each public package contributes) | `tests/test_analysis_dsl_public_static.py; tests/test_analysis_help_resolution.py; tests/test_unified_help.py; tests/typing/analysis_dsl_public_contract.py; tests/test_cli.py` | Independent exports/typing/Help reachability/budgets/dynamic K/repair and current EN/ZH example checks | `tests/test_analysis_help_resolution.py::test_invalid_objects_have_bounded_resolvable_repairs` |
| V18 | R7.9 | `tests/installed_r7_journeys.py` | Same noneditable wheel/origin/hash/poison checks; A09/A10/A13 and produce/continue/cold independent processes | `tests/installed_r6_journeys.py` |

A09 belongs to R7.3/R7.4: real same-Run dropout -> members -> prepared Metric
observation, then complete funnel compare/allocation with nonempty Findings.
A10 belongs to R7.5/R7.6: source-origin History, every named view, actual Subject
image and same-Run prepared Metric observation. A13 belongs to R7.7/R7.8:
Event/Journey Anchors, relative Metric windows, fixed Omega and explicit subject
quantification. R7.9 owns the same-wheel/native-table/Parquet producer/continuation/
source-offline recovery closeout for all three. L1 tests only complete decidable
selection; L6 compares definitions, every part and actual K, not repr alone.
L8/L9 apply only to qualified numeric state methods, not ordered replay or
overlapping Anchor bags. Oracle expected values/eligible row sets must not be
generated by the product registry, extractor or compiler under test.

## Static blockers, disclosure and handoff

| Blocker | Frozen treatment and exact recovery condition | Owner |
| --- | --- | --- |
| No Event/StateModel/order closed graph captures today | Introduce the captured definitions/ordered dependencies and exact method/state variants before domain lowering | R7.2 |
| Current source plan refuses local predecessor | Implement explicit preprepared contribution support and registered local consumer; no allowlist-only admission | R7.2, connected R7.3/R7.6/R7.7 |
| SourceSession has no verified shared domain snapshot contract | Qualify exact native snapshot/immutable file-manifest authority, preserving check/consume identity; otherwise refuse that route | R7.2, R9 for remote form |
| Current graph Store validates only empty Findings | Implement producer policy/set/body/input validation and atomic common reads; no universal zeroing | R7.4/R7.9 |
| Native function lowering/semantic witnesses not qualified | Public Ibis support plus full assignment/state/coverage parity and transfer/deadline evidence; never private visitor or SQL-name injection | R7.2-R7.8, R9 native backend qualification |
| Required future skill synchronization has no edit approval | Keep skills unchanged; prepare reviewable changes in the connecting package and obtain explicit approval before editing | M15, later implementation package |

F01-F14 have no undecided mandatory policy. The blockers above are implementation
or qualification gaps, not deferred semantic choices. R7.1 exit is documentation
and static inventory only. R7.2-R7.9, R8 statistics, R9 remote/native/backend scope,
R10 real Agents/release remain unqualified by this package. Validation and exact
artifact fingerprints are in the [R7.1 evidence index](2026-10-01-marivo-r71-evidence-index.md).
