"""Interpret ClickHouse partition metadata against captured catalog columns."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from marivo.datasource.metadata_types import ColumnMetadata, PartitionMetadata

_CH_PARTITION_FUNC_RE = re.compile(r"^(\w+)\((\w+)\)$")
_CH_PARTITION_BARE_RE = re.compile(r"^(\w+)$")


def _parse_clickhouse_partition_key(
    partition_key: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[PartitionMetadata, ...]:
    if not partition_key or partition_key == "tuple()":
        return ()
    elements: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(partition_key):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            elements.append(partition_key[start:i].strip())
            start = i + 1
    elements.append(partition_key[start:].strip())
    parts: list[PartitionMetadata] = []
    for element in elements:
        func_match = _CH_PARTITION_FUNC_RE.match(element)
        if func_match:
            func_name, col_name = func_match.group(1), func_match.group(2)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=func_name,
                        comment=None,
                    )
                )
            continue
        bare_match = _CH_PARTITION_BARE_RE.match(element)
        if bare_match:
            col_name = bare_match.group(1)
            col = catalog_columns.get(col_name)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=col_name,
                        type=col.type,
                        transform=None,
                        comment=None,
                    )
                )
            continue
        # Unparseable expression: store full raw string as transform.
        # Try to identify a column name from identifier-like tokens,
        # stripping commas so inner args like "uid," in intDiv(uid, 100) match.
        for token in element.replace("(", " ").replace(")", " ").replace(",", " ").split():
            col = catalog_columns.get(token)
            if col is not None:
                parts.append(
                    PartitionMetadata(
                        name=token,
                        type=col.type,
                        transform=element,
                        comment=None,
                    )
                )
                break
    return tuple(parts)


def clickhouse_partitions(
    engine: str,
    partition_key: str,
    catalog_columns: Mapping[str, ColumnMetadata],
    local_partition_key: str | None,
    *,
    table_metadata_available: bool,
    include_partitions: bool,
) -> tuple[tuple[PartitionMetadata, ...], Literal["known", "none", "unknown"]]:
    if not include_partitions:
        return (), "unknown"
    partitions = _parse_clickhouse_partition_key(
        (local_partition_key or "") if engine == "Distributed" else partition_key,
        catalog_columns,
    )
    if partitions:
        return partitions, "known"
    if table_metadata_available and engine != "Distributed" and partition_key in ("", "tuple()"):
        return (), "none"
    return (), "unknown"
