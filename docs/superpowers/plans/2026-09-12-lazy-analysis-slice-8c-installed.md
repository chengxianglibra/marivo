# Slice 8c: Installed Dataset surface verification

Date: 2026-09-12

Status: **Slice 8c complete; parent Slice 8 closed**. Slice 9 and release remain pending.

## Parent milestone and prerequisite units

Slice 8c follows accepted Slice 8b at commit
`b4ed87e15ee9f5960b0820cf17b731e444a0a829`. Its 1,183 files match the
8b final manifest `77453b46a9a20a6b328401c8c9eaad1a6f356829946b9d78fba6aa379bf90f56`;
all 20 handoff attachments were verified before changes.

## User-visible or runtime outcome

The non-editable installed distribution exposes the same single Dataset algebra,
Help topology, Store generation and current examples as the accepted source switch.

## Frozen contract owners consumed

The parent public-cutover plan's Slice 8 contract, accepted Dataset Core,
Observation Model, Materialization Runtime, typed operators and domain designs;
current analysis and agent-result specs; repository test-fixture skill.

## Exact method, backend, storage, and fixture scope

Python 3.12 on the current host; real DuckDB datasource and local Parquet results.
Reuse the independent public/export/Help/protocol tests, native disclosure example
fixtures and bilingual current-doc examples. A separate public comparison journey
uses three processes for production, source-offline continuation and exact recovery.
The installed subprocesses never receive the source checkout on their import path.

## Exact files owned

- `tests/test_analysis_runtime_wheel.py`: release-marked distribution acceptance.
- `tests/installed_wheel_probe.py`: installed-origin guard and public cold journey.
- `tests/test_unified_help.py`: resolve packaged skill input from the imported package.
- This record, its precise `.gitignore` exception and parent status/gate links.

## Shared seams changed

Replace the existing eager wheel smoke with current Dataset acceptance; retain
the `make release-test` selector, serial release marker and existing CI route. The
planning statement that this module was missing was incorrect: it existed at the
baseline but still expected eager `observe()` and `artifact.ref` behavior. Preserve
its graph, revalidation, Finding-count and packaged-skill assertions under current
contracts. Do not alter
shared fixture semantics or the default Runtime selection.

## Public additions

None.

## Public removals

None beyond verifying the accepted 8b removal ledger.

## Persistence changes

None. Temporary projects only; reject generations 0, 2 and 4 without mutation.

## Implementation order

Freeze baseline; implement installed checks; build/check distributions; run focused
wheel acceptance; repair failures in their owner; rebuild; run release-test and
check-agent; freeze evidence and close the parent Slice 8 gate.

## Focused positive tests

`tests/test_analysis_runtime_wheel.py` stages existing independent contract tests
and their imported fixture closure outside the repository. It checks archive
contents, installed module/metadata origin, all exports and Help, Dataset states,
native and current EN/ZH examples, CLI, and separate-process public recovery.

## Adjacent negative tests

Existing removed-path and Store-generation rejection tests execute against the
installed wheel. An injected source import must fail the same installed-origin
guard used by positive tests. Package membership matches current owned source
files and excludes retired implementations and bytecode.

## Failure injections

Foreign import location; old Store generation; unavailable origin database after
production. Preserve failed gate logs and rebuild after implementation repairs.

## Real runtime journey

Construct current/baseline aggregate Metrics and compare, execute a Delta, inspect
Run/Artifact/Evidence/Findings/graph, disconnect the source, then read and transform
the retained result in another process. A third process reconstructs the identical
definition and recovers the same Artifact without source work or another Run.

## Capability-to-acceptance row and evidence locations

Owns the installed-facade consumer for 4d and installed switch row for 8a-8c.
Evidence: `evidence/slice-8c/2026-09-12-installed/`, including baseline, commands,
logs, package hashes, dependency versions, origins and terminal runtime identities.

## Disclosure updates

Update this record and the parent cutover status only after passing gates. Repair
current documentation only if an installed test demonstrates drift.

## Explicitly deferred contracts

Slice 9a full distribution/platform acceptance; 9b backend/economics matrix;
9c full adversarial acceptance; 9d real-Agent journeys. No release, commit or push.

## Exit gate

Fresh installed checks, `make release-test`, and `make check-agent` pass on one
fingerprinted candidate; prior evidence remains unchanged. Final package bytes
and status-documentation additions are recorded separately. Slice 9 stays pending.

## Completed implementation and fresh acceptance

The accepted test candidate fingerprint is
`dc32c6824862a6ba5265fab26e6b1ea39b4ebdebb65274ba934414fa0f359231`,
based on unchanged HEAD `b4ed87e15ee9f5960b0820cf17b731e444a0a829` plus this
task's test and record changes. Every accepted gate's before/after file manifest
matches this fingerprint. No library implementation, public API, persisted schema,
current site content or packaged skill changed.

| Gate | Fresh result |
| --- | --- |
| `make pypi-build pypi-check` | sdist and wheel built; metadata and package-content checks passed |
| Installed contract selection | 361 passed; 100 ordered exports/native bindings and callable signatures/Help match the source; nine family state pairs checked |
| Installed Runtime examples | Five tests passed, including native examples and current bilingual workflow, Evidence and semantic tutorial execution |
| Public cold journey | Three distinct processes; production, offline retained rank/limit, then exact-definition recovery; Run counts 1, 2, 2 |
| Complete `make release-test` | 29 passed, including both installer paths, the installed wheel gate and Help environment checks |
| `make check-agent` | 4,824 passed; 332 source files typed; formatting, lint, import contracts and API docs passed |
| New test-module typing | Both new/reworked modules passed with explicit namespace-package bases |
| Installed negative audit | All 46 retired exports and six retired packages absent |
| Current disclosure and old schema audit | 376 current files clean; neither retired persistence schema appears in wheel code |

The nested installed counts overlap the outer release gate and are not added to
it. Runtime-marked tests are selected separately from the pure contracts; their
counterpart deselections are marker routing, not skipped acceptance cases.
The existing standalone typing diagnostics for `test_unified_help.py` were
compared with its HEAD version using `--shadow-file` and are byte-identical;
the repository's required broad typing gate passes.

The subprocess venv installs the actual wheel and independently installed,
version-constrained dependencies, with no editable Marivo or source-path injection.
The origin guard verifies every loaded Marivo module, the single installed
distribution and its wheel SHA-256 before and after tests. Injecting the source
checkout fails that same guard. The final contract process inspected 276 loaded
Marivo modules; both archives match all 328 owned package files.

The retained Delta is
`artifact_44fde5e170984e8aa63d523f291746be`, produced by
`run_47cb4015a1fa96d0d835ce6e`. Its evidence digest is
`17e7d5e6a8e4c8efe5826574a0ace5ced325a90a64d216405ba9dc278e479d40`.
Current/baseline/delta rows are 30/12/18. All three processes read the same
Artifact, producing Run and Findings; integrity is valid and storage readable.
After the source database is renamed, downstream rank/limit uses retained
Parquet while datasource resolution is explicitly forbidden. Exact reconstruction
then emits no statements and adds no Run. A consumed Artifact is no longer a
global graph head; the focused read preserves that current topology.

## Repairs and superseded attempts

The old installed smoke still assumed eager observation and Frame-style reads.
It was replaced with the current independent tests and public cold journey while
preserving its graph, Findings, revalidation and packaged-skill checks. The only
existing shared test edit resolves its skill path from `marivo.__file__`.

The first build failed because the configured Tsinghua package mirror terminated
TLS. Subsequent commands used official PyPI through a per-command environment
override; no machine configuration or certificate verification changed. Harness
iteration corrected an assumed `tests/__init__.py`, distinguished retained native
Parquet statements from origin queries, and made graph-head assertions reflect
the completed downstream Run. All failed logs remain recorded. In particular,
`installed-final` is a superseded failure, not the final accepted gate; only
`build-accepted`, `installed-accepted`, `release-accepted`, `check-accepted` and
`added-tests-typing` are accepted receipts.

## Distribution and Slice 9 handoff

The retained `0.5.3.dev0` distribution from the final release gate has:

- wheel SHA-256: `ca16632e1943616b9768aea4c9054f2a33d43f8f6bfe66c47c0cee630b6442fb`;
- sdist SHA-256: `11971e7c0e113ec6070d7f85092b3cb787d3a75027a1380b85c3c22f72b89958`.

The [final handoff](../../../evidence/slice-8c/2026-09-12-installed/handoff-final.json)
binds the tested candidate, final source manifest, complete working-tree patch,
distribution copies, accepted receipts and terminal evidence. Its artifact index
hashes the logs, environment versions, surface comparison and negative audits.
Manifest fingerprints are SHA-256 over sorted `path`/`sha256` records serialized
as JSON with `sort_keys=True` and separators `(',', ':')`; the existing
`CLAUDE.md -> AGENTS.md` symlink is checked separately.

Only this completion record and parent status/selector corrections follow the
accepted tests. Test code and package contents remain unchanged. All 616 prior
evidence files match their initial hashes. No commit, push, public publication,
user-global Marivo installation or historical Session cleanup occurred. Slice 9
retains full platform/backend/adversarial/real-Agent and release-readiness gates.
