# marivo-py-analysis backend setup

Use this reference before running `mv.observe`, `mv.compare`, `mv.decompose`,
`mv.discover`, or `mv.correlate` when the session needs live data.

Profile definition and repair are owned by `marivo-py-semantic`; see
`../marivo-py-semantic/references/datasource.md`. This analysis skill only
consumes existing profiles or uses explicit backend overrides for tests/CI.

## Backend Resolution Order

`marivo.analysis_py` resolves backends in this order:

1. `backends={name: callable}` if provided.
2. `backend_factory=lambda name: ...` if provided.
3. Existing user-scope datasource profiles by default.
4. With `use_profiles=False` and no explicit factory, materialization fails
   with `NoBackendFactoryError`.

## Profile-Backed Session

Use this when semantic authoring has already created and tested the required
profiles.

```python
import marivo.analysis_py as mv

session = mv.session.create(name="analysis")
```

If a profile is missing or invalid, switch to `marivo-py-semantic` and repair
the datasource/profile before continuing the analysis.

## Explicit Factory

Tests, CI, and deterministic scripts can opt out of profiles:

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
    use_profiles=False,
)
```

Use exactly one override form:

- `backends={name: callable}` for a small static mapping. Each callable takes no
  arguments and returns a live ibis backend.
- `backend_factory=lambda datasource_name: ...` when routing depends on the
  datasource name.

Accept both full semantic ids such as `sales.warehouse` and short names such as
`warehouse` in agent-authored factories.

## Guardrails

- Do not define or repair profiles in this skill; that belongs to
  `marivo-py-semantic`.
- Do not write passwords, tokens, cookies, or private keys into scripts.
- Use `use_profiles=False` in tests so local profiles cannot mask fixture bugs.
- Treat DNS, gateway, auth, and 5xx errors as datasource reachability failures,
  not semantic modeling failures.
