# Analysis DSL S1 implementation record

Date: 2026-09-24. Branch: `panda`.

This is the versioned evidence location for the private S1 implementation. The
working plan was introduced in commit `46a01b767d2cc93a97ca1f513989a8b1029acc95`
and removed from the tracked tree by `ed9d4b1af3` when working plans were
ignored. The [S0 acceptance snapshot](s0-acceptance.md) is preserved beside
this record. Neither record is public DSL or Runtime acceptance.

## W1 baseline and review repairs

W1 code baseline: `e372827b2733925c4466f68a9ff86229a74e2974`. The
private J1 construction chain and real Semantic declarations were committed
there. The independent review reported 20 J1/fixture and 319 Semantic tests
passing on that commit; those numbers are reviewer evidence, not a new run in
this record.

During W2, J1 now checks any explicit authored Null and empty policy against
its admitted graph rule. The builder-backed `ms.aggregate(..., agg="sum")`
derives ignore-Null and empty-Null from its graph because that builder does
not accept separate value-policy arguments. The three previously missing
policy constructor Help targets resolve by string and callable. Tests cover
the additive-all, authored Null, and authored empty-policy rejection gates.
Two stale additivity repair descriptions were corrected in the Semantic
constraint and object-model owners.

## W2 private route evidence

The private J1 root uses actual Dataset row and row-set contracts, a placement
check, Ibis lowering and a DuckDB batch producer. The pandas route consumes
validated source output or an exhausted, receipt-checked category read or
coordinate-free Revenue observation. It preserves explicit keys, Cells, and
sum/non-null-count/row-count state. Source and local methods share versioned
J1 method qualifications. No public API or packaged skill was activated.

Observed checks on this working tree:

| Layer | Command | Result |
| --- | --- | --- |
| J1, exchange, execution identity, Semantic Help/registry | `make test TESTS='tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_execution_identity.py tests/test_semantic_live_help.py tests/test_semantic_live_registry.py'` | 117 passed |
| Touched Python source typing | `make typecheck TYPECHECK_TARGETS='marivo/analysis/compiler/dsl_j1_source.py marivo/analysis/compiler/placement.py marivo/analysis/materialization/contracts.py marivo/analysis/materialization/dsl_j1_receipt.py marivo/analysis/materialization/local_stage.py marivo/analysis/materialization/reads.py marivo/analysis/materialization/source_stage.py marivo/analysis/observation/dsl_j1.py marivo/analysis/operators/dsl_j1_contracts.py marivo/analysis/operators/dsl_j1_values.py marivo/analysis/operators/registry.py marivo/semantic/_capabilities/registry.py marivo/semantic/constraints.py'` | No issues in 13 source files |
| Touched lint and import contracts | `make lint-agent LINT_TARGETS='marivo/analysis/compiler/dsl_j1_source.py marivo/analysis/compiler/placement.py marivo/analysis/materialization/contracts.py marivo/analysis/materialization/dsl_j1_receipt.py marivo/analysis/materialization/local_stage.py marivo/analysis/materialization/reads.py marivo/analysis/materialization/source_stage.py marivo/analysis/observation/dsl_j1.py marivo/analysis/operators/dsl_j1_contracts.py marivo/analysis/operators/dsl_j1_values.py marivo/analysis/operators/registry.py marivo/semantic/_dsl_authoring.py marivo/semantic/_capabilities/registry.py marivo/semantic/constraints.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_semantic_live_help.py'` | All checks and import contracts passed |
| Broad default gate | `make check-agent` | Ruff/import contracts passed; mypy 367 source files passed; 5093 passed, 4 skipped; API docs built |

`tests/test_analysis_dsl_j1_source.py` pins J1's independent oracle values:
total 1000; Region east 600, south 400, west Null; selected east's Channel
web 450 and mobile 150. It compares direct and read-based Region grouping,
source and pandas state, exact receipt continuation without DuckDB, empty
current-row policies, and rejection of Null classification, missing or
duplicate keys, int64 overflow, nonfinite values, unqualified backend and
physical type. The broad gate is bounded default-suite evidence; it does not
run release-only Runtime or real Agent acceptance.

W2 does not publish a successful Artifact, persist a contribution-coordinate
part, prove cold recovery, or establish the W4 execution-identity protocol.
Those remain W3/W4 obligations. A final S1 acceptance record will need the
committed W2 code SHA and the actual W3/W4 results.
