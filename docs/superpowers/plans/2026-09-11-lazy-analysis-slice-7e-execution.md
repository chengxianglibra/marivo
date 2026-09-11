# Slice 7e: private Lifecycle reducers and subject selection

Status: private Slice 7e complete; adversarial review fixes and frozen acceptance passed on 2026-09-11.

## Baseline and ownership

The implementation starts from `lazy-dataset` commit `68f0c74f`, the committed
Slice 7d replay and review fixes. The earlier planning snapshot was preserved;
its five post-acceptance changes were included in that commit before this work
began. Baseline revalidation passed 64 focused tests, typing of four changed
modules, and 37 Lifecycle Runtime tests. Existing evidence is preserved.

The reviewed differences from accepted `c5f4190f` to the planned `732f7d27`
candidate are `domains/lifecycle.py`, `materialization/lifecycle_publication.py`,
`materialization/retained.py`, `observation/contracts.py` (under
`marivo/analysis/`) and `tests/test_lazy_event_contracts.py`. The existing 7d
review-triage record independently binds that baseline to its complete gates.
`evidence/slice-7e/baseline.json` verifies the committed baseline file hashes,
records all five differences and checks the inherited 7d gate log checksums.

This slice owns private distribution, transitions, dwell, violations, InState
selection, exact retained-role consumption and Metric/Event continuation.
Public exports, the existing public `as_of` selector, Help/site activation,
integrated public journeys, commit, push and release remain outside this slice.

## Implementation

Paired Lifecycle Dataset methods consume logical replay or compatible retained
engine history. Private `in_state(state, *, at)` validates an exact model state
and aware instant against retained model/window meaning. Summary schemas,
coordinates, keys, ordering, allowed predicates, producer versions, Evidence and
cold recovery are registered together. History remains structurally unfilterable.

Distribution classifies every coverage-ledger subject at each explicit instant,
including subjects with no positive interval. It keeps known denominators,
not-yet-incepted members and coverage uncertainty distinct. Explicit governed
Dimension enrichment uses retained identities and each requested instant;
null groups are retained. The exclusive replay end uses left-limit state with
complete-through coverage. InState places a complete-membership validation fence
before filtering, sampling, downstream membership and publication.

Transitions reads only the exact legal trace, including same-time cycles and
self-transitions. Counts describe observed legal triggers under retained coverage;
they are not conditional transition probabilities. Dwell reads only positive
intervals and estimates completed clipped-window fragments, with exact native
median/p90 and a separate left-clipped completed count. Fractional microsecond
statistics remain floating microsecond values in storage and local filtering;
terminal pandas presentation uses timedeltas. Violations projects only its
persisted trace and emits zero Findings.

Each consumer declares the exact private roles for its particular input node.
Trace-only reducers validate Artifact bindings without querying history interval
rows. Required-role absence or corruption fails; no consumer replays triggers,
rebuilds membership or substitutes current Entity enumeration. Current Dimension
and downstream Metric/Event sources are explicit separate dependencies.
Terminal reducer results can use local Parquet and exact admitted pandas filters;
identity/source-required computation remains native with no local fallback.

## Validation strategy

Focused tests cover source-free construction, independent numerical references,
exact schemas/metadata, interval and coverage boundaries, completed-fragment
statistics, same-time transition multiplicity and private role demand. Runtime
checks cover logical and retained computation, grouped membership, null axes,
empty/uncertain selection, Metric/Event loops, local filtering, corruption,
publication failure/cancellation and retry.

Three interpreters separately produce history, execute continuations with trigger
readers and original Population enumeration disabled and occurrence tables removed,
and recover exact existing
bindings without placement, compilation or source execution. Both local and
engine terminal storage are tested; history and selected membership remain
engine-backed where source-native continuation requires them.

Final acceptance binds focused checks, explicit source/test typing,
`make check-agent`, and focused Lifecycle/Event/shared Runtime regressions to one
unchanged executable fingerprint. Runtime concurrency is at most two workers.
The candidate manifest, commands, exit codes, logs and process evidence are
retained under `evidence/slice-7e/`.

The initial `9b81ba74` candidate passed 156 focused tests, ten-file test typing,
`make check-agent` (472 source modules and 7,088 default tests) and 99 Lifecycle
Runtime tests. Those logs are retained in `initial-candidate/`. The final candidate
adds an explicit prohibition on original Population enumeration in both
source-offline continuation and cold binding. It retains required Dimension
and downstream Metric sources. The complete gate set is rerun against that
final candidate; initial results are not substituted for final acceptance.

## Initial acceptance

The final executable candidate contains 978 files and has SHA-256
`bc16da43c38067b1e0d9d6ff22326c41b2faba8b62de9babaf414bf4b28e38be`.
The manifest hashes sorted repository-relative paths, NUL separators and exact
bytes of all Python files under `marivo/` and `tests/`, plus `pyproject.toml`,
`Makefile` and `.importlinter`. Documentation, generated API pages and evidence
are outside this executable fingerprint.

All seven gates in `evidence/slice-7e/gates.json` passed with the same before/after
candidate and verified log/runner checksums. Runtime gates ran sequentially
after the successful broad check, with two workers per invocation.

| Gate | Observed result |
| --- | --- |
| Focused contracts, numerical references and shared defaults | 156 passed |
| Explicit modified-source typing | 22 modules passed |
| Explicit test and worker typing | 10 files passed |
| `make check-agent` | Format, lint, import contracts, 472 source modules, 7,088 default tests and API documentation passed |
| Lifecycle Runtime | 99 passed |
| Event Runtime | 108 passed |
| Shared membership/materialization Runtime | 85 passed |

The 292 Runtime regressions include independent local and engine three-process
proofs saved in `runtime/slice-7e-local.json` and `runtime/slice-7e-engine.json`.
Producer, continuation and cold reader have distinct process identities.
Cold binding preserves Artifact records, bounded Evidence, terminal hashes and
Store counts. Trigger readers and original Population enumeration are explicitly
forbidden; necessary Dimension and downstream Metric sources remain available.
Separate logical and retained Runtime cases prove the full Metric -> Lifecycle
-> Population -> Metric/Event loop.

Independent numerical and Runtime references cover dense zero state rows,
null shares, no-interval subjects, retained coverage prefixes, grouped/null-axis
reconciliation, same-instant transition cycles, complete empty selection and
atomic rejection of any unknown member before filtering, sampling or downstream
membership. Dwell uses `completed_window_fragment_duration@v1`: the two-day
window fragment is measured rather than its eleven-day unclipped episode.
Completed fragments alone feed exact median/p90, including fractional
microsecond results. Required-role absence/corruption and malformed continuation
Evidence fail without replay; failure/cancellation publishes no partial artifact
and allows same-Session retry.

This closes only private Slice 7e. Integrated public Slice 7 acceptance,
public exports, Help/site activation, commit, push and release remain outside
this delivery. Runtime coverage routing is updated in
`docs/testing/runtime-coverage.md`.

## Adversarial review disposition (2026-09-11)

The review was checked against uncommitted candidate `bc16da43`, with base
`68f0c74f46ba54bd1eb42c892a518e26b036fff5`. Its quoted line numbers and 15/52-test
summary describe a narrower snapshot; the original seven gate records remain
available. The following decisions use the current owning contracts, including
this plan's private-first/public-once boundary and the explicit instruction to
preserve the public `as_of` constructor in Slice 7e.

| Review item | Decision and evidence |
| --- | --- |
| Missing method parameter descriptions | Accepted. Both paired classes now describe `at`, `axes`, `selection` and `predicates`; the private constructor describes `state` and `at`. The three parameterless reducers already state that they take no parameters. An `Args:` heading itself is not the owning requirement; complete parameter descriptions are. |
| Two incompatible InState types | Rejected as a current public-contract violation. The accepted scope explicitly requires private `at: datetime` while preserving public `as_of: str`. Neither private Dataset class nor constructor is publicly exported. A focused test pins both signatures and the current public return type. Unification or public replacement belongs to Slice 8; adding a temporary compatibility alias would violate this slice's boundary. |
| No Help/skill/site activation | Rejected for this private slice. The delivery strategy explicitly forbids advertising private implementations in Help or current EN/ZH docs before Slice 8. Existing public snapshots and the full API documentation gate remain mandatory. Internal row contracts and execution documentation are updated. |
| Repeated paired method wrappers | Not adopted. Each class keeps an explicit typed receiver and return contract, consistent with existing paired Dataset families. The algorithms already have one owner in `lifecycle_reducers`; introducing a mixin would add inheritance and typing structure without removing duplicated execution logic. |
| Repeated distinct transition-pair calculation | Accepted. `LifecycleSemantics.transition_pairs` now owns first-declaration ordering, shared by compilation, publication proofs, local ordering and stream validation. |
| Mixed family and shape checks | Partially accepted. The retained history scan bypass is now restricted to `LifecycleSemantics`. Other family-wide decisions, such as zero Findings and Lifecycle artifact handling, remain family checks; history-only retained-role ownership remains a shape check. A blanket replacement would erase those different contracts. |
| `event_coverages` also carries Lifecycle coverage | Not adopted as a correctness fix. These values are `EventCoverageResolution` records for Lifecycle's trigger Events, indexed by exact input definition or Artifact. Renaming the shared plumbing is unnecessary for the present behavior. |
| Synthetic `__pair` ordering term | Accepted. Transition stream validation now compares a dedicated declared-pair ordinal without injecting a fake field into generic ordering terms. |
| Missing reducer parity and order evidence | Accepted as a coverage gap. A six-subject, three-group reference checks dense state rows, null/unincepted groups, declared state and transition-pair order, complete count/share equations, exact violation identities, and completed-fragment statistics against rational arithmetic. It compares all four reducer DataFrames and filtered results across independent engine/local terminal storage. This is not a new pandas execution fallback. |
| Missing high-cardinality native-transfer proof | Accepted. A 5,000-subject test executes history, all four reducers and selection with local transfer entry points forbidden. It verifies zero transferred rows/bytes, no local worker/handoff, and 5,000 identity-bearing violation/selection rows retained natively. |
| Missing incompatible-placement proof | Accepted. Five local-history consumers and six local/foreign-engine selected-Population continuations reject before backend construction or local supervision. No Run is created and Store counts remain unchanged. Choosing an engine target in the successful cold worker is retained; it proves a compatible path rather than a rejection path. |
| Missing subject-identity canaries | Accepted. The native test scans subject and three occurrence canaries across stored metadata/Findings, recorded statements, paths, repr/contracts/Evidence digests, captured logs/output and cold missing-storage errors. Authorized row storage remains the only location for those identities. |
| Missing Lifecycle-source continuation | Accepted. Both logical and retained selection now continue into Lifecycle, alongside Metric and Event; unknown membership also blocks the Lifecycle consumer. This exposed a real publication bug: inherited selection coverage caused a final history to enter reducer-summary validation. Admission now chooses that validation only for a concrete reducer shape or an actual selection output. |
| Missing same-plan single membership evaluation | Accepted. Metric, Event and Lifecycle continuations each execute one complete logical plan and publish only its terminal Artifact. Executed native `CREATE TEMPORARY TABLE` statements prove exactly one realization of the selected identity relation, shared by downstream use. |
| Global float64 duration admission | Accepted and fixed. Generic duration admission again requires int64 microseconds. Realized schema checks now receive their row contract; only the three exact dwell statistics under `DwellSemantics` admit float64 microseconds. Local validation and terminal conversion use the same owner. Negative tests reject floating durations without that owner; Event storage tests exercise the unchanged integer path. |
| Broad Lifecycle scan bypass | Accepted and narrowed to history semantics. Reducer scans use the common retained scan path. Exact history part admission remains with the existing history owner. |
| Relocated history decoder | Retained with justification. The pure parser is now domain-owned so reducers can consume retained meaning without an import from materialization into the domain. The existing codec entry point delegates to it; persisted meaning and validation are unchanged. Existing history contract, codec, Runtime and cold-recovery gates are rerun. |

Review-fix acceptance passed all seven frozen gates for 979-file candidate
`a9a1ddbe562ae795499e055852709e6cbfff3e117021ab9a6e2f8102b0813df3`.
Commands, logs and before/after manifests are retained separately under
`evidence/slice-7e/review-fix/`. No prior acceptance log is overwritten.


The current acceptance in `evidence/slice-7e/review-fix/gates.json` supersedes the
initial candidate. Every gate preserves the same executable fingerprint and has
verified log and runner hashes:

| Review-fix gate | Observed result |
| --- | --- |
| Focused contracts, references and affected Event storage | 210 passed |
| Explicit modified-source typing | 23 modules passed |
| Explicit test/worker typing | 13 files passed |
| `make check-agent` | Format, lint, import contracts, 472 source modules, 7,090 default tests and API documentation passed |
| Lifecycle Runtime | 116 passed |
| Event Runtime | 108 passed |
| Shared membership/materialization Runtime | 85 passed |

All 309 Runtime regressions ran after the successful broad check, sequentially
with two workers per invocation. Both independent-process proofs were refreshed
against the 979-file candidate. `review.json` records the reviewed baseline,
the 17 changed executable files and the confirmed history-publication defect.
The cutover acceptance and Runtime coverage map now point to this follow-up.
Public activation, parent Slice 7 acceptance, commit, push and release remain
outside this change.
