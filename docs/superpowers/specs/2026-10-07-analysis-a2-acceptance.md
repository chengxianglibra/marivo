# A2 bounded fixed selection acceptance

Date: 2026-10-07

Scope: A2 in the [execution optimization design](2026-10-07-analysis-algebra-execution-optimization-design.md). A1 retains its independent acceptance; A3 remains deferred.

Implementation base: `panda@e158e74348d15fc0291af4ef434fe32d7b60fa67`, including the committed A1 and native numeric changes. This record describes bounded A2 implementation evidence, not a release or additional backend/type qualification.

## Execution and identity boundaries

- The L1 owner exposes a read-only pair eligibility check shared by the existing explicit graph transformation and fixed execution. Grouping constructs no new MethodNode and does not derive another semantic graph.
- After fixed schedule validation, adjacent qualified int64 `artifact_python` receiver selections can share one primary row representation, complete-key index and retained-part key positioning. Each stage evaluates only the preceding stage's surviving positions, with all leaves consumed inside each original predicate tree.
- Actual terminal primary and parts retain the original node's Signature, Cell fields, physical types, complete keys, row/part ordering and continuation. Ordinary Subject, original/coordinate/row state, coverage, statistical weight, endpoints and correspondence have their existing keyed restriction behavior.
- Shared outputs, explicit Artifact inputs, request boundaries, pending checks, external predicates, tag selection, differing policies, cohort/limit/display/attribution views, business-coverage changes, fixed references and specialized parts remain normal execution boundaries. Physical schemas and required parts are checked before starting a group.
- Definition fingerprints, frozen graphs, selected implementations, ordered `plan_digest`, stage proof chaining and actual completed checks retain their existing owners. Store 8, graph DAG v2, descriptor v3 and continuation v4 remain unchanged. No public parameter, export, Help route, site example or packaged skill changes are needed.
- Duplicate index insertion and invalid predicate consumption still fail. Predicate CoreRuleError locations identify the original logical node. Deadline checks cover row/index and part positioning, survivor evaluation, stage transitions and final part restriction. Started groups do not retry ordinary execution; existing cancellation, cleanup and atomic publication remain authoritative.

## Independent behavior evidence

`tests/analysis/graph/test_fixed_selection.py` contains 16 default cases. They cover short/long chains, high selectivity, empty results, composite complete keys and predicate trees, independent Cell/part oracles, original part ordering, exact enabled/disabled terminal contracts and identity, identical predicate visit sequences, shared-node single execution, external/tag/type/policy/projection boundaries, consumed malformed keys/Cells, missing parts, deadlines, and prohibition of graph construction/derivation during execution.

`tests/analysis/numeric/test_fixed_selection_runtime.py` contains eight public Runtime cases. They cover retained original-state/coverage/Subject parts, members/summarize/rollup continuation, contract restoration and exact cache hits, malformed-key/predicate/timeout/cancel failure without retries or new publications, source-offline fresh-process result reads and new grouped execution, ordinary comparison endpoints, and preserved display/fixed-reference scope. The public numerical oracle is the independent J2 data: current values A=60, B=120, C=0, D=0; selected original state and rollup remain A=60. Ordered period difference selection independently expects A=-40 and D=0.

Passed:

- New default behavior: 16 passed.
- New public Runtime behavior: 8 passed, including a separate cold process with sources and current Semantic removed.
- Existing L1/source lowering plus initial new cases: 118 passed; the two subsequently added default cases have their own final 16-case run.
- Expanded affected Runtime: 28 passed, 52 non-Runtime cases deselected. This covers graph publication, the eight new public cases and three existing independent cold continuations; no selected case was skipped.
- `make test`: 4,796 passed, 1 skipped, exit 0.
- `make check-agent`: 4,796 passed, 1 skipped, exit 0; full lint/format/import contracts, typecheck (327 files) and API documentation build passed.
- All six touched/new Python files additionally pass explicit typing with `MYPY_FLAGS='--no-pretty --no-color-output --no-warn-unused-configs --follow-imports=silent'`. The normal implementation/fixture/probe scope also passed without this flag. Expanding typing into ordinary tests initially exposed eight existing diagnostics in the imported, unchanged `tests/shared_fixtures.py`; that dependency is excluded from this extra check's diagnostic scope and was not edited or claimed as newly qualified. Final focused lint, formatting and import contracts pass for all six files.

The default skip is the existing metadata channel-failure vector in `test_metadata_profiles_degrade_to_schema_when_statement_channel_fails`: its owner propagates failure to dispatcher fallback. A focused `-rs` run confirms five passed and one skipped; this is not an A2 execution skip. The final test-only annotation updates retain their focused 16 default and eight Runtime reruns; production kernels and measured source hashes are unchanged.

The expanded Runtime command was:

```sh
make runtime-test TESTS='tests/analysis/materialization/test_analysis_graph_publication.py tests/analysis/numeric/test_fixed_selection_runtime.py tests/analysis/graph/test_public_composition.py::test_public_exact_cold_continuations_after_source_and_models_are_deleted'
```

## Comparable local cost evidence

Reproduce with `.venv/bin/python -m devtools.fixed_selection_cost`. The complete repeated samples, work counts and memory peaks are saved in [cost data](2026-10-07-analysis-a2-cost.json), including implementation/input-builder SHA-256 bindings. The same graph, 20,000 int64 values, compound `(int64, string)` keys and selected methods are used for both arrangements. Disabling only the private grouping function retains ordinary stage execution; no public optimization flag is added.

Environment: macOS 26.6.2 arm64, Python 3.12.13, PyArrow 25.0.1. Each arrangement warms up once, then measures seven uninstrumented executions. No other test invocation ran during timing. Graph construction, input setup, source/Store/publication and cache hits are outside the timed local executor. Counts and one Python tracemalloc/Arrow proxy allocator peak measurement per arrangement are separate executions. These allocator peaks are not process RSS or summed simultaneous maxima.

The three-part cases retain Subject, original sum/count state and coverage. Timing observations do not qualify other types, remote methods or end-to-end publication latency.

| Chain | First survival | Parts | Ordinary median ms | Grouped median ms |
| --- | --- | --- | --- | --- |
| 2 | 90% | none | 133.712 | 95.245 |
| 2 | 90% | three | 197.321 | 135.296 |
| 2 | 20% | none | 83.811 | 66.865 |
| 2 | 20% | three | 125.971 | 103.595 |
| 8 | 90% | none | 511.556 | 323.493 |
| 8 | 90% | three | 767.736 | 353.204 |
| 8 | 20% | none | 165.732 | 116.344 |
| 8 | 20% | three | 249.657 | 152.148 |
| Empty 8 | 0% | three | 104.494 | 94.587 |
| Shared branch | 90% | none | 399.634 | 399.368 |

Deterministic selection-consumer work:

- Two-stage eligible chains: primary conversion 4 -> 1; complete-key index 2 -> 1; selection result construction 2 -> 1. With three parts, part-key conversion and part restriction each change 6 -> 3.
- Eight-stage eligible chains, including empty results: primary conversion 16 -> 1; index and result construction 8 -> 1; three-part conversion/restriction 24 -> 3.
- The shared graph does not fuse across the shared intermediate: primary conversion remains 6, indexes/results remain 3. Its timing includes the unchanged final difference consumer.
- Predicate row counts agree exactly. The 90% eight-stage chain visits `[20000, 18000, 17999, 17998, 17997, 17996, 17995, 17994]`; 20% visits `[20000, 4000, 3999, 3998, 3997, 3996, 3995, 3994]`; empty visits `[20000, 0, 0, 0, 0, 0, 0, 0]`. Every output is checked against independently generated keys and values.

Memory tradeoff:

- Without parts, measured Python peak falls from 15.35-16.45 MiB to 8.61-9.13 MiB.
- With three parts, keeping original part-key positioning until the terminal boundary increases Python peak from 15.35-16.91 MiB to 18.39 MiB. This implementation does not claim a uniform Python-memory reduction.
- Arrow allocation peak falls in every eligible nonempty case; for the eight-stage 90% three-part sample it changes from 14,961.25 KiB to 2,079.81 KiB. Empty changes from 7.5 KiB to 0.375 KiB. Shared-branch peaks remain unchanged.

There is no percentage performance promise. The cost exit is actual eliminated repeated conversions/indexes/results with preserved sequential predicate work, not logical node reduction.

## Qualification limits

Float, Decimal, Duration, external/tag predicates and specialized scope transports gain no fusion qualification. Source lowering and physical implementation registrations were not changed. Full Runtime, release, wheel and remote backend qualification were not run. A3 state-kernel convergence and L8 remain unimplemented. Existing A1 baseline limits retain their independent owner.
