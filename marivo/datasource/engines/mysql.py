"""MySQL engine profile."""

from __future__ import annotations

from collections.abc import Mapping
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
from marivo.datasource.strptime import python_to_mysql_strptime


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis.expr.schema as sch
    import pandas as pd
    from ibis.backends.mysql import Backend
    from ibis.backends.mysql.converter import MySQLPandasData

    from marivo.datasource.engines.scalar_decode import ScalarCursor, checked_dataframe

    # Ibis optional backend classes do not ship typing metadata.
    class CheckedBackend(Backend):  # type: ignore[misc]
        def _fetch_from_cursor(self, cursor: ScalarCursor, schema: sch.Schema) -> pd.DataFrame:
            return checked_dataframe(cursor, schema, MySQLPandasData.convert_table)

    host = require_field(name, kwargs, "host", help_target="mysql")
    database = require_field(name, kwargs, "database", help_target="mysql")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return CheckedBackend().connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    schema_name = (
        str(database) if database is not None and not isinstance(database, tuple) else None
    )
    if schema_name is None:
        schema_name = str(request.datasource_ir.fields["database"])
    return (schema_name, request.source.table)


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify MySQL table-metadata permission denial from native errno."""
    for candidate in structured_exception_chain(exc):
        args = getattr(candidate, "args", ())
        if isinstance(args, tuple) and args and args[0] in {1142, 1143}:
            return "metadata_unavailable"
    return None


PROFILE = EngineProfile(
    name="mysql",
    aliases=(),
    authoring_func="mysql",
    required_modules=("ibis.backends.mysql",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    identifier_quote="`",
    table_name_parts=table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(
        inspect_table=schema_only_metadata_inspect,
        classify_table_resolution_failure=classify_table_resolution_failure,
    ),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=False,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=python_to_mysql_strptime,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=None,
)
