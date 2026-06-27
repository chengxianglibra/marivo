# marivo-analysis backend setup

Use this reference before running the default operators that materialize live
data: `session.observe`, `session.compare`, `session.attribute`,
`session.discover.<objective>`, `session.correlate`, `session.hypothesis_test`,
`session.forecast`, `session.derive_metric_frame`, or `session.assess_quality`.
The default operator surface is `observe`, `compare`, `attribute`,
`discover.<objective>`, `correlate`, `hypothesis_test`, `forecast`,
`derive_metric_frame`, and `assess_quality`; each needs a resolvable backend
when the session touches live data.

Datasource definition and repair are owned by `marivo-semantic`; see
`../marivo-semantic/references/datasource.md`. This analysis skill consumes
project datasources or uses explicit backend overrides for tests/CI.

## Backend Resolution Order

`marivo.analysis` resolves backends in this order:

1. `backends={name: callable}` if provided.
2. `backend_factory=lambda name: ...` if provided.
3. Project datasource files in `models/datasources/*.py` by default.
4. With `use_datasources=False` and no explicit factory, materialization fails
   with `NoBackendFactoryError`.

## Project Datasource Session

Use this when the project already has the required `models/datasources/*.py`
files and any referenced secret env vars are exported.

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="analysis")
```

If a datasource is missing or invalid, switch to `marivo-semantic` and
repair `models/datasources/<name>.py` before continuing the analysis.

## Explicit Factory

Tests, CI, and deterministic scripts can opt out of project datasource lookup:

```python
import os

import ibis
import marivo.analysis as mv

def make_backend(datasource_name: str):
    if datasource_name != "warehouse":
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="trino.example.internal",
        port=8080,
        user=os.environ["TRINO_USER"],
        database="hive",
        schema="default",
        http_scheme="https",
        client_tags=["standby", "routing_group=wide"],
    )

session = mv.session.get_or_create(
    name="analysis",
    backend_factory=make_backend,
    use_datasources=False,
)
```

Datasource names are global. Accept names such as `warehouse`; do not route on
model-qualified ids such as `sales.warehouse`.

## Guardrails

- Do not define or repair datasources in this skill; that belongs to
  `marivo-semantic`.
- Do not write users, passwords, tokens, cookies, or private keys into scripts.
- Use `use_datasources=False` in tests so project datasource files cannot mask
  fixture bugs.
- Treat DNS, gateway, auth, and 5xx errors as datasource reachability failures,
  not semantic modeling failures.
