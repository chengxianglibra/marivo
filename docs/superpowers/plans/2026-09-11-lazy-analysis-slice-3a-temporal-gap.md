# Slice 3a temporal source prerequisite found during Slice 8a acceptance

Status: accepted private implementation and accepted Slice 8a integration on
2026-09-12. The complete private supplement from
`407c8c35eb4a3364797f8540f8ddfc123c291d08` is integrated in the isolated
candidate. The original three public temporal reproductions and six private
Runtime regressions pass together. The candidate preserves both certified
calendar snapshots and persisted report-time authority. The source checkout
has not received the public cutover.

Earlier failure descriptions below are historical evidence. The final Slice 8a
assembly record supersedes their outstanding-work statements.

## Ownership and evidence

Slice 3a owns temporal source compilation and coordinates. Slice 2a owns the
normalized semantic time facts; Slice 4a owns execution-time source resolution.
Slice 8a may connect these capabilities once implemented and privately accepted.
The parent cutover plan explicitly returns missing algorithms to their private
owner instead of implementing placeholders during public assembly.

The frozen main baseline is `5ae1de7eac4d8a610eda764b8641904894b09603`.
The compiler's `_validate_temporal_axis` method has exactly the same AST in that
baseline and this candidate. `temporal-private-gap-proof.json` in the assembly
directory records that comparison. The failure is not introduced by public
facade binding. The accepted RuntimeMetric 3b supplement remains accepted for
its tested scope.

## Original reproduction (2026-09-11)

From the isolated candidate, using its existing project interpreter:

```sh
PYTHONPATH="$PWD" make runtime-test TESTS='tests/test_lazy_temporal_public_runtime.py'
```

The test creates a real DuckDB source with exact declared physical types and
stable identity. It verifies the Ibis DuckDB connection reads in UTC, declares a
Session report timezone of Asia/Shanghai, and observes two instants:

| Source UTC wall clock | Value | Expected report day |
| --- | --- | --- |
| 2026-07-01 15:59:00 | 1 | 2026-07-01 |
| 2026-07-01 16:01:00 | 2 | 2026-07-02 |

It covers explicit `ms.timestamp(timezone="UTC")`, native `timestamp(6)` with
engine-default read timezone, and `ms.strptime(..., timezone="UTC")` over string
source values. All three failed before publishing a Dataset at that baseline. A separate
pure compiler diagnostic preserves the underlying structured error:

- Expected: native date/timestamp coordinate without parsing or timezone conversion.
- Received: unsupported temporal source representation.

The native timestamp case also exposes a precision mismatch: normalization uses
logical `timestamp`, while the actual declared source is `timestamp(6)`. Exact
physical source validation must remain distinct from logical temporal-kind
admission. Do not cast away source precision to make the check pass.

The seed connection created with the low-level DuckDB API uses the machine's
local timezone; that is not the analysis reader. Earlier probe logs 2 and 3
failed on that test assumption and are superseded by
`migration-temporal-gap-runtime-4.log` (3 real compiler failures) and
`temporal-compiler-gap.txt`. The final fixture explicitly verifies the Ibis
reader, which is the relevant native execution path.

## Required private completion

Preserve the owning timezone/calendar and lazy observation contracts. Thread
persisted Session report-time authority into the native construction/execution
contract without source I/O during construction. Resolve and preserve declared
or engine read-time authority at the correct execution boundary. Compile
supported temporal parsers and distinguish absolute instants, localizable wall
clocks, and civil dates. Preserve source precision, half-open endpoint meaning,
report-time bucketing and certified calendar boundary authority.

Acceptance must cover timestamp precision, explicit/default read timezone,
string/hour partitions, report-day changes, DST behavior under the owning
contract, exact endpoints, and cold retained continuations. Keep independent
numeric and boundary assertions from the old temporal suites, including the
84 whole-hour and 85 partial-final-hour comparison cases. Do not delete those
assertions or change positive tests to expect rejection as a substitute.

At the original baseline, the positive Runtime test remained enabled and failing. Existing legacy
temporal tests remain present until their distinct assertions have a working
new owner. Broader test collection and Slice 8a's full gate are still open;
this three-case reproduction is not the complete private temporal acceptance.

## Private completion (2026-09-12)

The main workspace now preserves exact physical temporal types and parser facts,
captures persisted report authority during pure construction, resolves reader
authority at execution, and uses native DuckDB timezone expressions for scope,
version selection, coordinates, semi-additive and cumulative calculations.
Validated temporal metadata and retained instant coverage survive source-offline
continuations under a changed host timezone. Literal half-open endpoints and the
independent 84/85-hour comparison assertions remain covered; no old temporal test
was removed.

The frozen executable candidate SHA-256 is
`61e4f4a410e7b23c53a855f62b3397a27e4e80cd3fc0ae16e209a3637597d271`.
Final validation passed: 47 focused tests, 36 selected Runtime regressions,
four new test/helper files typed, `make check-agent` (7244 tests, lint, source
typing and API docs), and the original three tests in an isolated assembly copy.
The cold producer published a complete Run/Artifact/Evidence; the separate
recovery process changed host timezone from UTC to Pacific/Honolulu and forbade
source access while checking rows, time authority, filter, rank/limit and rollup.

The [acceptance record](/Users/lichengxiang/source/oss/marivo/evidence/slice-3a-temporal-63vbtwzu/README.md)
contains the exact candidate manifest, commands, logs, transfer counts, publication
identities, cold recovery proof and the complete private patch. The original
candidate's code and existing assembly changes were preserved; only this gap
record is updated there. Only the Slice 3a temporal prerequisite is closed.

## Isolated integration acceptance (2026-09-12)

The integrated candidate passes the temporal Runtime cases (9), all default
checks (4,824), source typing (332 modules), import/lint checks and API docs.
The current public examples, EN/ZH documentation and native Help execute against
this integration. Historical eager temporal tests have been replaced by the
native owners recorded in `temporal-final-test-replacement-map.json`; the exact
84/85-hour, precision, timezone, half-open-boundary and cold recovery assertions
remain covered. Retired Frame metadata and implicit alignment policies are
identified as removed contracts rather than claimed as preserved behavior.

See [the final Slice 8a assembly record](2026-09-11-lazy-analysis-slice-8a-assembly.md)
for the complete replayable delivery and separate activation boundary.
