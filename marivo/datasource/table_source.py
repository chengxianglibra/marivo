"""Internal Ibis relation construction for physical table sources."""

from __future__ import annotations

from typing import Protocol, TypeGuard

import ibis.expr.types as ir

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


def supports_table_lookup(value: object) -> TypeGuard[TableLookupBackend]:
    """Return whether *value* exposes callable table lookup."""
    return callable(getattr(value, "table", None))


def _missing_capability(
    backend: object,
    source: TableSourceIR,
    *,
    capability: str,
) -> DatasourceSourceCapabilityError:
    return DatasourceSourceCapabilityError(
        message=f"datasource backend cannot materialize this {source.kind} source",
        expected="a datasource backend exposing callable table(name, database=...)",
        received=f"{type(backend).__name__} without callable {capability}()",
        location=f"table source {source.table!r}",
        effect_observed=DatasourceObservedEffects(query_executed=False),
        repair=repair(
            kind="configure",
            canonical_id="table",
            action="Use a datasource backend that supports physical table lookup.",
            preserves_evidence=False,
        ),
    )


def table_source_expression(backend: object, source: TableSourceIR) -> ir.Table:
    """Construct the one Ibis relation represented by a physical table source."""
    if not supports_table_lookup(backend):
        raise _missing_capability(backend, source, capability="table")
    if source.database is None:
        table = backend.table(source.table)
    else:
        table = backend.table(source.table, database=source.database)
    if not source.columns:
        return table
    return table.select(
        *(table[source_name].name(output_name) for output_name, source_name in source.columns)
    )
