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
- canonical Findings and derivation traces remain the raw audit surface;
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
  `session.evidence.digests()` and `session.evidence.digest()`; this design
  removes only the duplicate collection and retains the exact metadata-only
  digest lookup;
- [`2026-07-18-evidence-typed-digest-refactor-design.md`](2026-07-18-evidence-typed-digest-refactor-design.md),
  only for its session recap and recovery API layout;
- [`2026-08-27-progressive-analysis-live-help-design.md`](2026-08-27-progressive-analysis-live-help-design.md),
  only for the `runtime.artifacts`, `runtime.jobs`, Evidence browse, and Evidence
  exact navigation members changed here.

The capability kernel, Artifact family algebra, focused-help ownership,
Evidence semantics, Artifact compatibility, revalidation algorithm, and result
protocol remain authoritative unless this design explicitly changes their
public entry point.

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
  -> Runs perform typed computations
  -> Runs consume zero or more Artifacts
  -> Runs produce or reuse one Artifact
  -> Artifacts carry commit-time Evidence and quality facts
  -> Findings provide exact raw audit records
```

The target public read surface is:

```python
session.show()                          # bounded Session recap
session.graph()                         # bounded factual Session DAG
session.graph(artifact_ref=ref, direction="ancestors")
session.graph(artifact_ref=ref, direction="descendants")
session.artifacts(...)                  # paged Artifact summaries
session.artifact(ref)                   # exact live Artifact recovery
session.revalidate(ref)                 # explicit current authority check
session.runs(...)                       # paged typed execution records
session.run(run_id)                     # exact typed execution record
session.evidence.digest(artifact_ref)   # exact metadata-only Artifact digest
session.evidence.findings(...)          # canonical raw audit page
session.evidence.finding(finding_id)    # exact Finding
session.evidence.trace(finding_id)      # exact derivation trace
session.evidence.compatibility(ids)     # exact selection compatibility
```

The corresponding removals are:

```text
session.frame_summaries
session.get_frame
session.jobs
session.recent_jobs
session.job
session.evidence.digests
```

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

- one collection and one exact read per public identity family;
- one optional Graph entrypoint rather than a persisted snapshot or planner;
- existing `ArtifactSummary`, `RunRecord`, and `ArtifactDigest` values reused
  across pages, exact reads, and Graphs;
- one edge value instead of node and edge class hierarchies;
- one Session Store Run projection and input index instead of a second graph
  authority;
- no public family filter, opaque Graph fingerprint, exact omission counts,
  wrapper handle, or duplicate Run summary type in V1;
- exact digest lookup retained because avoiding a full Parquet load is worth
  more than removing one already implemented drill-down method.

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
jobs even though the agent's conceptual unit is one typed operator run. An
agent must therefore remember three translations:

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

### Artifact digest browsing is duplicated

An Artifact already owns its exact commit-time `evidence_digest`, and
`artifact.show()` renders it. Listing the same family again through
`session.evidence.digests()` creates a second browsing surface and encourages
agents to call two collection APIs for the same Artifact set. The exact
`session.evidence.digest(ref)` read is different: it is a metadata-only index
lookup that avoids loading Artifact rows. It remains as a drill-down access path
to the Artifact-owned snapshot, not a second semantic owner.

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
7. Preserve exact Artifact recovery and raw Finding audit without requiring
   private storage inspection.
8. Keep Graph construction read-only and free of backend queries, Evidence
   recomputation, business judgment, and method recommendation.
9. Keep the public result algebra small by reusing Artifact summaries and Run
   records inside the Graph rather than creating parallel node families.
10. Make Help, result cards, the packaged skill, English/Chinese documentation,
   persistence validation, and public type snapshots one coordinated contract.

## Non-Goals

This design does not add:

- an `AnalysisSnapshot` Artifact;
- a mutable graph, graph authoring API, or graph persistence document;
- a workflow planner, ranked next action, hypothesis manager, or stopping rule;
- a scalar `session.status` or `artifact.health` that combines unrelated axes;
- automatic Artifact revalidation while rendering or building a graph;
- datasource freshness checks;
- causal or business interpretation across Artifact digests;
- Mermaid, Graphviz, NetworkX, HTML, or visualization-specific public output;
- graph pagination;
- public access to raw SQL, credentials, backend objects, stack traces, or
  arbitrary persisted parameter payloads;
- a compatibility period for the removed public names.

The structured `SessionGraph.artifacts`, `.runs`, and `.edges` are sufficient
for an external renderer. A Marivo-owned visualization format requires a
separate demonstrated need.

## Ownership and Vocabulary

### Session

A Session owns one persistent analysis namespace, stable identity, current
question, runtime configuration, Artifacts, Runs, and Finding ledger.

A Session is not one analysis result and has no single analytical conclusion.

### Run

A Run is one admitted invocation of a typed Session operator after Session
ownership and Artifact inputs have been normalized. `Run` is the public term.
`job` is storage vocabulary only.

A Run has exactly one closed lifecycle variant:

```text
IncompleteRun  # admitted and started; no terminal record was committed
SucceededRun   # returned exactly one produced or reused Artifact
FailedRun      # terminated with one sanitized AnalysisError or internal summary
```

Calls rejected before Session ownership and Artifact inputs are normalized do
not become Runs. They remain ordinary structured call-site errors and do not
pollute Session history.

### Artifact

An Artifact is one immutable typed analysis result. `Artifact` is the public
recovery term even when the concrete Python family is `MetricFrame`,
`DeltaFrame`, `CandidateSet`, or another result class.

`ref` remains the single public Artifact identity field end to end. This design
does not introduce an `artifact_id` alias.

### Finding

A Finding is one canonical raw Evidence record with stable identity and exact
derivation. Findings remain under `session.evidence` because they belong to the
Session Evidence ledger and may be selected across Artifacts.

### SessionGraph

A SessionGraph is a bounded immutable projection of persisted Run and Artifact
facts. Its existence does not create a fourth persisted identity authority.

## Status Model

There is no overall Session status.

The following axes remain independent:

| Axis | Owner | Values | Observation time |
| --- | --- | --- | --- |
| Run lifecycle | `RunRecord` | `incomplete`, `succeeded`, `failed` | invocation time |
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
facts remain on bounded Artifact summaries and exact Artifacts. Head derivation
uses the normalized Run-input index rather than constructing the Graph. The
recap does not render Artifact digest items, rows, complete Run records, or the
graph.

### Artifact collection

```python
session.artifacts(
    *,
    evidence_status: EvidenceStatus | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> ArtifactSummaryPage
```

This replaces `session.frame_summaries()`.

`ArtifactSummaryPage` is newest-first keyset pagination with the existing
`items`, `limit`, `has_more`, and opaque `next_cursor` contract. `limit` remains
bounded to `[1, 100]`.

Linked Component and Coverage sidecars are always excluded. They are
implementation projections, not independent Session results. V1 deliberately
omits both family filtering and an `include_sidecars` flag: paging and exact
Artifact refs cover the ordinary recovery choices without publishing a second
kind/family vocabulary.

### Exact Artifact recovery

```python
session.artifact(ref: str) -> BaseFrame
```

This replaces `session.get_frame(ref)` while preserving concrete Artifact
families, immutability, exact Session ownership, cold-start reconstruction, and
typed corruption errors.

The return annotation may use the existing closed public Artifact union if it
is available at cutover. It must not widen to `Any` or a wrapper result.

The recovered Artifact continues to expose:

```python
artifact.show()
artifact.contract()
artifact.state
artifact.lineage
artifact.evidence_status
artifact.evidence_digest
artifact.quality_summary
artifact.to_pandas()
```

No `ArtifactHandle`, `ArtifactView`, or second `.load()` step is introduced.

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
recent-jobs use case without a separate method. `RunPage` uses immutable
`items`, `limit`, `has_more`, and opaque `next_cursor`, matching the Artifact
page protocol.

`capability_id` is one exact Help-resolvable capability id. Persisted legacy
intent spellings are normalized by the internal current-schema decoder and are
never accepted as a second public filter vocabulary. An unsupported historical
intent fails typed decoding instead of silently becoming a public alias.

### Exact Run recovery

```python
session.run(run_id: str) -> RunRecord
```

This replaces `session.job(job_id) -> dict[str, Any]`.

`RunRecord` is the closed union:

```python
RunRecord = IncompleteRun | SucceededRun | FailedRun
```

The concrete variant determines which fields exist. There is no optional-field
mega-record with nullable output and error fields.

### Evidence audit

The canonical Evidence namespace keeps one exact Artifact digest lookup plus
Finding audit and compatibility:

```python
session.evidence.digest(artifact_ref)
session.evidence.findings(...)
session.evidence.finding(finding_id)
session.evidence.trace(finding_id)
session.evidence.compatibility(finding_ids)
```

Remove:

```python
session.evidence.digests(...)
```

The Artifact remains the semantic owner of the digest:

```python
artifact = session.artifact(ref)
digest = artifact.evidence_digest
```

When the agent has only a ref and does not need rows, the retained exact lookup
returns the same persisted `ArtifactDigest` without loading Parquet:

```python
digest = session.evidence.digest(ref)
```

The exact lookup is a metadata index read, not another collection, synthesis,
or recomputation path.

`ArtifactSummary` carries a bounded Evidence summary so Session browsing does
not require loading Artifact rows merely to distinguish complete, partial, and
unavailable Evidence.

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
```

It does not repeat digest items, inference boundaries, or Finding payloads.

`ArtifactIssueCounts` contains typed severity counts, not free-form messages.
Exact issues remain on the recovered Artifact contract.

### Run records

`RunPage.items` contains the same closed `RunRecord` values returned by
`session.run(id)`. Runs are small metadata records, so V1 does not create a
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
invocation: RunInvocation
started_at: datetime
```

`RunInvocation` is one bounded, capability-neutral envelope for admitted public
arguments:

```python
class RunInvocation:
    arguments: tuple[RunArgument, ...]
    omitted_argument_names: tuple[str, ...]

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
Runs without decoding an opaque hash. `RunInvocation` is an inspection record,
not an automatic replay API; any omission is explicit and prevents a mechanical
replay claim.

The implementation may retain a private parameter digest for integrity and
Artifact-lineage cross-checking. It is not a public Run field and is not a
substitute for invocation facts.

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

`SessionGraph`, `ArtifactSummaryPage`, `RunPage`, and the terminal Run
record union are public analysis result families. `ArtifactSummary` and the Run
variants are reused directly by `SessionGraph`; there is no parallel graph-node
class hierarchy. Supporting immutable values such as `RunInvocation`,
`RunArgument`, `SessionGraphEdge`, summary counts, and failure shapes resolve
through their owning result's focused type Help but do not become top-level
`__all__` exports or root discovery entries.

Every new terminal public result joins the `__all__` snapshot, public result
protocol, type resolver, API reference, and Help reachability checks exactly
once. Nested value objects are tested through their owner and do not expand the
root surface.

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
runs: tuple[RunRecord, ...]
edges: tuple[SessionGraphEdge, ...]
```

`ArtifactSummary` still excludes rows, complete digest items, complete issues,
and current revalidation state. `RunRecord` retains its closed lifecycle
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
invocation inspection, but Graph edges are normalized and deterministically
sorted.

### Roots, heads, and attention sets

`SessionGraph` exposes:

```python
session_id: str
artifacts: tuple[ArtifactSummary, ...]
runs: tuple[RunRecord, ...]
edges: tuple[SessionGraphEdge, ...]
root_run_ids: tuple[str, ...]
head_artifact_refs: tuple[str, ...]
failed_run_ids: tuple[str, ...]
incomplete_run_ids: tuple[str, ...]
boundary_artifact_refs: tuple[str, ...]
boundary_run_ids: tuple[str, ...]
truncated: bool
```

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
| Run identity, capability, lifecycle, inputs, output, reuse, timing, safe invocation | Session Store canonical Run record |
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
raises a structured size error and teaches `session.artifacts(...)` followed by
the exact ancestor- or descendant-focused call.

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
- input Artifact refs and the safe `RunInvocation` projection are normalized;
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
- run_... failed compare; call session.run('run_...').show()
heads:
- delta_... DeltaFrame evidence=complete
- attr_... AttributionFrame evidence=partial
current authority: not checked; call session.revalidate('<ref>')
source freshness: not checked by Session reads
available:
- .graph()
- .graph(artifact_ref='<ref>', direction='ancestors')
- .graph(artifact_ref='<ref>', direction='descendants')
- .artifacts(...)
- .artifact(ref)
- .runs(...)
- .run(run_id)
- .revalidate(ref)
- .evidence
...
```

The concrete card remains within the Session render budget. Counts are exact
for the Session Store snapshot used by the read; listed attention and head
items are explicitly bounded. When the indexed Session size exceeds the Graph
scan bound, the card does not advertise an overall `.graph()` call that will
fail. It states that the overall graph is too large and advertises Artifact
paging plus the two focused call templates instead.

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
- provides exact `session.artifact(ref)`, `session.run(id)`, or focused
  `session.graph(artifact_ref=..., direction=...)` recovery calls when detail
  is omitted.

## Error Contract

Add or rename structured errors so every public path states expected, received,
location, and a current executable repair:

| Error | Trigger | Repair direction |
| --- | --- | --- |
| `ArtifactNotFoundError` | exact Artifact ref absent from Session | use `session.artifacts(...)` |
| `RunNotFoundError` | exact Run id absent from Session | use `session.runs(...)` |
| `SessionGraphLimitError` | `max_nodes` outside `[1, 500]` | pass the bounded value |
| `SessionGraphArgumentError` | focused direction is invalid or lacks `artifact_ref` | pass an exact ref and one supported direction |
| `SessionGraphTooLargeError` | overall scan exceeds the hard bound | page Artifacts, then call one focused graph direction |
| `SessionGraphIntegrityError` | selected persisted facts disagree or cycle | inspect the concrete Run/Artifact ids carried by the error, then regenerate that named computation in a fresh Session if canonical storage is corrupt |
| existing Artifact corruption errors | exact Artifact bytes/meta invalid | preserve existing typed repair |
| existing Session lock errors | Session persistence is locked | preserve existing ownership repair |

`ArtifactNotFoundError` may replace the Frame-named recovery error only when the
complete active public contract, tests, Help, and docs use Artifact identity.
There is no compatibility subclass or duplicate public error spelling.

An empty `SessionGraph`, `ArtifactSummaryPage`, or `RunPage` means the
healthy Session matched no records. Store unavailability and corruption always
raise.

## Help and Disclosure Contract

### Analysis runtime navigation

`marivo.help("analysis.runtime")` becomes a decision page for persisted runtime
facts rather than storage identities:

```text
runtime
  -> runtime.sessions   # create, locate, inspect, resume, delete
  -> session.graph      # understand one resumed Session
  -> runtime.artifacts  # list and recover Artifacts
  -> runtime.runs       # list and inspect Runs
```

`runtime.artifacts` owns:

```text
session.artifacts
session.artifact
session.revalidate
ArtifactSummaryPage
```

`runtime.runs` owns:

```text
session.runs
session.run
RunPage
RunRecord
```

### Evidence navigation

`marivo.help("analysis.evidence")` preserves proof-boundary routing but makes
ownership explicit:

```text
Artifact commit-time Evidence -> artifact.show / artifact.evidence_digest
Exact digest without row load    -> evidence.digest
Finding audit                 -> evidence.findings / finding / trace
Finding compatibility         -> evidence.compatibility
current Artifact authority    -> session.revalidate
quality                       -> Artifact quality summary and issues
source freshness              -> outside this Session surface
```

The `evidence.browse` grouping lists Finding collection operations only. The
`evidence.exact` grouping lists exact digest, Finding, and trace reads. There is
no second Artifact-digest collection.

### Focused leaves

Every new callable and public result has one exact leaf:

```text
analysis.session.graph
analysis.session.artifacts
analysis.session.artifact
analysis.session.runs
analysis.session.run
analysis.session.revalidate
analysis.SessionGraph
analysis.ArtifactSummaryPage
analysis.RunPage
analysis.RunRecord
```

All removed ids fail as unknown targets and suggest the new canonical target
from registry-owned migration facts during the development candidate only.
The released public registry contains no callable alias descriptor.

### Object-near disclosure

`Session.show()` advertises `graph`, Artifact recovery, Run inspection, and raw
Evidence without expanding their signatures.

`SessionGraph.show()` points to exact Run and Artifact reads. It never lists
analysis operators as recommendations.

`Artifact.show()` remains the only immediate Evidence summary and updates
recovery text from `session.get_frame(ref)` to `session.artifact(ref)`.

## Packaged Skill Contract

The packaged `marivo-analysis` skill must change in the same atomic cutover.

It teaches:

1. after resuming an unfamiliar Session, call `session.show()`;
2. call `session.graph()` only when cross-Artifact structure matters;
3. use the ancestor-focused graph for provenance and the descendant-focused
   graph for downstream impact when the overall graph is too large;
4. recover an exact result through `session.artifact(ref)`;
5. inspect failed or incomplete execution through `session.run(id)`;
6. trust `artifact.show()` for immediate commit-time Evidence, use
   `session.evidence.digest(ref)` for an exact metadata-only digest read, and
   use Findings for canonical audit detail;
7. revalidate explicitly when current semantic authority matters;
8. never treat Graph structure, Evidence completeness, or successful Runs as a
   business conclusion or datasource-freshness proof.

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
not teach both old and new public names.

## Persistence and Compatibility

### Session Store

The Session Store remains the only mutable runtime index. The implementation
performs one additive SQLite schema upgrade so the canonical Run row owns its
closed lifecycle variant, capability id, safe invocation projection, input
refs, output mode/ref, timing, and safe failure. Terminal transition and output
relationship update occur in one SQLite transaction.

A normalized `run_inputs(session_id, run_id, artifact_ref, position)` relation
is maintained in that transaction. It is a Run lookup index, not a persisted
Session graph or second lineage authority. It supports focused descendant
navigation without scanning every Run. `position` preserves invocation input
order for exact Run reads; Graph edges continue to ignore input order.

New canonical Run state is not split between mutable JSON and SQLite. Existing
job JSON may remain as a legacy immutable source for current-schema succeeded
records, but new incomplete/terminal transitions live in the Session Store.
The implementation must not persist a `session_graph` table or graph JSON
snapshot.

### Existing Sessions

One internal canonical decoder accepts either the new Session Store Run payload
or the exact current `marivo.analysis_job/v2` succeeded record and emits the
same target `RunRecord`. For v2 it maps storage names such as
`input_frame_refs`, `output_frame_ref`, and `reused_artifact` internally,
normalizes known intent names to current capability ids, and projects safe
arguments from the persisted `params`. These mappings never become public
aliases or Help targets.

The additive Store upgrade decodes current v2 records once to backfill the
canonical Run payload and `run_inputs` relation transactionally. A failed
decode aborts the upgrade and raises the exact schema/integrity error; it never
publishes a partial index. After successful backfill, ordinary reads use only
the canonical Store payload. The v2 decoder remains the cold-recovery input
adapter, not a second runtime read path.

Current-schema records naturally lack failed or incomplete Runs that were never
persisted; the Graph must not infer them. Unsupported intent names, unsafe or
contradictory values, and non-current schemas fail with the existing
schema/integrity errors. There is no best-effort decoding, public compatibility
method, or second public vocabulary. The narrow internal v2 decoder is the
explicit compatibility required to keep existing current sessions readable.

### Atomic cutover

The public cutover is released only when:

- new runtime reads and result types are complete;
- Graph and Run persistence are complete;
- old Session methods and the digest collection method are removed;
- all registry/help references resolve to new ids;
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

### Return `ArtifactHandle` wrappers

Rejected. The existing concrete Artifact already owns immediate reading,
contract, lineage, Evidence digest, quality, and terminal row access. A wrapper
would add another mandatory step and identity surface.

### Put all Evidence under Artifact

Rejected. Artifact digest belongs to the Artifact, but canonical Findings,
derivation traces, and selection compatibility belong to the Session ledger
and may span Artifacts.

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

- every new terminal result has bounded deterministic `repr`, `render()`, and
  `show()`;
- `session.run()` returns the exact closed Run variant, never a dict;
- pages expose immutable tuples, exact limits, and opaque keyset cursors;
- exact Run records expose bounded safe invocation facts rather than only an
  opaque parameter digest;
- exact `session.evidence.digest(ref)` performs no Artifact Parquet read;
- graph records reuse `ArtifactSummary` and `RunRecord` instead of duplicating
  public node variants;
- no new public callable or type exposes `Any`;
- Artifact, Run, and Finding ids cannot be silently interchanged;
- public exports, API reference, type resolver, and result protocol snapshots
  agree.

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
- cold-start decoding of current-schema succeeded Runs;
- exact v2 storage-name normalization without public legacy aliases;
- all-or-nothing v2 canonical payload and Run-input index backfill;
- recovery after interruption between Artifact commit and Run completion;
- one-snapshot reads while another process transitions a Run;
- Session lock behavior under parallel processes.

### Help and docs

Tests must prove:

- each new focused leaf resolves from string, callable/type object, and exact
  runtime instance where applicable;
- removed targets are absent from the released registry and root topology;
- runtime and Evidence navigation list each canonical target exactly once;
- every route emitted by root, hub, grouping, result, contract, and error output
  resolves;
- Session and Graph cards stay within independent line/codepoint/byte budgets;
- current English and Chinese docs contain new names and reject old names;
- packaged skill guidance contains boundaries and routes but no copied API
  inventory.

### Agent acceptance

At least six isolated cold-start recovery journeys must demonstrate:

1. identify the current head Artifact from a branched Session;
2. locate the Run that produced one Artifact;
3. identify a failed downstream Run in an oversized Session through focused
   descendant navigation without treating its input as failed;
4. distinguish partial Evidence from stale or unchecked semantic authority;
5. distinguish two Runs with the same capability and inputs but different safe
   invocation arguments;
6. recover an exact digest without loading Artifact rows, then recover exact
   Findings omitted from that digest.

Acceptance targets:

- the ordinary Session shape is recovered with `session.show()` plus at most
  one `session.graph()` call;
- no journey manually joins `jobs()` and `frame_summaries()`;
- no journey calls a second digest `show()` after `artifact.show()` merely to
  discover immediate Evidence; exact digest lookup is used only for requested
  drill-down;
- no answer claims datasource freshness, business validity, or recommended
  next action from Graph topology;
- every recovery call used by the agent is a canonical Help-resolvable public
  path.

Model-backed acceptance belongs in `marivo-agent-evals`; deterministic topology,
persistence, typing, Help, and rendering gates belong in this repository.

## Delivery Slices

The implementation may be developed in slices, but the public release remains
atomic.

### Slice 1: canonical Run persistence

- add canonical Session Store Run payloads and the normalized Run-input index;
- add the narrow current-v2 decoder and all-or-nothing backfill;
- persist incomplete Runs before execution;
- publish succeeded and failed terminal variants;
- preserve Artifact/Evidence independence and interruption recovery;
- verify one-snapshot reads, lock behavior, sanitization, and failure precedence.

### Slice 2: typed Run reads

- define closed Run records, `RunPage`, and the safe invocation projection;
- add bounded Run paging and exact read;
- keep the new API private until the cutover surface is complete.

### Slice 3: Artifact recovery naming and summaries

- define `ArtifactSummary` and bounded pages;
- implement `session.artifact(ref)` and ref-based revalidation;
- preserve exact Artifact family reconstruction;
- update internal recovery builders without advertising aliases.

### Slice 4: SessionGraph projection

- define the reused Artifact/Run collections, one edge shape, boundary tuples,
  truncation, and errors;
- implement overall and ancestor/descendant-focused construction;
- validate reuse, branches, failures, integrity, bounds, and purity;
- add bounded Session and Graph rendering.

### Slice 5: atomic disclosure cutover

- replace public Session methods and exports;
- remove digest collection duplication while retaining the exact metadata-only
  digest read;
- update capability registry, Help topology, dynamic recovery strings, errors,
  packaged skill, API docs, current bilingual site docs, and release notes;
- delete old contract tests and add negative absence tests.

### Slice 6: acceptance and closeout

- run focused Session, Evidence, result-protocol, registry, persistence, and
  process-isolation tests;
- run `make test`, `make typecheck`, `make lint`, and `make docs-api`;
- run site content verification and build;
- run clean installed-wheel Help and recovery smoke tests;
- run the six isolated agent recovery journeys;
- confirm the released tree contains one canonical runtime vocabulary.

## Acceptance Criteria

This design is complete only when all of the following are true:

1. `session.show()` provides a bounded factual recap and points to
   `session.graph()`.
2. `session.graph()` returns a typed immutable bounded DAG projection with
   truthful branch, merge, reuse, failure, incomplete, head, truncation,
   ancestor, and descendant semantics.
3. Graph construction uses persisted facts without querying data, recomputing
   Evidence/quality, or performing revalidation.
4. `session.artifacts()` and `session.runs()` are bounded typed pages.
5. `session.artifact(ref)` and `session.run(id)` are exact typed reads, and the
   Run read exposes bounded safe invocation facts.
6. `session.revalidate(ref)` is the one-step current-authority read.
7. Artifact digest has one semantic owner: the Artifact; exact
   `session.evidence.digest(ref)` remains a metadata-only access path.
8. `session.evidence` contains one exact digest lookup plus canonical Finding
   audit and compatibility, with no duplicate digest collection.
9. No public Session collection returns an unbounded list or `dict[str, Any]`.
10. No public scalar combines Run, Evidence, quality, authority, and freshness
    into one status.
11. Failed and incomplete Run persistence is explicit, typed, safe, and
    fail-closed; Evidence availability never determines Run success.
12. Old public names, Help ids, examples, skill text, and current-doc references
    are absent from the released contract.
13. Graph construction reads one Session Store snapshot, and focused descendant
    navigation does not scan every Run.
14. Every new terminal public symbol is reachable through canonical Help and
    covered by export, typing, render-budget, persistence, and drift tests;
    nested value objects do not inflate root discovery.
15. Existing unrelated working-tree changes remain untouched during
    implementation.
