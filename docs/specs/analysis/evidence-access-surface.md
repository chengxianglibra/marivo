# Analysis Evidence Access

Evidence records deterministic facts about a committed Dataset. It does not
choose the next analysis step or make causal or business judgments. The owning
Runtime publishes Artifact, Evidence and Findings in the same transaction.

## Artifact digest

A Materialized Dataset exposes `evidence_digest`, `findings(limit=..., cursor=...)`
and `finding(finding_id)`. A Logical Dataset has no evidence reads before execution.

```python
artifact = current.compare(baseline).execute()
artifact.show()
digest = artifact.evidence_digest
print(digest.finding_count)
page = artifact.findings(limit=20)
if page.items:
    finding = artifact.finding(page.items[0].finding_id)
    finding.show()
```

The current ArtifactDigest is version `v3` with Evidence schema
`marivo.dataset_evidence/v1`. It retains exact Artifact identity, extractor
contract versions, quality summary digest, typed issue digest, Evidence digest,
Finding count and Finding-set digest. These are committed extraction facts, not
a generated narrative or a second mutable evidence model.

Findings retain typed subject, coordinate, input/derivation and value facts.
Projection checks the exact owning Artifact and registered extractor schema.
A bounded FindingPage has immutable items, limit, has_more and next_cursor; its
cursor is valid only for the owning read. Empty Findings mean an empty committed
set, not an unreadable or unavailable Store.

## Recovery and integrity

Recover through `session.artifact(reference)` or a SucceededRun's
`output_artifact_ref`. Session Run/graph reads report committed runtime history;
Artifact lookup does not execute an origin recipe or resolve a current source.

`session.revalidate(reference)` checks separate Artifact, storage authority and
Evidence integrity axes. Confirmed integrity does not prove freshness, causal
validity or suitability for the agent's question. Typed unavailable/unknown states
must not be collapsed into a successful reuse verdict.

Corrupt metadata, incompatible Store generations, missing private parts, altered
receipts and invalid cursor/ownership fail through structured errors. Rejected
old-generation reads leave existing bytes unchanged. Bounded cards do not hide
missing Evidence by returning an empty page or making a source query.

## Interpretation boundaries

- Observation values describe their retained scope and authority.
- Delta and Attribution are algebraic, not causal.
- Association is descriptive and may reflect selected/search inputs.
- Forecast is a model result, not an observed outcome or calibrated guarantee.
- Candidate scores are evaluated leads, not confirmed anomalies or recommendations.
- Event and Lifecycle reducers retain assignment/replay and completeness limits.

Start at `marivo.help("analysis.evidence")` and follow its native focused routes.
The packaged analysis skill owns investigation judgment; it does not duplicate
these schema facts or introduce a parallel evidence store.
