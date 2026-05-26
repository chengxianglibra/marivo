# marivo-py-semantic datasource reference

Use this reference when semantic authoring needs a datasource but the prompt
does not provide enough information to declare one safely.

## When to Ask the User

Ask for datasource information before writing or repairing the semantic model
when:

- a new dataset is being declared and no datasource is named
- a dataset points at a datasource that is not declared in the model
- the user gives only a table name, SQL snippet, or metric idea
- materialization or analysis fails because no backend factory is available
- the backend type, schema, table, or column inventory is ambiguous

Do not infer the datasource from field names or from the business question. A
datasource is execution grounding, not a metric definition.

## Minimum Information to Collect

Collect the smallest set needed to declare the semantic object and let runtime
code provide the actual connection:

- stable datasource name for semantic refs, such as `warehouse`
- `backend_type`, such as `duckdb`, `trino`, or `mysql`
- physical relation identity: file/path for DuckDB, or catalog/schema/table for
  warehouse engines such as Trino
- key columns, time columns, measure columns, and descriptor columns needed by
  the dataset and downstream metrics
- where the project already resolves a live Ibis backend, such as a
  `backend_factory`, environment-managed connection, or existing analysis setup

Never ask the user to paste credentials into a semantic model. The semantic
file only declares datasource identity (`name`, `backend_type`). The actual
connection lives in the user-scope profile registry at
`~/.marivo/profiles/profiles.json`, written via `mv.profiles.set(...)` from
`marivo.analysis_py`. Sensitive fields go through `*_env` references and are
read from `os.environ` at backend-build time. See
[`marivo-py-analysis/references/profiles.md`](../../marivo-py-analysis/references/profiles.md)
for the full registry contract.

## Semantic Declaration Rule

`ms.datasource(...)` is a top-level metadata declaration. It records datasource
identity and backend type; it does not open a connection, store a DSN, create a
pool, or hold credentials.

```python
import marivo.semantic_py as ms

ms.model(name="sales")
warehouse = ms.datasource(
    name="warehouse",
    backend_type="trino",
    description="Production analytics warehouse.",
)
```

Datasets bind to the datasource ref and use the backend passed by
materialization or analysis:

```python
@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders")
```

The caller that executes or materializes the model must provide the live Ibis
backend:

```python
expr = project.materialize_metric(
    "sales.revenue",
    backend_factory=lambda datasource_name: live_backend_for(datasource_name),
)
```

## DuckDB Example

Use DuckDB for local files, local development, and small reproducible examples.
The semantic file names the datasource and the table; test or analysis code owns
the actual `ibis.duckdb.connect(...)` call.

```python
import marivo.semantic_py as ms

ms.model(name="sales")

local_warehouse = ms.datasource(
    name="local_warehouse",
    backend_type="duckdb",
    description="Local DuckDB fixture for sales examples.",
)

@ms.dataset(name="orders", datasource=local_warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders")
```

Ask the user for:

- the DuckDB file path, or confirmation that the example should use `:memory:`
- the table name visible to DuckDB
- primary key, time field, dimensions, and measures to expose semantically

Keep the file path in the execution setup when possible. If a local example
must mention a path, keep it in example/test code rather than in
`ms.datasource(...)`.

## Trino Example

Use Trino for production warehouse relations where table identity normally
includes catalog, schema, and table. The semantic model should describe that
identity without embedding hostnames, tokens, usernames, or passwords.

```python
import marivo.semantic_py as ms

ms.model(name="sales")

warehouse = ms.datasource(
    name="warehouse",
    backend_type="trino",
    description="Production Trino warehouse.",
)

@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders", database=("hive", "sales_mart"))
```

Ask the user for:

- semantic datasource name, usually a stable alias such as `warehouse`
- Trino catalog, schema, and table
- project-approved connection setup or backend factory name
- key, time, dimension, and measure columns confirmed from live metadata

If the user gives a Trino DSN or credentials, do not copy them into the semantic
file. Use the project runtime/configuration boundary for the live connection and
keep the semantic declaration limited to `name=`, `backend_type=`, description,
and dataset table selection.

## Prompt to User When Information Is Missing

Use a concise request like this:

```text
I need datasource grounding before declaring the dataset. Please provide:
1. backend type (duckdb, trino, mysql, etc.)
2. stable datasource alias to use in semantic refs
3. physical table identity (DuckDB table/path, or Trino catalog.schema.table)
4. primary key, time column, and measure/dimension columns to model
5. connection metadata for the profile registry (host, port, user, catalog/schema;
   for any password/token, the env var name only — never the literal)
```

Stop and ask instead of writing a guessed datasource when any of these fields
would change semantic ids, physical grounding, or execution behavior.

After the user supplies the connection metadata, persist it once via
`mv.profiles.set(...)` from `marivo.analysis_py` so future sessions reuse it
without re-prompting. Sensitive fields go via `<field>_env="VAR_NAME"`. The
detailed contract lives in
[`marivo-py-analysis/references/profiles.md`](../../marivo-py-analysis/references/profiles.md).
