# Isolated local datasource test environment

This opt-in environment supplies ready services for native source reads, driver
control, and analysis recovery tests. Pytest and Make test gates never start it.
Services are reused across cases and repeated test invocations. Fresh client
processes, connections and UUID-named tables do not recreate containers.
All lifecycle commands target the dedicated `marivo-multisource` Colima socket;
other Colima profiles, containers, and volumes are outside its ownership.

## Select a ready service

Test gates and selection rules are documented in [CONTRIBUTING.md](../../../CONTRIBUTING.md#测试).

Enable only already-ready backend profiles with `MARIVO_POSTGRES_ANALYSIS_TEST`,
`MARIVO_MYSQL_ANALYSIS_TEST`, `MARIVO_MARIADB_ANALYSIS_TEST`, `MARIVO_TRINO_ANALYSIS_TEST`, or
`MARIVO_CLICKHOUSE_ANALYSIS_TEST`, set to `1`. The isolated MySQL time zone
regression uses `MARIVO_MYSQL_NO_TIMEZONES_TEST=1`. Distributed ClickHouse and the
Trino memory catalog use `MARIVO_CLICKHOUSE_CLUSTER_TEST` and
`MARIVO_TRINO_NON_ICEBERG_TEST`. Unavailable services skip their opt-in cases;
those skips provide no real-backend evidence.

Run the relevant Runtime files with the ready-service flag, using bounded
concurrency when other invocations share the host. For example:

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/datasource/test_source_profiles.py' RUNTIME_WORKERS=1
```

Tests use UUID-named fixtures, keep administration separate from the restricted
reader, and remove their own objects in `finally`. Passwords enter datasource
declarations through environment references.

## Topology

| Profile | Host endpoint | Runtime scope |
| --- | --- | --- |
| `trino` | `127.0.0.1:18080` | Trino 483; one coordinator/worker; Iceberg JDBC V1 / table format v2; Hive file metastore; local warehouse |
| `postgres-analysis` | `127.0.0.1:15432` | PostgreSQL 17; database `analysis`; reader `analysis_reader`; administrator `analysis_admin` |
| `mysql-analysis` | `127.0.0.1:23306` | MySQL 8.4; InnoDB; database `analysis`; SELECT-only reader `analysis_reader` |
| `mysql-no-timezones` | `127.0.0.1:23308` | Same MySQL reader with empty named time zone tables in a separate volume |
| `mariadb-analysis` | `127.0.0.1:23307` | MariaDB 11.4; database `analysis`; SELECT-only reader `analysis_reader` |
| `clickhouse` | `127.0.0.1:18123` | ClickHouse 26.3 LTS; local MergeTree; database `qualification` |
| `clickhouse-cluster` | `127.0.0.1:18201`, `127.0.0.1:18203` | Two shards with one replica each; Distributed over local tables |
| `clickhouse-22-shanghai` | `127.0.0.1:18324` | Standalone ClickHouse 22.3.15.33; server default `Asia/Shanghai`; separate config and data volume |

Compose pins every image by digest. The `trino` profile also owns its internal
PostgreSQL catalog service and named catalog/warehouse volumes. Its warehouse
uses `local.location=/warehouse` and `local:///data`; reset both volumes together
only when deliberately changing the JDBC catalog format.

Starting any of `trino`, `clickhouse`, or `clickhouse-cluster` stops the other two
in this Compose project. PostgreSQL, MySQL and MariaDB analysis profiles have
separate services and volumes.
The single-node ClickHouse profile also declares `marivo_single_shard` over its
loopback native endpoint for Distributed-to-View qualification. Distributed tests use GLOBAL IN/JOIN over the single-node and two-shard profiles.

The cluster has no Keeper, replicated engines, or `ON CLUSTER` Distributed DDL;
setup creates fixtures on each node directly. Hive uses the real Hive connector
with Trino's file metastore at `local:///hive`, separate from Iceberg data in the
same persistent warehouse volume. This test-only topology supplies no S3,
REST catalog, remote Thrift metastore or multi-worker Trino.

The isolated Trino profile sets `query.max-stage-count=500` for the nested
relationship/allocation coverage cases. The default server budget of 150 stages
rejects those composed queries before their output checks run. This is a test
service setting; Marivo does not change a production server's stage budget.

## ClickHouse 22.3 non-UTC server

The non-UTC hour-sampling regression uses the server's default timezone, without
`session_timezone` or a per-user timezone override. ClickHouse 22.3 does not have
that newer session setting. The fixture requires `timezone() = 'Asia/Shanghai'`
and checks neutral wall carriers, exact UTC filtering across the previous date,
and sum-before-mean against independent native SQL.

Reuse an existing `marivo-multisource-clickhouse-22-shanghai-1` container. For its
initial creation, reuse the local `marivo-clickhouse-22:22.3.15.33` image built
from the official ARM64 release binary and the private UTC 22.3 fixture config.
Copy the config into a separate private directory and set the server timezone:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

cache = Path.home() / '.cache/marivo-multisource'
target = cache / 'clickhouse-22-shanghai-config'
target.mkdir(mode=0o700)
for name in ('config.xml', 'users.xml'):
    shutil.copy2(cache / 'clickhouse-22-config' / name, target / name)
    (target / name).chmod(0o600)
tree = ET.parse(target / 'config.xml')
zone = tree.getroot().find('timezone')
if zone is None:
    zone = ET.SubElement(tree.getroot(), 'timezone')
zone.text = 'Asia/Shanghai'
tree.write(target / 'config.xml', encoding='unicode')
PY
docker --host "unix://$HOME/.colima/marivo-multisource/docker.sock" run -d \
  --name marivo-multisource-clickhouse-22-shanghai-1 \
  --label com.docker.compose.project=marivo-multisource \
  --label com.docker.compose.service=clickhouse-22-shanghai \
  --memory=1g --cpus=1 --pids-limit=4096 -p 127.0.0.1:18324:8123 \
  --mount "type=bind,src=$HOME/.cache/marivo-multisource/clickhouse-22-shanghai-config,dst=/etc/clickhouse,readonly" \
  --mount type=volume,src=marivo-multisource_clickhouse-22-shanghai,dst=/var/lib/clickhouse \
  marivo-clickhouse-22:22.3.15.33
```

Initialize the dedicated database, then let the existing setup check the reader's
SELECT-only grants. The password stays in the private credential cache:

```bash
MARIVO_CLICKHOUSE_TEST_PORT=18324 .venv/bin/python - <<'PY'
import clickhouse_connect
from tests.datasource.environment.credentials import password
from tests.datasource.environment.clickhouse_analysis import setup

client = clickhouse_connect.get_client(
    host='127.0.0.1', port=18324, username='qualifier', password=password(),
    database='default', autogenerate_session_id=False,
)
try:
    client.command('CREATE DATABASE IF NOT EXISTS qualification')
finally:
    client.close()
print(setup())
PY
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT=18324 \
  make runtime-test RUNTIME_WORKERS=1 \
  TESTS='tests/analysis/temporal/test_wall_consumer_runtime.py::test_clickhouse_hour_sampling_uses_non_utc_server_timezone'
```

The same server also runs the calendar regression. It checks that a single
Asia/Shanghai civil day is one `day` bucket and 288 five-minute buckets, and that
week, month and 3-hour buckets keep facts after local 16:00 in the right period:

```bash
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 MARIVO_CLICKHOUSE_NON_UTC_TEST_PORT=18324 \
  make runtime-test RUNTIME_WORKERS=1 \
  TESTS='tests/analysis/temporal/test_clickhouse_server_timezone_calendar.py'
```

This standalone container uses its own data volume and leaves the UTC 22.3
service at port 18323 and other running services in place. Stop only this fixture
with `docker --host "unix://$HOME/.colima/marivo-multisource/docker.sock" stop marivo-multisource-clickhouse-22-shanghai-1`.

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

The manager waits for container health and performs PostgreSQL, MySQL and
MariaDB reader setup. Trino and ClickHouse reader setup use explicit module
commands. Choose only the profiles needed, with one Trino/ClickHouse group:

```bash
bash tests/datasource/environment/manage.sh start postgres-analysis
bash tests/datasource/environment/manage.sh start mysql-analysis
bash tests/datasource/environment/manage.sh start mysql-no-timezones
bash tests/datasource/environment/manage.sh start mariadb-analysis
bash tests/datasource/environment/manage.sh start trino
.venv/bin/python -m tests.datasource.environment.trino_analysis
bash tests/datasource/environment/manage.sh start clickhouse
.venv/bin/python -m tests.datasource.environment.clickhouse_analysis
bash tests/datasource/environment/manage.sh start clickhouse-cluster
.venv/bin/python -c 'from tests.datasource.environment import clickhouse_analysis as ch; print(ch.setup_cluster())'
```

The `mysql-no-timezones` profile sets `MYSQL_INITDB_SKIP_TZINFO=1` on its isolated
volume and starts with a `+08:00` default session timezone. Run its connection and
temporal transport regression with:

```bash
MARIVO_MYSQL_NO_TIMEZONES_TEST=1 make runtime-test TESTS='tests/datasource/test_source_adapters_runtime.py::test_mysql_reader_without_named_timezone_tables'
```

The test verifies empty time zone tables and error 1298 for named UTC before
opening the Marivo reader. It never clears system tables on shared services.

Trino startup initializes warehouse ownership with a short root process from
its pinned image. Smoke commands check service access and basic native queries,
then remove their fixtures:

```bash
.venv/bin/python -m tests.datasource.environment.smoke trino
.venv/bin/python -m tests.datasource.environment.smoke clickhouse
```

The manager accepts `status`, `logs` and `stop` for each profile. For example:

```bash
bash tests/datasource/environment/manage.sh status trino
bash tests/datasource/environment/manage.sh logs trino
bash tests/datasource/environment/manage.sh stop trino
```

Stopping `clickhouse-cluster` stops both shard services and the single-node
ClickHouse service. Reuse running services while other test invocations need them.
Stop the dedicated VM with `colima stop marivo-multisource` only after all of its
profiles are no longer in use. Stopping preserves data volumes; volume deletion
is an explicit reset.

## Reader permissions

PostgreSQL setup removes PUBLIC CREATE/TEMP, grants public-schema USAGE and
SELECT, and defaults reader transactions to read-only and UTC. MySQL and MariaDB
grant the reader only SELECT on `analysis.*`.

Trino file rules restrict `analysis_reader` to Iceberg/Hive/system metadata and the
`noniceberg` memory catalog. The loopback service does not authenticate identities.
Memory tables disappear on restart; tests using that catalog call
`trino_analysis.setup_non_iceberg()` before their reads. Query history retains at most 100 entries
with a one-minute minimum age.

ClickHouse grants SELECT and uses `join_use_nulls=1`. Current-server fixtures
use `readonly=1`; the 22.3 fixtures use `readonly=0` with SELECT-only grants
for compatibility. Their setup verifies that reader writes are denied.
Fixture setup permits query-local `max_execution_time`,
`timeout_before_checking_execution_speed`, cancellation response settings, and
`enable_materialized_cte` through `CHANGEABLE_IN_READONLY`.

## Installed-package tests

Installed tests require the current wheel, supported driver dependencies, and
already-ready services. Select backends explicitly and keep Trino separate from
ClickHouse:

```bash
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=postgres,mysql,clickhouse make installed-multisource-test
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=trino make installed-multisource-test
```

Use `MARIVO_TEST_WHEEL_DIR` for an isolated candidate directory. Process receipts
and logs live under the installed-wheel pytest temporary directory.
