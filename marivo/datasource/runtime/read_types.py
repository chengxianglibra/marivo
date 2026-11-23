"""Owned bindings, issued reads and submission receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import ibis.expr.types as ir
import pyarrow as pa

from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR

DURATION_UNIT_METADATA_KEY = b"marivo:duration_unit"
TIMESTAMP_UNIT_METADATA_KEY = b"marivo:timestamp_unit"

SourceIR = TableSourceIR | CsvSourceIR | ParquetSourceIR | JsonSourceIR
Parameter = str | int | float | bool | bytes | Decimal | date | datetime | None
Termination = Literal["local_closed", "remote_unknown", "remote_confirmed"]


@dataclass(frozen=True, slots=True)
class PhysicalFacts:
    """Scoped physical observations; unknown never grants business coverage."""

    source_identity: str
    schema: pa.Schema
    observed_scope: Literal["unknown", "bounded_sample", "complete"] = "unknown"
    business_coverage: Literal["unknown"] = "unknown"


@dataclass(frozen=True, slots=True, eq=False)
class BoundSource:
    source: SourceIR
    relation: ir.Table
    facts: PhysicalFacts
    _owner: object


@dataclass(frozen=True, slots=True)
class PhysicalRequirement:
    """Physical operations required by an Analysis-owned method version."""

    method_id: str
    version: int
    operations: frozenset[
        Literal["scan", "filter", "project", "group", "count", "join", "union", "window", "sort"]
    ]


@dataclass(frozen=True, slots=True, eq=False)
class QualifiedSource:
    binding: BoundSource
    requirement: PhysicalRequirement
    _owner: object


@dataclass(frozen=True, slots=True, eq=False)
class CompiledRead:
    """Opaque session-issued evidence of one exact Ibis compilation."""

    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str
    _owner: object


@dataclass(frozen=True, slots=True)
class _IssuedRead:
    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str
    requirements: tuple[PhysicalRequirement, ...]


@dataclass(slots=True)
class SourceSubmission:
    purpose: str
    source_identity: str
    expression_identity: int
    sql: str
    state: Literal["submitted", "succeeded", "failed"] = "submitted"
    connection_disconnected: bool = False
    termination: Termination | None = None
    query_executed: bool = False
