# Isolated local datasource test environment

This opt-in environment supplies ready services for native source reads, driver
control, and analysis recovery tests. Pytest and Make test gates never start it.
Services are reused across cases and repeated test invocations. Fresh client
processes, connections and UUID-named tables do not recreate containers.
Lifecycle commands target the `marivo-multisource` Compose project on a Podman
machine. They use the forwarded API socket of `podman-machine-default` or the
machine selected by `MARIVO_PODMAN_MACHINE`.

## Select a ready service

Test gates and selection rules are documented in [CONTRIBUTING.md](../../../CONTRIBUTING.md#测试).

Set the opt-in flag to `1` after its service is ready. Pytest skips cases whose
opt-in flags are unset. `MARIVO_TRINO_NON_ICEBERG_TEST=1` enables additional
tests for the memory catalog in the same Trino service.

| Profile | Host endpoint | Opt-in flag | Runtime scope |
| --- | --- | --- | --- |
| `postgres-analysis` | `127.0.0.1:15432` | `MARIVO_POSTGRES_ANALYSIS_TEST` | PostgreSQL 17 with separate `analysis` and Trino `iceberg` databases |
| `trino` | `127.0.0.1:18080` | `MARIVO_TRINO_ANALYSIS_TEST` | Trino 483 with Iceberg, Hive and memory catalogs |
| `mysql-analysis` | `127.0.0.1:23306` | `MARIVO_MYSQL_ANALYSIS_TEST` | MySQL 8.4 InnoDB with named and fixed-offset time zone tests |
| `mariadb-analysis` | `127.0.0.1:23307` | `MARIVO_MARIADB_ANALYSIS_TEST` | MariaDB 11.4 with native timeout and MySQL-family query tests |
| `clickhouse` | `127.0.0.1:18123` | `MARIVO_CLICKHOUSE_ANALYSIS_TEST` | ClickHouse 26.3 LTS with MergeTree, Distributed and civil time tests |
| `minio` | `127.0.0.1:19000` | `MARIVO_MINIO_TEST` | Private S3 fixture for Parquet reads |

Run the relevant Runtime files with the ready-service flags and bounded
concurrency when other invocations share the host.

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/datasource/test_source_profiles.py' RUNTIME_WORKERS=1
```

Tests use UUID-named fixtures, keep administration separate from the restricted
reader, and remove their own objects in `finally`. Passwords enter datasource
declarations through environment references.

## Shared backend fixtures

Compose pins the six images by digest. Trino's startup creates a dedicated login
and database for the Iceberg JDBC V1 catalog on `postgres-analysis`. The JDBC
schema version is independent of Iceberg table format v2. Catalog data and the
`trino-warehouse` volume belong together. Hive uses the file metastore at
`local:///hive`, separate from the Iceberg data at `local:///data` in the same
warehouse volume. The Trino profile sets `query.max-stage-count=500` for nested
relationship and allocation cases.

ClickHouse uses `Asia/Shanghai` as the server time zone. Date32, civil calendar,
and UTC transport checks use the same server as the MergeTree queries. The
`marivo_single_shard` topology lets Distributed tables and Distributed-to-View
fixtures exercise native `GLOBAL IN` and `GLOBAL JOIN` over the loopback endpoint.

## Initial setup on ARM64 macOS

Run from the repository root. The Docker CLI and Compose plugin send requests
to the selected Podman machine. Reuse a running machine with sufficient memory
and disk space. For a new machine, start with 4 CPUs, 8 GiB RAM, and a 40 GiB disk
for simultaneous services. Trino has a 2 GiB heap in a 4 GiB container, and
single-node ClickHouse has a 3 GiB limit. The manager requires at least 8 GiB free
on the host before startup. Leave additional space for the first image pulls.

```bash
brew install podman docker docker-compose
podman machine init --rootful --cpus 4 --memory 8192 --disk-size 40 podman-machine-default
podman machine start --no-info podman-machine-default
```

To resize an existing machine, stop it after its workloads finish, run
`podman machine set --memory 8192 <machine>`, and start it again.

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

The manager waits for container health and initializes the PostgreSQL, MySQL and
MariaDB readers during explicit startup. Select individual profiles or start all
six with `start all`. Starting `trino` also starts the shared PostgreSQL service
and initializes its Iceberg catalog database. Trino and ClickHouse reader checks
use explicit module commands.

```bash
bash tests/datasource/environment/manage.sh start all
.venv/bin/python -m tests.datasource.environment.trino_analysis
.venv/bin/python -m tests.datasource.environment.clickhouse_analysis
```

Trino startup initializes warehouse ownership with a short root process from
its pinned image. Smoke commands check service access and basic native queries,
then remove their fixtures:

```bash
.venv/bin/python -m tests.datasource.environment.smoke trino
.venv/bin/python -m tests.datasource.environment.smoke clickhouse
```

Use `status all` to inspect the project. `logs` and `stop` accept a profile.

```bash
bash tests/datasource/environment/manage.sh status all
bash tests/datasource/environment/manage.sh logs trino
bash tests/datasource/environment/manage.sh stop trino
```

`stop trino` stops Trino while PostgreSQL continues to serve the `analysis`
database. Keep PostgreSQL running for Trino's Iceberg catalog. Use `stop all` to
stop the whole Compose project. Reuse running services while other test
invocations need them. Stopping preserves named volumes.

## Reader permissions

PostgreSQL setup removes PUBLIC CREATE/TEMP, grants public-schema USAGE and
SELECT, and defaults reader transactions to read-only and UTC. MySQL and MariaDB
grant the reader only SELECT on `analysis.*`.

Trino file rules restrict `analysis_reader` to Iceberg/Hive/system metadata and the
`noniceberg` memory catalog. The loopback service does not authenticate identities.
Memory tables disappear on restart; tests using that catalog call
`trino_analysis.setup_non_iceberg()` before their reads. Query history retains at most 100 entries
with a one-minute minimum age.

ClickHouse grants SELECT with `readonly=1` and `join_use_nulls=1`. Setup verifies
that reader writes are denied. It permits query-local `max_execution_time`,
`timeout_before_checking_execution_speed`, cancellation response settings, and
`enable_materialized_cte` through `CHANGEABLE_IN_READONLY`.

## Installed-package tests

Installed tests require the current wheel, supported driver dependencies, and
already-ready services. Select backends explicitly.

On macOS, Homebrew's keg-only `libpq` may not be discoverable by the isolated
PostgreSQL installation even when it is installed. If the adapter reports that
`libpq` cannot be found, pass its library directory as a Make variable so the
isolated processes receive it:

```bash
make release-test DYLD_LIBRARY_PATH="$(brew --prefix libpq)/lib"
```

The same Make variable can be passed to `make release-check`. An environment
assignment before `make` may be stripped by macOS before the test processes start.

```bash
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=postgres,mysql,clickhouse make installed-multisource-test
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=trino make installed-multisource-test
```

Use `MARIVO_TEST_WHEEL_DIR` for an isolated candidate directory. Process receipts
and logs live under the installed-wheel pytest temporary directory.

## Private S3 through MinIO

The `minio` profile exposes only `127.0.0.1:19000` and retains a separate data
volume. It reuses the private qualification password. Runtime tests create and
delete UUID-named private buckets, upload Parquet with PyArrow, and never start
or stop services.

Official MinIO Docker Hub and Quay repositories no longer serve this release.
The profile pins the ARM64 manifest of the preserved upstream
`RELEASE.2025-04-22T22-12-26Z` image from
[jcometki/minio](https://hub.docker.com/r/jcometki/minio), not a moving tag.
This is an isolated test fixture, independent of production backend selection.

```bash
bash tests/datasource/environment/manage.sh start minio
MARIVO_MINIO_TEST=1 make runtime-test RUNTIME_WORKERS=1 TESTS='tests/datasource/test_s3_runtime.py'
```

The tests cover private Parquet files and globs with path-style addressing,
plain HTTP on the loopback endpoint, anonymous denial outside the credential
scope, and host resolver injection. STS/session tokens remain local coverage.
