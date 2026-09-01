# Analysis evidence access surface

Status: implemented contract.

This document defines the deterministic evidence surface exposed by
`marivo.analysis`. Marivo is a pure Python analysis library: it does not call an
LLM, generate a narrative judgment, choose the next analysis step, or infer a
business decision. Its evidence engine only projects operator results into
typed, bounded, auditable values.

## Contract

The runtime has two evidence stages:

```text
operator artifact -> typed findings -> bounded artifact digest
```

- A `Finding` is the loss-minimizing, typed extraction of one result element.
- An `ArtifactDigest` is a bounded, operator-specific projection of those
  findings for the immediate read after execution.
- The agent owns every cross-artifact synthesis, causal interpretation,
  business judgment, and decision about what to execute next.

There is no proposition, assessment, confidence label, session knowledge
projection, open-item model, or persisted follow-up planner. A digest must not
upgrade algebraic contribution into cause, association into effect, a candidate
into a confirmed anomaly, a test decision into business importance, or a
forecast into an observed outcome.

## Artifact read surface

Every committed artifact exposes one structured path:

```python
artifact.evidence_status  # "complete" | "partial" | "unavailable"
artifact.evidence_digest  # ArtifactDigest | None
```

`artifact.show()` renders the same bounded digest before the data preview. It is
the human-readable form of `artifact.evidence_digest`, not a second evidence
model. `frame.meta.evidence_digest` is persistence plumbing and is not a public
read path.

The digest is an immutable commit-time snapshot with:

| Field | Meaning |
| --- | --- |
| `digest_version` | Digest projection contract version; current producers emit `v2`. |
| `artifact_ref` | Exact source artifact identity. |
| `operator` | Operator, version, artifact family, and semantic shape. |
| `subject` | Closed metric, Event, Lifecycle, or SubjectSet subject and analysis axis. |
| `scope` | Closed metric, Event journey/reducer, Lifecycle replay/reducer, or SubjectSet scope. |
| `items` | At most five typed operator-local items. |
| `boundaries` | At most three explicit prohibited inference upgrades. |
| `omissions` | Retained and omitted item counts and kinds. |
| `quality` | Intrinsic quality summary when available. |
| `fallback` | Whether exact findings or raw rows are available and when to use them. |
| `fingerprint` | Stable fingerprint of the normalized digest payload. |

Digest item variants are deliberately operator-specific:

| Operator family | Digest item | Epistemic meaning |
| --- | --- | --- |
| `observe` | `ObservationFact` | observed |
| `compare` | `ChangeFact` | algebraic |
| `attribute` / `decompose` | `ContributionFact` | algebraic, not causal |
| `correlate` | `AssociationFact` | estimated association, not causal |
| `hypothesis_test` | `TestDecision` | statistical decision under the declared test |
| `forecast` | `ForecastOutput` | predicted, not observed |
| Scored `discover.*` | `AnomalyCandidate` | candidate, not confirmed |
| `discover.semantic_hypotheses` | empty digest | ontology candidate only; no finding is seeded |
| `transform.*` / `MetricFrame.metric(...)` | empty digest | lineage-preserving transformation or projection only |

Construction quality is stored on the parent Artifact as `quality_summary` and
typed issues; it is not an evidence-producing operator or a separate Artifact.

Every item contains its `epistemic_kind`, source artifact, subject, scope, and
`DerivationRule`. Unknown operator rules fail closed. Digest items can only be
built from declared typed finding paths.

## Bounds and fallback

A digest retains no more than five items and three inference boundaries. The
ordering rule is deterministic. For a v2 multi-metric `observe` digest, items
follow the ordered `AnalysisScope.metric_identities` supplied by the observation
and retain the first five metrics; metric totals are never compared across
metrics. Each metric's internal `top_segments` ranking remains independent and
uses absolute segment magnitude. `omissions` says when items were left out;
absence from a bounded digest never means absence from the result. The readable
digest and frame card report `selection=metric_input_order` when this bound is
exceeded.

Use the fallback references when:

- the question asks about row-level or omitted detail;
- a boundary says the operator did not compute the required statistic;
- evidence is partial or unavailable;
- the question requires causal, business-policy, or independent-review
  evidence outside the operator contract.

The fallback is mechanical. It points to `artifact.findings()` and
`session.artifact(...)`; it does not recommend an analysis plan.

## Artifact issues

`artifact.contract().issues` is the only structured issue collection. The
closed `ArtifactIssue` union contains:

- `DataQualityIssue` for an evaluated data-quality predicate;
- `ComparabilityIssue` for incompatible or approximate comparison scope;
- `EvidenceAvailabilityIssue` for extraction, digest, or store degradation.

Issue prose shown by `artifact.show()` is derived from the typed issue. There is
no generic message/payload issue and no issue resolution lifecycle. A typed
`AnalysisRepair` may describe how to retry a failing capability; it is local
error recovery, not a persisted next-step recommendation.

`EvidenceScope` is the closed `kind`-discriminated union for persisted analysis
scope:

| `kind` | Variant | Use |
| --- | --- | --- |
| `metric` | `AnalysisScope` | metric artifacts |
| `event` | `EventAnalysisScope` | Event journeys |
| `event_funnel` | `EventFunnelAnalysisScope` | Event funnel reducers |
| `event_time_to_event` | `EventTimeToEventAnalysisScope` | Event time-to-event reducers |
| `lifecycle` | `LifecycleAnalysisScope` | Lifecycle replay and reducers |
| `subject_set` | `SubjectSetAnalysisScope` | persisted subject selections |

No scope variant exposes a `compatible_with()` method.

## Session recovery and Artifact audit

Runtime recovery begins with bounded Run history and exact Artifact refs:

```python
runs = session.runs(limit=20)
run = session.get_run(runs.items[0].run_id)
artifact = session.artifact(run.output_artifact_ref)
```

`RunPage` is immutable, newest-first, and cursor-bounded. Each Run is exactly
one of `IncompleteRun`, `SucceededRun`, or `FailedRun`; there is no generic
public Run record and no job-shaped compatibility projection. Terminal Runs
carry their ordered captured query executions. Each query retains actual SQL
and a literal-neutral shape digest; normalized SQL and parser-derived literal
lists are not persisted as execution facts. Default Run rendering shows only
bounded query summaries, while `.queries` explicitly exposes full SQL. Reused
Artifact Runs carry no query records, so an empty tuple is not evidence that no
datasource activity occurred. When ancestry,
descendant impact, heads, failures, or incomplete work matter, use the factual
Session graph instead of joining private Store collections.

Findings are owned and read by their Artifact:

```python
page = artifact.findings(limit=20)
if page.has_more:
    page = artifact.findings(limit=20, cursor=page.next_cursor)
finding = artifact.finding(page.items[0].finding_id)
```

`artifact.finding_count` is the exact committed count. Finding projection opens
the ledger identified by the Artifact's immutable `project_root` and
`session_id`; it does not retain a mutable Session handle. The Finding includes
its full derivation, source Artifact, source fields and refs, and retained
digest item refs, so a separate derivation-trace API is unnecessary.

An unavailable ledger raises `EvidenceStoreUnavailableError`; an empty page
therefore means a healthy ledger matched no Findings. `FindingNotFoundError`
is Artifact-scoped and directs recovery back to `artifact.findings()`.

These reads do not combine Findings, judge causality or business validity,
check current semantic authority, or establish datasource freshness.

## Artifact revalidation

Revalidate an exact recovered or newly committed Artifact before relying on it
as current evidence:

```python
artifact = session.artifact(artifact_ref)
revalidation = session.revalidate(artifact_ref)
revalidation.show()
```

`ArtifactRevalidation` is frozen, bounded, read-only, and ephemeral. It reports
the Artifact identity, recorded and current catalog fingerprints, normalized
authority fingerprint, `semantic_status`, `evidence_status`, overall `status`,
typed issues, and a UTC-aware `checked_at`. Its stable `fingerprint` excludes
`checked_at`, so repeated checks of unchanged authority and evidence return the
same identity.

Semantic status is `current`, `stale`, or `indeterminate`. A confirmed scoped
dependency drift wins over unresolved authority, including for multi-source
Artifacts. Overall status is `stale` when semantic authority is stale;
otherwise it is `indeterminate` when authority cannot be proven or evidence
does not satisfy the contract, and `admissible` only when both checks pass.
Complete evidence satisfies the contract. Partial evidence does so only when
all availability issues are warnings and its explicit fallback retains safe
rows; unavailable evidence is indeterminate. Business data-quality issues are
reported as evidence, not misclassified as store corruption.

The read verifies Session Store registration, sidecar/parquet content identity,
session ownership, Artifact and evidence schemas, lineage, digest, findings,
issues, scope, quality, and evidence-status agreement. Broken committed
evidence raises `EvidenceIntegrityError`; an unavailable evidence store raises
`EvidenceStoreUnavailableError`; Frame content or schema damage keeps the
existing typed Frame errors. Revalidation never changes the Frame, its sidecar,
digest, findings, hashes, or ledger, and never queries datasource or source
health. It therefore does not prove source freshness.

Persisted linked `ComponentFrame` and `CoverageFrame` sidecars do not own an
independent evidence projection. Revalidation verifies their own Session Store
and content identity plus the canonical parent evidence, then reports their
healthy `evidence_status="unavailable"` as an indeterminate result.

## Operator semantic admission

Artifact-bearing operators declare one closed authority policy in the private
capability registry. A `semantic_current` consumer checks the Artifact's
canonical scoped dependency closure immediately before execution, using the
same normalized authority context and semantic comparator as revalidation.
Confirmed definition drift raises `mv.errors.ArtifactStaleError`; authority
that cannot be established raises
`mv.errors.ArtifactAuthorityUnknownError`. Both errors identify the capability,
parameter, Artifact and source refs, affected definition refs, recorded/current
fingerprints, and an executable repair.

`materialized_only` consumers do not read current catalog authority. They still
retain their existing Artifact integrity, Session ownership, shape, coverage,
content identity, and operator-specific checks. This allows closed reducers,
transforms, projections, and terminal reads to consume committed values without
mistaking unrelated catalog drift for corruption. Admission never calls
`session.revalidate(...)`, reads datasource health, or folds evidence coverage
into execution eligibility. Focused help reports the registered authority
policy; `artifact.contract()` remains a commit-time mechanical continuation
contract and performs no currentness check.

## Commit and persistence

`judgment.db` remains the on-disk filename for existing session layouts, but its
schema v4 stores only:

- artifacts;
- typed findings;
- one digest per artifact;
- typed artifact issues.

Metric findings carry one current-schema typed subject. Catalog subjects record
the canonical metric id; runtime-expression subjects record the expression
fingerprint; delta subjects record the ordered current/baseline comparison
identity. Every subject includes the owning session/artifact scope, so evidence
from different sessions cannot merge merely because value expressions match.

Finding families that derive identity from source-row coordinates encode
`canonical_item_key` as versioned canonical JSON with explicit null, string,
boolean, integer, float, and temporal scalar tags. Coordinate names and values are
encoded structurally rather than joined with delimiters, so null and empty string,
typed values with the same display text, and values containing `=` or `|` remain
distinct. Unsupported composite coordinate values fail extraction instead of
falling back to lossy `str(...)` identity.

Artifact, findings, digest, and issues commit in one transaction, after the frame
bytes and sidecar have been atomically published. That schema-v4 Artifact row is
also the recovery marker when interruption occurs before the Session Store index
write; recovery validates the sidecar, frame hash, digest, session, and canonical
path before restoring the index. There is no phase-two judgment transaction or
parallel marker schema. Only schema v4 is accepted: every non-v4 `judgment.db`
raises `SchemaVersionMismatchError` and must be replaced by a fresh analysis
session. SQLite lock/timeout failures remain typed execution errors and are never
converted into empty evidence or an unavailable Artifact.

After a non-lock projection failure, a still-writable ledger receives a separate
fallback transaction containing only the unavailable Artifact and its typed issues.
Findings and digest remain absent. A later evidence-requesting retry republishes the
same deterministic ref instead of reusing that failure marker; a completely
unwritable ledger leaves only the unavailable sidecar when no prior marker exists.
If replacement of an existing unavailable marker cannot commit, Marivo restores
the prior sidecar and Session Store registration before raising, so recovery never
observes a new sidecar paired with the old ledger issue.

The digest serialized into frame metadata and the digest stored in SQLite are
the same normalized value and fingerprint. Sidecars containing removed
`confidence_scope`, `evidence_summary`, or `blocking_issues` fields are rejected
with `FrameMetaInvalidError`; there is no legacy display adapter.

## Agent read-write-execute-read loop

The intended agent loop is:

1. read live help and the input artifact contract;
2. execute one typed operator;
3. read `artifact.evidence_status`, `artifact.evidence_digest`, or
   `artifact.show()`;
4. inspect exact findings/raw rows only when the digest boundary or question
   requires it;
5. make the next analytical choice itself and execute another operator;
6. use bounded session pages to recover state in a later turn.

Affordances preserve exact parameter roles and accepted artifact families, but
they do not enumerate invocation plans. `CandidateSet.select(item_id=...)` returns a
closed typed selection variant that can be passed to the relevant consumer; it
does not return arbitrary row attributes.

This contract reduces reading cost without replacing analysis. The agent should
prefer the digest for supported immediate facts, and should prefer findings or
the artifact whenever the question exceeds the digest.
