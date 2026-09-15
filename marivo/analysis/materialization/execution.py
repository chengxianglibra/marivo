"""Private immutable execution inputs and action-local execution ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

import ibis
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.datasource.timezone import DatasourceEngineTimezone

Parameter = str | int | float | bool | bytes | Decimal | date | datetime | None


@dataclass(frozen=True, slots=True, eq=False)
class ExecutionContext:
    """Identity token for one owned execution lifetime."""


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
    def read_table(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> pa.Table: ...
    def read_scalar(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> object: ...
    def batches(
        self,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> BatchStream: ...
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
    def prepare_dataset(self, dataset: LogicalDataset) -> None: ...
    def sample_sql(self, fence: CompiledSampleFence) -> str: ...
    def sample_validation_sql(self, fence: CompiledSampleFence) -> str: ...
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


class AdapterFactory(Protocol):
    def __call__(
        self, candidate: object, *, reserve: Callable[[str], None], run_ref: str
    ) -> ExecutionAdapter: ...


@dataclass(frozen=True, slots=True)
class ExecutionBackend:
    """Runtime factories implementing a backend declared in the method registry."""

    bind: AdapterFactory
    open_retained: Callable[[], object]
    admit: Callable[[LogicalDataset], None]


def resolve_execution(backend: str) -> ExecutionBackend | None:
    """Bind a pure registration to concrete Runtime functions, without new capabilities."""
    from marivo.analysis.materialization.duckdb_execution import (
        admit_dataset,
        bind_duckdb,
        open_native_backend,
    )
    from marivo.analysis.operators.registry import backend_execution

    registration = backend_execution(backend)
    if registration is None:
        return None
    factories = {"duckdb": ExecutionBackend(bind_duckdb, open_native_backend, admit_dataset)}
    return factories.get(registration.backend)
