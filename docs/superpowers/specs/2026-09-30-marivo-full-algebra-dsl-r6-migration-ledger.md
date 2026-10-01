# R6.1 contract freeze and consumer migration ledger

Date: 2026-09-30. Status: R6.1 contract/static inventory complete; R6.2 public comparison implementation is connected, with final validation recorded below;
R6.3 and R6.4 are connected through the bounded qualifications recorded below;
R6.5 ranking and terminal display are connected, with final validation recorded below;
R6.6 allocation is connected; final qualification is recorded below. R6.7 remains unimplemented. This ledger indexes the
[R6 implementation plan](2026-09-30-marivo-full-algebra-dsl-r6-implementation-plan.md)
and sole contract owners; it is not another method registry or executable API.

## Actual baseline and scope

Branch `panda`, HEAD `44ff8478a740c60b23fc1566a52523acaa476851`.
At entry, staged/unstaged tracked diffs were empty. The sole untracked file was
`docs/superpowers/specs/2026-09-30-marivo-full-algebra-dsl-r6-implementation-plan.md`.
Its original content is retained; R6.1 only updates status/navigation after the
freeze. The draft's `ea787d116b` plus dirty R5.7 description is historical,
not this execution baseline. During this turn another writer committed the
previously untracked plan as `41e2a69126b73eca0f0973e6c4f7e434420ca076`.
The committed blob SHA256 matches the initial untracked plan exactly; that
commit changes only the plan. Current HEAD is that commit. Product baseline and
consumer snapshot remain valid; this task did not create or undo that commit.
R5.7 acceptance is read from the
[main record](2026-09-26-marivo-full-refactor-acceptance.md#r57-completed-qualification-2026-09-30),
not rerun or enlarged. No product, tests, AGENTS.md or packaged skills changed;
no commit, push, publication, external message, release-check or MinIO.

R6.1 success means: C07–C09 have closed signatures, compatibility, numeric/Cell
rules, RequiredParts/K, state versions, intended physical routes and named test
owners; real legacy imports/calls/registrations and deletion gates are recorded;
R6.2 can start without inventing a policy deferred from this freeze. It does
not mean any new method passed Runtime or installed-package acceptance.

Excluded: named ms.statistical_weight / mv.statistical_weight and dependent
current-row weighted mean; distinct_membership/distribution_shapley public
attribution; arbitrary FormulaBasis; R7 domain producers/funnel methods; R8
statistical extensions; R9 six-backend expansion; R10 real Agents/release.
R5 Decimal/Duration current-row sum/mean/min/max remain unqualified.

## Sole owners and resolved decisions

| ID / coverage | Sole owner | Frozen decision / implementation package |
| --- | --- | --- |
| F01 / C07 | [Analysis API](../../specs/analysis/python-analysis-design.md#r61-frozen-relation-composition-target) | Closed three-design constructors, Exact/Union and ordinary one-to-one ratio; all numeric families use concrete paired variants; R6.2 |
| F02 / C07 | [Comparison templates](../../specs/analysis/python-analysis-design.md#comparison-compatibility-and-quantity-templates) | Same explicit target realization for time/period, common Group/Singleton for cohort; recursive ordered templates, no cancellation of independent July captures; R6.2 |
| F03 / C07 | [Method matrix](../../specs/analysis/operators-and-frames.md#method-parts-and-continuation-matrix) | Full-key injection/image checks, MissingCoordinate separate from four Cells, concrete Metric empty finish (including Null), no Difference/ordinary-ratio original rollup; R6.2 |
| F04 / C07–C08 | [Predicate/cohort rules](../../specs/analysis/operators-and-frames.md#predicate-and-cohort-consumption) | Closed bound StatePredicate and composites, strict where, no short-circuit exemption; full opportunities and decidable quantifiers; R6.3 |
| F05 / C08 | [Reference/view protocols](../../specs/analysis/python-analysis-design.md#reference-named-view-and-terminal-protocols) | ReferenceWeights binds complete strata and unit; Singleton reference shape, named view families, terminal table output; R6.4/R6.5 |
| F06 / C08 | [Numeric reference policy](../../specs/analysis/operators-and-frames.md#r6-numerical-target-and-reference-policy) | Exact I/D weight sum=1; exact represented F sum tolerance 1e-12, no normalization; zero-weight keys still required, non-Defined zero-weight values retained without arithmetic; explicit output types; R6.4 |
| F07 / C08–C09 | [Rank/attribution rules](../../specs/analysis/operators-and-frames.md#rank-attribution-arithmetic-and-reconciliation) | Four ties, stable typed order, global limit, side terms, common Top-K/typed Other, independent resolutions and filtered scope; R6.5/R6.6 |
| F08 / C09 | [Numerical and reconciliation owner](../../specs/analysis/operators-and-frames.md#rank-attribution-arithmetic-and-reconciliation) | Additive I/D/T exact; component_mix I/F/D with exact components and single finish; same old threshold for float/rounded Decimal, no float coercion of exact state; R6.6 |
| F09 / cross-cutting | [Runtime](../../specs/analysis/session-state-and-runtime.md#r61-composition-state-and-recovery) | Ordered dependencies, common publication, difference state v2, new state kinds v1, changed existing implementation contracts v4; R6.2–R6.7 |
| F10 / C07 time | [Temporal owner](../../specs/analysis/timezone-and-calendar-design.md#r61-period-correspondence) | Complete retained bucket ordinals, equal lengths, no post-filter renumbering, DATE/instant/calendar authority retained; R6.2 |
| F11 / definitions | [Semantic handoff](../../specs/semantic/semantic-object-model.md#r61-relation-composition-handoff) | Existing units/roles/one-to-one declarations consumed, no analysis facts in Catalog, withdrawal preserved |

### Conflict dispositions

- Historical Delta exposes delta/relative_delta as fields and accepts older
  Dataset alignment shapes. The target has one value per Difference and a
  closed compare value parameter. M03/M04 delete old consumers; no alias/shim.
- Existing core ValuePredicate is a single bound scalar with an unknown policy;
  legacy AnalysisPredicate has a different tree. F04 becomes the single new
  bound-predicate owner. Legacy unknown="drop" cannot implement strict where.
- Earlier empty-side prose names only Undefined, while R5 Metric empty mean/sum
  can produce Null. F03 retains the concrete empty finish's tag/reason for the
  synthesized missing side; it never overrides a Present non-Defined endpoint.
- The old attribution formulas include a W_i=0/N_i!=0 contradiction check. The
  abbreviated N_i/W_total description does not repeal it. F07 preserves that
  guard, full endpoint reproduction and complete-partition admission.
- Legacy compiler _close casts to float, while _reconciles can compare exact
  carriers. F08 requires exact I/D/T additive reconciliation and exact Decimal
  evaluation of the accepted rounded-allocation threshold; helper reuse must
  preserve that distinction. Duration component_mix/standardize are explicitly
  rejected new methods, not claims against R5 Duration Metric methods.
- Existing mv.window_bucket and all_of/any_of/not_ are public legacy bindings.
  Their unique names migrate in their owning packages; do not export competing
  new constructors or redirect old Help as compatibility.
- R6.4 accepts the subsequently confirmed sum/count/linear standardization
  admission alongside mean/weighted_mean/original ratio. This is an explicit
  receiver-method expansion, including public int64 positives, with the
  statistical Entity proved from frozen contribution/component definitions.
  It does not admit ordinary relation ratios, Difference or quantiles.

## Actual consumer inventory and deletion gates

Paths below use A=`marivo/analysis`; line numbers refer to the baseline above.
Direct import/call inventory is static evidence, not a Runtime reachability
proof. Imports under TYPE_CHECKING are distinguished from lazy public export
resolution. A read-only import probe confirmed LogicalDeltaDataset,
MaterializedDeltaDataset, LogicalAttributionDataset, window_bucket and all_of
are in mv.__all__ and resolve at baseline; TimeChange, reference_weights and
table are absent. Private legacy tests passing does not qualify new public work.

| ID | Actual baseline edge / registration / reachability | Replacement and deletion condition | Residual owner / counterexample |
| --- | --- | --- | --- |
| M01 | `A/public_dsl.py:1666,1822` compare → `materialization/graph_relation.py:430` combine → graph_composition or CellDerive; fixed endpoints restricted to ObserveMetric/ObserveCount and same frozen member | F01–F03, R6.2 extends sole public graph path; remove narrow restrictions only with exact new qualifications | Independent equal-definition target; swapped/nested endpoints; V01–V03/V10 |
| M02 | `core/predicates.py::ValuePredicate`; `public_dsl.py::_select`; `compiler/predicates.py::lower_bound_predicate`; `observation/predicates.py` legacy trees | F04, R6.3 registers closed bound trees and shared input mapping; migrate all_of/any_of/not_ public dispatch without duplicate grammar | R7/R8 legacy predicate consumers need explicit adapters within their own blocked chains; no silent unknown drop; V04/V05 |
| M03 | `observation/metric.py:138,310` → `operators/compare.py::compare`; `compare.py:314` constructs metric.compare; `compare.py:456` register_delta called by `observation/contracts.py:1947`; Delta publicly resolves through __init__/_public | F01–F03, R6.2/R6.7 remove old Metric→Delta constructors, Delta family and registration after public regression replacement | `domains/event.py:61`, `event_comparison.py:18` import Delta; split required R7 contracts before deletion; V01–V03/V12 |
| M04 | `compiler/lowering.py:16,2525` → comparison.lower_compare; `comparison_codec` read by `materialization/contracts.py:1507,1593,2307`; `comparison_publication` used by dataset_publication, forecast, association, event comparison and attribution | F09, R6.2/R6.7 replace general comparison with graph state v2; delete R6 branches/codec producers and readers, no old state continuation | Common ordered-input authority used by R7/R8 must move to owning neutral contracts; wrong roles/receipts; V10/V11 |
| M05 | `operators/delta.py:98,210` → attribute; `attribute.py:213,226` constructs delta.attribute/expanded; register_attribution at :469 called by `observation/contracts.py:1948` | F07–F09, R6.6/R6.7 migrate to Difference.attribute, remove old family constructors and R6 registrations | `domains/event_attribution.py:22` and `operators/driver_axes.py:17` import attribute; retain only identified R7/R8 helper facts until their cutover; V08/V09 |
| M06 | `attribute_expansion` imported by attribute:222, driver_axes:145, lowering:2674; `attribute_values` imported by event_attribution_values, distribution_values, driver_values, publication and local_execution | R6.6 makes axis expansion explicit graph dependencies and uses registered allocation on exchange; R6.7 deletes R6 caller branches | R7 funnel/R8 driver helpers are real residual consumers, not authority to retain old R6 execute; fixed missing axes must fail; V09 |
| M07 | `compiler/attribution` imported by lowering:2538/3298, event_attribution:8, driver_candidate:12, entity_candidate:12, distribution:10; contains _close/_reconciles | R6.6 moves accepted arithmetic/checks to method owner and selected route; R6.7 removes R6 lowering branches | R7 event/R8 candidate/distribution dependencies require split by symbol; exact large-int/Decimal counterexample; V08 |
| M08 | distinct_attribution consumed by lowering:2545/3318; distribution_attribution by lowering:2569/3306 and operators/distribution_values:11 | No R6 public qualification. Remove R6 dispatch and family capability claims in R6.7; do not resurrect retained membership/distribution to satisfy old tests | Actual R8 distribution_values dependency remains assigned R8; display scalar cannot prove required state; V08/V12 |
| M09 | attribution_codec read by `materialization/contracts.py:1503,1589,2303`; attribution_publication imported by dataset_execution:44, source_stage:28, local_execution:51 and store:964; evidence/_dataset_reads consumes summaries | F09 R6.6/R6.7 uses common graph parts/atomic publication; delete old R6 codec/publication dispatch, migrate Evidence reads | Shared event publication and R8 evidence consumers split before helper deletion; stale reconciliation scope/corrupt side view; V09/V11 |
| M10 | `operators/registry.py:58` legacy_source_migration_stage marks metric.compare/delta/attribution R6; source_admission consumes marker; dataset_execution carries AttributionSourceSummary | Remove migrated R6 registrations and dispatch, not merely the marker; R6.7 reverse scan imports, IDs, module strings, private workers and codec branches | Keep event/lifecycle R7 and candidate/forecast/association R8 blocks. No default source admission; V10/V12 |
| M11 | observation.population/metric still imported by session/core, _lazy_sources and domains; coordinates imported by attribute_expansion, event_axes/sources, lowering/normalize/source_dependencies; fold_contracts has compare/attribute, forecast/correlation/event consumers | R5 ledger M05/M06 R6 consumers migrate here; remove their actual calls after replacement; no whole observation-directory deletion | R7 Subject/Event and R8 statistics remain explicit residual owners; fold/coverage contradictions; V08/V09 |
| M12 | AN11 unused summary module deleted in R6.6; AN12 source_stage hook is rejection-only, with no backend statement submission | R6.6 public typed scope checks and registered allocation have native submission/raw-fact evidence; R6.7 owns remaining legacy hook/helper deletion | The old hook is **retained but blocked**, not reported deleted. No handwritten R6 replacement or remote qualification is granted |
| M13 | `methods/semantics.py` CONNECTED_METHODS and MethodName; builtin cell.difference int64 source/local declarations; graph_plan/lowering and graph_{source,local}_execution consume exact registry selection | Extend F01–F09 through existing registry/rules; no family executor, backend-name dispatch or runtime fallback | Required method×type×route/time positive cells below; static registration is not qualification |
| M14 | Native _help, analysis/_capabilities, lazy __init__/_public, CLI, latest site en/zh, export/typing/help tests retain current surface | R6.2–R6.6 update affected disclosures per connected capability, R6.7 remove obsolete targets/imports without redirects | V12 exact export/reachability/budget and current-state continuation checks, not shadow renderer inventories |
| M15 | tests/test_lazy_compare_* and lazy_compare_runtime_worker; tests/test_lazy_attribute_*, test_lazy_attribution_* and lazy_attribution_runtime_worker still exercise old families | Preserve raw-fact/Fraction/Decimal oracles and negative cases; move public consumers into planned R6 tests; remove old worker only after its assertions have replacements | Do not delete/skip legacy tests to claim closure; R6.7 test manifest maps every disposition |

`observation.aggregation` had no direct import-from edge in this static scan;
that is not proof of absence of re-exports/dynamic imports. Reverse-text scan
and registry audit remain required at R6.7. The raw snapshot contains exact
symbols and source hashes for 253 files, 419 import statements and 538 direct
alias calls; receiver calls/dynamic strings require the explicit rows above.

## Required physical and test ownership

All new cells below are **planned / unverified**, not passed, blocked by user,
or intentionally skipped. Source S means separately tested DuckDB native table
and local Parquet. Fixed F means artifact_python on verified exchange with
DuckDB/source access disabled. No source/fixed mixed mode is admitted. Each
registration must name exact physical type, time shape, domain, selected
implementation and checks; broad family-level rows here do not register it.

| Package / methods | Target selected route | Required time/domain positive cells | Planned new test owner / V coverage |
| --- | --- | --- | --- |
| R6.2 map_correspond, cell.difference/relative_change/ratio | S: Ibis key/coverage/endpoint preparation, registered local arithmetic where required by numeric matrix; preserve existing qualified int64 Ibis difference. F: registered local same semantic method | Entity composite keys, Group, Singleton; NoTime ratio/cohort contrast, scoped UTC-us TimeChange, DATE and aware-local PeriodChange grids; I/F/D/T per owner | `tests/test_analysis_comparison_r62.py`, `tests/test_analysis_comparison_runtime_r62.py`; V01–V03/V10/V11 |
| R6.3 predicate transport, domain.cohort, members | S: Ibis full-domain/key/coverage checks and predicate/quantifier expressions; F: registered local same rules | Entity and complete Entity×Time, four Cells, composite Subject keys; NoTime and R5 grids, empty full opportunity domain | `tests/test_analysis_predicates_r63.py`, `tests/test_analysis_cohort_r63.py`; V04/V05/V10/V11 |
| R6.4 reference.share/penetration/standardize | S: Ibis governed key/support/strata preparation, registered local numeric method for exact weighted finish; F: same local consumer | NoTime or exactly matching frozen time scopes; Group strata→Singleton; original Metric/runtime Metric sum/count/linear/mean/weighted_mean/ratio standardization, I/F/D values; additive I/F/D/T share; complete member reference | `tests/test_analysis_references_r64.py`; V06/V10/V11; actual cells recorded in the R6.4 section below |
| R6.5 display.rank/table and transport | S: Ibis key/partition preparation plus registered local deterministic order/table assembly; F: same local consumer | Entity/Group/Singleton and preserved time products; I/F/D/T ranks, all scalar Relation kinds in terminal table | `tests/test_analysis_display_r65.py`; V07/V10/V11 |
| R6.6 attribution.additive_difference/component_mix | S: Ibis explicit observation expansion and complete component/check preparation, registered local allocation; F: retained-state same local consumer | Per complete comparison scope and time correspondence, joint/hierarchy; I/F/D/T additive, I/F/D component_mix | `tests/test_analysis_attribution_r66.py`, `tests/test_analysis_attribution_runtime_r66.py`; V08/V09/V10/V11, AN11/AN12 |
| R6.7 installed continuations/consumer closure | Same selected methods/qualifications as producing candidate, no fresh backend qualification | Same wheel, produce/continue/recover A02/A06/A07/A08 and J1–J4, table+Parquet | `tests/test_analysis_recovery_r67.py`, `tests/installed_r6_journeys.py`; V10–V12 and cross-package matrix |

These filenames reserve responsibility; they do not exist yet and are not
runnable commands in R6.1. New/modified tests use the marivo-test-fixtures skill
at implementation time. Extend existing `tests/typing/analysis_dsl_public_contract.py`,
`tests/test_analysis_dsl_public_static.py`, `tests/test_analysis_help_resolution.py`,
`tests/test_unified_help.py` and CLI/docs checks for V12. Existing R5 numeric,
temporal and recovery files are L8/L9/shared-behavior regression owners.

Before implementation, route selection is explicit in the relevant registration;
Ibis preparation + registered Python is an intended source plan, never a failed
SQL retry. Python cannot open Catalog/connections or fetch ungoverned rows.
R6.2–R6.6 may select a qualified full-Ibis implementation only after proving the
same owner rules; each final route is recorded per cell with actual evidence.
No R6 backend-name branches, alternate executor or handwritten SQL is allowed.

### Independent acceptance obligations

| V | Required independent discriminator and owner |
| --- | --- |
| V01 | r62: same length/different composite keys, duplicate keys, double empty, wrong roles, independent equal target capture, full bucket maps |
| V02 | r62: four Cells separate from absence; selected/ranked missing row cannot become Metric empty; preserve Null empty finish and Undefined reason |
| V03 | r62: negative/zero baseline, min-int abs, overflow/nonfinite, recursive definitions, wrong one-to-one domain, no many-to-one or ordinary-ratio rollup |
| V04 | r63: multi-input dependencies, strict is_defined AND numeric failure versus legal staged selection; all children checked, L1 only on full defined domain |
| V05 | r63: three true plus Unknown decidable, three true plus Undefined hard fail, two true/one false/one Unknown undecidable; explicit three empty policies and missing opportunity error |
| V06 | r64: original reference identity survives selection/Top-K, missing/duplicate/zero-weight keys fail, zero-weight non-Defined values retained, tolerance boundary without renormalization, overlapping penetrations |
| V07 | r65: four ties, non-Defined tail, global prefix versus per-group filter, view correspondence, empty complete-key table, duplicate/foreign/mixed table early rejection |
| V08 | r66: independent raw-fact oracle, asymmetric two-side Top-K, real "Other" collision, joint/hierarchy independent checks, allocated side terms, exact high-magnitude and rounded Decimal threshold cases |
| V09 | r66: explicit Logical axis dependency, fixed missing axis refuses source replay, filtered scope revoked even if sum coincides, residual alone cannot prove partition |
| V10 | every package: construction zero reads; invalid static inputs no business reads/Run; source new evaluation; explicit shared-node counts; ordered roles/reference affect identity; failure no reroute |
| V11 | every package + r67: same frozen definitions/parts/K after source-free recovery, actual continuations, corruption/part swap/version/receipt checks before cache hit; resource and atomic-failure checks |
| V12 | every public package + r67: typed positive/negative shapes, exact exports and Help reachability/budgets, bounded repr/show/contract/repair, latest en/zh examples, installed public journeys and legacy deletion |

R6.1 imposes no actual Runtime pass count. R6.7 must complete these obligations,
not substitute private tests or the inherited R5 wheel. R7 receives
SubjectBinding/opportunity and domain overload boundaries; R8 gets named numeric
views and fixed continuation; R9 gets actual method/type/time/form/route cells;
R10 gets the installed reproducible journeys.

## Evidence and reproducibility

Local raw evidence is ignored under `docs/superpowers/specs/evidence/r61/`.
The version-control deliverables are this ledger, owning-spec sections, plan
status and acceptance entry; the essential inventory and decisions are above.
No ignored artifact is required to understand the freeze.

| Artifact | SHA256 / meaning |
| --- | --- |
| baseline.json | `2bd64a2ef428d7a53618e36c63f4ecbada492081522df338e175244728a70b05`; entry HEAD/status and original plan hash |
| consumer-snapshot.json | `274d18f422cabd4a8696635bda3cf6ef6a9b1cc37a46976ed96f673600bc5a09`; static source digests, ImportFrom symbols/direct alias calls and registration/dynamic candidates |

To reproduce the inventory on the recorded SHA, inspect these commands and
resolve each import/call by its defining symbol (do not infer Runtime reachability):

```sh
git rev-parse HEAD
git status --short
rg -n 'operators\.(compare|delta|attribute|attribution)|compiler\.(comparison|attribution|distinct_attribution|distribution_attribution)|materialization\.(comparison|attribution)' marivo tests
rg -n 'metric\.compare|delta\.attribute|register_delta|register_attribution|lazy_compare_runtime_worker|lazy_attribution_runtime_worker' marivo tests
rg -n 'attribution_summary_sql|attribution_source_summary|membership_integrity_sql' marivo tests
rg -n 'observation\.(population|metric|aggregation|coordinates|fold_contracts)' marivo tests
```

The snapshot was collected with `.venv/bin/python` AST parsing, recording each
ImportFrom's module/symbol/line, direct calls through its local alias, matching
registration text, and SHA256 per source file. It intentionally does not claim
complete dynamic call resolution. The public probe imports `marivo.analysis`
and tests membership in `mv.__all__` plus `hasattr` for the eight names recorded
above; it reads no business rows.

### R6.1 validation record

- Baseline/source inspection and AST snapshot: passed (exit 0), macOS/zsh,
  repository `.venv/bin/python`; 253 files/419 imports/538 direct alias calls.
- Public export probe: passed (exit 0); results recorded above, no new exports.
- Document validation: passed (exit 0), 8 Markdown deliverables, 21 new local
  links/anchors, 9 added tables, F01–F11/M01–M15/V01–V12 coverage; zero failures.
  Local report: `evidence/r61/document-validation.json`.
- `git diff --check`: passed (exit 0); untracked ledger additionally checked for
  whitespace. Final scope inspection confirms document-only changes and no
  staged changes. Product/test/skill/AGENTS files match the entry baseline.
- Product tests, Runtime, typecheck, site build and wheel acceptance: not run;
  this package changes contracts/inventory only. No runtime or public code,
  tests or site examples changed, so these are not R6.1 evidence claims.
- Both packaged workflow skills were read for applicability. They already defer
  signatures to Help, state-valid actions to contracts and repair to errors,
  preserve scope/reference/coverage and distinguish computed from causal claims.
  R6.1 adds no executable workflow; no skill edit is necessary in this package.
  R6.2–R6.6 reassess upon connecting each capability; required future edits need
  the explicit approval required by AGENTS.md. AGENTS.md remains unchanged.


## R6.2 implementation and qualification (2026-09-30)

Implementation entry: `panda`, HEAD `5b4d58da0d3ba369bb4ad8afef91d318e77fe540`.
Existing R6.1 owner edits are preserved. No commit, push, release-check, MinIO,
publication, AGENTS.md or packaged skill edit. The separately authored snapshot
DAG optimization handoff is preserved and is not part of this implementation.

### Connected public and Runtime contracts

- M01/F01–F03: `ExactKeys`, `UnionKeys`, `TimeChange`, `CohortContrast`,
  `PeriodChange`, `one_to_one`, ordinary `ratio`, and absolute/relative `compare`
  use the same typed graph path for Logical and Materialized receivers. Numeric
  Measure reads retain unit/time quantity definitions; nested ordered templates
  retain independent captures. Full typed keys, not row counts, govern pairing.
- `window_bucket()` and `WindowBucketAlignment` have one definition in
  `analysis._comparison`. The old contracts module consumes that definition;
  it does not define a compatibility constructor. Legacy tests import the public
  constructor. M03/M04/M10/M11 shared legacy code remains assigned to R7/R8 and
  final deletion to R6.7; none of those old executors gained source qualification.
- MissingCoordinate has its own presence/coordinate columns. Union keep retains
  present Cells and emits `Undefined(missing_side)` only for absent coordinates.
  Metric-empty admission requires complete raw original definitions and coverage;
  it invokes the registered concrete empty-state finish, including count zero,
  sum/mean Null and original-ratio zero-denominator Undefined. Row selection cannot
  manufacture empty contribution evidence. Existing consumed Cells remain strict.
- M13/F09: Ibis prepares sources and checks keys/coverage/buckets/numeric premises.
  Absolute difference retains the qualified Ibis route. Relative change and
  ordinary ratio select `ibis_python` before execution; fixed operands use
  `artifact_python`. There is no failed-route retry, handwritten production SQL,
  source/fixed mixing, family executor, or source access from local arithmetic.
- Difference state/part v2 and relative-change/relation-ratio v1 retain endpoints,
  correspondence, presence, error bounds, policies and bucket maps. Old Difference
  state rejects with a re-execute repair. Canonical bounded compressed definitions
  retain recursive captured nodes without following Artifact history. Fixed
  continuations validate exact receipts, versions and actual parts before execution.
- Float operand envelopes use retained magnitudes, including original sum/mean,
  ratio, weighted mean and linear components. A nonzero denominator interval crossing
  zero rejects. I/D/Duration use exact widened arithmetic, checked storage and one
  final quotient rounding; Decimal uses HALF_EVEN independent of caller context.
  Bind-project and affected original/row/transport implementations use contract 4.
- M14/M15: exports, typing, native Help routes/budgets, current contracts, CLI
  bootstrap regression and identical English/Chinese executable examples are
  synchronized. Public comparison never grants original rollup/share/attribution.
  Packaged workflows continue to delegate signatures to Help and capabilities to
  result contracts; no workflow edit is required or authorized.

### Public-path qualification cells

S-table and S-Parquet below are separately exercised; F is retained
`artifact_python`. These are observed cells, not whole-backend or installed-wheel
qualification. DuckDB native elapsed intervals have microsecond storage; the
Parquet duration carriers preserve s/ms/us/ns individually.

| Method / domain / time | Types and routes with public evidence | Evidence owner |
| --- | --- | --- |
| Absolute TimeChange, Entity/string key, UTC window | I, F, D(30,6), T(us): S-table/S-Parquet Ibis + F; T(s/ms/ns): S-Parquet Ibis + F | `test_public_numeric_difference_source_and_fixed` |
| Relative TimeChange, Entity, UTC window | Same I/F/D/T cells, source Ibis preparation + registered local finish, F; zero baseline reason retained | `test_relative_change_exact_finish_and_zero_baseline` |
| Ordinary exact ratio, Entity | Same I/F/D/T cells and source-local/F routes; zero denominator; D HALF_EVEN tie cases | `test_ordinary_ratio_numeric_families`, `test_public_decimal_ratio_rounds_once_half_even` |
| Ordinary ratio, numeric field / NoTime | I source Ibis preparation + registered local finish, F; declared Measure quantity/unit | `test_ordinary_ratio_over_untimed_numeric_read` |
| Exact full-key pairing, Entity/composite(string,int64), UTC | I S-table/S-Parquet + F; two-empty Exact/Union; equal row counts with wrong key images reject | `test_composite_keys_double_empty_and_wrong_key_images` |
| CohortContrast, Group/Singleton | I S-table/S-Parquet + F; common coordinates; count-zero and sum-Null Metric-empty; Union keep retains present Null/Undefined | `test_ordinary_ratio_and_cohort_singleton`, `test_cohort_group_metric_empty_preserves_metric_null_policy`, `test_union_keeps_present_nondefined_cell_separate_from_absence` |
| PeriodChange, Entity×Time | I/count S-table/S-Parquet + F; UTC, native DATE, aware local boundaries; original ordered grids; unequal/filtered buckets reject | `test_period_change_retains_original_buckets_and_rejects_renumbering` |
| Nested Difference | I S-table/S-Parquet + F; independent July captures and ordered recursive endpoints | `test_nested_difference_keeps_independent_captures` |
| Declared one-to-one ratio | I S-table/S-Parquet + F; full retained identity-key relationship, exact ordered nodes, many-to-one/reuse reject | `test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship` |
| Original mean/ratio/linear/weighted mean as comparison operands | F S-table + F; retained magnitude bounds, cancellation/unstable denominator rejection | `test_float_denominator_interval_and_mean_operand_envelope`, `test_comparison_of_float_original_expression_operands` |
| Widened integer / strict numeric failures | I negative/min-int baseline and overflow through public source/F; independent Fraction/Decimal and nonfinite/type/unit rules | `test_public_relative_negative_and_minimum_integer_baseline`, `test_analysis_comparison_r62.py` |

### Acceptance discriminators and remaining ownership

V01–V03 are exercised through the public comparison tests above and independent
rule/kernel counterexamples. Unknown has no newly introduced public producer:
its Cell representation and strict-consumption rejection remain core/exchange
contracts, not a new R6.3/R7 producer qualification. Many-to-one mappings,
unretained relationship keys/definitions and mismatched physical units reject.

V10 public evidence covers zero execution reads during composition, static
independent-target rejection, a shared endpoint staged once per Run, and fresh
source evaluation at each top-level execute. V11 public evidence produces table
and Parquet Artifacts, moves sources offline, disables DuckDB and Semantic in a
new process, then resumes Difference/relative/ratio and fixed composition with
same values, reasons, versions and capabilities. Correspondence corruption,
incomplete empty schemas and stale versions reject; the shared publication tests
own receipt/part swap, cache, resource and atomic-failure cases. V12 retains one
public Help owner with exact exports and typed signatures; CLI remains bootstrap
only and directs execution discovery to native Python Help.

Only the explicit cells above are granted. Arbitrary Decimal scales, native
non-us elapsed carriers, every cross-product of design/domain/type/time, remote
backends, site builds, installed-wheel and real-Agent journeys are not inferred
from generic registrations. R6.3–R6.7 remain separate work, including complete
legacy deletion and same-wheel acceptance. The existing R5 restriction on
Decimal/Duration current-row reducers is unchanged. Float temporal folds and
quantiles have no retained comparison error envelope and reject statically;
`test_float_fold_comparison_without_error_envelope_rejects_statically` guards
this qualification boundary. Their positive comparison cells remain unverified.

### Verification

- `make runtime-test TESTS='tests/test_analysis_comparison_runtime_r62.py tests/test_analysis_members_r52.py'`: **98 passed**. The final added float-fold admission/action-disclosure regression: **1 passed**. All 72 comparison Runtime cases plus 27 member cases have passing execution evidence; no skips.
- Focused comparison kernel/Help/bilingual tests: **60 passed**; numeric specialization regression: **18 passed**.
- `make check-agent`: **5476 passed, 5 skipped**, 409 source files typechecked; formatting/lint/import contracts and API documentation generation passed. The five skips are four SQLite fixtures without Decimal storage and one metadata channel-failure dispatcher case; skips grant no capability.
- Earlier failing runs remain recorded in the [acceptance entry](2026-09-26-marivo-full-refactor-acceptance.md#r62-corresponding-numeric-relations-2026-09-30), with the corresponding repaired runs. No release check, MinIO, remote-backend, wheel or real-Agent acceptance was run.
- `git diff --check`: passed. Branch and HEAD remain the implementation baseline; existing R6.1 edits and the separate snapshot-DAG handoff are preserved.

### Review repair follow-up

- Float RowStatistic operands now retain and propagate their error envelopes,
  including grouped state merge; stable comparisons pass while denominator
  intervals spanning zero reject on source and fixed paths. Malformed bounds
  reject through the shared numerical state validator.
- PeriodChange after `group_by(grid).rollup()` compares normalized time-coordinate
  templates and still consumes complete original bucket correspondence.
- Fixed endpoint parts are indexed once per complete key, avoiding per-row Arrow
  conversion and whole-part scans. A deterministic Runtime regression checks
  exactly one indexing pass per endpoint.
- Additional observed cells: float row sum/mean/min/max comparison envelopes on
  S-Parquet and F; original bucket rollup PeriodChange on S-table/S-Parquet and F.
  Unlisted combinations remain unqualified.
- Repair validation: **149 Runtime tests passed**, **38 focused tests passed**;
  `make check-agent`: **5481 passed, 5 skipped**, typing and API docs passed.
  Initial execution-key snapshot failures were repaired and rerun. Measured
  before/after hotspot timings and skip disposition are in the acceptance entry.


## Unified snapshot DAG follow-up (2026-09-30)

The R6.2 baseline is commit `377bc341f8`. All graph snapshots now use one
capture-identity DAG codec; retained endpoint definitions share its node table
without becoming executable dependencies. Continuation and graph execution-key
versions advance to v2; Store 7 and numeric state/part versions are unchanged.
Old snapshots require source re-execution, without migration or dual reading.
This does not qualify R6.3–R6.7 or installed-wheel/remote-backend journeys.

Measurements, reproduction commands and validation outcomes are recorded in
[the snapshot DAG evidence](2026-09-30-marivo-snapshot-dag-evidence.md).

Final gate: `make check-agent PYTEST_FLAGS='-q --tb=short --maxfail=5 -n 2'`
exited 0: lint/import contracts, typing (410 modules), default tests (5,514 passed,
5 skipped), and API docs. Comparison Runtime: 81 passed; supplemental publication
and relationship Runtime: 20 passed; strengthened nested cold recovery: 2 passed.
After the exact canonical-record equality guard, 5 affected Runtime journeys passed
again. These overlapping runs are not summed into a unique-case count. Earlier
fixture/golden-hash repairs and concurrency-related timeouts are distinguished in
the evidence record; timeout thresholds were not changed.


## R6.3 predicates and full-opportunity cohorts (2026-09-30)

Execution baseline: branch `panda`, clean HEAD
`29686cadbd51c4efc3c373c35842b58f04449284`. The earlier R6.2 and snapshot-DAG
changes were already committed at entry. This work adds predicate transport,
Subject mapping consumption and `domain.cohort@v1` to the existing graph/Runtime/
Store 7, without a parallel executor. Implementation and validation below are
checkout evidence, not installed-wheel or whole-R6 acceptance.

### Connected contracts and migration

- Bound numeric/category/Boolean/temporal/state predicates and authored composites
  retain every input edge. Numeric literals exclude bool; exact Decimal scale,
  Duration field units, scalar kind, temporal authority and quantity units are
  checked. Every ordinary leaf is consumed before composition. Sequential tag
  selection is intentionally different from an is_defined/comparison conjunction;
  L1 refuses to fuse that domain change.
- Input full-key images must match, or a retained ancestor/total projection proves
  restriction to the receiver. Missing consumed keys still fail. Fixed producer
  evidence closures preserve their original definition hashes independently of
  their source shape qualifications; they are metadata, never fixed source stages.
- `SubjectBinding` is producer-owned. Entity and Entity/time mappings are implicit;
  explicit `through` must match the retained mapping. Members project complete
  Subject identities without rereading or changing instance multiplicity.
- A complete Entity target times the retained finite grid defines opportunities,
  or there is one opportunity per Entity without a grid. Contribution-free cells
  remain opportunities. `cohort_decision` v1 stores each target's int64 t/u/f and
  accepted flag, including false targets. The primary must equal exactly the true
  decision keys. All targets must be decidable before exact publication.
- Public `all_of`/`any_of`/`not_` now have one bound-relation grammar and Help owner.
  Internal observation predicate functions still serve explicitly blocked R7/R8
  chains; the source-construction test imports those private functions explicitly,
  without claiming a second public grammar. R6.7 retains the final deletion gate.
  Native Help routes, budgets, export snapshots, typing rejection tests, API
  docstrings and current English/Chinese site examples are synchronized. The
  packaged analysis skill already delegates signatures to Help and continuations
  to contracts; it required no change and was not edited.

### Bounded qualification evidence

| Scope | Evidence and boundary |
| --- | --- |
| V04 strict multi-input where | `tests/test_analysis_predicates_r63.py`: DuckDB table/Parquet source and artifact_python fixed; numeric/category composition, cross-relation numeric field pairs and self field comparison, exact int64/float64/Decimal, native Duration us and Parquet s/ms/ns, first-tag versus conjunction/disjunction hard failure, fixed ancestor and sibling projection, cross-Session/mixed rejection before execution |
| Boolean, temporal, composite Subject keys | `tests/test_analysis_members_r52.py::test_r63_composite_subject_predicates`: table/Parquet source and fixed, full `(tenant,id)` identities and numeric/Boolean/timestamp field composition |
| V05 full opportunities | `tests/test_analysis_cohort_r63.py::test_full_entity_time_cohort`: table/Parquet, UTC timestamp, civil DATE and explicit America/New_York grid authority, full target×month image and independently counted activity; filtering opportunities before cohort rejects |
| Quantifiers and Unknown | Public NoTime any/at_least/all, nested negation and all-target decisions; controlled existing-Cell Ibis/local consumption exercises three true plus Unknown, decided false, two true/one false/one Unknown rejection, Undefined hard failure, false AND Unknown and true OR Unknown without hiding hard failure |
| Empty opportunities | All three closed policies and any/at_least empty rules have independent count-rule tests. R6 Entity/finite nonempty-grid producers do not manufacture per-Subject zero opportunities; Journey/Interval/Anchor producers and their empty opportunity images remain R7. Empty contributions are covered publicly as existing opportunities with zero count. No new Unknown producer is claimed. |
| V10/V11 and K | New processes restore fixed selection and cohort after the database is renamed offline; both DuckDB connection and Semantic load are forbidden. They actually repeat where/cohort and members continuations. Cohort counts/selected keys are cross-validated; missing/corrupt decision files and foreign receipt/state version revoke contract/recovery. Shared v7 schema, receipt and publication checks remain active. |

Source predicate and quantifier expressions use issued Ibis reads. A dedicated
truth column precedes count aggregation, preserving nested Boolean truth and
null detection through backend compilation. Fixed inputs use the registered
artifact_python consumer of the same rule. The method implementation contract
for cohort is 1; transported numerical state keeps the existing contract 4.
This grants no R5 Decimal/Duration row reduction, SQLite/remote R6 method,
installed-wheel, real-Agent or release qualification.

### Validation

Final gates on the implementation above all exited 0:

- `make check-agent`: formatting/lint/import contracts, typing of **413** source
  files, **5529 passed / 5 skipped**, and API documentation generation. Four
  existing SQLite fixtures lack Decimal storage declarations; the existing
  metadata-owner case delegates a channel failure to dispatcher fallback. Those
  five skips are not Runtime or backend qualification.
- `make runtime-test TESTS='tests/test_analysis_predicates_r63.py tests/test_analysis_cohort_r63.py tests/test_analysis_members_r52.py tests/test_analysis_temporal_r55.py::test_public_grid_observation_and_retained_axis tests/test_analysis_comparison_runtime_r62.py::test_composite_keys_double_empty_and_wrong_key_images tests/test_analysis_comparison_runtime_r62.py::test_ordinary_ratio_over_untimed_numeric_read'`:
  **59 passed / 13 deselected**, no skips or failures; deselected cases are the
  ordinary tests excluded by the Runtime marker, covered by the broad gate.
- The final added cross-relation numeric field-pair assertions passed with
  `make runtime-test TESTS='tests/test_analysis_predicates_r63.py::test_public_multi_input_source_and_fixed'`
  (**2 passed**, table/Parquet source and fixed). This is supplemental to the
  59-case gate, with no product-code change after that gate.
- `npm --prefix site run build`: API prebuild, Astro check/build and install-script
  verification passed. The checked-in bilingual R6.3 examples are included.
- `git diff --check`: passed.

Earlier failed iterations exposed export/Help/type/import drift, strict-consumption legacy test
expectations, the fixed multi-input lowering arity, nested-negation null counting,
and retained inclusion evidence; each was repaired with a focused regression.
No failures are counted as passed. Default-test skips remain separate from
Runtime acceptance. R6.4–R6.7, R9 and R10 remain outside this task. No commit,
push, publication, release-check, MinIO, AGENTS.md or packaged skill edit.

### R6.3 review repairs

The three confirmed uncommitted-diff findings are repaired within R6.3:

- Decimal literals have an explicit tagged wire value, preserving their type,
  scale and signed zero without reinterpreting numeric-looking category strings.
  Independent codec tests cover exact scalar/temporal types and malformed tagged
  values. Table/Parquet source and fixed category selections persist, restore and
  continue; existing precise numeric tests also repeat restored Decimal selection.
- Fixed cohort selects its independently ordered Subject part by complete keys.
  The consumer regression first verifies valid exchanges with independently
  reordered primary or Subject rows, then checks the exact composite `(tenant,id)`
  image and Subject payload for a partially accepted cohort.
- Selected logical and materialized numeric/category/Boolean/temporal/Difference
  relations disclose further `where` actions. Independent Runtime assertions
  resolve the action's native Help target and actually execute the continuation.
  Current English/Chinese examples show `defined.contract().show()` before the
  second selection.

The original failures were reproduced before repair. This adds no method,
backend, wheel, release or later-phase qualification.

Repair validation, all exit 0:

- `make check-agent`: **5544 passed / 5 existing skipped**, typing of **413**
  source files, lint/import contracts and API documentation generation.
- `make runtime-test-agent TESTS='tests/test_analysis_predicates_r63.py tests/test_analysis_cohort_r63.py tests/test_analysis_members_r52.py'`:
  **64 passed**, covering the affected public source/fixed continuations and
  existing source-offline fresh-process recovery. No skips or failures.
- `npm --prefix site run build`: API prebuild, Astro check/build and bilingual
  install-script verification passed.
- `git diff --check`: passed. Changes remain uncommitted in the same worktree.

## R6.4 fixed references and standardization (2026-10-01)

Baseline: clean `panda`, HEAD `85107a9a49cb6939505b7341167cfcf7df3ed7b0`.
The accepted R6.4 scope additionally admits original Metric/runtime Metric
sum/count/linear receivers, with public int64 positives. This package connects
`NumericRelation.share_of`, `AnalysisDomain.penetration_in`,
`mv.reference_weights` and `NumericRelation.standardize` to the existing logical
and materialized NumericRelation family. `ReferenceWeights` is an immutable,
factory-only input with ordered frozen values/strata/Entity dependencies and
bounded repr/show; it has no independent execute operation.

### Actual qualified cells

All public positives below execute both source and fixed consumers; table and
Parquet are separate parameterized cases unless explicitly limited to table.
Numeric observations use the matching frozen August 2026 scope and UTC-us event
axis; penetration uses untimed complete Entity domains. This is not a grant for
every registered time/type/backend cross-product.

| Method / receiver | Actual input/output qualification | Independent public evidence |
| --- | --- | --- |
| reference.share / original sum | I=int64 and F=binary64 to binary64; D=Decimal(30,6) to Decimal(38,6); T=microsecond ticks from native INTERVAL or Parquet Duration to binary64 | `test_public_share_numeric_matrix`; Fraction/Decimal raw-amount oracles; source/fixed, table/Parquet |
| reference.share / count and linear | int64 count; original linear over I/F/D sums; complete support and explicit original Singleton rollup | `test_public_references_source_and_fixed`, `test_original_metric_standardization_matrix`; independent raw count/region totals, source/fixed, table/Parquet |
| reference.penetration | Complete Entity identities, including composite `(tenant,customer_id)`; checked intersection/reference counts to binary64; overlaps and empty reference | `test_composite_penetration_overlap_and_empty`; independent set/cardinality oracle, source/fixed, table/Parquet |
| reference.standardize / original sum/count/linear | int64 receivers with I/F weights to binary64; binary64 sum/linear with F weights to binary64; Decimal(30,6) sum/linear with D weights to Decimal(38,6) | `test_public_references_source_and_fixed`, `test_public_exact_integer_reference_weights`, `test_original_metric_standardization_matrix`; source/fixed, table/Parquet |
| reference.standardize / original mean/weighted_mean/ratio | I/F original inputs produce binary64 stratum values with I/F weights; D original inputs and D weights produce Decimal(38,6); zero-weight Null mean and Undefined ratio strata retained | `test_original_metric_standardization_matrix`, `test_public_exact_integer_reference_weights`; raw-fact Fraction/Decimal oracles, source/fixed, table/Parquet |
| Independent source origins and statistical Entity | Extra order-line source in weights; original ratio with order-line numerator uses its order-count denominator Entity; linear with inconsistent contribution Entities rejects before business reads | `test_reference_preparation_captures_independent_source_origins`; source/fixed positive ratio and source zero-read rejection; native table only |

Mean uses the sample contribution Entity; weighted mean uses the paired
contribution Entity; sum/count use their contribution Entity. Original ratio
uses its denominator component Entity, and linear requires equality across all
terms. Ordinary relation ratio, Difference, quantile and field reads do not gain
standardization, share or original-state rollup permission. Standardization
preserves measurement units and discloses a weighted stratum value, including
stratum totals/counts, without claiming an actual population value.

### Consumption, recovery and disclosure

| Obligation | Result and owner |
| --- | --- |
| Zero-read construction and known early rejection | **Passed**: cross-Session, mixed mode, non-Singleton share, wrong/repeated/reversed/unretained axes, statistical Entity and time mismatch, ordinary ratio and unproved linear reject before business reads; no new Run. `test_static_reference_rejections_are_zero_read` and the independent-origin test. |
| Complete immutable share / penetration reference | **Passed**: signed shares, zero denominator, inclusion overflow, overlapping composite sets and empty reference; where keeps original reference identity/denominator and all independent parts. A fixed selected original can consume its exact retained full reference Artifact. |
| Exact represented arithmetic | **Passed**: Fraction widened products/sums and one finish; exact I/D sum=1; F represented-value exact sum within 1e-12 without normalization; order changes, boundary/tolerance rejection, nonfinite/negative/wrong weights, tiny positive weights, zero-weight non-Defined Cells and malformed Defined rejection. Sixteen pure tests supplement the public matrix. |
| Complete strata consumption | **Passed**: missing/duplicate/wrong keys, weight/value mismatch and invalid error state reject. Zero-weight stratum values and their original tags remain persisted. `test_invalid_public_standardization_rejects_without_normalization`, exchange-corruption and pure kernel cases. |
| One graph / selected route | **Passed**: registered reference v1 methods use source `ibis_python` preparation/controlled exchange and fixed `artifact_python`; explicit shared reference node realizes once per execution, changed source facts are freshly evaluated, fixed captures retain original inputs, failed source execution does not switch routes. |
| Store 7 and cold fixed continuation | **Passed**: `share`, `penetration`, `standardized` state v1; independent `fixed_reference`, `reference_proof`, `stratum_values` and standardization `strata` receipts retain their own complete keys. In a fresh process with the table/Parquet source removed, Semantic load forbidden and DuckDB import/connection disabled, all three saved families actually execute where continuations and recover the new Artifact. |
| Integrity and publication atomicity | **Passed**: deletion and alteration of every retained part, incompatible role versions/keys, bad receipt content and inconsistent arithmetic/error state reject before recovery/cache use. Faults after part writes, file publication and receipt verification clean the failed publication while preserving prior successful Artifacts. No hardware or remote-storage atomicity claim. |
| Native disclosure | **Passed**: exports/typing/Help, bounded progressive reference/summary routes, factory acquisition/consumption, dynamic actions and repair targets. Duration receivers do not offer standardization; additive linear share permission is independent of statistical-Entity standardization permission. Static reference identity/interpretation survives where. Verified result cards independently disclose complete/partial current partition, nonnegative range, intersection/reference counts, represented weight-sum deviation and arithmetic error bound. API and current English/Chinese examples are aligned. |

### Validation and remaining boundaries

Final package checks, all exit 0:

- `make runtime-test TESTS='tests/test_analysis_references_r64.py'`:
  **33 passed**, no failures or skips. The same file's **16 pure tests** are
  included in the broad default gate.
- After making incompatible reference-state version repairs point to the
  reference input rather than a comparison, the cold recovery/corruption cases
  passed again (**2 passed**) with explicit v2-state rejection assertions.
- Necessary shared-state gate:
  `make runtime-test TESTS='tests/test_analysis_references_r64.py tests/test_analysis_comparison_runtime_r62.py tests/test_analysis_cohort_r63.py tests/test_analysis_coordinates_r54.py tests/test_analysis_numeric_r56.py tests/test_analysis_graph_publication_r44.py'`:
  **375 passed**, no failures or skips, at the earlier 29-case R6.4 matrix.
  Subsequent R6.4 support/origin/disclosure assertions and exact integer weights
  are included in the final 33-case run above.
- `make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing/analysis_dsl_public_contract.py'`:
  **269 source files passed**; focused lint/import checks passed.
- `make check-agent`: **5560 passed / 5 skipped**, typing of **415** source
  files, formatting, lint/import contracts and API documentation generation.
  Four existing SQLite fixtures lack Decimal storage declarations; the existing
  metadata-owner case delegates a channel failure to dispatcher fallback. These
  five skips confer no Runtime or backend qualification.
- `npm --prefix site run build`: API prebuild, Astro check/build and bilingual
  install-script verification passed.
- `git diff --check`: passed.

Earlier failed iterations exposed independent-key row alignment, normalized
frozen classification identity, retained fixed support and independent source
origin binding, plus Help/type/export/example drift and test fixture/assertion
errors. They were repaired and rerun; failed runs are not counted as passes.

**Unverified / deferred**: R6.5 ranking/Top-K reference invariance and terminal
table views, R6.6 allocation, R6.7 same-wheel continuation/recovery, untested
time/type combinations (including non-us Duration share), SQLite/remote R6
methods, full six-backend and real-Agent journeys. Top-K is a handoff, not a
passed reference test. No release-check, MinIO, commit, push, publication,
AGENTS.md or packaged-skill edit was performed.

### R6.4 review repair: zero-weight error propagation (2026-10-01)

Confirmed and repaired: a Defined stratum with represented weight zero still
propagates `abs(value) * weight_error + value_error * weight_error`. Only
non-Defined zero-weight strata skip numerical consumption. The independent
Fraction regression failed before the repair and now passes; it also pins the
Null/Undefined/Unknown exemptions. The owning numerical policy is clarified.

Validation: focused pure tests **20 passed**; R6.4 Runtime **33 passed**;
`make check-agent` **5564 passed / 5 existing skipped**, including typing,
lint/import checks and API docs; `git diff --check` passed. No commit or push.

## R6.5 ranking and terminal display completed (2026-10-01)

Implementation baseline: `panda`, HEAD
`ea406d51ef26a1a8986d2c95d0c35c174007fc39` (contains R6.4). The working tree
was clean at implementation entry; all preceding committed work is preserved.
No commit, push, release, AGENTS.md or packaged-skill edit is included.

### Connected contracts and state

- `rank(order, ties, partition_by=())` produces paired Logical/Materialized
  RankingResult variants with fixed `values` and `ranks` numeric views.
  Ordinal/dense/min/max use exact represented values and canonical complete
  typed keys. Partition tuples precede Defined rank blocks and the instance-key
  ordered non-Defined tail. View export preserves that order.
- Ranking `where`/global `limit(1..100000)` restrict both views through the same
  selection map and retain original ranks, domain, partitions, order and fixed
  reference. Two-step `is_defined` then selected-rank `lte(k)` gives per-group
  Top-K, including ties beyond K. Numeric-view selection keeps the numeric family;
  values retains its actual sufficient parts and quantity, ranks never acquires
  original Metric state. View access does not allocate a Run.
- `mv.table` produces terminal LogicalTable/MaterializedTable variants, requiring
  equal complete keys, compatible time meaning, one Session and one source/fixed
  mode. It retains authored column order, concrete types, per-column Cell reason
  policies and exact ordered input/view bindings. Duplicate/missing keys reject;
  paired empty domains and empty Singletons are valid. Labels cannot collide
  with exported keys. No contract/K, dynamic columns or analysis feedback exists.
- Compact pandas export contains keys once and authored values only, using exact
  Arrow-backed pandas dtypes. Null/Undefined/Unknown become missing and lose
  their tags/reasons in this export. Saved Cells and bounded `show()` retain that
  information. Mutating the returned DataFrame cannot change the Artifact.
- `display.rank/table@v1` use the shared method registry, GraphPlan, Runtime,
  execution keys, DAG snapshots, receipts and atomic publication. Source Ibis
  prepares/checks keys and partitions for the registered local finish. Fixed
  execution uses checked Arrow/Parquet through the controlled pandas carrier.
  Store remains generation 7; `ranking/table` state contract v1 and their parts
  verify original scope, selected Cells, order and bindings before publication
  or fixed reuse. One explicit rank and its source realization are shared when
  table consumes both views. Independent captures remain distinct; no fallback.

### Actual qualification and validation

| Cell / check | Result | Boundary |
| --- | --- | --- |
| Rank I/F/D/T, table/Parquet source and fixed; ties × directions | Passed | Duration us; Decimal fixture `(30,6)` and exact widened observation output; no epsilon float ties |
| Entity composite keys, Group, Singleton; UTC monthly Entity×Time | Passed | Null category partition first; other grids/timezones remain unverified |
| Numeric/Category/Boolean/Temporal terminal columns | Passed | Temporal UTC timestamp; full keys, author order, precision, missing values and isolation |
| Four Cell labels and reasons | Passed | All four on controlled fixed exchange; public source Defined/Null/Undefined; no new Unknown producer |
| Global prefix vs partition Top-K, tied overflow, no rerank, share identity/denominator | Passed | Actual sufficient parts govern view K and rollup |
| Shared realization, new source evaluation, fixed retention, failure refusal | Passed | Exact explicit capture identities; no automatic merge or retry |
| Fresh-process offline ranking selection and table recovery | Passed | Native table and Parquet; source renamed away and Semantic/DuckDB disabled |
| Missing/corrupt parts, part/state versions, ranks/order/bindings; receipt reuse; publication faults | Passed | Reject before publication or fixed reuse; prior Artifacts survive failed publication |
| Native Help, exports, typing positive/negative, reachability/budgets, API and bilingual examples | Passed | Existing Help/contract skill delegation remains applicable |
| Installed wheel, remote backends, R6.6 attribution | Unverified / deferred | R6.7, R9 and R6.6 respectively; no qualification inferred |

Validation commands and outcomes:

- Targeted default disclosure/typing/registration/export/display tests:
  **108 passed** at the earlier focused set; final full default gate includes
  the subsequent independent disclosure assertions and all nine pure display cases.
- `make runtime-test-agent TESTS='tests/test_analysis_display_r65.py'`:
  **45 passed**, no failures or skips, after controlled pandas fixed transport.
- R6.2/R6.3/R6.4/common publication Runtime regression with the earlier R6.5
  matrix: **184 passed**, no failures/skips. The exact files were
  `test_analysis_display_r65.py`, `test_analysis_references_r64.py`,
  `test_analysis_comparison_runtime_r62.py`, `test_analysis_cohort_r63.py`, and
  `test_analysis_graph_publication_r44.py`.
- Display + latest display example + R6.3 predicate Runtime set:
  **63 passed / 27 non-Runtime deselected**, no failures/skips.
  Both bilingual copies have identical executable code; the example executes
  and restores its actual terminal Artifact on native table and Parquet.
- `make check-agent`: **5573 passed / 5 existing skipped**, **417** typed files,
  formatting/lint/import contracts and API documentation passed. Four SQLite
  expression fixtures lack declared Decimal storage; the existing metadata
  channel-failure fixture delegates to dispatcher fallback. Those skips grant
  no R6 qualification.
- `npm --prefix site run build`: Sphinx API prebuild, Astro check/build
  (**321 pages**) and standard/Chinese install script verification passed.
- Final strengthened raw-data partition oracle and missing-partition-key checks:
  **18 passed** (four ties, both directions, table/Parquet, composite keys),
  after the 45-case run; focused formatting/lint and diff whitespace checks pass.
- `git diff --check`: passed.

Failed development iterations exposed missing execution-key part encoding,
source-local check handoff, ambiguous implementation identities, metadata-only
frozen endpoints, reference/ranking state layout separation, UTC column typing,
empty Singleton transport and disclosure/doc drift. These were repaired and
rerun. Fixture timestamp syntax and result-family assertions were also corrected.
The first broad default run had two stale disclosure/example assertions; the
first site/API run had five Sphinx imports under the wrong currentmodule. Final
checks above pass; failed runs are not counted as successful evidence.

Untested Duration s/ms/ns, additional Decimal precisions/scales, temporal Date
columns and other time shapes remain explicitly unverified. No full Runtime
release-check, MinIO, same-wheel installation, publication or real-Agent test
was run. R6.6 and R6.7 are separate remaining phases.

## R6.6 bounded implementation and qualification (2026-10-01)

### Baseline and accepted scope

Branch `panda`, HEAD `ea406d51ef26a1a8986d2c95d0c35c174007fc39`.
The working tree already contained uncommitted R6.5 implementation, tests and
documentation, including both graph_display modules and test_analysis_display_r65.py.
Those changes were preserved and extended only where allocation consumers needed
them. The entry tracked diff was captured with SHA256
`a61ceac8957bd0a439aada346818dbfeecf8664df6cd9ab0f872e6c22780e5f2`.
This task implements R6.6 only; R6.7 legacy-family removal and installed-wheel
closure remain separate. No commit, push, release, release-check, MinIO,
AGENTS.md or packaged-skill edit occurred.

The existing packaged analysis skill still delegates static navigation to Help,
current mechanical continuations to contract and repair to structured errors.
Allocation uses those same owners; no workflow or judgment boundary changed, so
no skill edit was necessary. Tests use the repository fixture skill and shared
DslCaseFactory/raw-fact rows rather than a second semantic fixture family.

### Connected methods, parts and consumers

- `Difference.attribute(axes=..., mode="joint"|"hierarchy", top_k=None|1..1000)`
  selects additive_difference or component_mix from original state. Closed
  LogicalAttributionResult/MaterializedAttributionResult expose three same-key
  NumericRelation views and strict where. Invalid axes, bool/invalid Top-K,
  relative/nested/selected changes, unsupported original methods, illegal
  folds/overlap and missing fixed axes reject without an alternate execution route.
- Logical missing-axis expansion adds a frozen observation dependency to the
  same graph. Original reduction retains allocation_state without restoring
  removed public group axes. Fixed execution uses retained parts only.
- Common union-basis Top-K uses exact scores and typed tie-breaking, including
  every mapped parent and Other. Hierarchy reuses that mapping and checks each
  resolution separately. Real string "Other" and remainder mask remain distinct.
  Contradictory local zero basis is checked before Top-K; no balancing residual.
- Store 7/DAG/registry/Runtime carry six ordered v1 roles: current_endpoint,
  baseline_endpoint, basis, allocation, reconciliation and selection_scope.
  Original-scope parts, complete allocation parts and selected keys have separate
  declared keys. Independent validation reproduces endpoints, components,
  target, mapping, side values, bounds, each resolution and selected primary.
  Filtering always revokes current completeness, including an all-row filter.
- Floating allocation retains current/baseline/contribution R5 bounds from
  original magnitudes and denominator intervals. Source/fixed numeric views
  consume their own bound in explicit ratio arithmetic. Ranking retains allocation
  evidence on its values view, orders typed Other after ordinary coordinates,
  and preserves its own result family and complete-key validation.
- UTC monthly PeriodChange and retained day-to-month partitions preserve the
  authored time axis. Inherited pairing checks use the originating comparison's
  frozen map; coarsened quantity templates compare grain/timezone authority,
  rather than concrete date labels.
- Help, exports, positive typing, independent disclosure snapshots and latest
  English/Chinese executable examples are aligned. AN11's unused
  attribution_summary_sql module is deleted; AN12's private legacy hook remains
  rejection-only with no SQL statement call. Issued SQL, direct Ibis compilation
  and native driver submissions match exactly on the replacement route.

### Actual qualification

| Cell / check | Result | Boundary |
| --- | --- | --- |
| Additive sum/linear I/F/D; count I; component_mix mean/weighted_mean/original ratio I/F/D | Passed | DuckDB table/local Parquet, retained source, retained fixed and explicit logical axis expansion; Decimal fixture (30,6), widened outputs |
| Additive Duration sum/linear | Passed | Native us and Parquet ns plus fixed; pure ns exact carrier; s/ms Runtime remain unverified |
| Original scope and time | Passed | Singleton/member groups, UTC monthly PeriodChange and whole-cell day-to-month retained allocation; additional grids/timezones unverified |
| Common Top-K/Other, two-axis joint/hierarchy, side terms and each resolution | Passed | Independent raw-fact expectations; asymmetric sides, real "Other", mapped parent and mask |
| Numeric views, selection, ratio bounds, ranked ties and terminal table | Passed | Same keys, exact float oracle bound, unconditional completeness revocation, typed remainder last; no original Metric rollup permission |
| New source evaluation and fixed retention | Passed | Source facts changed on both table/Parquet; fixed reuse retains original basis and Artifact identity |
| Missing/corrupt parts, schemas/versions, independent key order | Passed | Common exchange and descriptor checks reject; complete allocation is not inferred from residual |
| Produce/continue/recover in separate source-offline processes | Passed | Native/Parquet count Difference; sources renamed away and Semantic/DuckDB/SourceSession disabled; actual attribute/where/view/table continuations |
| Numerical negatives and failed publication | Passed | Large exact carriers, checked overflow/nonfinite, contradictory zero basis, denominator interval spanning zero; Decimal high precision and 3000 small partitions, no extra successful publication on excess residual |
| Six-part publication fault cleanup | Passed | Three common writer boundaries on both source/fixed; only failed-run files removed, resource journal empty, prior Difference unchanged and clean retry succeeds |
| Native submission/SQL closure, shared realization, Help/typing/docs | Passed | Ibis-issued reads match actual driver strings; three views share one allocation; no legacy summary SQL route |
| True-null original contribution coordinates | Rejected under existing R5 contract | Nullable output slots belong only to Other/inactive hierarchy positions; no identity-null relaxation |
| Installed wheel, legacy family removal, remote backends, real Agents | Unverified / deferred | R6.7, R9 and R10 retain their respective ownership |

### Validation and repaired iterations

- `make runtime-test TESTS='tests/test_analysis_attribution_runtime_r66.py'`:
  **54 passed**, no failures/skips, including source/fixed/type matrix, fresh
  source evaluation, Decimal failed publication and float numeric-view arithmetic.
  The final two added typed-Other ranking/disclosure cases passed separately with
  `-k numeric_view_rank`: **2 passed**. This is **56** qualified R6.6 Runtime cases;
  the two-case run is not represented as part of the earlier 54-case command.
  Three additional publication fault cases (`-k publication_cleans`) cover both
  source and fixed at part-written/files-published/receipts-verified boundaries:
  **3 passed**. Total qualified R6.6 Runtime cases: **59** across these explicit
  runs, rather than one claimed 59-case invocation.
- The affected comparison/coordinate/display/temporal/example Runtime set passed
  **303 cases**, with no failures/skips. Exact files: attribution_runtime_r66,
  comparison_runtime_r62, coordinates_r54, display_r65, temporal_r55 and
  cutover_documentation_examples. This preceded the final view-bound/rank additions;
  their final focused checks are recorded above and below.
- `make test TESTS='tests/test_analysis_attribution_r66.py'`: **10 passed**,
  independent exact/Fraction/Decimal numerical and threshold checks.
- Latest display and all bilingual examples are checked with
  `make runtime-test TESTS='tests/test_analysis_display_r65.py tests/test_cutover_documentation_examples.py'`:
  **60 passed**, no failures/skips, after the final Other/ranking transport changes.
- Final full default/lint/type/API gate is `make check-agent`; site gate is
  `npm --prefix site run build`: **5583 passed / 5 existing skipped**, **419** typed
  files, lint/import contracts and API documentation passed; site built **321 pages**
  and verified standard/Chinese install outputs. Focused final lint and
  `git diff --check` passed. Existing default skips grant no R6 Runtime qualification.

Failed iterations exposed incomplete source-local part binding, independent part
ordering and nullable Other keys, hidden coordinate-role lookup, duration tick
restoration, inherited period maps/coarsening templates, lost float view bounds,
and allocation verification with additional ranking parts. Each was repaired and
rerun on its affected public path. Test assumptions about generic exported key
names and last_run_ref on failed Runs were corrected against current contracts;
failed Runs are recorded and have no successful output Artifact. Failed commands
are not counted as qualification. Raw command logs were captured under
`/tmp/marivo-r66-*.log`; the checked-in tests and commands above are the durable
reproduction sources.
