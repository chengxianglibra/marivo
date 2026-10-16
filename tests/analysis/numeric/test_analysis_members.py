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
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT


@pytest.fixture
def members_session(analysis_dsl_case_factory: DslCaseFactory) -> Session:
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
    (case.root / "models" / "semantic" / domain / "r52.py").write_text(source)
    return mv.session.get_or_create("r52-members", report_timezone="UTC")


def _domain(session: Session) -> str:
    return next(
        path.rsplit(".", 1)[0]
        for path in session.catalog._state.registry.entities
        if path.endswith(".plain")
    )


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

    monkeypatch.setattr(SourceSession, "execute", forbidden)
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


def _replace_source(session: Session, table: str, query: str) -> None:
    """Mutate only this isolated fixture and republish its Parquet input when present."""
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as db:
        db.execute(query)
        path = session.project_root / f"{table}.parquet"
        if path.exists():
            pq.write_table(db.table(table).to_arrow_table(), path)


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
    with pytest.raises(MaterializationError) as missing:
        members.read(amount, at=september, via=route).execute()
    assert missing.value.expected == (
        f"an owner operand for {prefix}.snapshot.amount on every consumed complete key"
    )
    assert (
        missing.value.received
        == "required field owner operand is missing for 1 consumed complete keys"
    )
    assert missing.value.repair is not None
    assert missing.value.run_ref is not None
    assert session.get_run(missing.value.run_ref).lifecycle == "failed"


def test_temporal_literals_fixed_recovery_and_foreign_predicates(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    logical_instant = members.read(ms.ref.time_dimension(f"{prefix}.plain.moment"))
    with pytest.raises(AnalysisError, match="timezone-aware"):
        logical_instant.value.eq(datetime(2026, 8, 1))
    instant = logical_instant.execute()
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


def test_cold_typed_read_continuation_without_source(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    fixed = (
        session.members(ms.ref.entity(f"{prefix}.plain"))
        .read(ms.ref.time_dimension(f"{prefix}.plain.moment"))
        .execute()
    )
    reference = fixed.state.artifact_ref.ref
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
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, reference],
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
    assert read_result(tmp_path, saved_image.descriptor).primary.to_dict(orient="records") == [
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


def test_wrong_role_and_many_mapping_reject_before_io(
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
        with pytest.raises(AnalysisError) as many:
            members.read(field, at=at, via=ms.ref.relationship(f"{prefix}.many_snapshot"))
        assert many.value.expected == "a contiguous directed to-one path"
        assert many.value.received == f"{prefix}.many_snapshot"
        assert many.value.repair is not None
        with pytest.raises(AnalysisError, match="directed"):
            versioned.read(field, at=at, via=ms.ref.relationship(f"{prefix}.to_snapshot"))
    # Omission infers the one legal route; explicit paths preserve its meaning.
    path = mv.path(ms.ref.relationship(f"{prefix}.to_snapshot"))
    assert members.read(field, at=at).execute().to_pandas()["value"].tolist() == [10, 20]
    assert members.read(field, at=at, via=path).execute().to_pandas()["value"].tolist() == [10, 20]


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
    session = members_session
    prefix = _domain(session)
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

    daily_grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-02"), grain=mv.grain("day")
    )
    product = session.members(ms.ref.entity(f"{prefix}.plain"))
    field = ms.ref.measure(f"{prefix}.snapshot.amount")
    route = ms.ref.relationship(f"{prefix}.to_snapshot")
    for point in (daily_grid.start, daily_grid.before_end):
        assert product.read(field, at=point, via=route).execute().to_pandas().value.tolist() == [
            10,
            20,
        ]
    with pytest.raises(AnalysisError):
        product.read(field, at=daily_grid.end, via=route).execute()


def test_grid_classification_uses_grid_point(members_session: Session) -> None:
    prefix = _domain(members_session)
    members = members_session.members(ms.ref.entity(f"{prefix}.plain"))
    scope = mv.time_scope(start="2026-08-01T00:00:00+00:00", end="2026-08-01T02:00:00+00:00")
    grid = mv.time_grid(during=scope, grain=mv.grain("hour"))
    category = members.read(
        ms.ref.dimension(f"{prefix}.snapshot.category"),
        at=grid.before_end,
        via=ms.ref.relationship(f"{prefix}.to_snapshot"),
    )
    assert isinstance(category, mv.LogicalCategoryRelation)
    metric = ms.ref.metric(f"{prefix}.observed_total")
    observed = members.observe(metric, during=grid, by=(category,))
    frame = observed.execute().to_pandas()
    assert len(frame) == 4
    assert frame.value.dropna().tolist() == [10]
    assert set(frame.group) == {"west", "east"}


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
