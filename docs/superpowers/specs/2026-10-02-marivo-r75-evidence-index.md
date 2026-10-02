# R7.5 canonical Lifecycle History evidence

Baseline: `panda`, `8646ceb7bdda4f5eaa986df61a5481f3a1607aca`.
Status: complete within the bounded local R7.5 scope described here.
Date: 2026-10-02. The entry tree was clean: R7.4 had already been committed at
that baseline. No existing changes were removed. This implementation is uncommitted.

## Public and execution owners

`session.lifecycle.replay(model, *, population, window, seed, completeness=())`
returns LogicalHistoryResult; explicit execution returns MaterializedHistoryResult.
Model is an exact StateModel Ref, population is a required logical same-Session
Subject domain, and seed is from_inception(). Old PopulationInput, catalog entries,
fixed membership, omitted population and call-level order overrides are rejected.
Construction, snapshot and planning do not read business rows or allocate a Run.

`history.replay@v1` explicitly depends on both the complete member domain and
`occurrence.prepare@v1`. Each distinct modeled Event is captured once, using the
model's own business order. All source preparation precedes the registered local
scan of the same capture. The source route is ibis_python; fixed/cold reading uses
the existing Arrow/Parquet receipt owner. No new executor or public fixed replay
constructor is installed. Native source replay and remote backends are unqualified.

The scan consumes real inception through exclusive window.end and clips only the
reported intervals. Complete origin with no modeled trigger is NotStarted; complete
origin with triggers but no required inception fails atomically. Insufficient origin
or follow-up is Unknown/coverage_censored with the known prefix retained. Without
origin proof, observed triggers have unknown_origin disposition with no invented
initial state or interval. Beyond a known prefix, nonterminal facts retain
unknown_followup without inventing legal transitions. A proved absorbing terminal
prefix still justifies later invariant terminal violations; coverage remains
censored. Legal self-transitions and zero-duration intermediate transitions remain
in the full trace.
Illegal/terminal triggers retain their exact identities and keep state. Pre-inception
facts have their own disposition. Identity/declaration/physical order never orders
business triggers. Ambiguous same-time groups reject, including equal final states
with different violation identities. Only a terminal state already proved before
the group permits a set of invariant per-occurrence terminal violations.

## Retention and source-free validation

`canonical_history` and closed HistoryPart@v1 retain one row per complete Subject
key, including empty lists for no-event/no-interval Subjects. Primary classification,
trusted inception and known-through mirror the closed typed record. That record
retains all evaluations, legal transitions, violations, pre-inception dispositions,
raw interval boundary occurrences, clipped bounds/statuses and exact observed ticks.

Initial exchange, publication, public read and cold recovery verify model, Event,
Subject and input bindings, complete keys, schema/state/part versions, order groups,
trace dispositions and all projections, coverage and precision. Recovery validates
the saved trace rather than invoking origin replay. Int64 identity overflow and
noncanonical timestamp encodings that would silently lose precision are rejected.
Canonical coverage instants are UTC. The accepted R7.2 native UTC/us conversion and
original s/ms/us/ns/possible-loss disclosure are preserved; this is not ns-exact
source qualification.

Violations retain the existing graph.no_findings@v1 zero policy. Artifact, Evidence,
empty Findings and succeeded terminal use the existing atomic Store transaction.
The common 600-second deadline covers preparation, scan, validation and publication;
no row, memory or tie-width quota is added. Cancellation, timeout and transaction
failure remove only the failed Run's resources, and prior Artifacts remain readable.

## P10 physical cells and independent processes

The [qualification record](2026-10-02-marivo-r75-qualification.json) preserves all
270 original Q-R7-P10 requirement IDs, with every original profile requirement listed.
All 9 full key profiles and 10 time/source profiles pass source, fixed read and cold
read: 90 + 90 + 90 cells. Int64 keys exceed 2**53; composite keys are compared in full.
The 90 pytest producer cases compare the complete output against an independent
scalar oracle. Each launches a distinct fresh recovery process after removing the
source database/Parquet. That process forbids SourceSession, DuckDB, current Semantic
loading and both replay entry points. It verifies retained records, frame, state,
contract/show, zero Findings, unchanged Runs and no submitted statements.

F/C verify already published History. They do not grant a new fixed replay entry
or R7.6 view continuation. Original V10/V11 and later-phase requirements remain
listed with explicit deferred dispositions rather than being silently deleted.

## Executable evidence

| Requirement | Owning checks |
| --- | --- |
| V09 | Independent complete scalar output and 10 varied physical-row orders; pre-window inception, empty domain, no-event Subject, Unknown/NotStarted/known prefix, self/zero-duration, illegal/terminal/pre-inception and exclusive end; terminal-only ties and equal-final-state/different-violation rejection |
| V01/V02 | Exact model/Session/Subject/seed/input authority, frozen required signature, no business read/Run at construction, canonical graph snapshot, fixed+live rejection and model-owned order |
| V13 | Public source -> published History -> independent cold read in all 270 cells; source mutation causes reevaluation, explicit shared member dependency, every source batch precedes scan |
| V14 | Closed record/trace/transition/interval/class/coverage/key/order/encoding/precision corruption; missing/corrupt primary and required History receipts; independent cold cross-Artifact swap, binding, model, part and state-version tampering; no new Run and intact sibling Artifact |
| V15 | Artifact/Evidence/Findings/terminal/before-commit failures, cancellation and timeout preserve the previous History; affected R7.2 tests own cross-batch complete keys, exact issued/submitted SQL, stream cleanup and capture atomicity |
| V17 | Export/order snapshots, precise positive/negative typing, native Help/reachability/budgets, bounded result protocol and current contract; API docs and executed identical English/Chinese latest examples; R7.6 methods absent |
| Migration | Physical deletion and public registry retirement checks; pure internal reducer contracts/numeric oracles remain with an explicit R7.6 exit owner |

## Retirement and deferred declarations

M07: make_replay and LazyLifecycle are removed; the production registry and public
exports/Help no longer register the old Lifecycle Dataset family. Runtime rejects
private legacy family execution before Run/source access; public recovery rejects
legacy descriptors. No compatibility alias is provided.

M08/M10: compiler/lifecycle.py, compiler/lifecycle_array.py,
materialization/lifecycle_bundle.py and lifecycle_integrity.py are physically deleted.
Exclusive replay/native-summary/inspection and ClickHouse lifecycle fold/packet
branches are removed. Shared Event/R8 lowering/adapters are retained.

M09: dedicated replay native_summary/inspect_history and integrity text calls are
removed. lifecycle_publication retains only schema/key declarations consumed by
private reducer lowering. lifecycle.py semantics/payload/schema/registration,
lifecycle_codec pure history decode, lifecycle_reducers and reducer codecs remain
for actual private R7.6 contracts, lowering/retained and numeric consumers. The
test-only history fixture constructs those declarations without an executable
producer. These consumers exit in R7.6; they are not public compatibility routes
or current Runtime qualification. Old History-dependent reducer Runtime tests are
also deferred to that phase, not included as R7.5 passing evidence.

The exclusive old replay tests and remote replay acceptance helpers are retired.
Their independent inception/empty/prefix/self/zero-duration/illegal/terminal/end
business cases now run on the new public path; former ID-sorted equal-time cases
require actual business order. Pure reducer numeric oracles retain the actual R7.6
owner. Shared R8 Forecast/Association/Event code remains unchanged.

## Validation receipts

The final gate after all semantic/validation corrections passed **151 Runtime tests**
in **416.30 seconds**: 150 R7.5 cases (including all 90 producers/270 P10 cells)
and the early legacy-route retirement guard. Its JUnit receipt is
`/tmp/marivo-r75-exit.xml`; the qualification record binds its SHA-256 and current
code hashes. The affected R7.2–R7.4 receipt is `/tmp/marivo-r75-regressions.xml`.
The final command was:

```sh
make runtime-test TESTS='tests/test_analysis_lifecycle_r75.py tests/test_r11_legacy_domain_block.py -k "test_analysis_lifecycle_r75 or lifecycle_source_route" --junitxml=/tmp/marivo-r75-exit.xml'
```

Earlier passing iteration receipts below establish progress; the final gate above
is the exit authority for the corrected Unknown semantics and full matrix.

- P10 matrix: 90 independent producer cases, **270 cells passed**, 366.75 seconds.
- R7.5 Runtime gate: **138 passed**, 469.69 seconds; after the encoding/key-strengthening
  change, **143 passed**, 403.26 seconds (140 R7.5 cases plus three migration guards).
- Encoding/key/coverage validation closeout: **18 passed**, 14.78 seconds.
- Independent cold descriptor-corruption closeout: **5 passed**, 13.75 seconds;
  actual receipt-read and transferred cycle/tie oracles: **6 passed**, 6.16 seconds.
- Unknown-origin/follow-up/terminal-prefix correction and full nonmatrix Runtime
  gate: **60 passed**, 53.01 seconds. It rejects false inception/transition assertions
  under insufficient authority and preserves the proved terminal case.
- Affected R7.2–R7.4 Runtime regression: **197 passed**, 490.83 seconds.
- make check-agent: passed lint/import contracts, typing of 430 modules,
  **5329 default passed, 5 skipped**, and API documentation generation. Final
  closeout after qualification assertions and the Unknown correction passed:
  **5330 default passed, 5 skipped**, typing of 430 modules and API docs.
- npm --prefix site run build: passed, 321 pages and both install-script outputs.
- git diff --check: passed.

Intermediate failures were repaired rather than counted: missing physical placements
for string Subject keys, state-vector reconstruction, stale legacy disclosure
snapshots, assertions comparing show() return values instead of output, and corruption
tests rereading a mutated Artifact's state instead of preserving its original Ref.

AGENTS.md, packaged skills and the R7.1 historical snapshot are unchanged. No commit,
push, release, MinIO, release-check, same-wheel or remote execution occurred. Six
History views, Duration statistics and selected-Subject observation remain R7.6;
R7.7–R7.9/full R7 and R8–R10 qualification remain separate.
