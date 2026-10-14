# Session State and Runtime

Status: current Runtime and Store 9 contract, 2026-10-09.
[Analysis Design](python-analysis-design.md) owns construction and compilation;
[Operators and Frames](operators-and-frames.md) owns method meaning and sufficient
state. This document owns execution identity, publication, storage and recovery.

## Session and invocation ownership

A Session owns an investigation and immutable Run/Artifact history under the
project's `.marivo/` directory. Creation, current selection and resume use
`mv.session.get_or_create(...)`, `mv.session.current()` and
`mv.session.resume(...)`. Report timezone is persisted Session authority.
Identity/timezone conflicts and cross-Session operands fail explicitly.

Construction and compilation create no Run and read no business rows.
Schema-only source preflight may connect to resolve unknown key/value types.
Business reads follow exact physical admission and Run allocation, and recheck
the schema captured during preflight. One typed-graph Runtime owns the path;
scenario executors and former Population/Metric Dataset routes are removed.

## Source and fixed execution identity

Classification follows the root's transitive data dependencies. An explicit
FixedLeaf is a retained boundary; its origin lineage is not a current source.
Source output passed to a local stage within the invocation remains source-only.

| Classification | Identity and execution |
| --- | --- |
| Source-only | Every admitted top-level execute gets a fresh Run/evaluation key and reads current sources. A saved Artifact with the same definition cannot satisfy it. |
| Fixed-only | Exact ordered Artifact inputs, bindings and method/plan identity define the key. An exact published hit returns its original Artifact without a new Run; a miss executes the admitted local consumer. |
| Mixed live source and fixed Artifact | Reject before Run admission, business source acquisition or retained row consumption. |

Definition identity describes a normalized question and explicit bindings,
not a source snapshot. One shared logical node realizes once per invocation;
separate equal definitions remain separate nodes. Checks, required parts and
terminal native expressions may read independently. A Run does not promise
one scan, transaction or stable source version.

The Runtime orders admission as follows:

1. Capture the constructed graph's structural closure and ownership, classify
   dependencies and select the exact qualified plan with one parameter admission
   per method. Bind source inputs or lower and validate the fixed schedule once.
2. Acquire the Session writer guard and reconcile unfinished Run/resource state.
3. Resolve a fixed-only exact key and return an existing committed result if found.
4. On a producer miss, determine the key and allocate/admit its Run.
5. Execute preparation, methods and checks under their captured contracts.
6. Publish complete Artifact/Evidence/Findings and successful Run atomically.

The source key binds protocol, definition/plan and fresh Run identity.
The fixed key binds exact ordered Artifact inputs and method/protocol/plan
identity without a new random value. Store uniqueness remains
`(Session, execution_key)`. Source evaluations can publish different immutable
Artifacts for one definition; one fixed key has at most one successful output.

Source admission uses the leaves of the current executable graph, including
temporal shapes unified while composing dependencies. When preparation captures
new physical relations, their Entity output expressions are rebound to those
captured relations before lowering; the original source bindings remain the
execution key authority.

Prepared analysis over local Parquet reads the declared file directly rather
than making a temporary copy. It records the file identity and content digest
before execution and checks them again after the graph finishes; a detected
change fails the Run. This check does not provide a filesystem snapshot while
queries are running.

Plan identity normalizes retained references to an Artifact already occupying
one unambiguous execution slot to that slot's identity. Fresh-process rebinding
therefore preserves the same plan and fixed key when frozen cohort evidence
mentions its original member input. Multiple independent slots for one Artifact
remain distinct.

Exact Artifact references recover their own producing Run, never the latest
result for a definition. A failed later evaluation does not replace an earlier
success. Lost commit acknowledgement is reconciled against the original Run/key,
without allocating a new identity or replaying sources.

## Acquisition and local consumption

A selected `ibis` plan keeps governed dependencies as Ibis expressions.
Checks, terminal primary and required parts execute through SourceSession, which
uses the selected Ibis backend's `execute()` and returns pandas DataFrames.
Source-only execution does not capture intermediates only to restage them as
literal/temporary source relations. Temporary validation output may still be
read for independent checks.

`ibis_python` prepares the exact projected/filtered input as pandas for its
registered local consumer. Safe member/time filters, joins, attributes and
component aggregation stay at source when the admitted Ibis expression can
express them. Source-side Ibis execution is preferred when qualified for the
complete requested contract. Route selection happens before execution; failure
never switches implementations.

Every source dependency needed after local selection is collected from the
Logical DAG and prepared before that selection consumes rows. Prepared
observation retains per-Subject support, components, path/version facts, axes,
time envelope and full keys. Local consumers restrict that capture by the
selected Subject image. A population scalar cannot replace this support.
No selected-ID upload, source-after-local query or implicit second
selection is admitted.

Fixed execution reads persisted Result Parquet directly into pandas for
registered pandas/NumPy/SciPy methods. It does not open DuckDB to scan retained
Parquet, load current Semantic/calendar definitions or reconnect historical sources.
A fixed member domain does not fix a new live Metric/property dependency.

Independent native queries have no shared transaction-snapshot guarantee.
A completed source check proves its own query, not a later calculation.
Where common captured authority is required, the exact producer must supply it;
equal paths, definitions or row counts do not supply it.

## Check scheduling and transport

Declarations, constructor guarantees, call assumptions and completed checks
have distinct evidence bases. Obligations retain exact originating ordered
nodes, scope and consume/publish deadline. Composite facts bind windows,
versions, quantities and paths as well as symbolic domains. One check serves
consumers only for the same actual input and fulfillment location.

A consumer of inherited evidence requires the originating completed record.
Completing a local stage does not prove its upstream checks ran. Checks over
local History views consume actual controlled inputs before their deadline.
Predicate transport checks complete consumed keys, including interval and
violation identities, before selection. Later source observation candidates
are prepared before History selection consumes local rows.

Local numeric finishes rebind a retained source projection to finished values.
Downstream statistics check those actual inputs; a local stage does not erase
its source/input binding. Historical attribute selections retain captured
versions through a subsequent observation.

The in-memory exchange uses pandas while preserving the frozen logical schema,
compact Cell bindings, ordered parts, and resource ownership. Producer
key/Cell/business guarantees are trusted rather than
repeatedly re-audited. Ibis/provider value conversion may be lossy; persistence
continues to validate its existing Arrow/Parquet schema and Cell bindings.
Necessary indexes reject encountered duplicates and required lookups reject
missing operands. Methods still enforce their actual consumed Cell, finite,
coverage and arithmetic contracts. A partial or failed source read cannot
publish successful evidence.

## Retained schemas and method state

Store, graph, descriptor, continuation and method-state versions are distinct:

| Current protocol | Authority |
| --- | --- |
| SQLite Store user_version=9 | Session, Run, Artifact, resources, committed Evidence/Findings and execution-key uniqueness |
| graph_dag/v10, `graph-dag-v10:` | Bounded frozen Source/Fixed/Method records, final relationship paths, independent coordinate binding identities, ordered edges, derivations and retained references |

| run_input/v1 | Closed source/fixed invocation inputs and selected plan identity |
| artifact_descriptor/v6 | Signature, row/row-set and realized schema, producing Run/key, method bindings, completed records, receipts, state, continuation and saved time shape |
| receipt/v1 (primary), receipt/v2 (part) | Complete key schema, local storage facts, exact part payload SHA-256 and closed table/keyed/partition layout with an explicit owner role |
| method_state/v2 | Kind-dispatched state, binding and method/contract versions |
| continuation/v5 | Frozen graph and Entity/Dimension/semantic/method/input facts, without receipt or method-state proof digests |
| execution_key/v3 | Same-Session execution identity, independent of Store generation |

Graph v7 removes the field-read matching strategy and freezes scoped matching
assumptions instead of automatic matching obligations. Only graph v7 is accepted.
Prior graph protocols return a structured integrity error and source re-execution
guidance; there is no dual read or migration. Fixed grouping,
rollup and cold recovery use captured complete keys and coordinate bindings without
resolving relationships or acquiring source data.

Graph v7 stores Binding, DomainSignature, Fact and Evidence once in four closed
value tables. References bind their exact kind and nonnegative integer index;
values restore in dependency order without expanding a recursive JSON copy.
Canonical first-use ordering rejects duplicate, unused or inline pooled values.
The full uncompressed document, including tables, remains bounded at 4 MiB,
and the compressed/Base64 graph remains bounded at 256 KiB. Value tables admit
at most 16,384 entries and 65,536 references in addition to the existing graph
node, edge and depth budgets. Pooling preserves complete derivations, obligations,
retained endpoints, capture identities and definition fingerprints. Graph v6 and
earlier require source re-execution; existing state and files are preserved.


Complete graph protocol names have the `marivo.analysis.` prefix. Parts preserve
exact physical types, Decimal precision/scale and Duration/timestamp units.
Each Cell slot freezes its carrier and canonical reason dictionary in the
descriptor's primary Arrow schema or its part receipt. Parquet payloads carry
only physical fields; they do not repeat the dictionary in their headers. Reads
restore the one frozen binding before validating codes. Physical implementation ABI 7 and
state/part contract 4 bind this representation. Defined is code 0. Non-Defined
codes use the low two bits for Null (1), Undefined (2), Unknown (3), with the
high bits holding the dictionary ordinal (starting at 1). Dictionaries sort by
tag then reason, admit at most 8191 pairs, and are deterministically rebound when
inputs combine. Encoded state fields are non-null int16; invalid codes, undeclared
reasons and state/value-validity disagreements reject without fallback.

Contribution state is persisted directly as flat Arrow/Parquet. A keyed
coordinate receipt reuses its original or endpoint owner components and retains
only complete parent keys and presence. A partition receipt retains parent keys,
remaining coordinates and concrete component carriers. Receipts freeze the layout
and owner; restoration does not query Semantic or infer endpoint correspondence.
Descriptor v6, part receipt v2 and state/part contract 4 reject prior formats with
structured source re-execution guidance, preserving existing files. Store 9,
graph DAG v10 and continuation v5 remain independent; there is no migration or dual read.

Optional correspondence endpoints use -1 solely for an absent endpoint, outside
the four Cell states. Only a frozen optional binding permits that sentinel;
correspondence parts still own presence and pairing checks. An absent endpoint
is distinct from a present Null Cell. Empty DataFrame results retain bindings,
schemas and domains; actual row distributions never alter their carrier.

Descriptor v5 and earlier, method-state v1 and earlier, and prior state/part
contracts reject on recovery. Existing history and files are preserved. Re-execute
the producing source analysis to create a current Artifact; there is no migration
or alternate read path. Store 9 adds canonical Session domain-scope metadata;
Artifact and graph codec versions remain independent. Current public `show()` and
`to_pandas()` restore the same value/tag/reason fields and types.

Ordinary state includes Subject/coordinate maps, original components, current-row
statistic state, coverage, correspondence/endpoints and references as required.
Coordinate recovery preserves the numeric relation family, complete coordinate
rows and original state. Domain/statistical selection can restrict current rows
while retaining original assignment, trace, fit, classification, search, training,
or reconciliation scope. The tables below describe semantic layouts;
the typed protocol and method-state consumers own their exact field schemas.


| Method key / parameters | Output/state kind | Required retained parts and continuation condition |
| --- | --- | --- |
| existing parts_transport@v1 / bound selection, owned read, Subject image | receiver-specific transported state | preserve/rekey the required parts and explicit original scope; never recreate members from counts |
| existing row.mean@v1 / Duration current rows | row_statistic | exact checked tick sum/count, unit, complete selected domain and final rounding policy |

### Domain continuation conditions

| Producer/view | Permitted continuation and exact condition |
| --- | --- |
| parts_transport / row.mean | transport preserves only capabilities justified by surviving rekeyed parts; mean retains exact sum/count/unit for its existing row-statistic continuations, never original Metric state |

A missing required part removes its continuation and an attempted successor
fails at the earliest known boundary. Materialized receivers construct Logical
fixed work; successor derivation/admission still validate the request.
A serialized list of actions is not authority for K.

### Retained member layouts

| Part role | Complete row key | Required payload beyond the key |
| --- | --- | --- |
| subjects | exact instance K | exact complete Subject K and role/definition binding; total single-valued map |

No summary can reconstruct member identity without its retained map.
Statistical fit_inputs, fit_state, grid_cells, condition_cells, run_cells,
pair_inputs, association_state, training_inputs, forecast_state and future_cells
are specified by [the method owner](operators-and-frames.md#requiredparts-transformations-and-closed-failures).

## Deadline and resource ownership

Graph execution has no default wall-clock deadline. Source connections use
explicit datasource native settings; authoring scopes request native query
limits where supported and skip them otherwise. Marivo does not track remaining
time, reject late results, start deadline workers or send KILL QUERY.
Native connection, request and query timeouts retain their backend meanings.

Native errors, Python interruption, malformed DataFrame results and failed reads abort
uncommitted publication and release owned resources through their actual owners.
Elapsed time is diagnostic only. Durable success remains committed after an
acknowledgement error; unknown commit state belongs to reconciliation.

There are no occurrence/attempt/Subject/tie-width, input-row, part-row,
captured-byte or workspace-memory execution quotas. Protocol-envelope limits,
association's 4096 candidate ceiling, explicit horizon/limit bounds and Findings'
1000 cap remain separate contracts. Resource failure never truncates, samples
or replaces valid input.

Actual submissions, returned rows, simultaneous buffers, stage time and
output/part sizes describe cost; they do not qualify correctness or another
route. pandas materialization is eager and does not imply a streaming kernel:
rank/order methods and forecast retain their full required vectors; runs carries
unfinished sequence state.

Driver APIs own cancellation and close. Unknown remote termination is never
reported as confirmed termination. Writer/commit ownership must be safe before
continuation; unresolved commit or resource journal obligations block
reconciliation. Recovery never resubmits the action.

### Ibis read cleanup

SourceSession releases owned provider-scoped resources and connections,
including when one cleanup operation fails. Borrowed backend disconnection
belongs to the external owner. Ibis/provider execution is eager at the Marivo
boundary; Marivo does not maintain a separate row stream or cursor-close state.
Cleanup can block without suitable native settings; no additional worker or
deadline bounds it.

MySQL and ClickHouse graphs create no control connection or control resource
reservation. Python and provider interruption behavior remains native; there is
no custom SIGINT wakeup listener. A native timeout or an interrupted read never
publishes partial success. Remote receipts retain `remote_unknown` without
independent termination proof. Previously recorded unresolved control resources
remain reconciliation blockers; they are not silently discharged or targeted
by a new connection. Local-output resource journals, commit reconciliation and
atomic publication remain active.

## Store 9 publication and local trust

Output is project-local Parquet under `.marivo/analysis/generations/v9/`.
Session/Run/Artifact metadata is in the generation's SQLite Store.
Database result storage and project-level analysis storage settings are absent.

The writer journal tracks owned staging/final resources. Primary and every part
have independent typed schemas, key layouts and local receipts. Publication
makes complete Artifact, RequiredParts, Evidence, capped Findings and successful
Run visible in one Store transaction. Deadline-aware publication cannot commit
late or partial results. Failure cleanup preserves previously committed outputs
and another Run's resources.

Committed local results and private in-process compiler objects are trusted.
Readers decode required types/data without content hashes, anti-tamper chains,
producer method proofs or re-extracting Findings. Local receipts retain paths,
file sizes, row counts and format version without file/manifest/schema hashes.
Remaining definition, execution and recorded-evidence digests identify work;
they do not certify unchanged file bytes.

Store 8 and earlier, graph DAG v1 and superseded descriptor/continuation formats
reject without mutation, migration, dual reading or automatic rebuilding.
Preserve old bytes. A new Session may create the independent current-generation
directory in the same project; it never reads or upgrades the earlier generation.
`session.revalidate` and its former result types are removed. Re-executing a
current question creates a new result; it does not upgrade an old Store.

## Recovery and bounded reads

Session discovery selects only the current-generation Store. If its database
path is absent, `mv.session.current()` returns `None` and
`mv.session.recent(limit=...)` returns an empty page with the requested limit,
`has_more=False` and `next_cursor=None`. These reads never create Store state
or inspect earlier generations; earlier directories and unrelated sibling files
do not block discovery or creation of a new Session in the same project.
History arguments are validated before the absent-Store result. An existing
but invalid, unreadable or incompatible current Store still raises a structured
error. Missing Session identities cannot be resumed or created through recovery.

Use `session.runs(limit=..., cursor=...)`, `session.get_run(run_id)`,
`session.artifact(reference)` and `session.graph(...)`. Run variants are
incomplete, succeeded and failed; inspect the exact type before accessing
success-only or failure-only fields.

An execution error's optional `run_ref` identifies its exact Run when known.
Run admission supplies this identity to structured failures without changing
their specific type or repair. A failure before admission has no new Run;
explicit original-Run references in recovery errors remain authoritative.
Error text and instance Help provide the same-Session `get_run` inspection call.
Ordinary execution exceptions become `MaterializationError` with their original
cause; `KeyboardInterrupt` and `SystemExit` retain their interruption semantics.
Invocations never infer the error's Run from Runtime history.

Each source execution is a fresh invocation, including after failure; repeating
the logical relation creates a new Run rather than resuming the failed one.
`mv.session.abandon_run(session_id=..., run_id=...)` reconciles incomplete or
failed Runs and their owned resources under the writer guard. It preserves
audit history and cannot abandon committed success. `session.get_run` can read
incomplete, failed and succeeded Runs.

```python
import marivo.analysis as mv

run = session.get_run(run_id)
if isinstance(run, mv.SucceededRun):
    saved = session.artifact(run.output_artifact_ref)
    saved.show()
```

Recovery returns the concrete Materialized family from saved metadata, types
and required data. It preserves original time/report/calendar authority,
versions, primary values and parts. It does not select the original producer,
replay its method validation, load current models, read sources, refit statistics,
resegment runs or reforecast. Recovery cannot
establish freshness or suitability for a new question.

Inspection uses frozen graph records and signatures. A new continuation uses
retained parts and current successor capability, then enters ordinary
admission/execution. Independent operations decode their own metadata;
an invocation-local ValidatedDescriptor is a handoff, not a disk-integrity cache.
Missing/unreadable data, incompatible generations, invalid Session/cursor
ownership and incomplete publication still raise structured errors.

Cards/pages are bounded and deterministic; `to_pandas()` returns an isolated
copy. Run graphs report recorded input/output edges without inferring or
replaying historical lineage.

## Fixed execution groups

A validated fixed schedule may group adjacent admitted local stages.
Publication checks pending original-rollup obligations and fixed consumer domain
constraints before reading Artifacts or allocating a Run. The executor consumes
that same lowered schedule without repeating its validation or method admission;
actual Artifact input bindings, data consumption, deadlines and checks retain
their execution-time owners.
Groups are invocation-local arrangements of the original DAG; they do not
replace definition fingerprints, selected method records, plan digests,
execution keys or saved graph/continuation versions. Public semantics and
numerical qualifications remain unchanged.

### A2 sequential selection

After fixed schedule validation, the local executor may group adjacent selected
`artifact_python` int64 `PartsTransport(mode="where")` stages. The existing L1
owner checks the same quantity, domain, edge roles, retained parts and unknown
policy without constructing another MethodNode. Each stage remains independently
admitted and physically qualified; source execution and numeric type qualification
are unchanged.

Only receiver-bound ordinary predicates qualify. External dependencies, tag
selection, cohort, limit, display or attribution views, business-coverage changes,
and stages with pending check requirements retain their normal execution. A shared
intermediate, requested output, explicit Artifact leaf or unfamiliar part transport
ends a group. Ordinary Subject, original/coordinate/row state, coverage, statistical
weight, endpoint and correspondence parts qualify only with the actual required
schema and complete-key layout. Fixed references and domain-specific parts retain
their specialized owners.

A group converts its primary rows and builds its complete-key index once, preserving
duplicate-insertion failures. Predicates execute in original stage order over only
the surviving original positions; all leaves in each predicate tree are consumed
before truth composition. Required part key positions are located once. Primary
and restricted parts are constructed only at the group's consumption boundary,
with original row/part ordering and the exact terminal Signature and continuation.
Non-Defined predicate operands still fail with the original CoreRuleError; its
location identifies the consuming logical node.

Grouping is invocation-local and preserves the logical DAG, definition fingerprint,
ordered implementation records, per-stage proof digests and `plan_digest`. There
is no public optimization switch or persisted second graph. An unqualified group
uses the already selected ordinary stages. Once grouped computation starts, failure
never retries those stages or changes route. Native error propagation, resource cleanup and atomic publication still apply.

### A3 direct-key original reduction

After `validate_fixed_schedule`, the executor may group adjacent fixed
`artifact_python` OriginalReduce stages for sum, sum_zero, count, mean, ratio,
weighted mean and linear. The original logical DAG, node identities and selected
implementations remain intact. Qualification is invocation-local and inspects the
the implementation-local L8 contracts in
[`core/local_laws.py`](../../../marivo/analysis/core/local_laws.py) without
constructing or saving a replacement graph.

Each mapping must project the complete input key, and successive projections
may only remove coordinates. Each stage retains the same complete original-state
binding, contribution, method, output type and finish/empty policy. Only ordinary
Subject, original_state and coverage parts qualify, with their actual declared
schemas and full-key layouts. A shared intermediate, requested boundary,
explicit Artifact leaf, pending check, time coarsening map, retained contribution
coordinate, allocation or other specialized part ends a group. Reference or
weight changes and ordered folds never qualify.

A group consumes and indexes the original input once, merges every component
directly under terminal keys, then finishes and constructs the terminal result.
It preserves the terminal Signature, complete parts, method state, empty policy
and the original per-node proof chain. Exact carriers keep their captured range
checks; floating merges keep the existing numeric contract and retained absolute
magnitudes. This implementation-local L8 does not grant a new source route or numerical implementation.
The earlier explicit sum-only StateEquation helper retains its independent
qualification; invocation grouping does not invoke or broaden that helper.
Grouping does not scan sources to prove every hypothetical intermediate range.
Actual component and finish overflows remain execution failures in the captured
carrier, as specified by the accepted numerical policy.

Index insertion and grouping preserve the captured business checks.
Unqualified schedules execute their already selected ordinary stages. Once a
group starts, a numerical error, malformed consumed state, interruption or native timeout
propagates without retry; Runtime publication remains atomic.

## Evidence, Findings and interpretation

Evidence and Findings are deterministic committed facts about one Artifact.
Producer extraction/publication occur with the Artifact transaction.
Recovery reads saved typed records without regeneration.
Reads check the stored collection count and frozen extractor version, including
the empty collection required by a zero-Finding producer, without re-extraction
or comparing content hashes.

| Producer | Extractor / policy | Eligibility and deterministic order |
| --- | --- | --- |
| association.pearson/spearman/kendall@v1 | graph.association_findings@v1 / bounded_descriptive_findings@v1 | Every valid candidate, including nonselected lags; descending abs(coefficient), then complete typed key |
| forecast.naive/drift/seasonal_naive@v1 | graph.forecast_findings@v1 / bounded_prediction_findings@v1 | Every valid future point; complete typed series/future key |
| deviation.zscore/mad@v1 and time.runs@v1 | graph.no_findings@v1 / zero_findings@v1 | No automatic Finding; eligible/emitted/truncated=0 |

Nonzero policies count eligibility before cap=1000. Emitted is
min(eligible,1000); truncated is eligible-emitted. Selection transports the
producer's capped bodies, policy, original eligibility scope and selected-lag
facts, rebinding Artifact identities without a selected-only extraction.

Graph Association/Forecast subjects bind actual observed, derived, RowStatistic
or rolled quantities, including runtime Metric identities. No invented catalog
Metric Ref or raw Subject identity substitutes for the binding. Closed
subject/value contracts retain their own schema versions.

Read `evidence_digest`, `findings(limit=..., cursor=...)` or
`finding(finding_id)` on Materialized results. Empty committed Findings differ
from unavailable storage; cursor validity is bound to its owning read.
[Analysis Evidence Access](evidence-access-surface.md) owns their public contract.

Attribution is algebraic, association is descriptive, forecast is conditional
model output, and deviation/runs retain chosen fit/condition scope.
Publication and numerical admission do not establish causality, business
authority, calibration or release qualification.


## Execution diagnostics and usage telemetry

Execution diagnostics are always-on, project-local JSONL in
`.marivo/logs/execution-YYYY-MM-DD.NNN.jsonl`. They record actual Marivo-owned SQL
submissions before driver calls, submission/completion outcomes, returned row
counts, Analysis execution phases and retained Artifact reuse. Governed source
records retain the compiled Ibis SQL and its purpose/source identity.
Generated-only SQL, third-party driver/Ibis bootstrap SQL and Store persistence
SQL are outside their coverage. Local Python/pandas stages record timing and
result counts without inventing SQL.

Diagnostic records share available operation/session IDs with usage telemetry;
Run admission adds the actual Run ID. Project-root and operation contexts are
propagated into connectivity workers and captured by query records, so completion
still refers to the original project after ambient context changes. Diagnostics
observe execution facts; they do not admit operations, prove remote termination,
authorize publication or restore Artifacts. Runtime/Store remain the authority.

The telemetry sink retains its independently configurable operation-level usage
contract and excludes SQL text and exception messages. Turning it off does not
disable diagnostics. SQL stays verbatim and complete; bound credential values,
result rows and exception locals are excluded. Parameterized credential failures
retain the error type without their potentially sensitive message. Other errors
use the existing bounded, redacted backend summary.

Both sinks use the shared private JSONL append/rolling primitives with separate
content, directories and enablement. Execution logs roll by UTC day and an
approximate 128 MiB size threshold; individual records are never truncated. Daily
cleanup preserves the current day and unrelated files, retains 14 UTC days, and
caps managed historical files at 1 GiB. This is not a whole-directory hard cap.
Execution log directories/files use 0700/0600 permissions. Write and cleanup
failures never replace execution results/errors; rate-limited warnings and
per-project recovered-write drop counts expose logging failures.

## Session domain scope

`mv.session.get_or_create(name, domains="sales")` or a nonempty sequence selects
an immutable semantic scope on creation. Names are exact, deduplicated and
sorted. Omission recovers the saved selection, including None for all domains;
conflicting explicit selections fail before activation. `session.domains`
returns the persisted tuple or None. Source construction loads only that scope;
retained Artifact reads never require current semantic models.

The Session metadata column requires SQLite Store generation 9. Existing
Store-generation directories are preserved without migration or dual reads.
A prior generation is not qualified by the new source/fixed/cold acceptance.

ComparisonResult publishes both mandatory Cells and shared endpoint/correspondence
parts as one successful Artifact. Graph v8 includes CellCompare, ComparisonRead and
ComparisonPart in the shared codec. Store generation remains 9; older graphs require
source re-execution. Artifact restore returns MaterializedComparisonResult; reading
absolute/relative allocates no Run and needs no source access.
