"""Scope resolver for per-object semantic verification.

Builds partition-scoped ibis expressions for entities and returns
structured scan reports for the verify_object workflow.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from marivo.datasource.ir import EntitySourceIR, FileSourceIR, TableSourceIR
from marivo.datasource.scan import PartitionResolution, ScanReport


@dataclass(frozen=True)
class ScopedEntityExpression:
    """An ibis expression scoped by partition, with an attached scan report."""

    expr: Any
    scan: ScanReport


def scoped_entity_expression(
    *,
    backend: Any,
    entity_source: EntitySourceIR,
    partition: Mapping[str, str] | None = None,
) -> ScopedEntityExpression:
    """Build a partition-scoped ibis expression for a loaded entity.

    Parameters
    ----------
    backend:
        Live ibis backend for the entity's datasource.
    entity_source:
        Physical source descriptor (TableSourceIR or FileSourceIR).
    partition:
        Explicit partition filter mapping, or ``None`` for unpruned scans.

    Returns
    -------
    ScopedEntityExpression
        The scoped expression and a preliminary ScanReport.
    """
    started = time.perf_counter()
    source = entity_source
    if isinstance(source, TableSourceIR):
        if source.database is None:
            expr = backend.table(source.table)
        else:
            expr = backend.table(source.table, database=source.database)
    elif isinstance(source, FileSourceIR):
        reader_name = {
            "parquet": "read_parquet",
            "csv": "read_csv",
            "json": "read_json",
        }[source.format]
        reader = getattr(backend, reader_name)
        expr = reader(source.path, **source.options)
    else:
        raise TypeError(f"Unsupported entity source {type(source).__name__}.")

    resolution: PartitionResolution
    partition_used: Mapping[str, str] | None

    if partition is not None:
        for column, value in partition.items():
            expr = expr.filter(expr[column] == value)
        resolution = "explicit"
        partition_used = dict(partition)
    else:
        resolution = "unpruned"
        partition_used = None

    elapsed = time.perf_counter() - started

    return ScopedEntityExpression(
        expr=expr,
        scan=ScanReport(
            partition_used=partition_used,
            partition_resolution=resolution,
            rows_scanned=0,
            columns_scanned=(),
            truncated=False,
            elapsed_seconds=elapsed,
            warnings=(),
        ),
    )
