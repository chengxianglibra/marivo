# Compact Cell execution and storage

## Scope and behavior

The four semantic states remain Defined, Null, Undefined and Unknown. They still
distinguish a valid zero from an empty contribution, undefined arithmetic and
incomplete coverage. The optimization changes their physical representation,
not the algebra, available methods, error policies or public result columns.

Each slot freezes one closed carrier before execution:

| Carrier | Physical fields | Static premise |
| --- | --- | --- |
| Known | value | One statically known state |
| Validity | value and its Arrow/SQL validity | Defined or one declared non-Defined pair |
| Encoded | value and non-null int16 state | Multiple possible non-Defined pairs, or an optional endpoint |

Defined is code 0. Other codes are `4 * ordinal + tag`, with Null=1,
Undefined=2 and Unknown=3. The dictionary sorts by semantic tag then reason,
contains at most 8191 pairs, and is remapped deterministically when inputs
combine. Endpoint absence uses -1 only in an explicitly optional binding;
correspondence presence remains independently owned and checked. Empty outputs
retain their static binding. Actual rows never choose the carrier or dictionary.

`core/cell_encoding.py` owns the closed encoding. `compiler/cell_lowering.py`
rewrites typed Ibis relations and state predicates before a source read is
issued. It does not rewrite issued SQL text. `materialization/cell_arrow.py`
owns compact projection, renaming, scalar consumption and vector decoding.
Public result disclosure expands the same value/tag/reason columns as before.
Producer-owned logical construction is packed once; issued reads, exchanged
tables, Parquet results and statistical retained Cell views carry compact fields.

The source lowering preserves check identities, stage dependencies, numeric
finishing, rounding certificates and read ownership. In particular, downstream
aggregates consume completed staged arithmetic rather than preparation
placeholders. Outer-join input presence is inserted before NULL extension, so a
present Null Cell cannot be mistaken for an absent endpoint.

Only immutable binding JSON decoding is reused within one execution operation.
The scope is discarded on completion or failure. It does not certify mutable
state arrays, Arrow schemas, callable behavior or disk payloads.

## Persistence boundary

The current versions are descriptor v5, method_state v2, state/part contract 3,
execution_key v3 and physical implementation ABI 6. The previous implementation
ABI was already 5, so this change advances it to 6. Store 8, graph DAG v3,
continuation v5 and receipt v1 retain their existing versions.

The descriptor's primary schema and each part receipt own the frozen dictionary.
Parquet headers contain physical fields without a second copy of that dictionary.
Reads restore the frozen binding and reject invalid codes, undeclared reasons,
null state carriers and value/state disagreements.

Old descriptors, method states and state/part contracts reject before a fixed
cache hit. Existing files and history remain intact. Re-execute the producing
source analysis to obtain a current Artifact. There is no migration or dual read
path. Public exports and callable contracts are unchanged; native Help and both
documentation editions describe this recovery boundary.

## Bounded cost evidence

The baseline is the frozen checkout of
`2cd8d9932ca5f0561b32dd14d27880157ab5b30e` with the initial workspace diff recorded
separately. Baseline and candidate use the same DuckDB fixture, numeric oracle
and source-trace instrumentation. Each scenario executes seven fresh invocations
per round; fixed summaries use a newly captured producer rather than an exact
cache hit. Fixture setup and public disclosure are outside the timed interval.
SQL metrics include every native query issued by the action. Arrow and storage
metrics include primary and retained parts.

The workloads contain 64 or 10,000 fact rows. They cover member observation,
source original rollup, and a fixed current-row sum. This is bounded evidence,
not qualification of every method, engine, storage codec or data distribution.

| Rows | Action | SQL bytes before/after | CASE before/after | Arrow bytes before/after | Controlled Zstd Parquet bytes before/after |
| --- | --- | --- | --- | --- | --- |
| 64 | Observe | 3090 / 2903 | 3 / 1 | 9336 / 8368 | 7964 / 7517 |
| 64 | Source rollup | 3730 / 3395 | 6 / 2 | 44 / 28 | 2395 / 1932 |
| 64 | Fixed sum | 0 / 0 | 0 / 0 | 43 / 27 | 4193 / 2452 |
| 10000 | Observe | 3090 / 2903 | 3 / 1 | 1458750 / 1307500 | 144674 / 144210 |
| 10000 | Source rollup | 3730 / 3395 | 6 / 2 | 44 / 28 | 2395 / 1932 |
| 10000 | Fixed sum | 0 / 0 | 0 / 0 | 43 / 27 | 4193 / 2452 |

Observe and source rollup still issue two queries; fixed sum issues none. Parquet
uses dictionary compression already, so reducing Arrow state bytes does not
imply the same reduction on disk. Descriptor metadata also has a cost and is
included separately in the timing and measurement record.

Two final sequential rounds ran without concurrent test jobs. Each cell below
is the median of seven invocations in milliseconds; the rounds reversed
baseline/candidate order to expose order sensitivity.

| Rows | Action | Round 6 before/after (change) | Round 7 before/after (change) |
| --- | --- | --- | --- |
| 64 | Observe | 89.22 / 91.96 (+3.1%) | 87.72 / 90.66 (+3.4%) |
| 64 | Source rollup | 94.74 / 98.01 (+3.5%) | 100.95 / 99.91 (-1.0%) |
| 64 | Fixed sum | 85.03 / 84.82 (-0.2%) | 92.05 / 85.45 (-7.2%) |
| 10000 | Observe | 135.66 / 140.64 (+3.7%) | 148.61 / 144.32 (-2.9%) |
| 10000 | Source rollup | 94.90 / 98.96 (+4.3%) | 99.17 / 104.70 (+5.6%) |
| 10000 | Fixed sum | 94.29 / 95.62 (+1.4%) | 96.33 / 97.10 (+0.8%) |

These timings do not demonstrate a general execution speedup. Typed lowering
adds compiler work; smaller state buffers and fewer SQL expressions are the
measured benefit. In particular, the 10,000-row source rollup has a small latency
regression in both final rounds. Earlier development rounds also varied, so
these results are not a latency guarantee or proof of a sub-5% regression bound.

Frozen dictionaries cost metadata space. In round 6, descriptor JSON bytes
before/after were 20,423/21,163 (64-row observe), 21,992/22,441 (source rollup),
and 21,754/21,459 (fixed sum); the 10,000-row equivalents were 20,457/21,209,
21,992/22,437 and 21,882/21,375. Actual persisted Parquet plus manifest bytes
were 8,763/8,345, 2,470/2,036 and 4,226/2,513 for the three small scenarios;
the large observe was 298,204/297,770. These are separate from descriptor JSON.

## Validation and limits

- `make check-agent` passed: formatting, lint, import contracts, typechecking
  of 333 source modules, 5,153 default tests passed with one skipped, and API
  documentation built. Subsequent edits only corrected Runtime fixture grain,
  explicit zero-denominator input and current typed-error expectations; both
  touched test files passed Ruff checks and formatting.
- Compact-encoding regressions cover all four states and defined zero, static
  Known/Validity choices, canonical dictionary remapping, int16 capacity,
  empty/chunked buffers, invalid codes and value validity, lossy pandas casts,
  outer-join absence, prepared arithmetic guards and operation-scoped binding
  reuse. Six dialects have static SQL lowering coverage; static compilation is
  not remote engine execution evidence.
- The extended Runtime run completed 161 passing cases and 20 skips before it
  was interrupted for diagnosis of six failures; four cases remained unfinished.
  The six failing cases were repaired and passed focused reruns: rank selection,
  the Undefined union endpoint, both period/coarsened attribution variants and
  both historical snapshot/validity mappings. The Null union endpoint also
  passed after its fixture was aligned with the same common group grain.
  A separate four-case probe passed source/fixed numeric finishing, empty
  cohort policy, local original sum and a downstream statistical ratio.
- Runtime fixture corrections preserve the intended semantic assertions.
  Cohort comparisons retain their explicit common group coordinate; the
  Undefined endpoint uses a real zero denominator instead of relying on an
  absent fact group. History tests assert the current mapping failure owners.
  The production rank-transport repair projects and renames compact bindings
  instead of looking up removed physical string columns.
- Remote PostgreSQL, MySQL, Trino and ClickHouse execution was unavailable in
  the extended run. The interrupted run is not full Runtime qualification;
  release, installer, object-storage and real-Agent qualification were not run.

The implementation is therefore supported by the full daily gate, focused
source/fixed/cold and corruption tests, and bounded cost measurements. It does
not establish an execution-speed gain across methods or engines.

## Adversarial review corrections

The review compared the uncommitted compact-Cell implementation against the
frozen baseline above and reproduced three regressions. All three are repaired:

- Fixed classification attachment projects logical columns, retaining each
  compact Cell binding while adding the classification key.
- Fixed grouped statistics build an explicit keyed schema with the original
  Cell bindings and ordered logical fields. Empty and nonempty results use the
  same schema construction. Explicit target completion also encodes scalar
  logical rows through the frozen binding instead of writing them directly to
  a physical Arrow schema.
- Mixed display validation aligns retained columns by logical field names, so
  different valid carrier choices do not imply identical physical column counts.
  Per-column typed validation and logical value/state comparisons remain active.

Four new Runtime regressions cover empty/nonempty fixed groups, count/mean,
public column order, values, states and artifact recovery. Together with existing
classification, grouped-statistic, explicit-empty-target and mixed-fit table
cases, 27 distinct targeted Runtime cases passed across the focused runs.

The expanded mixed-fit table selection also exposed four existing ranking
presentation failures (both fit methods and column orders). All four fail with
the same fit-disclosure assertion on the frozen baseline; they are not counted
as passing or repaired here. They remain outside these confirmed review fixes.

After the final production fix, `make check-agent` passed again: formatting,
lint, import contracts, typechecking of 333 source modules, 5,153 default tests
passed with one skipped, and API documentation built. `git diff --check` passed.
