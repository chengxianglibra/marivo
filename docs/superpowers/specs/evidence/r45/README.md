# R4.5 workspace acceptance evidence

This evidence covers the uncommitted workspace on base commit
`9c5e3577ddd6cc4df977f4524cae9bb669e81c7b`. It does not certify an installed wheel,
real Agent execution, additional backends or R5–R9 methods. R4.6 owns the same-wheel
isolated installation gate. `manifest.json` records code/test input hashes and
log hashes; the source-tree digest includes untracked implementation files.

## Commands and ownership

- `make check-agent`: formatting, lint, import boundaries, positive typing
  (`marivo tests/typing`), full default tests and API documentation.
- `make runtime-test TESTS='tests/test_analysis_dsl_public.py tests/test_analysis_dsl_r45_migration.py tests/test_analysis_graph_preflight_r45.py tests/test_analysis_graph_publication_r44.py tests/test_analysis_lowering_r34.py'`:
  qualified public and underlying graph execution, atomicity and cold recovery.
- `make test TESTS='tests/test_analysis_dsl_public_static.py tests/test_analysis_dsl_p2_disclosure.py tests/test_analysis_help_resolution.py tests/test_agent_api_drift.py tests/test_public_surface.py tests/test_cli.py tests/test_analysis_session_helpers.py'`:
  negative typing, Help reachability/budgets/drift, exports and CLI.
- `npm run build` in `site`: English/Chinese latest examples and site output.
- `make lint-agent`, disclosure refresh and `git diff --check`: final small
  naming/guidance cleanup verification.

## Independent expectations and scope

| Cell | Current evidence owner | Expected result |
| --- | --- | --- |
| V01 | preflight_r45, r45_migration, graph_publication_r44 | Schema-only preflight; mixed, cross-session and unqualified shapes rejected before business rows/Run. |
| V02 | preflight_r45, dsl_public, graph_publication_r44 | New source evaluations; explicit shared nodes execute once; independent roots remain distinct. |
| V03 | graph_publication_r44, r45_migration | Ordered exact fixed key, verified hit without Run; binding/receipt mutation cannot hit. |
| V04–V05 | lowering_r34, preflight_r45, graph_publication_r44 | Full keyed/Cell oracle, exhausted checks, typed failures and no publication on failure. |
| V06–V07 | graph_publication_r44 | Fault/process-exit and two-writer atomicity; reconcile original Run without replay. |
| V08–V10 | dsl_public, r45_migration, preflight_r45 | J1 empty contribution; J2 ordered Difference and selected-member re-observation; J3 complete tuple ratio vs row statistic; J4 independently ranked keyed pairs and fixed coefficient continuation. New child process forbids source/DuckDB/Semantic loading after deleting sources and models. |
| V09 | graph_publication_r44, r45_migration, zero_init | Receipt, part, snapshot, state version and binding corruption refuse consistently; old generation remains unchanged. |
| V11 | disclosure/default/type/site logs | Existing public types/exports; exact-state K; native Help/CLI and bilingual examples. |
| V12 | R4.6 | Not run: isolated same-wheel installation. Product source scan in this workspace has no scenario identifiers or deleted scenario codec imports. |

DuckDB and local Parquet journey routes have separate executions. The Parquet
matrix covers J1/J2/J3/J4 plus tied Spearman ranks. Physical qualification remains
exact: string/int64 identity, registered finite int64/float64 operations, qualified
window roles, one/two direct string coordinates; int64 Difference only.
Store generation is 7; graph exchange/descriptor/receipt/snapshot, run input and
method-state envelopes use their frozen v1 contracts. No source-free recovery
uses current semantic definitions to fill absent state.

## Replaced test migration

| Removed v6 file/family | Active owner |
| --- | --- |
| dsl_execution_identity | graph_r33, graph_runtime_r42, graph_publication_r44 exact key and sharing tests |
| dsl_j1_construction/source/runtime/artifact | graph_preflight_r45, dsl_public, dsl_r45_migration; lower-level lowering/publication fault tests |
| dsl_s2_p1/p2/p3 | dsl_public J2; r45_migration ordered endpoints, predicates, empty rows, ownership and cold continuation |
| dsl_s3_p1/p2/p3/p4 | dsl_public J3/J4; r45_migration complete tuple/original ratio/pair rank/cold coefficient tests |
| Semantic dependency ownership assertions in old scenario tests | dsl_semantic_authority |
| old public generic Dataset execution positives | explicit pre-Run R5–R9 refusal in lazy_public_session; qualified public recovery in r45_migration |

This is a contract migration, not a claim that removed private tests ran unchanged.
No new skips hide replaced behavior. Existing default-suite skips remain separate.
Private generic v6 R5 harness code is not selectable by public Session entrypoints;
its old concurrency failures remain the separately recorded R5 handoff in the main
ledger and `evidence/r44/baseline-concurrency.log`. That suite was not rerun here.

Earlier intermediate failures (stale v6 imports/expectations, an API heading and a
public coordinate guard placed in a private helper) were repaired before final
gates. The final logs are the acceptance evidence, not the intermediate results.

Log trailing whitespace was normalized for repository hooks. The manifest retains
raw pre-normalization hashes separately from the committed log hashes.
