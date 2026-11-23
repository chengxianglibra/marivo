"""Parse native Trino table comments and physical partition declarations."""

import re
from collections.abc import Mapping

from marivo.datasource.metadata_types import ColumnMetadata, PartitionMetadata, _empty_to_none

_TRINO_PARTITION_ARRAY_RE = re.compile(
    r"\b(?:partitioned_by|partitioning)\s*=\s*ARRAY\s*\[(.*?)\]",
    re.IGNORECASE | re.DOTALL,
)
_TRINO_ARRAY_STRING_RE = re.compile(r"'((?:[^']|'')*)'")
_TRINO_PARTITION_TRANSFORM_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$")
_TRINO_TABLE_COMMENT_RE = re.compile(
    r"\)\s*COMMENT\s+'((?:[^']|'')*)'",
    re.IGNORECASE | re.DOTALL,
)


def _trino_partition_specs_from_show_create(create_sql: str) -> tuple[str, ...]:
    match = _TRINO_PARTITION_ARRAY_RE.search(create_sql)
    if not match:
        return ()
    return tuple(
        value.replace("''", "'").strip() for value in _TRINO_ARRAY_STRING_RE.findall(match.group(1))
    )


def _trino_partition_from_spec(
    spec: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> PartitionMetadata | None:

    transform: str | None = None
    column_name = spec.strip()
    transform_match = _TRINO_PARTITION_TRANSFORM_RE.match(column_name)
    if transform_match:
        transform = transform_match.group(1)
        first_arg = transform_match.group(2).split(",", 1)[0].strip()
        column_name = first_arg.strip('"')
    column = catalog_columns.get(column_name)
    if column is None:
        return None
    return PartitionMetadata(
        name=column_name,
        type=column.type,
        transform=transform,
        comment=None,
    )


def partitions_from_show_create(
    *,
    create_sql: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[PartitionMetadata, ...]:
    partitions: list[PartitionMetadata] = []
    for spec in _trino_partition_specs_from_show_create(create_sql):
        partition = _trino_partition_from_spec(spec, catalog_columns)
        if partition is not None:
            partitions.append(partition)
    return tuple(partitions)


def table_comment_from_show_create(create_sql: str) -> str | None:

    match = _TRINO_TABLE_COMMENT_RE.search(create_sql)
    if match is None:
        return None
    return _empty_to_none(match.group(1).replace("''", "'"))
