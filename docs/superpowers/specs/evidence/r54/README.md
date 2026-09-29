# R5.4 coordinate, target and partial-reduction evidence

Baseline: `panda`, `62a740113e6effd50a9b6e40ed586215a478e821`.
The workspace was clean before creating this evidence directory: R5.3 was already
committed. `baseline.diff.gz` is empty; `baseline.json` captured the newly created
evidence directory as the sole untracked path. All implementation changes remain
uncommitted. This is source-tree evidence, not installed-wheel acceptance.

## Independent oracles and qualification

| Obligation | Independent expected result / evidence |
| --- | --- |
| Full tuple union | Web/paid and mobile/refunded only; values 850/150, two real tuples instead of four Cartesian cells. Same-root count ratio retains denominator-only mobile at zero; signed cancellation retains both zero coordinates |
| Combined classifications | Region × cohort has exactly four actual member tuples; three fact totals 450/150/400; total 1000 with no fact amplification |
| Partial axes / L8 | Preserve complete Entity + channel, then channel, then Singleton; direct and hierarchical sum state both `(1000,3)`, equal quantity identity and usable K |
| Explicit Entity targets | Empty target groups retain Subject identities A/B/C/D; selected-east total stays600, not1000 |
| Explicit targets / L9 | Same east/south/west target on both paths, including empty groups; row mean states `(600,2)`, `(0,0)`, `(0,0)`; identical values, quantity and retained components; total mean300 |
| Original versus current rows | 100 one-unit orders plus one 100-unit order: member means 1 and100; row mean50.5, original mean200/101 |
| Six row methods | Source/fixed sum/min/max/mean/count/count_defined, grouped merge preserves row identity and true state; nonnumeric receivers accept only count descriptors |
| Four Cells | Pure registered fixed reducers consume Defined/Null/Undefined/Unknown: count4, count_defined1; empty count0 |
| Empty row state | Sum0; mean/min/max Undefined with method-specific reason. Empty mean retains valid sum0/count0 and merges |
| Weighted coordinates | Table/Parquet channel means150/425; original paired state `(1000,3,3,3)` gives1000/3 rather than averaging subgroup values |
| Exact correspondence | Consumed missing/null classifications, repeated keys and outside-target tuples reject; retained duplicate/foreign/tampered component keys and values reject |
| Integrity / K | Missing/corrupt row-state files reject contract, execution and recovery; unsupported state version and different row binding reject |
| Cold recovery | Fresh process resumes grouped RowStatistic while models/database are offline, Semantic loading, datasource access and DuckDB are forbidden; retained mean49 from `(147,3)` |
| Historical obligations | D04 no-I/O planning; D05-D08 diagnostic versions cannot change plan or147; D14 source-offline140 and140/3 plus component damage checks |
| Disclosure | Native registry, type/export/drift tests, dynamic K, bilingual executable examples and site build; CLI consumes the existing Help owner |

Runtime cases are in `tests/test_analysis_coordinates_r54.py`, the re-enabled
historical tests, and `tests/test_cutover_documentation_examples.py`. Existing
R5.3 and R4.5 regressions remain separate evidence, not substituted numerical
oracles. Exact commands and outcomes are in `commands.json`; logs are retained
alongside it. `candidate.json` binds the non-evidence candidate files by SHA-256.

## Boundaries and contract changes

The qualification retains complete string/int64 member identities, string contribution coordinates and string/int64 classifications, existing finite numeric inputs,
DuckDB table/Parquet and current UTC/no-time shapes. Original mean uses direct
int64 components. No time grids or Grain coarsening, full Decimal/Duration/other
numeric matrix, installed candidate, release or real Agent acceptance is claimed.
Those remain R5.5, R5.6 and R5.7 respectively. Named statistical_weight remains
withdrawn. Packaged skills and AGENTS.md were not modified.

OriginalReduce now binds a tuple of retained axes. RowState distinguishes new
current-row reduction from retained-state merge. Row sum includes a support count.
Group attachment/completion use the existing closed graph, method registry, Ibis
lowering, fixed Python consumer and Store 7. No parallel executor, compatibility
alias, wire migration or Store generation was introduced. Old incompatible
frozen encodings do not acquire a dual reader.

## Adversarial-review follow-up

`candidate-before-review-fixes.json` preserves the reviewed fingerprint.
`review-fixes.json` records accepted findings, independent business oracles and
rejected interpretations. The repaired paths include target-domain materialization,
selected-category grouping, independent-root numeric predicates, and fixed grouped
current-row statistics. The latter retains group axes and uses the already
materialized rows; it does not recreate pre-group members. All six row factories
have independently checked Help examples, CountMethod has count-only acquisition,
and the statistic class retains a docstring.

The Runtime owner now explicitly accepts the incompatible R5.4 frozen layouts in
`docs/specs/analysis/session-state-and-runtime.md`. Ordered arity tests cover
parts_transport and both grouping methods. Duplicate dispatcher literals were
removed; static comparison found no missing durable state kind or qualified fixed
method. Existing count semantics, part-corruption tests and diagnostic-version
invariance tests remain unchanged. Follow-up command records below the original
records in `commands.json` supersede earlier candidate results for this repair.

Final repaired candidate: `d26d5fbe700cf75bda801601174edcda931cbc9a1293f2e162ddae165f5ed203`.
Final compact broad gate:5391 passed,13 skipped, all401 modules typed, lint/import
contracts and API docs passed. Expanded Runtime:179 passed. Site:321 pages,
Astro diagnostics and bilingual installer verification passed. `git diff --check`
passed. These final gates include both review batches; earlier intermediate
results do not substitute for them.

Raw diff snapshots and command logs containing tool-rendered trailing whitespace are gzip-compressed so the staged patch check inspects source changes without misreading evidence payloads.
