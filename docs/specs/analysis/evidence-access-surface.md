# Analysis Evidence Access

Status: AnalysisFrame cutover audit contract, 2026-10-19.
Evidence and Findings describe an exact committed result. A publishing Run
commits them with the Frame manifest and success. They are audit facts, not an
executable derivation graph or a business narrative.

## Read saved records

Only a persisted Frame exposes `.artifact`. Data preview/export and data
operations belong to the Frame; Artifact exposes audit-only `show()`,
`findings(limit=20, cursor=None)` and `evidence_digest()`:

```python
saved = pending.persist()
saved.show(n=20)
artifact = saved.artifact
artifact.show()
digest = artifact.evidence_digest()
print(digest.items)
print(digest.finding_count)
page = artifact.findings(limit=20)
for finding in page.items:
    finding.show()
```

The digest takes no limit or cursor. `.items` contains every saved Evidence
fact; `.show()` is bounded and deterministic. Collection digests and counts
refer to committed records. They encode no formula, semantic Ref, producer graph
or hidden aggregate state. Artifact has no row preview/export or DSL continuation.
A FindingPage retains immutable items, limit, has_more and next_cursor. Its cursor
belongs to that Artifact/read. An empty set differs from unavailable/unreadable
records. Consume typed paged items; there is no separate finding(id) lookup.

## Actual observation facts

Ordinary observe produces an empty Finding set. The result still owns one audit:
inherited non-secret source facts plus this result's current evaluation record.
Source facts retain the actual read identity, contribution role, requested scope,
read time, checks and applicability. Only exactly equal facts deduplicate;
different times or scopes remain distinct. by=() adds no output time axis or time condition.
Source scope is not relabeled as the current filtered output domain.

Each new evaluation writes its own completion time, input kinds/identities,
directly consumed stored references, operation and checks. It does not accumulate
intermediate operation history. Pure carrier conversion, persistence and reopen
preserve that audit. Read-integrity validation does not rewrite original facts.
A check records its real input and outcome; failed required checks prevent
publication. Unexecuted checks cannot appear as completed Evidence. Internal
sum/count components, Python objects and executable source definitions are not saved.

## Recovery and integrity

`session.persisted_frame(artifact_id)` accepts an exact string ID in the original
Session. `reopened.artifact` opens that same result's audit. Neither loads a
Catalog, source schema, connection or model. Audit reads and Frame.show(n=0)
consume no result payload. Positive preview reads the result prefix and required
coordinate records; complete compute/export validates all consumed data and
attachments. Metadata or preview success does not certify unread payload.

Typed decoding preserves record identity, ownership and format. Missing data,
corrupt required records, incompatible generations and foreign cursors fail.
Store 13 refuses every older generation's data without changing old bytes;
read-only recent discovery may return an empty page without exposing old state.
An old-only project cannot initialize a new Store as a bypass. See the
[cutover note](analysis-frame-cutover.md). Reconciliation resolves the original
publication transaction; it never reevaluates values to resolve uncertainty.

## Interpretation and disclosure

Source-read, evaluation and commit times are distinct. A recent commit is not a
fresh source observation. Saved values, arithmetic change and classification do
not establish cause, completeness or current semantic authority. Calendar
certification does not certify business capture. A stored input reference is
audit identity, not a demand that old input files remain available forever.

Use `marivo.help("analysis.evidence")` and focused type Help for static contracts.
Population/Frame.show describe their captured structure. Artifact.show owns
submission/audit facts; errors own concrete repairs. The analysis skill owns
investigation judgment and introduces no additional evidence store.
