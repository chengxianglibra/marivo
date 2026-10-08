# R10 current acceptance index

## R10.3 current implementation and qualification (2026-10-08)

Baseline: `panda@c1f98a4c0ebfd9a81680bce0928728daaefa7285` plus the scoped
uncommitted implementation. **R10.3 is complete for the finite required installed
journeys, producer/physical witnesses and independent risks below.** R10.4, R10.5, release, commits and previously authorized skipped
cost collection are outside this task.

The implementation repairs History originating-check binding and actual local
predicate inputs, historical selection dependencies in new observations,
coordinate-observation recovery, and Anchor templates/component matching.
Installed row-statistics checks additionally reproduced a downstream finite-value
proof lost after a local ratio finish. Its source projection must stay bound to
the finished values at the consume deadline. This repair supersedes candidate 02;
its passed and failed runs remain diagnostic evidence and do not qualify a new wheel.
Public API, method semantics and Store 8 remain unchanged. Current catalog disclosure
and row-statistics behavior are exercised through their existing owners. Owning
Runtime specification and latest English/Chinese observation-recovery examples are
updated; packaged skills and AGENTS.md are unchanged.

The current frozen package is candidate 06, built outside the repository with
`make pypi-build pypi-check` (exit 0). Wheel SHA-256:
`194343a15b75c74f3cc35759f6cd88f5b68e795e349d4261f1d039ad16ea4236`;
sdist SHA-256:
`5f47b8507082c22ca06f52ff68f678ff80fda5385571bca1828829d6036a7f31`.
The baseline plus `evidence/r103/candidate-06.diff` (SHA-256
`9710297661e51804b367fa5f470748d45eb0534f95a0f15198ab4104c3980419`),
six new-file hashes and the 1289-file snapshot inventory in `candidate-06.json`
identify the build, including the Store 1 through 7 refusal owner. All 327
package/resource and dependency owner hashes plus LICENSE remain frozen.
Candidate 06 formal runs use identical archive bytes on Python 3.12 and 3.10.
Candidates 01 through 05 are
superseded; their commands and failures remain diagnostic evidence.

Candidate 05 is now also superseded: its selected healthy-service group completed
43 owners but failed the MySQL authoring `sample()` deadline under mysqlclient
2.3.0 (21 unselected/inapplicable skips). The separate engine timeout hook still
called active connection metadata on the timer thread, and release took 34.05
seconds. Its datasource owner now captures the socket before submission, guards
the prepared connection by object identity, and closes the duplicate on the owner
thread after joining the timer. Real driver 2.3.0 authoring and source deadlines
pass after the repair; candidate 06 contains it. Candidate 05's 39 journey
phases per interpreter and partial independent-owner runs are preserved without
qualification transfer.

The [public journey entrypoint](../../../tests/packaging/complete_journeys.py)
accepts `PROJECT a01..a13 produce|fixed|cold REPORT`, with three independent
processes per journey. Each installed process guards noneditable `site-packages`,
`direct_url.json` and the candidate archive hash. The
[installed journey gate](../../../tests/packaging/test_installed_journeys.py)
removes source files and declarations before fixed/cold phases and runs finite
independent behavior owners; it does not read this index.
Both interpreters have completed all 39 journey phases on candidate 06; the
26 independent behavior owners per interpreter are closed, including the explicitly
recorded repaired refusal owner. Native backend and resource exits are separate below.

| Journey | Script/oracle owner and business discriminants | Current source/fixed/cold/installed exit |
| --- | --- | --- |
| A01 | hierarchy_journey.py: raw J1 regional/channel totals, Null/zero, empty groups, Ref/read grouping and coordinate rows | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A02 | relation_journeys.py: raw monthly differences, negative selection and next observation; display owner checks legal-zero current-row mean | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A03 | graph_journeys.py: raw order/line grains and complete tuple groups; original 40 versus current-row 130/3 | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A04 | graph_journeys.py plus association_edges_journey.py: exact rank/tie oracle, negative coefficient, Null pairing, wrong-domain atomic refusal and source/fixed routes | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A05 | versioned_journey.py and versioned_recovery_worker.py: exact snapshots/validity, historical fields, absent snapshot pre-I/O refusal and recorded/settled observations | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A06 | relation_journeys.py: branch components, half-additive/cumulative state, periods, Exact/UnionKeys, zero/negative baseline and nested differences | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A07 | relation_journeys.py and fixed selection owner: raw fixed references, share, penetration, standardization, attribution and Top-K/Other | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A08 | relation_journeys.py and public History Unknown cohort owner: complete opportunities, counts/all/any, empty and decidable/undecidable Cells | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A09 | funnel_public_recovery_worker.py: matching/Funnel comparison and allocation, exact five-second Duration, Journey unit and dropout follow-up | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A10 | history_public_recovery_worker.py and history_oracle.py: inception/replay, state/interval/violation selection, complete intervals/dwell, views/checkpoint grid and captured observations without replay | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A11 | statistics recovery/journeys/oracles and composition/statistics owners: nine methods, fixed fitting, complete grids, lag direction, pairing, original coordinates and forecast equations/intervals | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A12 | renamed operations/device/reading/sample with kWh; source/profile, schema-drift, atomicity, Session and disclosure owners | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |
| A13 | anchors_worker.py, anchors_oracle.py and retention_worker.py: complete composite identities, DST elapsed/calendar, overlap, all executable fixed K, fixed opportunity domain and Unknown bounds | 3.12 and 3.10: source 0 / fixed 0 / cold 0; installed passed |

Candidate 03 additionally failed the required MySQL deadline/SIGINT installed
owner with mysqlclient 2.3.0: cancellation called active connection metadata
across threads. The datasource owner now captures connection identity and a
duplicated socket before submission, closes it on the owner thread, and finalizes
interrupted pending submissions after SIGINT unwinding. Directed unit and native
Runtime checks pass; this product change requires a new frozen candidate. No
candidate 03 success transfers to that candidate. Its original failed aggregate
and the separately successful MySQL source-journey retry remain recorded.

The ClickHouse source preflight also reproduced a required weight-sum overflow
that became Null through `CAST(... AS Nullable(Int64))` and was published as a
zero-denominator result. The numeric owner now checks the non-nullable carrier
and preserves legitimate Null in an outer branch. Source and fixed overflow
refusals retain prior primary, runs and resources atomically. Candidate 04 was
built before this repair and grants no formal qualification. Current directed
numeric Runtime checks pass (34 passed, three unselected backend skips), strict
typing passes, and `make check-agent` passes (4939 passed, one skip; 329 typed
modules). Distributed producer and datasource preflight also pass separately;
these source diagnostics do not substitute for candidate 06 installed proofs.

Backend and resource exits are separate from these journey exits. Qualification
uses the dedicated multisource environment and explicit `make installed-multisource-test`
opt-in: PostgreSQL/MySQL/Trino first, then serial ClickHouse and Distributed topology.
The candidate 06 healthy-service gate passed: 44 passed, 21 unselected or
inapplicable skips, exit 0; log SHA-256
`4f46a40b72cbc30b016cbf2d21669fff1af911d8a68a1c628722b20d19965304`.
ClickHouse standalone is closed by its 12 unchanged successful checks and the
two-case deadline/SIGINT owner retry. Distributed is independently closed by
its producer and profile gate (two passed, 63 deselected, exit 0).
Actual table/view, file/HTTP, connector and Distributed producers retain separate
physical keys, native submissions, raw-fact oracles and producer/fixed/cold proofs.
The four historical unverified producers are adjudicated individually; no skipped
cost binding is promoted by these functional fixtures.

All formal exits bind one frozen wheel and the R10.2 constraints (including
SQLGlot 30.8.0). Native numeric oracles use their current component/result carrier
contract; Duration and statistical assertions stay strict. Cold reads preserve
stored primary values. Required failures and unverified exits keep this phase
incomplete; no required unresolved failure or unverified exit remains in this scope.
Unqualified physical shapes remain explicit refusals and do not gain
qualification by sharing a backend name.

The capability mapping below identifies the finite R10.3 witnesses. It does not
grant real Agent, release or unexecuted physical-key qualification.

| Capability | Journey and independent owner |
| --- | --- |
| C01 datasource | A12; source_profiles, datasource_profiles_store and producer_recovery, including separate file/HTTP boundaries |
| C02 authoring identities and units | A05/A09/A10/A12/A13; persisted definitions, roles, version and exact subject/occurrence keys |
| C03 members/read/version | A01/A05/A12; versioned recovery and source schema-drift refusal |
| C04 original observations | A01/A03/A06; native_numeric, multiroot_consumers and reference_consumers |
| C05 complete coordinates/reductions | A01/A03/A07/A11/A13; complete keys, row-statistics and partial original rollup |
| C06 time/folds | A05/A06/A11/A13; temporal timezone refusal and elapsed/calendar oracles |
| C07 comparisons/selection | A02/A06; comparison_consumers and analysis_comparison_runtime |
| C08 cohort/reference/display | A07/A08; fixed_selection_runtime, cohort_unknown and analysis_display |
| C09 attribution | A07/A09; attribution_recovery with source/fixed/cold component carriers |
| C10 distribution methods | distribution_consumers finite exact/explicit-approximate numeric/date/string witnesses with their own physical keys |
| C11 Event matching/Funnel | A09; native_funnel_recovery, raw assignment/multiplicity and complete units |
| C12 exact duration/domain | A09; strict Duration and Journey-to-Subject dropout observation |
| C13 Lifecycle | A10; history_public_recovery_worker raw-event assertions, actual controlled selection inputs and retained canonical history |
| C14 statistical methods | A04/A11; method_consumers, strict independent statistics oracles, statistics_kernel and composition |
| C15 Session/Run/Artifact | All journeys; analysis_state, graph_publication, public_refusals, premise_verification and local/native deadline owners |
| C16 public disclosure | installed surface/console/Help, actual dynamic K and default drift/reachability/budget gates; real Agent remains R10.4 |
| C17 packaging/privacy | Python 3.12 extras and Python 3.10 base/DuckDB isolation, poisoned imports, A12 secret/subject-key owners |
| C18 Anchor/retention | A13; anchors_oracle and retention_worker raw-fact oracle, multiple Anchors, DST, overlap and complete fixed opportunity domain |

## R10.3 final exits and retained evidence

The reproducible command/exit/log-hash records are in
`evidence/r103/final-06/commands/`. Full argv and working directories are retained;
installed child commands, per-phase report hashes, three-process identities,
physical trace keys, native submission counts, raw-fact oracle references and
producer/profile recovery are in
[qualification-06.json](../../../evidence/r103/final-06/qualification-06.json)
(SHA-256 `58f42abccea97dd5d1f0e640f3498f0057b31f1669312099e6b1194ed0b13721`). This is the per-item ledger for the 26 interpreter/journey
combinations and 106 producer recovery receipts, all with three independent
processes. The [file manifest](../../../evidence/r103/final-06/SHA256SUMS.json)
binds retained logs, declaration/data fixtures, raw projects, archives, constraints,
collection scripts and final dependency/audit records. Durable tests do not read it.

| Gate | Recorded command and exact disposition | Original exit | Log SHA-256 |
| --- | --- | --- | --- |
| Engineering | `engineering-evidence-final-14`: make check-agent; 4939 passed, one skip, 329 typed modules, lint/import/API docs | 0 | `c1f57cb3a6d37e4ffd4e42956588cc3d9313f197f773d47961e5c025f7aaf752` |
| Python 3.12 package | `installed-312-final-06`: 49 passed, one obsolete admission-tripwire owner failed, ten deselected; all 39 journey phases passed | 1 | `1711e5b16a055dfd64b2f5ea8d61f817ed47cb6883c77ff606405978b1cc42a1` |
| Python 3.10 package | `installed-310-final-06`: 44 passed, the same obsolete tripwire owner failed, 15 deselected; all 39 journey phases passed | 1 | `227be2e48b9aff7e10a493330104819335f9366929fddab86c054cfa27435fd2` |
| Python 3.12 refusal repair | `installed-312-owner-retry-06`: seven passed; current graph_store.admit tripwire, no pre-refusal I/O or Run | 0 | `d290886ba1fd121f228842b715b3d69adbfe23531fb3ab8898245889e4747526` |
| Python 3.10 refusal repair | `installed-310-owner-retry-06`: seven passed; same independent refusal checks | 0 | `28236fa6a5c0a779127232473817b547800928669f795dfda45a0af9c02803ae` |
| Healthy native group | `installed-healthy-final-06`: 44 passed, 21 unselected/inapplicable skips; SQLite/PostgreSQL/MySQL/Trino | 0 | `4f46a40b72cbc30b016cbf2d21669fff1af911d8a68a1c628722b20d19965304` |
| ClickHouse standalone original | `installed-clickhouse-final-06`: 12 passed, one deadline-load fixture failed, 52 unselected/inapplicable skips | 2 | `619e08f0df958e035e25ebccac5711f13eff6bd3dcd0a4bffa12e9e646305a88` |
| ClickHouse resource repair | `installed-clickhouse-owner-retry-06`: two passed; native timeout/SIGINT, exact server termination and atomicity | 0 | `8ae7096bf9a33e3e85af5e3852c5563a92bf82415398cf9ac6aef3ce3b18e668` |
| ClickHouse Distributed | `installed-distributed-final-06b`: two passed, 63 deselected; separate distributed producer/profile topology | 0 | `ec86e5c0209e39584062a208362411595645344db61ba94ebba80ccc9c3e3df8` |
| Setup logging regression | `installed-logging-final-06`: one base isolation passed, 20 deselected; distinct setup and boundary logs | 0 | `49b7a943144476071878262a17f64e294c3adfef9fb7fb94c2ef40205029cb10` |
| Evidence consistency | `evidence-consistency-final-06`: 13 isolated environments; original/repaired exits, hashes, noneditable origins and poisoned-source refusals | 0 | `ca5696f394ce0f4a689294da291ffe27e8010c77fefdfcff9b563e7faf4d2735` |

The two failed package aggregates and the failed ClickHouse aggregate retain their
original nonzero exits. Their successes are not described as aggregate passes.
The Store 8 refusal owner still targeted removed `SessionStore.admit`; it now
intercepts `graph_store.admit`. The original ClickHouse pending query finished in
23 ms and read only three rows; a random pushed-down predicate could empty the
load. The repaired fixture consumes random values over the full product before
returning the original schema. Server query IDs, expected timeout/cancel codes,
zero active queries, owner-thread cleanup, prior primary, Run/publication counts
and resource assertions remain strict. Only these affected owners were repeated
on the same wheel.

[Harness 14](../../../evidence/r103/final-06/harness-14.json) (SHA-256
`b4d630973167c3050fa4ab56360125963bb1636ec87d7fa7ca506e34c92cecf9`)
binds the three test-only post-freeze edits and their diff: the two owner repairs
and distinct setup dependency log naming. Harness 13 retains the exact staged
owner hashes. Nine overwritten setup pip-list logs were reconstructed byte for
byte against their original recorded SHA-256; their reconstruction and the later
boundary-probe logs are explicitly separate in `recovered-setup-logs-06.json`.
A fresh base install verifies the naming repair. The failed initial Distributed
shell invocation never collected tests; its quoted-selector retry is the two-pass
record above. The broad typing probe's unrelated imported-fixture/stub diagnostics
remain recorded; both touched owner modules and the installer helper pass strict
targeted typing with imported implementation checking left to the broad gate.

| Dimension | Current candidate disposition and independent boundary |
| --- | --- |
| source | Passed: 13 complete journeys on each interpreter, fresh realization/sharing and raw-fact oracles; six-backend finite method/physical traces |
| fixed | Passed: separate processes after removing sources/declarations, controlled retained parts, fixed-only continuation, exact hits, fitting/reference/coordinate scope and executable K |
| cold | Passed: separate source-forbidden processes; stored primary, exact identities/parts/definitions, no history replay/refit/source registry dependence |
| installed | Passed at the one candidate 06 wheel: Python 3.12 base and each extra; Python 3.10 base/DuckDB; noneditable site-packages/direct_url/hash and pre-business poisoned-source/wrong-hash refusals |
| backend | Passed at finite current keys: DuckDB/Parquet and SQLite; PostgreSQL/MySQL table and view; Trino Iceberg and memory connector; ClickHouse MergeTree and separate Distributed. No other connector, engine or key is inferred |
| resource | Passed: local deadline/SIGINT and selected native graph/authoring/source/transport owners; exact active reader/query proof, cancellation/cleanup, prior primary and failed Run/resource/publication atomicity |

The four historical unverified producers have separate current functional exits:

| Producer | Current evidence owner | source / fixed / cold / installed |
| --- | --- | --- |
| CSV | `test_producer_recovery/r94-recovery-duckdb-csv.json`, both interpreters | Passed / Passed / Passed / Passed |
| Parquet | `test_producer_recovery/r94-recovery-duckdb-parquet.json`, both interpreters | Passed / Passed / Passed / Passed |
| Local JSON | `test_producer_recovery/r94-recovery-duckdb-local-json.json`, both interpreters | Passed / Passed / Passed / Passed |
| Trino memory connector | `trino-test_producer_recovery/r94-recovery-trino-non-iceberg.json` | Passed / Passed / Passed / Passed |

HTTP is separately passed for datasource binding and the expected pre-I/O typed
analysis refusal, including query parameters and no partial publication;
fixed/cold are not applicable because no analysis producer is admitted. Table/view
and file/HTTP proofs are not interchangeable. Ordinary native numeric uses its
actual carrier contract; Duration and statistics retain strict assertions.

The 1260 physical trace records retain actual native submission counts;
zero-submission refusal records are not positive producer proofs. The 2988 saved
Artifact bindings are separately retained static diagnostics, not additional cold
or native invocations. All descriptors decode. Each local interpreter also retains
two intentionally empty/unversioned negative database fixtures, owned by
`test_read_factory_never_initializes_missing_or_unversioned_state`; read refusal
preserves their bytes. Store 1 through 7 refusal, missing/undecodable payload,
cross-Session and mixed-input refusal, and stored Findings without result payload
remain separately asserted by their current owners.

`delivery-audit.json` binds the unchanged baseline/branch, all 328 frozen
package/resource/dependency/LICENSE owner hashes, final dependency closures and
scoped delivery diff/new-file hashes. Existing repository build outputs and the
untracked P3/G1 document are retained. The dedicated environment is restored to
healthy PostgreSQL/MySQL/Trino with ClickHouse topologies stopped. All 15 authorized
skipped R9 cost exits remain unchanged; current functional producer qualification
does not promote old cost evidence. No commit, push, packaged skill edit, R10.4,
R10.5, real Agent, release or unconstrained latest-dependency qualification is made.

## Preserved R10.2 acceptance at its original candidate and scope

The pending/failed statements below retain their original R10.2 candidate and
scope; the current R10.3 dispositions are recorded above.

Date: 2026-10-07. Requested baseline: `panda@1db2ebc93dcee6014a2c8434b12dd76a0cc0bedb`.
Status: required R10.1 incremental closure and bounded R10.2 qualification passed.

Frozen HEAD is `74ca9447284089703ab9a1d7c2ab9da2238fb6ee`. The intervening
commit adds the G0 documentation and measurement script only; it changes no
product code, packaged resources or dependencies. The frozen HEAD plus the
recorded uncommitted diff and new files defines this candidate, not HEAD alone.

This index consumes the current R10 implementation plan. Historical acceptance
belongs to its original candidate and scope. R10.3 complete installed journeys,
R10.4 real Agent acceptance and R10.5 delivery/release gates remain pending.

## Current owners and incremental impact

| Capability | Current owner and applicable change | Evidence disposition |
| --- | --- | --- |
| C01 datasource | Datasource layer: selected driver errors, terminal SQL/preview display, credentials | Nine isolated environments, DuckDB/SQLite public reads, six missing-driver repairs and affected terminal Runtime checks passed; remote native source qualification remains separate |
| C02 semantic authoring | Semantic object model and native authoring Help: coherent statements and sequential local binding | Engineering gate and installed native Help/signatures passed on both interpreters; historical runtime evidence is not transferred automatically |
| C03 members/attributes | Analysis design: typed domains and version selection | Existing behavioral owner retained; J1/J2 graph and History views witnesses passed |
| C04 observation | Current method registry, native numeric implementation contract v5 | Earlier uniform numeric oracles superseded where the owning contract changed; J1–J4 raw-fact installation witnesses passed |
| C05 coordinates/reduction | Analysis design and A3 original-state/direct-key reductions | Local evidence retained at its original scope; installed J3 current-row/original-state witness passed |
| C06 temporal | Temporal semantics and current grid owner | Timed numeric/NoTime category composition passed in three new processes on both interpreters |
| C07 comparison/selection | Typed graph and A2 fixed selection | Existing behavior tests and installed J2/a02 representative selections passed |
| C08 cohort/reference/display | Analysis design and business data display contract | Row/byte budgets, precision, Cell states, redaction and source-free fixed rank/table passed in affected Runtime and installed checks |
| C09 attribution | Current allocation and component-state owners | Independent current/baseline attribution Runtime oracle and installed a07 witness passed |
| C10 distinct/quantile | Current method admission and producer qualification | R9 physical producer gaps remain; package import is not method qualification |
| C11 Event/Funnel | Event matching and Funnel contracts | Existing behavior retained; representative installed source/offline/cold witness passed |
| C12 Duration/selection | Exact Duration and Journey-to-Subject owners | Strict precision contract remains; not granted by ordinary numeric acceptance |
| C13 Lifecycle | History replay and saved state owners | Six affected Runtime checks and installed views/checkpoint recovery passed. Baseline source-selection fact scheduling gap remains failed and assigned to R10.3 |
| C14 statistics | Deviation/runs/association/forecast owners | Existing numerical laws retained; representative installed statistics/association recovery passed |
| C15 Session/Artifact/evidence | Store 8, descriptor v3, continuation v4, trusted local reads | Store 7 and older trust/revalidate obligations superseded; representative persisted Artifact reads/continuations passed. Full production, missing-payload and atomicity coverage remains with its behavioral owners/R10.3 |
| C16 public disclosure | Native Help, types, errors, result protocols, CLI and packaged skills | Engineering, installed signatures/Help/bootstrap and unchanged skill-resource links passed; real Agent acceptance remains separate |
| C17 project/dependencies/package | Project tools, ontology and package boundary | Core Arrow repair, isolated base/six-extra installation and identical archive bytes passed under the recorded constraints |
| C18 Anchor/retention | Current Anchor and retained opportunity-domain contracts | Existing behavior owner retained; complete installed/Agent coverage belongs to R10.3/R10.4 |

Current A1/A2/A3 documents retain their own bounded evidence. The committed
display change `1db2ebc93d` enters this candidate together with its compiler and
execution changes. No declaration/data fixture is granted broader source coverage
by a display row count or successful package import.

## Historical evidence boundary

Historical records are addressed as
`642271bf351c686a16b6d45151d4e896bb20c972:docs/superpowers/specs/<path>`;
the R10 implementation plan section 1.2 lists their exact paths. The R10.1
validation Markdown and committed candidate attachments are available through
Git. The local raw-log directory `evidence/r101/final-01/` is absent; its old
hashes cannot recover those discarded logs. None of these historical records
satisfies a current required package or Agent exit.

The R9 handoff retains 394 original IDs: 379 finite owner-proof bindings,
15 authorized skipped cost exits and four unverified producer bindings.
R10.2 does not restart cost collection or change these statuses.

## Incremental repairs and independent boundaries

The current candidate adds core `pyarrow>=10.0.1`. Current-row integer mean now
aggregates scalar operand bounds without calling a column-only reduction.
Fixed display views sharing exact Artifact receipts reuse one isolated executable
and retained definition closure; distinct receipt sets remain isolated. Definition
fingerprints, physical receipts and the 4 MiB/256 KiB limits remain unchanged.
`SlicePredicate` uses `typing_extensions.TypedDict` on supported Python versions;
Finding reads use the existing timezone-aware timestamp codec. Historical axis
preparation reads its final Dimension only after completing every relationship hop.

The durable regressions bind independent raw facts to integer mean, attribution
current/baseline columns and timed numeric/NoTime category rank/table results.
Installed probes check root and focused Help, concrete callable signatures, Enum
members, CLI initialization, packaged skill link bytes, optional-driver absence,
actual two-row DuckDB/SQLite reads, missing-driver repairs and pre-business source
rejection. Recovery probes prohibit source connections and current semantic loading
before Session resume, with source files and declarations removed.

SQLGlot 30.21.0 produces `DROP VIEW IF EXISTS` without a view name for the Ibis
cleanup expression used by these journeys. This is reproduced in
`evidence/r102/sqlglot-compatibility.log`. The applicable isolated constraints use
30.8.0. Qualification applies to the recorded dependency closures; unconstrained
latest dependency resolution is not qualified by this candidate.

## Journey handoff

The entries below are representative installation witnesses, not complete A01–A13
acceptance. Every complete A journey remains pending for R10.3; real Agent
acceptance remains pending for R10.4.

| Journey | R10.2 witness and owning boundary | Complete journey |
| --- | --- | --- |
| A01 | J1 numeric graph production, saved continuations and cold recovery | Pending |
| A02 | J2 and a02 comparison/selection continuations | Pending |
| A03 | J3 multi-root ratio and original/current-row reductions | Pending |
| A04 | J4 paired association and fixed continuations | Pending |
| A05 | No complete installed historical-member/time-role witness in this phase | Pending |
| A06 | Complete branch/period/nested comparison combinations remain unverified | Pending |
| A07 | a07 share, reference, attribution, component mix and fixed tables | Pending |
| A08 | a08 full opportunity-domain cohort and structured incomplete-coverage rejection | Pending |
| A09 | Representative Funnel source/fixed/cold worker | Pending |
| A10 | Representative History views/checkpoint axes worker; source-selection gap below | Pending |
| A11 | Representative statistics source/fixed/cold worker | Pending |
| A12 | Renamed operations/device/reading/sample declarations and kWh units | Pending |
| A13 | Complete Anchor/retention journey remains unverified | Pending |

## Baseline failure kept separate

An expanded History Runtime check returned nine failures and 42 passes. The
failures concern `_fact_relations` resolving a local History selection during
source preparation, outside the display/axis repair. A fresh baseline snapshot
of `1db2ebc93d` reproduces the same `KeyError` for
`test_same_run_history_selection_prepares_all_sources_before_local[parquet-interval]`.
The captured-observation recovery probe also fails at that fact scheduling boundary.
These are failed, not skipped or passed; their raw logs and commands are retained
as `history-runtime-repair`, `history-axes-acceptance` and
`baseline-history-selection`. The current affected History views and checkpoint
axis checks pass separately as `history-axes-focused` (six checks).
This gap stays with R10.3/A10; this phase does not grant complete History consumer
qualification or modify the source/local scheduling contract.

## Candidate and executed evidence

All required R10.2 exits below passed. The default engineering suite retains one
reported skip; the five unselected Python 3.10 remote-extra cases were deselected
by the accepted scope. No required installation check was skipped. Earlier
failed/superseded attempts and the baseline History failures remain in the evidence
archive with their original exit codes. Tests do not consume this index or local
qualification artifacts.

### Frozen identity and retained files

- Version: `0.5.3.dev0`; frozen source inventory: 1282 files; product/resources: 325 files.
- Full frozen diff SHA-256: `2fae041250c7347ca1ca122e6343d316119bd3ec58f999343d9392389de35077`; [candidate manifest](../../../evidence/r102/final-04/commands/candidate-delivery.json) and [diff including new files](../../../evidence/r102/final-04/commands/candidate-delivery.diff).
- Frozen build directory: `/tmp/marivo-r102-zncy6a94/snapshot-delivery`; current product/resource/dependency bytes remain identical. Subsequent changes are documentation/acceptance output only, recorded in the final review manifest and diff.
- Existing repository build/dist/egg-info artifacts were retained. Builds ran in external snapshots; no editable install or copied development site-packages was used.
- [Wheel](../../../evidence/r102/final-04/marivo-0.5.3.dev0-py3-none-any.whl): `96cea6f38fa5a7465495997a9fe8517f4cf0d6f2901a3511b1eb62ab8a34671c`.
- [sdist](../../../evidence/r102/final-04/marivo-0.5.3.dev0.tar.gz): `6f603e21aef5c2f7f6fefee4a9472ccb9a0e4bada1fbb5da74a47e0cd4fecf11`.
- [Archive metadata and complete per-file hashes](../../../evidence/r102/final-04/archives.json): 331 wheel files and 337 sdist files; all 325 product/resource bytes match both archives and the frozen candidate. Record SHA-256: `5011400b61b0efdbb51a3748eb49a8c50387b2f766255cf89d9f21ddd1413e0b`.
- [Final review identity/diff](../../../evidence/r102/final-04/review.json), [qualification report](../../../evidence/r102/final-04/qualification.json), [complete evidence manifest](../../../evidence/r102/final-04/manifest.json) and [manifest SHA-256](../../../evidence/r102/final-04/manifest.sha256) bind the delivered local evidence. The manifest excludes its own two output files.
- Nine successful virtual environments and their Artifact projects remain under `/tmp/marivo-r102-zncy6a94/installed-{310,312}-delivery`. Failed superseded environments were removed only after retaining their pyvenv configs, constraints and reports; their snapshots, archives, Artifact projects and raw logs remain. No unrelated workspace state was removed.

### Required commands and raw-log hashes

Each linked record contains the exact argument vector, working directory, exit code and raw-log SHA-256. All eight required exits are `0`.

| Record | Command | Exit | Finite scope | Raw-log SHA-256 |
| --- | --- | --- | --- | --- |
| [package-build-delivery](../../../evidence/r102/final-04/commands/package-build-delivery.json) | `make pypi-build pypi-check` | 0 | Passed: external frozen snapshot; build, metadata and exact archive-content checks | `a257c7f644d49a553fefb17c6a7fa949896e4921b7fc145506efd38decbba028` |
| [installed-312-delivery](../../../evidence/r102/final-04/commands/installed-312-delivery.json) | `.venv/bin/pytest -q --tb=short --maxfail=5 -n 0 -m release --basetemp=/tmp/marivo-r102-zncy6a94/installed-312-delivery tests/packaging/test_installer.py tests/packaging/test_installer_uv.py tests/packaging/test_wheel.py` | 0 | Passed: 46 serial release checks; base plus each of six extras; installer/uv/CLI, representative three-process recovery and source rejection | `c33d764d8425ddae82f14cbcd198f74e37e1aae851f7014910d7a8f572e1dbaa` |
| [installed-310-delivery](../../../evidence/r102/final-04/commands/installed-310-delivery.json) | `.venv/bin/pytest -q --tb=short --maxfail=5 -n 0 -m release --basetemp=/tmp/marivo-r102-zncy6a94/installed-310-delivery tests/packaging/test_wheel.py -k 'not installed_dependency_isolation or base or duckdb'` | 0 | Passed: 16 checks; base and DuckDB representative journeys; five remote-extra cases deselected | `2e7dcdd15a4d7a9febd5fa86f73bf61e69f2797d7a7bd5a47f5edaac5f6ac089` |
| [engineering-delivery](../../../evidence/r102/final-04/commands/engineering-delivery.json) | `make check-agent` | 0 | Passed: lint/import contracts, typing (329 source files), 4930 default tests; one reported skip; API documentation | `1d10982acc6af063b09d23998b7233da644973df6cd7f695c698fc1c9c84e3e2` |
| [affected-runtime-delivery](../../../evidence/r102/final-04/commands/affected-runtime-delivery.json) | `make runtime-test-agent 'TESTS=tests/analysis/numeric/test_analysis_display.py tests/analysis/statistics/test_analysis_deviation_display.py tests/surface/test_documentation_examples.py tests/project/test_preview.py tests/datasource/test_datasource_raw_sql.py'` | 0 | Passed: 97 display/statistics/documentation/preview/raw-SQL checks | `0a8654454e1f98565586c0cfb113e86bb653033df885a494affd9851a6f173b4` |
| [history-axes-focused](../../../evidence/r102/final-04/commands/history-axes-focused.json) | `make runtime-test-agent 'TESTS=tests/analysis/lifecycle/test_history_public_recovery.py::test_history_public_recovery[views] tests/analysis/lifecycle/test_analysis_history.py::test_checkpoint_axes_use_each_historical_version_and_full_null_tuple tests/analysis/lifecycle/test_analysis_history.py::test_checkpoint_axes_prepared_on_complete_subject_domain'` | 0 | Passed: six History views/checkpoint historical-version/full-null-axis checks | `26e2beba4c6fadf294172c8b079c3252b1cdee70b9fd68f6db4f91101155d184` |
| [site-content](../../../evidence/r102/final-04/commands/site-content.json) | `npm run verify:content` | 0 | Passed: 343 required content files | `c037ea4de2bbf7b72f771ee9f2a046a629b12a8bad1d3d89a6f1398f0e571673` |
| [site-build](../../../evidence/r102/final-04/commands/site-build.json) | `npm run build` | 0 | Passed: installation-script checks and 308 generated routes | `6daf056548e566ec55b6ea76572e845664afa1453dffbdf7d5ccbf1f1e943116` |

### Isolated dependency closures

Every environment passed `pip check`, inspected actual distributions and noneditable `direct_url.json`, and confirmed the same wheel hash. Core imports and backend discovery loaded no optional native driver; unselected native drivers were absent. The two DuckDB environments include full representative recovery probes; SQLite includes a real two-row public read. Remote extras include driver loading and structured missing-driver repairs only.

The [environment inventory](../../../evidence/r102/final-04/environments.json) has SHA-256 `e80c7e6a402ea9ab4bb91d22610e22574261a8bb7522983eb7711bb81cbebeac`. Each environment directory contains the complete freeze, dependency list, install/`pip check` logs, command records, origin reports and `pyvenv.cfg`. Positive probes retain 200 valid paired start/exit origin captures across 100 processes. Separate rejected-origin/hash reports preserve deliberate negative cases.

| Python | Extra | Selected driver | NumPy / SciPy | Origin captures | Complete constraint SHA-256 |
| --- | --- | --- | --- | --- | --- |
| 3.10.20 | [base](../../../evidence/r102/final-04/environments/310-base/constraints.txt) | none | 2.2.6 / 1.15.3 | 4 | `aacce56b882e50022801cf05eec705cc2f1b275da010c0d4ced582daa7f4553f` |
| 3.10.20 | [duckdb](../../../evidence/r102/final-04/environments/310-duckdb/constraints.txt) | duckdb 1.5.6 | 2.2.6 / 1.15.3 | 86 | `ed3cd12bbfd43a3ad4a99a13167aa73a37628af36f083db8fc3c9bf7bb389cf4` |
| 3.12.13 | [base](../../../evidence/r102/final-04/environments/312-base/constraints.txt) | none | 2.5.3 / 1.18.1 | 4 | `2a6205f13dc385ec2e291fe0c5dbeed91ba20039803e6910c620d3ba4d2efb23` |
| 3.12.13 | [clickhouse](../../../evidence/r102/final-04/environments/312-clickhouse/constraints.txt) | clickhouse-connect 1.9.0 | 2.5.3 / 1.18.1 | 4 | `1f7f045d716c7dc91669f2c07c2ae4ed70e6e6b486756c736f651b9937c95912` |
| 3.12.13 | [mysql](../../../evidence/r102/final-04/environments/312-mysql/constraints.txt) | mysqlclient 2.3.0 | 2.5.3 / 1.18.1 | 4 | `2a37ffa2e547f89939fc4be04c9d59e60a3dc632806d85b0e35eb8b95eb2ff05` |
| 3.12.13 | [postgres](../../../evidence/r102/final-04/environments/312-postgres/constraints.txt) | psycopg 3.3.6 | 2.5.3 / 1.18.1 | 4 | `8f130014d56fd7debef8facffffbcf3ed2d43cbda08b70fd19c093848ef56541` |
| 3.12.13 | [sqlite](../../../evidence/r102/final-04/environments/312-sqlite/constraints.txt) | stdlib sqlite3 | 2.5.3 / 1.18.1 | 4 | `30999e05969dea17b193ea9db4f0eaefdd1c208cbb9ce51710a17f0c90a71fe1` |
| 3.12.13 | [trino](../../../evidence/r102/final-04/environments/312-trino/constraints.txt) | trino 0.340.0 | 2.5.3 / 1.18.1 | 4 | `9719bd68be649a186dffaaf79386331519012c0644683252e5fe365086060c45` |
| 3.12.13 | [duckdb](../../../evidence/r102/final-04/environments/312-duckdb/constraints.txt) | duckdb 1.5.6 | 2.5.3 / 1.18.1 | 86 | `1320a2e180bbccaa78c244aa36f0c5745aa405d52e1fd1e0ddab9b1d24b5aa95` |

All nine environments resolved Arrow `25.0.1`, Ibis `12.0.0`, SQLGlot `30.8.0` and pip `26.2.1`. Python 3.10 and 3.12 use distinct compatible base constraints; the 3.12 all-environment freeze was not imposed on 3.10.

Native prerequisites are recorded in [native-prerequisites.json](../../../evidence/r102/final-04/commands/native-prerequisites.json): macOS arm64, PostgreSQL `libpq 18.6` at `/opt/homebrew/opt/libpq` with `DYLD_LIBRARY_PATH=/opt/homebrew/opt/libpq/lib`; existing MariaDB Connector/C `10.8.8` at `/opt/homebrew/opt/mariadb-connector-c`. The retained mysqlclient `2.3.0` wheel is linked to `libmariadb.3.dylib`, SHA-256 `e502d3b93b4068996ea2f98091ced3c2e01536bfb39fc67cfc9152790c6dad4d`. The initial cached mysqlclient binary failed native import and is not qualified; its log remains.

Formal runs set `MARIVO_TEST_WHEEL_DIR=/tmp/marivo-r102-zncy6a94/snapshot-delivery/dist/pypi`, `MARIVO_TEST_PYTHON=/Users/lichengxiang/.local/bin/python3.12` (or `python3.10`), and `MARIVO_TEST_CONSTRAINTS` to the matching `evidence/r102/constraints/base-312.txt` (or `base-310.txt`). Python 3.12 additionally used the native settings above, `MYSQLCLIENT_CFLAGS=-I/opt/homebrew/opt/mariadb-connector-c/include/mariadb`, `MYSQLCLIENT_LDFLAGS="-L/opt/homebrew/opt/mariadb-connector-c/lib -lmariadb"`, and `PIP_FIND_LINKS=/tmp/marivo-r102-zncy6a94/mysql-driver`; `PIP_NO_BINARY` was unset. Reuse may point these paths to the retained `final-04` wheel/native/input-constraints directories. Both formal pytest runs used `-m release -n 0`; `release-test` was not used to rebuild the candidate.

Later R10 journeys and Agent runs must reuse this wheel together with the recorded applicable dependency constraints and native prerequisites. Product/resource/dependency changes require a new freeze and affected requalification. This record grants no remote business execution, complete A01–A13 coverage, real Agent, full Runtime, release or publication qualification.
