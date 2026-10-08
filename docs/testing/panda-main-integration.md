# Panda and main integration candidate

Status: validated local integration of the frozen inputs below. No remote push.

## Frozen inputs and strategy

- First parent: main `86055a2ef9f6b8ed1b70098497c257e794e0df0c`.
- Second parent: panda `109c499f99dbaa61f9d2258351c390da76bd3497`.
- Merge base: `d3af482c02d4ff1f5a986c7e23c505a6bfbd093d`.
- Unique commits: 35 on main, 252 on panda.

Panda supplies the Analysis architecture, Source adapters, current Help and
lifecycle contracts. Main's independent capabilities are adapted to those
owners. The merge preserves both histories without replaying panda's commits
or restoring retired eager Analysis APIs. Physical Source facts and Entity
output facts remain distinct.

Local backup refs preserve both input tips. Work occurs on
`codex/panda-main-integration`. No remote push is part of this change.

## Main-only commit disposition

"Adapted" describes the candidate implementation, not release or full Runtime
qualification. Validation below owns the actual evidence boundary.

| Commit | Disposition in the candidate |
| --- | --- |
| b888e64e5b | Retain 0.5.3 release history; development version is 0.6.0.dev0. |
| 7614d0740b | Superseded development bump; retain history. |
| f93493f32b | Adapted typed SemanticDefinition and direct dependency projection. |
| e4366042e5 | Adapted host credential scope, captured resolver, typed repair and redaction. |
| 0337b3b5f2 | Adapted central backend construction, operation ownership and leases; preserve panda SourceSession controls. |
| ca4d0fe59f | Adapted normalized definition display and temporal description to explicit panda policies. |
| 9532d1896e | Panda's current instructions and workflow skills own the merged surface; no skill edits added. |
| 15f4165cc3 | Retain 0.5.4 release history. |
| 507633ad80 | Panda's current JSON typing and lint replace the earlier implementation. |
| 61d171556f | Panda's pinned sqlglot 30.8.0 owns dependency compatibility. |
| de5eef4c57 | Superseded development bump; retain history. |
| 5a73110283 | Adapted reserved default in-memory DuckDB, pure discovery and per-connection memory. |
| bb0e9508b1 | Panda's current typed continuation and terminal boundaries own guidance; raw SQL stays terminal. |
| 8cc46198de | Adapted complete raw-query rows, SQL-authored size control and independent display budgets. |
| c86447eeff | Retain 0.5.5 release history. |
| 97e049e626 | Superseded development bump; retain history. |
| 3e25c21c4c | Adapted precise direct/decorator Entity dispatch and restricted one-Source expressions. |
| cdc3c2c35d | Adapted one shared Entity output materialization boundary. |
| 84bf75d0c9 | Retain expression Entity design; current owning specs describe panda adaptation. |
| 6532af0410 | Adapted output schema and health checks; actual input types come from physical binding. |
| 32cf0f0550 | Panda's current shared fixtures and strict typing own test setup. |
| 2eed9f28d5 | Adapted independent output-grain and downstream consumer tests, including current source/fixed/cold journey. |
| be4d051104 | Adapted native Help, API reference, current English/Chinese docs and Entity Details. |
| ec40fc3e22 | Adapted finite literal preservation in structural and normalized expression descriptions. |
| 2e88cd1a31 | Adapted authoring Help facts under panda's native registry and bounded routes. |
| 2e25b01069 | Panda's current Analysis disclosure/continuation contracts supersede eager API guidance. |
| aef464c65b | Panda's bounded retained result rendering owns inspection tables and discovery. |
| 372427746c | Retain 0.5.6 release history. |
| 248d2f380f | Superseded development bump; retain history. |
| 971d31a510 | Retain cached function AST/source syntax and catalog grouping indexes; no legacy catalog/session introduced. |
| fab2761481 | Adapted off/on/full capture policy; execution logging remains independent. |
| 7e75d599d1 | Adapted immutable Session domain scope, selected semantic loading and cold metadata recovery. |
| bea51e0a1d | Adapted once-per-operation telemetry emission decision. |
| ffef6bb635 | Retain 0.5.7 release history. |
| 86055a2ef9 | Retain final 0.6.0.dev0 development version. |

## Schema and public behavior changes

Expression Entity keys, version coordinates and downstream fields use output
schema. Source qualification and Run input verification use the original
physical descriptor and schema. Body identity participates in dependency
fingerprints. Fixed Artifacts continue without source or model reconstruction.
A compiled expression alone does not qualify every backend/Source/method cell.

Default datasource discovery includes `default`. Its definition is fresh and
it cannot be registered, replaced or removed. Its connection memory is not
shared or persisted.

An explicit host resolver never falls back to environment/cache values, caches
injected secrets or exposes them in Marivo-owned diagnostics. Structured owner
errors retain their original repair contract across backend boundaries. Raw
backend calls outside a Marivo-owned operation retain caller ownership.

Raw SQL has no `limit`, `requested_limit` or `is_truncated` API. It submits SQL
verbatim, retains all returned query rows in client memory and keeps bounded
terminal display. Author SQL filters, aggregation and LIMIT to control size.

Session domains add canonical metadata to Store generation 9. The selected
current generation alone is read. Earlier directories remain untouched;
there is no migration, reconstruction or compatibility read. Graph, descriptor,
receipt and method-state codec versions remain independent of Store generation.

## Verification record

- `make check-agent` passed: lint, import contracts, typing of 343 modules,
  5,362 default tests passed, one skipped, and strict API documentation build.
- Focused Store-9 Runtime checks passed three tests: expression Entity Parquet
  execution at output grain, changed-body execution and fingerprint, offline
  fixed reduction, fresh-process recovery, and ordinary direct-Entity recovery.
- The public composition Runtime regression passed 26 tests, covering
  multi-root ratios, source/fixed parity, selected rows, local recovery and
  separate Parquet source journeys.
- Session domain canonicalization, immutable selection, cold metadata recovery
  and prior-generation byte preservation passed in the default gate.
- The bilingual documentation site built 363 pages. Its checks verified 308
  original routes, 48 latest usage pages and 351 internal route/anchor targets.
- Isolated sdist and wheel builds and archive-content checks passed for
  0.6.0.dev0. Wheel SHA-256:
  `bd96d8e8d30bd46a39f328b678c87bb6b4505d1a4b21bb629a20f241284aa8aa`.
- A fresh noneditable installation of that exact wheel passed default discovery,
  complete 137-row raw SQL, scoped expression-Entity Parquet execution, offline
  fixed reduction and a second process's source-free recovery. The package
  origin was inside the isolated environment and `direct_url.json` bound the
  installed archive hash. This is a bounded package smoke check.
- Source checks explicitly set `PYTHONPATH` to the integration checkout because
  its shared development environment's editable install points to the original
  panda checkout. The installed-wheel probe used an isolated environment and
  no repository import path.
- Full Runtime acceptance, release qualification, the complete remote-backend
  matrix and real-Agent qualification were not run or claimed.

Historical main acceptance documents and panda's existing acceptance records
retain their original identities. They do not qualify this changed candidate.
