"""Authoring surface for project-level datasources."""

from __future__ import annotations

import inspect
import re
from contextvars import ContextVar
from dataclasses import MISSING, Field, dataclass, field, fields
from typing import Any, ClassVar, TypeAlias, cast, get_args, get_origin

from marivo._authoring.model import AuthoringContract
from marivo.datasource.errors import (
    DatasourceFieldInvalidError,
    DatasourceSecretInPlaintextError,
    repair,
)
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.typing import AiContextValue
from marivo.refs import DatasourceKind, Ref, SemanticKind, _validate_segment


def _build_ai_context(ai_context: AiContextValue | None) -> AiContextIR:
    if ai_context is None:
        return AiContextIR()
    if not isinstance(ai_context, AiContextValue):
        raise DatasourceFieldInvalidError(
            message=(
                "ai_context= expects an AiContextValue from ms.ai_context(...), "
                "not a raw dict. Construct it explicitly with "
                "ms.ai_context(business_definition=..., guardrails=[...]). "
                "summary= and other unsupported metadata keys are not accepted."
            ),
            expected="an AiContextValue from ms.ai_context(...)",
            received=type(ai_context).__name__,
            location="datasource ai_context",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Construct ai_context with ms.ai_context(...).",
            ),
        )
    return AiContextIR(
        business_definition=ai_context.business_definition,
        guardrails=ai_context.guardrails,
    )


SENSITIVE_FIELD_STEMS = frozenset(
    {
        "user",
        "password",
        "token",
        "secret",
        "secret_key",
        "access_key",
        "auth",
        "private_key",
        "passphrase",
        "api_key",
    }
)
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]  # noqa: UP040

_META_FIELDS = frozenset({"name", "ai_context", "extra", "fields", "env_refs"})


def _description(text: str) -> dict[str, str]:
    return {"description": text}


def _is_field_required(dataclass_field: Field[object]) -> bool:
    return dataclass_field.default is MISSING and dataclass_field.default_factory is MISSING


def _allows_none(annotation: object) -> bool:
    """Check whether a type annotation admits None.

    Handles both resolved type objects and string annotations
    (from ``from __future__ import annotations``).
    """
    if annotation is None or annotation is type(None):
        return True
    if isinstance(annotation, str):
        return "None" in annotation and "|" in annotation
    origin = get_origin(annotation)
    if origin is None:
        return False
    return type(None) in get_args(annotation)


def _is_jsonable_value(value: Any) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_jsonable_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_jsonable_value(item) for key, item in value.items())
    return False


def _normalize_value(value: object) -> JsonValue:
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return cast("JsonValue", value)


def _validate_jsonable_field(name: str, key: str, value: object) -> JsonValue:
    normalized = _normalize_value(value)
    if not _is_jsonable_value(normalized):
        raise DatasourceFieldInvalidError(
            message=(
                f"datasource {name!r} field {key!r} has unsupported value type "
                f"{type(value).__name__}"
            ),
            expected="a JSON-compatible datasource field value",
            received=type(value).__name__,
            location=f"models/datasources/ entry {name!r} field {key!r}",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Use a JSON-compatible datasource field value.",
            ),
        )
    return normalized


@dataclass
class DatasourceLoaderContext:
    pending_objects: list[DatasourceIR] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class _SpecBase:
    """Shared behavior for concrete datasource specification classes."""

    name: str = field(
        metadata=_description(
            "Global datasource name; letters, digits, underscores, and hyphens only."
        )
    )
    ai_context: AiContextValue | None = field(
        default=None,
        metadata=_description(
            "Optional AI-facing datasource context, via ms.ai_context(...). "
            "Put text descriptions in ai_context.business_definition."
        ),
    )
    extra: dict[str, JsonValue] | None = field(
        default=None,
        metadata=_description(
            "Rare JSON-safe ibis keyword arguments not modeled by the typed class."
        ),
    )

    backend_type: ClassVar[str]
    fields: dict[str, JsonValue] = field(init=False)
    env_refs: dict[str, str] = field(init=False)

    @property
    def ref(self) -> Ref[DatasourceKind]:
        """Typed reference to this datasource for semantic authoring."""
        return Ref.datasource(self.name)

    def __post_init__(self) -> None:
        validate_datasource_name(self.name)
        self._validate_required_string_fields()
        literal_fields, env_refs = self._split_declared_fields()
        object.__setattr__(self, "fields", literal_fields)
        object.__setattr__(self, "env_refs", env_refs)
        object.__setattr__(self, "ai_context", _build_ai_context(self.ai_context))

    def contract(self) -> AuthoringContract:
        """Return the mechanical registration contract for this declaration."""
        from marivo.datasource._capabilities.contracts import contract_for_spec

        return contract_for_spec(self.name)

    def _validate_required_string_fields(self) -> None:
        for dataclass_field in fields(self):
            if dataclass_field.name in _META_FIELDS:
                continue
            value = getattr(self, dataclass_field.name)
            if value is None:
                continue
            if _is_field_required(dataclass_field) and isinstance(value, str) and not value:
                raise DatasourceFieldInvalidError(
                    message=(
                        f"datasource {self.name!r} field {dataclass_field.name!r} "
                        "must be a non-empty string"
                    ),
                    expected="a non-empty string",
                    received=repr(value),
                    location=f"models/datasources/ entry {self.name!r} field {dataclass_field.name!r}",
                    repair=repair(
                        kind="reauthor",
                        canonical_id="duckdb",
                        action="Provide a non-empty required datasource field.",
                    ),
                )

    def _split_declared_fields(self) -> tuple[dict[str, JsonValue], dict[str, str]]:
        literal_fields: dict[str, JsonValue] = {}
        env_refs: dict[str, str] = {}
        for dataclass_field in fields(self):
            key = dataclass_field.name
            if key in _META_FIELDS:
                continue
            value = getattr(self, key)
            if value is None and _allows_none(dataclass_field.type):
                continue
            if key.endswith("_env"):
                if value is None:
                    continue
                stem = key[: -len("_env")]
                if not isinstance(value, str) or not value:
                    raise DatasourceFieldInvalidError(
                        message=f"datasource {self.name!r} field {key!r} must be a non-empty env var name",
                        expected="a non-empty environment variable name",
                        received=repr(value),
                        location=f"models/datasources/ entry {self.name!r} field {key!r}",
                        repair=repair(
                            kind="reauthor",
                            canonical_id="duckdb",
                            action="Reference a non-empty environment variable name.",
                        ),
                    )
                env_refs[stem] = value
                continue
            literal_fields[key] = _validate_jsonable_field(self.name, key, value)

        if self.extra:
            for key, value in self.extra.items():
                if key in SENSITIVE_FIELD_STEMS:
                    raise DatasourceSecretInPlaintextError(
                        message=(
                            f"datasource {self.name!r} field {key!r} is sensitive and must not "
                            "be stored as a literal"
                        ),
                        expected="an environment-variable reference for a sensitive field",
                        received=key,
                        location=f"models/datasources/ entry {self.name!r} field {key!r}",
                        repair=repair(
                            kind="environment",
                            canonical_id="duckdb",
                            action="Use the matching *_env datasource field.",
                            snippet=f'{key}_env="<ENV_VAR>"',
                        ),
                    )
                literal_fields[key] = _validate_jsonable_field(self.name, key, value)
        return literal_fields, env_refs

    def to_ir(self, *, location: DatasourceSourceLocation) -> DatasourceIR:
        return DatasourceIR(
            semantic_id=self.name,
            name=self.name,
            backend_type=self.backend_type,
            fields=dict(self.fields),
            env_refs=dict(self.env_refs),
            ai_context=cast("AiContextIR", self.ai_context),
            python_symbol=self.name,
            location=location,
        )


@dataclass(frozen=True, kw_only=True)
class DuckDBSpec(_SpecBase):
    """DuckDB datasource specification."""

    backend_type: ClassVar[str] = "duckdb"
    path: str = field(
        default=":memory:", metadata=_description("DuckDB database path; defaults to in-memory.")
    )
    read_only: bool = field(
        default=False, metadata=_description("Open the DuckDB database in read-only mode.")
    )


@dataclass(frozen=True, kw_only=True)
class TrinoSpec(_SpecBase):
    """Trino datasource specification."""

    backend_type: ClassVar[str] = "trino"
    host: str = field(metadata=_description("Trino coordinator host."))
    catalog: str = field(
        metadata=_description("Trino catalog; mapped to ibis database at connect time.")
    )
    port: int | None = field(
        default=None, metadata=_description("Trino port; ibis default is 8080.")
    )
    schema: str | None = field(default=None, metadata=_description("Optional default schema."))
    source: str | None = field(
        default=None, metadata=_description("Optional client application/source tag.")
    )
    timezone: str | None = field(
        default=None, metadata=_description("Optional engine session timezone.")
    )
    http_scheme: str | None = field(default=None, metadata=_description("Set to 'https' for TLS."))
    client_tags: tuple[str, ...] | None = field(
        default=None, metadata=_description("Optional Trino client tags.")
    )
    session_properties: dict[str, JsonValue] | None = field(
        default=None, metadata=_description("Optional Trino session properties.")
    )
    user_env: str | None = field(
        default=None, metadata=_description("Environment variable for Trino user.")
    )
    auth_env: str | None = field(
        default=None,
        metadata=_description("Environment variable for Trino auth token or password."),
    )


@dataclass(frozen=True, kw_only=True)
class MySQLSpec(_SpecBase):
    """MySQL datasource specification."""

    backend_type: ClassVar[str] = "mysql"
    host: str = field(metadata=_description("MySQL host."))
    database: str = field(metadata=_description("MySQL database name."))
    port: int | None = field(
        default=None, metadata=_description("MySQL port; ibis default is 3306.")
    )
    autocommit: bool | None = field(
        default=None, metadata=_description("Optional autocommit override.")
    )
    user_env: str | None = field(
        default=None, metadata=_description("Environment variable for MySQL user.")
    )
    password_env: str | None = field(
        default=None, metadata=_description("Environment variable for MySQL password.")
    )


@dataclass(frozen=True, kw_only=True)
class PostgresSpec(_SpecBase):
    """Postgres datasource specification."""

    backend_type: ClassVar[str] = "postgres"
    host: str = field(metadata=_description("Postgres host."))
    database: str = field(metadata=_description("Postgres database name."))
    port: int | None = field(
        default=None, metadata=_description("Postgres port; ibis default is 5432.")
    )
    schema: str | None = field(default=None, metadata=_description("Optional default schema."))
    autocommit: bool | None = field(
        default=None, metadata=_description("Optional autocommit override.")
    )
    user_env: str | None = field(
        default=None, metadata=_description("Environment variable for Postgres user.")
    )
    password_env: str | None = field(
        default=None, metadata=_description("Environment variable for Postgres password.")
    )


@dataclass(frozen=True, kw_only=True)
class ClickHouseSpec(_SpecBase):
    """ClickHouse datasource specification."""

    backend_type: ClassVar[str] = "clickhouse"
    host: str = field(metadata=_description("ClickHouse host."))
    port: int | None = field(
        default=None,
        metadata=_description("ClickHouse port; native default is 9000, secure default is 9440."),
    )
    database: str | None = field(
        default=None, metadata=_description("ClickHouse database; ibis default is 'default'.")
    )
    secure: bool | None = field(default=None, metadata=_description("Enable TLS for ClickHouse."))
    settings: dict[str, JsonValue] | None = field(
        default=None, metadata=_description("Optional ClickHouse settings map.")
    )
    user_env: str | None = field(
        default=None, metadata=_description("Environment variable for ClickHouse user.")
    )
    password_env: str | None = field(
        default=None, metadata=_description("Environment variable for ClickHouse password.")
    )


DatasourceSpec: TypeAlias = DuckDBSpec | TrinoSpec | MySQLSpec | PostgresSpec | ClickHouseSpec  # noqa: UP040


def _storage_name(value: str | Ref[DatasourceKind]) -> str:
    """Return the private project storage key for a validated datasource identity."""
    if type(value) is Ref and value.kind is SemanticKind.DATASOURCE:
        return value.path
    if type(value) is str:
        validate_datasource_name(value)
        return value
    raise TypeError(
        f"datasource storage identity must be str or Ref[datasource], got {type(value).__name__}"
    )


def _require_datasource_ref(
    value: object,
    *,
    argument: str = "datasource",
) -> Ref[DatasourceKind]:
    if type(value) is Ref and value.kind is SemanticKind.DATASOURCE:
        return value
    raise TypeError(
        f"{argument} must be Ref[datasource] from a datasource spec's .ref or "
        "Ref.datasource('warehouse'). Do not pass a bare string."
    )


_DATASOURCE_CTX: ContextVar[DatasourceLoaderContext | None] = ContextVar(
    "_DATASOURCE_CTX",
    default=None,
)


_AUTHORING_FILE = __file__


def _caller_location() -> DatasourceSourceLocation:
    try:
        frame = inspect.currentframe()
        if frame is not None:
            caller_frame = frame.f_back
            while caller_frame is not None:
                if (
                    caller_frame.f_code.co_filename != _AUTHORING_FILE
                    and caller_frame.f_globals.get("__name__") != "marivo.telemetry"
                ):
                    return DatasourceSourceLocation(
                        file=caller_frame.f_code.co_filename,
                        line=caller_frame.f_lineno,
                    )
                caller_frame = caller_frame.f_back
    except AttributeError:
        pass
    return DatasourceSourceLocation(file="<unknown>", line=0)


def _current_ctx() -> DatasourceLoaderContext | None:
    return _DATASOURCE_CTX.get()


def _require_ctx() -> DatasourceLoaderContext:
    ctx = _DATASOURCE_CTX.get()
    if ctx is None:
        raise DatasourceFieldInvalidError(
            message="md.datasource can only be called while loading models/datasources/ files",
            expected="a models/datasources loader context",
            received="outside loader",
            location="md.datasource",
            repair=repair(
                kind="reauthor",
                canonical_id="load",
                action="Declare datasources from models/datasources/ files.",
            ),
        )
    return ctx


def validate_datasource_name(name: Any) -> None:
    if not isinstance(name, str) or not name:
        raise DatasourceFieldInvalidError(
            message="datasource name must be a non-empty string",
            expected="a non-empty datasource storage name",
            received=repr(name),
            location="datasource name",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Provide a non-empty datasource name.",
            ),
        )
    try:
        _validate_segment(name, role="datasource name")
    except ValueError:
        suggested = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        if not suggested:
            suggested = "datasource"
        if not suggested[0].isalpha():
            suggested = f"ds_{suggested}"
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} is not a valid datasource name",
            expected="[a-z][a-z0-9_]*",
            received=name,
            location="datasource name",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action=(
                    f"Rename the datasource to {suggested!r}. Its identity becomes "
                    f"datasource:{suggested}. Existing ~/.marivo/secrets.toml entries are "
                    "not renamed; cached credentials are reused only when the conventional "
                    "environment-variable name remains unchanged."
                ),
            ),
        ) from None


def _ir_from_spec(spec: DatasourceSpec, *, location: DatasourceSourceLocation) -> DatasourceIR:
    return spec.to_ir(location=location)


def _declare(spec: DatasourceSpec) -> None:
    """Internal: append a spec's IR to the current loader context."""
    ctx = _require_ctx()
    ctx.pending_objects.append(_ir_from_spec(spec, location=_caller_location()))


def _declare_if_loading(spec: DatasourceSpec) -> None:
    ctx = _current_ctx()
    if ctx is not None:
        ctx.pending_objects.append(_ir_from_spec(spec, location=_caller_location()))


def duckdb(
    name: str,
    *,
    path: str = ":memory:",
    read_only: bool = False,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> DuckDBSpec:
    """Declare a DuckDB datasource.

    Args:
        name: Global datasource name; letters, digits, underscores, and hyphens only.
        path: DuckDB database path; defaults to in-memory.
        read_only: Open the DuckDB database in read-only mode.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.

    Returns:
        ``DuckDBSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.duckdb(name="warehouse", path=":memory:")
        >>> md.register(spec)
        >>> md.test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders")).show()
        >>> md.inspect(spec.ref, md.parquet("data/orders/*.parquet")).show()

        ``md.table(...)`` selects an internal table or view in the DuckDB
        datasource. ``md.parquet(...)``, ``md.csv(...)``, and ``md.json(...)``
        build DuckDB file source descriptors; they are not datasource
        declarations.

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
    """
    spec = DuckDBSpec(
        name=name,
        path=path,
        read_only=read_only,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


def trino(
    name: str,
    *,
    host: str,
    catalog: str,
    port: int | None = None,
    schema: str | None = None,
    source: str | None = None,
    timezone: str | None = None,
    http_scheme: str | None = None,
    client_tags: tuple[str, ...] | None = None,
    session_properties: dict[str, JsonValue] | None = None,
    user_env: str | None = None,
    auth_env: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> TrinoSpec:
    """Declare a Trino datasource.

    Args:
        name: Global datasource name; letters, digits, underscores, and hyphens only.
        host: Trino coordinator host.
        catalog: Trino catalog; mapped to ibis database at connect time.
        port: Trino port; ibis default is 8080.
        schema: Optional default schema.
        source: Optional client application/source tag.
        timezone: Optional engine session timezone.
        http_scheme: Set to 'https' for TLS.
        client_tags: Optional Trino client tags.
        session_properties: Optional Trino session properties.
        user_env: Environment variable for Trino user.
        auth_env: Environment variable for Trino auth token or password.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.

    Returns:
        ``TrinoSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.trino(
        ...     name="warehouse",
        ...     host="trino.example",
        ...     catalog="hive",
        ...     schema="analytics",
        ...     user_env="WAREHOUSE_USER",
        ...     auth_env="WAREHOUSE_AUTH",
        ... )
        >>> md.register(spec)
        >>> md.test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``*_env`` references, not plaintext literals.
    """
    spec = TrinoSpec(
        name=name,
        host=host,
        catalog=catalog,
        port=port,
        schema=schema,
        source=source,
        timezone=timezone,
        http_scheme=http_scheme,
        client_tags=client_tags,
        session_properties=session_properties,
        user_env=user_env,
        auth_env=auth_env,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


def mysql(
    name: str,
    *,
    host: str,
    database: str,
    port: int | None = None,
    autocommit: bool | None = None,
    user_env: str | None = None,
    password_env: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> MySQLSpec:
    """Declare a MySQL datasource.

    Args:
        name: Global datasource name; letters, digits, underscores, and hyphens only.
        host: MySQL host.
        database: MySQL database name.
        port: MySQL port; ibis default is 3306.
        autocommit: Optional autocommit override.
        user_env: Environment variable for MySQL user.
        password_env: Environment variable for MySQL password.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.

    Returns:
        ``MySQLSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.mysql(
        ...     name="oltp",
        ...     host="mysql.example",
        ...     database="app",
        ...     user_env="WAREHOUSE_USER",
        ...     password_env="WAREHOUSE_PASSWORD",
        ... )
        >>> md.register(spec)
        >>> md.test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="app")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``*_env`` references, not plaintext literals.
    """
    spec = MySQLSpec(
        name=name,
        host=host,
        database=database,
        port=port,
        autocommit=autocommit,
        user_env=user_env,
        password_env=password_env,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


def postgres(
    name: str,
    *,
    host: str,
    database: str,
    port: int | None = None,
    schema: str | None = None,
    autocommit: bool | None = None,
    user_env: str | None = None,
    password_env: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> PostgresSpec:
    """Declare a Postgres datasource.

    Args:
        name: Global datasource name; letters, digits, underscores, and hyphens only.
        host: Postgres host.
        database: Postgres database name.
        port: Postgres port; ibis default is 5432.
        schema: Optional default schema.
        autocommit: Optional autocommit override.
        user_env: Environment variable for Postgres user.
        password_env: Environment variable for Postgres password.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.

    Returns:
        ``PostgresSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.postgres(
        ...     name="oltp",
        ...     host="pg.example",
        ...     database="app",
        ...     user_env="WAREHOUSE_USER",
        ...     password_env="WAREHOUSE_PASSWORD",
        ... )
        >>> md.register(spec)
        >>> md.test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="app")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``*_env`` references, not plaintext literals.
    """
    spec = PostgresSpec(
        name=name,
        host=host,
        database=database,
        port=port,
        schema=schema,
        autocommit=autocommit,
        user_env=user_env,
        password_env=password_env,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


def clickhouse(
    name: str,
    *,
    host: str,
    port: int | None = None,
    database: str | None = None,
    secure: bool | None = None,
    settings: dict[str, JsonValue] | None = None,
    user_env: str | None = None,
    password_env: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> ClickHouseSpec:
    """Declare a ClickHouse datasource.

    Args:
        name: Global datasource name; letters, digits, underscores, and hyphens only.
        host: ClickHouse host.
        port: ClickHouse port; native default is 9000, secure default is 9440.
        database: ClickHouse database; ibis default is 'default'.
        secure: Enable TLS for ClickHouse.
        settings: Optional ClickHouse settings map.
        user_env: Environment variable for ClickHouse user.
        password_env: Environment variable for ClickHouse password.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.
            ClickHouse connections default ``autogenerate_session_id`` to ``False``
            for analysis stability; pass ``{"autogenerate_session_id": True}``
            only when the datasource needs ClickHouse session state such as
            temporary tables.

    Returns:
        ``ClickHouseSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.clickhouse(
        ...     name="warehouse",
        ...     host="clickhouse.example",
        ...     database="analytics",
        ...     user_env="WAREHOUSE_USER",
        ...     password_env="WAREHOUSE_PASSWORD",
        ... )
        >>> md.register(spec)
        >>> md.test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="analytics")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``*_env`` references, not plaintext literals.
    """
    spec = ClickHouseSpec(
        name=name,
        host=host,
        port=port,
        database=database,
        secure=secure,
        settings=settings,
        user_env=user_env,
        password_env=password_env,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec
