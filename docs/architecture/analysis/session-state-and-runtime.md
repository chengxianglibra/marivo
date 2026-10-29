# Session State and Runtime

Session owns exact project execution/persistence context. Evaluation produces
complete data; publication commits that existing result. The
[Frame contract](analysis-frame.md) owns structure and
[Evidence](evidence-access-surface.md) owns interpretation of saved audit/method facts.

## Session and invocation ownership

Session binds project Store identity, frozen timezone, Catalog/source construction
and invocation-local services. Frames retain exact local ownership without
capturing the producer Session/Catalog, source connection or bound callback.
Frame/Column/FrameAxis dependencies stay alive while consumers need them; no
Session result cache, automatic eviction/spill or public close/unpersist is added.
Pure memory reading/computation does not require the persistent destination writable.

Session methods introduce Catalog, Store, execution ownership or frozen default
timezone. Even source-free `session.values(...)` needs the receiving Frame's
execution and persistence owner. Explicit descriptions introduce no Session;
composition such as
`mv.product(P, Q)` inherits its unique input owner and rejects conflicts.
Receiver methods derive from existing context; public result types are acquired
values, not supported constructors.

Construction, repr, Help and show do not evaluate or create a Run. Compute
completes immutable memory data without Run/Artifact/temporary saved output.
Expression persist is compute followed by publish-existing. Evaluation fails
before Run admission, returns no partial result, and cleans its own resources.
A publishing Run identifies the completed result ID, not a source graph. Result
evaluation may precede admission; commit time belongs to the publishing Run.
Memory persist never rereads sources. Stored persist checks commit metadata and
returns itself without another Run. Reusing a pending source expression can
read changed data; use the returned fixed root when exact prior values matter.

Choose retention before execution according to the caller's Python process lifetime.
Keep the returned memory Frame in a persistent process, or complete one-shot work
in the same script without publication. If a later process needs the same values,
persist and output/save the Session and Artifact IDs before the producer exits.
Printing a memory card after computation cannot recover that data once the process
has exited. Session acquisition alone does not recover unsaved results. An existing
exact saved reference needs no new publication; explicit new source evaluation
remains the way to obtain fresh values.

## Explicit lifecycle and frozen context

Use `mv.session.get_or_create(name, *, question=None, timezone=None, domains=None)` to
create a new project-unique name or recover the exact existing name. Empty names
and surrounding whitespace fail. Creation freezes question, normalized domains
and timezone. `timezone=None` resolves the system zone once at creation, retaining
its resolution facts; `domains=None` selects all authoring domain directories.
An explicit invalid zone fails. `session.timezone` returns the saved normalized name.

On recovery, omitted or `None` configuration retains saved values without
rechecking the system timezone. Non-None question, timezone and domains must
match the saved description, normalized timezone name and sorted, deduplicated
domain scope. Conflicts raise `SessionStateError` before recovery, with expected
and received values and the existing ID in `error.repair.candidates`; callers
can omit the configuration, pass matching values or use a new Session name.
Acquisition never rewrites the saved configuration or sets a current Session.
Concurrent creators share the unique-name transaction; a losing creator follows
the same configuration checks and recovery path. An active writer still rejects
recovery. `get_or_create` is the sole name-based acquisition entry point.

`mv.session.resume(session_id)` opens only that exact existing identity and performs
necessary recovery under the writer guard. `recent()` is bounded read-only
name/ID discovery; `inspect(session_id)` is exact-ID read-only inspection, returning
a SessionSummary with basic metadata and Artifact count. Neither discovery nor
inspection performs recovery. There is no implicit current Session or activation. A resume with no recovery work, an inspect,
or compute does not refresh updated_at; it records committed state changes.

Session recovery checks the original publishing transaction and owned resources
without source recomputation and refuses an active writer. Run records and
identities remain internal; no public Run history or targeted reconciliation is
exposed. `session.members(entity, ...)` accepts a resolved Entity with the existing identity/version/at contract. `values` defaults
to `by=()`. Source-free typed literal frames and `population_from(..., on=())` need
no unrelated Catalog load. Session has no source_bindings or graph method.

## One prepared stage schedule

Invocation capture collects exact Frame operands and sidecars without opening
sources. Source preparation opens and qualifies only explicit source inputs
under the evaluator's existing context stack. The pure compiler fixes one
topological schedule for source, literal, fixed and local operations. Source
Ibis executes through its declared datasource and returns pandas. Fixed reads
consume complete memory/stored data; local operations reuse existing kernels.
There is one prepared schedule, without recursive compute or a second local SQL executor.

After Frame planning, a qualified pending field-read Top can bound the native
read on DuckDB, PostgreSQL, MySQL, Trino or ClickHouse. The initial route requires
one consumer, ordinary complete identity axes with integer/string keys, and a
direct int64 score. Native ordering preserves NA-last and complete-key ties.
Full-input numeric and Frame integrity checks precede cropping, including Top-0.
Completed and reopened inputs continue through fixed-data execution without
source reads or uploads. Unsupported expressions retain their local stages.

`QueryPlan` owns executable stage layouts; lowering and physical rewrites return
that plan together with stage-ID-associated Ibis/check payloads. Native execution
programs retain their finish rules, independently of logical-read provenance and
historical rewrite members. Actual returned carriers determine local effective
types through the existing shared rules.

Every logical binding keeps full keys, types, presence and required checks. Consumer counts
control sharing/release; self-combination reads one shared input while retaining
both consumers. Preparation checks and demanded numeric flags retain their source
or formula owners and finish before downstream projection/Top-0 can hide failures.
Conditional branches retain their existing demand policy.
Checks on the same source relation share a multi-column aggregate summary;
source completion dispatches each retained obligation once. Summary-only rows
carry required failures even for empty data and are removed before public output.
Compatible stored handles share exact Store/Session/Artifact input identity;
equal fingerprints or names cannot substitute an input. Completed data contains
no source graph, internal aggregation state or semantic object.

Fixed inputs retain complete validation of rows, keys and frozen attachments.
Each exact scheduled input is read once and adapted without an intermediate
complete-data carrier. The scheduler holds data through its actual consumers;
method history retains validated immutable payloads separately. Terminal memory
roots and public exports keep their independent ownership and isolation copies.
After source cleanup, method facts fold in producer/operand order, with each
node's own facts before inherited facts and exact-origin conflict checks.

An ordinary-axis population_from bridge projects complete joint keys, then feeds
an explicit new source dependency through qualified parameter transport. Fixed
selections do not rerank against changed sources. Their new source values can
change, with no cross-read snapshot promise. Source-only relation qualification
is separate from fixed Frame inputs and local combination.

## Store generation and data layout

Store 22 is under `<project_root>/.marivo/analysis/generations/v22/`. One closed
`marivo.frame_output/v11` protocol stores values, complete axes, independent presence, typed
frozen interval/calendar/map attachments, receipts and one audit. It stores no executable
Python class path, source/Metric graph or hidden components. All analysis
artifacts are Frame commits; stored Frames expose their identity and audit directly.

Closed method facts distinguish level, absolute and relative targets, retaining
their explicit output paths and observed versus newly evaluated parent totals.
The process-local source declaration is not serialized. A persist return can retain
its caller's declaration reference; independently reopening the same Artifact
returns fixed data and method facts without that executable capability.

Actual row order is data: writers/readers do not reconstruct it by key sorting or
a second display-order field. Top order and left-first/right-only combination
order survive batching, persistence and reopen. Parquet row groups, compression
and batch profiles retain their owning storage implementation; no public
row-group setting or codec extension registry is added.

Older generations remain unchanged alongside a newly initialized current Store
in the same project. There is no migration or cross-generation data read. Each
version writes its own generation; Session names, IDs and Artifacts are independent
and are never merged or resolved across generations. An invalid current Store
still fails validation and cannot be replaced through initialization.

`get_or_create()` and `recent()` discover older Store paths through filesystem
metadata only and emit a Python warning once per project, current generation and
observed older-path set in each process. File mtime is not the last business write
time. Discovery does not inspect older schemas, count Sessions or create a manifest.
`recent()` returns only current history, including an empty page when the current
Store is absent. `resume()` and `inspect()` raise `SessionNotFoundError` when the
current Store or ID is absent; older paths add repair guidance without claiming
that the requested ID belongs to any of them. These reads create no Store.
Doctor reports retained older paths on every invocation and validates the current
Store through its owning validator.

Export required old results with a compatible Marivo environment. Old generations
are retained by default with no automatic cleanup. After verifying an export and
confirming that the original history is no longer needed, users may explicitly
archive or delete the old generation themselves.

## Concurrent writers and atomic publication

Evaluation produces complete data and audit before the existing writer guard
admits a publishing Run. Publication allocates Artifact identity, writes owned
files/receipts, and atomically commits manifest, audit records and Run success.
No partial successful Frame/Artifact/Finding/Evidence combination is visible.
Memory results and prior commits remain usable after publication failure.

Retain the existing Store writer guard, resource journal and success transaction.
Failures clean only their Run's resources. Unknown commit acknowledgement is
reconciled against that same transaction and identity; never reevaluate to settle
it. Stored persist is idempotent only for that stored commit; publishing a memory
root again may create a new commit without new evaluation. Retain the returned
Session and Artifact IDs for exact reopening. If an Artifact ID is lost, recover
the Session and inspect `session.persisted_frames()` before selecting an exact
commit. There is no public Run history or implicit latest result.
Unknown outcomes must first pass Session recovery; resubmission never bypasses
active writers, unresolved ownership or integrity checks.

## Resource lifetime and native cancellation

Each source invocation owns its connection/read resources and releases them after
its actual consumers. Fixed data does not retain those resources. Existing native
query-timeout/cleanup contracts apply to source reads; a caller row limit is not
a scan bound or an end-to-end deadline. A cleanup failure is reported through the
owning structured error. No new timer, automatic route retry or cancellation
control channel is introduced by Frame carriers.

Complete memory data is independent of old files after stored.compute. Deleting
old payload later does not invalidate that memory root or its new persistence;
a fresh full read from the old stored root still fails. Strong lifetime ownership
does not guarantee the external Store remains available.

## Errors and recovery

Errors state expected input, received input and a repair from real state.
Cross-Session ownership, foreign exact handles, incomplete maps, incompatible
types/units/time, invalid value names and actual data integrity fail at their
earliest information-complete boundary. Construction never reads rows to guess
an answer. A strict source failure cannot become a missing cell or be hidden by
an output projection. Missing stored IDs use ArtifactNotFoundError; malformed
protocol/coordinates are format failures, not a non-Frame-kind guess.

Save Session ID and `saved.artifact_id`. Resume the same Session and call
`session.persisted_frame(artifact_id)` with the exact string. This opens commit
metadata without Catalog/model/source access or a payload scan. It does not
recover original source objects or history. Missing/corrupt saved data fails;
it is not a cache miss that authorizes recomputation.

## Committed Frame discovery and descriptions

`frame.persist(description=None)` optionally records the caller's purpose in the
same atomic commit as its data and audit. The text is preserved unchanged and
belongs to the commit, not the computed result or expression identity. Stored
Frames expose read-only `description`; pending and memory Frames require an
explicit persist first. Repeating stored persist with omitted/None or the same
description validates and returns that commit. A different non-None description
fails; use `compute().persist(description=...)` for a new commit. New commits do
not inherit an ancestor's description.

`session.persisted_frames(*, limit=20, cursor=None)` returns immutable
`PersistedFrameSummaryPage` items with `artifact_id`, `committed_at`, optional
`description` and the existing complete `FrameSchema`. It reads only successful
current-generation commits from that Session, in one SQLite metadata snapshot,
ordered by `(committed_at, artifact_id)` descending. Limits are 1..100, excluding
bool. Cursors bind Store, Session, operation and page size; continue with the same
limit. Pages do not freeze history across requests; discover new commits from
the first page. Empty Sessions return empty pages.

Discovery validates selected commit metadata without result-file reads, Catalog
loads, source access, recovery, publication or Session updates. Corrupt metadata
fails rather than disappearing from the directory. Summary and page cards show
bounded descriptions, output names and axis names/roles; typed schemas remain
complete. A Session summary with saved Artifacts points to resume and discovery.
Descriptions are caller claims, not proof of business meaning or freshness.
Missing descriptions are explicit; ambiguous candidates require further
inspection or caller selection. Open a selected `artifact_id` through the
existing `persisted_frame(id)` and inspect its schema, lineage or saved evidence.
The directory does not establish which tool invocation created a candidate.

## Metadata opening, preview and complete export

Pending Frame.show discloses captured structure and pending operations without
parent row reads/schema preparation. All show calls validate nonnegative integer
n (excluding bool) before reads. Memory/stored show(n=0) gives metadata only;
Completed cards include bounded inputs/operations and source-fact/check/Finding
counts; stored cards add commit time. Summaries never load complete audit or
method payloads. Frame Findings and Evidence reads do not read Parquet or
coordinate attachments.

Positive show and read_page share the internal ordered range reader and resolve
only necessary coordinates. They validate consumed file receipts, selected field
physical types/nullability/numeric constraints, page-local complete keys,
unique required coordinate resolution and required presence relations. They do
not certify cross-page uniqueness, unread semantic fields/rows or the complete
calendar domain/mapping. A consumed file's receipt mismatch fails even when the
changed bytes belong to an unreturned row. No full-export/head, sampling, silent
fallback or cached prefix may replace a bounded read. show's nonnegative n is
independent of read_page's 10000-row page limit.

read_page always retains full axis keys and readable coordinates, with optional
full-name value/presence projection. Primary reads use selected columns and
bounded batches; coordinate lookup scans fixed batches and retains only required
records. Detached pages retain no whole parent Arrow buffer, coordinate directory,
reader, audit or method facts. Stored totals come from receipts; audit counts come
from saved collection descriptors. Receipt hashing and coordinate lookup may
scan whole files, so bounded memory does not imply bounded total I/O. Captured
read_stats records actual decoded/checked data rows, coordinate rows and receipt
hash bytes. It does not measure peak memory or all disk I/O.

Persisted descriptors include structure, lineage, evaluation/input/operation facts,
collection counts/digests and owned payload descriptors, without audit bodies.
Opening and show(n=0) check commit ownership/version, bounded descriptors, receipt
locations and collection metadata in SQLite, without complete records or method
files. Findings and Evidence each read their own ordered page in a single SQLite
snapshot, checking each body's digest/types/identity/slots plus a bounded next
record probe. They never scan count(*) or recompute full collection digests.
Expected missing, duplicate, disordered or extra tail records fail explicitly.
Publication atomically writes descriptors and records. Full audit consumers,
transaction confirmation and required recovery retain complete-record and
collection checks, reconstructing source facts and checks from saved partitions.
Saved derived results remain self-contained without ancestor or source reads.

`to_pandas()` validates all current data, complete key uniqueness, all related
coordinates and mapping authority. It does not consume complete audit or method
payloads. It returns isolated flat coordinates, full value names and independent
presence columns. Its existing attrs snapshot contains axes, units, comparisons
and isolated frozen coordinate tables; it is not a stable deserialization
protocol. FramePage.to_pandas only copies this page, with empty attrs. Both use
the same layout and coordinate decoding. Stable clients use schema/layout and
exact coordinates instead of attrs. Complete stored compute/fixed-input evaluation
consumes data, required method facts and complete audit. Neither boundary proves
business completeness or current source state.

`method_facts` explicitly consumes complete immutable explanation payloads only,
validating the owned descriptors, structure, digest and numerical consistency;
it does not read primary rows, Catalog, model or source. Pending access requires
compute/persist first. A fact's bounded show/repr operates in memory after that
read. Stored compute and evaluated derivatives consume current data and complete
method facts. A missing/corrupt method payload can leave metadata, previews and
current-data export usable while full consumers fail. Cards show only count,
bounded origins and the getter pointer, never full parents/coordinate rows.

All Frames use `marivo.frame_output/v11`, `frame.v11` and `frame-output-v11` under
`.marivo/analysis/generations/v22/`. Method files share the existing data receipt,
atomic publication/readback and unknown-commit reconciliation. A saved derivative
owns its exact inherited payloads; deleting the original Artifact does not force
an origin lookup or source/calendar rebuild. Origin explains the original total,
not a freshly filtered or aggregated decomposition.

## Execution diagnostics and usage telemetry

Execution diagnostics are always-on project-local JSONL in
`.marivo/logs/execution-YYYY-MM-DD.NNN.jsonl`. They record actual Marivo-owned SQL
submissions before driver calls, completion outcomes, returned row counts and
analysis phases. Governed source records retain compiled Ibis SQL and its
purpose/source identity. Generated-only SQL, third-party bootstrap SQL and Store
persistence SQL are outside coverage. Local pandas stages record timing/counts
without inventing SQL. Operation/project context propagates into connectivity
workers; Run admission adds the actual Run ID.

Each newly emitted record has `schema_version: 2` and a sink-owned `runtime`
object: `marivo_version`, `python_version`, `python_executable`, `package_path`
and `process_id`. Version, executable and package path share the facts used by
the current environment fingerprint. Identity is captured lazily and cached per
PID; a child process refreshes it. Every event carries the complete identity,
including query and local-stage events, so reading a tail or rotated segment
does not require locating a startup event. Caller context cannot override runtime
or schema version. Identity capture remains inside logging failure isolation.

These fields identify the declared software version, interpreter, package path
and process, not the package's content hash, a data snapshot or build attestation.
Equal development versions and paths do not establish equal code. No Git scan,
subprocess, environment dump or installation-directory hashing is performed.
Doctor describes the current environment, not historical events. Existing lines
are not rewritten; missing runtime means unknown historical identity. A file can
contain both old and new records, interpreted by each line's schema version.

Diagnostics do not admit operations, prove remote termination or authorize
publication. Independently configurable usage telemetry excludes SQL text and
exception messages; disabling it does not disable execution diagnostics. Bound
credential values, returned rows and exception locals are excluded from these
logs. Credential failures retain error type without a potentially sensitive
message; other errors use the bounded redacted backend summary.

Usage telemetry accepts only `on` and `off`, defaulting to `on`. The
`MARIVO_TELEMETRY` environment variable overrides `[telemetry].enabled` in
`marivo.toml`; invalid environment values are ignored, while invalid project
values fail configuration validation. `ProjectConfig.telemetry_enabled` and
`ProjectConfig.telemetry_mode` retain the parsed boolean and on/off setting.
When enabled, explicit long operations emit start and completion events, while
short operations emit completion only. Lightweight reads emit failures only.
Analysis constructors and semantic references emit no events. Delegated and
internal-load calls omit success events but retain eligible failures with their
parent operation identity. Same-capability wrappers do not duplicate events.
Disabling telemetry preserves execution-log operation context and skips telemetry
attribute preparation in public capability wrappers.

The sinks share private append/rolling primitives while retaining separate content,
directories and enablement. Execution logs roll by UTC day and approximately
128 MiB; individual records are not truncated. Cleanup preserves the current day
and unrelated files, retains 14 UTC days and caps managed historical files at
1 GiB. This is not a whole-directory hard cap. Directories/files use 0700/0600.
Logging failures never replace the execution result/error; rate-limited warnings
and recovered-write drop counts disclose failures.

## Implementation map

[session/core.py](../../../marivo/analysis/session/core.py) owns the public Session;
[frame_plan.py](../../../marivo/analysis/compiler/frame_plan.py) and
[graph_plan.py](../../../marivo/analysis/compiler/graph_plan.py) own preparation.
[frame_evaluation.py](../../../marivo/analysis/materialization/frame_evaluation.py)
evaluates runless complete results; [graph_execution.py](../../../marivo/analysis/materialization/graph_execution.py)
schedules stages. [frame_services.py](../../../marivo/analysis/materialization/frame_services.py)
holds local ownership without a producer Session.
[terminal_publication.py](../../../marivo/analysis/materialization/terminal_publication.py),
[store.py](../../../marivo/analysis/materialization/store.py),
[writer_guard.py](../../../marivo/analysis/materialization/writer_guard.py) and
[reconciliation.py](../../../marivo/analysis/materialization/reconciliation.py) own publication/recovery.
[graph_reading.py](../../../marivo/analysis/materialization/graph_reading.py) owns
metadata/preview/complete readers; [pandas_export.py](../../../marivo/analysis/materialization/pandas_export.py)
owns isolated export.

## Captured semantic lineage

Completed result metadata includes immutable, non-executable semantic column
lineage. Pending Frames derive it from explicit inputs at construction; compute,
publication and cold recovery preserve it. Store 22 / output v11 requires the
complete value/axis/presence directories and their captured identities. Missing or
invalid saved lineage is an integrity failure, rather than empty lineage. A saved
derivative owns its complete lineage without requiring ancestor artifacts or the
original model. Reading frame.lineage does not consume rows, coordinate attachments,
method payloads, or source connections. Older Store/output formats are not migrated.
