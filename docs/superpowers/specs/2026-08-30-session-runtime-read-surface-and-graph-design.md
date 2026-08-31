# Session Runtime Read Surface and Graph Design

Date: 2026-08-30

Status: proposed

## Relationship to Existing Contracts

This design refines the public Session recovery and Evidence read surface. It
preserves the following implemented boundaries:

- an Artifact is the immutable result of one typed analysis computation;
- `artifact.show()` is the single bounded immediate read after execution;
- `artifact.contract()` owns mechanically valid continuations for one concrete
  Artifact;
- an `ArtifactDigest` is an immutable commit-time projection, not a later
  session synthesis;
- canonical Findings remain Artifact-owned raw audit records and carry their
  complete derivation trace;
- revalidation is explicit, read-only, and does not prove datasource freshness;
- the agent owns cross-Artifact interpretation, business judgment, method
  choice, and stopping;
- Marivo does not expose a natural-language planner or recommended-next-step
  API.

This design supersedes the public recovery names and topology in:

- [`docs/specs/analysis/session-state-and-runtime.md`](../../specs/analysis/session-state-and-runtime.md),
  especially `frame_summaries()`, `get_frame()`, `jobs()`, `recent_jobs()`,
  `job()`, and the decision not to expose any Session-level DAG projection;
- [`docs/specs/analysis/evidence-access-surface.md`](../../specs/analysis/evidence-access-surface.md),
  where Artifact digests are currently exposed again through
  `session.evidence.digests()` and `session.evidence.digest()`, and raw Finding
  audit is Session-scoped; this design removes the Session Evidence namespace,
  keeps digest and Finding reads on their owning Artifact, folds trace into
  `Finding`, and removes public selection compatibility;
- [`2026-07-18-evidence-typed-digest-refactor-design.md`](2026-07-18-evidence-typed-digest-refactor-design.md),
  only for its session recap and recovery API layout;
- [`evidence-compatibility-and-revalidation-design.md`](evidence-compatibility-and-revalidation-design.md),
  for the complete public `EvidenceCompatibility` API, result algebra, Help,
  skill, documentation, and multi-Finding combination promise; its Artifact
  revalidation contract remains authoritative;
- [`2026-08-27-progressive-analysis-live-help-design.md`](2026-08-27-progressive-analysis-live-help-design.md),
  only for removing the now-singleton `runtime.artifacts`, replacing
  `runtime.jobs` with `runtime.runs`, and removing the now-singleton Evidence
  browse/exact pages; its root, hub, discovery-ownership, render-class, budget,
  and exact-leaf rules remain authoritative.

The capability kernel, Artifact family algebra, focused-help ownership,
Evidence semantics, operator Artifact-admission compatibility, revalidation
algorithm, and result protocol remain authoritative unless this design
explicitly changes their public entry point. Marivo no longer promises that an
arbitrary caller-selected set of Findings is mechanically safe to combine.

This is a clean public-surface cutover. The final release contains no aliases,
deprecation wrappers, dual read paths, or two public vocabularies for the same
capability. Internal persistence names such as `jobs`, `frames`, and
`output_frame_ref` may remain implementation details until separately cleaned
up; they must not leak into the new public contract.

## Summary

Replace the storage-shaped Session recovery surface with one agent-shaped
runtime model:

```text
Session
  -> exposes typed analysis Capabilities
  -> each admitted materializing Capability execution becomes one Run
  -> Runs consume zero or more Artifacts
  -> successful Runs produce or reuse one Artifact
  -> Artifacts carry commit-time Evidence, quality, and Finding ownership
  -> each Finding provides one exact raw audit record and derivation trace
```

The primary Session navigation surface is deliberately Run-first:

```python
session.show()                          # bounded Session recap
session.runs(...)                       # primary paged runtime history
session.get_run(run_id)                 # exact typed execution record
session.graph()                         # bounded factual Session DAG
session.graph(artifact_ref=ref, direction="ancestors")
session.graph(artifact_ref=ref, direction="descendants")
```

Exact Artifact recovery and authority remain explicit:

```python
session.artifact(ref)                   # exact live Artifact recovery
session.revalidate(ref)                 # explicit current authority check
```

Evidence drill-down is discovered from the exact Artifact rather than mixed
into Session history navigation:

```python
artifact.evidence_digest                # immediate commit-time snapshot
artifact.finding_count                  # exact bounded metadata fact
artifact.findings(...)                  # bounded Artifact-scoped raw audit page
artifact.finding(finding_id)            # exact Artifact-owned Finding
```

The corresponding removals are:

```text
session.frame_summaries
session.get_frame
session.jobs
session.recent_jobs
session.job
session.evidence.digest
session.evidence.digests
session.evidence.findings
session.evidence.finding
session.evidence.trace
session.evidence.compatibility
EvidenceCompatibility
EvidenceCompatibilityIssue
EvidenceSelectionError
EvidenceDigestNotAvailableError
analysis.runtime.artifacts
analysis.runtime.jobs
analysis.evidence.browse
analysis.evidence.exact
```

Compatibility-only status/type aliases and renderers are removed with that
public result algebra. `EvidenceDigestNotAvailableError` is removed because its
only public producer was the deleted exact Session digest lookup.
`EvidenceStoreUnavailableError` remains for Artifact-scoped Finding reads and
revalidation; `EvidenceRuleIssue` remains because Artifact revalidation
independently consumes it.

Add `session.graph()` as a first-class factual projection. It returns a typed,
immutable, bounded `SessionGraph` containing Artifact summaries, Run records,
and closed edges. It is reconstructed from one Session Store snapshot plus
immutable Artifact metadata. It is never persisted as a second graph document
or used as a planner.

The runtime must not collapse materialization, Evidence availability, quality,
semantic authority, and datasource freshness into one Session or Artifact
status. Those facts have different owners and observation times. The new
surface makes them adjacent and consistently named while keeping them
independent.

### Design economy

The target optimizes implementation and agent cost together:

- one primary history collection (`runs`) plus exact identity reads;
- one optional Graph entrypoint rather than a persisted snapshot or planner;
- existing `ArtifactSummary`, concrete Run variant, and `ArtifactDigest` values
  reused without a parallel Artifact collection hierarchy;
- one edge value instead of node and edge class hierarchies;
- one Session Store Run projection and input index instead of a second graph
  authority;
- no public family filter, opaque Graph fingerprint, exact omission counts,
  wrapper handle, duplicate Run summary type, or Session Evidence namespace in
  V1;
- Artifact digest inspection requires exact Artifact recovery; the narrow
  ref-only optimization does not justify a second public access path.

New public structure is justified only when it removes a repeated agent join or
makes an invalid state unrepresentable. Internal integrity fields and storage
adapters do not become agent concepts.

## Current-State Assessment

The current runtime already persists most of the facts needed for an Artifact
DAG:

- Session Store Artifact rows retain Session ownership, Artifact identity,
  kind, content hash, Evidence status, creation time, and producing job;
- persisted job records retain intent, input Artifact refs, output Artifact
  ref, purpose, timing, status, and Artifact-reuse information;
- Artifact metadata retains lineage, commit-time Evidence status and digest,
  quality summary, typed issues, content hash, and producing job;
- `LineageStep` retains job identity, input refs, parameter digest, and purpose.

The problem is the public projection, not the absence of persisted facts.

### Recovery requires a manual multi-surface join

To reconstruct one Session today, an agent must independently call and join:

```python
session.frame_summaries()
session.jobs()
session.job(job_id)
session.get_frame(ref)
session.evidence.digests()
session.evidence.findings(...)
```

The join keys are available, but the public library asks every agent to rebuild
the same graph. This is mechanical work owned by Marivo, not business judgment
owned by the agent.

### Public vocabulary follows storage rather than analysis

The runtime documentation consistently calls returned values Artifacts, while
recovery uses `frame_summaries()` and `get_frame()`. Execution is presented as
jobs even though the agent needs a record of one typed Capability execution
attempt. An agent must therefore remember three translations:

```text
Artifact <-> Frame <-> artifact row
Run      <-> Job   <-> job JSON record
Evidence digest on Artifact <-> digest under session.evidence
```

These translations carry no analytical meaning and should not be public.

### Status facts are scattered

One Artifact's observable state is currently split across:

- `artifact.state.materialization` and `content_hash`;
- `artifact.evidence_status` and `artifact.evidence_digest`;
- `artifact.quality_summary` and `artifact.contract().issues`;
- `session.revalidate(artifact)` for current semantic authority and Evidence
  integrity;
- no Marivo read for current datasource freshness.

Keeping these axes separate is correct. Requiring the agent to discover their
owners across unrelated recovery routes is not.

### Collection shape is inconsistent

`frame_summaries()` returns a bounded immutable page. `jobs()` returns every
job in an ordinary list, `recent_jobs()` slices that full list, and `job()`
returns `dict[str, Any]`. This violates the public result and concrete typing
principles even before a graph is introduced.

### Artifact digest access is duplicated

An Artifact already owns its exact commit-time `evidence_digest`, and
`artifact.show()` renders it. Both `session.evidence.digests()` and
`session.evidence.digest(ref)` create a second route to the same Artifact-owned
value. The exact Session lookup can avoid loading Artifact rows, but that narrow
optimization does not justify a second namespace, Help route, failure surface,
and agent decision. V1 keeps one canonical path: recover the exact Artifact,
then read or render its digest.

### The no-synthesis rule is too broad

The current Session specification correctly rejects a public object that
generates conclusions or recommends analysis. It then rejects every
Session-level factual projection and requires the agent to synthesize even
identity and lineage joins.

Joining persisted `input_artifact_refs`, `output_artifact_ref`,
`produced_by_job`, and Artifact refs is not analytical synthesis. It is a
deterministic runtime projection and belongs in Marivo.

### Failure recovery is not a closed persisted contract

The current specification describes failed steps as recoverable through job
records, but ordinary intent implementations primarily persist succeeded jobs
after Artifact publication. A Session graph must not invent failed Run records
from missing output. The target contract therefore makes Run lifecycle
persistence explicit and typed.

## Goals

1. Let an agent recover the shape of an ordinary Session with one call.
2. Make Artifact, Run, and Finding the only public runtime identity families.
3. Keep the immediate result path on `artifact.show()` with no second mandatory
   Evidence read.
4. Make every Session collection read bounded, immutable, typed, and
   deterministically ordered.
5. Represent branching, merging, Artifact reuse, failed Runs, incomplete Runs,
   and both upstream and downstream Artifact navigation truthfully.
6. Keep commit-time state, current semantic authority, and datasource freshness
   visibly distinct.
7. Preserve exact Artifact recovery and Artifact-scoped raw Finding audit
   without requiring private storage inspection.
8. Keep Graph construction read-only and free of backend queries, Evidence
   recomputation, business judgment, and method recommendation.
9. Keep the public result algebra small by reusing Artifact summaries and Run
   records inside the Graph rather than creating parallel node families.
10. Make Help, result cards, the packaged skill, English/Chinese documentation,
   persistence validation, and public type snapshots one coordinated contract.
11. Accept only the exact current Session Store, Run payload, and Artifact
    schemas; incompatible prior or future state fails before mutation and is
    never reused.

## Non-Goals

This design does not add:

- an `AnalysisSnapshot` Artifact;
- a mutable graph, graph authoring API, or graph persistence document;
- a workflow planner, ranked next action, hypothesis manager, or stopping rule;
- a scalar `session.status` or `artifact.health` that combines unrelated axes;
- automatic Artifact revalidation while rendering or building a graph;
- datasource freshness checks;
- multi-Finding compatibility, combination, subset selection, or synthesis;
- causal or business interpretation across Artifact digests;
- Mermaid, Graphviz, NetworkX, HTML, or visualization-specific public output;
- graph pagination;
- public access to raw SQL, credentials, backend objects, stack traces, or
  arbitrary persisted parameter payloads;
- a compatibility period for removed public names;
- migration, import, decoding, backfill, or reuse of pre-cutover Session state.

The structured `SessionGraph.artifacts`, `.runs`, and `.edges` are sufficient
for an external renderer. A Marivo-owned visualization format requires a
separate demonstrated need.

## Ownership and Vocabulary

### Session

A Session owns one persistent analysis namespace, stable identity, current
question, runtime configuration, Artifacts, Runs, and Finding ledger.

A Session is not one analysis result and has no single analytical conclusion.

### Run

Decision: retain `Run` as the runtime-attempt abstraction.

A Run is the persistent record of one admitted execution attempt of a typed,
Help-resolvable, materializing analysis Capability. Admission occurs after
Session ownership, Capability arguments, and Artifact inputs have been
normalized. `Run` is the public term; `job` is storage vocabulary only.

The abstraction boundary is:

```text
Capability   what can be executed
Run          what happened in one admitted execution attempt
Artifact     what one successful Run produced or reused
Finding      what exact Evidence records support an Artifact
```

A Run is therefore a runtime attempt identity, not a callable operation, a
workflow plan or step, a backend queue job, an Artifact, or a business
conclusion. Each retry creates a new Run. A successful attempt that reuses an
existing Artifact is still a distinct Run because the attempt, inputs, safe
arguments, time, and reuse outcome are facts of the current Session history.

This layer is necessary because failed and incomplete attempts have no output
Artifact, while multiple attempts may return the same content-addressed
Artifact. Neither Capability nor Artifact identity can truthfully represent
those lifecycle facts or the consumed-by edges needed for downstream impact
navigation.

A Run has exactly one closed lifecycle variant:

```text
IncompleteRun  # admitted and started; no terminal record was committed
SucceededRun   # returned exactly one produced or reused Artifact
FailedRun      # terminated with one sanitized AnalysisError or internal summary
```

Read-only Session methods never create Runs. Calls rejected before admission
also do not become Runs; they remain ordinary structured call-site errors and
do not pollute Session history.

### Artifact

An Artifact is one immutable typed analysis result. `Artifact` is the public
recovery term even when the concrete Python family is `MetricFrame`,
`DeltaFrame`, `CandidateSet`, or another result class.

`ref` remains the single public Artifact identity field end to end. This design
does not introduce an `artifact_id` alias.

### Finding

A Finding is one canonical raw Evidence record with stable identity, exactly
one owning Artifact ref, and its complete derivation trace. The Session Evidence
ledger remains the persistence authority, but it does not make Findings a
Session-level collection: public browsing is always scoped by the owning
Artifact.

`artifact.findings(...)` is an explicit bounded read rather than a Python
property. A property named `artifact.findings` must not hide SQLite I/O or imply
that an unbounded collection is already resident in the immutable Artifact.
`artifact.finding(id)` performs an exact ownership-checked read. Marivo does not
expose a public operation for combining Findings from one or more Artifacts.

### SessionGraph

A SessionGraph is a bounded immutable projection of persisted Run and Artifact
facts. Its existence does not create a fourth persisted identity authority.

## Status Model

There is no overall Session status.

The following axes remain independent:

| Axis | Owner | Values | Observation time |
| --- | --- | --- | --- |
| Run lifecycle | concrete Run variant | `incomplete`, `succeeded`, `failed` | execution-attempt time |
| Artifact materialization | `ArtifactState` | existing closed materialization values | commit time |
| Evidence availability | Artifact metadata | `complete`, `partial`, `unavailable` | commit time |
| Quality | `QualitySummary` and typed issues | existing closed summary/issues | commit time |
| Semantic authority | `ArtifactRevalidation` | `current`, `stale`, `indeterminate` | explicit revalidation time |
| Evidence integrity | `ArtifactRevalidation` | existing revalidation values | explicit revalidation time |
| Datasource freshness | datasource/runtime authority | not reported by this surface | outside this contract |

`Session.show()` and `SessionGraph.show()` may place these facts next to each
other. They must not compute labels such as `healthy`, `ready`, `valid`,
`successful analysis`, or `safe to report` from them.

When semantic authority or freshness was not checked, the renderer states the
boundary rather than silently omitting it:

```text
current authority: not checked; call session.revalidate('<ref>')
source freshness: not checked by SessionGraph
```

The structured Graph does not store `not_checked` as if it were an Artifact
property. These are renderer boundaries on the Graph read itself.

## Public API Contract

### Session recap

```python
session.show()
```

`Session.show()` remains a no-argument bounded read. It adds a compact runtime
recap after identity and runtime mode:

- total public Artifacts;
- exact head Artifact count and at most three newest head refs;
- Run counts by `succeeded`, `failed`, and `incomplete`;
- Artifact counts by Evidence status;
- at most three newest failed or incomplete Run ids;
- `session.graph()` as the canonical overall continuation when the Session is
  within the Graph scan bound.

The exact aggregate counts come from one Session Store read transaction. The
recap does not scan Artifact metadata to aggregate quality or issues; those
facts remain on bounded Graph Artifact summaries and exact Artifacts. Head
derivation uses the normalized Run-input index rather than constructing the
Graph. The recap does not render Artifact digest items, rows, complete Run
records, or the graph.

### Run-first Artifact discovery

There is no `session.artifacts(...)` collection. It would create a second
history browsing axis parallel to Runs and force the agent to decide which
collection to inspect first.

Artifacts are discovered through facts already owned by the primary runtime
model:

- `session.runs(...)` exposes ordered input refs and the succeeded output ref;
- `session.get_run(id)` exposes the exact attempt and its Artifact refs;
- `session.graph(...)` exposes bounded `ArtifactSummary` values when topology
  matters;
- `session.show()` exposes only bounded global head refs and attention Runs.

Linked Component and Coverage sidecars remain excluded from these public
Artifact summaries. Once an exact ref is known, `session.artifact(ref)` is the
one live recovery operation.

### Exact Artifact recovery

```python
session.artifact(ref: str) -> BaseFrame
```

This replaces `session.get_frame(ref)` while preserving concrete Artifact
families, immutability, exact Session ownership, cold-start reconstruction, and
typed corruption errors. `BaseFrame` is the stable public supertype; the runtime
returns the exact concrete subclass. The API does not add an Artifact union
alias, widen to `Any`, or return a wrapper result.

The recovered Artifact continues to expose:

```python
artifact.show()
artifact.contract()
artifact.state
artifact.lineage
artifact.evidence_status
artifact.evidence_digest
artifact.finding_count                  # exact non-negative int
artifact.quality_summary
artifact.to_pandas()
artifact.findings(...)
artifact.finding(finding_id)
```

No `ArtifactHandle`, `ArtifactView`, or second `.load()` step is introduced.

Finding access is explicit and bounded:

```python
artifact.findings(
    *,
    limit: int = 20,
    cursor: str | None = None,
) -> FindingPage

artifact.finding(finding_id: str) -> Finding
```

`artifact.findings()` is newest-first keyset pagination bounded to `[1, 100]`.
It opens the owning Session ledger through the immutable `session_id` and
`project_root` already persisted in Artifact metadata; no mutable Session
handle or public wrapper is attached to the Artifact. `artifact.finding(id)`
requires the canonical Finding to name `artifact.ref` as its owner. An unknown
id or a Finding owned by another Artifact raises the existing
`FindingNotFoundError` scoped to this Artifact; no second ownership-error class
is added.

`Finding` includes the derivation rule, source Artifact ref, source fields,
source refs, and retained digest-item refs needed for exact audit. There is no
second trace result or `artifact.trace(...)` call.

### Artifact revalidation

```python
session.revalidate(ref: str) -> ArtifactRevalidation
```

The public input changes from a live `BaseFrame` to the exact Artifact ref. The
Session already owns Artifact resolution and must not require this two-call
sequence:

```python
session.revalidate(session.get_frame(ref))
```

Internally the method performs exact Artifact recovery and the existing
revalidation algorithm. Its authority, Evidence, freshness, mutation, and
error boundaries do not change.

### Run collection

```python
session.runs(
    *,
    status: Literal["incomplete", "succeeded", "failed"] | None = None,
    capability_id: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> RunPage
```

This replaces both `session.jobs()` and `session.recent_jobs()`.

The page is newest-first and keyset-paged by `(started_at, run_id)`. `limit` is
bounded to `[1, 100]`. An ordinary call is bounded; `limit=5` replaces the old
recent-jobs use case without a separate method. `RunPage` uses the standard
immutable page fields: `items`, `limit`, `has_more`, and opaque `next_cursor`.

`capability_id` is one exact Help-resolvable capability id. Every v1 Run payload
must carry that canonical id; a legacy or non-canonical intent spelling is an
integrity error and is never normalized into a second vocabulary.

### Exact Run recovery

```python
session.get_run(run_id: str) -> IncompleteRun | SucceededRun | FailedRun
```

This replaces `session.job(job_id) -> dict[str, Any]`.

The `get_` prefix is intentional. `session.get_run(...)` is an exact read and
must not look like an imperative method that starts execution. Collection
discovery remains `session.runs(...)`. Artifact recovery remains
`session.artifact(...)`: `artifact` is unambiguously a noun in method position,
while `run` is also an action verb.

`RunRecord` is the closed union:

```python
RunRecord = IncompleteRun | SucceededRun | FailedRun
```

`RunRecord` is specification and annotation shorthand, not an exported runtime
class or Help target. The three concrete variants are the public result types;
the concrete variant determines which fields exist. There is no optional-field
mega-record with nullable output and error fields.

### Artifact Evidence audit

The Artifact is the semantic owner and only public read path for its digest:

```python
artifact = session.artifact(ref)
digest = artifact.evidence_digest
```

Remove the complete Session Evidence namespace:

```python
session.evidence.digest(...)
session.evidence.digests(...)
session.evidence.findings(...)
session.evidence.finding(...)
session.evidence.trace(...)
session.evidence.compatibility(...)
```

V1 does not optimize ref-only digest recovery. An agent with only an Artifact
ref recovers the exact Artifact once, then uses `artifact.show()` or
`artifact.evidence_digest`. This keeps one owner, one discovery route, and one
recovery decision.

`ArtifactDigest.fallback`, `.show()`, `.contract()`, structured repairs, and
dynamic guidance must route raw audit to `artifact.findings()` or
`artifact.finding(id)` and must never emit a `session.evidence.*` continuation.

Canonical Finding audit is reached from the owning Artifact:

```python
page = artifact.findings(limit=20)
finding = artifact.finding(page.items[0].finding_id)
```

There is no Session-wide Finding browse, separate derivation-trace call, or
Finding-selection compatibility API. Marivo guarantees each Finding's own
canonical identity, Artifact ownership, derivation, and integrity; it makes no
promise that multiple Findings are safe to combine.

`ArtifactSummary` carries a bounded Evidence summary including the exact Finding
count persisted by the producing Artifact. Session browsing reads that metadata
fact and never opens the Evidence ledger merely to fill the field.

## Public Result Types

### ArtifactSummary

```python
class ArtifactSummary:
    ref: str
    family: ArtifactFamily
    semantic_shape: str | None
    created_at: datetime
    produced_by_run: str | None
    analysis_purpose: str | None
    row_count: int
    content_hash: str | None
    materialization: ArtifactMaterialization
    evidence: ArtifactEvidenceSummary
    quality: QualitySummary | None
    issue_counts: ArtifactIssueCounts
```

`ArtifactEvidenceSummary` contains only commit-time facts:

```python
class ArtifactEvidenceSummary:
    status: EvidenceStatus
    digest_present: bool
    digest_item_count: int
    omitted_item_count: int
    finding_count: int
```

It does not repeat digest items, inference boundaries, Finding ids, or Finding
payloads. Every exact current-schema Artifact persists an exact non-negative
`finding_count` at commit, so the property remains a pure bounded metadata fact.
Exact records always require `artifact.findings(...)`.

`ArtifactIssueCounts` contains typed severity counts, not free-form messages.
Exact issues remain on the recovered Artifact contract.

### Finding

The existing canonical Finding is tightened to one Artifact-owned exact audit
record:

```python
class Finding:
    finding_id: str
    artifact_ref: str
    session_id: str
    finding_type: FindingType
    epistemic_kind: EpistemicKind
    subject: EvidenceSubject
    canonical_item_key: str
    value: FindingValue
    derivation: DerivationRule
    source_artifact_ref: str
    source_fields: tuple[str, ...]
    source_refs: tuple[str, ...]
    retained_digest_item_refs: tuple[str, ...]
    committed_at: datetime
```

`artifact_ref`, not `artifact_id`, is the only public Artifact identity name.
The derivation fields subsume the previous `EvidenceDerivationTrace`; no trace
identity or second read result remains. `FindingPage.items` contains these same
full immutable Findings and carries the standard bounded keyset page fields.

### Run records

`RunPage.items` contains the same three closed concrete variants returned by
`session.get_run(id)`. Runs are small metadata records, so V1 does not create a
second summary hierarchy for list results.

All Run types are immutable bounded results with deterministic `repr`,
`render()`, and `show()`.

Common fields are carried by each variant rather than hidden in an untyped
payload:

```python
run_id: str
capability_id: str
analysis_purpose: str | None
input_artifact_refs: tuple[str, ...]
arguments: tuple[RunArgument, ...]
omitted_argument_names: tuple[str, ...]
started_at: datetime
```

`RunArgument` is one normalized public argument value:

```python
class RunArgument:
    name: str
    value: JsonValue
```

Arguments are sorted by public parameter name and contain only normalized,
bounded, non-secret JSON values. Semantic refs use their existing typed payload
encoding. Session identity, Artifact inputs, and `analysis_purpose` are not
repeated because their owning Run fields already expose them. Raw SQL,
credentials, backend objects, executable callables, and unvalidated `repr`
strings are never projected. Their parameter names appear in
`omitted_argument_names`. The projection must preserve every other safe value
that changes the observable computation, so an agent can distinguish ordinary
Runs without decoding an opaque hash. These argument fields are inspection
facts, not an automatic replay API; any omission is explicit and prevents a
mechanical replay claim.

The implementation may retain a private parameter digest for integrity and
Artifact-lineage cross-checking. It is not a public Run field and is not a
substitute for safe argument facts.

`SucceededRun` additionally carries:

```python
output_artifact_ref: str
output_mode: Literal["produced", "reused"]
finished_at: datetime
```

`FailedRun` additionally carries:

```python
failed_at: datetime
failure: RunFailure
```

`IncompleteRun` carries no invented finish time, error, or output. V1 does not
publish `duration_ms`; exact start and terminal timestamps are sufficient for
inspection, while interruption recovery must not invent monotonic duration.

`RunFailure` is a persisted safe subset of the structured Analysis error:

```python
error_type: str
message: str
expected: JsonValue | None
received: JsonValue | None
location: str | None
repair: AnalysisRepair | None
```

It excludes stack traces, exception reprs, arbitrary locals, backend handles,
raw secret values, raw SQL, and unvalidated parameter payloads. All values are
bounded before persistence. Known `AnalysisError` fields pass through one
recursive JSON allowlist, secret-pattern redaction, depth/item limit, and byte
limit before `RunFailure` construction; the same sanitizer covers nested
`AnalysisRepair` snippets. If a known error cannot be safely projected, it uses
the generic `InternalExecutionError` failure rather than persisting the raw
message. Unknown exceptions never contribute their original message.

### Public type placement

`SessionGraph`, `ArtifactSummary`, `RunPage`, `IncompleteRun`, `SucceededRun`,
`FailedRun`, `FindingPage`, and `Finding` are public analysis result types. The
same `ArtifactSummary` and concrete Run variants are reused directly by
`SessionGraph`; there is no parallel Artifact page or graph-node class
hierarchy. `RunRecord` remains union shorthand in annotations and prose rather
than another exported identity. Supporting immutable values such as
`RunArgument`, `SessionGraphEdge`, summary counts, and failure shapes resolve as
exact focused type leaves reached through their owning result. They do not
become top-level `__all__` exports or discovery members.

Every public result type named above joins the `__all__` snapshot, public result
protocol where terminal, type resolver, API reference, and Help reachability
checks exactly once. Nested value objects and the `RunRecord` shorthand are
tested through their owner and do not expand the root surface.

Public errors named by this design join the existing Analysis error family,
`__all__` snapshot, API reference, and focused error Help. They do not implement
the result protocol or enter root discovery.

## SessionGraph Contract

### Entrypoint

```python
session.graph(
    *,
    artifact_ref: str | None = None,
    direction: Literal["ancestors", "descendants"] = "ancestors",
    max_nodes: int = 100,
) -> SessionGraph
```

With no `artifact_ref`, the method returns the bounded overall Session graph.
For an overall graph, `direction` must retain its default. With `artifact_ref`,
`direction="ancestors"` returns the bounded producer/input ancestry required to
create that Artifact; `direction="descendants"` returns bounded consuming Runs
and their output descendants, including failed and incomplete consumers. The
focused forms use Session Store indexes and do not perform a whole-session scan.

`direction` is the only focused policy slot because upstream provenance and
downstream impact are two distinct recovery questions already required by the
runtime. Unsupported values, or a non-default direction without
`artifact_ref`, raise a structured argument error with a copyable call.

`max_nodes` is bounded to `[1, 500]`. It bounds the combined number of selected
Artifact summaries and Run records, not only rendered text.
`SessionGraph.show()` is separately constrained by the normal result byte
budget.

Graph pagination is rejected. Pages split topology, make roots and heads
relative to a page, and encourage incorrect joins. A large Session uses one of
the exact Artifact-focused directions instead.

### Graph collections

The Graph is bipartite but does not introduce graph-specific copies of Artifact
and Run records. It exposes:

```python
artifacts: tuple[ArtifactSummary, ...]
runs: tuple[IncompleteRun | SucceededRun | FailedRun, ...]
edges: tuple[SessionGraphEdge, ...]
```

`ArtifactSummary` still excludes rows, complete digest items, complete issues,
and current revalidation state. Every Run value retains its concrete lifecycle
variant. Separate tuple fields provide the identity-family discriminator, so
there is no generic node bag or `SessionGraphNodeRef` wrapper.

### Edge model

One immutable edge shape covers the three legal relations without duplicating
source and target fields:

```python
class SessionGraphEdge:
    kind: Literal["consumes", "produces", "reuses"]
    run_id: str
    artifact_ref: str
```

Direction is fixed:

```text
Artifact --consumed_by--> Run
Run      --produced-----> Artifact
Run      --reused-------> Artifact
```

An edge with `kind="produces"` is legal only when the Artifact's canonical
`produced_by_run` equals the Run id.

An edge with `kind="reuses"` is legal only when the Run returned an already
committed Artifact and the Artifact's canonical producer is another Run. Reuse
must not rewrite Artifact metadata or impersonate a second producer.

Input ordering has no graph meaning. A Run record retains input order for exact
attempt inspection, but Graph edges are normalized and deterministically
sorted.

### Roots, heads, and attention sets

`SessionGraph` exposes:

```python
session_id: str
artifacts: tuple[ArtifactSummary, ...]
runs: tuple[IncompleteRun | SucceededRun | FailedRun, ...]
edges: tuple[SessionGraphEdge, ...]
root_run_ids: tuple[str, ...]
head_artifact_refs: tuple[str, ...]
failed_run_ids: tuple[str, ...]
incomplete_run_ids: tuple[str, ...]
boundary_artifact_refs: tuple[str, ...]
boundary_run_ids: tuple[str, ...]
truncated: bool
```

Every id tuple is a subset of the records selected into this `SessionGraph`.
Root and head membership is nevertheless evaluated against the complete
Session Store snapshot, not relative to the selected subgraph. Failed and
incomplete ids likewise name only selected attention Runs. Boundary tuples name
selected records with omitted adjacency; any other unselected records are
reported only by `truncated=True` and the readable omission summary.

A root Run consumes no Session Artifact. It may still depend on governed
semantic definitions, datasource snapshots, or external values retained in its
Artifact lineage. Those remain Run/Artifact metadata and are not promoted to
graph nodes in V1.

A head Artifact has no indexed `consumed_by` edge to a succeeded Run in the
complete Session Store snapshot. This definition is global and does not change
when a focused or truncated Graph selects only part of the Session. A failed or
incomplete Run may consume a head Artifact; the Artifact remains a materialized
head while the Run appears in the attention set.

The two boundary tuples identify selected records with omitted adjacent records
when the `max_nodes` bound truncates a graph. Separate Artifact and Run tuples
avoid another public identity wrapper and prevent a bounded subgraph root or
head from being mistaken for a complete-session root or head.

### Deterministic selection

When the complete overall graph exceeds `max_nodes`, selection is deterministic
and attention-first:

1. select failed and incomplete Runs, newest first;
2. select head Artifacts, newest first;
3. select the producing/reusing Runs and recursively required ancestors;
4. select remaining newest succeeded Runs and their outputs;
5. stop before exceeding `max_nodes`;
6. retain only edges whose endpoints are selected;
7. set `truncated=True` and report the two boundary tuples when any discovered
   adjacency was omitted.

Within equal timestamps, ids provide the stable tie-breaker. Graph rendering
uses a deterministic topological order. A cycle is integrity corruption and
never falls back to timestamp order.

In ancestor-focused mode, selection starts from the exact Artifact and follows
producer and input ancestry before applying the same bound. In
descendant-focused mode, it starts from the exact Artifact and follows consuming
Runs and their produced or reused outputs. Both modes always retain the focus
Artifact; if the remaining budget cannot retain one adjacent record, that
adjacency is reported at the boundary rather than dropping the focus.

Focused traversal orders records by causal distance from the focus first, then
by canonical timestamp and id. This keeps the nearest explanation or impact
visible before distant history and gives both directions one inexpensive,
deterministic queue discipline.

### Graph purity

`session.graph()`:

- reads one Session Store snapshot and immutable Artifact metadata;
- validates Session ownership and identity agreement;
- does not load Artifact parquet rows;
- does not open the Evidence ledger merely to repeat metadata;
- does not execute SQL or contact a datasource;
- does not revalidate semantic authority;
- does not recompute quality or Evidence;
- does not mutate Session state;
- does not infer analytical importance or recommend an operator.

An unavailable Evidence store therefore does not prevent Graph construction.
The Graph reports the Artifact's committed `evidence_status` and makes no claim
about current ledger readability.

## Graph Construction and Integrity

### Sources of truth

The projection uses each source only for facts it owns:

| Fact | Authority |
| --- | --- |
| Session membership and Artifact index | Session Store |
| Run identity, capability, lifecycle, inputs, output, reuse, timing, safe arguments | Session Store canonical Run record |
| Artifact identity, canonical producer, family, state, Evidence, quality | Artifact metadata |
| historical computation ancestry | Artifact lineage, as an integrity cross-check |
| exact Findings and derivations | Evidence ledger, not read by Graph |
| current semantic authority | explicit revalidation, not read by Graph |

The Session Store canonical Run record owns output mode and exact input refs;
the normalized Run-input relation is only a navigation index. Artifact metadata
owns the canonical producer. Graph construction requires these values to agree.
The SQLite read transaction fixes the Run/Artifact index snapshot before any
immutable Artifact metadata is read, so a concurrent Run transition cannot
create a mixed incomplete/terminal view.

### Sidecars

Component and Coverage sidecars do not enter the Graph Artifact collection.
Their parent Artifact owns their analytical role. Graph integrity may validate
an existing sidecar relationship while keeping it outside the public topology.

### Reused Artifacts

Content-addressed reuse creates a new succeeded Run and no new Artifact. The
Run points to the existing Artifact through a `kind="reuses"` edge. The
Artifact keeps its original `produced_by_run`, creation time, purpose, digest,
quality, and content hash.

### Missing records

The graph fails closed when any selected fact is structurally contradictory:

- a Run input names an Artifact registered to another Session;
- a produced output is missing from the Session Store;
- a Run claims `produced` but Artifact metadata names another producer;
- a Run claims `reused` but Artifact metadata names the same Run as producer;
- Artifact metadata and Session Store disagree on identity or content hash;
- duplicate normalized Run inputs appear where the persisted contract forbids
  them;
- lineage names a different current step or input set from the producing Run;
- an edge creates a directed cycle;
- a Run record has an unsupported schema or lifecycle state.

The graph does not silently omit corrupted selected records. Corruption outside a
bounded focused ancestry does not block the focused graph unless an owning
index invariant cannot be trusted.

### Scan bound

An overall graph requires an exact hard scan bound in addition to
`max_nodes`. The implementation defines a private, tested bound derived from
the Session Store read budget. If the Session exceeds it, the overall read
raises a structured size error and teaches bounded `session.runs(...)` browsing
followed by the exact ancestor- or descendant-focused call.

The hard bound is not a user policy parameter. Raising it is an implementation
and resource-contract change, not an analysis choice.

Focused traversal does not pre-count the complete reachable subgraph. It stops
when the next indexed adjacency would exceed `max_nodes`, marks the selected
boundary, and returns `truncated=True`. This keeps focused reads bounded even
when one Artifact has a very large downstream impact graph.

## Run Lifecycle Persistence

### Admission boundary

A Run begins only after:

- the Session is resolved and writable;
- capability inputs are structurally valid;
- every Artifact input has exact same-Session ownership;
- input Artifact refs and the safe argument projection are normalized;
- a stable Run id is allocated.

Errors before this point remain call-site errors and do not create Run history.

### State transitions

The only legal transitions are:

```text
absent -> incomplete -> succeeded
absent -> incomplete -> failed
```

There is no retry transition on one Run id. A retry is a new Run that may
produce or reuse the same content-addressed Artifact.

Before backend or Artifact execution, the runtime persists an incomplete Run
record. If that write fails, execution does not start.

After successful Artifact publication, including its independently recorded
Evidence status, the runtime atomically updates the Session Store Run row to
the succeeded variant and publishes its output relationship. Existing Artifact
publication and recovery ordering remains authoritative; the Graph must never
show a succeeded producer for an Artifact whose canonical commit is absent.

If a process stops after canonical Artifact/Evidence publication but before the
succeeded Run transition, Session recovery reconciles the incomplete Run before
Graph projection. A fully validated Artifact whose `produced_by_run` matches
the incomplete Run deterministically completes that Run with the Artifact ref,
`output_mode="produced"`, and the Artifact commit time as the terminal time. An
Artifact with `evidence_status="partial"` or `"unavailable"` is still eligible:
Evidence availability and quality do not determine Run lifecycle. Only an
absent canonical commit, producer/identity mismatch, invalid Artifact schema,
content corruption, or incomplete publication prevents completion. Existing
Artifact recovery or rollback runs first, and the Run remains incomplete until
the committed Artifact facts prove a terminal result. This is recovery of an
already committed output, not inference from a missing response.

When an admitted Run raises any ordinary `Exception`, the runtime persists a
sanitized failed variant and then re-raises the original exception. A known
`AnalysisError` contributes only allowlisted, bounded structured fields. An
unknown exception persists `error_type="InternalExecutionError"` and a generic
safe message; it never persists the original message, repr, stack, locals, SQL,
or backend payload. A Run failure record is diagnostic state, not an alternative
non-raising outcome.

Abrupt process termination or a `BaseException` not handled as an ordinary
execution failure may leave `IncompleteRun`. The runtime does not infer whether
the process is still alive, retry automatically, or label the Run failed. A
later explicit retry creates a new Run.

### Persistence failure precedence

Run history is part of the Session contract, not best-effort telemetry:

- failure to persist the initial incomplete record prevents execution;
- failure to publish a succeeded terminal record follows the existing
  Artifact/Session recovery contract and must not claim a complete Run;
- failure to persist a failed terminal record raises a structured Session
  persistence error chained from the original exception and identifies
  that Run recovery is incomplete;
- no path silently turns persistence failure into an empty Run page or missing
  Graph record.

The implementation must preserve safe structured error fields without storing
secrets, raw locals, or unbounded backend messages.

## Rendering Contract

### Session show

Example shape:

```text
Session id=sess_... name=revenue_investigation
mode: writable
question: Why did weekly revenue fall?
artifacts: total=7 heads=2 evidence_complete=5 partial=1 unavailable=1
runs: succeeded=7 failed=1 incomplete=0
attention:
- run_... failed compare; call session.get_run('run_...').show()
heads:
- delta_... DeltaFrame evidence=complete
- attr_... AttributionFrame evidence=partial
current authority: not checked; call session.revalidate('<ref>')
source freshness: not checked by Session reads
available:
- .graph()
- .graph(artifact_ref='<ref>', direction='ancestors')
- .graph(artifact_ref='<ref>', direction='descendants')
- .artifact(ref)
- .runs(...)
- .get_run(run_id)
- .revalidate(ref)
...
```

The concrete card remains within the Session render budget. Counts are exact
for the Session Store snapshot used by the read; listed attention and head
items are explicitly bounded. When the indexed Session size exceeds the Graph
scan bound, the card does not advertise an overall `.graph()` call that will
fail. It states that the overall graph is too large and advertises Run paging
plus the two focused call templates instead.

### SessionGraph show

The readable form is a deterministic textual topology, not a table dump or
business narrative:

```text
SessionGraph session=sess_... artifacts=4 runs=4 edges=7 heads=1
attention:
- run_04 failed attribute; inputs=[delta_02]
flow:
- run_01 observe -> metric_01 [produced evidence=complete]
- metric_01 -> run_02 compare
- metric_00 -> run_02 compare
- run_02 compare -> delta_02 [produced evidence=complete]
- delta_02 -> run_03 attribute
- run_03 attribute -> attr_03 [produced evidence=partial]
- delta_02 -> run_04 attribute [failed]
heads:
- attr_03 AttributionFrame evidence=partial quality=warning
boundaries:
- semantic authority is not checked by SessionGraph
- datasource freshness is not checked by SessionGraph
available:
- .artifacts
- .runs
- .edges
- .head_artifact_refs
- .failed_run_ids
- .show()
```

The renderer:

- shows failed and incomplete Runs before the flow;
- uses public Artifact and Run vocabulary;
- distinguishes produced from reused output;
- reports truncation and boundary records;
- never calls an incomplete selected topology the complete Session DAG;
- never renders Evidence items or data rows;
- provides exact `session.artifact(ref)`, `session.get_run(id)`, or focused
  `session.graph(artifact_ref=..., direction=...)` recovery calls when detail
  is omitted.

## Error Contract

Add or rename structured errors so every public path states expected, received,
location, and a current executable repair:

| Error | Trigger | Repair direction |
| --- | --- | --- |
| `ArtifactNotFoundError` | exact Artifact ref absent from Session | inspect `session.runs(...)` or `session.graph()` |
| `RunNotFoundError` | exact Run id absent from Session | use `session.runs(...)` |
| `FindingNotFoundError` | exact Finding id absent from the owning Artifact | use `artifact.findings(...)` |
| `SessionGraphLimitError` | `max_nodes` outside `[1, 500]` | pass the bounded value |
| `SessionGraphArgumentError` | focused direction is invalid or lacks `artifact_ref` | pass an exact ref and one supported direction |
| `SessionGraphTooLargeError` | overall scan exceeds the hard bound | page Runs, take an exact Artifact ref, then call one focused graph direction |
| `SessionGraphIntegrityError` | selected persisted facts disagree or cycle | inspect the concrete Run/Artifact ids carried by the error, then regenerate that named computation in a fresh Session if canonical storage is corrupt |
| `SchemaVersionMismatchError` | Session Store or Run payload version is not the exact current contract | create a new named Session; do not migrate or reuse the old state |
| `FrameMetaInvalidError` | Artifact metadata version is not `analysis-artifact/v13` | create a new named Session and rerun the analysis |
| existing Artifact corruption errors | exact Artifact bytes/meta invalid | preserve existing typed repair |
| existing Session lock errors | Session persistence is locked | preserve existing ownership repair |

`ArtifactNotFoundError` replaces the Frame-named recovery error in the atomic
cutover, together with the complete active public contract, tests, Help, and
docs. There is no compatibility subclass or duplicate public error spelling.

An empty `SessionGraph`, `RunPage`, or `FindingPage` means the healthy owning
store matched no records. Store unavailability and corruption always raise.

## Help and Disclosure Contract

### Progressive disclosure invariants

This design does not add a seventh analysis hub or expand the analysis root.
`marivo.help("analysis")` remains the bounded root defined by the progressive
Help design: six decision hubs plus the exact terminal boundary. This design
changes membership only inside the existing `analysis.runtime` and
`analysis.evidence` hubs.

The existing render classes and budgets remain unchanged:

- `analysis.runtime` and `analysis.evidence` are `decision_hub` pages;
- `analysis.runtime.runs` is a `navigation` page;
- Session and Artifact methods are `exact_callable` leaves with reflected
  signatures and at most one minimal example;
- public result types are `public_type` leaves reached from their producer or
  consumer contract;
- error classes use focused error Help and error instances use the bounded
  `current_briefing` contract.

Root, hub, and navigation pages contain no signatures, parameter tables,
examples, public-type field inventories, or error catalogs. Membership is an
explicit registry fact, never inferred from dotted prefixes or copied into the
renderer. Every exact callable has one discovery owner; output-type, error,
Artifact-type, result-card, and structured-repair routes are cross-links rather
than duplicate membership.

### Analysis runtime navigation

`marivo.help("analysis.runtime")` remains the decision page for persisted
runtime facts rather than storage identities:

```text
runtime
  -> runtime.sessions   # create, locate, inspect, resume, delete
  -> session.graph      # understand one resumed Session
  -> session.artifact   # recover one exact Artifact
  -> runtime.runs       # list and inspect Runs
```

The previous `runtime.artifacts` page is removed. After `frame_summaries()` is
deleted it would contain only one recovery callable, so the progressive
singleton rule requires `analysis.runtime` to route directly to
`analysis.session.artifact`. Artifact revalidation and Finding audit are
Evidence questions, not runtime-recovery membership.

`runtime.runs` owns:

```text
session.runs
session.get_run
```

`RunPage` and the three concrete Run variants are return-type cross-links from
those exact leaves. They are not extra navigation members. `session.graph` and
`session.artifact` are direct singleton routes owned by `analysis.runtime`;
`session.runs` and `session.get_run` are discovery-owned by
`analysis.runtime.runs`.

### Evidence navigation

`marivo.help("analysis.evidence")` preserves proof-boundary routing but makes
ownership explicit:

```text
Artifact commit-time Evidence -> BaseFrame.show / artifact.evidence_digest
browse Artifact Findings      -> artifact.findings
read one exact Finding        -> artifact.finding
current Artifact authority    -> session.revalidate
quality                       -> Artifact quality summary and issues
source freshness              -> outside this Session surface
```

The previous `evidence.browse` and `evidence.exact` groupings are removed.
Each would become a one-callable pass-through after the Session Evidence
namespace is deleted. `analysis.evidence` therefore discovery-owns the exact
`artifact.findings`, `artifact.finding`, and `session.revalidate` leaves
directly. `BaseFrame.show`, the Artifact type page, `ArtifactDigest`,
`FindingPage`, `Finding`, `ArtifactRevalidation`, and quality are typed
cross-links owned by their existing contracts. There is no Session Evidence
namespace, second Artifact-digest route, separate trace result, or
selection-compatibility target.

### Focused leaves

Every new callable and public result has one exact leaf:

```text
analysis.session.graph
analysis.session.artifact
analysis.session.runs
analysis.session.get_run
analysis.session.revalidate
analysis.artifact.findings
analysis.artifact.finding
analysis.SessionGraph
analysis.ArtifactSummary
analysis.FindingPage
analysis.Finding
analysis.RunPage
analysis.IncompleteRun
analysis.SucceededRun
analysis.FailedRun
```

Supporting values remain exact resolvable leaves without discovery ownership:

```text
analysis.ArtifactEvidenceSummary
analysis.RunArgument
analysis.RunFailure
analysis.SessionGraphEdge
```

Every new or renamed public error also has one exact leaf:

```text
analysis.ArtifactNotFoundError
analysis.RunNotFoundError
analysis.FindingNotFoundError
analysis.SessionGraphLimitError
analysis.SessionGraphArgumentError
analysis.SessionGraphTooLargeError
analysis.SessionGraphIntegrityError
analysis.SchemaVersionMismatchError
analysis.FrameMetaInvalidError
```

All removed ids fail as unknown targets. The ordinary bounded lexical
suggestion mechanism may show nearby installed canonical ids, but there is no
migration map, alias descriptor, fallback resolver, or automatic translation
from an old id.

### Object-near disclosure

`Session.show()` advertises `graph`, Artifact recovery, and Run inspection
without expanding their signatures.

`SessionGraph.show()` points to exact Run and Artifact reads. It never lists
analysis operators as recommendations.

`Artifact.show()` remains the only immediate Evidence summary, reports
the exact `finding_count`, and points to Artifact-scoped raw audit when the
count is nonzero. It updates recovery text from `session.get_frame(ref)` to
`session.artifact(ref)`.

## Packaged Skill Contract

The packaged `marivo-analysis` skill must change in the same atomic cutover.

It teaches:

1. resume only an exact current-format Session; on a schema mismatch, create a
   new named Session rather than migrating or reusing old state;
2. after resuming an unfamiliar current-format Session, call `session.show()`;
3. call `session.graph()` only when cross-Artifact structure matters;
4. use the ancestor-focused graph for provenance and the descendant-focused
   graph for downstream impact when the overall graph is too large;
5. recover an exact result through `session.artifact(ref)`;
6. inspect failed or incomplete execution through `session.get_run(id)`;
7. trust `artifact.show()` for immediate commit-time Evidence, use
   `artifact.evidence_digest` for structured digest access, and use
   `artifact.findings()` / `artifact.finding(id)` for canonical raw audit;
8. revalidate explicitly when current semantic authority matters;
9. never treat Graph structure, Evidence completeness, or successful Runs as a
   business conclusion or datasource-freshness proof.

The skill does not teach multi-Finding combination or claim that two Findings
are mechanically safe to use together.

The skill does not copy signatures, result fields, graph ordering rules, error
catalogs, or render budgets. Those remain live runtime facts.

## Documentation Contract

The same change set updates:

- `docs/specs/analysis/session-state-and-runtime.md`;
- `docs/specs/analysis/evidence-access-surface.md`;
- `docs/specs/analysis/operators-and-frames.md` where recovery calls appear;
- `docs/specs/analysis/python-analysis-design.md`;
- `docs/specs/agent-friendly-public-surface.md`;
- generated API reference inputs;
- current English and Chinese analysis workflow pages;
- current English and Chinese Evidence pages;
- release notes for the breaking cutover;
- the packaged `marivo-analysis` skill.

Historical versioned site documentation remains historical. Current docs must
not teach both old and new public names. Current Session/runtime guidance states
that pre-cutover state is incompatible, remains untouched, and must be replaced
by a newly named Session rather than migrated or imported.

## Persistence and Incompatible State

### Session Store

The Session Store remains the only mutable runtime index. A new Session is
created directly with Session Store `PRAGMA user_version = 1` and canonical Run
payload schema `marivo.analysis_run/v1`. The canonical Run row owns its closed
lifecycle variant, capability id, safe argument projection, input refs, output
mode/ref, timing, and safe failure. Terminal transition and output relationship
update occur in one SQLite transaction.

A normalized `run_inputs(session_id, run_id, artifact_ref, position)` relation
is maintained in that transaction. It is a Run lookup index, not a persisted
Session graph or second lineage authority. It supports focused descendant
navigation without scanning every Run. `position` preserves execution input
order for exact Run reads; Graph edges continue to ignore input order.

Canonical Run state is not split between JSON and SQLite. The old
`marivo.analysis_job/v2` files are neither written nor read by this contract.
The implementation must not persist a `session_graph` table or graph JSON
snapshot.

### Incompatible pre-cutover state

Pre-cutover Session state is intentionally not reusable. `session.resume(...)`
validates the Session Store version before any mutation or recovery work. A
Store other than exact version 1, a Run payload other than
`marivo.analysis_run/v1`, or Artifact metadata other than
`analysis-artifact/v13` fails closed.

There is no decoder, migration, import, backfill, state rewrite, dual read, or
fallback to `marivo.analysis_job/v2` or `analysis-artifact/v12`. The failure
uses `SchemaVersionMismatchError` for Session Store or Run payload mismatch and
the existing `FrameMetaInvalidError` for Artifact schema mismatch. Both errors
state expected, received, location, and the exact repair: preserve or move the
entire old `.marivo/analysis` directory, initialize one fresh Store, create a
new Session with a new name, and rerun the required analysis. A new Session in
the incompatible Store is not a repair. The errors never suggest reopening,
upgrading, or partially copying the old Session.

The old Session directory remains byte-for-byte untouched after the error.
Session discovery may list its bounded identity without claiming runtime
readability, but `show()`, `runs()`, `artifact()`, `graph()`, and revalidation
never project old runtime facts. The repair creates fresh state; it does not
reuse old Artifact refs, job ids, Evidence rows, or lineage.

### Atomic cutover

The public cutover is released only when:

- new runtime reads and result types are complete;
- Graph and Run persistence are complete;
- old Session methods and the complete Session Evidence namespace are removed;
- removed singleton/legacy navigation topics are absent and all advertised
  registry/Help references resolve to canonical ids;
- fresh state uses only Store v1, Run payload v1, and Artifact v13, while
  incompatible state fails without mutation or migration;
- Artifact recovery strings are updated;
- packaged skill and current bilingual docs are updated;
- public export, typing, persistence, and agent-journey gates pass.

There is no state where both `get_frame()` and `artifact()` or both
`jobs()` and `runs()` are advertised.

## Rejected Alternatives

### Add only `session.status()`

Rejected. A scalar or mega-status conflates Run lifecycle, Artifact Evidence,
quality, current authority, and source freshness. It would be easy to read and
wrong to trust.

### Add `session.graph()` without changing existing reads

Rejected. This would add another recovery path while leaving storage-shaped
vocabulary, unbounded jobs, untyped job records, and duplicate digest browsing
in place.

### Persist `AnalysisSnapshot` or `graph.json`

Rejected. It creates a second state authority that can drift from Session
Store, Run records, Artifact metadata, and the Evidence ledger.

### Migrate or decode pre-cutover Session state

Rejected. Supporting job-v2, Artifact-v12, or unknown future schemas would add
decoder, backfill, dual-read, partial-migration, and repair branches for state
that the user does not require. The exact current schemas fail closed and teach
creation of a new named Session; old bytes remain untouched.

### Return `ArtifactHandle` wrappers

Rejected. The existing concrete Artifact already owns immediate reading,
contract, lineage, Evidence digest, quality, and terminal row access. A wrapper
would add another mandatory step and identity surface.

### Persist raw Findings inside Artifact metadata

Rejected. Each Finding is owned and publicly scoped by one Artifact, but the
Session Evidence ledger remains the canonical indexed storage authority.
Embedding every Finding in Artifact metadata would make Artifact recovery and
`artifact.show()` unbounded. New Artifacts store only bounded Evidence metadata
and `finding_count`; explicit `artifact.findings()` reads the ledger.

### Expose a raw adjacency dictionary

Rejected. `dict[str, Any]` loses node identity families, edge semantics,
closed Run variants, deterministic rendering, structured errors, and public
type guidance.

### Use Graph pagination

Rejected. A page cannot truthfully own complete roots, heads, adjacency, or
acyclicity. Bounded whole-session and exact Artifact ancestor/descendant
projections are the supported modes.

### Render Mermaid or Graphviz in V1

Rejected. The structured Graph already enables external visualization. A
format-specific API adds dependencies and output contracts without improving
the core recovery decision.

## Validation Strategy

### Result and typing contracts

Tests must prove:

- every new public result has a bounded deterministic `repr`; terminal results
  additionally have bounded deterministic `render()` and `show()`;
- `session.get_run()` returns the exact closed Run variant, never a dict;
- pages expose immutable tuples, exact limits, and opaque keyset cursors;
- exact Run records expose bounded safe argument facts rather than only an
  opaque parameter digest;
- `artifact.evidence_digest` returns the Artifact's immutable commit-time
  snapshot without a second Evidence Store lookup or recomputation;
- `artifact.finding_count` and `ArtifactEvidenceSummary.finding_count` are exact
  non-negative integers for every exact current-schema Artifact;
- `artifact.findings()` is bounded, Artifact-scoped, and explicit about ledger
  I/O;
- exact `artifact.finding(id)` rejects a Finding owned by another Artifact;
- each exact Finding carries its complete derivation trace without a second
  public read;
- public Help, exports, structured repairs, digest continuations, skills, and
  current docs contain no `session.evidence` route,
  `EvidenceDigestNotAvailableError`, Finding-selection compatibility API, or
  multi-Finding combination promise;
- graph records reuse `ArtifactSummary` and the concrete Run variants instead
  of duplicating public node variants;
- no new public callable or type exposes `Any`;
- Artifact, Run, and Finding ids cannot be silently interchanged;
- public exports, API reference, type resolver, and result protocol snapshots
  agree;
- each new or renamed error class and concrete instance resolves to focused
  Help with expected, received, location, and a canonical repair.

### Graph topology

Deterministic fixtures must cover:

- an empty Session;
- one root observe Run and Artifact;
- a linear observe -> compare -> attribute chain;
- two input Artifacts merged by compare;
- one Artifact branching into several Runs;
- content-addressed Artifact reuse with one canonical producer;
- failed Run with no output;
- incomplete Run after abrupt termination;
- failed downstream Run while the consumed Artifact remains a head;
- sidecars excluded from graph Artifact summaries;
- same timestamps ordered by id;
- whole-session truncation and separate Artifact/Run boundary tuples;
- focused ancestor projection;
- focused descendant projection with failed and incomplete consumers;
- focused lookup through the normalized Run-input index without a full scan;
- missing Run record, missing Artifact, cross-Session input, producer mismatch,
  unsupported schema, and directed cycle;
- stable normalized ordering across repeated cold reads;
- changed returned facts after one committed Run or Artifact fact changes.

### Status boundaries

Tests must independently prove:

- complete Evidence does not imply current semantic authority;
- current semantic authority does not imply datasource freshness;
- successful Run does not imply complete Evidence or clean quality;
- failed Run does not mutate or invalidate an upstream Artifact;
- unavailable Evidence store does not become an empty Finding page and does not
  prevent metadata-only Graph construction;
- Graph construction never calls datasource execution, revalidation, quality
  evaluation, or Evidence projection.

### Persistence and recovery

Tests must cover:

- no operator execution before incomplete Run persistence succeeds;
- legal Run transitions only;
- succeeded Artifact publication and Run completion ordering;
- successful recovery from complete, partial, and unavailable Evidence states;
- sanitized failed Run persistence followed by re-raising the original
  exception;
- safe generic failure persistence for an unknown ordinary exception;
- secret-, SQL-, and oversized-message redaction in every persisted failure
  field;
- persistence failure precedence and chaining;
- Artifact reuse without producer metadata rewrite;
- fresh Sessions initialize exact Store version 1, Run payload v1, Artifact
  metadata v13, and the normalized Run-input index directly;
- Store versions other than 1, Run payloads other than
  `marivo.analysis_run/v1`, and Artifact metadata other than
  `analysis-artifact/v13` fail before any runtime fact is projected;
- incompatible-state failure leaves the old Session directory byte-for-byte
  unchanged and teaches creation of a newly named Session;
- no decoder, migration, import, backfill, dual read, job-v2 fallback, or
  Artifact-v12 fallback exists in runtime code;
- recovery after interruption between Artifact commit and Run completion;
- one-snapshot reads while another process transitions a Run;
- Session lock behavior under parallel processes.

### Help and docs

Tests must prove:

- each new focused leaf resolves from string, callable/type object, and exact
  runtime instance where applicable;
- removed targets are absent from the released registry and root topology;
- the analysis root remains the six existing decision hubs plus the exact
  terminal boundary, with no Session/Run/Graph inventory added to it;
- `analysis.runtime` discovery-owns direct `session.graph` and
  `session.artifact` routes plus `runtime.sessions` and `runtime.runs`;
- `analysis.runtime.runs` owns exactly `session.runs` and `session.get_run`;
- `analysis.evidence` discovery-owns `artifact.findings`, `artifact.finding`,
  and `session.revalidate`, while result/type/error links remain cross-links;
- `runtime.artifacts`, `runtime.jobs`, `evidence.browse`, and `evidence.exact`
  are absent because the target topology would make them aliases or singleton
  pass-through pages;
- every exact callable has one discovery owner, every registered navigation
  page has at least two members, and renderer/prefix inference owns no members;
- every route emitted by root, hub, grouping, result, contract, and error output
  resolves;
- old Help ids fail normally with no migration map or fallback resolution;
- root, decision-hub, navigation, exact-callable, public-type, and
  current-briefing budgets are enforced independently;
- Session and Graph cards stay within independent line/codepoint/byte budgets;
- current English and Chinese docs contain new names and reject old names;
- packaged skill guidance contains boundaries and routes but no copied API
  inventory.

### Agent acceptance

All positive journeys use newly created exact current-schema Sessions. One
separate negative journey proves that resuming pre-cutover state fails before
mutation and teaches creation of a new named Session. At least six isolated
cold-start recovery journeys must demonstrate:

1. identify the current head Artifact from a branched Session;
2. locate the Run that produced one Artifact;
3. identify a failed downstream Run in an oversized Session through focused
   descendant navigation without treating its input as failed;
4. distinguish partial Evidence from stale or unchecked semantic authority;
5. distinguish two Runs with the same capability and inputs but different safe
   arguments;
6. recover one exact Artifact, inspect its bounded digest, then recover exact
   Findings omitted from that digest through the same owning Artifact.

Acceptance targets:

- the ordinary Session shape is recovered with `session.show()` plus at most
  one `session.graph()` call;
- no journey manually joins `jobs()` and `frame_summaries()`;
- no journey calls a second Session digest API after `artifact.show()`;
- no answer claims datasource freshness, business validity, or recommended
  next action from Graph topology;
- every recovery call used by the agent is a canonical Help-resolvable public
  path.

Model-backed acceptance belongs in `marivo-agent-evals`; deterministic topology,
persistence, typing, Help, and rendering gates belong in this repository.

## Delivery Slices

Implementation is divided into exactly three dependent but independently
verifiable slices. "Independently verifiable" means that each slice ends in a
bounded behavioral invariant, dedicated automated tests, and a reviewable diff;
it does not mean the slices can land in arbitrary order or that an intermediate
commit is a public release. Slice 1 and Slice 2 keep candidate read services and
types internal. The public release remains the atomic Slice 3 cutover.

Each slice must pass its own focused gate before the next begins. Later slices
rerun the earlier focused gates; they do not defer unfinished tests or repairs
to the final full suite.

### Slice 1: persisted runtime truth

Outcome: the Session Store can represent and recover every target Run lifecycle
fact, and incompatible old state fails before mutation, without advertising any
target runtime API.

Implementation scope:

- add the canonical Run payload and normalized `run_inputs` index;
- place one admission wrapper before materializing Capability execution and
  persist `IncompleteRun` before backend work starts;
- atomically publish succeeded and sanitized failed terminal variants;
- implement interruption reconciliation, persistence-failure precedence, and
  one-snapshot/lock behavior;
- initialize new Sessions directly with Store version 1 and canonical
  `marivo.analysis_run/v1` payloads;
- require `analysis-artifact/v13` and persist exact `finding_count` on every
  exact current-schema Artifact commit;
- reject any other Store, Run, or Artifact schema before recovery or mutation,
  with no decoder, migration, backfill, import, or legacy fallback;
- keep every new type, adapter, and Store read private; the staged Artifact
  metadata field is not documented as public until Slice 3, and public exports,
  Session methods, Help, skills, and current docs remain unchanged in this
  slice.

Independent verification:

- transition tests cover only `absent -> incomplete -> succeeded|failed` and
  prove execution never starts when initial persistence fails;
- failure tests cover allowlisting, recursive bounds, secret/SQL redaction,
  unknown exceptions, re-raise behavior, and terminal-write failure chaining;
- recovery tests cover reuse, partial/unavailable Evidence, interruption after
  Artifact commit, parallel readers, and process locks;
- schema tests prove v1/v13 exact reads, v2/v12/future-version rejection,
  byte-for-byte preservation of old Session state, and new-Session repair;
- Artifact metadata tests prove exact non-negative counts for every exact
  current-schema Artifact.

Focused gate:

```bash
make test TESTS='tests/test_analysis_run_persistence.py tests/test_analysis_runtime_schema.py tests/test_analysis_evidence_pipeline.py tests/test_analysis_session_store.py tests/test_public_surface.py'
make typecheck
make lint
```

Exit criterion: canonical persisted records can be reconstructed and validated
without Graph construction or any new public symbol, and the public export/Help
snapshots are unchanged. This intermediate schema-bearing commit is not a
releasable public version.

### Slice 2: typed internal read model and Graph

Outcome: the complete target read behavior works through internal candidate
services over Slice 1 persistence, without exposing a half-cut-over public
surface.

Implementation scope:

- define `ArtifactSummary`, `ArtifactEvidenceSummary`, `FindingPage`, the three
  concrete Run variants, `RunPage`, `SessionGraph`, its edge value, boundaries,
  typed errors, and bounded renderers;
- implement internal bounded Run paging and exact Run recovery with safe
  arguments;
- implement exact Artifact recovery, ref-based revalidation, Artifact-scoped
  Finding paging/exact ownership checks, and full derivation fields;
- implement overall and ancestor/descendant-focused Graph construction,
  deterministic selection, reuse, integrity validation, and purity;
- implement the bounded Session recap and object-near continuation builders;
- keep the candidate methods detached from the public `Session`/Artifact
  surface and absent from `__all__`, Help, the packaged skill, and user docs.

Independent verification:

- read-model tests cover empty pages, pagination, exact variants, unknown ids,
  exact Finding counts, cross-Artifact Finding rejection, and Artifact-family
  cold reconstruction from exact current schemas;
- topology tests cover empty, linear, branch, merge, reuse, failed, incomplete,
  focused, truncated, boundary, cycle, mismatch, and concurrent-transition
  fixtures;
- purity probes fail if Graph construction loads Parquet, opens the Evidence
  ledger, executes a datasource, revalidates, or recomputes Evidence/quality;
- result-protocol tests prove bounded deterministic `repr` for every candidate
  public result and `render()` / `show()` for every terminal result.

Focused gate:

```bash
make test TESTS='tests/test_analysis_runtime_reads.py tests/test_analysis_artifact_evidence_reads.py tests/test_analysis_session_graph.py tests/test_agent_result_protocol.py tests/test_public_surface.py'
make typecheck
make lint
```

Exit criterion: every target read and error can be exercised through the
internal candidate service, all graph facts come from their declared authority,
and the released public vocabulary is still unchanged.

### Slice 3: atomic public cutover and acceptance

Outcome: the verified internal model becomes the one released public runtime
contract, with the old vocabulary and Session Evidence namespace absent.

Implementation scope:

- attach `runs`, `get_run`, `artifact`, `revalidate`, and `graph` to `Session`,
  and attach Finding reads to the owning Artifact;
- export and register only the canonical public result types, including the
  three concrete Run variants; keep `RunRecord` as non-exported annotation
  shorthand;
- remove `frame_summaries`, `get_frame`, `jobs`, `recent_jobs`, `job`, the
  complete `session.evidence` namespace, compatibility result algebra, obsolete
  errors, aliases, renderers, tests, and dynamic continuations;
- preserve the six-hub analysis root, remove the singleton
  `runtime.artifacts`, `evidence.browse`, and `evidence.exact` pages, replace
  `runtime.jobs` with the two-member `runtime.runs` page, and assign every exact
  callable one registry-owned discovery owner;
- update focused leaves, structured repairs,
  Session/Artifact/Graph cards, API reference inputs, the packaged skill,
  current English/Chinese specs and site pages, and release notes atomically;
- add negative absence, reachability, drift, render-budget, clean-wheel, and six
  isolated agent-recovery journey tests.

Independent verification:

- black-box tests import only `marivo.analysis`, resolve every advertised route,
  exercise every new callable and concrete result type, and prove every removed
  symbol and Help id is absent;
- Help graph tests prove root/hub/navigation budgets, singleton rejection,
  unique discovery ownership, cross-link reachability, and absence of renderer
  or prefix-derived membership;
- incompatible Session state fails with canonical structured errors and no
  migration-target or alias fallback;
- current English/Chinese docs and the packaged skill contain only canonical
  calls and preserve Evidence, authority, and freshness boundaries;
- a clean built wheel reproduces Help, Session recovery, Artifact audit, and
  Graph behavior without importing the source checkout;
- the six cold-start journeys satisfy the Agent acceptance targets before the
  cutover is considered complete.

Focused and release gate:

```bash
make test TESTS='tests/test_analysis_runtime_cutover.py tests/test_analysis_help.py tests/test_analysis_help_resolution.py tests/test_agent_api_drift.py tests/test_public_surface.py tests/test_packaged_skill_shape.py tests/test_analysis_runtime_agent_journeys.py'
make test
make typecheck
make lint
make docs-api
cd site && npm run verify:content && npm run build
```

After those repository gates, build and inspect the wheel, run the installed
wheel smoke tests, and run the model-backed journeys in `marivo-agent-evals`.

Exit criterion: the released tree exposes one canonical runtime vocabulary,
all deterministic and model-backed acceptance evidence is recorded, and no
old/new dual path remains.

## Acceptance Criteria

This design is complete only when all of the following are true:

1. `session.show()` provides a bounded factual recap and points to
   `session.graph()`.
2. `session.graph()` returns a typed immutable bounded DAG projection with
   truthful branch, merge, reuse, failure, incomplete, head, truncation,
   ancestor, and descendant semantics.
3. Graph construction uses persisted facts without querying data, recomputing
   Evidence/quality, or performing revalidation.
4. `session.runs()` is the one bounded typed history page; Artifact discovery
   uses Run refs or Graph summaries rather than a second collection.
5. `session.artifact(ref)` and `session.get_run(id)` are exact typed reads, and
   the Run read exposes bounded safe argument facts.
6. `session.revalidate(ref)` is the one-step current-authority read.
7. Artifact digest has one semantic owner and public read path: the Artifact;
   there is no public Session Evidence namespace.
8. Canonical Finding browse and exact audit are Artifact-scoped, every Finding
   carries its derivation trace, and no public multi-Finding compatibility
   promise remains.
9. No public Session collection returns an unbounded list or `dict[str, Any]`.
10. No public scalar combines Run, Evidence, quality, authority, and freshness
    into one status.
11. Failed and incomplete Run persistence is explicit, typed, safe, and
    fail-closed; Evidence availability never determines Run success.
12. Old public names, Help ids, examples, skill text, and current-doc references
    are absent from the released contract.
13. Graph construction reads one Session Store snapshot, and focused descendant
    navigation does not scan every Run.
14. Every new public result type and error class is reachable through canonical
    Help and covered by export, typing, persistence, and drift tests; terminal
    results also satisfy the result protocol, structured errors preserve their
    repair contract, and nested values do not inflate root discovery.
15. The analysis root remains six decision hubs plus the exact terminal leaf;
    runtime and Evidence membership is explicit, singleton navigation pages are
    absent, every exact callable has one discovery owner, and cross-links do not
    create duplicate membership.
16. Only Store version 1, `marivo.analysis_run/v1`, and
    `analysis-artifact/v13` are readable; incompatible Session state fails
    before mutation and is never decoded, migrated, imported, backfilled, or
    reused.
17. Existing unrelated working-tree changes remain untouched during
    implementation.
