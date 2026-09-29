# R5.3 graph observation evidence — reviewed follow-up

Baseline: `panda` / `09a1ef3b73e9cf2420b7e3ee087dcbe8727c3a68`.
This is an uncommitted source-tree candidate, not a wheel or release. The current
record supersedes the initial 15-case and review-only 26-case handoffs, including
the earlier assertion that weighted_mean must move entirely to R5.6.

## Executed behavior and independent oracles

| Case | Expected result from raw facts |
| --- | --- |
| Two roots, unequal grain ratio | A 100/2=50, B 60/2=30; a fanout would give 40 for both |
| Same-root web branch | A 450, C 400, B Null/empty_contribution; defined total 850 versus unsliced 1000 |
| Signed linear, two roots | A 102, B 62; subtracting line revenue gives -98/-58 |
| Three real contribution roots | A 104, B 64 |
| Empty count versus empty sum | Count zero stays Defined; empty-null sum propagates Null |
| Omitted window | A 450+99+77=626 versus August 450 |
| Nested linear | revenue-(revenue-count): A/B/C 1 each; source/fixed/cold original rollup 3 |
| Outer slice over linear/ratio | Canonical pushdown selects web leaves; linear A900/C800, total1700; ratio A450/C400, original total425 |
| Composite member identity | Same first key A at tenants x/y remains 450/700; original sum1700, ratio425 |
| Weighted mean, catalog/runtime × table/Parquet | A (450×2+100×1)/3=1000/3, C400; original total(1000+400×4)/(3+4)=2600/7, distinct from the average of member means |
| Weighted Null/zero cases | Single-sided Null pairs excluded; B zero_weight_sum, D empty_contribution |
| Invalid state | Inconsistent pair/row counts, unsupported nonzero sums, overflowed state and Cell/state disagreement reject |
| Invalid bindings | Wrong declared root role, extra route, repeated root, empty route, incompatible CNY/USD reject |
| Opaque boundary | Opaque Metric loads and catalog.require succeeds; observation rejects instead of inferring a contribution decomposition; governed equivalent still executes |

The primary runtime cases live in `tests/test_analysis_observation_r53.py`.
They hand-list raw-fact expected values; no product aggregation helper serves as
the numerical oracle. Registration, state validation and typing tests are
separate from runtime numerical evidence.

## Persistent state and continuations

Weighted mean retains four int64 columns: weighted_numerator, weight_sum,
non_null_pair_count, row_count. Source/fixed rollups merge those components and
finish afterward. Linear retains each signed additive occurrence's magnitude,
support and empty policy. Both recover through the public session/artifact API
in a new process with models and source database offline and source access
forbidden. Weighted missing original-state files block contract and recovery.
The existing R4.5 source/fixed/coordinate/recovery regressions run alongside these
new cases. This does not close the full R5.7 recovery matrix.

MethodSemantics owns method-to-state-kind mapping; source exchange and exchange
validation consult it. Protocol state-kind/role sets remain explicit wire
constraints. No new public factory, alternate executor or registration owner was
introduced. `observe` typing permits omitted during and returns the actual
numeric/ratio union, independent of route syntax. Public Help and current EN/ZH
examples match the qualified shapes. Packaged skills and AGENTS.md are unchanged.

## Exact boundary

The five factories execute under bounded qualification: DuckDB native table or
Parquet, UTC instant-us, current one/two-hop routes; additive int64 sum/count
ratio and linear leaves; paired direct int64 value/weight means. Composite
member keys are supported. Weighted contribution coordinates, nested nonlinear
finishes and ratio error policy are not newly qualified. Constructing a runtime
descriptor does not qualify every composition. The 256 KiB continuation budget
is unchanged; a 17-term stress probe remains rejected rather than silently
raising the budget.

V03 has bounded positive/negative evidence. V08 has the paired-int64 slice;
remaining Decimal/Duration/float precision/overflow matrix belongs to R5.6.
Coordinate/group expansion is R5.4, temporal expansion R5.5, wheel/full recovery
R5.7. D01-D22 are not promoted; the existing 19 skips remain. Shared
source_admission/dataset_execution consumers remain for R6-R8; the R5 public
graph does not call that route.

## Frozen contract change under plan section 3.5

ObservedQuantity.metric_ref, DirectMetricDefinition.metric_ref and OriginalRatio
operands accept the closed Ref/runtime union, with stable execution-key tags.
OriginalStatePart carries per-component empty_rules. Ratio now retains both
sides' magnitude/support, replacing its former sum/count-only tuple. Weighted
mean adds its own four-part state and observation method. No dual reader, legacy
state migration or new Store generation is added; old frozen-state encodings are
not promised unchanged.

## Verification

Final command results and file/diff SHA-256 digests are recorded in
`manifest.json`; complete logs are alongside this file. Default and Runtime
counts remain separate. No numeric assertion was removed, softened or xfailed.
The source-tree broad gate, focused Runtime tests, static gate and site build
are distinct from installed-wheel, full Runtime and real-Agent acceptance.

| Final command | Result | Log |
| --- | --- | --- |
| `make check-agent` | 5374 passed, 19 existing skips; lint/import checks, typing (401 files), API docs passed | check-agent.log |
| `make runtime-test TESTS='tests/test_analysis_observation_r53.py tests/test_analysis_graph_preflight_r45.py tests/test_analysis_dsl_public.py'` | 75 passed | targeted.log |
| `make runtime-test TESTS='tests/test_analysis_observation_r53.py -k "linear or weighted_mean"'` | 14 passed after final method-metadata alignment | state-continuation.log |
| `make test TESTS='tests/test_analysis_methods_r32.py tests/test_analysis_observation_r53.py'` | 41 passed | default-targeted.log |
| `make typecheck` | 401 files clean | typecheck.log |
| `make lint-agent` and `git diff --check` | clean | static.log; manifest |
| `npm --prefix site run build` | 321 pages; install-script verification passed | site-build.log |

The R5.3 test module now collects 43 cases (33 Runtime, 10 default).
