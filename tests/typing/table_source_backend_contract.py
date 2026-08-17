"""Static backend protocol coverage for typed table-source relations."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir
from typing_extensions import assert_type

from marivo.datasource.table_source import (
    TableLookupBackend,
    TableSqlBackend,
    supports_table_lookup,
    supports_table_sql,
)
from marivo.semantic.typing import IbisBackend


class CompleteBackend:
    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ir.Table:
        return ibis.table({"value": "string"}, name=name)

    def sql(
        self,
        query: str,
        /,
        *,
        schema: Mapping[str, str],
    ) -> ir.Table:
        return ibis.table(schema, name="projected")

    def read_parquet(self, path: str, /, **options: object) -> ir.Table:
        return ibis.table({"value": "string"}, name="parquet")

    def read_csv(self, path: str, /, **options: object) -> ir.Table:
        return ibis.table({"value": "string"}, name="csv")

    def read_json(self, path: str, /, **options: object) -> ir.Table:
        return ibis.table({"value": "string"}, name="json")


backend: IbisBackend = CompleteBackend()


def narrow_lookup(value: object) -> None:
    if supports_table_lookup(value):
        assert_type(value, TableLookupBackend)
        assert_type(value.table("events"), ir.Table)


def narrow_sql(value: object) -> None:
    if supports_table_sql(value):
        assert_type(value, TableSqlBackend)
        assert_type(value.sql("SELECT 1", schema={"value": "int64"}), ir.Table)
