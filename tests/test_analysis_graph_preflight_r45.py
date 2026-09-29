"""R1 schema admission does not turn physical metadata into business evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import (
    ComponentColumn,
    CoordinateColumn,
    PartColumns,
    RelationLayout,
    SourceBinding,
    lower,
)
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import Binding, Coordinate, DomainSignature, Signature, SubjectPart
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import BindProject, MapCorrespond, PartsTransport
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_execution import prepare_graph
from marivo.analysis.materialization.graph_members import construct_members
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest
from marivo.analysis.materialization.graph_source_execution import execute_source_graph
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import TargetDimensionContract
from marivo.semantic.validator import normalize_target_dimension
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
def test_preflight_reads_only_r1_schema_and_preserves_exact_key_type(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = f"{case.names.domain}.{case.names.customer}"
    registry = case.catalog._state.registry

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema admission must not issue a business read")

    monkeypatch.setattr(SourceSession, "compile", forbidden)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    runs_before = len(case.session.runs().items)
    selected = preflight_entities(registry, case.root, (entity,))

    assert len(selected) == 1
    assert selected[0].identity_type.name == "string"
    assert str(selected[0].schema.field(case.names.customer_id).type) == "string"
    assert len(case.session.runs().items) == runs_before


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("table", "parquet"))
@pytest.mark.parametrize("numeric_type", ("int64", "float64"))
def test_exact_member_builder_publishes_v7_from_authored_project(
    analysis_dsl_case_factory: DslCaseFactory, tmp_path: Path, form: str, numeric_type: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    fresh = tmp_path / "graph_project"
    fresh.mkdir()
    shutil.copytree(case.root / "models", fresh / "models")
    shutil.copyfile(case.root / "marivo.toml", fresh / "marivo.toml")
    source_files = fresh / "source_files"
    source_files.mkdir()
    backend = ibis.duckdb.connect(case.database_path)
    try:
        if numeric_type == "float64":
            data = backend.table(case.names.order).to_pyarrow()
            column = data.schema.get_field_index(case.names.amount)
            data = data.set_column(
                column, case.names.amount, data.column(column).cast(pa.float64())
            )
            backend.create_table(case.names.order, data, overwrite=True)
    finally:
        backend.disconnect()
    if form == "parquet":
        export_dsl_parquet_models(case, fresh)
    catalog = ms.load(workspace_dir=fresh)
    store = SessionStore._graph_store(fresh)
    session = store.create_session("v7-members")
    runtime = DatasetRuntime(store, session.session_ref)
    entity = ms.ref.entity(f"{case.names.domain}.{case.names.customer}")
    graph = construct_members(runtime, catalog._state.registry, entity)
    saved = graph.execute()
    actual = read_result(fresh, saved.descriptor)
    from marivo.analysis.materialization.graph_dataset import GraphDataset

    public_result = GraphDataset(runtime, saved)
    assert public_result.state.artifact_ref.ref == saved.artifact_ref
    assert public_result.state.producing_run_ref == saved.producing_run_ref
    assert sorted(public_result.to_pandas()["member"].tolist()) == ["A", "B", "C", "D"]
    assert sorted(actual.primary.column("key_0").to_pylist()) == ["A", "B", "C", "D"]
    assert tuple(part.role for part in actual.parts) == ("subject",)
    assert store._graph_run(saved.producing_run_ref) is not None
    region = ms.ref.dimension(f"{case.names.domain}.{case.names.customer}.{case.names.region}")
    read = graph.read(region)
    with pytest.raises(DatasetConstructionError, match="exact physical type"):
        read.where(ValuePredicate(graph.root.signature.domain.binding, "eq", 1))
    read_result_value = read_result(fresh, read.execute().descriptor)
    assert sorted(read_result_value.primary.column("value").to_pylist()) == [
        "east",
        "east",
        "south",
        "west",
    ]
    selected = read.where(ValuePredicate(graph.root.signature.domain.binding, "eq", "west"))
    selected_result = read_result(fresh, selected.execute().descriptor)
    assert selected_result.primary.column("key_0").to_pylist() == ["D"]
    grouped = read_result(fresh, graph.group_by_value(region).execute().descriptor)
    assert sorted(grouped.primary.column("key_0").to_pylist()) == ["east", "south", "west"]
    import marivo.analysis as mv
    from marivo.analysis.materialization.graph_observation import observe_members

    observed = observe_members(
        graph,
        ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
        coordinates=(
            ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}"),
        ),
    )
    saved_observation = observed.execute()
    observation = read_result(fresh, saved_observation.descriptor)
    rows = {row["key_0"]: row for row in observation.primary.to_pylist()}
    assert sum(row["value"] or 0 for row in rows.values()) == 1000
    assert rows["D"]["cell_tag"] == "null"
    assert rows["D"]["cell_reason"] == "empty_contribution"
    assert tuple(part.role for part in observation.parts) == (
        "subject",
        "original_state",
        "coverage",
        "coordinate_state",
    )
    coordinate_rows = observation.parts[-1].table.to_pylist()
    assert (
        next(row for row in coordinate_rows if row["key_0"] == "D")["coordinate_state__groups"]
        == []
    )
    assert (
        sum(group["sum"] for row in coordinate_rows for group in row["coordinate_state__groups"])
        == 1000
    )
    from marivo.analysis.core.graph import FixedLeaf
    from marivo.analysis.core.rules import OriginalReduce
    from marivo.analysis.materialization.graph_protocol import fixed_signature
    from marivo.analysis.methods.physical import FixedShape, NoTime
    from marivo.analysis.refs import ArtifactRef

    singleton = DomainSignature(
        observed.root.signature.domain.binding, "singleton", (), (), "total"
    )
    rolled = method_node(
        (Edge("quantity", observed.root),),
        OriginalReduce(
            singleton, "source.contribution_partition@v1", "source.complete_coverage@v1"
        ),
        value_type=ScalarType(numeric_type),
    )
    total = replace(observed, root=rolled).execute()
    assert read_result(fresh, total.descriptor).primary["value"].to_pylist() == [1000]
    fixed = FixedLeaf(
        ArtifactRef(saved_observation.artifact_ref),
        saved_observation.descriptor.definition_fingerprint,
        fixed_signature(saved_observation.descriptor),
        ScalarType(numeric_type),
        FixedShape(NoTime()),
    )
    from tests.shared_fixtures import analysis_dsl_rows

    channel = ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}")
    group_key = (
        Coordinate(ms.ref.entity(f"{case.names.domain}.{case.names.order}"), channel.path, "group"),
    )
    group_domain = DomainSignature(
        observed.root.signature.domain.binding, "group", group_key, group_key, "channels"
    )
    group_params = OriginalReduce(
        group_domain,
        "source.contribution_partition@v1",
        "source.complete_coverage@v1",
        coordinates=group_domain.instance_key,
    )
    group_root = method_node(
        (Edge("quantity", observed.root),), group_params, value_type=ScalarType(numeric_type)
    )
    grouped_saved = replace(observed, root=group_root).execute()
    grouped_rows = read_result(fresh, grouped_saved.descriptor).primary.to_pylist()
    expected_channels: dict[str, int | float] = {}
    for _, _, label, _, instant, amount in analysis_dsl_rows("j1").orders:
        if "2026-08-01" <= instant < "2026-09-01":
            assert label is not None
            expected_channels[label] = expected_channels.get(label, 0) + amount
    assert {row["key_0"]: row["value"] for row in grouped_rows} == expected_channels
    fixed_group = method_node(
        (Edge("quantity", fixed),),
        OriginalReduce(group_domain, coordinates=group_domain.instance_key),
        value_type=ScalarType(numeric_type),
    )
    grouped_saved = runtime._execute_graph(
        fixed_group, (RouteChoice(fixed_group.identity, "artifact_python"),)
    )
    assert {
        row["key_0"]: row["value"]
        for row in read_result(fresh, grouped_saved.descriptor).primary.to_pylist()
    } == expected_channels
    fixed_total = method_node(
        (Edge("quantity", fixed),), OriginalReduce(singleton), value_type=ScalarType(numeric_type)
    )
    saved_total = runtime._execute_graph(
        fixed_total, (RouteChoice(fixed_total.identity, "artifact_python"),)
    )
    assert read_result(fresh, saved_total.descriptor).primary["value"].to_pylist() == [1000]
    for zone, expected in (("Asia/Shanghai", 677), ("America/Los_Angeles", 649)):
        zoned = observe_members(
            graph,
            ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
            sidecar=catalog._state.sidecar,
            report_timezone=zone,
        )
        assert (
            sum(
                value or 0
                for value in read_result(fresh, zoned.execute().descriptor)
                .primary["value"]
                .to_pylist()
            )
            == expected
        )
    filtered_observation = observe_members(
        selected,
        ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
    )
    filtered_rows = read_result(
        fresh, filtered_observation.execute().descriptor
    ).primary.to_pylist()
    assert filtered_rows == [
        {"key_0": "D", "value": None, "cell_tag": "null", "cell_reason": "empty_contribution"}
    ]
    grouped_observation = observe_members(
        graph.group_by_value(region),
        ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
    )
    group_result = read_result(fresh, grouped_observation.execute().descriptor)
    assert {row["key_0"]: row["value"] for row in group_result.primary.to_pylist()} == {
        "east": 600,
        "south": 400,
        "west": None,
    }
    assert tuple(part.role for part in group_result.parts) == ("original_state", "coverage")
    counted = observe_members(
        graph,
        ms.ref.metric(f"{case.names.domain}.{case.names.order_count}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
    )
    counted_artifact = counted.execute()
    counted_rows = read_result(fresh, counted_artifact.descriptor).primary.to_pylist()
    assert {row["key_0"]: row["value"] for row in counted_rows} == {"A": 1, "B": 1, "C": 1, "D": 0}
    assert all(row["cell_tag"] == "defined" for row in counted_rows)
    count_root = method_node(
        (Edge("quantity", counted.root),),
        OriginalReduce(
            singleton, "source.contribution_partition@v1", "source.complete_coverage@v1", "count"
        ),
        value_type=ScalarType("int64"),
    )
    assert read_result(fresh, replace(counted, root=count_root).execute().descriptor).primary[
        "value"
    ].to_pylist() == [3]
    count_leaf = FixedLeaf(
        ArtifactRef(counted_artifact.artifact_ref),
        counted_artifact.descriptor.definition_fingerprint,
        fixed_signature(counted_artifact.descriptor),
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    fixed_count_root = method_node(
        (Edge("quantity", count_leaf),),
        OriginalReduce(singleton, method="count"),
        value_type=ScalarType("int64"),
    )
    count_total = runtime._execute_graph(
        fixed_count_root, (RouteChoice(fixed_count_root.identity, "artifact_python"),)
    )
    assert read_result(fresh, count_total.descriptor).primary["value"].to_pylist() == [3]
    from marivo.analysis.core.graph import topology
    from marivo.analysis.materialization.graph_composition import combine_observations

    association = combine_observations(observed, counted, "spearman")
    assert len([node for node in topology(association.root) if isinstance(node, SourceLeaf)]) == 2
    association_result = read_result(fresh, association.execute().descriptor)
    assert association_result.primary["status"].to_pylist() == ["constant_b"]
    assert association_result.parts[0].table["pair_counts__complete_pair_count"].to_pylist() == [3]
    offline = case.database_path.with_suffix(".offline")
    models_offline = fresh / "models.offline"
    case.database_path.rename(offline)
    (fresh / "models").rename(models_offline)
    source_files.rename(fresh / "source_files.offline")
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                """\
import sys
import ibis
import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
from marivo.analysis.core.model import DomainSignature
from marivo.analysis.core.rules import OriginalReduce
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.graph_protocol import fixed_signature
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.refs import ArtifactRef
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError("cold fixed rollup must not connect or load Semantic")

ibis.duckdb.connect = forbidden
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
store = SessionStore._graph_store(sys.argv[1], existing_only=True)
with store._read() as conn:
    saved = graph_store.artifact(store, conn, sys.argv[2])
assert saved is not None
signature = fixed_signature(saved.descriptor)
leaf = FixedLeaf(ArtifactRef(saved.artifact_ref), saved.descriptor.definition_fingerprint,
                 signature, ScalarType(sys.argv[3]), FixedShape(NoTime()))
root = method_node((Edge("quantity", leaf),),
                   OriginalReduce(DomainSignature(signature.domain.binding, "singleton", (), (), "cold-total")),
                   value_type=ScalarType(sys.argv[3]))
runtime = DatasetRuntime(store, saved.session_ref)
output = runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
assert output.producing_run_ref != saved.producing_run_ref
assert read_result(store.project_root, output.descriptor).primary["value"].to_pylist() == [1000]
""",
                str(fresh),
                saved_observation.artifact_ref,
                numeric_type,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
        models_offline.rename(fresh / "models")
        (fresh / "source_files.offline").rename(source_files)
    assert process.returncode == 0, process.stderr


@pytest.mark.runtime
def test_preflight_rejects_unqualified_source_before_open(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    original_registry = case.catalog._state.registry
    original = original_registry.datasources["warehouse"]
    registry = replace(
        original_registry,
        datasources={
            **original_registry.datasources,
            "warehouse": replace(original, backend_type="postgres"),
        },
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsupported backend must reject before source open")

    monkeypatch.setattr(DatasourceConnectionService, "use_backend", forbidden)
    with pytest.raises(DatasetConstructionError, match="DuckDB"):
        preflight_entities(registry, case.root, (f"{case.names.domain}.{case.names.customer}",))


@pytest.mark.runtime
def test_preflight_schema_change_is_not_accepted_as_selected_binding(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = f"{case.names.domain}.{case.names.customer}"
    registry = case.catalog._state.registry
    first = preflight_entities(registry, case.root, (entity,))[0]
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{case.names.customer}" ADD COLUMN changed INTEGER')
    datasource = registry.datasources["warehouse"]
    service = DatasourceConnectionService(case.root, include_semantic_layers=True)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(first.contract.source, source_identity=entity)
        with pytest.raises(DatasetConstructionError, match="changed physical binding"):
            first.verify(bound)


@pytest.mark.runtime
def test_string_member_view_uses_exact_graph_and_r1_read(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = f"{case.names.domain}.{case.names.customer}"
    selected = preflight_entities(case.catalog._state.registry, case.root, (entity,))[0]
    member_ref = ms.ref.entity(entity)
    coordinate = Coordinate(member_ref, case.names.customer_id, "identity")
    domain = DomainSignature(
        Binding(case.session.id, "customer", "members", "all"),
        "entity",
        (coordinate,),
        (coordinate,),
        "customer-members",
    )
    leaf = SourceLeaf(
        SourceDefinition(
            member_ref,
            selected.contract.dependency_fingerprint,
            ms.ref.datasource("warehouse"),
            selected.shape,
        ),
        Signature(domain, None),
        selected.identity_type,
    )
    root = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", domain, (), False),
        value_type=selected.identity_type,
    )
    prepared = prepare_graph(
        root, session_ref=case.session.id, routes=(RouteChoice(root.identity, "ibis"),)
    )
    datasource = case.catalog._state.registry.datasources["warehouse"]
    service = DatasourceConnectionService(case.root, include_semantic_layers=True)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(selected.contract.source, source_identity=leaf.identity)
        selected.verify(bound)
        layout = RelationLayout((CoordinateColumn(coordinate, case.names.customer_id),), None)
        lowered = lower(prepared.admitted, bindings=(SourceBinding(leaf, bound, layout),))
        result = execute_source_graph(prepared, lowered, source)
    assert sorted(result.primary.column("key_0").to_pylist()) == ["A", "B", "C", "D"]


@pytest.mark.runtime
def test_string_dimension_binding_uses_exact_graph_and_r1_read(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    registry = case.catalog._state.registry
    entity = f"{case.names.domain}.{case.names.customer}"
    selected = preflight_entities(registry, case.root, (entity,))[0]
    member_ref = ms.ref.entity(entity)
    coordinate = Coordinate(member_ref, case.names.customer_id, "identity")
    domain = DomainSignature(
        Binding(case.session.id, "customer", "members", "all"),
        "entity",
        (coordinate,),
        (coordinate,),
        "customer-members",
    )
    leaf = SourceLeaf(
        SourceDefinition(
            member_ref,
            selected.contract.dependency_fingerprint,
            ms.ref.datasource("warehouse"),
            selected.shape,
        ),
        Signature(domain, None),
        selected.identity_type,
    )
    dimension_ref = ms.ref.dimension(f"{entity}.{case.names.region}")
    authored = normalize_target_dimension(registry, dimension_ref.path)
    assert authored.logical_type == "unknown"
    dimension = replace(authored, logical_type=selected.field_type(authored.source_column).name)
    root = method_node(
        (Edge("subject", leaf),),
        BindProject(dimension_ref, member_ref, dimension, None, (), ()),
        sources=(leaf,),
        value_type=selected.identity_type,
    )
    selected_root = method_node(
        (Edge("subject", root),),
        PartsTransport(
            "where",
            domain,
            (),
            True,
            (ValuePredicate(domain.binding, "eq", "east", "drop"),),
        ),
        value_type=selected.identity_type,
    )
    prepared = prepare_graph(
        selected_root,
        session_ref=case.session.id,
        routes=(RouteChoice(root.identity, "ibis"), RouteChoice(selected_root.identity, "ibis")),
    )
    datasource = registry.datasources["warehouse"]
    service = DatasourceConnectionService(case.root, include_semantic_layers=True)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(selected.contract.source, source_identity=leaf.identity)
        selected.verify(bound)
        layout = RelationLayout((CoordinateColumn(coordinate, case.names.customer_id),), None)
        lowered = lower(prepared.admitted, bindings=(SourceBinding(leaf, bound, layout),))
        result = execute_source_graph(prepared, lowered, source)
    assert dict(
        zip(
            result.primary.column("key_0").to_pylist(),
            result.primary.column("value").to_pylist(),
            strict=True,
        )
    ) == {"A": "east", "B": "east"}


@pytest.mark.runtime
def test_string_group_is_a_qualified_source_graph_method(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    registry = case.catalog._state.registry
    entity_path = f"{case.names.domain}.{case.names.customer}"
    selected = preflight_entities(registry, case.root, (entity_path,))[0]
    entity = ms.ref.entity(entity_path)
    identity = Coordinate(entity, case.names.customer_id, "identity")
    binding = Binding(case.session.id, "customer", "members", "all")
    domain = DomainSignature(binding, "entity", (identity,), (identity,), "customer-members")
    leaf = SourceLeaf(
        SourceDefinition(
            entity,
            selected.contract.dependency_fingerprint,
            ms.ref.datasource("warehouse"),
            selected.shape,
        ),
        Signature(domain, None),
        selected.identity_type,
    )
    dimension_ref = ms.ref.dimension(f"{entity_path}.{case.names.region}")
    authored = normalize_target_dimension(registry, dimension_ref.path)
    dimension = replace(authored, logical_type="string")
    read = method_node(
        (Edge("subject", leaf),),
        BindProject(dimension_ref, entity, dimension, None, (), ()),
        sources=(leaf,),
        value_type=selected.identity_type,
    )
    group_key = Coordinate(entity, case.names.region, "group")
    group_domain = DomainSignature(binding, "group", (group_key,), (group_key,), "region")
    grouped = method_node(
        (Edge("subject", read),),
        MapCorrespond("group", group_domain, "source.group_mapping@v1"),
        value_type=ScalarType("string"),
    )
    routes = (RouteChoice(read.identity, "ibis"), RouteChoice(grouped.identity, "ibis"))
    prepared = prepare_graph(grouped, session_ref=case.session.id, routes=routes)
    datasource = registry.datasources["warehouse"]
    service = DatasourceConnectionService(case.root, include_semantic_layers=True)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(selected.contract.source, source_identity=leaf.identity)
        lowered = lower(
            prepared.admitted,
            bindings=(
                SourceBinding(
                    leaf,
                    bound,
                    RelationLayout((CoordinateColumn(identity, case.names.customer_id),), None),
                ),
            ),
        )
        result = execute_source_graph(prepared, lowered, source)
    assert sorted(result.primary.column("key_0").to_pylist()) == ["east", "south", "west"]


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("table", "parquet"))
def test_v7_member_subject_part_survives_publication_and_exact_read(
    tmp_path: Path, form: Literal["table", "parquet"]
) -> None:
    store = SessionStore._graph_store(tmp_path)
    session = store.create_session("member-part")
    runtime = DatasetRuntime(store, session.session_ref)
    entity = ms.ref.entity("inventory.product")
    coordinate = Coordinate(entity, "id", "identity")
    binding = Binding(session.session_ref, "inventory", "products", "all")
    domain = DomainSignature(binding, "entity", (coordinate,), (coordinate,), "products")
    subject = SubjectPart(binding, entity, (coordinate,), (coordinate,), True, True, "v1")
    leaf = SourceLeaf(
        SourceDefinition(
            entity,
            "inventory-product-v1",
            ms.ref.datasource("db"),
            SourceShape("duckdb", form, "native" if form == "table" else "parquet", NoTime()),
        ),
        Signature(domain, None, parts=(subject,)),
        ScalarType("string"),
    )
    root = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", domain, ("subject",), False),
        value_type=ScalarType("string"),
    )
    data = pa.table({"id": ["A", "B"], "region": ["east", "west"]})
    source_path = tmp_path / ("source.duckdb" if form == "table" else "source.parquet")
    if form == "table":
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        backend.create_table("products", data)
        backend.disconnect()
        source_ir: TableSourceIR | ParquetSourceIR = TableSourceIR("products")
    else:
        pq.write_table(data, source_path)
        source_ir = ParquetSourceIR(str(source_path))
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("db.py", 1)
    )

    @contextmanager
    def source() -> Iterator[tuple[SourceSession, tuple[SourceBinding, ...]]]:
        with SourceSession(
            provider_for("duckdb"), datasource, ibis.duckdb.connect(tmp_path / "source.duckdb")
        ) as selected:
            bound = selected.bind(source_ir, source_identity=leaf.identity)
            layout = RelationLayout(
                (CoordinateColumn(coordinate, "id"),),
                None,
                (PartColumns(subject, (ComponentColumn("key_0", "id"),)),),
            )
            yield selected, (SourceBinding(leaf, bound, layout),)

    selected_binding = SourceKeyBinding(
        leaf,
        leaf.definition.shape,
        leaf.definition.fingerprint,
        digest(canonical_json(source_ir.to_dict())),
    )
    with SourceSession(
        provider_for("duckdb"), datasource, ibis.duckdb.connect(tmp_path / "source.duckdb")
    ) as preflight_source:
        selected_schema = preflight_source.bind(
            source_ir, source_identity=leaf.identity
        ).facts.schema
    saved = runtime._execute_graph(
        root,
        (RouteChoice(root.identity, "ibis"),),
        source_bindings=(selected_binding,),
        source_factory=source,
        source_schemas=(selected_schema,),
    )
    restored = read_result(tmp_path, saved.descriptor)
    assert sorted(restored.primary.column("key_0").to_pylist()) == ["A", "B"]
    assert tuple(part.role for part in restored.parts) == ("subject",)
    mapped_root = method_node(
        (Edge("subject", leaf),),
        MapCorrespond("subjects", domain, "source.exact_pairing@v1"),
        value_type=ScalarType("string"),
    )
    mapped_saved = runtime._execute_graph(
        mapped_root,
        (RouteChoice(mapped_root.identity, "ibis"),),
        source_bindings=(selected_binding,),
        source_factory=source,
        source_schemas=(selected_schema,),
    )
    assert sorted(
        read_result(tmp_path, mapped_saved.descriptor).primary.column("key_0").to_pylist()
    ) == ["A", "B"]
    dimension_ref = ms.ref.dimension("inventory.product.region")
    dimension = TargetDimensionContract(
        RefPayloadV1.from_ref(dimension_ref),
        RefPayloadV1.from_ref(entity),
        "region",
        "string",
        True,
        False,
        None,
        False,
        None,
    )
    read = method_node(
        (Edge("subject", leaf),),
        BindProject(dimension_ref, entity, dimension, None, (), ()),
        sources=(leaf,),
        value_type=ScalarType("string"),
    )
    selected_root = method_node(
        (Edge("subject", read),),
        PartsTransport(
            "where", domain, ("subject",), True, (ValuePredicate(binding, "eq", "east"),)
        ),
        value_type=ScalarType("string"),
    )
    selected_saved = runtime._execute_graph(
        selected_root,
        (RouteChoice(read.identity, "ibis"), RouteChoice(selected_root.identity, "ibis")),
        source_bindings=(selected_binding,),
        source_factory=source,
        source_schemas=(selected_schema,),
    )
    selected_result = read_result(tmp_path, selected_saved.descriptor)
    assert selected_result.primary.column("key_0").to_pylist() == ["A"]
    assert selected_result.primary.column("value").to_pylist() == ["east"]
    assert tuple(part.role for part in selected_result.parts) == ("subject",)
    offline_path = tmp_path / "source.offline"
    source_path.rename(offline_path)
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                """\
import sys
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_storage import read_result

store = SessionStore._graph_store(sys.argv[1], existing_only=True)
with store._read() as conn:
    record = graph_store.artifact(store, conn, sys.argv[2])
assert record is not None
result = read_result(store.project_root, record.descriptor)
assert result.primary.column('key_0').to_pylist() == ['A']
assert result.primary.column('value').to_pylist() == ['east']
assert tuple(part.role for part in result.parts) == ('subject',)
""",
                str(tmp_path),
                selected_saved.artifact_ref,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline_path.rename(source_path)
    assert process.returncode == 0, process.stderr
    if form == "table":
        with duckdb.connect(str(source_path)) as connection:
            connection.execute('ALTER TABLE "products" ADD COLUMN changed INTEGER')
    else:
        pq.write_table(data.append_column("changed", pa.array([1, 2])), source_path)
    reads_before = runtime.statistics.primary_queries
    with pytest.raises(IntegrityError, match="selected preflight"):
        runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=(selected_binding,),
            source_factory=source,
            source_schemas=(selected_schema,),
        )
    assert runtime.statistics.primary_queries == reads_before


@pytest.mark.runtime
@pytest.mark.parametrize("scenario", ("j2", "j4", "j4_ties"))
def test_window_composition_matches_independent_oracle_and_reads_shared_sources_once(
    analysis_dsl_case_factory: DslCaseFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    from collections import Counter
    from datetime import datetime
    from statistics import correlation

    import ibis.expr.operations as ops

    import marivo.analysis as mv
    from marivo.analysis.core.graph import FixedLeaf, topology
    from marivo.analysis.core.rules import RowState
    from marivo.analysis.materialization.graph_composition import combine_observations
    from marivo.analysis.materialization.graph_observation import observe_members
    from marivo.analysis.materialization.graph_protocol import fixed_signature
    from marivo.analysis.methods.physical import FixedShape, NoTime
    from marivo.analysis.refs import ArtifactRef
    from tests.shared_fixtures import analysis_dsl_rows

    case = analysis_dsl_case_factory(scenario)
    fresh = tmp_path / "qualified_graph"
    fresh.mkdir()
    shutil.copytree(case.root / "models", fresh / "models")
    shutil.copyfile(case.root / "marivo.toml", fresh / "marivo.toml")
    catalog = ms.load(workspace_dir=fresh)
    store = SessionStore._graph_store(fresh)
    session = store.create_session("composed-window")
    runtime = DatasetRuntime(store, session.session_ref)
    members = construct_members(
        runtime,
        catalog._state.registry,
        ms.ref.entity(f"{case.names.domain}.{case.names.customer}"),
    )
    via = ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}")

    def observe(metric, month):
        return observe_members(
            members,
            ms.ref.metric(f"{case.names.domain}.{metric}"),
            during=mv.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
            via=via,
            sidecar=catalog._state.sidecar,
            report_timezone="UTC",
        )

    left = observe(case.names.revenue, 8)
    right = (
        observe(case.names.revenue, 7) if scenario == "j2" else observe(case.names.order_count, 8)
    )
    composed = combine_observations(left, right, "difference" if scenario == "j2" else "spearman")
    nodes = topology(composed.root)
    assert len([node for node in nodes if isinstance(node, SourceLeaf)]) == 2
    physical_reads = []
    staged = []
    original_compile, original_stage = SourceSession.compile, SourceSession.stage_derived

    def compile_read(self, qualified, expression, **kwargs):
        physical_reads.extend(
            table.name
            for table in expression.op().find(ops.DatabaseTable)
            if table.name in (case.names.customer, case.names.order)
        )
        return original_compile(self, qualified, expression, **kwargs)

    def stage_read(self, read):
        staged.append(read.purpose)
        return original_stage(self, read)

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "compile", compile_read)
        patch.setattr(SourceSession, "stage_derived", stage_read)
        saved = composed.execute()
    assert Counter(physical_reads) == Counter({case.names.customer: 1, case.names.order: 1})
    assert len(staged) == len(nodes)
    with store._read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_action_runs").fetchone()[0] == 1
    result = read_result(fresh, saved.descriptor)
    facts = analysis_dsl_rows(scenario)
    keys = sorted(customer for customer, _ in facts.customers)

    def totals(month):
        return {
            key: sum(
                row[-1]
                for row in facts.orders
                if row[1] == key and datetime.fromisoformat(row[-2]).month == month
            )
            for key in keys
        }

    if scenario == "j2":
        current, baseline = totals(8), totals(7)
        expected = {key: current[key] - baseline[key] for key in keys}
        assert {row["key_0"]: row["value"] for row in result.primary.to_pylist()} == expected
        assert tuple(part.role for part in result.parts) == (
            "subject",
            "current_endpoint",
            "baseline_endpoint",
        )
        selected = method_node(
            (Edge("quantity", composed.root),),
            PartsTransport(
                "where",
                composed.root.signature.domain,
                ("subject", "current_endpoint", "baseline_endpoint"),
                True,
                (ValuePredicate(composed.root.signature.domain.binding, "lt", 0),),
            ),
            value_type=ScalarType("int64"),
        )
        projected = method_node(
            (Edge("quantity", selected),),
            PartsTransport("projection", selected.signature.domain, ("subject",), False),
            value_type=ScalarType("int64"),
        )
        selected_members = replace(composed, root=projected)
        september = observe_members(
            selected_members,
            ms.ref.metric(f"{case.names.domain}.{case.names.revenue}"),
            during=mv.time_scope(start="2026-09-01", end="2026-10-01"),
            via=via,
            sidecar=catalog._state.sidecar,
            report_timezone="UTC",
        )
        next_result = read_result(fresh, september.execute().descriptor)
        assert {row["key_0"]: row["value"] for row in next_result.primary.to_pylist()} == {
            key: value for key, value in totals(9).items() if expected[key] < 0
        }
        return

    totals_by_key = totals(8)
    counts_by_key = {key: sum(row[1] == key for row in facts.orders) for key in keys}

    def ranks(values):
        return [
            1
            + sum(other < value for other in values)
            + (sum(other == value for other in values) - 1) / 2
            for value in values
        ]

    expected_coefficient = correlation(
        ranks(list(totals_by_key.values())), ranks(list(counts_by_key.values()))
    )
    assert result.primary["value"].to_pylist() == pytest.approx([expected_coefficient])
    assert result.primary["status"].to_pylist() == ["valid"]
    leaf = FixedLeaf(
        ArtifactRef(saved.artifact_ref),
        saved.descriptor.definition_fingerprint,
        fixed_signature(saved.descriptor),
        ScalarType("float64"),
        FixedShape(NoTime()),
    )
    for method in ("sum", "mean", "count"):
        for accepted in (True, False):
            selected = method_node(
                (Edge("quantity", leaf),),
                PartsTransport(
                    "where",
                    leaf.signature.domain,
                    ("pair_counts",),
                    True,
                    (
                        ValuePredicate(
                            leaf.signature.domain.binding, "gt" if accepted else "lt", -1, "reject"
                        ),
                    ),
                ),
                value_type=ScalarType("float64"),
            )
            root = method_node(
                (Edge("quantity", selected),),
                RowState(
                    method,
                    leaf.signature.domain,
                    f"coefficient-{method}-{accepted}",
                    "count_all" if method == "count" else "strict",
                    numeric_check_id="source.finite_numeric@v1",
                ),
                value_type=ScalarType("int64" if method == "count" else "float64"),
            )
            before = runtime.last_run_ref
            output = runtime._execute_graph(
                root,
                (
                    RouteChoice(selected.identity, "artifact_python"),
                    RouteChoice(root.identity, "artifact_python"),
                ),
            )
            assert output.producing_run_ref != before
            row = read_result(fresh, output.descriptor).primary.to_pylist()[0]
            if method == "count":
                assert row["value"] == int(accepted)
            elif accepted:
                assert row["value"] == pytest.approx(expected_coefficient)
            elif method == "sum":
                assert row["value"] == 0.0
            else:
                assert row["cell_tag"] == "undefined" and row["cell_reason"] == "empty_mean"


@pytest.mark.runtime
@pytest.mark.parametrize("scenario", ("j3", "j3_weighting", "zero_denominator"))
def test_two_hop_original_sum_zero_preserves_component_state(
    analysis_dsl_case_factory: DslCaseFactory,
    tmp_path: Path,
    scenario: str,
) -> None:
    import marivo.analysis as mv
    from marivo.analysis.core.graph import FixedLeaf
    from marivo.analysis.core.rules import OriginalReduce
    from marivo.analysis.materialization.graph_observation import observe_members
    from marivo.analysis.materialization.graph_protocol import fixed_signature
    from marivo.analysis.methods.physical import FixedShape, NoTime
    from marivo.analysis.refs import ArtifactRef
    from tests.shared_fixtures import analysis_dsl_rows

    case = analysis_dsl_case_factory(scenario)
    fresh = tmp_path / "component_graph"
    fresh.mkdir()
    shutil.copytree(case.root / "models", fresh / "models")
    shutil.copyfile(case.root / "marivo.toml", fresh / "marivo.toml")
    catalog = ms.load(workspace_dir=fresh)
    store = SessionStore._graph_store(fresh)
    session = store.create_session("original-components")
    runtime = DatasetRuntime(store, session.session_ref)
    members = construct_members(
        runtime,
        catalog._state.registry,
        ms.ref.entity(f"{case.names.domain}.{case.names.customer}"),
    )
    observed = observe_members(
        members,
        ms.ref.metric(f"{case.names.domain}.{case.names.line_revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=(
            ms.ref.relationship(f"{case.names.domain}.{case.names.line_order}"),
            ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
        ),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
    )
    saved = observed.execute()
    result = read_result(fresh, saved.descriptor)
    facts = analysis_dsl_rows(scenario)
    order_owner = {row[0]: row[1] for row in facts.orders}
    expected = {
        key: sum(amount for _, order, amount in facts.lines if order_owner[order] == key)
        for key, _ in facts.customers
    }
    assert {row["key_0"]: row["value"] for row in result.primary.to_pylist()} == expected
    assert all(row["cell_tag"] == "defined" for row in result.primary.to_pylist())
    singleton = DomainSignature(
        observed.root.signature.domain.binding, "singleton", (), (), "component-total"
    )
    root = method_node(
        (Edge("quantity", observed.root),),
        OriginalReduce(
            singleton, "source.contribution_partition@v1", "source.complete_coverage@v1", "sum_zero"
        ),
        value_type=ScalarType("int64"),
    )
    total = replace(observed, root=root).execute()
    assert read_result(fresh, total.descriptor).primary["value"].to_pylist() == [
        sum(expected.values())
    ]
    leaf = FixedLeaf(
        ArtifactRef(saved.artifact_ref),
        saved.descriptor.definition_fingerprint,
        fixed_signature(saved.descriptor),
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    fixed_root = method_node(
        (Edge("quantity", leaf),),
        OriginalReduce(singleton, method="sum_zero"),
        value_type=ScalarType("int64"),
    )
    total = runtime._execute_graph(
        fixed_root, (RouteChoice(fixed_root.identity, "artifact_python"),)
    )
    assert read_result(fresh, total.descriptor).primary["value"].to_pylist() == [
        sum(expected.values())
    ]


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("table", "parquet"))
@pytest.mark.parametrize("scenario", ("j3", "j3_weighting", "zero_denominator"))
def test_original_ratio_preserves_independent_root_components(
    analysis_dsl_case_factory: DslCaseFactory,
    tmp_path: Path,
    scenario: str,
    form: str,
) -> None:
    import marivo.analysis as mv
    from marivo.analysis.core.rules import OriginalReduce
    from marivo.analysis.materialization.graph_observation import observe_ratio_members
    from tests.shared_fixtures import analysis_dsl_rows

    case = analysis_dsl_case_factory(scenario)
    fresh = tmp_path / "ratio_graph"
    fresh.mkdir()
    shutil.copytree(case.root / "models", fresh / "models")
    shutil.copyfile(case.root / "marivo.toml", fresh / "marivo.toml")
    if form == "parquet":
        export_dsl_parquet_models(case, fresh)
    catalog = ms.load(workspace_dir=fresh)
    store = SessionStore._graph_store(fresh)
    session = store.create_session("original-ratio")
    runtime = DatasetRuntime(store, session.session_ref)
    members = construct_members(
        runtime,
        catalog._state.registry,
        ms.ref.entity(f"{case.names.domain}.{case.names.customer}"),
    )
    ratio = observe_ratio_members(
        members,
        ms.ref.metric(f"{case.names.domain}.{case.names.aov}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        paths=(
            (
                ms.ref.relationship(f"{case.names.domain}.{case.names.line_order}"),
                ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),
            ),
            (ms.ref.relationship(f"{case.names.domain}.{case.names.buyer}"),),
        ),
        sidecar=catalog._state.sidecar,
        report_timezone="UTC",
        coordinates=(
            ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}"),
        ),
    )
    saved = ratio.execute()
    result = read_result(fresh, saved.descriptor)
    facts = analysis_dsl_rows(scenario)
    owners = {row[0]: row[1] for row in facts.orders}
    sums = {
        key: sum(amount for _, order, amount in facts.lines if owners[order] == key)
        for key, _ in facts.customers
    }
    counts = {key: sum(owner == key for _, owner, *_ in facts.orders) for key, _ in facts.customers}
    channels = {row[0]: row[2] for row in facts.orders}
    coordinates = {(owner, channel) for _, owner, channel, *_ in facts.orders}
    expected_rows = {}
    for owner, channel in coordinates:
        numerator = sum(
            amount
            for _, order, amount in facts.lines
            if owners[order] == owner and channels[order] == channel
        )
        denominator = sum(
            1 for _, customer, group, *_ in facts.orders if customer == owner and group == channel
        )
        expected_rows[(owner, channel)] = numerator / denominator if denominator else None
    assert {
        (row["key_0"], row["key_1"]): row["value"] for row in result.primary.to_pylist()
    } == expected_rows
    singleton = DomainSignature(ratio.root.signature.domain.binding, "singleton", (), (), "total")
    root = method_node(
        (Edge("quantity", ratio.root),),
        OriginalReduce(
            singleton, "source.contribution_partition@v1", "source.complete_coverage@v1", "ratio"
        ),
        value_type=ScalarType("float64"),
    )
    total = replace(ratio, root=root).execute()
    actual = read_result(fresh, total.descriptor).primary["value"].to_pylist()
    assert actual == [sum(sums.values()) / sum(counts.values()) if sum(counts.values()) else None]

    from marivo.analysis.core.graph import FixedLeaf
    from marivo.analysis.materialization.graph_protocol import fixed_signature
    from marivo.analysis.methods.physical import FixedShape, NoTime
    from marivo.analysis.refs import ArtifactRef

    leaf = FixedLeaf(
        ArtifactRef(saved.artifact_ref),
        saved.descriptor.definition_fingerprint,
        fixed_signature(saved.descriptor),
        ScalarType("float64"),
        FixedShape(NoTime()),
    )
    root = method_node(
        (Edge("quantity", leaf),),
        OriginalReduce(singleton, method="ratio"),
        value_type=ScalarType("float64"),
    )
    fixed = runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    assert read_result(fresh, fixed.descriptor).primary["value"].to_pylist() == actual
    channel = ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{case.names.channel}")
    key = (
        Coordinate(ms.ref.entity(f"{case.names.domain}.{case.names.order}"), channel.path, "group"),
    )
    group_domain = DomainSignature(
        ratio.root.signature.domain.binding, "group", key, key, "ratio-channels"
    )
    group_root = method_node(
        (Edge("quantity", ratio.root),),
        OriginalReduce(
            group_domain,
            "source.contribution_partition@v1",
            "source.complete_coverage@v1",
            "ratio",
            group_domain.instance_key,
        ),
        value_type=ScalarType("float64"),
    )
    group_saved = replace(ratio, root=group_root).execute()
    channel_by_order = {order: channel for order, _, channel, *_ in facts.orders}
    labels = sorted({channel for channel in channel_by_order.values() if channel is not None})
    expected_groups = {
        label: sum(amount for _, order, amount in facts.lines if channel_by_order[order] == label)
        / sum(channel == label for channel in channel_by_order.values())
        for label in labels
    }
    assert {
        row["key_0"]: row["value"]
        for row in read_result(fresh, group_saved.descriptor).primary.to_pylist()
    } == expected_groups
    fixed_group = method_node(
        (Edge("quantity", leaf),),
        OriginalReduce(group_domain, method="ratio", coordinates=group_domain.instance_key),
        value_type=ScalarType("float64"),
    )
    group_saved = runtime._execute_graph(
        fixed_group, (RouteChoice(fixed_group.identity, "artifact_python"),)
    )
    assert {
        row["key_0"]: row["value"]
        for row in read_result(fresh, group_saved.descriptor).primary.to_pylist()
    } == expected_groups
    from marivo.analysis.materialization.graph_dataset import GraphDataset
    from marivo.analysis.materialization.graph_relation import Relation

    restored_relation = Relation.restore(GraphDataset(runtime, saved))
    projected = restored_relation.rollup(channel).execute()
    assert projected.to_pandas().set_index("group")["value"].to_dict() == expected_groups
    from marivo.analysis.methods.registry import REGISTRY

    assert "state_rollup.ratio@v1" in {
        str(item.method) for item in REGISTRY.continuations(fixed_signature(saved.descriptor))
    }
    if scenario == "j3_weighting":
        case.database_path.unlink()
        shutil.rmtree(fresh / "models")
        if form == "parquet":
            shutil.rmtree(fresh / "source_files")
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                """\
import json
import sys
import ibis
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
from marivo.analysis.core.model import Coordinate, CoordinateStatePart, DomainSignature
from marivo.analysis.core.rules import OriginalReduce
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_protocol import fixed_signature
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.refs import ArtifactRef

def forbidden(*args, **kwargs):
    raise AssertionError('cold original ratio cannot consult a current source or Semantic')

ibis.duckdb.connect = forbidden
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
store = SessionStore._graph_store(sys.argv[1], existing_only=True)
with store._read() as connection:
    record = graph_store.artifact(store, connection, sys.argv[2])
assert record is not None
signature = fixed_signature(record.descriptor)
leaf = FixedLeaf(ArtifactRef(record.artifact_ref), record.descriptor.definition_fingerprint,
                 signature, ScalarType('float64'), FixedShape(NoTime()))
domain = DomainSignature(signature.domain.binding, 'singleton', (), (), 'cold-ratio-total')
root = method_node((Edge('quantity', leaf),), OriginalReduce(domain, method='ratio'),
                   value_type=ScalarType('float64'))
runtime = DatasetRuntime(store, record.session_ref)
saved = runtime._execute_graph(root, (RouteChoice(root.identity, 'artifact_python'),))
assert read_result(store.project_root, saved.descriptor).primary['value'].to_pylist() == json.loads(sys.argv[3])
coordinate = next(part for part in signature.parts if isinstance(part, CoordinateStatePart))
key = (Coordinate(coordinate.owner, coordinate.dimension.path, 'group'),)
domain = DomainSignature(signature.domain.binding, 'group', key, key, 'cold-ratio-channels')
root = method_node((Edge('quantity', leaf),),
    OriginalReduce(domain, method='ratio', coordinates=domain.instance_key), value_type=ScalarType('float64'))
saved = runtime._execute_graph(root, (RouteChoice(root.identity, 'artifact_python'),))
assert {row['key_0']: row['value'] for row in read_result(store.project_root, saved.descriptor).primary.to_pylist()} == json.loads(sys.argv[4])
""",
                str(fresh),
                saved.artifact_ref,
                json.dumps(actual),
                json.dumps(expected_groups),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert process.returncode == 0, process.stderr


@pytest.mark.parametrize(
    "groups",
    (
        [{"coordinate": "web", "sum": 9, "non_null_count": 1}],
        [
            {"coordinate": "web", "sum": 5, "non_null_count": 1},
            {"coordinate": "web", "sum": 5, "non_null_count": 0},
        ],
        [{"coordinate": "web", "sum": 10.0, "non_null_count": 1}],
        [{"coordinate": "web", "sum": 10, "non_null_count": -1}],
        [{"coordinate": None, "sum": 10, "non_null_count": 1}],
    ),
)
def test_coordinate_partition_rejects_changed_components(groups: object) -> None:
    from marivo.analysis.methods.state_validation import coordinate_state_matches

    original = {"original_state__sum": 10, "original_state__non_null_count": 1}
    assert coordinate_state_matches(
        ("sum", "non_null_count"),
        "int64",
        [{"coordinate": "web", "sum": 10, "non_null_count": 1}],
        original,
    )
    assert not coordinate_state_matches(("sum", "non_null_count"), "int64", groups, original)
