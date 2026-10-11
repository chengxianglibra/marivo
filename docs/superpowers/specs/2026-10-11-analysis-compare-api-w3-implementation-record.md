# W3 unified weekday and holiday time correspondence

Date: 2026-10-11
Baseline: clean `main@3e9a70d5fd`
Status: W3 and follow-up repairs implemented. Timestamp repair passed focused tests, related slow tests and the complete daily gate; the earlier complete slow gate passed before this repair. ClickHouse Runtime remains unverified.

## Delivered contract

The closed public family is `ordinal()`, `weekday()`, `holiday(current=..., baseline=...)`
and `holiday_then_weekday(current=..., baseline=...)`, with concrete public value types.
All four produce canonical string aligned coordinates. The three new descriptors
require single-day grids, including a certified calendar's day level. Holiday
occurrences require date bounds or aware midnight timestamp bounds in the
occurrence's boundary timezone, which must match its corresponding grid.

The sole rule owner is the [Period correspondence section](../../specs/analysis/timezone-and-calendar-design.md#period-correspondence).
Bucket dates use frozen `original_start` and boundary timezone. Hybrid counters
measure outward civil-day distance from each occurrence boundary, including dates
outside the observation window. Occurrence lengths and window lengths may differ;
W2 exact/outer coverage policies remain in force.

Internal closed captures retain occurrence Ref, key, original bounds, timezone
and snapshot digest. One pure evaluator constructs the complete injective frozen
tables and checks them against the original grids during definition validation.
Source, fixed and native SQL execution consume the tables. Original quantity
state, contribution coordinates, endpoint presence and both original time
projections remain retained. Both comparison Cell views keep independent finish
semantics. Time-mapped ratios require an exact ordered `one_to_one` binding;
ordinary `ratio(align=exact(time=...))` still rejects.

Time-mapped `CorrespondencePart` is v4; `ComparisonPart` remains v2. The graph
snapshot and shared value-table envelope use `graph_dag/v10` and `graph-dag-v10:`.
Store generation remains 9. Old graph schemas reject with source re-execution
guidance, without migration or dual reading. Time-key kind and exact occurrence
identity/digest participate in definition and execution identity.

## Disclosure

Exports, concrete public annotations, native Help, constructor acquisition routes,
independent export/reachability/budget checks, bounded descriptor repr/show and
actual-kind dynamic guidance were updated together. The existing task-context
budget reference is preserved separately from the refreshed output snapshot;
no budget limit was raised. The latest English and Chinese site examples cover
weekday and both holiday constructors. API documentation is generated through the
existing build.

## Validation boundaries

Independent key tables cover repeated weekdays, signed offsets, half-open
occurrences of unequal lengths, outward counts beyond a clipped window, partial
buckets, DST, and certified calendar day grids. Source/fixed and single-quantity
native SQL comparisons cover all four descriptors. Equal key images exercise
exact and empty finish with independent Null/Undefined propagation; unequal images
exercise outer keep and construction rejection. Holiday exact absolute views
retain original attribution. Ordered one-to-one ratios cover all four descriptors.

Independent source-offline fixed/cold workers cover ordinal and occurrence-bound
hybrid mappings, forbid source/Catalog/native connection opens, and forbid cached
execution from recomputing or creating another Run. Corruption cases reject
noncanonical string mappings, changed snapshot digests, incomplete/duplicate maps,
forged original projections/presence, independent Cell reasons and old schemas.

| Gate | Result |
| --- | --- |
| Standalone `make check-agent` after coverage repair | 4847 passed, 1 skipped, 1 existing DuckDB deprecation warning; lint, typing and generated API docs passed; 358.75 seconds |
| Final focused key/disclosure suite (`test_time_alignment`, period guidance, independent disclosure) | 50 passed |
| Full `PYTEST_ADDOPTS='-n 2' make slow-test` after coverage repair | 104 passed, 3 existing DuckDB deprecation warnings; 603.76 seconds |
| W3-focused slow suite, run sequentially with `PYTEST_ADDOPTS='-n 0'` | 4 passed, 17 deselected |
| Native PostgreSQL, MySQL and Trino period carrier Runtime | 6 passed |
| ClickHouse period carrier Runtime | 2 skipped: service port 18123 closed and readiness flag unset; unverified |
| Source/public typing | 350 files passed in the daily gate; 204 analysis/typing files passed after final signature changes |
| Focused test-module typing (`--follow-imports=silent`) | 4 files passed |
| Latest site content check | 385 required files verified |
| Final site build | 0 errors/warnings/hints; 363 pages built; 308 original routes, 48 latest pages and 351 internal targets verified; API docs generated through the build |
| Whitespace | `git diff --check` passed |

The earlier standalone W3 daily gate passed in 312.99 seconds. Its two warnings were
an Ibis Comparable destructor-cache KeyError warning and an existing DuckDB
`fetch_arrow_table()` deprecation warning. Neither failed the gate. A separate
bounded-descriptor full-output continuation test passed, with its touched source
module passing typing. Two repeated period fixed/cold tests exceeded their unchanged 90-second worker limit while
several test gates ran concurrently; the standalone sequential slow rerun passed
in 58.45 seconds. A concurrent daily rerun likewise hit one existing Session cold
worker's 30-second limit. These failed runs are recorded as test-resource failures,
not as passing evidence. The Session cold case then passed independently in
2.84 seconds, and the final gate ran alone and passed. No timeout or fixture
lifecycle policy was changed.

The refreshed task-context output has 35,344 characters against the preserved
41,647-character reference (85% total limit: 35,399.95). The actual slow budget
assertions passed without the recording environment variable.

## Full slow baseline failure and authorized repair

The complete slow suite failed only
`tests/analysis/statistics/test_deviation_contributions.py::test_captured_contributions_followup_profiles[KC-parquet-us-America/New_York-True-entity_time]`.
The failure is a typed `CoreRuleError` in `graph_lowering.lower()`: expected an
implemented checker for the bound obligation, received `source.complete_coverage@v1`.
This occurs when an exact ordinal absolute comparison is scored for Deviation,
filtered to positive scores, projected to members and observed again under a
certified calendar's period grid.

A clean source archive from `git archive 3e9a70d5fd`, using the same dependency
runtime and an explicit archive-root `PYTHONPATH`, reproduced the identical
failure with this command:

```sh
make slow-test TESTS='tests/analysis/statistics/test_deviation_contributions.py::test_captured_contributions_followup_profiles'
```

The baseline run failed in 4.67 seconds; the current W3 tree independently
reproduced the same failure in 4.77 seconds. Its traceback resolves all Marivo and
worker code beneath the archive root, including the unchanged checker dispatch
at `marivo/analysis/compiler/graph_lowering.py:4500`; it does not import W3 source.
The initial W3 lowering diff changed only mapped comparison CASE fallback types from
int64 to string and did not modify this checker dispatch. This was an existing
acceptance defect, not a W3 regression or an environment skip.

The user subsequently authorized repairing this failure, adding regressions and
rerunning both complete gates. The originating comparison validates complete
original period buckets locally. Lowering now retains the same deferred obligation
for an inherited consumer when the originating requirement belongs to its actual
graph ancestry, rather than trying to build another SQL checker from the source
operands. Execution still requires the completed originating proof before the
consumer runs; no coverage policy or checker was removed.

Two default-suite regressions cover successful proof retention with source/fixed
score equivalence, and incomplete buckets rejecting before Deviation fitting.
The existing certified-calendar/composite-key/parquet/decimal slow worker passes
after the repair. Its stale physical-shape assertion now expects `NoTime()` for
aligned comparison input and verifies an aligned coordinate with no real TimeGrid;
the followup observation still independently checks the original calendar and
boundary timezone. The narrow slow case passed in 11.49 seconds, the new two-case
regression passed in 2.36 seconds, and 128 related regressions passed in 35.98 seconds.
The changed lowerer and both new/modified test modules passed targeted typing.
The complete slow rerun passed all 104 tests in 603.76 seconds. The original failing
case was included; no skip, timeout or coverage-policy adjustment was used.
The subsequent standalone `make check-agent` passed 4847 tests with one existing
skip and one DuckDB deprecation warning in 358.75 seconds. All 828 Python files
passed formatting/lint, import contracts passed, 350 source/typing files passed
typing, and the API documentation build succeeded. Final `git diff --check` passed.

## Timestamp capture cold-restoration correction

Review found that certified whole-day timestamp bounds used `ZoneInfo("UTC")`,
while JSON decoding produced an offset timezone object. Their differing reprs
changed the derived coverage-fact fingerprints. Execution and retained data reads
succeeded, but restoring the definition for public `contract()` failed with
`saved method derivation differs from its registered semantics`.

Occurrence captures now normalize aware bounds to the certification's canonical
UTC representation on both construction and decode, after checking whole-day
boundaries in the occurrence timezone. Instants, civil-day evaluation and the
captured boundary timezone remain preserved. No wire schema or Store bump is needed.

The existing independent offline worker now covers timestamp occurrences for
both holiday descriptors, including source and fixed results, the root and both
view contracts, cold recovery and cached execution without recomputation. Before
the fix, both timestamp cases failed and both existing cases passed. After the fix,
the complete related slow scope (period alignment, comparison recovery and Help
context budget) passed all 6 tests in 62.01 seconds. The focused time-alignment suite
passed 28 tests in 41.57 seconds, including two definition round-trip regressions
and a New York DST capture round trip preserving its 23-hour interval and civil
dates. All four touched code/test modules passed targeted typing.

The earlier full slow run above predates this timestamp repair; this follow-up
reruns the requested related slow scope and complete daily gate.
The final `make check-agent` passed: 4850 tests passed, 1 skipped and 2 existing
warnings (Ibis Comparable destructor cache and DuckDB deprecated Arrow fetch), in
359.32 seconds. Formatting/lint of 828 Python files, import contracts, typing of
350 source/typing files and generated API documentation all passed. Final
`git diff --check` passed. No commit or push was performed.
During the final gate, an external commit advanced HEAD to `99093029ca`, solely
untracking `docs/specs/analysis/algebra-theory.md` while retaining its local file.
It changed no implementation or test code. That external change and the local
proposal were preserved.

## Scope

No WorkSchedule alignment, release, commit, push, packaged skill change or
AGENTS.md change is included. The baseline was clean; all requested changes
remain uncommitted for review.
