# Analysis evidence access surface

Status: implemented contract for Cutover A.

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
| `artifact_ref` | Exact source artifact identity. |
| `operator` | Operator, version, artifact family, and semantic shape. |
| `subject` | Metric-shaped subject and analysis axis. |
| `scope` | Existing metric ids, segment keys, window, and assumptions. |
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
| `discover.*` | `AnomalyCandidate` | candidate, not confirmed |
| `assess_quality` | `QualityCheckResult` | evaluated quality predicate |
| `transform.*` / `MetricFrame.metric(...)` | empty digest | lineage-preserving transformation or projection only |

Every item contains its `epistemic_kind`, source artifact, subject, scope, and
`DerivationRule`. Unknown operator rules fail closed. Digest items can only be
built from declared typed finding paths.

## Bounds and fallback

A digest retains no more than five items and three inference boundaries. The
ordering rule is deterministic. `omissions` says when items were left out;
absence from a bounded digest never means absence from the result.

Use the fallback references when:

- the question asks about row-level or omitted detail;
- a boundary says the operator did not compute the required statistic;
- evidence is partial or unavailable;
- the question requires causal, business-policy, or independent-review
  evidence outside the operator contract.

The fallback is mechanical. It points to `session.evidence.findings(...)` and
`session.get_frame(...)`; it does not recommend an analysis plan.

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

`AnalysisScope` is the renamed metric-shaped scope needed by artifacts and
digests. Cutover A adds no Event/Lifecycle scope variant and no
`compatible_with()` method.

## Session recovery and audit

Session reads are bounded by default:

```python
frames = session.frame_summaries(
    kind=None,
    evidence_status=None,
    limit=20,
    cursor=None,
)

digests = session.evidence.digests(
    operator=None,
    subject=None,
    limit=10,
    cursor=None,
)

findings = session.evidence.findings(
    kind=None,
    artifact_ref=None,
    subject=None,
    limit=50,
    cursor=None,
)
```

These return `FrameSummaryPage`, `ArtifactDigestPage`, and `FindingPage`.
Pages have immutable `items`, the requested `limit`, `has_more`, and an opaque
`next_cursor`. To continue, pass `page.next_cursor` to the same method:

```python
page = session.evidence.digests(limit=10)
if page.has_more:
    next_page = session.evidence.digests(limit=10, cursor=page.next_cursor)
```

Paging uses newest-first keyset order. It is not snapshot isolation: if the
agent commits another artifact between pages, ordinary keyset behavior applies.
The cursor is an opaque continuation token, not a durable cross-version query
identity.

Exact reads remain available:

```python
digest = session.evidence.digest(artifact_ref)
finding = session.evidence.finding(finding_id)
trace = session.evidence.trace(finding_id)
frame = session.get_frame(artifact_ref)
```

`EvidenceDerivationTrace` connects one finding to its derivation rule, declared
source fields, source refs, source artifact, and any retained digest items. It
does not construct a claim or judgment around the finding.

If the evidence store is unavailable, all list and exact evidence reads raise
`EvidenceStoreUnavailableError`. An empty page therefore means “the healthy
store matched no records,” never “the store could not be read.” Missing exact
digests and findings raise their typed not-available/not-found errors.

## Commit and persistence

`judgment.db` remains the on-disk filename for existing session layouts, but its
schema v2 stores only:

- artifacts;
- typed findings;
- one digest per artifact;
- typed artifact issues.

Artifact, findings, digest, and issues commit in one transaction. There is no
phase-two judgment transaction. Only schema v2 is accepted: every non-v2
`judgment.db` raises `SchemaVersionMismatchError` and must be replaced by a
fresh analysis session.

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
they do not enumerate invocation plans. `CandidateSet.select(rank=1)` returns a
closed typed selection variant that can be passed to the relevant consumer; it
does not return arbitrary row attributes.

This contract reduces reading cost without replacing analysis. The agent should
prefer the digest for supported immediate facts, and should prefer findings or
the artifact whenever the question exceeds the digest.
