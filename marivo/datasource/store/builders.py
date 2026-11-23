"""Authoring surface for project-level datasources."""

from __future__ import annotations

from collections.abc import Sequence

from marivo.datasource.credentials.inputs import (
    DuckDBSecret,
    EnvRef,
)
from marivo.datasource.store.authoring import (
    ClickHouseSpec,
    DuckDBSpec,
    JsonValue,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
    _declare_if_loading,
)
from marivo.datasource.typing import AiContextValue
from marivo.telemetry import traced


@traced(surface="datasource", operation="duckdb")
def duckdb(
    name: str,
    *,
    path: str = ":memory:",
    read_only: bool = False,
    secrets: Sequence[DuckDBSecret] = (),
    wall_clock_timezone: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> DuckDBSpec:
    """Declare a DuckDB datasource.

    Args:
        name: Global datasource name; must match ``[a-z][a-z0-9_]*``.
        path: DuckDB database path or :memory:.
        read_only: Open a DuckDB database file in native read-only mode.
        secrets: Scoped md.s3_secret, md.http_bearer_secret or md.http_headers_secret
            values; credentials must use md.env references.
        wall_clock_timezone: Optional source timezone for undeclared naive timestamps,
            encoded local times and business dates. Accepts IANA names or fixed offsets;
            physical instants and connection settings are unaffected.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: Rare JSON-safe ibis keyword arguments not modeled by the typed class.

    Returns:
        ``DuckDBSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.duckdb(name="warehouse", path=":memory:")
        >>> md.register(spec)
        >>> md.load().test(spec.ref).show()
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
        secrets=secrets,
        wall_clock_timezone=wall_clock_timezone,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


@traced(surface="datasource", operation="trino")
def trino(
    name: str,
    *,
    host: str,
    catalog: str,
    user: EnvRef,
    port: int | None = None,
    schema: str | None = None,
    source: str | None = None,
    http_scheme: str | None = None,
    client_tags: tuple[str, ...] | None = None,
    session_properties: dict[str, JsonValue] | None = None,
    auth: EnvRef | None = None,
    wall_clock_timezone: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> TrinoSpec:
    """Declare a Trino datasource.

    Args:
        name: Global datasource name; must match ``[a-z][a-z0-9_]*``.
        host: Trino coordinator host.
        catalog: Trino catalog; mapped to ibis database at connect time.
        user: Required EnvRef from md.env for the Trino user.
        port: Trino port; ibis default is 8080.
        schema: Optional default schema.
        source: Optional client application/source tag.
        http_scheme: Set to 'https' for TLS.
        client_tags: Optional Trino client tags.
        session_properties: Optional Trino session properties.
        auth: Optional EnvRef from md.env for a Trino auth token
            or password. When omitted, Marivo passes no auth value.
        wall_clock_timezone: Optional source timezone for undeclared naive timestamps,
            encoded local times and business dates. Accepts IANA names or fixed offsets;
            physical instants and connection settings are unaffected.
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
        ...     user=md.env("WAREHOUSE_USER"),
        ...     auth=md.env("WAREHOUSE_AUTH"),
        ... )
        >>> md.register(spec)
        >>> md.load().test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``md.env(...)`` references, not plaintext literals.
        Marivo never infers credential environment-variable names.
        Trino uses native autocommit. Omit ``isolation_level`` from ``extra``.
        Exact ``median()`` and ``quantile(q)`` fail source compilation because
        Trino has no qualified native exact quantile algorithm. Explicit ``approx_median()`` and
        ``approx_quantile(q)`` use native ``approx_percentile``. Analysis
        preparation qualifies the actual input types; semantic readiness does
        not qualify execution. Exact and approximate algorithms are never
        substituted for one another.
    """
    spec = TrinoSpec(
        name=name,
        host=host,
        catalog=catalog,
        user=user,
        port=port,
        schema=schema,
        source=source,
        http_scheme=http_scheme,
        client_tags=client_tags,
        session_properties=session_properties,
        auth=auth,
        wall_clock_timezone=wall_clock_timezone,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


@traced(surface="datasource", operation="mysql")
def mysql(
    name: str,
    *,
    host: str,
    database: str,
    port: int | None = None,
    autocommit: bool | None = None,
    user: EnvRef | None = None,
    password: EnvRef | None = None,
    wall_clock_timezone: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> MySQLSpec:
    """Declare a MySQL datasource.

    Args:
        name: Global datasource name; must match ``[a-z][a-z0-9_]*``.
        host: MySQL host.
        database: MySQL database name.
        port: MySQL port; default is 3306.
        autocommit: Use True or omit this option to use autocommit.
        user: EnvRef from md.env for MySQL user.
        password: EnvRef from md.env for MySQL password.
        wall_clock_timezone: Optional source timezone for undeclared naive timestamps,
            encoded local times and business dates. Accepts IANA names or fixed offsets;
            physical instants and connection settings are unaffected.
        ai_context: Optional AI-facing context, via ``ms.ai_context(...)``.
            Put text descriptions in ``business_definition``.
        extra: JSON-safe PyMySQL connection options not modeled by the typed class.

    Returns:
        ``MySQLSpec`` usable with ``md.register(...)`` or ``.ref``.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.mysql(
        ...     name="oltp",
        ...     host="mysql.example",
        ...     database="app",
        ...     user=md.env("WAREHOUSE_USER"),
        ...     password=md.env("WAREHOUSE_PASSWORD"),
        ... )
        >>> md.register(spec)
        >>> md.load().test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="app")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``md.env(...)`` references, not plaintext literals.
        Install ``marivo[mysql]`` for PyMySQL and RSA authentication support;
        no local MySQL client C library is required. mysqlclient-specific
        ``ssl_mode``, ``auth_plugin`` and protocol compression are unsupported.
    """
    spec = MySQLSpec(
        name=name,
        host=host,
        database=database,
        port=port,
        autocommit=autocommit,
        user=user,
        password=password,
        wall_clock_timezone=wall_clock_timezone,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


@traced(surface="datasource", operation="postgres")
def postgres(
    name: str,
    *,
    host: str,
    database: str,
    port: int | None = None,
    schema: str | None = None,
    autocommit: bool | None = None,
    user: EnvRef | None = None,
    password: EnvRef | None = None,
    wall_clock_timezone: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> PostgresSpec:
    """Declare a Postgres datasource.

    Args:
        name: Global datasource name; must match ``[a-z][a-z0-9_]*``.
        host: Postgres host.
        database: Postgres database name.
        port: Postgres port; ibis default is 5432.
        schema: Optional default schema.
        autocommit: Use True or omit this option to use autocommit.
        user: EnvRef from md.env for Postgres user.
        password: EnvRef from md.env for Postgres password.
        wall_clock_timezone: Optional source timezone for undeclared naive timestamps,
            encoded local times and business dates. Accepts IANA names or fixed offsets;
            physical instants and connection settings are unaffected.
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
        ...     user=md.env("WAREHOUSE_USER"),
        ...     password=md.env("WAREHOUSE_PASSWORD"),
        ... )
        >>> md.register(spec)
        >>> md.load().test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="app")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``md.env(...)`` references, not plaintext literals.
    """
    spec = PostgresSpec(
        name=name,
        host=host,
        database=database,
        port=port,
        schema=schema,
        autocommit=autocommit,
        user=user,
        password=password,
        wall_clock_timezone=wall_clock_timezone,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec


@traced(surface="datasource", operation="clickhouse")
def clickhouse(
    name: str,
    *,
    host: str,
    port: int | None = None,
    database: str | None = None,
    secure: bool | None = None,
    settings: dict[str, JsonValue] | None = None,
    user: EnvRef | None = None,
    password: EnvRef | None = None,
    wall_clock_timezone: str | None = None,
    ai_context: AiContextValue | None = None,
    extra: dict[str, JsonValue] | None = None,
) -> ClickHouseSpec:
    """Declare a ClickHouse datasource.

    Args:
        name: Global datasource name; must match ``[a-z][a-z0-9_]*``.
        host: ClickHouse host.
        port: ClickHouse port; native default is 9000, secure default is 9440.
        database: ClickHouse database; ibis default is 'default'.
        secure: Enable TLS for ClickHouse.
        settings: Optional ClickHouse settings map.
        user: EnvRef from md.env for ClickHouse user.
        password: EnvRef from md.env for ClickHouse password.
        wall_clock_timezone: Optional source timezone for undeclared naive timestamps,
            encoded local times and business dates. Accepts IANA names or fixed offsets;
            physical instants and connection settings are unaffected.
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
        ...     user=md.env("WAREHOUSE_USER"),
        ...     password=md.env("WAREHOUSE_PASSWORD"),
        ... )
        >>> md.register(spec)
        >>> md.load().test(spec.ref).show()
        >>> md.inspect(spec.ref, md.table("orders", database="analytics")).show()

    Constraints:
        When called while loading a datasource file, the spec is automatically
        declared for that project.
        Sensitive fields must use ``md.env(...)`` references, not plaintext literals.
    """
    spec = ClickHouseSpec(
        name=name,
        host=host,
        port=port,
        database=database,
        secure=secure,
        settings=settings,
        user=user,
        password=password,
        wall_clock_timezone=wall_clock_timezone,
        ai_context=ai_context,
        extra=extra,
    )
    _declare_if_loading(spec)
    return spec
