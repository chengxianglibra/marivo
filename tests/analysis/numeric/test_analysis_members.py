"""Independent complete-identity, version and scalar-read acceptance vectors."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.expressions import Entity
from tests.analysis.statistics.pandas_workflows import receipt_paths
from tests.shared_fixtures import DslCaseFactory, terminal_runs
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
            f", versioning=ms.snapshot(partition_field=ms.ref.time_dimension('{domain}.snapshot.day'), timezone='UTC')",
        ),
        (
            "valid",
            "r52",
            f", versioning=ms.validity(valid_from=ms.ref.time_dimension('{domain}.valid.day'), valid_to=ms.ref.time_dimension('{domain}.valid.finish'), interval='closed_open', open_end=(None,), timezone='UTC')",
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
                f"{name}_{field} = ms.time_dimension_column(name='{field}', entity={name}, column='{field}')\n"
            )
        definitions.append(
            f"{name}_moment = ms.time_dimension_column(name='moment', entity={name}, column='moment', parse=ms.timestamp())\n"
        )
        definitions.append(
            f"{name}_amount = ms.measure_column(name='amount', entity={name}, column='amount', additivity=ms.additive_all())\n"
        )
    definitions.append(
        "to_snapshot = ms.relationship(name='to_snapshot', from_entity=plain, to_entity=snapshot, keys=[ms.join_on(plain_tenant, snapshot_tenant), ms.join_on(plain_id, snapshot_id)])\n"
        "many_snapshot = ms.relationship(name='many_snapshot', from_entity=plain, to_entity=snapshot, keys=[ms.join_on(plain_tenant, snapshot_tenant)])\n"
        "@ms.dimension(name='positive', entity=plain)\n"
        "def positive(rows):\n"
        "    return rows.amount > 15\n"
        ""
        "plain_amount_value = plain_amount\n"
        "plain_moment_value = plain_moment\n"
        "@ms.metric()\n"
        "def observed_total():\n"
        "    return plain_amount_value.sum(time=plain_moment_value)\n"
        "@ms.measure(additivity=ms.additive_all())\n"
        "def discounted():\n"
        "    return plain_amount_value * 0.9\n"
        "@ms.measure(additivity=ms.additive_all())\n"
        "def uplifted():\n"
        "    return discounted + 5\n"
    )
    source = "".join(definitions)
    (case.root / "models" / "semantic" / domain / "models.py").write_text(source)
    return mv.session.get_or_create("r52-members", timezone="UTC")


def _domain(session: Session) -> str:
    return next(
        path.rsplit(".", 1)[0]
        for path in session.catalog._state.registry.entities
        if path.endswith(".plain")
    )


def _read(
    session: Session,
    population: mv.Population,
    field: str,
    *,
    at: datetime | None = None,
    via: str | None = None,
) -> mv.AnalysisFrame:
    value = session.catalog.fields.get(field)
    if via is not None:
        value = value.via((session.catalog.relationships.get(via),))
    if at is not None:
        value = value.at(session.instant(at))
    return session.read(population, {"value": value})


def _values(result: mv.AnalysisFrame) -> list[object]:
    return result.to_pandas()[("value", "value")].tolist()


def _identity_population(
    session: Session, frame: mv.AnalysisFrame, entity: Entity
) -> mv.Population:
    axes = tuple(axis for axis in frame.axes if axis.role == "identity")
    assert len(axes) == 1
    return session.population_from(frame, on=((axes[0], entity),))


def test_member_axis_retains_ordered_composite_identity(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    category = session.catalog.fields.get(f"{prefix}.plain.category")
    for axes, expected in (
        ((members.axes[0], category), {((1, "A"), "west"): 10, ((1, "B"), "east"): 20}),
        ((category, members.axes[0]), {("west", (1, "A")): 10, ("east", (1, "B")): 20}),
    ):
        observed = session.observe({"value": metric}, by=axes)
        fixed = observed.compute()
        rows = fixed.to_pandas()
        assert rows.set_index(list(rows.columns[:2]))[("value", "value")].to_dict() == expected
        selected = _identity_population(
            session, fixed, session.catalog.entities.get(f"{prefix}.plain")
        )
        identity_position = next(i for i, axis in enumerate(fixed.axes) if axis.role == "identity")
        expected_sequence = list(dict.fromkeys(rows.iloc[:, identity_position]))
        actual_sequence = session.frame(selected).compute().to_pandas().iloc[:, 0].tolist()
        assert actual_sequence == expected_sequence
        assert set(actual_sequence) == {(1, "A"), (1, "B")}
    # Separate scalar primary-key fields retain their Field roles.
    fields = (
        session.catalog.fields.get(f"{prefix}.plain.tenant"),
        session.catalog.fields.get(f"{prefix}.plain.id"),
    )
    grouped = session.observe({"value": metric}, by=fields)
    assert all(
        axis.kind == "field"
        for axis in session.population_from(
            grouped, on=tuple(zip(grouped.axes, fields, strict=True))
        ).axes
    )
    grid = session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day"))
    rows = session.observe({"value": metric}, by=mv.product(members, grid)).compute().to_pandas()
    assert len(rows) == 6
    assert rows[("value", "value")].to_dict() == {0: 10, 1: 0, 2: 0, 3: 0, 4: 20, 5: 0}


def test_observation_classification_reads_explicit_version(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    route = session.catalog.relationships.get(f"{prefix}.to_snapshot")
    field = session.catalog.fields.get(f"{prefix}.snapshot.category")
    category = field.via((route,)).at(session.instant("2026-08-01"))
    observed = session.observe({"value": metric}, by=(category,)).compute().to_pandas()
    assert observed.set_index(observed.columns[0])[("value", "value")].to_dict() == {
        "west": 10,
        "east": 20,
    }
    own = _read(session, members, f"{prefix}.plain.category")
    selected_values = own.where(own["value"] == "west")
    selected = _identity_population(
        session, selected_values, session.catalog.entities.get(f"{prefix}.plain")
    )
    later = field.via((route,)).at(session.instant("2026-09-01"))
    read = session.read(selected, {"value": later})
    assert _values(read.compute()) == ["east"]
    frame = (
        session.observe({"value": metric}, by=(later,), where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert frame.set_index(frame.columns[0])[("value", "value")].to_dict() == {"east": 10}


def test_mixed_predicate_uses_nullable_missing_scalar_matches(
    members_session: Session,
) -> None:
    session = members_session
    prefix = _domain(session)
    entity = session.catalog.entities.get(f"{prefix}.plain")
    category = session.catalog.fields.get(f"{prefix}.plain.category")
    selected = session.members(entity, where=category == "west")
    route = session.catalog.relationships.get(f"{prefix}.to_snapshot")
    later = (
        session.catalog.fields.get(f"{prefix}.snapshot.category")
        .via((route,))
        .at(session.instant("2026-09-01"))
    )
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    for condition in (
        mv.in_population(selected) & (later == "east"),
        (later == "east") | mv.in_population(selected),
    ):
        rows = session.observe({"value": metric}, where=condition).compute().to_pandas()
        assert rows[("value", "value")].tolist() == [10]


def test_computed_measure_and_bound_dependency(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    discounted = session.observe(
        {"value": session.catalog.fields.get(f"{prefix}.plain.discounted").sum()}, by=members
    )
    uplifted = session.observe(
        {"value": session.catalog.fields.get(f"{prefix}.plain.uplifted").sum()}, by=members
    )
    assert _values(discounted.compute()) == [9.0, 18.0]
    assert _values(uplifted.compute()) == [14.0, 23.0]
    chosen_values = uplifted.where(uplifted["value"] > 20)
    chosen = _identity_population(
        session, chosen_values, session.catalog.entities.get(f"{prefix}.plain")
    )
    assert session.frame(chosen).compute().to_pandas().iloc[:, 0].tolist() == [(1, "B")]
    day = _read(session, members, f"{prefix}.plain.day")
    assert _values(day.where(day["value"] == date(2026, 8, 1)).compute()) == [date(2026, 8, 1)] * 2
    with pytest.raises(AnalysisError):
        day.where(day["value"] == datetime(2026, 8, 1, tzinfo=timezone.utc)).compute()


def test_computed_boolean_dimension(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    members = session.members(session.catalog.entities.get(f"{_domain(session)}.plain"))
    relation = _read(session, members, f"{_domain(session)}.plain.positive")
    saved = relation.persist()
    assert _values(saved) == [False, True]
    enabled = True
    selected = relation.where(relation["value"] == enabled).persist()
    assert _values(selected) == [True]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Boolean Artifact read its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    frame = session.persisted_frame(saved.artifact.id).to_pandas()
    selected_frame = frame.loc[frame[("value", "value")]].reset_index(drop=True)
    assert selected_frame.equals(session.persisted_frame(selected.artifact.id).to_pandas())
    assert selected_frame.iloc[:, 0].tolist() == [(1, "B")]
    assert session._runtime.store.resources(session.id) == ()


def _replace_source(session: Session, query: str) -> None:
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as db:
        db.execute(query)


def test_read_path_coverage_null_and_consumed_scope(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    category = _read(session, members, f"{prefix}.plain.category")
    chosen_values = category.where(category["value"] == "west")
    chosen = _identity_population(
        session, chosen_values, session.catalog.entities.get(f"{prefix}.plain")
    )
    september = datetime(2026, 9, 1, tzinfo=timezone.utc)
    route = f"{prefix}.to_snapshot"
    assert _values(
        _read(session, chosen, f"{prefix}.snapshot.amount", at=september, via=route).compute()
    ) == [30]
    missing = (
        _read(session, members, f"{prefix}.snapshot.amount", at=september, via=route)
        .compute()
        .to_pandas()
    )
    assert missing[("value", "value")].iloc[0] == 30
    assert missing[("value", "value")].isna().tolist() == [False, True]
    assert terminal_runs(session) == ()


def test_temporal_literals_fixed_recovery_and_foreign_predicates(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    logical = _read(session, members, f"{prefix}.plain.moment")
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        logical.where(logical["value"] == datetime(2026, 8, 1))
    instant = logical.compute()
    dates = _read(session, members, f"{prefix}.plain.day").compute()
    assert _values(dates) == [date(2026, 8, 1)] * 2
    boundary = datetime(2026, 8, 2, tzinfo=timezone.utc)
    selected = logical.where(logical["value"] < boundary).persist()
    restored = session.persisted_frame(selected.artifact.id)
    assert restored.to_pandas().iloc[:, 0].tolist() == [(1, "A")]
    other = mv.session.get_or_create("other-r52", timezone="UTC")
    foreign = _read(
        other,
        other.members(other.catalog.entities.get(f"{prefix}.plain")),
        f"{prefix}.plain.moment",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("foreign construction opened a source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "bind", forbidden)
        with pytest.raises(AnalysisError):
            logical.where(foreign["value"] < boundary)
        assert instant.to_pandas().loc[lambda frame: frame[("value", "value")] < boundary].iloc[
            :, 0
        ].tolist() == [(1, "A")]


def test_cold_typed_read_continuation_without_source(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    fixed = _read(
        session,
        session.members(session.catalog.entities.get(f"{prefix}.plain")),
        f"{prefix}.plain.moment",
    ).persist()
    shutil.rmtree(session.project_root / "models")
    (session.project_root / "warehouse.duckdb").unlink()
    script = """
import sys
from datetime import datetime, timezone
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
def forbidden(*args, **kwargs):
    raise AssertionError('cold Artifact opened a source or model')
ms.load = forbidden
duckdb.connect = forbidden
SourceSession.bind = forbidden
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2]).to_pandas()
selected = frame.loc[frame[('value','value')] < datetime(2026,8,2,tzinfo=timezone.utc)]
assert selected.iloc[:,0].tolist() == [(1,'A')]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, fixed.artifact.id],
        cwd=session.project_root,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT),
            "MARIVO_PROJECT_ROOT": str(session.project_root),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_complete_subject_set_image_source_and_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "subjects.duckdb")) as db:
        db.execute("CREATE TABLE instances(iid BIGINT, tenant BIGINT, sid VARCHAR)")
        db.execute("INSERT INTO instances VALUES (1,7,'A'),(2,7,'A'),(3,7,'B')")
        db.execute("CREATE TABLE subjects(tenant BIGINT, sid VARCHAR)")
        db.execute("INSERT INTO subjects VALUES (7,'A'),(7,'B')")
    semantic = tmp_path / "models/semantic/test"
    datasource = tmp_path / "models/datasources"
    semantic.mkdir(parents=True)
    datasource.mkdir(parents=True)
    (tmp_path / "marivo.toml").write_text('[project]\nname="subject-image"\n')
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='subjects.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='test', owner='Fixture', default=True)\n"
    )
    (semantic / "models.py").write_text("""import marivo.datasource as md
import marivo.semantic as ms
instances = ms.entity(name="instances", datasource=ms.ref.datasource("warehouse"), source=md.table("instances"), primary_key=["iid"])
subjects = ms.entity(name="subjects", datasource=ms.ref.datasource("warehouse"), source=md.table("subjects"), primary_key=["tenant", "sid"])
it = ms.dimension_column(name="tenant", entity=instances, column="tenant")
isi = ms.dimension_column(name="sid", entity=instances, column="sid")
st = ms.dimension_column(name="tenant", entity=subjects, column="tenant")
ssi = ms.dimension_column(name="sid", entity=subjects, column="sid")
to_subject = ms.relationship(name="to_subject", from_entity=instances, to_entity=subjects, keys=[ms.join_on(it,st), ms.join_on(isi,ssi)])
""")
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("subject-image", timezone="UTC")
    domain = session.members(session.catalog.entities.get("test.instances"))
    image = domain.image(via=(session.catalog.relationships.get("test.to_subject"),))
    result = session.frame(image).persist()
    assert result.to_pandas().iloc[:, 0].tolist() == [(7, "A"), (7, "B")]
    (tmp_path / "subjects.duckdb").rename(tmp_path / "subjects.offline")
    assert session.persisted_frame(result.artifact.id).to_pandas().iloc[:, 0].tolist() == [
        (7, "A"),
        (7, "B"),
    ]


def test_exact_boundary_null_and_corrupt_subject(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    entity = session.catalog.entities.get(f"{prefix}.valid")
    boundary = session.instant("2026-09-01")
    members = session.members(entity, at=boundary)
    assert _values(_read(session, members, f"{prefix}.valid.amount", at=boundary).compute()) == [
        30,
        20,
    ]
    before = boundary - timedelta(microseconds=1)
    left_members = session.members(entity, at=session.instant(before))
    assert _values(_read(session, left_members, f"{prefix}.valid.amount", at=before).compute()) == [
        10,
        20,
    ]
    noon = session.instant("2026-09-01T12:00:00+00:00")
    assert _values(
        _read(
            session, session.members(entity, at=noon), f"{prefix}.valid.amount", at=noon
        ).compute()
    ) == [30, 20]
    snapshot = session.catalog.entities.get(f"{prefix}.snapshot")
    first = session.members(snapshot, at=session.instant("2026-08-01T23:59:59.999999+00:00"))
    assert session.frame(first).compute().to_pandas().iloc[:, 0].tolist() == [(1, "A"), (1, "B")]
    assert (
        session.frame(session.members(snapshot, at=session.instant("2026-08-02")))
        .compute()
        .to_pandas()
        .empty
    )
    with pytest.raises(AnalysisError, match="an exact InstantAnchor"):
        session.members(entity, at=datetime(2026, 9, 1))  # type: ignore[arg-type]  # Runtime probe: membership requires an InstantAnchor, not datetime.
    plain = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    integer = _read(session, plain, f"{prefix}.plain.tenant")
    saved = integer.persist()
    assert saved.to_pandas().iloc[:, 0].tolist() == [(1, "A"), (1, "B")]
    assert _values(saved) == [1, 1]
    payload = receipt_paths(session.project_root, saved)[0]
    payload.write_bytes(b"corrupt complete identity")
    with pytest.raises(AnalysisError):
        session.persisted_frame(saved.artifact.id).to_pandas()
    _replace_source(
        session,
        "UPDATE r52 SET finish = DATE '2026-09-02' WHERE id = 'A' AND day = DATE '2026-08-01'",
    )
    with pytest.raises(AnalysisError):
        _read(session, members, f"{prefix}.valid.amount", at=boundary).compute()


def test_wrong_role_and_many_mapping_reject_before_io(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    at = session.instant("2026-08-01")
    field = session.catalog.fields.get(f"{prefix}.snapshot.amount")
    many = session.catalog.relationships.get(f"{prefix}.many_snapshot")
    route = session.catalog.relationships.get(f"{prefix}.to_snapshot")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid path requested a schema or business source")

    with monkeypatch.context() as patch:
        patch.setattr(SourceSession, "bind", forbidden)
        with pytest.raises((AnalysisError, SemanticExpressionError)):
            session.read(members, {"value": field.via((many,)).at(at)})
        versioned = session.members(session.catalog.entities.get(f"{prefix}.snapshot"), at=at)
        with pytest.raises((AnalysisError, SemanticExpressionError)):
            session.read(versioned, {"value": field.via((route,)).at(at)})
    assert _values(session.read(members, {"value": field.at(at)}).compute()) == [10, 20]
    assert _values(session.read(members, {"value": field.via((route,)).at(at)}).compute()) == [
        10,
        20,
    ]


@pytest.mark.release
def test_grid_attribute_point_is_independent_of_member_version(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(
        session.catalog.entities.get(f"{prefix}.valid"), at=session.instant("2026-08-01")
    )
    for points, expected in (
        (("2026-08-01", "2026-09-01"), [10, 30, 20, 20]),
        (("2026-09-01", "2026-10-01"), [30, 30, 20, 20]),
        (
            ("2026-08-31T23:59:59.999999+00:00", "2026-09-30T23:59:59.999999+00:00"),
            [10, 30, 20, 20],
        ),
    ):
        frames = [
            _read(session, members, f"{prefix}.valid.amount", at=session.instant(point))
            .compute()
            .to_pandas()
            for point in points
        ]
        actual = [frame[("value", "value")].iloc[i] for i in range(2) for frame in frames]
        assert actual == expected
    outputs = []
    for point in ("2026-08-31T23:59:59.999999+00:00", "2026-09-30T23:59:59.999999+00:00"):
        fields: dict[str, Deferred] = {
            name: session.catalog.fields.get(f"{prefix}.valid.{name}").at(session.instant(point))
            for name in ("category", "enabled", "moment")
        }
        result = session.read(members, fields).persist()
        frame = result.to_pandas()
        assert session.persisted_frame(result.artifact.id).to_pandas().equals(frame)
        outputs.append(frame)
    assert [frame[("value", "category")].iloc[i] for i in range(2) for frame in outputs] == [
        "west",
        "east",
        "east",
        "east",
    ]
    assert [frame[("value", "enabled")].iloc[i] for i in range(2) for frame in outputs] == [
        True,
        False,
        False,
        False,
    ]
    assert [frame[("value", "moment")].iloc[i] for i in range(2) for frame in outputs] == [
        datetime(2026, 8, 1, 1, 2, 3, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 1, 2, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 2, 1, 2, 3, tzinfo=timezone.utc),
    ]
    stable = session.catalog.fields.get(f"{prefix}.plain.category")
    with pytest.raises(SemanticExpressionError):
        session.read(
            session.members(session.catalog.entities.get(f"{prefix}.plain")),
            {"value": stable.at(session.instant("2026-08-01"))},
        )


def test_grid_classification_uses_grid_point(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    anchor = session.instant("2026-08-01T00:59:59.999999+00:00")
    members = session.members(session.catalog.entities.get(f"{prefix}.snapshot"), at=anchor)
    category = session.catalog.fields.get(f"{prefix}.snapshot.category").at(anchor)
    categories = members.classify(by=(category,))
    grid = session.time_scope(
        start="2026-08-01T00:00:00+00:00", end="2026-08-01T02:00:00+00:00"
    ).by(mv.grain("hour"))
    frame = (
        session.observe(
            {"value": session.catalog.metrics.get(f"{prefix}.observed_total")},
            by=mv.product(categories, grid),
        )
        .compute()
        .to_pandas()
    )
    assert len(frame) == 4
    assert frame[("value", "value")].tolist() == [0, 0, 0, 10]
    assert set(frame.iloc[:, 0]) == {"west", "east"}


def test_composite_subject_predicates(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(session.catalog.entities.get(f"{prefix}.plain"))
    fields = {
        name: session.catalog.fields.get(f"{prefix}.plain.{name}")
        for name in ("amount", "enabled", "moment")
    }
    values = session.read(members, fields)
    enabled = True
    predicate = (
        (values["enabled"] == enabled)
        & (values["moment"] <= values["moment"])
        & (values["amount"] > 0)
    )
    result = values.where(predicate).compute().to_pandas()
    assert result.iloc[:, 0].tolist() == [(1, "A")]
    frame = values.compute().to_pandas()
    mask = (
        frame[("value", "enabled")]
        & (frame[("value", "moment")] <= frame[("value", "moment")])
        & (frame[("value", "amount")] > 0)
    )
    assert frame.loc[mask].iloc[:, 0].tolist() == [(1, "A")]


def test_identity_grouping_binds_an_explicit_versioned_path(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    route = session.catalog.relationships.get(f"{prefix}.to_snapshot")
    target = session.catalog.entities.get(f"{prefix}.snapshot")
    instant = session.instant("2026-08-01")
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    path = mv.identity((route,), at=instant)
    automatic = mv.identity(target, at=instant)
    from tests.analysis.graph.execution_fixtures import source_graph

    explicit_frame = session.observe({"value": metric}, by=(path,))
    inferred_frame = session.observe({"value": metric}, by=(automatic,))
    explicit_binding = source_graph(explicit_frame).signature.observation
    inferred_binding = source_graph(inferred_frame).signature.observation
    assert explicit_binding is not None and inferred_binding is not None
    assert explicit_binding.contributions == inferred_binding.contributions
    rows = explicit_frame.compute().to_pandas()
    assert rows.set_index(rows.columns[0])[("value", "value")].to_dict() == {
        (1, "A"): 10,
        (1, "B"): 20,
    }
    with pytest.raises(AnalysisError, match="exact attribute anchor"):
        session.observe({"value": metric}, by=(mv.identity((route,)),))
    with pytest.raises(AnalysisError):
        mv.identity((route, route), at=instant)
    many = session.catalog.relationships.get(f"{prefix}.many_snapshot")
    with pytest.raises(AnalysisError):
        session.observe({"value": metric}, by=(mv.identity((many,), at=instant),))
