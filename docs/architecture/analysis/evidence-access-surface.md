# Analysis Evidence and Method Facts

Audit describes the exact completed result. Method facts explain an original
successful contribution investigation. Neither is an executable producer graph,
a source of hidden Metric state or a stored business narrative.
[Runtime](session-state-and-runtime.md) owns publication, recovery and integrity;
[Observation](populations-and-observation.md#explicit-endpoint-investigation)
owns endpoint and contribution admission.

## Saved audit access

Only a persisted Frame exposes artifact. Data preview/export and operations
belong to Frame; Artifact exposes audit-only show(), findings(limit=20,
cursor=None) and evidence_digest().

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

The digest takes no limit/cursor. Its items contain every saved Evidence fact;
show is bounded and deterministic. FindingPage has immutable items, limit,
has_more and next_cursor. Its cursor belongs to that Artifact/read. Consume
paged typed items; there is no independent finding(id) lookup. An empty set is
distinct from unavailable or corrupt records. Audit access reads no result rows
or coordinate attachments.

## Source and evaluation facts

Ordinary observe creates no Findings. Its audit still contains inherited
non-secret source facts and this result's current evaluation record.

| Record | Captured meaning |
| --- | --- |
| Source facts | Actual read identity, contribution role, scope, read time, checks and applicability |
| Current evaluation | Completion time, input kinds/identities, directly consumed stored references, operation and checks |
| Submission | Artifact/Session identity, publishing Run and commit time |

Only exactly equal source facts deduplicate. Different scopes/times remain
separate. A new evaluation replaces the prior evaluation record rather than
accumulating every intermediate event. Source scope is not relabeled as the
current filtered output domain. Pure carrier conversion, publication and reopen
preserve result audit. Read-integrity checks do not rewrite original facts.

A check records its actual input and outcome; failed required checks block a
successful result/publication. Unexecuted checks cannot appear as completed
Evidence. Internal aggregate components, Python objects, semantic Refs and
executable formulas are not saved. Stored input identity is audit context,
without requiring the original input files to survive indefinitely or triggering
reconstruction of a missing input.

## Complete method explanations

Frame.method_facts is a tuple of complete immutable FrameMethodFacts. A successful
Frame.attribute evaluation generates new facts. Each Attribute node places its
own facts before inherited facts;
binary composition retains the complete left sequence then right, with stable
origin_result_id deduplication. Fixed completed roots are self-contained leaves.
Different pending producers retain different origins.

Facts preserve original output schema, complete typed parent keys and independent
frozen coordinates. parent_schema retains the original receiver layout and its
consumed endpoint correspondence independently of the report schema. Coordinate
snapshots cover the exact union required by both schemas, preserving original
comparison maps without attaching comparison metadata to contribution columns.
No original value table is retained. The explicit targets directory retains each complete target
path, level/absolute/relative kind and role-to-output mapping even for an empty
parent domain. Parent metrics preserve observed_total separately from the freshly
read total, defined/zero_total share state and ordered typed rule/support trees.
Level rules contain a genuine single endpoint; absolute/relative retain both.
Output diagnostics retain original fine values, presence and applicable components
and valid counts. Physical columns retain numeric carriers, units and precision. where/top/select/reduce retain the original explanation without
changing its denominator or certifying derived arithmetic. Process-local source
declarations are a separate instance capability and never enter these saved facts;
independent reopen cannot acquire source execution authority from them. Facts explain the
original total, rather than the current filtered or reduced values. They carry
no Metric or source authority and expose no general lineage query.

Memory roots retain the complete records. Store 16 uses marivo.frame_output/v5,
frame.v5 and frame-output-v5. Stored manifests carry bounded target/schema,
origin/count/digest and owned-payload descriptors. The explicit getter validates
complete method files without reading primary rows, Catalog, model or sources.
Pending access requires compute/persist. facts.show(n=0) controls only display
after that read. Native Help at analysis.frame_method_facts owns nested fields.

Metadata/repr/cards and idempotent stored persist do not read full method files.
to_pandas validates current rows/coordinates only; stored compute and evaluated
derivatives consume both data and complete method facts. A corrupt method file
can leave current-data export usable while full consumption fails. A saved
derivative copies its own payloads, so inherited origin does not require the
original Artifact or a source/calendar rebuild.

## Interpretation

Source read, evaluation completion and commit times are distinct. Recent
publication does not establish source freshness. Calendar partition certification
does not certify business capture. Arithmetic change and complete reconciliation
do not establish cause or universal source completeness. Findings and method
explanations retain the method's exact scope and assumptions.

Use analysis.results.evidence and focused type Help for static contracts, captured
objects for current facts and structured errors for repair. The packaged
analysis skill owns judgment without adding another evidence store.

## Implementation map

[frame_audit.py](../../../marivo/analysis/core/frame_audit.py) owns evaluation facts;
[frame_source_facts.py](../../../marivo/analysis/core/frame_source_facts.py) owns
source-scope records. [dsl/artifact.py](../../../marivo/analysis/dsl/artifact.py)
and [evidence](../../../marivo/analysis/evidence/) own public audit reads.
[frame_method_facts.py](../../../marivo/analysis/core/frame_method_facts.py) owns
immutable explanations; [materialization/frame_method_facts.py](../../../marivo/analysis/materialization/frame_method_facts.py)
owns their saved payloads and receipts.
