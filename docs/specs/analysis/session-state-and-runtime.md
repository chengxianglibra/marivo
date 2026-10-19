# Session State and Runtime

Status: AnalysisFrame cutover lifecycle contract, 2026-10-19. The
[final data contract](query-result-reuse.md) owns semantics; the implementation
progress record owns acceptance. This page is not a provider/release certificate.

## Session and invocation ownership

Session binds project Store identity, report timezone, Catalog/source construction
and invocation-local services. Frames retain exact local ownership without
capturing the producer Session/Catalog, source connection or bound callback.
Frame/Column/FrameAxis dependencies stay alive while consumers need them; no
Session result cache, automatic eviction/spill or public close/unpersist is added.
Pure memory reading/computation does not require the persistent destination writable.

Construction, repr, Help and show do not evaluate or create a Run. Compute
completes immutable memory data without Run/Artifact/temporary saved output.
Expression persist is compute followed by publish-existing. Evaluation fails
before Run admission, returns no partial result, and cleans its own resources.
A publishing Run identifies the completed result ID, not a source graph. Result
evaluation may precede admission; commit time belongs to the publishing Run.
Memory persist never rereads sources. Stored persist checks commit metadata and
returns itself without another Run. Reusing a pending source expression can
read changed data; use the returned fixed root when exact prior values matter.

## One prepared stage schedule

Preparation resolves only explicit source inputs. The pure compiler fixes one
topological schedule for source, literal, fixed and local operations. Source
Ibis executes through its declared datasource and returns pandas. Fixed reads
consume complete memory/stored data; local operations reuse existing kernels.
No recursive compute, second executor or hidden local SQL engine is added.

Every logical binding keeps full keys, types, presence and checks. Consumer counts
control sharing/release; self-combination reads one shared input while retaining
both consumers. Strict obligations finish before projection/Top-0 can prune values.
Compatible stored handles share exact Store/Session/Artifact input identity;
equal fingerprints or names cannot substitute an input. Completed data contains
no source graph, internal aggregation state or semantic object.

An ordinary-axis population_from bridge projects complete joint keys, then feeds
an explicit new source dependency through qualified parameter transport. Fixed
selections do not rerank against changed sources. Their new source values can
change, with no cross-read snapshot promise. Source-only relation qualification
is separate from fixed Frame inputs and local combination.

## Store generation and data layout

Store 12 is under `<project_root>/.marivo/analysis/generations/v12/`. One closed
Frame output protocol stores values, complete axes, independent presence, typed
frozen calendar/map attachments, receipts and one audit. It stores no executable
Python class path, source/Metric graph or hidden components. All analysis
Artifacts are Frame commits; Artifact and stored Frame share one commit authority.

Actual row order is data: writers/readers do not reconstruct it by key sorting or
a second display-order field. Top order and left-first/right-only combination
order survive batching, persistence and reopen. Parquet row groups, compression
and batch profiles retain their owning storage implementation; no public
row-group setting or codec extension registry is added.

Older generations' Session/result/Findings/Evidence data refuse unchanged. There
is no migration, dual read or new Store initialization around an old-only project.
Documented current/recent discovery can return None/empty for an absent current
generation without exposing old state. Export required old results with their
original version or retain a compatible environment. See [cutover](analysis-frame-cutover.md).

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
root again may create a new commit without new evaluation. SessionGraph retains
Run-to-Artifact publishing edges and does not claim complete data lineage or
choose a latest answer.

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

Save Session ID and `saved.artifact.id`. Resume the same Session and call
`session.persisted_frame(artifact_id)` with the exact string. This opens commit
metadata without Catalog/model/source access or a payload scan. It does not
recover original source objects or history. Missing/corrupt saved data fails;
it is not a cache miss that authorizes recomputation.

## Metadata opening, preview and complete export

Pending Frame.show discloses captured structure and pending operations without
parent row reads/schema preparation. All show calls validate nonnegative integer
n (excluding bool) before reads. Memory/stored show(n=0) gives metadata only;
Artifact.show() has no n and reads submission/audit only. Findings and Evidence
reads do not read Parquet or coordinate attachments.

Positive show renders the actual prefix in result order and resolves only needed
frozen labels, nominal/actual bounds and paired endpoints. It cannot use full
export/head, sample, silently fall back on metadata, or cache a prefix as complete
data. Receipt hashing or Parquet decompression may consume more bytes than n;
preview success says nothing about unread payload integrity.

Stored compute/to_pandas validates complete required values/keys/presence and
attachments: receipts, types, nullability, finite values, uniqueness, directory
references and paired maps. Missing/corrupt records never become legitimate
unmatched endpoints. Full export is isolated and retains raw coordinate keys in
`(kind, name)` MultiIndex columns, with kinds coordinate/value/presence. Typed
attrs contain axis descriptions, units and isolated frozen coordinate tables;
no reader, Frame, Store, semantic object or executable calendar is retained.
Attrs cannot be reimported as computation authority.

## Execution diagnostics and usage telemetry

Execution diagnostics are always-on project-local JSONL in
`.marivo/logs/execution-YYYY-MM-DD.NNN.jsonl`. They record actual Marivo-owned SQL
submissions before driver calls, completion outcomes, returned row counts and
analysis phases. Governed source records retain compiled Ibis SQL and its
purpose/source identity. Generated-only SQL, third-party bootstrap SQL and Store
persistence SQL are outside coverage. Local pandas stages record timing/counts
without inventing SQL. Operation/project context propagates into connectivity
workers; Run admission adds the actual Run ID.

Diagnostics do not admit operations, prove remote termination or authorize
publication. Independently configurable usage telemetry excludes SQL text and
exception messages; disabling it does not disable execution diagnostics. Bound
credential values, returned rows and exception locals are excluded from these
logs. Credential failures retain error type without a potentially sensitive
message; other errors use the bounded redacted backend summary.

The sinks share private append/rolling primitives while retaining separate content,
directories and enablement. Execution logs roll by UTC day and approximately
128 MiB; individual records are not truncated. Cleanup preserves the current day
and unrelated files, retains 14 UTC days and caps managed historical files at
1 GiB. This is not a whole-directory hard cap. Directories/files use 0700/0600.
Logging failures never replace the execution result/error; rate-limited warnings
and recovered-write drop counts disclose failures.
