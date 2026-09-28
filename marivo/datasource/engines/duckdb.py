"""DuckDB engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Timer

from ibis.backends import BaseBackend

from marivo.datasource.capabilities import (
    ProviderStatement,
    register_provider_statements,
    url_is_in_http_scope,
)
from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    QuantileCapability,
    default_table_name_parts,
    identity_str,
    schema_only_metadata_inspect,
)
from marivo.datasource.errors import DatasourceFieldInvalidError, repair

register_provider_statements(
    "duckdb",
    {
        "http_secret_bearer": ProviderStatement(
            statement_id="duckdb.http_secret_bearer",
            template=(
                "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, BEARER_TOKEN ?, SCOPE ?)"
            ),
            parameterized=True,
        ),
        "http_secret_headers": ProviderStatement(
            statement_id="duckdb.http_secret_headers",
            template=(
                "CREATE OR REPLACE SECRET marivo_http_auth "
                "(TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)"
            ),
            parameterized=True,
        ),
    },
)


@dataclass(frozen=True)
class DuckDbHttpCredentials:
    """Scoped HTTP credentials installed on one DuckDB connection."""

    scope: str
    headers: tuple[tuple[str, str], ...]

    def headers_for(self, url: str) -> dict[str, str]:
        if not url_is_in_http_scope(url, self.scope):
            return {}
        return dict(self.headers)


def http_credentials(
    backend: BaseBackend,
    *,
    scope: object,
    bearer_token: object,
    headers: object,
) -> DuckDbHttpCredentials | None:
    """Install the declared scoped HTTP secret and return in-memory headers.

    The secret values are passed as statement parameters, so they never appear
    in rendered SQL text or the capability submission log.
    """
    if bearer_token is None and not headers:
        return None
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth requires a backend with raw_sql support",
            expected="a DuckDB backend",
            received=type(backend).__name__,
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reconnect",
                canonical_id="test",
                action="Reconnect using the declared DuckDB datasource.",
            ),
        )
    if not isinstance(scope, str):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth scope was not resolved",
            expected="an HTTP(S) scope string",
            received=repr(scope),
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Declare an explicit HTTP(S) scope on the DuckDB datasource.",
            ),
        )
    if isinstance(bearer_token, str):
        raw_sql(
            "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, BEARER_TOKEN ?, SCOPE ?)",
            parameters=[bearer_token, scope],
        )
        return DuckDbHttpCredentials(
            scope=scope,
            headers=(("Authorization", f"Bearer {bearer_token}"),),
        )
    if (
        isinstance(headers, Mapping)
        and headers
        and all(isinstance(name, str) and isinstance(value, str) for name, value in headers.items())
    ):
        raw_sql(
            "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)",
            parameters=[dict(headers), scope],
        )
        return DuckDbHttpCredentials(scope=scope, headers=tuple(headers.items()))
    raise DatasourceFieldInvalidError(
        message="DuckDB custom HTTP authentication was not fully resolved",
        expected="environment-sourced custom HTTP headers",
        received="incomplete HTTP authentication fields",
        location="DuckDB HTTP auth",
        repair=repair(
            kind="reauthor",
            canonical_id="duckdb",
            action="Declare one complete environment-backed HTTP auth mode.",
        ),
    )


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    path = kwargs.get("path", ":memory:")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs.pop("path", None)
    connect_kwargs["database"] = path
    connect_kwargs["threads"] = 1
    connect_kwargs["TimeZone"] = "UTC"
    if "read_only" in connect_kwargs:
        connect_kwargs["read_only"] = bool(connect_kwargs["read_only"])
    backend = ibis.duckdb.connect(**connect_kwargs)
    backend._marivo_timezone_name = "UTC"
    return backend


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    connection = getattr(backend, "con", None)
    interrupt = getattr(connection, "interrupt", None)
    if not callable(interrupt):
        raise RuntimeError("duckdb backend does not expose connection.interrupt()")
    timer = Timer(timeout_seconds, interrupt)
    try:
        timer.start()
        yield
    finally:
        timer.cancel()


PROFILE = EngineProfile(
    name="duckdb",
    aliases=(),
    authoring_func="duckdb",
    required_modules=("ibis.backends.duckdb",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(inspect_table=schema_only_metadata_inspect),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=False,
    ),
    translate_strptime_format=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="exact", method="linear_interpolation"),
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
    http_credentials=http_credentials,
)
