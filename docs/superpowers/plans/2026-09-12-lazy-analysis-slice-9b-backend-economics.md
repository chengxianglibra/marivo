# Slice 9b: Backend and execution-economics acceptance

Date: 2026-09-12

Status: Complete for the environment and registered paths below; 9c, 9d and
release readiness remain pending.

## Contract and candidate

The public-cutover plan owns this acceptance unit. Start from `b42b556d`,
which commits the Slice 9a record over `df633187`. Before implementation, all
1,186 files in the 9a final manifest and all 80 indexed attachments matched;
867 historical evidence files were inventoried for preservation.

Current registered analysis source methods target DuckDB 1.5.3 with Ibis 12.0.0.
Basic relational adapter acceptance is separate from Dataset method support.
No new backend method, public API, persistence generation or compatibility path
is introduced. Existing private and public Runtime fixtures remain the owners
of numerical, authority and continuation assertions.

## Environment and ownership

Use this macOS arm64 host and Python 3.12.13. DuckDB and SQLite use isolated files;
PostgreSQL, MySQL and versioned MinIO use the task-owned `marivo-slice9b` Colima
profile, with ports bound only to loopback. Preserve the pre-existing stopped
Colima profile and restore Docker's original context. Remove task-owned services
and VM resources after acceptance. Record actual image digests and driver versions.

ClickHouse `cdn_ch` and Trino `trino_iceberg` use datasource declarations from the
owner-designated `cdn-semantic-layer` checkout and current secret references.
Only metadata and bounded, partition-filtered SELECT expressions are allowed.
The external project, its state and its data are not modified. Evidence excludes
resolved credentials and raw identity rows.

## Implementation and gates

An opt-in pytest observer captures existing terminal actions, registrations,
Run/Artifact deltas, statement and transfer statistics, output receipts and local
worker RSS without reading result rows or changing dispatch. Existing assertions
own numerical and row/authority validation. Fresh-process workers retain their
own reports. Reuse the private-Parquet fixture for live object publication,
required parts, native continuation and fixed-version readback.

The six-adapter report distinguishes real relational reads from supported
Dataset execution and exact typed rejection before admission. Cover all twelve
parent-plan economics outcomes, including the compound fused source chain,
zero-I/O construction/recovery, source-offline continuation and rollup,
high-cardinality rank/limit, exact resource guards, captured bindings,
source-prefix/local-suffix placement, direct DataFrame handoffs and no retry.

Run focused default and Runtime selections with two Runtime workers, the separate
live object gate, followed by `make check-agent` and `git diff --check`. Repair
only reproducible defects and rerun affected gates on the final candidate.
Package changes require renewed installed-package acceptance. No full
`release-check`, commit, push or release belongs to this slice.

## Evidence and exit

Evidence is retained under `evidence/slice-9b/2026-09-12-backend-economics/`.
Command receipts bind source manifests, timestamps, exit status and log hashes.
Keep failed attempts and distinguish deselection, unsupported contracts and
unavailable measurements from passing acceptance. Close only when the complete
required matrix is green on one candidate; record any post-gate status-only
changes separately. Closing 9b does not close 9c, 9d or the parent release gate.

## Reproduced repairs

The old Runtime assertions still expected durable engine relations or mandatory
pandas continuation for local files. Align them with the accepted native Parquet
contract: test pandas by preselecting its legal registry route, retain source-private
rejections for unregistered readers/versions, and count bounded publication streams
separately from original-source queries. Corruption tests target receipt-owned
`data.parquet` files; complete input, process loss and atomic publication assertions
remain enforced. Add missing Attribution rank/limit Runtime coverage.

Three implementation defects were reproduced against real execution: final Funnel Delta
ordering overwrote authored pattern order with lexical step order, and independent
temporal membership parts lacked the explicit physical casts already applied to
primary rows. Apply authored ordering at the existing compiler ordering points
and physical casts at both private relation publication exits. Existing source/local
numerical references, temporal distinct checkpoints, filtering and cold reads
serve as regression owners. The complete matrix additionally exposed pandas Funnel Delta filtering being dispatched to Metric Delta retained-part validation; keep the explicit Event semantics on its own row-validation route. No public contract or method support changes.

## Final acceptance

All final gates passed against candidate
`82a4c7faf6b30d061b46ba0bc60ca0d45eed6e99c6a75c436ab9d092768bf0da`
at unchanged HEAD `b42b556db25c8e309aeb720849bde5d60b992190`. The candidate manifest
includes tracked changes and new test/helper files. Only this completion record
and the parent plan's status text follow those gates; `handoff.json` independently
checks that exact difference and unchanged package/test bytes.

Evidence paths below are relative to
[`evidence/slice-9b/2026-09-12-backend-economics/`](../../../evidence/slice-9b/2026-09-12-backend-economics/).

| Gate | Fresh result | Receipt |
| --- | --- | --- |
| `make check-agent` | Lint, import contracts, typing, 4,824 default tests and API docs passed | `check-final-v2/gate.json` |
| Focused default economics/numeric owners | 104 passed | `default-economics-final-v2/gate.json` |
| `make runtime-test`, 134 selected modules, two workers | 778 passed; no failures or skips | `runtime-final-v2/gate.json` |
| `make object-storage-test`, live versioned MinIO | 7 passed | `object-final/gate.json` |
| `make release-test`, including build/archive checks | 29 passed; nested wheel checks: 361 contracts, five Runtime examples and three independent production/continuation/recovery processes | `installed-final/gate.json` and its `hooks/` |
| Six real basic adapters | Six passed; five unregistered Dataset roots rejected before admission | `relational-candidate-final-v2/`, `live-read-candidate-final-v2/` and Runtime nodes |
| Preserved project readback | Two Artifacts readable, revenue rows 100/47 and rollup 147; two Runs and two Artifacts unchanged | `representative-readback-v3/readback.json` |
| Final whitespace and historical preservation | `git diff --check` passed; all 867 historical evidence hashes unchanged | `handoff.json` |

Counts overlap and must not be added into one test total. The final
[`acceptance-index.json`](../../../evidence/slice-9b/2026-09-12-backend-economics/acceptance-index.json)
maps all twelve parent outcomes and the approved plan's numbering to passing
test nodes, 53 observed operators, 121 registration/shape variants and 1,343
terminal-action observations. Those include 1,051 successful or recovered
actions, 104 exact recoveries and 27 errors with no additional Run. Registry
variants include deliberately absent lowerers; the report is an observation
index, not a product capability registry. Existing numeric and Runtime test
assertions own values, order, status, authority and readback. Individual action
records bind Session, Run, Artifact, Evidence, receipt, resource metrics and
selected execution stages; cold-process reports remain in each gate's `hooks/`.

| Backend | Real basic path | Dataset source status |
| --- | --- | --- |
| DuckDB 1.5.3 / Ibis 12.0.0 | Isolated local file | Current registered source methods and Parquet scans exercised |
| SQLite 3.53.1 | Isolated local file | Unregistered; typed root rejection |
| PostgreSQL 17.11 | Task container, loopback 15439 | Unregistered; typed root rejection |
| MySQL 8.4 | Task container, loopback 13369 | Unregistered; typed root rejection |
| ClickHouse `cdn_ch` | Metadata and one-day bounded aggregate | Unregistered; typed root rejection |
| Trino `trino_iceberg` | Metadata and one-partition aggregate | Unregistered; typed root rejection |

The index includes each adapter's method eligibility, registered pandas path,
input roles, source-private boundary and actual evidence. A basic read never
establishes Dataset support. `environment.json` records image identifiers,
versions and ownership. Runtime statistics separate primary statements,
validation statements, origin-source bindings, Parquet bindings, decoded
transfers and stored output/parts. Adapter-internal metadata wire calls,
storage network request counts and per-action native-engine RSS are unavailable
and explicitly identified, never replaced with zero. Local worker peak RSS is
recorded where the worker supplies it.

The live S3 gate owns connector operations, materialization of required distinct,
linear and t-digest private bundles, version-pinned readback/native continuation,
and three-process Pearson/Spearman/Kendall publication and recovery. Ordinary
method coverage uses local Parquet; other object combinations remain owned by
SDK-boundary tests and are not advertised as additional live-S3 coverage.

The tested wheel and sdist are preserved in `tested-distributions/`; their bytes
match installed acceptance's `archives.json`:

- Wheel SHA-256: `12fc8c01e789684147c5870ba983a4efb54408f99d8ddea48ecd55d611edd089`.
- Sdist SHA-256: `e4d782c02583f2f2eaec1daddcb5839c58a24dec0099efc087f3d58d6c40fa3d`.

## Failed attempts, cleanup and handoff

All failed attempts remain in the attachment inventory. Earlier full Runtime
runs exposed stale pre-cutover assertions and the three implementation defects
above; the first final attempt had 776 passing and two failing cases. After the
last repair, 45 affected Runtime cases and then every final gate passed on the
same candidate. Setup failures include unavailable Docker credential helpers,
image retrieval and an incompatible initial MySQL client build. An initial
ClickHouse SQL metadata-inference path attempted automatic view creation and
was rejected by the read-only account; no write succeeded. The accepted probe
uses schema-qualified Ibis table expressions and bounded SELECTs. No external
project or business data was changed. Two extra project-preservation attempts
failed on script import/path selection before the final successful readback;
their logs and exact script inputs are retained too.

`cleanup-services/cleanup.json` proves removal of the three task containers,
the `marivo-slice9b` VM and its data, absence of listeners on 15439/13369/19009,
and the original stopped `default` profile/context. Optional local MySQL and
PostgreSQL development clients and the MariaDB connector remain installed for
reproduction; repository dependency declarations are unchanged.

`attachments.json` hashes all evidence files except itself, including failed
attempts, command/exit receipts, candidate manifests, observer copies, installed
reports, distribution bytes and the preserved readable project. `handoff.json`
records the final status-only delta and historical hash verification. This is
macOS arm64/Python 3.12 acceptance, with no cross-platform claim. Slice 9b alone
is complete; no full `release-check`, commit, push or publication was performed.
