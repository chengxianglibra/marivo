"""Actual source calls and fixed Frame metadata for native regressions."""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.materialization import query_parameters
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.datasource.adapters import (
    BoundSource,
    CompiledRead,
    Parameter,
    PhysicalRequirement,
    QualifiedSource,
    SourceSession,
)
from tests.analysis.materialization.domain_recovery_worker import _result_facts, _scalar
from tests.support.json import Json


@dataclass
class SourceTrace:
    native_sql: list[str] = field(default_factory=list)
    returned_rows: list[int] = field(default_factory=list)
    returned_columns: list[tuple[str, ...]] = field(default_factory=list)
    uploaded_key_rows: list[int] = field(default_factory=list)
    uploaded_key_bytes: list[int] = field(default_factory=list)
    calendar_payload_bytes: list[int] = field(default_factory=list)
    source_seconds: list[float] = field(default_factory=list)
    started_at: list[datetime] = field(default_factory=list)
    returned_pandas_bytes: list[int] = field(default_factory=list)
    returned_arrow_bytes: list[int] = field(default_factory=list)
    owners: list[SourceSession] = field(default_factory=list)
    qualifications: list[dict[str, object]] = field(default_factory=list)
    frames: list[dict[str, Json]] = field(default_factory=list)

    def record(self, result: mv.AnalysisFrame) -> None:
        """Record actual fixed data facts without reading payloads or publishing."""
        backing = result._backing
        if not isinstance(backing, (MemoryFrameBacking, StoredFrameBacking)):
            raise AssertionError("Source trace metadata requires a computed or stored Frame")
        record: dict[str, Json] = {
            "kind": "AnalysisFrame",
            "carrier": "stored" if isinstance(backing, StoredFrameBacking) else "memory",
            "session": result._local.session_ref,
            "input_id": result._input_id,
            "schema": _scalar(asdict(backing.data.schema)),
            "metadata": _scalar(asdict(backing.metadata))
            if isinstance(backing, StoredFrameBacking)
            else _result_facts(backing.metadata),
        }
        if isinstance(backing, StoredFrameBacking):
            record["artifact"] = backing.artifact_id
            record["data"] = _scalar(asdict(backing.data))
        self.frames.append(record)

    def save(
        self,
        name: str,
        environment: dict[str, object],
        inputs: dict[str, object],
        oracle: dict[str, object],
        result: mv.AnalysisFrame | None,
        excluded: tuple[SourceSession, ...],
    ) -> None:
        executions = [owner for owner in self.owners if owner not in excluded]
        submissions = [asdict(item) for owner in executions for item in owner.submissions]
        assert submissions and self.native_sql
        assert all(owner._closed for owner in executions)
        assert all(item["connection_disconnected"] is True for item in submissions)
        assert all(item["state"] == "succeeded" for item in submissions)
        assert all(item["sql"] in self.native_sql for item in submissions)
        if result is not None:
            self.record(result)
        directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if directory:
            Path(directory, name + ".json").write_text(
                json.dumps(
                    {
                        "environment": environment,
                        "input": inputs,
                        "oracle": oracle,
                        "qualified_requirements": self.qualifications,
                        "frames": self.frames,
                        "submissions": submissions,
                        "actual_native_submissions": self.native_sql,
                        "source_returned_rows": self.returned_rows,
                        "source_returned_columns": self.returned_columns,
                        "uploaded_key_rows": self.uploaded_key_rows,
                        "uploaded_key_bytes": self.uploaded_key_bytes,
                        "connection_disconnected": True,
                        "boundary": "Actual source qualification/submission and fixed Frame facts; stored records alone include committed receipts. No producer graph or general backend qualification is inferred",
                    },
                    sort_keys=True,
                )
            )


def capture_source(monkeypatch: pytest.MonkeyPatch, *, measure_cost: bool = False) -> SourceTrace:
    trace = SourceTrace()
    execute = SourceSession.execute
    enter = SourceSession.__enter__
    qualify = SourceSession.qualify
    bind_keys = SourceSession._bind_key_relation
    instantiate = query_parameters.instantiate
    compile_read = SourceSession.compile
    pending_keys: list[tuple[int, int]] = []
    compiled_keys: dict[int, tuple[tuple[int, int], ...]] = {}

    def captured_execute(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        submitted_before = len(owner.submissions)
        started = perf_counter()
        trace.started_at.append(datetime.now(timezone.utc))
        try:
            result = execute(owner, read)
            trace.source_seconds.append(perf_counter() - started)
            if measure_cost:
                trace.returned_pandas_bytes.append(int(result.memory_usage(deep=True).sum()))
                trace.returned_arrow_bytes.append(
                    pa.Table.from_pandas(result, preserve_index=False).nbytes
                )
            from marivo.datasource._calendar_relations import CalendarData

            trace.calendar_payload_bytes.append(
                sum(len(node.payload.encode()) for node in read.expression.op().find(CalendarData))
                + sum(
                    node.data.to_pyarrow(node.schema).nbytes
                    for node in read.expression.op().find(ops.InMemoryTable)
                    if node.name.startswith("_marivo_calendar_")
                )
            )
            trace.returned_rows.append(len(result))
            trace.returned_columns.append(tuple(result.columns))
            return result
        finally:
            if len(owner.submissions) > submitted_before:
                trace.native_sql.append(read.sql)
                for rows, size in compiled_keys.pop(id(read), ()):
                    trace.uploaded_key_rows.append(rows)
                    trace.uploaded_key_bytes.append(size)

    def captured_keys(owner: SourceSession, data: pa.Table) -> ir.Table:
        result = bind_keys(owner, data)
        trace.uploaded_key_rows.append(data.num_rows)
        trace.uploaded_key_bytes.append(data.nbytes)
        return result

    def captured_parameter(
        expression: ir.Table,
        name: str,
        columns: tuple[DataColumn, ...],
        output: StageOutput,
        *,
        relation: ir.Table | None = None,
        provider: str | None = None,
        source: SourceSession | None = None,
    ) -> ir.Table:
        result = instantiate(
            expression, name, columns, output, relation=relation, provider=provider, source=source
        )
        if relation is None:
            data = query_parameters.parameter_data(columns, output)
            pending_keys.append((data.num_rows, data.nbytes))
        return result

    def compiled(
        owner: SourceSession,
        qualified: QualifiedSource | Sequence[QualifiedSource],
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        payloads = tuple(pending_keys)
        pending_keys.clear()
        result = compile_read(
            owner,
            qualified,
            expression,
            params=params,
            purpose=purpose,
            expected_schema=expected_schema,
        )
        if purpose != "query.prepare":
            compiled_keys[id(result)] = payloads
        return result

    def entered(owner: SourceSession) -> SourceSession:
        result = enter(owner)
        trace.owners.append(owner)
        return result

    def qualified(
        owner: SourceSession, binding: BoundSource, need: PhysicalRequirement
    ) -> QualifiedSource:
        result = qualify(owner, binding, need)
        trace.qualifications.append(
            {
                "source": binding.facts.source_identity,
                "method": need.method_id,
                "version": need.version,
                "operations": sorted(need.operations),
                "schema": [[item.name, str(item.type)] for item in binding.facts.schema],
            }
        )
        return result

    monkeypatch.setattr(query_parameters, "instantiate", captured_parameter)
    monkeypatch.setattr(SourceSession, "compile", compiled)
    monkeypatch.setattr(SourceSession, "execute", captured_execute)
    monkeypatch.setattr(SourceSession, "_bind_key_relation", captured_keys)
    monkeypatch.setattr(SourceSession, "__enter__", entered)
    monkeypatch.setattr(SourceSession, "qualify", qualified)
    return trace
