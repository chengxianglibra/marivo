"""Metadata-only source inspection for datasource authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import ibis
from ibis.backends.sql.datatypes import ClickHouseType
from typing_extensions import override

from marivo._kernel.refs import DatasourceKind, Ref
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    symbol_factory,
)
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.metadata_types import PartitionMetadata, TablePhysicalProfile
from marivo.datasource.source import (
    TableSource,
)

_PARTITION_VALUE_LIMIT = 100


@dataclass(frozen=True)
class PhysicalExtent:
    row_count: int | None
    row_count_kind: Literal["exact", "estimated", "unknown"]
    size_bytes: int | None
    size_kind: Literal["exact", "estimated", "unknown"]
    source: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Partitioning:
    state: Literal["known", "none", "unknown"]
    fields: tuple[PartitionMetadata, ...]
    value_source: str | None
    values: tuple[tuple[tuple[str, str], ...], ...]
    values_complete: bool
    truncated: bool


@dataclass(frozen=True)
class ExecutionCapabilities:
    partition_predicate_supported: bool
    transformed_partition_supported: bool
    timeout_enforced: bool
    byte_estimate_supported: bool


@dataclass(frozen=True, repr=False)
class PartitionInspection(RenderableResult):
    _datasource: Ref[DatasourceKind]
    source: TableSource
    partitioning: Partitioning
    limit: int
    order: Literal["asc", "desc"]
    status: Literal["complete", "truncated", "incomplete"]
    issues: tuple[str, ...]

    @property
    def datasource(self) -> DatasourceSymbol:
        """Native lookup selector for the inspected datasource."""
        return symbol_factory.datasource(self._datasource.path)

    @override
    def _repr_identity(self) -> str:
        return (
            f"PartitionInspection datasource={self._datasource.path} "
            f"state={self.partitioning.state} order={self.order} "
            f"limit={self.limit} status={self.status}"
        )

    @override
    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".show()",),
        ).field(
            label="partition fields",
            value=", ".join(field.name for field in self.partitioning.fields) or "none",
        )
        card.field("value source", self.partitioning.value_source or "none")
        card.field("listing", f"order={self.order} limit={self.limit}")
        card.field("values complete", str(self.partitioning.values_complete))
        card.field("values truncated", str(self.partitioning.truncated))
        if self.partitioning.truncated:
            opposite = "asc" if self.order == "desc" else "desc"
            card.field(
                "opposite edge",
                f"inspection.partitions(limit={self.limit}, order={opposite!r})",
            )
        range_field = _time_range_partition_field(self.partitioning)
        if range_field is not None:
            card.field(
                "scope template",
                (
                    f"md.time_range({range_field.name!r}, start=<inclusive ISO boundary>, "
                    "end=<exclusive ISO boundary>, max_rows=<positive int>, "
                    "timeout_seconds=<positive int>)"
                ),
            )
        elif self.partitioning.values:
            card.table(
                columns=("captured partition",),
                rows=((repr(dict(value)),) for value in self.partitioning.values),
                row_count=len(self.partitioning.values),
                label="captured partition values",
                show_omission_counts=True,
            )
            card.field(
                "scope template",
                (
                    f"md.partition({dict(self.partitioning.values[0])!r}, "
                    "max_rows=<positive int>, timeout_seconds=<positive int>)"
                ),
            )
        elif self.partitioning.state == "known" and self.partitioning.value_source is None:
            card.field(
                "scope template",
                "md.unpruned(max_rows=<positive int>, timeout_seconds=<positive int>) "
                "because the partition-value hook captured no values",
            )
        elif self.partitioning.state == "known":
            card.field(
                "scope template",
                "unavailable: supply one complete mapping for "
                + ", ".join(field.name for field in self.partitioning.fields),
            )
        if self.issues:
            card.listing("issues", self.issues)
        return card


def _partition_issues(partitioning: Partitioning) -> tuple[str, ...]:
    issues: list[str] = []
    if partitioning.state == "unknown":
        issues.append("partition state is unknown")
    if (
        partitioning.state == "known"
        and not partitioning.values_complete
        and not partitioning.truncated
    ):
        issues.append("partition values are incomplete")
    if (
        any(field.transform is not None for field in partitioning.fields)
        and _time_range_partition_field(partitioning) is None
    ):
        issues.append("transformed partition fields are not expressible in V1")
    return tuple(issues)


def _physical_extent(profile: TablePhysicalProfile | None) -> PhysicalExtent:
    if profile is None:
        return PhysicalExtent(
            row_count=None,
            row_count_kind="unknown",
            size_bytes=None,
            size_kind="unknown",
            source="metadata_unavailable",
            notes=(),
        )
    row_count_kind: Literal["exact", "estimated", "unknown"]
    if profile.row_count_kind == "metadata":
        row_count_kind = "exact"
    elif profile.row_count_kind == "estimate":
        row_count_kind = "estimated"
    else:
        row_count_kind = "unknown"
    size_kind: Literal["exact", "estimated", "unknown"]
    if profile.size_kind == "on_disk":
        size_kind = "exact"
    elif profile.size_kind in {"data_plus_index", "table_stats"}:
        size_kind = "estimated"
    else:
        size_kind = "unknown"
    return PhysicalExtent(
        row_count=profile.row_count,
        row_count_kind=row_count_kind,
        size_bytes=profile.size_bytes,
        size_kind=size_kind,
        source=profile.source,
        notes=profile.notes,
    )


def _execution_capabilities(profile: EngineProfile) -> ExecutionCapabilities:
    capabilities = profile.authoring_capabilities
    return ExecutionCapabilities(
        partition_predicate_supported=capabilities.partition_predicate_supported,
        transformed_partition_supported=capabilities.transformed_partition_supported,
        timeout_enforced=profile.native_query_timeout is not None,
        byte_estimate_supported=capabilities.byte_estimate_supported,
    )


def _time_range_partition_field(partitioning: Partitioning) -> PartitionMetadata | None:
    transformed = tuple(
        field
        for field in partitioning.fields
        if field.transform is not None and field.type is not None and _is_temporal_type(field.type)
    )
    return transformed[0] if len(transformed) == 1 and len(partitioning.fields) == 1 else None


def _is_temporal_type(type_name: str) -> bool:
    try:
        dtype = ibis.dtype(type_name)
    except (TypeError, ValueError, RuntimeError):
        try:
            dtype = ClickHouseType.from_string(type_name)
        except (TypeError, ValueError, RuntimeError):
            return False
    return bool(dtype.is_date() or dtype.is_timestamp())
