"""Invocation-local preparation reuse without changing exact contribution semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

import marivo.analysis as mv
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
from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline
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


@dataclass
class _TableSpy:
    table: pa.Table
    conversions: int = 0

    def to_pylist(self) -> list[dict[str, object]]:
        self.conversions += 1
        rows: list[dict[str, object]] = self.table.to_pylist()
        return rows

    @property
    def schema(self) -> pa.Schema:
        return self.table.schema

    @property
    def num_rows(self) -> int:
        return int(self.table.num_rows)

    def __getitem__(self, name: str) -> pa.ChunkedArray:
        raise AssertionError(f"Selected output must reuse validated restrictions, not {name}")


@dataclass(frozen=True)
class _Case:
    stage: LoweredLocal
    selected: ExchangeResult
    original: pa.Table
    candidates: pa.Table
    primary: _TableSpy
    grid: BoundTimeGrid


def _case() -> _Case:
    binding = Binding("session", "commerce", "subjects", "scope")
    entity = ref.entity("commerce.subjects")
    keys = (Coordinate(entity, "tenant", "identity"), Coordinate(entity, "id", "identity"))
    grid = bind_grid(mv.time_scope(start=START, end=END), mv.grain("day"), report_timezone="UTC")
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
    primary = _TableSpy(pa.Table.from_pylist(rows, schema=key_schema))
    subject_table = pa.Table.from_pylist(
        [{**row, "subject__key_0": row["key_0"], "subject__key_1": row["key_1"]} for row in rows],
        schema=key_schema.append(pa.field("subject__key_0", pa.string())).append(
            pa.field("subject__key_1", pa.int64())
        ),
    )
    contract = ExchangeContract(
        selected_signature,
        MethodKey("bind_project"),
        "selected-capture",
        key_schema,
        ("key_0", "key_1", "key_2"),
        (PartContract("subject", subject_table.schema, ("key_0", "key_1", "key_2")),),
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
    output = preparation._observation(case.stage, case.candidates, case.selected, case.original)
    rows = output.to_pylist()
    assert [row["value"] for row in rows] == [5, 0, 5, 7, 0]
    assert case.primary.conversions == 1
    assert calls == [START.isoformat(), END.isoformat()]
    assert [tuple(row[f"key_{i}"] for i in range(3)) for row in rows] == [
        tuple(row[f"key_{i}"] for i in range(3)) for row in case.primary.table.to_pylist()
    ]
    assert [(row["subject__key_0"], row["subject__key_1"]) for row in rows] == [
        (row["key_0"], row["key_1"]) for row in rows
    ]
    assert all(row["coverage__complete"] is True for row in rows)
    assert [row["original_state__non_null_count"] for row in rows] == [1, 0, 2, 1, 0]


def test_preparation_does_not_cache_across_calls() -> None:
    case = _case()
    first = preparation._observation(case.stage, case.candidates, case.selected, case.original)
    assert first["value"].to_pylist() == [5, 0, 5, 7, 0]
    assert first["original_state__sum"].to_pylist() == first["value"].to_pylist()
    empty = preparation._observation(
        case.stage, case.candidates.slice(0, 0), case.selected, case.original
    )
    assert empty["value"].to_pylist() == [0] * 5
    assert case.primary.conversions == 2


@pytest.mark.parametrize("failure", ["key", "envelope", "grid", "duplicate"])
def test_preparation_retains_complete_key_and_subject_window_rejections(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    case = _case()
    selected = case.selected
    parts = selected.parts
    rows = case.primary.table.to_pylist()
    if failure == "key":
        selected = replace(
            selected,
            primary=case.primary.table.set_column(1, "key_1", pa.array([True] * len(rows))),
        )
    elif failure == "envelope":
        subject_rows = parts[0].table.to_pylist()
        subject_rows[0]["subject__key_0"] = "escaped"
        selected = replace(
            selected, parts=(ExchangePart("subject", pa.Table.from_pylist(subject_rows)),)
        )
    elif failure == "grid":
        rows[0]["key_2"] = "outside-grid"
        subject_rows = parts[0].table.to_pylist()
        subject_rows[0]["key_2"] = "outside-grid"
        selected = replace(
            selected,
            primary=pa.Table.from_pylist(rows),
            parts=(ExchangePart("subject", pa.Table.from_pylist(subject_rows)),),
        )
    else:
        rows.append(rows[0])
        selected = replace(selected, primary=pa.Table.from_pylist(rows))
    calls: list[str] = []

    class DateParser:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            calls.append(value)
            return datetime.fromisoformat(value)

    monkeypatch.setattr(preparation, "datetime", DateParser)
    with pytest.raises(DomainPreparationError) as caught:
        preparation._observation(case.stage, case.candidates, selected, case.original)
    assert caught.value.constraint_id == "r7.input_binding"
    assert calls == ([] if failure in ("key", "envelope") else [START.isoformat(), END.isoformat()])


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
        primary=case.primary.table.slice(0, 0),
        contract=replace(case.selected.contract, signature=signature),
    )

    class DateParser:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            raise AssertionError(f"Empty selection must not resolve unused bounds {value}")

    monkeypatch.setattr(preparation, "datetime", DateParser)
    output = preparation._observation(case.stage, case.candidates, selected, case.original)
    assert output.num_rows == 0
    assert output.column_names == list(case.stage.output_layout.columns)


def test_preparation_preserves_first_selected_row_deadline() -> None:
    case = _case()
    token = CURRENT.set(ExecuteDeadline(0, lambda: 601.0))
    try:
        with pytest.raises(DomainPreparationError) as caught:
            preparation._observation(case.stage, case.candidates, case.selected, case.original)
        assert caught.value.constraint_id == "r7.execute_timeout"
    finally:
        CURRENT.reset(token)
