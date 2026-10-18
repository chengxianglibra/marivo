# Analysis Evidence Access

Status: current Store 11 output-record contract, 2026-10-18.
Evidence and Findings describe an exact committed Artifact. Runtime commits them
with its output manifest and successful Run. They are output facts, not an
executable derivation graph or a business narrative.

## Read saved records

Query has no Evidence reads before execution. Artifact exposes
`findings(limit=20, cursor=None)` and `evidence_digest()`:

```python
artifact = query.execute()
artifact.show(n=20)
digest = artifact.evidence_digest()
print(digest.items)
print(digest.finding_count)
page = artifact.findings(limit=20)
for finding in page.items:
    finding.show()
```

The Evidence digest takes no limit or cursor. `.items` contains every saved
Evidence fact, while `.show()` is bounded and deterministic. Collection digests
and Finding counts describe committed records. They do not encode a formula,
semantic Ref, node fingerprint, proof dependency graph or hidden value state.

A FindingPage retains immutable items, limit, has_more and next_cursor. Its cursor
belongs to that Artifact/read; invalid ownership fails. An empty committed set
is distinct from unavailable or unreadable records. The Artifact has no separate
finding(id) method; consume its typed paged items.

## Actual observation facts

Ordinary observe produces an empty Finding set. It still records actual source
read summaries and actual completed checks. A read summary preserves its source,
contribution role, requested scope and fact origin. Omitted during explicitly
means no added time condition. Nested Boolean grouping is preserved as readable
scope text, without storing an executable predicate or SQL program.

A completed check states its kind/version, consumed scope and outcome. A failed
required check prevents success publication; an unexecuted audit cannot appear
as completed Evidence. Output coordinates and required numeric facts are
self-contained. Internal sum/count components are not saved merely because they
would be useful to an unspecified later question.

## Recovery and integrity

`session.artifact(artifact_id)` opens exact Session-owned committed metadata.
It does not query current sources, load the Catalog, restore a Query or replay
an origin. Saved record reads need no Parquet data; preview/export verify the
parts they consume. New and reopened handles expose the same records and values.

Typed decoding and saved collection checks preserve record identity and format.
Missing/unreadable records, incompatible Store generations and invalid Session
or cursor ownership remain structured errors. Earlier formats are rejected
without changing their bytes. Reconciliation resolves the original transaction,
not an automatic replacement calculation.

## Interpretation and disclosure

Observation values describe their requested scope and method. Arithmetic change,
classification or descriptive statistics do not establish a cause. Source trust
is not an executed completeness audit; calendar certification does not certify
business capture, and a saved result does not certify freshness or suitability
for the current question.

Use `marivo.help("analysis.evidence")`, `marivo.help(fact)` or
`marivo.help(coordinate)` for the owning static contract. Query/Population
contracts describe legal work before execution; Artifact cards disclose actual
saved output and read actions. The packaged analysis workflow owns investigation
judgment and does not introduce another evidence store.
