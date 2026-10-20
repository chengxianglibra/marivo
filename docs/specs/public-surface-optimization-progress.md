# Public surface optimization progress

## Baseline and scope

- Baseline: current worktree at `c2cef31ec909d2205726d7d743ca14665818ed8a`, including the preexisting semantic and analysis changes.
- Preserve all unrelated edits. Do not edit AGENTS.md or the packaged workflow skills.
- Optimize only candidates verified against current contracts and consumers. Keep persisted data, numerical semantics and fingerprints stable.
- No commit or release is included.

## Work and acceptance

| ID | Owner | Work | Acceptance | Status |
| --- | --- | --- | --- | --- |
| P01 | datasource agent | Privatize spec conversion and remove identity source conversion methods | Consumer migration, focused tests, typing and lint | Implemented; 45 tests, typing and lint passed |
| P02 | semantic agent | Verify redundant entry aliases and tuple conversion; assess construction boundaries | Current Help/docs and consumer parity; focused tests, typing and lint | 240 tests, typing and lint passed; independent review passed |
| P03 | analysis agent | Privatize internal population definitions and predicate conditions; assess membership disclosure | Internal consumers retain behavior, public Help excludes internal IR; focused tests, typing and lint | 196 tests, typing, lint and docs alignment passed |
| P04 | coordinator | Cross-surface verification and unified acceptance | Broad check-agent, independent review, no unresolved P2+ findings | Complete |

## Candidate decisions

- Remove four source IR identity `to_ir()` methods; make spec conversion `_to_ir`. The removed methods were not disclosed in owning Help/docs, and conversion has only internal consumers.
- Retain `DatasourceSummary.semantic_id`: current Help explicitly owns this stable discovery identity; redundant storage alone does not invalidate the contract.
- Remove CatalogEntry identity aliases in favor of `.ref` and remove JoinKey tuple conversion in favor of its typed fields. Migrate production consumers and current disclosure together.
- Retain public Catalog result types. Their construction boundary is documented; no guarded constructors or additional factories are introduced.
- Make Population capture `_definition` and predicate capture `_condition` private. Preserve serialized source-fact keys and fingerprint the same inner definitions.
- Retain `Population.memberships`: current bounded show output does not disclose its allocation tuple, so privatizing it would remove available captured facts.

## Review and failures

At most two formal review rounds. Preserve failure evidence and distinguish baseline failures from regressions.

- Round 1 datasource review by semantic agent: no findings. Conversion bodies, source serialization and parser round trips retain their contracts; no stale `to_ir` consumers or Help references remain.
- Coordinator review of analysis production delta: inner IR and serialized source-fact keys are unchanged; access names alone become private. Final disclosure/test review is pending source freeze.
- A concurrent preexisting Grouping task updated its progress plan during this work. Preserve that update and validate the combined current tree; do not attribute its earlier gate evidence to this task.
- Site content verification passed: 381 required files. `git diff --check` passed.
- Round 1 semantic review by datasource agent: no actionable P2+ findings. Live Help, identity cards, migrated typed-field consumers and current EN/ZH documentation were checked.
- Round 1 coordinator analysis review: no findings in the production delta or disclosure tests. Former raw-IR Help targets reject and the public field sets are pinned.
- The first analysis focused run had 193 passes and 3 failures from the same stale `operation.definition` test access. After migrating it, the same focused scope passed all 196 tests. Original output remains in the analysis agent tool transcript.
- Unified source freeze: 300 production Python modules, hashes retained in `/tmp/marivo-public-surface-20261020-c2cef31e/source-freeze.json`. Unrestricted `make check-agent` is running against this tree.
- Before/after dependency-free probes matched: Deferred recipe `7568133cc15b29e7c5b9b0a7973f7bfef6de7cfdcd0a051103c2e567aab34db5`; Population computation `dd418c5e51bfa3bf0b0c4786c6d11fc5cc5e2be756291089ae8546dd8790d710`.
- Coordinator live Help probe: Population, PopulationPredicate, CatalogEntry and JoinKey type targets resolve; the removed Population definition, predicate condition and JoinKey tuple-conversion targets reject. Latest site content verification passed again after doc updates.
- First unrestricted gate: lint, imports and typing passed; default tests had 4772 passes and one failure in `test_layered_loading_injects_default_once`. Two assertions still read the removed CatalogEntry `.name` alias. Migrated them to `.ref.name`; all 15 tests in that datasource module then passed. Raw gate output is retained in `/tmp/marivo-public-surface-20261020-c2cef31e/check-agent.log`.
- Production source remains frozen. A second unrestricted `make check-agent` is running; bounded formal review round 2 covers the final test-consumer repair.

## Final acceptance

- Second unrestricted `make check-agent` passed: formatting/lint, import contracts, typing (305 files), all 4773 default tests (635 warnings, 173.43 seconds), and API documentation build. Raw output: `/tmp/marivo-public-surface-20261020-c2cef31e/check-agent-final.log`.
- Round 2 independent review confirmed the `.ref.name` consumer repair preserves the original assertion. No unresolved P2+ findings remain.
- All 300 production Python source hashes still match the unified freeze after acceptance. Final diff checks and site content validation (381 required files) passed.
- Before/after fingerprint probes and default recovery/execution tests provide local evidence. External Runtime and release acceptance were not run.
- Existing dirty changes and concurrent Grouping progress records were preserved. No AGENTS.md, packaged skill, commit or release changes were made.
