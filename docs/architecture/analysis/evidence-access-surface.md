# Analysis Evidence and Method Facts

Audit describes the exact completed result. Method facts explain an original
successful contribution investigation. Neither is an executable producer graph,
a source of hidden Metric state or a stored business narrative.
[Runtime](session-state-and-runtime.md) owns publication, recovery and integrity;
[Observation](populations-and-observation.md#explicit-endpoint-investigation)
owns endpoint and contribution admission.

## Static semantic origins

`frame.lineage` exposes complete immutable value, axis and presence directories on
all carriers, including pending expressions. Each column separates value origins
from context origins; identities retain kind, path and consumed definition_id.
These inert facts survive independent recovery without model or source access.
They explain logical dependencies, including selection context, independently of
actual read/evaluation evidence. They do not grant source execution or original
Metric authority. Lineage does not reconstruct a producer graph or use method
facts as a current-column lineage index. Metadata access reads no result rows or
complete method payloads. See [Frame lineage](analysis-frame.md#静态语义对象血缘).

## Saved audit access

A persisted Frame exposes its captured session_id and artifact_id, plus
findings(limit=20, cursor=None) and evidence(). There is no separate
public Artifact view. Pending and memory Frames require persist before commit
identity or audit reads; session_id is available on every carrier.

show() combines captured structure, bounded audit summaries and a data preview.
show(n=0) reads metadata only: result/commit identities, evaluation/commit times,
bounded inputs/operations and source-fact/check/Finding counts. It never reads
result rows, coordinate attachments, complete method facts or full audit payloads.

```python
saved = pending.persist()
saved.show(n=0)
page = saved.evidence(limit=20)
assert page.input_id == saved.schema.input_id
print(page.total_count)
for item in page.items:
    item.show()
if page.has_more:
    page = saved.evidence(limit=20, cursor=page.next_cursor)
findings = saved.findings(limit=20)
```

FindingPage and EvidencePage contain immutable items, input_id, session_id,
artifact_ref, limit, total_count, has_more and next_cursor. Both accept integer
limits 1..100, excluding bool, and preserve saved order. Evidence retains source
facts first and current evaluation checks second, using saved partition counts.
There is no public digest, complete-Evidence alternative, record lookup or filter.
Counts belong to each collection; absent Findings do not imply absent Evidence.

Opaque cursors bind exact input, collection kind/version and limit. Repeat the
same parameters; mismatches fail before record reads. Independent reopen in the
same Runtime version can continue a stored cursor. Empty sets return total_count=0,
no cursor and no further page. They remain distinct from unavailable or corrupt
records. Each page reads its own collection in one SQLite snapshot, checks only
its ordered range and a bounded next-record probe, and validates per-record
identity, body digest, concrete types and current-schema slot constraints.
It does not recompute the collection digest, count all records or check coordinate
values against result rows. Full audit consumption and transaction confirmation
retain complete collection integrity checks.

Resolve OutputCoordinate.slot_id through the same input's schema.slots. Field
layout entries identify projected value/presence display columns; axis key fields
identify the whole axis and its display columns. Data-page coordinates hold exact
complete keys. A coordinate may reference a value without complete row keys;
empty coordinates do not mean global applicability, and identities do not grant
an automatic unique-row or chart-point association. Inherited source facts cannot
reference current output slots; current checks and Findings use unique valid
current slots. Page reads do not trace ancestors or prove coordinate membership.

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
Division rules expose `divisor`, `operation` (divide or rescale) and the ordered
child `method`; result columns retain the final unit. The original typed divisor
is preserved rather than replaced by a reciprocal multiplication coefficient.
Additive expressions containing division retain their ordered linear wrappers
and nested division facts through publication and independent recovery.
Parent support describes the independent parent read; fine support and presence
describe the fine read. They do not certify matching source versions or identities.
Paired rate/mix facts retain `symmetric_rate_mix/v1`, `attribute_components/v1`
and `zero_mass_entry_exit/v1`. Output mappings include within, mix and entry_exit;
mixed additive/rate formulas additionally retain additive effects. Level and pure
additive targets have no artificial rate/mix columns. Each linear child retains its
own denominator. Filtering and reduction preserve the original complete explanation,
rather than changing the effect boundaries or recomputing a coarser decomposition.
Audit records retain actual independent reads and the local final-target closure
policy: `attribute_rate_mix/v1` uses numeric rounding only for paired rate/mix;
unchanged additive/level paths retain `attribute_closure/v2` with the Float64 1%
final-target allowance and a numeric rounding floor. Component consistency and
positive-support original N/D checks precede the new effect closure. These checks
add no identity or uniqueness preflight and do not certify a shared snapshot. Cold
reads retain captured policy versions and do not reread sources or reapply checks.

Sampled targets use the closed `FrameShapleyMethod` variant. It retains native typed
endpoints/change, permutation count/seed/version, player count and actual unique
coalition/transition costs. `game` holds nonexecuting endpoint formula digests,
replacement/domain/closure policies and, for state games, captured proof clauses; `leaves` retains
original typed reducers, q and NULL masks with both endpoint capture directories.
`captures` records each actual submitted input stage once, including domain/diagnostic
or shared sibling work. A leaf may reference multiple captures, and an identical
actual capture may serve both endpoints. Decoded returned bytes and logical state
payload bytes are distinct from unknown scanned/wire bytes and process memory.
The directory supports truthful sharing/cost inspection without saved state or SQL.
Sampled contributions and SE are Float64 in the target unit; endpoint diagnostics,
total and observed_total retain their owning common carrier. SE measures permutation
sampling error conditional on captured states, excluding summary bias; native-query SE describes observed marginal variation
and cannot isolate source changes. No rate/mix effect columns are manufactured for
sampled targets. Successful execution records a state-domain or evaluated-native-value
closure check on its consumer. Cold recovery validates closed types, references, costs and payload digests;
it does not rerun the formula, source, game or a newer numeric policy.

Float64 method values are finite captured facts, not arithmetic identities to
recompute. Validation skips floating ratio, linear, division, endpoint-difference and relative
arithmetic before formula evaluation, with no second tolerance. Exact non-floating
arithmetic, method structure, field bindings, types, units, finite values and stored
payload digests remain checked. Division uses closed one-endpoint and two-endpoint
fact variants within the existing Store format.
Output diagnostics retain original fine values, presence and applicable components
and valid counts. Physical columns retain numeric carriers, units and precision. where/top/select/reduce retain the original explanation without
changing its denominator or certifying derived arithmetic. Process-local source
declarations are a separate instance capability and never enter these saved facts;
independent reopen cannot acquire source execution authority from them. Facts explain the
original total, rather than the current filtered or reduced values. They carry
no Metric or source authority and expose no general lineage query.

Memory roots retain the complete records. Store 21 uses marivo.frame_output/v10,
frame.v10 and frame-output-v10. Stored manifests carry structure, lineage, result/evaluation/input/operation facts,
source/check/Finding counts, internal collection digests and owned-payload
descriptors. Evidence and Finding bodies exist only in their SQLite record tables;
opening a manifest never loads either collection. Atomic publication writes both
description and records. Full consumption reconstructs the original audit using
the saved source/check partition and verifies complete collections. The explicit getter validates
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
source-scope records. [dsl/frame.py](../../../marivo/analysis/dsl/frame.py)
and [evidence](../../../marivo/analysis/evidence/) own public audit reads.
[frame_method_facts.py](../../../marivo/analysis/core/frame_method_facts.py) owns
immutable explanations; [materialization/frame_method_facts.py](../../../marivo/analysis/materialization/frame_method_facts.py)
owns their saved payloads and receipts.

`AnalysisFrame.fix` transports existing row presence and comparison explanations with the
selected cells. Its evaluation records a `fix` operation without creating source
method facts. Fixed source coordinates remain process-local declarations for an
explicit later attribute request; they are not executable persisted provenance.


### Independent native Shapley reads

Native-query method facts use FrameShapleyQueryGame with independent_reads/v1,
evaluated_coalitions/v1 and observed_marginal_mean/v1. They describe only the
actually queried cached values, without an all-coalition certificate or snapshot.
FrameShapleyQuery entries count actual directory, diagnostic and coalition reads;
their returned_bytes are decoded Arrow bytes. State entries are zero for this
route. Shared reads have one ID; unknown scan/wire/backend-version data stay None.
FrameShapleyNativeQuantile records the original exact/approximate reducer and q,
with its original-native-reducer policy and linked backend receipt. These records
contain no SQL, raw data, callable, session or source handle and confer no source
execution authority after cold recovery. State-backed facts retain their existing
fixed-input, domain-proof, frequency and permutation-SE meanings.

Float SUM/native AVG summary games mark `FrameShapleyGame.metric_approximation`
as `summary_reconstruction/v1`. This is a captured evaluation policy, with no
summary-bias bound. Cold validation correlates it with original typed leaf reducers;
it does not recompute or certify original native endpoint equality.

Native approximate distinct retains the canonical `approx_nunique` aggregate
reducer in `FrameShapleyReduction` and requires the query game's
`native_approximate_metric/v1` policy. It cannot appear as a reconstructed state
leaf. Its record fields and wire layout remain unchanged.
