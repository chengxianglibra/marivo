# Session State and Runtime

Execution follows the [unified operator and backend ownership contract](python-analysis-design.md#unified-operator-and-execution-ownership). Backend-specific preparation does not change operator semantics.


A Session owns one investigation and its immutable Run/Artifact history under the
project's `.marivo/` directory. Use `mv.session.get_or_create(name, ...)`,
`mv.session.current()` and `mv.session.resume(session_id)` through their native
Help contracts. Identity resolution and report-timezone conflicts fail explicitly;
opening a different Session never grants access to another Session's inputs.

## Source boundary

Session owns `observe`, `population`, `events.match`, `lifecycle.replay` and
`source_bindings`. Dataset methods own downstream operations. Constructors load
needed semantic definitions and certified project snapshots without querying
sources. They capture immutable source binding values, period authority and
persisted report timezone. Credentials and live engine timezone are resolved only
for admitted source execution.

An Entity key is stable identity; version coordinates are separate. Membership
selection and observation windows are independent. Definitions retain exact
semantic dependencies, roles and field identities. RuntimeMetric expressions
normalize into the same governed graph and retain each carried output's identity.

## Fixed execution and storage

`execute()` admits a Run, fixes a registered implementation and writes one atomic
result. Shared logical inputs can share realization under exact identity; existing
materialized inputs remain immutable. No failed operation is retried on another
executor or storage target.

No storage configuration is required: output uses project-local Parquet. Explicit
object storage is selected in project configuration:

```toml
[analysis]
storage = "object:archive"

[analysis.object_stores.archive]
endpoint_url = "https://objects.example.test"
bucket = "analysis"
access_key_id_env = "MARIVO_ARCHIVE_ACCESS_ID"
secret_access_key_env = "MARIVO_ARCHIVE_SECRET"
```

`storage = "local"` explicitly chooses the default. Additional named object
bindings can remain configured for reading exact older Artifacts. Credentials
are environment references; serialized project/Artifact state never contains the
resolved secrets. An absent or invalid explicit binding fails without fallback.
Changing the selected write target does not relocate retained results.

Database result storage is absent. Registered native methods may scan immutable
Parquet using transient DuckDB execution resources. Every primary and private
part uses the exact selected storage authority, with independent schemas,
cardinalities, hashes and integrity checks. Cleanup covers interrupted and failed
publication without deleting another Run's resources.

## Atomic Store v5

A new Store publishes only a complete initialized generation 5 database. Existing
v0, v2, v3, v4 or other incompatible generations fail read-only preflight; their original
bytes remain intact. No migration, dual reader or in-place generation upgrade is
provided. Older generation files and resource obligations remain untouched.

A successful publication commits the Run terminal, Artifact descriptor, storage
receipts, Evidence and Findings together. A failed Run has no successful output;
an interrupted Run is incomplete until its explicit lifecycle operation. Store
writer ownership and caller-owned transactions govern all related records.

## Recovery and bounded reads

Use `session.runs(limit=..., cursor=...)`, `session.get_run(run_id)`,
`session.artifact(reference)` and `session.graph(...)`. Runs have closed
`incomplete`, `succeeded` and `failed` variants. Inspect the exact type before
reading success-only or failure-only fields. Graphs report recorded input/output
relationships; they do not infer or replay lineage.

```python
run = session.get_run(run_id)
if isinstance(run, mv.SucceededRun):
    saved = session.artifact(run.output_artifact_ref)
    saved.show()
```

Recovery binds a concrete Materialized Dataset from retained facts. It does not
open current sources. Retained filters, projection, aggregation and other admitted
continuations use complete retained rows/private state. Their Runtime guards
apply even when the final output is small. Source-offline cold recovery preserves
stored report/read/calendar authority rather than resolving the new host's zone.

`session.revalidate(reference)` exposes separate Artifact, storage-authority and
Evidence integrity assessments. It is not a source-freshness verdict, permission
to reuse stale values, or a business recommendation. Runtime cards and pages stay
bounded and do not expose raw secrets or private implementation inventories.

## Read-only execution cleanup

Normal driver cancellation and close are attempted on errors or interruption.
Missing query IDs, failed cancellation and unknown remote read status do not
certify server termination and do not block later work once Store commit state
and local writer ownership are safe. Failed Runs have no successful local output.
Unknown commit state, conflicting publishers and unresolved write-capable object
requests still prevent unsafe continuation. Recovery never resubmits the action.
Engine, driver and Ibis versions are diagnostics, not admission or identity facts.
