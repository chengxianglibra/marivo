# Isolated local datasource test environment

This opt-in environment supplies ready services for native source reads, driver
control, and analysis recovery tests. Pytest and Make test gates never start it.
All lifecycle commands target the dedicated `marivo-multisource` Colima socket;
other Colima profiles, containers, and volumes are outside its ownership.

## Test goals and routes

| Goal | Tests | Gate |
| --- | --- | --- |
| Source profiles, physical types, identity and decoding | `tests/datasource/test_source_profiles.py`, `test_source_adapters_runtime.py` | `make runtime-test` |
| Read-only settings, timezone and native control | `tests/datasource/test_control_boundaries.py` | `make test` for local contracts; `make runtime-test` for services |
| Acquisition expiry and atomic snapshot publication | `tests/datasource/test_acquisition_timeout.py`, `test_mysql_authoring_deadline.py` | `make runtime-test` |
| Native cancellation, cleanup and independent server observation | `tests/datasource/test_source_deadline.py`, `test_mysql_cancellation.py`, `test_datasource_clickhouse_cancellation.py` | `make runtime-test` |
| Governed, provider, terminal and Store SQL authority | `tests/datasource/test_sql_audit.py`, `test_driver_audit.py`, `test_sql_runtime.py` | `make test` for static/local witnesses; `make runtime-test` for public execution |
| Installed package source execution and cold recovery | `tests/packaging/test_installed_sources.py` | `make installed-multisource-test` |

Enable only already-ready backend profiles with `MARIVO_POSTGRES_ANALYSIS_TEST`,
`MARIVO_MYSQL_ANALYSIS_TEST`, `MARIVO_TRINO_ANALYSIS_TEST`, or
`MARIVO_CLICKHOUSE_ANALYSIS_TEST`, set to `1`. Distributed ClickHouse and the
Trino memory catalog use `MARIVO_CLICKHOUSE_CLUSTER_TEST` and
`MARIVO_TRINO_NON_ICEBERG_TEST`. Unavailable services skip their opt-in cases;
those skips provide no real-backend evidence. SQLite uses files under `tmp_path`
and needs no service.

Use bounded concurrency while other agents are testing. For example:

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/datasource/test_source_profiles.py tests/datasource/test_source_deadline.py' RUNTIME_WORKERS=1
```

Tests use UUID-named fixtures, keep administration separate from the restricted
reader, and remove their own objects in `finally`. Passwords enter datasource
declarations through environment references. Source profiling rejects SQLite's
float-backed NUMERIC carrier when an exact Decimal cannot be preserved. Source
profile, cancellation, and analysis method tests each retain their own scope;
passing one does not qualify the others.

## Topology

| Profile | Host endpoint | Runtime scope |
| --- | --- | --- |
| `trino` | `127.0.0.1:18080` | Trino 483; one coordinator/worker; Iceberg JDBC V1; local warehouse and table format v2 |
| `postgres-analysis` | `127.0.0.1:15432` | PostgreSQL 17; database `analysis`; reader `analysis_reader`; administrator `analysis_admin` |
| `mysql-analysis` | `127.0.0.1:23306` | MySQL 8.4; InnoDB; database `analysis`; SELECT-only reader `analysis_reader` |
| `clickhouse` | `127.0.0.1:18123` | ClickHouse 26.3 LTS; local MergeTree; database `qualification` |
| `clickhouse-cluster` | `127.0.0.1:18201`, `127.0.0.1:18203` | Two shards with one replica each; Distributed over local tables |

Compose pins every image by digest. The `trino` profile also owns its internal
PostgreSQL catalog service and named catalog/warehouse volumes. Its warehouse
uses `local.location=/warehouse` and `local:///data`; reset both volumes together
only when deliberately changing the JDBC catalog format.

Starting any of `trino`, `clickhouse`, or `clickhouse-cluster` stops the other two
in this Compose project. Stopping a profile stops only the named group.
PostgreSQL analysis and MySQL analysis have separate services and volumes.
The cluster has no Keeper, replicated engines, or `ON CLUSTER` Distributed DDL;
setup creates fixtures on each node directly. This topology supplies no S3,
REST/Hive catalog, multi-worker Trino, or cross-table atomicity evidence.

## Initial setup on ARM64 macOS

Run from the repository root. The dedicated VM uses 4 CPUs and 6 GiB RAM.
Trino has a 2 GiB heap in a 4 GiB container; its metadata PostgreSQL uses 512 MiB,
and single-node ClickHouse uses 3 GiB. Run the mutually exclusive groups serially.
The manager requires at least 8 GiB free before starting a profile.

```bash
HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install docker-compose
colima start marivo-multisource --activate=false --arch aarch64 --vm-type vz \
  --runtime docker --cpu 4 --memory 6 --disk 12 --root-disk 8
```

Create private test credentials and a separate Docker CLI configuration:

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

This configuration avoids an unavailable global credential helper during public
image pulls without changing the user's Docker configuration. Keep the private
password while retaining initialized service volumes. Never commit or print
resolved Compose configuration, passwords, or container environment variables.

## Start, verify and stop

The manager waits for container health and performs PostgreSQL/MySQL reader
setup. Trino and ClickHouse reader setup are explicit module commands:

```bash
bash tests/datasource/environment/manage.sh start postgres-analysis
bash tests/datasource/environment/manage.sh start mysql-analysis
bash tests/datasource/environment/manage.sh start trino
.venv/bin/python -m tests.datasource.environment.trino_analysis
bash tests/datasource/environment/manage.sh start clickhouse
.venv/bin/python -m tests.datasource.environment.clickhouse_analysis
bash tests/datasource/environment/manage.sh start clickhouse-cluster
.venv/bin/python -c 'from tests.datasource.environment import clickhouse_analysis as ch; print(ch.setup_cluster())'
```

Trino startup initializes warehouse ownership with a short root process from
its pinned image. The smoke command separately checks real catalog and data
operations, then removes its own fixtures:

```bash
.venv/bin/python -m tests.datasource.environment.smoke trino
.venv/bin/python -m tests.datasource.environment.smoke clickhouse
bash tests/datasource/environment/manage.sh status trino
bash tests/datasource/environment/manage.sh logs trino
bash tests/datasource/environment/manage.sh stop trino
bash tests/datasource/environment/manage.sh stop clickhouse
bash tests/datasource/environment/manage.sh stop clickhouse-cluster
bash tests/datasource/environment/manage.sh stop postgres-analysis
bash tests/datasource/environment/manage.sh stop mysql-analysis
```

Smoke verifies environment setup, exact Decimal aggregation, effective settings,
and Trino snapshot reads; it is separate from product execution acceptance.
Stop the dedicated VM with `colima stop marivo-multisource` only after all of its
profiles are no longer in use. Stopping preserves data volumes; volume deletion
is an explicit reset.

## Reader and observation contracts

PostgreSQL setup removes PUBLIC CREATE/TEMP, grants public-schema USAGE and
SELECT, and defaults reader transactions to read-only and UTC. MySQL grants only
SELECT on `analysis.*`; InnoDB fixtures use `utf8mb4_0900_bin`. SQLite's production
connection is query-only. Native driver buffering, SQLite busy-handler waits,
and SQLite internal statement recompilation remain driver behavior, rather than
Marivo replay or retry policies. `fetchmany` limits rows, not cell bytes.

Trino file rules restrict `analysis_reader` to Iceberg/system metadata and the
`noniceberg` memory catalog. The loopback service does not authenticate identities;
these tests verify authorization only. Memory tables disappear on restart, so
`trino_analysis.setup_non_iceberg()` runs before those reader journeys. Completed
query history retains at most 100 entries with a one-minute minimum age; native
cancellation observers read the original query state immediately.

ClickHouse grants SELECT with `readonly=1`, `join_use_nulls=1`, and shared-storage
snapshot support. Fixture setup permits query-local `max_execution_time`,
`timeout_before_checking_execution_speed`, cancellation response settings, and
`enable_materialized_cte` through `CHANGEABLE_IN_READONLY`. Event matching requires
materialized CTEs; lifecycle folds use their own ordered statement. Admin
connections observe query IDs and termination independently from product control
SQL. Shared storage snapshots do not establish cross-table atomicity or a single
CTE evaluation.

Installed tests require the current wheel, supported driver dependencies, and
already-ready services. Select backends explicitly and keep Trino separate from
ClickHouse:

```bash
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=sqlite,postgres,mysql,clickhouse make installed-multisource-test
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=trino make installed-multisource-test
```

Use `MARIVO_TEST_WHEEL_DIR` for an isolated candidate directory. Process receipts
and logs live under the installed-wheel pytest temporary directory.

On macOS, a cached `mysqlclient` wheel can be missing its client-library link.
Rebuild that dependency with the installed library's include/link flags instead of
reusing the cache. For an Apple Silicon Homebrew MariaDB connector:

```bash
MYSQLCLIENT_CFLAGS='-I/opt/homebrew/opt/mariadb-connector-c/include/mariadb' \
MYSQLCLIENT_LDFLAGS='-L/opt/homebrew/opt/mariadb-connector-c/lib -lmariadb' \
PIP_NO_BINARY=mysqlclient PIP_NO_CACHE_DIR=1 \
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=mysql \
make installed-multisource-test
```

Installed-origin and wheel-hash checks own package isolation. Native source
matrices own broader decoding, shape, streaming, fault, and cancellation coverage.
Historical qualification plans and local evidence retain their original scope;
current gate ownership is listed in [the Runtime coverage map](../../../docs/testing/runtime-coverage.md).
