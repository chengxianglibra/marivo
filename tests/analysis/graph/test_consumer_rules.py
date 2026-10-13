"""Frozen declarations and specialization probes, with A1 typed coverage check support."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal, TypedDict

import pytest

from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainKind,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import (
    DirectMetricDefinition,
    EntityObservationTarget,
    ObserveMetric,
    PartsTransport,
    RowState,
)
from marivo.analysis.methods import builtin
from marivo.analysis.methods.builtin import specialize_arity, specialize_numeric
from marivo.analysis.methods.consumer_rules import (
    native_distribution,
    prepared_numeric,
    subject_image,
)
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
    ValueType,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistration
from marivo.analysis.methods.semantics import MethodKey, MethodName
from marivo.datasource.ir import TableSourceIR
from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import (
    TargetDimensionContract,
    TargetEntityContract,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
)
from tests.shared_fixtures import observation_temporal


class _Baseline(TypedDict):
    count: int
    declarations: str
    specialization: str
    exact_indices: list[int]
    prepared_indices: list[int]
    subject_indices: list[int]
    distribution_indices: list[int]


_BASELINE: dict[str, _Baseline] = json.loads(
    Path(__file__).with_name("consumer_rule_baseline.json").read_text()
)


def _record(item: Implementation) -> str:
    # Explicit pre-O2 fields: the internal specialization policy is not persisted.
    return repr(
        (
            item.key,
            item.checks,
            item.parts,
            item.precision,
            item.resources,
            item.qualification,
            item.contract_version,
        )
    )


def _declaration_digest(items: tuple[Implementation, ...]) -> str:
    return hashlib.sha256("\n".join(map(_record, items)).encode()).hexdigest()


def _requests(key: QualificationKey) -> Iterator[QualificationKey]:
    yield key
    types: tuple[ValueType, ...] = (
        ScalarType("int64"),
        ScalarType("float64"),
        DecimalType(1, 0),
        DecimalType(18, 2),
        DecimalType(38, 0),
        DecimalType(38, 6),
        DecimalType(38, 38),
        *(DurationType(unit) for unit in ("s", "ms", "us", "ns")),
        ScalarType("string"),
        ScalarType("boolean"),
        ScalarType("date"),
        ScalarType("timestamp"),
    )
    for typ in types:
        yield replace(key, input_types=(typ,) * len(key.input_types))
        if len(key.input_types) > 1:
            yield replace(key, input_types=(typ, *key.input_types[1:]))
    for arity in (1, 2, 3, 16, 17, 64, 65):
        yield replace(
            key,
            input_types=(key.input_types[0],) * arity,
            input_domains=(key.input_domains[0],) * arity,
        )
    group: DomainKind = "group"
    yield replace(key, input_domains=(group,) * len(key.input_types))
    yield replace(key, shape=replace(key.shape, time=TimeShape("instant", "us", "Asia/Tokyo")))
    if isinstance(key.shape, SourceShape):
        yield replace(key, route="ibis_python" if key.route == "ibis" else "ibis")
        yield replace(key, shape=replace(key.shape, table_kind="other"))
        yield replace(
            key,
            shape=replace(
                key.shape, backend="postgres" if key.shape.backend == "duckdb" else "duckdb"
            ),
        )


def _specialization_digest(items: tuple[Implementation, ...]) -> str:
    digest = hashlib.sha256()
    for item in items:
        for key in _requests(item.key):
            digest.update(repr(key).encode())
            try:
                result = specialize_numeric(specialize_arity(item, len(key.input_types)), key)
            except (MethodRegistrationError, ValueError) as error:
                value = repr((type(error).__name__, str(error)))
            else:
                value = repr((result.key == key, _record(result)))
            digest.update(value.encode())
            digest.update(b"\n")
    return digest.hexdigest()


@pytest.mark.parametrize("registration", REGISTRY.registrations, ids=lambda r: str(r.semantics.key))
def test_declarations_and_dynamic_specialization_match_baseline(
    registration: MethodRegistration,
) -> None:
    expected = _BASELINE[str(registration.semantics.key)]
    items = registration.implementations
    # The explicitly requested remote ordinary Count adds eight keys. Preserve
    # the original ordered declarations and specialization oracle unchanged.
    if registration.semantics.key == MethodKey("metric.count"):
        additions = tuple(
            item
            for item in items
            if isinstance(item.key.shape, SourceShape)
            and item.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
            and item.key.route == "ibis"
            and item.key.input_domains == ("entity",)
        )
        assert len(additions) == 8
        items = tuple(item for item in items if item not in additions)
    if registration.semantics.key == MethodKey("state_rollup.count"):
        additions = tuple(
            item
            for item in items
            if isinstance(item.key.shape, SourceShape)
            and item.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
        )
        assert len(additions) == 12
        items = tuple(item for item in items if item not in additions)
    assert len(items) == expected["count"]
    assert _declaration_digest(items) == expected["declarations"]
    assert _specialization_digest(items) == expected["specialization"]
    assert [n for n, i in enumerate(items) if i.numeric_specialization == "exact"] == expected[
        "exact_indices"
    ]
    for name, predicate in (
        ("prepared", prepared_numeric),
        ("subject", subject_image),
        ("distribution", native_distribution),
    ):
        actual = [n for n, i in enumerate(items) if predicate(i)]
        assert (
            actual
            == {
                "prepared": expected["prepared_indices"],
                "subject": expected["subject_indices"],
                "distribution": expected["distribution_indices"],
            }[name]
        )


@pytest.mark.parametrize("name", ["row.count", "metric.linear", "state_rollup.sum_zero"])
def test_specialization_policy_is_independent_of_provenance_id(name: MethodName) -> None:
    entries = REGISTRY.lookup(MethodKey(name)).implementations
    for policy in ("consumer", "exact"):
        item = next(i for i in entries if i.numeric_specialization == policy)
        assert isinstance(item.qualification, Qualified)
        renamed = replace(
            item,
            qualification=replace(item.qualification, implementation_id="arbitrary.provenance"),
        )
        for key in _requests(item.key):
            expected = specialize_numeric(specialize_arity(item, len(key.input_types)), key)
            actual = specialize_numeric(specialize_arity(renamed, len(key.input_types)), key)
            assert _record(replace(actual, qualification=expected.qualification)) == _record(
                expected
            )


@pytest.mark.parametrize("predicate", [prepared_numeric, subject_image, native_distribution])
def test_shared_consumer_rules_do_not_read_provenance_and_reject_neighbors(
    predicate: Callable[[Implementation], bool],
) -> None:
    item = next(i for r in REGISTRY.registrations for i in r.implementations if predicate(i))
    assert isinstance(item.qualification, Qualified)
    assert predicate(
        replace(item, qualification=replace(item.qualification, implementation_id="x"))
    )
    assert not predicate(replace(item, parts=()))
    assert not predicate(
        replace(item, qualification=replace(item.qualification, consumer_id="other"))
    )
    with pytest.raises(MethodRegistrationError, match="version 1"):
        replace(item.key.method, version=2)
    assert not predicate(replace(item, key=replace(item.key, method=MethodKey("bind_project"))))
    assert isinstance(item.key.shape, SourceShape)
    shape = item.key.shape
    assert not predicate(
        replace(item, key=replace(item.key, shape=replace(shape, table_kind="other")))
    )
    assert not predicate(
        replace(
            item,
            key=replace(
                item.key, shape=replace(shape, time=TimeShape("instant", "us", "Asia/Tokyo"))
            ),
        )
    )


def test_selected_implementation_provenance_does_not_change_parameter_admission() -> None:
    item = next(
        i
        for i in REGISTRY.lookup(MethodKey("parts_transport")).implementations
        if i.key.route == "ibis"
    )
    assert isinstance(item.qualification, Qualified)
    renamed = replace(item, qualification=replace(item.qualification, implementation_id="x"))
    builtin.admit(renamed, PartsTransport("view", _domain(), (), True))


def test_invalid_specialization_policy_is_rejected() -> None:
    item = replace(REGISTRY.registrations[0].implementations[0])
    object.__setattr__(item, "numeric_specialization", "unknown")
    with pytest.raises(MethodRegistrationError, match="complete immutable implementation"):
        item.__post_init__()


@pytest.mark.parametrize("value", [DecimalType(38, 6), DurationType("ns")])
def test_registry_preserves_specialized_selection_identity_and_exact_route(
    value: ValueType,
) -> None:
    source = Signature(_domain(), _observation().quantity)
    target = DomainSignature(source.domain.binding, "singleton", (), (), "all")
    params = RowState("count", target, "row-count", "count_all")
    key = QualificationKey(
        MethodKey("row.count"),
        (value,),
        ("entity",),
        SourceShape("duckdb", "table", "native", NoTime()),
        "ibis",
    )
    selected = REGISTRY.select(key, (source,), params).implementation
    assert selected.key == key
    assert isinstance(selected.qualification, Qualified)
    assert selected.qualification.implementation_id == "r34.ibis.row.count@v1"
    assert selected.contract_version == 7
    assert selected.precision == "checked_int64"
    if isinstance(value, DecimalType):
        unqualified = replace(
            key, shape=SourceShape("mysql", "table", "native", TimeShape("instant", "us", "UTC"))
        )
        with pytest.raises(MethodRegistrationError, match="qualified exact key"):
            REGISTRY.select(unqualified, (source,), params)


def _domain() -> DomainSignature:
    keys = (Coordinate(ref.entity("sales.subjects"), "id", "identity"),)
    return DomainSignature(
        Binding("session", "sales", "subjects", "scope"), "entity", keys, keys, "subjects"
    )


def _dimension(entity: str, name: str, logical_type: str) -> TargetDimensionContract:
    return TargetDimensionContract(
        RefPayloadV1.from_ref(ref.dimension(f"{entity}.{name}")),
        RefPayloadV1.from_ref(ref.entity(entity)),
        name,
        logical_type,
        False,
        False,
        None,
        False,
        None,
    )


def _entity(name: str) -> TargetEntityContract:
    return TargetEntityContract(
        RefPayloadV1.from_ref(ref.entity(name)),
        RefPayloadV1.from_ref(ref.datasource("db")),
        "entity-v1",
        TableSourceIR(name.split(".")[-1]),
        ("id",),
        (("id", "int64"),),
        ("id",),
        (("id", "int64"), ("day", "date32[day]"), ("end", "date32[day]")),
        None,
        (),
    )


def _observation() -> ObserveMetric:
    metric = ref.metric("sales.total")
    entity = ref.entity("sales.facts")
    event_ref = ref.time_dimension("sales.facts.time")
    expression = MetricExpressionGraphV1(
        "metric-expression/v1",
        ("sum",),
        (
            MetricGraphNodeRecordV1(
                "sum",
                AggregateNodeV1(
                    "aggregate",
                    RefPayloadV1.from_ref(ref.measure("sales.facts.amount")),
                    "facts-v1",
                    "sum",
                    None,
                ),
            ),
        ),
        (),
    )
    definition = DirectMetricDefinition(
        metric, expression, "sum", "metric-v1", "sales-v1", entity, event_ref, None, "zero", ()
    )
    quantity = ObservedQuantity(
        "total", metric, "metric-v1", None, "scope", "facts", "strict", "sum_zero@v1"
    )
    event = replace(
        _dimension("sales.facts", "time", "timestamp"),
        ref=RefPayloadV1.from_ref(event_ref),
        is_time_dimension=True,
        timezone="UTC",
    )
    return ObserveMetric(
        definition,
        EntityObservationTarget(_domain()),
        quantity,
        entity,
        (),
        event,
        "start",
        "end",
        "amount",
        "int64",
        temporal=observation_temporal(event),
    )


@pytest.mark.parametrize("case", ["distinct", "start", "end"])
def test_native_distribution_parameter_boundaries(case: str) -> None:
    item = next(
        i for r in REGISTRY.registrations for i in r.implementations if native_distribution(i)
    )
    params = replace(_observation(), method="count_distinct")
    if case == "distinct":
        params = replace(params, distinct_columns=("amount",))
    elif case == "start":
        params = replace(params, start=None)
    elif case == "end":
        params = replace(params, end=None)
    with pytest.raises(MethodRegistrationError, match="bounded single-column native distribution"):
        builtin.admit(item, params)


@pytest.mark.parametrize(
    "method,amount_type,accepted",
    [
        ("count_distinct", amount_type, True)
        for amount_type in ("int64", "float64", "string", "boolean", "date", "timestamp")
    ]
    + [
        # The scalar carrier list is shared by exact and approximate distinct;
        # keep the second method's name covered without replaying every carrier.
        ("approx_count_distinct", "float64", True),
        ("count_distinct", "decimal(38, 6)", True),
        ("count_distinct", "interval('us')", False),
        ("percentile", "float64", True),
        ("approx_percentile", "float64", True),
        ("percentile", "decimal(38, 6)", False),
        ("percentile", "interval('us')", False),
        ("approx_percentile", "string", False),
    ],
)
def test_native_distribution_method_carriers(
    method: Literal["count_distinct", "approx_count_distinct", "percentile", "approx_percentile"],
    amount_type: str,
    accepted: bool,
) -> None:
    item = next(
        i for r in REGISTRY.registrations for i in r.implementations if native_distribution(i)
    )
    params = replace(_observation(), method=method, amount_type=amount_type)
    if accepted:
        builtin.admit(item, params)
    else:
        with pytest.raises(
            MethodRegistrationError, match="bounded single-column native distribution"
        ):
            builtin.admit(item, params)


def test_production_behavior_does_not_branch_on_implementation_id() -> None:
    import ast

    assert tuple(str(r.semantics.key) for r in REGISTRY.registrations) == tuple(_BASELINE)
    root = Path(__file__).parents[3] / "marivo" / "analysis"
    for path in root.rglob("*.py"):
        source = path.read_text()
        if "implementation_id" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
            ):
                assert not any(
                    isinstance(child, ast.Attribute) and child.attr == "implementation_id"
                    for child in ast.walk(node.func.value)
                ), path
