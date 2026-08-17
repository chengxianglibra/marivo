"""Internal Ibis relation construction for physical table sources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeGuard

import ibis.expr.types as ir

from marivo.datasource.engines import EngineProfile, profile_for_backend, quote_identifier
from marivo.datasource.errors import (
    DatasourceObservedEffects,
    DatasourceSourceCapabilityError,
    repair,
)
from marivo.datasource.ir import TableSourceIR


class TableLookupBackend(Protocol):
    """Backend capability required by catalog-backed table sources."""

    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ir.Table: ...


class TableSqlBackend(Protocol):
    """Backend capability required by typed projected table sources."""

    def sql(
        self,
        query: str,
        /,
        *,
        schema: Mapping[str, str],
    ) -> ir.Table: ...


def supports_table_lookup(value: object) -> TypeGuard[TableLookupBackend]:
    """Return whether *value* exposes callable table lookup."""
    return callable(getattr(value, "table", None))


def supports_table_sql(value: object) -> TypeGuard[TableSqlBackend]:
    """Return whether *value* exposes callable schema-aware SQL relations."""
    return callable(getattr(value, "sql", None))


def _missing_capability(
    backend: object,
    source: TableSourceIR,
    *,
    capability: str,
) -> DatasourceSourceCapabilityError:
    projected = bool(source.columns)
    if projected:
        expected = "a datasource backend exposing callable sql(query, schema=...)"
        action = (
            "Use an unprojected table, a datasource backend with schema-aware sql(), "
            "or a database view."
        )
    else:
        expected = "a datasource backend exposing callable table(name, database=...)"
        action = "Use a datasource backend that supports physical table lookup."
    return DatasourceSourceCapabilityError(
        message=f"datasource backend cannot materialize this {source.kind} source",
        expected=expected,
        received=f"{type(backend).__name__} without callable {capability}()",
        location=f"table source {source.table!r}",
        effect_observed=DatasourceObservedEffects(query_executed=False),
        repair=repair(
            kind="configure",
            canonical_id="table",
            action=action,
            preserves_evidence=False,
        ),
    )


def _qualified_table(source: TableSourceIR, *, profile: EngineProfile) -> str:
    database = source.database
    parts: tuple[str, ...]
    if database is None:
        parts = (source.table,)
    elif isinstance(database, tuple):
        parts = (*database, source.table)
    else:
        parts = (database, source.table)
    return ".".join(quote_identifier(part, profile) for part in parts)


def table_source_expression(backend: object, source: TableSourceIR) -> ir.Table:
    """Construct the one Ibis relation represented by a physical table source."""
    if not source.columns:
        if not supports_table_lookup(backend):
            raise _missing_capability(backend, source, capability="table")
        if source.database is None:
            return backend.table(source.table)
        return backend.table(source.table, database=source.database)

    profile = profile_for_backend(backend)
    selections = ", ".join(
        (f"{quote_identifier(binding.source, profile)} AS {quote_identifier(output_name, profile)}")
        for output_name, binding in source.columns
    )
    query = f"SELECT {selections} FROM {_qualified_table(source, profile=profile)}"
    declared_schema = {output_name: binding.data_type for output_name, binding in source.columns}

    if not supports_table_sql(backend):
        raise _missing_capability(backend, source, capability="sql")
    return backend.sql(query, schema=declared_schema)
