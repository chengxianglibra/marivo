# Frame Result Metadata and Method Facts

Captured metadata describes the exact completed result. Method facts explain an original
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
actual read/evaluation metadata. They do not grant source execution or original
Metric authority. Lineage does not reconstruct a producer graph or use method
facts as a current-column lineage index. Metadata access reads no result rows or
complete method payloads. See [Frame lineage](analysis-frame.md#静态语义对象血缘).
`lineage.input_id` binds those directories to `schema.input_id` for the exact
current Frame; derivation and cold reopen bind the facts to their own current
input. `schema` also projects captured fixed ordinary keys, known observation or
statistic contexts, current comparison roles/endpoints, and current decomposition
targets/output roles. Missing optional context means no additional statement was
captured; it does not certify an unfiltered or complete population. Semantic refs
remain locators; query details only through an explicit Catalog lookup.

`frame.read_periods(target, limit=1000, cursor=None)` reads a bounded page of
saved period records for a current calendar/interval/paired axis or time comparison.
`FramePeriodPage` is owned by the same input and its typed period keys join to the
target axis's complete key slots in `read_page().coordinates`. Comparison items
include actual saved endpoint mappings and absence reasons; a paired absent side
does not acquire an inferred reason. The period directory can contain selected
periods with no current result rows. Read stats do not bound total receipt-check
or file-scan I/O.

## Saved actual source reads

A persisted Frame exposes its captured session_id, artifact_id and immutable
`source_reads: tuple[FrameSourceRead, ...]`. Pending and memory roots require
explicit `persist()` before source-read access; accessing the property never
executes or saves a result. `session_id` remains available on every carrier.

```python
saved = pending.persist()
saved.show(n=0)
for read in saved.source_reads:
    print(read.read_id, read.completed_at, read.returned_rows)
    for scope in read.scopes:
        print(scope.source, scope.period)
```

Each successful actual source read has one immutable read_id and UTC completed_at.
A shared physical read is recorded once with its complete ordered logical scopes;
cache hits create no new read. FrameSourceReadScope retains a non-secret source
identifier and an optional FrameSourceReadPeriod(start, end, timezone). Start may
be None; end is exclusive. Periods retain explicitly captured original time
restrictions. None states no captured period, not an unfiltered or all-time read.
Records do not save complete filters, member domains, SQL or a reproducible query.

returned_rows is the reliable raw returned row count, or None when unavailable.
Zero is a known empty read; None is unknown. Counts are neither scanned rows,
business events nor final Frame rows, and multiple scopes do not multiply them.
Read completion is the successful processing boundary, not a database snapshot
or pure network timestamp. Evaluation completion and publication commit times
remain distinct; recent reads or commits do not establish business freshness.

Fixed-data operations inherit reads in stable input order and deduplicate only
by read_id, rejecting conflicting contents. Independent reads retain distinct IDs
regardless of equal scope. New source reads append to inherited reads; inherited
periods are never relabeled as the current filtered domain. Literal results have
no synthetic read. There is no read-ID lookup or source-execution capability.

`source_reads` and `show(n=0)` use already captured metadata without Parquet,
method-payload, Catalog or source access. Cards retain result/evaluation/commit
identities and times, bounded inputs/operations and source-read summaries.
Required numeric, key and method checks still run and block invalid results;
success does not create a general passed-check log.

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
Actual independent reads are retained separately. The method retains its local final-target closure
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
sampled targets. Successful execution enforces the state-domain or evaluated-native-value
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

Memory roots retain complete result metadata and method records. Store 25 uses
marivo.frame_output/v14 and frame.v14. One manifest contains
structure, interpretation, lineage, result/evaluation/input/operation facts,
source_reads and owned-payload descriptors. Publication atomically commits that
manifest and success; unknown acknowledgement is reconciled by exact complete
manifest equality. There are no separate Finding or Evidence record collections.
Cold recovery needs no ancestor Artifact, model or source connection.
The explicit method_facts getter validates complete method files without reading
primary rows, Catalog, model or sources. Pending access requires compute/persist.
facts.show(n=0) controls only display after that read. Native Help at
analysis.frame_method_facts owns nested fields.

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
do not establish cause or universal source completeness. Method
explanations retain the method's exact scope and assumptions.

Use analysis.results.inspect and focused type Help for static contracts, captured
objects for current facts and structured errors for repair. The packaged
analysis skill owns judgment without adding another result store.

## Implementation map

[frame_metadata.py](../../../marivo/analysis/core/frame_metadata.py) owns evaluation metadata;
[frame_source_reads.py](../../../marivo/analysis/core/frame_source_reads.py) owns
actual read records. [dsl/frame.py](../../../marivo/analysis/dsl/frame.py) owns
public source-read metadata access.
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
