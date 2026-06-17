# Datasource: Per-Backend Typed Spec Classes

- Date: 2026-06-17
- Status: Approved design, pending implementation plan
- Surface: `marivo.datasource`
- Compatibility: **Hard cutover. No backward compatibility or data migration.**

## Problem

The datasource authoring surface is agent-facing (write-run-read loop), but its
core constructor is an untyped mega-bag:

```python
DatasourceSpec(*, name, backend_type, description=None, ai_context=None, **fields: Any)
```

This contradicts the repository's own agent-surface principles
(`agent-guide.md`): "Public API functions must not accept or return `Any`" and
"Prefer one entry shape with closed, kind-dispatched variants over
optional-field mega-classes: precise types fail loudly, optional-field unions
fail silently."

Concrete consequences for an authoring agent:

1. **The signature teaches nothing.** `md.help('DatasourceSpec')` shows
   `**fields` plus one example. It cannot reveal that trino needs
   `host`+`catalog`, mysql/postgres need `host`+`database`, clickhouse needs
   `host`. That contract lives only in prose and in the `_require()` calls
   inside `_build_*` (`marivo/datasource/backends.py`).
2. **Failure is deferred and silent.** A spec missing a required field
   constructs and `register()`s fine; it only fails at `md.test()`/connect.
3. **Typo trap.** Any field name is accepted (`prot=8080`), silently stored,
   and forwarded to ibis or ignored.
4. **`backend_type` is an unchecked `str`**, validated only at connect time.
5. **`DatasourceDescription`** (returned by `md.describe()`, the read step of
   the loop) is a public result type that uses the default dataclass repr and
   has no `.show()`, violating the shared result protocol that its siblings
   `DatasourceSummary` and `DatasourceTestResult` implement.

## Goals

- Make the per-backend connection contract **discoverable from the type
  surface** (signature + `help`), not from prose or runtime failure.
- Make invalid authoring **fail loud at construction** wherever the type system
  can express it.
- Keep secrets out of plaintext and keep the flexible passthrough for rare ibis
  kwargs.
- Bring `DatasourceDescription` up to the result protocol.

## Non-Goals

- Load-time validation of already-persisted datasource files. The builder
  `_require()` calls remain the backstop for the disk-load path (which bypasses
  spec construction). This is exercised today by
  `tests/test_analysis_profiles_backends.py::test_trino_required_field_missing`.
- A plugin mechanism for third-party backends. The supported set stays the five
  current backends.

## Approach

**Per-backend typed spec classes** (chosen over a data-driven validation
registry on a single `DatasourceSpec`).

Five frozen dataclasses — `DuckDBSpec`, `TrinoSpec`, `MySQLSpec`,
`PostgresSpec`, `ClickHouseSpec` — each with the **dataclass-generated
`__init__`** so that `dataclasses.fields()` (and therefore the existing
`marivo/introspection/describe.py::dataclass_field_infos`) sees every
connection parameter. This delivers rich `help` for free: `md.help("TrinoSpec")`
renders each field's name, annotation, `required` flag (derived from
absence of a default), default, and description (from
`field(metadata={"description": ...})`) with no new help code.

Why this over the registry approach:

- Several findings collapse into the type system instead of runtime checks:
  unknown/typo'd fields raise native `TypeError`; an unsupported backend is
  structurally impossible (no class exists for it).
- It mirrors an existing repository pattern: `EntitySourceIR = TableSourceIR |
  ParquetSourceIR | CsvSourceIR` (closed, kind-dispatched typed variants) and
  the `ms.table()/ms.parquet()/ms.csv()` typed source constructors.
- `help` works per concrete class without a `backend=` argument, because each
  class is the backend.

The cost: five new public classes (gated by the `__all__` snapshot test). They
form one new family, "Datasource specs", which satisfies the surface-growth
rule.

## Design

### Class hierarchy

```python
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

@dataclass(frozen=True, kw_only=True)
class _DatasourceSpecBase:
    name: str
    description: str | None = None
    ai_context: AiContext | dict[str, Any] | None = None
    extra: dict[str, JsonValue] | None = None   # verbatim ibis-kwarg escape hatch

    # backend_type is a ClassVar on each concrete subclass (not a field).
    backend_type: ClassVar[str]

    def __post_init__(self) -> None:
        # validate name (validate_datasource_name)
        # validate non-empty required string fields -> DatasourceFieldInvalidError
        # validate extra: every value JSON-able AND no sensitive stem keys
        ...

    def to_ir(self, *, location: DatasourceSourceLocation) -> DatasourceIR:
        # partition declared fields into literal `fields` vs `env_refs`
        #   - a "<stem>_env" field contributes env_refs[stem] = value (when set)
        #   - other non-meta fields contribute fields[name] = value (when set)
        #   - meta fields (name/description/ai_context/extra) are excluded
        #   - extra contents merge into `fields`
        # build AiContextIR from ai_context
        ...
```

- `kw_only=True` on base and subclasses so a required field (no default) may
  follow optional base fields across the inheritance boundary without the
  "non-default argument follows default argument" error.
- All datasource construction is keyword-only, matching the current API.

### Per-backend fields

Source of truth: ibis 12.0.0 connect signatures (read from installed source),
the current `_build_*` requiredness, and repo conventions. ibis defaults
*everything*; "required" is a marivo product decision equal to the current
`_require()` contract.

Default rule: optional fields default to `None` and are **forwarded to ibis only
when set**, so ibis applies its own default. The meaningful default is documented
in the field's help description. Exception: `DuckDBSpec.path` defaults to
`":memory:"` to match current behavior.

**TrinoSpec** — `backend_type = "trino"`

| field | role | type | notes |
|---|---|---|---|
| `host` | required | `str` | ibis `host` |
| `catalog` | required | `str` | renamed to ibis `database` in the builder |
| `port` | optional | `int \| None` | ibis default 8080 |
| `schema` | optional | `str \| None` | default schema; usually selected at table time |
| `source` | optional | `str \| None` | client application/source tag |
| `timezone` | optional | `str \| None` | engine session tz (ibis default UTC); probed at runtime |
| `http_scheme` | optional | `str \| None` | set `"https"` for TLS (passthrough kwarg) |
| `client_tags` | optional | `tuple[str, ...] \| None` | passthrough (trino client) |
| `session_properties` | optional | `dict[str, str] \| None` | passthrough (trino client) |
| `user_env` | secret | `str \| None` | -> ibis `user` |
| `auth_env` | secret | `str \| None` | -> ibis `auth`; ibis wraps as `BasicAuthentication(user, auth)` for non-`http` hosts; also accepts a JWT token |

Trino has **no `password` param**. Secrets are `user_env` + `auth_env`,
matching ibis and the existing repo convention
(`tests/test_analysis_profiles_backends.py` uses `user_env`/`auth_env`).

**ClickHouseSpec** — `backend_type = "clickhouse"`

| field | role | type | notes |
|---|---|---|---|
| `host` | required | `str` | |
| `port` | optional | `int \| None` | native 9000 / secure 9440 |
| `database` | optional | `str \| None` | ibis default `"default"` |
| `secure` | optional | `bool \| None` | TLS |
| `settings` | optional | `dict[str, Any] \| None` | ClickHouse settings map |
| `user_env` | secret | `str \| None` | -> ibis `user` (default `"default"`) |
| `password_env` | secret | `str \| None` | -> ibis `password` (default `""`) |

`compression` and `client_name` are intentionally relegated to `extra=`.

**MySQLSpec** — `backend_type = "mysql"`

| field | role | type | notes |
|---|---|---|---|
| `host` | required | `str` | |
| `database` | required | `str` | forwarded via `**kwargs` (mysql do_connect has no named `database`) |
| `port` | optional | `int \| None` | ibis default 3306 |
| `autocommit` | optional | `bool \| None` | ibis default True |
| `user_env` | secret | `str \| None` | -> ibis `user` |
| `password_env` | secret | `str \| None` | -> ibis `password` |

**PostgresSpec** — `backend_type = "postgres"`

| field | role | type | notes |
|---|---|---|---|
| `host` | required | `str` | |
| `database` | required | `str` | |
| `port` | optional | `int \| None` | ibis default 5432 |
| `schema` | optional | `str \| None` | |
| `autocommit` | optional | `bool \| None` | ibis default True |
| `user_env` | secret | `str \| None` | -> ibis `user` |
| `password_env` | secret | `str \| None` | -> ibis `password` |

**DuckDBSpec** — `backend_type = "duckdb"`

| field | role | type | notes |
|---|---|---|---|
| `path` | optional | `str` | default `":memory:"`; renamed to ibis `database` in the builder |
| `read_only` | optional | `bool` | default `False` |

No secrets. `extensions` is relegated to `extra=`.

### `DatasourceSpec` union alias

```python
DatasourceSpec = DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec
```

Mirrors `EntitySourceIR` in `marivo/datasource/ir.py`. Used to annotate
`register(spec: DatasourceSpec)` and `datasource(spec: DatasourceSpec)`. It is
importable for typing but is **not a constructable class**. Per the
surface-growth rule, the union alias stays out of the top-level `help` index;
the five concrete specs appear there folded under a "Datasource specs" family.

### Secret handling

- Named `*_env` fields per backend (`user_env`, `auth_env`, `password_env`) are
  the only credential surface. There is no plaintext credential parameter, so
  "no plaintext secrets" becomes structural for the common path.
- The conventional connect-time fallback `MARIVO_{NAME}_{STEM}` is unchanged
  (`marivo/datasource/backends.py::_effective_kwargs`). All `*_env` fields are
  optional, so no-auth/local setups still work.
- The generic `secret_env` mapping considered earlier is **dropped**: the secret
  stems per supported backend are a known closed set, so named fields are
  strictly more discoverable. Exotic auth (e.g. Kerberos objects) is out of
  scope; it cannot be expressed as an env-var string.

### `extra=` escape hatch

- `extra: dict[str, JsonValue] | None` forwards rare ibis kwargs verbatim
  (merged into the IR `fields`).
- `extra` still runs the JSON-able check and the sensitive-stem rejection, so a
  plaintext secret or a non-JSON Python object cannot be smuggled through it.

### Validation and error behavior

| Situation | Behavior |
|---|---|
| Missing required field | Native `TypeError: TrinoSpec.__init__() missing 1 required keyword-only argument: 'catalog'`. It names the field. Accepted over a custom error because adding a default to intercept it would corrupt the help `required` flag. |
| Unknown / typo'd field | Native `TypeError: ... got an unexpected keyword argument 'prot'`. Docstring and `extra=` cover genuine passthrough. |
| Invalid value (empty string, bad name) | Teaching `DatasourceFieldInvalidError` from `__post_init__`. |
| Plaintext secret in `extra` | `DatasourceSecretInPlaintextError`; fix snippet points at the relevant `*_env` field. |
| Unsupported backend | Structurally impossible from the public surface. Runtime `DatasourceBackendTypeUnsupportedError` remains only as a disk-load backstop. |

### Help and discovery

- `help`/`describe` already extract `FieldInfo` from any dataclass via
  `marivo/introspection/describe.py::dataclass_field_infos`. Each spec field
  carries `field(metadata={"description": "..."})` so descriptions render.
- `marivo/datasource/help.py`: add `_SUMMARIES` entries for the five classes;
  update the `DatasourceSpec` summary to describe the union/family; add a
  "Datasource specs" `FamilyFold` in the top-level index.

### `DatasourceDescription` result protocol (finding #5)

Give `DatasourceDescription` (`marivo/datasource/manage.py`) the same quartet as
its siblings: `_repr_identity()`, `render()` (via `format_bounded_card`),
`__repr__` (via `result_repr`), and `show()`. Decorate with
`@dataclass(frozen=True, repr=False)`. Bound the rendered field/env listing.

### Builders

`_build_*` in `marivo/datasource/backends.py` consume the IR `fields`/`env_refs`
dicts, whose keys are unchanged by this design, so the builders are largely
untouched. The `_require()` calls stay as the disk-load backstop. The
`client_tags` string-splitting and `session_properties`/`settings` handling
remain.

### `datasource()` / `register()`

- `register(spec: DatasourceSpec)` and `datasource(spec: DatasourceSpec)` accept
  the union alias.
- `datasource()` drops the legacy `name=..., backend_type=..., **fields`
  keyword path entirely (no compatibility requirement).

## Public surface changes

`marivo/datasource/__init__.py` and the `__all__` snapshot
(`tests/test_public_surface.py::DATASOURCE_PUBLIC`):

- Add: `DuckDBSpec`, `TrinoSpec`, `MySQLSpec`, `PostgresSpec`, `ClickHouseSpec`.
- `DatasourceSpec`: now the union alias (kept in `__all__` for typing imports).

## Findings -> resolution

| Finding | Resolution |
|---|---|
| #1 per-backend contract not discoverable | Class signature is the contract; `help` renders it from `dataclasses.fields()`. |
| #2 `backend_type` unchecked | Structurally impossible to pick an unsupported backend. |
| #3 `DatasourceDescription` repr/show | Implements the result protocol. |
| #4 unknown-field typo trap | Native `TypeError` + `extra=` for real passthrough. |
| (deferred failure) | Required/typo failures move to construction. |

## Testing strategy

New / updated tests:

- Per-class IR shape: each spec builds the expected `DatasourceIR`
  (`backend_type`, `fields`, `env_refs`); name-mappings hold (`catalog`->
  `database` for trino, `path`->`database` for duckdb; mysql `database` present).
- Missing required field raises native `TypeError` naming the field.
- Unknown kwarg raises native `TypeError`.
- `extra=` passthrough merges into `fields`; a sensitive stem or non-JSON value
  in `extra` raises `DatasourceSecretInPlaintextError` /
  `DatasourceFieldInvalidError`.
- Trino secrets: `user_env`/`auth_env` populate `env_refs["user"]`/
  `env_refs["auth"]`.
- `help`/`describe` for one class renders fields with correct `required` flags
  and descriptions.
- `DatasourceDescription` repr is bounded single-line; `show()` renders a card.
- `tests/test_public_surface.py::DATASOURCE_PUBLIC` updated and passing.
- Schema/builder agreement: a test asserting each spec's required set matches the
  builder's `_require()` calls, preventing drift.

Migration:

- `tests/test_analysis_profiles_backends.py::_spec` (and similar helpers) change
  from `md.DatasourceSpec(name=, backend_type=, **fields)` to dispatching on
  `backend_type` to the right typed class. Trino cases using `password_env` are
  corrected to `auth_env` (today a silent no-op).
- `tests/shared_fixtures.py`, `tests/conftest.py`, and other callers that
  construct `DatasourceSpec(**fields)` migrate to the typed classes (duckdb
  fixtures -> `DuckDBSpec`).
- `tests/test_analysis_profiles_store.py` literal-secret rejection case
  (`auth="literal-token"`) migrates to constructing a `TrinoSpec` and asserting
  the sensitive-stem rejection still fires (via `extra=` or the relevant `*_env`
  contract).

## Documentation updates

- `.claude/skills/marivo-py-semantic/references/datasource.md`: rewrite the
  DuckDB/Trino examples to the typed classes; correct the Trino secret example
  to `user_env`/`auth_env`.
- Docstrings on the new classes, `register`, and `datasource`.

## Success criteria

- `make typecheck`, `make lint`, `make test` pass.
- `md.help("TrinoSpec")` shows host/catalog as required and the optional fields
  with descriptions and defaults.
- Constructing a spec without a required field fails immediately with a message
  naming the field.
- No `Any` on the public construction path except inside `extra=` values and the
  documented `settings` map.
