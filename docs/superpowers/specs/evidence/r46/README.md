# R4.6 installed graph acceptance

Status: completed for the existing local J1–J4 qualification. Final installed
gate: **1 passed** in 312.03 seconds; **444 default + 93 Runtime tests passed**,
plus **24 journey phase processes**. Source broad gate: **5363 passed / 19 skipped**,
400 product/positive-typing files, lint/import/API docs passed. Site: **321 pages**,
0 errors / 0 warnings. Typed probe/example modules: **4 passed**; evidence tools:
**2 passed**. Existing skips and R5 failures are excluded, not accepted.

Final wheel SHA-256:
`24c3caa6d09c21f97f20662f300c04d77d64cb35615dc2063e5a812b9db14ae3`.
`wheel-final-verified.log` and the final `installed-wheel/` reports are authoritative;
other wheel logs retain superseded intermediate attempts. The installed pytest
runs each emit one harmless assert-rewrite warning because the origin hook loads
the probe before pytest's plugin rewrite; all origin assertions execute.

Baseline: clean `panda`, `fabd277a8d4510dce3973bd772f35175ea8c42e4`.
This directory records the resulting uncommitted workspace. No commit, push,
package publication, full release-check, MinIO or remote service was requested.

## Reproduce

Use the repository `.venv` and run from the repository root:

```sh
make test TESTS='tests/test_analysis_graph_r33.py tests/test_analysis_graph_runtime_r42.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_r45_migration.py tests/test_analysis_graph_publication_r44.py'
make runtime-test TESTS='tests/test_analysis_dsl_public.py tests/test_analysis_dsl_r45_migration.py tests/test_analysis_graph_preflight_r45.py tests/test_analysis_graph_publication_r44.py tests/test_analysis_lowering_r34.py tests/test_cutover_documentation_examples.py'
make check-agent
make pypi-build pypi-check
PIP_INDEX_URL=https://pypi.org/simple MARIVO_R46_EVIDENCE_DIR="$PWD/docs/superpowers/specs/evidence/r46" .venv/bin/pytest -n 0 -m release tests/test_analysis_runtime_wheel.py -q --tb=short
.venv/bin/python devtools/analysis_r46/scan.py docs/superpowers/specs/evidence/r46/residue-scan.json
.venv/bin/python devtools/analysis_r46/record.py
```

Run `npm run build` from `site/` for the bilingual documentation gate.
`typing-probes.log` additionally checks the four typed installation/probe/example
modules with `.venv/bin/mypy --explicit-package-bases --follow-imports=silent`;
the broad gate independently checks product code and positive typing examples.

The release-marked wheel test creates one isolated environment outside the
checkout, installs one non-editable candidate wheel with pinned local dependency
constraints, runs `pip check`, and checks all loaded Marivo module paths against
that environment. The source-poisoning negative control must fail. A test-only environment-local `.pth`
site hook records package authority at process startup and normal exit; crash
workers still have startup records. It adds only the staged test directory,
which contains no `marivo` source package. It is never shipped in the wheel.

`installed-wheel/inputs.json` hashes the actual staged scripts, fixtures and
bilingual examples; `archives.json` checks all package source/skill bytes in both
wheel and sdist against the candidate checkout. `commands.json` records exact
commands, cwd, duration and exit status, including the expected negative control.
The two JUnit files list individual installed test outcomes. The reports preserve
`direct_url.json`, dependencies, origin checks and exact Run/Artifact references.
Temporary projects and the virtualenv are disposable; reports and rerunnable
fixtures are the durable evidence. Wheel binaries remain in ignored `dist/pypi/`.

## Independent expectations and matrix

`matrix.json` maps concrete installed test nodes to every cell. Source and private
unit evidence remain distinguishable from public and installed execution.

| Cell | Required observation and active owner |
| --- | --- |
| V01 | `members_schema_preflight`, `incompatible_inputs`, `mixed_and_foreign_session`, `unqualified_method`: no business rows or Run for rejected construction/admission. |
| V02 | `public_source_repeats`, `window_composition`, `explicit_sharing`, `each_invocation`: fresh source identity, real source/stage counts, same-node sharing and independent-node distinction. |
| V03 | `fixed_key_*`, `source_roundtrip`, `equal_values_from_distinct`, `valid_parquet_with_changed`: ordered exact keys; verified hit without Run; changed bindings/receipts/state cannot hit. |
| V04 | `four_cells`, `r43_source_parquet`, `r43_empty_three`, `real_ibis_exchange_retains`, `governed_parquet`: empty/schema/Cell and keyed-state vectors, large integer/Decimal/time precision at their existing exchange qualification. |
| V05 | `r43_checked_stream`, `checks_keep_origin`, `inconsistent_live_method`, reader/native write failures: no false completed evidence, resource leak, fallback or publication. |
| V06 | Publication faults, lost acknowledgement, unknown/contradictory commit and process-exit tests: original-Run reconciliation without replay or unrelated cleanup. |
| V07 | `two_process_writers`: one fixed-key success, busy contender, released guard and exact subsequent hit. |
| V08 | Installed three-process probes plus cold recovery/fixed endpoint tests: exact references, rows, full descriptor/parts, contract equality and actual retained continuations without sources. |
| V09 | Strict descriptor, old project, receipt/part/snapshot/state/binding corruption: explicit refusal with old state preserved and no repair from current models. |
| V10 | Public J1–J4, tuple union, component weighting, endpoints and separate Parquet tests; renamed installed probes use source facts, Fraction arithmetic and independent rank calculation. |
| V11 | Public export snapshot, Help resolution/budgets, native disclosure, CLI and bilingual executable examples in the installed suite; positive/negative typing and site/API builds in the source broad gate. |
| V12 | One final wheel, identical archive/source inventory, isolated imports, 24 journey phase processes and source/wheel residue scan. |

The standalone probes run each J1–J4 journey over native tables and local Parquet
using `operations.device/reading/sample`, renamed fields and `kWh`. Each journey
has separate producer, source-free continuation and recovery processes. Producer
files and Semantic models are deleted before continuation. New processes forbid
SourceSession construction, datasource acquisition, native DuckDB and Ibis
DuckDB connections, and Semantic loading before Session recovery.

Primary rows are checked against raw facts by full member/coordinate keys.
J1 retains the empty-contribution Null and verifies original rollup/count plus
fixed association. Current-row sum/mean require Defined Cells: the J1 Null is
not silently dropped. J2 verifies exact differences and cold compatible-endpoint
comparison, selection, member projection and summary. J3 independently computes
numerator/denominator ratios and distinguishes original rollup from current-row
mean; retained coordinate grouping runs after model deletion. J4 independently
ranks facts and checks coefficient selection/summary and pair counts. Existing
installed tests cover ties, Null pairing, insufficient/constant pairs, complete
tuple union, empty/undefined Cells and extra source-identity/fault cases.
Repeated cold continuations must recover exactly the previous output Artifacts
without extra Runs. These checks concern admitted calls and existing Cell
policies; they do not qualify every possible parameter or physical type.

## Residue and ownership review

`residue-scan.json` lists every selected token hit, including deferred code; it
does not equate a zero scenario-token count with proof of a single Runtime.
Actual installed shared-stage/read counts and codec-forbidden tests supply the
behavioral evidence. No scene-specific constructor, executor, codec, alias or
snapshot shim was introduced. The unused `_public_snapshot_text` helper was
removed after confirming it had no callers.

| Remaining private owner | Why it remains and public exclusion | Reopening condition |
| --- | --- | --- |
| `materialization/{store,admission,contracts,execution_key,dataset_execution}.py` | Private v6 Dataset harness and generic metadata/keys. Public Session factories select generation 7; `DatasetRuntime._execute` rejects Dataset execution at that generation before the generic executor. New graph publication/store use only `graph_source_execution_key` / `graph_fixed_execution_key` and the graph descriptor. Generic `analysis_exchange/v1` serves old BatchStream fixtures; it is not decoded as the graph protocol. Shared physical LocalReceipt/Parquet v1 is intentionally retained. | R5 methods must gain typed graph, receipt/state, recovery and physical qualification. No v6 fallback or double-read is authorized. |
| `compiler/source_admission.py`, `operators/registry.py` | Private generic source-route migration ledger, still called only by the deferred Dataset route. | Method-owning R5–R9 registration and native evidence. |
| Family publication/codec modules, including `lifecycle_publication.py` | Generic private Dataset harness consumers; Store generation guard blocks old writers from v7. J2 Difference/J4 Spearman use graph method state instead. | R6 comparison/attribution, R7 Event/Lifecycle and R8 discovery/forecast qualification and new-state recovery. |
| `datasets/_disclosure.py` | Public Dataset signatures remain for later phases, but Help now explicitly states Store 7 rejection and routes to `session.members`. The earlier R1 basic-route success claim was removed. | Owning phase may change disclosure only with implementation and independent tests. |
| `graph_exchange.py`, `graph_protocol.py`, `methods/semantics.py` | Remaining uses of “legacy” document a prohibition, not a consumer or compatibility route. | No migration action. |

Public latest Evidence now documents relation `show/contract` and producing Run
inspection instead of unavailable Dataset Findings; workflow Store references
are generation 7. Deferred monthly/mean examples are tested as structured R5
refusals with no Run. Their historical numeric execution is not counted as a
current pass. The accepted owning boundary is in
`docs/specs/analysis/session-state-and-runtime.md`, “R4.4 v7 publication and R4.5
public selection”; this package does not extend it.

## Failures, exclusions and handoff

The initial installed Runtime attempt failed because the staging import walker
missed `tests.graph_publication_runtime_worker`, named as a subprocess string
rather than imported. `initial-wheel-runtime-failure.log` preserves those five
failures; explicit staging fixes them. Only the final candidate reports are
acceptance evidence. `wheel-final.log` records an intermediate all-journey run
whose origin hook was shadowed by Homebrew's standard-library `sitecustomize`;
the final `.pth` hook has a startup self-check. `mirror-install-failure.log`
records the subsequent Tsinghua mirror TLS failure before tests. The final run
uses official PyPI with the same constraints and candidate wheel; these attempts
do not change product qualification. `wheel-final-verified.log` is the final gate.
Earlier local probe assumptions about summing Null Cells
were corrected to the existing finite-Defined policy, without changing algorithms.

The 19 existing default skips remain skipped with their prior owners/conditions
in the main ledger; no skip was added or counted as a pass. Historical 64-to-19
migration accounting remains owned by R1.6/R4.5. The private R5 concurrency
baseline's eight failures remain in `evidence/r44/baseline-concurrency.log`; this
package neither reruns nor clears that separate deferred route.

No real Agent, additional backend, hardware power-loss, remote-storage, capacity
or release qualification is claimed. R9 owns six-backend expansion; R10 owns
real-Agent capability-cluster acceptance. Scripts and static scans are bounded
local evidence only. AGENTS.md and packaged workflow skills are unchanged.
