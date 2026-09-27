"""Postgres engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    TableRefRequest,
    identity_read_only_kwargs,
    require_field,
    schema_only_metadata_inspect,
    structured_exception_chain,
)
from marivo.datasource.strptime import python_to_postgres_strptime


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host", help_target="postgres")
    database = require_field(name, kwargs, "database", help_target="postgres")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return ibis.postgres.connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    schema_name = (
        str(database) if database is not None and not isinstance(database, tuple) else None
    )
    if schema_name is None:
        schema_value = request.datasource_ir.fields.get("schema")
        schema_name = str(schema_value) if schema_value is not None else None
    return (request.source.table,) if schema_name is None else (schema_name, request.source.table)


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify PostgreSQL metadata permission denial from SQLSTATE."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "sqlstate", None) == "42501":
            return "metadata_unavailable"
    return None


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    if getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds:
        raise RuntimeError("postgres terminal connection has no configured statement timeout")
    connection = backend.con
    if connection.read_only is not True or connection.autocommit:
        raise RuntimeError("postgres terminal connection is not in read-only transaction mode")
    try:
        yield
    finally:
        connection.rollback()


PROFILE = EngineProfile(
    name="postgres",
    aliases=("postgresql", "redshift"),
    authoring_func="postgres",
    required_modules=("ibis.backends.postgres",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(
        inspect_table=schema_only_metadata_inspect,
        classify_table_resolution_failure=classify_table_resolution_failure,
    ),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=python_to_postgres_strptime,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
