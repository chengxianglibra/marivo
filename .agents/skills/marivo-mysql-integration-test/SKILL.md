---
name: marivo-mysql-integration-test
description: Use when running, repairing, or explaining Marivo live MySQL metadata integration tests, especially tasks involving MARIVO_TEST_MYSQL_DSN, local MySQL containers, Docker or Colima setup, MySQL metadata DDL validation, or tests/test_mysql_metadata_integration.py.
---

# Marivo MySQL Integration Test

Use this skill to run Marivo's skip-by-default live MySQL metadata tests from a local
container-backed MySQL instance.

## Rules

- Follow repository entrypoints: use `make test` or explicit `.venv/bin/...`; never use bare
  `python`, `pytest`, `mypy`, or `ruff`.
- Treat MySQL integration as opt-in. Export `MARIVO_TEST_MYSQL_DSN` only for the targeted run.
- Use a MySQL user with `CREATE DATABASE` and `DROP DATABASE`; root is acceptable for local tests.
- Do not assume Docker Desktop. Prefer native arm64 Colima on Apple Silicon.
- If global Docker config references a missing credential helper, use a temporary `DOCKER_CONFIG`
  and explicit Colima socket for test containers.
- Stop the MySQL test container after the targeted test run finishes.

## Environment Check

Confirm CLI/runtime shape before starting MySQL:

```bash
which docker
which colima
file "$(which docker)"
file "$(which colima)"
colima status || true
docker version || true
```

Expected Apple Silicon shape:

- `docker`, `colima`, and `limactl` should come from `/opt/homebrew/bin`.
- `file ...` should report `arm64`.
- `colima status` should report `arch: aarch64` and `runtime: docker` once started.

If Colima is installed but stopped, start it:

```bash
colima start --cpu 2 --memory 2 --disk 30 --runtime docker
```

If an old x86_64 Colima VM blocks startup and the user approves deleting it:

```bash
colima delete --force default || true
rm -rf ~/.colima/default ~/.lima/colima
colima start --cpu 2 --memory 2 --disk 30 --runtime docker
```

## MySQL Container

Use a temp Docker config when Docker fails with `docker-credential-osxkeychain`:

```bash
mkdir -p /tmp/marivo-docker-config
export DOCKER_CONFIG=/tmp/marivo-docker-config
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
```

Start a clean MySQL 8.4 container:

```bash
docker rm -f marivo-mysql-test 2>/dev/null || true
docker run -d \
  --name marivo-mysql-test \
  -e MYSQL_ROOT_PASSWORD=marivo_root \
  -e MYSQL_DATABASE=marivo_metadata \
  -p 3306:3306 \
  mysql:8.4 \
  --character-set-server=utf8mb4 \
  --collation-server=utf8mb4_unicode_ci
```

Wait until MySQL is ready:

```bash
for i in $(seq 1 90); do
  if docker exec marivo-mysql-test mysqladmin ping -h127.0.0.1 -uroot -pmarivo_root --silent; then
    echo ready
    break
  fi
  sleep 1
done
```

## Test Commands

Install the MySQL extra in the current venv if `PyMySQL` is missing:

```bash
.venv/bin/python -m pip install -e '.[mysql]'
```

Use this DSN for the local container:

```bash
export MARIVO_TEST_MYSQL_DSN='mysql+pymysql://root:marivo_root@127.0.0.1:3306/marivo_metadata?connect_timeout=10'
```

Run the startup-focused integration test:

```bash
make test TESTS='tests/test_mysql_metadata_integration.py::MySQLMetadataIntegrationTests::test_app_startup_with_mysql_config_supports_basic_session_api'
```

Run the full live MySQL integration file:

```bash
make test TESTS='tests/test_mysql_metadata_integration.py'
```

Run supporting non-live checks after MySQL DDL changes:

```bash
make test TESTS='tests/test_mysql_metadata_ddl.py tests/test_metadata_schema_bootstrap.py tests/test_config.py'
make lint
make typecheck
```

## DDL Troubleshooting

For MySQL DDL failures, execute generated DDL one statement at a time against a throwaway database
to identify the exact statement before patching:

```bash
MARIVO_TEST_MYSQL_DSN="$MARIVO_TEST_MYSQL_DSN" .venv/bin/python - <<'PY'
import os
from uuid import uuid4

import pymysql
from pymysql.cursors import DictCursor

from app.config import MetadataConfig
from app.storage.schema import MYSQL_METADATA_DDL

base = MetadataConfig.model_validate(
    {"engine": "mysql", "dsn": os.environ["MARIVO_TEST_MYSQL_DSN"]}
).mysql_connection_config()
db = "marivo_debug_" + uuid4().hex
admin = pymysql.connect(
    host=base["host"],
    port=int(base["port"]),
    user=base["user"],
    password=base.get("password"),
    connect_timeout=int(base["connect_timeout"]),
    cursorclass=DictCursor,
)
cur = admin.cursor()
cur.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
admin.commit()
cur.close()
admin.close()

con = pymysql.connect(
    host=base["host"],
    port=int(base["port"]),
    user=base["user"],
    password=base.get("password"),
    database=db,
    connect_timeout=int(base["connect_timeout"]),
    cursorclass=DictCursor,
)
try:
    for idx, ddl in enumerate(MYSQL_METADATA_DDL, 1):
        try:
            cur = con.cursor()
            cur.execute(ddl)
            con.commit()
            cur.close()
        except Exception as exc:
            print("FAILED", idx, type(exc).__name__, exc)
            print(ddl)
            raise SystemExit(1)
    print("all ok")
finally:
    con.close()
    admin = pymysql.connect(
        host=base["host"],
        port=int(base["port"]),
        user=base["user"],
        password=base.get("password"),
        connect_timeout=int(base["connect_timeout"]),
        cursorclass=DictCursor,
    )
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
    admin.commit()
    cur.close()
    admin.close()
PY
```

Common MySQL 8.4 DDL failures:

- `TEXT/LONGTEXT ... DEFAULT`: remove defaults from MySQL large text columns.
- `Invalid default value for created_at`: match `DATETIME(6)` with `CURRENT_TIMESTAMP(6)`.
- `non-boolean type specified to a check constraint`: rewrite `CHECK (CASE ...)` as boolean `OR`.
- `Specified key was too long`: shorten indexed `VARCHAR` columns or avoid wide composite indexes.

## Cleanup

Stop the MySQL container after the targeted test run:

```bash
docker stop marivo-mysql-test
```

Remove the container only when a clean container is needed for the next run:

```bash
docker rm -f marivo-mysql-test
```
