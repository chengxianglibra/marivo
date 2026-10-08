# Production-code reduction: P1 result disclosure convergence

Date: 2026-10-07 (Asia/Shanghai)

Status: P1-C01 and P1-C02 implemented; package acceptance passed for affected
daily/Runtime, typing, lint, public-contract, scope and accounting checks, plus
the full daily `make check-agent` gate.
Execution-pilot performance sampling, G1/G2 and the under-100k target remain
unverified.

The user authorized this first package from the
[implementation plan](../plans/2026-10-07-production-code-under-100k-implementation-plan.md).
The [G0 record](2026-10-07-production-code-g0-baseline-and-pilots.md) remains
the original baseline and candidate definition; its evidence is not rewritten.

## Baseline and accounting

This package starts from clean `panda@66856dd9e3e5c722d2f4590dda9140c37388852e`.
The earlier planning HEAD and dirty worktree had advanced before implementation.
Both candidate files still matched their G0 hashes. The G0 counter froze a new
[before snapshot](evidence/production-code-p1/before/manifest.json), source archive,
index and status before any production edit. Its count is 323 production Python
files and 131,886 nonempty lines. The three-line difference from G0 belongs to
other work and receives no P1 reduction credit.

| Candidate | Production owner | Before | After | Net reduction |
| --- | --- | ---: | ---: | ---: |
| P1-C01 | `semantic/catalog.py` | 6,447 | 6,349 | 98 |
| P1-C02 | `analysis/public_dsl.py` | 6,937 | 6,827 | 110 |
| Total | Two mutually exclusive files | 13,384 | 13,176 | **208** |

The verified current production total is **131,678**. Final totals and auxiliary
inventory are bound by the [after snapshot](evidence/production-code-p1/after/manifest.json)
and [accounting record](evidence/production-code-p1/accounting.json).
The after archive precedes final status-only edits to this record and the local
plan; production and test bytes are unchanged.
At this total, reaching 99,999 still requires 31,679 fewer lines; reaching the
98,000 planning target requires 33,678. This package does not establish that the
remaining candidates can cover either gap.

The counter remains `nonempty-physical-v1`: comments, docstrings, imports and
declarations count. New forwarding and helper code is already deducted. There
are no new production files, resources, generators or imports from tests/tools.
Required public docstrings and normal formatting remain intact.

## Fact owners and consumers

**P1-C01.** Existing `_DetailsBase._detail_sections()` now forwards the common
fields to the unchanged `_common_detail_sections()` renderer. Each concrete
Details implementation obtains a fresh common section list through `super()`
and appends its original specialized sections. Dataclass fields, constructors,
frozen mappings and public render/show protocols are unchanged.

Consumers: Datasource, Domain, Entity, Dimension, Measure, TimeDimension,
SimpleMetric, DerivedMetric, Relationship, Event, StateModel, BusinessOrder,
PeriodCalendar, TemporalSet and WorkSchedule Details. CalendarLevelDetails and
page results are outside this candidate.

**P1-C02.** Private `_Value._summarize_rows(method: RowMethod)` owns the unchanged
closed-method rejection and exact `LogicalStatisticRelation` construction.
Public wrappers retain their own signatures, docstrings and concrete return
annotations. The helper retains `inputs=(self,)`, the original node/runtime,
Session ownership and source/fixed classification.

Consumers: Logical/Materialized Numeric, RolledNumeric, Ratio, RolledRatio,
Difference and SelectedDifference Relations; MaterializedCoefficientRelation;
Logical/Materialized CoefficientSelection Relations. The complete symbol list is
retained in the [owner audit](evidence/production-code-p1/owner-audit.json).

Grouped coordinate handling, original-contribution continuation, CountMethod
variants and LogicalCoefficientRelation's different behavior remain unchanged.
The existing capability registry and `CATALOG_MEMBER_CONTRACTS` keep their owners;
this delivery neither introduces a second inventory nor credits prior registry
consolidation as a new saving.

## Verification

The first stage changed Datasource/Entity Details and Logical/Materialized Numeric
summarize only. It reduced production code by 13 lines and passed 162 daily tests,
one int64 Runtime test, both production-module typechecks and public-contract
comparison before extension. See [pilot.json](evidence/production-code-p1/pilot.json)
and its retained patch. An initial new field-order assertion matched a Ref value
as a field label; it was corrected to match line prefixes, and the scope passed.

| Check | Result and boundary |
| --- | --- |
| Affected daily: `make test TESTS='tests/semantic tests/surface tests/analysis/graph/test_disclosure.py tests/analysis/numeric/test_analysis_numeric.py'` | **1,657 passed**, 30.96 seconds; [log](evidence/production-code-p1/affected-daily.log) |
| G0 N1 DuckDB native original mean / fixed / independent cold recovery | **Passed**, included in the five Runtime tests below |
| Current-row count/count-defined and early invalid-method repair for int64, float64, Decimal and Duration, on Logical and Materialized Numeric | **Passed**; Runtime total **5 passed**, 26.15 seconds; [log](evidence/production-code-p1/runtime.log) |
| Targeted typecheck: both production modules and the existing public DSL typing contract | **Passed**, three source files; [log](evidence/production-code-p1/typing.log) |
| Targeted format, Ruff and import contracts | **Passed**, four touched Python files; [log](evidence/production-code-p1/lint.log) |
| Public contract comparison | **Passed**: exact exports, 15 Details constructors/fields/annotations/docstrings/Help, 15 summarize signatures/annotations/docstrings/Help, and progressive Help roots match [before](evidence/production-code-p1/before-contracts.json) and [after](evidence/production-code-p1/after-contracts.json) |
| AST owner/scope audit | **Passed**: every specialized Details tail and the common renderer are unchanged; each old statistic body equals the helper; all other functions are unchanged |
| `make check-agent` | **Passed**: 4,930 daily tests passed, one skipped, 46.17 seconds; 329 typing source files; formatting, Ruff, import contracts and API docs passed; [log](evidence/production-code-p1/check-agent.log) |
| Independent snapshot/archive recomputation and auxiliary-cost audit | **Passed**: every archived file matches its manifest bytes/hash; production totals independently recompute; only the two owned production files changed; package resources/stubs are unchanged |

The strengthened existing Details tests check public field order, exactly-once
sections, empty navigation, defensive mapping copies, forbidden mutation and
repeatable rendering. Existing tests retain secret hiding, bounded output,
specialized fields, native Help reachability/budgets, concrete typing and current
English/Chinese documentation examples. The current-row Runtime test deliberately
crosses the static typing boundary and forbids graph dispatch while asserting the
exact expected/received/location/repair fields. Narrow type-ignore annotations
exist only for these intentionally forbidden calls and mutations.

No public disclosure content changed, so Help, examples and bilingual API docs
needed no content edits. Their existing checks remain part of acceptance.
Elapsed test durations above are validation durations, not operation-performance
samples or evidence of an end-to-end speedup. This first package does not collect
the third package's performance baseline or claim G1 acceptance.

## Delivery boundary

Production edits are confined to the two candidate files; two existing test
modules contain the independent regression assertions. This English record and
the ignored implementation plan document the delivery. `AGENTS.md`, packaged
skills, other packages, release work and G0 evidence are unchanged. The
implementation pass did not commit or push; this delivery commit was requested
separately. Full Runtime/backend qualification and installed-package validation
were not run for this internal extraction.
