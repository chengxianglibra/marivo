"""FDN (fully-distinguished name) validation for datasource table names."""

from __future__ import annotations

from typing import Any, Final

from marivo.datasource.errors import DatasourceFieldInvalidError

__all__ = [
    "FDN_FORMAT_HINT",
    "FDN_MIN_DOTS",
    "ValidatingBackend",
    "validate_fdn",
]

FDN_MIN_DOTS: Final[dict[str, int]] = {
    "trino": 2,
    "mysql": 1,
    "postgres": 1,
    "clickhouse": 1,
}

FDN_FORMAT_HINT: Final[dict[str, str]] = {
    "trino": "catalog.schema.table (e.g. 'hive.sales.orders')",
    "mysql": "database.table (e.g. 'sales_db.orders')",
    "postgres": "database.table (e.g. 'sales_db.orders')",
    "clickhouse": "database.table (e.g. 'analytics_db.orders')",
}


def validate_fdn(name: str, backend_type: str, datasource_name: str) -> None:
    """Raise DatasourceFieldInvalidError if *name* is not a fully-distinguished name."""
    min_dots = FDN_MIN_DOTS.get(backend_type)
    if min_dots is None:
        return
    if not isinstance(name, str) or not name or name.count(".") < min_dots:
        hint = FDN_FORMAT_HINT.get(backend_type, "")
        raise DatasourceFieldInvalidError(
            message=(
                f"datasource {datasource_name!r} table name {name!r} is not a "
                "fully-distinguished name"
            ),
            details={
                "datasource": datasource_name,
                "field": "table_name",
                "reason": (
                    f"for backend_type={backend_type!r}, use {hint} format"
                    if hint
                    else f"for backend_type={backend_type!r}, use a fully-distinguished name"
                ),
            },
        )


class ValidatingBackend:
    """Wraps an ibis backend and enforces FDN rules on ``.table(name)`` calls."""

    __slots__ = ("_backend", "_backend_type", "_datasource_name")

    def __init__(self, backend: Any, backend_type: str, datasource_name: str) -> None:
        self._backend = backend
        self._backend_type = backend_type
        self._datasource_name = datasource_name

    def table(self, name: str, /) -> Any:
        validate_fdn(name, self._backend_type, self._datasource_name)
        return self._backend.table(name)

    def sql(self, query: str, /) -> Any:
        return self._backend.sql(query)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)
