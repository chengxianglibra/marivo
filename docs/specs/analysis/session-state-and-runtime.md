# Session State and Runtime

Status: current Store 11 terminal execution, publication and reading contract,
2026-10-18. [Analysis Design](python-analysis-design.md) owns construction and
compilation; [Operators and Frames](operators-and-frames.md) owns computation.
This document does not qualify every target DSL route or remote backend.

## Session and invocation ownership

A Session owns an investigation and Run/Artifact history beneath the project's
`.marivo/` directory. Bootstrap, current selection and resume use
`mv.session.get_or_create(...)`, `mv.session.current()` and
`mv.session.resume(session_id)`. Report timezone is persisted authority; conflicting
explicit reopen settings and cross-Session operands fail.

The optional domains selection is immutable Session scope: exact names are
sorted and deduplicated, omission reuses the saved choice, and conflicting
explicit choices reject. Source construction loads only that semantic scope.
Committed output reads do not load current models. Same-generation discovery
uses the current Store only; an absent current Store yields no current Session
and an empty recent page without creating state. Invalid history arguments are
still rejected before returning an absent-Store result.

Query/Population construction, repr, Help and `.show()` inspection create no Run
and read no business rows or source schemas. `session.read(population, fields)`
captures one explicit Session and complete identity domain; no ambient Session
resolution occurs. Execution preparation may connect for actual schemas and
physical admission. Source templates are prevalidated before Run admission,
and instantiated inputs are checked again before consumption. Required schema,
route and binding failures precede the affected business read.

Every admitted execute has a fresh Run/evaluation identity and reevaluates its
source and literal dependencies. Reusing a Query does not reuse its previous
Artifact or selected rows. Definition identity is not a source snapshot; independent
reads have no cross-source or cross-query transaction guarantee. Execution-only
sharing is scoped to the actual invocation and explicit producer dependencies.

## One prepared stage schedule

Preparation fixes source identities, projected fields, complete keys, physical
types, occurrence-specific paths/anchors, numerical routes and check deadlines.
The pure compiler then fixes a topological schedule. The Runtime executes that
schedule without loading an Artifact, opening another executor, changing algorithm
or retrying a missing route.

Source reads lower admitted Ibis expressions and return pandas through
SourceSession.execute. Local value, domain and comparison work consumes pandas
stage outputs. Literal and source-dependent domain inputs are explicit stages;
any complete-key transport to a later source consumer has a qualified planned
route. No hidden source write or local SQL engine supplies a fallback.

Stage normalization validates the ordered schema, complete-key uniqueness, actual
dtypes, nullability, timezone and units. Under the revised
[source handoff contract](lazy-analysis-dsl-execution.md#151-ibis-原生返回与-pandas-类型交接),
native source numerical precision loss is accepted and backend type differences
are normalized in pandas; this adjustment still requires implementation. Saved-output
fidelity applies to the final accepted values, not the original database values. An input producer may
serve multiple consumers; consumers do not mutate shared frames. A stage remains
available until its last scheduled consumer, then its execution-only data can be
released. RequiredSlots retains coordinate, mapping, presence and check support
that the final business value projection does not expose.

Checks bind their actual source occurrence or local producer output and a consume
or publish deadline. The scheduler runs a ready check before its dependent
consumer/release, or before publication as required. A missing route rejects during
planning. A failed mapping, duplicate key, coverage, strict-NA or arithmetic check
prevents successful publication. Projection, filtering and Top-N cannot erase a
still-required check. Evidence records only checks that actually completed over
that invocation's input.

A source check and later calculation are independent reads unless an admitted
route explicitly shares the actual input. Equal names, definitions or hashes do
not supply that guarantee. Operators such as share that require one actual input
must retain it in their selected route.

## Store generation and data layout

The current Store is generation 11 under `.marivo/analysis/generations/v11/`.
Its SQLite database owns Sessions, Runs, Artifact manifests, resources and output
records. Final values live in committed project-local Parquet. There is no
configurable result database, saved execution graph or historical input mode.

`ArtifactPublication` accepts final pandas output plus explicit output records.
Its frozen schema retains complete ordered keys, named nullable values and
independent boolean presence. Necessary output attachments retain calendar
nominal/actual bounds, coordinate projections and display order. Saved metadata
contains data names/tokens, key component types, units, timezone and bounds.
It contains no semantic Ref, formula, definition fingerprint, internal aggregation
components or executable continuation. Run input records retain an invocation
marker and lifecycle rather than a replayable graph.

Output uses Parquet version 2.6 with zstd level 3 and at most 128 Ki rows per row
group. The shared reader batch profile is at most 64 Ki rows. These file profiles
do not bound upstream eager pandas memory or claim streaming execution.
Each output part has an explicit schema and committed local receipt.

Store 11 removes the obsolete Artifact-input relation and index. Run records expose
no consumed Artifact references; `dataset_input` remains the invocation marker.
The terminal output v1 codec and Parquet profiles are unchanged.

Store 11 is an incompatible format switch. Store 10 and earlier Stores and graph/continuation
formats reject without mutation, migration, dual reads or rebuilding. Changing
a Session name inside an incompatible project does not grant a new format.
Preserve the old project state, use a fresh current-format project and rerun the
script. A current-generation resume restores Session identity/history, not a
computation receiver.

Only terminal publication admits new Runs. The retired graph/Cell executor,
publisher and historical graph decoder are removed rather than retained as a
compatibility route. Shared receipt, path, ownership and resource checks remain
part of terminal execution and reading.

## Concurrent writers and atomic publication

An admitted write uses the existing per-Session writer guard and resource journal.
Reconciliation runs under that guard before new work. SQLite uses WAL durability;
write transactions use the Store's established transaction boundary. Readers see
committed records. An invocation never deletes another Run's owned files or
previously committed output during cleanup.

The writer reserves resources, creates staging files, closes/fsyncs data, checks
receipts and places final files before the SQLite success transaction. That
transaction commits manifest, Findings/Evidence, Run success and resource ownership
as one visible unit. Only durable success returns an Artifact. A failure before
that commit publishes no partial Artifact or independent success records.

Lost acknowledgement after durable commit is resolved against the original Run
and transaction without replaying the calculation. An unknown commit outcome
preserves its journal and owned resources until reconciliation determines it.
Reconciliation cleans exactly owned uncommitted resources, preserves committed
success, and leaves unresolved resource/commit obligations as blockers. New
execution does not silently discharge such obligations.

## Resource lifetime and native cancellation

There is no default graph wall-clock deadline or row/byte/memory execution quota.
Datasource connection/request/query settings retain their native meanings;
authoring read scopes use the owning datasource's supported limits. Elapsed time
is diagnostic, not a reason to publish partial output or retry another route.
Memory/resource failure does not truncate, sample or replace the valid input.

SourceSession releases every owned provider-scoped resource and connection,
including when another cleanup operation fails. Borrowed backend disconnection
belongs to its external owner. Ibis/provider execution is eager at this boundary;
Marivo maintains no separate row-stream or cursor-close state. Cleanup can block
without suitable native settings; there is no additional deadline worker.

Driver APIs own cancellation and close. Python interruption and native query errors
abort uncommitted publication. Unknown remote termination remains unknown rather
than being reported as confirmed cancellation. No custom KILL QUERY connection or
SIGINT listener is created. Previously unresolved remote resources remain owned
reconciliation obligations. KeyboardInterrupt and SystemExit retain their normal
interruption semantics; durable success remains committed after acknowledgement
failure.

## Errors and recovery

Structured failures state expected input, received facts and concrete repair.
After admission, an error's optional `run_ref` identifies the exact Run; inspect
it through that same Session's `get_run`. Before admission there is no new Run.
Ordinary execution exceptions are wrapped by the owning materialization error
with their cause; the Runtime never guesses the failed Run from recent history.

```python
run = session.get_run(error.run_ref)
run.show()
```

`session.runs(limit=..., cursor=...)` and `session.get_run(id)` expose incomplete,
failed and succeeded variants. Access success-only fields only on success.
`mv.session.abandon_run(session_id=..., run_id=...)` reconciles incomplete/failed
Runs under the writer guard, preserves history and cannot abandon committed
success. Retrying a corrected Query is a new invocation rather than resume of
failed computation.

Session graph reads show same-Session Run-to-Artifact production edges and saved
output summaries. Failed and incomplete Runs remain isolated nodes. Every selected
Run is a root and every selected Artifact a head; heads do not select a latest
answer. The recap counts all Artifacts as heads and displays at most three refs.
An Artifact-focused `ancestors` read includes its unique producer; `descendants`
returns only the terminal Artifact. Without a focus, only `ancestors` is admitted.
Selection preserves attention priority, deterministic producer-before-output order
and the existing node/scan bounds. Boundary fields identify selected nodes with
omitted requested neighbors; `truncated` reports an incomplete selection. Reads use
one SQLite snapshot without Parquet, current models or source access. They do not
reconstruct computation lineage or replay an origin.
Invalid Session/cursor ownership, missing records, incomplete publication and
unreadable/incompatible current Store state remain structured errors.

## Metadata opening, preview and complete export

`session.artifact(artifact_id)` accepts an exact string ID belonging to that Session
and opens its saved manifest/receipt metadata. Opening and repr read no Parquet,
current Catalog or datasource. New and reopened Artifacts use the same committed
output reader. Holding the original Query gives the Artifact no additional methods.

`show(n=20)` decodes bounded rows. Only consumed parts are verified, although their
first digest verification may read the whole file. `to_pandas()` exports every
row as an isolated DataFrame; it never silently truncates. Types, precision, NA,
complete composite identities, Field Null categories, presence, coordinate
projections and display order survive the saved-data boundary. Empty and keys-only
outputs retain their schema. A corrupt or missing consumed part fails rather than
falling back to a source read or cached in-memory output.

Export uses RangeIndex and `(kind, name)` columns. Data-only attrs describe actual
axes and quantities; calendar starts and UTC nominal/actual boundaries retain the
saved boundary timezone. Artifact has no contract, Column, population or DSL
continuation. Custom computation continues in ordinary pandas.

Findings use bounded pages. `evidence_digest()` accepts no arguments and returns
all saved Evidence in `.items` plus collection summaries; its show remains bounded.
Record reads do not need result Parquet or current semantic definitions. Their
independence does not certify freshness, source completeness or business meaning.
Detailed record shapes belong to [Evidence Access](evidence-access-surface.md).

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
