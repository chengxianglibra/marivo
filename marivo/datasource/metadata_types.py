"""Physical metadata records and pure schema projections for datasource providers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import ibis.expr.schema as sch
from typing_extensions import override

from marivo._kernel.render import Card, RenderableResult


class SchemaRelation(Protocol):
    def schema(self) -> sch.Schema: ...


MetadataWarningKind = Literal[
    "comments_unavailable",
    "table_comments_unavailable",
    "column_comments_unavailable",
    "nullable_unavailable",
    "view_unavailable",
    "physical_profile_unavailable",
    "partitions_unavailable",
    "primary_keys_unavailable",
    "metadata_query_failed",
    "schema_only_fallback",
    "base_table_metadata_unavailable",
    "projected_column_unverified",
    "projected_partition_unavailable",
    "projected_constraint_incomplete",
    "partition_state_unknown",
    "projectable_columns_unavailable",
    "projectable_column_type_conflict",
    "projectable_column_type_unparsed",
]

RowCountKind = Literal["estimate", "metadata", "unknown"]
SizeKind = Literal["on_disk", "data_plus_index", "table_stats", "unknown"]


@dataclass(frozen=True)
class MetadataWarning:
    kind: MetadataWarningKind
    message: str
    columns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "columns": list(self.columns),
        }


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    type: str
    nullable: bool | None
    comment: str | None
    ordinal_position: int | None
    _type_is_ibis: bool = field(default=False, compare=False, repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "comment": self.comment,
            "ordinal_position": self.ordinal_position,
        }


def uses_ibis_column_type(column: ColumnMetadata) -> bool:
    """Whether the stored column spelling came from Ibis rather than a provider."""
    return column._type_is_ibis  # noqa: SLF001 - Preserve the physical metadata provenance.


@dataclass(frozen=True)
class PartitionMetadata:
    name: str
    type: str | None = None
    transform: str | None = None
    comment: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "transform": self.transform,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class UniqueConstraintMetadata:
    """A declared primary-key or unique constraint on a table.

    Attributes:
        name: Constraint name if the backend exposes one, else ``None``.
        columns: Column names participating in the constraint, in declared order.
        kind: ``"primary"`` for primary-key constraints, ``"unique"`` for unique
            constraints or unique indexes.
    """

    name: str | None
    columns: tuple[str, ...]
    kind: Literal["primary", "unique"]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "kind": self.kind,
        }


@dataclass(frozen=True)
class TablePhysicalProfile:
    row_count: int | None
    row_count_kind: RowCountKind
    size_bytes: int | None
    size_kind: SizeKind
    source: str
    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        parts = [
            f"rows={self.row_count}" if self.row_count is not None else "rows=unknown",
            f"row_count_kind={self.row_count_kind}",
            f"size_bytes={self.size_bytes}"
            if self.size_bytes is not None
            else "size_bytes=unknown",
            f"size_kind={self.size_kind}",
            f"source={self.source}",
        ]
        if self.notes:
            parts.append(f"notes={'; '.join(self.notes)}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {
            "row_count": self.row_count,
            "row_count_kind": self.row_count_kind,
            "size_bytes": self.size_bytes,
            "size_kind": self.size_kind,
            "source": self.source,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, repr=False)
class TableMetadata(RenderableResult):
    datasource: str
    table: str
    database: str | tuple[str, ...] | None
    backend_type: str
    comment: str | None
    columns: tuple[ColumnMetadata, ...]
    partitions: tuple[PartitionMetadata, ...]
    warnings: tuple[MetadataWarning, ...]
    projectable_columns: tuple[ColumnMetadata, ...] = ()
    partition_state: Literal["known", "none", "unknown"] = "unknown"
    is_view: bool | None = None
    view_definition: str | None = None
    primary_keys: tuple[str, ...] = ()
    unique_constraints: tuple[UniqueConstraintMetadata, ...] = ()
    physical_profile: TablePhysicalProfile | None = None

    @property
    def partition(self) -> PartitionMetadata | None:
        """First partition, or None if no partitions.

        For all partitions use ``.partitions``.
        """
        return self.partitions[0] if self.partitions else None

    @property
    def ref(self) -> str:
        if self.database is None:
            return f"{self.datasource}.{self.table}"
        database = ".".join(self.database) if isinstance(self.database, tuple) else self.database
        return f"{self.datasource}.{database}.{self.table}"

    @override
    def _repr_identity(self) -> str:
        return (
            f"TableMetadata ref={self.ref} backend={self.backend_type} columns={len(self.columns)}"
        )

    @override
    def _card(self) -> Card:
        def column_rows() -> Iterable[Sequence[str]]:
            for c in self.columns:
                yield [
                    c.name,
                    c.type,
                    "Y" if c.nullable else ("N" if c.nullable is False else "?"),
                    c.comment or "",
                ]

        parts: list[str] = []
        if self.is_view:
            parts.append("view=yes")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        if self.partitions:
            parts.append(f"partitions={len(self.partitions)}")
        card = Card(identity=self._repr_identity(), available=(".show()",))
        if parts:
            card.status(" ".join(parts))
        if self.comment:
            card.field(label="comment", value=self.comment)
        if self.physical_profile is not None:
            card.field(label="physical profile", value=self.physical_profile.summary())
        card.lazy_table(
            columns=["column", "type", "nullable", "comment"],
            rows_provider=column_rows,
            row_count=len(self.columns),
        )
        if self.projectable_columns:
            card.lazy_table(
                columns=["physical column", "ibis type", "nullable"],
                rows_provider=lambda: (
                    (
                        column.name,
                        column.type,
                        "Y" if column.nullable else ("N" if column.nullable is False else "?"),
                    )
                    for column in self.projectable_columns
                ),
                row_count=len(self.projectable_columns),
                label="projectable physical columns",
            )
        if self.partitions:
            partition_columns = ", ".join(partition.name for partition in self.partitions)
            partition_values = ", ".join(
                f'"{partition.name}": "<value>"' for partition in self.partitions
            )
            card.listing(
                "suggested next calls",
                (
                    f'md.inspect(ms.ref.datasource("{self.datasource}"), '
                    f'md.table("{self.table}")).partitions().show()',
                    f"md.partition({{{partition_values}}}, max_rows=..., timeout_seconds=...) "
                    f"to scope a snapshot to {partition_columns}",
                ),
            )
        return card

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        return {
            "datasource": self.datasource,
            "table": self.table,
            "database": database,
            "backend_type": self.backend_type,
            "comment": self.comment,
            "columns": [column.to_dict() for column in self.columns],
            "projectable_columns": [column.to_dict() for column in self.projectable_columns],
            "partitions": [partition.to_dict() for partition in self.partitions],
            "partition_state": self.partition_state,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "is_view": self.is_view,
            "view_definition": self.view_definition,
            "primary_keys": list(self.primary_keys),
            "unique_constraints": [uc.to_dict() for uc in self.unique_constraints],
            "physical_profile": (
                self.physical_profile.to_dict() if self.physical_profile is not None else None
            ),
            "ref": self.ref,
        }


def _schema_columns(table_expr: SchemaRelation) -> tuple[ColumnMetadata, ...]:
    schema = table_expr.schema()
    return tuple(
        ColumnMetadata(
            name=str(name),
            type=str(dtype),
            nullable=None,
            comment=None,
            ordinal_position=index,
            _type_is_ibis=True,
        )
        for index, (name, dtype) in enumerate(schema.items(), start=1)
    )


def schema_only_metadata(
    *,
    datasource: str,
    table: str,
    database: str | tuple[str, ...] | None,
    backend_type: str,
    table_expr: SchemaRelation,
    warnings: Iterable[MetadataWarning],
    is_view: bool | None = None,
) -> TableMetadata:
    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type=backend_type,
        comment=None,
        columns=_schema_columns(table_expr),
        partitions=(),
        partition_state="unknown",
        is_view=is_view,
        warnings=(
            *warnings,
            *(
                (
                    MetadataWarning(
                        kind="view_unavailable",
                        message="Table or view kind is unavailable through the bound Ibis schema.",
                    ),
                )
                if is_view is None
                else ()
            ),
            MetadataWarning(
                kind="nullable_unavailable",
                message="Column nullability is unavailable through the bound Ibis schema.",
            ),
            MetadataWarning(
                kind="physical_profile_unavailable",
                message="Physical row and size estimates are unavailable through the bound Ibis schema.",
            ),
            MetadataWarning(
                kind="schema_only_fallback",
                message="metadata inspection returned schema-only metadata",
            ),
        ),
    )


def _database_label(database: str | tuple[str, ...] | None) -> str | None:
    if database is None:
        return None
    return ".".join(database) if isinstance(database, tuple) else database


def _merge_columns(
    schema_columns: Sequence[ColumnMetadata],
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[ColumnMetadata, ...]:
    out: list[ColumnMetadata] = []
    for column in schema_columns:
        catalog = catalog_columns.get(column.name)
        if catalog is None:
            out.append(column)
            continue
        out.append(
            ColumnMetadata(
                name=column.name,
                type=catalog.type or column.type,
                nullable=catalog.nullable,
                comment=catalog.comment,
                ordinal_position=catalog.ordinal_position or column.ordinal_position,
                _type_is_ibis=uses_ibis_column_type(catalog)
                if catalog.type
                else uses_ibis_column_type(column),
            )
        )
    return tuple(out)


def _empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _bool_from_nullable(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"YES", "Y", "TRUE", "1"}:
        return True
    if text in {"NO", "N", "FALSE", "0"}:
        return False
    return None


def _split_top_level_expressions(text: str) -> tuple[str, ...]:
    expressions: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {'"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            expressions.append(text[start:index].strip())
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        expressions.append(tail)
    return tuple(expressions)


_SIMPLE_PARTITION_COLUMN_RE = re.compile(r'^[`"]?([A-Za-z_][A-Za-z0-9_]*)[`"]?$')


def _simple_partition_column(expression: object) -> str | None:
    text = str(expression or "").strip()
    if not text:
        return None
    match = _SIMPLE_PARTITION_COLUMN_RE.match(text)
    if match:
        return match.group(1)
    return None


def _partition_columns_from_expression(expression: object) -> tuple[str, ...]:
    text = str(expression or "").strip()
    if not text:
        return ()
    simple = _simple_partition_column(text)
    if simple is not None:
        return (simple,)
    for prefix in ("range", "list", "hash"):
        wrapped = re.match(rf"^{prefix}\s*\((.*)\)$", text, re.IGNORECASE | re.DOTALL)
        if not wrapped:
            continue
        columns: list[str] = []
        for element in _split_top_level_expressions(wrapped.group(1)):
            column = _simple_partition_column(element)
            if column is None:
                return ()
            columns.append(column)
        return tuple(columns)
    return ()


def _partition_column_from_expression(expression: object) -> str | None:
    columns = _partition_columns_from_expression(expression)
    return columns[0] if len(columns) == 1 else None
