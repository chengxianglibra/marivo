"""Independent occurrence preparation, retained precision and placement oracles."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.analysis.compiler.graph_lowering import CoordinateColumn, RelationLayout, SourceBinding
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.domain_captures import DomainPreparationError, EventCapture
from marivo.analysis.core.graph import Edge, FixedLeaf, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import Binding, Coordinate, DomainSignature, Signature
from marivo.analysis.core.rules import OccurrencePrepare
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_protocol import (
    digest,
    fixed_signature,
    freeze_graph,
    thaw_graph,
)
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import FixedShape, ScalarType, SourceShape, TimeShape
from marivo.analysis.refs import ArtifactRef
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.refs import RefPayloadV1, ref
from marivo.semantic.ir import (
    EventIR,
    EventParticipantIR,
    SourceLocation,
    TargetDimensionContract,
    TargetEntityContract,
    TargetRelationshipContract,
    TimestampParse,
)


def _payload(value):
    return RefPayloadV1.from_ref(value)


def _entity(name, source, keys):
    return TargetEntityContract(
        _payload(ref.entity(name)),
        _payload(ref.datasource("db")),
        name + "@v1",
        source,
        keys,
        tuple((key, key) for key in keys),
        keys,
        (),
        None,
        (),
    )


def _dimension(entity, column, *, time=False, zone="UTC"):
    return TargetDimensionContract(
        _payload(
            ref.time_dimension(entity.ref.path + "." + column)
            if time
            else ref.dimension(entity.ref.path + "." + column)
        ),
        entity.ref,
        column,
        "timestamp" if time else "string",
        False,
        time,
        "second" if time else None,
        False,
        zone if time else None,
        TimestampParse(timezone=zone) if time else None,
    )


def _keys(profile, prefix, size):
    if profile == "s":
        return {prefix: [prefix + str(index) for index in range(size)]}
    if profile == "i":
        return {prefix: [9007199254740993 + index for index in range(size)]}
    return {
        prefix: [prefix + str(index) for index in range(size)],
        prefix + "2": [9007199254740993 + index for index in range(size)],
    }


@contextmanager
def _case(
    tmp_path, subject="s", occurrence="s", form="table", unit="us", zone="UTC", *, empty=False
):
    store = SessionStore(tmp_path)
    session = store.create_session("r72")
    runtime = DatasetRuntime(store, session.session_ref)
    users = _keys(subject, "subject", 2)
    facts = _keys(occurrence, "occurrence", 3)
    for name, values in users.items():
        facts[name] = [values[0], values[0], values[1]]
    factor = {"s": 1, "ms": 1000, "us": 1000000, "ns": 1000000000}[unit]
    facts["amount"] = [10, 20, 30]
    facts["instant"] = pa.array(
        [factor, 2 * factor + (123 if unit == "ns" else 0), 3 * factor],
        type=pa.timestamp(unit, tz="UTC"),
    )
    users_table, facts_table = pa.table(users), pa.table(facts)
    if empty:
        facts_table = facts_table.slice(0, 0)
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    if form == "table":
        backend.create_table("users", users_table)
        backend.create_table("facts", facts_table)
        user_source, event_source = TableSourceIR("users"), TableSourceIR("facts")
    else:
        pq.write_table(users_table, tmp_path / "users.parquet")
        pq.write_table(facts_table, tmp_path / "facts.parquet")
        user_source, event_source = (
            ParquetSourceIR(str(tmp_path / "users.parquet")),
            ParquetSourceIR(str(tmp_path / "facts.parquet")),
        )
    backend.disconnect()
    shape = SourceShape(
        "duckdb", form, "native" if form == "table" else "parquet", TimeShape("instant", unit, zone)
    )
    binding = Binding(session.session_ref, "commerce", "users", "all")
    subject_contract = _entity("commerce.users", user_source, tuple(users))
    occurrence_contract = _entity(
        "commerce.facts",
        event_source,
        tuple(name for name in facts if name.startswith("occurrence")),
    )

    def leaf(contract):
        keys = tuple(
            Coordinate(ref.entity(contract.ref.path), name, "identity")
            for name in contract.primary_key
        )
        return SourceLeaf(
            SourceDefinition(
                ref.entity(contract.ref.path),
                contract.dependency_fingerprint,
                ref.datasource("db"),
                shape,
            ),
            Signature(DomainSignature(binding, "entity", keys, keys, contract.ref.path)),
            ScalarType("int64"),
        )

    population, occurrence_leaf = leaf(subject_contract), leaf(occurrence_contract)
    relationship = TargetRelationshipContract(
        _payload(ref.relationship("commerce.facts_to_users")),
        occurrence_contract.ref,
        subject_contract.ref,
        "subject",
        tuple((name, name) for name in users),
        "many_to_one",
        False,
        False,
    )
    definition = EventIR(
        "commerce.hit",
        "commerce",
        "hit",
        occurrence_contract.ref.path,
        tuple(occurrence_contract.ref.path + "." + key for key in occurrence_contract.primary_key),
        "commerce.facts.instant",
        (EventParticipantIR("subject", (relationship.ref.path,), "one"),),
        "all_rows",
        AiContextIR(),
        "hit",
        SourceLocation("r72.py", 1),
        "all_rows@v1",
    )
    event = EventCapture(
        ref.event("commerce.hit"),
        "hit@v1",
        occurrence_contract,
        occurrence_leaf.identity,
        tuple(
            replace(
                _dimension(occurrence_contract, name),
                logical_type="int64"
                if pa.types.is_int64(facts_table.schema.field(name).type)
                else "string",
            )
            for name in occurrence_contract.primary_key
        ),
        _dimension(occurrence_contract, "instant", time=True),
        "subject",
        subject_contract,
        (relationship,),
        "all_rows@v1",
        "all_rows",
        "commerce@v1",
        definition,
    )
    keys = (
        Coordinate(ref.entity("commerce.facts"), "event_binding", "identity"),
        *(
            Coordinate(ref.entity("commerce.facts"), name, "identity")
            for name in occurrence_contract.primary_key
        ),
    )
    output = DomainSignature(binding, "occurrence", keys, keys, "hit-inputs")
    params = OccurrencePrepare(
        output, (event,), "1970-01-01T00:00:00+00:00", "1970-01-01T00:00:10+00:00"
    )
    root = method_node(
        (Edge("subject", population),),
        params,
        value_type=ScalarType("int64"),
        sources=(population, occurrence_leaf),
    )
    leaves = (population, occurrence_leaf)
    contracts = (subject_contract, occurrence_contract)
    bindings = tuple(
        SourceKeyBinding(
            item,
            shape,
            item.definition.fingerprint,
            digest(canonical_json(contract.source.to_dict())),
        )
        for item, contract in zip(leaves, contracts, strict=True)
    )
    submissions = []
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("r72.py", 1)
    )

    @contextmanager
    def source():
        with SourceSession(
            provider_for("duckdb"), datasource, ibis.duckdb.connect(tmp_path / "source.duckdb")
        ) as selected:
            physical = tuple(
                selected.bind(contract.source, source_identity=item.identity)
                for item, contract in zip(leaves, contracts, strict=True)
            )
            source_bindings = tuple(
                SourceBinding(
                    item,
                    bound,
                    RelationLayout(
                        tuple(
                            CoordinateColumn(key, key.field)
                            for key in item.signature.domain.instance_key
                        ),
                        None,
                        (),
                    ),
                )
                for item, bound in zip(leaves, physical, strict=True)
            )
            try:
                yield selected, source_bindings
            finally:
                submissions.extend(selected.submissions)

    yield runtime, root, bindings, source, submissions


PROFILES = [
    (s, o, form, unit, zone)
    for s, o in product(("s", "i", "si"), repeat=2)
    for form, unit in (
        ("table", "us"),
        ("parquet", "s"),
        ("parquet", "ms"),
        ("parquet", "us"),
        ("parquet", "ns"),
    )
    for zone in ("UTC", "America/New_York")
]


@pytest.mark.runtime
@pytest.mark.parametrize("subject,occurrence,form,unit,zone", PROFILES)
def test_source_fixed_and_cold_precision(tmp_path, subject, occurrence, form, unit, zone):
    with _case(tmp_path, subject, occurrence, form, unit, zone) as (
        runtime,
        root,
        bindings,
        source,
        submissions,
    ):
        assert thaw_graph(freeze_graph(root)).fingerprint == root.fingerprint
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        assert result.primary.num_rows == 3
        assert len({tuple(row.values()) for row in cell_rows(result.primary)}) == 3
        expected_keys = _keys(occurrence, "occurrence", 3)
        actual = {
            tuple(row[name] for name in result.contract.key_fields)
            for row in cell_rows(result.primary)
        }
        assert actual == {
            ("commerce.hit", *(values[index] for values in expected_keys.values()))
            for index in range(3)
        }
        mappings = next(part.table for part in result.parts if part.role == "subject").to_pylist()
        users = _keys(subject, "subject", 2)
        expected_mapping = {
            ("commerce.hit", *(values[index] for values in expected_keys.values())): tuple(
                values[0 if index < 2 else 1] for values in users.values()
            )
            for index in range(3)
        }
        assert {
            tuple(row[name] for name in result.contract.key_fields): tuple(
                row[f"subject__key_{i}"] for i in range(len(users))
            )
            for row in mappings
        } == expected_mapping
        precision = json.loads(result.primary.schema.metadata[b"r7.precision"])
        assert precision[0]["declared_unit"] == unit
        assert precision[0]["source_unit"] == ("ms" if form == "parquet" and unit == "s" else unit)
        assert precision[0]["possible_loss"] is (unit == "ns")
        times = next(part.table for part in result.parts if part.role == "occurrences")[
            "occurrences__occurred_at"
        ].to_pylist()
        assert sorted(point.second for point in times) == [1, 2, 3]
        assert all(point.microsecond == 0 for point in times)
        assert all(submission.state == "succeeded" for submission in submissions)
        fixed_leaf = FixedLeaf(
            ArtifactRef(artifact.artifact_ref),
            root.fingerprint,
            fixed_signature(artifact.descriptor),
            ScalarType("int64"),
            FixedShape(TimeShape("instant", "us", zone)),
        )
        continuation = method_node(
            (Edge("subject", fixed_leaf),),
            replace(root.parameters, output=fixed_leaf.signature.domain),
            value_type=ScalarType("int64"),
        )
        fixed = runtime._execute_graph(
            continuation, (RouteChoice(continuation.identity, "artifact_python"),)
        )
        fixed_result = read_result(tmp_path, fixed.descriptor)
        assert logical_table(fixed_result.primary).equals(logical_table(result.primary))
        assert (
            runtime._execute_graph(
                continuation, (RouteChoice(continuation.identity, "artifact_python"),)
            ).artifact_ref
            == fixed.artifact_ref
        )
        for path in (*tmp_path.glob("*.parquet"), *tmp_path.glob("source.duckdb*")):
            path.unlink()
        script = """
import sys
from pathlib import Path
import ibis
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.graph_store import artifact
from marivo.analysis.materialization.graph_storage import read_result
def forbidden(*args, **kwargs):
    raise AssertionError('source must be offline')
SourceSession.bind = forbidden
ibis.duckdb.connect = forbidden
store = SessionStore(Path(sys.argv[1]))
with store._connection() as connection:
    record = artifact(store, connection, sys.argv[2])
result = read_result(store.project_root, record.descriptor)
assert result.primary.num_rows == 3
assert b'r7.precision' in result.primary.schema.metadata
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
from marivo.analysis.core.rules import OccurrencePrepare
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.graph_protocol import SNAPSHOT, decode, fixed_signature, thaw_graph
from marivo.analysis.refs import ArtifactRef
from dataclasses import replace
root = thaw_graph(decode(record.descriptor.continuation_snapshot, SNAPSHOT).root)
assert isinstance(root.parameters, OccurrencePrepare)
leaf = FixedLeaf(ArtifactRef(record.artifact_ref), root.fingerprint, fixed_signature(record.descriptor), root.value_type, root.inputs[0].node.shape)
continued = method_node((Edge('subject', leaf),), replace(root.parameters, output=leaf.signature.domain), value_type=root.value_type)
runtime = DatasetRuntime(store, record.session_ref)
produced = runtime._execute_graph(continued, (RouteChoice(continued.identity, 'artifact_python'),))
assert read_result(store.project_root, produced.descriptor).primary.equals(result.primary, check_metadata=True)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), fixed.artifact_ref],
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.mark.runtime
def test_empty_input_retains_schema_and_authority(tmp_path):
    with _case(tmp_path, empty=True) as (runtime, root, bindings, source, _):
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        assert result.primary.num_rows == 0
        assert result.primary.column_names == ["key_0", "key_1"]
        assert result.parts[1].table.schema.field("occurrences__occurred_at").type == pa.timestamp(
            "us", tz="UTC"
        )


def test_deadline_shared_boundary_and_restore(monkeypatch):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline, check

    now = [0.0]
    deadline = ExecuteDeadline(0.0, lambda: now[0])
    token = CURRENT.set(deadline)
    try:
        for value in (599.0, 600.0):
            now[0] = value
            check()
        now[0] = 600.001
        with pytest.raises(DomainPreparationError) as caught:
            check()
        assert caught.value.constraint_id == "r7.execute_timeout"
    finally:
        CURRENT.reset(token)


def _observation(root, method="count"):
    from marivo.analysis.core.model import ObservedQuantity
    from marivo.analysis.core.rules import (
        DirectMetricDefinition,
        EntityObservationTarget,
        MapCorrespond,
        ObserveCount,
        ObserveMetric,
        PreparedObservation,
    )
    from marivo.semantic.metric_graph import (
        AggregateNodeV1,
        MetricExpressionGraphV1,
        MetricGraphNodeRecordV1,
    )

    population, contribution = root.sources
    selected = method_node(
        (Edge("subject", root),),
        MapCorrespond("subjects", population.signature.domain),
        value_type=ScalarType("int64"),
    )
    event = root.parameters.events[0]
    metric_ref = ref.metric("commerce.total")
    graph = MetricExpressionGraphV1(
        "metric-expression/v1",
        ("aggregate",),
        (
            MetricGraphNodeRecordV1(
                "aggregate",
                AggregateNodeV1(
                    "aggregate",
                    _payload(
                        contribution.definition.ref
                        if method == "count"
                        else ref.measure("commerce.facts.amount")
                    ),
                    "contribution@v1",
                    method,
                    None,
                ),
            ),
        ),
        (),
    )
    definition = DirectMetricDefinition(
        metric_ref,
        graph,
        "aggregate",
        "metric@v1",
        "commerce@v1",
        contribution.definition.ref,
        ref.time_dimension(event.occurred_at.ref.path),
        None,
        "zero",
        (),
    )
    quantity = ObservedQuantity(
        "observed",
        metric_ref,
        "metric@v1",
        None,
        "bounded",
        "contributions",
        "strict",
        "count@v1" if method == "count" else "sum_zero@v1" if method == "sum" else "mean@v1",
    )
    kwargs = {
        "metric": definition,
        "target": EntityObservationTarget(selected.signature.domain),
        "quantity": quantity,
        "contribution": contribution.definition.ref,
        "path": event.path,
        "event": event.occurred_at,
        "start": root.parameters.start,
        "end": root.parameters.end,
    }
    observation = (
        ObserveCount(**kwargs)
        if method == "count"
        else ObserveMetric(**kwargs, amount_column="amount", amount_type="int64", method=method)
    )
    result = method_node(
        (Edge("subject", selected), Edge("subject", population)),
        PreparedObservation(observation),
        value_type=ScalarType("float64" if method == "mean" else "int64"),
        sources=root.sources,
    )
    routes = (
        RouteChoice(root.identity, "ibis"),
        RouteChoice(selected.identity, "ibis_python"),
        RouteChoice(result.identity, "ibis_python"),
    )
    return result, routes


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("method", ["count", "sum", "mean"])
@pytest.mark.parametrize("subset", [False, True])
@pytest.mark.parametrize("form,unit", [("table", "us"), ("parquet", "ns")])
def test_f13_source_prefix_before_actual_subject_image(
    tmp_path, monkeypatch, empty, method, subset, form, unit
):
    with _case(tmp_path, form=form, unit=unit, empty=empty) as (
        runtime,
        root,
        bindings,
        source,
        submissions,
    ):
        if subset:
            from marivo.semantic._expression_binding import (
                CompiledExpressionSidecar,
                ExpressionBody,
            )

            event = root.parameters.events[0]
            body = ExpressionBody(lambda table: table.amount < 30, "subset@v1", 1, ())
            event = replace(
                event,
                predicate_hash=body.body_ast_hash,
                predicate_kind="filtered",
                definition=replace(
                    event.definition, predicate_kind="filtered", body_ast_hash=body.body_ast_hash
                ),
            )
            root = method_node(
                root.inputs,
                replace(root.parameters, events=(event,)),
                value_type=root.value_type,
                sources=root.sources,
            )
            original_source = source

            @contextmanager
            def filtered_source():
                with original_source() as (selected, bound):
                    sidecar = CompiledExpressionSidecar(
                        {event.ref: body}, {}, frozenset((event.ref,))
                    )
                    yield (
                        selected,
                        tuple(replace(binding, expression_sidecar=sidecar) for binding in bound),
                    )

            source = filtered_source
        graph, routes = _observation(root, method)
        import marivo.analysis.materialization.graph_local_execution as local
        import marivo.analysis.materialization.graph_preparation as consumer
        import marivo.datasource.adapters as adapters

        trace = []
        original_read = adapters.SourceSession.batches
        original_image = local._subject_image
        original_observe = consumer._observation

        def read(self, *args, **kwargs):
            trace.append("source")
            assert "local" not in trace
            return original_read(self, *args, **kwargs)

        def image(*args, **kwargs):
            trace.append("local")
            return original_image(*args, **kwargs)

        def observe(*args, **kwargs):
            trace.append("local")
            return original_observe(*args, **kwargs)

        def upload(*args, **kwargs):
            raise AssertionError("F13 must never upload a local result")

        monkeypatch.setattr(adapters.SourceSession, "batches", read)
        monkeypatch.setattr(adapters.SourceSession, "stage_calculated", upload)
        monkeypatch.setattr(local, "_subject_image", image)
        monkeypatch.setattr(consumer, "_observation", observe)
        fixed = runtime._execute_graph(
            graph, routes, source_bindings=bindings, source_factory=source
        )
        result = read_result(tmp_path, fixed.descriptor)
        assert trace[-2:] == ["local", "local"]
        expected = (
            {}
            if empty
            else {"subject0": 2 if method == "count" else 30 if method == "sum" else 15.0}
        )
        if not empty and not subset:
            expected["subject1"] = 1 if method == "count" else 30 if method == "sum" else 30.0
        assert {row["key_0"]: row["value"] for row in cell_rows(result.primary)} == expected
        assert b"r7.restriction" in result.primary.schema.metadata
        assert json.loads(result.primary.schema.metadata[b"r7.precision"])[0]["possible_loss"] is (
            unit == "ns"
        )
        restriction = json.loads(result.primary.schema.metadata[b"r7.restriction"])
        assert restriction["original_member_rows"] == 2
        assert restriction["selected_member_rows"] == (0 if empty else 1 if subset else 2)
        assert restriction["candidate_rows"] == (0 if empty else 3)
        expected_candidates = pa.table(
            {
                "key": ["occurrence0", "occurrence1", "occurrence2"],
                "member": ["subject0", "subject0", "subject1"],
                "amount": [1, 1, 1] if method == "count" else [10, 20, 30],
                "time": pa.array([1000000, 2000000, 3000000], type=pa.timestamp("us", tz="UTC")),
            }
        )
        assert restriction["candidate_arrow_bytes"] == (0 if empty else expected_candidates.nbytes)
        assert all(submission.state == "succeeded" for submission in submissions)


def _rows(event, values, *, instants=None):
    return pa.table(
        {
            "key_0": [event.ref.path] * len(values),
            "key_1": [str(index) for index in range(len(values))],
            "subject__key_0": ["subject0"] * len(values),
            "occurrences__occurred_at": pa.array(
                instants or [0] * len(values), type=pa.timestamp("us", tz="UTC")
            ),
            "occurrences__sequence_int": pa.array(values, type=pa.int64()),
        }
    )


def _order(event, *, enum=False, conflicts=()):
    from marivo.analysis.core.domain_captures import OrderCapture
    from marivo.semantic.ir import BusinessOrderIR, EventSequenceIR

    field = _dimension(event.source, "sequence")
    definition = BusinessOrderIR(
        "commerce.order",
        "commerce",
        "order",
        event.subject.ref.path,
        (
            EventSequenceIR(
                event.ref.path,
                field.ref.path,
                ("first", "second") if enum else "integer",
                "subject",
            ),
        ),
        conflicts,
        AiContextIR(),
        "order",
        SourceLocation("r72.py", 1),
    )
    return OrderCapture(
        ref.business_order("commerce.order"),
        "order@v1",
        definition,
        (field,),
        ((event.ref.path, event.fingerprint),),
    )


def test_construction_plan_and_snapshot_do_no_business_io_or_run(tmp_path, monkeypatch):
    from marivo.analysis.compiler.graph_plan import plan
    from marivo.analysis.materialization import graph_store

    with _case(tmp_path) as (_, root, _, _, _):

        def forbidden(*args, **kwargs):
            raise AssertionError("construction/plan must not read or admit")

        monkeypatch.setattr(SourceSession, "bind", forbidden)
        monkeypatch.setattr(SourceSession, "compile", forbidden)
        monkeypatch.setattr(SourceSession, "batches", forbidden)
        monkeypatch.setattr(graph_store, "admit", forbidden)
        same = method_node(
            root.inputs, root.parameters, value_type=root.value_type, sources=root.sources
        )
        assert same.identity != root.identity
        assert thaw_graph(freeze_graph(same)).fingerprint == same.fingerprint
        assert (
            plan(same, routes=(RouteChoice(same.identity, "ibis"),)).classification.kind == "source"
        )
        with pytest.raises(DomainPreparationError):
            method_node(
                root.inputs,
                replace(root.parameters, events=root.parameters.events * 2),
                value_type=root.value_type,
                sources=root.sources,
            )
        with pytest.raises(DomainPreparationError):
            replace(root.parameters.events[0], participant="wrong")
        with pytest.raises(DomainPreparationError):
            replace(
                root.parameters.events[0],
                path=(replace(root.parameters.events[0].path[0], cardinality="one_to_many"),),
            )


def test_business_order_closed_sets_and_typed_sequences(tmp_path):
    from marivo.analysis.materialization.domain_preparation import validate_rows

    with _case(tmp_path) as (_, root, _, _, _):
        event = root.parameters.events[0]
        tied = _rows(event, [1, 2])
        validate_rows(root.parameters, tied)
        validate_rows(root.parameters, tied.take(pa.array([1, 0])))
        with pytest.raises(DomainPreparationError, match="business_order"):
            validate_rows(replace(root.parameters, order_use="ordered"), tied)
        ordered = replace(root.parameters, order=_order(event), order_use="ordered")
        validate_rows(ordered, tied)
        for values in ([1, 1], [1, None]):
            with pytest.raises(DomainPreparationError, match="business_order"):
                validate_rows(ordered, _rows(event, values))
        enum = replace(root.parameters, order=_order(event, enum=True), order_use="ordered")
        table = tied.drop(["occurrences__sequence_int"]).append_column(
            "occurrences__sequence_enum", pa.array(["first", "second"])
        )
        validate_rows(enum, table)
        with pytest.raises(DomainPreparationError, match="business_order"):
            validate_rows(
                enum,
                table.set_column(
                    3 + 1, "occurrences__sequence_enum", pa.array(["first", "unknown"])
                ),
            )
        with pytest.raises(DomainPreparationError):
            method_node(
                root.inputs,
                replace(root.parameters, order_use="after_terminal", terminal_state="closed"),
                value_type=root.value_type,
                sources=root.sources,
            )


def test_coverage_exact_binding_unknown_observed_declared_mixed(tmp_path):
    from datetime import datetime, timezone

    from marivo.analysis.domains.completeness import (
        BoundedCompletenessDeclarationV1,
        BoundedCoverageStartV1,
        EventCoverageReceiptV1,
        SourceOriginCompletenessDeclarationV1,
    )
    from marivo.analysis.methods.domain_coverage import coverage

    with _case(tmp_path) as (_, root, _, _, _):
        params = root.parameters
        event = params.events[0]
        start, end = datetime.fromisoformat(params.start), datetime.fromisoformat(params.end)
        claim = BoundedCompletenessDeclarationV1(
            inputs=(event.ref,),
            complete_from=start,
            complete_through=end,
            rationale="Exact fixture interval",
        )
        observed = EventCoverageReceiptV1(
            event_ref=event.ref,
            event_fingerprint=event.fingerprint,
            source_entity_ref=event.source.ref.path,
            source_origin_ref=ref.datasource("db"),
            occurred_at_ref=event.occurred_at.ref.path,
            coverage_start=BoundedCoverageStartV1(complete_from=start),
            complete_through=end,
            authority="fixture receipt",
            observed_at=datetime.now(timezone.utc),
            source_revision="captured",
            source_binding_fingerprint=event.source.dependency_fingerprint,
            execution_domain_id=params.output.definition_id,
        )
        assert coverage(params)[0].basis == "unknown"
        assert not coverage(params)[0].complete
        assert coverage(replace(params, completeness=(claim,)))[0].complete
        assert coverage(params, (observed,))[0].basis == "observed"
        insufficient = replace(observed, complete_through=start)
        assert not coverage(params, (insufficient,))[0].complete
        assert coverage(replace(params, completeness=(claim,)), (insufficient,))[0].basis == "mixed"
        origin = SourceOriginCompletenessDeclarationV1(
            inputs=(event.ref,),
            source_origin_ref=ref.datasource("db"),
            complete_through=end,
            rationale="Source origin",
        )
        assert coverage(replace(params, start=None, completeness=(origin,)))[0].complete
        assert not coverage(replace(params, start=None, completeness=(claim,)))[0].complete
        for bad in (
            replace(observed, event_fingerprint="other"),
            replace(observed, source_binding_fingerprint="other"),
            replace(observed, execution_domain_id="other"),
            replace(observed, source_origin_ref=ref.datasource("other")),
        ):
            with pytest.raises(DomainPreparationError, match="coverage_binding"):
                coverage(params, (bad,))
        with pytest.raises(DomainPreparationError, match="coverage_binding"):
            coverage(replace(params, completeness=(claim, claim)))


def test_anchor_window_restriction_preserves_overlap_and_full_subject_keys():
    from datetime import datetime, timezone

    from marivo.analysis.methods.prepared_observation import Restriction, restrict

    point = lambda second: datetime.fromtimestamp(second, tz=timezone.utc)
    candidates = pa.table(
        {
            "member_0": ["s", "s", "s", "s"],
            "member_1": [2**53 + 1, 2**53 + 1, 2**53 + 1, 2**53 + 2],
            "event_time": pa.array([0, 1, 2, 1], type=pa.timestamp("s", tz="UTC")),
            "amount": [10, 20, 30, 100],
        }
    )
    selections = (
        Restriction(("a",), ("s", 2**53 + 1), point(0), point(2)),
        Restriction(("b",), ("s", 2**53 + 1), point(1), point(3)),
    )
    rows = restrict(candidates, selections)
    assert [[row["amount"] for row in selected] for selected in rows] == [[10, 20], [20, 30]]
    assert restrict(candidates, ()) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("phase", ["graph_admitted", "graph_receipts_verified", "before_commit"])
@pytest.mark.parametrize("elapsed", [599.0, 600.0, 600.001])
def test_execute_deadline_atomic_boundaries(tmp_path, phase, elapsed):
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    with _case(tmp_path) as (runtime, root, bindings, source, _):
        now = [0.0]
        token = CURRENT.set(ExecuteDeadline(0.0, lambda: now[0]))
        original = runtime._event

        def event(name):
            original(name)
            if name == phase:
                now[0] = elapsed

        runtime._event = event
        try:
            if elapsed <= 600:
                runtime._execute_graph(
                    root,
                    (RouteChoice(root.identity, "ibis"),),
                    source_bindings=bindings,
                    source_factory=source,
                )
            else:
                with pytest.raises(DomainPreparationError, match="execute_timeout"):
                    runtime._execute_graph(
                        root,
                        (RouteChoice(root.identity, "ibis"),),
                        source_bindings=bindings,
                        source_factory=source,
                    )
                assert runtime.store._graph_run(runtime.last_run_ref).lifecycle == "failed"
                with runtime.store._connection() as conn:
                    assert conn.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
                    assert conn.execute("SELECT count(*) FROM dataset_evidence").fetchone()[0] == 0
                    assert conn.execute("SELECT count(*) FROM findings").fetchone()[0] == 0
                assert runtime.store.resources(runtime.session_ref) == ()
        finally:
            CURRENT.reset(token)


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_input_snapshot_replacement_and_issued_sql(tmp_path, monkeypatch, form):
    import marivo.datasource.adapters as adapters

    with _case(tmp_path, form=form) as (runtime, root, bindings, source, submissions):
        original_batches = adapters.SourceSession.batches
        native = adapters._native_cursor
        sql = []
        submitted = []
        replaced = []

        def batches(self, read, **kwargs):
            sql.append(read.sql)
            result = original_batches(self, read, **kwargs)
            if not replaced:
                replaced.append(True)
                if form == "parquet":
                    replacement = pq.read_table(tmp_path / "facts.parquet").slice(0, 0)
                    pq.write_table(replacement, tmp_path / "replacement.parquet")
                    (tmp_path / "replacement.parquet").replace(tmp_path / "facts.parquet")
                else:
                    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
                    try:
                        backend.con.execute("DELETE FROM facts")
                    finally:
                        backend.disconnect()
            return result

        def cursor(backend, name, statement):
            submitted.append(statement)
            return native(backend, name, statement)

        monkeypatch.setattr(adapters.SourceSession, "batches", batches)
        monkeypatch.setattr(adapters, "_native_cursor", cursor)
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        assert result.primary.num_rows == 3
        assert sql == submitted
        assert all(item.connection_disconnected for item in submissions)


@pytest.mark.runtime
def test_real_query_interrupt_closes_native_capture(tmp_path):
    import time

    from marivo.analysis.materialization.execute_deadline import (
        CURRENT,
        ExecuteDeadline,
        check,
        guard,
    )
    from marivo.datasource.domain_snapshot import capture

    with _case(tmp_path) as (_, _, _, factory, submissions):
        with factory() as (source, bindings):
            binding = bindings[1].source
            relation = binding.relation
            for _ in range(18):
                relation = relation.cross_join(binding.relation.view()).select(relation)
            relation = relation.mutate(noise=ibis.random())
            relation = relation.aggregate(value=relation.noise.sum())
            qualified = source.qualify(
                binding, PhysicalRequirement("deadline", 1, frozenset({"scan", "join", "group"}))
            )
            read = source.compile(
                qualified,
                relation,
                purpose="deadline",
                expected_schema=relation.schema().to_pyarrow(),
            )
            started = time.monotonic()
            token = CURRENT.set(ExecuteDeadline(started, seconds=0.05))
            try:
                with (
                    pytest.raises(DomainPreparationError, match="execute_timeout"),
                    capture(source, checkpoint=check, guard=guard),
                    closing(source.batches(read, chunk_size=1)) as stream,
                ):
                    list(stream)
            finally:
                CURRENT.reset(token)
            assert time.monotonic() - started < 3
        assert submissions[-1].state == "failed"
        assert submissions[-1].connection_disconnected


def test_state_model_repeated_triggers_share_one_capture(tmp_path):
    from marivo.analysis.core.domain_captures import StateModelCapture
    from marivo.semantic.ir import (
        LifecycleStateIR,
        StateInceptionIR,
        StateModelIR,
        StateTransitionIR,
        StateTriggerIR,
    )

    with _case(tmp_path) as (_, root, _, _, _):
        event = root.parameters.events[0]
        trigger = StateTriggerIR(event.ref.path, event.participant)
        model = StateModelIR(
            "commerce.model",
            "commerce",
            "model",
            event.subject.ref.path,
            (LifecycleStateIR("open", True, False), LifecycleStateIR("closed", False, True)),
            (StateInceptionIR(trigger),),
            (StateTransitionIR("open", trigger, "closed"),),
            AiContextIR(),
            "model",
            SourceLocation("r72.py", 1),
        )
        capture = StateModelCapture(
            ref.state_model(model.semantic_id),
            "model@v1",
            model,
            (event,),
            None,
            ((event.ref.path, event.fingerprint),),
        )
        combined = method_node(
            root.inputs,
            replace(root.parameters, model=capture),
            value_type=root.value_type,
            sources=root.sources,
        )
        restored = thaw_graph(freeze_graph(combined))
        assert restored.parameters.model.triggers == restored.parameters.events
        with pytest.raises(DomainPreparationError):
            replace(capture, triggers=(event, event))


def test_f13_refuses_unbounded_or_source_after_local(tmp_path):
    from marivo.analysis.compiler.graph_plan import plan
    from marivo.analysis.core.rules import PreparedObservation
    from marivo.analysis.errors import AnalysisError

    with _case(tmp_path) as (_, root, _, _, _):
        prepared, routes = _observation(root)
        observation = prepared.parameters.observation
        with pytest.raises(DomainPreparationError):
            method_node(
                prepared.inputs,
                PreparedObservation(replace(observation, start=None)),
                value_type=prepared.value_type,
                sources=prepared.sources,
            )
        selected = prepared.inputs[0].node
        source_after = method_node(
            (Edge("subject", selected),),
            observation,
            value_type=prepared.value_type,
            sources=prepared.sources,
        )
        with pytest.raises(AnalysisError):
            plan(source_after, routes=(*routes[:-1], RouteChoice(source_after.identity, "ibis")))


def test_cross_batch_complete_keys_schema_and_early_close():
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import CheckedStream

    class Stream:
        def __init__(self, batches):
            self.batches = batches
            self.schema = batches[0].schema
            self.closed = False

        def __iter__(self):
            return iter(self.batches)

        def close(self):
            self.closed = True

    first = pa.record_batch({"key_0": ["same"], "key_1": [2**53 + 1]})
    distinct = pa.record_batch({"key_0": ["same"], "key_1": [2**53 + 2]})
    for batches in ((first, distinct, first), (first, pa.record_batch({"key_0": ["bad"]}))):
        source = Stream(batches)
        stream = CheckedStream(source, first.schema, ("key_0", "key_1"), validate_cells=False)
        with pytest.raises(AnalysisError):
            list(stream)
        assert source.closed and not stream.completed
    source = Stream((first, distinct))
    stream = CheckedStream(source, first.schema, ("key_0", "key_1"), validate_cells=False)
    iterator = iter(stream)
    next(iterator)
    iterator.close()
    assert source.closed and not stream.completed


@pytest.mark.runtime
def test_ns_native_truncation_boundaries_ties_and_fixed_disclosure(tmp_path):
    ticks = [-2001, -1999, -1001, -999, -501, -1, 0, 1, 501, 999, 1001, 1999, 2001]
    with _case(tmp_path, form="parquet", unit="ns") as (runtime, original, bindings, source, _):
        pq.write_table(
            pa.table(
                {
                    "occurrence": [f"o{i}" for i in range(len(ticks))],
                    "subject": ["subject0"] * len(ticks),
                    "amount": [1] * len(ticks),
                    "instant": pa.array(ticks, type=pa.timestamp("ns")),
                }
            ),
            tmp_path / "facts.parquet",
        )
        params = replace(
            original.parameters,
            start="1969-12-31T23:59:59.999999+00:00",
            end="1970-01-01T00:00:00.000002+00:00",
        )
        root = method_node(
            original.inputs, params, value_type=original.value_type, sources=original.sources
        )
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        occurrence = next(part.table for part in result.parts if part.role == "occurrences")
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        expected = {
            f"o{i}": int(value / 1000)
            for i, value in enumerate(ticks)
            if -1 <= int(value / 1000) < 2
        }
        assert {
            row["key_1"]: int((row["occurrences__occurred_at"] - epoch).total_seconds() * 1000000)
            for row in occurrence.to_pylist()
        } == expected
        disclosure = json.loads(result.primary.schema.metadata[b"r7.precision"])
        assert (
            disclosure[0]["possible_loss"] and "truncates toward zero" in disclosure[0]["behavior"]
        )
        leaf = FixedLeaf(
            ArtifactRef(artifact.artifact_ref),
            root.fingerprint,
            fixed_signature(artifact.descriptor),
            root.value_type,
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        fixed = method_node(
            (Edge("subject", leaf),),
            replace(params, output=leaf.signature.domain),
            value_type=root.value_type,
        )
        continued = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
        assert (
            read_result(tmp_path, continued.descriptor).primary.schema.metadata[b"r7.precision"]
            == result.primary.schema.metadata[b"r7.precision"]
        )
        ordered = method_node(
            root.inputs,
            replace(params, order_use="ordered"),
            value_type=root.value_type,
            sources=root.sources,
        )
        with pytest.raises(DomainPreparationError, match="business_order"):
            runtime._execute_graph(
                ordered,
                (RouteChoice(ordered.identity, "ibis"),),
                source_bindings=bindings,
                source_factory=source,
            )
        assert runtime.store._graph_run(runtime.last_run_ref).lifecycle == "failed"


@pytest.mark.runtime
@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("version_kind", ["validity", "snapshot"])
def test_f13_captures_historical_coordinate_at_contribution_time(tmp_path, overlap, version_kind):
    from marivo.analysis.core.rules import PreparedObservation
    from marivo.semantic.ir import TargetSnapshotVersion, TargetValidityVersion

    with _case(tmp_path) as (runtime, occurrence, source_keys, source, _):
        population, facts = occurrence.sources
        historical = _entity("commerce.history", TableSourceIR("history"), ("record",))
        version = TargetValidityVersion(
            _payload(ref.time_dimension("commerce.history.start")),
            _payload(ref.time_dimension("commerce.history.end")),
            "start",
            "end",
            "closed_open",
            (None,),
            "UTC",
        )
        if version_kind == "snapshot":
            version = TargetSnapshotVersion(
                _payload(ref.time_dimension("commerce.history.snapshot")),
                "snapshot",
                "date",
                "America/New_York",
                None,
            )
        historical = replace(historical, version=version)
        coord = _dimension(historical, "segment")
        leaf = SourceLeaf(
            SourceDefinition(
                ref.entity(historical.ref.path),
                historical.dependency_fingerprint,
                ref.datasource("db"),
                facts.definition.shape,
                version,
            ),
            Signature(
                DomainSignature(
                    population.signature.domain.binding,
                    "entity",
                    (Coordinate(ref.entity(historical.ref.path), "record", "identity"),),
                    (),
                    historical.ref.path,
                )
            ),
            ScalarType("int64"),
        )
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        try:
            if version_kind == "snapshot":
                facts_table = backend.table("facts").to_pyarrow()
                facts_table = facts_table.set_column(
                    facts_table.schema.get_field_index("instant"),
                    "instant",
                    pa.array([17999, 18000, 18001], type=pa.timestamp("s", tz="UTC")),
                )
                backend.create_table("facts", facts_table, overwrite=True)
            backend.create_table(
                "history",
                pa.table(
                    {
                        "record": ["old", "new", "other"],
                        "subject": ["subject0", "subject0", "subject1"],
                        "segment": ["before", "after", "other"],
                        "start": pa.array(
                            [0, 1 if overlap else 2, 0], type=pa.timestamp("s", tz="UTC")
                        ),
                        "end": pa.array([2, None, None], type=pa.timestamp("s", tz="UTC")),
                        "snapshot": pa.array([-1, -1 if overlap else 0, 0], type=pa.date32()),
                    }
                ),
            )
        finally:
            backend.disconnect()
        first = TargetRelationshipContract(
            _payload(ref.relationship("commerce.facts_history")),
            occurrence.parameters.events[0].source.ref,
            historical.ref,
            "history",
            (("subject", "subject"),),
            "many_to_one",
            False,
            False,
        )
        second = TargetRelationshipContract(
            _payload(ref.relationship("commerce.history_users")),
            historical.ref,
            occurrence.parameters.events[0].subject.ref,
            "subject",
            (("subject", "subject"),),
            "many_to_one",
            False,
            False,
        )
        first = replace(first, to_version_resolution_required=True)
        second = replace(second, from_version_resolution_required=True)
        if version_kind == "snapshot":
            occurrence = method_node(
                occurrence.inputs,
                replace(occurrence.parameters, end="1970-01-02T00:00:00+00:00"),
                value_type=occurrence.value_type,
                sources=occurrence.sources,
            )
        original, routes = _observation(occurrence, "sum")
        params = replace(
            original.parameters.observation, path=(first, second), coordinates=(coord,)
        )
        root = method_node(
            original.inputs,
            PreparedObservation(params),
            value_type=original.value_type,
            sources=(*original.sources, leaf),
        )
        routes = (*routes[:-1], RouteChoice(root.identity, "ibis_python"))
        key = SourceKeyBinding(
            leaf,
            leaf.definition.shape,
            leaf.definition.fingerprint,
            digest(canonical_json(historical.source.to_dict())),
        )

        @contextmanager
        def factory():
            with source() as (session, bindings):
                bound = session.bind(historical.source, source_identity=leaf.identity)
                yield (
                    session,
                    (
                        *bindings,
                        SourceBinding(
                            leaf,
                            bound,
                            RelationLayout(
                                (
                                    CoordinateColumn(
                                        leaf.signature.domain.instance_key[0], "record"
                                    ),
                                ),
                                None,
                                (),
                            ),
                        ),
                    ),
                )

        if overlap:
            with pytest.raises(DomainPreparationError, match="input_binding"):
                runtime._execute_graph(
                    root, routes, source_bindings=(*source_keys, key), source_factory=factory
                )
        else:
            artifact = runtime._execute_graph(
                root, routes, source_bindings=(*source_keys, key), source_factory=factory
            )
            result = read_result(tmp_path, artifact.descriptor)
            state = next(part.table for part in result.parts if part.role == "coordinate_state")
            grouped = {row["key_0"]: row["coordinate_state__groups"] for row in state.to_pylist()}
            assert {item["coordinate"]: item["sum"] for item in grouped["subject0"]} == {
                "before": 10,
                "after": 20,
            }


@pytest.mark.runtime
@pytest.mark.parametrize("enum", [False, True])
def test_native_sequence_capture_and_fixed_order(tmp_path, enum):
    with _case(tmp_path) as (runtime, original, bindings, source, _):
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        try:
            facts = backend.table("facts").to_pyarrow()
            facts = facts.set_column(
                facts.schema.get_field_index("instant"),
                "instant",
                pa.array([1, 1, 2], type=pa.timestamp("s", tz="UTC")),
            )
            facts = facts.append_column(
                "sequence", pa.array(["first", "second", "first"] if enum else [1, 2, 1])
            )
            backend.create_table("facts", facts, overwrite=True)
        finally:
            backend.disconnect()
        root = method_node(
            original.inputs,
            replace(
                original.parameters,
                order=_order(original.parameters.events[0], enum=enum),
                order_use="ordered",
            ),
            value_type=original.value_type,
            sources=original.sources,
        )
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        occurrence = next(part.table for part in result.parts if part.role == "occurrences")
        name = "occurrences__sequence_enum" if enum else "occurrences__sequence_int"
        assert name in occurrence.column_names
        leaf = FixedLeaf(
            ArtifactRef(artifact.artifact_ref),
            root.fingerprint,
            fixed_signature(artifact.descriptor),
            root.value_type,
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        fixed = method_node(
            (Edge("subject", leaf),),
            replace(root.parameters, output=leaf.signature.domain),
            value_type=root.value_type,
        )
        continued = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
        assert logical_table(read_result(tmp_path, continued.descriptor).primary).equals(
            logical_table(result.primary)
        )


def test_precedence_cycle_is_not_repaired_by_occurrence_identity(tmp_path):
    from marivo.analysis.core.domain_captures import OrderCapture
    from marivo.analysis.materialization.domain_preparation import validate_rows
    from marivo.semantic.ir import BusinessOrderIR, EventPrecedenceIR

    with _case(tmp_path) as (_, root, _, _, _):
        first = root.parameters.events[0]
        second = replace(
            first,
            ref=ref.event("commerce.second"),
            fingerprint="second@v1",
            definition=replace(first.definition, semantic_id="commerce.second", name="second"),
        )
        forward = EventPrecedenceIR(
            first.ref.path, first.participant, second.ref.path, second.participant
        )
        definition = BusinessOrderIR(
            "commerce.order",
            "commerce",
            "order",
            first.subject.ref.path,
            (),
            (forward,),
            AiContextIR(),
            "order",
            SourceLocation("r72.py", 1),
        )
        order = OrderCapture(
            ref.business_order(definition.semantic_id),
            "order@v1",
            definition,
            (),
            ((first.ref.path, first.fingerprint), (second.ref.path, second.fingerprint)),
        )
        rows = pa.concat_tables(
            (
                _rows(first, [None]).drop(["occurrences__sequence_int"]),
                _rows(second, [None]).drop(["occurrences__sequence_int"]),
            )
        )
        params = replace(root.parameters, events=(first, second), order=order, order_use="ordered")
        validate_rows(params, rows)
        reverse = EventPrecedenceIR(
            second.ref.path, second.participant, first.ref.path, first.participant
        )
        with pytest.raises(DomainPreparationError, match="cycle"):
            validate_rows(
                replace(
                    params,
                    order=replace(
                        order, definition=replace(definition, conflicts=(forward, reverse))
                    ),
                ),
                rows,
            )


@pytest.mark.runtime
def test_filtered_event_predicate_uses_frozen_sidecar(tmp_path):
    from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody

    with _case(tmp_path) as (runtime, original, keys, source, _):
        event = original.parameters.events[0]
        body = ExpressionBody(lambda table: table.amount >= 20, "predicate@v1", 1, ())
        event = replace(
            event,
            predicate_hash=body.body_ast_hash,
            predicate_kind="filtered",
            definition=replace(
                event.definition, predicate_kind="filtered", body_ast_hash=body.body_ast_hash
            ),
        )
        root = method_node(
            original.inputs,
            replace(original.parameters, events=(event,)),
            value_type=original.value_type,
            sources=original.sources,
        )

        @contextmanager
        def factory():
            with source() as (selected, bindings):
                sidecar = CompiledExpressionSidecar({event.ref: body}, {}, frozenset((event.ref,)))
                yield (
                    selected,
                    tuple(replace(binding, expression_sidecar=sidecar) for binding in bindings),
                )

        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=keys,
            source_factory=factory,
        )
        assert {
            row["key_1"] for row in cell_rows(read_result(tmp_path, artifact.descriptor).primary)
        } == {"occurrence1", "occurrence2"}


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["sum", "mean"])
@pytest.mark.parametrize("empty", [False, True])
def test_f13_float64_retains_support_and_magnitude(tmp_path, method, empty):
    from marivo.analysis.core.rules import PreparedObservation

    with _case(tmp_path, empty=empty) as (runtime, occurrence, keys, factory, _):
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        try:
            facts = backend.table("facts").to_pyarrow()
            amounts = pa.array([] if empty else [10.5, None, -20.25], type=pa.float64())
            backend.create_table(
                "facts",
                facts.set_column(facts.schema.get_field_index("amount"), "amount", amounts),
                overwrite=True,
            )
        finally:
            backend.disconnect()
        original, routes = _observation(occurrence, method)
        params = replace(original.parameters.observation, amount_type="float64")
        root = method_node(
            original.inputs,
            PreparedObservation(params),
            value_type=ScalarType("float64"),
            sources=original.sources,
        )
        artifact = runtime._execute_graph(
            root,
            (*routes[:-1], RouteChoice(root.identity, "ibis_python")),
            source_bindings=keys,
            source_factory=factory,
        )
        result = read_result(tmp_path, artifact.descriptor)
        assert {row["key_0"]: row["value"] for row in cell_rows(result.primary)} == (
            {} if empty else {"subject0": 10.5, "subject1": -20.25}
        )
        original_state = next(part.table for part in result.parts if part.role == "original_state")
        states = {row["key_0"]: row for row in original_state.to_pylist()}
        if not empty:
            assert states["subject0"]["original_state__non_null_count"] == 1
            assert states["subject0"]["original_state__absolute_sum"] == 10.5
            if method == "mean":
                assert states["subject0"]["original_state__row_count"] == 2


@pytest.mark.runtime
def test_review_reordered_key():
    from dataclasses import replace
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import ibis
    import pyarrow as pa

    from marivo.analysis.core.graph import method_node
    from marivo.analysis.materialization.graph_storage import read_result

    f = globals()
    with TemporaryDirectory(prefix="marivo-review-reordered-key-") as d:
        directory = Path(d).resolve()
        with f["_case"](directory, subject="si") as (
            runtime,
            original,
            bindings,
            source,
            submissions,
        ):
            con = ibis.duckdb.connect(directory / "source.duckdb")
            users = pa.table({"subject": ["A", "B"], "subject2": ["B", "A"]})
            facts = con.table("facts").to_pyarrow()
            facts = facts.set_column(
                facts.schema.get_field_index("subject"), "subject", pa.array(["A", "A", "B"])
            )
            facts = facts.set_column(
                facts.schema.get_field_index("subject2"), "subject2", pa.array(["B", "B", "A"])
            )
            facts = facts.set_column(
                facts.schema.get_field_index("amount"), "amount", pa.array([10, 20, 70])
            )
            con.create_table("users", users, overwrite=True)
            con.create_table("facts", facts, overwrite=True)
            con.disconnect()
            event = original.parameters.events[0]
            hop = replace(event.path[0], keys=(("subject2", "subject2"), ("subject", "subject")))
            assert {key for _, key in hop.keys} == set(event.subject.primary_key)
            occurrence = method_node(
                original.inputs,
                replace(original.parameters, events=(replace(event, path=(hop,)),)),
                value_type=original.value_type,
                sources=original.sources,
            )
            root, routes = f["_observation"](occurrence, "sum")
            artifact = runtime._execute_graph(
                root, routes, source_bindings=bindings, source_factory=source
            )
            result = read_result(directory, artifact.descriptor)
            assert [row["value"] for row in cell_rows(result.primary)] == [30, 70]


@pytest.mark.runtime
def test_review_normalized_version_path():
    from contextlib import contextmanager
    from dataclasses import replace
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import ibis
    import pyarrow as pa

    from marivo.analysis.compiler.graph_lowering import (
        CoordinateColumn,
        RelationLayout,
        SourceBinding,
    )
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import SourceLeaf, method_node
    from marivo.analysis.core.model import Coordinate, DomainSignature, Signature
    from marivo.analysis.core.rules import PreparedObservation
    from marivo.analysis.materialization.contracts import canonical_json
    from marivo.analysis.materialization.execution_key import SourceKeyBinding
    from marivo.analysis.materialization.graph_protocol import digest
    from marivo.analysis.materialization.graph_storage import read_result
    from marivo.refs import ref
    from marivo.semantic.ir import TargetValidityVersion

    f = globals()
    with TemporaryDirectory(prefix="marivo-review-version-") as d:
        directory = Path(d).resolve()
        with f["_case"](directory) as (runtime, occurrence, keys, source, submissions):
            population, original = occurrence.sources
            a = occurrence.parameters.events[0]
            version = TargetValidityVersion(
                f["_payload"](ref.time_dimension("commerce.metrics.start")),
                f["_payload"](ref.time_dimension("commerce.metrics.end")),
                "start",
                "end",
                "closed_open",
                (None,),
                "UTC",
            )
            contract = replace(
                f["_entity"]("commerce.metrics", f["TableSourceIR"]("metrics"), ("occurrence",)),
                version=version,
            )
            k = Coordinate(ref.entity("commerce.metrics"), "occurrence", "identity")
            leaf = SourceLeaf(
                replace(
                    original.definition,
                    ref=ref.entity("commerce.metrics"),
                    fingerprint=contract.dependency_fingerprint,
                    version=version,
                ),
                Signature(
                    DomainSignature(
                        population.signature.domain.binding,
                        "entity",
                        (k,),
                        (k,),
                        "commerce.metrics",
                    )
                ),
                original.value_type,
            )
            hop = replace(
                a.path[0],
                ref=f["_payload"](ref.relationship("commerce.metrics_to_users")),
                from_entity_ref=contract.ref,
                from_version_resolution_required=True,
            )
            old, routes = f["_observation"](occurrence, "sum")
            observation = old.parameters.observation
            graph = observation.metric.graph
            record = graph.nodes[0]
            graph = replace(
                graph,
                nodes=(
                    replace(
                        record,
                        node=replace(
                            record.node,
                            target_ref=f["_payload"](ref.measure("commerce.metrics.amount")),
                        ),
                    ),
                ),
            )
            event = f["_dimension"](contract, "instant", time=True)
            metric = replace(
                observation.metric,
                graph=graph,
                contribution=leaf.definition.ref,
                event_ref=ref.time_dimension(event.ref.path),
            )
            params = replace(
                observation,
                metric=metric,
                contribution=leaf.definition.ref,
                path=(hop,),
                event=event,
            )
            root = method_node(
                old.inputs,
                PreparedObservation(params),
                value_type=old.value_type,
                sources=(population, leaf),
            )
            routes = (*routes[:-1], RouteChoice(root.identity, "ibis_python"))
            con = ibis.duckdb.connect(directory / "source.duckdb")
            con.create_table(
                "metrics",
                pa.table(
                    {
                        "occurrence": ["same", "same"],
                        "subject": ["subject0", "subject0"],
                        "amount": [10, 900],
                        "instant": pa.array([1, 15], type=pa.timestamp("s", tz="UTC")),
                        "start": pa.array([0, 10], type=pa.timestamp("s", tz="UTC")),
                        "end": pa.array([10, 30], type=pa.timestamp("s", tz="UTC")),
                    }
                ),
            )
            con.disconnect()
            binding = SourceKeyBinding(
                leaf,
                leaf.definition.shape,
                leaf.definition.fingerprint,
                digest(canonical_json(contract.source.to_dict())),
            )

            @contextmanager
            def factory():
                with source() as (session, bindings):
                    bound = session.bind(contract.source, source_identity=leaf.identity)
                    yield (
                        session,
                        (
                            *bindings,
                            SourceBinding(
                                leaf,
                                bound,
                                RelationLayout((CoordinateColumn(k, "occurrence"),), None, ()),
                            ),
                        ),
                    )

            artifact = runtime._execute_graph(
                root, routes, source_bindings=(*keys, binding), source_factory=factory
            )
            result = read_result(directory, artifact.descriptor)
            assert [row["value"] for row in cell_rows(result.primary)] == [10, 0]


@pytest.mark.runtime
def test_review_version_ns_zone():
    from contextlib import contextmanager
    from dataclasses import replace
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import ibis
    import pyarrow as pa

    from marivo.analysis.compiler.graph_lowering import (
        CoordinateColumn,
        RelationLayout,
        SourceBinding,
    )
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import SourceLeaf, method_node
    from marivo.analysis.core.model import Coordinate, DomainSignature, Signature
    from marivo.analysis.core.rules import PreparedObservation
    from marivo.analysis.materialization.contracts import canonical_json
    from marivo.analysis.materialization.execution_key import SourceKeyBinding
    from marivo.analysis.materialization.graph_protocol import digest
    from marivo.analysis.materialization.graph_storage import read_result
    from marivo.refs import ref
    from marivo.semantic.ir import TargetValidityVersion

    f = globals()
    with TemporaryDirectory(prefix="marivo-review-version-") as d:
        directory = Path(d).resolve()
        with f["_case"](directory, form="parquet", unit="ns", zone="America/New_York") as (
            runtime,
            occurrence,
            keys,
            source,
            submissions,
        ):
            population, original = occurrence.sources
            a = occurrence.parameters.events[0]
            version = TargetValidityVersion(
                f["_payload"](ref.time_dimension("commerce.metrics.start")),
                f["_payload"](ref.time_dimension("commerce.metrics.end")),
                "start",
                "end",
                "closed_open",
                (None,),
                "UTC",
            )
            contract = replace(
                f["_entity"](
                    "commerce.metrics",
                    f["ParquetSourceIR"](str(directory / "metrics.parquet")),
                    ("occurrence",),
                ),
                version=version,
            )
            k = Coordinate(ref.entity("commerce.metrics"), "occurrence", "identity")
            leaf = SourceLeaf(
                replace(
                    original.definition,
                    ref=ref.entity("commerce.metrics"),
                    fingerprint=contract.dependency_fingerprint,
                    version=version,
                ),
                Signature(
                    DomainSignature(
                        population.signature.domain.binding,
                        "entity",
                        (k,),
                        (k,),
                        "commerce.metrics",
                    )
                ),
                original.value_type,
            )
            hop = replace(
                a.path[0],
                ref=f["_payload"](ref.relationship("commerce.metrics_to_users")),
                from_entity_ref=contract.ref,
            )
            old, routes = f["_observation"](occurrence, "sum")
            observation = old.parameters.observation
            graph = observation.metric.graph
            record = graph.nodes[0]
            graph = replace(
                graph,
                nodes=(
                    replace(
                        record,
                        node=replace(
                            record.node,
                            target_ref=f["_payload"](ref.measure("commerce.metrics.amount")),
                        ),
                    ),
                ),
            )
            event = f["_dimension"](contract, "instant", time=True, zone="America/New_York")
            metric = replace(
                observation.metric,
                graph=graph,
                contribution=leaf.definition.ref,
                event_ref=ref.time_dimension(event.ref.path),
            )
            params = replace(
                observation,
                metric=metric,
                contribution=leaf.definition.ref,
                path=(hop,),
                event=event,
                end="1970-01-02T00:00:00+00:00",
            )
            root = method_node(
                old.inputs,
                PreparedObservation(params),
                value_type=old.value_type,
                sources=(population, leaf),
            )
            routes = (*routes[:-1], RouteChoice(root.identity, "ibis_python"))
            con = ibis.duckdb.connect(directory / "source.duckdb")
            __import__("pyarrow.parquet", fromlist=["write_table"]).write_table(
                pa.table(
                    {
                        "occurrence": ["same", "same"],
                        "subject": ["subject0", "subject0"],
                        "amount": [10, 900],
                        "instant": pa.array([1000000123, 100001000000000], type=pa.timestamp("ns")),
                        "start": pa.array([0, 100000], type=pa.timestamp("s", tz="UTC")),
                        "end": pa.array([100000, 200000], type=pa.timestamp("s", tz="UTC")),
                    }
                ),
                directory / "metrics.parquet",
            )
            con.disconnect()
            binding = SourceKeyBinding(
                leaf,
                leaf.definition.shape,
                leaf.definition.fingerprint,
                digest(canonical_json(contract.source.to_dict())),
            )

            @contextmanager
            def factory():
                with source() as (session, bindings):
                    bound = session.bind(contract.source, source_identity=leaf.identity)
                    yield (
                        session,
                        (
                            *bindings,
                            SourceBinding(
                                leaf,
                                bound,
                                RelationLayout((CoordinateColumn(k, "occurrence"),), None, ()),
                            ),
                        ),
                    )

            artifact = runtime._execute_graph(
                root, routes, source_bindings=(*keys, binding), source_factory=factory
            )
            result = read_result(directory, artifact.descriptor)
            assert [row["value"] for row in cell_rows(result.primary)] == [10, 0]


@pytest.mark.runtime
def test_review_table_precision():
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import ibis
    import pyarrow as pa

    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.materialization.graph_storage import read_result

    f = globals()
    with TemporaryDirectory(prefix="marivo-review-table-precision-") as d:
        directory = Path(d).resolve()
        with f["_case"](directory) as (runtime, root, bindings, source, submissions):
            con = ibis.duckdb.connect(directory / "source.duckdb")
            facts = con.table("facts").to_pyarrow()
            facts = facts.set_column(
                facts.schema.get_field_index("instant"),
                "instant",
                pa.array([1000000123, 2000000123, 3000000123], type=pa.timestamp("ns")),
            )
            con.create_table("facts", facts, overwrite=True)
            physical = str(con.table("facts").instant.type())
            con.disconnect()
            artifact = runtime._execute_graph(
                root,
                (RouteChoice(root.identity, "ibis"),),
                source_bindings=bindings,
                source_factory=source,
            )
            result = read_result(directory, artifact.descriptor)
            occurrence = next(p.table for p in result.parts if p.role == "occurrences")
            assert (
                json.loads(result.primary.schema.metadata[b"r7.precision"])[0]["source_unit"]
                == "ns"
            )
            assert json.loads(result.primary.schema.metadata[b"r7.precision"])[0]["possible_loss"]


@pytest.mark.runtime
def test_review_version():
    from contextlib import contextmanager
    from dataclasses import replace
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import ibis
    import pyarrow as pa

    from marivo.analysis.compiler.graph_lowering import (
        CoordinateColumn,
        RelationLayout,
        SourceBinding,
    )
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import SourceLeaf, method_node
    from marivo.analysis.core.model import Coordinate, DomainSignature, Signature
    from marivo.analysis.core.rules import PreparedObservation
    from marivo.analysis.materialization.contracts import canonical_json
    from marivo.analysis.materialization.execution_key import SourceKeyBinding
    from marivo.analysis.materialization.graph_protocol import digest
    from marivo.refs import ref
    from marivo.semantic.ir import TargetValidityVersion

    f = globals()
    with TemporaryDirectory(prefix="marivo-review-version-") as d:
        directory = Path(d).resolve()
        with f["_case"](directory) as (runtime, occurrence, keys, source, submissions):
            population, original = occurrence.sources
            a = occurrence.parameters.events[0]
            version = TargetValidityVersion(
                f["_payload"](ref.time_dimension("commerce.metrics.start")),
                f["_payload"](ref.time_dimension("commerce.metrics.end")),
                "start",
                "end",
                "closed_open",
                (None,),
                "UTC",
            )
            contract = replace(
                f["_entity"]("commerce.metrics", f["TableSourceIR"]("metrics"), ("occurrence",)),
                version=version,
            )
            k = Coordinate(ref.entity("commerce.metrics"), "occurrence", "identity")
            leaf = SourceLeaf(
                replace(
                    original.definition,
                    ref=ref.entity("commerce.metrics"),
                    fingerprint=contract.dependency_fingerprint,
                    version=version,
                ),
                Signature(
                    DomainSignature(
                        population.signature.domain.binding,
                        "entity",
                        (k,),
                        (k,),
                        "commerce.metrics",
                    )
                ),
                original.value_type,
            )
            hop = replace(
                a.path[0],
                ref=f["_payload"](ref.relationship("commerce.metrics_to_users")),
                from_entity_ref=contract.ref,
            )
            old, routes = f["_observation"](occurrence, "sum")
            observation = old.parameters.observation
            graph = observation.metric.graph
            record = graph.nodes[0]
            graph = replace(
                graph,
                nodes=(
                    replace(
                        record,
                        node=replace(
                            record.node,
                            target_ref=f["_payload"](ref.measure("commerce.metrics.amount")),
                        ),
                    ),
                ),
            )
            event = f["_dimension"](contract, "instant", time=True)
            metric = replace(
                observation.metric,
                graph=graph,
                contribution=leaf.definition.ref,
                event_ref=ref.time_dimension(event.ref.path),
            )
            params = replace(
                observation,
                metric=metric,
                contribution=leaf.definition.ref,
                path=(hop,),
                event=event,
            )
            root = method_node(
                old.inputs,
                PreparedObservation(params),
                value_type=old.value_type,
                sources=(population, leaf),
            )
            routes = (*routes[:-1], RouteChoice(root.identity, "ibis_python"))
            con = ibis.duckdb.connect(directory / "source.duckdb")
            con.create_table(
                "metrics",
                pa.table(
                    {
                        "occurrence": ["same", "same"],
                        "subject": ["subject0", "subject0"],
                        "amount": [10, 900],
                        "instant": pa.array([1, 15], type=pa.timestamp("s", tz="UTC")),
                        "start": pa.array([0, 10], type=pa.timestamp("s", tz="UTC")),
                        "end": pa.array([20, 30], type=pa.timestamp("s", tz="UTC")),
                    }
                ),
            )
            con.disconnect()
            binding = SourceKeyBinding(
                leaf,
                leaf.definition.shape,
                leaf.definition.fingerprint,
                digest(canonical_json(contract.source.to_dict())),
            )

            @contextmanager
            def factory():
                with source() as (session, bindings):
                    bound = session.bind(contract.source, source_identity=leaf.identity)
                    yield (
                        session,
                        (
                            *bindings,
                            SourceBinding(
                                leaf,
                                bound,
                                RelationLayout((CoordinateColumn(k, "occurrence"),), None, ()),
                            ),
                        ),
                    )

            with pytest.raises(DomainPreparationError, match="non-overlapping"):
                artifact = runtime._execute_graph(
                    root, routes, source_bindings=(*keys, binding), source_factory=factory
                )


@pytest.mark.runtime
def test_review_postcommit_deadline_returns_durable_success(tmp_path, monkeypatch):
    import marivo.analysis.materialization.graph_publication as publication

    with _case(tmp_path) as (runtime, root, bindings, source, _):
        now = [0.0]
        original = runtime._event

        def event(name):
            original(name)
            if name == "after_commit":
                now[0] = 600.001

        runtime._event = event
        monkeypatch.setattr(publication.time, "monotonic", lambda: now[0])
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        assert runtime.store._graph_run(runtime.last_run_ref).lifecycle == "succeeded"
        assert artifact.producing_run_ref == runtime.last_run_ref


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("policy", ["first_per_subject", "shared", "exclusive"])
def test_matching_consumes_prepared_capture(tmp_path, form, policy):
    from marivo.analysis.core.model import SubjectPart
    from marivo.analysis.core.rules import JourneyMatch
    from marivo.analysis.materialization.journey_execution import ASSIGNMENT

    with _case(tmp_path, form=form) as (runtime, capture, bindings, source, submissions):
        capture = method_node(
            capture.inputs,
            replace(capture.parameters, order_use="ordered"),
            value_type=capture.value_type,
            sources=capture.sources,
        )
        mapping = next(part for part in capture.signature.parts if isinstance(part, SubjectPart))
        keys = (*mapping.subject_key, *capture.signature.domain.instance_key)
        domain = DomainSignature(
            capture.signature.domain.binding, "journey", keys, keys, "r73-journey"
        )
        root = method_node(
            (Edge("subject", capture),),
            JourneyMatch(
                domain,
                ("start", "end"),
                ("commerce.hit", "commerce.hit"),
                policy,
                "1970-01-01T00:00:00+00:00",
                "1970-01-01T00:00:10+00:00",
                "1970-01-01T00:00:10+00:00",
            ),
            value_type=ScalarType("int64"),
        )
        assert thaw_graph(freeze_graph(root)).fingerprint == root.fingerprint
        artifact = runtime._execute_graph(
            root,
            (RouteChoice(capture.identity, "ibis"), RouteChoice(root.identity, "ibis_python")),
            source_bindings=bindings,
            source_factory=source,
        )
        result = read_result(tmp_path, artifact.descriptor)
        values = [
            ASSIGNMENT.validate_json(value)
            for value in next(part.table for part in result.parts if part.role == "journey")[
                "journey__assignment"
            ].to_pylist()
        ]
        assert len(values) == (2 if policy == "first_per_subject" else 3)
        assert values[0].reach == ("reached", "reached")
        assert all(value.reach == ("reached", "unknown") for value in values[1:])

        captured = runtime._execute_graph(
            capture,
            (RouteChoice(capture.identity, "ibis"),),
            source_bindings=bindings,
            source_factory=source,
        )
        leaf = FixedLeaf(
            ArtifactRef(captured.artifact_ref),
            capture.fingerprint,
            fixed_signature(captured.descriptor),
            ScalarType("int64"),
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        fixed_match = method_node(
            (Edge("subject", leaf),), root.parameters, value_type=ScalarType("int64")
        )
        fixed = runtime._execute_graph(
            fixed_match, (RouteChoice(fixed_match.identity, "artifact_python"),)
        )
        retained = read_result(tmp_path, fixed.descriptor)
        assert logical_table(retained.primary).equals(logical_table(result.primary))
        assert next(part.table for part in retained.parts if part.role == "journey").equals(
            next(part.table for part in result.parts if part.role == "journey")
        )

        for path in (*tmp_path.glob("*.parquet"), *tmp_path.glob("source.duckdb*")):
            path.unlink()
        script = """
import sys
from pathlib import Path
import ibis
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.graph_store import artifact
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.journey_execution import ASSIGNMENT
def forbidden(*args, **kwargs):
    raise AssertionError('cold Journey recovery must not read sources')
SourceSession.bind = forbidden
ibis.duckdb.connect = forbidden
store = SessionStore(Path(sys.argv[1]))
with store._connection() as connection:
    record = artifact(store, connection, sys.argv[2])
result = read_result(store.project_root, record.descriptor)
values = [ASSIGNMENT.validate_json(value) for value in next(part.table for part in result.parts if part.role == 'journey')['journey__assignment'].to_pylist()]
assert len(values) == int(sys.argv[3])
assert values[0].reach == ('reached', 'reached')
assert all(value.reach == ('reached', 'unknown') for value in values[1:])
assert b'r7.precision' in result.primary.schema.metadata
"""
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), artifact.artifact_ref, str(len(values))],
            check=True,
            capture_output=True,
            text=True,
        )
