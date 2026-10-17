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
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticExpressionError
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
        "many_snapshot = ms.relationship(name='many_snapshot', from_entity=plain, to_entity=snapshot, keys=[ms.join_on(plain_tenant, snapshot_tenant)])\n"
        "@ms.dimension(name='positive', entity=plain)\n"
        "def positive(rows):\n"
        "    return rows.amount > 15\n"
        "frozen = ms.finalize()\n"
        "plain_amount_value = frozen.resolve(plain_amount)\n"
        "plain_moment_value = frozen.resolve(plain_moment)\n"
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
    return mv.session.get_or_create("r52-members", report_timezone="UTC")


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
) -> mv.Query:
    value = session.catalog.fields.get(field)
    if via is not None:
        value = value.via((session.catalog.relationships.get(via),))
    if at is not None:
        value = value.at(session.instant(at))
    return session.read(population, {"value": value})


def _values(result: mv.Artifact) -> list[object]:
    return result.to_pandas()[("value", "value")].tolist()


def test_member_axis_retains_ordered_composite_identity(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    category = session.catalog.fields.get(f"{prefix}.plain.category")
    for axes, expected in (
        ((members.axes[0], category), {((1, "A"), "west"): 10, ((1, "B"), "east"): 20}),
        ((category, members.axes[0]), {("west", (1, "A")): 10, ("east", (1, "B")): 20}),
    ):
        observed = session.observe({"value": metric}, by=axes)
        rows = observed.execute().to_pandas()
        assert rows.set_index(list(rows.columns[:2]))[("value", "value")].to_dict() == expected
        selected = observed.population().project(by=members.axes)
        assert session.query(selected).execute().to_pandas().iloc[:, 0].tolist() == [
            (1, "A"),
            (1, "B"),
        ]
    # Separate scalar primary-key fields retain their Field roles.
    fields = (
        session.catalog.fields.get(f"{prefix}.plain.tenant"),
        session.catalog.fields.get(f"{prefix}.plain.id"),
    )
    grouped = session.observe({"value": metric}, by=fields)
    assert all(axis.field is not None for axis in grouped.population().axes)
    grid = session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day"))
    rows = session.observe({"value": metric}, by=mv.product(members, grid)).execute().to_pandas()
    assert len(rows) == 6
    assert rows[("value", "value")].to_dict() == {0: 10, 1: None, 2: None, 3: None, 4: 20, 5: None}


def test_observation_classification_reads_explicit_version(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    metric = session.catalog.metrics.get(f"{prefix}.observed_total")
    route = session.catalog.relationships.get(f"{prefix}.to_snapshot")
    field = session.catalog.fields.get(f"{prefix}.snapshot.category")
    category = field.via((route,)).at(session.instant("2026-08-01"))
    observed = session.observe({"value": metric}, by=(category,)).execute().to_pandas()
    assert observed.set_index(observed.columns[0])[("value", "value")].to_dict() == {
        "west": 10,
        "east": 20,
    }
    own = _read(session, members, f"{prefix}.plain.category")
    selected = own.where(own["value"] == "west").population()
    later = field.via((route,)).at(session.instant("2026-09-01"))
    read = session.read(selected, {"value": later})
    assert _values(read.execute()) == ["east"]
    frame = session.observe({"value": metric}, members=selected, by=(later,)).execute().to_pandas()
    assert frame.set_index(frame.columns[0])[("value", "value")].to_dict() == {"east": 10}


def test_computed_measure_and_bound_dependency(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    discounted = session.observe(
        {"value": session.catalog.fields.get(f"{prefix}.plain.discounted").sum()}, by=members
    )
    uplifted = session.observe(
        {"value": session.catalog.fields.get(f"{prefix}.plain.uplifted").sum()}, by=members
    )
    assert _values(discounted.execute()) == [9.0, 18.0]
    assert _values(uplifted.execute()) == [14.0, 23.0]
    chosen = uplifted.where(uplifted["value"] > 20).population()
    assert session.query(chosen).execute().to_pandas().iloc[:, 0].tolist() == [(1, "B")]
    day = _read(session, members, f"{prefix}.plain.day")
    assert _values(day.where(day["value"] == date(2026, 8, 1)).execute()) == [date(2026, 8, 1)] * 2
    with pytest.raises(AnalysisError):
        day.where(day["value"] == datetime(2026, 8, 1, tzinfo=timezone.utc)).execute()


def test_computed_boolean_dimension(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    members = session.members(ms.ref.entity(f"{_domain(session)}.plain"))
    relation = _read(session, members, f"{_domain(session)}.plain.positive")
    saved = relation.execute()
    assert _values(saved) == [False, True]
    enabled = True
    selected = relation.where(relation["value"] == enabled).execute()
    assert _values(selected) == [True]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Boolean Artifact read its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    frame = session.artifact(saved.id).to_pandas()
    selected_frame = frame.loc[frame[("value", "value")]].reset_index(drop=True)
    assert selected_frame.equals(session.artifact(selected.id).to_pandas())
    assert selected_frame.iloc[:, 0].tolist() == [(1, "B")]
    assert session._runtime.store.resources(session.id) == ()


def _replace_source(session: Session, query: str) -> None:
    with duckdb.connect(str(session.project_root / "warehouse.duckdb")) as db:
        db.execute(query)


def test_read_path_coverage_null_and_consumed_scope(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    category = _read(session, members, f"{prefix}.plain.category")
    chosen = category.where(category["value"] == "west").population()
    september = datetime(2026, 9, 1, tzinfo=timezone.utc)
    route = f"{prefix}.to_snapshot"
    assert _values(
        _read(session, chosen, f"{prefix}.snapshot.amount", at=september, via=route).execute()
    ) == [30]
    with pytest.raises(MaterializationError) as missing:
        _read(session, members, f"{prefix}.snapshot.amount", at=september, via=route).execute()
    assert missing.value.expected and missing.value.received and missing.value.repair
    assert missing.value.run_ref is not None
    assert session.get_run(missing.value.run_ref).lifecycle == "failed"


def test_temporal_literals_fixed_recovery_and_foreign_predicates(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
    logical = _read(session, members, f"{prefix}.plain.moment")
    with pytest.raises((AnalysisError, SemanticExpressionError)):
        logical.where(logical["value"] == datetime(2026, 8, 1))
    instant = logical.execute()
    dates = _read(session, members, f"{prefix}.plain.day").execute()
    assert _values(dates) == [date(2026, 8, 1)] * 2
    boundary = datetime(2026, 8, 2, tzinfo=timezone.utc)
    selected = logical.where(logical["value"] < boundary).execute()
    restored = session.artifact(selected.id)
    assert restored.to_pandas().iloc[:, 0].tolist() == [(1, "A")]
    other = mv.session.get_or_create("other-r52", report_timezone="UTC")
    foreign = _read(
        other, other.members(ms.ref.entity(f"{prefix}.plain")), f"{prefix}.plain.moment"
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
        session, session.members(ms.ref.entity(f"{prefix}.plain")), f"{prefix}.plain.moment"
    ).execute()
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
session = mv.session.resume(sys.argv[1], by='id')
frame = session.artifact(sys.argv[2]).to_pandas()
selected = frame.loc[frame[('value','value')] < datetime(2026,8,2,tzinfo=timezone.utc)]
assert selected.iloc[:,0].tolist() == [(1,'A')]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, fixed.id],
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
    session = mv.session.get_or_create("subject-image", report_timezone="UTC")
    domain = session.members(ms.ref.entity("test.instances"))
    image = domain.image(via=(session.catalog.relationships.get("test.to_subject"),))
    result = session.query(image).execute()
    assert result.to_pandas().iloc[:, 0].tolist() == [(7, "A"), (7, "B")]
    (tmp_path / "subjects.duckdb").rename(tmp_path / "subjects.offline")
    assert session.artifact(result.id).to_pandas().iloc[:, 0].tolist() == [(7, "A"), (7, "B")]


def test_exact_boundary_null_and_corrupt_subject(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    entity = ms.ref.entity(f"{prefix}.valid")
    boundary = session.instant("2026-09-01")
    members = session.members(entity, at=boundary)
    assert _values(_read(session, members, f"{prefix}.valid.amount", at=boundary).execute()) == [
        30,
        20,
    ]
    before = boundary - timedelta(microseconds=1)
    left_members = session.members(entity, at=session.instant(before))
    assert _values(_read(session, left_members, f"{prefix}.valid.amount", at=before).execute()) == [
        10,
        20,
    ]
    noon = session.instant("2026-09-01T12:00:00+00:00")
    assert _values(
        _read(
            session, session.members(entity, at=noon), f"{prefix}.valid.amount", at=noon
        ).execute()
    ) == [30, 20]
    snapshot = ms.ref.entity(f"{prefix}.snapshot")
    first = session.members(snapshot, at=session.instant("2026-08-01T23:59:59.999999+00:00"))
    assert session.query(first).execute().to_pandas().iloc[:, 0].tolist() == [(1, "A"), (1, "B")]
    assert (
        session.query(session.members(snapshot, at=session.instant("2026-08-02")))
        .execute()
        .to_pandas()
        .empty
    )
    with pytest.raises(AnalysisError, match="an exact InstantAnchor"):
        session.members(entity, at=datetime(2026, 9, 1))
    plain = session.members(ms.ref.entity(f"{prefix}.plain"))
    integer = _read(session, plain, f"{prefix}.plain.tenant")
    saved = integer.execute()
    assert saved.to_pandas().iloc[:, 0].tolist() == [(1, "A"), (1, "B")]
    assert _values(saved) == [1, 1]
    receipt = saved._manifest.data.rows.receipt
    payload = (
        session.project_root
        / receipt.project_relative_path
        / receipt.file_manifest[0].relative_path
    )
    payload.write_bytes(b"corrupt complete identity")
    with pytest.raises(AnalysisError):
        session.artifact(saved.id).to_pandas()
    _replace_source(
        session,
        "UPDATE r52 SET finish = DATE '2026-09-02' WHERE id = 'A' AND day = DATE '2026-08-01'",
    )
    with pytest.raises(AnalysisError):
        _read(session, members, f"{prefix}.valid.amount", at=boundary).execute()


def test_wrong_role_and_many_mapping_reject_before_io(
    members_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
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
        versioned = session.members(ms.ref.entity(f"{prefix}.snapshot"), at=at)
        with pytest.raises((AnalysisError, SemanticExpressionError)):
            session.read(versioned, {"value": field.via((route,)).at(at)})
    assert _values(session.read(members, {"value": field.at(at)}).execute()) == [10, 20]
    assert _values(session.read(members, {"value": field.via((route,)).at(at)}).execute()) == [
        10,
        20,
    ]


def test_grid_attribute_point_is_independent_of_member_version(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.valid"), at=session.instant("2026-08-01"))
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
            .execute()
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
        result = session.read(members, fields).execute()
        frame = result.to_pandas()
        assert session.artifact(result.id).to_pandas().equals(frame)
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
            session.members(ms.ref.entity(f"{prefix}.plain")),
            {"value": stable.at(session.instant("2026-08-01"))},
        )


def test_grid_classification_uses_grid_point(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    anchor = session.instant("2026-08-01T00:59:59.999999+00:00")
    members = session.members(ms.ref.entity(f"{prefix}.snapshot"), at=anchor)
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
        .execute()
        .to_pandas()
    )
    assert len(frame) == 4
    assert frame[("value", "value")].dropna().tolist() == [10]
    assert frame[("value", "value")].isna().sum() == 3
    assert set(frame.iloc[:, 0]) == {"west", "east"}


def test_composite_subject_predicates(members_session: Session) -> None:
    session = members_session
    prefix = _domain(session)
    members = session.members(ms.ref.entity(f"{prefix}.plain"))
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
    result = values.where(predicate).execute().to_pandas()
    assert result.iloc[:, 0].tolist() == [(1, "A")]
    frame = values.execute().to_pandas()
    mask = (
        frame[("value", "enabled")]
        & (frame[("value", "moment")] <= frame[("value", "moment")])
        & (frame[("value", "amount")] > 0)
    )
    assert frame.loc[mask].iloc[:, 0].tolist() == [(1, "A")]
