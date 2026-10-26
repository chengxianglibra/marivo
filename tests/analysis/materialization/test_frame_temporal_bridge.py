"""FrameKeys preserve complete temporal tuples and a distinct real null category."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import duckdb
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.core.data_schema import DataAxis, DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.population import Population
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.query_population import _token, evaluate_population
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.expressions import Entity
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    # Projection and set algebra must not open this intentionally absent database.
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='temporal-bridge.duckdb')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table('facts'),primary_key=['channel','t'])
t=ms.dimension_column(name='t',entity=facts,column='t')
channel=ms.dimension_column(name='channel',entity=facts,column='channel')
amount=ms.measure_column(name='amount',entity=facts,column='amount',unit='USD',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("temporal-bridge", timezone="UTC")


def _series(ticks: list[int | None], physical: pa.DataType) -> pd.Series:
    integer = pa.int32() if physical == pa.date32() else pa.int64()
    return pd.Series(pd.arrays.ArrowExtensionArray(pa.array(ticks, type=integer).cast(physical)))


def _frame(
    session: Session,
    dtype: DataType,
    physical: pa.DataType,
    ticks: list[int | None],
    *,
    identity: bool = False,
    nullable: bool = True,
) -> AnalysisFrame:
    temporal = DataColumn("t", "time", dtype, nullable and not identity)
    channel = DataColumn("channel", "channel", ScalarDataType("string"), False)
    detail = DataColumn("detail", "detail", ScalarDataType("int64"), False)
    axes = (
        (DataAxis("identity", (channel, temporal), name="identity", kind="member"),)
        if identity
        else (
            DataAxis("time", (temporal,), name="time"),
            DataAxis("channel", (channel,), name="channel"),
        )
    )
    schema = DataSchema((*axes, DataAxis("detail", (detail,), name="detail")), ())
    rows = pd.DataFrame(
        {
            "t": _series(ticks, physical),
            "channel": [
                "A" if index % 3 == 0 else "B" if index % 3 == 1 else "C"
                for index in range(len(ticks))
            ],
            "detail": list(range(len(ticks))),
        }
    )
    rows = rows.loc[:, [column.slot_id for column in schema.columns]]
    return AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(schema, rows),
            FrameResultMetadata(
                "temporal-selected",
                FrameAudit(
                    (), EvaluationRecord(datetime(2026, 10, 20, tzinfo=timezone.utc), (), ())
                ),
            ),
        ),
    )


def _population(
    session: Session, frame: AnalysisFrame, *, joint: bool = False, identity: bool = False
) -> Population:
    target = (
        session.catalog.entities.get("sales.facts")
        if identity
        else session.catalog.fields.get("sales.facts.t")
    )
    on: tuple[tuple[FrameAxis, Entity | Deferred], ...] = ((frame.axes[0], target),)
    if joint:
        on = (*on, (frame.axes[1], session.catalog.fields.get("sales.facts.channel")))
    return session.population_from(frame, on=on)


def _ticks(
    session: Session, population: Population, integer: pa.DataType | None = None
) -> list[int | None]:
    result = session.frame(population).compute().to_pandas()
    ticks: list[int | None] = (
        pa.array(result.iloc[:, 0]).cast(pa.int64() if integer is None else integer).to_pylist()
    )
    return ticks


TEMPORAL_TYPES: tuple[tuple[DataType, pa.DataType], ...] = (
    (DurationDataType("ns"), pa.duration("ns")),
    (TimestampDataType("ns", None), pa.timestamp("ns")),
    (TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC")),
    (TimestampDataType("ns", "Asia/Shanghai"), pa.timestamp("ns", "Asia/Shanghai")),
    (ScalarDataType("date"), pa.date32()),
)


@pytest.mark.parametrize("dtype,physical", TEMPORAL_TYPES)
def test_frame_keys_keep_extrema_nulls_joint_dedup_and_order(
    session: Session, dtype: DataType, physical: pa.DataType, monkeypatch: pytest.MonkeyPatch
) -> None:
    minimum, maximum = (-(2**31), 2**31 - 1) if physical == pa.date32() else (-(2**63), 2**63 - 1)
    memory = _frame(session, dtype, physical, [minimum, 0, None, minimum, maximum])
    stored = memory.persist()
    reopened = session.persisted_frame(stored.artifact.id)
    # Catalog resolution is metadata only; the dependent fixed-key plan remains source free.
    session.catalog.fields.get("sales.facts.t")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed temporal key projection attempted a source read")

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_source_preparation.open_query_sources", forbidden
    )
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    integer = pa.int32() if physical == pa.date32() else pa.int64()
    for frame in (memory, stored, reopened):
        before = terminal_runs(session)
        selected = _population(session, frame, joint=True)
        result = session.frame(selected).compute().to_pandas()
        assert pa.array(result.iloc[:, 0]).type == physical
        assert pa.array(result.iloc[:, 0]).cast(integer).to_pylist() == [minimum, 0, None, maximum]
        assert result.iloc[:, 1].tolist() == ["A", "B", "C", "B"]
        projected = selected.project(by=(selected.axes[0],))
        assert _ticks(session, projected, integer) == [minimum, 0, None, maximum]
        assert _ticks(session, projected.union(projected), integer) == [minimum, 0, None, maximum]
        assert _ticks(session, projected.intersection(projected), integer) == [
            minimum,
            0,
            None,
            maximum,
        ]
        assert _ticks(session, projected.difference(projected), integer) == []
        assert _ticks(session, projected.where(mv.in_population(projected)), integer) == [
            minimum,
            0,
            None,
            maximum,
        ]
        assert _ticks(session, projected.where(~mv.in_population(projected)), integer) == []
        assert terminal_runs(session) == before


@pytest.mark.parametrize(
    "dtype,physical,literal",
    [
        (
            TimestampDataType("ns", "UTC"),
            pa.timestamp("ns", "UTC"),
            datetime(1970, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))),
        ),
        (DurationDataType("ns"), pa.duration("ns"), timedelta(0)),
        (ScalarDataType("date"), pa.date32(), date(1970, 1, 1)),
    ],
)
def test_frame_keys_match_ordinary_python_temporal_literals(
    session: Session, dtype: DataType, physical: pa.DataType, literal: date | timedelta
) -> None:
    minimum = -(2**31) if physical == pa.date32() else -(2**63)
    frame = _frame(session, dtype, physical, [minimum, 0, None])
    selected = _population(session, frame)
    finite = mv.coordinates(
        by=(session.catalog.fields.get("sales.facts.t"),), keys=((literal,), (None,))
    )
    rows = pd.DataFrame({"t": _series([minimum, 0, None], physical)})
    for operation, expected in (
        (selected.intersection(finite), (literal, None)),
        (selected.difference(finite), (pa.scalar(minimum, type=physical),)),
        (selected.where(mv.in_population(finite)), (literal, None)),
    ):
        actual = evaluate_population(
            operation._definition, (selected._definition,), (rows,), ((("t",),),)
        )
        assert tuple(_token(key) for key in actual) == tuple(_token((key,)) for key in expected)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_temporal_tokens_equate_pandas_python_and_arrow_without_nanosecond_loss(
    unit: Literal["s", "ms", "us", "ns"],
) -> None:
    timestamp = pd.Timestamp(1, unit=unit, tz="UTC")
    duration = pd.Timedelta(1, unit=unit)
    assert _token(timestamp) == _token(pa.scalar(1, type=pa.timestamp(unit, "UTC")))
    assert _token(duration) == _token(pa.scalar(1, type=pa.duration(unit)))
    assert _token(timestamp) != _token(pa.scalar(0, type=pa.timestamp("ns", "UTC")))
    assert _token(duration) != _token(pa.scalar(0, type=pa.duration("ns")))
    extreme = pd.Timestamp(2**63 - 1, unit=unit)
    assert _token(extreme) == _token(pa.scalar(2**63 - 1, type=pa.timestamp(unit)))


def test_complete_temporal_identity_and_empty_zero_axis_projection(session: Session) -> None:
    frame = _frame(
        session,
        TimestampDataType("ns", "UTC"),
        pa.timestamp("ns", "UTC"),
        [-(2**63), 0, 1],
        identity=True,
    )
    population = _population(session, frame, identity=True)
    result = session.frame(population).compute().to_pandas()
    keys = result.iloc[:, 0].tolist()
    assert [(key[0], key[1].cast(pa.int64()).as_py()) for key in keys] == [
        ("A", -(2**63)),
        ("B", 0),
        ("C", 1),
    ]
    scalar = session.population_from(frame, on=())
    assert session.frame(scalar).compute().to_pandas().shape == (1, 0)
    empty = frame.where(frame.coord(frame.axes[1]) < 0).compute()
    assert session.frame(session.population_from(empty, on=())).compute().to_pandas().shape == (
        0,
        0,
    )


def test_source_free_shared_axis_rejects_incompatible_exact_duration_units(
    session: Session,
) -> None:
    left = _frame(session, DurationDataType("ns"), pa.duration("ns"), [1])
    right = _frame(session, DurationDataType("ms"), pa.duration("ms"), [1])
    population = _population(session, left).union(_population(session, right))
    with pytest.raises(AnalysisError, match="compatible complete types"):
        session.frame(population).compute()


def test_source_free_same_type_axis_merges_nullable_keys(session: Session) -> None:
    left = _frame(session, DurationDataType("ns"), pa.duration("ns"), [0], nullable=False)
    right = _frame(session, DurationDataType("ns"), pa.duration("ns"), [None])
    selected = _population(session, left).union(_population(session, right))
    assert _ticks(session, selected) == [0, None]


def test_cold_frame_key_bridge_keeps_minimum_separate_from_null(session: Session) -> None:
    saved = _frame(
        session, TimestampDataType("ns", "UTC"), pa.timestamp("ns", "UTC"), [-(2**63), 0, None]
    ).persist()
    script = """
import os, sys
from unittest.mock import patch
import pyarrow as pa
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
from marivo.analysis.materialization import frame_source_preparation
from marivo.datasource.adapters import SourceSession
os.environ['MARIVO_PROJECT_ROOT'] = sys.argv[1]
def forbidden(*args, **kwargs):
    raise AssertionError('cold FrameKeys projection attempted source access')
with patch.object(SourceSession, '__init__', forbidden), patch.object(frame_source_preparation, 'open_query_sources', forbidden):
    session = mv.session.resume(sys.argv[2])
    saved = session.persisted_frame(sys.argv[3])
    before = terminal_runs(session)
    target = session.catalog.fields.get('sales.facts.t')
    population = session.population_from(saved, on=((saved.axes[0], target),))
    result = session.frame(population).compute()
    assert pa.array(result.to_pandas().iloc[:, 0]).cast(pa.int64()).to_pylist() == [-(2**63), 0, None]
    assert terminal_runs(session) == before
print('cold-frame-keys-temporal-exact')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(session.project_root),
            session._runtime.local.session_ref,
            saved.artifact.id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-frame-keys-temporal-exact" in result.stdout


def test_explicit_source_key_transport_preserves_minimum_tick_and_null(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = pa.timestamp("us", "UTC")
    with duckdb.connect(str(session.project_root / "temporal-bridge.duckdb")) as connection:
        connection.execute("CREATE TABLE facts(channel VARCHAR,t TIMESTAMPTZ,amount BIGINT)")
        connection.execute(
            "INSERT INTO facts VALUES ('A','2000-01-01 00:00:00+00',10),('B','1970-01-01 00:00:00+00',20),('C',NULL,30)"
        )
    selected = _frame(session, TimestampDataType("us", "UTC"), physical, [-(2**63), 0, None])
    original = SourceSession._bind_key_relation
    transported: list[pa.Table] = []

    def bind(self: SourceSession, data: pa.Table) -> ir.Table:
        transported.append(data)
        return original(self, data)

    monkeypatch.setattr(SourceSession, "_bind_key_relation", bind)
    population = _population(session, selected, joint=True)
    computed = session.observe(
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
        where=mv.in_population(population),
    ).compute()
    assert len(transported) == 1
    assert transported[0].column(0).type == physical
    assert transported[0].column(0).cast(pa.int64()).to_pylist() == [-(2**63), 0, None]
    assert transported[0].column(1).to_pylist() == ["A", "B", "C"]
    assert computed.to_pandas()["amount"].tolist() == [50]
    assert terminal_runs(session) == ()
    assert isinstance(computed._backing, MemoryFrameBacking)
    facts = computed._backing.metadata.audit.source_facts
    scopes_by_fact: list[str] = []
    for fact in facts:
        if fact.kind == "read":
            scopes = dict(fact.values)["source_scopes"]
            assert isinstance(scopes, str)
            scopes_by_fact.append(scopes)
    selections = [
        json.loads(item[1])
        for scopes in scopes_by_fact
        for item in json.loads(scopes)
        if "frame_key_selection" in item[1]
    ]
    assert len(selections) == 1
    selection = selections[0]
    assert selection[-1] == [
        [["int", "-0x8000000000000000"], ["str", "A"]],
        [["int", "0x0"], ["str", "B"]],
        [["none", ""], ["str", "C"]],
    ]
    assert selection[-2][0]["dtype"] == {
        "scope": "TimestampDataType",
        "unit": ["str", "us"],
        "timezone": ["str", "UTC"],
    }
    saved = computed.persist()
    reopened = session.persisted_frame(saved.artifact.id)
    assert isinstance(reopened._backing, StoredFrameBacking)
    assert reopened._backing.metadata.audit.source_facts == facts
