"""Independent complete-identity, version and scalar-read acceptance vectors."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import ibis
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT


@pytest.fixture(params=("table", "parquet"))
def members_session(
    analysis_dsl_case_factory: DslCaseFactory, request: pytest.FixtureRequest
) -> Session:
    case = analysis_dsl_case_factory("j1")
    domain = case.names.domain
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "CREATE TABLE r52 (tenant BIGINT, id VARCHAR, day DATE, finish DATE, amount BIGINT, category VARCHAR, enabled BOOLEAN, moment TIMESTAMPTZ)"
        )
        db.execute(
            "INSERT INTO r52 VALUES (1, 'A', '2026-08-01', '2026-09-01', 10, 'west', true, '2026-08-01 01:02:03+00'), (1, 'B', '2026-08-01', NULL, 20, 'east', false, '2026-08-02 01:02:03+00'), (1, 'A', '2026-09-01', NULL, 30, 'east', false, '2026-09-01 01:02:03+00')"
        )
        db.execute("CREATE TABLE r52_plain AS SELECT * FROM r52 WHERE day = DATE '2026-08-01'")
    definitions = ["import marivo.datasource as md\nimport marivo.semantic as ms\n"]
    for name, table, version in (
        ("plain", "r52_plain", ""),
        (
            "snapshot",
            "r52",
            f", versioning=ms.snapshot(partition_field=ms.ref.time_dimension('{domain}.snapshot.day'), grain='day', timezone='UTC')",
        ),
        (
            "valid",
            "r52",
            f", versioning=ms.validity(valid_from=ms.ref.time_dimension('{domain}.valid.day'), valid_to=ms.ref.time_dimension('{domain}.valid.finish'), interval='closed_open', open_end=(None,))",
        ),
    ):
        definitions.append(
            f"{name} = ms.entity(name='{name}', datasource=ms.ref.datasource('warehouse'), source=md.table('{table}'), primary_key=['tenant', 'id']{version})\n"
        )
        for field in ("tenant", "id", "category", "enabled"):
            definitions.append(
                f"{name}_{field} = ms.dimension_column(name='{field}', entity={name}, column='{field}')\n"
            )
        for field in ("day", "finish"):
            definitions.append(
                f"{name}_{field} = ms.time_dimension_column(name='{field}', entity={name}, column='{field}', granularity='day')\n"
            )
        definitions.append(
            f"{name}_moment = ms.time_dimension_column(name='moment', entity={name}, column='moment', granularity='second', parse=ms.timestamp(timezone='UTC'))\n"
        )
        definitions.append(
            f"{name}_amount = ms.measure_column(name='amount', entity={name}, column='amount', additivity=ms.additive_all())\n"
        )
    definitions.append(
        "to_snapshot = ms.relationship(name='to_snapshot', from_entity=plain, to_entity=snapshot, keys=[ms.join_on(plain_tenant, snapshot_tenant), ms.join_on(plain_id, snapshot_id)])\n"
        "observed_total = ms.aggregate(name='observed_total', measure=plain_amount, agg='sum', time=plain_moment)\n"
    )
    definitions.append(
        "@ms.measure(name='discounted', entity=plain, additivity=ms.additive_all())\n"
        "def discounted(rows):\n"
        "    return rows.amount * 0.9\n"
        "@ms.measure(name='uplifted', entity=plain, additivity=ms.additive_all())\n"
        "def uplifted(rows):\n"
        "    return ms.bind(discounted, rows) + 5\n"
    )
    definitions.append(
        "@ms.dimension(name='positive', entity=plain)\n"
        "def positive(rows):\n"
        "    return rows.amount > 15\n"
        "many_snapshot = ms.relationship(name='many_snapshot', from_entity=plain, to_entity=snapshot, keys=[ms.join_on(plain_tenant, snapshot_tenant)])\n"
    )
    source = "".join(definitions)
    if request.param == "parquet":
        backend = ibis.duckdb.connect(case.database_path)
        try:
            for table in ("r52", "r52_plain"):
                path = case.root / f"{table}.parquet"
                pq.write_table(backend.table(table).to_pyarrow(), path)
                source = source.replace(f"md.table('{table}')", f"md.parquet({str(path)!r})")
        finally:
            backend.disconnect()
    (case.root / "models" / "semantic" / domain / "r52.py").write_text(source)
    return mv.session.get_or_create("r52-members", report_timezone="UTC")


def _domain(session: Session) -> str:
    return next(
        path.rsplit(".", 1)[0]
        for path in session.catalog._state.registry.entities
        if path.endswith(".plain")
    )


@pytest.mark.runtime
def test_complete_key_and_four_read_kinds(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    assert members.execute().to_pandas()["member"].tolist() == [1, 1]
    numeric = members.read(ms.ref.measure(f"{prefix}.plain.amount"))
    category = members.read(ms.ref.dimension(f"{prefix}.plain.category"))
    boolean = members.read(ms.ref.dimension(f"{prefix}.plain.enabled"))
    temporal = members.read(ms.ref.time_dimension(f"{prefix}.plain.moment"))
    assert isinstance(numeric, mv.LogicalNumericRelation)
    assert isinstance(category, mv.LogicalCategoryRelation)
    assert isinstance(boolean, mv.LogicalBooleanRelation)
    assert isinstance(temporal, mv.LogicalTemporalRelation)
    assert numeric.execute().to_pandas()["value"].tolist() == [10, 20]
    assert category.execute().to_pandas()["value"].tolist() == ["west", "east"]
    fixed = boolean.execute()
    assert fixed.to_pandas()["value"].tolist() == [True, False]
    assert fixed.where(fixed.value.eq(True)).members().execute().to_pandas()[
        "coord_0"
    ].tolist() == ["A"]
    temporal_fixed = temporal.execute()
    for relation in (temporal, temporal_fixed):
        with pytest.raises(AnalysisError, match="timezone-aware"):
            relation.value.eq(datetime(2026, 8, 1))
    assert temporal_fixed.to_pandas()["value"].tolist() == [
        datetime(2026, 8, 1, 1, 2, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
    ]
    assert isinstance(
        session.artifact(temporal_fixed.state.artifact_ref), mv.MaterializedTemporalRelation
    )
    assert numeric.where(numeric.value.gt(10)).members().execute().to_pandas()[
        "coord_0"
    ].tolist() == ["B"]


@pytest.mark.runtime
def test_member_axis_retains_ordered_composite_identity(members_session: Session) -> None:
    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    metric = ms.ref.metric(f"{prefix}.observed_total")
    category = ms.ref.dimension(f"{prefix}.plain.category")
    for axes, expected in (
        ((mv.member(), category), {(1, "A", "west"): 10, (1, "B", "east"): 20}),
        ((category, mv.member()), {("west", 1, "A"): 10, ("east", 1, "B"): 20}),
    ):
        observed = members.observe(metric, by=axes)
        fixed = observed.execute()
        rows = fixed.to_pandas()
        assert rows.set_index(list(rows.columns[:3]))["value"].to_dict() == expected
        assert fixed.members().execute().to_pandas().iloc[:, :2].values.tolist() == [
            [1, "A"],
            [1, "B"],
        ]
    primary_key_dimensions = members.observe(
        metric,
        by=(ms.ref.dimension(f"{prefix}.plain.tenant"), ms.ref.dimension(f"{prefix}.plain.id")),
    )
    with pytest.raises(AnalysisError):
        primary_key_dimensions.members()
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    rows = members.observe(metric, during=grid, by=(mv.member(),)).execute().to_pandas()
    assert len(rows) == 6
    assert rows["value"].dropna().tolist() == [10, 20]
    assert rows["cell_tag"].tolist().count("null") == 4


@pytest.mark.runtime
def test_observation_classification_reads_explicit_version(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    metric = ms.ref.metric(f"{prefix}.observed_total")
    field = ms.ref.dimension(f"{prefix}.snapshot.category")
    route = ms.ref.relationship(f"{prefix}.to_snapshot")
    category = members.read(field, at=datetime(2026, 8, 1, tzinfo=timezone.utc), via=route)
    assert isinstance(category, mv.LogicalCategoryRelation)
    selected_category = category.where(category.value.is_defined())
    assert type(selected_category) is mv.LogicalCategoryRelation
    assert (
        selected_category._node.classification_coordinate()
        == category._node.classification_coordinate()
    )
    observed = members.observe(metric, by=(selected_category,)).execute()
    assert observed.to_pandas().set_index("group")["value"].to_dict() == {"west": 10, "east": 20}
    own = members.read(ms.ref.dimension(f"{prefix}.plain.category"))
    assert isinstance(own, mv.LogicalCategoryRelation)
    selected = own.where(own.value.eq("west")).members()
    later = selected.read(field, at=datetime(2026, 9, 1, tzinfo=timezone.utc), via=route)
    assert isinstance(later, mv.LogicalCategoryRelation)
    assert selected.observe(metric, by=(later,)).execute().to_pandas().set_index("group")[
        "value"
    ].to_dict() == {"east": 10}


@pytest.mark.runtime
def test_computed_measure_and_bound_dependency(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    discounted = members.read(ms.ref.measure(f"{prefix}.plain.discounted"))
    uplifted = members.read(ms.ref.measure(f"{prefix}.plain.uplifted"))
    assert discounted.execute().to_pandas()["value"].tolist() == [9.0, 18.0]
    saved = uplifted.execute()
    assert saved.to_pandas()["value"].tolist() == [14.0, 23.0]
    assert saved.where(saved.value.gt(20)).members().execute().to_pandas()["coord_0"].tolist() == [
        "B"
    ]
    day = members.read(ms.ref.time_dimension(f"{prefix}.plain.day"))
    with pytest.raises(AnalysisError) as error:
        day.where(day.value.eq(datetime(2026, 8, 1, tzinfo=timezone.utc)))
    rendered = str(error.value)
    assert "datetime" in rendered and "Use a date literal" in rendered


@pytest.mark.runtime
def test_computed_boolean_dimension(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    members = session.members(ms.ref.entity(f"{_domain(session)}.plain"))
    relation = members.read(ms.ref.dimension(f"{_domain(session)}.plain.positive"))
    assert isinstance(relation, mv.LogicalBooleanRelation)
    saved = relation.execute()
    assert saved.to_pandas()["value"].tolist() == [False, True]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Fixed Boolean continuation read its source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    restored = session.artifact(saved.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedBooleanRelation)
    selected = restored.where(restored.value.eq(True)).execute()
    assert isinstance(selected, mv.MaterializedSelectedBooleanRelation)
    restored_selection = session.artifact(selected.state.artifact_ref)
    assert isinstance(restored_selection, mv.MaterializedSelectedBooleanRelation)
    assert restored_selection.to_pandas()["value"].tolist() == [True]
    assert saved.where(saved.value.eq(True)).members().execute().to_pandas()[
        "coord_0"
    ].tolist() == ["B"]
    assert session._runtime.store.resources(session._runtime.session_ref) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["snapshot", "valid"])
def test_exact_versions_and_independent_read(members_session: Session, kind: str) -> None:
    session = members_session
    entity = f"{_domain(session)}.{kind}"
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    members = session.members(ms.ref.entity(entity), at=august.before_end)
    assert members.execute().to_pandas()["coord_0"].tolist() == (
        ["A", "B"] if kind == "valid" else []
    )
    first = session.members(ms.ref.entity(entity), at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert first.execute().to_pandas()["coord_0"].tolist() == ["A", "B"]
    with pytest.raises(AnalysisError):
        first.read(ms.ref.measure(f"{entity}.amount"))
    read = first.read(
        ms.ref.measure(f"{entity}.amount"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    assert read.execute().to_pandas()["value"].tolist() == [10, 20]
    with pytest.raises(AnalysisError):
        session.members(ms.ref.entity(entity))
    with pytest.raises(AnalysisError):
        session.members(
            ms.ref.entity(f"{_domain(session)}.plain"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )


def _replace_source(session: Session, table: str, query: str) -> None:
    """Mutate only this isolated fixture and republish its Parquet input when present."""
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as db:
        db.execute(query)
        path = session.project_root / f"{table}.parquet"
        if path.exists():
            pq.write_table(db.table(table).to_arrow_table(), path)


@pytest.mark.runtime
def test_read_path_coverage_null_and_consumed_scope(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    category = members.read(ms.ref.dimension(f"{prefix}.plain.category"))
    assert isinstance(category, mv.LogicalCategoryRelation)
    chosen = category.where(category.value.eq("west")).members()
    route = ms.ref.relationship(f"{prefix}.to_snapshot")
    amount = ms.ref.measure(f"{prefix}.snapshot.amount")
    september = datetime(2026, 9, 1, tzinfo=timezone.utc)
    read = chosen.read(amount, at=september, via=route)
    assert read.execute().to_pandas()["value"].tolist() == [30]
    # Missing B is not a Null field and cannot be silently dropped.
    with pytest.raises(AnalysisError, match="mapping_total"):
        members.read(amount, at=september, via=route).execute()
    # Duplicate an unrelated owner identity; scoped A consumption stays valid.
    _replace_source(
        session,
        "r52",
        "INSERT INTO r52 VALUES (9, 'unrelated', '2026-09-01', NULL, 5, 'x', true, NULL), (9, 'unrelated', '2026-09-01', NULL, 6, 'x', true, NULL)",
    )
    assert read.execute().to_pandas()["value"].tolist() == [30]
    _replace_source(
        session, "r52", "UPDATE r52 SET amount = NULL WHERE id = 'A' AND day = DATE '2026-09-01'"
    )
    null = read.execute().to_pandas()
    assert null["cell_tag"].tolist() == ["null"]
    _replace_source(
        session,
        "r52",
        "INSERT INTO r52 SELECT * FROM r52 WHERE id = 'A' AND day = DATE '2026-09-01'",
    )
    duplicate = read.execute()
    assert duplicate.to_pandas()["cell_tag"].tolist() == ["null", "null"]
    with pytest.raises(AnalysisError, match="duplicate"):
        duplicate.rank(order="ascending", ties="ordinal").execute()


@pytest.mark.runtime
def test_declared_versions_are_trusted_and_actual_indexes_reject_conflicts(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    runs = len(session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction submitted a business read")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "compile", forbidden)
        patch.setattr(SourceSession, "batches", forbidden)
        members = session.members(
            ms.ref.entity(f"{prefix}.snapshot"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )
        read = members.read(
            ms.ref.measure(f"{prefix}.snapshot.amount"),
            at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        with pytest.raises(AnalysisError):
            members.read(ms.ref.measure(f"{prefix}.snapshot.amount"))
    assert len(session.runs().items) == runs
    _replace_source(
        session,
        "r52",
        "INSERT INTO r52 SELECT * FROM r52 WHERE id = 'A' AND day = DATE '2026-08-01'",
    )
    assert members.execute().to_pandas()["coord_0"].tolist() == ["A", "A", "B"]
    saved = read.execute()
    assert saved.to_pandas()["coord_0"].tolist().count("A") > 1
    with pytest.raises(AnalysisError, match="duplicate"):
        saved.rank(order="ascending", ties="ordinal").execute()


@pytest.mark.runtime
def test_temporal_literals_fixed_recovery_and_foreign_predicates(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    instant = members.read(ms.ref.time_dimension(f"{prefix}.plain.moment")).execute()
    dates = members.read(ms.ref.time_dimension(f"{prefix}.plain.day")).execute()
    assert dates.to_pandas()["value"].tolist() == [date(2026, 8, 1), date(2026, 8, 1)]
    boundary = datetime(2026, 8, 2, tzinfo=timezone.utc)
    selected = instant.where(instant.value.lt(boundary)).execute()
    restored = session.artifact(selected.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedSelectedTemporalRelation)
    assert restored.members().execute().to_pandas()["coord_0"].tolist() == ["A"]
    assert dates.where(dates.value.eq(date(2026, 8, 1))).execute().to_pandas()[
        "coord_0"
    ].tolist() == ["A", "B"]
    other = mv.session.get_or_create("other-r52", report_timezone="UTC")
    foreign = other.members(ms.ref.entity(f"{prefix}.plain")).read(
        ms.ref.time_dimension(f"{prefix}.plain.moment")
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed or foreign construction opened a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "bind", forbidden)
        with pytest.raises(AnalysisError):
            instant.where(foreign.value.lt(boundary))
        with pytest.raises(AnalysisError):
            restored.members().read(ms.ref.dimension(f"{prefix}.plain.category"))
        assert restored.members().execute().to_pandas()["coord_0"].tolist() == ["A"]


@pytest.mark.runtime
def test_cold_typed_read_continuation_without_source(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    fixed = (
        session.members(ms.ref.entity(f"{prefix}.plain"))
        .read(ms.ref.time_dimension(f"{prefix}.plain.moment"))
        .execute()
    )
    reference = fixed.state.artifact_ref.ref
    computed = (
        session.members(ms.ref.entity(f"{prefix}.plain"))
        .read(ms.ref.measure(f"{prefix}.plain.uplifted"))
        .execute()
    )
    computed_reference = computed.state.artifact_ref.ref
    shutil.rmtree(session.project_root / "models")
    (session.project_root / "warehouse.duckdb").unlink()
    for path in session.project_root.glob("*.parquet"):
        path.unlink()
    script = """
import sys
from datetime import datetime, timezone
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError('cold fixed continuation opened a source or model')
ms.load = forbidden
duckdb.connect = forbidden
SourceSession.bind = forbidden
session = mv.session.resume(sys.argv[1], by='id')
fixed = session.artifact(sys.argv[2])
assert isinstance(fixed, mv.MaterializedTemporalRelation)
selected = fixed.where(fixed.value.lt(datetime(2026, 8, 2, tzinfo=timezone.utc)))
result = selected.members().execute()
assert result.to_pandas()['coord_0'].tolist() == ['A']
again = selected.members().execute()
assert result.state.artifact_ref == again.state.artifact_ref
computed = session.artifact(sys.argv[3])
assert isinstance(computed, mv.MaterializedNumericRelation)
computed_members = computed.where(computed.value.gt(20)).members().execute()
assert computed_members.to_pandas()['coord_0'].tolist() == ['B']
assert computed_members.state.artifact_ref == computed.where(computed.value.gt(20)).members().execute().state.artifact_ref
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, reference, computed_reference],
        cwd=session.project_root,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT),
            "MARIVO_PROJECT_ROOT": str(session.project_root),
        },
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.runtime
def test_complete_subject_set_image_source_and_fixed(tmp_path: Path) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager

    import pyarrow as pa

    from marivo.analysis.compiler.graph_lowering import (
        ComponentColumn,
        CoordinateColumn,
        PartColumns,
        RelationLayout,
        SourceBinding,
    )
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
    from marivo.analysis.core.model import (
        Binding,
        Coordinate,
        DomainSignature,
        Signature,
        SubjectPart,
    )
    from marivo.analysis.core.rules import MapCorrespond, PartsTransport
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.contracts import canonical_json
    from marivo.analysis.materialization.execution_key import SourceKeyBinding
    from marivo.analysis.materialization.graph_dataset import GraphDataset
    from marivo.analysis.materialization.graph_protocol import digest
    from marivo.analysis.materialization.graph_relation import Relation
    from marivo.analysis.materialization.graph_storage import read_result
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
    from marivo.datasource.adapters import provider_for
    from marivo.datasource.ir import (
        AiContextIR,
        DatasourceIR,
        DatasourceSourceLocation,
        ParquetSourceIR,
    )

    store = SessionStore(tmp_path)
    runtime = DatasetRuntime(store, store.create_session("subject-image").session_ref)
    instance, subject_ref = ms.ref.entity("test.instance"), ms.ref.entity("test.subject")
    binding = Binding(runtime.session_ref, store.store_id, "image", "all")
    key = (Coordinate(instance, "iid", "identity"),)
    subject_key = (
        Coordinate(subject_ref, "tenant", "identity"),
        Coordinate(subject_ref, "sid", "identity"),
    )
    domain = DomainSignature(binding, "entity", key, key, "instance")
    subject = SubjectPart(binding, subject_ref, key, subject_key, False, True, "v1")
    leaf = SourceLeaf(
        SourceDefinition(
            instance,
            "subject-definition",
            ms.ref.datasource("db"),
            SourceShape("duckdb", "parquet", "parquet", NoTime()),
        ),
        Signature(domain, parts=(subject,)),
        ScalarType("int64"),
    )
    data = pa.table({"iid": [1, 2, 3], "tenant": [7, 7, 7], "sid": ["A", "A", "B"]})
    path = tmp_path / "instances.parquet"
    pq.write_table(data, path)
    source_ir = ParquetSourceIR(str(path))
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("db.py", 1)
    )

    @contextmanager
    def sources() -> Iterator[tuple[SourceSession, tuple[SourceBinding, ...]]]:
        with SourceSession(provider_for("duckdb"), datasource, ibis.duckdb.connect()) as source:
            bound = source.bind(source_ir, source_identity=leaf.identity)
            layout = RelationLayout(
                (CoordinateColumn(key[0], "iid"),),
                None,
                (
                    PartColumns(
                        subject,
                        (ComponentColumn("key_0", "tenant"), ComponentColumn("key_1", "sid")),
                    ),
                ),
            )
            yield source, (SourceBinding(leaf, bound, layout),)

    target = DomainSignature(binding, "entity", subject_key, subject_key, "subjects")
    image = method_node(
        (Edge("subject", leaf),),
        MapCorrespond("subjects", target, "source.unique_key@v1"),
        value_type=ScalarType("int64"),
    )
    view = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", domain, ("subject",), False),
        value_type=ScalarType("int64"),
    )
    with sources() as (_, inputs):
        schema = inputs[0].source.facts.schema
    source_key = SourceKeyBinding(
        leaf,
        leaf.definition.shape,
        leaf.definition.fingerprint,
        digest(canonical_json(source_ir.to_dict())),
    )
    saved_image = runtime._execute_graph(
        image,
        (RouteChoice(image.identity, "ibis"),),
        source_bindings=(source_key,),
        source_factory=sources,
        source_schemas=(schema,),
    )
    assert cell_rows(read_result(tmp_path, saved_image.descriptor).primary) == [
        {"key_0": 7, "key_1": "A"},
        {"key_0": 7, "key_1": "B"},
    ]
    saved_view = runtime._execute_graph(
        view,
        (RouteChoice(view.identity, "ibis"),),
        source_bindings=(source_key,),
        source_factory=sources,
        source_schemas=(schema,),
    )
    path.unlink()
    fixed = Relation.restore(GraphDataset(runtime, saved_view)).selected_members().execute()
    assert fixed.to_pandas()["coord_0"].tolist() == ["A", "B"]


@pytest.mark.runtime
def test_exact_boundary_null_and_corrupt_subject(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    entity = ms.ref.entity(f"{prefix}.valid")
    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    instant = session.members(entity, at=at).read(ms.ref.measure(f"{prefix}.valid.amount"), at=at)
    assert instant.execute().to_pandas()["value"].tolist() == [30, 20]
    before = mv.time_scope(start="2026-08-01", end="2026-09-01").before_end
    left = session.members(entity, at=before).read(
        ms.ref.measure(f"{prefix}.valid.amount"), at=before
    )
    assert left.execute().to_pandas()["value"].tolist() == [10, 20]
    noon = mv.time_scope(
        start="2026-09-01T00:00:00+00:00", end="2026-09-01T12:00:00+00:00"
    ).before_end
    assert session.members(entity, at=noon).read(
        ms.ref.measure(f"{prefix}.valid.amount"), at=noon
    ).execute().to_pandas()["value"].tolist() == [30, 20]
    snapshot = ms.ref.entity(f"{prefix}.snapshot")
    end_of_first = mv.time_scope(start="2026-08-01", end="2026-08-02").before_end
    assert session.members(snapshot, at=end_of_first).execute().to_pandas()["coord_0"].tolist() == [
        "A",
        "B",
    ]
    assert (
        session.members(snapshot, at=datetime(2026, 8, 2, tzinfo=timezone.utc))
        .execute()
        .to_pandas()
        .empty
    )
    _replace_source(
        session,
        "r52",
        "UPDATE r52 SET finish = DATE '2026-09-02' WHERE id = 'A' AND day = DATE '2026-08-01'",
    )
    # Overlapping versions violate the declaration; analysis does not re-audit it.
    assert session.members(entity, at=at).execute().to_pandas()["coord_0"].tolist() == [
        "A",
        "A",
        "B",
    ]
    with pytest.raises(AnalysisError, match="aware"):
        session.members(entity, at=datetime(2026, 9, 1))
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    integer = members.read(ms.ref.dimension(f"{prefix}.plain.tenant"))
    assert isinstance(integer, mv.LogicalCategoryRelation)
    saved = integer.execute()
    assert saved.where(saved.value.eq(1)).members().execute().to_pandas()["coord_0"].tolist() == [
        "A",
        "B",
    ]
    assert saved._dataset is not None
    part = saved._dataset.artifact.descriptor.parts[0].local
    directory = session.project_root / part.project_relative_path
    payload = directory / part.file_manifest[0].relative_path
    payload.write_bytes(b"corrupt subject")
    with pytest.raises(AnalysisError):
        saved.members().execute()


@pytest.mark.runtime
def test_root_projection_reads_one_source_without_distinct(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.operations as ops
    import ibis.expr.types as ir

    from marivo.datasource.adapters import CompiledRead, SourceBatchStream

    session = members_session
    members = session.members(ms.ref.entity(f"{_domain(session)}.plain"))
    original = SourceSession.batches
    primary: list[ir.Expr] = []
    purposes: list[str] = []

    def read(
        self: SourceSession,
        issued: CompiledRead,
        *,
        chunk_size: int,
    ) -> SourceBatchStream:
        purposes.append(issued.purpose)
        if issued.purpose == "analysis.graph.stage":
            primary.append(issued.expression)
        return original(self, issued, chunk_size=chunk_size)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Root projection captured or restaged an intermediate exchange")

    monkeypatch.setattr(SourceSession, "batches", read)
    monkeypatch.setattr(SourceSession, "stage_derived", forbidden)
    monkeypatch.setattr(SourceSession, "stage_calculated", forbidden)
    members.execute()
    # The declared identity needs no independent business audit.
    assert purposes == ["analysis.graph.stage"]
    assert len(primary) == 1
    assert len(primary[0].op().find(ops.DatabaseTable)) == 1
    assert not primary[0].op().find(ops.Distinct)
    assert not primary[0].op().find(ops.Aggregate)


@pytest.mark.runtime
def test_wrong_role_many_mapping_and_missing_coverage_reject_before_io(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    versioned = session.members(
        ms.ref.entity(f"{prefix}.snapshot"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    field = ms.ref.measure(f"{prefix}.snapshot.amount")
    at = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid path requested a schema or business source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "bind", forbidden)
        with pytest.raises(AnalysisError, match="single-valued"):
            members.read(field, at=at, via=ms.ref.relationship(f"{prefix}.many_snapshot"))
        with pytest.raises(AnalysisError, match="directed"):
            versioned.read(field, at=at, via=ms.ref.relationship(f"{prefix}.to_snapshot"))
        with pytest.raises(AnalysisError, match="path"):
            members.read(field, at=at)
    # One explicit route and one Relationship Ref have the same scalar meaning.
    path = mv.routes(
        mv.route(
            ms.ref.entity(f"{prefix}.plain"),
            through=(ms.ref.relationship(f"{prefix}.to_snapshot"),),
        )
    )
    assert members.read(field, at=at, via=path).execute().to_pandas()["value"].tolist() == [10, 20]


@pytest.mark.runtime
def test_grid_attribute_point_is_independent_of_member_version(members_session: Session) -> None:
    prefix = _domain(members_session)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    members = members_session.members(
        ms.ref.entity(f"{prefix}.valid"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    field = ms.ref.measure(f"{prefix}.valid.amount")
    for endpoint, expected in (
        (grid.start, [10, 30, 20, 20]),
        (grid.end, [30, 30, 20, 20]),
        (grid.before_end, [10, 30, 20, 20]),
    ):
        result = members.read(field, at=endpoint).execute()
        assert result.to_pandas().value.tolist() == expected
        assert (
            members_session.artifact(result.state.artifact_ref).to_pandas().value.tolist()
            == expected
        )


@pytest.mark.runtime
def test_grid_snapshot_before_end_uses_symbolic_left_period(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-02"), grain=mv.grain("day")
    )
    product = session.members(ms.ref.entity(f"{prefix}.plain"))
    field = ms.ref.measure(f"{prefix}.snapshot.amount")
    route = ms.ref.relationship(f"{prefix}.to_snapshot")
    for point in (grid.start, grid.before_end):
        assert product.read(field, at=point, via=route).execute().to_pandas().value.tolist() == [
            10,
            20,
        ]
    with pytest.raises(AnalysisError):
        product.read(field, at=grid.end, via=route).execute()


@pytest.mark.runtime
def test_direct_grid_endpoint_keeps_four_attribute_families(members_session: Session) -> None:
    prefix = _domain(members_session)
    members = members_session.members(
        ms.ref.entity(f"{prefix}.valid"), at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    category = members.read(ms.ref.dimension(f"{prefix}.valid.category"), at=grid.before_end)
    boolean = members.read(ms.ref.dimension(f"{prefix}.valid.enabled"), at=grid.before_end)
    temporal = members.read(ms.ref.time_dimension(f"{prefix}.valid.moment"), at=grid.before_end)
    assert isinstance(category, mv.LogicalCategoryRelation)
    assert isinstance(boolean, mv.LogicalBooleanRelation)
    assert isinstance(temporal, mv.LogicalTemporalRelation)
    for relation, expected in (
        (category, ["west", "east", "east", "east"]),
        (boolean, [True, False, False, False]),
        (
            temporal,
            [
                datetime(2026, 8, 1, 1, 2, 3, tzinfo=timezone.utc),
                datetime(2026, 9, 1, 1, 2, 3, tzinfo=timezone.utc),
                datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
                datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
            ],
        ),
    ):
        result = relation.execute()
        frame = result.to_pandas()
        assert frame.value.tolist() == expected
        assert frame.groupby(["member", "coord_0"]).size().tolist() == [2, 2]
        recovered = members_session.artifact(result.state.artifact_ref)
        assert isinstance(recovered, type(result))
        assert recovered.to_pandas().equals(frame)
    stable_members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    stable = stable_members.read(ms.ref.dimension(f"{prefix}.plain.category"), at=grid.before_end)
    assert isinstance(stable, mv.LogicalCategoryRelation)
    stable_frame = stable.execute().to_pandas()
    assert stable_frame.value.tolist() == ["west", "west", "east", "east"]
    assert stable_frame.groupby(["member", "coord_0"]).size().tolist() == [2, 2]
    with pytest.raises(AnalysisError):
        stable_members.read(
            ms.ref.dimension(f"{prefix}.plain.category"),
            at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )


@pytest.mark.runtime
def test_grid_classification_requires_explicit_exact_observation_grid(
    members_session: Session,
) -> None:
    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    scope = mv.time_scope(start="2026-08-01T00:00:00+00:00", end="2026-08-01T02:00:00+00:00")
    grid = mv.time_grid(during=scope, grain=mv.grain("hour"))
    other = mv.time_grid(
        during=mv.time_scope(start="2026-08-01T00:00:00+00:00", end="2026-08-01T03:00:00+00:00"),
        grain=mv.grain("hour"),
    )
    category = members.read(
        ms.ref.dimension(f"{prefix}.snapshot.category"),
        at=grid.before_end,
        via=ms.ref.relationship(f"{prefix}.to_snapshot"),
    )
    assert isinstance(category, mv.LogicalCategoryRelation)
    metric = ms.ref.metric(f"{prefix}.observed_total")
    for during in (None, scope, other):
        before = len(members_session.runs().items)
        with pytest.raises(AnalysisError, match=r"classification.*grid|classification grid"):
            members.observe(metric, during=during, by=(category,))
        assert len(members_session.runs().items) == before
    foreign_session = mv.session.get_or_create("foreign-grid-category", report_timezone="UTC")
    foreign_category = foreign_session.members(ms.ref.entity(f"{prefix}.plain")).read(
        ms.ref.dimension(f"{prefix}.snapshot.category"),
        at=grid.before_end,
        via=ms.ref.relationship(f"{prefix}.to_snapshot"),
    )
    with pytest.raises(AnalysisError):
        members.observe(metric, during=grid, by=(foreign_category,))
    assert foreign_session.runs().items == ()
    observed = members.observe(metric, during=grid, by=(category,))
    frame = observed.execute().to_pandas()
    assert len(frame) == 4
    assert frame.value.dropna().tolist() == [10]
    assert set(frame.group) == {"west", "east"}
    incomplete = category.where(category.value.eq("west"))
    with pytest.raises(AnalysisError, match="mapping_total"):
        members.observe(metric, during=grid, by=(incomplete,)).execute()


@pytest.mark.runtime
def test_direct_grid_binding_is_pure_and_rejects_conflicting_authority(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-02"), grain=mv.grain("day")
    )
    before = len(members_session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction read business rows")

    with monkeypatch.context() as pure:
        pure.setattr(SourceSession, "batches", forbidden)
        members.read(
            ms.ref.measure(f"{prefix}.snapshot.amount"),
            at=grid.before_end,
            via=ms.ref.relationship(f"{prefix}.to_snapshot"),
        )
        observed = members.observe(ms.ref.metric(f"{prefix}.observed_total"), during=grid)
        observed.contract().show()
        with pytest.raises(AnalysisError, match="both during and at"):
            members.observe(ms.ref.metric(f"{prefix}.observed_total"), during=grid, at=grid.end)
        for during in (None, mv.time_scope(start="2026-08-01", end="2026-08-02")):
            with pytest.raises(AnalysisError) as incomplete:
                members.observe(
                    ms.ref.metric(f"{prefix}.observed_total"), during=during, complete_during=()
                )
            assert incomplete.value.expected == (
                "a single original sum on during=grid without dimension grouping or at"
            )
    assert len(members_session.runs().items) == before
    other_session = mv.session.get_or_create("grid-conflict", report_timezone="Asia/Shanghai")
    other_members = other_session.members(ms.ref.entity(f"{prefix}.plain"))
    with pytest.raises(AnalysisError, match="conflicting authority"):
        other_members.observe(ms.ref.metric(f"{prefix}.observed_total"), during=grid)


@pytest.mark.runtime
def test_composite_subject_predicates(members_session: Session) -> None:
    prefix = _domain(members_session)
    targets = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    numeric = targets.read(ms.ref.measure(f"{prefix}.plain.amount"))
    boolean = targets.read(ms.ref.dimension(f"{prefix}.plain.enabled"))
    temporal = targets.read(ms.ref.time_dimension(f"{prefix}.plain.moment"))
    for values, enabled, moments in (
        (numeric, boolean, temporal),
        (numeric.execute(), boolean.execute(), temporal.execute()),
    ):
        predicate = mv.all_of(
            enabled.value.eq(True), moments.value.lte(moments.value), values.value.gt(0)
        )
        result = values.where(predicate).members(through=values.subject_binding).execute()
        frame = result.to_pandas()
        assert list(zip(frame.member, frame.coord_0, strict=True)) == [(1, "A")]


@pytest.mark.runtime
@pytest.mark.parametrize("reordered", ["primary", "subject"])
def test_cohort_matches_subject_parts_by_complete_key(
    members_session: Session, reordered: str
) -> None:
    from marivo.analysis.compiler.graph_lowering import LoweredLocal, canonical_layout
    from marivo.analysis.compiler.graph_plan import LocalMethodStage
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
    from marivo.analysis.materialization.graph_local_execution import _cohort_stage
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.semantics import MethodKey

    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    category = members.read(ms.ref.dimension(f"{prefix}.plain.category"))
    fixed_targets, fixed_values = members.execute(), category.execute()
    target = fixed_targets._dataset.verified()
    primary = target.primary.take([1, 0]) if reordered == "primary" else target.primary
    parts = tuple(
        ExchangePart(part.role, part.table.take([1, 0]))
        if reordered == "subject" and part.role == "subject"
        else part
        for part in target.parts
    )
    supplied = from_arrow(primary, target.contract, parts=parts, method_state=target.method_state)
    node = fixed_targets.cohort(fixed_values.value.eq("east"), rule=mv.any_instance())._node.root
    implementation = next(
        item
        for item in implementations(MethodKey("domain.cohort"))
        if item.key.route == "artifact_python"
    )
    method = LoweredLocal(
        LocalMethodStage("cohort", (), node, implementation),
        (),
        canonical_layout(node.signature, has_value=False),
    )
    result = _cohort_stage(method, supplied, (fixed_values._dataset.verified(),), "controlled")
    assert cell_rows(result.primary) == [{"key_0": 1, "key_1": "B"}]
    subject = next(part.table for part in result.parts if part.role == "subject")
    assert subject.to_pylist() == [
        {"key_0": 1, "key_1": "B", "subject__key_0": 1, "subject__key_1": "B"}
    ]


@pytest.mark.runtime
def test_selected_where_is_disclosed_and_resolvable(members_session: Session) -> None:
    from marivo._help.model import NativeHelpRoute
    from marivo._help.route import route_help_target

    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    numeric = members.read(ms.ref.measure(f"{prefix}.plain.amount"))
    for relation in (
        numeric,
        members.read(ms.ref.dimension(f"{prefix}.plain.category")),
        members.read(ms.ref.dimension(f"{prefix}.plain.enabled")),
        members.read(ms.ref.time_dimension(f"{prefix}.plain.moment")),
    ):
        selected = relation.where(relation.value.is_defined())
        for current in (selected, selected.execute()):
            action = next(
                action
                for action in current.contract().actions
                if action.call == "relation.where(predicate)"
            )
            assert isinstance(route_help_target(action.help_target), NativeHelpRoute)
            result = current.where(current.value.is_defined()).execute()
            assert len(result.to_pandas()) == 2
