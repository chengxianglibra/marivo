"""schema admission does not turn physical metadata into business evidence."""

from __future__ import annotations

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
from marivo.analysis.materialization.graph_exchange import ExchangeResult, _pandas_records
from marivo.analysis.materialization.graph_execution import prepare_graph
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
from tests.shared_fixtures import DslCaseFactory


def _records(exchange: ExchangeResult) -> list[dict[str, object]]:
    return _pandas_records(exchange.primary, exchange.contract.schema)


def test_preflight_reads_only_source_schema_and_preserves_exact_key_type(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    entity = f"{case.names.domain}.{case.names.customer}"
    registry = case.catalog._state.registry

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema admission must not issue a business read")

    monkeypatch.setattr(SourceSession, "compile", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    runs_before = len(case.session.runs().items)
    selected = preflight_entities(registry, case.root, (entity,))

    assert len(selected) == 1
    assert selected[0].identity_type.name == "string"
    assert str(selected[0].schema.field(case.names.customer_id).type) == "string"
    assert len(case.session.runs().items) == runs_before


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
            "warehouse": replace(original, backend_type="unsupported"),
        },
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsupported backend must reject before source open")

    monkeypatch.setattr(DatasourceConnectionService, "use_backend", forbidden)
    with pytest.raises(DatasetConstructionError, match="qualified graph source provider"):
        preflight_entities(registry, case.root, (f"{case.names.domain}.{case.names.customer}",))


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
    service = DatasourceConnectionService(case.root)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(first.contract.source, source_identity=entity)
        with pytest.raises(DatasetConstructionError, match="changed physical binding"):
            first.verify(bound)


def test_string_member_view_uses_exact_graph_and_source_read(
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
    service = DatasourceConnectionService(case.root)
    with (
        service.use_backend(datasource.name, read_only=True) as backend,
        SourceSession(provider_for("duckdb"), datasource, backend, owns_backend=False) as source,
    ):
        bound = source.bind(selected.contract.source, source_identity=leaf.identity)
        selected.verify(bound)
        layout = RelationLayout((CoordinateColumn(coordinate, case.names.customer_id),), None)
        lowered = lower(prepared.admitted, bindings=(SourceBinding(leaf, bound, layout),))
        result = execute_source_graph(prepared, lowered, source)
    assert sorted(result.primary["key_0"].tolist()) == ["A", "B", "C", "D"]


def test_string_dimension_binding_uses_exact_graph_and_source_read(
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
    service = DatasourceConnectionService(case.root)
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
            result.primary["key_0"].tolist(),
            result.primary["value"].tolist(),
            strict=True,
        )
    ) == {"A": "east", "B": "east"}


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
    service = DatasourceConnectionService(case.root)
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
    assert sorted(result.primary["key_0"].tolist()) == ["east", "south", "west"]


@pytest.mark.slow
@pytest.mark.parametrize("form", ("table", "parquet"))
def test_v7_member_subject_part_survives_publication_and_exact_read(
    tmp_path: Path, form: Literal["table", "parquet"]
) -> None:
    store = SessionStore(tmp_path)
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
    assert sorted(restored.primary["key_0"].tolist()) == ["A", "B"]
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
    assert sorted(read_result(tmp_path, mapped_saved.descriptor).primary["key_0"].tolist()) == [
        "A",
        "B",
    ]
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
    assert selected_result.primary["key_0"].tolist() == ["A"]
    assert selected_result.primary["value"].tolist() == ["east"]
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

store = SessionStore(sys.argv[1], existing_only=True)
with store._read() as conn:
    record = graph_store.artifact(store, conn, sys.argv[2])
assert record is not None
result = read_result(store.project_root, record.descriptor)
    assert result.primary['key_0'].tolist() == ['A']
    assert result.primary['value'].tolist() == ['east']
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
