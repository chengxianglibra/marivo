"""Invocation-local preparation reuse without changing exact contribution semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo._temporal import time_scope
from marivo.analysis.compiler.graph_lowering import LoweredLocal, canonical_layout
from marivo.analysis.compiler.graph_plan import LocalMethodStage, RouteChoice, plan
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
    SubjectPart,
)
from marivo.analysis.core.rules import (
    DirectMetricDefinition,
    EntityObservationTarget,
    ObserveMetric,
    PreparedObservation,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid
from marivo.analysis.materialization import graph_preparation as preparation
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
)
from marivo.analysis.methods.physical import ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.semantics import MethodKey
from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import TargetDimensionContract, TargetRelationshipContract, TimestampParse
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
)
from tests.shared_fixtures import observation_temporal

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = START + timedelta(days=2)
WIDE = 2**53 + 17


@dataclass(frozen=True)
class _Case:
    stage: LoweredLocal
    selected: ExchangeResult
    original: pa.Table
    candidates: pa.Table
    primary: pd.DataFrame
    grid: BoundTimeGrid


def _case() -> _Case:
    binding = Binding("session", "commerce", "subjects", "scope")
    entity = ref.entity("commerce.subjects")
    keys = (Coordinate(entity, "tenant", "identity"), Coordinate(entity, "id", "identity"))
    grid = bind_grid(time_scope(start=START, end=END), mv.grain("day"), report_timezone="UTC")
    instances = (*keys, Coordinate(entity, "time:grid", "anchor"))
    domain = DomainSignature(binding, "entity", instances, keys, "selected", time_grid=grid)
    subject = SubjectPart(binding, entity, instances, keys, False, True, "subject-v1")
    selected_signature = Signature(domain, parts=(subject,))
    original_signature = Signature(DomainSignature(binding, "entity", keys, keys, "original"))
    shape = SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC"))
    selected_leaf = SourceLeaf(
        SourceDefinition(entity, "selected-v1", ref.datasource("db"), shape),
        selected_signature,
        ScalarType("int64"),
    )
    original_leaf = SourceLeaf(
        SourceDefinition(entity, "original-v1", ref.datasource("db"), shape),
        original_signature,
        ScalarType("int64"),
    )
    contribution = ref.entity("commerce.facts")
    metric = ref.metric("commerce.total")
    relationship_ref = ref.relationship("commerce.facts_subjects")
    relationship = TargetRelationshipContract(
        RefPayloadV1.from_ref(relationship_ref),
        RefPayloadV1.from_ref(contribution),
        RefPayloadV1.from_ref(entity),
        "subject",
        (("tenant", "tenant"), ("subject_id", "id")),
        "many_to_one",
        False,
        False,
    )
    event = TargetDimensionContract(
        RefPayloadV1.from_ref(ref.time_dimension("commerce.facts.event_time")),
        RefPayloadV1.from_ref(contribution),
        "event_time",
        "timestamp",
        False,
        True,
        "second",
        False,
        "UTC",
        TimestampParse(timezone="UTC"),
    )
    graph = MetricExpressionGraphV1(
        "metric-expression/v1",
        ("aggregate",),
        (
            MetricGraphNodeRecordV1(
                "aggregate",
                AggregateNodeV1(
                    "aggregate",
                    RefPayloadV1.from_ref(ref.measure("commerce.facts.amount")),
                    "contribution-v1",
                    "sum",
                    None,
                ),
            ),
        ),
        (),
    )
    definition = DirectMetricDefinition(
        metric,
        graph,
        "aggregate",
        "metric-v1",
        "commerce-v1",
        contribution,
        ref.time_dimension(event.ref.path),
        None,
        "zero",
        (),
    )
    quantity = ObservedQuantity(
        "observed",
        metric,
        "metric-v1",
        None,
        "bounded",
        "contributions",
        "strict",
        "sum_zero@v1",
    )
    target = EntityObservationTarget(domain)
    observation = ObserveMetric(
        definition,
        target,
        quantity,
        contribution,
        (relationship,),
        event,
        START.isoformat(),
        END.isoformat(),
        "amount",
        "int64",
        method="sum",
        grid_window=True,
        temporal=observation_temporal(event),
    )
    fact_keys = tuple(
        Coordinate(contribution, name, "identity") for name in ("tenant", "id", "revision")
    )
    facts = SourceLeaf(
        SourceDefinition(contribution, "facts-v1", ref.datasource("db"), shape),
        Signature(DomainSignature(binding, "entity", fact_keys, fact_keys, "facts")),
        ScalarType("int64"),
    )
    node = method_node(
        (Edge("subject", selected_leaf), Edge("subject", original_leaf)),
        PreparedObservation(observation),
        value_type=ScalarType("int64"),
        sources=(original_leaf, facts),
    )
    planned = plan(node, routes=(RouteChoice(node.identity, "ibis_python"),))
    local = next(stage for stage in planned.stages if isinstance(stage, LocalMethodStage))
    lowered = LoweredLocal(
        local,
        (
            canonical_layout(selected_signature, has_value=False),
            canonical_layout(original_signature, has_value=False),
        ),
        canonical_layout(node.signature, has_value=True),
    )
    rows = [
        {"key_0": tenant, "key_1": WIDE + index, "key_2": cell.identity}
        for tenant, index, cell in (
            ("tenant-a", 1, grid.cells[1]),
            ("tenant-b", 2, grid.cells[0]),
            ("tenant-a", 1, grid.cells[0]),
            ("tenant-b", 2, grid.cells[1]),
            ("tenant-c", 3, grid.cells[0]),
        )
    ]
    key_schema = pa.schema([("key_0", pa.string()), ("key_1", pa.int64()), ("key_2", pa.string())])
    primary = pd.DataFrame(rows)
    subject_rows = [
        {**row, "subject__key_0": row["key_0"], "subject__key_1": row["key_1"]} for row in rows
    ]
    subject_schema = key_schema.append(pa.field("subject__key_0", pa.string())).append(
        pa.field("subject__key_1", pa.int64())
    )
    subject_table = pd.DataFrame(subject_rows)
    contract = ExchangeContract(
        selected_signature,
        MethodKey("bind_project"),
        "selected-capture",
        key_schema,
        ("key_0", "key_1", "key_2"),
        (PartContract("subject", subject_schema, ("key_0", "key_1", "key_2")),),
    )
    selected = ExchangeResult(contract, primary, (ExchangePart("subject", subject_table),), ())
    original = pa.table(
        {"key_0": ["tenant-a", "tenant-b", "tenant-c"], "key_1": [WIDE + 1, WIDE + 2, WIDE + 3]}
    )
    candidates = pa.table(
        {
            "fact_0": ["tenant-a", "tenant-a", "tenant-a", "tenant-b", "tenant-b", "tenant-a"],
            "fact_1": [1, 1, 2, 3, 4, 5],
            "fact_2": [1, 2, 1, 1, 1, 1],
            "member_0": ["tenant-a", "tenant-a", "tenant-a", "tenant-b", "tenant-b", "tenant-a"],
            "member_1": [WIDE + 1, WIDE + 1, WIDE + 1, WIDE + 2, WIDE + 2, WIDE + 1],
            "event_time": pa.array(
                [
                    START,
                    START + timedelta(hours=1),
                    grid.cells[1].start,
                    START + timedelta(hours=2),
                    grid.cells[1].start + timedelta(hours=1),
                    END,
                ],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "amount": pa.array([3, 2, 5, None, 7, 99], type=pa.int64()),
        }
    )
    return _Case(lowered, selected, original, candidates, primary, grid)


def test_preparation_reuses_selected_rows_and_exact_subject_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    calls: list[str] = []

    class DateParser:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            calls.append(value)
            return datetime.fromisoformat(value)

    monkeypatch.setattr(preparation, "datetime", DateParser)
    output, _schema, _retained = preparation._observation(
        case.stage,
        case.candidates.to_pandas(),
        case.candidates.schema,
        case.selected,
        case.original.to_pandas(),
    )
    rows = output.to_dict("records")
    assert [row["value"] for row in rows] == [5, 0, 5, 7, 0]
    assert calls == [START.isoformat(), END.isoformat()]
    assert [tuple(row[f"key_{i}"] for i in range(3)) for row in rows] == [
        tuple(row[f"key_{i}"] for i in range(3)) for row in case.primary.to_dict("records")
    ]
    assert [(row["subject__key_0"], row["subject__key_1"]) for row in rows] == [
        (row["key_0"], row["key_1"]) for row in rows
    ]
    assert all(row["coverage__complete"] is True for row in rows)
    assert [row["original_state__non_null_count"] for row in rows] == [1, 0, 2, 1, 0]


def test_preparation_does_not_cache_across_calls() -> None:
    case = _case()
    first, _schema, _retained = preparation._observation(
        case.stage,
        case.candidates.to_pandas(),
        case.candidates.schema,
        case.selected,
        case.original.to_pandas(),
    )
    assert first["value"].tolist() == [5, 0, 5, 7, 0]
    assert first["original_state__sum"].tolist() == first["value"].tolist()
    empty, _schema, _retained = preparation._observation(
        case.stage,
        case.candidates.to_pandas().iloc[0:0],
        case.candidates.schema,
        case.selected,
        case.original.to_pandas(),
    )
    assert empty["value"].tolist() == [0] * 5


def test_preparation_retains_complete_key_and_subject_window_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for failure in ("key", "envelope", "grid", "duplicate"):
        case = _case()
        selected = case.selected
        parts = selected.parts
        rows = case.primary.to_dict("records")
        if failure == "key":
            invalid_primary = case.primary.copy()
            invalid_primary["key_1"] = [True] * len(invalid_primary)
            selected = replace(
                selected,
                primary=invalid_primary,
            )
        elif failure == "envelope":
            subject_rows = parts[0].table.to_dict("records")
            subject_rows[0]["subject__key_0"] = "escaped"
            selected = replace(
                selected, parts=(ExchangePart("subject", pd.DataFrame(subject_rows)),)
            )
        elif failure == "grid":
            rows[0]["key_2"] = "outside-grid"
            subject_rows = parts[0].table.to_dict("records")
            subject_rows[0]["key_2"] = "outside-grid"
            selected = replace(
                selected,
                primary=pd.DataFrame(rows),
                parts=(ExchangePart("subject", pd.DataFrame(subject_rows)),),
            )
        else:
            rows.append(rows[0])
            selected = replace(selected, primary=pd.DataFrame(rows))
        calls: list[str] = []

        class DateParser:
            @staticmethod
            def fromisoformat(value: str, _calls: list[str] = calls) -> datetime:
                _calls.append(value)
                return datetime.fromisoformat(value)

        with monkeypatch.context() as patch:
            patch.setattr(preparation, "datetime", DateParser)
            with pytest.raises(DomainPreparationError) as caught:
                preparation._observation(
                    case.stage,
                    case.candidates.to_pandas(),
                    case.candidates.schema,
                    selected,
                    case.original.to_pandas(),
                )
        assert caught.value.constraint_id == "r7.input_binding"
        assert calls == (
            [] if failure in ("key", "envelope") else [START.isoformat(), END.isoformat()]
        )


def test_preparation_empty_selection_does_not_eagerly_parse_or_require_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    signature = replace(
        case.selected.contract.signature,
        domain=replace(case.selected.contract.signature.domain, time_grid=None),
    )
    selected = replace(
        case.selected,
        primary=case.primary.iloc[0:0].copy(),
        contract=replace(case.selected.contract, signature=signature),
    )

    class DateParser:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            raise AssertionError(f"Empty selection must not resolve unused bounds {value}")

    monkeypatch.setattr(preparation, "datetime", DateParser)
    output, _schema, _retained = preparation._observation(
        case.stage,
        case.candidates.to_pandas(),
        case.candidates.schema,
        selected,
        case.original.to_pandas(),
    )
    assert output.empty
    assert list(output.columns) == list(case.stage.output_layout.columns)


def test_preparation_has_no_execution_clock() -> None:
    case = _case()
    result = preparation._observation(
        case.stage,
        case.candidates.to_pandas(),
        case.candidates.schema,
        case.selected,
        case.original.to_pandas(),
    )
    assert result is not None
