"""Private immutable execution inputs and action-local execution ownership."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

import ibis
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.datasource.timezone import DatasourceEngineTimezone

Parameter = str | int | float | bool | bytes | Decimal | date | datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    identity: str


@dataclass(frozen=True, slots=True)
class Statement:
    sql: str
    parameters: tuple[Parameter, ...]
    schema: pa.Schema
    role: str
    context: ExecutionContext
    preparations: tuple[ir.Expr, ...] = ()


class ScalarRows(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...


class BatchStream(Protocol):
    @property
    def schema(self) -> pa.Schema: ...
    def __iter__(self) -> Iterator[pa.RecordBatch]: ...
    def close(self) -> None: ...


class ExecutionAdapter(Protocol):
    def prepare(self, expression: ir.Expr, *, role: str = "query") -> Statement: ...
    def compile(self, expression: ir.Expr) -> str: ...
    def statement(
        self,
        sql: str,
        *,
        role: str = "statement",
        parameters: tuple[Parameter, ...] = (),
        inputs: tuple[Statement, ...] = (),
    ) -> Statement: ...
    def submit(self, statement: Statement) -> ScalarRows: ...
    def read_table(self, statement: Statement) -> pa.Table: ...
    def read_scalar(self, statement: Statement) -> object: ...
    def batches(self, statement: Statement, *, chunk_size: int) -> BatchStream: ...
    def resolve_coverage(
        self,
        definition: EventDefinition,
        *,
        provider: EventCoverageProvider | None,
        source_binding_fingerprint: str,
        execution_domain_id: str,
        require_source_origin: bool,
    ) -> EventCoverageResolution: ...
    def timezone(self) -> DatasourceEngineTimezone: ...
    def install_numeric(self) -> None: ...
    def interrupt(self) -> None: ...
    def initialize(self) -> None: ...
    def disconnect(self) -> None: ...
    def finish(self) -> None: ...
    def get_schema(
        self, name: str, *, database: str | None, catalog: str | None
    ) -> ibis.Schema: ...
    def table(self, name: str) -> ir.Table: ...
    def read_parquet(self, path: str, *, table_name: str) -> ir.Table: ...
    def freeze_reader(self, name: str, reader: pa.RecordBatchReader) -> ir.Table: ...
    def table_statement(self, name: str, expression: ir.Table) -> Statement: ...
    def read_json(
        self,
        path: str,
        *,
        table_name: str,
        columns: Mapping[str, str],
        format: str,
    ) -> ir.Table: ...
