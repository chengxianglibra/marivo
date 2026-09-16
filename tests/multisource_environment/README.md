# Isolated local multi-datasource qualification environment

This opt-in environment prepares Slice 0 probes. It does not enable a Marivo
backend and is never started by pytest, `make test`, or `make runtime-test`.
All lifecycle commands target the dedicated `marivo-multisource` Colima socket.
Existing `marivo-slice9d` containers and volumes are outside its ownership.

## Topology and frozen scope

| Service | Image selection | Qualified environment scope |
| --- | --- | --- |
| Trino | 483, pinned linux/arm64 digest in Compose | One coordinator/worker, Iceberg connector, JDBC schema V1, local warehouse, table format v2 |
| PostgreSQL | 17 Alpine, pinned linux/arm64 digest | Iceberg catalog metadata only; not a Marivo PostgreSQL execution test |
| PostgreSQL analysis | Same 17 Alpine digest, separate service and volume | Group A analysis acceptance using a restricted read-only role |
| MySQL analysis | MySQL 8.4, pinned digest, separate service and volume | InnoDB Group A using a SELECT-only role |
| ClickHouse | 26.3 LTS, pinned linux/arm64 digest | One local MergeTree node |

All images are digest-pinned, including moving branch tags resolved during setup.
The matching Trino 483 POM selects Iceberg 1.11.0. `jdbc-init.sql` is the two
catalog tables from that version's `JdbcUtil` V1 definition; it is not an
Iceberg table schema. Compose retains PostgreSQL and warehouse volumes together.
Reinitialize both if the JDBC catalog format is deliberately changed.

The tested Trino 483 runtime requires `local.location=/warehouse` (a filesystem
path), while the JDBC warehouse is `local:///data` (a URI resolved beneath that
root). The documentation's URI example for `local.location` fails startup in
this image; the versioned `LocalFileSystemConfig` accepts a `Path` with
`@FileExists`. Data therefore resides under `/warehouse/data` in the named volume.
See the [483 configuration owner](https://github.com/trinodb/trino/blob/483/lib/trino-filesystem/src/main/java/io/trino/filesystem/local/LocalFileSystemConfig.java).

This topology does not qualify S3, REST/Hive catalogs, multiple Trino workers,
ClickHouse Distributed/Replicated engines, cross-table atomicity or production
streaming/recovery. The tests' Artifact destination remains local Parquet.

References: [Trino JDBC catalogs](https://trino.io/docs/483/object-storage/metastores.html#jdbc-catalog),
[local filesystem](https://trino.io/docs/483/object-storage/file-system-local.html),
[Iceberg 1.11.0 JDBC schema](https://github.com/apache/iceberg/blob/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/jdbc/JdbcUtil.java),
[ClickHouse shared snapshots](https://clickhouse.com/blog/clickhouse-release-25-06).

## Initial setup on this ARM64 Mac

Run from the repository root. The measured machine has 16 GiB RAM. Use 4 CPUs /
6 GiB for the new VM; Trino has a 2 GiB heap inside a 4 GiB container, PostgreSQL
512 MiB, and ClickHouse 3 GiB. Run the two groups serially.

Disk capacity is sparse, not preallocated. Start from roughly 25 GiB host free
when available; always retain at least 8 GiB and monitor after pulls. Initial
setup on this machine began with 17 GiB and reused Colima's existing base-image
cache. Do not delete other projects or use global `docker system prune`.

```bash
HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install docker-compose
colima start marivo-multisource --activate=false --arch aarch64 --vm-type vz \
  --runtime docker --cpu 4 --memory 6 --disk 12 --root-disk 8
```

Create private test credentials and a task-local Docker CLI configuration:

```bash
.venv/bin/python - <<'PY'
import json
import secrets
from pathlib import Path

root = Path.home() / '.cache/marivo-multisource'
root.mkdir(mode=0o700, parents=True, exist_ok=True)
password_file = root / 'secrets.env'
if not password_file.exists():
    with password_file.open('x') as stream:
        password_file.chmod(0o600)
        stream.write('QUALIFICATION_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
docker_config = root / 'docker'
docker_config.mkdir(mode=0o700, exist_ok=True)
(docker_config / 'config.json').write_text(json.dumps({
    'auths': {},
    'cliPluginsExtraDirs': ['/opt/homebrew/lib/docker/cli-plugins'],
}))
PY
```

The separate Docker config avoids this host's unavailable
`docker-credential-osxkeychain` helper for public image pulls, without editing
the user's Docker config or credentials. It contains no registry credentials.
The database password is in the private file only. Do not commit it or print
resolved Compose configuration / container environment variables.

## Start, verify, stop

```bash
bash tests/multisource_environment/manage.sh start clickhouse
.venv/bin/python tests/multisource_environment/smoke.py clickhouse
bash tests/multisource_environment/manage.sh start trino
.venv/bin/python tests/multisource_environment/smoke.py trino
```

For Trino and ClickHouse, `start` first stops the opposite group in this Compose project. Trino startup
also initializes warehouse ownership with a short root process from its own
pinned image. It does not change other filesystem paths. A container being
started is not readiness: the helper waits up to 180 seconds for container
health, then smoke verifies actual catalog and data operations. Inspect the
bounded logs when startup fails.

| Service | Host endpoint | Client identity |
| --- | --- | --- |
| Trino | `http://127.0.0.1:18080` | user `qualifier`, catalog `iceberg`, session timezone UTC |
| ClickHouse | `http://127.0.0.1:18123` | user `qualifier`, database `qualification`, password from private file |
| PostgreSQL | Compose network only, `postgres:5432` | database/user `iceberg`; same private test password |
| PostgreSQL analysis | `127.0.0.1:15432` | database `analysis`, reader `analysis_reader`, fixture administrator `analysis_admin` |

Smoke creates UUID-named fixtures and drops only those fixtures in `finally`.
ClickHouse checks exact Decimal aggregation and effective session settings.
Trino creates a v2 Iceberg table, commits two snapshots, verifies old/current
results and rejects an invalid snapshot ID. Receipts label themselves
`dataset_acceptance=false`. No production datasource declarations are created.

Capture the current ID from the exact `main` row of `orders$refs`, not the first
row of `orders$snapshots` (which can be the empty creation snapshot). Trino 483
also rejects small INTEGER version expressions: use an explicit BIGINT cast for
the invalid-snapshot check. These details were established by live smoke runs.

```bash
bash tests/multisource_environment/manage.sh status trino
bash tests/multisource_environment/manage.sh logs trino
bash tests/multisource_environment/manage.sh stop trino
bash tests/multisource_environment/manage.sh stop clickhouse
```

Stopping preserves volumes. After both groups are stopped, the dedicated VM may
be stopped with `colima stop marivo-multisource`. Restart it with
`colima start marivo-multisource --activate=false` before using the helper again.
Deleting the environment's volumes is an explicit reset, not normal cleanup;
it discards both Iceberg catalog and warehouse state. Keep the private password
while retaining initialized PostgreSQL/ClickHouse volumes.

## PostgreSQL Group A analysis environment

This profile owns only `postgres-analysis` and its separate named volume. Starting
or stopping it does not stop ClickHouse or modify the Trino metadata database.
Its 512 MiB container can run alongside ClickHouse within the dedicated VM.

```bash
bash tests/multisource_environment/manage.sh start postgres-analysis
bash tests/multisource_environment/manage.sh status postgres-analysis
```

Start waits for health, then runs `postgres_analysis.py`. The setup provisions the
reader, removes PUBLIC CREATE/TEMP privileges, grants public-schema USAGE and
SELECT, and defaults reader transactions to read-only and UTC. The reader cannot
write, create ordinary tables, or create temporary tables even after switching
its transaction default to read-write. The setup verifies these permissions and
a disposable exact-decimal aggregate, then removes its fixture. This setup smoke
does not constitute Dataset acceptance.

Runtime fixtures may import the following helpers. Administrative writes stay
in fixture preparation; Marivo datasource declarations must use `READER` and
an environment reference for the password.

```python
import os
from tests.multisource_environment import postgres_analysis as pg

os.environ["MARIVO_POSTGRES_ANALYSIS_PASSWORD"] = pg.password()
with pg.connection(admin=True) as admin:
    # Use UUID-named tables and remove them in a finally block.
    # Tables created by this administrator in public grant reader SELECT.
    pass
with pg.connection() as reader:
    assert reader.execute("SELECT current_user").fetchone() == (pg.READER,)
```

The helper constants are `HOST`, `PORT`, `DATABASE`, `ADMIN`, and `READER`.
Connections are psycopg connections with autocommit and UTC. Tests that exercise
server cursors explicitly own their transactions. No service is started by these
connection helpers or the default test gates. Stop only this service with:

```bash
bash tests/multisource_environment/manage.sh stop postgres-analysis
```

Stopping preserves its data volume and private credentials. Do not stop the VM
while another qualification service is in use.

## Qualifications still required

- Trino: actually submit the Ibis snapshot visitor's SQL, preserve every repeated
  scan and assertion snapshot, exercise expiration and multi-page transfer.
- ClickHouse: observe overlapping writer/query lifetimes, verify per-table
  shared reads and required assertion barriers, including empty output.
- Both: bounded decode/slow fetch, cancellation receipts, lost acknowledgement,
  process recovery and actual Dataset publication/cold reads.

`enable_shared_storage_snapshot_in_query=1` is verified by ClickHouse smoke.
It does not prove single evaluation or a cross-table snapshot. Any debug delay
setting used in later race tests must be checked on the pinned server, and a
sleep without observed overlap is inconclusive.

## Complete Slice 0 live qualification

The smoke command verifies setup only. From the repository root, run the actual
compiler/decoder feasibility gate with disposable UUID databases:

```bash
bash tests/multisource_environment/manage.sh start trino
.venv/bin/python -m tests.multisource_environment.qualify trino --output /tmp/marivo-q0-trino.json
bash tests/multisource_environment/manage.sh start clickhouse
.venv/bin/python -m tests.multisource_environment.qualify clickhouse --output /tmp/marivo-q0-clickhouse.json
```

The Trino gate submits the version-qualified compiler SQL unchanged before and
after source mutation, then actually expires the once-readable snapshot and
requires snapshot-specific failures for the primary and both source assertions.

The ClickHouse gate submits the typed envelope, validates all required records
and retained projections, and uses the server's test-only snapshot delay to create
observable query/write overlap. It requires a disabled-sharing negative control,
three consistent enabled-sharing runs and one enabled run with duplicate writes
whose source assertion count agrees with the same snapshot and rejects publication.
The debug delay is not part of the candidate production configuration. Failure to
observe overlap or negative-control divergence fails the gate; do not relabel it
as proof. The scoped HTTP reader disables HTTP/Arrow compression and caps wire
bytes before Arrow decoding, but is not a production adapter or general Arrow
memory-safety guarantee.

Live ClickHouse testing exposed unsigned COUNT results becoming an unsupported
Variant after UNION; the private SQLGlot branch projections now explicitly pin
wire types. The original offline Ibis algebra alone did not establish this fact.
The gate also verifies actual JOIN NULL, finite-value predicates, exact Decimal
and UTC timestamp conversion. It never registers a Marivo backend, executes a
Dataset Run or grants a pre-transfer assertion-order amendment.

See the [completed qualification and remaining activation gates](../../docs/superpowers/specs/2026-09-15-multisource-slice-0-qualification.md).

## MySQL and SQLite Group A analysis

MySQL uses the dedicated `mysql-analysis` service and volume, with loopback port
**23306**. Port 13306 belongs to the separate Slice 9d VM and must not be reused.
Starting this profile leaves the other profiles and VMs running. The image is
pinned to the tested MySQL 8.4 digest; this is reproduction information, not a
runtime version eligibility gate. Its external container memory limit is test
environment configuration, not a Marivo execution budget.

```bash
bash tests/multisource_environment/manage.sh start mysql-analysis
bash tests/multisource_environment/manage.sh status mysql-analysis
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_runtime.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_scalar_recovery.py' RUNTIME_WORKERS=1
```

Setup provisions `analysis_reader` with only SELECT on `analysis.*`, checks
object-creation denial, and keeps the password in the existing private environment
file. Fixture administration uses root solely on this disposable service. Dataset
declarations reference `MARIVO_TEST_MYSQL_PASSWORD`; credentials are never saved
in receipts. Runtime tests also prove INSERT, DELETE and CREATE TEMPORARY denial.
InnoDB fixtures use `utf8mb4_0900_bin`; every UUID fixture is dropped in `finally`.
MySQLdb/mysqlclient is provided by Marivo's existing optional `mysql` dependency.
Tests never start the service. Without the explicit flag, skipped MySQL tests are
not live acceptance evidence.

SQLite tests use separate persistent files under each test's `tmp_path`; they
need no service. Production reads use query-only connections. The tests cover
storage-class/date validation, exact composite identities, native integer
overflow, execute/fetch interruption, atomic publication and fresh-process recovery.

To retain large-input query receipts, set `MARIVO_SLICE4_RECEIPTS` to an output
directory when running the two engine runtime suites. They record the independent
expected top two groups, actual driver SQL and parameter tuples, query counts and
transferred Arrow rows/bytes. DuckDB is an additional comparator; explicit arithmetic
remains the primary oracle. Parameter capture is test-only and does not change Store
or persist connection credentials.
Unavailable server scan metrics remain explicitly unavailable.

```bash
bash tests/multisource_environment/manage.sh stop mysql-analysis
```

Stopping preserves its volume. Do not stop the VM while another profile is in use.
MySQL unbuffered cursor close can drain unread results; connection close is not
proof of immediate server termination. No analysis timeout, retry, database DDL,
source upload or version admission is added to the production adapter.


### Slice 4 driver audit

The installed MySQLdb cursor `_query` calls `db.query` once; SSCursor `fetchmany`
uses `_fetch_row` on that result. Its connection `ping` documentation states that
modern MySQL defaults to reconnect disabled. Neither Ibis's execution hooks nor
these adapters call ping/reconnect or replay failed statements. Actual closed
connection tests fail; service-free execute/fetch fault tests assert one submission
and preserve the original exception even when close fails.

SQLite's native cursor advances the current statement incrementally; it has no
network reconnect layer. Native [busy-handler waits](https://www.sqlite.org/c3ref/busy_timeout.html)
remain possible while acquiring locks. These waits are not a Marivo retry policy.
SQLite also documents [automatic schema-change recompilation and retry](https://www.sqlite.org/c3ref/prepare.html)
inside prepared-statement stepping. The adapter does not disable those native
mechanisms or claim zero internal retries. No application-level execute/fetch
failure restarts a Dataset action. The existing
execute/fetch interrupt tests exercise SQLite's own interrupted-statement errors.

For both drivers, complete individual cells are decoded before Arrow batch creation.
`fetchmany` bounds row count, not bytes; a large cell can exceed an expected batch
memory budget. SQLite may also materialize sorts/aggregates within its engine.
Small metadata/assertion `submit` results are collected in full. This is not a hard
memory or cancellation bound. The slow-execution cases use real MySQL SLEEP and a
SQLite progress-handler delay; blocked-fetch cases use a controlled gate around
real cursor fetches. They test ownership across a pause, not a real network stall.
