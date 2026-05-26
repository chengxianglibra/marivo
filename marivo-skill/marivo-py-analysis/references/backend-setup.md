# marivo-py-analysis backend setup

Use this reference before running `mv.observe`, `mv.compare`, `mv.decompose`,
`mv.discover`, or `mv.correlate` when the session needs live data.

`marivo.analysis_py` resolves backends in this order:

1. `backends={name: callable}` if you pass one.
2. `backend_factory=lambda name: ...` if you pass one.
3. The user-scope profile registry (`~/.marivo/profiles/profiles.json`) —
   the default. See [profiles.md](profiles.md) for the full reference.
4. With `use_profiles=False` and no explicit factory, the session refuses
   to materialize data and raises `NoBackendFactoryError`.

## Recommended flow (profile-backed)

This is the path agents should take by default:

```python
import marivo.analysis_py as mv

mv.profiles.set(
    "warehouse",
    backend_type="trino",
    host="trino.example.internal",
    port=8080,
    user="analytics",
    catalog="hive",
    schema="default",
    http_scheme="https",
    password_env="WAREHOUSE_PWD",   # secret read from os.environ
)

session = mv.session.create(name="analysis")   # auto-loads from profile
```

Once the profile exists on the developer's machine, every future session
reuses it — the agent does not have to ask for connection details again.

### DuckDB template

```python
mv.profiles.set("local_warehouse", backend_type="duckdb", path=":memory:")
# or
mv.profiles.set("local_warehouse", backend_type="duckdb", path="/data/warehouse.duckdb")
session = mv.session.create(name="analysis")
```

### MySQL template

```python
mv.profiles.set(
    "ops",
    backend_type="mysql",
    host="mysql.internal", port=3306,
    user="reporter", database="analytics",
    password_env="OPS_DB_PWD",
)
```

### Postgres template

```python
mv.profiles.set(
    "ops",
    backend_type="postgres",
    host="pg.internal", port=5432,
    user="reporter", database="analytics", schema="public",
    password_env="OPS_DB_PWD",
)
```

### Sensitive credentials

Profile fields whose stem is `password / token / secret / secret_key /
access_key / private_key / passphrase / api_key` **must** be supplied as the
sibling `*_env` reference. Passing the literal raises
`ProfileSecretInPlaintextError`. The agent should ask the user to export the
secret to an environment variable, then store only the variable name in the
profile.

### Cross-checking before analysis

```python
result = mv.profiles.audit_project(session.semantic_project)
if result.missing:
    # Ask the user once for everything missing, then call mv.profiles.set(...).
    ...
```

This collects all missing datasources in a single round trip rather than
failing mid-analysis on the first one.

## Explicit factory (override or CI)

Tests, CI, and any flow that needs deterministic backend wiring can opt out
of the profile registry:

```python
import ibis
import marivo.analysis_py as mv

def make_backend(datasource_name: str):
    if datasource_name not in {"warehouse", "sales.warehouse"}:
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="trino.example.internal",
        port=8080,
        user="analytics",
        database="hive",
        schema="default",
        http_scheme="https",
        client_tags=["standby", "routing_group=wide"],
    )

session = mv.session.create(
    name="analysis",
    backend_factory=make_backend,
    use_profiles=False,            # disable auto-fallback to profile registry
)
```

Use exactly one of these forms when overriding:

- `backends={name: callable}` for a small static mapping. Each callable takes
  no arguments and returns a live Ibis backend.
- `backend_factory=lambda datasource_name: ...` when routing depends on the
  datasource name.

`backend_factory` receives the datasource referenced by the semantic model.
In practice this can be the full semantic id such as `sales.warehouse`;
analysis also supports short-name fallback such as `warehouse`. Prefer
accepting both in agent-authored scripts.

`use_profiles=False` is recommended in tests so a stray profile on the
developer's machine cannot mask a misconfigured fixture.

## Trino field mapping (prompt JSON → profile)

| Prompt field | Profile field | Rule |
| --- | --- | --- |
| `host` | `host` | Copy. |
| `port` | `port` | Copy integer. |
| `user` | `user` | Copy. |
| `catalog` | `catalog` | Profile uses the Trino-native name; mapped to ibis `database` at build time. |
| `schema` | `schema` | Use when the prompt provides a default schema. |
| `source` | `source` | Copy source/application name. |
| `http_scheme` | `http_scheme` | Usually `http` or `https`. |
| `client-tags` (string) | `client_tags` (list) | Split on `,` and strip. |
| `client_tags` (list) | `client_tags` | Pass through. |
| `password` | `password_env` | **Never** store the literal — write the env var name. |
| `session_properties` | `session_properties` | Dict pass-through. |

## Semantic grounding

Profile setup only opens the connection. Table selection still belongs in the
semantic dataset function:

```python
@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders", database=("hive", "sales_mart"))
```

If the profile sets `schema=...`, the dataset can usually call
`backend.table("orders")`. If the schema varies by dataset, keep the schema in
the semantic dataset `database=(catalog, schema)` tuple instead.

## Guardrails

- Do not write passwords, tokens, cookies, or private keys into semantic
  model files.
- Do not write secrets into the profile registry — use `*_env` references.
- The registry is user-scope and project-isolated; do not commit
  `~/.marivo/profiles/` (it lives outside any project anyway).
- If analysis raises `NoBackendFactoryError`, either create a profile with
  `mv.profiles.set(...)` or attach the session again with explicit
  `backends=` / `backend_factory=`.
- If analysis raises `ProfileMissingError`, the message lists the configured
  profiles and gives a pasteable `mv.profiles.set(...)` snippet for the
  missing one.
- If Trino fails before SQL execution with DNS, gateway, or 5xx errors,
  report it as datasource reachability, not semantic modeling failure.
- See [profiles.md](profiles.md) for the full registry reference.
