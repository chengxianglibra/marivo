"""Frozen declarations and specialization probes from panda@440dabc14f."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal, TypedDict

import pytest

import marivo.analysis as mv
from marivo.analysis.core.domain_captures import DomainPreparationError, EntryAxisCapture
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainKind,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.rules import (
    AnchorObserve,
    DirectMetricDefinition,
    EntityObservationTarget,
    FunnelAxesPrepare,
    FunnelReduce,
    ObserveMetric,
    OccurrenceFilter,
    PartsTransport,
    RowState,
)
from marivo.analysis.methods import builtin
from marivo.analysis.methods.anchor_physical import admit_observation
from marivo.analysis.methods.builtin import specialize_arity, specialize_numeric
from marivo.analysis.methods.consumer_rules import (
    native_distribution,
    prepared_numeric,
    subject_image,
)
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.funnel_physical import admit_axes
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
    TargetRelationshipContract,
    TargetSnapshotVersion,
    TargetValidityVersion,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
)


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
                key.shape, backend="sqlite" if key.shape.backend == "duckdb" else "duckdb"
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


def test_renamed_implementation_still_requires_genuine_registration() -> None:
    item = next(
        i
        for i in REGISTRY.lookup(MethodKey("parts_transport")).implementations
        if i.key.route == "ibis"
    )
    assert isinstance(item.qualification, Qualified)
    renamed = replace(item, qualification=replace(item.qualification, implementation_id="x"))
    with pytest.raises(MethodRegistrationError, match=r"implemented R3\.4 consumer"):
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
    assert selected.contract_version == 4
    assert selected.precision == "checked_int64"
    if isinstance(value, DecimalType):
        unqualified = replace(
            key, shape=SourceShape("sqlite", "table", "native", TimeShape("instant", "us", "UTC"))
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


def _axis(logical_type: Literal["string", "int64"], history: str = "direct") -> EntryAxisCapture:
    subject = _entity("sales.subjects")
    if history == "direct":
        return EntryAxisCapture(
            _dimension("sales.subjects", "region", logical_type),
            subject,
            (),
            (subject,),
            ("subjects",),
        )
    target = _entity("sales.history")
    day = RefPayloadV1.from_ref(ref.time_dimension("sales.history.day"))
    version = (
        TargetSnapshotVersion(day, "day", "date", "UTC", None)
        if history == "snapshot"
        else TargetValidityVersion(
            day,
            RefPayloadV1.from_ref(ref.time_dimension("sales.history.end")),
            "day",
            "end",
            "closed_open",
            (None,),
            "UTC",
        )
    )
    target = replace(target, version=version)
    path = TargetRelationshipContract(
        RefPayloadV1.from_ref(ref.relationship("sales.history_path")),
        subject.ref,
        target.ref,
        "history",
        (("id", "id"),),
        "many_to_one",
        False,
        False,
    )
    return EntryAxisCapture(
        _dimension("sales.history", "region", logical_type),
        subject,
        (path,),
        (subject, target),
        ("subjects", "history"),
    )


@pytest.mark.parametrize("kind", ["prepare", "reduce"])
@pytest.mark.parametrize(
    "case,accepted",
    [
        ("string", True),
        ("int64", True),
        ("pair", True),
        ("reverse", True),
        ("strings", True),
        ("integers", True),
        ("triple", True),
        ("long_direct", True),
        ("empty", False),
        ("mixed_history", True),
        ("two_history", True),
        ("snapshot", True),
        ("validity", True),
        ("int_history", True),
        ("subject_version", False),
        ("timestamp", False),
        ("zone", False),
        ("closure", False),
        ("open_end", False),
        ("long_path", True),
    ],
)
def test_sqlite_funnel_parameter_boundaries(kind: str, case: str, accepted: bool) -> None:
    axis = _axis(
        "string",
        "snapshot"
        if case in ("snapshot", "timestamp", "zone", "subject_version", "long_path")
        else "validity"
        if case in ("validity", "closure", "open_end")
        else "direct",
    )
    if case == "int_history":
        axis = _axis("int64", "snapshot")
    elif case in ("timestamp", "zone", "closure", "open_end"):
        target = axis.entities[-1]
        version = target.version
        assert version is not None
        if case == "timestamp":
            target = replace(target, columns=(("id", "int64"), ("day", "timestamp")))
        elif case == "zone":
            version = replace(version, timezone="Asia/Tokyo")
        elif case == "closure":
            assert isinstance(version, TargetValidityVersion)
            version = replace(version, interval="closed_closed")
        else:
            assert isinstance(version, TargetValidityVersion)
            version = replace(version, open_end=("9999-12-31",))
        axis = replace(axis, entities=(axis.subject, replace(target, version=version)))
    elif case == "subject_version":
        subject = replace(axis.subject, version=axis.entities[-1].version)
        axis = replace(axis, subject=subject, entities=(subject, axis.entities[-1]))
    elif case == "long_path":
        middle = _entity("sales.middle")
        first = replace(axis.path[0], to_entity_ref=middle.ref)
        last = replace(axis.path[0], from_entity_ref=middle.ref)
        axis = replace(
            axis,
            path=(first, last),
            entities=(axis.subject, middle, axis.entities[-1]),
            source_ids=("subjects", "middle", "history"),
        )
    axes = (
        (_axis("int64"), axis)
        if case == "pair"
        else (axis, _axis("int64"))
        if case == "reverse"
        else (axis, _axis("string"))
        if case == "strings"
        else (_axis("int64"), _axis("int64"))
        if case == "integers"
        else (axis, _axis("int64"), _axis("string"))
        if case == "triple"
        else tuple(_axis("string" if i % 2 else "int64") for i in range(17))
        if case == "long_direct"
        else (axis, _axis("string", "snapshot"))
        if case == "mixed_history"
        else (_axis("string", "snapshot"), _axis("string", "validity"))
        if case == "two_history"
        else ()
        if case == "empty"
        else (_axis("int64"),)
        if case == "int64"
        else (axis,)
    )
    axes = tuple(
        replace(
            a,
            dimension=replace(
                a.dimension,
                ref=RefPayloadV1.from_ref(ref.dimension(f"{a.dimension.entity_ref.path}.axis_{i}")),
            ),
        )
        for i, a in enumerate(axes)
    )
    name: MethodName = "funnel.entry_axes" if kind == "prepare" else "funnel.reduce"
    item = next(
        i
        for i in REGISTRY.lookup(MethodKey(name)).implementations
        if isinstance(i.key.shape, SourceShape) and i.key.shape.backend == "sqlite"
    )
    params = (
        FunnelAxesPrepare("start", "end", axes, "event")
        if kind == "prepare"
        else FunnelReduce(_domain(), "scope", axes, "subjects")
    )
    if accepted:
        builtin.admit(item, params)
    else:
        with pytest.raises(
            MethodRegistrationError,
            match="unversioned Subject through to-one UTC DATE history paths",
        ):
            builtin.admit(item, params)
    assert isinstance(item.qualification, Qualified)
    renamed = replace(item, qualification=replace(item.qualification, implementation_id="x"))
    if accepted:
        admit_axes(renamed, params)
    else:
        with pytest.raises(
            MethodRegistrationError,
            match="unversioned Subject through to-one UTC DATE history paths",
        ):
            admit_axes(renamed, params)


@pytest.mark.parametrize("logical_type", ("float64", "boolean", "date32[day]"))
def test_funnel_axis_types_reject_at_capture_owner(logical_type: str) -> None:
    axis = _axis("string")
    with pytest.raises(DomainPreparationError, match="string/int64 entry-axis path"):
        replace(axis, dimension=replace(axis.dimension, logical_type=logical_type))


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
    )


@pytest.mark.parametrize(
    "case",
    [
        "accepted",
        "type",
        "method",
        "empty",
        "fold",
        "distinct",
        "coordinates",
        "filters",
        "multiple",
        "composition",
    ],
)
def test_sqlite_anchor_observation_parameter_boundaries(case: str) -> None:
    item = next(
        i
        for i in REGISTRY.lookup(MethodKey("anchor.observe")).implementations
        if isinstance(i.key.shape, SourceShape) and i.key.shape.backend == "sqlite"
    )
    observation = _observation()
    if case == "type":
        observation = replace(observation, amount_type="float64")
    elif case == "method":
        observation = replace(observation, method="mean")
    elif case == "empty":
        observation = replace(observation, metric=replace(observation.metric, empty_rule="null"))
    elif case == "fold":
        observation = replace(observation, fold="last")
    elif case == "distinct":
        observation = replace(observation, distinct_columns=("amount",))
    elif case == "coordinates":
        observation = replace(
            observation, coordinates=(_dimension("sales.facts", "region", "string"),)
        )
    elif case == "filters":
        observation = replace(
            observation,
            filters=(
                OccurrenceFilter(_dimension("sales.facts", "region", "string"), "==", "east"),
            ),
        )
    observations = (observation, observation) if case == "multiple" else (observation,)
    params = AnchorObserve(
        mv.elapsed(mv.duration(seconds=10)),
        observations,
        Signature(_domain(), observation.quantity),
    )
    if case == "composition":
        from marivo.analysis.core.rules import OriginalRatio

        params = replace(
            params,
            composition=OriginalRatio(
                observation.quantity, ref.metric("sales.total"), ref.metric("sales.other")
            ),
        )
    if case in ("accepted", "type", "empty", "filters"):
        builtin.admit(item, params)
    else:
        with pytest.raises(
            MethodRegistrationError, match="count or additive int64/float64 Anchor sums"
        ):
            builtin.admit(item, params)
    assert isinstance(item.qualification, Qualified)
    renamed = replace(item, qualification=replace(item.qualification, implementation_id="x"))
    if case in ("accepted", "type", "empty", "filters"):
        admit_observation(renamed, params)
    else:
        with pytest.raises(
            MethodRegistrationError, match="count or additive int64/float64 Anchor sums"
        ):
            admit_observation(renamed, params)


@pytest.mark.parametrize("case", ["accepted", "type", "distinct", "start", "end"])
def test_native_distribution_parameter_boundaries(case: str) -> None:
    item = next(
        i for r in REGISTRY.registrations for i in r.implementations if native_distribution(i)
    )
    params = replace(_observation(), method="count_distinct")
    if case == "type":
        params = replace(params, amount_type="float64")
    elif case == "distinct":
        params = replace(params, distinct_columns=("amount",))
    elif case == "start":
        params = replace(params, start=None)
    elif case == "end":
        params = replace(params, end=None)
    if case in ("accepted", "type"):
        builtin.admit(item, params)
    else:
        with pytest.raises(
            MethodRegistrationError, match="bounded single-column native distribution"
        ):
            builtin.admit(item, params)


@pytest.mark.parametrize(
    "method,amount_type,accepted",
    [
        (method, amount_type, True)
        for method in ("count_distinct", "approx_count_distinct")
        for amount_type in ("int64", "float64", "string", "boolean", "date", "timestamp")
    ]
    + [
        ("count_distinct", "decimal(38, 6)", True),
        ("approx_count_distinct", "decimal(38, 6)", True),
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
        tree = ast.parse(path.read_text())
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
