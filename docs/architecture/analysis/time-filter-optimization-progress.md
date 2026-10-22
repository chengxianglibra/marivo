# Backend-native time-filter optimization

Status: backend-native source semantics and shared filtering implemented; final local acceptance passed. No commit or push was made. Earlier acceptance sections below are historical and superseded by the backend-native plan and final results.

## Contract and ownership

- One implementer owns the DuckDB connection and shared temporal physical rewrite,
  focused regressions and Yellow Taxi acceptance. Existing unrelated edits in
  both repositories are preserved.
- Preserve native source conversion results, precision, nulls, exact interval
  endpoints and independent observation/member/version domains. Source DST
  rejection is retired; independent cardinality/sample/calendar checks remain.
- The physical rewrite owns UTC cancellation and common native time filtering.
  It must not narrow a preparation-check domain to the selected data domain.
- Named zones use conservative raw scan envelopes with exact backend-native
  residuals. No custom TZif directory, horizon limit, rule-agreement prerequisite
  or runtime rule probe remains. All five backends share physical filtering;
  provider SQL lowering retains its existing type/representation behavior.

## Previous acceptance stages and evidence

| Stage | Status | Acceptance |
| --- | --- | --- |
| Baseline | Captured | Original source and dirty-tree evidence under `/tmp/marivo-time-optimization-20261022` |
| DuckDB threads | Passed | Independent production process observes 8; pytest worker cap stays separate |
| UTC and common time rewrite | Passed | UTC inverse cancellation preserves type/precision; scan guards attach to existing filters |
| Native rule qualification and DST | Passed in previous acceptance | Generated constants qualified finite rules; transition and uncovered clocks retained native checks |
| Focused regressions | Passed | 48 focused cases after explicit canonical-parser ownership repair |
| Yellow Taxi | Passed | Full 12-file count, amount, Population, within and DST comparisons below |
| Broad gate | Passed | Final unnarrowed `make check-agent`: 5214 passed; lint, typing and API docs passed |

Performance measurements must separate compilation, source execution and total
latency. Historical timings are not the new baseline. Generated acceptance
artifacts stay outside the source tree.

## Previous implementation repairs

- Preserve the output timestamp precision when cancelling a UTC inverse pair.
- Rebind expanded check expressions through their actual filter input; version
  identity, attribute, relationship-filter and image reads retain their scopes.
- Classify the source-free Ibis rule probe in the existing SQL ownership audit.
- Reuse the canonical endpoint encoder and keep native evaluation when a scan
  literal cannot fit its physical integer type.
- A compiler-owned DuckDB parser node retains the explicit `ms.strptime`
  canonical-width promise. Ordinary authored Ibis parsing cannot acquire that
  promise from its operation name or format. Variable-width accepted strings
  are a regression witness.

## Previous acceptance with runtime rule qualification

The original source is immutable under
`/tmp/marivo-time-optimization-20261022/baseline`. The same frozen demo models,
read-only warehouse and all 12 Parquet files are used. Each January borough
count configuration has one warmup and three measured public `compute()` calls,
with independently verified results and observed effective thread settings.

| Configuration | End-to-end median | Business SQL median | Count |
| --- | ---: | ---: | ---: |
| Original, 1 thread | 135.012 s | 134.794 s | 3,475,234 |
| Original, 8 threads | 39.661 s | 39.400 s | 3,475,234 |
| Final candidate, 8 threads | 3.833 s | 1.064 s | 3,475,234 |

The January count includes 30 January pickups in the February file. No
`source_month` predicate is added. July daily counts and fare sums match the SQL
oracle with `rtol=1e-12, atol=1e-6`; Population counts match the independent
265-zone left-join oracle. Correctness-only runs made during development are
not performance trials.

The final candidate reduces this observed end-to-end median by 35.2x versus
the original one-thread configuration and 10.3x versus the original eight-thread
configuration. End-to-end measurements include source-free rule qualification.
They are warm local observations, not a latency SLA or a cold-install claim.

Measured end-to-end trials, in seconds:

- Original, 1 thread: 121.915, 135.012, 141.333.
- Original, 8 threads: 40.128, 38.399, 39.661.
- Final candidate, 8 threads: 3.833, 3.919, 3.692.

Separate compile-only trials use the same public observation and stop immediately
before business execution. Three measured `SourceSession.compile` calls give
median business-SQL compilation costs of 0.105 s, 0.106 s and 0.172 s respectively.
These include issuance validation and backend compilation; they do not attribute
the entire preparation phase to compilation.

## Previous correctness and physical evidence

- January borough counts: all eight groups and the 3,475,234 total match the
  independent SQL oracle for every measured run. The 30 spillover records remain.
- July: all 31 daily counts and fare sums match both the original implementation
  and the SQL oracle. Floating sums use the stated tolerance.
- Explicit Population: all 265 pickup-zone members match the independent
  left-join oracle, including zero-contribution members.
- `TimeField.within` combined with the same observation scope matches all eight
  borough groups in the original and final implementations.
- March 9: the 23-hour New York day matches in both implementations and SQL.
  The raw files contain no nonexistent 02:00-03:00 clocks that day; nonexistent
  clock rejection is established by the focused native regression fixtures.
- November 2: the files contain 11,687 ambiguous 01:00-02:00 clocks. Selecting
  only the first repeated hour still rejects in both implementations with the
  same `MaterializationError` type, expected input and received field identity.
- Actual `EXPLAIN` on the original January business SQL shows no Parquet time
  filter. The final SQL's `READ_PARQUET.Filters` contains direct
  `tpep_pickup_datetime >= TIMESTAMP '2025-01-01'` and
  `< TIMESTAMP '2025-02-01'` conditions. This establishes scan pushdown, not a
  claim that eleven files are excluded; file month is not business time.

## Previous reproduction and scope

Artifacts are under `/tmp/marivo-time-optimization-20261022/`:
`demo_acceptance.py`, frozen models, original source, per-trial SQL/JSON/logs,
`summary.json`, `scan-plans.json`, `candidate-fingerprints.json`, and
`check-agent-final.log`. The demo interpreter used Python 3.14 and DuckDB 1.5.5;
repository checks used its pinned virtual environment. The baseline-eight-thread
experiment changes only connection threads in the original source environment.
All configurations observe their actual setting before business execution.

Run the public January case using the demo interpreter and explicit source path:

```sh
PYTHONPATH=/path/to/marivo /path/to/demo/.venv/bin/python \
  /tmp/marivo-time-optimization-20261022/demo_acceptance.py \
  --mode optimized8 --runs 3 --warmup 1
```

The original baselines remain immutable while the candidate is finalized; the
final candidate is fingerprint-checked throughout its acceptance runs. Existing
tracked dirty diffs were compared byte-for-byte with the initial diff and remain
unchanged. HEAD remains at its initial revision. The original demo models and raw
data were not edited. No external provider Runtime or release gate is claimed.

## Removal of runtime qualification

The user accepted equivalent Python/DuckDB timezone rules as a deployment
prerequisite. Remove the second-grid probe, backend cache, rule fingerprint,
probe-only SQL audit entries and backend argument plumbing. Keep explicit finite
TZif transitions, the existing horizon policy, native gap/fold witnesses and
unknown-coverage fallback. Replace retired mismatch-probe tests with directory
unavailability fallback coverage; retain native DST and Parquet pushdown tests.

Owner: current implementation task. Acceptance: focused native regressions,
unnarrowed `make check-agent`, and renewed Yellow Taxi count, amount, Population,
within, DST and performance comparisons using the original immutable baseline.
New artifacts: `/tmp/marivo-time-no-probe-20261022/`.

### Renewed Yellow Taxi acceptance

The final no-probe implementation uses the same frozen models and all 12 source
files, through public observations with independent SQL oracles. The original
1-thread and 8-thread timings below are reused immutable measurements from the
previous acceptance, not newly rerun baselines. New candidate measurements use
one warmup and three measured computes, without concurrent test-suite load.

| Configuration | End-to-end median | Business SQL median |
| --- | ---: | ---: |
| Original, 1 thread (previous measurement) | 135.012 s | 134.794 s |
| Original, 8 threads (previous measurement) | 39.661 s | 39.400 s |
| Optimized with runtime qualification (previous measurement) | 3.833 s | 1.064 s |
| Optimized without runtime qualification (renewed measurement) | 1.226 s | 0.809 s |

New end-to-end trials: 1.223, 1.243, 1.226 seconds. Every run observes 8 threads,
executes source reads and matches the independent oracle: 3,475,234 January
pickups. No source-month pruning is introduced. The new median is about 3.1x
faster than the previous qualified implementation; these are local warm timings.

July daily counts and fare sums, all 265 Population members, within selection,
and the March 9 spring-transition day match the original 8-thread implementation
and independent SQL. November 2 first-fold-hour selection still raises the same
MaterializationError with identical expected/received fields as the original.
The real spring day contains no gap rows; synthetic native tests retain gap,
fold, half-hour transition and date-line jump coverage.

EXPLAIN again confirms direct January lower/upper bounds in READ_PARQUET Filters.
It does not prove eleven files are skipped. `scan-plans.json` records the plan.
A one-row, one-year New York query now rewrites in 0.007 s, compared with the
review's 20.318 s before probe removal, and returns the same native result.

Focused validation: 199 tests passed. The removed mismatch/probe-offset cases
belong to the retired runtime agreement contract; directory-unavailability and
future-directory fallback assertions remain. Unnarrowed `make check-agent` passed: 5213 tests, 776 existing warnings,
269.28 seconds; lint, typing, import contracts and API documentation passed. Candidate fingerprints match the source used
throughout acceptance. Baseline comparison JSON, trial SQL/JSON/logs, the
reproduction runner and small-query result are in the new artifact directory.

Other pre-existing tracked diffs are byte-for-byte unchanged. No commit or push
was made, and no external provider Runtime or release validation is claimed.

## Backend-native time semantics plan

Status: implemented and accepted locally; no commit authorized.

The accepted source contract delegates gap/fold interpretation to each backend.
Remove source DST rejection, alternative instants and error transport, while
keeping user boundary/calendar validation and independent cardinality checks.
Delete the TZif directory and DuckDB-only rule rewrite. A shared physical pass
cancels UTC inverses and adds conservative raw-column envelopes for named zones,
retaining the native expression as the exact residual. UTC/fixed offsets retain
exact inverses. No Python/backend rule agreement or bounded-year directory is
needed. All five backends use the pass and their existing native SQL lowering.

Acceptance: native conversion oracles for gap/fold, boundaries and nulls;
compiler coverage for every backend; available provider Runtime checks; full
local gate; full 12-file Yellow Taxi count/amount/Population/within and native DST
oracles plus warm performance measurements. Preserve unrelated changes.
Artifacts: `/tmp/marivo-native-time-20261022/`.

## Final backend-native acceptance

The candidate deletes `compiler/time_rules.py` and the DuckDB-only rewrite,
replacing it with shared `materialization/time_filter.py`. Source gap/fold
rejection, alternative instant selection, version error columns, membership
check transport and the dedicated source-time error branch are removed. The
compiler still preserves independent scalar cardinality, sampling completeness,
Session-boundary and calendar contracts. Live Help, authoring docstrings,
architecture docs and English/Chinese time-series documentation are aligned.

### Repeated full-file performance

Both configurations below use the final source, backend-native source semantics,
DuckDB 1.5.5, Python 3.14, 8 threads, the same frozen models and all 12 Parquet
files. `native8` disables only the new UTC cancellation and scan-envelope pass;
it does not restore the retired DST rejection semantics. One warmup precedes
three measured public computes, without simultaneous test-suite load. Every
compute executes source reads and reconciles with independent native SQL.

| Configuration | End-to-end median | Business SQL median |
| --- | ---: | ---: |
| Native semantics, shared optimization disabled | 4.999 s | 4.836 s |
| Native semantics, shared optimization enabled | 0.920 s | 0.720 s |

End-to-end trials: baseline 4.939, 4.999, 5.025 s; optimized 1.221, 0.847,
0.920 s. The local warm end-to-end improvement is approximately 5.4x. These are
new same-round measurements; earlier baselines in this ledger are historical.

### Correctness and scan evidence

- Every January count is 3,475,234, including the previously established 30
  January pickups in the February file. No source-month filter is introduced.
- July daily counts/amounts, all 265 Population members, within and the spring
  DST day match independent SQL and earlier nonambiguous original results.
- November first fold hour (05:00Z-06:00Z): zero source contributions, matching
  DuckDB's native interpretation. Second fold hour (06:00Z-07:00Z): 11,687
  contributions, matching native SQL. Neither query raises a Marivo DST error.
- Native test oracles cover nonexistent clocks, repeated clocks, half-hour
  transitions, a date-line jump, microsecond boundaries and NULLs. The demo's
  spring day has no nonexistent source clocks; synthetic tests own that case.
- EXPLAIN on the unoptimized native query has no Parquet time Filters. The
  optimized scan contains raw bounds from 2024-12-31 05:00 through
  2025-02-02 05:00, while the original native January predicate remains exact.
  These are deliberately conservative bounds, not a one-file pruning claim.

Focused tests: 227 passed. Modified production modules passed focused typing;
all edited Python passed lint. Unnarrowed `make check-agent` passed: 5217 tests, 781 warnings,
268.38 seconds; lint, typing, import contracts and API documentation passed.
SQL generation covers DuckDB, PostgreSQL, MySQL, ClickHouse and Trino, including
2050/400-day ranges without a TZif directory. Local service ports for the latter
four providers were unavailable; no real-provider Runtime success is claimed.

Artifacts in `/tmp/marivo-native-time-20261022/` include the reproduction scripts,
frozen models, same-round baseline/candidate SQL and JSON, summary, scan plans,
logs and accepted source fingerprints. The original demo models and data remain
untouched. All backend behavior claims assume native support for the requested
conversion; native NULLs and engine errors are preserved rather than overridden.

Final source fingerprints match the benchmark candidate after the complete gate.
HEAD is unchanged, original thread-setting edits are preserved, unrelated local
proposals remain untouched, and no files were committed or pushed.
