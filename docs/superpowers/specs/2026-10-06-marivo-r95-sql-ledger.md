# R9.5 SQL ownership and physical retirement

Date: 2026-10-06

This ledger consumes the R9 implementation plan, section 5, and preserves the
55 original DS01–DS22 / AN01–AN33 obligations. Historical constructors and
callers remain in the [R0 ledger](2026-09-26-marivo-full-refactor-r0-sql-ledger.md).
The current disposition below does not rewrite their original execution evidence.
R9.6 cost and R9.7 final-candidate qualification have separate exits.

## Current owners

`G` means a source-bound Ibis expression compiled and issued by `SourceSession`;
`V` means the authorized closed provider channel; `T` means the public original-text
terminal; `D` means a driver API without product-authored SQL; `X` means physical
retirement. Registered Ibis expression operations are distinct from SQL AST or
compiled-text patches. Store persistence has a separate connection authority.

| ID | Disposition | Current owner and scope |
| --- | --- | --- |
| DS01 | G | `datasource.adapters._probe_backend`: compiled Ibis literal round trip. |
| DS02 | T | `datasource.manage.raw_sql`: required reason, returned-row bound, deadline, original text; `RawSqlResult` is terminal. |
| DS03 | V/G | `engines.duckdb` metadata statements; bound Ibis schema baseline. |
| DS04 | V/G | `engines.postgres` metadata statements; bound namespace/schema. |
| DS05 | V/G | `engines.mysql` metadata statements; bound Ibis schema. |
| DS06 | V/G | `engines.sqlite` metadata statements; main table/view schema. |
| DS07 | V/G | `engines.trino` metadata statements and Ibis schema; Iceberg/non-Iceberg remain distinct. |
| DS08 | G | `engines.trino.inspect_partition_values`: bound Ibis partition relation. |
| DS09 | V/G | `engines.clickhouse` metadata statements and bound schema; physical profiles remain distinct. |
| DS10 | G | `engines.clickhouse` bound Ibis partition/topology relations. |
| DS11 | D/V | PostgreSQL/Trino driver deadlines; MySQL approved certification SET/read and owned-query cancellation only. No PostgreSQL snapshot-control statements remain. |
| DS12 | D | `engines.sqlite.connect`: native authorizer for write denial. Old product PRAGMA control is removed. |
| DS13 | D/X | `datasource.timezone.probe_engine_timezone`: configured/driver facts; arbitrary SQL callback resolver removed. |
| DS14 | X | `EngineProfile.postprocess_sql` and ClickHouse compiled-text hook absent. |
| DS15 | V | `engines.duckdb` scoped parameterized HTTP secret installation; credentials excluded from submitted SQL audit. |
| DS16 | X | Old `_configure_http` / `SET force_download` path absent. |
| DS17 | G | `semantic.source_health` uses owned source collection for business checks. |
| DS18 | G | Shared compiled connectivity probe; no handwritten ping. |
| DS19 | X | Parity executor and public `ms.parity_check` absent. |
| DS20 | X | Provenance qualification/rewrite executor absent. Documentation text grants no submission authority. |
| DS21 | G | Inspection, snapshots, semantic preview and source-health use source-bound collection. Optional preview `.execute()` fallback removed. |
| DS22 | V | `datasource.capabilities.execute_provider_statement`: exact registered provider/template/parameters/purpose, independent native boundary audit. |
| AN01 | X/G | Old numeric macro installer absent; current typed numeric expressions and selected retained kernels. |
| AN02 | X/G | Old Lifecycle SQL compiler absent; graph preparation and domain replay. |
| AN03 | X/G | Old Trino/ClickHouse replay templates absent; registered graph preparation/kernel. |
| AN04 | X | Old private Event SQL visitors absent; no product SQL AST patch. |
| AN05 | X/G | Event SQL bundle absent; graph source reads and verified primary/parts publication. |
| AN06 | X/G | Trino Event compiled-SQL CTE patch absent; unchanged Ibis compilation. |
| AN07 | X/D | Old Event snapshot/control SQL adapters absent; current independent-read acquisition authority and native cancellation. |
| AN08 | X/G | Lifecycle integrity SQL/rewrite helpers absent; typed checks and retained validation. |
| AN09 | X/G | Old Lifecycle packet bundle absent; graph output/parts. |
| AN10 | X/G | Old Lifecycle summary/inspect SQL absent; graph checks and retained parts. |
| AN11 | X/G | Old attribution SQL module absent; current graph scope reads and allocation kernel. |
| AN12 | X/G | Old attribution source-summary chain absent; source-bound Ibis handles. |
| AN13 | X/G | Old `local_stage._source_count` absent; typed count or complete retained-input count. |
| AN14 | X/G | Old source-private validation SQL absent; graph parts/receipt validation. Retired private transfer rejects. |
| AN15 | X | Old DuckDB initialize/SET/macro adapter absent. |
| AN16 | X/G | Old DuckDB DESCRIBE constructor absent; Ibis schema binding. |
| AN17 | X/G | Old DuckDB CTAS/view/file wrappers absent; datasource-owned Ibis file readers. |
| AN18 | X | Old Artifact-to-DuckDB attach/view scan absent; retained pandas/Arrow path remains. |
| AN19 | X/D | Old MySQL schema/timezone statement adapter absent; datasource provider/driver owns facts. |
| AN20 | X/G | Old MySQL date SQL predicate templates absent; governed representation checks and exact decode. |
| AN21 | X/D | Old SQLite schema/timezone statement adapter absent; datasource provider/driver owns facts. |
| AN22 | X/G/D | Old SQLite storage SQL and PRAGMA submitter absent; exact decode and native authorizer. |
| AN23 | X/G | Old PostgreSQL compiled-text wrapper absent; original Ibis driver argument. |
| AN24 | X/D | Old PostgreSQL schema/timezone SQL adapter absent; datasource provider/driver owns facts. |
| AN25 | X/G | Old PostgreSQL finite-number templates absent; governed checks and exact decode. |
| AN26 | X/D | Old Trino schema/timezone SQL adapter absent; datasource provider/driver owns facts. |
| AN27 | X/G | Old Trino finite-number templates absent; governed checks and exact decode. |
| AN28 | X/D | Old ClickHouse schema/timezone SQL adapter absent; datasource provider/driver owns facts. |
| AN29 | X/G | Old ClickHouse time/finite-number templates absent; governed checks and exact decode. |
| AN30 | X/D | Old Trino/ClickHouse feature/control SQL adapters absent; configured native APIs and explicit missing-fact refusal. |
| AN31 | G | `materialization.temporal_sql.lower_temporal`: closed Ibis operation lowering and SQLite scalar registration retained; no SQL parse/render patch. |
| AN32 | X/G/V | Old `_ReservedJsonReader` absent; datasource Ibis reader and scoped provider credential owner. |
| AN33 | X/G | All legacy generic `statement/submit/batches` adapters absent; source-session issued expression/purpose/schema remains sole governed submitter. |

## Later approved control and Store authority

DS23 is appended without shrinking or rewriting the original 55-ID denominator:
`clickhouse.analysis.cancel_owned_query` is the separately approved 2026-10-06
query-ID/authenticated-reader-bound `KILL QUERY ... SYNC`. Its exact template,
bound parameter names and purpose stay pinned. MySQL cancellation belongs to
DS11 and keeps both authorized purposes separate from certification SET/read.
The [datasource contract](../../specs/semantic/datasource-layer.md) owns these
controls. The superseded PostgreSQL repeatable-read registrations are absent.

Store SQLite SQL belongs to `analysis.materialization.store.SessionStore`, its
schema, transaction and graph/evidence/finding publication methods. The audit
identifies each Store-created connection from its constructor call, separately
from datasource SQLite and test administration; neither a file suffix nor a
`.marivo` path grants SQL authority. Doctor/telemetry Store diagnostics are
read-only Store consumers and receive exact static owner entries.

## Validation and evidence

The whole `marivo/` AST inventory and closed submission-owner manifest are owned
by `scripts/r95_sql_audit.py`. It also inventories compiler operations, string
clues, imports and reachability; a text match alone is not a SQL submitter.
The manifest rejects unknown submitters and legacy statement/SQL patch paths.

`tests/r95_driver_audit.py` observes native SQL arguments, installed-Ibis
metadata/preparation, provider statements, terminal text and Store connection
identity. It checks source-issued text and provider purposes independently of
the producer's own log. Test fixture administration/oracles are classified
separately. This is a test audit, not a new product SQL classifier or a parser
for the user's terminal query.

New small public probe/inspection/members/terminal witnesses supplement the
original R9.2–R9.4 business, cancellation and HTTP-scope evidence. They do not
replay method matrices, grant new physical method keys, or relabel old execution
candidates. Raw original failures, scoped retries, commands, output and hashes
are retained in the [R9.5 evidence record](2026-10-06-marivo-r95-evidence/README.md).
No local-environment tampering cases are part of this work.
